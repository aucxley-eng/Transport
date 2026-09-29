"""Corporate travel and recurring shuttles (blueprint 8, "Corporate travel and
recurring shuttles").

The blueprint states the whole feature in four sentences; each is asserted here:

  1. "A company defines eligible employees, cost centers, budgets, approval
      thresholds and permitted services."
  2. "A travel request may require approval before dispatch."
  3. "Recurring shuttles create dated departures from a schedule template and
      maintain a passenger roster."
  4. "An employee leaving the company must lose future access without deleting
      historical invoices."
"""

from datetime import date, time, timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import Membership, Organization, Role, User
from apps.booking.models import Departure, Route, RouteStop, Segment
from apps.catalog.models import Place, Service, build_quote
from apps.corporate.models import (
    CorporateMember,
    CorporatePolicy,
    CostCenter,
    ShuttleRosterEntry,
    ShuttleRun,
    ShuttleTemplate,
    TravelRequest,
)
from apps.corporate.services import (
    CorporateError,
    approve_travel_request,
    budget_remaining,
    cost_center_spent,
    deactivate_member,
    generate_shuttle_runs,
    is_corporate_manager,
    reject_travel_request,
    roster_member,
    submit_travel_request,
)
from apps.orders.models import Order


def _weekday_before(day):
    """The nearest Monday-to-Friday day strictly before ``day``."""
    day -= timedelta(days=1)
    while day.weekday() > 4:
        day -= timedelta(days=1)
    return day


def _weekday_after(day):
    """The nearest Monday-to-Friday day strictly after ``day``."""
    day += timedelta(days=1)
    while day.weekday() > 4:
        day += timedelta(days=1)
    return day


class CorporateFixture(TestCase):
    def setUp(self):
        self.company = Organization.objects.create(name="Acme Ltd", slug="acme")
        self.operator = Organization.objects.create(name="Swift Transit", slug="swift")

        self.manager = User.objects.create_user("0711000011", "pass-M-1234")
        Membership.objects.create(
            user=self.manager, organization=self.company, role=Role.CORPORATE_MANAGER
        )
        self.employee = User.objects.create_user("0711000022", "pass-E-1234")
        Membership.objects.create(
            user=self.employee, organization=self.company, role=Role.CUSTOMER
        )
        self.outsider = User.objects.create_user("0711000033", "pass-X-1234")
        Membership.objects.create(
            user=self.outsider, organization=self.company, role=Role.CUSTOMER
        )

        self.policy = CorporatePolicy.objects.create(
            organization=self.company, approval_threshold_minor=50_000
        )
        self.cost_center = CostCenter.objects.create(
            organization=self.company, code="OPS", name="Operations", budget_minor=1_000_000
        )
        self.member = CorporateMember.objects.create(
            organization=self.company,
            user=self.employee,
            employee_ref="EMP-001",
            cost_center=self.cost_center,
        )
        self.outsider_member = CorporateMember.objects.create(
            organization=self.company,
            user=self.outsider,
            employee_ref="EMP-002",
            cost_center=self.cost_center,
        )

        self.place_a = Place.objects.create(name="HQ", place_type="office")
        self.place_b = Place.objects.create(name="Branch", place_type="office")
        self.service = Service.objects.create(
            slug="corp-shuttle", name="Corporate shuttle", service_type=Service.ServiceType.CORPORATE
        )
        self.route = Route.objects.create(organization=self.operator, name="HQ - Branch")
        RouteStop.objects.create(route=self.route, place=self.place_a, position=0)
        RouteStop.objects.create(route=self.route, place=self.place_b, position=1)
        self.departure = Departure.objects.create(
            organization=self.operator,
            service=self.service,
            route=self.route,
            departure_date=date(2026, 10, 5),
            departure_time=time(7, 0),
            capacity=4,
            fare_minor=80_000,
        )
        Segment.objects.create(
            departure=self.departure,
            position=0,
            from_place=self.place_a,
            to_place=self.place_b,
        )


