"""Mid-route insertion, once the driver has begun the route (blueprint 11).

"Once a driver begins a route, preserve completed stops and communicate any
remaining-stop changes clearly. Do not automatically reorder scheduled
passenger stops for parcel convenience."

The product decisions under test, from ``docs/phase3c-scope.md``: an insertion
may push an already-promised stop by up to the operator's tolerance and is
refused beyond it; a dispatcher may insert directly and a driver may accept an
offer; the inserted consignment keeps its own frozen price for the remaining leg.
"""

from datetime import datetime, timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import Membership, Organization, Role, User
from apps.catalog.models import Place, Service
from apps.common.models import OutboxEvent
from apps.fleet.models import CargoCompartment, Vehicle
from apps.goods.insertion import (
    MAX_TOLERANCE_MINUTES,
    REASON_ALREADY_DECIDED,
    REASON_ALREADY_INSERTED,
    REASON_ALREADY_SERVED,
    REASON_NO_REMAINING_STOPS,
    REASON_OTHER_DRIVER,
    REASON_TOO_HEAVY,
    REASON_TOLERANCE_EXCEEDED,
    REASON_TRIP_NOT_RUNNING,
    REASON_WINDOW_PASSED,
    REASON_WRONG_DESTINATION,
    FreightError,
    accept_insertion,
    amend_manifest,
    complete_stop,
    current_plan,
    decline_insertion,
    insert_consignment,
    request_insertion,
    route_state,
)
from apps.goods.models import (
    ConsolidationPlan,
    ConsolidationStop,
    InsertionRequest,
    LoadType,
    Manifest,
    ManifestLine,
    Shipment,
)
from apps.goods.services import (
    build_manifest,
    create_freight_trip,
    create_shipment,
    quote_shipment,
    reconcile_manifest,
    release_manifest,
)
from apps.goods.consolidation import assess_coverage, confirm_departure


class RunningTripFixture(TestCase):
    """A truck that left the depot with two consignments and is halfway along."""

    def setUp(self):
        self.org = Organization.objects.create(
            name="Inserters", slug="inserters", insertion_grace_minutes=30
        )
        self.other_org = Organization.objects.create(
            name="Bystanders", slug="bystanders"
        )
        self.dispatcher = User.objects.create_user("0744000011", "pass-D-1234")
        self.driver_user = User.objects.create_user("0744000022", "pass-R-1234")
        self.shipper_a = User.objects.create_user("0744000033", "pass-A-1234")
        self.shipper_b = User.objects.create_user("0744000044", "pass-B-1234")
        for user, role in ((self.dispatcher, Role.DISPATCHER), (self.driver_user, Role.DRIVER)):
            Membership.objects.create(user=user, organization=self.org, role=role)

        self.service = Service.objects.create(
            slug="line-haul", name="Line haul", service_type=Service.ServiceType.FREIGHT
        )
        self.farm = Place.objects.create(
            organization=self.org, name="Farm", place_type=Place.PlaceType.BRANCH
        )
        self.roadside = Place.objects.create(
            organization=self.org, name="Roadside depot", place_type=Place.PlaceType.BRANCH
        )
        self.market = Place.objects.create(
            organization=self.org, name="Market", place_type=Place.PlaceType.HUB
        )
        self.truck = Vehicle.objects.create(
            organization=self.org,
            registration_number="KTR900A",
            vehicle_class=Vehicle.VehicleClass.TRUCK,
            cargo_weight_kg=1000,
            cargo_volume_m3=15.0,
        )
        CargoCompartment.objects.create(
            organization=self.org,
            vehicle=self.truck,
            name="Bed",
            max_weight_kg=1000,
            max_volume_m3=15.0,
        )
        from apps.fleet.models import Driver

        self.driver = Driver.objects.create(
            user=self.driver_user,
            organization=self.org,
            status=Driver.Status.APPROVED,
            working_hours_per_day=10,
        )

        self.start = timezone.make_aware(datetime(2027, 3, 1, 8, 0))
        self.end = timezone.make_aware(datetime(2027, 3, 1, 16, 0))

    def _shipment(
        self,
        name="Shipper",
        weight=200,
        total=40_000,
        collection=None,
        window_start=None,
        window_end=None,
        sender=None,
        organization=None,
    ):
        org = organization or self.org
        collection = collection or self.farm
        window_start = window_start or self.start
        window_end = window_end or self.end
        quote = quote_shipment(
            organization=org,
            service=self.service,
            sender_name=name,
            sender_phone="0744000099",
            recipient_name="Market Trader",
            recipient_phone="0744000088",
            collection=collection,
            destination=self.market,
            total_weight_kg=weight,
            total_volume_m3=1.0,
            service_window_start=window_start,
            service_window_end=window_end,
            total_minor=total,
        )
        quote.accept()
        return create_shipment(
            quote=quote,
            organization=org,
            service=self.service,
            sender=sender,
            sender_name=name,
            sender_phone="0744000099",
            recipient_name="Market Trader",
            recipient_phone="0744000088",
            collection=collection,
            destination=self.market,
            service_window_start=window_start,
            service_window_end=window_end,
            packages=[
                {"weight_kg": weight, "length_cm": 50, "width_cm": 50, "height_cm": 40}
            ],
        )

    def _running_trip(self, *, extra_shipment=None, collection=None, window_end=None):
        """A departed trip with a published plan, a released manifest and one
        completed stop."""
        a = self._shipment("A", weight=200, total=40_000, sender=self.shipper_a)
        b = self._shipment(
            "B",
            weight=300,
            total=55_000,
            sender=self.shipper_b,
            window_end=window_end,
        )
        trip = create_freight_trip(
            organization=self.org,
            vehicle=self.truck,
            driver=self.driver,
            service=self.service,
            origin=self.farm,
            destination=self.market,
            planned_start=self.start,
            planned_end=self.end,
        )
        trip.trip_cost_minor = 50_000
        trip.save(update_fields=["trip_cost_minor"])
        manifest = build_manifest(
            trip=trip, packages=[*a.packages.all(), *b.packages.all()]
        )
        reconcile_manifest(
            manifest=manifest,
            scanned={line.label: ManifestLine.ScanState.SCANNED for line in manifest.lines.all()},
        )
        release_manifest(manifest=manifest)
        from apps.goods.consolidation import propose_consolidation, publish_plan, validate_plan

        plan = validate_plan(
            propose_consolidation(
                organization=self.org,
                trip=trip,
                shipments=[a, b],
                proposed_by=self.dispatcher,
            )
        )
        if plan.status != ConsolidationPlan.Status.VALIDATED:
            self.fail(f"fixture plan rejected: {plan.explanation}")
        plan = publish_plan(plan)
        confirm_departure(trip)
        # The collection stop is served: the fact that must never move again.
        first = plan.stops.order_by("sequence").first()
        complete_stop(plan=plan, stop=first, actor=self.driver_user)
        return trip, plan, [a, b]

    def _tight_couple(self, plan):
        """A long stop at the market, and a stop behind it whose promise is
        already tight. An insertion between them cannot be served without
        breaking the second one."""
        market = plan.stops.filter(is_completed=False).order_by("sequence").last()
        market.window_start = self.start + timedelta(hours=4)
        market.window_end = self.start + timedelta(hours=5)
        market.save(update_fields=["window_start", "window_end"])
        depot = Place.objects.create(
            organization=self.org, name="Crossroads", place_type=Place.PlaceType.BRANCH
        )
        ConsolidationStop.objects.create(
            organization=self.org,
            plan=plan,
            place=depot,
            sequence=market.sequence + 1,
            window_start=self.start + timedelta(hours=4, minutes=30),
            window_end=self.start + timedelta(hours=4, minutes=35),
        )
        return self.start + timedelta(hours=4, minutes=5)


