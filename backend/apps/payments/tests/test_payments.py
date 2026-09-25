"""Payment idempotency tests (blueprint 13, 23): a replay of the same
idempotency key and repeated/or stale callbacks must not create a second
business effect."""

from django.test import TestCase

from apps.accounts.models import Membership, Organization, Role, User
from apps.catalog.models import Service, build_quote
from apps.orders.models import Order
from apps.payments.models import LedgerEntry, PaymentAttempt
from apps.payments.services import (
    PaymentError,
    create_payment_attempt,
    handle_payment_callback,
)


class PaymentFlowTestCase(TestCase):
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