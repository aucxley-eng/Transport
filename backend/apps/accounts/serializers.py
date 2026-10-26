from django.db import transaction
from django.db.models import Q
from rest_framework import serializers
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.models import (
    STAFF_ROLES,
    Branch,
    Membership,
    Organization,
    Role,
    User,
)
from apps.accounts.phones import normalize_phone
from apps.catalog.models import Place


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
    branch_name = serializers.CharField(source="branch.name", read_only=True)
    branch_code = serializers.CharField(source="branch.code", read_only=True)
    workplace_name = serializers.CharField(source="workplace.name", read_only=True)

    class Meta:
        model = Membership
        fields = [
            "id",
            "organization",
            "organization_name",
            "organization_slug",
            "role",
            "branch",
            "branch_name",
            "branch_code",
            "workplace",
            "workplace_name",
            "job_title",
            "employee_number",
            "hired_at",
            "ended_at",
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


class BranchSerializer(serializers.ModelSerializer):
    organization_name = serializers.CharField(source="organization.name", read_only=True)

    class Meta:
        model = Branch
        fields = [
            "id",
            "organization",
            "organization_name",
            "name",
            "code",
            "address",
            "contact_phone",
            "is_head_office",
            "is_active",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]

    def validate_code(self, value):
        return value.strip().upper()

    def validate(self, attrs):
        organization = attrs.get("organization") or getattr(
            self.instance, "organization", None
        )
        if organization is None:
            raise serializers.ValidationError(
                {"organization": "An organisation is required."}
            )
        if (
            self.instance is not None
            and organization.pk != self.instance.organization_id
        ):
            raise serializers.ValidationError(
                {"organization": "A branch cannot be moved to another organisation."}
            )

        is_head_office = attrs.get(
            "is_head_office",
            self.instance.is_head_office if self.instance else False,
        )
        is_active = attrs.get(
            "is_active", self.instance.is_active if self.instance else True
        )
        others = Branch.objects.filter(organization=organization).exclude(
            pk=getattr(self.instance, "pk", None)
        )
        if not is_head_office:
            active_others = others.filter(is_active=True)
            if active_others.exists() and not active_others.filter(
                is_head_office=True
            ).exists():
                raise serializers.ValidationError(
                    {
                        "is_head_office": (
                            "An organisation with more than one active branch "
                            "must have a head office."
                        )
                    }
                )
        if (
            self.instance is not None
            and self.instance.is_head_office
            and (not is_head_office or not is_active)
            and others.filter(is_active=True).exists()
        ):
            raise serializers.ValidationError(
                {
                    "is_head_office": (
                        "Promote another branch before removing head-office "
                        "status from this branch."
                    )
                }
            )
        return attrs

    def _clear_other_head_offices(self, organization):
        Branch.objects.filter(
            organization=organization, is_head_office=True
        ).exclude(pk=getattr(self.instance, "pk", None)).update(
            is_head_office=False
        )

    @transaction.atomic
    def create(self, validated_data):
        if validated_data.get("is_head_office"):
            self._clear_other_head_offices(validated_data["organization"])
        return super().create(validated_data)

    @transaction.atomic
    def update(self, instance, validated_data):
        if validated_data.get("is_head_office"):
            self._clear_other_head_offices(instance.organization)
        return super().update(instance, validated_data)


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
    branch = serializers.UUIDField(required=False, allow_null=True)
    is_active = serializers.BooleanField(default=True)

    def validate(self, attrs):
        user = User.objects.filter(pk=attrs["user"]).first()
        organization = Organization.objects.filter(pk=attrs["organization"]).first()
        if user is None:
            raise serializers.ValidationError({"user": "User not found."})
        if organization is None:
            raise serializers.ValidationError({"organization": "Organization not found."})
        branch = None
        if attrs.get("branch"):
            branch = Branch.objects.filter(pk=attrs["branch"]).first()
            if branch is None or branch.organization_id != organization.pk:
                raise serializers.ValidationError(
                    {"branch": "Branch must belong to this organization."}
                )
        return {**attrs, "user": user, "organization": organization, "branch": branch}

    def create(self, validated_data):
        member, _ = Membership.objects.get_or_create(
            user=validated_data["user"],
            organization=validated_data["organization"],
            role=validated_data["role"],
            branch=validated_data.get("branch"),
            defaults={"is_active": validated_data["is_active"]},
        )
        return member


class StaffSerializer(serializers.ModelSerializer):
    """Read shape for the staff directory: membership plus the person."""

    user_id = serializers.UUIDField(source="user.id", read_only=True)
    phone = serializers.CharField(source="user.phone", read_only=True)
    full_name = serializers.CharField(source="user.full_name", read_only=True)
    email = serializers.EmailField(source="user.email", read_only=True)
    organization_name = serializers.CharField(source="organization.name", read_only=True)
    branch_name = serializers.CharField(source="branch.name", read_only=True)
    branch_code = serializers.CharField(source="branch.code", read_only=True)
    workplace_name = serializers.CharField(source="workplace.name", read_only=True)
    role_label = serializers.CharField(source="get_role_display", read_only=True)

    class Meta:
        model = Membership
        fields = [
            "id",
            "user_id",
            "phone",
            "full_name",
            "email",
            "organization",
            "organization_name",
            "branch",
            "branch_name",
            "branch_code",
            "workplace",
            "workplace_name",
            "role",
            "role_label",
            "job_title",
            "employee_number",
            "hired_at",
            "ended_at",
            "is_active",
            "created_at",
        ]
        read_only_fields = fields


class StaffHireSerializer(serializers.Serializer):
    """Hire one person into one role at one organisation.

    If the phone already belongs to a user, that person is attached to the
    organisation instead of creating a duplicate identity. A password is
    required only when this hire creates a new user; an existing user keeps
    their current credentials.
    """

    organization = serializers.UUIDField()
    phone = serializers.CharField(max_length=16)
    full_name = serializers.CharField(max_length=160, required=False, allow_blank=True)
    email = serializers.EmailField(required=False, allow_blank=True)
    password = serializers.CharField(write_only=True, required=False, allow_blank=True, min_length=8)
    role = serializers.ChoiceField(choices=Role.choices)
    branch = serializers.UUIDField(required=False, allow_null=True)
    workplace = serializers.UUIDField(required=False, allow_null=True)
    job_title = serializers.CharField(max_length=80, required=False, allow_blank=True)
    employee_number = serializers.CharField(max_length=32, required=False, allow_blank=True)
    hired_at = serializers.DateField(required=False, allow_null=True)

    def validate_phone(self, value):
        normalized = normalize_phone(value)
        if normalized is None:
            raise serializers.ValidationError("Enter a valid Kenyan phone number.")
        return normalized

    def validate_role(self, value):
        if value not in STAFF_ROLES:
            raise serializers.ValidationError("This role cannot be assigned as staff.")
        return value

    def validate(self, attrs):
        organization = Organization.objects.filter(pk=attrs["organization"]).first()
        if organization is None:
            raise serializers.ValidationError({"organization": "Organization not found."})

        branch = None
        if attrs.get("branch"):
            branch = Branch.objects.filter(pk=attrs["branch"]).first()
            if branch is None or branch.organization_id != organization.pk:
                raise serializers.ValidationError(
                    {"branch": "Branch must belong to this organization."}
                )

        workplace = None
        if attrs.get("workplace"):
            workplace = Place.objects.filter(pk=attrs["workplace"]).first()
            if workplace is None or workplace.organization_id != organization.pk:
                raise serializers.ValidationError(
                    {"workplace": "Workplace must belong to this organization."}
                )

        existing = User.objects.filter(phone=attrs["phone"]).first()
        if existing is None and not attrs.get("password"):
            raise serializers.ValidationError(
                {"password": "A password is required when creating a new user."}
            )
        duplicate = Membership.objects.filter(
            user=existing, organization=organization, role=attrs["role"]
        )
        if branch is not None:
            duplicate = duplicate.filter(branch=branch)
        else:
            duplicate = duplicate.filter(branch__isnull=True)
        if existing and duplicate.exists():
            raise serializers.ValidationError(
                {
                    "role": (
                        "This person already holds that role "
                        + (f"at {branch.name}." if branch else "in this organisation.")
                    )
                }
            )

        return {
            **attrs,
            "organization": organization,
            "branch": branch,
            "workplace": workplace,
            "existing_user": existing,
        }

    def create(self, validated_data):
        existing = validated_data.pop("existing_user")
        organization = validated_data.pop("organization")
        branch = validated_data.pop("branch", None)
        workplace = validated_data.pop("workplace", None)
        password = validated_data.pop("password", "")
        phone = validated_data.pop("phone")

        if existing is None:
            user = User.objects.create_user(
                phone,
                password=password,
                full_name=validated_data.pop("full_name", ""),
                email=validated_data.pop("email", "") or None,
            )
        else:
            user = existing
            validated_data.pop("full_name", None)
            validated_data.pop("email", None)

        return Membership.objects.create(
            user=user,
            organization=organization,
            branch=branch,
            workplace=workplace,
            approved_by=self.context["request"].user,
            **validated_data,
        )


class StaffUpdateSerializer(serializers.ModelSerializer):
    """Employment details an administrator may change after hiring."""

    class Meta:
        model = Membership
        fields = [
            "role",
            "branch",
            "workplace",
            "job_title",
            "employee_number",
            "hired_at",
            "ended_at",
            "is_active",
        ]

    def validate_role(self, value):
        if value not in STAFF_ROLES:
            raise serializers.ValidationError("This role cannot be assigned as staff.")
        return value

    def validate(self, attrs):
        branch = attrs.get("branch")
        if branch and branch.organization_id != self.instance.organization_id:
            raise serializers.ValidationError(
                {"branch": "Branch must belong to this organization."}
            )
        workplace = attrs.get("workplace")
        if workplace and workplace.organization_id != self.instance.organization_id:
            raise serializers.ValidationError(
                {"workplace": "Workplace must belong to this organization."}
            )
        return attrs


def token_payload_for(user):
    refresh = RefreshToken.for_user(user)
    return {"refresh": str(refresh), "access": str(refresh.access_token)}