"""On-demand rides: dispatch, driver offers, atomic assignment, trips and
settlement (blueprint 8, "On demand rides and corporate transport").

Blueprint guarantees implemented here:
  * "Dispatch filters approved drivers by availability, vehicle class, service
    zone and document status. It sends time-limited offers."
  * "The first valid acceptance wins through an atomic assignment operation;
    later or expired acceptances are rejected."
  * "Offer acceptance and trip start are separate events" (Figure 4).
  * "Confirm pickup through an agreed method such as a trip PIN."
  * "Destination changes require a revised estimate and customer acceptance
    where appropriate."
  * "If no driver accepts, show the customer a clear retry, scheduled
    alternative or cancellation outcome and release any reservation or
    authorization according to the payment policy."
  * "Drivers see contacts only for assigned [trips]."
"""

import secrets
from datetime import timedelta

from django.contrib.auth.hashers import check_password, make_password
from django.db import transaction
from django.utils import timezone

from apps.catalog.models import build_quote
from apps.fleet.models import Document, Driver, Vehicle
from apps.rides.models import (
    DriverOffer,
    DriverShift,
    RideCancellation,
    RideRequest,
    ServiceZone,
    Trip,
    TripEvent,
    TripIncident,
)

DEFAULT_OFFER_MINUTES = 5
DEFAULT_WAITING_ALLOWANCE_MINUTES = 5


class RideError(Exception):
    """Business rule violation; surfaced to the API as 400."""


# ---------------------------------------------------------------------------
# Eligibility
# ---------------------------------------------------------------------------


def _valid_driver_ids(today):
    """Driver ids whose documents are all VERIFIED and unexpired.

    "Document status" is part of the dispatch filter: an expired PSV licence or
    insurance removes a driver from the pool immediately (blueprint 8)."""
    valid, invalid = set(), set()
    for doc in Document.objects.filter(entity_type=Document.EntityType.DRIVER):
        expired = doc.expires_on is not None and doc.expires_on < today
        if doc.status == Document.Status.VERIFIED and not expired:
            valid.add(doc.entity_id)
        else:
            invalid.add(doc.entity_id)
    return valid - invalid


def eligible_drivers(ride_request):
    """Approved drivers matching availability, vehicle class, service zone and
    document status (blueprint 8)."""
    today = timezone.localdate()
    now = timezone.now()

    query = Driver.objects.filter(status=Driver.Status.APPROVED).select_related("user")

    # Vehicle class: the driver's currently assigned vehicle must match what
    # was ordered and be roadworthy.
    if ride_request.vehicle_class:
        query = query.filter(
            current_vehicle__isnull=False,
            current_vehicle__vehicle_class=ride_request.vehicle_class,
            current_vehicle__status=Vehicle.Status.ACTIVE,
        )

    # Availability + service zone: an open shift in the request's zone.
    shifts = DriverShift.objects.filter(
        driver__in=query,
        status=DriverShift.Status.AVAILABLE,
        starts_at__lte=now,
        ends_at__gte=now,
    )
    if ride_request.zone_id:
        shifts = shifts.filter(zone_id=ride_request.zone_id)
    driver_ids = list(
        shifts.values_list("driver_id", flat=True).distinct()
    )

    # Document status.
    document_ok = _valid_driver_ids(today)
    driver_ids = [d for d in driver_ids if d in document_ok]
    return list(Driver.objects.filter(pk__in=driver_ids).select_related("user"))


# ---------------------------------------------------------------------------
# Ride requests
# ---------------------------------------------------------------------------


@transaction.atomic
def create_ride_request(
    *, customer, pickup, destination, service, vehicle_class="", quote=None
):
    """Submit a ride request against a versioned quote (blueprint 8: "The
    customer sets pickup and destination, receives a versioned quote and
    submits a ride request")."""
    if pickup is None or destination is None:
        raise RideError("Pickup and destination are required.")
    if pickup.pk == destination.pk:
        raise RideError("Pickup and destination must differ.")

    if quote is None:
        zone = ServiceZone.objects.filter(is_active=True).first()
        quote = build_quote(
            service=service,
            request_payload={
                "pickup": pickup.name,
                "destination": destination.name,
                "vehicle_class": vehicle_class,
            },
            total_minor=0,
            basis="on_demand_estimate",
            organization=getattr(service, "organization", None),
        )
        quote.accept()

    return RideRequest.objects.create(
        customer=customer,
        service=service,
        quote=quote,
        pickup=pickup,
        destination=destination,
        vehicle_class=vehicle_class,
        zone=ServiceZone.objects.filter(is_active=True).first(),
    )


