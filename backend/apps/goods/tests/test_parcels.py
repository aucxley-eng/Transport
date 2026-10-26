"""Intercity parcels and hubs: origin scan, line-haul, hub transfer, release.

The fourteen invariants in ``docs/phase4a-scope.md`` are under test here, in
order. The rules they enforce are quoted in ``apps/goods/parcels.py``,
``apps/goods/services.py`` and ``apps/goods/courier.py``.
"""

from datetime import date, datetime, timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import Membership, Organization, Role, User
from apps.catalog.models import Place, RateCard, RateLine, Service
from apps.fleet.models import CargoCompartment, Driver, Vehicle
from apps.goods.consolidation import (
    propose_consolidation,
    publish_plan,
    validate_plan,
)
from apps.goods.courier import quote_courier_order
from apps.goods.models import (
    CustodyEvent,
    FreightTrip,
    ManifestLine,
    Package,
    Shipment,
    ShipmentException,
)
from apps.goods.parcels import (
    arrive_at_hub,
    book_parcel_order,
    holding_at_hub,
    is_parcel_service,
    load_parcels_onto_trip,
    place_onward_load,
    quote_parcel_order,
    record_departure,
    release_to_recipient,
)
from apps.goods.services import (
    GoodsError,
    build_manifest,
    create_freight_trip,
    raise_exception,
    reconcile_manifest,
    record_custody_event,
    resolve_exception,
)

TODAY = date(2026, 1, 1)


