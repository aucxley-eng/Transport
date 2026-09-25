"""On-demand ride invariants (blueprint 8).

Covers:
  * dispatch filters by availability, vehicle class, service zone AND document
    status (an expired or unverified document removes a driver);
  * time-limited offers; the first valid acceptance wins and later/expired
    acceptances are rejected (atomic assignment);
  * offer acceptance and trip start are separate events;
  * pickup is confirmed with a trip PIN;
  * a destination change requires a revised estimate and customer acceptance;
  * cancellations/no-shows record reason, evidence and the applicable fee;
  * failed matching gives an explicit retry / scheduled-alternative / cancel
    outcome;
  * a driver sees contact details only for trips assigned to them.
"""

from datetime import date, timedelta

from django.contrib.auth.hashers import make_password
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import Membership, Organization, Role, User
from apps.catalog.models import Place, Service
from apps.fleet.models import Document, Driver, Vehicle
from apps.rides.models import (
    DriverOffer,
    DriverShift,
    RideRequest,
    ServiceZone,
    Trip,
)
from apps.rides.services import (
    RideError,
    accept_destination_change,
    accept_offer,
    cancel_ride,
    change_destination,
    complete_trip,
    create_ride_request,
    dispatch_offers,
    eligible_drivers,
    expire_offers,
    fail_matching,
    mark_arrived,
    verify_pickup,
    viewer_may_see_contact,
)


def _make_document(driver, *, status=Document.Status.VERIFIED, expires_in_days=90):
    return Document.objects.create(
        organization=driver.organization,
        entity_type=Document.EntityType.DRIVER,
        entity_id=driver.pk,
        doc_type="psv_licence",
        status=status,
        expires_on=date(2026, 12, 31) if expires_in_days is None else (
            timezone.localdate() + timedelta(days=expires_in_days)
        ),
    )


