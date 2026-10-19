"""Local courier: one pickup, many parcels, many recipients (blueprint 9, 10).

The nineteen invariants in ``docs/phase3d-scope.md`` are under test here, in
order. The rules they enforce are quoted in ``apps/goods/courier.py``,
``apps/goods/consolidation.py``, ``apps/goods/insertion.py`` and
``apps/goods/services.py``.
"""

from datetime import date, datetime, timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import Membership, Organization, Role, User
from apps.catalog.models import Place, RateCard, RateLine, Service
from apps.common.models import OutboxEvent
from apps.fleet.models import CargoCompartment, Driver, Vehicle
from apps.orders.models import Order
from apps.goods.consolidation import (
    REASON_DROP_OVERLOADS_WINDOW,
    REASON_TRIP_IS_PASSENGER,
    REASON_WRONG_DESTINATION,
    propose_consolidation,
    publish_plan,
    validate_plan,
)
from apps.goods.courier import (
    DROP_SERVICE_MINUTES,
    book_courier_order,
    deliver_package,
    fail_delivery_attempt,
    quote_courier_order,
)
from apps.goods.insertion import (
    REASON_MULTI_DROP_INSERTION,
    FreightError,
    insert_consignment,
    request_insertion,
)
from apps.goods.models import (
    ConsolidationPlan,
    ConsolidationStop,
    FreightTrip,
    InsertionRequest,
    LoadType,
    MeasurementBasis,
    Package,
    Shipment,
    ShipmentEvidence,
    ShipmentException,
)
from apps.goods.services import (
    GoodsError,
    cargo_capacity,
    create_freight_trip,
    create_return_shipment,
    create_shipment,
    quote_shipment,
    record_evidence,
    resolve_exception,
)

TODAY = date(2026, 1, 1)


class CourierFixture(TestCase):
    """One sender, one pickup, parcels for three different people."""

    def setUp(self):
        self.org = Organization.objects.create(name="Swift Courier", slug="swift")
        self.other_org = Organization.objects.create(name="Rival Courier", slug="rival")
        self.sender = User.objects.create_user("0744000011", "pass-S-1234")
        self.staff = User.objects.create_user("0744000022", "pass-S-1234")
        Membership.objects.create(
            user=self.staff, organization=self.org, role=Role.DISPATCHER
        )
        self.branch = User.objects.create_user("0744000033", "pass-B-1234")
        Membership.objects.create(
            user=self.branch, organization=self.org, role=Role.BRANCH_STAFF
        )

        self.service = Service.objects.create(
            slug="city-courier", name="City courier", service_type=Service.ServiceType.COURIER
        )
        self.freight_service = Service.objects.create(
            slug="road-freight", name="Road freight", service_type=Service.ServiceType.FREIGHT
        )
        self.shuttle = Service.objects.create(
            slug="morning-shuttle",
            name="Morning shuttle",
            service_type=Service.ServiceType.SCHEDULED,
        )

        self.depot = Place.objects.create(
            organization=self.org, name="Depot", place_type=Place.PlaceType.BRANCH
        )
        self.market = Place.objects.create(
            organization=self.org, name="Market", place_type=Place.PlaceType.HUB
        )
        self.mill = Place.objects.create(
            organization=self.org, name="Mill", place_type=Place.PlaceType.BRANCH
        )
        self.joiner = Place.objects.create(
            organization=self.org, name="Joiner", place_type=Place.PlaceType.BRANCH
        )

        self.van = Vehicle.objects.create(
            organization=self.org,
            registration_number="KCP900A",
            vehicle_class=Vehicle.VehicleClass.VAN,
            cargo_weight_kg=900,
            cargo_volume_m3=6.0,
        )
        CargoCompartment.objects.create(
            organization=self.org,
            vehicle=self.van,
            name="load bay",
            max_weight_kg=900,
            max_volume_m3=6.0,
        )
        self.driver = Driver.objects.create(
            user=User.objects.create_user("0744000044", "pass-D-1234"),
            organization=self.org,
            status=Driver.Status.APPROVED,
            working_hours_per_day=8,
        )
        # A driver is a member of the operator they drive for, with the driver
        # role: that is what lets them read the trip and record its handovers.
        Membership.objects.create(
            user=self.driver.user, organization=self.org, role=Role.DRIVER
        )

        self.start = timezone.make_aware(datetime(2026, 6, 1, 8, 0))
        self.end = timezone.make_aware(datetime(2026, 6, 1, 16, 0))
        self.card = self._rate_card()

    # -- fixtures ----------------------------------------------------------

    def _rate_card(self, *, organization=None, name="Courier 2026", amount=30_000):
        card = RateCard.objects.create(
            organization=organization,
            service=self.service,
            name=name,
            version=1,
            currency="KES",
            effective_from=TODAY - timedelta(days=30),
        )
        # "Up to 1 kg: 300, 1-5 kg: 600, above 5 kg: 1200."
        for index, (from_value, to_value, amount_minor) in enumerate(
            ((0, 1, 300), (1, 5, 600), (5, None, 1_200))
        ):
            RateLine.objects.create(
                rate_card=card,
                line_type=RateLine.LineType.PER_KG,
                label=f"{from_value}-{to_value or 'up'} kg",
                from_value=from_value,
                to_value=to_value,
                amount_minor=amount_minor,
            )
        return card

    def _parcel(self, name="Achieng Otieno", place=None, weight=0.8, **overrides):
        spec = {
            "recipient_name": name,
            "recipient_phone": "0722000001",
            "recipient_place": str((place or self.market).pk),
            "description": "Books",
            "weight_kg": weight,
            "length_cm": 20,
            "width_cm": 15,
            "height_cm": 10,
        }
        spec.update(overrides)
        return spec

    def _parcels(self):
        return [
            self._parcel("Achieng Otieno", self.market, 0.8),
            self._parcel("Brian Kiptoo", self.mill, 3.0),
            self._parcel("Carol Mwangi", self.joiner, 9.0),
        ]

    def _many_parcels(self, count):
        """Parcels for `count` distinct new drop places, and the last of those
        places as the order's destination."""
        places = [
            Place.objects.create(
                organization=self.org, name=f"Stop {index}", place_type=Place.PlaceType.BRANCH
            )
            for index in range(count)
        ]
        parcels = [
            self._parcel(f"Recipient {index}", place) for index, place in enumerate(places)
        ]
        return parcels, places[-1]

    def _quote(self, packages=None, **overrides):
        payload = {
            "organization": self.org,
            "service": self.service,
            "collection": self.depot,
            "destination": self.market,
            "packages": self._parcels() if packages is None else packages,
            "service_window_start": self.start,
            "service_window_end": self.end,
            "sender_name": "Grace Wanjiku",
            "sender_phone": "0722000099",
        }
        payload.update(overrides)
        return quote_courier_order(**payload)

    def _order(self, packages=None, **overrides):
        quote = self._quote(packages, **overrides)
        quote.accept()
        return book_courier_order(
            quote=quote, organization=self.org, sender=self.sender
        )

    def _trip(self, **overrides):
        payload = {
            "organization": self.org,
            "vehicle": self.van,
            "driver": self.driver,
            "service": self.service,
            "origin": self.depot,
            "destination": self.market,
            "planned_start": self.start,
            "planned_end": self.end,
        }
        payload.update(overrides)
        return create_freight_trip(**payload)

    def _deliver(self, package, **overrides):
        payload = {
            "recipient_name": package.recipient_name,
            "recipient_id_type": "national_id",
            "recipient_id_last4": "4321",
            "photos": ["https://files.example/parcel.jpg"],
            "recorded_by": self.staff,
        }
        payload.update(overrides)
        return deliver_package(package, **payload)


