"""Goods delivery and freight (blueprint 9, 10, 11).

Scope note: this app is the single-owner movement a shipper actually buys --
quote, shipment, packages and manifest, custody chain with receipts, delivery
evidence, and exceptions with an explicit next action. Consolidated
shared-load selling, backhaul and opportunistic mid-route insertion are later
envelopes, on purpose.

Blueprint rules that shape these models:

* "A goods booking records sender, recipient, collection and destination,
  package count, dimensions, weight, declared value, handling needs and service
  window." -> :class:`Shipment` / :class:`Package`.
* "Save the accepted quote and measurement basis." -> the accepted ``Quote``,
  the measurement basis and the agreed terms are frozen onto the shipment, so a
  later tariff edit cannot rewrite history.
* "Preserve the accepted carrier offer and terms before dispatch." ->
  ``terms_snapshot``.
* "A passenger seat is never treated as interchangeable with cargo weight." ->
  freight has its own capacity ledger; nothing here reads seat inventory.
* "A departure manifest lists the packages assigned to a trip. Loading scans
  must reconcile against the manifest before release." ->
  :class:`Manifest` / :class:`ManifestLine`.
* "A custody record identifies the package, giving party, receiving party,
  location, event type, device event time, server receipt time and evidence.
  Corrections add a new event with a reason." -> :class:`CustodyEvent` is
  insert-only and keeps both timestamps.
"""

import uuid
from decimal import ROUND_HALF_UP, Decimal

from django.db import models
from django.utils import timezone

from apps.catalog.models import Place, Quote, Service
from apps.common.models import BaseModel, OrganizationScopedQuerySet
from apps.fleet.models import CargoCompartment, Driver, Vehicle

# Volumetric divisor: air freight convention is 6000 cm3 per kg, road freight
# is commonly 4000-5000. The blueprint allows charging by volumetric weight but
# does not fix a divisor, so it is a named constant the tariff can be measured
# against rather than a hidden literal.
VOLUMETRIC_DIVISOR_CM3_PER_KG = 4000


def volumetric_weight_kg(length_cm, width_cm, height_cm):
    """Volumetric (dimensional) weight, the chargeable weight a bulky but
    light consignment is priced on."""
    if not (length_cm and width_cm and height_cm):
        return Decimal("0.000")
    raw = (
        Decimal(length_cm) * Decimal(width_cm) * Decimal(height_cm)
    ) / Decimal(VOLUMETRIC_DIVISOR_CM3_PER_KG)
    return raw.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)


class MeasurementBasis(models.TextChoices):
    """The basis a freight quote was computed on (blueprint 9: "a business may
    charge by actual weight, volumetric weight, distance, zone or vehicle
    capacity")."""

    ACTUAL_WEIGHT = "actual_weight", "Actual weight"
    VOLUMETRIC = "volumetric", "Volumetric weight"
    DISTANCE = "distance", "Distance"
    ZONE = "zone", "Zone"
    VEHICLE_CAPACITY = "vehicle_capacity", "Vehicle capacity"


class LoadType(models.TextChoices):
    """Full truckload is one shipper's consignment filling the vehicle;
    shared-load is a consignment riding alongside others (blueprint 9: "For
    full-truckload or shared-load freight")."""

    FULL_TRUCKLOAD = "full_truckload", "Full truckload"
    SHARED_LOAD = "shared_load", "Shared load"


