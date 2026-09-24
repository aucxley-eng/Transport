"""Transactional outbox helpers (blueprint 6).

A business change and its outbox row are written atomically inside the same
transaction through ``record_outbox``. Publication happens later via the
``publish_outbox`` management command (or a worker); subscribers receive each
event with a stable ``correlation_id`` and must be idempotent.
"""

import logging
import uuid

from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

# Registry of subscriber callables: event_type -> set of callables.
_SUBSCRIBERS = {}


class OutboxError(Exception):
    pass


def subscribe(event_type):
    """Register a subscriber for an event type. Subscribers are idempotent
    handlers that receive the serialized OutboxEvent dict."""

    def decorator(func):
        _SUBSCRIBERS.setdefault(event_type, set()).add(func)
        return func

    return decorator


def record_outbox(
    *,
    event_type: str,
    aggregate_type: str = "",
    aggregate_id=None,
    organization_id=None,
    payload: dict | None = None,
    correlation_id=None,
):
    """Append an outbox event inside the current transaction (or a new one
    if none is active). Set ``payload`` last for simplicity; serialized as JSON."""
    from .models import OutboxEvent

    event = OutboxEvent(
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        organization_id=organization_id,
        payload=payload or {},
        correlation_id=correlation_id or uuid.uuid4(),
    )
    event.save()
    return event


@transaction.atomic
def publish_single(event_id):
    """Publish one event. Called by the relay command; safe to retry."""
    from .models import OutboxEvent

    try:
        with transaction.atomic():
            event = (
                OutboxEvent.objects.select_for_update()
                .get(pk=event_id, state="pending")
            )
            event.attempts += 1
            event.save(update_fields=["attempts", "updated_at"])
    except OutboxEvent.DoesNotExist:
        # Already published (successful) or not pending; nothing to do.
        return False

    serialized = {
        "event_id": str(event.pk),
        "event_type": event.event_type,
        "aggregate_type": event.aggregate_type,
        "aggregate_id": str(event.aggregate_id) if event.aggregate_id else None,
        "organization_id": str(event.organization_id) if event.organization_id else None,
        "payload": event.payload,
        "correlation_id": str(event.correlation_id),
        "occurred_at": event.created_at.isoformat(),
    }

    # Steps outside the locking transaction: subscribers may perform slow work.
    try:
        for subscriber in _SUBSCRIBERS.get(event.event_type, ()):
            subscriber(serialized)
    except Exception as exc:  # noqa: BLE001 - keep the event for inspection/retry.
        message = f"outbox {event.pk} failed after attempt {event.attempts}: {exc}"
        logger.exception(message)
        from .models import OutboxEvent as Ev

        Ev.objects.filter(pk=event.pk).update(
            state="failed",
            last_error=str(message)[:8000],
            updated_at=timezone.now(),
        )
        return False

    # Accepted by every subscriber -> mark published.
    from .models import OutboxEvent as Ev

    Ev.objects.filter(pk=event.pk).update(
        state="published",
        published_at=timezone.now(),
        updated_at=timezone.now(),
    )
    return True