from django.contrib import admin

from apps.goods.models import (
    ConsolidationPlan,
    ConsolidationStop,
    CustodyEvent,
    FreightTrip,
    InsertionRequest,
    Manifest,
    ManifestLine,
    Package,
    PlanShipment,
    Shipment,
    ShipmentEvidence,
    ShipmentException,
    ShipmentLeg,
)


@admin.register(Shipment)
class ShipmentAdmin(admin.ModelAdmin):
    list_display = [
        "reference",
        "organization",
        "sender_name",
        "status",
        "load_type",
        "agreed_total_minor",
    ]
    list_filter = ["status", "is_return", "load_type", "measurement_basis", "organization"]
    search_fields = ["reference", "sender_name", "recipient_name"]


@admin.register(Package)
class PackageAdmin(admin.ModelAdmin):
    list_display = [
        "label",
        "shipment",
        "weight_kg",
        "chargeable_weight_kg",
        "recipient_name",
        "recipient_place",
        "status",
    ]
    list_filter = ["status", "is_fragile", "recipient_place", "organization"]
    search_fields = ["label", "recipient_name"]


@admin.register(FreightTrip)
class FreightTripAdmin(admin.ModelAdmin):
    list_display = ["__str__", "vehicle", "driver", "status", "planned_start"]
    list_filter = ["status", "organization"]


@admin.register(ShipmentLeg)
class ShipmentLegAdmin(admin.ModelAdmin):
    list_display = ["shipment", "sequence", "stop", "trip", "status"]
    list_filter = ["status", "organization"]


@admin.register(Manifest)
class ManifestAdmin(admin.ModelAdmin):
    # A mid-route pickup amends the manifest by publishing a new version, so the
    # released one stays on record; version and supersedes are what say so.
    list_display = ["__str__", "trip", "version", "status", "reconciled_at", "released_at"]
    list_filter = ["status", "organization"]


@admin.register(ManifestLine)
class ManifestLineAdmin(admin.ModelAdmin):
    list_display = ["manifest", "label", "compartment", "scan_state", "expected_weight_kg"]
    list_filter = ["scan_state", "organization"]


@admin.register(CustodyEvent)
class CustodyEventAdmin(admin.ModelAdmin):
    # Append-only: read-only everywhere, and the change form cannot write.
    list_display = [
        "package",
        "event_type",
        "device_event_time",
        "server_receipt_time",
        "corrects_event",
    ]
    list_filter = ["event_type", "organization"]
    search_fields = ["package__label"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ShipmentEvidence)
class ShipmentEvidenceAdmin(admin.ModelAdmin):
    list_display = ["shipment", "package", "kind", "otp_verified", "signature_ref", "created_at"]
    list_filter = ["kind", "organization"]


@admin.register(ShipmentException)
class ShipmentExceptionAdmin(admin.ModelAdmin):
    list_display = ["shipment", "package", "kind", "next_action", "status", "created_at"]
    list_filter = ["kind", "next_action", "status", "organization"]


@admin.register(ConsolidationPlan)
class ConsolidationPlanAdmin(admin.ModelAdmin):
    list_display = ["trip", "version", "status", "change_reason", "published_at"]
    list_filter = ["status", "organization"]
    search_fields = ["trip__vehicle__registration_number", "change_reason", "explanation"]


@admin.register(ConsolidationStop)
class ConsolidationStopAdmin(admin.ModelAdmin):
    list_display = [
        "plan",
        "sequence",
        "place",
        "window_start",
        "window_end",
        "is_completed",
        "completed_at",
    ]
    list_filter = ["is_completed", "organization"]


@admin.register(InsertionRequest)
class InsertionRequestAdmin(admin.ModelAdmin):
    list_display = [
        "trip",
        "shipment",
        "status",
        "tolerance_minutes",
        "plan_version",
        "requested_by",
        "decided_by",
        "decided_at",
    ]
    list_filter = ["status", "organization"]
    search_fields = ["shipment__reference", "trip__vehicle__registration_number"]


@admin.register(PlanShipment)
class PlanShipmentAdmin(admin.ModelAdmin):
    list_display = ["plan", "sequence", "shipment", "accepted", "reason", "revenue_minor"]
    list_filter = ["accepted", "organization"]
    search_fields = ["shipment__reference"]