class CompletedStopPreservationTestCase(RunningTripFixture):
    """Invariant 1: a completed stop is byte-identical in the next version."""

    def test_the_completed_stop_is_carried_across_untouched(self):
        trip, plan, _ = self._running_trip()
        completed_before = plan.stops.filter(is_completed=True).get()
        inserted = self._shipment("C", weight=100, total=20_000, collection=self.roadside)

        new_plan = insert_considential = insert_consignment(
            organization=self.org,
            trip=trip,
            shipment=inserted,
            actor=self.dispatcher,
            reason="Farmer asked at the gate",
        )
        completed_after = new_plan.stops.filter(is_completed=True).get()
        self.assertEqual(completed_after.place_id, completed_before.place_id)
        self.assertEqual(completed_after.sequence, completed_before.sequence)
        self.assertEqual(completed_after.window_start, completed_before.window_start)
        self.assertEqual(completed_after.window_end, completed_before.window_end)
        self.assertIsNotNone(completed_after.completed_at)

    def test_completing_a_stop_cannot_be_undone(self):
        trip, plan, _ = self._running_trip()
        stop = plan.stops.filter(is_completed=True).get()
        again = complete_stop(plan=plan, stop=stop, actor=self.dispatcher)
        self.assertTrue(again.is_completed)
        first_completion = stop.completed_at
        self.assertEqual(again.completed_at, first_completion)

    def test_a_stop_cannot_be_completed_before_departure(self):
        trip, plan, _ = self._running_trip()
        trip.started_at = None
        trip.save(update_fields=["started_at"])
        with self.assertRaises(FreightError):
            complete_stop(plan=plan, stop=plan.stops.filter(is_completed=False).first())


