from django.db import transaction
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.models import organization_ids_for
from apps.accounts.permissions import IsAuthenticated
from apps.audit.models import record_audit
from apps.catalog.models import Quote
from apps.common.outbox import record_outbox
from apps.orders.models import Order
from apps.orders.serializers import OrderCreateSerializer, OrderSerializer


class OrderViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    queryset = Order.objects.all()
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        if self.action == "create":
            return OrderCreateSerializer
        return OrderSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user.is_superuser:
            return qs
        # Customers see their own orders; staff see their organizations' orders.
        from django.db.models import Q

        return qs.filter(
            Q(customer=user) | Q(organization_id__in=organization_ids_for(user))
        )

    @transaction.atomic
    def perform_create(self, serializer):
        order = serializer.save(customer=self.request.user)
        record_outbox(
            event_type="OrderCreated",
            aggregate_type="order",
            aggregate_id=order.pk,
            organization_id=order.organization_id,
            payload={
                "service": str(order.service_id),
                "currency": order.currency,
                "total_minor": order.total_minor,
            },
        )
        record_audit(
            actor=self.request.user,
            organization=order.organization,
            action="order.created",
            entity_type="order",
            entity_id=order.pk,
            new_value={"total_minor": order.total_minor, "currency": order.currency},
        )

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        return Response(
            OrderSerializer(serializer.instance, context=self.get_serializer_context()).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["post"])
    def confirm(self, request, pk=None):
        order = self.get_object()
        if order.customer_id != request.user.pk and not request.user.is_superuser:
            return Response(
                {"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND
            )
        try:
            order.confirm()
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        record_outbox(
            event_type="OrderConfirmed",
            aggregate_type="order",
            aggregate_id=order.pk,
            organization_id=order.organization_id,
        )
        return Response(OrderSerializer(order).data)