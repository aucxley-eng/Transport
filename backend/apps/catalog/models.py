from django.db import models

from apps.common.models import BaseModel, OrganizationScopedQuerySet


class Service(BaseModel):
    """A purchasable service (blueprint 1). Platform-level catalog entry."""

    class ServiceType(models.TextChoices):
        SCHEDULED = "scheduled", "Scheduled bus / shuttle"
        RIDE = "ride", "On-demand ride"
        CORPORATE = "corporate", "Corporate transport"
        CHARTER = "charter", "Charter / vehicle hire"
        COURIER = "courier", "Local courier"
        PARCEL = "parcel", "Intercity parcel"
        FREIGHT = "freight", "Freight / trucking"
        WAREHOUSE = "warehouse", "Warehousing / fulfillment"

    slug = models.SlugField(max_length=60, unique=True)
    name = models.CharField(max_length=120)
    service_type = models.CharField(max_length=16, choices=ServiceType.choices)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Place(BaseModel):
    """A physical point customers interact with: stage, branch, hub, warehouse,
    landmark. A street address alone may be insufficient (blueprint 4)."""

    class PlaceType(models.TextChoices):
        STAGE = "stage", "Stage / stop"
        BRANCH = "branch", "Branch"
        HUB = "hub", "Hub"
        WAREHOUSE = "warehouse", "Warehouse"
        LANDMARK = "landmark", "Landmark"

    organization = models.ForeignKey(
        "accounts.Organization",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="places",
    )
    name = models.CharField(max_length=160)
    place_type = models.CharField(max_length=12, choices=PlaceType.choices)
    lat = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    lng = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    address = models.CharField(max_length=255, blank=True)
    landmark = models.CharField(max_length=160, blank=True)
    directions = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    objects = OrganizationScopedQuerySet.as_manager()

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class RateCard(BaseModel):
    """Pricing table version applicable from a date (blueprint 9)."""

    organization = models.ForeignKey(
        "accounts.Organization",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="rate_cards",
    )  # null = platform-wide
    service = models.ForeignKey(Service, on_delete=models.CASCADE, related_name="rate_cards")
    name = models.CharField(max_length=120)
    version = models.PositiveIntegerField(default=1)
    currency = models.CharField(max_length=3, default="KES")
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    objects = OrganizationScopedQuerySet.as_manager()

    class Meta:
        ordering = ["-effective_from"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "service", "version"],
                name="uq_ratecard_org_service_version",
            ),
        ]

    def __str__(self):
        owner = self.organization_id or "platform"
        return f"{self.name} v{self.version} [{owner}]"


class RateLine(BaseModel):
    """One pricing rule on a rate card. Amounts are exact minor units."""

    class LineType(models.TextChoices):
        FIXED = "fixed", "Fixed"
        PER_KM = "per_km", "Per distance unit"
        PER_KG = "per_kg", "Per weight unit"
        PER_PASSENGER = "per_passenger", "Per passenger"
        ZONE = "zone", "Zone"

    rate_card = models.ForeignKey(RateCard, on_delete=models.CASCADE, related_name="lines")
    line_type = models.CharField(max_length=16, choices=LineType.choices)
    label = models.CharField(max_length=120, blank=True)
    from_value = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    to_value = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    amount_minor = models.PositiveBigIntegerField(default=0)

    class Meta:
        ordering = ["line_type", "amount_minor"]

    def __str__(self):
        return f"{self.label or self.line_type}: {self.amount_minor}"


class Quote(BaseModel):
    """A versioned price offer. Once accepted a quote is immutable:
    the priced basis is preserved for later dispute and reconciliation
    (blueprint 9, 17)."""

    class Status(models.TextChoices):
        VALID = "valid", "Valid"
        ACCEPTED = "accepted", "Accepted"
        EXPIRED = "expired", "Expired"

    organization = models.ForeignKey(
        "accounts.Organization",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="quotes",
    )
    service = models.ForeignKey(Service, on_delete=models.PROTECT, related_name="quotes")
    request_payload = models.JSONField(default=dict, blank=True)  # locations, capacity, intent
    currency = models.CharField(max_length=3, default="KES")
    total_minor = models.PositiveBigIntegerField()
    basis = models.TextField(blank=True)  # measurement basis (weight, distance, zone...)
    rate_card = models.ForeignKey(RateCard, null=True, blank=True, on_delete=models.PROTECT)
    rate_card_name = models.CharField(max_length=160, blank=True)  # immutable snapshot
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.VALID)
    expires_at = models.DateTimeField(null=True, blank=True)
    accepted_at = models.DateTimeField(null=True, blank=True)

    objects = OrganizationScopedQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Quote {self.pk} {self.currency} {self.total_minor}"

    def accept(self):
        from django.utils import timezone

        if self.status != self.Status.VALID:
            raise ValueError("Only valid quotes can be accepted.")
        now = timezone.now()
        if self.expires_at is not None and now > self.expires_at:
            self.status = self.Status.EXPIRED
            self.save(update_fields=["status", "updated_at"])
            raise ValueError("Quote has expired.")
        self.status = self.Status.ACCEPTED
        self.accepted_at = now
        self.save(update_fields=["status", "accepted_at", "updated_at"])


def build_quote(*, service, request_payload, total_minor, currency="KES", rate_card=None,
                basis="", organization=None, expires_in_minutes=15):
    """Create a quote, snapshotting the rate card name so the quoted basis
    survives later tariff edits."""
    quote = Quote.objects.create(
        organization=organization,
        service=service,
        request_payload=request_payload,
        currency=currency,
        total_minor=total_minor,
        basis=basis,
        rate_card=rate_card,
        rate_card_name=rate_card.name if rate_card else "",
    )
    if expires_in_minutes:
        from datetime import timedelta

        from django.utils import timezone

        quote.expires_at = timezone.now() + timedelta(minutes=expires_in_minutes)
        quote.save(update_fields=["expires_at"])
    return quote