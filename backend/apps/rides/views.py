from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.models import Role, organization_ids_for
from apps.accounts.permissions import IsAuthenticated
from apps.audit.models import record_audit
from apps.catalog.models import Place, Service
from apps.fleet.models import Driver
from apps.rides.models import (
    DriverOffer,
    DriverShift,
    RideRequest,
    ServiceZone,
    Trip,
)
from apps.rides.serializers import (
    DriverOfferSerializer,
    DriverShiftSerializer,
    RideRequestSerializer,
    ServiceZoneSerializer,
    TripIncidentSerializer,
    TripSerializer,
)
from apps.rides.services import (
    RideError,
    accept_destination_change,
    accept_offer,
    change_destination,
    complete_trip,
    create_ride_request,
    decline_offer,
    dispatch_offers,
    fail_matching,
    mark_arrived,
    report_incident,
    verify_pickup,
)


class _RideError(Exception):
    pass


def _err(exc):
    return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)


class RideRequestViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = RideRequestSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if not user.is_authenticated:
            return RideRequest.objects.none()
        qs = RideRequest.objects.select_related("pickup", "destination", "service")
        if user.is_superuser:
            return qs
        # A customer sees their own; an org admin sees their org's; a driver
        # sees rides assigned to them.
        from django.db.models import Q

        return qs.filter(
            Q(customer=user)
            | Q(organization_id__in=list(organization_ids_for(user)))
            | Q(trip__driver__user=user)
        )

    def create(self, request):
        pickup = Place.objects.filter(pk=request.data.get("pickup")).first()
        destination = Place.objects.filter(pk=request.data.get("destination")).first()
        service = Service.objects.filter(
            pk=request.data.get("service"), service_type=Service.ServiceType.RIDE
        ).first()
        if service is None:
            return Response(
                {"detail": "A valid on-demand ride service is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            ride = create_ride_request(
                customer=request.user,
                pickup=pickup,
                destination=destination,
                service=service,
                vehicle_class=request.data.get("vehicle_class", ""),
            )
        except RideError as exc:
            return _err(exc)
        return Response(
            RideRequestSerializer(ride).data, status=status.HTTP_201_CREATED
        )

    @action(detail=True, methods=["post"], url_path="send-offers")
    def send_offers(self, request, pk=None):
        ride = self.get_object()
        if not (request.user.is_superuser or self._is_dispatcher(request.user)):
            return Response({"detail": "Dispatcher privileges required."}, status=403)
        try:
            offers = dispatch_offers(ride)
        except RideError as exc:
            return _err(exc)
        return Response(
            {
                "offers": DriverOfferSerializer(offers, many=True).data,
                "status": ride.status,
            }
        )

    @action(detail=True, methods=["post"])
    def fail(self, request, pk=None):
        ride = self.get_object()
        resolution = request.data.get("resolution", RideRequest.Resolution.RETRY)
        if resolution not in RideRequest.Resolution.values:
            return Response({"detail": "Invalid resolution."}, status=400)
        fail_matching(ride, resolution)
        return Response(RideRequestSerializer(ride).data)

    @staticmethod
    def _is_dispatcher(user):
        return user.memberships.filter(
            is_active=True,
            role__in=[Role.DISPATCHER, Role.OPERATOR_ADMIN, Role.PLATFORM_ADMIN],
        ).exists()


class DriverOfferViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    serializer_class = DriverOfferSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if not user.is_authenticated:
            return DriverOffer.objects.none()
        return DriverOffer.objects.filter(driver__user=user)

    @action(detail=True, methods=["post"])
    def accept(self, request, pk=None):
        offer = self.get_object()
        try:
            trip = accept_offer(offer)
        except RideError as exc:
            return _err(exc)
        return Response(TripSerializer(trip).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def decline(self, request, pk=None):
        offer = self.get_object()
        try:
            decline_offer(offer)
        except RideError as exc:
            return _err(exc)
        return Response(DriverOfferSerializer(offer).data)


class TripViewSet(mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    serializer_class = TripSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if not user.is_authenticated:
            return Trip.objects.none()
        qs = Trip.objects.select_related("ride_request", "driver", "vehicle")
        if user.is_superuser:
            return qs
        # Visible to the assigned driver and to the customer who booked it.
        from django.db.models import Q

        return qs.filter(Q(driver__user=user) | Q(ride_request__customer=user))

    @action(detail=True, methods=["post"])
    def arrive(self, request, pk=None):
        try:
            trip = mark_arrived(self.get_object())
        except RideError as exc:
            return _err(exc)
        return Response(TripSerializer(trip).data)

    @action(detail=True, methods=["post"])
    def verify_pin(self, request, pk=None):
        try:
            trip = verify_pickup(self.get_object(), request.data.get("pin", ""))
        except RideError as exc:
            return _err(exc)
        return Response(TripSerializer(trip).data)

    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        try:
            trip = complete_trip(self.get_object())
        except RideError as exc:
            return _err(exc)
        record_audit(
            actor=request.user,
            organization=trip.ride_request.organization,
            action="trip.completed",
            entity_type="trip",
            entity_id=trip.pk,
        )
        return Response(TripSerializer(trip).data)

    @action(detail=True, methods=["post"])
    def change_destination(self, request, pk=None):
        trip = self.get_object()
        new_destination = Place.objects.filter(pk=request.data.get("destination")).first()
        try:
            quote = change_destination(trip, new_destination)
        except RideError as exc:
            return _err(exc)
        return Response(
            {
                "revised_estimate_id": str(quote.pk),
                "revised_estimate_minor": quote.total_minor,
                "requires_customer_acceptance": True,
            }
        )

    @action(detail=True, methods=["post"])
    def accept_destination(self, request, pk=None):
        try:
            trip = accept_destination_change(self.get_object())
        except RideError as exc:
            return _err(exc)
        return Response(TripSerializer(trip).data)

    @action(detail=True, methods=["post"])
    def incident(self, request, pk=None):
        trip = self.get_object()
        incident = report_incident(
            trip,
            kind=request.data.get("kind", "general"),
            description=request.data.get("description", ""),
            escalation_path=request.data.get("escalation_path", ""),
        )
        return Response(
            TripIncidentSerializer(incident).data, status=status.HTTP_201_CREATED
        )


class DriverShiftViewSet(
    mixins.ListModelMixin, mixins.CreateModelMixin, viewsets.GenericViewSet
):
    serializer_class = DriverShiftSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if not user.is_authenticated:
            return DriverShift.objects.none()
        return DriverShift.objects.filter(driver__user=user)

    def perform_create(self, serializer):
        driver = Driver.objects.filter(user=self.request.user).first()
        if driver is None:
            raise RideError("Only an approved driver has a profile.")
        serializer.save(driver=driver)


class ServiceZoneViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    queryset = ServiceZone.objects.filter(is_active=True)
    serializer_class = ServiceZoneSerializer
    permission_classes = [IsAuthenticated]
