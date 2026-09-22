"""Transactional outbox tests (blueprint 6): events are written atomically
with their business change and publish exactly once."""

from django.db import transaction
from django.test import TestCase

from apps.common.models import OutboxEvent
from apps.common.outbox import record_outbox, publish_single, subscribe


class OutboxTestCase(TestCase):
    def test_record_is_atomic_with_the_change(self):
        try:
            with transaction.atomic():
                record_outbox(event_type="TestEvent", payload={"n": 1})
                raise RuntimeError("roll back")
        except RuntimeError:
            pass
        self.assertEqual(OutboxEvent.objects.count(), 0)

    def test_publish_invokes_subscriber_and_marks_published(self):
        received = []
        subscribe("MyEvent")(lambda event: received.append(event["correlation_id"]))

        event = record_outbox(
            event_type="MyEvent",
            aggregate_type="order",
            payload={"a": 1},
        )
        self.assertEqual(event.state, "pending")

        published = publish_single(event.pk)
        self.assertTrue(published)
        event.refresh_from_db()
        self.assertEqual(event.state, "published")
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0], str(event.correlation_id))

    def test_publish_is_skipped_for_already_published(self):
        event = record_outbox(event_type="OnceEvent", payload={})
        self.assertTrue(publish_single(event.pk))
        self.assertFalse(publish_single(event.pk))

    def test_failing_subscriber_marks_failed_but_keeps_data(self):
        def boom(_event):
            raise ValueError("subscriber exploded")

        subscribe("BoomEvent")(boom)
        event = record_outbox(event_type="BoomEvent", payload={"x": 1})
        self.assertFalse(publish_single(event.pk))
        event.refresh_from_db()
        self.assertEqual(event.state, "failed")
        self.assertGreater(event.attempts, 0)
        self.assertIn("subscriber exploded", event.last_error)