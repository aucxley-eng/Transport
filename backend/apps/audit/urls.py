from django.urls import path
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import organization_ids_for
from apps.audit.models import AuditEvent


class AuditEventListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        org_ids = organization_ids_for(request.user)
        qs = AuditEvent.objects.all()
        if request.user.is_superuser:
            pass
        else:
            qs = qs.filter(organization_id__in=org_ids)
        entity_type = request.query_params.get("entity_type")
        entity_id = request.query_params.get("entity_id")
        if entity_type:
            qs = qs.filter(entity_type=entity_type)
        if entity_id:
            qs = qs.filter(entity_id=entity_id)
        actor_id = request.query_params.get("actor")
        if actor_id:
            qs = qs.filter(actor_id=actor_id)
        page = qs[:100]
        return Response([
            {
                "id": str(e.id),
                "created_at": e.created_at.isoformat(),
                "actor": str(e.actor_id) if e.actor_id else None,
                "organization": str(e.organization_id) if e.organization_id else None,
                "action": e.action,
                "entity_type": e.entity_type,
                "entity_id": str(e.entity_id) if e.entity_id else None,
                "reason": e.reason,
                "previous_value": e.previous_value,
                "new_value": e.new_value,
                "correlation_id": str(e.correlation_id),
            }
            for e in page
        ])


urlpatterns = [
    path("audit/events/", AuditEventListView.as_view(), name="audit-events"),
]