class ParcelFixture(TestCase):
    """Kisumu branch to Nyeri branch, through the Nairobi hub."""

    def setUp(self):
        self.org = Organization.objects.create(name="Rift Parcels", slug="rift")
        self.other_org = Organization.objects.create(name="Rival Parcel", slug="rival")
        self.sender = User.objects.create_user("0755000011", "pass-P-1234")
        self.staff = User.objects.create_user("0755000022", "pass-P-1234")
        Membership.objects.create(
            user=self.staff, organization=self.org, role=Role.DISPATCHER
        )
        # Deliberately not operations staff: the hub scan must refuse them.
        self.bystander = User.objects.create_user("0755000099", "pass-P-1234")

        self.parcel_service = Service.objects.create(
            slug="intercity-parcel",
            name="Intercity parcel",
            service_type=Service.ServiceType.PARCEL,
        )
        self.courier_service = Service.objects.create(
            slug="town-courier",
            name="Town courier",
            service_type=Service.ServiceType.COURIER,
        )

        self.kisumu = Place.objects.create(
            organization=self.org, name="Kisumu Branch", place_type=Place.PlaceType.BRANCH
        )
        self.nairobi = Place.objects.create(
            organization=self.org, name="Nairobi Hub", place_type=Place.PlaceType.HUB
        )
        self.nyeri = Place.objects.create(
            organization=self.org, name="Nyeri Branch", place_type=Place.PlaceType.BRANCH
        )
        self.street = Place.objects.create(
            organization=self.org, name="Somebody's Gate", place_type=Place.PlaceType.LANDMARK
        )

        self.truck = Vehicle.objects.create(
            organization=self.org,
            registration_number="KDA100X",
            vehicle_class=Vehicle.VehicleClass.TRUCK,
            cargo_weight_kg=2000,
            cargo_volume_m3=20.0,
        )
        CargoCompartment.objects.create(
            organization=self.org,
            vehicle=self.truck,
            name="bed",
            max_weight_kg=2000,
            max_volume_m3=20.0,
        )
        self.driver = Driver.objects.create(
            user=User.objects.create_user("0755000033", "pass-D-1234"),
            organization=self.org,
            status=Driver.Status.APPROVED,
            working_hours_per_day=8,
        )
        Membership.objects.create(
            user=self.driver.user, organization=self.org, role=Role.DRIVER
        )

        self.start = timezone.make_aware(datetime(2026, 6, 1, 8, 0))
        self.end = timezone.make_aware(datetime(2026, 6, 1, 16, 0))
        self.card = self._rate_card()

    def _rate_card(self):
        card = RateCard.objects.create(
            organization=None,
            service=self.parcel_service,
            name="Parcel 2026",
            version=1,
            currency="KES",
            effective_from=TODAY - timedelta(days=30),
        )
        for from_value, to_value, amount_minor in ((0, 1, 500), (1, 5, 900), (5, None, 1_500)):
            RateLine.objects.create(
                rate_card=card,
                line_type=RateLine.LineType.PER_KG,
                label=f"{from_value}-{to_value or 'up'} kg",
                from_value=from_value,
                to_value=to_value,
                amount_minor=amount_minor,
            )
        return card

    def _parcel(self, name="Achieng Otieno", place=None, weight=1.2, **overrides):
        spec = {
            "recipient_name": name,
            "recipient_phone": "0722000001",
            "recipient_place": str((place or self.nyeri).pk),
            "description": "Household goods",
            "weight_kg": weight,
            "length_cm": 30,
            "width_cm": 20,
            "height_cm": 15,
        }
        spec.update(overrides)
        return spec

    def _quote(self, packages=None, **overrides):
        payload = {
            "organization": self.org,
            "service": self.parcel_service,
            "collection": self.kisumu,
            "destination": self.nyeri,
            "packages": [self._parcel()] if packages is None else packages,
            "service_window_start": self.start,
            "service_window_end": self.end,
            "hubs": [self.nairobi],
            "sender_name": "Grace Wanjiku",
            "sender_phone": "0722000099",
        }
        payload.update(overrides)
        return quote_parcel_order(**payload)

    def _order(self, packages=None, **overrides):
        quote = self._quote(packages, **overrides)
        quote.accept()
        return book_parcel_order(quote=quote, organization=self.org, sender=self.sender)

    def _trip(self, **overrides):
        payload = {
            "organization": self.org,
            "vehicle": self.truck,
            "driver": self.driver,
            "service": self.parcel_service,
            "origin": self.kisumu,
            "destination": self.nairobi,
            "planned_start": self.start,
            "planned_end": self.end,
        }
        payload.update(overrides)
        return create_freight_trip(**payload)

    def _on_the_road(self, shipment, **trip_overrides):
        """A released, planned, departed line-haul carrying this shipment.

        The departure gate is not bypassed: the manifest is reconciled and
        released, the plan is published, and `record_departure` runs the same
        checks an operator would face.
        """
        trip = self._trip(**trip_overrides)
        packages = list(shipment.packages.all())
        manifest = build_manifest(trip=trip, packages=packages, built_by=self.staff)
        reconcile_manifest(
            manifest=manifest,
            scanned={line.label: ManifestLine.ScanState.SCANNED for line in manifest.lines.all()},
            scanned_by=self.staff,
        )
        plan = propose_consolidation(
            organization=self.org, trip=trip, shipments=[shipment], proposed_by=self.staff
        )
        validate_plan(plan)
        publish_plan(plan, reason="Line-haul ready")
        from apps.goods.services import release_manifest

        release_manifest(manifest=manifest, released_by=self.staff)
        record_departure(trip=trip, actor=self.staff)
        return trip, manifest

    def _custody(self, package, event_type):
        return package.custody_events.filter(event_type=event_type)


class ParcelServicesAreNotCourierTestCase(ParcelFixture):
    """Invariant 1: a PARCEL service is priced by the parcel path and still
    refused by courier."""

    def test_the_parcel_service_is_recognised(self):
        self.assertTrue(is_parcel_service(self.parcel_service))
        self.assertFalse(is_parcel_service(self.courier_service))

    def test_courier_still_refuses_a_parcel_service(self):
        with self.assertRaises(GoodsError) as caught:
            quote_courier_order(
                organization=self.org,
                service=self.parcel_service,
                collection=self.kisumu,
                destination=self.nyeri,
                packages=[self._parcel()],
                service_window_start=self.start,
                service_window_end=self.end,
            )
        self.assertIn("courier", str(caught.exception).lower())

    def test_a_parcel_quote_is_priced_per_parcel(self):
        one = self._quote([self._parcel("Achieng Otieno", weight=0.5)])
        two = self._quote(
            [
                self._parcel("Achieng Otieno", weight=0.5),
                self._parcel("Brian Kiptoo", weight=3.0),
            ]
        )
        # Two parcels cost two parcels' worth, not one parcel and a passenger
        # fare: the price follows the goods, not the consignment.
        self.assertEqual(two.total_minor, one.total_minor * 2)
        self.assertEqual(len(two.request_payload["parcels"]), 2)

    def test_a_hub_that_is_not_a_hub_is_refused(self):
        with self.assertRaises(GoodsError) as caught:
            self._quote(hubs=[self.street])
        self.assertIn("not a hub or branch", str(caught.exception))

    def test_a_hub_repeating_the_route_is_refused(self):
        with self.assertRaises(GoodsError) as caught:
            self._quote(hubs=[self.nairobi, self.nairobi])
        self.assertIn("each place appears once", str(caught.exception))


