import uuid

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q

from apps.accounts.phones import normalize_phone
from apps.common.models import BaseModel, OrganizationScopedQuerySet


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


class Branch(BaseModel):
    """An operating location of an organisation.

    Branches own fleets, staff, routes and cash-up. They are deliberately
    separate from ``catalog.Place``: a Place is a point on a map (stage,
    landmark, warehouse) and is shared by routes and parcels, while a Branch is
    an operational unit with its own team and P&L.
    """

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="branches"
    )
    name = models.CharField(max_length=160)
    code = models.CharField(
        max_length=16,
        blank=True,
        help_text="Short operator code, e.g. NBO or KSM. Unique within the organisation.",
    )
    address = models.CharField(max_length=255, blank=True)
    contact_phone = models.CharField(max_length=16, blank=True)
    is_head_office = models.BooleanField(
        default=False,
        help_text=(
            "Exactly one branch is head office when an organisation has more "
            "than one active branch."
        ),
    )
    is_active = models.BooleanField(default=True)

    objects = OrganizationScopedQuerySet.as_manager()

    class Meta:
        ordering = ["organization__name", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "code"],
                condition=~Q(code=""),
                name="uq_branch_org_code",
            ),
            models.UniqueConstraint(
                fields=["organization"],
                condition=Q(is_head_office=True),
                name="uq_branch_head_office_per_org",
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.organization.slug})"


class Role(models.TextChoices):
    CUSTOMER = "customer", "Customer or sender"
    RECIPIENT = "recipient", "Recipient"
    DRIVER = "driver", "Driver"
    CONDUCTOR = "conductor", "Conductor"
    BRANCH_STAFF = "branch_staff", "Counter or warehouse staff"
    BRANCH_MANAGER = "branch_manager", "Branch manager"
    DISPATCHER = "dispatcher", "Dispatcher"
    OPERATOR_ADMIN = "operator_admin", "Operator administrator"
    CORPORATE_MANAGER = "corporate_manager", "Corporate travel manager"
    SUPPORT_AGENT = "support_agent", "Support agent"
    FINANCE_OFFICER = "finance_officer", "Finance officer"
    PLATFORM_ADMIN = "platform_admin", "Platform administrator"


#: Roles that represent someone employed by an operator rather than a
#: customer, recipient, or platform operator. Hiring screens use this to keep
#: customer accounts out of the staff directory.
STAFF_ROLES = (
    Role.DRIVER,
    Role.CONDUCTOR,
    Role.BRANCH_STAFF,
    Role.BRANCH_MANAGER,
    Role.DISPATCHER,
    Role.OPERATOR_ADMIN,
    Role.CORPORATE_MANAGER,
    Role.SUPPORT_AGENT,
    Role.FINANCE_OFFICER,
    Role.PLATFORM_ADMIN,
)


class Membership(BaseModel):
    """A person's employment and role within an organization.

    One person may hold several memberships across organizations and roles.
    Deactivation is soft (``is_active`` plus ``ended_at``) so historical
    operational records keep a valid actor reference.
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="memberships")
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="memberships"
    )
    role = models.CharField(max_length=24, choices=Role.choices)
    branch = models.ForeignKey(
        Branch,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="memberships",
        help_text="Operating branch the person reports to. Null for HQ-wide roles.",
    )
    workplace = models.ForeignKey(
        "catalog.Place",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="memberships",
        help_text="Legacy map place. Prefer ``branch`` for operational scope.",
    )
    job_title = models.CharField(
        max_length=80,
        blank=True,
        help_text="Free-text label shown in the staff directory, e.g. 'Counter clerk'.",
    )
    employee_number = models.CharField(
        max_length=32,
        blank=True,
        help_text="Operator-assigned staff number, e.g. 'NKT-014'.",
    )
    hired_at = models.DateField(null=True, blank=True)
    ended_at = models.DateField(
        null=True, blank=True, help_text="Set when employment ends; keeps the row auditable."
    )
    is_active = models.BooleanField(default=True)
    approved_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="approved_memberships"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "organization", "role"],
                condition=Q(branch__isnull=True),
                name="uq_membership_user_org_role_hq",
            ),
            models.UniqueConstraint(
                fields=["user", "organization", "role", "branch"],
                condition=Q(branch__isnull=False),
                name="uq_membership_user_org_role_branch",
            ),
        ]
        ordering = ["organization__name"]

    def __str__(self):
        return f"{self.user.phone} @ {self.organization.slug} as {self.role}"


def is_platform_admin(user):
    """True for superusers and platform administrators."""
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return user.memberships.filter(is_active=True, role=Role.PLATFORM_ADMIN).exists()


def can_view_all_branches(user):
    """Organisation administrators see every branch in their organisations."""
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser or is_platform_admin(user):
        return True
    return user.memberships.filter(
        is_active=True, role=Role.OPERATOR_ADMIN
    ).exists()


def branch_ids_for(user):
    """Branches the user may see.

    Organisation admins get every branch in the organisations they administer.
    Branch-scoped staff get only the branches on their active memberships.
    Shared (null-branch) records are handled by callers, not here.
    """
    if not user or not user.is_authenticated:
        return []
    if user.is_superuser or is_platform_admin(user):
        return list(Branch.objects.values_list("id", flat=True))

    memberships = user.memberships.filter(is_active=True)
    admin_org_ids = list(
        memberships.filter(role=Role.OPERATOR_ADMIN).values_list(
            "organization_id", flat=True
        )
    )
    branch_ids = set(
        memberships.filter(branch__isnull=False).values_list("branch_id", flat=True)
    )
    if admin_org_ids:
        branch_ids.update(
            Branch.objects.filter(organization_id__in=admin_org_ids).values_list(
                "id", flat=True
            )
        )
    return list(branch_ids)


def default_branch_for(user, organization_id):
    """Best branch to attribute a sale to for ``user`` in an organisation.

    A branch-scoped membership wins. Otherwise an HQ user gets the head office,
    falling back to the first active branch.
    """
    if user and user.is_authenticated:
        membership = (
            user.memberships.filter(
                is_active=True, organization_id=organization_id, branch__isnull=False
            )
            .select_related("branch")
            .first()
        )
        if membership is not None and membership.branch_id:
            return membership.branch
    return (
        Branch.objects.filter(organization_id=organization_id, is_active=True)
        .order_by("-is_head_office", "name")
        .first()
    )


def organization_ids_for(user):
    """Active organization ids for a user (operator isolation scope).

    Platform administrators and superusers operate across every organization,
    including ones they have just created but not yet been given a membership
    in. Without this every org created from the setup screen would disappear
    from the creator's list the moment it was saved.
    """
    if not user or not user.is_authenticated:
        return []
    if user.is_superuser or user.memberships.filter(
        is_active=True, role=Role.PLATFORM_ADMIN
    ).exists():
        return list(Organization.objects.values_list("id", flat=True))
    return list(
        user.memberships.filter(is_active=True).values_list("organization_id", flat=True)
    )