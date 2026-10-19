from django.db import models

from apps.common.models import BaseModel, OrganizationScopedQuerySet


class Order(BaseModel):
    """A purchase of a service against an accepted (immutable) quote
    (blueprint 1, 17). Commercial state lives here; payment state lives on
    PaymentAttempt and they must be read separately."""

    class Status(models.TextChoices):
        CREATED = "created", "Created"
        CONFIRMED = "confirmed", "Confirmed"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    organization = models.ForeignKey(
        "accounts.Organization",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="orders",
    )
    customer = models.ForeignKey(
        "accounts.User", on_delete=models.PROTECT, related_name="orders"
    )
    service = models.ForeignKey(
        "catalog.Service", on_delete=models.PROTECT, related_name="orders"
    )
    quote = models.ForeignKey(
        "catalog.Quote", on_delete=models.PROTECT, related_name="orders"
    )
    currency = models.CharField(max_length=3, default="KES")
    total_minor = models.PositiveBigIntegerField()
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.CREATED, db_index=True
    )
    is_payment_required = models.BooleanField(default=True)
    service_detail = models.JSONField(default=dict, blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancel_reason = models.CharField(max_length=255, blank=True)

    objects = OrganizationScopedQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "created_at"]),
        ]

    def __str__(self):
        return f"Order {self.pk} ({self.service_id}) {self.currency} {self.total_minor}"

    def confirm(self):
        from django.utils import timezone

        if self.status != self.Status.CREATED:
            raise ValueError("Only created orders can be confirmed.")
        self.status = self.Status.CONFIRMED
        self.confirmed_at = timezone.now()
        self.save(update_fields=["status", "confirmed_at", "updated_at"])

    def complete(self):
        """Mark a confirmed order delivered. The performance obligation is
        satisfied here, which is the point revenue is recognised from unearned
        revenue; a created (unpaid) order cannot complete, and an already
        terminal order is left alone."""
        from django.utils import timezone

        if self.status != self.Status.CONFIRMED:
            raise ValueError("Only a confirmed order can be completed.")
        self.status = self.Status.COMPLETED
        self.completed_at = timezone.now()
        self.save(update_fields=["status", "completed_at", "updated_at"])

    def cancel(self, reason=""):
        from django.utils import timezone

        if self.status in (self.Status.COMPLETED, self.Status.CANCELLED):
            raise ValueError("Order is already terminal.")
        self.status = self.Status.CANCELLED
        self.cancelled_at = timezone.now()
        self.cancel_reason = reason
        self.save(update_fields=["status", "cancelled_at", "cancel_reason", "updated_at"])