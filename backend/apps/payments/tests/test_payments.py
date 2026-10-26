"""Payment idempotency tests (blueprint 13, 23): a replay of the same
idempotency key and repeated/or stale callbacks must not create a second
business effect."""

import uuid
from unittest import mock

from django.db import DatabaseError, IntegrityError, transaction
from django.test import TestCase

from apps.accounts.models import Membership, Organization, Role, User
from apps.audit.models import AuditEvent
from apps.catalog.models import Service, build_quote
from apps.orders.models import Order
from apps.payments.models import LedgerEntry, PaymentAttempt
from apps.payments.services import (
    PaymentError,
    create_payment_attempt,
    handle_payment_callback,
    record_cash_collection,
)


class PaidOrderFixture(TestCase):
    """An organization, a customer and one KES 300 order awaiting payment."""

    def setUp(self):
        self.org = Organization.objects.create(name="Gamma Logistics", slug="gamma-logistics")
        self.user = User.objects.create_user("0722000001", "pass-X-1234")
        Membership.objects.create(user=self.user, organization=self.org, role=Role.OPERATOR_ADMIN)
        service = Service.objects.create(slug="courier", name="Courier", service_type="courier")
        quote = build_quote(
            service=service,
            request_payload={"from": "nairobi", "to": "nairobi"},
            total_minor=30000,
            organization=self.org,
        )
        quote.accept()
        self.order = Order.objects.create(
            organization=self.org,
            customer=self.user,
            service=service,
            quote=quote,
            currency="KES",
            total_minor=30000,
        )

    def _attempt(self, key="11111111-1111-1111-1111-111111111111"):
        return create_payment_attempt(
            organization_id=self.org.pk,
            order_id=self.order.pk,
            idempotency_key=key,
            amount_minor=30000,
            currency="KES",
            provider_slug="mpesa",
        )

    @staticmethod
    def _success_callback(attempt, amount_minor=30000):
        return {
            "provider_reference": attempt.provider_reference,
            "status": "success",
            "amount_minor": amount_minor,
            "currency": "KES",
        }


