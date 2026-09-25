"""Freight API (blueprint 9, 10).

Writes are gated on organization administration plus an operations role, and the
custody endpoint is the only way to write custody history: there is no update or
delete route, so "branch staff cannot change recorded custody history" holds at
the API surface as well as in the service layer.
"""

from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from apps.accounts.models import Role, organization_ids_for
from apps.accounts.permissions import IsAuthenticated
from apps.audit.models import record_audit
from apps.catalog.models import Place, Quote, Service
from apps.fleet.models import Driver, Vehicle
from apps.goods.models import (
    CustodyEvent,
    FreightTrip,
    Manifest,
    Package,
    Shipment,
    ShipmentException,
)
from apps.goods.serializers import (
    CustodyEventSerializer,
    FreightTripSerializer,
    ManifestSerializer,
    PackageSerializer,
    ShipmentExceptionSerializer,
    ShipmentSerializer,
)
from apps.goods.services import (
    GoodsError,
    build_manifest,
    create_freight_trip,
    create_return_shipment,
    create_shipment,
    quote_shipment,
    raise_exception,
    reconcile_manifest,
    record_custody_event,
    record_evidence,
    release_manifest,
    resolve_exception,
)

# Roles that may load, dispatch or handle freight. Branch staff are deliberately
# absent: they can read and hand over, but cannot rewrite custody.
FREIGHT_OPS_ROLES = (
    Role.DISPATCHER,
    Role.OPERATOR_ADMIN,
    Role.PLATFORM_ADMIN,
)


def _err(exc):
    return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)


class _OrgScopedViewSet(viewsets.GenericViewSet):
    """Query set always constrained to the caller's organizations (operator
    isolation, blueprint 3)."""

    def get_queryset(self):
        qs = super().get_queryset()
        return qs.for_organizations(organization_ids_for(self.request.user))

    def _is_ops(self, user):
        if not (user and user.is_authenticated):
            return False
        if user.is_superuser:
            return True
        return user.memberships.filter(
            is_active=True, role__in=FREIGHT_OPS_ROLES
        ).exists()

    def _require_ops(self):
        if not self._is_ops(self.request.user):
            raise PermissionDenied("Freight operations privileges required.")


