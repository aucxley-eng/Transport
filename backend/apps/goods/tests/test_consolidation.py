"""Consolidated shared-load, the coverage floor and backhaul (blueprint 9, 11).

The rules under test, quoted in ``apps/goods/consolidation.py``:
planning is dispatcher-assisted and rule-based, subject to time windows,
capacity and working hours; a proposal is validated before publication; plan
versions are saved rather than edited; infeasible work is explained, not hidden;
and the owner's coverage-floor decision gates departure.
"""

from datetime import datetime, timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import Membership, Organization, Role, User
from apps.catalog.models import Place, Service
from apps.fleet.models import CargoCompartment, Vehicle
from apps.goods.consolidation import (
    REASON_FULL_TRUCKLOAD_ALONE,
    REASON_SHIPMENT_CLOSED,
    REASON_TOO_HEAVY,
    REASON_TRIP_DEPARTED,
    REASON_WINDOW_OUTSIDE_TRIP,
    REASON_WRONG_DESTINATION,
    FreightError,
    assess_coverage,
    confirm_departure,
    empty_running_report,
    find_backhaul_opportunities,
    propose_consolidation,
    publish_plan,
    supersede_plan,
    validate_plan,
)
from apps.goods.models import (
    ConsolidationPlan,
    FreightTrip,
    LoadType,
    ManifestLine,
    Package,
    PlanShipment,
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


class ConsolidationFixture(TestCase):
    """Three shippers sending to the same market on the same corridor."""

    def setUp(self):
        self.org = Organization.objects.create(name="Consolidators", slug="consol")
        self.shipper_a = User.objects.create_user("0733000011", "pass-A-1234")
        self.shipper_b = User.objects.create_user("0733000022", "pass-B-1234")
        self.shipper_c = User.objects.create_user("0733000033", "pass-C-1234")
        self.staff = User.objects.create_user("0733000044", "pass-S-1234")
        Membership.objects.create(
            user=self.staff, organization=self.org, role=Role.DISPATCHER
        )

        self.service = Service.objects.create(
            slug="shared-freight", name="Shared freight", service_type=Service.ServiceType.FREIGHT
        )
        self.farm = Place.objects.create(
            organization=self.org, name="Farm", place_type=Place.PlaceType.BRANCH
        )
        self.market = Place.objects.create(
            organization=self.org, name="Market", place_type=Place.PlaceType.HUB
        )

        self.truck = Vehicle.objects.create(
            organization=self.org,
            registration_number="KTR700A",
            vehicle_class=Vehicle.VehicleClass.TRUCK,
            cargo_weight_kg=1000,
            cargo_volume_m3=15.0,
        )
        self.bed = CargoCompartment.objects.create(
            organization=self.org,
            vehicle=self.truck,
            name="Bed",
            max_weight_kg=1000,
            max_volume_m3=15.0,
        )
        self.driver = self._make_driver("0733000055", working_hours=10)

        self.start = timezone.make_aware(datetime(2027, 1, 10, 8, 0))
        self.end = timezone.make_aware(datetime(2027, 1, 10, 16, 0))

    def _make_driver(self, phone, working_hours=10):
        from apps.fleet.models import Driver

        return Driver.objects.create(
            user=User.objects.create_user(phone, "pass-D-1234"),
            organization=self.org,
            status=Driver.Status.APPROVED,
            working_hours_per_day=working_hours,
        )

    def _shipment(
        self,
        sender=None,
        name="Shipper",
        weight=200,
        volume=1.0,
        total=40_000,
        load_type=LoadType.SHARED_LOAD,
        window=None,
        destination=None,
    ):
        quote = quote_shipment(
            organization=self.org,
            service=self.service,
            sender_name=name,
            sender_phone="0733000099",
            recipient_name="Market Trader",
            recipient_phone="0733000088",
            collection=self.farm,
            destination=destination or self.market,
            total_weight_kg=weight,
            total_volume_m3=volume,
            service_window_start=self.start,
            service_window_end=window or self.end,
            total_minor=total,
        )
        quote.accept()
        return create_shipment(
            quote=quote,
            organization=self.org,
            service=self.service,
            sender=sender,
            sender_name=name,
            sender_phone="0733000099",
            recipient_name="Market Trader",
            recipient_phone="0733000088",
            collection=self.farm,
            destination=destination or self.market,
            service_window_start=self.start,
            service_window_end=window or self.end,
            packages=[{"weight_kg": weight, "length_cm": 50, "width_cm": 50, "height_cm": 40}],
            load_type=load_type,
        )

    def _trip(self, **overrides):
        payload = {
            "organization": self.org,
            "vehicle": self.truck,
            "driver": self.driver,
            "service": self.service,
            "origin": self.farm,
            "destination": self.market,
            "planned_start": self.start,
            "planned_end": self.end,
        }
        payload.update(overrides)
        return create_freight_trip(**payload)


class SharedLoadProposalTestCase(ConsolidationFixture):
    """A proposal is rule-based, admits what fits, and explains every rejection."""

    def test_several_shippers_share_one_trip(self):
        a = self._shipment(self.shipper_a, "A", weight=200)
        b = self._shipment(self.shipper_b, "B", weight=300)
        trip = self._trip()
        plan = propose_consolidation(
            organization=self.org, trip=trip, shipments=[a, b], proposed_by=self.staff
        )
        self.assertEqual(plan.shipments.filter(accepted=True).count(), 2)
        # Two consignments, two stops, and the shared collection stop appears
        # once rather than twice.
        self.assertEqual(plan.stops.count(), 2)
        self.assertEqual(plan.stops.order_by("sequence").first().place, self.farm)

    def test_each_shipper_keeps_its_own_frozen_revenue(self):
        """The owner's decision: a shipper's price never moves because of who
        else is aboard."""
        a = self._shipment(self.shipper_a, "A", weight=200, total=40_000)
        b = self._shipment(self.shipper_b, "B", weight=300, total=55_000)
        trip = self._trip()
        plan = propose_consolidation(organization=self.org, trip=trip, shipments=[a, b])
        revenues = dict(
            plan.shipments.values_list("shipment__sender_name", "revenue_minor")
        )
        self.assertEqual(revenues, {"A": 40_000, "B": 55_000})
        a.refresh_from_db()
        self.assertEqual(a.agreed_total_minor, 40_000)

    def test_a_consignment_too_heavy_for_the_remainder_is_rejected_with_a_reason(self):
        a = self._shipment(self.shipper_a, "A", weight=800)
        b = self._shipment(self.shipper_b, "B", weight=400)
        trip = self._trip()
        plan = propose_consolidation(organization=self.org, trip=trip, shipments=[a, b])
        rejected = plan.shipments.get(shipment=b)
        self.assertFalse(rejected.accepted)
        self.assertEqual(rejected.reason, REASON_TOO_HEAVY)
        # The unassigned work is visible, not hidden.
        self.assertEqual(plan.shipments.filter(accepted=True).count(), 1)

    def test_a_window_outside_the_trip_is_rejected(self):
        late_end = self.end + timedelta(hours=6)
        late = self._shipment(
            self.shipper_a, "A", weight=100, window=late_end
        )
        trip = self._trip()
        plan = propose_consolidation(organization=self.org, trip=trip, shipments=[late])
        self.assertEqual(
            plan.shipments.get(shipment=late).reason, REASON_WINDOW_OUTSIDE_TRIP
        )

    def test_a_different_destination_is_rejected(self):
        other = Place.objects.create(
            organization=self.org, name="Other market", place_type=Place.PlaceType.HUB
        )
        elsewhere = self._shipment(self.shipper_a, "A", weight=100, destination=other)
        trip = self._trip()
        plan = propose_consolidation(organization=self.org, trip=trip, shipments=[elsewhere])
        self.assertEqual(
            plan.shipments.get(shipment=elsewhere).reason, REASON_WRONG_DESTINATION
        )

    def test_a_full_truckload_travels_alone(self):
        full = self._shipment(
            self.shipper_a, "A", weight=900, load_type=LoadType.FULL_TRUCKLOAD
        )
        shared = self._shipment(self.shipper_b, "B", weight=100)
        trip = self._trip()
        plan = propose_consolidation(
            organization=self.org, trip=trip, shipments=[full, shared]
        )
        self.assertTrue(plan.shipments.get(shipment=full).accepted)
        self.assertEqual(
            plan.shipments.get(shipment=shared).reason, REASON_FULL_TRUCKLOAD_ALONE
        )

    def test_a_full_truckload_alone_is_fine(self):
        full = self._shipment(
            self.shipper_a, "A", weight=900, load_type=LoadType.FULL_TRUCKLOAD
        )
        trip = self._trip()
        plan = propose_consolidation(organization=self.org, trip=trip, shipments=[full])
        self.assertTrue(plan.shipments.get(shipment=full).accepted)

    def test_a_closed_consignment_is_rejected(self):
        closed = self._shipment(self.shipper_a, "A", weight=100)
        closed.status = Shipment.Status.CANCELLED
        closed.save(update_fields=["status", "updated_at"])
        trip = self._trip()
        plan = propose_consolidation(organization=self.org, trip=trip, shipments=[closed])
        self.assertEqual(plan.shipments.get(shipment=closed).reason, REASON_SHIPMENT_CLOSED)

    def test_a_trip_beyond_the_working_day_admits_nothing(self):
        a = self._shipment(self.shipper_a, "A", weight=100)
        trip = self._trip(
            planned_end=timezone.make_aware(datetime(2027, 1, 10, 23, 0))
        )
        plan = propose_consolidation(organization=self.org, trip=trip, shipments=[a])
        self.assertEqual(plan.status, ConsolidationPlan.Status.REJECTED)
        self.assertIn("working hours", plan.explanation)

    def test_an_infeasible_plan_explains_itself(self):
        """"If no feasible plan exists, return an explanation to dispatch rather
        than hiding unassigned work." """
        heavy = self._shipment(self.shipper_a, "A", weight=5000)
        trip = self._trip()
        plan = propose_consolidation(organization=self.org, trip=trip, shipments=[heavy])
        self.assertEqual(plan.status, ConsolidationPlan.Status.REJECTED)
        self.assertIn("No consignment fits this trip", plan.explanation)
        self.assertIn(REASON_TOO_HEAVY, plan.explanation)

    def test_each_proposal_saves_a_new_version(self):
        """"Save each route plan version." """
        a = self._shipment(self.shipper_a, "A", weight=200)
        trip = self._trip()
        first = propose_consolidation(organization=self.org, trip=trip, shipments=[a])
        second = propose_consolidation(organization=self.org, trip=trip, shipments=[a])
        self.assertEqual(first.version, 1)
        self.assertEqual(second.version, 2)
        self.assertEqual(second.supersedes_id, first.pk)
        self.assertEqual(trip.consolidation_plans.count(), 2)


class PlanValidationTestCase(ConsolidationFixture):
    """"Validate its proposed result against your own business rules before
    publication." """

    def _published_candidate(self):
        a = self._shipment(self.shipper_a, "A", weight=200, total=40_000)
        trip = self._trip()
        plan = propose_consolidation(organization=self.org, trip=trip, shipments=[a])
        return trip, plan

    def test_a_clean_plan_validates(self):
        _, plan = self._published_candidate()
        plan = validate_plan(plan)
        self.assertEqual(plan.status, ConsolidationPlan.Status.VALIDATED)
        self.assertIsNotNone(plan.validated_at)
        self.assertEqual(plan.explanation, "")

    def test_publication_requires_validation(self):
        _, plan = self._published_candidate()
        with self.assertRaises(FreightError):
            publish_plan(plan)

    def test_an_infeasible_plan_cannot_be_published(self):
        a = self._shipment(self.shipper_a, "A", weight=200)
        trip = self._trip()
        plan = propose_consolidation(organization=self.org, trip=trip, shipments=[a])
        # A proposal nobody could carry: validation rejects it with a reason.
        heavy = self._shipment(self.shipper_b, "B", weight=5000)
        PlanShipment.objects.filter(plan=plan, shipment=heavy).delete()
        PlanShipment.objects.create(
            organization=self.org,
            plan=plan,
            shipment=heavy,
            accepted=True,
            reason="",
            revenue_minor=heavy.agreed_total_minor,
        )
        plan = validate_plan(plan)
        self.assertEqual(plan.status, ConsolidationPlan.Status.REJECTED)
        with self.assertRaises(FreightError):
            publish_plan(plan)

    def test_validation_catches_a_tampered_revenue(self):
        """Validation re-derives the rules; it does not trust the proposer."""
        trip, plan = self._published_candidate()
        PlanShipment.objects.filter(plan=plan).update(revenue_minor=999_999)
        plan = validate_plan(plan)
        self.assertEqual(plan.status, ConsolidationPlan.Status.REJECTED)
        self.assertIn("frozen quote", plan.explanation)

    def test_validation_catches_a_window_that_overruns_the_trip(self):
        trip, plan = self._published_candidate()
        shipment = plan.shipments.get(accepted=True).shipment
        shipment.service_window_end = trip.planned_end + timedelta(hours=2)
        shipment.save(update_fields=["service_window_end"])
        plan = validate_plan(plan)
        self.assertEqual(plan.status, ConsolidationPlan.Status.REJECTED)
        self.assertIn("after the trip arrives", plan.explanation)

    def test_publishing_supersedes_the_previous_version(self):
        a = self._shipment(self.shipper_a, "A", weight=200)
        trip = self._trip()
        first = propose_consolidation(organization=self.org, trip=trip, shipments=[a])
        publish_plan(validate_plan(first))

        second = propose_consolidation(organization=self.org, trip=trip, shipments=[a])
        second = validate_plan(second)
        with self.assertRaises(FreightError):
            # Replacing a published plan needs a reason for the change.
            publish_plan(second)
        second = publish_plan(second, reason="Added a second collection window")
        first.refresh_from_db()
        self.assertEqual(first.status, ConsolidationPlan.Status.SUPERSEDED)
        self.assertEqual(second.status, ConsolidationPlan.Status.PUBLISHED)
        self.assertEqual(second.change_reason, "Added a second collection window")

    def test_supersede_keeps_the_old_version_and_needs_a_reason(self):
        a = self._shipment(self.shipper_a, "A", weight=200)
        trip = self._trip()
        plan = publish_plan(validate_plan(propose_consolidation(
            organization=self.org, trip=trip, shipments=[a]
        )))
        with self.assertRaises(FreightError):
            supersede_plan(plan, reason="")
        new_plan = supersede_plan(plan, reason="Consignee moved collection forward")
        self.assertEqual(new_plan.version, 2)
        self.assertEqual(new_plan.supersedes_id, plan.pk)
        # The old version is retained, not edited.
        plan.refresh_from_db()
        self.assertEqual(plan.status, ConsolidationPlan.Status.PUBLISHED)

    def test_a_departed_trip_cannot_be_replanned(self):
        """"Once a driver begins a route, preserve completed stops and
        communicate any remaining-stop changes clearly." """
        a = self._shipment(self.shipper_a, "A", weight=200)
        trip = self._trip()
        publish_plan(validate_plan(propose_consolidation(
            organization=self.org, trip=trip, shipments=[a]
        )))
        trip.started_at = timezone.now()
        trip.save(update_fields=["started_at"])
        with self.assertRaises(FreightError) as ctx:
            propose_consolidation(organization=self.org, trip=trip, shipments=[a])
        self.assertIn(REASON_TRIP_DEPARTED, str(ctx.exception))
        with self.assertRaises(FreightError):
            supersede_plan(
                trip.consolidation_plans.get(status=ConsolidationPlan.Status.PUBLISHED),
                reason="Too late",
            )


class CoverageFloorTestCase(ConsolidationFixture):
    """The owner's decision, applied as a departure gate: each shipper pays its
    own quote, and the trip only leaves if the revenue covers its cost."""

    def _loaded_trip(self, cost_minor, shipment_totals):
        shipments = [
            self._shipment(None, f"S{i}", weight=100, total=total)
            for i, total in enumerate(shipment_totals)
        ]
        trip = self._trip()
        manifest = build_manifest(
            trip=trip, packages=[p for s in shipments for p in s.packages.all()]
        )
        scanned = {
            line.label: ManifestLine.ScanState.SCANNED for line in manifest.lines.all()
        }
        reconcile_manifest(manifest=manifest, scanned=scanned)
        release_manifest(manifest=manifest)
        plan = propose_consolidation(organization=self.org, trip=trip, shipments=shipments)
        publish_plan(validate_plan(plan))
        trip.trip_cost_minor = cost_minor
        trip.save(update_fields=["trip_cost_minor"])
        return trip, shipments

    def test_a_covered_trip_departs(self):
        trip, _ = self._loaded_trip(cost_minor=60_000, shipment_totals=[40_000, 40_000])
        coverage = assess_coverage(trip)
        self.assertTrue(coverage["covered"])
        self.assertEqual(coverage["committed_minor"], 80_000)
        self.assertEqual(coverage["surplus_minor"], 20_000)

        trip = confirm_departure(trip)
        self.assertEqual(trip.status, FreightTrip.Status.IN_TRANSIT)
        self.assertIsNotNone(trip.started_at)
        self.assertTrue(trip.has_revenue_load)

    def test_a_shortfall_blocks_departure_and_is_reported(self):
        trip, _ = self._loaded_trip(cost_minor=100_000, shipment_totals=[40_000, 30_000])
        coverage = assess_coverage(trip)
        self.assertFalse(coverage["covered"])
        self.assertEqual(coverage["shortfall_minor"], 30_000)
        with self.assertRaises(FreightError) as ctx:
            confirm_departure(trip)
        self.assertIn("falls short", str(ctx.exception))
        trip.refresh_from_db()
        self.assertNotEqual(trip.status, FreightTrip.Status.IN_TRANSIT)

    def test_exactly_covering_revenue_departs(self):
        trip, _ = self._loaded_trip(cost_minor=70_000, shipment_totals=[40_000, 30_000])
        self.assertTrue(assess_coverage(trip)["covered"])
        self.assertEqual(confirm_departure(trip).status, FreightTrip.Status.IN_TRANSIT)

    def test_departure_needs_a_released_manifest(self):
        a = self._shipment(self.shipper_a, "A", weight=100, total=90_000)
        trip = self._trip()
        trip.trip_cost_minor = 50_000
        trip.save(update_fields=["trip_cost_minor"])
        publish_plan(validate_plan(propose_consolidation(
            organization=self.org, trip=trip, shipments=[a]
        )))
        with self.assertRaises(FreightError) as ctx:
            confirm_departure(trip)
        self.assertIn("Release the manifest", str(ctx.exception))

    def test_departure_needs_a_published_plan(self):
        a = self._shipment(self.shipper_a, "A", weight=100, total=90_000)
        trip = self._trip()
        manifest = build_manifest(
            trip=trip, packages=list(a.packages.all())
        )
        reconcile_manifest(
            manifest=manifest,
            scanned={line.label: ManifestLine.ScanState.SCANNED for line in manifest.lines.all()},
        )
        release_manifest(manifest=manifest)
        trip.trip_cost_minor = 50_000
        trip.save(update_fields=["trip_cost_minor"])
        with self.assertRaises(FreightError) as ctx:
            confirm_departure(trip)
        self.assertIn("Publish a consolidation plan", str(ctx.exception))

    def test_a_trip_cannot_depart_twice(self):
        trip, _ = self._loaded_trip(cost_minor=10_000, shipment_totals=[40_000])
        confirm_departure(trip)
        with self.assertRaises(FreightError):
            confirm_departure(trip)

    def test_packages_are_in_transit_after_departure(self):
        trip, shipments = self._loaded_trip(cost_minor=10_000, shipment_totals=[40_000])
        confirm_departure(trip)
        for shipment in shipments:
            for package in shipment.packages.all():
                package.refresh_from_db()
                self.assertEqual(package.status, Package.Status.IN_TRANSIT)


class BackhaulTestCase(ConsolidationFixture):
    """Backhaul: find cargo for a leg that would otherwise run empty, and
    measure empty running the way the blueprint defines it."""

    def _completed_trip(self, *, origin, destination, distance_km, revenue_load, cost=0):
        trip = create_freight_trip(
            organization=self.org,
            vehicle=self.truck,
            driver=self.driver,
            service=self.service,
            origin=origin,
            destination=destination,
            planned_start=self.start,
            planned_end=self.end,
        )
        trip.status = FreightTrip.Status.COMPLETED
        trip.distance_km = Decimal(distance_km)
        trip.has_revenue_load = revenue_load
        trip.trip_cost_minor = cost
        trip.save(
            update_fields=["status", "distance_km", "has_revenue_load", "trip_cost_minor"]
        )
        return trip

    def test_a_vehicle_at_the_market_is_a_backhaul_opportunity(self):
        self._completed_trip(
            origin=self.farm, destination=self.market, distance_km="120", revenue_load=True
        )
        opportunities = find_backhaul_opportunities(
            organization=self.org,
            origin=self.market,
            destination=self.farm,
            window_start=self.end,
            window_end=self.end + timedelta(hours=10),
        )
        self.assertEqual(len(opportunities), 1)
        self.assertEqual(opportunities[0]["vehicle"], self.truck)
        self.assertGreater(opportunities[0]["free_weight_kg"], 0)

    def test_a_full_vehicle_has_no_opportunity(self):
        trip = self._planned_trip()
        manifest = build_manifest(
            trip=trip,
            packages=[
                self._shipment(None, "Big", weight=1000, volume=10).packages.get()
            ],
        )
        reconcile_manifest(
            manifest=manifest,
            scanned={line.label: ManifestLine.ScanState.SCANNED for line in manifest.lines.all()},
        )
        trip.status = FreightTrip.Status.COMPLETED
        trip.distance_km = Decimal("120")
        trip.has_revenue_load = True
        trip.save(update_fields=["status", "distance_km", "has_revenue_load"])
        self.assertEqual(
            find_backhaul_opportunities(
                organization=self.org,
                origin=self.market,
                destination=self.farm,
                window_start=self.end,
                window_end=self.end + timedelta(hours=10),
            ),
            [],
        )
        self.assertIsNotNone(manifest)

    def _planned_trip(self):
        return create_freight_trip(
            organization=self.org,
            vehicle=self.truck,
            driver=self.driver,
            service=self.service,
            origin=self.farm,
            destination=self.market,
            planned_start=self.start,
            planned_end=self.end,
        )

    def test_a_blocked_vehicle_is_not_an_opportunity(self):
        self._completed_trip(
            origin=self.farm, destination=self.market, distance_km="120", revenue_load=True
        )
        self.truck.status = Vehicle.Status.BLOCKED
        self.truck.save(update_fields=["status", "updated_at"])
        self.assertEqual(
            find_backhaul_opportunities(
                organization=self.org,
                origin=self.market,
                destination=self.farm,
                window_start=self.end,
                window_end=self.end + timedelta(hours=10),
            ),
            [],
        )

    def test_a_window_that_closes_too_soon_offers_nothing(self):
        self._completed_trip(
            origin=self.farm, destination=self.market, distance_km="120", revenue_load=True
        )
        self.assertEqual(
            find_backhaul_opportunities(
                organization=self.org,
                origin=self.market,
                destination=self.farm,
                window_start=self.end,
                window_end=self.end + timedelta(hours=1),
            ),
            [],
        )

    def test_empty_running_is_distance_without_revenue_over_measured(self):
        """"Empty running: distance without the defined revenue load divided by
        measured distance" (p. 23)."""
        self._completed_trip(
            origin=self.farm, destination=self.market, distance_km="100", revenue_load=True
        )
        self._completed_trip(
            origin=self.market, destination=self.farm, distance_km="300", revenue_load=False
        )
        report = empty_running_report(organization=self.org, since=self.start - timedelta(days=1))
        self.assertEqual(report["measured_distance_km"], Decimal("400"))
        self.assertEqual(report["empty_distance_km"], Decimal("300"))
        self.assertEqual(report["empty_running_ratio"], Decimal("0.7500"))

    def test_empty_running_is_zero_with_no_trips(self):
        report = empty_running_report(organization=self.org, since=self.start)
        self.assertEqual(report["trips"], 0)
        self.assertEqual(report["empty_running_ratio"], Decimal("0"))


class ConsolidationApiTestCase(ConsolidationFixture):
    def setUp(self):
        super().setUp()
        self.staff_client = APIClient()
        self.staff_client.force_authenticate(self.staff)

    def test_propose_validate_publish_over_http(self):
        a = self._shipment(self.shipper_a, "A", weight=200, total=40_000)
        trip = self._trip()
        response = self.staff_client.post(
            f"/api/v1/freight-trips/{trip.pk}/consolidation/propose/",
            {"shipments": [str(a.pk)]},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["version"], 1)
        self.assertEqual(response.data["accepted_count"], 1)
        self.assertEqual(response.data["committed_revenue_minor"], 40_000)
        plan_id = response.data["id"]

        response = self.staff_client.post(
            f"/api/v1/consolidation-plans/{plan_id}/validate/", {}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], ConsolidationPlan.Status.VALIDATED)

        response = self.staff_client.post(
            f"/api/v1/consolidation-plans/{plan_id}/publish/", {}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], ConsolidationPlan.Status.PUBLISHED)

    def test_publishing_an_unvalidated_plan_is_refused_over_http(self):
        a = self._shipment(self.shipper_a, "A", weight=200)
        trip = self._trip()
        plan = propose_consolidation(organization=self.org, trip=trip, shipments=[a])
        response = self.staff_client.post(
            f"/api/v1/consolidation-plans/{plan.pk}/publish/", {}, format="json"
        )
        self.assertEqual(response.status_code, 400)

    def test_coverage_endpoint(self):
        a = self._shipment(self.shipper_a, "A", weight=200, total=40_000)
        trip = self._trip()
        trip.trip_cost_minor = 60_000
        trip.save(update_fields=["trip_cost_minor"])
        manifest = build_manifest(trip=trip, packages=list(a.packages.all()))
        reconcile_manifest(
            manifest=manifest,
            scanned={line.label: ManifestLine.ScanState.SCANNED for line in manifest.lines.all()},
        )
        release_manifest(manifest=manifest)
        publish_plan(validate_plan(propose_consolidation(
            organization=self.org, trip=trip, shipments=[a]
        )))
        response = self.staff_client.get(f"/api/v1/freight-trips/{trip.pk}/coverage/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["committed_minor"], 40_000)
        self.assertEqual(response.data["shortfall_minor"], 20_000)
        self.assertFalse(response.data["covered"])

    def test_departure_is_blocked_over_http(self):
        a = self._shipment(self.shipper_a, "A", weight=200, total=40_000)
        trip = self._trip()
        trip.trip_cost_minor = 90_000
        trip.save(update_fields=["trip_cost_minor"])
        manifest = build_manifest(trip=trip, packages=list(a.packages.all()))
        reconcile_manifest(
            manifest=manifest,
            scanned={line.label: ManifestLine.ScanState.SCANNED for line in manifest.lines.all()},
        )
        release_manifest(manifest=manifest)
        publish_plan(validate_plan(propose_consolidation(
            organization=self.org, trip=trip, shipments=[a]
        )))
        response = self.staff_client.post(
            f"/api/v1/freight-trips/{trip.pk}/depart/", {}, format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("falls short", response.data["detail"])

    def test_empty_running_report_endpoint(self):
        self._completed_report_trip(distance="100", revenue=True)
        self._completed_report_trip(distance="100", revenue=False)
        response = self.staff_client.get(
            "/api/v1/reports/empty-running/", {"organization": str(self.org.pk)}
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["empty_running_ratio"], "0.5000")

    def _completed_report_trip(self, *, distance, revenue):
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
        trip.status = FreightTrip.Status.COMPLETED
        trip.distance_km = Decimal(distance)
        trip.has_revenue_load = revenue
        trip.save(update_fields=["status", "distance_km", "has_revenue_load"])
        return trip