class Shipment(BaseModel):
    """One shipper's consignment from collection to destination."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        BOOKED = "booked", "Booked"
        COLLECTED = "collected", "Collected"
        IN_TRANSIT = "in_transit", "In transit"
        PARTIALLY_DELIVERED = "partially_delivered", "Partially delivered"
        DELIVERED = "delivered", "Delivered"
        EXCEPTION = "exception", "Exception"
        CANCELLED = "cancelled", "Cancelled"

    organization = models.ForeignKey(
        "accounts.Organization", on_delete=models.CASCADE, related_name="shipments"
    )
    reference = models.CharField(max_length=32, unique=True, editable=False)
    service = models.ForeignKey(Service, on_delete=models.PROTECT, related_name="shipments")

    # Parties. A recipient is often not a platform user, so the name and phone
    # are stored directly; the sender keeps a link when they are one.
    sender = models.ForeignKey(
        "accounts.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="shipments_sent",
    )
    sender_name = models.CharField(max_length=160)
    sender_phone = models.CharField(max_length=32)
    recipient_name = models.CharField(max_length=160)
    recipient_phone = models.CharField(max_length=32)

    collection = models.ForeignKey(
        Place, on_delete=models.PROTECT, related_name="collections"
    )
    destination = models.ForeignKey(
        Place, on_delete=models.PROTECT, related_name="destinations"
    )

    package_count = models.PositiveIntegerField(default=0)
    total_weight_kg = models.DecimalField(max_digits=10, decimal_places=3, default=0)
    total_volume_m3 = models.DecimalField(max_digits=10, decimal_places=3, default=0)
    volumetric_weight_kg = models.DecimalField(
        max_digits=10, decimal_places=3, default=0, help_text="Sum of package volumetric weights."
    )
    declared_value_minor = models.PositiveBigIntegerField(default=0)
    handling_needs = models.JSONField(default=list, blank=True)

    service_window_start = models.DateTimeField()
    service_window_end = models.DateTimeField()

    # Frozen commercial terms.
    quote = models.ForeignKey(
        Quote, null=True, blank=True, on_delete=models.PROTECT, related_name="shipments"
    )
    measurement_basis = models.CharField(
        max_length=20,
        choices=MeasurementBasis.choices,
        blank=True,
        help_text="Basis the accepted quote was computed on; preserved, not recomputed.",
    )
    measurement_snapshot = models.JSONField(
        default=dict, blank=True, help_text="The measurements the quote was computed from."
    )
    terms_snapshot = models.JSONField(
        default=dict,
        blank=True,
        help_text="Frozen carrier terms: equipment type, loading access, limits, charges.",
    )
    agreed_total_minor = models.PositiveBigIntegerField(default=0)

    # A return is a linked reverse movement with its own legs and custody
    # history, not a flag on the original consignment.
    is_return = models.BooleanField(default=False)
    return_of = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="returns"
    )

    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.DRAFT, db_index=True
    )

    load_type = models.CharField(
        max_length=16,
        choices=LoadType.choices,
        default=LoadType.SHARED_LOAD,
        help_text="A full truckload travels alone; a shared load rides with others.",
    )

    objects = OrganizationScopedQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["organization", "status", "service_window_start"])]

    def __str__(self):
        return f"{self.reference} {self.sender_name} -> {self.recipient_name}"


class Package(BaseModel):
    """A single physical item. Package identity survives every leg and hub, so
    a partial delivery stays visible at package level (blueprint 9)."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        LOADED = "loaded", "Loaded"
        IN_TRANSIT = "in_transit", "In transit"
        DELIVERED = "delivered", "Delivered"
        MISSING = "missing", "Missing"
        DAMAGED = "damaged", "Damaged"
        RETURNED = "returned", "Returned"

    organization = models.ForeignKey(
        "accounts.Organization", on_delete=models.CASCADE, related_name="packages"
    )
    shipment = models.ForeignKey(
        Shipment, on_delete=models.CASCADE, related_name="packages"
    )
    label = models.CharField(max_length=40, unique=True, editable=False)
    description = models.CharField(max_length=200, blank=True)
    weight_kg = models.DecimalField(max_digits=10, decimal_places=3, default=0)
    length_cm = models.PositiveIntegerField(default=0)
    width_cm = models.PositiveIntegerField(default=0)
    height_cm = models.PositiveIntegerField(default=0)
    volume_m3 = models.DecimalField(max_digits=10, decimal_places=3, default=0)
    volumetric_weight_kg = models.DecimalField(max_digits=10, decimal_places=3, default=0)
    declared_value_minor = models.PositiveBigIntegerField(default=0)
    handling_flags = models.JSONField(default=list, blank=True)
    is_fragile = models.BooleanField(default=False)
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    current_location = models.ForeignKey(
        Place, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    objects = OrganizationScopedQuerySet.as_manager()

    class Meta:
        ordering = ["shipment_id", "label"]
        indexes = [models.Index(fields=["shipment", "status"])]

    def __str__(self):
        return f"{self.label} ({self.status})"

    def measure(self, save=True):
        """Derive volume and volumetric weight from dimensions."""
        volume = (
            Decimal(self.length_cm) * Decimal(self.width_cm) * Decimal(self.height_cm)
        ) / Decimal(1_000_000)
        self.volume_m3 = volume.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
        self.volumetric_weight_kg = volumetric_weight_kg(
            self.length_cm, self.width_cm, self.height_cm
        )
        if save:
            self.save(
                update_fields=["volume_m3", "volumetric_weight_kg", "updated_at"]
            )
        return self


class FreightTrip(BaseModel):
    """A vehicle movement that freight is assigned to. Freight owns its own trip
    rather than borrowing a passenger trip, because cargo capacity is
    independent of seat capacity (blueprint 11)."""

    class Status(models.TextChoices):
        PLANNED = "planned", "Planned"
        LOADING = "loading", "Loading"
        RELEASED = "released", "Released"
        IN_TRANSIT = "in_transit", "In transit"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    organization = models.ForeignKey(
        "accounts.Organization", on_delete=models.CASCADE, related_name="freight_trips"
    )
    vehicle = models.ForeignKey(
        Vehicle, on_delete=models.PROTECT, related_name="freight_trips"
    )
    driver = models.ForeignKey(
        Driver, null=True, blank=True, on_delete=models.PROTECT, related_name="freight_trips"
    )
    service = models.ForeignKey(
        Service, null=True, blank=True, on_delete=models.PROTECT, related_name="freight_trips"
    )
    origin = models.ForeignKey(
        Place, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    destination = models.ForeignKey(
        Place, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    planned_start = models.DateTimeField()
    planned_end = models.DateTimeField()
    trip_cost_minor = models.PositiveBigIntegerField(
        default=0,
        help_text=(
            "What it costs the operator to run this movement. Confirmed shipment "
            "revenue must cover it before the trip may depart."
        ),
    )
    started_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Set when the driver departs; a plan cannot be rewritten after this.",
    )
    distance_km = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=0,
        help_text="Measured distance, the denominator of the empty-running measure.",
    )
    has_revenue_load = models.BooleanField(
        default=False,
        help_text="False while the measured distance is being run without a revenue load.",
    )
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.PLANNED, db_index=True
    )
    notes = models.TextField(blank=True)

    objects = OrganizationScopedQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["organization", "status", "planned_start"])]

    def __str__(self):
        return f"FreightTrip {self.pk} {self.vehicle.registration_number} ({self.status})"


