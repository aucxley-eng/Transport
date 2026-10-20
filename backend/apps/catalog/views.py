from django.db import transaction
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.models import organization_ids_for
from apps.accounts.permissions import IsAuthenticated, IsPlatformAdmin
from apps.audit.models import record_audit
from apps.catalog.models import Place, Quote, RateCard, Service
from apps.catalog.serializers import (
    PlaceSerializer,
    QuoteSerializer,
    RateCardSerializer,
    ServiceSerializer,
)


class ServiceViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    queryset = Service.objects.filter(is_active=True)
    serializer_class = ServiceSerializer
    permission_classes = [IsAuthenticated]


class PlaceViewSet(viewsets.ModelViewSet):
    queryset = Place.objects.all()
    serializer_class = PlaceSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .for_organizations(organization_ids_for(self.request.user))
        )

    def get_permissions(self):
        if self.action in ("create", "update", "partial_update", "destroy"):
            return [IsPlatformAdmin()]
        return [IsAuthenticated()]


class RateCardViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    queryset = RateCard.objects.all()
    serializer_class = RateCardSerializer

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .for_organizations(organization_ids_for(self.request.user))
        )

    def get_permissions(self):
        if self.action == "create":
            return [IsPlatformAdmin()]
        return [IsAuthenticated()]


class QuoteViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    queryset = Quote.objects.all()
    serializer_class = QuoteSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .for_organizations(organization_ids_for(self.request.user))
        )

    @transaction.atomic
    def perform_create(self, serializer):
        quote = serializer.save()
        record_audit(
            actor=self.request.user,
            organization=quote.organization,
            action="quote.created",
            entity_type="quote",
            entity_id=quote.pk,
            new_value={"total_minor": quote.total_minor, "currency": quote.currency},
        )

    @action(detail=True, methods=["post"])
    def accept(self, request, pk=None):
        """Accept a valid, unexpired quote. The transition is one-way: the quote
        is immutable afterwards, and a second accept is refused rather than
        treated as a repeat success."""
        quote = self.get_object()
        try:
            quote.accept()
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        record_audit(
            actor=request.user,
            organization=quote.organization,
            action="quote.accepted",
            entity_type="quote",
            entity_id=quote.pk,
            previous_value={"status": "valid"},
            new_value={"status": "accepted"},
        )
        return Response(self.get_serializer(quote).data)