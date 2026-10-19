from rest_framework import serializers

from apps.accounts.models import organization_ids_for
from apps.catalog.models import Quote
from apps.orders.models import Order


class OrderCreateSerializer(serializers.Serializer):
    # Currency is never accepted from the client: the quote is the frozen
    # commercial truth, and letting a caller relabel money would propagate a
    # false unit into the payment attempt and the ledger.
    quote = serializers.UUIDField()
    service_detail = serializers.JSONField(default=dict)

    def validate_quote(self, value):
        try:
            quote = Quote.objects.get(pk=value)
        except Quote.DoesNotExist:
            raise serializers.ValidationError("Quote not found.")
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if user and not user.is_superuser:
            # A quote from an organization the caller does not belong to must
            # not be usable to mint an order in that organization.
            if quote.organization_id is not None and quote.organization_id not in organization_ids_for(user):
                raise serializers.ValidationError("Quote not found.")
        self._quote = quote
        return value

    def create(self, validated_data):
        quote = self._quote
        if quote.status != Quote.Status.ACCEPTED:
            raise serializers.ValidationError(
                {"quote": "The quote must be accepted before ordering."}
            )
        return Order.objects.create(
            organization=quote.organization,
            customer=validated_data["customer"],
            service=quote.service,
            quote=quote,
            currency=quote.currency,
            total_minor=quote.total_minor,
            service_detail=validated_data.get("service_detail", {}),
        )


class OrderSerializer(serializers.ModelSerializer):
    service_name = serializers.CharField(source="service.name", read_only=True)
    quote_total_minor = serializers.IntegerField(source="quote.total_minor", read_only=True)

    class Meta:
        model = Order
        fields = [
            "id",
            "organization",
            "customer",
            "service",
            "service_name",
            "quote",
            "quote_total_minor",
            "currency",
            "total_minor",
            "status",
            "is_payment_required",
            "service_detail",
            "confirmed_at",
            "cancelled_at",
            "cancel_reason",
            "created_at",
        ]