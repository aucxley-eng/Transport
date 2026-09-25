"""Charter and vehicle hire (blueprint 8, "Charter and vehicle hire").

The blueprint states the feature in three sentences; each is asserted here:

  1. "A hire request specifies dates, itinerary, vehicle class, passenger count
      and included hours or distance."
  2. "Staff issue a quote, reserve the vehicle and driver for the entire
      interval, record deposits, and finalize permitted extras at completion."
  3. "Availability checks include travel to the pickup, turnaround time and
      existing reservations."
"""

from datetime import date, datetime, timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import Membership, Organization, Role, User
from apps.booking.models import Route, RouteStop
from apps.catalog.models import Place, Service
from apps.charter.models import (
    CharterDeposit,
    CharterExtra,
    CharterReservation,
    HireRequest,
)
from apps.charter.services import (
    CharterError,
    add_extra,
    blocked_window,
    cancel_hire,
    check_availability,
    confirm_hire,
    create_hire_request,
    finalize_hire,
    issue_quote,
    record_deposit,
)
from apps.fleet.models import Document, Driver, Vehicle


class CharterFixture(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Swift Transit", slug="swift")
        self.customer = User.objects.create_user("0711000011", "pass-C-1234")
        self.staff = User.objects.create_user("0711000022", "pass-S-1234")
        Membership.objects.create(
            user=self.staff, organization=self.org, role=Role.DISPATCHER
        )
        self.plain = User.objects.create_user("0711000033", "pass-P-1234")
        Membership.objects.create(user=self.plain, organization=self.org, role=Role.CUSTOMER)

        self.service = Service.objects.create(
            slug="charter-hire",
            name="Charter hire",
            service_type=Service.ServiceType.CHARTER,
        )
        self.place_a = Place.objects.create(name="Nairobi", place_type="office")
        self.place_b = Place.objects.create(name="Nakuru", place_type="office")
        self.route = Route.objects.create(organization=self.org, name="Nairobi - Nakuru")
        RouteStop.objects.create(route=self.route, place=self.place_a, position=0)
        RouteStop.objects.create(route=self.route, place=self.place_b, position=1)

        self.vehicle = Vehicle.objects.create(
            organization=self.org,
            registration_number="KDA900A",
            vehicle_class=Vehicle.VehicleClass.BUS,
            seating_capacity=40,
        )
        self.other_vehicle = Vehicle.objects.create(
            organization=self.org,
            registration_number="KDA900B",
            vehicle_class=Vehicle.VehicleClass.BUS,
            seating_capacity=40,
        )
        self.driver = self._make_driver("0711000044")
        self.other_driver = self._make_driver("0711000055")

        self.start = timezone.make_aware(datetime(2026, 11, 2, 10, 0))
        self.end = timezone.make_aware(datetime(2026, 11, 2, 14, 0))

    def _make_driver(self, phone):
        driver = Driver.objects.create(
            user=User.objects.create_user(phone, "pass-D-1234"),
            organization=self.org,
            status=Driver.Status.APPROVED,
        )
        Document.objects.create(
            organization=self.org,
            entity_type=Document.EntityType.DRIVER,
            entity_id=driver.pk,
            doc_type="psv_licence",
            status=Document.Status.VERIFIED,
            expires_on=date(2026, 12, 31),
        )
        return driver

    def _hire(self, **kwargs):
        return create_hire_request(
            organization=self.org,
            customer=self.customer,
            service=self.service,
            start_at=kwargs.get("start_at", self.start),
            end_at=kwargs.get("end_at", self.end),
            vehicle_class=kwargs.get("vehicle_class", Vehicle.VehicleClass.BUS),
            passenger_count=kwargs.get("passenger_count", 20),
            route=self.route,
            included_hours=kwargs.get("included_hours", 4),
        )

    def _quoted(self, **kwargs):
        hire = self._hire(**kwargs)
        issue_quote(hire, total_minor=120_000)
        return hire


class ClauseOneRequestDetailTestCase(CharterFixture):
    """A hire request specifies dates, itinerary, vehicle class, passenger count
    and included hours or distance."""

    def test_request_records_the_specified_detail(self):
        hire = self._hire()
        self.assertEqual(hire.start_at, self.start)
        self.assertEqual(hire.end_at, self.end)
        self.assertEqual(hire.route, self.route)
        self.assertEqual(hire.vehicle_class, Vehicle.VehicleClass.BUS)
        self.assertEqual(hire.passenger_count, 20)
        self.assertEqual(float(hire.included_hours), 4.0)
        self.assertEqual(hire.status, HireRequest.Status.DRAFT)

    def test_end_must_be_after_start(self):
        with self.assertRaises(CharterError):
            self._hire(end_at=self.start)

    def test_hours_or_distance_must_be_included(self):
        with self.assertRaises(CharterError):
            create_hire_request(
                organization=self.org,
                customer=self.customer,
                service=self.service,
                start_at=self.start,
                end_at=self.end,
                included_hours=None,
                included_distance_km=None,
            )

    def test_distance_only_hire_is_accepted(self):
        hire = create_hire_request(
            organization=self.org,
            customer=self.customer,
            service=self.service,
            start_at=self.start,
            end_at=self.end,
            included_distance_km=600,
        )
        self.assertEqual(hire.included_distance_km, 600)


class ClauseTwoQuoteReserveDepositFinalizeTestCase(CharterFixture):
    """Staff issue a quote, reserve the vehicle and driver for the entire
    interval, record deposits, and finalize permitted extras at completion."""

    def test_staff_issue_quote(self):
        hire = self._hire()
        quote = issue_quote(hire, total_minor=120_000)
        self.assertEqual(quote.status, quote.Status.ACCEPTED)
        self.assertEqual(quote.basis, "charter")
        hire.refresh_from_db()
        self.assertEqual(hire.status, HireRequest.Status.QUOTED)
        self.assertEqual(hire.quoted_total_minor, 120_000)

    def test_quote_must_be_positive(self):
        with self.assertRaises(CharterError):
            issue_quote(self._hire(), total_minor=0)

    def test_confirm_reserves_vehicle_and_driver_for_the_interval(self):
        hire = self._quoted()
        reservation = confirm_hire(hire, vehicle=self.vehicle, driver=self.driver)
        self.assertEqual(reservation.vehicle, self.vehicle)
        self.assertEqual(reservation.driver, self.driver)
        self.assertEqual(reservation.reserved_from, self.start)
        self.assertEqual(reservation.reserved_to, self.end)
        hire.refresh_from_db()
        self.assertEqual(hire.status, HireRequest.Status.CONFIRMED)

    def test_unquoted_hire_cannot_be_confirmed(self):
        with self.assertRaises(CharterError):
            confirm_hire(self._hire(), vehicle=self.vehicle, driver=self.driver)

    def test_vehicle_class_must_match(self):
        van = Vehicle.objects.create(
            organization=self.org,
            registration_number="KDA900C",
            vehicle_class=Vehicle.VehicleClass.VAN,
            seating_capacity=8,
        )
        with self.assertRaises(CharterError):
            confirm_hire(self._quoted(), vehicle=van, driver=self.driver)

    def test_vehicle_must_carry_the_passengers(self):
        small = Vehicle.objects.create(
            organization=self.org,
            registration_number="KDA900D",
            vehicle_class=Vehicle.VehicleClass.BUS,
            seating_capacity=10,
        )
        with self.assertRaises(CharterError):
            confirm_hire(self._quoted(passenger_count=20), vehicle=small, driver=self.driver)

    def test_blocked_vehicle_is_refused(self):
        self.vehicle.status = Vehicle.Status.BLOCKED
        self.vehicle.save(update_fields=["status", "updated_at"])
        with self.assertRaises(CharterError):
            confirm_hire(self._quoted(), vehicle=self.vehicle, driver=self.driver)

    def test_unapproved_driver_is_refused(self):
        self.driver.status = Driver.Status.SUSPENDED
        self.driver.save(update_fields=["status", "updated_at"])
        with self.assertRaises(CharterError):
            confirm_hire(self._quoted(), vehicle=self.vehicle, driver=self.driver)

    def test_deposits_are_recorded_and_totalled(self):
        hire = self._quoted()
        confirm_hire(hire, vehicle=self.vehicle, driver=self.driver)
        record_deposit(hire, amount_minor=50_000, recorded_by=self.staff)
        record_deposit(hire, amount_minor=20_000, recorded_by=self.staff)
        hire.refresh_from_db()
        self.assertEqual(CharterDeposit.objects.filter(hire_request=hire).count(), 2)
        self.assertEqual(hire.deposit_total_minor, 70_000)

    def test_deposit_requires_a_confirmed_hire(self):
        with self.assertRaises(CharterError):
            record_deposit(self._quoted(), amount_minor=10_000)

    def test_only_permitted_extras_are_finalized(self):
        hire = self._quoted()
        confirm_hire(hire, vehicle=self.vehicle, driver=self.driver)
        add_extra(hire, kind="overtime", price_minor=15_000, is_permitted=True)
        add_extra(hire, kind="driver_allowance", price_minor=5_000, is_permitted=True)
        # Not permitted: recorded and visible, but never charged.
        add_extra(hire, kind="unapproved_waiver", price_minor=99_000, is_permitted=False)

        finalize_hire(hire)
        hire.refresh_from_db()
        self.assertEqual(hire.status, HireRequest.Status.COMPLETED)
        self.assertEqual(hire.extras_total_minor, 20_000)
        self.assertEqual(hire.final_total_minor, 140_000)
        # All three extras remain on record.
        self.assertEqual(CharterExtra.objects.filter(hire_request=hire).count(), 3)

    def test_finalize_requires_a_quote(self):
        hire = self._hire()
        with self.assertRaises(CharterError):
            finalize_hire(hire)

    def test_cancel_releases_the_reservation(self):
        hire = self._quoted()
        confirm_hire(hire, vehicle=self.vehicle, driver=self.driver)
        cancel_hire(hire, reason="Customer cancelled")
        hire.refresh_from_db()
        self.assertEqual(hire.status, HireRequest.Status.CANCELLED)
        self.assertEqual(hire.cancel_reason, "Customer cancelled")
        self.assertFalse(CharterReservation.objects.filter(hire_request=hire).exists())
        # The interval becomes bookable again.
        vehicles, drivers = check_availability(
            vehicle_class=Vehicle.VehicleClass.BUS, start_at=self.start, end_at=self.end
        )
        self.assertIn(self.vehicle, vehicles)


class ClauseThreeAvailabilityTestCase(CharterFixture):
    """Availability checks include travel to the pickup, turnaround time and
    existing reservations."""

    def test_blocked_window_is_wider_than_the_hire(self):
        blocked_from, blocked_to = blocked_window(self.start, self.end)
        # 10:00-14:00 hire, 60 min travel buffer, 30 min turnaround.
        self.assertEqual(blocked_from, self.start - timedelta(minutes=60))
        self.assertEqual(blocked_to, self.end + timedelta(minutes=30))

    def test_availability_is_empty_while_reserved(self):
        hire = self._quoted()
        confirm_hire(hire, vehicle=self.vehicle, driver=self.driver)
        vehicles, drivers = check_availability(
            vehicle_class=Vehicle.VehicleClass.BUS,
            start_at=self.start,
            end_at=self.end,
            passenger_count=20,
        )
        self.assertNotIn(self.vehicle, vehicles)
        self.assertNotIn(self.driver, drivers)
        # The other vehicle and driver remain free.
        self.assertIn(self.other_vehicle, vehicles)
        self.assertIn(self.other_driver, drivers)

    def test_travel_to_pickup_blocks_a_later_start(self):
        # Existing hire 10:00-14:00 blocks 09:00-14:30.
        hire = self._quoted()
        confirm_hire(hire, vehicle=self.vehicle, driver=self.driver)
        # A hire starting 14:15 overlaps the turnaround tail.
        later_start = self.start + timedelta(hours=4, minutes=15)
        later_end = later_start + timedelta(hours=2)
        vehicles, _ = check_availability(
            vehicle_class=Vehicle.VehicleClass.BUS, start_at=later_start, end_at=later_end
        )
        self.assertNotIn(self.vehicle, vehicles)

    def test_back_to_back_hire_after_turnaround_and_travel_is_allowed(self):
        hire = self._quoted()
        confirm_hire(hire, vehicle=self.vehicle, driver=self.driver)
        # The first hire blocks 09:00-14:30 (14:00 end + 30 min turnaround).
        # A second hire also needs its own 60 min travel to its pickup, so the
        # earliest it can start is 15:30.
        later_start = self.end + timedelta(minutes=30 + 60)
        later_end = later_start + timedelta(hours=2)
        vehicles, drivers = check_availability(
            vehicle_class=Vehicle.VehicleClass.BUS, start_at=later_start, end_at=later_end
        )
        self.assertIn(self.vehicle, vehicles)
        self.assertIn(self.driver, drivers)

    def test_earlier_hire_travel_buffer_blocks_a_later_booking(self):
        # A hire at 16:00-18:00 has a travel buffer back to 15:00, which still
        # clears the 14:30 end of the first hire's blocked window.
        hire = self._quoted()
        confirm_hire(hire, vehicle=self.vehicle, driver=self.driver)
        later_start = self.end + timedelta(hours=2)
        later_end = later_start + timedelta(hours=2)
        vehicles, _ = check_availability(
            vehicle_class=Vehicle.VehicleClass.BUS, start_at=later_start, end_at=later_end
        )
        self.assertIn(self.vehicle, vehicles)

        # A hire whose travel buffer reaches back into the first hire conflicts.
        tight_start = self.end + timedelta(minutes=45)
        tight_end = tight_start + timedelta(hours=2)
        vehicles, _ = check_availability(
            vehicle_class=Vehicle.VehicleClass.BUS, start_at=tight_start, end_at=tight_end
        )
        self.assertNotIn(self.vehicle, vehicles)

    def test_confirming_an_overlapping_hire_is_refused(self):
        first = self._quoted()
        confirm_hire(first, vehicle=self.vehicle, driver=self.driver)
        second = self._quoted()
        with self.assertRaises(CharterError):
            confirm_hire(second, vehicle=self.vehicle, driver=self.driver)

    def test_conflicting_driver_is_refused_even_with_another_vehicle(self):
        first = self._quoted()
        confirm_hire(first, vehicle=self.vehicle, driver=self.driver)
        second = self._quoted()
        with self.assertRaises(CharterError):
            confirm_hire(second, vehicle=self.other_vehicle, driver=self.driver)

    def test_custom_buffers_are_honoured(self):
        # The first hire still blocks until 14:30 through its own stored
        # turnaround. A second hire with no travel buffer and no turnaround can
        # therefore start exactly at 14:30, even though it is not back-to-back
        # with the 14:00 hire end.
        hire = self._quoted()
        confirm_hire(hire, vehicle=self.vehicle, driver=self.driver)
        later_start = self.end + timedelta(minutes=30)
        later_end = later_start + timedelta(hours=1)
        vehicles, _ = check_availability(
            vehicle_class=Vehicle.VehicleClass.BUS,
            start_at=later_start,
            end_at=later_end,
            travel_buffer_minutes=0,
            turnaround_minutes=0,
        )
        self.assertIn(self.vehicle, vehicles)

    def test_driver_without_valid_documents_is_not_available(self):
        Document.objects.filter(entity_id=self.driver.pk).update(
            status=Document.Status.EXPIRED
        )
        vehicles, drivers = check_availability(
            vehicle_class=Vehicle.VehicleClass.BUS, start_at=self.start, end_at=self.end
        )
        self.assertNotIn(self.driver, drivers)
        self.assertIn(self.other_driver, drivers)

    def test_suspend_you_confirm_excludes_you(self):
        hire = self._quoted()
        confirm_hire(hire, vehicle=self.vehicle, driver=self.driver)
        self.driver.status = Driver.Status.SUSPENDED
        self.driver.save(update_fields=["status", "updated_at"])
        # A suspended driver already holding a reservation is a data problem
        # worth surfacing, but the reservation still blocks the vehicle.
        vehicles, _ = check_availability(
            vehicle_class=Vehicle.VehicleClass.BUS, start_at=self.start, end_at=self.end
        )
        self.assertNotIn(self.vehicle, vehicles)


class CharterApiTestCase(CharterFixture):
    def setUp(self):
        super().setUp()
        self.staff_client = APIClient()
        self.staff_client.force_authenticate(self.staff)
        self.plain_client = APIClient()
        self.plain_client.force_authenticate(self.plain)

    def _payload(self, **overrides):
        payload = {
            "organization": self.org.pk,
            "service": self.service.pk,
            "route": self.route.pk,
            "start_at": "2026-11-02T10:00:00Z",
            "end_at": "2026-11-02T14:00:00Z",
            "vehicle_class": "bus",
            "passenger_count": 20,
            "included_hours": "4.00",
        }
        payload.update(overrides)
        return payload

    def test_staff_create_and_quote(self):
        response = self.staff_client.post(
            "/api/v1/charter/requests/", self._payload(), format="json"
        )
        self.assertEqual(response.status_code, 201, response.data)
        hire_id = response.data["id"]
        self.assertEqual(response.data["status"], HireRequest.Status.DRAFT)

        response = self.staff_client.post(
            f"/api/v1/charter/requests/{hire_id}/quote/",
            {"total_minor": 120_000},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], HireRequest.Status.QUOTED)

    def test_non_staff_cannot_create_a_hire(self):
        response = self.plain_client.post(
            "/api/v1/charter/requests/", self._payload(), format="json"
        )
        self.assertEqual(response.status_code, 403)

    def test_confirm_and_finalize_over_http(self):
        response = self.staff_client.post(
            "/api/v1/charter/requests/", self._payload(), format="json"
        )
        hire_id = response.data["id"]
        self.staff_client.post(
            f"/api/v1/charter/requests/{hire_id}/quote/",
            {"total_minor": 120_000},
            format="json",
        )
        response = self.staff_client.post(
            f"/api/v1/charter/requests/{hire_id}/confirm/",
            {"vehicle": self.vehicle.pk, "driver": self.driver.pk},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["status"], HireRequest.Status.CONFIRMED)
        # The reservation exposes the widened blocked window.
        self.assertIsNotNone(response.data["reservation"]["blocked_from"])

        self.staff_client.post(
            f"/api/v1/charter/requests/{hire_id}/deposit/",
            {"amount_minor": 50_000},
            format="json",
        )
        self.staff_client.post(
            f"/api/v1/charter/requests/{hire_id}/extras/",
            {"kind": "overtime", "price_minor": 15_000, "is_permitted": True},
            format="json",
        )
        response = self.staff_client.post(
            f"/api/v1/charter/requests/{hire_id}/complete/", {}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], HireRequest.Status.COMPLETED)
        self.assertEqual(response.data["final_total_minor"], 135_000)
        self.assertEqual(response.data["deposit_total_minor"], 50_000)

    def test_availability_endpoint(self):
        response = self.staff_client.get(
            "/api/v1/charter/availability/",
            {
                "start_at": "2026-11-02T10:00:00Z",
                "end_at": "2026-11-02T14:00:00Z",
                "vehicle_class": "bus",
            },
        )
        self.assertEqual(response.status_code, 200, response.data)
        ids = [v["id"] for v in response.data["vehicles"]]
        self.assertIn(str(self.vehicle.pk), ids)

    def test_availability_requires_dates(self):
        response = self.staff_client.get("/api/v1/charter/availability/")
        self.assertEqual(response.status_code, 400)

    def test_anonymous_access_is_rejected(self):
        response = APIClient().get("/api/v1/charter/requests/")
        self.assertIn(response.status_code, (401, 403))
