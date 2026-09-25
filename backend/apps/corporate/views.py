from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from apps.accounts.models import Role, organization_ids_for
from apps.accounts.permissions import IsAuthenticated
from apps.booking.models import Departure
from apps.corporate.models import (
    CorporateMember,
    CorporatePolicy,
    CostCenter,
    ShuttleRosterEntry,
    ShuttleRun,
    ShuttleTemplate,
    TravelRequest,
)
from apps.corporate.services import (
    CorporateError,
    approve_travel_request,
    budget_remaining,
    cost_center_spent,
    deactivate_member,
    generate_shuttle_runs,
    is_corporate_manager,
    reject_travel_request,
    remove_from_roster,
    roster_member,
    submit_travel_request,
)

MANAGER_ROLES = (Role.CORPORATE_MANAGER, Role.OPERATOR_ADMIN, Role.PLATFORM_ADMIN)


class CorporatePolicySerializer(serializers.ModelSerializer):
    class Meta:
        model = CorporatePolicy
        fields = [
            "id",
            "organization",
            "permitted_services",
            "approval_threshold_minor",
            "budget_period_start",
            "budget_period_end",
            "is_active",
        ]


class CostCenterSerializer(serializers.ModelSerializer):
    spent_minor = serializers.SerializerMethodField()
    remaining_minor = serializers.SerializerMethodField()

    class Meta:
        model = CostCenter
        fields = [
            "id",
            "organization",
            "code",
            "name",
            "budget_minor",
            "currency",
            "is_active",
            "spent_minor",
            "remaining_minor",
        ]
        read_only_fields = ["spent_minor", "remaining_minor"]

    def get_spent_minor(self, obj):
        return cost_center_spent(obj)

    def get_remaining_minor(self, obj):
        return budget_remaining(obj)


class CorporateMemberSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source="user.username", read_only=True)
    is_eligible = serializers.SerializerMethodField()

    class Meta:
        model = CorporateMember
        fields = [
            "id",
            "organization",
            "user",
            "username",
            "employee_ref",
            "cost_center",
            "joined_on",
            "left_on",
            "is_active",
            "is_eligible",
            "notes",
        ]
        read_only_fields = ["is_eligible"]

    def get_is_eligible(self, obj):
        return obj.is_eligible()


class TravelRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = TravelRequest
        fields = [
            "id",
            "organization",
            "member",
            "cost_center",
            "departure",
            "purpose",
            "estimated_minor",
            "currency",
            "status",
            "requires_approval",
            "decided_by",
            "decided_at",
            "decision_note",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "organization",
            "status",
            "requires_approval",
            "decided_by",
            "decided_at",
            "decision_note",
            "created_at",
        ]


class ShuttleTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = ShuttleTemplate
        fields = [
            "id",
            "organization",
            "name",
            "route",
            "service",
            "departure_time",
            "weekdays",
            "valid_from",
            "valid_until",
            "capacity",
            "fare_minor",
            "currency",
            "is_active",
        ]


class ShuttleRunSerializer(serializers.ModelSerializer):
    template_name = serializers.CharField(source="template.name", read_only=True)
    route_name = serializers.CharField(source="departure.route.name", read_only=True)
    departure_time = serializers.TimeField(source="departure.departure_time", read_only=True)
    roster_count = serializers.SerializerMethodField()

    class Meta:
        model = ShuttleRun
        fields = [
            "id",
            "organization",
            "template",
            "template_name",
            "departure",
            "route_name",
            "run_date",
            "departure_time",
            "roster_count",
        ]

    def get_roster_count(self, obj):
        return obj.roster.filter(
            state__in=[ShuttleRosterEntry.State.PLANNED, ShuttleRosterEntry.State.BOARDED]
        ).count()


class ShuttleRosterEntrySerializer(serializers.ModelSerializer):
    employee_ref = serializers.CharField(source="member.employee_ref", read_only=True)

    class Meta:
        model = ShuttleRosterEntry
        fields = ["id", "run", "member", "employee_ref", "seat_number", "state"]
        read_only_fields = ["id", "state"]


def _err(exc):
    return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)


class _ManagerGuardMixin:
    """Corporate decisions are restricted to the travel manager role."""

    def _require_manager(self, org_id):
        user = self.request.user
        if user and user.is_superuser:
            return
        if not user.memberships.filter(
            is_active=True, organization_id=org_id, role__in=MANAGER_ROLES
        ).exists():
            raise PermissionDenied()


class _OrgScopedViewSet(_ManagerGuardMixin, viewsets.ModelViewSet):
    """Query set always constrained to the caller's organizations (operator
    isolation, blueprint 3)."""

    def get_queryset(self):
        qs = super().get_queryset()
        return qs.for_organizations(organization_ids_for(self.request.user))

    def perform_create(self, serializer):
        org = serializer.validated_data.get("organization")
        if org is not None:
            self._require_manager(org.pk)
        serializer.save()


