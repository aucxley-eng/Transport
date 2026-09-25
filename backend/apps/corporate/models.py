"""Corporate travel and recurring shuttles (blueprint 8, "Corporate travel and
recurring shuttles").

The blueprint specifies the whole feature in four sentences:

  "A company defines eligible employees, cost centers, budgets, approval
   thresholds and permitted services. A travel request may require approval
   before dispatch. Recurring shuttles create dated departures from a schedule
   template and maintain a passenger roster. An employee leaving the company
   must lose future access without deleting historical invoices."

Each model below exists to hold one of those clauses. Nothing here manages seat
inventory: shuttles generate ordinary `booking.Departure` rows and reuse the
existing hold/ticket machinery.
"""

from django.db import models

from apps.common.models import BaseModel, OrganizationScopedQuerySet


class CorporatePolicy(BaseModel):
    """The company's rules: permitted services and the approval threshold.

    "A company defines ... approval thresholds and permitted services."
    """

    organization = models.OneToOneField(
        "accounts.Organization", on_delete=models.CASCADE, related_name="corporate_policy"
    )
    permitted_services = models.ManyToManyField(
        "catalog.Service", blank=True, related_name="corporate_policies"
    )
    approval_threshold_minor = models.PositiveBigIntegerField(
        default=0,
        help_text="Travel requests at or above this amount require approval before dispatch. "
        "Zero means every request needs approval.",
    )
    budget_period_start = models.DateField(null=True, blank=True)
    budget_period_end = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"Corporate policy {self.organization_id}"


class CostCenter(BaseModel):
    """A budget bucket employees charge travel to. "cost centers, budgets"."""

    organization = models.ForeignKey(
        "accounts.Organization", on_delete=models.CASCADE, related_name="cost_centers"
    )
    code = models.CharField(max_length=32)
    name = models.CharField(max_length=160)
    budget_minor = models.PositiveBigIntegerField(default=0)
    currency = models.CharField(max_length=3, default="KES")
    is_active = models.BooleanField(default=True)

    objects = OrganizationScopedQuerySet.as_manager()

    class Meta:
        ordering = ["code"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "code"], name="uq_cost_center_org_code"
            )
        ]

    def __str__(self):
        return f"{self.code} {self.name}"


class CorporateMember(BaseModel):
    """An eligible employee. Deactivation revokes future access only.

    "An employee leaving the company must lose future access without deleting
    historical invoices." The employee row is never deleted and never cascades
    to orders or tickets, so invoices survive departure.
    """

    organization = models.ForeignKey(
        "accounts.Organization", on_delete=models.CASCADE, related_name="corporate_members"
    )
    user = models.ForeignKey(
        "accounts.User", on_delete=models.PROTECT, related_name="corporate_memberships"
    )
    employee_ref = models.CharField(max_length=40)
    cost_center = models.ForeignKey(
        CostCenter,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="members",
    )
    joined_on = models.DateField(null=True, blank=True)
    left_on = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    notes = models.TextField(blank=True)

    objects = OrganizationScopedQuerySet.as_manager()

    class Meta:
        ordering = ["employee_ref"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "employee_ref"], name="uq_member_org_ref"
            )
        ]

    def __str__(self):
        return f"{self.employee_ref} ({'active' if self.is_active else 'inactive'})"

    def is_eligible(self, on_date=None):
        """Eligible employees only. A member who has left is not."""
        from django.utils import timezone

        on_date = on_date or timezone.localdate()
        if not self.is_active:
            return False
        if self.left_on and self.left_on <= on_date:
            return False
        if self.joined_on and self.joined_on > on_date:
            return False
        return True


