from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import Organization, Role, organization_ids_for
from apps.accounts.permissions import IsAuthenticated
from apps.audit.models import record_audit
from apps.charter.models import (
    CharterDeposit,
    CharterExtra,
    CharterReservation,
    HireRequest,
)
from apps.charter.services import (
    CharterError,
    add_extra,
    cancel_hire,
    check_availability,
    confirm_hire,
    create_hire_request,
    finalize_hire,
    issue_quote,
    record_deposit,
)

# "Staff issue a quote, reserve the vehicle and driver ... " (blueprint 8).
STAFF_ROLES = (Role.DISPATCHER, Role.OPERATOR_ADMIN, Role.PLATFORM_ADMIN)


class CharterReservationSerializer(serializers.ModelSerializer):
    class Meta:
        model = CharterReservation
        fields = [
            "id",
            "hire_request",
            "vehicle",
            "driver",
            "reserved_from",
            "reserved_to",
            "travel_buffer_minutes",
            "turnaround_minutes",
            "blocked_from",
            "blocked_to",
        ]
        read_only_fields = fields


class CharterExtraSerializer(serializers.ModelSerializer):
    class Meta:
        model = CharterExtra
        fields = ["id", "hire_request", "kind", "description", "price_minor", "is_permitted", "note"]
        read_only_fields = ["id"]


class CharterDepositSerializer(serializers.ModelSerializer):
    class Meta:
        model = CharterDeposit
        fields = ["id", "hire_request", "payment_attempt", "amount_minor", "currency", "note", "created_at"]
        read_only_fields = fields


class HireRequestSerializer(serializers.ModelSerializer):
    reservation = CharterReservationSerializer(read_only=True)
    extras = CharterExtraSerializer(many=True, read_only=True)
    quoted_total_minor = serializers.IntegerField(read_only=True)

    class Meta:
        model = HireRequest
        fields = [
            "id",
            "organization",
            "customer",
            "service",
            "route",
            "itinerary_notes",
            "start_at",
            "end_at",
            "vehicle_class",
            "passenger_count",
            "included_hours",
            "included_distance_km",
            "status",
            "quote",
            "quoted_total_minor",
            "deposit_total_minor",
            "extras_total_minor",
            "final_total_minor",
            "currency",
            "cancel_reason",
            "reservation",
            "extras",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "organization",
            "status",
            "quote",
            "deposit_total_minor",
            "extras_total_minor",
            "final_total_minor",
            "cancel_reason",
            "created_at",
        ]


def _err(exc):
    return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)


class _StaffMixin:
    def _require_staff(self, org_id):
        user = self.request.user
        if user and user.is_superuser:
            return
        if not user.memberships.filter(
            is_active=True, organization_id=org_id, role__in=STAFF_ROLES
        ).exists():
            raise PermissionDenied()


