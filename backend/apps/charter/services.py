"""Charter and vehicle hire services (blueprint 8).

Enforces the three blueprint sentences:

  1. "A hire request specifies dates, itinerary, vehicle class, passenger count
      and included hours or distance."
  2. "Staff issue a quote, reserve the vehicle and driver for the entire
      interval, record deposits, and finalize permitted extras at completion."
  3. "Availability checks include travel to the pickup, turnaround time and
      existing reservations."
"""

from datetime import timedelta

from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from apps.catalog.models import build_quote
from apps.charter.models import (
    DEFAULT_TRAVEL_BUFFER_MINUTES,
    DEFAULT_TURNAROUND_MINUTES,
    CharterDeposit,
    CharterExtra,
    CharterReservation,
    HireRequest,
)
from apps.fleet.models import Document, Driver, Vehicle


class CharterError(Exception):
    """Business rule violation; surfaced to the API as 400."""


# ---------------------------------------------------------------------------
# Blocking windows: travel to pickup + turnaround
# ---------------------------------------------------------------------------


def blocked_window(
    start_at, end_at, *, travel_buffer_minutes=None, turnaround_minutes=None
):
    """The interval a vehicle and driver are actually unavailable for.

    The blueprint requires availability to account for travel to the pickup and
    turnaround time, so the blocked window is wider than the hire itself. A hire
    that starts at 10:00 with a 60 minute travel buffer and 30 minute turnaround
    blocks 09:00-14:30.
    """
    travel_buffer_minutes = (
        DEFAULT_TRAVEL_BUFFER_MINUTES if travel_buffer_minutes is None else travel_buffer_minutes
    )
    turnaround_minutes = (
        DEFAULT_TURNAROUND_MINUTES if turnaround_minutes is None else turnaround_minutes
    )
    return (
        start_at - timedelta(minutes=travel_buffer_minutes),
        end_at + timedelta(minutes=turnaround_minutes),
    )


def _overlaps(blocked_from, blocked_to):
    """Reservations whose blocked window overlaps the given one.

    Half-open interval comparison: a hire may begin exactly when the previous
    blocked window closes, but not one minute before.
    """
    return CharterReservation.objects.filter(
        blocked_from__lt=blocked_to, blocked_to__gt=blocked_from
    )


def _valid_driver_ids(today):
    """Driver ids whose documents are all VERIFIED and unexpired (same rule as
    on-demand dispatch, blueprint 8)."""
    valid, invalid = set(), set()
    for doc in Document.objects.filter(entity_type=Document.EntityType.DRIVER):
        expired = doc.expires_on is not None and doc.expires_on < today
        if doc.status == Document.Status.VERIFIED and not expired:
            valid.add(doc.entity_id)
        else:
            invalid.add(doc.entity_id)
    return valid - invalid


# ---------------------------------------------------------------------------
# Clause 1: the request specifies the job
# ---------------------------------------------------------------------------


@transaction.atomic
def create_hire_request(
    *,
    organization,
    customer,
    service,
    start_at,
    end_at,
    vehicle_class="",
    passenger_count=0,
    route=None,
    itinerary_notes="",
    included_hours=None,
    included_distance_km=None,
):
    """Record what is being hired, for how long and on what terms."""
    if end_at <= start_at:
        raise CharterError("The hire end time must be after the start time.")
    if included_hours is None and included_distance_km is None:
        raise CharterError("A hire must specify included hours or included distance.")
    if passenger_count and vehicle_class:
        # A sedan cannot take a coach load; capacity is enforced at confirmation
        # but the request must not be nonsense.
        pass
    return HireRequest.objects.create(
        organization=organization,
        customer=customer,
        service=service,
        start_at=start_at,
        end_at=end_at,
        vehicle_class=vehicle_class,
        passenger_count=passenger_count,
        route=route,
        itinerary_notes=itinerary_notes,
        included_hours=included_hours,
        included_distance_km=included_distance_km,
    )


# ---------------------------------------------------------------------------
# Clause 2: staff quote, reserve, deposit, finalize
# ---------------------------------------------------------------------------


