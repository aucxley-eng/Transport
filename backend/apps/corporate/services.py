"""Corporate travel and recurring shuttle services (blueprint 8).

Enforces the four blueprint clauses:

  1. "A company defines eligible employees, cost centers, budgets, approval
      thresholds and permitted services."
  2. "A travel request may require approval before dispatch."
  3. "Recurring shuttles create dated departures from a schedule template and
      maintain a passenger roster."
  4. "An employee leaving the company must lose future access without deleting
      historical invoices."

Shuttles deliberately generate ordinary `booking.Departure` rows: corporate
travel is a policy and approval layer *over* the seat inventory built in
Envelope 1, not a second inventory system.
"""

from datetime import date, timedelta

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.accounts.models import Role
from apps.booking.models import Departure
from apps.booking.services import build_segments
from apps.corporate.models import (
    CorporatePolicy,
    ShuttleRosterEntry,
    ShuttleRun,
    TravelRequest,
)


class CorporateError(Exception):
    """Business rule violation; surfaced to the API as 400."""


def _policy(organization):
    policy = CorporatePolicy.objects.filter(organization=organization).first()
    if policy is None:
        policy = CorporatePolicy.objects.create(organization=organization)
    return policy


def is_corporate_manager(user):
    """Corporate travel managers (and admins) decide requests."""
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser:
        return True
    return user.memberships.filter(
        is_active=True,
        role__in=[
            Role.CORPORATE_MANAGER,
            Role.OPERATOR_ADMIN,
            Role.PLATFORM_ADMIN,
        ],
    ).exists()


# ---------------------------------------------------------------------------
# Clause 1: eligible employees, cost centers, budgets, thresholds, services
# ---------------------------------------------------------------------------


def cost_center_spent(cost_center, up_to=None):
    """Committed spend on a cost centre: approved and fulfilled requests.

    Deliberately derived from immutable history rather than a mutable counter, so
    a departure from the company can never change what a cost centre already
    spent.
    """
    if cost_center is None:
        return 0
    up_to = up_to or timezone.now()
    total = TravelRequest.objects.filter(
        cost_center=cost_center,
        status__in=[
            TravelRequest.Status.APPROVED,
            TravelRequest.Status.FULFILLED,
        ],
        created_at__lte=up_to,
    ).aggregate(total=Sum("estimated_minor"))
    return total["total"] or 0


def budget_remaining(cost_center):
    if cost_center is None or not cost_center.budget_minor:
        return None  # no budget cap configured
    return cost_center.budget_minor - cost_center_spent(cost_center)


# ---------------------------------------------------------------------------
# Clause 2: approval before dispatch
# ---------------------------------------------------------------------------


@transaction.atomic
def submit_travel_request(*, member, departure=None, purpose="", cost_center=None):
    """Submit a travel request. Approval is required when the estimated cost
    reaches the company's threshold, or when the company sets no threshold at
    all (conservative default)."""
    if not member.is_eligible():
        raise CorporateError("This employee is no longer eligible to request travel.")

    policy = _policy(member.organization)
    if not policy.is_active:
        raise CorporateError("Corporate travel is not active for this company.")

    cost_center = cost_center or member.cost_center
    estimated_minor = 0
    currency = "KES"
    if departure is not None:
        service = departure.service
        if service is None:
            raise CorporateError("This departure is not linked to a bookable service.")
        # Permitted services: an empty list means "anything the company sells".
        permitted = list(policy.permitted_services.all())
        if permitted and service not in permitted:
            raise CorporateError("This service is not permitted for corporate travel.")
        estimated_minor = departure.fare_minor
        currency = departure.currency

    # Budget: refuse rather than silently overspend a cost centre.
    if cost_center is not None and cost_center.is_active and cost_center.budget_minor:
        if estimated_minor > budget_remaining(cost_center):
            raise CorporateError("This request exceeds the remaining cost centre budget.")

    requires_approval = estimated_minor >= policy.approval_threshold_minor
    return TravelRequest.objects.create(
        organization=member.organization,
        member=member,
        cost_center=cost_center,
        departure=departure,
        purpose=purpose,
        estimated_minor=estimated_minor,
        currency=currency,
        requires_approval=requires_approval,
        status=(
            TravelRequest.Status.PENDING_APPROVAL
            if requires_approval
            else TravelRequest.Status.APPROVED
        ),
    )


@transaction.atomic
def approve_travel_request(travel_request, *, approver):
    if not is_corporate_manager(approver):
        raise CorporateError("Corporate travel manager privileges are required.")
    if travel_request.member.user_id == approver.pk:
        raise CorporateError("A request cannot be approved by the requesting employee.")
    if travel_request.status != TravelRequest.Status.PENDING_APPROVAL:
        raise CorporateError("This request is not awaiting approval.")
    travel_request.status = TravelRequest.Status.APPROVED
    travel_request.decided_by = approver
    travel_request.decided_at = timezone.now()
    travel_request.save(
        update_fields=["status", "decided_by", "decided_at", "updated_at"]
    )
    return travel_request


