from rest_framework.test import APITestCase

from apps.accounts.models import Branch, Membership, Organization, Role, User


class BranchApiTestCase(APITestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Alpha Sacco", slug="alpha")
        self.other = Organization.objects.create(name="Beta Sacco", slug="beta")
        self.admin = User.objects.create_user("0722000001", "admin-pass-1")
        Membership.objects.create(
            user=self.admin, organization=self.org, role=Role.OPERATOR_ADMIN
        )
        self.other_admin = User.objects.create_user("0722000002", "admin-pass-1")
        Membership.objects.create(
            user=self.other_admin, organization=self.other, role=Role.OPERATOR_ADMIN
        )
        self.manager = User.objects.create_user("0722000003", "manager-pass-1")
        self.head = Branch.objects.create(
            organization=self.org, name="Nairobi HQ", code="NBO", is_head_office=True
        )
        Membership.objects.create(
            user=self.manager,
            organization=self.org,
            role=Role.BRANCH_MANAGER,
            branch=self.head,
        )
        self.headers = self._headers(self.admin)

    @staticmethod
    def _headers(user):
        from rest_framework_simplejwt.tokens import RefreshToken

        return {"HTTP_AUTHORIZATION": f"Bearer {RefreshToken.for_user(user).access_token}"}

    def test_admin_creates_and_lists_branches_in_own_org_only(self):
        response = self.client.post(
            "/api/v1/branches/",
            {
                "organization": str(self.org.pk),
                "name": "Kisumu",
                "code": "KSM",
                "address": "Oginga Odinga Street",
            },
            format="json",
            **self.headers,
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["organization"], self.org.pk)

        listing = self.client.get("/api/v1/branches/", **self.headers)
        self.assertEqual(listing.status_code, 200)
        self.assertEqual({row["id"] for row in listing.data}, {
            str(self.head.pk),
            response.data["id"],
        })

        foreign = self.client.get(
            f"/api/v1/branches/?organization={self.other.pk}", **self.headers
        )
        self.assertEqual(foreign.status_code, 200)
        self.assertEqual(foreign.data, [])

    def test_foreign_admin_cannot_create_in_another_org(self):
        response = self.client.post(
            "/api/v1/branches/",
            {"organization": str(self.org.pk), "name": "Sneaky", "code": "SNK"},
            format="json",
            **self._headers(self.other_admin),
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Branch.objects.filter(name="Sneaky").exists())

    def test_head_office_cannot_be_removed_while_another_branch_is_active(self):
        second = Branch.objects.create(
            organization=self.org, name="Nakuru", code="NKR"
        )
        response = self.client.patch(
            f"/api/v1/branches/{self.head.pk}/",
            {"is_head_office": False},
            format="json",
            **self.headers,
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("is_head_office", response.data)

        promoted = self.client.patch(
            f"/api/v1/branches/{second.pk}/",
            {"is_head_office": True},
            format="json",
            **self.headers,
        )
        self.assertEqual(promoted.status_code, 200, promoted.data)
        self.head.refresh_from_db()
        self.assertFalse(self.head.is_head_office)

    def test_exactly_one_head_office_when_promoting(self):
        second = Branch.objects.create(
            organization=self.org, name="Nakuru", code="NKR"
        )
        response = self.client.patch(
            f"/api/v1/branches/{second.pk}/",
            {"is_head_office": True},
            format="json",
            **self.headers,
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            Branch.objects.filter(organization=self.org, is_head_office=True).count(), 1
        )
        second.refresh_from_db()
        self.assertTrue(second.is_head_office)

    def test_branch_manager_can_hire_counter_staff_at_their_branch(self):
        response = self.client.post(
            "/api/v1/staff/",
            {
                "organization": str(self.org.pk),
                "phone": "0722000010",
                "full_name": "Branch Clerk",
                "password": "clerk-pass-1",
                "role": Role.BRANCH_STAFF,
                "branch": str(self.head.pk),
            },
            format="json",
            **self._headers(self.manager),
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.data["branch"], self.head.pk)
        self.assertEqual(response.data["branch_name"], "Nairobi HQ")

    def test_branch_manager_cannot_hire_at_another_branch(self):
        other = Branch.objects.create(
            organization=self.org, name="Nakuru", code="NKR"
        )
        response = self.client.post(
            "/api/v1/staff/",
            {
                "organization": str(self.org.pk),
                "phone": "0722000011",
                "full_name": "Wrong Branch Clerk",
                "password": "clerk-pass-1",
                "role": Role.BRANCH_STAFF,
                "branch": str(other.pk),
            },
            format="json",
            **self._headers(self.manager),
        )
        self.assertEqual(response.status_code, 403)

    def test_branch_manager_can_read_but_not_create(self):
        headers = self._headers(self.manager)
        listing = self.client.get("/api/v1/branches/", **headers)
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(len(listing.data), 1)

        response = self.client.post(
            "/api/v1/branches/",
            {"organization": str(self.org.pk), "name": "Mombasa"},
            format="json",
            **headers,
        )
        self.assertEqual(response.status_code, 403)