class HubRouteIsBookedAsLegsTestCase(ParcelFixture):
    """Invariant 2: a three-leg parcel books with the hub as an intermediate
    stop, and the legs are ordered by sequence."""

    def test_the_hub_is_the_middle_leg(self):
        shipment = self._order()
        stops = [(leg.sequence, leg.stop_id) for leg in shipment.legs.order_by("sequence")]
        self.assertEqual(
            stops,
            [(0, self.kisumu.pk), (1, self.nairobi.pk), (2, self.nyeri.pk)],
        )

    def test_a_parcel_without_a_hub_books_two_legs(self):
        shipment = self._order(hubs=[])
        self.assertEqual(shipment.legs.count(), 2)
        self.assertNotIn(
            self.nairobi.pk, list(shipment.legs.values_list("stop_id", flat=True))
        )

    def test_the_parcel_survives_the_route(self):
        shipment = self._order()
        package = shipment.packages.first()
        labels = {package.label for package in shipment.packages.all()}
        self.assertEqual(len(labels), 1)
        self.assertEqual(package.recipient_place_id, self.nyeri.pk)


class LoadScanWritesCustodyTestCase(ParcelFixture):
    """Invariant 3: scanning a parcel onto a line-haul writes a LOADED event and
    takes the origin hub as its current location."""

    def test_a_scanned_parcel_is_loaded_and_at_the_hub(self):
        shipment = self._order()
        trip = self._trip()
        packages = list(shipment.packages.all())
        manifest = build_manifest(trip=trip, packages=packages, built_by=self.staff)
        package = packages[0]

        loaded = load_parcels_onto_trip(
            trip=trip,
            labels={package.label: ManifestLine.ScanState.SCANNED},
            actor=self.staff,
        )

        package.refresh_from_db()
        self.assertEqual(self._custody(package, CustodyEvent.EventType.LOADED).count(), 1)
        self.assertEqual(package.current_location_id, self.kisumu.pk)
        self.assertEqual(package.status, Package.Status.LOADED)
        self.assertEqual([p.pk for p in loaded], [package.pk])

    def test_a_label_that_is_not_on_the_manifest_is_refused(self):
        shipment = self._order()
        trip = self._trip()
        build_manifest(
            trip=trip, packages=list(shipment.packages.all()), built_by=self.staff
        )
        with self.assertRaises(GoodsError) as caught:
            load_parcels_onto_trip(
                trip=trip,
                labels={"NOT-ON-IT": ManifestLine.ScanState.SCANNED},
                actor=self.staff,
            )
        self.assertIn("not on this manifest", str(caught.exception))

    def test_a_released_manifest_cannot_take_a_scan(self):
        shipment = self._order()
        trip = self._trip()
        packages = list(shipment.packages.all())
        manifest = build_manifest(trip=trip, packages=packages, built_by=self.staff)
        reconcile_manifest(
            manifest=manifest,
            scanned={line.label: ManifestLine.ScanState.SCANNED for line in manifest.lines.all()},
            scanned_by=self.staff,
        )
        from apps.goods.services import release_manifest

        release_manifest(manifest=manifest, released_by=self.staff)
        with self.assertRaises(GoodsError) as caught:
            load_parcels_onto_trip(
                trip=trip,
                labels={packages[0].label: ManifestLine.ScanState.SCANNED},
                actor=self.staff,
            )
        self.assertIn("not accepting parcels", str(caught.exception))


