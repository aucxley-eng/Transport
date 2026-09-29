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
from apps.goods.consolidation import (
    FreightError,
    assess_coverage,
    confirm_departure,
    empty_running_report,
    find_backhaul_opportunities,
    propose_consolidation,
    publish_plan,
    supersede_plan,
    validate_plan,
)
from apps.goods.insertion import (
    accept_insertion,
    amend_manifest,
    complete_stop as mark_stop_complete,
    decline_insertion,
    insert_consignment,
    request_insertion,
    route_state,
)
from apps.goods.models import (
    ConsolidationPlan,
    CustodyEvent,
    FreightTrip,
    InsertionRequest,
    Manifest,
    Package,
    Shipment,
    ShipmentException,
)
from apps.goods.serializers import (
    ConsolidationPlanSerializer,
    ConsolidationStopSerializer,
    CustodyEventSerializer,
    FreightTripSerializer,
    InsertionRequestSerializer,
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


def _is_assigned_driver(trip, user):
    """True when the user is the driver on this trip, and nobody else."""
    if not (user and user.is_authenticated) or trip.driver_id is None:
        return False
    return trip.driver.user_id == user.pk


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
                load_type=request.data.get("load_type"),
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


    @action(detail=True, methods=["post"], url_path="consolidation/propose")
    def propose(self, request, pk=None):
        """Propose the next consolidation plan version for this trip."""
        trip = self.get_object()
        self._require_ops()
        shipments = Shipment.objects.filter(pk__in=request.data.get("shipments") or [])
        try:
            plan = propose_consolidation(
                organization=trip.organization,
                trip=trip,
                shipments=list(shipments),
                proposed_by=request.user,
            )
        except FreightError as exc:
            return _err(exc)
        return Response(
            ConsolidationPlanSerializer(plan).data, status=status.HTTP_201_CREATED
        )

    @action(detail=True, methods=["get"], url_path="consolidation")
    def consolidation(self, request, pk=None):
        """Every plan version for this trip, newest first."""
        trip = self.get_object()
        plans = trip.consolidation_plans.prefetch_related(
            "stops", "shipments__shipment"
        )
        return Response(ConsolidationPlanSerializer(plans, many=True).data)

    @action(detail=True, methods=["get"])
    def coverage(self, request, pk=None):
        """The coverage floor: revenue on the truck vs the cost of running it."""
        trip = self.get_object()
        assessment = assess_coverage(trip)
        return Response(
            {
                "trip": str(trip.pk),
                "committed_minor": assessment["committed_minor"],
                "trip_cost_minor": assessment["trip_cost_minor"],
                "shortfall_minor": assessment["shortfall_minor"],
                "surplus_minor": assessment["surplus_minor"],
                "covered": assessment["covered"],
            }
        )

    @action(detail=True, methods=["post"], url_path="depart")
    def depart(self, request, pk=None):
        """The departure gate: released manifest, published plan, covered cost."""
        trip = self.get_object()
        self._require_ops()
        try:
            trip = confirm_departure(trip)
        except FreightError as exc:
            return _err(exc)
        record_audit(
            actor=request.user,
            organization=trip.organization,
            action="goods.trip_departed",
            entity_type="freight_trip",
            entity_id=trip.pk,
        )
        return Response(FreightTripSerializer(trip).data)

    @action(detail=True, methods=["get"])
    def route(self, request, pk=None):
        """What the driver has done, what is still promised, and the coverage."""
        trip = self.get_object()
        try:
            state = route_state(trip)
        except FreightError as exc:
            return _err(exc)
        coverage = state["coverage"]
        return Response(
            {
                "trip": str(trip.pk),
                "plan": ConsolidationPlanSerializer(state["plan"]).data,
                "completed_stops": len(state["completed_stops"]),
                "remaining_stops": len(state["remaining_stops"]),
                "committed_minor": coverage["committed_minor"],
                "trip_cost_minor": coverage["trip_cost_minor"],
                "covered": coverage["covered"],
            }
        )

    @action(detail=True, methods=["post"])
    def insertion(self, request, pk=None):
        """Insert a consignment into a trip that is already on the road."""
        trip = self.get_object()
        self._require_ops()
        shipment = Shipment.objects.filter(
            pk=request.data.get("shipment"),
            organization=trip.organization,
        ).first()
        if shipment is None:
            return Response({"detail": "shipment is required."}, status=400)
        try:
            plan = insert_consignment(
                organization=trip.organization,
                trip=trip,
                shipment=shipment,
                actor=request.user,
                reason=request.data.get("reason", ""),
            )
        except FreightError as exc:
            return _err(exc)
        return Response(
            ConsolidationPlanSerializer(plan).data, status=status.HTTP_201_CREATED
        )

    @action(detail=True, methods=["post"], url_path="insertion-requests")
    def insertion_requests(self, request, pk=None):
        """Offer a consignment to this trip's driver."""
        trip = self.get_object()
        self._require_ops()
        shipment = Shipment.objects.filter(
            pk=request.data.get("shipment"),
            organization=trip.organization,
        ).first()
        if shipment is None:
            return Response({"detail": "shipment is required."}, status=400)
        try:
            row = request_insertion(
                organization=trip.organization,
                trip=trip,
                shipment=shipment,
                requested_by=request.user,
                reason=request.data.get("reason", ""),
            )
        except FreightError as exc:
            return _err(exc)
        return Response(
            InsertionRequestSerializer(row).data, status=status.HTTP_201_CREATED
        )


class ConsolidationPlanViewSet(
    _OrgScopedViewSet,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
):
    queryset = ConsolidationPlan.objects.all()
    serializer_class = ConsolidationPlanSerializer
    permission_classes = [IsAuthenticated]

    @action(detail=True, methods=["post"])
    def validate(self, request, pk=None):
        plan = self.get_object()
        self._require_ops()
        try:
            plan = validate_plan(plan)
        except FreightError as exc:
            return _err(exc)
        return Response(ConsolidationPlanSerializer(plan).data)

    @action(detail=True, methods=["post"])
    def publish(self, request, pk=None):
        plan = self.get_object()
        self._require_ops()
        try:
            plan = publish_plan(plan, reason=request.data.get("reason", ""))
        except FreightError as exc:
            return _err(exc)
        return Response(ConsolidationPlanSerializer(plan).data)

    @action(detail=True, methods=["post"])
    def supersede(self, request, pk=None):
        plan = self.get_object()
        self._require_ops()
        try:
            new_plan = supersede_plan(
                plan, reason=request.data.get("reason", ""), proposed_by=request.user
            )
        except FreightError as exc:
            return _err(exc)
        return Response(
            ConsolidationPlanSerializer(new_plan).data, status=status.HTTP_201_CREATED
        )

    @action(detail=True, methods=["post"], url_path="stops/(?P<stop_id>[^/.]+)/complete")
    def complete_stop(self, request, pk=None, stop_id=None):
        """Mark a stop served. A fact, not an edit: it is never undone."""
        plan = self.get_object()
        # The driver in the field is the one who knows the stop is done, so the
        # trip's own driver may record it as well as dispatch.
        if not self._is_ops(request.user) and not _is_assigned_driver(
            plan.trip, request.user
        ):
            self._require_ops()
        stop = plan.stops.filter(pk=stop_id).first()
        if stop is None:
            return Response({"detail": "That stop is not on the plan."}, status=404)
        try:
            stop = mark_stop_complete(
                plan=plan, stop=stop, actor=request.user, note=request.data.get("note", "")
            )
        except FreightError as exc:
            return _err(exc)
        return Response(ConsolidationStopSerializer(stop).data)


class InsertionRequestViewSet(
    _OrgScopedViewSet,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
):
    """The driver's insertion offers (blueprint 11: version-checked acceptance)."""

    queryset = InsertionRequest.objects.all()
    serializer_class = InsertionRequestSerializer
    permission_classes = [IsAuthenticated]

    def create(self, request, *args, **kwargs):
        """Offer a consignment to a running trip's driver.

        Same service call as the nested trip action, so both spellings of the
        request measure the same thing and record the same reason.
        """
        payload = InsertionRequestSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        trip = FreightTrip.objects.filter(
            pk=payload.validated_data["trip"].pk,
            organization_id__in=organization_ids_for(request.user),
        ).first()
        if trip is None:
            return Response({"detail": "trip is required."}, status=400)
        self._require_ops()
        shipment = Shipment.objects.filter(
            pk=payload.validated_data["shipment"].pk, organization=trip.organization
        ).first()
        if shipment is None:
            return Response({"detail": "shipment is required."}, status=400)
        try:
            row = request_insertion(
                organization=trip.organization,
                trip=trip,
                shipment=shipment,
                requested_by=request.user,
                reason=payload.validated_data.get("reason", ""),
            )
        except FreightError as exc:
            return _err(exc)
        return Response(
            InsertionRequestSerializer(row).data, status=status.HTTP_201_CREATED
        )

    @action(detail=True, methods=["post"])
    def accept(self, request, pk=None):
        row = self.get_object()
        try:
            plan = accept_insertion(
                request=row, driver=request.user, reason=request.data.get("reason", "")
            )
        except FreightError as exc:
            return _err(exc)
        return Response(
            ConsolidationPlanSerializer(plan).data, status=status.HTTP_201_CREATED
        )

    @action(detail=True, methods=["post"])
    def decline(self, request, pk=None):
        row = self.get_object()
        try:
            row = decline_insertion(
                request=row, driver=request.user, reason=request.data.get("reason", "")
            )
        except FreightError as exc:
            return _err(exc)
        return Response(InsertionRequestSerializer(row).data)


class BackhaulViewSet(_OrgScopedViewSet, mixins.ListModelMixin):
    """Vehicles that could carry cargo home instead of running empty."""

    queryset = FreightTrip.objects.none()
    serializer_class = FreightTripSerializer
    permission_classes = [IsAuthenticated]

    def list(self, request):
        from apps.catalog.models import Place

        origin = Place.objects.filter(pk=request.query_params.get("origin")).first()
        destination = Place.objects.filter(
            pk=request.query_params.get("destination")
        ).first()
        organization = _organization_for(request, None)
        try:
            opportunities = find_backhaul_opportunities(
                organization=organization,
                origin=origin,
                destination=destination,
                window_start=request.query_params.get("window_start"),
                window_end=request.query_params.get("window_end"),
            )
        except FreightError as exc:
            return _err(exc)
        return Response(
            [
                {
                    "trip": str(item["trip"].pk),
                    "vehicle": str(item["vehicle"].pk),
                    "registration_number": item["vehicle"].registration_number,
                    "free_weight_kg": str(item["free_weight_kg"]),
                    "free_volume_m3": str(item["free_volume_m3"]),
                    "available_from": item["available_from"],
                    "must_depart_by": item["must_depart_by"],
                }
                for item in opportunities
            ]
        )


class EmptyRunningReportViewSet(_OrgScopedViewSet, mixins.ListModelMixin):
    """The blueprint's empty-running measure (p. 23)."""

    queryset = FreightTrip.objects.none()
    serializer_class = FreightTripSerializer
    permission_classes = [IsAuthenticated]

    def list(self, request):
        organization = _organization_for(request, None)
        report = empty_running_report(
            organization=organization, since=request.query_params.get("since")
        )
        return Response(
            {
                "since": report["since"],
                "trips": report["trips"],
                "measured_distance_km": str(report["measured_distance_km"]),
                "empty_distance_km": str(report["empty_distance_km"]),
                "empty_running_ratio": str(report["empty_running_ratio"]),
            }
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

    @action(detail=True, methods=["post"])
    def amend(self, request, pk=None):
        """Add mid-route packages as a new manifest version.

        The released manifest is evidence of what the vehicle was checked
        against, so it is superseded rather than edited.
        """
        manifest = self.get_object()
        self._require_ops()
        packages = Package.objects.filter(
            pk__in=request.data.get("packages") or [],
            shipment__organization=manifest.organization,
        )
        try:
            amended = amend_manifest(
                trip=manifest.trip,
                packages=list(packages),
                actor=request.user,
                reason=request.data.get("reason", ""),
            )
        except FreightError as exc:
            return _err(exc)
        return Response(ManifestSerializer(amended).data, status=status.HTTP_201_CREATED)


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
