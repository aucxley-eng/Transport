from rest_framework import serializers
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.models import Membership, Organization, Role, User
from apps.accounts.phones import normalize_phone


class RegisterSerializer(serializers.Serializer):
    phone = serializers.CharField(max_length=16)
    full_name = serializers.CharField(max_length=160, required=False, allow_blank=True)
    email = serializers.EmailField(required=False, allow_blank=True)
    password = serializers.CharField(write_only=True, min_length=8)

    def validate_phone(self, value):
        normalized = normalize_phone(value)
        if normalized is None:
            raise serializers.ValidationError("Enter a valid Kenyan phone number.")
        if User.objects.filter(phone=normalized).exists():
            raise serializers.ValidationError("A user with this phone already exists.")
        return normalized

    def create(self, validated_data):
        return User.objects.create_user(
            phone=validated_data["phone"],
            password=validated_data["password"],
            full_name=validated_data.get("full_name", ""),
            email=validated_data.get("email", "") or None,
        )


class MembershipSerializer(serializers.ModelSerializer):
    organization_name = serializers.CharField(source="organization.name", read_only=True)
    organization_slug = serializers.CharField(source="organization.slug", read_only=True)

    class Meta:
        model = Membership
        fields = [
            "id",
            "organization",
            "organization_name",
            "organization_slug",
            "role",
            "is_active",
        ]


class MeSerializer(serializers.ModelSerializer):
    memberships = MembershipSerializer(many=True, read_only=True)

    class Meta:
        model = User
        fields = [
            "id",
            "phone",
            "email",
            "full_name",
            "memberships",
        ]


class OrganizationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Organization
        fields = [
            "id",
            "name",
            "slug",
            "org_type",
            "approval_status",
            "currency",
            "timezone",
            "address",
            "contact_phone",
            "is_active",
        ]
        read_only_fields = ["approval_status"]


class MembershipCreateSerializer(serializers.Serializer):
    user = serializers.UUIDField()
    organization = serializers.UUIDField()
    role = serializers.ChoiceField(choices=Role.choices)
    is_active = serializers.BooleanField(default=True)

    def validate(self, attrs):
        user = User.objects.filter(pk=attrs["user"]).first()
        organization = Organization.objects.filter(pk=attrs["organization"]).first()
        if user is None:
            raise serializers.ValidationError({"user": "User not found."})
        if organization is None:
            raise serializers.ValidationError({"organization": "Organization not found."})
        return {**attrs, "user": user, "organization": organization}

    def create(self, validated_data):
        member, _ = Membership.objects.get_or_create(
            user=validated_data["user"],
            organization=validated_data["organization"],
            role=validated_data["role"],
            defaults={"is_active": validated_data["is_active"]},
        )
        return member


def token_payload_for(user):
    refresh = RefreshToken.for_user(user)
    return {"refresh": str(refresh), "access": str(refresh.access_token)}