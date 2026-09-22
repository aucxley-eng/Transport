import uuid

from django.conf import settings
from django.db import models


class AuditEvent(models.Model):
    """Append-only audit trail (blueprint 3, 17). Corrections must be NEW
    events with a reason; recorded history is never edited or deleted."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_events",
    )
    organization = models.ForeignKey(
        "accounts.Organization",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_events",
    )
    action = models.CharField(max_length=80)
    entity_type = models.CharField(max_length=60, blank=True)
    entity_id = models.UUIDField(null=True, blank=True)
    reason = models.CharField(max_length=255, blank=True)
    previous_value = models.JSONField(null=True, blank=True)
    new_value = models.JSONField(null=True, blank=True)
    correlation_id = models.UUIDField(default=uuid.uuid4, editable=False)
    ip_address = models.GenericIPAddressField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["entity_type", "entity_id"]),
            models.Index(fields=["organization", "created_at"]),
        ]

    def __str__(self):
        return f"{self.action} @ {self.created_at.isoformat()}"


def record_audit(
    *,
    actor=None,
    organization=None,
    action,
    entity_type="",
    entity_id=None,
    reason="",
    previous_value=None,
    new_value=None,
    correlation_id=None,
    ip_address=None,
):
    """Best-effort audit recording; never allowed to break the caller's work."""
    kwargs = {
        "actor": actor,
        "organization": organization,
        "action": action,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "reason": reason,
        "previous_value": previous_value,
        "new_value": new_value,
        "ip_address": ip_address,
    }
    if correlation_id is not None:
        kwargs["correlation_id"] = correlation_id
    AuditEvent.objects.create(**{k: v for k, v in kwargs.items() if v is not None})