class ShipmentLeg(BaseModel):
    """One planned movement of a shipment. Legs connect freight to actual
    vehicle trips without losing package identity (blueprint 10, Figure 10)."""

    class Status(models.TextChoices):
        PLANNED = "planned", "Planned"
        LOADED = "loaded", "Loaded"
        DEPARTED = "departed", "Departed"
        COMPLETED = "completed", "Completed"
        SKIPPED = "skipped", "Skipped"

    organization = models.ForeignKey(
        "accounts.Organization", on_delete=models.CASCADE, related_name="shipment_legs"
    )
    shipment = models.ForeignKey(
        Shipment, on_delete=models.CASCADE, related_name="legs"
    )
    trip = models.ForeignKey(
        FreightTrip, null=True, blank=True, on_delete=models.SET_NULL, related_name="legs"
    )
    stop = models.ForeignKey(Place, on_delete=models.PROTECT, related_name="+")
    sequence = models.PositiveSmallIntegerField(default=0)
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.PLANNED, db_index=True
    )
    planned_at = models.DateTimeField(null=True, blank=True)
    actual_at = models.DateTimeField(null=True, blank=True)

    objects = OrganizationScopedQuerySet.as_manager()

    class Meta:
        ordering = ["shipment_id", "sequence"]
        constraints = [
            models.UniqueConstraint(
                fields=["shipment", "sequence"], name="uq_leg_shipment_sequence"
            ),
        ]

    def __str__(self):
        return f"Leg {self.sequence} of {self.shipment.reference}"