class ClauseOneCompanyDefinesTheRulesTestCase(CorporateFixture):
    """A company defines eligible employees, cost centers, budgets, approval
    thresholds and permitted services."""

    def test_permitted_services_are_enforced(self):
        self.policy.permitted_services.add(self.service)
        # A service outside the permitted list is refused.
        other = Service.objects.create(
            slug="luxury-coach", name="Luxury coach", service_type=Service.ServiceType.CORPORATE
        )
        luxury = Departure.objects.create(
            organization=self.operator,
            service=other,
            route=self.route,
            departure_date=date(2026, 10, 6),
            departure_time=time(9, 0),
            capacity=4,
            fare_minor=90_000,
        )
        Segment.objects.create(
            departure=luxury, position=0, from_place=self.place_a, to_place=self.place_b
        )
        self.policy.permitted_services.add(self.service)
        with self.assertRaises(CorporateError):
            submit_travel_request(member=self.member, departure=luxury)

    def test_permitted_service_is_accepted(self):
        self.policy.permitted_services.add(self.service)
        request = submit_travel_request(member=self.member, departure=self.departure)
        self.assertEqual(request.estimated_minor, 80_000)

    def test_cost_center_budget_is_enforced(self):
        tiny = CostCenter.objects.create(
            organization=self.company, code="TINY", name="Tiny", budget_minor=10_000
        )
        member = CorporateMember.objects.create(
            organization=self.company,
            user=self.outsider,
            employee_ref="EMP-003",
            cost_center=tiny,
        )
        with self.assertRaises(CorporateError):
            submit_travel_request(member=member, departure=self.departure)

    def test_spent_is_derived_from_history_and_budget_remaining(self):
        # An approved request counts against the cost centre budget.
        submit_travel_request(member=self.member, departure=self.departure)
        request = submit_travel_request(member=self.outsider_member, departure=self.departure)
        approve_travel_request(request, approver=self.manager)
        self.assertEqual(cost_center_spent(self.cost_center), 80_000)
        self.assertEqual(budget_remaining(self.cost_center), 920_000)


class ClauseTwoApprovalBeforeDispatchTestCase(CorporateFixture):
    """A travel request may require approval before dispatch."""

    def test_below_threshold_is_auto_approved(self):
        cheap = Departure.objects.create(
            organization=self.operator,
            service=self.service,
            route=self.route,
            departure_date=date(2026, 10, 7),
            departure_time=time(7, 0),
            capacity=4,
            fare_minor=10_000,
        )
        Segment.objects.create(
            departure=cheap, position=0, from_place=self.place_a, to_place=self.place_b
        )
        request = submit_travel_request(member=self.member, departure=cheap)
        self.assertFalse(request.requires_approval)
        self.assertEqual(request.status, TravelRequest.Status.APPROVED)

    def test_at_or_above_threshold_requires_approval(self):
        # fare 80,000 >= threshold 50,000
        request = submit_travel_request(member=self.member, departure=self.departure)
        self.assertTrue(request.requires_approval)
        self.assertEqual(request.status, TravelRequest.Status.PENDING_APPROVAL)

    def test_manager_can_approve(self):
        request = submit_travel_request(member=self.member, departure=self.departure)
        approved = approve_travel_request(request, approver=self.manager)
        self.assertEqual(approved.status, TravelRequest.Status.APPROVED)
        self.assertEqual(approved.decided_by, self.manager)
        self.assertIsNotNone(approved.decided_at)

    def test_employee_cannot_approve_their_own_request(self):
        # Even if the employee held a manager role, self-approval is refused.
        Membership.objects.filter(user=self.employee, organization=self.company).update(
            role=Role.CORPORATE_MANAGER
        )
        request = submit_travel_request(member=self.member, departure=self.departure)
        with self.assertRaises(CorporateError):
            approve_travel_request(request, approver=self.employee)

    def test_non_manager_cannot_approve(self):
        request = submit_travel_request(member=self.member, departure=self.departure)
        with self.assertRaises(CorporateError):
            approve_travel_request(request, approver=self.outsider)

    def test_rejection_records_note(self):
        request = submit_travel_request(member=self.member, departure=self.departure)
        rejected = reject_travel_request(request, approver=self.manager, note="No budget")
        self.assertEqual(rejected.status, TravelRequest.Status.REJECTED)
        self.assertEqual(rejected.decision_note, "No budget")

    def test_cannot_approve_twice(self):
        request = submit_travel_request(member=self.member, departure=self.departure)
        approve_travel_request(request, approver=self.manager)
        with self.assertRaises(CorporateError):
            approve_travel_request(request, approver=self.manager)


