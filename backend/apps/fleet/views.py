from django.db import transaction
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.models import organization_ids_for
from apps.accounts.permissions import IsAuthenticated, IsOrganizationAdmin
from apps.audit.models import record_audit
from apps.common.models import OrganizationScopedQuerySet
from apps.fleet.models import Document, Driver, Vehicle
from apps.fleet.serializers import (
    DocumentSerializer,
    DriverSerializer,
    VehicleSerializer,
)


class _OrgScopedViewSet(viewsets.ModelViewSet):
    """ViewSet whose query set is always constrained to the caller's
    organizations (operator isolation, blueprint 3)."""

    def get_queryset(self):
        qs = super().get_queryset()
        if not isinstance(qs, OrganizationScopedQuerySet):
            return qs
        return qs.for_organizations(organization_ids_for(self.request.user))


class VehicleViewSet(_OrgScopedViewSet):
    queryset = Vehicle.objects.all()
    serializer_class = VehicleSerializer

    def get_permissions(self):
        if self.action in ("create", "update", "partial_update", "destroy"):
            return [IsOrganizationAdmin()]
        return [IsAuthenticated()]

    @transaction.atomic
    def perform_create(self, serializer):
        organization = serializer.validated_data.get("organization")
        serializer.save()
        record_audit(
            actor=self.request.user,
            organization=organization,
            action="vehicle.created",
            entity_type="vehicle",
            entity_id=serializer.instance.pk,
        )


class DriverViewSet(_OrgScopedViewSet):
    queryset = Driver.objects.all()
    serializer_class = DriverSerializer

    def get_permissions(self):
        if self.action in ("create", "update", "partial_update", "destroy", "approve"):
            return [IsOrganizationAdmin()]
        return [IsAuthenticated()]

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        driver = self.get_object()
        if driver.status != Driver.Status.PENDING:
            return Response(
                {"detail": "Only pending drivers can be approved."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        driver.status = Driver.Status.APPROVED
        driver.save(update_fields=["status", "updated_at"])
        record_audit(
            actor=request.user,
            organization=driver.organization,
            action="driver.approved",
            entity_type="driver",
            entity_id=driver.pk,
            previous_value={"status": "pending"},
            new_value={"status": "approved"},
        )
        return Response(self.get_serializer(driver).data)


class DocumentViewSet(_OrgScopedViewSet):
    queryset = Document.objects.all()
    serializer_class = DocumentSerializer

    def get_permissions(self):
        if self.action in ("create", "update", "partial_update", "destroy", "verify"):
            return [IsOrganizationAdmin()]
        return [IsAuthenticated()]

    @action(detail=True, methods=["post"])
    def verify(self, request, pk=None):
        doc = self.get_object()
        if doc.status != Document.Status.PENDING:
            return Response(
                {"detail": "Only pending documents can be verified."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        doc.mark_verified(request.user)
        record_audit(
            actor=request.user,
            organization=doc.organization,
            action="document.verified",
            entity_type="document",
            entity_id=doc.pk,
            previous_value={"status": "pending"},
            new_value={"status": "verified"},
        )
        return Response(self.get_serializer(doc).data)