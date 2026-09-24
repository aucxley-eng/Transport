"""Quote lifecycle tests (blueprint 9): acceptance is impossible after a
quote's expiry window, and the quote is marked EXPIRED rather than accepted."""

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from apps.catalog.models import Quote, Service, build_quote


class QuoteExpiryTestCase(TestCase):
    def setUp(self):
        self.service = Service.objects.create(
            slug="courier-expiry", name="Courier", service_type="courier"
        )

    def test_expired_quote_cannot_be_accepted(self):
        quote = build_quote(
            service=self.service,
            request_payload={"from": "nairobi", "to": "nakuru"},
            total_minor=25000,
        )
        Quote.objects.filter(pk=quote.pk).update(
            expires_at=timezone.now() - timedelta(minutes=1)
        )
        quote.refresh_from_db()

        with self.assertRaises(ValueError):
            quote.accept()

        quote.refresh_from_db()
        self.assertEqual(quote.status, Quote.Status.EXPIRED)

    def test_quote_within_window_is_accepted(self):
        quote = build_quote(
            service=self.service,
            request_payload={"from": "nairobi", "to": "nakuru"},
            total_minor=25000,
            expires_in_minutes=15,
        )
        quote.accept()
        self.assertEqual(quote.status, Quote.Status.ACCEPTED)
        self.assertIsNotNone(quote.accepted_at)