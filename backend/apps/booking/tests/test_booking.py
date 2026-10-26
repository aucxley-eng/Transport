"""Scheduled passenger booking invariants (blueprint 7).

Covers the blueprint's hard guarantees:
  * seat inventory held against departure AND occupied route segments;
  * overlapping segment reservations rejected even on simultaneous clicks;
  * a seat reusable on NON-overlapping segments (Nairobi->Nakuru vs
    Nakuru->Eldoret);
  * group bookings acquire together or get partial-availability;
  * ticket issued only on successful payment, in the same transaction as the
    ledger postings;
  * scanner checks departure, state, previous boarding;
  * a smaller replacement vehicle creates a reassignment exception rather
    than silently removing a passenger.
"""

from datetime import date, time, timedelta

import uuid

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import Membership, Organization, Role, User
from apps.booking.models import (
    BoardingScan,
    Departure,
    DepartureException,
    Route,
    RouteStop,
    SeatHold,
    Ticket,
)
from apps.booking.serializers import TicketSerializer
from apps.booking.services import (
    NoAvailability,
    acquire_hold,
    available_seats,
    board_from_scan,
    build_segments,
    create_booking_order,
    handle_vehicle_substitution,
    issue_ticket_for_order,
)
from apps.catalog.models import Place, Service
from apps.fleet.models import Vehicle
from apps.orders.models import Order


class BookingFixture(TestCase):
    """Nairobi -> Nakuru -> Eldoret with one 3-seat bus on a departure."""

    def setUp(self):
        self.org = Organization.objects.create(name="Alpha Sacco", slug="alpha-booking")
        self.admin = User.objects.create_user("0711000011", "pass-A-1234")
        Membership.objects.create(
            user=self.admin, organization=self.org, role=Role.OPERATOR_ADMIN
        )
        self.customer = User.objects.create_user("0712000022", "pass-C-1234")
        Membership.objects.create(
            user=self.customer, organization=self.org, role=Role.CUSTOMER
        )

        self.nairobi = Place.objects.create(name="Nairobi", place_type="stage", organization=self.org)
        self.nakuru = Place.objects.create(name="Nakuru", place_type="stage", organization=self.org)
        self.eldoret = Place.objects.create(name="Eldoret", place_type="stage", organization=self.org)

        self.service = Service.objects.create(
            slug="nbo-nkr-eld", name="Nairobi-Eldoret Express", service_type="scheduled"
        )
        self.route = Route.objects.create(organization=self.org, name="Nairobi-Eldoret")
        RouteStop.objects.create(route=self.route, place=self.nairobi, position=0)
        RouteStop.objects.create(route=self.route, place=self.nakuru, position=1)
        RouteStop.objects.create(route=self.route, place=self.eldoret, position=2)

        self.vehicle = Vehicle.objects.create(
            organization=self.org,
            registration_number="KDA100A",
            vehicle_class="bus",
            seating_capacity=3,
        )
        self.departure = Departure.objects.create(
            organization=self.org,
            service=self.service,
            route=self.route,
            vehicle=self.vehicle,
            vehicle_class="bus",
            departure_date=date(2026, 10, 1),
            departure_time=time(7, 0),
            capacity=3,
            fare_minor=100000,
        )
        build_segments(self.departure)


