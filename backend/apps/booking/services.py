"""Booking services: atomic timed seat holds, hold->ticket conversion, and
boarding (blueprint 7, "Scheduled passenger booking").

The blueprint's hard requirements implemented here:
  * "Seat inventory is held against the departure and occupied route
    segments."
  * "The server enforces hold expiry and seat uniqueness within a transaction.
    It must reject overlapping segment reservations even if two customers
    click at the same instant. PostgreSQL supports row locking and
    transactional control for this purpose."
  * "For group bookings, acquire the requested seats together or return a
    clear partial-availability response before payment."
  * "Payment success and seat availability are evaluated together before
    issuing a confirmed ticket."
  * "A replacement vehicle with fewer seats creates an exception requiring
    reassignment and customer communication; it must never silently remove
    passengers."
"""

import secrets
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.accounts.phones import normalize_phone
from apps.booking.models import (
    BoardingScan,
    Departure,
    DepartureException,
    SeatHold,
    SeatHoldSegment,
    Segment,
    Ticket,
)
from apps.catalog.models import build_quote
from apps.common.outbox import record_outbox
from apps.orders.models import Order

DEFAULT_HOLD_MINUTES = 10


class BookingError(Exception):
    """Business rule violation; surfaced to the API as 400."""


class NoAvailability(BookingError):
    pass


def build_segments(departure):
    """Create the ordered legs (segments) between consecutive route stops."""
    stops = list(departure.route.stops.select_related("place").order_by("position"))
    if len(stops) < 2:
        raise BookingError("A route needs at least two stops to run a departure.")
    segments = []
    for index in range(len(stops) - 1):
        segment, _ = Segment.objects.get_or_create(
            departure=departure,
            position=index,
            defaults={
                "from_place": stops[index].place,
                "to_place": stops[index + 1].place,
            },
        )
        segments.append(segment)
    return segments


def _resolve_segment_positions(departure, from_place, to_place):
    """Translate an origin/destination pair into the contiguous run of segment
    positions between them (blueprint: Nairobi->Nakuru->Eldoret sells
    non-overlapping segments independently)."""
    segments = list(departure.segments.order_by("position"))
    if not segments:
        segments = build_segments(departure)
    positions = []
    found_from = False
    reached = False
    for segment in segments:
        if not found_from and segment.from_place_id == from_place.pk:
            found_from = True
        if found_from:
            positions.append(segment.position)
        if found_from and segment.to_place_id == to_place.pk:
            reached = True
            break
    if not reached:
        raise BookingError(
            "The destination is not reachable from the origin on this route."
        )
    if not positions:
        raise BookingError("Origin and destination must differ and follow the route order.")
    return positions


def _occupied_seat_numbers(departure, segment_positions):
    """Seat numbers already committed on ANY of these segments, by a still-valid
    hold or by a confirmed (issued/boarded) ticket. A ticket inherits the legs
    of the hold it was issued from, so a converted hold is still counted."""
    positions = list(segment_positions)
    hold_seats = set(
        SeatHold.objects.filter(
            departure=departure,
            state=SeatHold.State.VALID,
            expires_at__gt=timezone.now(),
            segments__segment__position__in=positions,
        ).values_list("seat_number", flat=True)
    )
    ticket_seats = set(
        Ticket.objects.filter(
            departure=departure,
            state__in=[Ticket.State.ISSUED, Ticket.State.BOARDED],
            hold__segments__segment__position__in=positions,
        ).values_list("seat_number", flat=True)
    )
    return hold_seats | ticket_seats


def available_seats(departure, from_place, to_place):
    """(sellable seat numbers, segment positions) for the requested leg."""
    positions = _resolve_segment_positions(departure, from_place, to_place)
    taken = _occupied_seat_numbers(departure, positions)
    capacity = departure.capacity or 0
    return [seat for seat in range(1, capacity + 1) if seat not in taken], positions


def search_departures(*, origin_name="", destination_name="", date=None):
    """Blueprint search: route, date and passengers -> available departures."""
    qs = (
        Departure.objects.filter(status=Departure.Status.SCHEDULED)
        .select_related("route", "vehicle")
        .order_by("departure_date", "departure_time")
    )
    if date is not None:
        qs = qs.filter(departure_date=date)
    if origin_name:
        qs = qs.filter(route__stops__place__name__icontains=origin_name)
    if destination_name:
        qs = qs.filter(route__stops__place__name__icontains=destination_name)
    return qs.distinct()


