"""Ledger completeness (H5): revenue recognition, refunds, and the outbox
subscribers that drive them.

The money cycle is collect -> recognise -> (maybe) refund. Each test states the
invariant it protects; the recognition and refund tests fail if the ledger can
be driven out of balance or past what the order actually holds.
"""

from apps.common.models import OutboxEvent
from apps.common.outbox import _SUBSCRIBERS, publish_single, record_outbox
from apps.payments import ledger
from apps.payments.models import LedgerEntry, Refund
from apps.payments.services import (
    PaymentError,
    handle_payment_callback,
    recognize_revenue,
    refund_collection,
)
from apps.payments.tests.test_payments import PaidOrderFixture


class PaidOrderMixin(PaidOrderFixture):
    def _collect(self, amount_minor=30000):
        attempt = self._attempt()
        handle_payment_callback(
            "mpesa", self._success_callback(attempt, amount_minor=amount_minor)
        )
        return attempt

    def _balance(self, account):
        return ledger.account_balance(order=self.order, account=account)


class CollectionPostingTestCase(PaidOrderMixin):
    def test_a_collection_posts_a_balanced_pair_into_unearned_revenue(self):
        self._collect()
        self.assertEqual(self._balance(ledger.CASH_COLLECTIONS), 30000)
        self.assertEqual(self._balance(ledger.UNEARNED_REVENUE), 30000)
        self.assertEqual(self._balance(ledger.SERVICE_REVENUE), 0)

        postings = list(LedgerEntry.objects.filter(order=self.order))
        self.assertEqual(len(postings), 2)
        self.assertEqual({p.correlation_id for p in postings}, {postings[0].correlation_id})
        self.assertEqual(
            {p.entry_type for p in postings}, {"debit", "credit"}
        )


class RevenueRecognitionTestCase(PaidOrderMixin):
    def test_recognition_moves_the_collection_from_liability_to_revenue(self):
        self._collect()
        recognize_revenue(order=self.order)
        self.assertEqual(self._balance(ledger.UNEARNED_REVENUE), 0)
        self.assertEqual(self._balance(ledger.SERVICE_REVENUE), 30000)
        self.assertEqual(self._balance(ledger.CASH_COLLECTIONS), 30000)

    def test_recognition_is_idempotent(self):
        self._collect()
        first = recognize_revenue(order=self.order)
        second = recognize_revenue(order=self.order)
        self.assertIsNotNone(first)
        self.assertIsNone(second, "an empty unearned balance must post nothing")
        self.assertEqual(
            LedgerEntry.objects.filter(order=self.order, journal="recognition").count(),
            2,
        )
        self.assertEqual(self._balance(ledger.SERVICE_REVENUE), 30000)

    def test_an_uncollected_order_recognises_nothing(self):
        self.assertIsNone(recognize_revenue(order=self.order))
        self.assertFalse(LedgerEntry.objects.filter(journal="recognition").exists())


