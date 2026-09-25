"""On-demand ride data model (blueprint 8, "On demand rides and corporate
transport"). Names follow the blueprint's own data-model inventory: ride
requests, assignments, trips, stops, offers, shifts and zones."""

from django.db import models

from apps.common.models import BaseModel, OrganizationScopedQuerySet


class ServiceZone(BaseModel):
    """A named service area. Dispatch filters drivers by service zone."""

    name = models.CharField(max_length=120, unique=True)
    center = models.ForeignKey(
        "catalog.Place", null=True, blank=True, on_delete=models.SET_NULL
    )
    radius_km = models.PositiveSmallIntegerField(default=25)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class DriverShift(BaseModel):
    """A driver's availability window and the zone they are working. Supplies
    the "availability" and "service zone" halves of the dispatch filter."""

    class Status(models.TextChoices):
        AVAILABLE = "available", "Available"
        ON_TRIP = "on_trip", "On trip"
        OFF_DUTY = "off_duty", "Off duty"

    driver = models.ForeignKey(
        "fleet.Driver", on_delete=models.CASCADE, related_name="shifts"
    )
    zone = models.ForeignKey(
        ServiceZone, null=True, blank=True, on_delete=models.SET_NULL, related_name="shifts"
    )
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.AVAILABLE, db_index=True
    )

    objects = OrganizationScopedQuerySet.as_manager()

    class Meta:
        ordering = ["starts_at"]
        indexes = [models.Index(fields=["driver", "status", "starts_at"])]

    def __str__(self):
        return f"{self.driver_id} {self.starts_at:%H:%M}-{self.ends_at:%H:%M} ({self.status})"


class RideRequest(BaseModel):
    """A customer's on-demand ride request against a versioned quote."""

    class Status(models.TextChoices):
        REQUESTED = "requested", "Requested"
        ASSIGNED = "assigned", "Assigned"
        EN_ROUTE = "en_route", "En route"
        IN_PROGRESS = "in_progress", "In progress"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"
        NO_SHOW = "no_show", "No-show"
        UNMATCHED = "unmatched", "Unmatched"

    class Resolution(models.TextChoices):
        RETRY = "retry", "Retry matching"
        SCHEDULED_ALTERNATIVE = "scheduled_alternative", "Scheduled alternative"
        CANCEL = "cancel", "Cancel and release"

    organization = models.ForeignKey(
        "accounts.Organization",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ride_requests",
    )
    customer = models.ForeignKey(
        "accounts.User", on_delete=models.PROTECT, related_name="ride_requests"
    )
    service = models.ForeignKey(
        "catalog.Service", on_delete=models.PROTECT, related_name="ride_requests"
    )
    quote = models.ForeignKey(
        "catalog.Quote", on_delete=models.PROTECT, related_name="ride_requests"
    )
    pickup = models.ForeignKey(
        "catalog.Place", on_delete=models.PROTECT, related_name="ride_pickups"
    )
    destination = models.ForeignKey(
        "catalog.Place", on_delete=models.PROTECT, related_name="ride_destinations"
    )
    vehicle_class = models.CharField(max_length=12, blank=True)
    zone = models.ForeignKey(
        ServiceZone, null=True, blank=True, on_delete=models.SET_NULL, related_name="ride_requests"
    )
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.REQUESTED, db_index=True
    )
    resolution = models.CharField(
        max_length=24, choices=Resolution.choices, blank=True
    )

    objects = OrganizationScopedQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "created_at"])]

    def __str__(self):
        return f"Ride {self.pk} {self.pickup_id}->{self.destination_id} ({self.status})"