class Manifest(BaseModel):
    """The packages assigned to one trip. "Loading scans must reconcile against
    the manifest before release" (blueprint 10)."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        RECONCILED = "reconciled", "Reconciled"
        RELEASED = "released", "Released"
        DISCREPANCY = "discrepancy", "Discrepancy"

    organization = models.ForeignKey(
        "accounts.Organization", on_delete=models.CASCADE, related_name="manifests"
    )
    trip = models.ForeignKey(
        FreightTrip, on_delete=models.CASCADE, related_name="manifests"
    )
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.DRAFT, db_index=True
    )
    built_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    reconciled_at = models.DateTimeField(null=True, blank=True)
    released_at = models.DateTimeField(null=True, blank=True)
    released_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    objects = OrganizationScopedQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["trip"], name="uq_manifest_per_trip"),
        ]

    def __str__(self):
        return f"Manifest {self.pk} trip={self.trip_id} ({self.status})"

    @property
    def is_reconciled(self):
        return self.reconciled_at is not None

    def has_open_discrepancy(self):
        """A line that is missing, damaged or unlisted is an open discrepancy
        that blocks release."""
        return self.lines.filter(
            scan_state__in=[
                ManifestLine.ScanState.MISSING,
                ManifestLine.ScanState.DAMAGED,
                ManifestLine.ScanState.EXTRA,
            ]
        ).exists()


class ManifestLine(BaseModel):
    """Expected vs scanned state for one package on a manifest."""

    class ScanState(models.TextChoices):
        PENDING = "pending", "Not yet scanned"
        SCANNED = "scanned", "Scanned and matched"
        MISSING = "missing", "Expected but not scanned"
        DAMAGED = "damaged", "Scanned but damaged"
        EXTRA = "extra", "Scanned but not on the manifest"

    organization = models.ForeignKey(
        "accounts.Organization", on_delete=models.CASCADE, related_name="manifest_lines"
    )
    manifest = models.ForeignKey(
        Manifest, on_delete=models.CASCADE, related_name="lines"
    )
    package = models.ForeignKey(
        Package, null=True, blank=True, on_delete=models.PROTECT, related_name="manifest_lines"
    )
    compartment = models.ForeignKey(
        CargoCompartment,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="manifest_lines",
    )
    label = models.CharField(max_length=40)
    expected_weight_kg = models.DecimalField(max_digits=10, decimal_places=3, default=0)
    scan_state = models.CharField(
        max_length=12, choices=ScanState.choices, default=ScanState.PENDING, db_index=True
    )
    scanned_at = models.DateTimeField(null=True, blank=True)
    note = models.CharField(max_length=200, blank=True)

    objects = OrganizationScopedQuerySet.as_manager()

    class Meta:
        ordering = ["label"]
        constraints = [
            models.UniqueConstraint(
                fields=["manifest", "label"], name="uq_manifest_line_label"
            ),
        ]

    def __str__(self):
        return f"{self.label}: {self.scan_state}"


class CustodyEvent(models.Model):
    """Append-only custody chain entry (blueprint 10).

    Insert-only by construction: no ``updated_at``, and corrections are new rows
    pointing at the event they correct, never edits. Both the device event time
    and the server receipt time are kept so offline capture is distinguishable
    from server arrival.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class EventType(models.TextChoices):
        BOOKED = "booked", "Booked"
        COLLECTED = "collected", "Collected from sender"
        LOADED = "loaded", "Loaded onto vehicle"
        DEPARTED = "departed", "Departed origin"
        ARRIVED = "arrived", "Arrived at stop"
        HELD = "held", "Held at branch or hub"
        DELIVERED = "delivered", "Delivered to recipient"
        REFUSED = "refused", "Delivery refused"
        ATTEMPTED = "attempted", "Delivery attempted"
        RETURNED = "returned", "Returned to sender"
        CORRECTION = "correction", "Correction"

    organization = models.ForeignKey(
        "accounts.Organization", on_delete=models.CASCADE, related_name="custody_events"
    )
    package = models.ForeignKey(
        Package, on_delete=models.CASCADE, related_name="custody_events"
    )
    event_type = models.CharField(max_length=16, choices=EventType.choices, db_index=True)
    giving_party = models.CharField(max_length=160, blank=True)
    receiving_party = models.CharField(max_length=160, blank=True)
    location = models.ForeignKey(
        Place, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    location_text = models.CharField(max_length=200, blank=True)
    device_event_time = models.DateTimeField(
        help_text="Time on the capturing device, as reported."
    )
    server_receipt_time = models.DateTimeField(default=timezone.now, db_index=True)
    evidence = models.JSONField(default=dict, blank=True)
    note = models.CharField(max_length=255, blank=True)
    corrects_event = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="corrections"
    )
    correction_reason = models.CharField(max_length=255, blank=True)
    recorded_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-server_receipt_time"]
        indexes = [models.Index(fields=["package", "event_type"])]

    def __str__(self):
        return f"{self.package_id} {self.event_type} @ {self.device_event_time:%Y-%m-%d %H:%M}"