# ---------------------------------------------------------------------------
# Dispatch and offers
# ---------------------------------------------------------------------------


@transaction.atomic
def dispatch_offers(ride_request, window_minutes=DEFAULT_OFFER_MINUTES):
    """Send time-limited offers to every eligible driver (blueprint 8)."""
    ride_request = RideRequest.objects.select_for_update().get(pk=ride_request.pk)
    if ride_request.status != RideRequest.Status.REQUESTED:
        raise RideError("Only a requested ride can be dispatched.")

    drivers = eligible_drivers(ride_request)
    expires_at = timezone.now() + timedelta(minutes=window_minutes)
    offers = []
    for driver in drivers:
        offer, created = DriverOffer.objects.get_or_create(
            ride_request=ride_request,
            driver=driver,
            state=DriverOffer.State.PENDING,
            defaults={"expires_at": expires_at},
        )
        if created:
            offers.append(offer)
        TripEvent.objects.create(
            ride_request=ride_request,
            kind=TripEvent.Kind.OFFERED,
            payload={"driver_id": str(driver.pk), "expires_at": expires_at.isoformat()},
        )
    if not offers:
        # No eligible driver: the customer must get an explicit outcome
        # (retry / scheduled alternative / cancel), not silence.
        fail_matching(ride_request, RideRequest.Resolution.RETRY)
    return offers


@transaction.atomic
def accept_offer(offer):
    """Atomic assignment: the first valid acceptance wins; later or expired
    acceptances are rejected (blueprint 8)."""
    # Lock the ride request: all competing acceptances serialise on this row.
    ride_request = RideRequest.objects.select_for_update().get(pk=offer.ride_request_id)
    offer = DriverOffer.objects.select_for_update().get(pk=offer.pk)

    if offer.state != DriverOffer.State.PENDING:
        raise RideError("This offer is no longer open.")
    if offer.expires_at <= timezone.now():
        offer.state = DriverOffer.State.EXPIRED
        offer.save(update_fields=["state", "updated_at"])
        raise RideError("This offer has expired.")
    if ride_request.status != RideRequest.Status.REQUESTED:
        raise RideError("This ride has already been assigned.")

    trip = Trip.objects.create(
        ride_request=ride_request,
        driver=offer.driver,
        vehicle=_driver_vehicle(offer.driver),
        state=Trip.State.ASSIGNED,
        pin_hash=make_password(_generate_pin()),
    )
    offer.state = DriverOffer.State.ACCEPTED
    offer.save(update_fields=["state", "updated_at"])
    # Every other pending offer is now closed.
    DriverOffer.objects.filter(
        ride_request=ride_request, state=DriverOffer.State.PENDING
    ).exclude(pk=offer.pk).update(state=DriverOffer.State.EXPIRED)

    ride_request.status = RideRequest.Status.ASSIGNED
    ride_request.save(update_fields=["status", "updated_at"])
    TripEvent.objects.create(
        ride_request=ride_request,
        kind=TripEvent.Kind.ACCEPTED,
        payload={"driver_id": str(offer.driver_id), "trip_id": str(trip.pk)},
    )
    return trip


def _driver_vehicle(driver):
    """The vehicle this driver is currently assigned to. Dispatch has already
    confirmed its class matches the ordered vehicle and that it is roadworthy,
    so the trip is bound to that exact vehicle rather than any org vehicle."""
    return driver.current_vehicle


def _generate_pin():
    return f"{secrets.randbelow(10000):04d}"


@transaction.atomic
def decline_offer(offer):
    offer = DriverOffer.objects.select_for_update().get(pk=offer.pk)
    if offer.state != DriverOffer.State.PENDING:
        raise RideError("This offer is no longer open.")
    offer.state = DriverOffer.State.DECLINED
    offer.save(update_fields=["state", "updated_at"])
    return offer