class PaymentFlowTestCase(PaidOrderFixture):
    def test_same_idempotency_key_reuses_the_attempt(self):
        key = "11111111-1111-1111-1111-111111111111"
        first = create_payment_attempt(
            organization_id=self.org.pk,
            order_id=self.order.pk,
            idempotency_key=key,
            amount_minor=30000,
            currency="KES",
            provider_slug="mpesa",
        )
        second = create_payment_attempt(
            organization_id=self.org.pk,
            order_id=self.order.pk,
            idempotency_key=key,
            amount_minor=30000,
            currency="KES",
            provider_slug="mpesa",
        )
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(PaymentAttempt.objects.count(), 1)

    def test_same_key_different_amount_is_rejected(self):
        key = "22222222-2222-2222-2222-222222222222"
        create_payment_attempt(
            organization_id=self.org.pk,
            order_id=self.order.pk,
            idempotency_key=key,
            amount_minor=30000,
            currency="KES",
        )
        with self.assertRaises(PaymentError):
            create_payment_attempt(
                organization_id=self.org.pk,
                order_id=self.order.pk,
                idempotency_key=key,
                amount_minor=31000,
                currency="KES",
            )

    def test_duplicate_callback_applies_exactly_once(self):
        attempt = create_payment_attempt(
            organization_id=self.org.pk,
            order_id=self.order.pk,
            idempotency_key="33333333-3333-3333-3333-333333333333",
            amount_minor=30000,
            currency="KES",
            provider_slug="mpesa",
        )
        callback = {
            "provider_reference": attempt.provider_reference,
            "status": "success",
            "amount_minor": 30000,
            "currency": "KES",
        }
        first_attempt, applied_first = handle_payment_callback("mpesa", callback)
        replay_attempt, applied_second = handle_payment_callback("mpesa", callback)

        self.assertTrue(applied_first)
        self.assertFalse(applied_second)
        self.assertEqual(first_attempt.state, PaymentAttempt.State.SUCCESS)
        self.assertEqual(replay_attempt.state, PaymentAttempt.State.SUCCESS)
        # Exactly one successful collection -> one balanced pair of entries.
        successful = PaymentAttempt.objects.filter(
            pk=attempt.pk, state=PaymentAttempt.State.SUCCESS
        )
        self.assertEqual(successful.count(), 1)
        self.assertTrue(LedgerEntry.objects.filter(payment_attempt=attempt, entry_type="debit").exists())
        self.assertTrue(LedgerEntry.objects.filter(payment_attempt=attempt, entry_type="credit").exists())

    def test_callback_with_wrong_amount_marks_unknown(self):
        attempt = create_payment_attempt(
            organization_id=self.org.pk,
            order_id=self.order.pk,
            idempotency_key="44444444-4444-4444-4444-444444444444",
            amount_minor=30000,
            currency="KES",
            provider_slug="mpesa",
        )
        callback = {
            "provider_reference": attempt.provider_reference,
            "status": "success",
            "amount_minor": 99999,
            "currency": "KES",
        }
        attempt, applied = handle_payment_callback("mpesa", callback)
        self.assertTrue(applied)
        self.assertEqual(attempt.state, PaymentAttempt.State.UNKNOWN)
        self.assertFalse(LedgerEntry.objects.filter(payment_attempt=attempt).exists())

    def test_state_cannot_move_backwards_after_success(self):
        attempt = create_payment_attempt(
            organization_id=self.org.pk,
            order_id=self.order.pk,
            idempotency_key="55555555-5555-5555-5555-555555555555",
            amount_minor=30000,
            currency="KES",
            provider_slug="mpesa",
        )
        handle_payment_callback(
            "mpesa",
            {"provider_reference": attempt.provider_reference, "status": "success",
             "amount_minor": 30000, "currency": "KES"},
        )
        # A later 'failed' callback for the same terminal reference must be stale.
        stale, applied = handle_payment_callback(
            "mpesa",
            {"provider_reference": attempt.provider_reference, "status": "failed",
             "amount_minor": 30000, "currency": "KES"},
        )
        self.assertFalse(applied)
        self.assertEqual(stale.state, PaymentAttempt.State.SUCCESS)

    def test_amount_must_match_order_total(self):
        with self.assertRaises(PaymentError):
            create_payment_attempt(
                organization_id=self.org.pk,
                order_id=self.order.pk,
                idempotency_key="66666666-6666-6666-6666-666666666666",
                amount_minor=100,  # under-payment attempt
                currency="KES",
            )

    def test_organization_must_match_order_organization(self):
        other = Organization.objects.create(name="Other Ltd", slug="other-org")
        with self.assertRaises(PaymentError):
            create_payment_attempt(
                organization_id=other.pk,
                order_id=self.order.pk,
                idempotency_key="77777777-7777-7777-7777-777777777777",
                amount_minor=30000,
                currency="KES",
            )

class OrderConfirmationTestCase(PaidOrderFixture):
    """A collected order leaves "created" (blueprint 1, 13).

    Nothing but the payment callback used to call ``Order.confirm()``, so a
    fully paid order stayed "created" forever and the one API route that could
    confirm it was manual.
    """

    def test_a_successful_collection_confirms_the_order(self):
        attempt = self._attempt()
        _, applied = handle_payment_callback("mpesa", self._success_callback(attempt))
        self.assertTrue(applied)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.CONFIRMED)
        self.assertIsNotNone(self.order.confirmed_at)

    def test_a_replayed_callback_confirms_exactly_once(self):
        attempt = self._attempt("22222222-2222-2222-2222-222222222222")
        _, applied_first = handle_payment_callback(
            "mpesa", self._success_callback(attempt)
        )
        self.order.refresh_from_db()
        confirmed_at = self.order.confirmed_at
        self.assertIsNotNone(confirmed_at)

        _, applied_second = handle_payment_callback(
            "mpesa", self._success_callback(attempt)
        )
        self.assertTrue(applied_first)
        self.assertFalse(applied_second)
        self.order.refresh_from_db()
        self.assertEqual(self.order.confirmed_at, confirmed_at)

    def test_a_failed_payment_leaves_the_order_created(self):
        attempt = self._attempt("33333333-3333-3333-3333-333333333333")
        callback = dict(self._success_callback(attempt), status="failed")
        handle_payment_callback("mpesa", callback)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.CREATED)
        self.assertIsNone(self.order.confirmed_at)

    def test_a_late_collection_does_not_resurrect_a_cancelled_order(self):
        # The status guard holds on its own, so a caller that skipped the
        # terminal-callback check would still be refused.
        from apps.payments.services import _confirm_order

        self.order.cancel(reason="customer changed their mind")
        _confirm_order(self.order)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.CANCELLED)