class ShipmentViewSet(
    _OrgScopedViewSet,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
):
    queryset = Shipment.objects.all()
    serializer_class = ShipmentSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .select_related("service", "collection", "destination")
        )

    def create(self, request):
        """Book from an accepted quote.

        The quote is the source of truth for the measurements and basis, so a
        caller cannot book different numbers from the ones they were priced on.
        """
        self._require_ops()
        quote = Quote.objects.filter(pk=request.data.get("quote")).first()
        if quote is None:
            return Response({"detail": "A quote is required."}, status=400)
        if quote.status == Quote.Status.VALID:
            try:
                quote.accept()
            except ValueError as exc:
                return _err(GoodsError(str(exc)))
        service = Service.objects.filter(pk=quote.service_id).first()
        organization = _organization_for(request, quote.organization_id)
        collection = Place.objects.filter(pk=request.data.get("collection")).first()
        destination = Place.objects.filter(pk=request.data.get("destination")).first()
        try:
            shipment = create_shipment(
                quote=quote,
                organization=organization,
                service=service,
                sender=request.user if request.data.get("sender") else None,
                sender_name=request.data.get("sender_name", ""),
                sender_phone=request.data.get("sender_phone", ""),
                recipient_name=request.data.get("recipient_name", ""),
                recipient_phone=request.data.get("recipient_phone", ""),
                collection=collection,
                destination=destination,
                service_window_start=request.data.get("service_window_start"),
                service_window_end=request.data.get("service_window_end"),
                packages=request.data.get("packages") or [],
            )
        except GoodsError as exc:
            return _err(exc)
        record_audit(
            actor=request.user,
            organization=organization,
            action="goods.shipment_booked",
            entity_type="shipment",
            entity_id=shipment.pk,
            new_value={"reference": shipment.reference},
        )
        return Response(ShipmentSerializer(shipment).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["post"], url_path="quote")
    def quote(self, request):
        service = Service.objects.filter(
            pk=request.data.get("service"), service_type=Service.ServiceType.FREIGHT
        ).first()
        organization = _organization_for(request, None)
        collection = Place.objects.filter(pk=request.data.get("collection")).first()
        destination = Place.objects.filter(pk=request.data.get("destination")).first()
        try:
            quote = quote_shipment(
                organization=organization,
                service=service,
                sender_name=request.data.get("sender_name", ""),
                sender_phone=request.data.get("sender_phone", ""),
                recipient_name=request.data.get("recipient_name", ""),
                recipient_phone=request.data.get("recipient_phone", ""),
                collection=collection,
                destination=destination,
                total_weight_kg=request.data.get("total_weight_kg"),
                total_volume_m3=request.data.get("total_volume_m3"),
                service_window_start=request.data.get("service_window_start"),
                service_window_end=request.data.get("service_window_end"),
                measurement_basis=request.data.get(
                    "measurement_basis", "actual_weight"
                ),
                total_minor=request.data.get("total_minor"),
                declared_value_minor=request.data.get("declared_value_minor") or 0,
                handling_needs=request.data.get("handling_needs") or [],
            )
        except GoodsError as exc:
            return _err(exc)
        return Response(
            {
                "quote": quote.pk,
                "total_minor": quote.total_minor,
                "currency": quote.currency,
                "basis": quote.basis,
                "measurements": quote.request_payload,
                "expires_at": quote.expires_at,
            },
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["get"])
    def custody(self, request, pk=None):
        shipment = self.get_object()
        events = CustodyEvent.objects.filter(package__shipment=shipment).select_related(
            "package", "corrects_event"
        )
        return Response(CustodyEventSerializer(events, many=True).data)

    @action(detail=True, methods=["get"])
    def packages(self, request, pk=None):
        shipment = self.get_object()
        return Response(
            PackageSerializer(shipment.packages.all(), many=True).data
        )

    @action(detail=True, methods=["post"])
    def evidence(self, request, pk=None):
        shipment = self.get_object()
        try:
            evidence = record_evidence(
                shipment,
                kind=request.data.get("kind", ""),
                photos=request.data.get("photos") or [],
                notes=request.data.get("notes", ""),
                recipient_name=request.data.get("recipient_name", ""),
                recipient_id_type=request.data.get("recipient_id_type", ""),
                recipient_id_last4=request.data.get("recipient_id_last4", ""),
                otp_verified=bool(request.data.get("otp_verified", False)),
                signature_ref=request.data.get("signature_ref", ""),
                is_delegate=bool(request.data.get("is_delegate", False)),
                device_event_time=request.data.get("device_event_time"),
                recorded_by=request.user,
            )
        except GoodsError as exc:
            return _err(exc)
        from apps.goods.serializers import ShipmentEvidenceSerializer

        return Response(
            ShipmentEvidenceSerializer(evidence).data, status=status.HTTP_201_CREATED
        )

    @action(detail=True, methods=["post"])
    def exceptions(self, request, pk=None):
        shipment = self.get_object()
        self._require_ops()
        package = Package.objects.filter(
            pk=request.data.get("package"), shipment=shipment
        ).first()
        try:
            exception = raise_exception(
                shipment,
                kind=request.data.get("kind", ""),
                next_action=request.data.get("next_action", ""),
                package=package,
                detail=request.data.get("detail", ""),
                raised_by=request.user,
            )
        except GoodsError as exc:
            return _err(exc)
        return Response(
            ShipmentExceptionSerializer(exception).data, status=status.HTTP_201_CREATED
        )

    @action(detail=True, methods=["post"], url_path="return")
    def return_(self, request, pk=None):
        shipment = self.get_object()
        self._require_ops()
        packages = Package.objects.filter(pk__in=request.data.get("packages") or [])
        try:
            returned = create_return_shipment(
                shipment,
                packages=list(packages),
                reason=request.data.get("reason", ""),
            )
        except GoodsError as exc:
            return _err(exc)
        return Response(ShipmentSerializer(returned).data, status=status.HTTP_201_CREATED)


class ShipmentExceptionViewSet(
    _OrgScopedViewSet,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
):
    queryset = ShipmentException.objects.all()
    serializer_class = ShipmentExceptionSerializer
    permission_classes = [IsAuthenticated]

    @action(detail=True, methods=["post"])
    def resolve(self, request, pk=None):
        exception = self.get_object()
        self._require_ops()
        try:
            exception = resolve_exception(
                exception, note=request.data.get("note", "")
            )
        except GoodsError as exc:
            return _err(exc)
        return Response(ShipmentExceptionSerializer(exception).data)


