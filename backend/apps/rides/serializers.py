from rest_framework import serializers

from apps.rides.models import (
    DriverOffer,
    DriverShift,
    RideCancellation,
    RideRequest,
    ServiceZone,
    Trip,
    TripEvent,
    TripIncident,
)


class ServiceZoneSerializer(serializers.ModelSerializer):
    class Meta:
        model = ServiceZone
        fields = ["id", "name", "center", "radius_km", "is_active"]


class DriverShiftSerializer(serializers.ModelSerializer):
    class Meta:
        model = DriverShift
        fields = ["id", "driver", "zone", "starts_at", "ends_at", "status"]


class RideRequestSerializer(serializers.ModelSerializer):
    pickup_name = serializers.CharField(source="pickup.name", read_only=True)
    destination_name = serializers.CharField(source="destination.name", read_only=True)
    resolution_label = serializers.CharField(source="get_resolution_display", read_only=True)

    class Meta:
        model = RideRequest
        fields = [
            "id",
            "organization",
            "customer",
            "service",
            "quote",
            "pickup",
            "pickup_name",
            "destination",
            "destination_name",
            "vehicle_class",
            "zone",
            "status",
            "resolution",
            "resolution_label",
            "created_at",
        ]
        read_only_fields = ["id", "status", "resolution", "created_at"]


class DriverOfferSerializer(serializers.ModelSerializer):
    class Meta:
        model = DriverOffer
        fields = ["id", "ride_request", "driver", "state", "expires_at", "created_at"]
        read_only_fields = ["id", "state", "created_at"]


class TripSerializer(serializers.ModelSerializer):
    """Contact details (customer phone) are included only when the requester is
    the assigned driver, an org admin for the trip's organization, or the
    customer (blueprint: "Drivers see contacts only for assigned [trips]")."""

    customer_phone = serializers.SerializerMethodField()
    state_label = serializers.CharField(source="get_state_display", read_only=True)

    class Meta:
        model = Trip
        fields = [
            "id",
            "ride_request",
            "driver",
            "vehicle",
            "state",
            "state_label",
            "customer_phone",
            "started_at",
            "ended_at",
            "pending_destination",
        ]
        read_only_fields = fields

    def get_customer_phone(self, obj):
        from apps.rides.services import viewer_may_see_contact

        if viewer_may_see_contact(obj, getattr(self.context.get("request"), "user", None)):
            return obj.ride_request.customer.phone
        return None


class TripEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = TripEvent
        fields = ["id", "ride_request", "kind", "payload", "created_at"]
        read_only_fields = fields


class RideCancellationSerializer(serializers.ModelSerializer):
    class Meta:
        model = RideCancellation
        fields = ["id", "ride_request", "reason", "evidence", "fee_minor", "is_no_show", "created_at"]
        read_only_fields = fields


class TripIncidentSerializer(serializers.ModelSerializer):
    class Meta:
        model = TripIncident
        fields = ["id", "trip", "kind", "description", "escalation_path", "status", "created_at"]
        read_only_fields = fields