class BookingFailureCannotCostTheCollectionTestCase(PaidOrderFixture):
    """A failure to turn a hold into a ticket never costs us the collection.

    The booking work runs on a savepoint of the callback's transaction, so a
    database failure inside it is undone alone: the collection and the ledger
    entry recording it still commit, and the failure is audited against the
    order (blueprint 7: recorded collection, refund/rebook owed).
    """

    def _fail_ticketing(self, error):
        return mock.patch(
            "apps.booking.services.issue_ticket_for_order",
            side_effect=error,
        )

    def test_the_collection_and_the_failure_are_both_recorded(self):
        attempt = self._attempt("44444444-4444-4444-4444-444444444444")
        with self._fail_ticketing(DatabaseError("current transaction is aborted")):
            _, applied = handle_payment_callback(
                "mpesa", self._success_callback(attempt)
            )

        self.assertTrue(applied)
        attempt.refresh_from_db()
        self.assertEqual(attempt.state, PaymentAttempt.State.SUCCESS)
        self.assertEqual(LedgerEntry.objects.filter(payment_attempt=attempt).count(), 2)

        failure = AuditEvent.objects.get(action="payment.ticket_issue_failed")
        self.assertEqual(failure.entity_id, self.order.pk)
        self.assertEqual(failure.new_value["error"], "DatabaseError")

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.CONFIRMED)

    def test_the_callback_transaction_is_left_usable_after_the_failure(self):
        # The invariant the savepoint exists for. On PostgreSQL a failure of
        # this shape marks the surrounding transaction aborted, so without the
        # isolation every write below -- including the audit record -- is
        # refused with "current transaction is aborted" and the collection
        # rolls back. That refusal cannot be produced on SQLite, so this is
        # proved for real by the PostgreSQL run (config.settings.test_postgres).
        from django.db import connection

        attempt = self._attempt("55555555-5555-5555-5555-555555555555")
        with self._fail_ticketing(DatabaseError("connection reset")):
            handle_payment_callback("mpesa", self._success_callback(attempt))

        self.assertFalse(connection.needs_rollback)
        AuditEvent.objects.create(action="payment.after_booking_failure")
        self.assertTrue(
            AuditEvent.objects.filter(action="payment.after_booking_failure").exists()
        )


class ProviderReferenceUniquenessTestCase(PaidOrderFixture):
    """One provider reference names one collection (blueprint 13, 17)."""

    def test_two_attempts_cannot_carry_the_same_provider_reference(self):
        first = self._attempt("66666666-6666-6666-6666-666666666666")
        second = self._attempt("77777777-7777-7777-7777-777777777777")
        first.provider_reference = "MPDUPLICATE"
        first.save(update_fields=["provider_reference", "updated_at"])
        second.provider_reference = "MPDUPLICATE"
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                second.save(update_fields=["provider_reference", "updated_at"])

    def test_attempts_that_have_no_reference_yet_are_not_constrained(self):
        first = self._attempt("88888888-8888-8888-8888-888888888888")
        second = self._attempt("99999999-9999-9999-9999-999999999999")
        first.provider_reference = None
        second.provider_reference = None
        first.save(update_fields=["provider_reference", "updated_at"])
        second.save(update_fields=["provider_reference", "updated_at"])
        self.assertEqual(
            PaymentAttempt.objects.filter(provider_reference=None).count(), 2
        )

    def test_an_ambiguous_reference_refuses_the_callback(self):
        # Unreachable while the constraint holds; the mapping keeps it from
        # becoming a 500 and from applying money to an arbitrary attempt.
        attempt = self._attempt("12121212-1212-1212-1212-121212121212")
        with mock.patch.object(
            PaymentAttempt.objects,
            "get",
            side_effect=PaymentAttempt.MultipleObjectsReturned,
        ):
            with self.assertRaises(PaymentError):
                handle_payment_callback("mpesa", self._success_callback(attempt))
        attempt.refresh_from_db()
        self.assertEqual(attempt.state, PaymentAttempt.State.PENDING)