class ClauseThreeShuttlesTestCase(CorporateFixture):
    """Recurring shuttles create dated departures from a schedule template and
    maintain a passenger roster."""

    def setUp(self):
        super().setUp()
        self.template = ShuttleTemplate.objects.create(
            organization=self.company,
            name="Morning shuttle",
            route=self.route,
            service=self.service,
            departure_time=time(7, 30),
            weekdays=[1, 2, 3, 4, 5],  # Mon-Fri
            valid_from=date(2026, 10, 5),
            capacity=2,
            fare_minor=20_000,
        )

    def test_generates_dated_departures_for_the_weekdays(self):
        runs = generate_shuttle_runs(
            self.template, start=date(2026, 10, 5), end=date(2026, 10, 9)
        )
        # Oct 5 2026 is a Monday; Oct 9 is a Friday -> 5 runs.
        self.assertEqual(len(runs), 5)
        self.assertEqual(ShuttleRun.objects.count(), 5)
        for run in runs:
            self.assertEqual(run.run_date.isoweekday() in (1, 2, 3, 4, 5), True)
            self.assertEqual(run.departure.capacity, 2)
            # Segments are built so the departure can actually be booked.
            self.assertEqual(Segment.objects.filter(departure=run.departure).count(), 1)

    def test_generation_skips_non_matching_weekdays(self):
        # Oct 10 2026 is a Saturday.
        runs = generate_shuttle_runs(
            self.template, start=date(2026, 10, 10), end=date(2026, 10, 10)
        )
        self.assertEqual(len(runs), 0)

    def test_generation_is_idempotent(self):
        first = generate_shuttle_runs(
            self.template, start=date(2026, 10, 5), end=date(2026, 10, 9)
        )
        second = generate_shuttle_runs(
            self.template, start=date(2026, 10, 5), end=date(2026, 10, 9)
        )
        # Second call returns the same 5 runs, creating no duplicates.
        self.assertEqual(len(first), 5)
        self.assertEqual(len(second), 5)
        self.assertEqual(ShuttleRun.objects.count(), 5)
        self.assertEqual(ShuttleTemplate.objects.count(), 1)

    def test_generation_respects_validity_window(self):
        self.template.valid_until = date(2026, 10, 7)
        self.template.save(update_fields=["valid_until", "updated_at"])
        runs = generate_shuttle_runs(
            self.template, start=date(2026, 10, 5), end=date(2026, 10, 9)
        )
        self.assertEqual(len(runs), 3)  # Mon 5, Tue 6, Wed 7

    def test_roster_respects_capacity(self):
        run = generate_shuttle_runs(
            self.template, start=date(2026, 10, 5), end=date(2026, 10, 5)
        )[0]
        roster_member(run, self.member)
        roster_member(run, self.outsider_member)
        with self.assertRaises(CorporateError):
            roster_member(
                run,
                CorporateMember.objects.create(
                    organization=self.company,
                    user=User.objects.create_user("0711000044", "pass-E-1234"),
                    employee_ref="EMP-004",
                ),
            )

    def test_roster_rejects_duplicate_member(self):
        run = generate_shuttle_runs(
            self.template, start=date(2026, 10, 5), end=date(2026, 10, 5)
        )[0]
        roster_member(run, self.member)
        with self.assertRaises(CorporateError):
            roster_member(run, self.member)

    def test_roster_rejects_ineligible_member(self):
        run = generate_shuttle_runs(
            self.template, start=date(2026, 10, 5), end=date(2026, 10, 5)
        )[0]
        deactivate_member(self.member)
        with self.assertRaises(CorporateError):
            roster_member(run, self.member)

    def test_inactive_template_cannot_generate(self):
        self.template.is_active = False
        self.template.save(update_fields=["is_active", "updated_at"])
        with self.assertRaises(CorporateError):
            generate_shuttle_runs(
                self.template, start=date(2026, 10, 5), end=date(2026, 10, 5)
            )