class RideFixture(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Alpha Rides", slug="alpha-rides")
        self.customer = User.objects.create_user("0711000011", "pass-A-1234")
        self.driver_user = User.objects.create_user("0711000022", "pass-D-1234")
        Membership.objects.create(
            user=self.customer, organization=self.org, role=Role.CUSTOMER
        )
        self.pickup = Place.objects.create(name="Nairobi", place_type="stage", organization=self.org)
        self.dest = Place.objects.create(name="Nakuru", place_type="stage", organization=self.org)
        self.service = Service.objects.create(
            slug="ride-ondemand", name="On-demand ride", service_type=Service.ServiceType.RIDE
        )
        self.zone = ServiceZone.objects.create(name="Nairobi Metro", radius_km=30)

        self.vehicle = Vehicle.objects.create(
            organization=self.org,
            registration_number="KDA200A",
            vehicle_class="sedan",
            seating_capacity=4,
        )
        self.driver = Driver.objects.create(
            user=self.driver_user,
            organization=self.org,
            status=Driver.Status.APPROVED,
            current_vehicle=self.vehicle,
        )
        _make_document(self.driver)
        now = timezone.now()
        DriverShift.objects.create(
            driver=self.driver,
            zone=self.zone,
            starts_at=now - timedelta(hours=1),
            ends_at=now + timedelta(hours=5),
            status=DriverShift.Status.AVAILABLE,
        )

    def _ride(self, **kwargs):
        return create_ride_request(
            customer=self.customer,
            pickup=self.pickup,
            destination=self.dest,
            service=self.service,
            vehicle_class=kwargs.get("vehicle_class", "sedan"),
        )


class EligibilityTestCase(RideFixture):
    def test_approved_driver_with_valid_documents_is_eligible(self):
        ride = self._ride()
        drivers = eligible_drivers(ride)
        self.assertIn(self.driver, drivers)

    def test_suspended_driver_is_not_eligible(self):
        self.driver.status = Driver.Status.SUSPENDED
        self.driver.save()
        self.assertNotIn(self.driver, eligible_drivers(self._ride()))

    def test_expired_document_removes_driver(self):
        _make_document(self.driver, expires_in_days=-1)  # an expired second doc
        self.assertNotIn(self.driver, eligible_drivers(self._ride()))

    def test_unverified_document_removes_driver(self):
        _make_document(self.driver, status=Document.Status.PENDING)
        self.assertNotIn(self.driver, eligible_drivers(self._ride()))

    def test_vehicle_class_mismatch_removes_driver(self):
        ride = self._ride(vehicle_class="bus")  # driver has a sedan
        self.assertNotIn(self.driver, eligible_drivers(ride))

    def test_zone_mismatch_removes_driver(self):
        ride = self._ride()
        ride.zone = ServiceZone.objects.create(name="Mombasa", radius_km=20)
        ride.save()
        self.assertNotIn(self.driver, eligible_drivers(ride))

    def test_off_shift_driver_is_not_eligible(self):
        self.driver.shifts.update(
            starts_at=timezone.now() + timedelta(hours=4),
            ends_at=timezone.now() + timedelta(hours=8),
        )
        self.assertNotIn(self.driver, eligible_drivers(self._ride()))


class OfferAndAssignmentTestCase(RideFixture):
    def test_dispatch_creates_time_limited_offers(self):
        ride = self._ride()
        offers = dispatch_offers(ride)
        self.assertEqual(len(offers), 1)
        self.assertGreater(offers[0].expires_at, timezone.now())

    def test_first_acceptance_wins_and_second_is_rejected(self):
        # A second eligible driver receives a competing offer.
        other_user = User.objects.create_user("0711000033", "pass-D-1234")
        other_vehicle = Vehicle.objects.create(
            organization=self.org, registration_number="KDA200B",
            vehicle_class="sedan", seating_capacity=4,
        )
        other_driver = Driver.objects.create(
            user=other_user, organization=self.org,
            status=Driver.Status.APPROVED, current_vehicle=other_vehicle,
        )
        _make_document(other_driver)
        now = timezone.now()
        DriverShift.objects.create(
            driver=other_driver, zone=self.zone,
            starts_at=now - timedelta(hours=1), ends_at=now + timedelta(hours=5),
        )
        ride = self._ride()
        offers = dispatch_offers(ride)
        self.assertEqual(len(offers), 2)

        first_trip = accept_offer(offers[0])
        ride.refresh_from_db()
        self.assertEqual(ride.status, RideRequest.Status.ASSIGNED)
        # The competing (later) acceptance must be rejected.
        with self.assertRaises(RideError):
            accept_offer(offers[1])
        self.assertEqual(Trip.objects.count(), 1)
        offers[1].refresh_from_db()
        self.assertEqual(offers[1].state, DriverOffer.State.EXPIRED)

    def test_expired_offer_acceptance_is_rejected(self):
        ride = self._ride()
        offers = dispatch_offers(ride)
        DriverOffer.objects.filter(pk=offers[0].pk).update(
            expires_at=timezone.now() - timedelta(minutes=1)
        )
        offers[0].refresh_from_db()
        with self.assertRaises(RideError):
            accept_offer(offers[0])
        self.assertEqual(Trip.objects.count(), 0)

    def test_expire_offers_command_sweeps(self):
        ride = self._ride()
        offers = dispatch_offers(ride)
        DriverOffer.objects.filter(pk=offers[0].pk).update(
            expires_at=timezone.now() - timedelta(minutes=1)
        )
        expire_offers()
        offers[0].refresh_from_db()
        self.assertEqual(offers[0].state, DriverOffer.State.EXPIRED)


class TripLifecycleTestCase(RideFixture):
    def _assigned_trip(self):
        ride = self._ride()
        offers = dispatch_offers(ride)
        return accept_offer(offers[0]), ride

    def test_acceptance_does_not_start_the_trip(self):
        # "Offer acceptance and trip start are separate events" (Figure 4).
        trip, ride = self._assigned_trip()
        self.assertEqual(trip.state, Trip.State.ASSIGNED)
        self.assertIsNone(trip.started_at)

    def test_pickup_pin_starts_the_trip(self):
        trip, ride = self._assigned_trip()
        mark_arrived(trip)
        # Wrong PIN rejected.
        with self.assertRaises(RideError):
            verify_pickup(trip, "0000")
        trip.refresh_from_db()
        self.assertEqual(trip.state, Trip.State.ARRIVED)
        # The correct PIN starts the trip.
        trip.pin_hash = make_password("4321")
        trip.save(update_fields=["pin_hash", "updated_at"])
        verify_pickup(trip, "4321")
        trip.refresh_from_db()
        self.assertEqual(trip.state, Trip.State.IN_PROGRESS)
        self.assertIsNotNone(trip.started_at)

    def test_complete_trip_settles_status(self):
        trip, ride = self._assigned_trip()
        mark_arrived(trip)
        trip.pin_hash = make_password("4321")
        trip.save(update_fields=["pin_hash", "updated_at"])
        verify_pickup(trip, "4321")
        complete_trip(trip)
        trip.refresh_from_db()
        self.assertEqual(trip.state, Trip.State.COMPLETED)
        self.assertIsNotNone(trip.ended_at)


class DestinationChangeTestCase(RideFixture):
    def _in_progress_trip(self):
        ride = self._ride()
        offers = dispatch_offers(ride)
        trip = accept_offer(offers[0])
        mark_arrived(trip)
        trip.pin_hash = make_password("4321")
        trip.save(update_fields=["pin_hash", "updated_at"])
        verify_pickup(trip, "4321")
        return trip, ride

    def test_destination_change_requires_acceptance(self):
        trip, ride = self._in_progress_trip()
        new_dest = Place.objects.create(name="Eldoret", place_type="stage")
        quote = change_destination(trip, new_dest, revised_estimate_minor=5000)
        # Not applied yet.
        ride.refresh_from_db()
        self.assertEqual(ride.destination, self.dest)
        # After acceptance it applies.
        accept_destination_change(trip)
        ride.refresh_from_db()
        self.assertEqual(ride.destination, new_dest)
        self.assertGreater(quote.total_minor, 0)


class CancellationTestCase(RideFixture):
    def test_cancellation_records_reason_and_fee(self):
        ride = self._ride()
        cancellation = cancel_ride(
            ride, reason="Customer changed plans", evidence="SMS", fee_minor=1000
        )
        self.assertEqual(cancellation.reason, "Customer changed plans")
        self.assertEqual(cancellation.fee_minor, 1000)
        ride.refresh_from_db()
        self.assertEqual(ride.status, RideRequest.Status.CANCELLED)


class FailedMatchingTestCase(RideFixture):
    def test_no_eligible_driver_marks_unmatched_with_outcome(self):
        self.driver.status = Driver.Status.SUSPENDED
        self.driver.save()
        ride = self._ride()
        offers = dispatch_offers(ride)
        self.assertEqual(len(offers), 0)
        ride.refresh_from_db()
        self.assertEqual(ride.status, RideRequest.Status.UNMATCHED)
        self.assertEqual(ride.resolution, RideRequest.Resolution.RETRY)

    def test_fail_matching_offers_three_explicit_resolutions(self):
        # Each customer-facing outcome is recorded on its own ride.
        for resolution in RideRequest.Resolution.values:
            with self.subTest(resolution=resolution):
                ride = self._ride()
                fail_matching(ride, resolution)
                ride.refresh_from_db()
                self.assertEqual(ride.status, RideRequest.Status.UNMATCHED)
                self.assertEqual(ride.resolution, resolution)
                # Pending offers are closed when matching fails.
                self.assertFalse(
                    DriverOffer.objects.filter(
                        ride_request=ride, state=DriverOffer.State.PENDING
                    ).exists()
                )


class ContactPrivacyTestCase(RideFixture):
    def test_driver_sees_contact_only_for_assigned_trip(self):
        ride = self._ride()
        offers = dispatch_offers(ride)
        trip = accept_offer(offers[0])
        # The assigned driver may see the customer's phone.
        self.assertTrue(viewer_may_see_contact(trip, self.driver_user))
        # Another driver may not.
        other_user = User.objects.create_user("0711000099", "pass-D-1234")
        self.assertFalse(viewer_may_see_contact(trip, other_user))
        # The customer may see their own.
        self.assertTrue(viewer_may_see_contact(trip, self.customer))


class RideApiTestCase(RideFixture):
    """The HTTP surface: request -> dispatch -> offer -> accept -> trip."""

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.customer_client = APIClient()
        self.customer_client.force_authenticate(self.customer)
        self.driver_client = APIClient()
        self.driver_client.force_authenticate(self.driver_user)

    def test_full_ride_flow_over_http(self):
        response = self.customer_client.post(
            reverse("rides-list"),
            {
                "pickup": self.pickup.pk,
                "destination": self.dest.pk,
                "service": self.service.pk,
                "vehicle_class": "sedan",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        ride_id = response.data["id"]
        self.assertEqual(response.data["status"], RideRequest.Status.REQUESTED)

        # Dispatch is a privileged operation.
        ride = RideRequest.objects.get(pk=ride_id)
        offers = dispatch_offers(ride)
        self.assertEqual(len(offers), 1)

        # The driver accepts through the API and gets a trip back.
        response = self.driver_client.post(
            reverse("driver-offers-accept", args=[offers[0].pk]), {}, format="json"
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["state"], Trip.State.ASSIGNED)
        # Acceptance alone does not start the trip.
        self.assertIsNone(response.data["started_at"])
        trip_id = response.data["id"]

        # Arrive, then a wrong PIN must not start the trip.
        response = self.driver_client.post(
            reverse("trips-arrive", args=[trip_id]), {}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["state"], Trip.State.ARRIVED)
        response = self.driver_client.post(
            reverse("trips-verify-pin", args=[trip_id]), {"pin": "0000"}, format="json"
        )
        self.assertEqual(response.status_code, 400)

    def test_trip_detail_hides_contact_from_unassigned_driver(self):
        ride = self._ride()
        offers = dispatch_offers(ride)
        trip = accept_offer(offers[0])

        # The assigned driver sees the phone.
        response = self.driver_client.get(reverse("trips-detail", args=[trip.pk]))
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["customer_phone"], self.customer.phone)

        # An unrelated driver does not.
        other_user = User.objects.create_user("0711000099", "pass-D-1234")
        other_client = APIClient()
        other_client.force_authenticate(other_user)
        response = other_client.get(reverse("trips-detail", args=[trip.pk]))
        self.assertEqual(response.status_code, 404)
        self.assertNotIn("customer_phone", response.data)

    def test_customer_cannot_accept_another_users_offer(self):
        ride = self._ride()
        offers = dispatch_offers(ride)
        response = self.customer_client.post(
            reverse("driver-offers-accept", args=[offers[0].pk]), {}, format="json"
        )
        self.assertEqual(response.status_code, 404)

    def test_anonymous_access_is_rejected(self):
        response = APIClient().get(reverse("rides-list"))
        self.assertIn(response.status_code, (401, 403))
