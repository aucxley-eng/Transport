from rest_framework import serializers, status, viewsets
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import organization_ids_for
from apps.orders.models import Order
from apps.payments.models import LedgerEntry, PaymentAttempt
from apps.payments.services import (
    PaymentError,
    create_payment_attempt,
    handle_payment_callback,
)


class PaymentAttemptSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentAttempt
        fields = [
            "id",
            "organization",
            "order",
            "provider",
            "idempotency_key",
            "expected_amount_minor",
            "currency",
            "customer_phone",
            "provider_reference",
            "state",
            "result_received",
            "note",
            "created_at",
        ]
        read_only_fields = ["id", "provider_reference", "state", "result_received", "created_at"]


class PaymentAttemptCreateSerializer(serializers.Serializer):
    order = serializers.UUIDField()
    organization = serializers.UUIDField(required=False)
    amount_minor = serializers.IntegerField(min_value=1)
    currency = serializers.CharField(max_length=3, default="KES")
    phone = serializers.CharField(max_length=16, required=False, allow_blank=True)
    idempotency_key = serializers.UUIDField(required=False)
    provider = serializers.CharField(default="mpesa")


class PaymentAttemptViewSet(viewsets.GenericViewSet):
    queryset = PaymentAttempt.objects.all()
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        if self.action == "create":
            return PaymentAttemptCreateSerializer
        return PaymentAttemptSerializer

    def get_queryset(self):
        if self.request.user.is_superuser:
            return PaymentAttempt.objects.all()
        return PaymentAttempt.objects.filter(
            organization_id__in=organization_ids_for(self.request.user)
        )

    def list(self, request):
        return Response(
            PaymentAttemptSerializer(self.get_queryset(), many=True, context={"request": request}).data
        )

    def retrieve(self, request, pk=None):
        attempt = self.get_queryset().filter(pk=pk).first()
        if attempt is None:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(PaymentAttemptSerializer(attempt, context={"request": request}).data)

    def create(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        import uuid

        order = Order.objects.filter(pk=data["order"]).first()
        if order is None:
            return Response(
                {"detail": "Order not found."}, status=status.HTTP_400_BAD_REQUEST
            )
        user = request.user
        can_pay = user.is_superuser or order.customer_id == user.pk
        if not can_pay:
            org_ids = set(organization_ids_for(user))
            if not (order.organization_id and order.organization_id in org_ids):
                return Response(
                    {"detail": "Order not found."}, status=status.HTTP_404_NOT_FOUND
                )
        try:
            attempt = create_payment_attempt(
                organization_id=data.get("organization"),
                order_id=data["order"],
                idempotency_key=data.get("idempotency_key") or uuid.uuid4(),
                amount_minor=data["amount_minor"],
                currency=data.get("currency", "KES"),
                customer_phone=data.get("phone", ""),
                provider_slug=data.get("provider", "mpesa"),
            )
        except PaymentError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            PaymentAttemptSerializer(attempt, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )


class ProviderCallbackView(APIView):
    """Provider webhook endpoint, deliberately friction-free for the provider.
    Idempotency and amount validation happen inside handle_payment_callback."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request, provider):
        try:
            attempt, applied = handle_payment_callback(
                provider, request.data, actor=None
            )
        except PaymentError as exc:
            return Response({"status": "unmatched"}, status=status.HTTP_202_ACCEPTED)
        return Response(
            {
                "status": "ok",
                "applied": applied,
                "attempt_state": attempt.state,
            },
            status=status.HTTP_200_OK,
        )


class LedgerEntryListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        org_ids = organization_ids_for(request.user)
        qs = LedgerEntry.objects.filter(payment_attempt__organization_id__in=org_ids)
        journal = request.query_params.get("journal")
        if journal:
            qs = qs.filter(journal=journal)
        return Response([
            {
                "id": str(e.id),
                "journal": e.journal,
                "account": e.account,
                "amount_minor": e.amount_minor,
                "currency": e.currency,
                "entry_type": e.entry_type,
                "order": str(e.order_id) if e.order_id else None,
                "reference": e.reference,
                "correlation_id": str(e.correlation_id),
                "created_at": e.created_at.isoformat(),
            }
            for e in qs[:500]
        ])