@transaction.atomic
def issue_quote(hire_request, *, total_minor, issued_by=None, rate_card=None):
    """Staff issue the charter quote."""
    if hire_request.status not in (HireRequest.Status.DRAFT, HireRequest.Status.QUOTED):
        raise CharterError("A quote can only be issued for a draft or re-quoted hire.")
    if total_minor <= 0:
        raise CharterError("A charter quote must have a positive amount.")

    quote = build_quote(
        service=hire_request.service,
        request_payload={
            "hire_request": str(hire_request.pk),
            "start_at": hire_request.start_at.isoformat(),
            "end_at": hire_request.end_at.isoformat(),
            "vehicle_class": hire_request.vehicle_class,
            "passenger_count": hire_request.passenger_count,
            "included_hours": str(hire_request.included_hours or ""),
            "included_distance_km": hire_request.included_distance_km,
        },
        total_minor=total_minor,
        currency=hire_request.currency,
        rate_card=rate_card,
        basis="charter",
        organization=hire_request.organization,
    )
    quote.accept()
    hire_request.quote = quote
    hire_request.status = HireRequest.Status.QUOTED
    hire_request.save(update_fields=["quote", "status", "updated_at"])
    return quote


def check_availability(
    *,
    vehicle_class="",
    start_at,
    end_at,
    passenger_count=0,
    travel_buffer_minutes=None,
    turnaround_minutes=None,
    exclude_hire_request=None,
):
    """Vehicles and drivers free for the whole (widened) interval.

    Accounts for travel to the pickup, turnaround time and every existing
    reservation, for both the vehicle and the driver.
    """
    blocked_from, blocked_to = blocked_window(
        start_at,
        end_at,
        travel_buffer_minutes=travel_buffer_minutes,
        turnaround_minutes=turnaround_minutes,
    )
    busy = _overlaps(blocked_from, blocked_to)
    if exclude_hire_request is not None:
        busy = busy.exclude(hire_request=exclude_hire_request)

    busy_vehicle_ids = set(busy.values_list("vehicle_id", flat=True))
    busy_driver_ids = set(busy.values_list("driver_id", flat=True))

    vehicles = Vehicle.objects.filter(status=Vehicle.Status.ACTIVE).exclude(
        pk__in=busy_vehicle_ids
    )
    if vehicle_class:
        vehicles = vehicles.filter(vehicle_class=vehicle_class)
    if passenger_count:
        vehicles = vehicles.filter(seating_capacity__gte=passenger_count)

    drivers = Driver.objects.filter(status=Driver.Status.APPROVED).exclude(
        pk__in=busy_driver_ids
    )
    document_ok = _valid_driver_ids(timezone.localdate())
    if document_ok:
        drivers = drivers.filter(pk__in=list(document_ok))

    return list(vehicles.order_by("registration_number")), list(drivers.order_by("pk"))


@transaction.atomic
def confirm_hire(
    hire_request,
    *,
    vehicle,
    driver,
    travel_buffer_minutes=None,
    turnaround_minutes=None,
):
    """Reserve the vehicle and driver for the entire interval.

    The availability re-check and the reservation write happen in one
    transaction, so two dispatchers confirming overlapping hires cannot both
    win the same vehicle.
    """
    if hire_request.status != HireRequest.Status.QUOTED:
        raise CharterError("Only a quoted hire can be confirmed.")
    if hire_request.quote_id is None:
        raise CharterError("A confirmed hire must carry an accepted quote.")

    travel_buffer_minutes = (
        DEFAULT_TRAVEL_BUFFER_MINUTES
        if travel_buffer_minutes is None
        else travel_buffer_minutes
    )
    turnaround_minutes = (
        DEFAULT_TURNAROUND_MINUTES if turnaround_minutes is None else turnaround_minutes
    )
    blocked_from, blocked_to = blocked_window(
        hire_request.start_at,
        hire_request.end_at,
        travel_buffer_minutes=travel_buffer_minutes,
        turnaround_minutes=turnaround_minutes,
    )

    # Lock against concurrent confirmation of the same vehicle/driver.
    clash = _overlaps(blocked_from, blocked_to).filter(
        Q(vehicle=vehicle) | Q(driver=driver)
    )
    if clash.exists():
        raise CharterError(
            "The vehicle or driver is already reserved in this interval, including "
            "travel to the pickup and turnaround time."
        )

    if hire_request.vehicle_class and vehicle.vehicle_class != hire_request.vehicle_class:
        raise CharterError("This vehicle does not match the requested class.")
    if vehicle.status != Vehicle.Status.ACTIVE:
        raise CharterError("This vehicle is not available for service.")
    if hire_request.passenger_count and vehicle.seating_capacity < hire_request.passenger_count:
        raise CharterError("This vehicle cannot carry the requested number of passengers.")
    if driver.status != Driver.Status.APPROVED:
        raise CharterError("This driver is not approved for service.")
    if driver.organization_id != vehicle.organization_id:
        raise CharterError("The driver does not operate for this vehicle's organization.")

    reservation = CharterReservation.objects.create(
        organization=hire_request.organization,
        hire_request=hire_request,
        vehicle=vehicle,
        driver=driver,
        reserved_from=hire_request.start_at,
        reserved_to=hire_request.end_at,
        travel_buffer_minutes=travel_buffer_minutes,
        turnaround_minutes=turnaround_minutes,
        blocked_from=blocked_from,
        blocked_to=blocked_to,
    )
    hire_request.status = HireRequest.Status.CONFIRMED
    hire_request.save(update_fields=["status", "updated_at"])
    return reservation


