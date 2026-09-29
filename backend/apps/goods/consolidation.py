"""Consolidation, the coverage floor and backhaul (blueprint 9, 11, p. 23).

Envelope 3A put one shipper on one consignment. This module adds what 3A
deliberately left out: several shippers sharing a vehicle, a published plan
version that survives later changes, a departure gate that refuses a truck that
cannot pay for itself, and the search for cargo on a return leg that would
otherwise run empty.

Two blueprint rules shape the design:

* "Start with dispatcher-assisted assignment and transparent rules" and "save
  each route plan version" -- a plan is a versioned record, never an in-place
  edit, and every rejection carries its reason so dispatch sees unassigned work
  instead of an empty plan.
* "Validate its proposed result against your own business rules before
  publication" -- :func:`validate_plan` re-derives every rule from the database,
  so a proposal cannot be published on the proposer's say-so alone.
"""

from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.fleet.models import Vehicle
from apps.goods.models import (
    ConsolidationPlan,
    ConsolidationStop,
    FreightTrip,
    LoadType,
    Manifest,
    ManifestLine,
    Package,
    PlanShipment,
    Shipment,
)
from apps.goods.services import cargo_capacity

ZERO = Decimal("0")

# Rejection reasons, so dispatch reads a cause rather than a bare "no".
REASON_FULL_TRUCKLOAD_ALONE = "a full truckload travels alone"
REASON_NOT_SHARED_LOAD = "not a shared-load consignment"
REASON_TOO_HEAVY = "exceeds remaining cargo weight"
REASON_TOO_BULKY = "exceeds remaining cargo volume"
REASON_WINDOW_OUTSIDE_TRIP = "service window falls outside the trip"
REASON_WRONG_ORIGIN = "collection place is not on this trip"
REASON_WRONG_DESTINATION = "destination is not on this trip"
REASON_ALREADY_PLANNED = "already on another published plan"
REASON_WORKING_HOURS = "would exceed the driver's working hours"
REASON_TRIP_DEPARTED = "the driver has already departed"
REASON_OTHER_ORGANIZATION = "belongs to another organization"
REASON_SHIPMENT_CLOSED = "consignment is already closed"


class FreightError(Exception):
    """Business-rule violation; the API layer renders it as 400."""


def _now():
    return timezone.now()


def _aware_dt(value, field):
    if isinstance(value, str):
        value = parse_datetime(value)
    if value is None:
        raise FreightError(f"{field} is required.")
    if timezone.is_naive(value):
        value = timezone.make_aware(value)
    return value


# ---------------------------------------------------------------------------
# Consolidation proposals
# ---------------------------------------------------------------------------


