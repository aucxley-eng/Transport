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

    def test_memberships_list_is_scoped_to_own_org(self):
        self._org_owned_data()
        response = self.client.get("/api/v1/memberships/", HTTP_AUTHORIZATION=self.token_a)
        self.assertEqual(response.status_code, 200)
        orgs = {m["organization"] for m in response.json()}
        self.assertEqual(orgs, {str(self.org_a.pk)})

    def test_org_members_route_is_scoped_by_url_and_honours_foreign_404(self):
        self._org_owned_data()
        # Readable for a member of the URL's organization.
        response = self.client.get(
            f"/api/v1/organizations/{self.org_a.pk}/members/",
            HTTP_AUTHORIZATION=self.token_a,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[0]["organization"], str(self.org_a.pk))
        # Foreign organization is not exposed and not 500s.
        response = self.client.get(
            f"/api/v1/organizations/{self.org_a.pk}/members/",
            HTTP_AUTHORIZATION=self.token_b,
        )
        self.assertEqual(response.status_code, 404)

class OrganizationCreationVisibilityTestCase(APITestCase):
    """A platform admin who creates an organization must be able to see it
    immediately, even before a membership row exists for them."""

    def _token_for(self, user):
        from rest_framework_simplejwt.tokens import RefreshToken

        return f"Bearer {RefreshToken.for_user(user).access_token}"

    def test_superuser_sees_newly_created_organization(self):
        admin = User.objects.create_superuser("0711000101", "admin-pass-1234")
        auth = self._token_for(admin)

        created = self.client.post(
            "/api/v1/organizations/",
            {"name": "New Carrier", "slug": "new-carrier"},
            format="json",
            HTTP_AUTHORIZATION=auth,
        )
        self.assertEqual(created.status_code, 201, created.content)

        listed = self.client.get("/api/v1/organizations/", HTTP_AUTHORIZATION=auth)
        self.assertEqual(listed.status_code, 200, listed.content)
        payload = listed.json()
        rows = payload["results"] if isinstance(payload, dict) else payload
        self.assertIn(created.json()["id"], {row["id"] for row in rows})

    def test_platform_admin_sees_newly_created_organization(self):
        home = Organization.objects.create(name="Platform Home", slug="platform-home")
        admin = User.objects.create_user("0711000102", "admin-pass-1234")
        Membership.objects.create(
            user=admin, organization=home, role=Role.PLATFORM_ADMIN
        )
        auth = self._token_for(admin)

        created = self.client.post(
            "/api/v1/organizations/",
            {"name": "Partner Carrier", "slug": "partner-carrier"},
            format="json",
            HTTP_AUTHORIZATION=auth,
        )
        self.assertEqual(created.status_code, 201, created.content)

        listed = self.client.get("/api/v1/organizations/", HTTP_AUTHORIZATION=auth)
        self.assertEqual(listed.status_code, 200, listed.content)
        payload = listed.json()
        rows = payload["results"] if isinstance(payload, dict) else payload
        self.assertIn(created.json()["id"], {row["id"] for row in rows})


class OrganizationOnboardingTestCase(APITestCase):
    """Platform administrators drive onboarding: approve an organization and
    hire its first operator admin, even though they hold no membership there."""

    def setUp(self):
        self.home = Organization.objects.create(name="Platform", slug="platform-home-2")
        self.admin = User.objects.create_user("0711000201", "admin-pass-1234")
        Membership.objects.create(
            user=self.admin, organization=self.home, role=Role.PLATFORM_ADMIN
        )
        from rest_framework_simplejwt.tokens import RefreshToken

        self.auth = f"Bearer {RefreshToken.for_user(self.admin).access_token}"

        self.org = Organization.objects.create(
            name="Onboarded Carrier", slug="onboarded-carrier"
        )

    def test_platform_admin_approves_organization(self):
        response = self.client.post(
            f"/api/v1/organizations/{self.org.pk}/approve/",
            HTTP_AUTHORIZATION=self.auth,
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["approval_status"], "approved")

    def test_operator_admin_cannot_approve_organization(self):
        operator = User.objects.create_user("0711000202", "operator-pass-1234")
        Membership.objects.create(
            user=operator, organization=self.org, role=Role.OPERATOR_ADMIN
        )
        from rest_framework_simplejwt.tokens import RefreshToken

        auth = f"Bearer {RefreshToken.for_user(operator).access_token}"
        response = self.client.post(
            f"/api/v1/organizations/{self.org.pk}/approve/", HTTP_AUTHORIZATION=auth
        )
        self.assertEqual(response.status_code, 403)

    def test_platform_admin_hires_operator_admin_into_new_org(self):
        response = self.client.post(
            "/api/v1/staff/",
            {
                "organization": str(self.org.pk),
                "phone": "0711000203",
                "full_name": "New Ops Lead",
                "password": "lead-pass-1234",
                "role": "operator_admin",
            },
            format="json",
            HTTP_AUTHORIZATION=self.auth,
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["role"], "operator_admin")
        self.assertEqual(response.json()["organization"], str(self.org.pk))

    def test_platform_admin_lists_staff_of_new_org(self):
        response = self.client.get(
            f"/api/v1/staff/?organization={self.org.pk}",
            HTTP_AUTHORIZATION=self.auth,
        )
        self.assertEqual(response.status_code, 200, response.content)