class HireRequestViewSet(
    _StaffMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = HireRequestSerializer
    permission_classes = [IsAuthenticated]
    queryset = HireRequest.objects.all()

    def get_queryset(self):
        return HireRequest.objects.filter(
            organization_id__in=organization_ids_for(self.request.user)
        ).select_related("service", "route", "customer", "quote")

    def create(self, request):
        # "A hire request specifies dates, itinerary, vehicle class, passenger
        # count and included hours or distance."
        from django.utils.dateparse import parse_datetime

        from apps.catalog.models import Service
        from apps.booking.models import Route

        organization = Organization.objects.filter(
            pk=request.data.get("organization"),
            pk__in=organization_ids_for(request.user),
        ).first()
        if organization is None:
            return Response({"detail": "A valid organization is required."}, status=400)
        # Staff raise hire requests (blueprint 8: "Staff issue a quote").
        self._require_staff(organization.pk)

        service = Service.objects.filter(
            pk=request.data.get("service"), service_type=Service.ServiceType.CHARTER
        ).first()
        if service is None:
            return Response(
                {"detail": "A valid charter service is required."}, status=400
            )
        start_at = parse_datetime(request.data.get("start_at") or "")
        end_at = parse_datetime(request.data.get("end_at") or "")
        if start_at is None or end_at is None:
            return Response(
                {"detail": "start_at and end_at must be ISO datetimes."}, status=400
            )
        route = None
        if request.data.get("route"):
            route = Route.objects.filter(pk=request.data["route"]).first()

        try:
            hire_request = create_hire_request(
                organization=organization,
                customer=request.user,
                service=service,
                start_at=start_at,
                end_at=end_at,
                vehicle_class=request.data.get("vehicle_class", ""),
                passenger_count=int(request.data.get("passenger_count", 0) or 0),
                route=route,
                itinerary_notes=request.data.get("itinerary_notes", ""),
                included_hours=request.data.get("included_hours"),
                included_distance_km=request.data.get("included_distance_km"),
            )
        except (CharterError, TypeError, ValueError) as exc:
            return _err(CharterError(str(exc)))
        return Response(
            self.get_serializer(hire_request).data, status=status.HTTP_201_CREATED
        )

    @action(detail=True, methods=["post"])
    def quote(self, request, pk=None):
        hire_request = self.get_object()
        self._require_staff(hire_request.organization_id)
        try:
            issue_quote(
                hire_request,
                total_minor=request.data.get("total_minor", 0),
                issued_by=request.user,
            )
        except (CharterError, TypeError) as exc:
            return _err(CharterError(str(exc)))
        return Response(self.get_serializer(hire_request).data)

    @action(detail=True, methods=["post"])
    def confirm(self, request, pk=None):
        hire_request = self.get_object()
        self._require_staff(hire_request.organization_id)
        from apps.fleet.models import Driver, Vehicle

        vehicle = Vehicle.objects.filter(pk=request.data.get("vehicle")).first()
        driver = Driver.objects.filter(pk=request.data.get("driver")).first()
        if vehicle is None or driver is None:
            return Response(
                {"detail": "A vehicle and a driver are required."}, status=400
            )
        try:
            reservation = confirm_hire(
                hire_request,
                vehicle=vehicle,
                driver=driver,
                travel_buffer_minutes=request.data.get("travel_buffer_minutes"),
                turnaround_minutes=request.data.get("turnaround_minutes"),
            )
        except CharterError as exc:
            return _err(exc)
        record_audit(
            actor=request.user,
            organization=hire_request.organization,
            action="charter.confirmed",
            entity_type="hire_request",
            entity_id=hire_request.pk,
            new_value={"vehicle_id": str(vehicle.pk), "driver_id": str(driver.pk)},
        )
        hire_request.refresh_from_db()
        return Response(
            self.get_serializer(hire_request).data, status=status.HTTP_201_CREATED
        )

    @action(detail=True, methods=["post"])
    def deposit(self, request, pk=None):
        hire_request = self.get_object()
        self._require_staff(hire_request.organization_id)
        try:
            record_deposit(
                hire_request,
                amount_minor=request.data.get("amount_minor", 0),
                recorded_by=request.user,
                note=request.data.get("note", ""),
            )
        except (CharterError, TypeError) as exc:
            return _err(CharterError(str(exc)))
        hire_request.refresh_from_db()
        return Response(self.get_serializer(hire_request).data)

    @action(detail=True, methods=["post"])
    def extras(self, request, pk=None):
        hire_request = self.get_object()
        self._require_staff(hire_request.organization_id)
        try:
            add_extra(
                hire_request,
                kind=request.data.get("kind", "general"),
                description=request.data.get("description", ""),
                price_minor=request.data.get("price_minor", 0),
                is_permitted=request.data.get("is_permitted", True),
                note=request.data.get("note", ""),
            )
        except (CharterError, TypeError) as exc:
            return _err(CharterError(str(exc)))
        hire_request.refresh_from_db()
        return Response(self.get_serializer(hire_request).data)

    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        hire_request = self.get_object()
        self._require_staff(hire_request.organization_id)
        try:
            finalize_hire(hire_request)
        except CharterError as exc:
            return _err(exc)
        return Response(self.get_serializer(hire_request).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        hire_request = self.get_object()
        self._require_staff(hire_request.organization_id)
        try:
            cancel_hire(hire_request, reason=request.data.get("reason", ""))
        except CharterError as exc:
            return _err(exc)
        return Response(self.get_serializer(hire_request).data)


class CharterAvailabilityView(APIView):
    """Read-only availability probe: which vehicles and drivers are free."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        from apps.charter.models import (
            DEFAULT_TRAVEL_BUFFER_MINUTES,
            DEFAULT_TURNAROUND_MINUTES,
        )

        start_at = request.query_params.get("start_at")
        end_at = request.query_params.get("end_at")
        if not start_at or not end_at:
            return Response(
                {"detail": "start_at and end_at are required."}, status=400
            )
        from django.utils.dateparse import parse_datetime

        parsed_start = parse_datetime(start_at)
        parsed_end = parse_datetime(end_at)
        if parsed_start is None or parsed_end is None:
            return Response({"detail": "Invalid ISO datetimes."}, status=400)
        try:
            vehicles, drivers = check_availability(
                vehicle_class=request.query_params.get("vehicle_class", ""),
                start_at=parsed_start,
                end_at=parsed_end,
                passenger_count=int(request.query_params.get("passenger_count", 0) or 0),
                travel_buffer_minutes=int(
                    request.query_params.get(
                        "travel_buffer_minutes", DEFAULT_TRAVEL_BUFFER_MINUTES
                    )
                ),
                turnaround_minutes=int(
                    request.query_params.get(
                        "turnaround_minutes", DEFAULT_TURNAROUND_MINUTES
                    )
                ),
            )
        except CharterError as exc:
            return _err(exc)
        return Response(
            {
                "vehicles": [
                    {
                        "id": str(v.pk),
                        "registration_number": v.registration_number,
                        "vehicle_class": v.vehicle_class,
                        "seating_capacity": v.seating_capacity,
                    }
                    for v in vehicles
                ],
                "drivers": [{"id": str(d.pk)} for d in drivers],
            }
        )