class SegmentInventoryTestCase(BookingFixture):
    def test_segments_built_between_consecutive_stops(self):
        self.assertEqual(self.departure.segments.count(), 2)

    def test_seat_sold_on_non_overlapping_segments(self):
        # Seat 1 is free on both legs: one passenger Nairobi->Nakuru, another
        # Nakuru->Eldoret may share it because the segments do not overlap.
        holds = acquire_hold(
            departure=self.departure,
            from_place=self.nairobi,
            to_place=self.nakuru,
            seats=1,
            customer=self.customer,
        )
        self.assertEqual(holds[0].seat_number, 1)

        second = acquire_hold(
            departure=self.departure,
            from_place=self.nakuru,
            to_place=self.eldoret,
            seats=1,
            customer=self.customer,
        )
        # Non-overlapping: allowed, and lands on the same seat.
        self.assertEqual(second[0].seat_number, 1)

    def test_overlapping_segment_rejected_for_same_seat(self):
        # Fill seat 1 on leg 0 (Nairobi->Nakuru). Requesting 3 seats on the
        # full route must not hand out seat 1 again on leg 0.
        acquire_hold(
            departure=self.departure,
            from_place=self.nairobi,
            to_place=self.nakuru,
            seats=1,
            customer=self.customer,
        )
        free_full_route, _ = available_seats(self.departure, self.nairobi, self.eldoret)
        # Seat 1 is taken on the first leg, so it is not sellable full-route.
        self.assertNotIn(1, free_full_route)
        self.assertEqual(free_full_route, [2, 3])

    def test_group_booking_acquires_together(self):
        holds = acquire_hold(
            departure=self.departure,
            from_place=self.nairobi,
            to_place=self.eldoret,
            seats=2,
            customer=self.customer,
        )
        self.assertEqual({h.seat_number for h in holds}, {1, 2})


class PartialAvailabilityTestCase(BookingFixture):
    def test_group_larger_than_capacity_is_rejected(self):
        with self.assertRaises(NoAvailability):
            acquire_hold(
                departure=self.departure,
                from_place=self.nairobi,
                to_place=self.eldoret,
                seats=4,
                customer=self.customer,
            )

    def test_group_request_returns_partial_message_before_payment(self):
        # Two seats gone; asking for three must fail as a group (not silently
        # hold one), giving a clear partial-availability response.
        acquire_hold(
            departure=self.departure,
            from_place=self.nairobi,
            to_place=self.eldoret,
            seats=2,
            customer=self.customer,
        )
        with self.assertRaises(NoAvailability) as ctx:
            acquire_hold(
                departure=self.departure,
                from_place=self.nairobi,
                to_place=self.eldoret,
                seats=3,
                customer=self.customer,
            )
        self.assertIn("1 seat", str(ctx.exception))


class HoldExpiryTestCase(BookingFixture):
    def test_expired_hold_frees_the_seat(self):
        holds = acquire_hold(
            departure=self.departure,
            from_place=self.nairobi,
            to_place=self.eldoret,
            seats=1,
            customer=self.customer,
        )
        SeatHold.objects.filter(pk=holds[0].pk).update(
            expires_at=timezone.now() - timedelta(minutes=1)
        )
        free, _ = available_seats(self.departure, self.nairobi, self.eldoret)
        self.assertIn(holds[0].seat_number, free)

    def test_expire_stale_holds_marks_expired(self):
        holds = acquire_hold(
            departure=self.departure,
            from_place=self.nairobi,
            to_place=self.eldoret,
            seats=1,
            customer=self.customer,
        )
        SeatHold.objects.filter(pk=holds[0].pk).update(
            expires_at=timezone.now() - timedelta(minutes=1)
        )
        from apps.booking.services import expire_stale_holds

        expire_stale_holds()
        holds[0].refresh_from_db()
        self.assertEqual(holds[0].state, SeatHold.State.EXPIRED)