@transaction.atomic
def propose_consolidation(*, organization, trip, shipments, proposed_by=None):
    """Build the next plan version for a trip from the consignments offered.

    Assignment is rule-based and transparent: the stops are sequenced by their
    service windows, and a consignment is admitted only if it fits the vehicle's
    remaining weight and volume inside the trip's own window. Anything that
    cannot be carried is recorded with the reason, never dropped -- "if no
    feasible plan exists, return an explanation to dispatch rather than hiding
    unassigned work".
    """
    if trip.organization_id != organization.pk:
        raise FreightError("The trip belongs to another organization.")
    if trip.status in (FreightTrip.Status.COMPLETED, FreightTrip.Status.CANCELLED):
        raise FreightError("A finished trip cannot take a consolidation plan.")
    if trip.started_at is not None:
        raise FreightError(REASON_TRIP_DEPARTED)

    shipments = list(shipments or [])
    if not shipments:
        raise FreightError("Offer at least one consignment to consolidate.")

    previous = trip.consolidation_plans.order_by("-version").first()
    plan = ConsolidationPlan.objects.create(
        organization=organization,
        trip=trip,
        version=(previous.version + 1) if previous is not None else 1,
        supersedes=previous,
        status=ConsolidationPlan.Status.PROPOSED,
        proposed_by=proposed_by,
    )

    capacity = cargo_capacity(trip)
    remaining_weight = capacity["free_weight_kg"]
    remaining_volume = capacity["free_volume_m3"]
    working_hours = getattr(trip.driver, "working_hours_per_day", None)
    trip_hours = (trip.planned_end - trip.planned_start).total_seconds() / 3600

    # Windows first, then creation order: a deterministic, explainable order.
    ordered = sorted(
        shipments,
        key=lambda s: (s.service_window_start, s.service_window_end, s.created_at),
    )

    # "Full truckload: a full vehicle load" (blueprint 9) is the whole vehicle, so
    # a full truckload offered alongside shared consignments takes the truck on
    # its own and the others are told why. Decided before admission, not after,
    # so the order consignments arrive in cannot smuggle a shared load aboard.
    full_offered = [s for s in ordered if s.load_type == LoadType.FULL_TRUCKLOAD]
    full_truckload_admitted = bool(full_offered)
    full_shipment_pk = full_offered[0].pk if full_offered else None

    stops = {}
    admitted = 0

    for shipment in ordered:
        reason = _rejection_reason(
            shipment,
            trip=trip,
            remaining_weight=remaining_weight,
            remaining_volume=remaining_volume,
            working_hours=working_hours,
            trip_hours=trip_hours,
            full_truckload_admitted=full_truckload_admitted,
            full_shipment_pk=full_shipment_pk,
        )
        if reason is not None:
            PlanShipment.objects.create(
                organization=organization,
                plan=plan,
                shipment=shipment,
                accepted=False,
                reason=reason,
                revenue_minor=shipment.agreed_total_minor,
            )
            continue

        remaining_weight -= shipment.total_weight_kg
        remaining_volume -= shipment.total_volume_m3
        admitted += 1
        if shipment.load_type == LoadType.FULL_TRUCKLOAD:
            full_truckload_admitted = True

        for place, window_start, window_end in (
            (shipment.collection, trip.planned_start, trip.planned_start),
            (
                shipment.destination,
                shipment.service_window_start,
                shipment.service_window_end,
            ),
        ):
            existing = stops.get(place.pk)
            if existing is not None:
                # A shared stop widens to the union of its consignments' windows.
                existing[1] = min(existing[1], window_start)
                existing[2] = max(existing[2], window_end)
                continue
            stops[place.pk] = [place, window_start, window_end, len(stops)]

        PlanShipment.objects.create(
            organization=organization,
            plan=plan,
            shipment=shipment,
            accepted=True,
            sequence=admitted,
            revenue_minor=shipment.agreed_total_minor,
        )

    for entry in sorted(stops.values(), key=lambda item: item[3]):
        ConsolidationStop.objects.create(
            organization=organization,
            plan=plan,
            place=entry[0],
            sequence=entry[3],
            window_start=entry[1],
            window_end=entry[2],
        )

    if admitted == 0:
        plan.status = ConsolidationPlan.Status.REJECTED
        plan.explanation = _explanation(plan)
        plan.save(update_fields=["status", "explanation", "updated_at"])
    return plan


def _rejection_reason(
    shipment,
    *,
    trip,
    remaining_weight,
    remaining_volume,
    working_hours,
    trip_hours,
    full_truckload_admitted,
    full_shipment_pk=None,
):
    if shipment.organization_id != trip.organization_id:
        return REASON_OTHER_ORGANIZATION
    if shipment.status in (Shipment.Status.CANCELLED, Shipment.Status.DELIVERED):
        return REASON_SHIPMENT_CLOSED
    if working_hours and trip_hours > working_hours:
        return REASON_WORKING_HOURS
    if trip.origin_id is not None and shipment.collection_id != trip.origin_id:
        return REASON_WRONG_ORIGIN
    if trip.destination_id is not None and shipment.destination_id != trip.destination_id:
        return REASON_WRONG_DESTINATION
    if (
        shipment.service_window_start < trip.planned_start
        or shipment.service_window_end > trip.planned_end
    ):
        return REASON_WINDOW_OUTSIDE_TRIP

    is_full = shipment.load_type == LoadType.FULL_TRUCKLOAD
    # A full truckload is the whole vehicle: it rides alone or not at all, and
    # only one full truckload can hold a trip.
    if full_truckload_admitted:
        if is_full and shipment.pk != full_shipment_pk:
            return REASON_FULL_TRUCKLOAD_ALONE
        if not is_full:
            return REASON_FULL_TRUCKLOAD_ALONE
    if not is_full and shipment.load_type != LoadType.SHARED_LOAD:
        return REASON_NOT_SHARED_LOAD
    if shipment.total_weight_kg > remaining_weight:
        return REASON_TOO_HEAVY
    if shipment.total_volume_m3 > remaining_volume:
        return REASON_TOO_BULKY
    if (
        PlanShipment.objects.filter(
            shipment=shipment,
            accepted=True,
            plan__status=ConsolidationPlan.Status.PUBLISHED,
        )
        .exclude(plan__trip=trip)
        .exists()
    ):
        return REASON_ALREADY_PLANNED
    return None