class RemainingStopOrderTestCase(RunningTripFixture):
    """Invariant 2: existing remaining stops keep their order; the new stop is
    added by its own window."""

    def test_existing_remaining_stops_keep_their_relative_order(self):
        trip, plan, _ = self._running_trip()
        before = [s.place_id for s in plan.stops.filter(is_completed=False).order_by("sequence")]
        inserted = self._shipment("C", weight=100, total=20_000, collection=self.roadside)
        new_plan = insert_consignment(
            organization=self.org, trip=trip, shipment=inserted, actor=self.dispatcher
        )
        after = [s.place_id for s in new_plan.stops.filter(is_completed=False).order_by("sequence")]
        self.assertEqual(after, before + [inserted.collection_id])

    def test_the_loading_order_agrees_with_the_new_route(self):
        trip, plan, _ = self._running_trip()
        inserted = self._shipment("C", weight=100, total=20_000, collection=self.roadside)
        new_plan = insert_consignment(
            organization=self.org, trip=trip, shipment=inserted, actor=self.dispatcher
        )
        order = list(
            new_plan.shipments.filter(accepted=True)
            .order_by("sequence")
            .values_list("shipment_id", flat=True)
        )
        # The two on board were collected at the farm before the truck left, so
        # the pallet taken on later is loaded after them, and the loading order
        # is dense rather than repeating a number.
        self.assertEqual(order[-1], inserted.pk)
        self.assertEqual(len(order), 3)
        self.assertEqual(
            new_plan.shipments.filter(accepted=True).count(),
            new_plan.shipments.filter(accepted=True).values("sequence").distinct().count(),
        )

    def test_the_version_chain_records_the_change(self):
        trip, plan, _ = self._running_trip()
        inserted = self._shipment("C", weight=100, total=20_000, collection=self.roadside)
        new_plan = insert_consignment(
            organization=self.org,
            trip=trip,
            shipment=inserted,
            actor=self.dispatcher,
            reason="Farmer asked at the gate",
        )
        self.assertEqual(new_plan.version, plan.version + 1)
        self.assertEqual(new_plan.supersedes_id, plan.pk)
        self.assertEqual(new_plan.status, ConsolidationPlan.Status.PUBLISHED)
        self.assertEqual(new_plan.change_reason, "Farmer asked at the gate")
        plan.refresh_from_db()
        self.assertEqual(plan.status, ConsolidationPlan.Status.SUPERSEDED)
        self.assertEqual(current_plan(trip).pk, new_plan.pk)


class DelayToleranceTestCase(RunningTripFixture):
    """Invariant 3: within tolerance yes, beyond tolerance no, and the refusal
    explains itself."""

    def test_an_insertion_within_tolerance_is_published(self):
        trip, plan, _ = self._running_trip()
        # A generous remaining window leaves slack for the new stop's 20 minutes.
        inserted = self._shipment(
            "C", weight=100, total=20_000, collection=self.roadside, window_end=self.end
        )
        new_plan = insert_consignment(
            organization=self.org, trip=trip, shipment=inserted, actor=self.dispatcher
        )
        self.assertEqual(new_plan.status, ConsolidationPlan.Status.PUBLISHED)
        self.assertEqual(new_plan.shipments.filter(accepted=True).count(), 3)

    def test_an_insertion_beyond_tolerance_is_refused(self):
        trip, plan, _ = self._running_trip()
        window_start = self._tight_couple(plan)
        inserted = self._shipment(
            "C",
            weight=100,
            total=20_000,
            collection=self.roadside,
            window_start=window_start,
        )
        with self.assertRaises(FreightError) as ctx:
            insert_consignment(
                organization=self.org, trip=trip, shipment=inserted, actor=self.dispatcher
            )
        self.assertIn(REASON_TOLERANCE_EXCEEDED, str(ctx.exception))
        # Nothing was published.
        self.assertEqual(current_plan(trip).pk, plan.pk)

    def test_the_tolerance_is_the_organizations_own(self):
        trip, plan, _ = self._running_trip()
        window_start = self._tight_couple(plan)
        inserted = self._shipment(
            "C",
            weight=100,
            total=20_000,
            collection=self.roadside,
            window_start=window_start,
        )
        with self.assertRaises(FreightError):
            insert_consignment(
                organization=self.org, trip=trip, shipment=inserted, actor=self.dispatcher
            )
        # A lenient operator gets the same insertion accepted.
        self.org.insertion_grace_minutes = 240
        self.org.save(update_fields=["insertion_grace_minutes"])
        new_plan = insert_consignment(
            organization=self.org, trip=trip, shipment=inserted, actor=self.dispatcher
        )
        self.assertEqual(new_plan.status, ConsolidationPlan.Status.PUBLISHED)

    def test_the_tolerance_a_caller_passes_is_capped(self):
        trip, plan, _ = self._running_trip()
        inserted = self._shipment("C", weight=100, total=20_000, collection=self.roadside)
        row = request_insertion(
            organization=self.org,
            trip=trip,
            shipment=inserted,
            requested_by=self.dispatcher,
            tolerance_minutes=10_000,
        )
        self.assertEqual(row.tolerance_minutes, MAX_TOLERANCE_MINUTES)

    def test_the_stored_policy_cannot_be_set_beyond_the_cap(self):
        self.org.insertion_grace_minutes = MAX_TOLERANCE_MINUTES + 1
        with self.assertRaises(ValidationError):
            self.org.full_clean()

    def test_a_refusal_names_the_stop_that_would_be_late(self):
        trip, plan, _ = self._running_trip()
        window_start = self._tight_couple(plan)
        inserted = self._shipment(
            "C",
            weight=100,
            total=20_000,
            collection=self.roadside,
            window_start=window_start,
        )
        with self.assertRaises(FreightError) as ctx:
            insert_consignment(
                organization=self.org, trip=trip, shipment=inserted, actor=self.dispatcher
            )
        self.assertIn("Crossroads", str(ctx.exception))

    def test_the_change_is_announced_with_the_numbers(self):
        trip, plan, _ = self._running_trip()
        inserted = self._shipment("C", weight=100, total=20_000, collection=self.roadside)
        insert_consignment(
            organization=self.org,
            trip=trip,
            shipment=inserted,
            actor=self.dispatcher,
            reason="Farmer asked at the gate",
        )
        event = OutboxEvent.objects.filter(event_type="freight.route_changed").latest("created_at")
        self.assertEqual(event.payload["inserted"]["shipment"], inserted.reference)
        self.assertEqual(event.payload["supersedes"], plan.version)
        self.assertIn("delayed_stops", event.payload)
        self.assertEqual(event.payload["tolerance_minutes"], 30)

    def test_an_audit_trail_names_the_approver(self):
        from apps.audit.models import AuditEvent

        trip, plan, _ = self._running_trip()
        inserted = self._shipment("C", weight=100, total=20_000, collection=self.roadside)
        new_plan = insert_consignment(
            organization=self.org, trip=trip, shipment=inserted, actor=self.dispatcher
        )
        event = AuditEvent.objects.filter(action="goods.consignment_inserted").get()
        self.assertEqual(event.entity_id, new_plan.pk)
        self.assertEqual(event.actor_id, self.dispatcher.pk)
        self.assertEqual(event.new_value["approved_by"], self.dispatcher.phone)