class ShipmentEvidence(BaseModel):
    """Collection and delivery evidence, captured separately (blueprint 9).

    "Do not let a photograph alone prove that the intended recipient received
    the correct package." A delivery therefore needs recipient verification;
    photos are supporting evidence only.
    """

    class Kind(models.TextChoices):
        COLLECTION = "collection", "Collection evidence"
        DELIVERY = "delivery", "Delivery evidence"

    organization = models.ForeignKey(
        "accounts.Organization", on_delete=models.CASCADE, related_name="shipment_evidence"
    )
    shipment = models.ForeignKey(
        Shipment, on_delete=models.CASCADE, related_name="evidence"
    )
    kind = models.CharField(max_length=12, choices=Kind.choices)
    # Recipient verification for delivery. A photo sets none of these.
    recipient_name = models.CharField(max_length=160, blank=True)
    recipient_id_type = models.CharField(max_length=40, blank=True)
    recipient_id_last4 = models.CharField(max_length=4, blank=True)
    otp_verified = models.BooleanField(default=False)
    signature_ref = models.CharField(max_length=120, blank=True)
    is_delegate = models.BooleanField(default=False)
    photos = models.JSONField(default=list, blank=True)
    notes = models.TextField(blank=True)
    device_event_time = models.DateTimeField(null=True, blank=True)
    recorded_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    objects = OrganizationScopedQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["shipment", "kind"])]

    def __str__(self):
        return f"{self.kind} evidence for {self.shipment.reference}"

    def is_recipient_verified(self):
        """A photo alone is never proof; a name with an identity check, an OTP,
        or a signature is."""
        return bool(
            self.otp_verified
            or self.signature_ref
            or (self.recipient_name and self.recipient_id_last4)
        )


class ShipmentException(BaseModel):
    """A discrepancy or failed attempt. "Missing, extra or damaged packages
    create exceptions rather than being silently accepted", and a failed
    attempt carries a next action: retry, hold at branch, return or claim."""

    class Kind(models.TextChoices):
        MISSING = "missing", "Missing package"
        EXTRA = "extra", "Unlisted package"
        DAMAGED = "damaged", "Damaged package"
        FAILED_ATTEMPT = "failed_attempt", "Failed delivery attempt"
        REFUSED = "refused", "Recipient refused"
        ABSENT = "absent", "Recipient absent"

    class NextAction(models.TextChoices):
        RETRY = "retry", "Retry delivery"
        HOLD_BRANCH = "hold_branch", "Hold at branch"
        RETURN = "return", "Return to sender"
        CLAIM = "claim", "Raise claim"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        RESOLVED = "resolved", "Resolved"

    organization = models.ForeignKey(
        "accounts.Organization", on_delete=models.CASCADE, related_name="shipment_exceptions"
    )
    shipment = models.ForeignKey(
        Shipment, on_delete=models.CASCADE, related_name="exceptions"
    )
    package = models.ForeignKey(
        Package, null=True, blank=True, on_delete=models.SET_NULL, related_name="exceptions"
    )
    kind = models.CharField(max_length=16, choices=Kind.choices, db_index=True)
    next_action = models.CharField(max_length=12, choices=NextAction.choices)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.OPEN, db_index=True
    )
    detail = models.CharField(max_length=255, blank=True)
    raised_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution_note = models.CharField(max_length=255, blank=True)

    objects = OrganizationScopedQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["shipment", "status"])]

    def __str__(self):
        return f"{self.kind}/{self.next_action} on {self.shipment.reference}"