def _explanation(plan):
    """Spell out, per consignment, why the plan carries nothing."""
    reasons = list(plan.shipments.filter(accepted=False).values_list("reason", flat=True))
    if not reasons:
        return "No consignment could be assigned."
    counts = {}
    for reason in reasons:
        counts[reason] = counts.get(reason, 0) + 1
    detail = "; ".join(f"{reason} ({count})" for reason, count in sorted(counts.items()))
    return f"No consignment fits this trip: {detail}."


# ---------------------------------------------------------------------------
# Validation and publication
# ---------------------------------------------------------------------------


@transaction.atomic
def validate_plan(plan, *, loaded_shipment_ids=None):
    """Re-derive every rule from the database before publication.

    "Validate its proposed result against your own business rules before
    publication." Nothing here trusts the proposer.

    ``loaded_shipment_ids`` names consignments already physically on the
    vehicle. The manifest is the cargo ledger, so their weight and volume are
    already committed; counting them again would charge a mid-route plan for
    load the truck is carrying.
    """
    trip = plan.trip
    problems = []
    admitted = list(plan.shipments.filter(accepted=True).select_related("shipment"))
    rejected = plan.shipments.filter(accepted=False).count()
    loaded = set(loaded_shipment_ids or ())

    if not admitted:
        problems.append("The plan carries no consignment.")
    if rejected:
        problems.append(f"{rejected} offered consignment(s) were not assignable.")

    to_load = [entry for entry in admitted if entry.shipment_id not in loaded]
    weight = sum((entry.shipment.total_weight_kg for entry in to_load), ZERO)
    volume = sum((entry.shipment.total_volume_m3 for entry in to_load), ZERO)
    capacity = cargo_capacity(trip)
    if weight > capacity["free_weight_kg"]:
        problems.append("The plan exceeds the vehicle's remaining cargo weight.")
    if volume > capacity["free_volume_m3"]:
        problems.append("The plan exceeds the vehicle's remaining cargo volume.")

    full = [e for e in admitted if e.shipment.load_type == LoadType.FULL_TRUCKLOAD]
    if full and len(admitted) > 1:
        problems.append("A full truckload travels alone; it is sharing this trip.")

    for entry in admitted:
        shipment = entry.shipment
        if shipment.service_window_start < trip.planned_start:
            problems.append(f"{shipment.reference} is due before the trip departs.")
        if shipment.service_window_end > trip.planned_end:
            problems.append(f"{shipment.reference} is due after the trip arrives.")
        if entry.revenue_minor != shipment.agreed_total_minor:
            problems.append(
                f"{shipment.reference} revenue does not match its frozen quote."
            )

    working_hours = getattr(trip.driver, "working_hours_per_day", None)
    if working_hours:
        trip_hours = (trip.planned_end - trip.planned_start).total_seconds() / 3600
        if trip_hours > working_hours:
            problems.append(
                f"The trip runs {trip_hours:.1f}h against a {working_hours}h working day."
            )

    if admitted and plan.stops.count() == 0:
        problems.append("The plan has no stop order.")

    if problems:
        plan.status = ConsolidationPlan.Status.REJECTED
        plan.explanation = " ".join(problems)
        plan.validated_at = None
    else:
        plan.status = ConsolidationPlan.Status.VALIDATED
        plan.explanation = ""
        plan.validated_at = _now()
    plan.save(update_fields=["status", "explanation", "validated_at", "updated_at"])
    return plan


