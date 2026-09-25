from rest_framework import serializers

from apps.booking.models import (
    BoardingScan,
    Departure,
    DepartureException,
    Route,
    RouteStop,
    Ticket,
)


class RouteStopSerializer(serializers.ModelSerializer):
    place_name = serializers.CharField(source="place.name", read_only=True)

    class Meta:
        model = RouteStop
        fields = ["id", "route", "place", "place_name", "position", "notes"]


class RouteSerializer(serializers.ModelSerializer):
    stops = RouteStopSerializer(many=True, read_only=True)

    class Meta:
        model = Route
        fields = ["id", "organization", "name", "description", "is_active", "stops"]


class DepartureSerializer(serializers.ModelSerializer):
    route_name = serializers.CharField(source="route.name", read_only=True)

    class Meta:
        model = Departure
        fields = [
            "id",
            "organization",
            "route",
            "route_name",
            "vehicle",
            "vehicle_class",
            "departure_date",
            "departure_time",
            "capacity",
            "fare_minor",
            "currency",
            "status",
        ]


class TicketSerializer(serializers.ModelSerializer):
    boarding_info = serializers.SerializerMethodField()

    class Meta:
        model = Ticket
        fields = [
            "id",
            "departure",
            "reference",
            "token",
            "seat_number",
            "passenger_name",
            "passenger_phone",
            "passenger_id_number",
            "state",
            "boarded_at",
            "boarding_info",
        ]
        read_only_fields = fields

    def get_boarding_info(self, obj):
        return obj.boarding_info()


class BoardingScanSerializer(serializers.ModelSerializer):
    class Meta:
        model = BoardingScan
        fields = ["id", "ticket", "departure", "boarded", "scanner_device", "note", "created_at"]
        read_only_fields = fields


class DepartureExceptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = DepartureException
        fields = [
            "id",
            "departure",
            "kind",
            "reason",
            "reassigned_from_seat",
            "requires_customer_communication",
            "communicated_at",
            "created_at",
        ]
        read_only_fields = fields
