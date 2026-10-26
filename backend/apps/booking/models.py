"""Passenger booking: routes, departures, segment-level seat inventory, atomic
timed seat holds, tickets and boarding (blueprint 7, "Scheduled passenger
booking").

Inventory is held against a departure AND the occupied route segments: a seat
on Nairobi->Nakuru->Eldoret may be sold to different passengers on
non-overlapping segments, but an overlapping reservation must be rejected even
when two customers click at the same instant. Concurrency safety comes from
row-locking the departure inside a transaction (see booking.services).
"""

from django.db import models

from apps.common.models import BaseModel, OrganizationScopedQuerySet


class Route(BaseModel):
    """An ordered set of stops a departure follows (blueprint 7: "A route
    defines an ordered set of stops")."""

    organization = models.ForeignKey(
        "accounts.Organization", on_delete=models.CASCADE, related_name="routes"
    )
    name = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    branch = models.ForeignKey(
        "accounts.Branch",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="routes",
        help_text="Owning branch. Null means a network-wide route.",
    )
    is_active = models.BooleanField(default=True)

    objects = OrganizationScopedQuerySet.as_manager()

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "name"], name="uq_route_org_name"
            ),
        ]

    def __str__(self):
        return self.name


class RouteStop(BaseModel):
    """A stop on a route, ordered by ``position``. Consecutive stops define the
    departure's segments."""

    route = models.ForeignKey(Route, on_delete=models.CASCADE, related_name="stops")
    place = models.ForeignKey(
        "catalog.Place", on_delete=models.PROTECT, related_name="route_stops"
    )
    position = models.PositiveIntegerField()
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["route_id", "position"]
        constraints = [
            models.UniqueConstraint(
                fields=["route", "position"], name="uq_routestop_route_position"
            ),
        ]

    def __str__(self):
        return f"{self.route.name} #{self.position} {self.place.name}"


class Departure(BaseModel):
    """A route on a date, time, operator, vehicle class and fare schedule
    (blueprint 7). Seat capacity is snapshotted from the assigned vehicle so a
    later vehicle edit cannot retroactively change a sold departure."""

    class Status(models.TextChoices):
        SCHEDULED = "scheduled", "Scheduled"
        BOARDING = "boarding", "Boarding"
        DEPARTED = "departed", "Departed"
        CANCELLED = "cancelled", "Cancelled"
        COMPLETED = "completed", "Completed"

    organization = models.ForeignKey(
        "accounts.Organization", on_delete=models.CASCADE, related_name="departures"
    )
    service = models.ForeignKey(
        "catalog.Service",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="departures",
        help_text="Purchasable catalog service this departure fulfils.",
    )
    route = models.ForeignKey(Route, on_delete=models.PROTECT, related_name="departures")
    branch = models.ForeignKey(
        "accounts.Branch",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="departures",
        help_text="Operating branch that runs this departure.",
    )
    vehicle = models.ForeignKey(
        "fleet.Vehicle",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="departures",
    )
    vehicle_class = models.CharField(max_length=12, blank=True)  # snapshot
    departure_date = models.DateField(db_index=True)
    departure_time = models.TimeField()
    capacity = models.PositiveSmallIntegerField(default=0)  # snapshot of seats
    fare_minor = models.PositiveBigIntegerField(default=0)  # per-seat, whole route
    currency = models.CharField(max_length=3, default="KES")
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.SCHEDULED, db_index=True
    )

    objects = OrganizationScopedQuerySet.as_manager()

    class Meta:
        ordering = ["-departure_date", "departure_time"]
        indexes = [
            models.Index(fields=["route", "departure_date"]),
        ]

    def __str__(self):
        return f"{self.route.name} {self.departure_date} {self.departure_time}"

    def departure_datetime(self):
        from datetime import datetime

        return datetime.combine(self.departure_date, self.departure_time)


class Segment(BaseModel):
    """One leg between two consecutive route stops on a departure. Holds
    consume segments; segment occupancy is the unit of seat inventory
    (blueprint 7)."""

    departure = models.ForeignKey(
        Departure, on_delete=models.CASCADE, related_name="segments"
    )
    position = models.PositiveIntegerField()  # 0-based leg index
    from_place = models.ForeignKey(
        "catalog.Place", on_delete=models.PROTECT, related_name="segments_from"
    )
    to_place = models.ForeignKey(
        "catalog.Place", on_delete=models.PROTECT, related_name="segments_to"
    )

    class Meta:
        ordering = ["departure_id", "position"]
        constraints = [
            models.UniqueConstraint(
                fields=["departure", "position"], name="uq_segment_departure_position"
            ),
        ]

    def __str__(self):
        return f"{self.departure_id} seg{self.position} {self.from_place_id}->{self.to_place_id}"