@transaction.atomic
def publish_plan(plan, *, reason=""):
    """Publish a validated plan, superseding the previous published version.

    Only a validated plan may be published, so an unvalidated or infeasible
    proposal cannot reach a driver.
    """
    if plan.status == ConsolidationPlan.Status.PUBLISHED:
        raise FreightError("This plan is already published.")
    if plan.status != ConsolidationPlan.Status.VALIDATED:
        raise FreightError("Validate the plan before publishing it.")

    previous = (
        ConsolidationPlan.objects.filter(
            trip=plan.trip, status=ConsolidationPlan.Status.PUBLISHED
        )
        .exclude(pk=plan.pk)
        .first()
    )
    if previous is not None:
        if not reason:
            raise FreightError(
                "A published plan is being replaced; give a reason for the change."
            )
        previous.status = ConsolidationPlan.Status.SUPERSEDED
        previous.save(update_fields=["status", "updated_at"])

    plan.status = ConsolidationPlan.Status.PUBLISHED
    plan.published_at = _now()
    if reason:
        plan.change_reason = reason
    plan.save(update_fields=["status", "published_at", "change_reason", "updated_at"])
    return plan


@transaction.atomic
def supersede_plan(plan, *, reason, proposed_by=None):
    """Replace a published plan with a new version, keeping the old one.

    "Once a driver begins a route, preserve completed stops and communicate any
    remaining-stop changes clearly": the reason is mandatory, and the previous
    version is retained rather than edited.
    """
    if not plan.is_published:
        raise FreightError("Only a published plan can be superseded.")
    if not reason:
        raise FreightError("Give a reason for changing the plan.")
    if plan.trip.started_at is not None:
        raise FreightError(REASON_TRIP_DEPARTED)
    new_plan = propose_consolidation(
        organization=plan.organization,
        trip=plan.trip,
        shipments=[
            entry.shipment
            for entry in plan.shipments.select_related("shipment")
        ],
        proposed_by=proposed_by,
    )
    new_plan.supersedes = plan
    new_plan.change_reason = reason
    new_plan.save(update_fields=["supersedes", "change_reason", "updated_at"])
    return new_plan


# ---------------------------------------------------------------------------
# The coverage floor
# ---------------------------------------------------------------------------


def assess_coverage(trip):
    """Compare the revenue on the truck with the cost of running it.

    The product owner's decision: each shipper pays its own frozen quote, and
    the trip only departs once the shipments physically on it together cover
    ``trip_cost_minor``. Nothing is apportioned or repriced.
    """
    manifest = Manifest.objects.filter(trip=trip).order_by("-version").first()
    committed = 0
    if manifest is not None:
        # Each shipper's revenue counted once, however many of its packages are
        # on the truck, and never collapsed together with another shipper's
        # identical figure.
        shipment_ids = (
            manifest.lines.filter(
                scan_state__in=[
                    ManifestLine.ScanState.PENDING,
                    ManifestLine.ScanState.SCANNED,
                    ManifestLine.ScanState.DAMAGED,
                ]
            )
            .exclude(package__isnull=True)
            .values_list("package__shipment_id", flat=True)
            .distinct()
        )
        committed = (
            Shipment.objects.filter(pk__in=list(shipment_ids)).aggregate(
                total=Sum("agreed_total_minor")
            )["total"]
            or 0
        )
    floor = trip.trip_cost_minor or 0
    return {
        "trip": trip,
        "committed_minor": committed,
        "trip_cost_minor": floor,
        "shortfall_minor": max(floor - committed, 0),
        "surplus_minor": max(committed - floor, 0),
        "covered": committed >= floor,
    }