class DriverOffer(BaseModel):
    """A time-limited offer of a ride to one eligible driver."""

    class State(models.TextChoices):
        PENDING = "pending", "Pending"
        ACCEPTED = "accepted", "Accepted"
        DECLINED = "declined", "Declined"
        EXPIRED = "expired", "Expired"

    ride_request = models.ForeignKey(
        RideRequest, on_delete=models.CASCADE, related_name="offers"
    )
    driver = models.ForeignKey(
        "fleet.Driver", on_delete=models.CASCADE, related_name="offers"
    )
    state = models.CharField(
        max_length=12, choices=State.choices, default=State.PENDING, db_index=True
    )
    expires_at = models.DateTimeField(db_index=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["ride_request", "driver"], name="uq_offer_request_driver"
            ),
        ]

    def __str__(self):
        return f"Offer {self.pk} ride={self.ride_request_id} driver={self.driver_id} ({self.state})"


class Trip(BaseModel):
    """The fulfilled ride. Acceptance and trip start are separate events
    (blueprint 8, Figure 4)."""

    class State(models.TextChoices):
        ASSIGNED = "assigned", "Assigned"
        ARRIVED = "arrived", "Arrived at pickup"
        IN_PROGRESS = "in_progress", "In progress"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    ride_request = models.OneToOneField(
        RideRequest, on_delete=models.CASCADE, related_name="trip"
    )
    driver = models.ForeignKey(
        "fleet.Driver", on_delete=models.PROTECT, related_name="trips"
    )
    vehicle = models.ForeignKey(
        "fleet.Vehicle", null=True, blank=True, on_delete=models.SET_NULL, related_name="trips"
    )
    state = models.CharField(
        max_length=16, choices=State.choices, default=State.ASSIGNED, db_index=True
    )
    pin_hash = models.CharField(max_length=128, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    pending_destination = models.ForeignKey(
        "catalog.Place", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    pending_destination_quote = models.ForeignKey(
        "catalog.Quote", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    objects = OrganizationScopedQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Trip {self.pk} ride={self.ride_request_id} ({self.state})"


class TripEvent(BaseModel):
    """Immutable ordered log of a ride's lifecycle: offers, acceptance, arrival,
    PIN verification, start, destination change, completion, cancellation."""

    class Kind(models.TextChoices):
        OFFERED = "offered", "Offer sent"
        ACCEPTED = "accepted", "Driver accepted"
        DECLINED = "declined", "Driver declined"
        ARRIVED = "arrived", "Arrived at pickup"
        PIN_FAILED = "pin_failed", "Incorrect PIN"
        STARTED = "started", "Trip started"
        DESTINATION_CHANGED = "destination_changed", "Destination change proposed"
        DESTINATION_ACCEPTED = "destination_accepted", "Destination change accepted"
        COMPLETED = "completed", "Trip completed"
        CANCELLED = "cancelled", "Ride cancelled"
        NO_SHOW = "no_show", "No-show"
        MATCHING_FAILED = "matching_failed", "No driver accepted"
        INCIDENT = "incident", "Incident reported"

    ride_request = models.ForeignKey(
        RideRequest, on_delete=models.CASCADE, related_name="events"
    )
    kind = models.CharField(max_length=32, choices=Kind.choices, db_index=True)
    payload = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["created_at"]


class RideCancellation(BaseModel):
    """A cancellation or no-show with its reason, evidence and the applicable
    price policy (blueprint 8)."""

    ride_request = models.ForeignKey(
        RideRequest, on_delete=models.CASCADE, related_name="cancellations"
    )
    reason = models.CharField(max_length=255)
    evidence = models.TextField(blank=True)
    fee_minor = models.PositiveBigIntegerField(default=0)
    is_no_show = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]


class TripIncident(BaseModel):
    """An incident on a trip with a documented escalation path (blueprint 8)."""

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        ACKNOWLEDGED = "acknowledged", "Acknowledged"
        RESOLVED = "resolved", "Resolved"

    trip = models.ForeignKey(Trip, on_delete=models.CASCADE, related_name="incidents")
    kind = models.CharField(max_length=60)
    description = models.TextField()
    escalation_path = models.CharField(max_length=255, blank=True)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.OPEN, db_index=True
    )

    class Meta:
        ordering = ["-created_at"]