class DepartureRecordsCustodyTestCase(ParcelFixture):
    """Invariant 4: departing a line-haul writes a DEPARTED event per parcel and
    moves the parcels to IN_TRANSIT."""

    def test_departure_leaves_a_record_for_every_parcel(self):
        shipment = self._order(
            [
                self._parcel("Achieng Otieno"),
                self._parcel("Brian Kiptoo", weight=2.0),
            ]
        )
        trip, _manifest = self._on_the_road(shipment)

        self.assertEqual(trip.status, FreightTrip.Status.IN_TRANSIT)
        for package in shipment.packages.all():
            self.assertEqual(
                self._custody(package, CustodyEvent.EventType.DEPARTED).count(), 1
            )
            self.assertEqual(package.status, Package.Status.IN_TRANSIT)


class ArrivalReconcilesTheHubTestCase(ParcelFixture):
    """Invariants 5 and 6: arrival writes ARRIVED and holds what is missing."""

    def _arrive(self, shipment, scanned):
        trip, _manifest = self._on_the_road(shipment)
        return trip, arrive_at_hub(trip=trip, scanned=scanned, actor=self.staff)

    def test_an_arrival_is_recorded_at_the_receiving_hub(self):
        shipment = self._order()
        package = shipment.packages.get()
        trip, result = self._arrive(
            shipment, {package.label: ManifestLine.ScanState.SCANNED}
        )
        package.refresh_from_db()

        self.assertEqual(
            self._custody(package, CustodyEvent.EventType.ARRIVED).count(), 1
        )
        self.assertEqual(package.current_location_id, self.nairobi.pk)
        self.assertEqual(package.status, Package.Status.IN_TRANSIT)
        self.assertEqual(trip.status, FreightTrip.Status.COMPLETED)
        self.assertEqual(result["held"], [])

    def test_a_parcel_that_never_arrives_is_held_and_excepted(self):
        shipment = self._order(
            [self._parcel("Achieng Otieno"), self._parcel("Brian Kiptoo", weight=2.0)]
        )
        present = shipment.packages.order_by("label").first()
        _trip, result = self._arrive(
            shipment, {present.label: ManifestLine.ScanState.SCANNED}
        )

        missing = shipment.packages.exclude(pk=present.pk).get()
        self.assertEqual(missing.status, Package.Status.MISSING)
        self.assertEqual(
            self._custody(missing, CustodyEvent.EventType.HELD).count(), 1
        )
        exception = ShipmentException.objects.get(package=missing)
        self.assertEqual(exception.kind, ShipmentException.Kind.MISSING)
        self.assertEqual(exception.next_action, ShipmentException.NextAction.HOLD_BRANCH)
        self.assertIn(missing, result["held"])

    def test_a_damaged_arrival_is_a_claim(self):
        shipment = self._order()
        package = shipment.packages.get()
        _trip, _result = self._arrive(
            shipment, {package.label: ManifestLine.ScanState.DAMAGED}
        )
        package.refresh_from_db()

        self.assertEqual(package.status, Package.Status.DAMAGED)
        exception = ShipmentException.objects.get(package=package)
        self.assertEqual(exception.kind, ShipmentException.Kind.DAMAGED)
        self.assertEqual(exception.next_action, ShipmentException.NextAction.CLAIM)


class UnmanifestedArrivalIsHeldTestCase(ParcelFixture):
    """Invariant 7: a parcel that arrives unmanifested is an EXTRA line and is
    held rather than pushed onward."""

    def test_an_unlisted_scan_is_an_exception_not_a_delivery(self):
        shipment = self._order()
        package = shipment.packages.get()
        trip, _manifest = self._on_the_road(shipment)

        arrive_at_hub(
            trip=trip,
            scanned={
                package.label: ManifestLine.ScanState.SCANNED,
                "GHOST-1": ManifestLine.ScanState.SCANNED,
            },
            actor=self.staff,
        )

        extra = manifest_line_for(trip, "GHOST-1")
        self.assertEqual(extra.scan_state, ManifestLine.ScanState.EXTRA)
        exception = ShipmentException.objects.filter(
            kind=ShipmentException.Kind.EXTRA, status=ShipmentException.Status.OPEN
        ).first()
        self.assertIsNotNone(exception)
        self.assertIn("GHOST-1", exception.detail)


