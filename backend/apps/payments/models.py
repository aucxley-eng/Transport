import uuid

from django.db import models
from django.db.models import Q

from apps.common.models import BaseModel, OrganizationScopedQuerySet


class PaymentAttempt(BaseModel):
    """One money movement request. States: pending / success / failed /
    unknown. A timeout does not prove failure; unknown results must be
    reconciled before retrying (blueprint 13)."""

    class State(models.TextChoices):
        PENDING = "pending", "Pending"
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"
        UNKNOWN = "unknown", "Unknown"

    organization = models.ForeignKey(
        "accounts.Organization",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="payment_attempts",
    )
    order = models.ForeignKey(
        "orders.Order",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="payment_attempts",
    )
    branch = models.ForeignKey(
        "accounts.Branch",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="payment_attempts",
        help_text="Branch that collected the money.",
    )
    provider = models.CharField(max_length=32, default="mpesa")
    idempotency_key = models.UUIDField(default=uuid.uuid4)
    expected_amount_minor = models.PositiveBigIntegerField()
    currency = models.CharField(max_length=3, default="KES")
    customer_phone = models.CharField(max_length=16, blank=True)
    provider_reference = models.CharField(max_length=120, null=True, blank=True, db_index=True)
    state = models.CharField(
        max_length=12, choices=State.choices, default=State.PENDING, db_index=True
    )
    provider_payload = models.JSONField(null=True, blank=True)
    result_received = models.BooleanField(default=False)
    note = models.TextField(blank=True)

    objects = OrganizationScopedQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "idempotency_key"],
                name="uq_paymentattempt_provider_key",
            ),
            # A provider reference names one collection, so it is unique rather
            # than merely indexed: two attempts carrying the same reference
            # would let a single provider callback be matched to an arbitrary
            # one of them and applied again. NULL and empty stay exempt, since
            # an attempt is routinely created before the provider has handed a
            # reference back.
            models.UniqueConstraint(
                fields=["provider", "provider_reference"],
                condition=Q(provider_reference__isnull=False)
                & ~Q(provider_reference=""),
                name="uq_paymentattempt_provider_ref",
            ),
        ]

    def __str__(self):
        return f"{self.provider} {self.state} {self.currency} {self.expected_amount_minor}"

    def is_terminal(self):
        return self.state in (self.State.SUCCESS, self.State.FAILED)


class Refund(BaseModel):
    """A reversal of a collection, recorded as its own first-class event.

    A refund is not an edit of the collection: it is a new, balanced set of
    ledger entries that moves the money back out, and this row records why.
    ``reference`` is the idempotency key -- replaying the same refund reference
    returns the same refund rather than posting twice. The split between
    ``from_unearned_minor`` and ``from_revenue_minor`` says whether the money
    came back before the service was recognised (out of the liability) or after
    (clawed back from revenue).
    """

    organization = models.ForeignKey(
        "accounts.Organization",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="refunds",
    )
    order = models.ForeignKey(
        "orders.Order", on_delete=models.PROTECT, related_name="refunds"
    )
    payment_attempt = models.ForeignKey(
        PaymentAttempt,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="refunds",
    )
    amount_minor = models.PositiveBigIntegerField()
    currency = models.CharField(max_length=3, default="KES")
    reference = models.CharField(max_length=120, unique=True)
    reason = models.CharField(max_length=255, blank=True)
    from_unearned_minor = models.PositiveBigIntegerField(default=0)
    from_revenue_minor = models.PositiveBigIntegerField(default=0)
    correlation_id = models.UUIDField(default=uuid.uuid4, editable=False)
    recorded_by = models.ForeignKey(
        "accounts.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="refunds_recorded",
    )

    objects = OrganizationScopedQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Refund {self.reference} {self.currency} {self.amount_minor}"


class LedgerEntry(models.Model):
    """Append-only balanced ledger entry (blueprint 13, 17). Records are never
    updated or deleted; corrections post new entries."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    journal = models.CharField(max_length=60, db_index=True)
    account = models.CharField(max_length=80, db_index=True)
    amount_minor = models.BigIntegerField()  # signed in accounting sense
    currency = models.CharField(max_length=3, default="KES")
    entry_type = models.CharField(
        max_length=8, choices=[("debit", "Debit"), ("credit", "Credit")]
    )
    order = models.ForeignKey(
        "orders.Order", null=True, blank=True, on_delete=models.SET_NULL
    )
    payment_attempt = models.ForeignKey(
        PaymentAttempt, null=True, blank=True, on_delete=models.SET_NULL
    )
    reference = models.CharField(max_length=120, blank=True)
    correlation_id = models.UUIDField(default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.entry_type} {self.amount_minor} {self.currency} [{self.account}]"