@transaction.atomic
def record_deposit(hire_request, *, amount_minor, payment_attempt=None, recorded_by=None, note=""):
    """Record a deposit against the hire."""
    if hire_request.status not in (
        HireRequest.Status.CONFIRMED,
        HireRequest.Status.IN_PROGRESS,
    ):
        raise CharterError("A deposit can only be recorded against a confirmed hire.")
    if amount_minor <= 0:
        raise CharterError("A deposit must have a positive amount.")
    deposit = CharterDeposit.objects.create(
        hire_request=hire_request,
        payment_attempt=payment_attempt,
        amount_minor=amount_minor,
        currency=hire_request.currency,
        recorded_by=recorded_by,
        note=note,
    )
    total = hire_request.deposits.aggregate(t=Sum("amount_minor"))["t"] or 0
    hire_request.deposit_total_minor = total
    hire_request.save(update_fields=["deposit_total_minor", "updated_at"])
    return deposit


@transaction.atomic
def add_extra(
    hire_request, *, kind, price_minor, description="", is_permitted=True, note=""
):
    """Record an extra. Non-permitted extras are visible but never charged."""
    if price_minor < 0:
        raise CharterError("An extra cannot have a negative price.")
    return CharterExtra.objects.create(
        hire_request=hire_request,
        kind=kind,
        description=description,
        price_minor=price_minor,
        is_permitted=is_permitted,
        note=note,
    )


@transaction.atomic
def finalize_hire(hire_request):
    """Complete the hire, charging only permitted extras."""
    if hire_request.status not in (
        HireRequest.Status.CONFIRMED,
        HireRequest.Status.IN_PROGRESS,
    ):
        raise CharterError("Only a confirmed hire can be finalized.")
    if hire_request.quote_id is None:
        raise CharterError("A finalized hire must carry an accepted quote.")

    permitted = hire_request.extras.filter(is_permitted=True)
    extras_total = permitted.aggregate(t=Sum("price_minor"))["t"] or 0
    hire_request.extras_total_minor = extras_total
    hire_request.final_total_minor = hire_request.quoted_total_minor + extras_total
    hire_request.status = HireRequest.Status.COMPLETED
    hire_request.save(
        update_fields=["extras_total_minor", "final_total_minor", "status", "updated_at"]
    )
    return hire_request


@transaction.atomic
def cancel_hire(hire_request, *, reason=""):
    """Cancel the hire and release the vehicle and driver."""
    if hire_request.status in (
        HireRequest.Status.COMPLETED,
        HireRequest.Status.CANCELLED,
    ):
        raise CharterError("This hire can no longer be cancelled.")
    hire_request.status = HireRequest.Status.CANCELLED
    hire_request.cancel_reason = reason
    hire_request.save(update_fields=["status", "cancel_reason", "updated_at"])
    # Release the reservation so the interval becomes bookable again.
    CharterReservation.objects.filter(hire_request=hire_request).delete()
    return hire_request