class ParcelIsTheDeliveryUnitTestCase(CourierFixture):
    """Invariant 1 and 3: a parcel names its own recipient and drop, and an
    order is a shared load."""

    def test_a_parcel_without_a_recipient_is_refused_by_position(self):
        parcels = self._parcels()
        parcels[1]["recipient_name"] = ""
        with self.assertRaises(GoodsError) as caught:
            self._quote(parcels)
        self.assertIn("Parcel 2", str(caught.exception))

    def test_a_parcel_without_a_drop_place_is_refused(self):
        parcels = self._parcels()
        parcels[0]["recipient_place"] = None
        with self.assertRaises(GoodsError) as caught:
            self._quote(parcels)
        self.assertIn("Parcel 1", str(caught.exception))

    def test_each_parcel_carries_its_own_recipient_and_drop(self):
        order = self._order()
        self.assertEqual(order.package_count, 3)
        self.assertEqual(order.collection, self.depot)
        self.assertEqual(
            {(p.recipient_name, p.recipient_place_id) for p in order.packages.all()},
            {
                ("Achieng Otieno", self.market.pk),
                ("Brian Kiptoo", self.mill.pk),
                ("Carol Mwangi", self.joiner.pk),
            },
        )

    def test_a_courier_order_names_no_single_consignee(self):
        order = self._order()
        self.assertEqual(order.recipient_name, "")
        self.assertEqual(order.recipient_phone, "")

    def test_an_empty_order_is_refused(self):
        with self.assertRaises(GoodsError):
            self._quote([])

    def test_a_courier_order_is_never_a_full_truckload(self):
        quote = self._quote()
        quote.accept()
        with self.assertRaises(GoodsError):
            book_courier_order(
                quote=quote,
                organization=self.org,
                sender=self.sender,
                load_type=LoadType.FULL_TRUCKLOAD,
            )

    def test_a_parcel_service_is_not_courier(self):
        parcel_service = Service.objects.create(
            slug="intercity-parcel", name="Intercity", service_type=Service.ServiceType.PARCEL
        )
        with self.assertRaises(GoodsError):
            self._quote(service=parcel_service)


class DestinationIsAPlaceParcelsGoToTestCase(CourierFixture):
    """Invariant 2: the order's destination is a place a parcel is bound for."""

    def test_a_destination_no_parcel_goes_to_is_refused(self):
        elsewhere = Place.objects.create(
            organization=self.org, name="Nowhere", place_type=Place.PlaceType.BRANCH
        )
        with self.assertRaises(GoodsError) as caught:
            self._quote(destination=elsewhere)
        self.assertIn("not a place any parcel", str(caught.exception))

    def test_a_destination_one_parcel_goes_to_is_accepted(self):
        order = self._order()
        self.assertEqual(order.destination, self.market)


class FreightStillNeedsAConsigneeTestCase(CourierFixture):
    """Invariant 4: the order-level consignee became optional for courier, not
    for freight."""

    def test_freight_still_requires_an_order_level_recipient(self):
        with self.assertRaises(GoodsError):
            create_shipment(
                quote=self._freight_quote(),
                organization=self.org,
                service=self.freight_service,
                sender=self.sender,
                sender_name="Grace Wanjiku",
                sender_phone="0722000099",
                recipient_name="",
                recipient_phone="",
                collection=self.depot,
                destination=self.market,
                service_window_start=self.start,
                service_window_end=self.end,
                packages=[{"weight_kg": 100, "length_cm": 20, "width_cm": 20, "height_cm": 20}],
            )

    def test_freight_parcels_carry_no_drop_place(self):
        shipment = self._freight_shipment()
        self.assertIsNone(shipment.packages.get().recipient_place)
        self.assertFalse(shipment.is_multi_drop())

    def _freight_quote(self):
        quote = quote_shipment(
            organization=self.org,
            service=self.freight_service,
            sender_name="Grace Wanjiku",
            sender_phone="0722000099",
            recipient_name="Market Trader",
            recipient_phone="0722000088",
            collection=self.depot,
            destination=self.market,
            total_weight_kg=100,
            total_volume_m3=0.02,
            service_window_start=self.start,
            service_window_end=self.end,
            total_minor=20_000,
        )
        quote.accept()
        return quote

    def _freight_shipment(self):
        return create_shipment(
            quote=self._freight_quote(),
            organization=self.org,
            service=self.freight_service,
            sender=self.sender,
            sender_name="Grace Wanjiku",
            sender_phone="0722000099",
            recipient_name="Market Trader",
            recipient_phone="0722000088",
            collection=self.depot,
            destination=self.market,
            service_window_start=self.start,
            service_window_end=self.end,
            packages=[{"weight_kg": 100, "length_cm": 20, "width_cm": 20, "height_cm": 20}],
        )