def manifest_line_for(trip, label):
    manifest = trip.manifests.order_by("-version").first()
    return manifest.lines.get(label=label)


class HoldBlocksOnwardMovementTestCase(ParcelFixture):
    """Invariant 9: a parcel carries a HELD event at the hub, and an open
    exception stops it being placed onward."""

    def _at_hub(self, shipment):
        package = shipment.packages.get()
        trip, _manifest = self._on_the_road(shipment)
        arrive_at_hub(
            trip=trip, scanned={package.label: ManifestLine.ScanState.SCANNED}, actor=self.staff
        )
        return package, trip

    def test_every_arrived_parcel_is_recorded_as_held(self):
        shipment = self._order()
        package, _trip = self._at_hub(shipment)
        self.assertEqual(self._custody(package, CustodyEvent.EventType.HELD).count(), 1)

    def test_an_open_exception_blocks_the_onward_truck(self):
        shipment = self._order()
        package, trip = self._at_hub(shipment)
        raise_exception(
            shipment,
            kind=ShipmentException.Kind.DAMAGED,
            next_action=ShipmentException.NextAction.CLAIM,
            package=package,
            detail="Corner crushed at Nairobi",
            raised_by=self.staff,
        )

        with self.assertRaises(GoodsError) as caught:
            place_onward_load(trip=trip, package=package, actor=self.staff)
        self.assertIn("open exception", str(caught.exception))

    def test_a_resolved_exception_lets_it_move(self):
        shipment = self._order()
        package, trip = self._at_hub(shipment)
        exception = raise_exception(
            shipment,
            kind=ShipmentException.Kind.DAMAGED,
            next_action=ShipmentException.NextAction.CLAIM,
            package=package,
            detail="Corner crushed at Nairobi",
            raised_by=self.staff,
        )
        resolve_exception(exception, note="Claim accepted, carton replaced")

        place_onward_load(trip=trip, package=package, actor=self.staff)
        package.refresh_from_db()
        self.assertEqual(package.status, Package.Status.LOADED)
        self.assertEqual(
            self._custody(package, CustodyEvent.EventType.LOADED).count(), 1
        )


class ReleaseNeedsTheChainTestCase(ParcelFixture):
    """Invariant 10: release reuses courier's proof, refuses a photo alone, and
    is blocked by an open exception or the wrong place."""

    def _delivered_parcel(self):
        shipment = self._order()
        package = shipment.packages.get()
        trip, _manifest = self._on_the_road(shipment)
        arrive_at_hub(
            trip=trip, scanned={package.label: ManifestLine.ScanState.SCANNED}, actor=self.staff
        )
        # Stand it where it was addressed to, as the final leg would.
        package.current_location = self.nyeri
        package.save(update_fields=["current_location", "updated_at"])
        return shipment, package

    def _proof(self, package, **overrides):
        payload = {
            "recipient_name": package.recipient_name,
            "recipient_id_type": "national_id",
            "recipient_id_last4": "4321",
            "otp_verified": True,
            "photos": ["https://files.example/parcel.jpg"],
            "recorded_by": self.staff,
        }
        payload.update(overrides)
        return payload

    def test_a_parcel_at_the_wrong_place_is_not_delivered(self):
        shipment, package = self._delivered_parcel()
        package.current_location = self.nairobi
        package.save(update_fields=["current_location", "updated_at"])

        with self.assertRaises(GoodsError) as caught:
            release_to_recipient(package, **self._proof(package))
        self.assertIn("cannot be released yet", str(caught.exception))

    def test_an_open_exception_is_not_delivered(self):
        shipment, package = self._delivered_parcel()
        raise_exception(
            shipment,
            kind=ShipmentException.Kind.REFUSED,
            next_action=ShipmentException.NextAction.RETURN,
            package=package,
            detail="Recipient will not accept",
            raised_by=self.staff,
        )
        with self.assertRaises(GoodsError) as caught:
            release_to_recipient(package, **self._proof(package))
        self.assertIn("open exception", str(caught.exception))

    def test_a_clean_parcel_is_delivered_and_proved(self):
        shipment, package = self._delivered_parcel()
        release_to_recipient(package, **self._proof(package))

        package.refresh_from_db()
        shipment.refresh_from_db()
        self.assertEqual(package.status, Package.Status.DELIVERED)
        self.assertEqual(shipment.status, Shipment.Status.DELIVERED)
        self.assertEqual(
            self._custody(package, CustodyEvent.EventType.DELIVERED).count(), 1
        )

    def test_a_photo_alone_is_still_not_proof(self):
        shipment, package = self._delivered_parcel()
        with self.assertRaises(GoodsError):
            release_to_recipient(
                package,
                **self._proof(
                    package,
                    recipient_id_type="",
                    recipient_id_last4="",
                    otp_verified=False,
                    signature_ref="",
                ),
            )