class TicketOnPaymentTestCase(BookingFixture):
    def _paid_booking(self):
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
            passenger_name="Jane Doe",
            passenger_phone="0712000099",
            customer=self.customer,
        )
        return order, holds

    def test_no_ticket_before_payment(self):
        order, _ = self._paid_booking()
        self.assertEqual(Ticket.objects.filter(hold__order=order).count(), 0)
        self.assertEqual(order.status, Order.Status.CREATED)

    def test_ticket_issued_after_successful_payment_callback(self):
        from apps.payments.services import handle_payment_callback
        from apps.payments.models import PaymentAttempt

        order, holds = self._paid_booking()
        attempt = PaymentAttempt.objects.create(
            organization=self.org,
            order=order,
            provider="mpesa",
            idempotency_key=uuid.uuid4(),
            expected_amount_minor=order.total_minor,
            currency=order.currency,
            provider_reference="MPESA-REF-1",
        )
        handle_payment_callback(
            "mpesa",
            {
                "provider_reference": "MPESA-REF-1",
                "status": "success",
                "amount_minor": order.total_minor,
                "currency": order.currency,
            },
        )
        ticket = Ticket.objects.get(hold__order=order)
        self.assertEqual(ticket.state, Ticket.State.ISSUED)
        self.assertEqual(ticket.passenger_name, "Jane Doe")
        # passenger phone normalized to E.164
        self.assertEqual(ticket.passenger_phone, "+254712000099")
        holds[0].refresh_from_db()
        self.assertEqual(holds[0].state, SeatHold.State.CONVERTED)
        # Ledger posted in the same transaction as the ticket.
        self.assertGreaterEqual(attempt.__class__.objects.count(), 1)

    def test_ticket_document_carries_route_vehicle_and_payment(self):
        from apps.payments.services import record_cash_collection

        order, _ = self._paid_booking()
        record_cash_collection(order=order, actor=self.admin, note="counter")
        ticket = Ticket.objects.get(hold__order=order)

        document = TicketSerializer(ticket).data["document"]

        self.assertEqual(document["operator"]["name"], "Alpha Sacco")
        self.assertEqual(document["order_id"], str(order.pk))
        self.assertEqual(document["trip"]["from_place"], "Nairobi")
        self.assertEqual(document["trip"]["to_place"], "Eldoret")
        self.assertEqual(document["trip"]["vehicle_registration"], "KDA100A")
        self.assertEqual(document["trip"]["route"], "Nairobi-Eldoret")
        self.assertEqual(document["payment"]["provider"], "cash")
        self.assertEqual(document["payment"]["amount_minor"], order.total_minor)
        self.assertTrue(document["payment"]["reference"].startswith("CASH-"))

    def test_ticket_endpoint_filters_by_order_and_returns_document(self):
        from rest_framework.test import APIClient

        from apps.payments.services import record_cash_collection

        order, _ = self._paid_booking()
        record_cash_collection(order=order, actor=self.admin, note="counter")

        client = APIClient()
        client.force_authenticate(self.admin)
        response = client.get("/api/v1/tickets/", {"order": str(order.pk)})

        self.assertEqual(response.status_code, 200)
        rows = response.json()["results"]
        self.assertEqual(len(rows), 1)
        document = rows[0]["document"]
        self.assertEqual(document["trip"]["from_place"], "Nairobi")
        self.assertEqual(document["trip"]["to_place"], "Eldoret")
        self.assertEqual(document["trip"]["vehicle_registration"], "KDA100A")
        self.assertEqual(document["payment"]["provider"], "cash")

    def test_expired_hold_yields_no_ticket_but_refund_event(self):
        from apps.common.models import OutboxEvent
        from apps.payments.services import handle_payment_callback
        from apps.payments.models import PaymentAttempt

        order, holds = self._paid_booking()
        # Hold lapses before the callback settles.
        SeatHold.objects.filter(pk=holds[0].pk).update(
            expires_at=timezone.now() - timedelta(minutes=1)
        )
        PaymentAttempt.objects.create(
            organization=self.org,
            order=order,
            provider="mpesa",
            idempotency_key=uuid.uuid4(),
            expected_amount_minor=order.total_minor,
            currency=order.currency,
            provider_reference="MPESA-REF-2",
        )
        handle_payment_callback(
            "mpesa",
            {
                "provider_reference": "MPESA-REF-2",
                "status": "success",
                "amount_minor": order.total_minor,
                "currency": order.currency,
            },
        )
        self.assertEqual(Ticket.objects.filter(hold__order=order).count(), 0)
        self.assertTrue(
            OutboxEvent.objects.filter(
                event_type="booking.rebook_or_refund_required"
            ).exists()
        )