class ChargeableWeightTestCase(CourierFixture):
    """Invariant 5: a parcel is charged on the greater of its actual and
    volumetric weight, and the basis is recorded."""

    def test_a_light_bulky_box_is_charged_on_its_size(self):
        # 20*20*20 = 8,000 cm3 / 4000 = 2 kg volumetric, against 0.2 kg actual.
        quote = self._quote(
            packages=[
                self._parcel(
                    "Achieng Otieno",
                    self.market,
                    weight=0.2,
                    length_cm=20,
                    width_cm=20,
                    height_cm=20,
                )
            ]
        )
        parcel = quote.request_payload["parcels"][0]
        self.assertEqual(Decimal(parcel["weight_kg"]), Decimal("0.200"))
        self.assertEqual(Decimal(parcel["volumetric_weight_kg"]), Decimal("2.000"))
        self.assertEqual(Decimal(parcel["chargeable_weight_kg"]), Decimal("2.000"))
        self.assertEqual(parcel["measured_on"], "volumetric")
        self.assertEqual(quote.basis, MeasurementBasis.CHARGEABLE_WEIGHT)

    def test_a_dense_small_box_is_charged_on_its_mass(self):
        quote = self._quote(
            packages=[
                self._parcel(
                    "Achieng Otieno",
                    self.market,
                    weight=9.0,
                    length_cm=10,
                    width_cm=10,
                    height_cm=10,
                )
            ]
        )
        parcel = quote.request_payload["parcels"][0]
        self.assertEqual(Decimal(parcel["chargeable_weight_kg"]), Decimal("9.000"))
        self.assertEqual(parcel["measured_on"], "actual_weight")

    def test_the_basis_and_charge_are_frozen_on_each_parcel(self):
        order = self._order()
        parcel = order.packages.get(recipient_name="Brian Kiptoo")
        self.assertEqual(Decimal(parcel.chargeable_weight_kg), Decimal("3.000"))
        self.assertEqual(parcel.agreed_charge_minor, 600)
        self.assertEqual(
            parcel.pricing_snapshot["rate_card_name"], self.card.name
        )
        self.assertEqual(
            parcel.pricing_snapshot["volumetric_divisor_cm3_per_kg"], 4000
        )


class WeightBandTariffTestCase(CourierFixture):
    """Invariants 6 and 7: each parcel is priced on the band that covers it, and
    an uncovered parcel is refused rather than guessed."""

    def test_each_parcel_is_priced_on_its_own_band(self):
        quote = self._quote()
        amounts = [p["amount_minor"] for p in quote.request_payload["parcels"]]
        self.assertEqual(amounts, [300, 600, 1_200])

    def test_the_order_total_is_exactly_the_sum_of_the_parcels(self):
        quote = self._quote()
        self.assertEqual(
            quote.total_minor, sum(p["amount_minor"] for p in quote.request_payload["parcels"])
        )
        order = self._quote()
        order.accept()
        booked = book_courier_order(quote=order, organization=self.org, sender=self.sender)
        self.assertEqual(
            booked.agreed_total_minor,
            sum(p.agreed_charge_minor for p in booked.packages.all()),
        )

    def test_a_parcel_above_every_band_is_refused(self):
        # Bands stop at 5 kg upwards; 50 kg is above the open-ended one.
        card = self._rate_card(name="Capped card")
        RateLine.objects.filter(rate_card=card, from_value=5).delete()
        RateLine.objects.create(
            rate_card=card,
            line_type=RateLine.LineType.PER_KG,
            label="0-5 kg",
            from_value=0,
            to_value=5,
            amount_minor=600,
        )
        with self.assertRaises(GoodsError) as caught:
            self._quote(rate_card=card)
        self.assertIn("outside every weight band", str(caught.exception))

    def test_a_light_parcel_costs_the_lowest_band(self):
        quote = self._quote(
            packages=[
                self._parcel(
                    "Achieng Otieno", self.market, weight=0.2, length_cm=5, width_cm=5, height_cm=5
                )
            ]
        )
        self.assertEqual(quote.request_payload["parcels"][0]["amount_minor"], 300)


class RateCardResolutionTestCase(CourierFixture):
    """Invariants 8 and 9: the account's own card wins, no card means no quote,
    and a later edit rewrites no booked money."""

    def test_the_accounts_own_card_wins_over_the_platform_card(self):
        own = self._rate_card(organization=self.org, name="Swift contract")
        RateLine.objects.filter(rate_card=own).update(amount_minor=999)
        quote = self._quote()
        self.assertEqual(quote.rate_card, own)
        self.assertEqual(quote.request_payload["parcels"][0]["amount_minor"], 999)

    def test_the_platform_card_is_used_when_the_account_has_none(self):
        quote = self._quote()
        self.assertEqual(quote.rate_card, self.card)

    def test_no_card_in_force_is_refused_with_a_reason(self):
        RateCard.objects.all().update(is_active=False)
        with self.assertRaises(GoodsError) as caught:
            self._quote()
        self.assertIn("no rate card is in force", str(caught.exception))

    def test_a_card_with_no_bands_cannot_price_a_parcel(self):
        empty = RateCard.objects.create(
            organization=self.org,
            service=self.service,
            name="Empty card",
            currency="KES",
            effective_from=TODAY - timedelta(days=1),
        )
        with self.assertRaises(GoodsError) as caught:
            self._quote(rate_card=empty)
        self.assertIn("no weight bands", str(caught.exception))

    def test_a_later_card_edit_changes_no_booked_money(self):
        order = self._order()
        RateLine.objects.filter(rate_card=self.card).update(amount_minor=1)
        order.refresh_from_db()
        self.assertEqual(order.agreed_total_minor, 2_100)
        self.assertEqual(
            sum(p.agreed_charge_minor for p in order.packages.all()), 2_100
        )

    def test_an_accepted_quote_survives_a_card_edit(self):
        quote = self._quote()
        quote.accept()
        RateLine.objects.filter(rate_card=self.card).update(amount_minor=1)
        quote.refresh_from_db()
        self.assertEqual(quote.total_minor, 2_100)


