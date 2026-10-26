"""Staff hiring and directory access.

A real operator onboards counter clerks, drivers and managers long before they
have a customer app. These tests pin the rules: an operator administrator
manages their own organisation, a branch manager manages only their own
workplace, identities are not duplicated, and offboarding is soft.
"""

from datetime import date

from rest_framework.test import APIClient, APITestCase

from apps.accounts.models import Membership, Organization, Role, User
from apps.catalog.models import Place


class StaffTestCase(APITestCase):
    def setUp(self):
        self.org_a = Organization.objects.create(name="Alpha Sacco", slug="alpha")
        self.org_b = Organization.objects.create(name="Beta Sacco", slug="beta")
        self.branch_a = Place.objects.create(
            organization=self.org_a, name="Nairobi CBD", place_type=Place.PlaceType.BRANCH
        )
        self.branch_a2 = Place.objects.create(
            organization=self.org_a, name="Nakuru", place_type=Place.PlaceType.BRANCH
        )
        self.branch_b = Place.objects.create(
            organization=self.org_b, name="Mombasa", place_type=Place.PlaceType.BRANCH
        )

        self.admin = User.objects.create_user("0711000101", "pass-A-1234", full_name="Ada Admin")
        Membership.objects.create(
            user=self.admin, organization=self.org_a, role=Role.OPERATOR_ADMIN
        )
        self.other_admin = User.objects.create_user("0711000102", "pass-B-1234")
        Membership.objects.create(
            user=self.other_admin, organization=self.org_b, role=Role.OPERATOR_ADMIN
        )
        self.manager = User.objects.create_user("0711000103", "pass-M-1234")
        Membership.objects.create(
            user=self.manager,
            organization=self.org_a,
            role=Role.BRANCH_MANAGER,
            workplace=self.branch_a,
        )

        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def hire(self, client=None, **overrides):
        payload = {
            "organization": str(self.org_a.pk),
            "phone": "0711000200",
            "full_name": "New Clerk",
            "password": "clerk-pass-1",
            "role": Role.BRANCH_STAFF,
            "workplace": str(self.branch_a.pk),
            "job_title": "Counter clerk",
            "employee_number": "NKT-014",
            "hired_at": "2026-10-01",
        }
        payload.update(overrides)
        return (client or self.client).post("/api/v1/staff/", payload, format="json")

    def test_operator_admin_hires_new_staff(self):
        response = self.hire()
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual(body["phone"], "+254711000200")
        self.assertEqual(body["role"], Role.BRANCH_STAFF)
        self.assertEqual(body["workplace_name"], "Nairobi CBD")
        self.assertEqual(body["employee_number"], "NKT-014")
        self.assertEqual(body["hired_at"], "2026-10-01")

        member = Membership.objects.get(pk=body["id"])
        self.assertEqual(member.user.full_name, "New Clerk")
        self.assertEqual(member.user.check_password("clerk-pass-1"), True)

    def test_existing_phone_is_attached_not_duplicated(self):
        existing = User.objects.create_user(
            "0711000200", "existing-pass-1", full_name="Existing Person"
        )
        response = self.hire(password="")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(User.objects.filter(phone="+254711000200").count(), 1)
        self.assertEqual(response.json()["user_id"], str(existing.pk))
        self.assertEqual(response.json()["full_name"], "Existing Person")

    def test_new_user_requires_password(self):
        response = self.hire(password="")
        self.assertEqual(response.status_code, 400)
        self.assertIn("password", response.json())

    def test_duplicate_role_is_rejected(self):
        self.hire()
        response = self.hire(full_name="Second")
        self.assertEqual(response.status_code, 400)
        self.assertIn("role", response.json())

    def test_cannot_hire_for_another_organization(self):
        response = self.hire(
            organization=str(self.org_b.pk), workplace=str(self.branch_b.pk)
        )
        self.assertEqual(response.status_code, 403)

    def test_branch_manager_can_hire_counter_staff_in_their_branch(self):
        client = APIClient()
        client.force_authenticate(self.manager)
        response = self.hire(client=client, full_name="Branch Hire")
        self.assertEqual(response.status_code, 201, response.content)

    def test_branch_manager_cannot_hire_into_another_branch(self):
        client = APIClient()
        client.force_authenticate(self.manager)
        response = self.hire(
            client=client, workplace=str(self.branch_a2.pk), phone="0711000201"
        )
        self.assertEqual(response.status_code, 403)

    def test_branch_manager_cannot_hire_a_dispatcher(self):
        client = APIClient()
        client.force_authenticate(self.manager)
        response = self.hire(client=client, role=Role.DISPATCHER, phone="0711000202")
        self.assertEqual(response.status_code, 403)

    def test_list_is_scoped_to_the_managers_workplace(self):
        other = User.objects.create_user("0711000300", "pass-X-1234")
        Membership.objects.create(
            user=other, organization=self.org_a, role=Role.BRANCH_STAFF, workplace=self.branch_a2
        )
        self.hire()  # branch_a

        self.client.force_authenticate(self.manager)
        response = self.client.get(f"/api/v1/staff/?organization={self.org_a.pk}")
        self.assertEqual(response.status_code, 200)
        phones = {row["phone"] for row in response.json()}
        self.assertIn("+254711000200", phones)
        self.assertNotIn("+254711000300", phones)

    def test_operator_admin_sees_every_branch(self):
        self.hire()
        other = User.objects.create_user("0711000301", "pass-X-1234")
        Membership.objects.create(
            user=other, organization=self.org_a, role=Role.BRANCH_STAFF, workplace=self.branch_a2
        )
        response = self.client.get(f"/api/v1/staff/?organization={self.org_a.pk}")
        phones = {row["phone"] for row in response.json()}
        self.assertIn("+254711000200", phones)
        self.assertIn("+254711000301", phones)

    def test_offboarding_is_soft(self):
        hired = self.hire().json()
        response = self.client.delete(f"/api/v1/staff/{hired['id']}/")
        self.assertEqual(response.status_code, 204)
        member = Membership.objects.get(pk=hired["id"])
        self.assertFalse(member.is_active)
        self.assertEqual(member.ended_at, date.today())

    def test_customer_role_cannot_be_hired_as_staff(self):
        response = self.hire(role=Role.CUSTOMER)
        self.assertEqual(response.status_code, 400)
        self.assertIn("role", response.json())

    def test_unauthenticated_caller_is_rejected(self):
        anonymous = APIClient()
        response = self.hire(client=anonymous)
        self.assertEqual(response.status_code, 401)
