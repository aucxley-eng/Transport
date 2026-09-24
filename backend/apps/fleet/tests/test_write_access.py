"""Fleet write-access tests: organization write scoping (blueprint 3).
An operator admin may only create/update records within organizations they
administer, regardless of query strings or request bodies."""

from rest_framework.test import APITestCase

from apps.accounts.models import Membership, Organization, Role, User
from apps.fleet.models import Vehicle


class FleetWriteAccessTestCase(APITestCase):
    def setUp(self):
        self.org_a = Organization.objects.create(name="Alpha Sacco", slug="alpha-w")
        self.org_b = Organization.objects.create(name="Beta Sacco", slug="beta-w")
        self.admin_a = User.objects.create_user("0711000011", "pass-A-1234")
        Membership.objects.create(
            user=self.admin_a, organization=self.org_a, role=Role.OPERATOR_ADMIN
        )
        self.token = f"Bearer {self._token_for(self.admin_a)}"

    @staticmethod
    def _token_for(user):
        from rest_framework_simplejwt.tokens import RefreshToken

        return str(RefreshToken.for_user(user).access_token)

    def _vehicle_payload(self, organization, registration_number):
        return {
            "organization": str(organization.pk),
            "registration_number": registration_number,
            "vehicle_class": "shuttle",
            "seating_capacity": 14,
        }

    def test_operator_can_create_vehicle_in_own_org(self):
        response = self.client.post(
            "/api/v1/vehicles/",
            self._vehicle_payload(self.org_a, "KDA111A"),
            format="json",
            HTTP_AUTHORIZATION=self.token,
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["organization"], str(self.org_a.pk))

    def test_operator_cannot_create_vehicle_in_other_org(self):
        response = self.client.post(
            "/api/v1/vehicles/",
            self._vehicle_payload(self.org_b, "KDA112B"),
            format="json",
            HTTP_AUTHORIZATION=self.token,
        )
        self.assertEqual(response.status_code, 403)

    def test_body_org_wins_over_query_param(self):
        # Even with the caller's own org in the query string, a differnt org
        # in the body must be rejected (no cross-org spoofing).
        response = self.client.post(
            "/api/v1/vehicles/?organization=" + str(self.org_a.pk),
            self._vehicle_payload(self.org_b, "KDA113C"),
            format="json",
            HTTP_AUTHORIZATION=self.token,
        )
        self.assertEqual(response.status_code, 403)

    def test_update_cannot_move_vehicle_to_other_org(self):
        vehicle = Vehicle.objects.create(
            organization=self.org_a,
            registration_number="KDA114D",
            vehicle_class="shuttle",
            seating_capacity=14,
        )
        response = self.client.patch(
            f"/api/v1/vehicles/{vehicle.pk}/",
            {"organization": str(self.org_b.pk)},
            format="json",
            HTTP_AUTHORIZATION=self.token,
        )
        self.assertEqual(response.status_code, 403)

        vehicle.refresh_from_db()
        self.assertEqual(vehicle.organization_id, self.org_a.pk)

    def test_non_admin_member_cannot_create_vehicle(self):
        member = User.objects.create_user("0711000012", "pass-A-1234")
        Membership.objects.create(
            user=member, organization=self.org_a, role=Role.CUSTOMER
        )
        response = self.client.post(
            "/api/v1/vehicles/",
            self._vehicle_payload(self.org_a, "KDA115E"),
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {self._token_for(member)}",
        )
        self.assertEqual(response.status_code, 403)