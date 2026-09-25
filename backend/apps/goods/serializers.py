from rest_framework import serializers

from apps.goods.models import (
    CustodyEvent,
    FreightTrip,
    Manifest,
    ManifestLine,
    MeasurementBasis,
    Package,
    Shipment,
    ShipmentEvidence,
    ShipmentException,
    ShipmentLeg,
)


class ShipmentLegSerializer(serializers.ModelSerializer):
    stop_name = serializers.CharField(source="stop.name", read_only=True)

    class Meta:
        model = ShipmentLeg
        fields = [
            "id",
            "trip",
            "stop",
            "stop_name",
            "sequence",
            "status",
            "planned_at",
            "actual_at",
        ]


class PackageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Package
        fields = [
            "id",
            "shipment",
            "label",
            "description",
            "weight_kg",
            "length_cm",
            "width_cm",
            "height_cm",
            "volume_m3",
            "volumetric_weight_kg",
            "declared_value_minor",
            "handling_flags",
            "is_fragile",
            "status",
            "current_location",
            "created_at",
        ]


class CustodyEventSerializer(serializers.ModelSerializer):
    package_label = serializers.CharField(source="package.label", read_only=True)
    corrects_label = serializers.SerializerMethodField()

    class Meta:
        model = CustodyEvent
        fields = [
            "id",
            "package",
            "package_label",
            "event_type",
            "giving_party",
            "receiving_party",
            "location",
            "location_text",
            "device_event_time",
            "server_receipt_time",
            "evidence",
            "note",
            "corrects_event",
            "corrects_label",
            "correction_reason",
            "recorded_by",
            "created_at",
        ]
        read_only_fields = fields

    def get_corrects_label(self, obj):
        if obj.corrects_event_id is None:
            return ""
        return obj.corrects_event.package.label


class ShipmentEvidenceSerializer(serializers.ModelSerializer):
    is_recipient_verified = serializers.SerializerMethodField()

    class Meta:
        model = ShipmentEvidence
        fields = [
            "id",
            "kind",
            "recipient_name",
            "recipient_id_type",
            "recipient_id_last4",
            "otp_verified",
            "signature_ref",
            "is_delegate",
            "is_recipient_verified",
            "photos",
            "notes",
            "device_event_time",
            "recorded_by",
            "created_at",
        ]
        read_only_fields = ["is_recipient_verified", "created_at"]

    def get_is_recipient_verified(self, obj):
        return obj.is_recipient_verified()


class ShipmentExceptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ShipmentException
        fields = [
            "id",
            "shipment",
            "package",
            "kind",
            "next_action",
            "status",
            "detail",
            "resolution_note",
            "resolved_at",
            "created_at",
        ]


class ShipmentSerializer(serializers.ModelSerializer):
    package_count = serializers.IntegerField(read_only=True)
    measurement_basis = serializers.ChoiceField(
        choices=MeasurementBasis.choices, required=False, allow_blank=True
    )
    open_exceptions = serializers.SerializerMethodField()

    class Meta:
        model = Shipment
        fields = [
            "id",
            "reference",
            "organization",
            "service",
            "sender",
            "sender_name",
            "sender_phone",
            "recipient_name",
            "recipient_phone",
            "collection",
            "destination",
            "package_count",
            "total_weight_kg",
            "total_volume_m3",
            "volumetric_weight_kg",
            "declared_value_minor",
            "handling_needs",
            "service_window_start",
            "service_window_end",
            "quote",
            "measurement_basis",
            "measurement_snapshot",
            "terms_snapshot",
            "agreed_total_minor",
            "is_return",
            "return_of",
            "status",
            "open_exceptions",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "reference",
            "package_count",
            "total_weight_kg",
            "total_volume_m3",
            "volumetric_weight_kg",
            "declared_value_minor",
            "quote",
            "measurement_basis",
            "measurement_snapshot",
            "terms_snapshot",
            "agreed_total_minor",
            "is_return",
            "return_of",
            "open_exceptions",
            "created_at",
        ]

    def get_open_exceptions(self, obj):
        return obj.exceptions.filter(status=ShipmentException.Status.OPEN).count()


class ManifestLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = ManifestLine
        fields = [
            "id",
            "package",
            "compartment",
            "label",
            "expected_weight_kg",
            "scan_state",
            "scanned_at",
            "note",
        ]


class ManifestSerializer(serializers.ModelSerializer):
    lines = ManifestLineSerializer(many=True, read_only=True)
    is_reconciled = serializers.BooleanField(read_only=True)

    class Meta:
        model = Manifest
        fields = [
            "id",
            "trip",
            "status",
            "lines",
            "is_reconciled",
            "reconciled_at",
            "released_at",
            "released_by",
            "created_at",
        ]


class FreightTripSerializer(serializers.ModelSerializer):
    capacity = serializers.SerializerMethodField()

    class Meta:
        model = FreightTrip
        fields = [
            "id",
            "vehicle",
            "driver",
            "service",
            "origin",
            "destination",
            "planned_start",
            "planned_end",
            "status",
            "capacity",
            "notes",
            "created_at",
        ]

    def get_capacity(self, obj):
        from apps.goods.services import cargo_capacity

        capacity = cargo_capacity(obj)
        return {
            "capacity_weight_kg": str(capacity["capacity_weight_kg"]),
            "capacity_volume_m3": str(capacity["capacity_volume_m3"]),
            "committed_weight_kg": str(capacity["committed_weight_kg"]),
            "free_weight_kg": str(capacity["free_weight_kg"]),
            "compartments": [
                {
                    "id": str(entry["compartment"].pk),
                    "name": entry["compartment"].name,
                    "used_weight_kg": str(entry["used_weight_kg"]),
                    "free_weight_kg": str(entry["free_weight_kg"]),
                }
                for entry in capacity["compartments"]
            ],
        }
