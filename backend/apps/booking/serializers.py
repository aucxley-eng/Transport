from rest_framework import serializers

from apps.accounts.models import Branch
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
    branch_name = serializers.CharField(source="branch.name", read_only=True)

    class Meta:
        model = Route
        fields = [
            "id",
            "organization",
            "branch",
            "branch_name",
            "name",
            "description",
            "is_active",
            "stops",
        ]

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


class DepartureSerializer(serializers.ModelSerializer):
    route_name = serializers.CharField(source="route.name", read_only=True)
    branch_name = serializers.CharField(source="branch.name", read_only=True)

    class Meta:
        model = Departure
        fields = [
            "id",
            "organization",
            "branch",
            "branch_name",
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

    def create(self, validated_data):
        route = validated_data.get("route")
        if validated_data.get("branch") is None:
            if route is not None and route.branch_id:
                validated_data["branch"] = route.branch
            else:
                validated_data["branch"] = (
                    Branch.objects.filter(
                        organization=validated_data["organization"],
                        is_active=True,
                    )
                    .order_by("-is_head_office", "name")
                    .first()
                )
        return super().create(validated_data)


class TicketSerializer(serializers.ModelSerializer):
    boarding_info = serializers.SerializerMethodField()
    document = serializers.SerializerMethodField()

    class Meta:
        model = Ticket
        fields = [
            "id",
            "departure",
            "selling_branch",
            "reference",
            "token",
            "seat_number",
            "passenger_name",
            "passenger_phone",
            "passenger_id_number",
            "state",
            "boarded_at",
            "boarding_info",
            "document",
        ]
        read_only_fields = fields

    def get_boarding_info(self, obj):
        return obj.boarding_info()

    def get_document(self, obj):
        """Everything a printed receipt or boarding ticket needs, in one read.

        The ticket itself stores only the passenger and the seat. Operator,
        vehicle and route detail are joined here so a counter printer never has
        to reconstruct a trip from several endpoints.
        """
        departure = obj.departure
        route = departure.route
        organization = departure.organization
        vehicle = departure.vehicle

        from_place = None
        to_place = None
        hold = obj.hold
        if hold is not None:
            segments = list(
                hold.segments.select_related(
                    "segment__from_place", "segment__to_place"
                ).order_by("segment__position")
            )
            if segments:
                from_place = segments[0].segment.from_place
                to_place = segments[-1].segment.to_place
        if from_place is None or to_place is None:
            stops = list(route.stops.all())
            if stops:
                from_place = from_place or stops[0].place
                to_place = to_place or stops[-1].place

        payment = None
        order = hold.order if hold is not None else None
        if order is not None:
            attempt = (
                order.payment_attempts.filter(state="success")
                .order_by("-created_at")
                .first()
            )
            if attempt is not None:
                payment = {
                    "provider": attempt.provider,
                    "reference": attempt.provider_reference,
                    "amount_minor": attempt.expected_amount_minor,
                    "currency": attempt.currency,
                    "collected_at": attempt.created_at.isoformat(),
                    "note": attempt.note,
                }

        def branch_payload(branch):
            if branch is None:
                return None
            return {
                "id": str(branch.pk),
                "name": branch.name,
                "code": branch.code,
            }

        return {
            "operator": {
                "name": organization.name,
                "address": organization.address,
                "contact_phone": organization.contact_phone,
                "kra_pin": organization.kra_pin,
            },
            "selling_branch": branch_payload(obj.selling_branch),
            "operating_branch": branch_payload(departure.branch),
            "order_id": str(order.pk) if order is not None else None,
            "issued_at": obj.created_at.isoformat(),
            "trip": {
                "route": route.name,
                "from_place": from_place.name if from_place else "",
                "to_place": to_place.name if to_place else "",
                "departure_date": departure.departure_date.isoformat(),
                "departure_time": departure.departure_time.isoformat(),
                "vehicle_registration": (
                    vehicle.registration_number if vehicle is not None else ""
                ),
                "vehicle_class": departure.vehicle_class or "",
                "vehicle_make": vehicle.make if vehicle is not None else "",
                "vehicle_model": vehicle.model if vehicle is not None else "",
                "fare_minor": departure.fare_minor,
                "currency": departure.currency,
            },
            "payment": payment,
        }


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