class PlanCarriesEveryDropTestCase(CourierFixture):
    """Invariant 10: the plan holds a stop for every distinct parcel drop, and a
    route of drops that overruns the clock is refused naming the overrun."""

    def test_a_stop_for_every_distinct_drop(self):
        order = self._order()
        trip = self._trip()
        plan = propose_consolidation(
            organization=self.org, trip=trip, shipments=[order], proposed_by=self.staff
        )
        self.assertEqual(plan.admitted_shipments().count(), 1)
        stops = set(plan.stops.values_list("place_id", flat=True))
        self.assertIn(self.depot.pk, stops)
        self.assertIn(self.market.pk, stops)
        self.assertIn(self.mill.pk, stops)
        self.assertIn(self.joiner.pk, stops)
        self.assertEqual(plan.stops.count(), 4)

    def test_two_parcels_for_one_place_need_one_stop(self):
        order = self._order(
            packages=[
                self._parcel("Achieng Otieno", self.market),
                self._parcel("Brian Kiptoo", self.market),
            ]
        )
        trip = self._trip()
        plan = propose_consolidation(
            organization=self.org, trip=trip, shipments=[order], proposed_by=self.staff
        )
        self.assertEqual(plan.stops.count(), 2)

    def test_a_route_of_drops_that_overruns_the_window_is_refused(self):
        # Ten drops plus the pickup need more minutes than the trip has, so the
        # trip is given a window the order's own window also fits inside: the
        # refusal being tested is the drops, not the window.
        parcels, destination = self._many_parcels(10)
        order = self._order(
            parcels,
            destination=destination,
            service_window_end=self.start + timedelta(minutes=30),
        )
        trip = self._trip(
            planned_start=self.start,
            planned_end=self.start + timedelta(minutes=30),
        )
        plan = propose_consolidation(
            organization=self.org, trip=trip, shipments=[order], proposed_by=self.staff
        )
        self.assertEqual(plan.admitted_shipments().count(), 0)
        (rejected,) = plan.shipments.all()
        self.assertFalse(rejected.accepted)
        self.assertIn(REASON_DROP_OVERLOADS_WINDOW, rejected.reason)

    def test_the_refusal_names_the_overrun(self):
        parcels, destination = self._many_parcels(10)
        order = self._order(
            parcels,
            destination=destination,
            service_window_end=self.start + timedelta(minutes=30),
        )
        trip = self._trip(
            planned_start=self.start,
            planned_end=self.start + timedelta(minutes=30),
        )
        plan = propose_consolidation(
            organization=self.org, trip=trip, shipments=[order], proposed_by=self.staff
        )
        (rejected,) = plan.shipments.all()
        self.assertIn("drops", rejected.reason)
        self.assertIn(str(DROP_SERVICE_MINUTES), rejected.reason)

    def test_a_trip_beyond_the_drivers_working_day_admits_no_drops(self):
        parcels, destination = self._many_parcels(3)
        order = self._order(
            parcels,
            destination=destination,
            service_window_end=self.start + timedelta(hours=8),
        )
        self.driver.working_hours_per_day = 4
        self.driver.save(update_fields=["working_hours_per_day"])
        trip = self._trip()
        plan = propose_consolidation(
            organization=self.org, trip=trip, shipments=[order], proposed_by=self.staff
        )
        (rejected,) = plan.shipments.all()
        self.assertIn("working hours", rejected.reason)


class PassengerAndFreightGatesTestCase(CourierFixture):
    """Invariant 11: goods never rides a passenger-service trip, and freight
    keeps its single-destination gate."""

    def test_a_courier_order_is_refused_on_a_passenger_service_trip(self):
        order = self._order()
        trip = self._trip(service=self.shuttle)
        plan = propose_consolidation(
            organization=self.org, trip=trip, shipments=[order], proposed_by=self.staff
        )
        (rejected,) = plan.shipments.all()
        self.assertFalse(rejected.accepted)
        self.assertEqual(rejected.reason, REASON_TRIP_IS_PASSENGER)

    def test_freight_keeps_its_single_destination_gate(self):
        freight = self._freight_shipment()
        trip = self._trip(destination=self.mill)
        plan = propose_consolidation(
            organization=self.org, trip=trip, shipments=[freight], proposed_by=self.staff
        )
        (rejected,) = plan.shipments.all()
        self.assertEqual(rejected.reason, REASON_WRONG_DESTINATION)

    def _freight_shipment(self):
        quote = quote_shipment(
            organization=self.org,
            service=self.freight_service,
            sender_name="Grace Wanjiku",
            sender_phone="0722000099",
            recipient_name="Market Trader",
            recipient_phone="0722000088",
            collection=self.depot,
            destination=self.market,
            total_weight_kg=100,
            total_volume_m3=0.02,
            service_window_start=self.start,
            service_window_end=self.end,
            total_minor=20_000,
        )
        quote.accept()
        return create_shipment(
            quote=quote,
            organization=self.org,
            service=self.freight_service,
            sender=self.sender,
            sender_name="Grace Wanjiku",
            sender_phone="0722000099",
            recipient_name="Market Trader",
            recipient_phone="0722000088",
            collection=self.depot,
            destination=self.market,
            service_window_start=self.start,
            service_window_end=self.end,
            packages=[{"weight_kg": 100, "length_cm": 20, "width_cm": 20, "height_cm": 20}],
        )


