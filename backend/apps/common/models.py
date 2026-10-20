import uuid

from django.db import models
from django.db.models import Q


class BaseModel(models.Model):
    """Shared abstract base: UUID primary keys, created/updated tracking.

    Financial and custody records are append-only; senders of those must not
    inherit this mutable updated_at pattern (see payments.LedgerEntry).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
        ordering = ["-created_at"]


class OrganizationScopedQuerySet(models.QuerySet):
    """QuerySet that can be constrained to one or more organizations."""

    def for_organizations(self, organization_ids):
        if organization_ids is None:
            return self
        return self.filter(
            Q(organization_id__in=organization_ids)
            | Q(organization__parent_id__in=organization_ids)
        )


class OutboxEvent(BaseModel):
    """Transactional outbox (blueprint 6): written in the same DB transaction
    as the business change, then published durably and processed idempotently.
    """

    event_type = models.CharField(max_length=120)
    aggregate_type = models.CharField(max_length=60, blank=True)
    aggregate_id = models.UUIDField(null=True, blank=True)
    organization_id = models.UUIDField(null=True, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    correlation_id = models.UUIDField(default=uuid.uuid4, editable=False)

    state = models.CharField(
        max_length=16,
        choices=[
            ("pending", "Pending"),
            ("published", "Published"),
            ("failed", "Failed"),
        ],
        default="pending",
        db_index=True,
    )
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True)
    published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["state", "created_at"]),
        ]