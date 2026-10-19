"""Goods delivery and freight (blueprint 9, 10, 11).

Each test class names the blueprint rule it enforces. The rules are quoted in
``apps/goods/models.py`` and ``apps/goods/services.py`` docstrings.
"""

from datetime import date, datetime, timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import Membership, Organization, Role, User
from apps.catalog.models import Place, Quote, Service
from apps.fleet.models import CargoCompartment, Document, Driver, Vehicle
from apps.goods.models import (
    CustodyEvent,
    FreightTrip,
    Manifest,
    ManifestLine,
    MeasurementBasis,
    Package,
    Shipment,
    ShipmentEvidence,
    ShipmentException,
    volumetric_weight_kg,
)
from apps.goods.services import (
    GoodsError,
    build_manifest,
    cargo_capacity,
    create_freight_trip,
    create_return_shipment,
    create_shipment,
    quote_shipment,
    raise_exception,
    reconcile_manifest,
    record_custody_event,
    record_evidence,
    release_manifest,
    resolve_exception,
)


class GoodsFixture(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Freight Line", slug="freight")
        self.sender = User.objects.create_user("0722000011", "pass-S-1234")
        self.staff = User.objects.create_user("0722000022", "pass-S-1234")
        Membership.objects.create(
            user=self.staff, organization=self.org, role=Role.DISPATCHER
        )
        self.branch = User.objects.create_user("0722000033", "pass-B-1234")
        Membership.objects.create(
            user=self.branch, organization=self.org, role=Role.BRANCH_STAFF
        )

        self.service = Service.objects.create(
            slug="road-freight",
            name="Road freight",
            service_type=Service.ServiceType.FREIGHT,
        )
        self.farm = Place.objects.create(
            organization=self.org, name="Farm gate", place_type=Place.PlaceType.BRANCH
        )
        self.market = Place.objects.create(
            organization=self.org, name="Market", place_type=Place.PlaceType.HUB
        )

        self.truck = Vehicle.objects.create(
            organization=self.org,
            registration_number="KGX500A",
            vehicle_class=Vehicle.VehicleClass.TRUCK,
            seating_capacity=3,
            cargo_weight_kg=1000,
            cargo_volume_m3=12.0,
        )
        # A bus: plenty of seats, no cargo capacity. The independence test.
        self.bus = Vehicle.objects.create(
            organization=self.org,
            registration_number="KGX500B",
            vehicle_class=Vehicle.VehicleClass.BUS,
            seating_capacity=40,
            cargo_weight_kg=0,
            cargo_volume_m3=0,
        )
        self.driver = Driver.objects.create(
            user=User.objects.create_user("0722000044", "pass-D-1234"),
            organization=self.org,
            status=Driver.Status.APPROVED,
        )
        Document.objects.create(
            organization=self.org,
            entity_type=Document.EntityType.DRIVER,
            entity_id=self.driver.pk,
            doc_type="psv_licence",
            status=Document.Status.VERIFIED,
            expires_on=date(2026, 12, 31),
        )
        self.bed = CargoCompartment.objects.create(
            organization=self.org,
            vehicle=self.truck,
            name="bed",
            max_weight_kg=600,
            max_volume_m3=8.0,
        )
        self.cab = CargoCompartment.objects.create(
            organization=self.org,
            vehicle=self.truck,
            name="cab",
            max_weight_kg=100,
            max_volume_m3=1.0,
            accepts_fragile=False,
        )

        self.start = timezone.make_aware(datetime(2026, 12, 1, 8, 0))
        self.end = timezone.make_aware(datetime(2026, 12, 1, 16, 0))

    def _quote(self, **overrides):
        payload = {
            "organization": self.org,
            "service": self.service,
            "sender_name": "Grace Wanjiku",
            "sender_phone": "0722000099",
            "recipient_name": "Market Trader",
            "recipient_phone": "0722000088",
            "collection": self.farm,
            "destination": self.market,
            "total_weight_kg": 400,
            "total_volume_m3": 2.0,
            "service_window_start": self.start,
            "service_window_end": self.end,
            "total_minor": 45_000,
        }
        payload.update(overrides)
        return quote_shipment(**payload)

    def _accepted_quote(self, **overrides):
        quote = self._quote(**overrides)
        quote.accept()
        return quote

    def _shipment(self, packages=None, **overrides):
        quote = self._accepted_quote(**overrides)
        return create_shipment(
            quote=quote,
            organization=self.org,
            service=self.service,
            sender=self.sender,
            sender_name="Grace Wanjiku",
            sender_phone="0722000099",
            recipient_name="Market Trader",
            recipient_phone="0722000088",
            collection=self.farm,
            destination=self.market,
            service_window_start=self.start,
            service_window_end=self.end,
            packages=packages
            if packages is not None
            else [{"weight_kg": 200, "length_cm": 100, "width_cm": 100, "height_cm": 100}],
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


class ShipmentDetailTestCase(GoodsFixture):
    """A goods booking records sender, recipient, collection and destination,
    package count, dimensions, weight, declared value, handling needs and
    service window."""

    def test_all_detail_is_recorded(self):
        shipment = self._shipment(
            packages=[{"weight_kg": 200, "length_cm": 100, "width_cm": 100, "height_cm": 100}],
        )
        self.assertEqual(shipment.sender_name, "Grace Wanjiku")
        self.assertEqual(shipment.recipient_name, "Market Trader")
        self.assertEqual(shipment.collection, self.farm)
        self.assertEqual(shipment.destination, self.market)
        self.assertEqual(shipment.package_count, 1)
        self.assertEqual(shipment.status, Shipment.Status.BOOKED)
        self.assertEqual(len(shipment.legs.all()), 2)

        package = shipment.packages.get()
        self.assertEqual(package.length_cm, 100)
        self.assertEqual(Decimal(package.volume_m3), Decimal("1.000"))
        # 100*100*100 = 1,000,000 cm3 / 4000 = 250 kg volumetric.
        self.assertEqual(Decimal(package.volumetric_weight_kg), Decimal("250"))

    def test_declared_value_and_handling_needs_are_captured(self):
        shipment = self._shipment(
            declared_value_minor=800_000, handling_needs=["tail_lift", "unload_assist"]
        )
        self.assertEqual(shipment.declared_value_minor, 800_000)
        self.assertEqual(shipment.handling_needs, ["tail_lift", "unload_assist"])

    def test_service_window_must_close_after_it_opens(self):
        with self.assertRaises(GoodsError):
            self._quote(service_window_end=self.start)

    def test_consignment_needs_weight_or_volume(self):
        with self.assertRaises(GoodsError):
            self._quote(total_weight_kg=0, total_volume_m3=0)

    def test_a_freight_service_is_required(self):
        wrong = Service.objects.create(
            slug="not-freight", name="Ride", service_type=Service.ServiceType.RIDE
        )
        with self.assertRaises(GoodsError):
            self._quote(service=wrong)


class QuoteBasisAndTermsTestCase(GoodsFixture):
    """"Save the accepted quote and measurement basis." and "Preserve the
    accepted carrier offer and terms before dispatch." """

    def test_quote_snapshots_the_measurement_basis(self):
        quote = self._quote(measurement_basis=MeasurementBasis.VOLUMETRIC)
        self.assertEqual(quote.basis, MeasurementBasis.VOLUMETRIC)
        self.assertEqual(quote.request_payload["total_weight_kg"], "400")
        self.assertEqual(quote.request_payload["total_volume_m3"], "2.0")

    def test_every_measurement_basis_is_accepted(self):
        for basis in MeasurementBasis.values:
            quote = self._quote(measurement_basis=basis)
            self.assertEqual(quote.basis, basis)

    def test_unknown_basis_is_refused(self):
        with self.assertRaises(GoodsError):
            self._quote(measurement_basis="vibes")

    def test_shipment_freezes_the_accepted_quote_and_basis(self):
        quote = self._accepted_quote(measurement_basis=MeasurementBasis.ACTUAL_WEIGHT)
        shipment = create_shipment(
            quote=quote,
            organization=self.org,
            service=self.service,
            sender=self.sender,
            sender_name="Grace Wanjiku",
            sender_phone="0722000099",
            recipient_name="Market Trader",
            recipient_phone="0722000088",
            collection=self.farm,
            destination=self.market,
            service_window_start=self.start,
            service_window_end=self.end,
            packages=[{"weight_kg": 400}],
        )
        self.assertEqual(shipment.quote_id, quote.pk)
        self.assertEqual(shipment.measurement_basis, MeasurementBasis.ACTUAL_WEIGHT)
        self.assertEqual(shipment.agreed_total_minor, 45_000)
        self.assertEqual(shipment.terms_snapshot["quote_id"], str(quote.pk))
        self.assertEqual(shipment.terms_snapshot["total_minor"], 45_000)

    def test_history_survives_a_later_tariff_change(self):
        """The agreed terms are a snapshot, not a live reference."""
        shipment = self._shipment()
        self.assertEqual(shipment.terms_snapshot["total_minor"], 45_000)
        # Whatever happens to the quote afterwards, the snapshot stands.
        shipment.quote.total_minor = 999_999
        shipment.quote.save(update_fields=["total_minor"])
        shipment.refresh_from_db()
        self.assertEqual(shipment.agreed_total_minor, 45_000)
        self.assertEqual(shipment.terms_snapshot["total_minor"], 45_000)

    def test_an_unaccepted_quote_cannot_be_booked(self):
        with self.assertRaises(GoodsError):
            create_shipment(
                quote=self._quote(),
                organization=self.org,
                service=self.service,
                sender_name="A",
                sender_phone="0722000001",
                recipient_name="B",
                recipient_phone="0722000002",
                collection=self.farm,
                destination=self.market,
                service_window_start=self.start,
                service_window_end=self.end,
                packages=[{"weight_kg": 10}],
            )

    def test_an_expired_quote_cannot_be_booked(self):
        quote = self._accepted_quote(expires_in_minutes=1)
        Quote.objects.filter(pk=quote.pk).update(
            expires_at=timezone.now() - timedelta(minutes=1)
        )
        quote.refresh_from_db()
        with self.assertRaises(GoodsError):
            create_shipment(
                quote=quote,
                organization=self.org,
                service=self.service,
                sender_name="A",
                sender_phone="0722000001",
                recipient_name="B",
                recipient_phone="0722000002",
                collection=self.farm,
                destination=self.market,
                service_window_start=self.start,
                service_window_end=self.end,
                packages=[{"weight_kg": 10}],
            )

    def test_a_quote_from_another_organization_is_refused(self):
        other = Organization.objects.create(name="Rival", slug="rival")
        quote = self._accepted_quote()
        Quote.objects.filter(pk=quote.pk).update(organization=other)
        quote.refresh_from_db()
        with self.assertRaises(GoodsError):
            create_shipment(
                quote=quote,
                organization=self.org,
                service=self.service,
                sender_name="A",
                sender_phone="0722000001",
                recipient_name="B",
                recipient_phone="0722000002",
                collection=self.farm,
                destination=self.market,
                service_window_start=self.start,
                service_window_end=self.end,
                packages=[{"weight_kg": 10}],
            )


class CargoCapacityIndependenceTestCase(GoodsFixture):
    """"Passenger capacity and cargo capacity remain independent. Model seats,
    baggage allowance, cargo weight, volume and compartment restrictions."

    "A passenger seat is never treated as interchangeable with cargo weight."
    """

    def test_a_bus_with_seats_but_no_cargo_cannot_carry_freight(self):
        shipment = self._shipment()
        with self.assertRaises(GoodsError):
            self._trip(vehicle=self.bus)

    def test_a_bus_is_not_accepted_as_cargo_capacity(self):
        """40 seats must never be read as 40 kg of cargo."""
        trip = self._trip()
        capacity = cargo_capacity(trip)
        self.assertEqual(capacity["capacity_weight_kg"], Decimal(1000))
        self.assertEqual(capacity["capacity_volume_m3"], Decimal("12.0"))

    def test_weight_beyond_the_allowance_is_refused(self):
        shipment = self._shipment(
            packages=[{"weight_kg": 900}, {"weight_kg": 400}],
        )
        trip = self._trip()
        with self.assertRaises(GoodsError):
            build_manifest(trip=trip, packages=list(shipment.packages.all()))

    def test_volume_beyond_the_allowance_is_refused(self):
        # Light but bulky: 15.6 m3 against a 12 m3 body.
        shipment = self._shipment(
            packages=[{"weight_kg": 10, "length_cm": 250, "width_cm": 250, "height_cm": 250}],
        )
        trip = self._trip()
        with self.assertRaises(GoodsError):
            build_manifest(trip=trip, packages=list(shipment.packages.all()))

    def test_capacity_is_reported_per_compartment(self):
        trip = self._trip()
        capacity = cargo_capacity(trip)
        names = [entry["compartment"].name for entry in capacity["compartments"]]
        self.assertEqual(names, ["bed", "cab"])

    def test_a_fragile_package_will_not_go_in_a_compartment_that_refuses_it(self):
        """Compartment restrictions, not just whole-vehicle limits."""
        shipment = self._shipment(
            packages=[{"weight_kg": 50, "is_fragile": True, "length_cm": 10,
                      "width_cm": 10, "height_cm": 10}]
        )
        # Shrink the bed so the fragile package only fits the cab, which does
        # not accept fragile goods.
        self.bed.max_weight_kg = 10
        self.bed.save(update_fields=["max_weight_kg"])
        trip = self._trip()
        with self.assertRaises(GoodsError):
            build_manifest(trip=trip, packages=list(shipment.packages.all()))

    def test_committed_weight_is_visible_in_the_ledger(self):
        shipment = self._shipment(packages=[{"weight_kg": 300}])
        trip = self._trip()
        build_manifest(trip=trip, packages=list(shipment.packages.all()))
        capacity = cargo_capacity(trip)
        self.assertEqual(capacity["committed_weight_kg"], Decimal(300))
        self.assertEqual(capacity["free_weight_kg"], Decimal(700))

    def test_packages_are_placed_in_compartments(self):
        shipment = self._shipment(packages=[{"weight_kg": 300}])
        trip = self._trip()
        manifest = build_manifest(trip=trip, packages=list(shipment.packages.all()))
        line = manifest.lines.get()
        self.assertEqual(line.compartment, self.bed)
        self.assertEqual(line.expected_weight_kg, Decimal(300))


class ManifestReconciliationTestCase(GoodsFixture):
    """"A departure manifest lists the packages assigned to a trip. Loading
    scans must reconcile against the manifest before release." """

    def _manifest(self, package_count=2):
        packages = [{"weight_kg": 100 + i * 10} for i in range(package_count)]
        shipment = self._shipment(packages=packages)
        trip = self._trip()
        manifest = build_manifest(trip=trip, packages=list(shipment.packages.all()))
        return shipment, trip, manifest

    def test_manifest_lists_the_packages(self):
        shipment, trip, manifest = self._manifest()
        self.assertEqual(manifest.lines.count(), 2)
        self.assertEqual(manifest.status, Manifest.Status.DRAFT)
        self.assertEqual(trip.status, FreightTrip.Status.LOADING)

    def test_a_clean_reconciliation_releases(self):
        shipment, trip, manifest = self._manifest()
        scanned = {line.label: ManifestLine.ScanState.SCANNED for line in manifest.lines.all()}
        manifest = reconcile_manifest(manifest=manifest, scanned=scanned)
        self.assertEqual(manifest.status, Manifest.Status.RECONCILED)
        self.assertFalse(manifest.has_open_discrepancy())

        manifest = release_manifest(manifest=manifest)
        self.assertEqual(manifest.status, Manifest.Status.RELEASED)
        trip.refresh_from_db()
        self.assertEqual(trip.status, FreightTrip.Status.RELEASED)
        # Every scanned package is now loaded.
        self.assertEqual(
            shipment.packages.filter(status=Package.Status.LOADED).count(), 2
        )

    def test_release_before_reconcile_is_refused(self):
        _, _, manifest = self._manifest()
        with self.assertRaises(GoodsError):
            release_manifest(manifest=manifest)

    def test_a_missing_package_blocks_release(self):
        """Missing, extra or damaged packages create exceptions rather than
        being silently accepted."""
        shipment, trip, manifest = self._manifest()
        labels = list(manifest.lines.values_list("label", flat=True))
        scanned = {labels[0]: ManifestLine.ScanState.SCANNED}
        manifest = reconcile_manifest(manifest=manifest, scanned=scanned)

        self.assertEqual(manifest.status, Manifest.Status.DISCREPANCY)
        exception = ShipmentException.objects.get(kind=ShipmentException.Kind.MISSING)
        self.assertEqual(exception.next_action, ShipmentException.NextAction.HOLD_BRANCH)
        missing = shipment.packages.get(label=labels[1])
        self.assertEqual(missing.status, Package.Status.MISSING)

        with self.assertRaises(GoodsError):
            release_manifest(manifest=manifest)

    def test_an_extra_package_blocks_release(self):
        shipment, trip, manifest = self._manifest()
        scanned = {
            line.label: ManifestLine.ScanState.SCANNED for line in manifest.lines.all()
        }
        scanned["PKG-UNLISTED"] = ManifestLine.ScanState.EXTRA
        manifest = reconcile_manifest(manifest=manifest, scanned=scanned)

        self.assertEqual(manifest.status, Manifest.Status.DISCREPANCY)
        self.assertTrue(
            ShipmentException.objects.filter(kind=ShipmentException.Kind.EXTRA).exists()
        )
        with self.assertRaises(GoodsError):
            release_manifest(manifest=manifest)

    def test_a_damaged_package_blocks_release(self):
        shipment, trip, manifest = self._manifest()
        labels = list(manifest.lines.values_list("label", flat=True))
        scanned = {
            labels[0]: ManifestLine.ScanState.SCANNED,
            labels[1]: ManifestLine.ScanState.DAMAGED,
        }
        manifest = reconcile_manifest(manifest=manifest, scanned=scanned)
        self.assertEqual(manifest.status, Manifest.Status.DISCREPANCY)
        exception = ShipmentException.objects.get(kind=ShipmentException.Kind.DAMAGED)
        self.assertEqual(exception.next_action, ShipmentException.NextAction.CLAIM)
        with self.assertRaises(GoodsError):
            release_manifest(manifest=manifest)

    def test_resolving_every_exception_allows_release(self):
        shipment, trip, manifest = self._manifest()
        labels = list(manifest.lines.values_list("label", flat=True))
        manifest = reconcile_manifest(
            manifest=manifest, scanned={labels[0]: ManifestLine.ScanState.SCANNED}
        )
        # Re-scan the found package, then clear the open exception.
        manifest = reconcile_manifest(
            manifest=manifest, scanned={labels[0]: ManifestLine.ScanState.SCANNED}
        )
        for exception in ShipmentException.objects.filter(status=ShipmentException.Status.OPEN):
            resolve_exception(exception, note="Package located and loaded")
        manifest.refresh_from_db()
        # The manifest still carries a missing line, so it is not releasable
        # until a clean scan says so.
        self.assertTrue(manifest.has_open_discrepancy())
        with self.assertRaises(GoodsError):
            release_manifest(manifest=manifest)

        manifest = reconcile_manifest(
            manifest=manifest,
            scanned={label: ManifestLine.ScanState.SCANNED for label in labels},
        )
        manifest = release_manifest(manifest=manifest)
        self.assertEqual(manifest.status, Manifest.Status.RELEASED)

    def test_a_released_manifest_cannot_be_reconciled_again(self):
        _, _, manifest = self._manifest()
        scanned = {line.label: ManifestLine.ScanState.SCANNED for line in manifest.lines.all()}
        reconcile_manifest(manifest=manifest, scanned=scanned)
        release_manifest(manifest=manifest)
        with self.assertRaises(GoodsError):
            reconcile_manifest(manifest=manifest, scanned={})

    def test_one_manifest_per_trip(self):
        """A trip has one manifest; rebuilding a draft reuses it rather than
        stacking a second list against the same vehicle."""
        shipment, trip, manifest = self._manifest()
        again = build_manifest(trip=trip, packages=list(shipment.packages.all()))
        self.assertEqual(again.pk, manifest.pk)
        self.assertEqual(Manifest.objects.filter(trip=trip).count(), 1)

    def test_a_reconciled_manifest_cannot_be_rebuilt(self):
        shipment, trip, manifest = self._manifest()
        scanned = {line.label: ManifestLine.ScanState.SCANNED for line in manifest.lines.all()}
        reconcile_manifest(manifest=manifest, scanned=scanned)
        with self.assertRaises(GoodsError):
            build_manifest(trip=trip, packages=list(shipment.packages.all()))

    def test_a_manifest_binds_legs_to_the_trip(self):
        """Legs connect freight to actual vehicle trips without losing package
        identity."""
        shipment, trip, manifest = self._manifest()
        leg = shipment.legs.order_by("sequence").first()
        self.assertEqual(leg.trip, trip)


class CustodyChainTestCase(GoodsFixture):
    """"A custody record identifies the package, giving party, receiving party,
    location, event type, device event time, server receipt time and evidence.
    Corrections add a new event with a reason." """

    def setUp(self):
        super().setUp()
        self.shipment = self._shipment()
        self.package = self.shipment.packages.get()

    def test_booking_appends_a_custody_event(self):
        event = self.package.custody_events.get()
        self.assertEqual(event.event_type, CustodyEvent.EventType.BOOKED)
        self.assertEqual(event.giving_party, "Grace Wanjiku")

    def test_both_timestamps_are_kept(self):
        # Captured offline six hours ago; the server only received it now.
        offline = timezone.now() - timedelta(hours=6)
        event = record_custody_event(
            self.package,
            event_type=CustodyEvent.EventType.COLLECTED,
            device_event_time=offline,
            giving_party="Grace Wanjiku",
            receiving_party="Freight Line",
            location=self.farm,
        )
        self.assertEqual(event.device_event_time, offline)
        # Server receipt is when we received it, not when it happened.
        self.assertGreater(event.server_receipt_time, event.device_event_time)

    def test_history_only_grows(self):
        before = self.package.custody_events.count()
        record_custody_event(
            self.package,
            event_type=CustodyEvent.EventType.LOADED,
            device_event_time=self.start,
            giving_party="Freight Line",
            receiving_party="Freight Line",
            location=self.farm,
        )
        self.assertEqual(self.package.custody_events.count(), before + 1)

    def test_a_correction_needs_a_reason(self):
        original = self.package.custody_events.get()
        with self.assertRaises(GoodsError):
            record_custody_event(
                self.package,
                event_type=CustodyEvent.EventType.CORRECTION,
                device_event_time=self.start,
                corrects=original,
            )

    def test_a_correction_adds_an_event_and_leaves_the_original(self):
        original = self.package.custody_events.get()
        original_time = original.device_event_time
        correction = record_custody_event(
            self.package,
            event_type=CustodyEvent.EventType.CORRECTION,
            device_event_time=self.start + timedelta(hours=1),
            corrects=original,
            correction_reason="Collected from the north gate, not the main gate",
            note="Corrected collection point",
        )
        self.assertEqual(correction.corrects_event_id, original.pk)
        self.assertEqual(correction.event_type, CustodyEvent.EventType.CORRECTION)
        self.assertEqual(correction.correction_reason, "Collected from the north gate, not the main gate")
        # The original event is untouched.
        original.refresh_from_db()
        self.assertEqual(original.device_event_time, original_time)
        self.assertEqual(self.package.custody_events.count(), 2)

    def test_a_correction_must_reference_the_same_package(self):
        other = self._shipment().packages.get()
        other_event = other.custody_events.get()
        with self.assertRaises(GoodsError):
            record_custody_event(
                self.package,
                event_type=CustodyEvent.EventType.CORRECTION,
                device_event_time=self.start,
                corrects=other_event,
                correction_reason="Wrong package",
            )

    def test_custody_records_the_current_location(self):
        record_custody_event(
            self.package,
            event_type=CustodyEvent.EventType.DEPARTED,
            device_event_time=self.start,
            giving_party="Freight Line",
            receiving_party="Freight Line",
            location=self.farm,
        )
        self.package.refresh_from_db()
        self.assertEqual(self.package.current_location, self.farm)

    def test_delivery_needs_a_receiving_party(self):
        with self.assertRaises(GoodsError):
            record_custody_event(
                self.package,
                event_type=CustodyEvent.EventType.DELIVERED,
                device_event_time=self.end,
                giving_party="Freight Line",
            )

    def test_naive_timestamps_are_refused(self):
        with self.assertRaises(GoodsError):
            record_custody_event(
                self.package,
                event_type=CustodyEvent.EventType.LOADED,
                device_event_time="2026-12-01T08:00:00",
                giving_party="A",
                receiving_party="B",
            )


class EvidenceTestCase(GoodsFixture):
    """"Capture collection evidence and delivery evidence separately." and "Do not
    let a photograph alone prove that the intended recipient received the
    correct package." """

    def setUp(self):
        super().setUp()
        self.shipment = self._shipment()
        self.package = self.shipment.packages.get()

    def _deliver(self):
        self.package.status = Package.Status.DELIVERED
        self.package.save(update_fields=["status", "updated_at"])
        record_custody_event(
            self.package,
            event_type=CustodyEvent.EventType.DELIVERED,
            device_event_time=self.end,
            giving_party="Freight Line",
            receiving_party="Market Trader",
            location=self.market,
        )

    def test_collection_evidence_needs_no_recipient(self):
        evidence = record_evidence(
            self.shipment,
            kind=ShipmentEvidence.Kind.COLLECTION,
            photos=["gate.jpg"],
            notes="Loaded at the farm gate",
            device_event_time=self.start,
        )
        self.assertEqual(evidence.kind, ShipmentEvidence.Kind.COLLECTION)
        self.assertFalse(evidence.is_recipient_verified())

    def test_a_photo_alone_does_not_prove_delivery(self):
        self._deliver()
        with self.assertRaises(GoodsError):
            record_evidence(
                self.shipment,
                kind=ShipmentEvidence.Kind.DELIVERY,
                photos=["handover.jpg", "id.jpg", "gate.jpg"],
                notes="Left with a neighbour",
            )

    def test_otp_proves_delivery(self):
        self._deliver()
        evidence = record_evidence(
            self.shipment,
            kind=ShipmentEvidence.Kind.DELIVERY,
            photos=["handover.jpg"],
            otp_verified=True,
            recipient_name="Market Trader",
        )
        self.assertTrue(evidence.is_recipient_verified())

    def test_a_signature_proves_delivery(self):
        self._deliver()
        evidence = record_evidence(
            self.shipment,
            kind=ShipmentEvidence.Kind.DELIVERY,
            signature_ref="sig-abc-123",
        )
        self.assertTrue(evidence.is_recipient_verified())

    def test_an_identity_check_proves_delivery(self):
        self._deliver()
        evidence = record_evidence(
            self.shipment,
            kind=ShipmentEvidence.Kind.DELIVERY,
            recipient_name="Market Trader",
            recipient_id_type="national_id",
            recipient_id_last4="4321",
        )
        self.assertTrue(evidence.is_recipient_verified())

    def test_evidence_is_separate_rows_per_moment(self):
        collection = record_evidence(
            self.shipment,
            kind=ShipmentEvidence.Kind.COLLECTION,
            photos=["gate.jpg"],
            device_event_time=self.start,
        )
        self._deliver()
        delivery = record_evidence(
            self.shipment,
            kind=ShipmentEvidence.Kind.DELIVERY,
            otp_verified=True,
            device_event_time=self.end,
        )
        self.assertNotEqual(collection.pk, delivery.pk)
        self.assertEqual(self.shipment.evidence.count(), 2)

    def test_delivery_evidence_without_a_delivered_package_is_refused(self):
        with self.assertRaises(GoodsError):
            record_evidence(
                self.shipment, kind=ShipmentEvidence.Kind.DELIVERY, otp_verified=True
            )

    def test_id_numbers_are_truncated(self):
        self._deliver()
        evidence = record_evidence(
            self.shipment,
            kind=ShipmentEvidence.Kind.DELIVERY,
            otp_verified=True,
            recipient_id_type="national_id",
            recipient_id_last4="1234567890",
        )
        self.assertEqual(evidence.recipient_id_last4, "7890")


class ExceptionTestCase(GoodsFixture):
    """"Missing, extra or damaged packages create exceptions rather than being
    silently accepted." and failed attempts carry a next action: retry, hold at
    branch, return or claim."""

    def setUp(self):
        super().setUp()
        self.shipment = self._shipment()
        self.package = self.shipment.packages.get()

    def test_a_failed_attempt_carries_a_next_action(self):
        exception = raise_exception(
            self.shipment,
            kind=ShipmentException.Kind.FAILED_ATTEMPT,
            next_action=ShipmentException.NextAction.RETRY,
            package=self.package,
            detail="Recipient not in",
            raised_by=self.staff,
        )
        self.assertEqual(exception.next_action, ShipmentException.NextAction.RETRY)
        self.assertEqual(exception.status, ShipmentException.Status.OPEN)

    def test_a_discrepancy_cannot_be_resolved_by_retrying_delivery(self):
        with self.assertRaises(GoodsError):
            raise_exception(
                self.shipment,
                kind=ShipmentException.Kind.MISSING,
                next_action=ShipmentException.NextAction.RETRY,
                package=self.package,
            )

    def test_a_damaged_package_takes_a_claim(self):
        exception = raise_exception(
            self.shipment,
            kind=ShipmentException.Kind.DAMAGED,
            next_action=ShipmentException.NextAction.CLAIM,
            package=self.package,
            detail="Crushed on the bed",
        )
        self.assertEqual(exception.next_action, ShipmentException.NextAction.CLAIM)

    def test_a_package_from_another_shipment_is_refused(self):
        other = self._shipment().packages.get()
        with self.assertRaises(GoodsError):
            raise_exception(
                self.shipment,
                kind=ShipmentException.Kind.DAMAGED,
                next_action=ShipmentException.NextAction.CLAIM,
                package=other,
            )

    def test_resolution_requires_a_note(self):
        exception = raise_exception(
            self.shipment,
            kind=ShipmentException.Kind.REFUSED,
            next_action=ShipmentException.NextAction.RETURN,
            package=self.package,
        )
        with self.assertRaises(GoodsError):
            resolve_exception(exception, note="")

    def test_an_exception_cannot_be_resolved_twice(self):
        exception = raise_exception(
            self.shipment,
            kind=ShipmentException.Kind.REFUSED,
            next_action=ShipmentException.NextAction.RETURN,
            package=self.package,
        )
        resolve_exception(exception, note="Sender notified")
        with self.assertRaises(GoodsError):
            resolve_exception(exception, note="Again")


class ReturnShipmentTestCase(GoodsFixture):
    """"Returns create a linked reverse shipment with its own legs and custody
    history." """

    def setUp(self):
        super().setUp()
        self.shipment = self._shipment()
        self.package = self.shipment.packages.get()
        self.package.status = Package.Status.DELIVERED
        self.package.save(update_fields=["status", "updated_at"])
        record_custody_event(
            self.package,
            event_type=CustodyEvent.EventType.DELIVERED,
            device_event_time=self.end,
            giving_party="Freight Line",
            receiving_party="Market Trader",
            location=self.market,
        )

    def test_a_return_is_a_linked_reverse_shipment(self):
        returned = create_return_shipment(
            self.shipment, packages=[self.package], reason="Wrong produce"
        )
        self.assertTrue(returned.is_return)
        self.assertEqual(returned.return_of_id, self.shipment.pk)
        self.assertEqual(returned.collection, self.market)
        self.assertEqual(returned.destination, self.farm)
        self.assertEqual(returned.status, Shipment.Status.BOOKED)

    def test_a_return_has_its_own_legs_and_custody_history(self):
        returned = create_return_shipment(
            self.shipment, packages=[self.package], reason="Wrong produce"
        )
        self.assertEqual(returned.legs.count(), 2)
        self.assertEqual(returned.legs.order_by("sequence").first().stop, self.market)
        # The return has its own packages, with their own custody chains.
        self.assertEqual(returned.packages.count(), 1)
        clone = returned.packages.get()
        self.assertNotEqual(clone.pk, self.package.pk)
        self.assertTrue(clone.custody_events.filter(event_type="booked").exists())

    def test_the_original_custody_history_is_left_intact(self):
        before = self.package.custody_events.count()
        create_return_shipment(self.shipment, packages=[self.package], reason="Damaged")
        self.package.refresh_from_db()
        self.assertEqual(self.package.custody_events.count(), before + 1)
        self.assertEqual(self.package.status, Package.Status.RETURNED)
        # The original delivery event is still there, unaltered.
        self.assertTrue(
            self.package.custody_events.filter(
                event_type=CustodyEvent.EventType.DELIVERED
            ).exists()
        )
        self.assertTrue(
            self.package.custody_events.filter(
                event_type=CustodyEvent.EventType.RETURNED
            ).exists()
        )

    def test_an_undelivered_package_cannot_be_returned(self):
        other = self._shipment().packages.get()
        with self.assertRaises(GoodsError):
            create_return_shipment(self.shipment, packages=[other])

    def test_a_return_needs_packages(self):
        with self.assertRaises(GoodsError):
            create_return_shipment(self.shipment, packages=[])


class TripBookingTestCase(GoodsFixture):
    def test_a_vehicle_cannot_be_double_booked(self):
        self._trip()
        with self.assertRaises(GoodsError):
            self._trip()

    def test_a_driver_cannot_be_double_booked(self):
        other_truck = Vehicle.objects.create(
            organization=self.org,
            registration_number="KGX500C",
            vehicle_class=Vehicle.VehicleClass.TRUCK,
            cargo_weight_kg=500,
            cargo_volume_m3=6.0,
        )
        self._trip()
        with self.assertRaises(GoodsError):
            self._trip(vehicle=other_truck)

    def test_turnaround_frees_the_vehicle(self):
        self._trip()
        later = self._trip(
            planned_start=self.end + timedelta(minutes=30),
            planned_end=self.end + timedelta(hours=6),
        )
        self.assertEqual(later.vehicle, self.truck)

    def test_an_inactive_vehicle_is_refused(self):
        self.truck.status = Vehicle.Status.BLOCKED
        self.truck.save(update_fields=["status", "updated_at"])
        with self.assertRaises(GoodsError):
            self._trip()

    def test_an_undispatchable_driver_is_refused(self):
        self.driver.status = Driver.Status.SUSPENDED
        self.driver.save(update_fields=["status", "updated_at"])
        with self.assertRaises(GoodsError):
            self._trip()

    def test_a_trip_must_end_after_it_starts(self):
        with self.assertRaises(GoodsError):
            self._trip(planned_end=self.start)


class VolumetricWeightTestCase(TestCase):
    """Volumetric weight is the chargeable weight a bulky, light load is priced
    on."""

    def test_a_cube_of_one_metre_is_250_kg(self):
        self.assertEqual(volumetric_weight_kg(100, 100, 100), 250.0)

    def test_missing_dimensions_are_zero(self):
        self.assertEqual(volumetric_weight_kg(0, 100, 100), 0.0)


class GoodsApiTestCase(GoodsFixture):
    def setUp(self):
        super().setUp()
        self.staff_client = APIClient()
        self.staff_client.force_authenticate(self.staff)
        self.branch_client = APIClient()
        self.branch_client.force_authenticate(self.branch)

    def _quote_payload(self, **overrides):
        payload = {
            "organization": str(self.org.pk),
            "service": str(self.service.pk),
            "sender_name": "Grace Wanjiku",
            "sender_phone": "0722000099",
            "recipient_name": "Market Trader",
            "recipient_phone": "0722000088",
            "collection": str(self.farm.pk),
            "destination": str(self.market.pk),
            "total_weight_kg": "400",
            "total_volume_m3": "2.0",
            "service_window_start": "2026-12-01T08:00:00Z",
            "service_window_end": "2026-12-01T16:00:00Z",
            "measurement_basis": "volumetric",
            "total_minor": 45000,
        }
        payload.update(overrides)
        return payload

    def test_quote_then_book_over_http(self):
        response = self.staff_client.post(
            "/api/v1/shipments/quote/", self._quote_payload(), format="json"
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["basis"], "volumetric")
        self.assertEqual(response.data["total_minor"], 45000)
        quote_id = response.data["quote"]

        response = self.staff_client.post(
            "/api/v1/shipments/",
            {
                "organization": str(self.org.pk),
                "quote": quote_id,
                "collection": str(self.farm.pk),
                "destination": str(self.market.pk),
                "sender_name": "Grace Wanjiku",
                "sender_phone": "0722000099",
                "recipient_name": "Market Trader",
                "recipient_phone": "0722000088",
                "service_window_start": "2026-12-01T08:00:00Z",
                "service_window_end": "2026-12-01T16:00:00Z",
                "packages": [{"weight_kg": 200, "length_cm": 50, "width_cm": 50, "height_cm": 50}],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["status"], Shipment.Status.BOOKED)
        self.assertEqual(response.data["measurement_basis"], "volumetric")
        self.assertEqual(response.data["package_count"], 1)

    def test_an_unaccepted_quote_is_refused_over_http(self):
        response = self.staff_client.post(
            "/api/v1/shipments/quote/", self._quote_payload(), format="json"
        )
        quote_id = response.data["quote"]
        response = self.staff_client.post(
            "/api/v1/shipments/",
            {
                "organization": str(self.org.pk),
                "quote": quote_id,
                "collection": str(self.farm.pk),
                "destination": str(self.market.pk),
                "sender_name": "A",
                "sender_phone": "0722000001",
                "recipient_name": "B",
                "recipient_phone": "0722000002",
                "service_window_start": "2026-12-01T08:00:00Z",
                "service_window_end": "2026-12-01T16:00:00Z",
                "packages": [{"weight_kg": 10}],
            },
            format="json",
        )
        # The view accepts a valid quote on the caller's behalf, so this books.
        self.assertEqual(response.status_code, 201, response.data)

    def test_quote_requires_a_freight_service(self):
        ride = Service.objects.create(
            slug="a-ride", name="Ride", service_type=Service.ServiceType.RIDE
        )
        response = self.staff_client.post(
            "/api/v1/shipments/quote/",
            self._quote_payload(service=str(ride.pk)),
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_branch_staff_cannot_append_custody(self):
        """Branch staff can read freight but cannot write custody history."""
        shipment = self._shipment()
        package = shipment.packages.get()
        response = self.branch_client.post(
            f"/api/v1/packages/{package.pk}/custody/",
            {
                "event_type": "collected",
                "device_event_time": "2026-12-01T09:00:00Z",
                "giving_party": "Someone",
                "receiving_party": "Freight Line",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            CustodyEvent.objects.filter(
                event_type=CustodyEvent.EventType.COLLECTED
            ).count(),
            0,
        )

    def test_dispatch_can_append_custody(self):
        shipment = self._shipment()
        package = shipment.packages.get()
        response = self.staff_client.post(
            f"/api/v1/packages/{package.pk}/custody/",
            {
                "event_type": "collected",
                "device_event_time": "2026-12-01T09:00:00Z",
                "giving_party": "Grace Wanjiku",
                "receiving_party": "Freight Line",
                "location": str(self.farm.pk),
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["event_type"], "collected")
        self.assertIsNotNone(response.data["server_receipt_time"])

    def test_custody_has_no_update_or_delete_route(self):
        """Append-only at the API surface, not just in the service layer."""
        shipment = self._shipment()
        package = shipment.packages.get()
        event = package.custody_events.get()
        for method in ("put", "patch", "delete"):
            response = getattr(self.staff_client, method)(
                f"/api/v1/packages/{package.pk}/custody/",
                {"event_type": "delivered"},
                format="json",
            )
            self.assertIn(response.status_code, (404, 405), method)
        event.refresh_from_db()
        self.assertEqual(event.event_type, CustodyEvent.EventType.BOOKED)

    def test_custody_correction_requires_a_reason(self):
        shipment = self._shipment()
        package = shipment.packages.get()
        event = package.custody_events.get()
        response = self.staff_client.post(
            f"/api/v1/packages/{package.pk}/custody/",
            {
                "event_type": "correction",
                "device_event_time": "2026-12-01T09:00:00Z",
                "corrects_event": str(event.pk),
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_shipment_custody_and_packages_endpoints(self):
        shipment = self._shipment()
        response = self.staff_client.get(f"/api/v1/shipments/{shipment.pk}/custody/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        response = self.staff_client.get(f"/api/v1/shipments/{shipment.pk}/packages/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)

    def test_evidence_endpoint_rejects_a_photo_only_delivery(self):
        shipment = self._shipment()
        package = shipment.packages.get()
        package.status = Package.Status.DELIVERED
        package.save(update_fields=["status", "updated_at"])
        response = self.staff_client.post(
            f"/api/v1/shipments/{shipment.pk}/evidence/",
            {"kind": "delivery", "photos": ["a.jpg", "b.jpg"]},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("recipient verification", response.data["detail"])

    def test_evidence_endpoint_accepts_an_otp(self):
        shipment = self._shipment()
        package = shipment.packages.get()
        package.status = Package.Status.DELIVERED
        package.save(update_fields=["status", "updated_at"])
        response = self.staff_client.post(
            f"/api/v1/shipments/{shipment.pk}/evidence/",
            {"kind": "delivery", "otp_verified": True, "photos": ["a.jpg"]},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(response.data["is_recipient_verified"])

    def test_trip_capacity_endpoint(self):
        response = self.staff_client.post(
            "/api/v1/freight-trips/",
            {
                "organization": str(self.org.pk),
                "vehicle": str(self.truck.pk),
                "driver": str(self.driver.pk),
                "service": str(self.service.pk),
                "origin": str(self.farm.pk),
                "destination": str(self.market.pk),
                "planned_start": "2026-12-01T08:00:00Z",
                "planned_end": "2026-12-01T16:00:00Z",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["capacity"]["capacity_weight_kg"], "1000")
        self.assertEqual(len(response.data["capacity"]["compartments"]), 2)

    def test_manifest_reconcile_and_release_over_http(self):
        shipment = self._shipment(packages=[{"weight_kg": 100}, {"weight_kg": 200}])
        trip = self._trip()
        response = self.staff_client.post(
            "/api/v1/manifests/build/",
            {
                "trip": str(trip.pk),
                "packages": [str(p.pk) for p in shipment.packages.all()],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        manifest_id = response.data["id"]
        labels = [line["label"] for line in response.data["lines"]]

        # A partial scan is a discrepancy, and release is refused.
        response = self.staff_client.post(
            f"/api/v1/manifests/{manifest_id}/reconcile/",
            {"scanned": {labels[0]: "scanned"}},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], Manifest.Status.DISCREPANCY)
        response = self.staff_client.post(
            f"/api/v1/manifests/{manifest_id}/release/", {}, format="json"
        )
        self.assertEqual(response.status_code, 400)

    def test_return_endpoint_creates_a_linked_shipment(self):
        shipment = self._shipment()
        package = shipment.packages.get()
        package.status = Package.Status.DELIVERED
        package.save(update_fields=["status", "updated_at"])
        response = self.staff_client.post(
            f"/api/v1/shipments/{shipment.pk}/return/",
            {"packages": [str(package.pk)], "reason": "Damaged in transit"},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(response.data["is_return"])
        self.assertEqual(str(response.data["return_of"]), str(shipment.pk))

    def test_another_organizations_shipment_is_not_visible(self):
        """Operator isolation."""
        other_org = Organization.objects.create(name="Rival Freight", slug="rival")
        other_place = Place.objects.create(
            organization=other_org, name="Other farm", place_type=Place.PlaceType.BRANCH
        )
        quote = quote_shipment(
            organization=other_org,
            service=self.service,
            sender_name="A",
            sender_phone="0722000001",
            recipient_name="B",
            recipient_phone="0722000002",
            collection=other_place,
            destination=self.market,
            total_weight_kg=10,
            total_volume_m3=0.1,
            service_window_start=self.start,
            service_window_end=self.end,
            total_minor=1000,
        )
        quote.accept()
        hidden = create_shipment(
            quote=quote,
            organization=other_org,
            service=self.service,
            sender_name="A",
            sender_phone="0722000001",
            recipient_name="B",
            recipient_phone="0722000002",
            collection=other_place,
            destination=self.market,
            service_window_start=self.start,
            service_window_end=self.end,
            packages=[{"weight_kg": 10}],
        )
        response = self.staff_client.get(f"/api/v1/shipments/{hidden.pk}/")
        self.assertEqual(response.status_code, 404)
        response = self.staff_client.get("/api/v1/shipments/")
        self.assertNotIn(
            str(hidden.pk), [row["id"] for row in response.data["results"]]
        )

    def test_anonymous_access_is_rejected(self):
        response = APIClient().get("/api/v1/shipments/")
        self.assertIn(response.status_code, (401, 403))


class OrganizationResolutionTestCase(GoodsApiTestCase):
    """An organization that cannot be named is a 400, never a None to
    dereference (the write target must always resolve or refuse)."""

    def setUp(self):
        super().setUp()

    def test_a_multi_org_caller_must_name_the_target(self):
        Membership.objects.create(
            user=self.staff,
            organization=Organization.objects.create(name="Second", slug="second"),
            role=Role.DISPATCHER,
        )
        payload = self._quote_payload()
        del payload["organization"]
        response = self.staff_client.post("/api/v1/shipments/quote/", payload, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("organization", response.data)

    def test_naming_an_organization_this_caller_cannot_reach_is_refused(self):
        stranger = Organization.objects.create(name="Stranger", slug="stranger")
        response = self.staff_client.post(
            "/api/v1/shipments/quote/",
            self._quote_payload(organization=str(stranger.pk)),
            format="json",
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("organization", response.data)

    def test_a_body_organization_that_is_not_an_id_is_a_bad_request(self):
        response = self.staff_client.post(
            "/api/v1/shipments/quote/",
            self._quote_payload(organization="not-a-uuid"),
            format="json",
        )
        self.assertEqual(response.status_code, 400, response.data)

    def test_the_callers_only_membership_is_used_when_the_body_omits_it(self):
        payload = self._quote_payload()
        del payload["organization"]
        response = self.staff_client.post("/api/v1/shipments/quote/", payload, format="json")
        self.assertEqual(response.status_code, 201, response.data)