@transaction.atomic
def confirm_departure(trip):
    """The departure gate.

    A truck leaves only when its load is correct (3A: manifest released), its
    plan is published (blueprint 11) and its revenue covers its cost.
    """
    if trip.status in (FreightTrip.Status.COMPLETED, FreightTrip.Status.CANCELLED):
        raise FreightError("This trip is finished.")
    if trip.started_at is not None:
        raise FreightError("This trip has already departed.")

    manifest = Manifest.objects.filter(trip=trip).order_by("-version").first()
    if manifest is None or manifest.status != Manifest.Status.RELEASED:
        raise FreightError("Release the manifest before departure.")

    if not trip.consolidation_plans.filter(
        status=ConsolidationPlan.Status.PUBLISHED
    ).exists():
        raise FreightError("Publish a consolidation plan before departure.")

    # The truck carries what the plan promised: a manifest and a plan that
    # disagree mean somebody changed one of them after the other was signed off.
    plan = trip.consolidation_plans.filter(
        status=ConsolidationPlan.Status.PUBLISHED
    ).order_by("-version").first()
    on_manifest = set(
        manifest.lines.exclude(package__isnull=True)
        .values_list("package__shipment_id", flat=True)
        .distinct()
    )
    in_plan = set(
        plan.shipments.filter(accepted=True).values_list("shipment_id", flat=True)
    )
    if on_manifest != in_plan:
        raise FreightError(
            "The released manifest does not match the published consolidation plan; "
            "publish a new plan version before departure."
        )

    coverage = assess_coverage(trip)
    if not coverage["covered"]:
        raise FreightError(
            f"Revenue of {coverage['committed_minor']} falls short of the "
            f"{coverage['trip_cost_minor']} cost of running this trip."
        )

    trip.status = FreightTrip.Status.IN_TRANSIT
    trip.started_at = _now()
    trip.has_revenue_load = True
    trip.save(update_fields=["status", "started_at", "has_revenue_load", "updated_at"])
    for line in manifest.lines.filter(scan_state=ManifestLine.ScanState.SCANNED):
        if line.package is not None:
            line.package.status = Package.Status.IN_TRANSIT
            line.package.save(update_fields=["status", "updated_at"])
    return trip


# ---------------------------------------------------------------------------
# Backhaul and the empty-running measure
# ---------------------------------------------------------------------------


def find_backhaul_opportunities(
    *, organization, origin, destination, window_start, window_end
):
    """Vehicles that could carry cargo home instead of running empty.

    A vehicle is an opportunity when it has already run a revenue leg to
    ``origin``, has cargo allowance left, and can be back before ``window_end``.
    """
    start = _aware_dt(window_start, "window_start")
    end = _aware_dt(window_end, "window_end")
    if end <= start:
        raise FreightError("The backhaul window must end after it starts.")

    candidates = FreightTrip.objects.filter(
        organization=organization,
        destination=origin,
        status=FreightTrip.Status.COMPLETED,
        planned_end__lte=start,
    ).exclude(
        vehicle__status__in=[Vehicle.Status.BLOCKED, Vehicle.Status.RETIRED]
    )

    opportunities = []
    for trip in candidates.select_related("vehicle"):
        capacity = cargo_capacity(trip)
        free = capacity["free_weight_kg"]
        if free <= 0:
            continue
        # The return leg must fit in the offered window.
        if trip.planned_end + timedelta(hours=8) > end:
            continue
        opportunities.append(
            {
                "trip": trip,
                "vehicle": trip.vehicle,
                "free_weight_kg": free,
                "free_volume_m3": capacity["free_volume_m3"],
                "available_from": trip.planned_end,
                "must_depart_by": end,
            }
        )
    return opportunities


def empty_running_report(*, organization, since=None):
    """The measure the blueprint names (p. 23).

    "Empty running: distance without the defined revenue load divided by
    measured distance."
    """
    since = _aware_dt(since, "since") if since else _now() - timedelta(days=30)
    trips = FreightTrip.objects.filter(organization=organization, planned_start__gte=since)
    measured = sum((trip.distance_km or ZERO for trip in trips), ZERO)
    empty = sum((trip.distance_km for trip in trips if not trip.has_revenue_load), ZERO)
    ratio = (empty / measured).quantize(Decimal("0.0001")) if measured else ZERO
    return {
        "since": since,
        "trips": trips.count(),
        "measured_distance_km": measured,
        "empty_distance_km": empty,
        "empty_running_ratio": ratio,
    }
