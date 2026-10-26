import uuid
from datetime import date as date_cls

from django.db import transaction
from django.db.models import Q
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import (
    Branch,
    Role,
    branch_ids_for,
    default_branch_for,
    is_platform_admin,
    organization_ids_for,
)
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
        qs = qs.for_organizations(organization_ids_for(self.request.user))
        branch = self.request.query_params.get("branch")
        if branch and hasattr(qs.model, "branch"):
            qs = qs.filter(branch_id=branch)
        return qs

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
        qs = (
            Ticket.objects.select_related(
                "departure__route",
                "departure__organization",
                "departure__vehicle",
                "departure__branch",
                "selling_branch",
                "hold__order",
            )
            .prefetch_related(
                "departure__route__stops__place",
                "hold__segments__segment__from_place",
                "hold__segments__segment__to_place",
            )
        )
        if not self.request.user.is_superuser:
            org_ids = organization_ids_for(self.request.user)
            qs = qs.filter(departure__organization_id__in=org_ids)

        if not (
            self.request.user.is_superuser or is_platform_admin(self.request.user)
        ):
            visible_branches = branch_ids_for(self.request.user)
            admin_org_ids = list(
                self.request.user.memberships.filter(
                    is_active=True, role=Role.OPERATOR_ADMIN
                ).values_list("organization_id", flat=True)
            )
            if self.request.query_params.get("view") == "operations":
                qs = qs.filter(
                    Q(departure__organization_id__in=admin_org_ids)
                    | Q(departure__branch_id__in=visible_branches)
                )
            else:
                qs = qs.filter(
                    Q(departure__organization_id__in=admin_org_ids)
                    | Q(selling_branch_id__in=visible_branches)
                    | Q(hold__order__customer=self.request.user)
                )

        branch_id = self.request.query_params.get("branch")
        if branch_id:
            try:
                branch_uuid = uuid.UUID(branch_id)
            except (TypeError, ValueError):
                return qs.none()
            qs = qs.filter(selling_branch_id=branch_uuid)

        operating_branch = self.request.query_params.get("operating_branch")
        if operating_branch:
            try:
                branch_uuid = uuid.UUID(operating_branch)
            except (TypeError, ValueError):
                return qs.none()
            qs = qs.filter(departure__branch_id=branch_uuid)

        order_id = self.request.query_params.get("order")
        if order_id:
            try:
                order_uuid = uuid.UUID(order_id)
            except (TypeError, ValueError):
                return qs.none()
            qs = qs.filter(hold__order_id=order_uuid)

        departure_id = self.request.query_params.get("departure")
        if departure_id:
            try:
                departure_uuid = uuid.UUID(departure_id)
            except (TypeError, ValueError):
                return qs.none()
            qs = qs.filter(departure_id=departure_uuid)

        return qs


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


class SeatMapView(APIView):
    """Seat map for a departure/leg showing free/taken/held seats."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        departure = Departure.objects.filter(pk=request.query_params.get("departure")).first()
        if departure is None:
            return Response({"detail": "Departure not found."}, status=status.HTTP_404_NOT_FOUND)
        from_place = Place.objects.filter(pk=request.query_params.get("from_place")).first()
        to_place = Place.objects.filter(pk=request.query_params.get("to_place")).first()
        if from_place is None or to_place is None:
            return Response(
                {"detail": "Valid from_place and to_place are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        from apps.booking.services import _occupied_seat_numbers, _resolve_segment_positions

        positions = _resolve_segment_positions(departure, from_place, to_place)
        taken = _occupied_seat_numbers(departure, positions)
        capacity = departure.capacity or 0
        seats = []
        for seat in range(1, capacity + 1):
            state = "taken" if seat in taken else "free"
            seats.append({"number": seat, "state": state})
        return Response(
            {
                "departure": str(departure.pk),
                "from_place": str(from_place.pk),
                "to_place": str(to_place.pk),
                "capacity": capacity,
                "seats": seats,
                "free": capacity - len(taken),
            }
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

        selling_branch = None
        branch_id = request.data.get("selling_branch")
        if branch_id:
            selling_branch = Branch.objects.filter(
                pk=branch_id, organization=departure.organization
            ).first()
            if selling_branch is None:
                return Response(
                    {"detail": "Selling branch must belong to this organisation."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if not is_platform_admin(request.user) and not request.user.is_superuser:
                if str(selling_branch.pk) not in branch_ids_for(request.user):
                    return Response(
                        {"detail": "You cannot sell from this branch."},
                        status=status.HTTP_403_FORBIDDEN,
                    )
        elif request.user.memberships.filter(
            is_active=True, organization=departure.organization
        ).exists():
            selling_branch = default_branch_for(
                request.user, departure.organization_id
            )

        try:
            order = create_booking_order(
                departure=departure,
                holds=holds,
                passenger_name=request.data.get("passenger_name", ""),
                passenger_phone=request.data.get("passenger_phone", ""),
                passenger_id_number=request.data.get("passenger_id_number", ""),
                customer=request.user,
                selling_branch=selling_branch,
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
