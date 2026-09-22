from django.db import transaction
from rest_framework import mixins, status, viewsets
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import Membership, Organization, User, organization_ids_for
from apps.accounts.permissions import IsPlatformAdmin
from apps.accounts.serializers import (
    MembershipCreateSerializer,
    MeSerializer,
    OrganizationSerializer,
    RegisterSerializer,
    token_payload_for,
)


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
        if self.action == "create":
            return [IsPlatformAdmin()]
        return [IsAuthenticated()]

    def perform_create(self, serializer):
        # Operator onboarding requires explicit approval (blueprint 3).
        serializer.save(approval_status=Organization.Approval.PENDING)


class MembershipViewSet(viewsets.GenericViewSet):
    """Member management. List memberships for an organization; create
    memberships as an operator/platform administrator."""

    serializer_class = MembershipCreateSerializer
    queryset = Membership.objects.all()

    def get_permissions(self):
        if self.action == "create":
            return [IsPlatformAdmin()]
        return [IsAuthenticated()]

    def list(self, request):
        org_id = request.query_params.get("organization")
        if org_id and org_id not in organization_ids_for(request.user):
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        qs = Membership.objects.all()
        if org_id:
            qs = qs.filter(organization_id=org_id)
        data = [{
            "id": str(m.id),
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