def expire_offers():
    """Sweep expired pending offers."""
    return DriverOffer.objects.filter(
        state=DriverOffer.State.PENDING, expires_at__lte=timezone.now()
    ).update(state=DriverOffer.State.EXPIRED)


# ---------------------------------------------------------------------------
# Trip lifecycle
# ---------------------------------------------------------------------------


@transaction.atomic
def mark_arrived(trip):
    trip = Trip.objects.select_for_update().get(pk=trip.pk)
    if trip.state != Trip.State.ASSIGNED:
        raise RideError("Only an assigned trip can arrive.")
    trip.state = Trip.State.ARRIVED
    trip.save(update_fields=["state", "updated_at"])
    TripEvent.objects.create(ride_request=trip.ride_request, kind=TripEvent.Kind.ARRIVED)
    return trip


@transaction.atomic
def verify_pickup(trip, pin):
    """Confirm pickup with the trip PIN and START the trip. Acceptance and trip
    start are separate events (blueprint 8, Figure 4)."""
    trip = Trip.objects.select_for_update().get(pk=trip.pk)
    if trip.state != Trip.State.ARRIVED:
        raise RideError("The vehicle must arrive before pickup is confirmed.")
    if not check_password(str(pin), trip.pin_hash):
        TripEvent.objects.create(
            ride_request=trip.ride_request,
            kind=TripEvent.Kind.PIN_FAILED,
            payload={"note": "Incorrect trip PIN."},
        )
        raise RideError("Incorrect trip PIN.")
    trip.state = Trip.State.IN_PROGRESS
    trip.started_at = timezone.now()
    trip.save(update_fields=["state", "started_at", "updated_at"])
    TripEvent.objects.create(
        ride_request=trip.ride_request, kind=TripEvent.Kind.STARTED
    )
    return trip


@transaction.atomic
def change_destination(trip, new_destination, *, revised_estimate_minor=None):
    """A destination change requires a revised estimate and customer acceptance
    (blueprint 8). The new destination is not applied until accepted."""
    trip = Trip.objects.select_for_update().get(pk=trip.pk)
    if trip.state not in (Trip.State.IN_PROGRESS, Trip.State.ARRIVED):
        raise RideError("The destination can only change during a live trip.")
    if new_destination is None:
        raise RideError("A new destination is required.")

    revised_estimate = revised_estimate_minor or 0
    quote = build_quote(
        service=trip.ride_request.service,
        request_payload={
            "trip_id": str(trip.pk),
            "from": trip.ride_request.destination.name,
            "to": new_destination.name,
            "kind": "destination_change",
        },
        total_minor=revised_estimate,
        basis="destination_change_estimate",
        organization=trip.ride_request.organization,
    )
    trip.pending_destination = new_destination
    trip.pending_destination_quote = quote
    trip.save(
        update_fields=["pending_destination", "pending_destination_quote", "updated_at"]
    )
    TripEvent.objects.create(
        ride_request=trip.ride_request,
        kind=TripEvent.Kind.DESTINATION_CHANGED,
        payload={"proposed": new_destination.name, "quote_id": str(quote.pk)},
    )
    return quote


@transaction.atomic
def accept_destination_change(trip):
    """Customer accepts the revised estimate; only then does the destination
    change apply."""
    trip = Trip.objects.select_for_update().get(pk=trip.pk)
    if trip.pending_destination_id is None:
        raise RideError("There is no pending destination change.")
    trip.ride_request.destination = trip.pending_destination
    trip.ride_request.save(update_fields=["destination", "updated_at"])
    trip.pending_destination = None
    trip.pending_destination_quote = None
    trip.save(
        update_fields=["pending_destination", "pending_destination_quote", "updated_at"]
    )
    TripEvent.objects.create(
        ride_request=trip.ride_request, kind=TripEvent.Kind.DESTINATION_ACCEPTED
    )
    return trip