class InsertionRefusalTestCase(RunningTripFixture):
    """Invariants 5, 6, 10: what the platform will not do, and why."""

    def test_a_consignment_too_heavy_for_what_is_left_is_refused(self):
        trip, plan, _ = self._running_trip()
        heavy = self._shipment("C", weight=800, total=20_000, collection=self.roadside)
        with self.assertRaises(FreightError) as ctx:
            insert_consignment(
                organization=self.org, trip=trip, shipment=heavy, actor=self.dispatcher
            )
        self.assertIn(REASON_TOO_HEAVY, str(ctx.exception))

    def test_a_full_truckload_is_refused_mid_route(self):
        from apps.goods.insertion import REASON_FULL_TRUCKLOAD_ALONE

        trip, plan, _ = self._running_trip()
        full = self._shipment("C", weight=100, total=20_000, collection=self.roadside)
        full.load_type = LoadType.FULL_TRUCKLOAD
        full.save(update_fields=["load_type", "updated_at"])
        with self.assertRaises(FreightError) as ctx:
            insert_consignment(
                organization=self.org, trip=trip, shipment=full, actor=self.dispatcher
            )
        self.assertIn(REASON_FULL_TRUCKLOAD_ALONE, str(ctx.exception))

    def test_a_trip_with_no_remaining_stops_refuses_insertion(self):
        trip, plan, _ = self._running_trip()
        for stop in plan.stops.filter(is_completed=False):
            complete_stop(plan=plan, stop=stop, actor=self.driver_user)
        inserted = self._shipment("C", weight=100, total=20_000, collection=self.roadside)
        with self.assertRaises(FreightError) as ctx:
            insert_consignment(
                organization=self.org, trip=trip, shipment=inserted, actor=self.dispatcher
            )
        self.assertIn(REASON_NO_REMAINING_STOPS, str(ctx.exception))

    def test_a_trip_that_has_not_departed_refuses_insertion(self):
        trip, plan, _ = self._running_trip()
        trip.started_at = None
        trip.save(update_fields=["started_at"])
        inserted = self._shipment("C", weight=100, total=20_000, collection=self.roadside)
        with self.assertRaises(FreightError) as ctx:
            insert_consignment(
                organization=self.org, trip=trip, shipment=inserted, actor=self.dispatcher
            )
        self.assertIn(REASON_TRIP_NOT_RUNNING, str(ctx.exception))

    def test_a_different_destination_is_refused(self):
        other = Place.objects.create(
            organization=self.org, name="Far market", place_type=Place.PlaceType.HUB
        )
        trip, plan, _ = self._running_trip()
        elsewhere = self._shipment("C", weight=100, total=20_000, collection=self.roadside)
        elsewhere.destination = other
        elsewhere.save(update_fields=["destination", "updated_at"])
        with self.assertRaises(FreightError) as ctx:
            insert_consignment(
                organization=self.org, trip=trip, shipment=elsewhere, actor=self.dispatcher
            )
        self.assertIn(REASON_WRONG_DESTINATION, str(ctx.exception))

    def test_a_window_already_closed_is_refused(self):
        trip, plan, _ = self._running_trip()
        late = self._shipment("C", weight=100, total=20_000, collection=self.roadside)
        # The window closes while the truck is already driving.
        late.service_window_start = timezone.now() - timedelta(hours=3)
        late.service_window_end = timezone.now() - timedelta(hours=1)
        late.save(update_fields=["service_window_start", "service_window_end", "updated_at"])
        with self.assertRaises(FreightError) as ctx:
            insert_consignment(
                organization=self.org, trip=trip, shipment=late, actor=self.dispatcher
            )
        self.assertIn(REASON_WINDOW_PASSED, str(ctx.exception))

    def test_inserting_the_same_consignment_twice_is_refused(self):
        trip, plan, _ = self._running_trip()
        inserted = self._shipment("C", weight=100, total=20_000, collection=self.roadside)
        insert_consignment(
            organization=self.org, trip=trip, shipment=inserted, actor=self.dispatcher
        )
        with self.assertRaises(FreightError) as ctx:
            insert_consignment(
                organization=self.org, trip=trip, shipment=inserted, actor=self.dispatcher
            )
        self.assertIn(REASON_ALREADY_INSERTED, str(ctx.exception))

    def test_a_place_the_truck_has_already_served_is_refused(self):
        trip, plan, _ = self._running_trip()
        # The completed stop was the farm; the truck cannot collect there again.
        revisit = self._shipment("C", weight=100, total=20_000, collection=self.farm)
        with self.assertRaises(FreightError) as ctx:
            insert_consignment(
                organization=self.org, trip=trip, shipment=revisit, actor=self.dispatcher
            )
        self.assertIn(REASON_ALREADY_SERVED, str(ctx.exception))

    def test_another_organizations_consignment_is_refused(self):
        trip, plan, _ = self._running_trip()
        foreign = self._shipment(
            "C", weight=100, total=20_000, organization=self.other_org
        )
        with self.assertRaises(FreightError) as ctx:
            insert_consignment(
                organization=self.org, trip=trip, shipment=foreign, actor=self.dispatcher
            )
        self.assertIn("another organization", str(ctx.exception))