@transaction.atomic
def acquire_hold(
    *,
    departure,
    from_place,
    to_place,
    seats=1,
    hold_minutes=DEFAULT_HOLD_MINUTES,
    customer=None,
):
    """Create an atomic, timed seat hold for the requested seats.

    The departure row is locked for the duration so two simultaneous clicks
    cannot both claim the same seat on overlapping segments. Group bookings
    acquire all requested seats together or raise NoAvailability (a clear
    partial-availability response before payment)."""
    # Row lock serialises concurrent holds on this departure (PostgreSQL).
    departure = Departure.objects.select_for_update().get(pk=departure.pk)
    if departure.status != Departure.Status.SCHEDULED:
        raise BookingError("This departure is not open for booking.")

    positions = _resolve_segment_positions(departure, from_place, to_place)
    taken = _occupied_seat_numbers(departure, positions)
    capacity = departure.capacity or 0
    free = [seat for seat in range(1, capacity + 1) if seat not in taken]
    if len(free) < seats:
        raise NoAvailability(
            f"Only {len(free)} seat(s) available for this leg; {seats} requested."
        )

    expires_at = timezone.now() + timedelta(minutes=hold_minutes)
    segments_by_position = {s.position: s for s in departure.segments.all()}
    holds = []
    for seat in free[:seats]:
        hold = SeatHold.objects.create(
            departure=departure,
            customer=customer,
            seat_number=seat,
            state=SeatHold.State.VALID,
            expires_at=expires_at,
        )
        for position in positions:
            SeatHoldSegment.objects.create(
                hold=hold, segment=segments_by_position[position]
            )
        holds.append(hold)
    return holds


def _new_ticket_credentials():
    reference = secrets.token_hex(4).upper()  # random human identifier
    token = secrets.token_urlsafe(32)  # scan token
    return reference, token


@transaction.atomic
def create_booking_order(
    *,
    departure,
    holds,
    passenger_name,
    passenger_phone,
    passenger_id_number="",
    customer=None,
):
    """Turn held seats into a pay-ready Order for a departure.

    Prices the whole group at the departure's per-seat fare, snapshots the
    passenger on the order (the ticket copies it at conversion), and links the
    holds so the payment callback can find them. The order stays CREATED until
    payment succeeds; the ticket is only issued on a successful callback."""
    departure = Departure.objects.select_for_update().get(pk=departure.pk)
    if departure.service_id is None:
        raise BookingError("This departure is not linked to a bookable service.")
    holds = list(SeatHold.objects.select_for_update().filter(pk__in=[h.pk for h in holds]))
    if len(holds) != len(set(h.pk for h in holds)):
        raise BookingError("Duplicate seat hold in request.")
    customer = customer or next((h.customer for h in holds if h.customer_id), None)
    if customer is None:
        raise BookingError("A booking customer is required.")
    for hold in holds:
        if hold.departure_id != departure.pk:
            raise BookingError("Seat hold belongs to a different departure.")
        if not hold.is_valid():
            raise BookingError("A seat hold has expired; re-select your seats.")

    phone = normalize_phone(passenger_phone)
    if not phone:
        raise BookingError("A valid Kenyan passenger phone number is required.")

    total_minor = departure.fare_minor * len(holds)
    quote = build_quote(
        service=departure.service,
        request_payload={
            "departure": str(departure.pk),
            "route": departure.route.name,
            "seats": [h.seat_number for h in holds],
        },
        total_minor=total_minor,
        currency=departure.currency,
        basis="per_seat",
        organization=departure.organization,
    )
    quote.accept()

    order = Order.objects.create(
        organization=departure.organization,
        customer=customer,
        service=departure.service,
        quote=quote,
        currency=departure.currency,
        total_minor=total_minor,
        service_detail={
            "kind": "scheduled_passenger_booking",
            "departure": str(departure.pk),
            "passenger": {
                "name": passenger_name,
                "phone": phone,
                "id_number": passenger_id_number,
            },
        },
    )
    for hold in holds:
        hold.order = order
        hold.save(update_fields=["order", "updated_at"])
    return order


@transaction.atomic
def issue_ticket_for_order(order):
    """Convert a paid order's valid seat hold(s) into confirmed tickets.

    Invoked from the payment callback's atomic block, so a ticket is issued
    only when payment succeeded AND the seat is still held. Returns
    ``(tickets, expired_holds)``. An expired hold yields NO ticket; the caller
    (payment hook) is expected to route a rebook/refund without discarding the
    recorded collection."""
    tickets = []
    expired_holds = []
    for hold in SeatHold.objects.select_for_update().filter(
        order=order, state=SeatHold.State.VALID
    ):
        if not hold.is_valid():
            hold.state = SeatHold.State.EXPIRED
            hold.save(update_fields=["state", "updated_at"])
            expired_holds.append(hold)
            continue
        reference, token = _new_ticket_credentials()
        detail = order.service_detail or {}
        passenger = detail.get("passenger", {})
        ticket = Ticket.objects.create(
            departure=hold.departure,
            hold=hold,
            reference=reference,
            token=token,
            seat_number=hold.seat_number,
            passenger_name=passenger.get("name", ""),
            passenger_phone=passenger.get("phone", ""),
            passenger_id_number=passenger.get("id_number", ""),
            state=Ticket.State.ISSUED,
        )
        hold.state = SeatHold.State.CONVERTED
        hold.save(update_fields=["state", "updated_at"])
        tickets.append(ticket)
    return tickets, expired_holds


