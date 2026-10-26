from rest_framework.test import APIClient

from apps.accounts.models import Branch, Membership, Role, User
from apps.booking.models import Ticket
from apps.booking.services import acquire_hold, create_booking_order
from apps.booking.tests.test_booking import BookingFixture


class BranchTicketVisibilityTestCase(BookingFixture):
    def setUp(self):
        super().setUp()
        self.selling = Branch.objects.create(
            organization=self.org, name="Nairobi Sales", code="NBO", is_head_office=True
        )
        self.operating = Branch.objects.create(
            organization=self.org, name="Nakuru Depot", code="NKR"
        )
        self.departure.branch = self.operating
        self.departure.save(update_fields=["branch"])

        self.agent = User.objects.create_user("0713000033", "pass-S-1234")
        Membership.objects.create(
            user=self.agent,
            organization=self.org,
            role=Role.BRANCH_STAFF,
            branch=self.selling,
        )
        self.ops_agent = User.objects.create_user("0713000044", "pass-O-1234")
        Membership.objects.create(
            user=self.ops_agent,
            organization=self.org,
            role=Role.BRANCH_STAFF,
            branch=self.operating,
        )
        self.agent_token = self._token(self.agent)
        self.ops_token = self._token(self.ops_agent)
        self.admin_token = self._token(self.admin)

    @staticmethod
    def _token(user):
        from rest_framework_simplejwt.tokens import RefreshToken

        return f"Bearer {RefreshToken.for_user(user).access_token}"

    def _sold_ticket(self, branch, seat, reference):
        return Ticket.objects.create(
            departure=self.departure,
            selling_branch=branch,
            reference=reference,
            token=f"token-{reference}",
            seat_number=seat,
            passenger_name=f"Passenger {seat}",
            passenger_phone="+254712000099",
        )

    def test_seller_sees_only_their_branch_sales(self):
        self._sold_ticket(self.selling, 1, "SALE-NBO")
        self._sold_ticket(self.operating, 2, "SALE-NKR")

        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=self.agent_token)
        response = client.get("/api/v1/tickets/")
        self.assertEqual(response.status_code, 200)
        references = {row["reference"] for row in response.json()["results"]}
        self.assertEqual(references, {"SALE-NBO"})

    def test_operations_view_includes_cross_branch_sales_at_the_departure(self):
        self._sold_ticket(self.selling, 1, "SALE-NBO")
        self._sold_ticket(self.operating, 2, "SALE-NKR")

        selling_client = APIClient()
        selling_client.credentials(HTTP_AUTHORIZATION=self.agent_token)
        selling_view = selling_client.get("/api/v1/tickets/?view=operations")
        self.assertEqual(selling_view.status_code, 200)
        self.assertEqual(selling_view.json()["results"], [])

        operations_client = APIClient()
        operations_client.credentials(HTTP_AUTHORIZATION=self.ops_token)
        response = operations_client.get("/api/v1/tickets/?view=operations")
        self.assertEqual(response.status_code, 200)
        references = {row["reference"] for row in response.json()["results"]}
        self.assertEqual(references, {"SALE-NBO", "SALE-NKR"})

    def test_customer_still_sees_their_own_ticket(self):
        from apps.payments.services import record_cash_collection

        holds = acquire_hold(
            departure=self.departure,
            from_place=self.nairobi,
            to_place=self.eldoret,
            seats=1,
            customer=self.customer,
        )
        order = create_booking_order(
            departure=self.departure,
            holds=holds,
            passenger_name="Own Ticket",
            passenger_phone="0712000099",
            customer=self.customer,
            selling_branch=self.selling,
        )
        record_cash_collection(order=order, actor=self.admin)

        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=self._token(self.customer))
        response = client.get("/api/v1/tickets/")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["count"], 1)
        self.assertEqual(response.json()["results"][0]["passenger_name"], "Own Ticket")

    def test_org_admin_sees_every_ticket(self):
        self._sold_ticket(self.selling, 1, "SALE-NBO")
        self._sold_ticket(self.operating, 2, "SALE-NKR")

        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=self.admin_token)
        response = client.get("/api/v1/tickets/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 2)

    def test_booking_records_selling_branch_and_document_names_both(self):
        from apps.payments.services import record_cash_collection

        holds = acquire_hold(
            departure=self.departure,
            from_place=self.nairobi,
            to_place=self.eldoret,
            seats=1,
            customer=self.customer,
        )
        order = create_booking_order(
            departure=self.departure,
            holds=holds,
            passenger_name="Cross Branch",
            passenger_phone="0712000099",
            customer=self.customer,
            selling_branch=self.selling,
        )
        record_cash_collection(order=order, actor=self.admin)
        ticket = Ticket.objects.get(hold__order=order)
        self.assertEqual(ticket.selling_branch_id, self.selling.pk)
        self.assertEqual(order.branch_id, self.selling.pk)

        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=self.admin_token)
        response = client.get(f"/api/v1/tickets/{ticket.pk}/")
        self.assertEqual(response.status_code, 200, response.content)
        document = response.json()["document"]
        self.assertEqual(document["selling_branch"]["name"], "Nairobi Sales")
        self.assertEqual(document["operating_branch"]["name"], "Nakuru Depot")
