"""Operator isolation tests (blueprint 3): records are invisible across
organizations even with valid tokens, object-level lookups and list views."""

from rest_framework.test import APITestCase

from apps.accounts.models import Membership, Organization, Role, User
from apps.catalog.models import Service, build_quote
from apps.fleet.models import Vehicle
from apps.orders.models import Order


class IsolationTestCase(APITestCase):
    def setUp(self):
        self.org_a = Organization.objects.create(name="Alpha Sacco", slug="alpha")
        self.org_b = Organization.objects.create(name="Beta Sacco", slug="beta")
        self.service = Service.objects.create(
            slug="intercity-parcel", name="Intercity Parcel", service_type="parcel"
        )

        self.user_a = User.objects.create_user("0711000001", "pass-A-1234")
        self.user_b = User.objects.create_user("0711000002", "pass-B-1234")
        Membership.objects.create(user=self.user_a, organization=self.org_a, role=Role.OPERATOR_ADMIN)
        Membership.objects.create(user=self.user_b, organization=self.org_b, role=Role.OPERATOR_ADMIN)

        self.token_a = f"Bearer {self._token_for(self.user_a)}"
        self.token_b = f"Bearer {self._token_for(self.user_b)}"

    @staticmethod
    def _token_for(user):
        from rest_framework_simplejwt.tokens import RefreshToken

        return str(RefreshToken.for_user(user).access_token)

    def _org_owned_data(self):
        quote_a = build_quote(
            service=self.service,
            request_payload={"route": "nairobi-nakuru"},
            total_minor=25000,
            organization=self.org_a,
        )
        quote_a.accept()
        order_a = Order.objects.create(
            organization=self.org_a,
            customer=self.user_a,
            service=self.service,
            quote=quote_a,
            currency="KES",
            total_minor=25000,
        )
        Vehicle.objects.create(
            organization=self.org_a,
            registration_number="KDA123A",
            vehicle_class="shuttle",
            seating_capacity=14,
        )
        return quote_a, order_a

    def test_vehicles_are_invisible_to_other_operator(self):
        self._org_owned_data()
        response = self.client.get("/api/v1/vehicles/", HTTP_AUTHORIZATION=self.token_b)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 0)
        self.assertEqual(response.json()["results"], [])

    def test_quotes_are_invisible_to_other_operator(self):
        self._org_owned_data()
        response = self.client.get("/api/v1/quotes/", HTTP_AUTHORIZATION=self.token_b)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 0)
        self.assertEqual(response.json()["results"], [])

    def test_order_detail_lookup_cross_org_uses_scope(self):
        _, order = self._org_owned_data()
        response = self.client.get(
            f"/api/v1/orders/{order.pk}/", HTTP_AUTHORIZATION=self.token_b
        )
        self.assertEqual(response.status_code, 404)

    def test_audit_events_are_org_scoped(self):
        self._org_owned_data()
        response = self.client.get("/api/v1/audit/events/", HTTP_AUTHORIZATION=self.token_b)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    def test_other_user_cannot_confirm_someone_elses_order(self):
        _, order = self._org_owned_data()
        response = self.client.post(
            f"/api/v1/orders/{order.pk}/confirm/",
            HTTP_AUTHORIZATION=self.token_b,
        )
        self.assertEqual(response.status_code, 404)