class TravelRequest(BaseModel):
    """An employee's request to travel, subject to the company's rules.

    "A travel request may require approval before dispatch."
    """

    class Status(models.TextChoices):
        PENDING_APPROVAL = "pending_approval", "Pending approval"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Cancelled"
        FULFILLED = "fulfilled", "Fulfilled"

    organization = models.ForeignKey(
        "accounts.Organization", on_delete=models.CASCADE, related_name="travel_requests"
    )
    member = models.ForeignKey(
        CorporateMember, on_delete=models.PROTECT, related_name="travel_requests"
    )
    cost_center = models.ForeignKey(
        CostCenter,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="travel_requests",
    )
    departure = models.ForeignKey(
        "booking.Departure",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="travel_requests",
    )
    purpose = models.CharField(max_length=255, blank=True)
    estimated_minor = models.PositiveBigIntegerField(default=0)
    currency = models.CharField(max_length=3, default="KES")
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING_APPROVAL, db_index=True
    )
    requires_approval = models.BooleanField(default=True)
    decided_by = models.ForeignKey(
        "accounts.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="travel_decisions",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.TextField(blank=True)

    objects = OrganizationScopedQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["organization", "status"])]

    def __str__(self):
        return f"Travel request {self.pk} ({self.status})"


class ShuttleTemplate(BaseModel):
    """A recurring schedule that generates dated departures.

    "Recurring shuttles create dated departures from a schedule template."
    """

    organization = models.ForeignKey(
        "accounts.Organization", on_delete=models.CASCADE, related_name="shuttle_templates"
    )
    name = models.CharField(max_length=160)
    route = models.ForeignKey(
        "booking.Route", on_delete=models.PROTECT, related_name="shuttle_templates"
    )
    service = models.ForeignKey(
        "catalog.Service", on_delete=models.PROTECT, related_name="shuttle_templates"
    )
    departure_time = models.TimeField()
    weekdays = models.JSONField(
        default=list, blank=True, help_text="ISO weekdays to run on, 1=Monday .. 7=Sunday."
    )
    valid_from = models.DateField()
    valid_until = models.DateField(null=True, blank=True)
    capacity = models.PositiveSmallIntegerField(default=0)
    fare_minor = models.PositiveBigIntegerField(default=0)
    currency = models.CharField(max_length=3, default="KES")
    is_active = models.BooleanField(default=True)

    objects = OrganizationScopedQuerySet.as_manager()

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class ShuttleRun(BaseModel):
    """One dated instance of a shuttle template, bound to a real departure.

    The unique (template, date) pair is what makes generation idempotent.
    """

    organization = models.ForeignKey(
        "accounts.Organization", on_delete=models.CASCADE, related_name="shuttle_runs"
    )
    template = models.ForeignKey(
        ShuttleTemplate, on_delete=models.CASCADE, related_name="runs"
    )
    departure = models.OneToOneField(
        "booking.Departure", on_delete=models.CASCADE, related_name="shuttle_run"
    )
    run_date = models.DateField(db_index=True)

    objects = OrganizationScopedQuerySet.as_manager()

    class Meta:
        ordering = ["run_date"]
        constraints = [
            models.UniqueConstraint(
                fields=["template", "run_date"], name="uq_shuttle_run_template_date"
            )
        ]

    def __str__(self):
        return f"{self.template.name} on {self.run_date}"


class ShuttleRosterEntry(BaseModel):
    """A passenger on a shuttle run. "maintain a passenger roster"."""

    class State(models.TextChoices):
        PLANNED = "planned", "Planned"
        BOARDED = "boarded", "Boarded"
        NO_SHOW = "no_show", "No-show"
        CANCELLED = "cancelled", "Cancelled"

    organization = models.ForeignKey(
        "accounts.Organization", on_delete=models.CASCADE, related_name="shuttle_roster"
    )
    run = models.ForeignKey(ShuttleRun, on_delete=models.CASCADE, related_name="roster")
    member = models.ForeignKey(
        CorporateMember, on_delete=models.PROTECT, related_name="shuttle_roster_entries"
    )
    seat_number = models.PositiveSmallIntegerField(null=True, blank=True)
    state = models.CharField(
        max_length=12, choices=State.choices, default=State.PLANNED, db_index=True
    )

    objects = OrganizationScopedQuerySet.as_manager()

    class Meta:
        ordering = ["run", "member"]
        constraints = [
            models.UniqueConstraint(
                fields=["run", "member"], name="uq_shuttle_roster_run_member"
            )
        ]

    def __str__(self):
        return f"{self.member_id} on run {self.run_id} ({self.state})"