class MidRouteInsertionGateTestCase(CourierFixture):
    """3C inserts one consignment with one delivery end. A courier order has a
    stop per parcel drop, so it is refused by name rather than half-inserted into
    a plan that would promise fewer stops than the order owes."""

    def _running_trip_with_plan(self):
        first = self._order(
            packages=[self._parcel("Achieng Otieno", self.market)],
        )
        trip = self._trip()
        plan = propose_consolidation(
            organization=self.org, trip=trip, shipments=[first], proposed_by=self.staff
        )
        validate_plan(plan)
        plan.status = ConsolidationPlan.Status.PUBLISHED
        plan.published_at = timezone.now()
        plan.save(update_fields=["status", "published_at", "updated_at"])
        trip.started_at = timezone.now()
        trip.status = FreightTrip.Status.IN_TRANSIT
        trip.save(update_fields=["started_at", "status", "updated_at"])
        return trip

    def test_a_multi_drop_courier_order_is_refused_mid_route(self):
        trip = self._running_trip_with_plan()
        late = self._order(
            packages=self._parcels(),
            service_window_start=self.start + timedelta(hours=1),
            service_window_end=self.end,
        )
        with self.assertRaises(FreightError) as caught:
            insert_consignment(
                organization=self.org, trip=trip, shipment=late, actor=self.staff
            )
        self.assertEqual(str(caught.exception), REASON_MULTI_DROP_INSERTION)

    def test_the_refusal_leaves_the_published_plan_untouched(self):
        trip = self._running_trip_with_plan()
        before = ConsolidationStop.objects.filter(plan__trip=trip).count()
        version = trip.consolidation_plans.get(status=ConsolidationPlan.Status.PUBLISHED).version
        late = self._order(
            packages=self._parcels(),
            service_window_start=self.start + timedelta(hours=1),
            service_window_end=self.end,
        )
        with self.assertRaises(FreightError):
            insert_consignment(
                organization=self.org, trip=trip, shipment=late, actor=self.staff
            )
        published = trip.consolidation_plans.get(status=ConsolidationPlan.Status.PUBLISHED)
        self.assertEqual(published.version, version)
        self.assertEqual(ConsolidationStop.objects.filter(plan__trip=trip).count(), before)

    def test_an_offer_for_a_multi_drop_order_is_refused_too(self):
        trip = self._running_trip_with_plan()
        late = self._order(
            packages=self._parcels(),
            service_window_start=self.start + timedelta(hours=1),
            service_window_end=self.end,
        )
        with self.assertRaises(FreightError) as caught:
            request_insertion(
                organization=self.org,
                trip=trip,
                shipment=late,
                requested_by=self.staff,
            )
        self.assertEqual(str(caught.exception), REASON_MULTI_DROP_INSERTION)
        self.assertFalse(InsertionRequest.objects.filter(trip=trip).exists())


class PartialDeliveryTestCase(CourierFixture):
    """Invariant 12: one parcel delivered leaves the rest visible."""

    def test_one_delivery_makes_the_order_partially_delivered(self):
        order = self._order()
        first = order.packages.get(recipient_name="Achieng Otieno")
        self._deliver(first)
        order.refresh_from_db()
        self.assertEqual(order.status, Shipment.Status.PARTIALLY_DELIVERED)
        self.assertEqual(
            order.packages.filter(status=Package.Status.PENDING).count(), 2
        )

    def test_delivering_every_parcel_closes_the_order(self):
        order = self._order()
        for package in list(order.packages.all()):
            self._deliver(package)
        order.refresh_from_db()
        self.assertEqual(order.status, Shipment.Status.DELIVERED)
        self.assertEqual(
            order.packages.filter(status=Package.Status.DELIVERED).count(), 3
        )


class ShipmentOrderLinkTestCase(CourierFixture):
    """H5a: a fully delivered shipment settles the order it fulfils.

    Before the fix a shipment could not name the order it delivered, so the
    money it collected had no way to leave unearned revenue."""

    def _linked_order(self, shipment):
        order = Order.objects.create(
            organization=self.org,
            customer=self.sender,
            service=self.service,
            quote=shipment.quote,
            currency=shipment.quote.currency,
            total_minor=shipment.quote.total_minor,
        )
        order.confirm()
        shipment.order = order
        shipment.save(update_fields=["order", "updated_at"])
        return order

    def test_delivering_every_parcel_completes_the_linked_order(self):
        shipment = self._order()
        order = self._linked_order(shipment)
        for package in list(shipment.packages.all()):
            self._deliver(package)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.COMPLETED)

    def test_a_partial_delivery_leaves_the_order_confirmed(self):
        shipment = self._order()
        order = self._linked_order(shipment)
        self._deliver(shipment.packages.first())
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.CONFIRMED)

    def test_the_delivery_event_names_the_order(self):
        shipment = self._order()
        order = self._linked_order(shipment)
        self._deliver(shipment.packages.first())
        event = OutboxEvent.objects.filter(
            event_type="goods.courier_parcel_delivered"
        ).first()
        self.assertEqual(event.payload["order_id"], str(order.pk))

    def test_a_shipment_without_an_order_delivers_without_settling(self):
        shipment = self._order()
        for package in list(shipment.packages.all()):
            self._deliver(package)
        shipment.refresh_from_db()
        self.assertEqual(shipment.status, Shipment.Status.DELIVERED)
        event = OutboxEvent.objects.filter(
            event_type="goods.courier_parcel_delivered"
        ).first()
        self.assertIsNone(event.payload["order_id"])


class DeliveryProofTestCase(CourierFixture):
    """Invariants 13 and 14: proof is per parcel, to the named recipient, and
    only for a parcel that was actually delivered."""

    def test_delivering_a_parcel_twice_is_refused(self):
        order = self._order()
        package = order.packages.get(recipient_name="Achieng Otieno")
        self._deliver(package)
        with self.assertRaises(GoodsError) as caught:
            self._deliver(package)
        self.assertIn("already been delivered", str(caught.exception))

    def test_a_photo_alone_does_not_prove_delivery(self):
        order = self._order()
        package = order.packages.get(recipient_name="Achieng Otieno")
        with self.assertRaises(GoodsError) as caught:
            self._deliver(
                package,
                recipient_id_type="",
                recipient_id_last4="",
                photos=["https://files.example/parcel.jpg"],
            )
        self.assertIn("recipient verification", str(caught.exception))
        package.refresh_from_db()
        self.assertEqual(package.status, Package.Status.PENDING)

    def test_proof_from_someone_else_is_refused(self):
        order = self._order()
        package = order.packages.get(recipient_name="Achieng Otieno")
        with self.assertRaises(GoodsError) as caught:
            self._deliver(package, recipient_name="Brian Kiptoo")
        self.assertIn("addressed to Achieng Otieno", str(caught.exception))

    def test_evidence_for_an_undelivered_parcel_is_refused(self):
        order = self._order()
        package = order.packages.get(recipient_name="Achieng Otieno")
        with self.assertRaises(GoodsError) as caught:
            record_evidence(
                order,
                kind=ShipmentEvidence.Kind.DELIVERY,
                package=package,
                recipient_name="Achieng Otieno",
                recipient_id_type="national_id",
                recipient_id_last4="4321",
                recorded_by=self.staff,
            )
        self.assertIn("not marked delivered", str(caught.exception))

    def test_order_level_evidence_must_name_the_parcel(self):
        order = self._order()
        with self.assertRaises(GoodsError) as caught:
            record_evidence(
                order,
                kind=ShipmentEvidence.Kind.DELIVERY,
                recipient_name="Achieng Otieno",
                recipient_id_type="national_id",
                recipient_id_last4="4321",
                recorded_by=self.staff,
            )
        self.assertIn("must name the parcel", str(caught.exception))

    def test_the_delivery_record_belongs_to_the_parcel(self):
        order = self._order()
        package = order.packages.get(recipient_name="Carol Mwangi")
        self._deliver(package)
        evidence = ShipmentEvidence.objects.get(package=package)
        self.assertEqual(evidence.kind, ShipmentEvidence.Kind.DELIVERY)
        self.assertTrue(evidence.is_recipient_verified())
        self.assertEqual(evidence.recipient_name, "Carol Mwangi")