class CorporatePolicyViewSet(mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    serializer_class = CorporatePolicySerializer
    permission_classes = [IsAuthenticated]
    queryset = CorporatePolicy.objects.all()

    def get_queryset(self):
        return CorporatePolicy.objects.filter(
            organization_id__in=organization_ids_for(self.request.user)
        )

    def retrieve(self, request, *args, **kwargs):
        org_ids = organization_ids_for(request.user)
        policy = CorporatePolicy.objects.filter(
            organization_id__in=org_ids
        ).first()
        if policy is None:
            return Response(
                {"detail": "No corporate policy is configured."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(self.get_serializer(policy).data)


class CostCenterViewSet(_OrgScopedViewSet):
    serializer_class = CostCenterSerializer
    queryset = CostCenter.objects.all()


class CorporateMemberViewSet(_OrgScopedViewSet):
    serializer_class = CorporateMemberSerializer
    queryset = CorporateMember.objects.all()

    def get_queryset(self):
        user = self.request.user
        qs = CorporateMember.objects.select_related("user", "cost_center")
        qs = qs.for_organizations(organization_ids_for(user))
        if is_corporate_manager(user):
            return qs
        # An employee sees only their own record.
        return qs.filter(user=user)

    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        member = self.get_object()
        self._require_manager(member.organization_id)
        deactivate_member(member)
        return Response(self.get_serializer(member).data)


class TravelRequestViewSet(
    _ManagerGuardMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = TravelRequestSerializer
    permission_classes = [IsAuthenticated]
    queryset = TravelRequest.objects.all()

    def get_queryset(self):
        user = self.request.user
        qs = TravelRequest.objects.select_related("member", "cost_center", "departure")
        qs = qs.for_organizations(organization_ids_for(user))
        if is_corporate_manager(user):
            return qs
        return qs.filter(member__user=user)

    def create(self, request):
        member = CorporateMember.objects.filter(
            pk=request.data.get("member"),
            organization_id__in=organization_ids_for(request.user),
        ).first()
        if member is None:
            return Response({"detail": "A valid employee is required."}, status=400)
        departure = None
        if request.data.get("departure"):
            # Scheduled departures are public bookable inventory owned by the
            # operator that runs them, so a corporate employee may request a
            # seat on any operator's departure. Permitted-services and budget
            # rules are still enforced in the service layer.
            departure = Departure.objects.filter(pk=request.data["departure"]).first()
        try:
            travel_request = submit_travel_request(
                member=member,
                departure=departure,
                purpose=request.data.get("purpose", ""),
            )
        except CorporateError as exc:
            return _err(exc)
        return Response(
            self.get_serializer(travel_request).data, status=status.HTTP_201_CREATED
        )

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        travel_request = self.get_object()
        self._require_manager(travel_request.organization_id)
        try:
            travel_request = approve_travel_request(travel_request, approver=request.user)
        except CorporateError as exc:
            return _err(exc)
        return Response(self.get_serializer(travel_request).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        travel_request = self.get_object()
        self._require_manager(travel_request.organization_id)
        try:
            travel_request = reject_travel_request(
                travel_request, approver=request.user, note=request.data.get("note", "")
            )
        except CorporateError as exc:
            return _err(exc)
        return Response(self.get_serializer(travel_request).data)


class ShuttleTemplateViewSet(_OrgScopedViewSet):
    serializer_class = ShuttleTemplateSerializer
    queryset = ShuttleTemplate.objects.all()

    @action(detail=True, methods=["post"])
    def generate(self, request, pk=None):
        template = self.get_object()
        self._require_manager(template.organization_id)
        start = request.data.get("start")
        end = request.data.get("end")
        if not start or not end:
            return Response({"detail": "start and end dates are required."}, status=400)
        try:
            runs = generate_shuttle_runs(template, start=start, end=end)
        except CorporateError as exc:
            return _err(exc)
        return Response(
            ShuttleRunSerializer(runs, many=True).data, status=status.HTTP_201_CREATED
        )


class ShuttleRunViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    serializer_class = ShuttleRunSerializer
    permission_classes = [IsAuthenticated]
    queryset = ShuttleRun.objects.all()

    def get_queryset(self):
        return ShuttleRun.objects.filter(
            organization_id__in=organization_ids_for(self.request.user)
        ).select_related("template", "departure")

    @action(detail=True, methods=["get", "post"])
    def roster(self, request, pk=None):
        run = self.get_object()
        if request.method == "GET":
            entries = run.roster.select_related("member").all()
            return Response(ShuttleRosterEntrySerializer(entries, many=True).data)

        member = CorporateMember.objects.filter(
            pk=request.data.get("member"), organization_id=run.organization_id
        ).first()
        if member is None:
            return Response({"detail": "A valid employee is required."}, status=400)
        try:
            entry = roster_member(run, member, seat_number=request.data.get("seat_number"))
        except CorporateError as exc:
            return _err(exc)
        return Response(
            ShuttleRosterEntrySerializer(entry).data, status=status.HTTP_201_CREATED
        )

    @action(detail=True, methods=["post"])
    def unroster(self, request, pk=None):
        run = self.get_object()
        member = CorporateMember.objects.filter(
            pk=request.data.get("member"), organization_id=run.organization_id
        ).first()
        if member is None:
            return Response({"detail": "A valid employee is required."}, status=400)
        try:
            remove_from_roster(run, member)
        except CorporateError as exc:
            return _err(exc)
        return Response(status=status.HTTP_204_NO_CONTENT)