class PackageViewSet(
    _OrgScopedViewSet,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
):
    queryset = Package.objects.all()
    serializer_class = PackageSerializer
    permission_classes = [IsAuthenticated]

    @action(detail=True, methods=["post"], url_path="custody")
    def custody(self, request, pk=None):
        """Append a custody event.

        Append-only: there is no PUT/PATCH/DELETE on this route, and a
        correction must name the event it corrects and give a reason.
        """
        package = self.get_object()
        self._require_ops()
        corrects = CustodyEvent.objects.filter(
            pk=request.data.get("corrects_event")
        ).first()
        location = Place.objects.filter(pk=request.data.get("location")).first()
        try:
            event = record_custody_event(
                package,
                event_type=request.data.get("event_type", ""),
                device_event_time=request.data.get("device_event_time"),
                giving_party=request.data.get("giving_party", ""),
                receiving_party=request.data.get("receiving_party", ""),
                location=location,
                location_text=request.data.get("location_text", ""),
                evidence=request.data.get("evidence") or {},
                note=request.data.get("note", ""),
                corrects=corrects,
                correction_reason=request.data.get("correction_reason", ""),
                recorded_by=request.user,
            )
        except GoodsError as exc:
            return _err(exc)
        record_audit(
            actor=request.user,
            organization=package.organization,
            action="goods.custody_appended",
            entity_type="custody_event",
            entity_id=event.pk,
            new_value={"package": package.label, "event_type": event.event_type},
        )
        return Response(
            CustodyEventSerializer(event).data, status=status.HTTP_201_CREATED
        )

    @action(detail=True, methods=["get"], url_path="custody-history")
    def custody_history(self, request, pk=None):
        package = self.get_object()
        return Response(
            CustodyEventSerializer(
                package.custody_events.all(), many=True
            ).data
        )


class FreightTripViewSet(
    _OrgScopedViewSet,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
):
    queryset = FreightTrip.objects.all()
    serializer_class = FreightTripSerializer
    permission_classes = [IsAuthenticated]

    def create(self, request):
        self._require_ops()
        vehicle = Vehicle.objects.filter(pk=request.data.get("vehicle")).first()
        driver = Driver.objects.filter(pk=request.data.get("driver")).first()
        service = Service.objects.filter(pk=request.data.get("service")).first()
        organization = _organization_for(request, vehicle.organization_id if vehicle else None)
        try:
            trip = create_freight_trip(
                organization=organization,
                vehicle=vehicle,
                driver=driver,
                service=service,
                origin=Place.objects.filter(pk=request.data.get("origin")).first(),
                destination=Place.objects.filter(pk=request.data.get("destination")).first(),
                planned_start=request.data.get("planned_start"),
                planned_end=request.data.get("planned_end"),
            )
        except GoodsError as exc:
            return _err(exc)
        return Response(
            FreightTripSerializer(trip).data, status=status.HTTP_201_CREATED
        )


class ManifestViewSet(
    _OrgScopedViewSet,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
):
    queryset = Manifest.objects.all()
    serializer_class = ManifestSerializer
    permission_classes = [IsAuthenticated]

    @action(detail=False, methods=["post"], url_path="build")
    def build(self, request):
        trip = FreightTrip.objects.filter(pk=request.data.get("trip")).first()
        if trip is None:
            return Response({"detail": "A trip is required."}, status=400)
        self._require_ops()
        packages = Package.objects.filter(pk__in=request.data.get("packages") or [])
        try:
            manifest = build_manifest(
                trip=trip, packages=list(packages), built_by=request.user
            )
        except GoodsError as exc:
            return _err(exc)
        return Response(ManifestSerializer(manifest).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def reconcile(self, request, pk=None):
        manifest = self.get_object()
        self._require_ops()
        try:
            manifest = reconcile_manifest(
                manifest=manifest,
                scanned=request.data.get("scanned") or {},
                scanned_by=request.user,
            )
        except GoodsError as exc:
            return _err(exc)
        return Response(ManifestSerializer(manifest).data)

    @action(detail=True, methods=["post"])
    def release(self, request, pk=None):
        manifest = self.get_object()
        self._require_ops()
        try:
            manifest = release_manifest(manifest=manifest, released_by=request.user)
        except GoodsError as exc:
            return _err(exc)
        record_audit(
            actor=request.user,
            organization=manifest.organization,
            action="goods.manifest_released",
            entity_type="manifest",
            entity_id=manifest.pk,
        )
        return Response(ManifestSerializer(manifest).data)


def _organization_for(request, fallback_id):
    """The organization a write targets: the request body, else the caller's
    only membership, else the fallback from an existing record."""
    from apps.accounts.models import Organization

    requested = request.data.get("organization")
    if requested:
        return Organization.objects.filter(pk=requested).first()
    if fallback_id:
        return Organization.objects.filter(pk=fallback_id).first()
    org_ids = list(organization_ids_for(request.user))
    if len(org_ids) == 1:
        return Organization.objects.filter(pk=org_ids[0]).first()
    return None