class FailedAttemptTestCase(CourierFixture):
    """Invariant 15: a failed attempt is an exception against the parcel, and
    the parcel is not delivered by being failed at."""

    def test_a_failed_attempt_raises_an_exception_against_the_parcel(self):
        order = self._order()
        package = order.packages.get(recipient_name="Brian Kiptoo")
        exception = fail_delivery_attempt(
            package,
            kind=ShipmentException.Kind.ABSENT,
            next_action=ShipmentException.NextAction.RETRY,
            detail="Nobody at the gate",
            raised_by=self.staff,
        )
        self.assertEqual(exception.package, package)
        self.assertEqual(exception.status, ShipmentException.Status.OPEN)
        package.refresh_from_db()
        self.assertNotEqual(package.status, Package.Status.DELIVERED)

    def test_the_order_shows_the_open_exception_while_the_parcel_is_outstanding(self):
        order = self._order()
        package = order.packages.get(recipient_name="Brian Kiptoo")
        fail_delivery_attempt(
            package,
            kind=ShipmentException.Kind.ABSENT,
            next_action=ShipmentException.NextAction.RETRY,
            raised_by=self.staff,
        )
        order.refresh_from_db()
        self.assertEqual(order.status, Shipment.Status.EXCEPTION)
        self.assertEqual(
            order.packages.filter(status=Package.Status.PENDING).count(), 3
        )


class ReturnAfterFailedAttemptTestCase(CourierFixture):
    """Invariant 16: one failed parcel comes back from its own drop on its own,
    and the rest of the order is untouched."""

    def test_a_failed_parcel_is_returned_from_its_own_drop(self):
        order = self._order()
        package = order.packages.get(recipient_name="Brian Kiptoo")
        fail_delivery_attempt(
            package,
            kind=ShipmentException.Kind.ABSENT,
            next_action=ShipmentException.NextAction.RETURN,
            raised_by=self.staff,
        )
        returned = create_return_shipment(order, packages=[package], reason="Recipient absent")
        self.assertTrue(returned.is_return)
        self.assertEqual(returned.return_of, order)
        self.assertEqual(returned.collection, self.mill)
        self.assertEqual(returned.destination, self.depot)
        self.assertEqual(returned.recipient_name, "Grace Wanjiku")
        self.assertEqual(returned.legs.count(), 2)
        package.refresh_from_db()
        self.assertEqual(package.status, Package.Status.RETURNED)

    def test_the_rest_of_the_order_is_left_alone(self):
        order = self._order()
        package = order.packages.get(recipient_name="Brian Kiptoo")
        fail_delivery_attempt(
            package,
            kind=ShipmentException.Kind.ABSENT,
            next_action=ShipmentException.NextAction.RETURN,
            raised_by=self.staff,
        )
        create_return_shipment(order, packages=[package], reason="Recipient absent")
        self.assertEqual(
            order.packages.filter(status=Package.Status.PENDING).count(), 2
        )
        other = order.packages.get(recipient_name="Achieng Otieno")
        self._deliver(other)
        order.refresh_from_db()
        self.assertEqual(order.status, Shipment.Status.PARTIALLY_DELIVERED)
        self.assertEqual(order.exceptions.filter(status=ShipmentException.Status.OPEN).count(), 0)

    def test_a_sibling_with_no_failure_of_its_own_cannot_be_returned(self):
        order = self._order()
        failed = order.packages.get(recipient_name="Brian Kiptoo")
        sibling = order.packages.get(recipient_name="Carol Mwangi")
        fail_delivery_attempt(
            failed,
            kind=ShipmentException.Kind.ABSENT,
            next_action=ShipmentException.NextAction.RETURN,
            raised_by=self.staff,
        )
        with self.assertRaises(GoodsError):
            create_return_shipment(order, packages=[failed, sibling], reason="Both")

    def test_returning_the_last_outstanding_parcel_refreshes_the_order(self):
        # Three parcels, one failure. Two are delivered, one is sent back: every
        # parcel is now settled, so the order must not sit at "exception" because
        # the return closed the only problem on it.
        order = self._order()
        package = order.packages.get(recipient_name="Brian Kiptoo")
        for name in ("Achieng Otieno", "Carol Mwangi"):
            self._deliver(order.packages.get(recipient_name=name))
        fail_delivery_attempt(
            package,
            kind=ShipmentException.Kind.ABSENT,
            next_action=ShipmentException.NextAction.RETURN,
            raised_by=self.staff,
        )
        order.refresh_from_db()
        self.assertEqual(order.status, Shipment.Status.EXCEPTION)
        create_return_shipment(order, packages=[package], reason="Recipient absent")
        order.refresh_from_db()
        self.assertEqual(order.status, Shipment.Status.DELIVERED)
        self.assertEqual(order.exceptions.filter(status=ShipmentException.Status.OPEN).count(), 0)

    def test_a_return_does_not_settle_an_unrelated_claim(self):
        # A return answers "where is the parcel", not "who pays for it". A claim
        # raised against the same parcel stays open for someone to work.
        order = self._order()
        package = order.packages.get(recipient_name="Brian Kiptoo")
        fail_delivery_attempt(
            package,
            kind=ShipmentException.Kind.ABSENT,
            next_action=ShipmentException.NextAction.RETURN,
            raised_by=self.staff,
        )
        claim = ShipmentException.objects.create(
            organization=self.org,
            shipment=order,
            package=package,
            kind=ShipmentException.Kind.DAMAGED,
            next_action=ShipmentException.NextAction.CLAIM,
            status=ShipmentException.Status.OPEN,
            detail="Box was soaked",
            raised_by=self.staff,
        )
        create_return_shipment(order, packages=[package], reason="Recipient absent")
        claim.refresh_from_db()
        self.assertEqual(claim.status, ShipmentException.Status.OPEN)
        order.refresh_from_db()
        self.assertEqual(order.status, Shipment.Status.EXCEPTION)

    def test_a_returned_freight_parcel_is_not_read_as_a_courier_drop(self):
        freight = self._freight_shipment()
        package = freight.packages.get()
        package.status = Package.Status.DELIVERED
        package.save(update_fields=["status", "updated_at"])
        returned = create_return_shipment(freight, packages=[package], reason="Wrong goods")
        self.assertIsNone(returned.packages.get().recipient_place)
        self.assertFalse(returned.is_multi_drop())

    def _freight_shipment(self):
        quote = quote_shipment(
            organization=self.org,
            service=self.freight_service,
            sender_name="Grace Wanjiku",
            sender_phone="0722000099",
            recipient_name="Market Trader",
            recipient_phone="0722000088",
            collection=self.depot,
            destination=self.market,
            total_weight_kg=100,
            total_volume_m3=0.02,
            service_window_start=self.start,
            service_window_end=self.end,
            total_minor=20_000,
        )
        quote.accept()
        return create_shipment(
            quote=quote,
            organization=self.org,
            service=self.freight_service,
            sender=self.sender,
            sender_name="Grace Wanjiku",
            sender_phone="0722000099",
            recipient_name="Market Trader",
            recipient_phone="0722000088",
            collection=self.depot,
            destination=self.market,
            service_window_start=self.start,
            service_window_end=self.end,
            packages=[{"weight_kg": 100, "length_cm": 20, "width_cm": 20, "height_cm": 20}],
        )