class InsertionPriceAndCoverageTestCase(RunningTripFixture):
    """Invariant 7: nobody's price moves, and the new revenue is reported."""

    def test_no_existing_shipper_price_moves(self):
        trip, plan, _ = self._running_trip()
        inserted = self._shipment("C", weight=100, total=20_000, collection=self.roadside)
        insert_consignment(
            organization=self.org, trip=trip, shipment=inserted, actor=self.dispatcher
        )
        for shipment in Shipment.objects.filter(
            pk__in=[inserted.pk, *plan.shipments.values_list("shipment_id", flat=True)]
        ):
            self.assertEqual(
                shipment.agreed_total_minor,
                shipment.plan_entries.filter(accepted=True).latest("created_at").revenue_minor,
            )

    def test_coverage_is_reported_with_the_new_revenue(self):
        trip, plan, _ = self._running_trip()
        before = assess_coverage(trip)
        inserted = self._shipment("C", weight=100, total=20_000, collection=self.roadside)
        new_plan = insert_consignment(
            organization=self.org, trip=trip, shipment=inserted, actor=self.dispatcher
        )
        # The manifest has not been amended yet, so coverage still counts what is
        # physically on the truck; the plan now commits the new revenue.
        self.assertEqual(assess_coverage(trip)["committed_minor"], before["committed_minor"])
        committed = sum(
            entry.revenue_minor for entry in new_plan.shipments.filter(accepted=True)
        )
        self.assertEqual(committed, 115_000)
        self.assertTrue(assess_coverage(trip)["covered"])

    def test_coverage_includes_the_inserted_revenue_once_it_is_on_the_manifest(self):
        trip, plan, _ = self._running_trip()
        inserted = self._shipment("C", weight=100, total=20_000, collection=self.roadside)
        insert_consignment(
            organization=self.org, trip=trip, shipment=inserted, actor=self.dispatcher
        )
        amended = amend_manifest(
            trip=trip, packages=list(inserted.packages.all()), actor=self.dispatcher
        )
        reconcile_manifest(
            manifest=amended,
            scanned={
                line.label: ManifestLine.ScanState.SCANNED for line in amended.lines.all()
            },
        )
        release_manifest(manifest=amended)
        self.assertEqual(assess_coverage(trip)["committed_minor"], 115_000)