@transaction.atomic
def board_from_scan(
    *, ticket_reference="", token="", departure=None, scanner_device="", note=""
):
    """Scan a ticket and mark boarded. Checks departure, ticket state and
    previous boarding (blueprint 7). Raises on a double board so a duplicate is
    visible, not silently accepted."""
    qs = Ticket.objects.select_for_update()
    if token:
        ticket = qs.filter(token=token).first()
    else:
        ticket = qs.filter(reference__iexact=ticket_reference).first()
    if ticket is None:
        raise BookingError("Ticket not found.")

    if departure is not None and ticket.departure_id != departure.pk:
        raise BookingError("Ticket is for a different departure.")

    if ticket.state == Ticket.State.BOARDED:
        raise BookingError("Ticket has already been boarded.")
    if ticket.state in (Ticket.State.CANCELLED, Ticket.State.REFUNDED):
        raise BookingError(f"Ticket is {ticket.get_state_display().lower()}.")

    ticket.state = Ticket.State.BOARDED
    ticket.boarded_at = timezone.now()
    ticket.save(update_fields=["state", "boarded_at", "updated_at"])
    BoardingScan.objects.create(
        ticket=ticket,
        departure=ticket.departure,
        boarded=True,
        scanner_device=scanner_device,
        note=note,
    )
    # Boarding is the performance obligation being satisfied for that seat, so
    # the order that paid for it is settled here. The event carries the order
    # the payments side recognises revenue against; a ticket whose hold has
    # been cleared (order link lost) boards but cannot settle an order.
    order = ticket.hold.order if ticket.hold_id and ticket.hold.order_id else None
    if order is not None:
        if not Ticket.objects.filter(
            hold__order=order, state=Ticket.State.ISSUED
        ).exists():
            _complete_boarded_order(order)
        record_outbox(
            event_type="booking.ticket_boarded",
            aggregate_type="ticket",
            aggregate_id=ticket.pk,
            organization_id=order.organization_id,
            payload={
                "ticket_id": str(ticket.pk),
                "order_id": str(order.pk),
                "departure_id": str(ticket.departure_id),
            },
        )
    return ticket


def _complete_boarded_order(order):
    """Complete an order once every seat it paid for has boarded.

    A cancellation without a refund is judged as money kept rather than a
    service owed, so it does not block completion; a seat still ISSUED -- still
    to be boarded -- does."""
    if order.status != Order.Status.CONFIRMED:
        return
    order.complete()


@transaction.atomic
def handle_vehicle_substitution(*, departure, new_vehicle):
    """Swap a departure's vehicle. If the replacement has fewer seats than
    confirmed tickets, create reassignment exceptions requiring customer
    communication — never silently drop passengers (blueprint 7)."""
    departure = Departure.objects.select_for_update().get(pk=departure.pk)
    new_capacity = new_vehicle.seating_capacity or 0
    departure.vehicle = new_vehicle
    departure.vehicle_class = new_vehicle.vehicle_class
    departure.capacity = new_capacity
    departure.save(update_fields=["vehicle", "vehicle_class", "capacity", "updated_at"])

    confirmed = list(
        Ticket.objects.filter(departure=departure, state=Ticket.State.ISSUED).order_by(
            "seat_number"
        )
    )
    overflow = confirmed[new_capacity:]
    exceptions = []
    for ticket in overflow:
        exception = DepartureException.objects.create(
            departure=departure,
            kind=DepartureException.Kind.VEHICLE_SUBSTITUTION,
            reason=(
                f"Replacement vehicle has {new_capacity} seats; passenger in "
                f"seat {ticket.seat_number} requires reassignment."
            ),
            reassigned_from_seat=ticket.seat_number,
            requires_customer_communication=True,
        )
        exceptions.append(exception)
    return departure, exceptions


def expire_stale_holds():
    """Sweep timed holds past their expiry (called by a periodic command; the
    authoritative check also happens at conversion time)."""
    return SeatHold.objects.filter(
        state=SeatHold.State.VALID, expires_at__lte=timezone.now()
    ).update(state=SeatHold.State.EXPIRED)