class CapacityAndIsolationTestCase(CourierFixture):
    """Invariant 17: every parcel counts against the cargo ledger, and courier
    orders are organization-scoped."""

    def test_every_parcel_counts_against_cargo_capacity(self):
        order = self._order()
        trip = self._trip()
        allowance = cargo_capacity(trip)
        # Every parcel of the order is on the ledger, so the whole order weighs
        # more than any single parcel does.
        self.assertGreater(
            order.total_weight_kg,
            order.packages.order_by("-weight_kg").first().weight_kg,
        )
        self.assertEqual(allowance["capacity_weight_kg"], Decimal("900"))
        self.assertGreaterEqual(
            allowance["free_weight_kg"], order.total_weight_kg
        )

    def test_a_courier_order_refuses_a_van_that_cannot_hold_it(self):
        order = self._order()
        self.van.cargo_weight_kg = Decimal("1")
        self.van.save(update_fields=["cargo_weight_kg"])
        trip = self._trip()
        plan = propose_consolidation(
            organization=self.org, trip=trip, shipments=[order], proposed_by=self.staff
        )
        self.assertEqual(plan.admitted_shipments().count(), 0)
        (rejected,) = plan.shipments.all()
        self.assertIn("cargo weight", rejected.reason)

    def test_another_organizations_courier_order_is_refused(self):
        rival = Organization.objects.create(name="Rival Two", slug="rival-two")
        depot = Place.objects.create(
            organization=rival, name="Rival depot", place_type=Place.PlaceType.BRANCH
        )
        drop = Place.objects.create(
            organization=rival, name="Rival drop", place_type=Place.PlaceType.BRANCH
        )
        their_card = RateCard.objects.create(
            organization=rival,
            service=self.service,
            name="Rival card",
            currency="KES",
            effective_from=TODAY - timedelta(days=1),
        )
        RateLine.objects.create(
            rate_card=their_card,
            line_type=RateLine.LineType.PER_KG,
            from_value=0,
            to_value=None,
            amount_minor=500,
        )
        quote = quote_courier_order(
            organization=rival,
            service=self.service,
            collection=depot,
            destination=drop,
            packages=[
                {
                    "recipient_name": "Rival Recipient",
                    "recipient_phone": "0722000099",
                    "recipient_place": str(drop.pk),
                    "weight_kg": 1,
                    "length_cm": 10,
                    "width_cm": 10,
                    "height_cm": 10,
                }
            ],
            service_window_start=self.start,
            service_window_end=self.end,
        )
        quote.accept()
        theirs = book_courier_order(quote=quote, organization=rival, sender=self.sender)
        trip = self._trip()
        plan = propose_consolidation(
            organization=self.org, trip=trip, shipments=[theirs], proposed_by=self.staff
        )
        self.assertEqual(plan.admitted_shipments().count(), 0)
        (rejected,) = plan.shipments.all()
        self.assertIn("another organization", rejected.reason)