class ManifestAmendmentTestCase(RunningTripFixture):
    """Invariant 8: a mid-route pickup is a new manifest version, reconciled and
    re-released, with the released one still on record."""

    def test_amending_creates_a_new_version_and_keeps_the_old(self):
        trip, plan, _ = self._running_trip()
        original = Manifest.objects.filter(trip=trip).get()
        inserted = self._shipment("C", weight=100, total=20_000, collection=self.roadside)
        insert_consignment(
            organization=self.org, trip=trip, shipment=inserted, actor=self.dispatcher
        )
        amended = amend_manifest(
            trip=trip,
            packages=list(inserted.packages.all()),
            actor=self.dispatcher,
            reason="Picked up at the gate",
        )
        self.assertEqual(amended.version, original.version + 1)
        self.assertEqual(amended.supersedes_id, original.pk)
        self.assertEqual(amended.status, Manifest.Status.DRAFT)
        original.refresh_from_db()
        self.assertEqual(original.status, Manifest.Status.RELEASED)
        # Everything already on the truck is carried across, plus the new package.
        self.assertEqual(amended.lines.count(), original.lines.count() + 1)

    def test_the_amendment_keeps_the_compartments_and_waits_for_the_scan(self):
        trip, plan, _ = self._running_trip()
        original = Manifest.objects.filter(trip=trip).get()
        placed = {
            line.package_id: line.compartment_id
            for line in original.lines.all()
            if line.compartment_id is not None
        }
        self.assertTrue(placed, "the departure manifest should place its packages")
        inserted = self._shipment("C", weight=100, total=20_000, collection=self.roadside)
        amend_manifest(
            trip=trip, packages=list(inserted.packages.all()), actor=self.dispatcher
        )
        amended = Manifest.objects.filter(trip=trip).order_by("-version").first()
        carried = {
            line.package_id: line.compartment_id
            for line in amended.lines.all()
            if line.package_id in placed
        }
        # The load on board keeps the compartments it was released with.
        self.assertEqual(carried, placed)
        # The mid-route pickup has been listed, not asserted as scanned.
        fresh = amended.lines.exclude(package_id__in=placed).get()
        self.assertEqual(fresh.scan_state, ManifestLine.ScanState.PENDING)
        self.assertIsNotNone(fresh.compartment_id)

    def test_the_amendment_must_be_reconciled_and_released(self):
        from apps.goods.services import GoodsError, release_manifest

        trip, plan, _ = self._running_trip()
        inserted = self._shipment("C", weight=100, total=20_000, collection=self.roadside)
        amended = amend_manifest(
            trip=trip, packages=list(inserted.packages.all()), actor=self.dispatcher
        )
        with self.assertRaises(GoodsError):
            release_manifest(manifest=amended)
        reconcile_manifest(
            manifest=amended,
            scanned={
                line.label: ManifestLine.ScanState.SCANNED for line in amended.lines.all()
            },
        )
        released = release_manifest(manifest=amended)
        self.assertEqual(released.status, Manifest.Status.RELEASED)

    def test_amending_the_same_package_twice_is_refused(self):
        trip, plan, _ = self._running_trip()
        inserted = self._shipment("C", weight=100, total=20_000, collection=self.roadside)
        amend_manifest(trip=trip, packages=list(inserted.packages.all()), actor=self.dispatcher)
        with self.assertRaises(FreightError) as ctx:
            amend_manifest(
                trip=trip, packages=list(inserted.packages.all()), actor=self.dispatcher
            )
        self.assertIn(REASON_ALREADY_INSERTED, str(ctx.exception))