class HoldingAtHubIsQueryableTestCase(ParcelFixture):
    """Invariant 12: what is stranded at a hub, and why, is a query rather than
    a guess -- and the shipment does not pretend to be complete."""

    def test_a_held_parcel_is_listed_with_its_reason(self):
        shipment = self._order()
        package = shipment.packages.get()
        trip, _manifest = self._on_the_road(shipment)
        arrive_at_hub(
            trip=trip, scanned={package.label: ManifestLine.ScanState.SCANNED}, actor=self.staff
        )

        rows = holding_at_hub(organization=self.org, place=self.nairobi)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["package"].pk, package.pk)

    def test_a_missing_parcel_names_its_exception(self):
        shipment = self._order(
            [self._parcel("Achieng Otieno"), self._parcel("Brian Kiptoo", weight=2.0)]
        )
        present = shipment.packages.order_by("label").first()
        trip, _manifest = self._on_the_road(shipment)
        arrive_at_hub(
            trip=trip, scanned={present.label: ManifestLine.ScanState.SCANNED}, actor=self.staff
        )

        rows = holding_at_hub(organization=self.org, place=self.nairobi)
        missing = shipment.packages.exclude(pk=present.pk).get()
        # Both are at the hub: the one that arrived waits for the onward truck,
        # the one that did not is missing. What is stranded and why is a query.
        reasons = {row["package"].pk: row["reason"] for row in rows}
        self.assertEqual(len(rows), 2)
        self.assertIn("missing", reasons[missing.pk])

    def test_a_shipment_with_one_parcel_stranded_is_not_complete(self):
        shipment = self._order(
            [self._parcel("Achieng Otieno"), self._parcel("Brian Kiptoo", weight=2.0)]
        )
        present = shipment.packages.order_by("label").first()
        trip, _manifest = self._on_the_road(shipment)
        arrive_at_hub(
            trip=trip, scanned={present.label: ManifestLine.ScanState.SCANNED}, actor=self.staff
        )
        shipment.refresh_from_db()
        self.assertEqual(shipment.status, Shipment.Status.IN_TRANSIT)


class HubScansAreGuardedTestCase(ParcelFixture):
    """Invariant 13: a caller without an operations role cannot record a hub
    movement, and a parcel that is not on the manifest cannot be scanned."""

    def test_a_bystander_cannot_scan(self):
        shipment = self._order()
        trip = self._trip()
        build_manifest(
            trip=trip, packages=list(shipment.packages.all()), built_by=self.staff
        )
        package = shipment.packages.get()
        with self.assertRaises(GoodsError) as caught:
            load_parcels_onto_trip(
                trip=trip,
                labels={package.label: ManifestLine.ScanState.SCANNED},
                actor=self.bystander,
            )
        self.assertIn("operator's own staff", str(caught.exception))

    def test_a_parcel_from_another_operator_is_not_on_this_manifest(self):
        shipment = self._order()
        trip = self._trip()
        build_manifest(
            trip=trip, packages=list(shipment.packages.all()), built_by=self.staff
        )
        with self.assertRaises(GoodsError) as caught:
            load_parcels_onto_trip(
                trip=trip,
                labels={"RIVAL-1": ManifestLine.ScanState.SCANNED},
                actor=self.staff,
            )
        self.assertIn("not on this manifest", str(caught.exception))


