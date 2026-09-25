from django.db import models

from apps.common.models import BaseModel, OrganizationScopedQuerySet


class Vehicle(BaseModel):
    """A physical unit. Capability and status drive dispatch eligibility
    (blueprint 11, 12). Seats and cargo limits stay independent."""

    class VehicleClass(models.TextChoices):
        BUS = "bus", "Bus"
        SHUTTLE = "shuttle", "Shuttle"
        VAN = "van", "Van"
        SEDAN = "sedan", "Sedan"
        TRUCK = "truck", "Truck"
        TRAILER = "trailer", "Trailer"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        MAINTENANCE = "maintenance", "Maintenance"
        BLOCKED = "blocked", "Blocked"
        RETIRED = "retired", "Retired"

    organization = models.ForeignKey(
        "accounts.Organization", on_delete=models.CASCADE, related_name="vehicles"
    )
    registration_number = models.CharField(max_length=20, unique=True)
    vehicle_class = models.CharField(max_length=12, choices=VehicleClass.choices)
    make = models.CharField(max_length=60, blank=True)
    model = models.CharField(max_length=80, blank=True)
    year = models.PositiveSmallIntegerField(null=True, blank=True)
    seating_capacity = models.PositiveSmallIntegerField(default=0)
    baggage_capacity_kg = models.PositiveIntegerField(default=0)
    cargo_weight_kg = models.PositiveIntegerField(default=0, help_text="Max cargo weight (kg).")
    cargo_volume_m3 = models.FloatField(default=0)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.ACTIVE, db_index=True
    )
    maintenance_due_on = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)

    objects = OrganizationScopedQuerySet.as_manager()

    class Meta:
        ordering = ["registration_number"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=["active", "maintenance", "blocked", "retired"]),
                name="ck_vehicle_status",
            ),
        ]

    def __str__(self):
        return self.registration_number

    def is_dispatchable(self):
        return self.status == self.Status.ACTIVE


class Document(BaseModel):
    """A verified file on a vehicle or driver. Uploading a file is NOT
    verification; expiry and approval state are authoritative (blueprint 19)."""

    class EntityType(models.TextChoices):
        VEHICLE = "vehicle", "Vehicle"
        DRIVER = "driver", "Driver"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        VERIFIED = "verified", "Verified"
        REJECTED = "rejected", "Rejected"
        EXPIRED = "expired", "Expired"

    organization = models.ForeignKey(
        "accounts.Organization", on_delete=models.CASCADE, related_name="documents"
    )
    entity_type = models.CharField(max_length=12, choices=EntityType.choices)
    entity_id = models.UUIDField()
    doc_type = models.CharField(max_length=60)  # e.g. logbook, insurance, PSV licence, NTSA inspection
    reference = models.CharField(max_length=80, blank=True)
    issuer = models.CharField(max_length=120, blank=True)
    issued_on = models.DateField(null=True, blank=True)
    expires_on = models.DateField(null=True, blank=True)
    file = models.FileField(upload_to="documents/", blank=True, null=True)
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    verified_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL
    )
    verified_at = models.DateTimeField(null=True, blank=True)

    objects = OrganizationScopedQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.doc_type} ({self.reference or self.entity_id})"

    def mark_verified(self, user):
        from django.utils import timezone

        self.status = self.Status.VERIFIED
        self.verified_by = user
        self.verified_at = timezone.now()
        self.save()


class Driver(BaseModel):
    """A user approved to operate vehicles for an organization."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        SUSPENDED = "suspended", "Suspended"

    user = models.OneToOneField(
        "accounts.User", on_delete=models.CASCADE, related_name="driver_profile"
    )
    organization = models.ForeignKey(
        "accounts.Organization", on_delete=models.CASCADE, related_name="drivers"
    )
    license_number = models.CharField(max_length=40, blank=True)
    license_expires_on = models.DateField(null=True, blank=True)
    current_vehicle = models.ForeignKey(
        "fleet.Vehicle",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="assigned_drivers",
        help_text="Vehicle the driver is currently assigned to; drives dispatch eligibility by vehicle class.",
    )
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    working_hours_per_day = models.PositiveSmallIntegerField(default=8)
    notes = models.TextField(blank=True)

    objects = OrganizationScopedQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "organization"], name="uq_driver_user_org"
            ),
        ]

    def __str__(self):
        return f"{self.user.full_name or self.user.phone} ({self.status})"

    def is_dispatchable(self):
        return self.status == self.Status.APPROVED