class AuditIsTransactionalTestCase(PaidOrderFixture):
    """record_audit commits with the caller's work or fails it.

    Pinned because the contract is the whole point of M3: an audit event
    silently dropped under load would be a hole in the trail, so the write is
    deliberately allowed to take the caller down with it.
    """

    def test_a_failure_to_write_the_audit_record_fails_the_caller(self):
        attempt = self._attempt("13131313-1313-1313-1313-131313131313")
        with mock.patch.object(
            AuditEvent.objects,
            "create",
            side_effect=DatabaseError("audit storage unavailable"),
        ):
            with self.assertRaises(DatabaseError):
                handle_payment_callback("mpesa", self._success_callback(attempt))

        attempt.refresh_from_db()
        self.assertEqual(attempt.state, PaymentAttempt.State.PENDING)
        self.assertFalse(
            LedgerEntry.objects.filter(payment_attempt=attempt).exists()
        )


class CashCollectionTestCase(PaidOrderFixture):
    """Cash is collected once, against the exact order, and issues the ticket."""

    def test_cash_confirms_the_order_and_posts_a_balanced_pair(self):
        attempt, created = record_cash_collection(
            order=self.order, actor=self.user
        )
        self.assertTrue(created)
        self.assertEqual(attempt.provider, "cash")
        self.assertEqual(attempt.state, PaymentAttempt.State.SUCCESS)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.CONFIRMED)
        entries = LedgerEntry.objects.filter(payment_attempt=attempt)
        self.assertEqual(entries.count(), 2)
        self.assertEqual(
            {entry.entry_type for entry in entries}, {"debit", "credit"}
        )

    def test_repeating_the_key_returns_the_same_collection(self):
        key = uuid.uuid4()
        first, created = record_cash_collection(
            order=self.order, actor=self.user, idempotency_key=key
        )
        second, created_again = record_cash_collection(
            order=self.order, actor=self.user, idempotency_key=key
        )
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(PaymentAttempt.objects.filter(order=self.order).count(), 1)

    def test_cash_cannot_be_collected_twice(self):
        record_cash_collection(order=self.order, actor=self.user)
        with self.assertRaises(PaymentError):
            record_cash_collection(order=self.order, actor=self.user)

    def test_a_mismatched_amount_is_refused(self):
        with self.assertRaises(PaymentError):
            record_cash_collection(
                order=self.order, actor=self.user, amount_minor=29_999
            )
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.CREATED)


class CashCollectionAPITestCase(PaidOrderFixture):
    """Counter staff can take cash; somebody outside the organization cannot."""

    def setUp(self):
        super().setUp()
        self.counter = User.objects.create_user("0722000099", "pass-C-1234")
        Membership.objects.create(
            user=self.counter, organization=self.org, role=Role.BRANCH_STAFF
        )
        from rest_framework.test import APIClient

        self.client = APIClient()
        self.client.force_authenticate(self.counter)

    def test_counter_staff_records_cash_and_the_order_confirms(self):
        response = self.client.post(
            "/api/v1/payments/cash/",
            {
                "order": str(self.order.pk),
                "amount_minor": self.order.total_minor,
                "currency": self.order.currency,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["state"], PaymentAttempt.State.SUCCESS)
        self.assertEqual(response.data["order_status"], Order.Status.CONFIRMED)

    def test_an_outsider_cannot_record_cash(self):
        outsider = User.objects.create_user("0722000088", "pass-O-1234")
        self.client.force_authenticate(outsider)
        response = self.client.post(
            "/api/v1/payments/cash/",
            {
                "order": str(self.order.pk),
                "amount_minor": self.order.total_minor,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 403)