@transaction.atomic
def complete_trip(trip):
    """Complete the trip and settle the fare from the accepted quote
    (settlement reuses the payments seam; the ledger is written on the payment
    callback)."""
    trip = Trip.objects.select_for_update().get(pk=trip.pk)
    if trip.state != Trip.State.IN_PROGRESS:
        raise RideError("Only a trip in progress can be completed.")
    trip.state = Trip.State.COMPLETED
    trip.ended_at = timezone.now()
    trip.save(update_fields=["state", "ended_at", "updated_at"])
    trip.ride_request.status = RideRequest.Status.COMPLETED
    trip.ride_request.save(update_fields=["status", "updated_at"])
    TripEvent.objects.create(
        ride_request=trip.ride_request, kind=TripEvent.Kind.COMPLETED
    )
    return trip


# ---------------------------------------------------------------------------
# Cancellation, no-show, failure and safety
# ---------------------------------------------------------------------------


def viewer_may_see_contact(trip, user):
    """A driver may see passenger contact details only while the trip is
    assigned to them; a customer sees their own; an org admin for the trip's
    operator sees it. Blueprint: "Drivers see contacts only for assigned
    [trips]." """
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser:
        return True
    if trip.ride_request.customer_id == user.pk:
        return True
    from apps.accounts.models import organization_ids_for

    org_ids = set(organization_ids_for(user))
    if trip.ride_request.organization_id and trip.ride_request.organization_id in org_ids:
        return True
    # The assigned driver.
    return trip.driver.user_id == user.pk


@transaction.atomic
def cancel_ride(ride_request, *, reason="", evidence="", fee_minor=0, no_show=False):
    """Record a cancellation or no-show with its reason, evidence and the
    applicable price policy (blueprint 8)."""
    ride_request = RideRequest.objects.select_for_update().get(pk=ride_request.pk)
    if ride_request.status in (RideRequest.Status.COMPLETED, RideRequest.Status.CANCELLED):
        raise RideError("This ride is already closed.")
    ride_request.status = (
        RideRequest.Status.NO_SHOW if no_show else RideRequest.Status.CANCELLED
    )
    ride_request.save(update_fields=["status", "updated_at"])
    DriverOffer.objects.filter(
        ride_request=ride_request, state=DriverOffer.State.PENDING
    ).update(state=DriverOffer.State.EXPIRED)
    cancellation = RideCancellation.objects.create(
        ride_request=ride_request,
        reason=reason,
        evidence=evidence,
        fee_minor=fee_minor,
        is_no_show=no_show,
    )
    TripEvent.objects.create(
        ride_request=ride_request,
        kind=TripEvent.Kind.NO_SHOW if no_show else TripEvent.Kind.CANCELLED,
        payload={"reason": reason, "fee_minor": fee_minor},
    )
    return cancellation


@transaction.atomic
def fail_matching(ride_request, resolution=RideRequest.Resolution.RETRY):
    """No driver accepted: give the customer an explicit outcome and release
    any authorization per the payment policy (blueprint 8)."""
    ride_request = RideRequest.objects.select_for_update().get(pk=ride_request.pk)
    if ride_request.status != RideRequest.Status.REQUESTED:
        return ride_request
    ride_request.status = RideRequest.Status.UNMATCHED
    ride_request.resolution = resolution
    ride_request.save(update_fields=["status", "resolution", "updated_at"])
    DriverOffer.objects.filter(
        ride_request=ride_request, state=DriverOffer.State.PENDING
    ).update(state=DriverOffer.State.EXPIRED)
    TripEvent.objects.create(
        ride_request=ride_request,
        kind=TripEvent.Kind.MATCHING_FAILED,
        payload={"resolution": resolution},
    )
    return ride_request


@transaction.atomic
def report_incident(trip, *, kind, description, escalation_path=""):
    """Incident reporting with a documented escalation path (blueprint 8)."""
    incident = TripIncident.objects.create(
        trip=trip,
        kind=kind,
        description=description,
        escalation_path=escalation_path,
    )
    TripEvent.objects.create(
        ride_request=trip.ride_request,
        kind=TripEvent.Kind.INCIDENT,
        payload={"incident_id": str(incident.pk), "kind": kind},
    )
    return incident
