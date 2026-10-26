from rest_framework import serializers

from apps.accounts.models import Branch
from apps.fleet.models import Document, Driver, Vehicle


class _BranchValidationMixin:
    def validate(self, attrs):
        organization = attrs.get("organization") or getattr(
            self.instance, "organization", None
        )
        branch = attrs.get("branch")
        if branch is not None and organization is not None:
            if branch.organization_id != organization.pk:
                raise serializers.ValidationError(
                    {"branch": "Branch must belong to this organization."}
                )
        return attrs


class VehicleSerializer(_BranchValidationMixin, serializers.ModelSerializer):
    branch_name = serializers.CharField(source="branch.name", read_only=True)

    class Meta:
        model = Vehicle
        fields = [
            "id",
            "organization",
            "branch",
            "branch_name",
            "registration_number",
            "vehicle_class",
            "make",
            "model",
            "year",
            "seating_capacity",
            "baggage_capacity_kg",
            "cargo_weight_kg",
            "cargo_volume_m3",
            "status",
            "maintenance_due_on",
            "notes",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]


class DriverSerializer(_BranchValidationMixin, serializers.ModelSerializer):
    name = serializers.CharField(source="user.full_name", read_only=True, default="")
    branch_name = serializers.CharField(source="branch.name", read_only=True)

    class Meta:
        model = Driver
        fields = [
            "id",
            "user",
            "name",
            "organization",
            "branch",
            "branch_name",
            "license_number",
            "license_expires_on",
            "status",
            "working_hours_per_day",
            "notes",
            "created_at",
        ]
        read_only_fields = ["id", "name", "status", "created_at"]


class DocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Document
        fields = [
            "id",
            "organization",
            "entity_type",
            "entity_id",
            "doc_type",
            "reference",
            "issuer",
            "issued_on",
            "expires_on",
            "file",
            "status",
            "verified_by",
            "verified_at",
            "created_at",
        ]
        read_only_fields = ["id", "status", "verified_by", "verified_at", "created_at"]