class DriverOfferTestCase(RunningTripFixture):
    """Invariants 9, 10: first accept wins, under a re-check."""

    def setUp(self):
        super().setUp()
        self.trip, self.plan, _ = self._running_trip()
        self.inserted = self._shipment(
            "C", weight=100, total=20_000, collection=self.roadside
        )

    def test_the_offer_records_what_was_free(self):
        row = request_insertion(
            organization=self.org,
            trip=self.trip,
            shipment=self.inserted,
            requested_by=self.dispatcher,
        )
        self.assertEqual(row.status, InsertionRequest.Status.PENDING)
        self.assertEqual(row.plan_version, self.plan.version)
        self.assertEqual(row.tolerance_minutes, 30)
        self.assertGreater(row.free_weight_kg, 0)

    def test_first_accept_wins(self):
        row = request_insertion(
            organization=self.org,
            trip=self.trip,
            shipment=self.inserted,
            requested_by=self.dispatcher,
        )
        plan = accept_insertion(request=row, driver=self.driver_user)
        self.assertEqual(plan.status, ConsolidationPlan.Status.PUBLISHED)
        row.refresh_from_db()
        self.assertEqual(row.status, InsertionRequest.Status.ACCEPTED)
        self.assertEqual(row.resulting_plan_id, plan.pk)
        # A second driver on the same offer loses.
        with self.assertRaises(FreightError) as ctx:
            accept_insertion(request=row, driver=self.driver_user)
        self.assertIn(REASON_ALREADY_DECIDED, str(ctx.exception))

    def test_a_decline_closes_the_offer(self):
        row = request_insertion(
            organization=self.org,
            trip=self.trip,
            shipment=self.inserted,
            requested_by=self.dispatcher,
        )
        decline_insertion(request=row, driver=self.driver_user, reason="No room")
        row.refresh_from_db()
        self.assertEqual(row.status, InsertionRequest.Status.DECLINED)
        self.assertEqual(row.reason, "No room")
        with self.assertRaises(FreightError):
            accept_insertion(request=row, driver=self.driver_user)

    def test_an_offer_measured_against_a_stale_plan_is_refused(self):
        row = request_insertion(
            organization=self.org,
            trip=self.trip,
            shipment=self.inserted,
            requested_by=self.dispatcher,
        )
        # Dispatch publishes another version before the driver gets to it.
        other = self._shipment("D", weight=50, total=10_000, collection=self.roadside)
        insert_consignment(
            organization=self.org, trip=self.trip, shipment=other, actor=self.dispatcher
        )
        with self.assertRaises(FreightError) as ctx:
            accept_insertion(request=row, driver=self.driver_user)
        self.assertIn("moved on", str(ctx.exception))
        row.refresh_from_db()
        self.assertEqual(row.status, InsertionRequest.Status.REFUSED)

    def test_an_expired_offer_is_refused(self):
        row = request_insertion(
            organization=self.org,
            trip=self.trip,
            shipment=self.inserted,
            requested_by=self.dispatcher,
            tolerance_minutes=0,
        )
        with self.assertRaises(FreightError) as ctx:
            accept_insertion(request=row, driver=self.driver_user)
        self.assertIn("moved on", str(ctx.exception))

    def test_another_driver_cannot_accept(self):
        row = request_insertion(
            organization=self.org,
            trip=self.trip,
            shipment=self.inserted,
            requested_by=self.dispatcher,
        )
        from apps.fleet.models import Driver

        other_driver = Driver.objects.create(
            user=User.objects.create_user("0744000077", "pass-O-1234"),
            organization=self.org,
            status=Driver.Status.APPROVED,
        )
        with self.assertRaises(FreightError) as ctx:
            accept_insertion(request=row, driver=other_driver.user)
        self.assertIn("another driver", str(ctx.exception))
        row.refresh_from_db()
        self.assertEqual(row.status, InsertionRequest.Status.PENDING)

    def test_another_driver_cannot_decline(self):
        row = request_insertion(
            organization=self.org,
            trip=self.trip,
            shipment=self.inserted,
            requested_by=self.dispatcher,
        )
        from apps.fleet.models import Driver

        other_driver = Driver.objects.create(
            user=User.objects.create_user("0744000077", "pass-O-1234"),
            organization=self.org,
            status=Driver.Status.APPROVED,
        )
        with self.assertRaises(FreightError) as ctx:
            decline_insertion(request=row, driver=other_driver.user, reason="No room")
        self.assertIn(REASON_OTHER_DRIVER, str(ctx.exception))
        row.refresh_from_db()
        self.assertEqual(row.status, InsertionRequest.Status.PENDING)


class RouteStateTestCase(RunningTripFixture):
    def test_the_route_read_separates_done_from_promised(self):
        trip, plan, _ = self._running_trip()
        state = route_state(trip)
        self.assertEqual(len(state["completed_stops"]), 1)
        self.assertGreaterEqual(len(state["remaining_stops"]), 1)
        self.assertEqual(state["plan"].pk, plan.pk)
        self.assertIn("committed_minor", state["coverage"])