class SeatHold(BaseModel):
    """A timed, atomic reservation of one seat across one or more of a
    departure's segments (blueprint 7). A hold is created inside a transaction
    that rejects any overlapping segment occupancy for the same seat, so two
    simultaneous clicks cannot both win."""

    class State(models.TextChoices):
        VALID = "valid", "Valid"
        EXPIRED = "expired", "Expired"
        CONVERTED = "converted", "Converted to ticket"
        RELEASED = "released", "Released"

    departure = models.ForeignKey(
        Departure, on_delete=models.CASCADE, related_name="seat_holds"
    )
    customer = models.ForeignKey(
        "accounts.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="seat_holds",
    )
    seat_number = models.PositiveSmallIntegerField()
    state = models.CharField(
        max_length=12, choices=State.choices, default=State.VALID, db_index=True
    )
    expires_at = models.DateTimeField(db_index=True)
    order = models.ForeignKey(
        "orders.Order",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="seat_holds",
    )

    objects = OrganizationScopedQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["departure", "seat_number", "state"]),
        ]

    def __str__(self):
        return f"Hold {self.pk} {self.departure_id} seat {self.seat_number} ({self.state})"

    def occupied_segments(self):
        return list(self.segments.values_list("segment__position", flat=True))

    def is_valid(self):
        from django.utils import timezone

        return self.state == self.State.VALID and timezone.now() < self.expires_at


class SeatHoldSegment(models.Model):
    """Through-model linking a hold to the exact segments it occupies."""

    hold = models.ForeignKey(
        SeatHold, on_delete=models.CASCADE, related_name="segments"
    )
    segment = models.ForeignKey(Segment, on_delete=models.CASCADE, related_name="holds")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["hold", "segment"], name="uq_hold_segment"
            ),
        ]


class Ticket(BaseModel):
    """A confirmed seat. Carries a random reference for humans and a signed
    token for scanning, the departure reference, the passenger details needed
    for operation, and boarding information (blueprint 7: "Tickets contain a
    random identifier or signed token, departure reference, passenger details
    required for operation and boarding information")."""

    class State(models.TextChoices):
        ISSUED = "issued", "Issued"
        BOARDED = "boarded", "Boarded"
        CANCELLED = "cancelled", "Cancelled"
        REFUNDED = "refunded", "Refunded"

    departure = models.ForeignKey(
        Departure, on_delete=models.PROTECT, related_name="tickets"
    )
    hold = models.OneToOneField(
        SeatHold, null=True, blank=True, on_delete=models.SET_NULL, related_name="ticket"
    )
    reference = models.CharField(max_length=24, unique=True, db_index=True)
    token = models.CharField(max_length=64, unique=True, db_index=True)
    selling_branch = models.ForeignKey(
        "accounts.Branch",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="tickets_sold",
        help_text="Branch that sold the ticket, which may differ from the operating branch.",
    )
    seat_number = models.PositiveSmallIntegerField()
    passenger_name = models.CharField(max_length=160)
    passenger_phone = models.CharField(max_length=16)
    passenger_id_number = models.CharField(max_length=40, blank=True)
    state = models.CharField(
        max_length=12, choices=State.choices, default=State.ISSUED, db_index=True
    )
    boarded_at = models.DateTimeField(null=True, blank=True)

    objects = OrganizationScopedQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["departure", "state"]),
        ]

    def __str__(self):
        return f"Ticket {self.reference} {self.departure_id} seat {self.seat_number}"

    def boarding_info(self):
        return {
            "reference": self.reference,
            "seat_number": self.seat_number,
            "departure": str(self.departure.departure_datetime().isoformat()),
            "route": self.departure.route.name,
            "passenger_name": self.passenger_name,
        }


class BoardingScan(BaseModel):
    """Immutable record of a ticket scan. The scanner checks departure, ticket
    state and previous boarding (blueprint 7)."""

    ticket = models.ForeignKey(
        Ticket, on_delete=models.CASCADE, related_name="scans"
    )
    departure = models.ForeignKey(
        Departure, on_delete=models.CASCADE, related_name="scans"
    )
    boarded = models.BooleanField(default=False)
    scanner_device = models.CharField(max_length=80, blank=True)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at"]


class DepartureException(BaseModel):
    """An operational exception on a departure: cancellation, reschedule,
    vehicle substitution, seat-map change, no-show or delay handled explicitly
    (blueprint 7). A replacement vehicle with fewer seats MUST create a
    reassignment exception requiring customer communication — it must never
    silently remove passengers."""

    class Kind(models.TextChoices):
        CANCELLATION = "cancellation", "Cancellation"
        RESCHEDULE = "reschedule", "Reschedule"
        VEHICLE_SUBSTITUTION = "vehicle_substitution", "Vehicle substitution"
        SEAT_MAP_CHANGE = "seat_map_change", "Seat map change"
        NO_SHOW = "no_show", "No-show"
        DELAY = "delay", "Delay"

    departure = models.ForeignKey(
        Departure, on_delete=models.CASCADE, related_name="exceptions"
    )
    kind = models.CharField(max_length=24, choices=Kind.choices)
    reason = models.TextField(blank=True)
    reassigned_from_seat = models.PositiveSmallIntegerField(null=True, blank=True)
    requires_customer_communication = models.BooleanField(default=False)
    communicated_at = models.DateTimeField(null=True, blank=True)

    objects = OrganizationScopedQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_kind_display()} on {self.departure_id}"
