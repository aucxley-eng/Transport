from django.db import transaction
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import (
    STAFF_ROLES,
    Branch,
    Membership,
    Organization,
    Role,
    is_platform_admin,
    organization_ids_for,
)
from apps.accounts.permissions import IsOrganizationAdmin, IsPlatformAdmin
from apps.accounts.serializers import (
    BranchSerializer,
    MembershipCreateSerializer,
    MeSerializer,
    OrganizationSerializer,
    RegisterSerializer,
    StaffHireSerializer,
    StaffSerializer,
    StaffUpdateSerializer,
    token_payload_for,
)


def administered_organization_ids(user):
    """Organisations where the user may hire and manage staff.

    ``None`` means every organisation (superuser or platform administrator).
    A platform administrator onboards new organisations, so their reach cannot
    be limited to the memberships they already hold. A branch manager is not an
    organisation administrator; their reach is handled separately by
    :func:`managed_workplace_ids`.
    """
    if user.is_superuser:
        return None
    memberships = user.memberships.filter(is_active=True)
    if memberships.filter(role=Role.PLATFORM_ADMIN).exists():
        return None
    return {
        str(org_id)
        for org_id in memberships.filter(
            role__in=[Role.OPERATOR_ADMIN, Role.PLATFORM_ADMIN]
        ).values_list("organization_id", flat=True)
    }


def managed_workplace_ids(user):
    """Branches a branch manager may administer."""
    return {
        str(place_id)
        for place_id in user.memberships.filter(
            is_active=True, role=Role.BRANCH_MANAGER, workplace__isnull=False
        ).values_list("workplace_id", flat=True)
    }


def managed_branch_ids(user):
    """Branches a branch manager may administer."""
    return {
        str(branch_id)
        for branch_id in user.memberships.filter(
            is_active=True, role=Role.BRANCH_MANAGER, branch__isnull=False
        ).values_list("branch_id", flat=True)
    }


def can_manage_organization(user, organization_id):
    admin = administered_organization_ids(user)
    if admin is None:
        return True
    return str(organization_id) in admin


def can_manage_membership(user, membership):
    if can_manage_organization(user, membership.organization_id):
        return True
    if membership.branch_id and str(membership.branch_id) in managed_branch_ids(user):
        return True
    workplaces = managed_workplace_ids(user)
    return bool(membership.workplace_id) and str(membership.workplace_id) in workplaces


class RegisterView(APIView):
    permission_classes = [AllowAny]

    @transaction.atomic
    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        from apps.common.outbox import record_outbox

        record_outbox(
            event_type="UserCreated",
            aggregate_type="user",
            aggregate_id=user.pk,
            payload={"role": "customer"},
        )
        return Response(
            {**token_payload_for(user), "user": MeSerializer(user).data},
            status=status.HTTP_201_CREATED,
        )


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(MeSerializer(request.user).data)


class BranchViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """Branch master and head-office rule.

    Organisation admins manage every branch in their organisations. Branch
    staff may read branch names in their organisation so a counter can sell a
    seat on another branch's departure.
    """

    queryset = Branch.objects.select_related("organization")
    serializer_class = BranchSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.user.is_superuser or is_platform_admin(self.request.user):
            return qs
        return qs.filter(
            organization_id__in=organization_ids_for(self.request.user)
        )

    def get_permissions(self):
        if self.action in ("create", "update", "partial_update"):
            return [IsOrganizationAdmin()]
        return [IsAuthenticated()]

    def list(self, request):
        qs = self.get_queryset()
        organization = request.query_params.get("organization")
        if organization:
            qs = qs.filter(organization_id=organization)
        if request.query_params.get("is_active") not in ("0", "false", "no"):
            qs = qs.filter(is_active=True)
        return Response(BranchSerializer(qs, many=True).data)

    def perform_create(self, serializer):
        from apps.audit.models import record_audit

        branch = serializer.save()
        record_audit(
            actor=self.request.user,
            organization=branch.organization,
            action="branch.created",
            entity_type="branch",
            entity_id=branch.pk,
            new_value={"name": branch.name, "code": branch.code},
        )

    def perform_update(self, serializer):
        from apps.audit.models import record_audit

        branch = serializer.save()
        record_audit(
            actor=self.request.user,
            organization=branch.organization,
            action="branch.updated",
            entity_type="branch",
            entity_id=branch.pk,
            new_value={
                "name": branch.name,
                "is_head_office": branch.is_head_office,
                "is_active": branch.is_active,
            },
        )


class OrganizationViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    queryset = Organization.objects.all()
    serializer_class = OrganizationSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.method == "GET":
            return qs.filter(id__in=organization_ids_for(self.request.user))
        return qs

    def get_permissions(self):
        if self.action in ("create", "approve", "suspend"):
            return [IsPlatformAdmin()]
        return [IsAuthenticated()]

    def perform_create(self, serializer):
        # Operator onboarding requires explicit approval (blueprint 3).
        serializer.save(approval_status=Organization.Approval.PENDING)

    @action(detail=True, methods=["post"], permission_classes=[IsPlatformAdmin])
    @transaction.atomic
    def approve(self, request, pk=None):
        """Platform administrators approve a newly onboarded organisation."""
        organization = self.get_object()
        if organization.approval_status == Organization.Approval.APPROVED:
            return Response(self.get_serializer(organization).data)

        from apps.audit.models import record_audit

        previous = organization.approval_status
        organization.approval_status = Organization.Approval.APPROVED
        organization.save(update_fields=["approval_status", "updated_at"])
        record_audit(
            actor=request.user,
            organization=organization,
            action="organization.approved",
            entity_type="organization",
            entity_id=organization.pk,
            previous_value={"approval_status": previous},
            new_value={"approval_status": organization.approval_status},
        )
        return Response(self.get_serializer(organization).data)

    @action(detail=True, methods=["post"], permission_classes=[IsPlatformAdmin])
    @transaction.atomic
    def suspend(self, request, pk=None):
        """Stop an organisation from taking new work without deleting history."""
        organization = self.get_object()
        previous = organization.approval_status
        organization.approval_status = Organization.Approval.SUSPENDED
        organization.is_active = False
        organization.save(update_fields=["approval_status", "is_active", "updated_at"])
        from apps.audit.models import record_audit

        record_audit(
            actor=request.user,
            organization=organization,
            action="organization.suspended",
            entity_type="organization",
            entity_id=organization.pk,
            previous_value={"approval_status": previous},
            new_value={"approval_status": organization.approval_status},
        )
        return Response(self.get_serializer(organization).data)