class CourierApiTestCase(CourierFixture):
    """The courier loop over HTTP: quote, book, deliver, return."""

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.client.force_authenticate(self.staff)

    def _parcel_body(self, name, place, weight=0.8):
        return {
            "recipient_name": name,
            "recipient_phone": "0722000001",
            "recipient_place": str(place.pk),
            "description": "Books",
            "weight_kg": weight,
            "length_cm": 20,
            "width_cm": 15,
            "height_cm": 10,
        }

    def _quote_body(self):
        return {
            "service": str(self.service.pk),
            "collection": str(self.depot.pk),
            "destination": str(self.market.pk),
            "sender_name": "Grace Wanjiku",
            "sender_phone": "0722000099",
            "service_window_start": self.start.isoformat(),
            "service_window_end": self.end.isoformat(),
            "parcels": [
                self._parcel_body("Achieng Otieno", self.market),
                self._parcel_body("Brian Kiptoo", self.mill, 3.0),
            ],
        }

    def test_quote_returns_the_per_parcel_breakdown(self):
        response = self.client.post(
            "/api/v1/shipments/courier-quote/", self._quote_body(), format="json"
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["total_minor"], 900)
        self.assertEqual([p["amount_minor"] for p in response.data["parcels"]], [300, 600])
        self.assertEqual(response.data["basis"], MeasurementBasis.CHARGEABLE_WEIGHT)

    def test_booking_a_courier_quote_creates_one_pickup_and_many_parcels(self):
        quote = self.client.post(
            "/api/v1/shipments/courier-quote/", self._quote_body(), format="json"
        ).data
        response = self.client.post(
            "/api/v1/shipments/", {"quote": str(quote["quote"])}, format="json"
        )
        self.assertEqual(response.status_code, 201, response.data)
        order = Shipment.objects.get(pk=response.data["id"])
        self.assertEqual(order.package_count, 2)
        self.assertTrue(response.data["is_multi_drop"])
        self.assertEqual(
            sorted(response.data["drop_place_names"]), ["Market", "Mill"]
        )

    def test_two_parcels_for_one_place_list_that_place_once(self):
        order = self._order(
            packages=[
                self._parcel("Achieng Otieno", self.market),
                self._parcel("Brian Kiptoo", self.market),
                self._parcel("Carol Mwangi", self.mill),
            ]
        )
        response = self.client.get(f"/api/v1/shipments/{order.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            sorted(response.data["drop_place_names"]), ["Market", "Mill"]
        )

    def test_a_courier_quote_must_be_accepted_before_booking(self):
        quote = self._quote()
        with self.assertRaises(GoodsError):
            book_courier_order(quote=quote, organization=self.org, sender=self.sender)

    def test_delivering_through_the_api_needs_the_named_recipient(self):
        order = self._order()
        package = order.packages.get(recipient_name="Achieng Otieno")
        body = {
            "recipient_name": "Brian Kiptoo",
            "recipient_id_type": "national_id",
            "recipient_id_last4": "4321",
            "photos": ["https://files.example/parcel.jpg"],
        }
        response = self.client.post(
            f"/api/v1/packages/{package.pk}/deliver/", body, format="json"
        )
        self.assertEqual(response.status_code, 400)
        package.refresh_from_db()
        self.assertEqual(package.status, Package.Status.PENDING)

        body["recipient_name"] = "Achieng Otieno"
        response = self.client.post(
            f"/api/v1/packages/{package.pk}/deliver/", body, format="json"
        )
        self.assertEqual(response.status_code, 200, response.data)
        package.refresh_from_db()
        self.assertEqual(package.status, Package.Status.DELIVERED)
        order.refresh_from_db()
        self.assertEqual(order.status, Shipment.Status.PARTIALLY_DELIVERED)

    def test_a_failed_attempt_can_be_raised_through_the_api(self):
        order = self._order()
        package = order.packages.get(recipient_name="Brian Kiptoo")
        response = self.client.post(
            f"/api/v1/packages/{package.pk}/failed-attempt/",
            {
                "kind": ShipmentException.Kind.ABSENT,
                "next_action": ShipmentException.NextAction.RETURN,
                "detail": "Nobody home",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(ShipmentException.objects.filter(package=package).count(), 1)

    def test_another_organizations_order_is_not_reachable(self):
        order = self._order()
        response = APIClient().get(f"/api/v1/shipments/{order.pk}/")
        self.assertEqual(response.status_code, 401)

    def test_the_trips_own_driver_may_record_the_handover(self):
        order = self._order()
        trip = self._trip()
        plan = propose_consolidation(
            organization=self.org, trip=trip, shipments=[order], proposed_by=self.staff
        )
        validate_plan(plan)
        publish_plan(plan, reason="Loaded")
        package = order.packages.get(recipient_name="Achieng Otieno")
        client = APIClient()
        client.force_authenticate(self.driver.user)
        response = client.post(
            f"/api/v1/packages/{package.pk}/deliver/",
            {
                "recipient_name": "Achieng Otieno",
                "recipient_id_type": "national_id",
                "recipient_id_last4": "4321",
                "photos": ["https://files.example/parcel.jpg"],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        package.refresh_from_db()
        self.assertEqual(package.status, Package.Status.DELIVERED)

    def test_a_driver_of_another_trip_cannot_deliver_this_parcel(self):
        order = self._order()
        self._trip()
        package = order.packages.get(recipient_name="Achieng Otieno")
        other_driver = Driver.objects.create(
            user=User.objects.create_user("0744000099", "pass-D-5678"),
            organization=self.org,
            status=Driver.Status.APPROVED,
            working_hours_per_day=8,
        )
        Membership.objects.create(
            user=other_driver.user, organization=self.org, role=Role.DRIVER
        )
        client = APIClient()
        client.force_authenticate(other_driver.user)
        response = client.post(
            f"/api/v1/packages/{package.pk}/deliver/",
            {
                "recipient_name": "Achieng Otieno",
                "photos": ["https://files.example/parcel.jpg"],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 403)
        package.refresh_from_db()
        self.assertEqual(package.status, Package.Status.PENDING)

    def test_a_malformed_place_id_is_a_bad_request_not_a_crash(self):
        body = self._quote_body()
        body["collection"] = "not-a-uuid"
        response = self.client.post(
            "/api/v1/shipments/courier-quote/", body, format="json"
        )
        self.assertEqual(response.status_code, 400, response.data)

    def test_a_resolved_failure_lets_the_order_close(self):
        order = self._order(packages=[self._parcel("Achieng Otieno", self.market)])
        package = order.packages.get()
        exception = fail_delivery_attempt(
            package,
            kind=ShipmentException.Kind.ABSENT,
            next_action=ShipmentException.NextAction.RETRY,
            raised_by=self.staff,
        )
        resolve_exception(exception, note="Delivered on the second attempt")
        self._deliver(package)
        order.refresh_from_db()
        self.assertEqual(order.status, Shipment.Status.DELIVERED)
