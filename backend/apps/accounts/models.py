import uuid

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.accounts.phones import normalize_phone
from apps.common.models import BaseModel


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, phone, password=None, **extra_fields):
        phone = normalize_phone(phone)
        if not phone:
            raise ValueError("A valid Kenyan phone number is required.")
        user = self.model(phone=phone, **extra_fields)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_user(self, phone, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(phone, password, **extra_fields)

    def create_superuser(self, phone, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        return self._create_user(phone, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    """A person. Identity is the phone number; email optional.

    Authorization lives in Memberships (person -> organization -> role),
    not on this record, so the same user can hold different roles in
    different organizations.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    phone = models.CharField(max_length=16, unique=True)
    email = models.EmailField(blank=True, null=True)
    full_name = models.CharField(max_length=160, blank=True)
    avatar = models.ImageField(upload_to="avatars/", blank=True, null=True)

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    USERNAME_FIELD = "phone"
    REQUIRED_FIELDS = []

    objects = UserManager()

    class Meta:
        verbose_name = "User"
        verbose_name_plural = "Users"

    def __str__(self):
        return self.full_name or self.phone

    def save(self, *args, **kwargs):
        if self.phone:
            self.phone = normalize_phone(self.phone) or self.phone
        super().save(*args, **kwargs)


class Organization(BaseModel):
    """An operator, carrier or corporate customer. Owned records carry an
    ``organization`` foreign key and are queried only within the caller's
    scope (operator isolation, blueprint 3).
    """

    class Type(models.TextChoices):
        CARRIER = "carrier", "Carrier / operator"
        PLATFORM = "platform", "Platform"
        CORPORATE = "corporate", "Corporate customer"

    class Approval(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        SUSPENDED = "suspended", "Suspended"

    name = models.CharField(max_length=160)
    slug = models.SlugField(max_length=80, unique=True)
    org_type = models.CharField(max_length=16, choices=Type.choices, default=Type.CARRIER)
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.CASCADE, related_name="children"
    )
    approval_status = models.CharField(
        max_length=16, choices=Approval.choices, default=Approval.PENDING, db_index=True
    )
    currency = models.CharField(max_length=3, default="KES")
    timezone = models.CharField(max_length=48, default="Africa/Nairobi")
    kra_pin = models.CharField(max_length=20, blank=True)
    address = models.CharField(max_length=255, blank=True)
    contact_phone = models.CharField(max_length=16, blank=True)
    is_active = models.BooleanField(default=True)
    insertion_grace_minutes = models.PositiveSmallIntegerField(
        default=30,
        validators=[MinValueValidator(0), MaxValueValidator(240)],
        help_text=(
            "How far a mid-route insertion may push an already-promised stop "
            "before the platform refuses it (blueprint 11)."
        ),
    )

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Role(models.TextChoices):
    CUSTOMER = "customer", "Customer or sender"
    RECIPIENT = "recipient", "Recipient"
    DRIVER = "driver", "Driver"
    CONDUCTOR = "conductor", "Conductor"
    BRANCH_STAFF = "branch_staff", "Branch or warehouse staff"
    DISPATCHER = "dispatcher", "Dispatcher"
    OPERATOR_ADMIN = "operator_admin", "Operator administrator"
    CORPORATE_MANAGER = "corporate_manager", "Corporate travel manager"
    SUPPORT_AGENT = "support_agent", "Support agent"
    FINANCE_OFFICER = "finance_officer", "Finance officer"
    PLATFORM_ADMIN = "platform_admin", "Platform administrator"


class Membership(BaseModel):
    """A person's role within an organization. A user may hold several."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="memberships")
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="memberships"
    )
    role = models.CharField(max_length=24, choices=Role.choices)
    is_active = models.BooleanField(default=True)
    approved_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="approved_memberships"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "organization", "role"],
                name="uq_membership_user_org_role",
            )
        ]
        ordering = ["organization__name"]

    def __str__(self):
        return f"{self.user.phone} @ {self.organization.slug} as {self.role}"


def organization_ids_for(user):
    """Active organization ids for a user (operator isolation scope)."""
    if not user or not user.is_authenticated:
        return []
    return list(
        user.memberships.filter(is_active=True).values_list("organization_id", flat=True)
    )