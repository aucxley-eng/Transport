from datetime import date as date_cls

from django.db import transaction
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import Role, organization_ids_for
from apps.accounts.permissions import IsAuthenticated, IsOrganizationAdmin
from apps.audit.models import record_audit
from apps.booking.models import (
    Departure,
    Route,
    Ticket,
)
from apps.booking.serializers import (
    DepartureExceptionSerializer,
    DepartureSerializer,
    RouteSerializer,
    TicketSerializer,
)
from apps.booking.services import (
    BookingError,
    NoAvailability,
    acquire_hold,
    available_seats,
    board_from_scan,
    create_booking_order,
    handle_vehicle_substitution,
    search_departures,
)
from apps.catalog.models import Place
from apps.fleet.models import Vehicle


class _OrgScopedViewSet(viewsets.ModelViewSet):
    """Query set always constrained to the caller's organizations (operator
    isolation, blueprint 3)."""

    ADMIN_ROLES = (Role.OPERATOR_ADMIN, Role.PLATFORM_ADMIN)

    def get_queryset(self):
        qs = super().get_queryset()
        return qs.for_organizations(organization_ids_for(self.request.user))

    def _require_admin(self, org_id):
        from rest_framework.exceptions import PermissionDenied

        user = self.request.user
        if user and user.is_superuser:
            return
        is_admin = user.memberships.filter(
            is_active=True, organization_id=org_id, role__in=self.ADMIN_ROLES
        ).exists()
        if not is_admin:
            raise PermissionDenied()

    def perform_create(self, serializer):
        org = serializer.validated_data.get("organization")
        if org is not None:
            self._require_admin(org.pk)
        serializer.save()


class RouteViewSet(_OrgScopedViewSet):
    queryset = Route.objects.all()
    serializer_class = RouteSerializer

    def get_permissions(self):
        if self.action in ("create", "update", "partial_update", "destroy"):
            return [IsOrganizationAdmin()]
        return [IsAuthenticated()]


class DepartureViewSet(_OrgScopedViewSet):
    queryset = Departure.objects.select_related("route", "vehicle")
    serializer_class = DepartureSerializer

    def get_permissions(self):
        if self.action in (
            "create",
            "update",
            "partial_update",
            "destroy",
            "substitute_vehicle",
        ):
            return [IsOrganizationAdmin()]
        return [IsAuthenticated()]

    @action(detail=True, methods=["post"])
    @transaction.atomic
    def substitute_vehicle(self, request, pk=None):
        """Swap the vehicle. A smaller replacement creates reassignment
        exceptions requiring customer communication (blueprint 7)."""
        departure = self.get_object()
        vehicle_id = request.data.get("vehicle")
        vehicle = Vehicle.objects.filter(pk=vehicle_id).first()
        if vehicle is None:
            return Response(
                {"detail": "Vehicle not found."}, status=status.HTTP_404_NOT_FOUND
            )
        _, exceptions = handle_vehicle_substitution(departure=departure, new_vehicle=vehicle)
        record_audit(
            actor=request.user,
            organization=departure.organization,
            action="departure.vehicle_substituted",
            entity_type="departure",
            entity_id=departure.pk,
            new_value={
                "vehicle": str(vehicle.pk),
                "capacity": departure.capacity,
                "exceptions": len(exceptions),
            },
        )
        return Response(
            {
                "departure": DepartureSerializer(departure).data,
                "exceptions": DepartureExceptionSerializer(exceptions, many=True).data,
            }
        )


class TicketViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    queryset = Ticket.objects.select_related("departure")
    serializer_class = TicketSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = Ticket.objects.select_related("departure")
        if self.request.user.is_superuser:
            return qs
        org_ids = organization_ids_for(self.request.user)
        return qs.filter(departure__organization_id__in=org_ids)