class MembershipViewSet(viewsets.GenericViewSet):
    """Member management. List memberships for an organization; create
    memberships as an operator/platform administrator."""

    serializer_class = MembershipCreateSerializer
    queryset = Membership.objects.all()

    def get_permissions(self):
        if self.action == "create":
            return [IsPlatformAdmin()]
        return [IsAuthenticated()]

    def list(self, request, pk=None):
        """List memberships scoped to the caller's organizations. A specific
        organization (URL ``pk`` for ``/organizations/<pk>/members/`` or the
        ``organization`` query param) is honoured only when the caller belongs
        to it; foreign organizations are treated as not found."""

        org_ids = organization_ids_for(request.user)
        org_id = pk or request.query_params.get("organization")
        if org_id:
            if not request.user.is_superuser and str(org_id) not in {
                str(oid) for oid in org_ids
            }:
                return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
            qs = Membership.objects.filter(organization_id=org_id)
        elif request.user.is_superuser:
            qs = Membership.objects.all()
        else:
            qs = Membership.objects.filter(organization_id__in=org_ids)
        data = [{
            "id": str(m.id),
            "organization": str(m.organization_id),
            "user": str(m.user_id),
            "user_name": m.user.full_name or m.user.phone,
            "role": m.role,
            "is_active": m.is_active,
        } for m in qs]
        return Response(data)

    def create(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        member = serializer.save(approved_by=request.user)
        return Response(
            {
                "id": str(member.id),
                "user": str(member.user_id),
                "organization": str(member.organization_id),
                "role": member.role,
                "is_active": member.is_active,
            },
            status=status.HTTP_201_CREATED,
        )

class StaffViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """Staff directory and hiring.

    An operator administrator manages everyone in their organisation. A branch
    manager manages staff who share their workplace. Deactivation is soft so
    historical trip, ticket and custody records keep a valid actor.
    """

    queryset = (
        Membership.objects.select_related("user", "organization", "workplace")
        .filter(role__in=STAFF_ROLES)
        .order_by("user__full_name", "user__phone")
    )

    def get_serializer_class(self):
        if self.action == "create":
            return StaffHireSerializer
        if self.action in ("update", "partial_update"):
            return StaffUpdateSerializer
        return StaffSerializer

    def _managed_queryset(self, user):
        admin = administered_organization_ids(user)
        workplaces = managed_workplace_ids(user)
        branches = managed_branch_ids(user)
        qs = self.get_queryset()
        if admin is None:
            return qs
        if not admin and not workplaces and not branches:
            return qs.none()
        from django.db.models import Q

        scope = Q(organization_id__in=admin)
        if workplaces:
            scope |= Q(workplace_id__in=workplaces)
        if branches:
            scope |= Q(branch_id__in=branches)
        return qs.filter(scope)

    def list(self, request):
        qs = self._managed_queryset(request.user)
        organization = request.query_params.get("organization")
        if organization:
            qs = qs.filter(organization_id=organization)
        branch = request.query_params.get("branch")
        if branch:
            qs = qs.filter(branch_id=branch)
        workplace = request.query_params.get("workplace")
        if workplace:
            qs = qs.filter(workplace_id=workplace)
        role = request.query_params.get("role")
        if role:
            qs = qs.filter(role=role)
        if request.query_params.get("include_inactive") not in ("1", "true", "yes"):
            qs = qs.filter(is_active=True)
        return Response(StaffSerializer(qs, many=True).data)

    @transaction.atomic
    def create(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        organization = serializer.validated_data["organization"]
        role = serializer.validated_data["role"]
        workplace = serializer.validated_data.get("workplace")
        branch = serializer.validated_data.get("branch")

        if not can_manage_organization(request.user, organization.pk):
            workplaces = managed_workplace_ids(request.user)
            branches = managed_branch_ids(request.user)
            branch_hire = (
                role == Role.BRANCH_STAFF
                and (
                    (branch is not None and str(branch.pk) in branches)
                    or (
                        workplace is not None
                        and bool(workplaces)
                        and str(workplace.pk) in workplaces
                    )
                )
            )
            if not branch_hire:
                return Response(
                    {"detail": "You cannot hire for this organization."},
                    status=status.HTTP_403_FORBIDDEN,
                )

        member = serializer.save()
        return Response(StaffSerializer(member).data, status=status.HTTP_201_CREATED)

    def retrieve(self, request, pk=None):
        member = self._managed_queryset(request.user).filter(pk=pk).first()
        if member is None:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(StaffSerializer(member).data)

    @transaction.atomic
    def update(self, request, pk=None, partial=False):
        member = self._managed_queryset(request.user).filter(pk=pk).first()
        if member is None:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        if not can_manage_membership(request.user, member):
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        serializer = StaffUpdateSerializer(member, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)

        if not can_manage_organization(request.user, member.organization_id):
            # A branch manager may adjust staff in their branch, but may not
            # promote someone into an organisation-wide role.
            new_role = serializer.validated_data.get("role", member.role)
            if new_role != Role.BRANCH_STAFF:
                return Response(
                    {"role": "A branch manager can only manage counter staff."},
                    status=status.HTTP_403_FORBIDDEN,
                )

        serializer.save()
        return Response(StaffSerializer(member).data)

    @transaction.atomic
    def destroy(self, request, pk=None):
        member = self._managed_queryset(request.user).filter(pk=pk).first()
        if member is None:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        if not can_manage_membership(request.user, member):
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        from django.utils import timezone

        member.is_active = False
        member.ended_at = timezone.localdate()
        member.save(update_fields=["is_active", "ended_at", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)
