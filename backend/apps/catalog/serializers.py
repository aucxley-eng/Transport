from rest_framework import serializers

from apps.catalog.models import Place, Quote, RateCard, RateLine, Service


class ServiceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Service
        fields = ["id", "slug", "name", "service_type", "is_active"]


class PlaceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Place
        fields = [
            "id",
            "organization",
            "name",
            "place_type",
            "lat",
            "lng",
            "address",
            "landmark",
            "directions",
            "is_active",
        ]


class RateLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = RateLine
        fields = ["id", "line_type", "label", "from_value", "to_value", "amount_minor"]


class RateCardSerializer(serializers.ModelSerializer):
    lines = RateLineSerializer(many=True, read_only=True)

    class Meta:
        model = RateCard
        fields = [
            "id",
            "organization",
            "service",
            "name",
            "version",
            "currency",
            "effective_from",
            "effective_to",
            "is_active",
            "lines",
        ]


class QuoteSerializer(serializers.ModelSerializer):
    service_name = serializers.CharField(source="service.name", read_only=True)

    class Meta:
        model = Quote
        fields = [
            "id",
            "organization",
            "service",
            "service_name",
            "request_payload",
            "currency",
            "total_minor",
            "basis",
            "rate_card",
            "rate_card_name",
            "status",
            "expires_at",
            "accepted_at",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "rate_card_name",
            "status",
            "expires_at",
            "accepted_at",
            "created_at",
        ]