class SearchView(APIView):
    """Blueprint 7 search: route, date and passengers -> available departures
    with per-leg seat availability."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        origin = request.query_params.get("from", "")
        destination = request.query_params.get("to", "")
        date_str = request.query_params.get("date")
        travel_date = None
        if date_str:
            try:
                travel_date = date_cls.fromisoformat(date_str)
            except ValueError:
                return Response(
                    {"detail": "date must be YYYY-MM-DD."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        from_place = Place.objects.filter(name__iexact=origin).first() if origin else None
        to_place = (
            Place.objects.filter(name__iexact=destination).first() if destination else None
        )
        departures = search_departures(
            origin_name=origin, destination_name=destination, date=travel_date
        )
        results = []
        for departure in departures[:100]:
            entry = DepartureSerializer(departure).data
            if from_place and to_place:
                try:
                    free, _ = available_seats(departure, from_place, to_place)
                    entry["available_seats"] = free
                except BookingError:
                    entry["available_seats"] = []
            else:
                entry["available_seats"] = []
            results.append(entry)
        return Response(results)


class HoldView(APIView):
    """Create an atomic timed seat hold (blueprint 7). Group bookings acquire
    all requested seats together or receive a clear partial-availability
    response before payment."""

    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request):
        departure = Departure.objects.filter(pk=request.data.get("departure")).first()
        if departure is None:
            return Response(
                {"detail": "Departure not found."}, status=status.HTTP_404_NOT_FOUND
            )
        from_place = Place.objects.filter(pk=request.data.get("from_place")).first()
        to_place = Place.objects.filter(pk=request.data.get("to_place")).first()
        if from_place is None or to_place is None:
            return Response(
                {"detail": "Valid from_place and to_place are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            holds = acquire_hold(
                departure=departure,
                from_place=from_place,
                to_place=to_place,
                seats=int(request.data.get("seats", 1)),
                customer=request.user,
            )
        except NoAvailability as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        except BookingError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            {
                "holds": [
                    {
                        "id": str(h.pk),
                        "seat_number": h.seat_number,
                        "expires_at": h.expires_at.isoformat(),
                    }
                    for h in holds
                ]
            },
            status=status.HTTP_201_CREATED,
        )


class CheckoutView(APIView):
    """Convert held seats into a pay-ready Order (blueprint 7 flow:
    select seat -> hold -> pay -> ticket)."""

    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request):
        from apps.booking.models import SeatHold

        departure = Departure.objects.filter(pk=request.data.get("departure")).first()
        if departure is None:
            return Response(
                {"detail": "Departure not found."}, status=status.HTTP_404_NOT_FOUND
            )
        hold_ids = request.data.get("hold_ids") or []
        holds = list(SeatHold.objects.filter(pk__in=hold_ids))
        try:
            order = create_booking_order(
                departure=departure,
                holds=holds,
                passenger_name=request.data.get("passenger_name", ""),
                passenger_phone=request.data.get("passenger_phone", ""),
                passenger_id_number=request.data.get("passenger_id_number", ""),
                customer=request.user,
            )
        except BookingError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            {
                "order_id": str(order.pk),
                "total_minor": order.total_minor,
                "currency": order.currency,
            },
            status=status.HTTP_201_CREATED,
        )


class BoardView(APIView):
    """Scan a ticket and mark boarded. Checks departure, ticket state and
    previous boarding (blueprint 7)."""

    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request):
        departure = Departure.objects.filter(pk=request.data.get("departure")).first()
        try:
            ticket = board_from_scan(
                ticket_reference=request.data.get("reference", ""),
                token=request.data.get("token", ""),
                departure=departure,
                scanner_device=request.data.get("scanner_device", ""),
            )
        except BookingError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        record_audit(
            actor=request.user,
            organization=ticket.departure.organization,
            action="ticket.boarded",
            entity_type="ticket",
            entity_id=ticket.pk,
            new_value={"reference": ticket.reference},
        )
        return Response(TicketSerializer(ticket).data)