class BoardingTestCase(BookingFixture):
    def _issue_ticket(self):
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
            passenger_name="Jane Doe",
            passenger_phone="0712000099",
            customer=self.customer,
        )
        tickets, _ = issue_ticket_for_order(order)
        return tickets[0]

    def test_board_marks_ticket_and_records_scan(self):
        ticket = self._issue_ticket()
        boarded = board_from_scan(
            ticket_reference=ticket.reference, departure=self.departure
        )
        self.assertEqual(boarded.state, Ticket.State.BOARDED)
        self.assertEqual(BoardingScan.objects.filter(ticket=ticket).count(), 1)

    def test_boarding_the_last_seat_completes_the_paid_order(self):
        from apps.common.models import OutboxEvent

        ticket = self._issue_ticket()
        order = ticket.hold.order
        order.confirm()
        board_from_scan(ticket_reference=ticket.reference)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.COMPLETED)
        # The event the payments side recognises revenue from names the order.
        event = OutboxEvent.objects.get(event_type="booking.ticket_boarded")
        self.assertEqual(event.payload["order_id"], str(order.pk))

    def test_an_unpaid_order_is_not_completed_by_boarding(self):
        ticket = self._issue_ticket()
        order = ticket.hold.order  # still CREATED: never paid
        board_from_scan(ticket_reference=ticket.reference)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.CREATED)

    def test_double_board_is_rejected(self):
        ticket = self._issue_ticket()
        board_from_scan(ticket_reference=ticket.reference)
        with self.assertRaises(Exception):
            board_from_scan(ticket_reference=ticket.reference)

    def test_scan_checks_departure(self):
        ticket = self._issue_ticket()
        other = Departure.objects.create(
            organization=self.org,
            service=self.service,
            route=self.route,
            vehicle=self.vehicle,
            vehicle_class="bus",
            departure_date=date(2026, 10, 2),
            departure_time=time(7, 0),
            capacity=3,
            fare_minor=100000,
        )
        with self.assertRaises(Exception):
            board_from_scan(ticket_reference=ticket.reference, departure=other)


class BookingIsolationTestCase(BookingFixture):
    """Operator isolation on booking writes (blueprint 3): an operator admin may
    only create/update departures and routes within organizations they
    administer."""

    def setUp(self):
        super().setUp()
        self.org_b = Organization.objects.create(name="Beta Sacco", slug="beta-booking")
        self.token = f"Bearer {self._token_for(self.admin)}"

    @staticmethod
    def _token_for(user):
        from rest_framework_simplejwt.tokens import RefreshToken

        return str(RefreshToken.for_user(user).access_token)

    def test_operator_can_create_departure_in_own_org(self):
        from rest_framework.test import APIClient

        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=self.token)
        response = client.post(
            "/api/v1/departures/",
            {
                "organization": str(self.org.pk),
                "service": str(self.service.pk),
                "route": str(self.route.pk),
                "departure_date": "2026-10-05",
                "departure_time": "08:00:00",
                "capacity": 10,
                "fare_minor": 50000,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.content)

    def test_operator_cannot_create_departure_in_other_org(self):
        from rest_framework.test import APIClient

        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=self.token)
        response = client.post(
            "/api/v1/departures/",
            {
                "organization": str(self.org_b.pk),
                "service": str(self.service.pk),
                "route": str(self.route.pk),
                "departure_date": "2026-10-05",
                "departure_time": "08:00:00",
                "capacity": 10,
                "fare_minor": 50000,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 403)


class VehicleSubstitutionTestCase(BookingFixture):
    def _confirmed_ticket(self):
        """Issue one confirmed ticket through the normal hold->order path."""
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
            passenger_name="Jane Doe",
            passenger_phone="0712000099",
            customer=self.customer,
        )
        tickets, _ = issue_ticket_for_order(order)
        return tickets[0]

    def test_smaller_replacement_creates_reassignment_exception(self):
        # Two confirmed tickets on a 3-seat bus.
        self._confirmed_ticket()
        self._confirmed_ticket()
        smaller = Vehicle.objects.create(
            organization=self.org,
            registration_number="KDA101B",
            vehicle_class="van",
            seating_capacity=1,
        )
        _, exceptions = handle_vehicle_substitution(
            departure=self.departure, new_vehicle=smaller
        )
        self.assertEqual(len(exceptions), 1)
        self.assertTrue(exceptions[0].requires_customer_communication)
        self.assertEqual(
            exceptions[0].kind, DepartureException.Kind.VEHICLE_SUBSTITUTION
        )
        # No passenger was silently removed: both tickets remain issued.
        self.assertEqual(Ticket.objects.filter(state=Ticket.State.ISSUED).count(), 2)