@transaction.atomic
def reject_travel_request(travel_request, *, approver, note=""):
    if not is_corporate_manager(approver):
        raise CorporateError("Corporate travel manager privileges are required.")
    if travel_request.member.user_id == approver.pk:
        raise CorporateError("A request cannot be rejected by the requesting employee.")
    if travel_request.status != TravelRequest.Status.PENDING_APPROVAL:
        raise CorporateError("This request is not awaiting approval.")
    travel_request.status = TravelRequest.Status.REJECTED
    travel_request.decided_by = approver
    travel_request.decided_at = timezone.now()
    travel_request.decision_note = note
    travel_request.save(
        update_fields=["status", "decided_by", "decided_at", "decision_note", "updated_at"]
    )
    return travel_request


# ---------------------------------------------------------------------------
# Clause 4: leaving loses future access, history survives
# ---------------------------------------------------------------------------


@transaction.atomic
def deactivate_member(member, *, on_date=None):
    """Revoke future access. Historical travel requests, orders and tickets are
    untouched: nothing is deleted and no row is rewritten."""
    member.is_active = False
    member.left_on = on_date or timezone.localdate()
    member.save(update_fields=["is_active", "left_on", "updated_at"])
    # Roster entries on runs that have not departed yet are cancelled, so a
    # leaver does not occupy a seat. Past runs keep their roster.
    today = member.left_on
    ShuttleRosterEntry.objects.filter(
        member=member,
        state=ShuttleRosterEntry.State.PLANNED,
        run__run_date__gte=today,
    ).update(state=ShuttleRosterEntry.State.CANCELLED)
    return member


# ---------------------------------------------------------------------------
# Clause 3: schedule template -> dated departures, plus roster
# ---------------------------------------------------------------------------


def _as_date(value, field_name):
    """API callers pass ISO strings; services compare real dates."""
    if value is None or isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise CorporateError(f"{field_name} is not a valid ISO date.") from exc


def _template_dates(template, start, end):
    """Dates in [start, end] on which the template runs."""
    start = _as_date(start, "start")
    end = _as_date(end, "end")
    if template.valid_from and start < template.valid_from:
        start = template.valid_from
    if template.valid_until and end > template.valid_until:
        end = template.valid_until
    if end < start:
        return []
    weekdays = set(template.weekdays or [])
    dates = []
    current = start
    while current <= end:
        if not weekdays or current.isoweekday() in weekdays:
            dates.append(current)
        current += timedelta(days=1)
    return dates


@transaction.atomic
def generate_shuttle_runs(template, *, start, end, vehicle=None):
    """Create one dated departure per run date. Idempotent: a second call over
    the same range creates nothing new."""
    if not template.is_active:
        raise CorporateError("This shuttle template is not active.")
    created = []
    for run_date in _template_dates(template, start, end):
        # The departure belongs to the operator that runs the vehicle (the
        # route's owner), not to the corporate client: it is ordinary bookable
        # inventory that Envelope 1 already knows how to hold and sell.
        departure, was_created = Departure.objects.get_or_create(
            organization=template.route.organization,
            route=template.route,
            departure_date=run_date,
            departure_time=template.departure_time,
            defaults={
                "service": template.service,
                "vehicle": vehicle,
                "vehicle_class": getattr(vehicle, "vehicle_class", ""),
                "capacity": template.capacity,
                "fare_minor": template.fare_minor,
                "currency": template.currency,
            },
        )
        if was_created:
            build_segments(departure)
        run, _ = ShuttleRun.objects.get_or_create(
            template=template,
            run_date=run_date,
            defaults={
                "organization": template.organization,
                "departure": departure,
            },
        )
        created.append(run)
    return created


@transaction.atomic
def roster_member(run, member, *, seat_number=None):
    """Add an eligible employee to a shuttle's passenger roster."""
    if not member.is_eligible():
        raise CorporateError("This employee is no longer eligible to travel.")
    if run.organization_id != member.organization_id:
        raise CorporateError("Employee and shuttle belong to different companies.")

    departure = run.departure
    if ShuttleRosterEntry.objects.filter(run=run, member=member).exists():
        raise CorporateError("This employee is already on the roster.")
    seated = ShuttleRosterEntry.objects.filter(
        run=run,
        state__in=[ShuttleRosterEntry.State.PLANNED, ShuttleRosterEntry.State.BOARDED],
    ).count()
    capacity = departure.capacity or run.template.capacity
    if capacity and seated >= capacity:
        raise CorporateError("This shuttle is full.")
    return ShuttleRosterEntry.objects.create(
        organization=run.organization,
        run=run,
        member=member,
        seat_number=seat_number,
    )


@transaction.atomic
def remove_from_roster(run, member):
    entry = ShuttleRosterEntry.objects.filter(run=run, member=member).first()
    if entry is None:
        raise CorporateError("This employee is not on the roster.")
    entry.state = ShuttleRosterEntry.State.CANCELLED
    entry.save(update_fields=["state", "updated_at"])
    return entry