class RefundTestCase(PaidOrderMixin):
    def test_a_refund_before_delivery_reverses_the_liability(self):
        self._collect()
        refund = refund_collection(
            order=self.order, amount_minor=30000, reference="RF-1", reason="cancelled"
        )
        self.assertEqual(refund.from_unearned_minor, 30000)
        self.assertEqual(refund.from_revenue_minor, 0)
        self.assertEqual(self._balance(ledger.CASH_COLLECTIONS), 0)
        self.assertEqual(self._balance(ledger.UNEARNED_REVENUE), 0)

    def test_a_refund_after_delivery_claws_back_revenue(self):
        self._collect()
        recognize_revenue(order=self.order)
        refund = refund_collection(
            order=self.order, amount_minor=30000, reference="RF-2"
        )
        self.assertEqual(refund.from_unearned_minor, 0)
        self.assertEqual(refund.from_revenue_minor, 30000)
        self.assertEqual(self._balance(ledger.SERVICE_REVENUE), 0)
        self.assertEqual(self._balance(ledger.CASH_COLLECTIONS), 0)

    def test_a_partial_refund_leaves_the_rest_where_it_was(self):
        self._collect()
        refund_collection(order=self.order, amount_minor=10000, reference="RF-3")
        self.assertEqual(self._balance(ledger.CASH_COLLECTIONS), 20000)
        self.assertEqual(self._balance(ledger.UNEARNED_REVENUE), 20000)

    def test_a_refund_cannot_exceed_what_the_order_still_holds(self):
        self._collect()
        with self.assertRaises(PaymentError):
            refund_collection(order=self.order, amount_minor=30001, reference="RF-4")
        self.assertFalse(Refund.objects.exists())

    def test_a_replayed_reference_returns_the_same_refund(self):
        self._collect()
        first = refund_collection(order=self.order, amount_minor=10000, reference="RF-5")
        second = refund_collection(order=self.order, amount_minor=10000, reference="RF-5")
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Refund.objects.count(), 1)
        self.assertEqual(
            LedgerEntry.objects.filter(order=self.order, journal="refunds").count(), 2
        )

    def test_a_reference_reused_for_a_different_amount_is_refused(self):
        self._collect()
        refund_collection(order=self.order, amount_minor=10000, reference="RF-6")
        with self.assertRaises(PaymentError):
            refund_collection(order=self.order, amount_minor=20000, reference="RF-6")

    def test_a_refund_refuses_a_non_positive_amount(self):
        self._collect()
        with self.assertRaises(PaymentError):
            refund_collection(order=self.order, amount_minor=0, reference="RF-7")

    def test_the_ledger_stays_balanced_after_a_refund(self):
        self._collect()
        refund_collection(order=self.order, amount_minor=30000, reference="RF-8")
        for correlation in LedgerEntry.objects.filter(
            order=self.order, journal="refunds"
        ).values_list("correlation_id", flat=True).distinct():
            rows = LedgerEntry.objects.filter(correlation_id=correlation)
            debits = sum(r.amount_minor for r in rows if r.entry_type == "debit")
            credits = sum(r.amount_minor for r in rows if r.entry_type == "credit")
            self.assertEqual(debits, credits)


class FulfilmentSubscriberTestCase(PaidOrderMixin):
    """The recognition handlers are reached through the outbox relay, so these
    tests publish events rather than calling the handlers: that is what proves
    ``PaymentsConfig.ready`` registered them."""

    def _publish(self, event_type, order):
        event = record_outbox(
            event_type=event_type,
            aggregate_type="order",
            aggregate_id=order.pk,
            organization_id=self.org.pk,
            payload={"order_id": str(order.pk)},
        )
        return event, publish_single(event.pk)

    def test_the_fulfilment_subscribers_are_registered(self):
        self.assertIn("booking.ticket_boarded", _SUBSCRIBERS)
        self.assertIn("goods.courier_parcel_delivered", _SUBSCRIBERS)

    def test_a_delivery_of_a_completed_order_recognises_revenue(self):
        self._collect()
        self.order.refresh_from_db()
        self.order.complete()
        _, delivered = self._publish("goods.courier_parcel_delivered", self.order)
        self.assertTrue(delivered)
        self.assertEqual(self._balance(ledger.SERVICE_REVENUE), 30000)

    def test_boarding_a_completed_order_recognises_revenue(self):
        self._collect()
        self.order.refresh_from_db()
        self.order.complete()
        _, delivered = self._publish("booking.ticket_boarded", self.order)
        self.assertTrue(delivered)
        self.assertEqual(self._balance(ledger.SERVICE_REVENUE), 30000)

    def test_a_delivery_of_an_unfinished_order_recognises_nothing(self):
        self._collect()
        # The order was confirmed by the collection but not yet fulfilled.
        _, delivered = self._publish("goods.courier_parcel_delivered", self.order)
        self.assertTrue(delivered)
        self.assertEqual(self._balance(ledger.SERVICE_REVENUE), 0)
        self.assertEqual(self._balance(ledger.UNEARNED_REVENUE), 30000)

    def test_publishing_a_delivery_event_settles_the_order(self):
        self._collect()
        self.order.refresh_from_db()
        self.order.complete()
        event, _ = self._publish("goods.courier_parcel_delivered", self.order)
        event.refresh_from_db()
        self.assertEqual(event.state, "published")
        self.assertEqual(self._balance(ledger.SERVICE_REVENUE), 30000)

    def test_a_published_delivery_event_is_not_applied_twice(self):
        self._collect()
        self.order.refresh_from_db()
        self.order.complete()
        event, _ = self._publish("goods.courier_parcel_delivered", self.order)
        # The event is published, so the relay will not redeliver it.
        self.assertFalse(publish_single(event.pk))
        self.assertEqual(self._balance(ledger.SERVICE_REVENUE), 30000)
        self.assertEqual(OutboxEvent.objects.filter(state="published").count(), 1)