class ConsolidationPlan(BaseModel):
    """A versioned proposal to share one vehicle between several consignments.

    Blueprint 11: "a route optimizer may propose vehicle assignments and stop
    order subject to time windows, capacity and working hours" and "save each
    route plan version". Each proposal is a new immutable version; publishing
    supersedes the previous published one rather than editing it.
    """

    class Status(models.TextChoices):
        PROPOSED = "proposed", "Proposed"
        VALIDATED = "validated", "Validated"
        PUBLISHED = "published", "Published"
        SUPERSEDED = "superseded", "Superseded"
        REJECTED = "rejected", "Rejected"

    organization = models.ForeignKey(
        "accounts.Organization", on_delete=models.CASCADE, related_name="consolidation_plans"
    )
    trip = models.ForeignKey(
        FreightTrip, on_delete=models.CASCADE, related_name="consolidation_plans"
    )
    version = models.PositiveSmallIntegerField()
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.PROPOSED, db_index=True
    )
    explanation = models.TextField(
        blank=True,
        help_text="Why an infeasible plan is infeasible; returned to dispatch, not hidden.",
    )
    validated_at = models.DateTimeField(null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    supersedes = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="superseded_by"
    )
    change_reason = models.CharField(
        max_length=255,
        blank=True,
        help_text="Required when superseding a plan after publication.",
    )
    proposed_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    objects = OrganizationScopedQuerySet.as_manager()

    class Meta:
        ordering = ["trip_id", "-version"]
        constraints = [
            models.UniqueConstraint(fields=["trip", "version"], name="uq_plan_trip_version"),
        ]

    def __str__(self):
        return f"Plan v{self.version} trip={self.trip_id} ({self.status})"

    @property
    def is_published(self):
        return self.status == self.Status.PUBLISHED

    def admitted_shipments(self):
        return self.shipments.filter(accepted=True).select_related("shipment")


class ConsolidationStop(BaseModel):
    """One stop in a plan's proposed order, with the window it must be served in."""

    organization = models.ForeignKey(
        "accounts.Organization", on_delete=models.CASCADE, related_name="consolidation_stops"
    )
    plan = models.ForeignKey(
        ConsolidationPlan, on_delete=models.CASCADE, related_name="stops"
    )
    place = models.ForeignKey(Place, on_delete=models.PROTECT, related_name="+")
    sequence = models.PositiveSmallIntegerField()
    window_start = models.DateTimeField()
    window_end = models.DateTimeField()
    is_completed = models.BooleanField(
        default=False,
        help_text="Completed stops are preserved once the driver has begun the route.",
    )

    objects = OrganizationScopedQuerySet.as_manager()

    class Meta:
        ordering = ["plan_id", "sequence"]
        constraints = [
            models.UniqueConstraint(
                fields=["plan", "sequence"], name="uq_plan_stop_sequence"
            ),
        ]

    def __str__(self):
        return f"Stop {self.sequence}: {self.place.name}"


class PlanShipment(BaseModel):
    """Whether a consignment rides on a plan, and why.

    A rejected consignment keeps its reason, so dispatch can see the unassigned
    work and its cause rather than an empty plan.
    """

    organization = models.ForeignKey(
        "accounts.Organization", on_delete=models.CASCADE, related_name="plan_shipments"
    )
    plan = models.ForeignKey(
        ConsolidationPlan, on_delete=models.CASCADE, related_name="shipments"
    )
    shipment = models.ForeignKey(
        Shipment, on_delete=models.CASCADE, related_name="plan_entries"
    )
    accepted = models.BooleanField(default=False)
    sequence = models.PositiveSmallIntegerField(null=True, blank=True)
    reason = models.CharField(
        max_length=255, blank=True, help_text="Why it was accepted or rejected."
    )
    revenue_minor = models.PositiveBigIntegerField(
        default=0, help_text="The shipment's own frozen quote total; never apportioned."
    )

    objects = OrganizationScopedQuerySet.as_manager()

    class Meta:
        ordering = ["plan_id", "sequence", "created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["plan", "shipment"], name="uq_plan_shipment"
            ),
        ]

    def __str__(self):
        state = "accepted" if self.accepted else f"rejected ({self.reason})"
        return f"{self.shipment.reference}: {state}"