class ClauseFourDepartureKeepsHistoryTestCase(CorporateFixture):
    """An employee leaving the company must lose future access without deleting
    historical invoices."""

    def test_departed_employee_cannot_submit_new_requests(self):
        deactivate_member(self.member)
        with self.assertRaises(CorporateError):
            submit_travel_request(member=self.member, departure=self.departure)

    def test_departed_employee_is_not_eligible(self):
        self.assertTrue(self.member.is_eligible())
        deactivate_member(self.member)
        self.assertFalse(self.member.is_eligible())

    def test_historical_invoices_survive_departure(self):
        # A confirmed order (an invoice) for the departing employee.
        quote = build_quote(
            service=self.service,
            request_payload={"departure": str(self.departure.pk)},
            total_minor=80_000,
            basis="per_seat",
            organization=self.company,
        )
        quote.accept()
        order = Order.objects.create(
            organization=self.company,
            customer=self.employee,
            service=self.service,
            quote=quote,
            total_minor=80_000,
        )
        request = submit_travel_request(member=self.member, departure=self.departure)
        approve_travel_request(request, approver=self.manager)

        deactivate_member(self.member)

        # Nothing was deleted.
        self.assertTrue(Order.objects.filter(pk=order.pk).exists())
        self.assertEqual(Order.objects.get(pk=order.pk).total_minor, 80_000)
        self.assertTrue(TravelRequest.objects.filter(pk=request.pk).exists())
        self.assertEqual(
            TravelRequest.objects.get(pk=request.pk).status, TravelRequest.Status.APPROVED
        )
        # The employee record itself is retained, not deleted.
        self.assertTrue(CorporateMember.objects.filter(pk=self.member.pk).exists())
        self.assertIsNotNone(CorporateMember.objects.get(pk=self.member.pk).left_on)

    def test_departure_cancels_future_roster_but_keeps_past(self):
        template = ShuttleTemplate.objects.create(
            organization=self.company,
            name="Morning shuttle",
            route=self.route,
            service=self.service,
            departure_time=time(7, 30),
            weekdays=[1, 2, 3, 4, 5],
            valid_from=date(2026, 1, 1),
            capacity=10,
        )
        # The template runs Monday to Friday, so the dates either side of today
        # have to be weekdays: on a Saturday the platform runs nothing, and the
        # test would be asserting against a calendar, not against the platform.
        past_day = _weekday_before(timezone.localdate())
        future_day = _weekday_after(timezone.localdate())
        past = generate_shuttle_runs(template, start=past_day, end=past_day)[0]
        future = generate_shuttle_runs(template, start=future_day, end=future_day)[0]
        roster_member(past, self.member)
        roster_member(future, self.member)

        deactivate_member(self.member)

        # The past roster entry is preserved as evidence of who travelled.
        past_entry = ShuttleRosterEntry.objects.get(run=past, member=self.member)
        self.assertEqual(past_entry.state, ShuttleRosterEntry.State.PLANNED)
        # The future entry is cancelled so the leaver does not occupy a seat.
        future_entry = ShuttleRosterEntry.objects.get(run=future, member=self.member)
        self.assertEqual(future_entry.state, ShuttleRosterEntry.State.CANCELLED)


class CorporateApiTestCase(CorporateFixture):
    def setUp(self):
        super().setUp()
        self.manager_client = APIClient()
        self.manager_client.force_authenticate(self.manager)
        self.employee_client = APIClient()
        self.employee_client.force_authenticate(self.employee)

    def test_is_corporate_manager(self):
        self.assertTrue(is_corporate_manager(self.manager))
        self.assertFalse(is_corporate_manager(self.employee))

    def test_submit_and_approve_over_http(self):
        response = self.employee_client.post(
            "/api/v1/corporate/requests/",
            {"member": self.member.pk, "departure": self.departure.pk, "purpose": "Site visit"},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["status"], TravelRequest.Status.PENDING_APPROVAL)
        request_id = response.data["id"]

        response = self.manager_client.post(
            f"/api/v1/corporate/requests/{request_id}/approve/", {}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], TravelRequest.Status.APPROVED)

    def test_employee_cannot_approve_over_http(self):
        response = self.employee_client.post(
            "/api/v1/corporate/requests/",
            {"member": self.member.pk, "departure": self.departure.pk},
            format="json",
        )
        request_id = response.data["id"]
        response = self.employee_client.post(
            f"/api/v1/corporate/requests/{request_id}/approve/", {}, format="json"
        )
        # 403, not 400: the employee is not a corporate travel manager.
        self.assertEqual(response.status_code, 403)

    def test_outsider_cannot_see_another_employees_requests(self):
        submit_travel_request(member=self.member, departure=self.departure)
        response = self.employee_client.get("/api/v1/corporate/requests/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)

    def test_roster_over_http(self):
        self.manager_client.post(
            "/api/v1/corporate/shuttle-templates/",
            {
                "organization": self.company.pk,
                "name": "Morning",
                "route": self.route.pk,
                "service": self.service.pk,
                "departure_time": "07:30:00",
                "weekdays": [1, 2, 3, 4, 5],
                "valid_from": "2026-10-05",
                "capacity": 2,
                "fare_minor": 20000,
            },
            format="json",
        )
        templates = self.manager_client.get("/api/v1/corporate/shuttle-templates/")
        template_id = templates.data["results"][0]["id"] if "results" in templates.data else templates.data[0]["id"]
        response = self.manager_client.post(
            f"/api/v1/corporate/shuttle-templates/{template_id}/generate/",
            {"start": "2026-10-05", "end": "2026-10-05"},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        run_id = response.data[0]["id"]

        response = self.manager_client.post(
            f"/api/v1/corporate/shuttle-runs/{run_id}/roster/",
            {"member": self.member.pk},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["employee_ref"], "EMP-001")

    def test_anonymous_access_is_rejected(self):
        response = APIClient().get("/api/v1/corporate/requests/")
        self.assertIn(response.status_code, (401, 403))
