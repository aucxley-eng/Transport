"""Order creation, where a quoted price becomes a payable one.

Each test names the rule it enforces. The currency rule is here because the
order is the document a payment attempt and a ledger entry are both built from:
a unit relabelled at this point is wrong in every record that follows.
"""

from rest_framework.test import APITestCase

from apps.accounts.models import Membership, Organization, Role, User
from apps.catalog.models import Service, build_quote


class OrderCurrencyTestCase(APITestCase):
    """The quote's currency is the order's currency.

    A client that could choose the unit would be choosing how much money the
    payment attempt and the ledger entry claim to represent.
    """

    def setUp(self):
        self.org = Organization.objects.create(
            name="Dollar Line", slug="dollar-line", currency="USD"
        )
        self.customer = User.objects.create_user("0711100055", "pass-W-1234")
        Membership.objects.create(
            user=self.customer, organization=self.org, role=Role.CUSTOMER
        )
        self.service = Service.objects.create(
            slug="parcel-usd", name="Parcel USD", service_type=Service.ServiceType.PARCEL
        )

    def _accepted_quote(self, currency="USD"):
        quote = build_quote(
            service=self.service,
            request_payload={"route": "nairobi-kisumu"},
            total_minor=25000,
            currency=currency,
            organization=self.org,
        )
        quote.accept()
        return quote

    def test_the_client_cannot_relabel_the_money(self):
        quote = self._accepted_quote(currency="USD")
        response = self.client.post(
            "/api/v1/orders/",
            {"quote": str(quote.pk), "currency": "KES"},
            format="json",
            HTTP_AUTHORIZATION=self._token_for(self.customer),
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["currency"], "USD")
        self.assertEqual(response.data["quote_total_minor"], 25000)

    def test_the_currency_comes_from_the_quote_when_not_sent_at_all(self):
        quote = self._accepted_quote(currency="KES")
        response = self.client.post(
            "/api/v1/orders/",
            {"quote": str(quote.pk)},
            format="json",
            HTTP_AUTHORIZATION=self._token_for(self.customer),
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["currency"], "KES")

    @staticmethod
    def _token_for(user):
        from rest_framework_simplejwt.tokens import RefreshToken

        return f"Bearer {RefreshToken.for_user(user).access_token}"