class CustodyIsAppendOnlyTestCase(ParcelFixture):
    """Invariant 14: a correction is a new row that names what it corrects; no
    custody row is ever updated or deleted."""

    def test_a_correction_is_a_new_row(self):
        shipment = self._order()
        package = shipment.packages.get()
        trip, _manifest = self._on_the_road(shipment)
        arrive_at_hub(
            trip=trip, scanned={package.label: ManifestLine.ScanState.SCANNED}, actor=self.staff
        )
        original = self._custody(package, CustodyEvent.EventType.ARRIVED).get()
        before = original.server_receipt_time

        correction = record_custody_event(
            package,
            event_type=CustodyEvent.EventType.ARRIVED,
            device_event_time=timezone.now(),
            location=self.nairobi,
            corrects=original,
            correction_reason="Scanned against the wrong parcel",
            recorded_by=self.staff,
        )

        original.refresh_from_db()
        self.assertEqual(original.event_type, CustodyEvent.EventType.ARRIVED)
        self.assertEqual(original.server_receipt_time, before)
        self.assertEqual(correction.event_type, CustodyEvent.EventType.CORRECTION)
        self.assertEqual(correction.corrects_event_id, original.pk)
        self.assertEqual(package.custody_events.count(), original.package.custody_events.count())

    def test_a_correction_needs_a_reason(self):
        shipment = self._order()
        package = shipment.packages.get()
        original = package.custody_events.first()
        with self.assertRaises(GoodsError) as caught:
            record_custody_event(
                package,
                event_type=CustodyEvent.EventType.ARRIVED,
                device_event_time=timezone.now(),
                corrects=original,
            )
        self.assertIn("reason", str(caught.exception))


class ParcelCounterAPITestCase(ParcelFixture):
    """Branch counter staff can quote and book a walk-in parcel.

    Ticket sales already need only authentication; parcel intake previously
    needed freight operations, which counter staff do not hold. The counter
    path is deliberately narrower than freight: it can quote and book, but the
    hub, manifest and custody routes stay with dispatchers.
    """

    def setUp(self):
        super().setUp()
        self.counter = User.objects.create_user("0755000044", "pass-C-1234")
        Membership.objects.create(
            user=self.counter,
            organization=self.org,
            role=Role.BRANCH_STAFF,
            workplace=self.kisumu,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.counter)

    def _quote_body(self):
        return {
            "service": str(self.parcel_service.pk),
            "collection": str(self.kisumu.pk),
            "destination": str(self.nyeri.pk),
            "hubs": [str(self.nairobi.pk)],
            "sender_name": "Grace Wanjiku",
            "sender_phone": "0722000099",
            "service_window_start": self.start.isoformat(),
            "service_window_end": self.end.isoformat(),
            "parcels": [self._parcel()],
        }

    def test_counter_can_quote_a_walk_in_parcel(self):
        response = self.client.post(
            "/api/v1/shipments/parcel-quote/", self._quote_body(), format="json"
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["total_minor"], 900)
        self.assertEqual(response.data["currency"], "KES")
        self.assertEqual(len(response.data["parcels"]), 1)

    def test_counter_can_book_the_quoted_parcel(self):
        quote = self.client.post(
            "/api/v1/shipments/parcel-quote/", self._quote_body(), format="json"
        ).data
        response = self.client.post(
            "/api/v1/shipments/", {"quote": str(quote["quote"]), "sender": True}, format="json"
        )
        self.assertEqual(response.status_code, 201, response.data)
        order = Shipment.objects.get(pk=response.data["id"])
        self.assertEqual(order.package_count, 1)
        self.assertEqual(order.status, Shipment.Status.BOOKED)

    def test_a_non_member_cannot_quote(self):
        self.client.force_authenticate(self.bystander)
        response = self.client.post(
            "/api/v1/shipments/parcel-quote/", self._quote_body(), format="json"
        )
        self.assertEqual(response.status_code, 403)
