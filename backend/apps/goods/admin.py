from django.contrib import admin

from apps.goods.models import (
    CustodyEvent,
    FreightTrip,
    Manifest,
    ManifestLine,
    Package,
    Shipment,
    ShipmentEvidence,
    ShipmentException,
    ShipmentLeg,
)


@admin.register(Shipment)
class ShipmentAdmin(admin.ModelAdmin):
    list_display = ["reference", "organization", "sender_name", "status", "agreed_total_minor"]
    list_filter = ["status", "is_return", "measurement_basis", "organization"]
    search_fields = ["reference", "sender_name", "recipient_name"]


@admin.register(Package)
class PackageAdmin(admin.ModelAdmin):
    list_display = ["label", "shipment", "weight_kg", "status"]
    list_filter = ["status", "is_fragile", "organization"]
    search_fields = ["label"]


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
    list_display = ["__str__", "trip", "status", "reconciled_at", "released_at"]
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
    list_display = ["shipment", "kind", "otp_verified", "signature_ref", "created_at"]
    list_filter = ["kind", "organization"]


@admin.register(ShipmentException)
class ShipmentExceptionAdmin(admin.ModelAdmin):
    list_display = ["shipment", "kind", "next_action", "status", "created_at"]
    list_filter = ["kind", "next_action", "status", "organization"]