class InsertionApiTestCase(RunningTripFixture):
    def setUp(self):
        super().setUp()
        self.trip, self.plan, _ = self._running_trip()
        self.staff_client = APIClient()
        self.staff_client.force_authenticate(self.dispatcher)
        self.driver_client = APIClient()
        self.driver_client.force_authenticate(self.driver_user)

    def test_dispatcher_insertion_over_http(self):
        inserted = self._shipment("C", weight=100, total=20_000, collection=self.roadside)
        response = self.staff_client.post(
            f"/api/v1/freight-trips/{self.trip.pk}/insertion/",
            {"shipment": str(inserted.pk), "reason": "Asked at the gate"},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["version"], self.plan.version + 1)
        self.assertEqual(response.data["status"], ConsolidationPlan.Status.PUBLISHED)

    def test_a_refused_insertion_is_a_400_with_the_reason(self):
        window_start = self._tight_couple(self.plan)
        inserted = self._shipment(
            "C",
            weight=100,
            total=20_000,
            collection=self.roadside,
            window_start=window_start,
        )
        response = self.staff_client.post(
            f"/api/v1/freight-trips/{self.trip.pk}/insertion/",
            {"shipment": str(inserted.pk)},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn(REASON_TOLERANCE_EXCEEDED, response.data["detail"])

    def test_a_branch_staff_caller_cannot_insert(self):
        branch = User.objects.create_user("0744000088", "pass-B-1234")
        Membership.objects.create(
            user=branch, organization=self.org, role=Role.BRANCH_STAFF
        )
        client = APIClient()
        client.force_authenticate(branch)
        inserted = self._shipment("C", weight=100, total=20_000, collection=self.roadside)
        response = client.post(
            f"/api/v1/freight-trips/{self.trip.pk}/insertion/",
            {"shipment": str(inserted.pk)},
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    def test_the_route_endpoint(self):
        response = self.staff_client.get(f"/api/v1/freight-trips/{self.trip.pk}/route/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["completed_stops"], 1)
        self.assertIn("committed_minor", response.data)

    def test_the_driver_offer_flow_over_http(self):
        inserted = self._shipment("C", weight=100, total=20_000, collection=self.roadside)
        response = self.staff_client.post(
            f"/api/v1/freight-trips/{self.trip.pk}/insertion-requests/",
            {"shipment": str(inserted.pk)},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["status"], InsertionRequest.Status.PENDING)
        request_id = response.data["id"]

        response = self.driver_client.post(
            f"/api/v1/insertion-requests/{request_id}/accept/", {}, format="json"
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["status"], ConsolidationPlan.Status.PUBLISHED)

    def test_a_driver_declines_over_http(self):
        inserted = self._shipment("C", weight=100, total=20_000, collection=self.roadside)
        response = self.staff_client.post(
            f"/api/v1/freight-trips/{self.trip.pk}/insertion-requests/",
            {"shipment": str(inserted.pk)},
            format="json",
        )
        request_id = response.data["id"]
        response = self.driver_client.post(
            f"/api/v1/insertion-requests/{request_id}/decline/",
            {"reason": "Truck is full after all"},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], InsertionRequest.Status.DECLINED)
        self.assertEqual(response.data["reason"], "Truck is full after all")

    def test_stop_completion_over_http(self):
        stop = self.plan.stops.filter(is_completed=False).first()
        response = self.staff_client.post(
            f"/api/v1/consolidation-plans/{self.plan.pk}/stops/{stop.pk}/complete/",
            {"note": "Handed over"},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["is_completed"])

    def test_an_offer_can_be_raised_on_the_collection_endpoint(self):
        inserted = self._shipment("C", weight=100, total=20_000, collection=self.roadside)
        response = self.staff_client.post(
            "/api/v1/insertion-requests/",
            {
                "trip": str(self.trip.pk),
                "shipment": str(inserted.pk),
                "reason": "Farmer asked at the gate",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["status"], InsertionRequest.Status.PENDING)
        self.assertEqual(response.data["plan_version"], self.plan.version)
        self.assertEqual(response.data["reason"], "Farmer asked at the gate")

    def test_a_client_cannot_write_the_measured_columns(self):
        inserted = self._shipment("C", weight=100, total=20_000, collection=self.roadside)
        response = self.staff_client.post(
            "/api/v1/insertion-requests/",
            {
                "trip": str(self.trip.pk),
                "shipment": str(inserted.pk),
                "status": InsertionRequest.Status.ACCEPTED,
                "tolerance_minutes": 240,
                "free_weight_kg": "9999",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        row = InsertionRequest.objects.get()
        self.assertEqual(row.status, InsertionRequest.Status.PENDING)
        self.assertEqual(row.tolerance_minutes, 30)
        self.assertNotEqual(row.free_weight_kg, 9999)

    def test_a_branch_staff_caller_cannot_raise_an_offer(self):
        branch = User.objects.create_user("0744000088", "pass-B-1234")
        Membership.objects.create(
            user=branch, organization=self.org, role=Role.BRANCH_STAFF
        )
        client = APIClient()
        client.force_authenticate(branch)
        inserted = self._shipment("C", weight=100, total=20_000, collection=self.roadside)
        response = client.post(
            "/api/v1/insertion-requests/",
            {"trip": str(self.trip.pk), "shipment": str(inserted.pk)},
            format="json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(InsertionRequest.objects.exists())

    def test_the_driver_can_record_a_served_stop(self):
        stop = self.plan.stops.filter(is_completed=False).first()
        response = self.driver_client.post(
            f"/api/v1/consolidation-plans/{self.plan.pk}/stops/{stop.pk}/complete/",
            {"note": "Handed over at the farm"},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["is_completed"])
        self.assertIsNotNone(response.data["completed_at"])

    def test_another_driver_cannot_record_a_served_stop(self):
        from apps.fleet.models import Driver

        other = Driver.objects.create(
            user=User.objects.create_user("0744000077", "pass-O-1234"),
            organization=self.org,
            status=Driver.Status.APPROVED,
        )
        # In the same organization, so this is a privilege question, not isolation.
        Membership.objects.create(
            user=other.user, organization=self.org, role=Role.BRANCH_STAFF
        )
        client = APIClient()
        client.force_authenticate(other.user)
        stop = self.plan.stops.filter(is_completed=False).first()
        response = client.post(
            f"/api/v1/consolidation-plans/{self.plan.pk}/stops/{stop.pk}/complete/",
            {},
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    def test_manifest_amendment_over_http(self):
        inserted = self._shipment("C", weight=100, total=20_000, collection=self.roadside)
        insert_consignment(
            organization=self.org, trip=self.trip, shipment=inserted, actor=self.dispatcher
        )
        manifest = Manifest.objects.filter(trip=self.trip).get()
        response = self.staff_client.post(
            f"/api/v1/manifests/{manifest.pk}/amend/",
            {"packages": [str(p.pk) for p in inserted.packages.all()], "reason": "At the gate"},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["version"], 2)
        self.assertEqual(response.data["status"], Manifest.Status.DRAFT)
