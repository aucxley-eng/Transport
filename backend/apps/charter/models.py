"""Charter and vehicle hire (blueprint 8, "Charter and vehicle hire").

The blueprint states the feature in three sentences:

  "A hire request specifies dates, itinerary, vehicle class, passenger count
   and included hours or distance. Staff issue a quote, reserve the vehicle and
   driver for the entire interval, record deposits, and finalize permitted
   extras at completion. Availability checks include travel to the pickup,
   turnaround time and existing reservations."

Charter inventory is neither a seat nor a driver-offer window: it is the
*vehicle and driver* held for a continuous interval. The reservation therefore
blocks a window wider than the hire itself, because travel to the pickup and
turnaround both consume time the vehicle and driver cannot be used for another
job.
"""

from django.db import models

from apps.common.models import BaseModel, OrganizationScopedQuerySet

# Blueprint: availability must account for travel to the pickup and turnaround.
DEFAULT_TRAVEL_BUFFER_MINUTES = 60
DEFAULT_TURNAROUND_MINUTES = 30


class HireRequest(BaseModel):
    """A staff-raised request to hire a vehicle and driver for an interval."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        QUOTED = "quoted", "Quoted"
        CONFIRMED = "confirmed", "Confirmed"
        IN_PROGRESS = "in_progress", "In progress"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    organization = models.ForeignKey(
        "accounts.Organization", on_delete=models.CASCADE, related_name="hire_requests"
    )
    customer = models.ForeignKey(
        "accounts.User", on_delete=models.PROTECT, related_name="hire_requests"
    )
    service = models.ForeignKey(
        "catalog.Service", on_delete=models.PROTECT, related_name="hire_requests"
    )
    route = models.ForeignKey(
        "booking.Route",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="hire_requests",
        help_text="Planned itinerary, when it follows a published route.",
    )
    itinerary_notes = models.TextField(blank=True)
    start_at = models.DateTimeField()
    end_at = models.DateTimeField()
    vehicle_class = models.CharField(
        max_length=12, blank=True, help_text="Snapshot of the requested vehicle class."
    )
    passenger_count = models.PositiveSmallIntegerField(default=0)
    included_hours = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    included_distance_km = models.PositiveIntegerField(null=True, blank=True)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.DRAFT, db_index=True
    )
    quote = models.ForeignKey(
        "catalog.Quote",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="hire_requests",
    )
    deposit_total_minor = models.PositiveBigIntegerField(default=0)
    extras_total_minor = models.PositiveBigIntegerField(default=0)
    final_total_minor = models.PositiveBigIntegerField(null=True, blank=True)
    currency = models.CharField(max_length=3, default="KES")
    cancel_reason = models.CharField(max_length=255, blank=True)

    objects = OrganizationScopedQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "start_at"])]

    def __str__(self):
        return f"Hire {self.pk} {self.start_at:%Y-%m-%d %H:%M}-{self.end_at:%H:%M}"

    @property
    def quoted_total_minor(self):
        return self.quote.total_minor if self.quote_id else 0


class CharterReservation(BaseModel):
    """The vehicle and driver held for the entire interval.

    `blocked_from`/`blocked_to` are the window the reservation actually consumes:
    the hire interval widened by travel-to-pickup and turnaround. Overlap checks
    use these, not `reserved_from`/`reserved_to`.
    """

    organization = models.ForeignKey(
        "accounts.Organization", on_delete=models.CASCADE, related_name="charter_reservations"
    )
    hire_request = models.OneToOneField(
        HireRequest, on_delete=models.CASCADE, related_name="reservation"
    )
    vehicle = models.ForeignKey(
        "fleet.Vehicle", on_delete=models.PROTECT, related_name="charter_reservations"
    )
    driver = models.ForeignKey(
        "fleet.Driver", on_delete=models.PROTECT, related_name="charter_reservations"
    )
    reserved_from = models.DateTimeField()
    reserved_to = models.DateTimeField()
    travel_buffer_minutes = models.PositiveSmallIntegerField(
        default=DEFAULT_TRAVEL_BUFFER_MINUTES
    )
    turnaround_minutes = models.PositiveSmallIntegerField(
        default=DEFAULT_TURNAROUND_MINUTES
    )
    blocked_from = models.DateTimeField(db_index=True)
    blocked_to = models.DateTimeField(db_index=True)

    objects = OrganizationScopedQuerySet.as_manager()

    class Meta:
        ordering = ["blocked_from"]
        indexes = [
            models.Index(fields=["vehicle", "blocked_from", "blocked_to"]),
            models.Index(fields=["driver", "blocked_from", "blocked_to"]),
        ]

    def __str__(self):
        return f"Charter reservation {self.pk} {self.vehicle_id} {self.blocked_from}"


class CharterExtra(BaseModel):
    """An additional chargeable item, settled at completion.

    "finalize permitted extras at completion" — only extras the operator permits
    are chargeable; the rest are recorded but excluded from the final total.
    """

    hire_request = models.ForeignKey(
        HireRequest, on_delete=models.CASCADE, related_name="extras"
    )
    kind = models.CharField(max_length=60)
    description = models.CharField(max_length=255, blank=True)
    price_minor = models.PositiveBigIntegerField(default=0)
    is_permitted = models.BooleanField(default=True)
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"Extra {self.kind} ({'permitted' if self.is_permitted else 'not permitted'})"


class CharterDeposit(BaseModel):
    """A deposit recorded against the hire. "record deposits"."""

    hire_request = models.ForeignKey(
        HireRequest, on_delete=models.CASCADE, related_name="deposits"
    )
    payment_attempt = models.ForeignKey(
        "payments.PaymentAttempt",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="charter_deposits",
    )
    amount_minor = models.PositiveBigIntegerField(default=0)
    currency = models.CharField(max_length=3, default="KES")
    recorded_by = models.ForeignKey(
        "accounts.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="recorded_charter_deposits",
    )
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"Deposit {self.amount_minor} for hire {self.hire_request_id}"
