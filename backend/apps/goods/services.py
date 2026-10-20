"""Transactional freight services (blueprint 9, 10, 11).

Every mutation is wrapped in ``transaction.atomic`` and re-checks its invariants
inside the transaction rather than trusting the caller. Custody is append-only:
there is no update or delete path for :class:`CustodyEvent`, and a correction is
a new event that names the event it corrects.
"""

from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.catalog.models import Quote, Service, build_quote
from apps.fleet.models import Driver, Vehicle
from apps.goods.base import refresh_order_status
from apps.goods.models import (
    CustodyEvent,
    FreightTrip,
    LoadType,
    Manifest,
    ManifestLine,
    MeasurementBasis,
    Package,
    Shipment,
    ShipmentEvidence,
    ShipmentException,
    ShipmentLeg,
)

# A vehicle is unavailable for this long after a trip ends, before it can load
# again.
FREIGHT_TURNAROUND_MINUTES = 30

ZERO = Decimal("0")

# Trip states that still hold the vehicle and driver.
_ACTIVE_TRIP_STATES = [
    FreightTrip.Status.PLANNED,
    FreightTrip.Status.LOADING,
    FreightTrip.Status.RELEASED,
    FreightTrip.Status.IN_TRANSIT,
]

# Manifest line states that count against committed cargo capacity.
_COMMITTED_SCAN_STATES = [
    ManifestLine.ScanState.PENDING,
    ManifestLine.ScanState.SCANNED,
    ManifestLine.ScanState.DAMAGED,
]


class GoodsError(Exception):
    """Business-rule violation; the API layer renders it as 400."""


def _now():
    return timezone.now()


def _aware(value, field):
    """Accept ISO strings or datetimes and require an aware result."""
    if value is None or value == "":
        raise GoodsError(f"{field} is required.")
    if isinstance(value, str):
        value = parse_datetime(value)
        if value is None:
            raise GoodsError(f"{field} is not a valid date-time.")
    if timezone.is_naive(value):
        raise GoodsError(f"{field} must include a timezone.")
    return value


def _decimal(value, field):
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (ArithmeticError, TypeError, ValueError) as exc:
        raise GoodsError(f"{field} must be a number.") from exc


def _ref(prefix):
    return f"{prefix}-{uuid4().hex[:10].upper()}"


# ---------------------------------------------------------------------------
# Quoting
# ---------------------------------------------------------------------------


def quote_shipment(
    *,
    organization,
    service,
    sender_name,
    sender_phone,
    recipient_name,
    recipient_phone,
    collection,
    destination,
    total_weight_kg,
    total_volume_m3,
    service_window_start,
    service_window_end,
    total_minor,
    measurement_basis=MeasurementBasis.ACTUAL_WEIGHT,
    declared_value_minor=0,
    handling_needs=None,
    rate_card=None,
    expires_in_minutes=60,
):
    """Price a consignment, snapshotting the measurement basis.

    "Quotes use the applicable tariff; a business may charge by actual weight,
    volumetric weight, distance, zone or vehicle capacity. Save the accepted
    quote and measurement basis." The measurements the total was computed from
    are stored on the quote's payload, because the tariff may change before the
    consignment is booked.
    """
    if service is None or service.service_type != Service.ServiceType.FREIGHT:
        raise GoodsError("A freight service is required.")
    if collection is None or destination is None:
        raise GoodsError("Collection and destination are required.")
    if collection.pk == destination.pk:
        raise GoodsError("Collection and destination must differ.")
    if measurement_basis not in MeasurementBasis.values:
        raise GoodsError("Unknown measurement basis.")
    if total_minor is None or int(total_minor) <= 0:
        raise GoodsError("A positive quote total is required.")

    window_start = _aware(service_window_start, "service_window_start")
    window_end = _aware(service_window_end, "service_window_end")
    if window_end <= window_start:
        raise GoodsError("The service window must end after it starts.")

    weight = _decimal(total_weight_kg, "total_weight_kg") or ZERO
    volume = _decimal(total_volume_m3, "total_volume_m3") or ZERO
    if weight <= 0 and volume <= 0:
        raise GoodsError("A consignment needs a weight or a volume.")

    measurements = {
        "total_weight_kg": str(weight),
        "total_volume_m3": str(volume),
        "declared_value_minor": int(declared_value_minor or 0),
        "handling_needs": list(handling_needs or []),
        "collection_id": str(collection.pk),
        "destination_id": str(destination.pk),
        "service_window_start": window_start.isoformat(),
        "service_window_end": window_end.isoformat(),
    }
    return build_quote(
        service=service,
        request_payload=measurements,
        total_minor=int(total_minor),
        rate_card=rate_card,
        basis=measurement_basis,
        organization=organization,
        expires_in_minutes=expires_in_minutes,
    )


def _terms_from_quote(quote):
    """Freeze the accepted offer onto the shipment.

    "Preserve the accepted carrier offer and terms before dispatch" -- a later
    tariff edit must not rewrite what was agreed.
    """
    return {
        "quote_id": str(quote.pk),
        "rate_card_name": quote.rate_card_name,
        "currency": quote.currency,
        "total_minor": quote.total_minor,
        "basis": quote.basis,
        "accepted_at": quote.accepted_at.isoformat() if quote.accepted_at else None,
        "measurements": quote.request_payload or {},
    }


# ---------------------------------------------------------------------------
# Booking
# ---------------------------------------------------------------------------


@transaction.atomic
def create_shipment(
    *,
    quote,
    organization,
    service,
    sender_name,
    sender_phone,
    recipient_name,
    recipient_phone,
    collection,
    destination,
    service_window_start,
    service_window_end,
    sender=None,
    terms=None,
    packages=None,
    load_type=None,
    order=None,
):
    """Book a consignment from an accepted quote.

    The quote must be accepted and unexpired, and the accepted quote plus the
    basis it was computed on are stored on the shipment rather than recomputed
    from the current tariff.
    """
    if quote.status == Quote.Status.EXPIRED:
        raise GoodsError("The quote has expired.")
    if quote.expires_at is not None and _now() > quote.expires_at:
        if quote.status == Quote.Status.VALID:
            quote.status = Quote.Status.EXPIRED
            quote.save(update_fields=["status", "updated_at"])
        raise GoodsError("The quote has expired.")
    if quote.status != Quote.Status.ACCEPTED:
        raise GoodsError("Only an accepted quote can be booked.")
    if quote.organization_id not in (None, organization.pk):
        raise GoodsError("The quote belongs to another organization.")
    if quote.service_id != service.pk:
        raise GoodsError("The quote was priced for a different service.")

    window_start = _aware(service_window_start, "service_window_start")
    window_end = _aware(service_window_end, "service_window_end")
    if window_end <= window_start:
        raise GoodsError("The service window must end after it starts.")
    if load_type is not None and load_type not in LoadType.values:
        raise GoodsError("load_type must be a shared load or a full truckload.")
    # Freight is one consignment to one consignee, so the order names them. A
    # courier order has several, and the parcels name them instead; those are
    # booked by courier.book_courier_order, not here.
    if not str(recipient_name or "").strip():
        raise GoodsError("A consignment needs the name of the person it is for.")
    if not str(recipient_phone or "").strip():
        raise GoodsError("A consignment needs a contact for the person it is for.")

    measurements = dict(quote.request_payload or {})
    created = []
    for spec in packages or []:
        weight = _decimal((spec or {}).get("weight_kg"), "weight_kg")
        if weight is None or weight <= 0:
            raise GoodsError("Each package needs a positive weight.")

    shipment = Shipment.objects.create(
        organization=organization,
        reference=_ref("SHP"),
        service=service,
        order=order,
        sender=sender,
        sender_name=sender_name,
        sender_phone=sender_phone,
        recipient_name=recipient_name,
        recipient_phone=recipient_phone,
        collection=collection,
        destination=destination,
        total_weight_kg=_decimal(
            measurements.get("total_weight_kg"), "total_weight_kg"
        )
        or ZERO,
        total_volume_m3=_decimal(
            measurements.get("total_volume_m3"), "total_volume_m3"
        )
        or ZERO,
        declared_value_minor=int(measurements.get("declared_value_minor") or 0),
        handling_needs=list(measurements.get("handling_needs") or []),
        service_window_start=window_start,
        service_window_end=window_end,
        quote=quote,
        measurement_basis=quote.basis,
        measurement_snapshot=measurements,
        terms_snapshot=terms or _terms_from_quote(quote),
        agreed_total_minor=quote.total_minor,
        load_type=load_type or LoadType.SHARED_LOAD,
        status=Shipment.Status.BOOKED,
    )

    for spec in packages or []:
        created.append(_create_package(shipment, spec))

    shipment.package_count = len(created)
    shipment.total_weight_kg = sum((p.weight_kg for p in created), ZERO)
    shipment.total_volume_m3 = sum((p.volume_m3 for p in created), ZERO)
    shipment.volumetric_weight_kg = sum((p.volumetric_weight_kg for p in created), ZERO)
    shipment.save(
        update_fields=[
            "package_count",
            "total_weight_kg",
            "total_volume_m3",
            "volumetric_weight_kg",
            "updated_at",
        ]
    )

    # Two legs: collection then delivery. A return reuses this shape with the
    # stops reversed.
    ShipmentLeg.objects.create(
        organization=organization,
        shipment=shipment,
        stop=collection,
        sequence=0,
        status=ShipmentLeg.Status.PLANNED,
        planned_at=window_start,
    )
    ShipmentLeg.objects.create(
        organization=organization,
        shipment=shipment,
        stop=destination,
        sequence=1,
        status=ShipmentLeg.Status.PLANNED,
        planned_at=window_end,
    )

    for package in created:
        record_custody_event(
            package,
            event_type=CustodyEvent.EventType.BOOKED,
            giving_party=sender_name,
            receiving_party=organization.name,
            location=collection,
            device_event_time=_now(),
        )
    return shipment


def _create_package(shipment, spec):
    length = int(spec.get("length_cm") or 0)
    width = int(spec.get("width_cm") or 0)
    height = int(spec.get("height_cm") or 0)
    if min(length, width, height) < 0:
        raise GoodsError("Package dimensions cannot be negative.")
    package = Package.objects.create(
        organization=shipment.organization,
        shipment=shipment,
        label=_ref("PKG"),
        description=spec.get("description", ""),
        weight_kg=_decimal(spec.get("weight_kg"), "weight_kg") or ZERO,
        length_cm=length,
        width_cm=width,
        height_cm=height,
        declared_value_minor=int(spec.get("declared_value_minor") or 0),
        handling_flags=list(spec.get("handling_flags") or []),
        is_fragile=bool(spec.get("is_fragile", False)),
    )
    package.measure()
    return package


# ---------------------------------------------------------------------------
# Trips and the cargo capacity ledger
# ---------------------------------------------------------------------------


@transaction.atomic
def create_freight_trip(
    *,
    organization,
    vehicle,
    planned_start,
    planned_end,
    driver=None,
    service=None,
    origin=None,
    destination=None,
):
    """Plan a vehicle movement for freight.

    The vehicle and driver are held for the planned interval plus a turnaround,
    so the same unit cannot be promised to two overlapping trips. Their rows
    are locked before the overlap check, so a concurrent dispatcher waits and
    then sees this trip instead of double-booking the unit. Nothing here reads
    seat inventory: cargo capacity is an independent ledger.
    """
    start = _aware(planned_start, "planned_start")
    end = _aware(planned_end, "planned_end")
    if end <= start:
        raise GoodsError("The trip must end after it starts.")
    if vehicle.organization_id != organization.pk:
        raise GoodsError("The vehicle belongs to another organization.")
    if driver is not None and driver.organization_id != organization.pk:
        raise GoodsError("The driver belongs to another organization.")

    # Serialise every reservation of this unit on the unit's own row. Without
    # this, two dispatchers can both pass the overlap check before either has
    # written, and both trips are created -- the check-then-insert race the
    # transaction alone does not close. The lock is held until commit, so the
    # second dispatcher sees the first trip once it is let through.
    #
    # Always vehicle first, then driver: a fixed order is what stops two
    # dispatchers reserving two units from deadlocking each other.
    vehicle = Vehicle.objects.select_for_update().get(pk=vehicle.pk)
    if driver is not None:
        driver = Driver.objects.select_for_update().get(pk=driver.pk)

    if vehicle.status != vehicle.Status.ACTIVE:
        raise GoodsError("The vehicle is not active.")
    if not vehicle.cargo_weight_kg or not vehicle.cargo_volume_m3:
        raise GoodsError("The vehicle has no cargo capacity configured.")
    if driver is not None and not driver.is_dispatchable():
        raise GoodsError("The driver is not dispatchable.")

    blocked_to = end + timedelta(minutes=FREIGHT_TURNAROUND_MINUTES)
    overlap = Q(planned_start__lt=blocked_to, planned_end__gt=start)
    active = FreightTrip.objects.filter(
        organization=organization, status__in=_ACTIVE_TRIP_STATES
    ).filter(overlap)

    if active.filter(vehicle=vehicle).exists():
        raise GoodsError("The vehicle is already committed to an overlapping trip.")
    if driver is not None and active.filter(driver=driver).exists():
        raise GoodsError("The driver is already committed to an overlapping trip.")

    return FreightTrip.objects.create(
        organization=organization,
        vehicle=vehicle,
        driver=driver,
        service=service,
        origin=origin,
        destination=destination,
        planned_start=start,
        planned_end=end,
        status=FreightTrip.Status.PLANNED,
    )


def cargo_capacity(trip):
    """Remaining cargo allowance for a trip, as its own ledger.

    Whole-vehicle weight and volume allowance, what the trip's manifest has
    already committed, what is left, and a per-compartment breakdown so
    compartment restrictions are visible and enforceable.
    """
    vehicle = trip.vehicle
    manifest = Manifest.objects.filter(trip=trip).order_by("-version").first()

    committed_weight = ZERO
    committed_volume = ZERO
    compartment_weight_used = {c.pk: ZERO for c in vehicle.cargo_compartments.all()}
    compartment_volume_used = {c.pk: ZERO for c in vehicle.cargo_compartments.all()}
    if manifest is not None:
        committed = manifest.lines.filter(scan_state__in=_COMMITTED_SCAN_STATES)
        committed_weight = committed.aggregate(w=Sum("expected_weight_kg"))["w"] or ZERO
        committed_volume = committed.aggregate(v=Sum("package__volume_m3"))["v"] or ZERO
        for line in committed.select_related("package"):
            if line.compartment_id is None:
                continue
            compartment_weight_used[line.compartment_id] += line.expected_weight_kg
            if line.package_id is not None:
                compartment_volume_used[line.compartment_id] += line.package.volume_m3

    capacity_weight = Decimal(vehicle.cargo_weight_kg)
    capacity_volume = Decimal(str(vehicle.cargo_volume_m3))
    compartments = []
    for compartment in vehicle.cargo_compartments.all():
        used_weight = compartment_weight_used.get(compartment.pk, ZERO)
        used_volume = compartment_volume_used.get(compartment.pk, ZERO)
        compartment_capacity_volume = Decimal(str(compartment.max_volume_m3))
        compartments.append(
            {
                "compartment": compartment,
                "used_weight_kg": used_weight,
                "used_volume_m3": used_volume,
                "free_weight_kg": max(Decimal(compartment.max_weight_kg) - used_weight, ZERO),
                "free_volume_m3": max(compartment_capacity_volume - used_volume, ZERO),
                "accepts_fragile": compartment.accepts_fragile,
                "accepts_hazardous": compartment.accepts_hazardous,
            }
        )

    return {
        "trip": trip,
        "manifest": manifest,
        "capacity_weight_kg": capacity_weight,
        "capacity_volume_m3": capacity_volume,
        "committed_weight_kg": committed_weight,
        "committed_volume_m3": committed_volume,
        "free_weight_kg": max(capacity_weight - committed_weight, ZERO),
        "free_volume_m3": max(capacity_volume - committed_volume, ZERO),
        "compartments": compartments,
    }


def _place_in_compartments(capacity, packages):
    """First-fit each package into a compartment.

    Compartment restrictions are enforced here: a fragile package will not go
    into a compartment that does not accept fragile goods, and no compartment is
    loaded past its own weight *or volume* limit. Returns
    {package_id: compartment}.
    """
    remaining_weight = {
        entry["compartment"].pk: entry["free_weight_kg"]
        for entry in capacity["compartments"]
    }
    remaining_volume = {
        entry["compartment"].pk: entry["free_volume_m3"]
        for entry in capacity["compartments"]
    }
    acceptors = {
        entry["compartment"].pk: (
            entry["accepts_fragile"],
            entry["accepts_hazardous"],
        )
        for entry in capacity["compartments"]
    }

    placement = {}
    for package in sorted(packages, key=lambda p: p.weight_kg, reverse=True):
        candidates = []
        for compartment_id, free_weight in remaining_weight.items():
            if free_weight < package.weight_kg:
                continue
            if remaining_volume[compartment_id] < package.volume_m3:
                continue
            accepts_fragile, accepts_hazardous = acceptors[compartment_id]
            if package.is_fragile and not accepts_fragile:
                continue
            if "hazardous" in (package.handling_flags or []) and not accepts_hazardous:
                continue
            candidates.append(compartment_id)
        if not candidates:
            raise GoodsError(
                f"{package.label} does not fit any cargo compartment on this vehicle."
            )
        chosen = min(candidates, key=lambda cid: remaining_weight[cid])
        remaining_weight[chosen] -= package.weight_kg
        remaining_volume[chosen] -= package.volume_m3
        placement[package.pk] = chosen
    return placement


@transaction.atomic
def build_manifest(*, trip, packages, built_by=None):
    """List the packages assigned to a trip.

    The manifest is the authoritative statement of what the vehicle should
    carry, so the cargo ledger refuses an assignment that does not fit in the
    vehicle's weight allowance or in any single compartment.
    """
    if trip.status not in (FreightTrip.Status.PLANNED, FreightTrip.Status.LOADING):
        raise GoodsError("A manifest can only be built for a planned or loading trip.")

    packages = list(packages or [])
    if not packages:
        raise GoodsError("A manifest needs at least one package.")

    existing = Manifest.objects.filter(trip=trip).order_by("-version").first()
    if existing is not None and existing.status != Manifest.Status.DRAFT:
        raise GoodsError("This trip's manifest has already been reconciled.")

    total_weight = sum((p.weight_kg for p in packages), ZERO)
    total_volume = sum((p.volume_m3 for p in packages), ZERO)
    capacity = cargo_capacity(trip)
    if total_weight > capacity["capacity_weight_kg"]:
        raise GoodsError("The packages exceed the vehicle's cargo weight allowance.")
    if total_volume > capacity["capacity_volume_m3"]:
        raise GoodsError("The packages exceed the vehicle's cargo volume allowance.")

    placement = _place_in_compartments(capacity, packages)

    if existing is None:
        manifest = Manifest.objects.create(
            organization=trip.organization, trip=trip, built_by=built_by
        )
    else:
        manifest = existing
        manifest.lines.all().delete()
        manifest.reconciled_at = None
        manifest.status = Manifest.Status.DRAFT
        manifest.save(update_fields=["status", "reconciled_at", "updated_at"])

    for package in packages:
        if package.organization_id != trip.organization_id:
            raise GoodsError("A package belongs to another organization.")
        if package.status == Package.Status.DELIVERED:
            raise GoodsError("A delivered package cannot be loaded again.")
        ManifestLine.objects.create(
            organization=trip.organization,
            manifest=manifest,
            package=package,
            compartment_id=placement[package.pk],
            label=package.label,
            expected_weight_kg=package.weight_kg,
        )
        # Legs keep package identity attached to the physical movement.
        leg = (
            package.shipment.legs.order_by("sequence")
            .filter(trip__isnull=True)
            .first()
        )
        if leg is not None:
            leg.trip = trip
            leg.status = ShipmentLeg.Status.LOADED
            leg.save(update_fields=["trip", "status", "updated_at"])

    if trip.status == FreightTrip.Status.PLANNED:
        trip.status = FreightTrip.Status.LOADING
        trip.save(update_fields=["status", "updated_at"])
    return manifest


# ---------------------------------------------------------------------------
# Reconciliation and release
# ---------------------------------------------------------------------------


@transaction.atomic
def reconcile_manifest(*, manifest, scanned, scanned_by=None):
    """Reconcile loading scans against the manifest.

    "Loading scans must reconcile against the manifest before release." Missing,
    extra and damaged lines become exceptions rather than being silently
    accepted, and a discrepancy leaves the manifest unreleasable.
    """
    if manifest.status == Manifest.Status.RELEASED:
        raise GoodsError("A released manifest cannot be reconciled again.")

    now = _now()
    scanned = dict(scanned or {})
    allowed = {
        ManifestLine.ScanState.SCANNED,
        ManifestLine.ScanState.DAMAGED,
        ManifestLine.ScanState.EXTRA,
    }
    for label, state in scanned.items():
        if state not in allowed:
            raise GoodsError(f"Invalid scan state for {label}.")

    expected = {line.label: line for line in manifest.lines.all()}
    discrepancy = False

    for label, line in expected.items():
        state = scanned.get(label)
        if state is None:
            line.scan_state = ManifestLine.ScanState.MISSING
            line.save(update_fields=["scan_state", "updated_at"])
            _set_package_status(line, Package.Status.MISSING)
            discrepancy = True
            _exception_for_line(
                manifest,
                line,
                kind=ShipmentException.Kind.MISSING,
                next_action=ShipmentException.NextAction.HOLD_BRANCH,
                detail=f"{label} was expected but not scanned",
                raised_by=scanned_by,
            )
            continue

        line.scan_state = state
        line.scanned_at = now
        line.save(update_fields=["scan_state", "scanned_at", "updated_at"])
        if state == ManifestLine.ScanState.DAMAGED:
            discrepancy = True
            _set_package_status(line, Package.Status.DAMAGED)
            _exception_for_line(
                manifest,
                line,
                kind=ShipmentException.Kind.DAMAGED,
                next_action=ShipmentException.NextAction.CLAIM,
                detail=f"{label} scanned damaged",
                raised_by=scanned_by,
            )

    for label, state in scanned.items():
        if label in expected:
            continue
        discrepancy = True
        ManifestLine.objects.create(
            organization=manifest.organization,
            manifest=manifest,
            package=None,
            label=label,
            scan_state=ManifestLine.ScanState.EXTRA,
            scanned_at=now,
        )
        _exception_for_line(
            manifest,
            None,
            label=label,
            kind=ShipmentException.Kind.EXTRA,
            next_action=ShipmentException.NextAction.HOLD_BRANCH,
            detail=f"{label} was scanned but is not on the manifest",
            raised_by=scanned_by,
        )

    manifest.reconciled_at = now
    manifest.status = (
        Manifest.Status.DISCREPANCY if discrepancy else Manifest.Status.RECONCILED
    )
    manifest.save(update_fields=["status", "reconciled_at", "updated_at"])
    return manifest


def _set_package_status(line, status):
    if line.package is None:
        return
    line.package.status = status
    line.package.save(update_fields=["status", "updated_at"])


def _exception_for_line(
    manifest, line, *, kind, next_action, detail, raised_by=None, label=None
):
    package = line.package if line is not None else None
    shipment = package.shipment if package is not None else None
    if shipment is None:
        # An unlisted scan has no consignment to attach to; it is still an
        # exception, recorded against the organization and the manifest.
        shipment = _manifest_shipment(manifest)
    if shipment is None:
        return None
    return ShipmentException.objects.create(
        organization=manifest.organization,
        shipment=shipment,
        package=package,
        kind=kind,
        next_action=next_action,
        detail=detail or (label or ""),
        raised_by=raised_by,
    )


def _manifest_shipment(manifest):
    """The consignment behind a manifest, for attributing a discrepancy."""
    leg = manifest.trip.legs.order_by("sequence").first()
    return leg.shipment if leg is not None else None


@transaction.atomic
def release_manifest(*, manifest, released_by=None):
    """Release a reconciled manifest onto the road.

    This is the enforcement point for "loading scans must reconcile against the
    manifest before release": an unreconciled load, an open discrepancy or an
    unresolved exception blocks the vehicle.
    """
    if manifest.status == Manifest.Status.RELEASED:
        raise GoodsError("This manifest is already released.")
    if manifest.reconciled_at is None:
        raise GoodsError("Reconcile the manifest before release.")
    if manifest.has_open_discrepancy():
        raise GoodsError("The manifest has unreconciled discrepancies.")
    if ShipmentException.objects.filter(
        shipment__legs__trip=manifest.trip, status=ShipmentException.Status.OPEN
    ).exists():
        raise GoodsError("Resolve the open shipment exceptions before release.")

    manifest.status = Manifest.Status.RELEASED
    manifest.released_at = _now()
    manifest.released_by = released_by
    manifest.save(
        update_fields=["status", "released_at", "released_by", "updated_at"]
    )
    manifest.trip.status = FreightTrip.Status.RELEASED
    manifest.trip.save(update_fields=["status", "updated_at"])
    for line in manifest.lines.filter(scan_state=ManifestLine.ScanState.SCANNED):
        _set_package_status(line, Package.Status.LOADED)
    return manifest


# ---------------------------------------------------------------------------
# Custody
# ---------------------------------------------------------------------------


@transaction.atomic
def record_custody_event(
    package,
    *,
    event_type,
    device_event_time,
    giving_party="",
    receiving_party="",
    location=None,
    location_text="",
    evidence=None,
    note="",
    corrects=None,
    correction_reason="",
    recorded_by=None,
):
    """Append one custody event. Insert-only by design.

    "A custody record identifies the package, giving party, receiving party,
    location, event type, device event time, server receipt time and evidence.
    Corrections add a new event with a reason." A correction is a new row that
    names the event it corrects; the corrected event is never mutated or
    deleted.
    """
    device_time = _aware(device_event_time, "device_event_time")
    if event_type not in CustodyEvent.EventType.values:
        raise GoodsError("Unknown custody event type.")
    if corrects is not None:
        if not correction_reason:
            raise GoodsError("A correction requires a reason.")
        if corrects.package_id != package.pk:
            raise GoodsError("A correction must reference an event for the same package.")
        event_type = CustodyEvent.EventType.CORRECTION
    if event_type in (
        CustodyEvent.EventType.DELIVERED,
        CustodyEvent.EventType.REFUSED,
        CustodyEvent.EventType.RETURNED,
    ) and not receiving_party:
        raise GoodsError("This custody event requires a receiving party.")

    event = CustodyEvent.objects.create(
        organization=package.organization,
        package=package,
        event_type=event_type,
        giving_party=giving_party,
        receiving_party=receiving_party,
        location=location,
        location_text=location_text,
        device_event_time=device_time,
        server_receipt_time=_now(),
        evidence=evidence or {},
        note=note,
        corrects_event=corrects,
        correction_reason=correction_reason,
        recorded_by=recorded_by,
    )
    if location is not None:
        package.current_location = location
        package.save(update_fields=["current_location", "updated_at"])
    return event


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------


@transaction.atomic
def record_evidence(
    shipment,
    *,
    kind,
    package=None,
    photos=None,
    notes="",
    recipient_name="",
    recipient_id_type="",
    recipient_id_last4="",
    otp_verified=False,
    signature_ref="",
    is_delegate=False,
    device_event_time=None,
    recorded_by=None,
):
    """Record collection or delivery evidence, captured separately.

    "Capture collection evidence and delivery evidence separately", and "do not
    let a photograph alone prove that the intended recipient received the
    correct package": delivery evidence without recipient verification is
    refused however many photos it carries.

    A multi-recipient order is delivered parcel by parcel, so its evidence must
    say which parcel it is about, and the recipient it names must be that
    parcel's recipient. Proving the wrong person received a parcel is the same
    failure as proving nothing.
    """
    if kind not in ShipmentEvidence.Kind.values:
        raise GoodsError("Unknown evidence kind.")
    if package is not None and package.shipment_id != shipment.pk:
        raise GoodsError("That package is not on this consignment.")

    evidence = ShipmentEvidence(
        organization=shipment.organization,
        shipment=shipment,
        package=package,
        kind=kind,
        photos=list(photos or []),
        notes=notes,
        recipient_name=recipient_name,
        recipient_id_type=recipient_id_type,
        recipient_id_last4=str(recipient_id_last4 or "")[-4:],
        otp_verified=bool(otp_verified),
        signature_ref=signature_ref,
        is_delegate=bool(is_delegate),
        device_event_time=device_event_time,
        recorded_by=recorded_by,
    )
    if kind == ShipmentEvidence.Kind.DELIVERY:
        if not evidence.is_recipient_verified():
            raise GoodsError(
                "Delivery evidence requires recipient verification; a photo alone "
                "does not prove the intended recipient received the package."
            )
        if package is None:
            if shipment.packages.filter(recipient_place__isnull=False).exists():
                raise GoodsError(
                    "This order is delivered parcel by parcel, so the evidence "
                    "must name the parcel it is about."
                )
            if not shipment.packages.filter(status=Package.Status.DELIVERED).exists():
                raise GoodsError("No package on this shipment is marked delivered.")
        else:
            if package.status != Package.Status.DELIVERED:
                raise GoodsError(
                    f"{package.label} is not marked delivered, so it cannot have "
                    "delivery evidence."
                )
            if package.recipient_name and str(recipient_name or "").strip() != (
                package.recipient_name
            ):
                raise GoodsError(
                    f"{package.label} is addressed to {package.recipient_name}, not "
                    f"{recipient_name or 'anyone else'}."
                )
    evidence.save()
    return evidence


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


@transaction.atomic
def raise_exception(
    shipment,
    *,
    kind,
    next_action,
    package=None,
    detail="",
    raised_by=None,
):
    """Raise a discrepancy or failed attempt with an explicit next action.

    "Failed attempts create an exception with the next action: retry, hold at
    branch, return or claim."
    """
    if kind not in ShipmentException.Kind.values:
        raise GoodsError("Unknown exception kind.")
    if next_action not in ShipmentException.NextAction.values:
        raise GoodsError("Unknown next action.")
    if next_action == ShipmentException.NextAction.RETRY and kind not in (
        ShipmentException.Kind.FAILED_ATTEMPT,
        ShipmentException.Kind.REFUSED,
        ShipmentException.Kind.ABSENT,
    ):
        raise GoodsError("Only a failed delivery attempt can be retried.")
    if package is not None and package.shipment_id != shipment.pk:
        raise GoodsError("The package belongs to a different shipment.")
    if kind in (
        ShipmentException.Kind.MISSING,
        ShipmentException.Kind.DAMAGED,
        ShipmentException.Kind.EXTRA,
    ) and next_action not in (
        ShipmentException.NextAction.HOLD_BRANCH,
        ShipmentException.NextAction.RETURN,
        ShipmentException.NextAction.CLAIM,
    ):
        raise GoodsError("A load discrepancy cannot be resolved by retrying delivery.")
    return ShipmentException.objects.create(
        organization=shipment.organization,
        shipment=shipment,
        package=package,
        kind=kind,
        next_action=next_action,
        detail=detail,
        raised_by=raised_by,
    )


@transaction.atomic
def resolve_exception(exception, *, note):
    if exception.status == ShipmentException.Status.RESOLVED:
        raise GoodsError("This exception is already resolved.")
    if not note:
        raise GoodsError("A resolution note is required.")
    exception.status = ShipmentException.Status.RESOLVED
    exception.resolution_note = note
    exception.resolved_at = _now()
    exception.save(
        update_fields=["status", "resolution_note", "resolved_at", "updated_at"]
    )
    if exception.kind == ShipmentException.Kind.MISSING and exception.package:
        exception.package.status = Package.Status.LOADED
        exception.package.save(update_fields=["status", "updated_at"])
    return exception


# ---------------------------------------------------------------------------
# Returns
# ---------------------------------------------------------------------------


@transaction.atomic
def create_return_shipment(shipment, *, packages, reason="", service=None, terms=None):
    """"Returns create a linked reverse shipment with its own legs and custody
    history."

    A return is a new consignment that references the original packages and runs
    the stops in reverse. The original shipment and its custody history are left
    intact.

    A courier parcel that failed delivery is returnable too: the recipient was
    absent, so the parcel was never delivered, and that is exactly when a sender
    wants it back. Such a parcel is collected from its own drop rather than
    from the order's destination, because its drop is where the van failed.
    """
    if shipment.status == Shipment.Status.CANCELLED:
        raise GoodsError("A cancelled shipment cannot be returned.")
    packages = list(packages or [])
    if not packages:
        raise GoodsError("A return needs at least one package.")
    # Each parcel is judged on its own record: one parcel's failed attempt is
    # not a licence to send back a sibling that was never attempted.
    returnable_kinds = (
        ShipmentException.Kind.FAILED_ATTEMPT,
        ShipmentException.Kind.REFUSED,
        ShipmentException.Kind.ABSENT,
    )
    failed = set(
        ShipmentException.objects.filter(
            package__in=packages,
            kind__in=returnable_kinds,
            status=ShipmentException.Status.OPEN,
        ).values_list("package_id", flat=True)
    )
    for package in packages:
        if package.shipment_id != shipment.pk:
            raise GoodsError("Only packages from the original shipment can be returned.")
        returnable = (
            Package.Status.DELIVERED,
            Package.Status.MISSING,
            Package.Status.DAMAGED,
        )
        # An order-level return of one parcel must not be blocked by its siblings.
        if package.status not in returnable and not (
            package.pk in failed
            and package.status
            in (Package.Status.PENDING, Package.Status.LOADED, Package.Status.IN_TRANSIT)
        ):
            raise GoodsError(
                f"{package.label} has not been delivered and cannot be returned."
            )

    # One parcel comes back from its own drop; several from the order's
    # destination, which is where a single-consignee consignment ended up.
    single_drop = len(packages) == 1 and packages[0].recipient_place_id is not None
    pickup = (
        packages[0].recipient_place
        if single_drop
        else shipment.destination
    )
    is_courier = (service or shipment.service).service_type == Service.ServiceType.COURIER

    window_start = _now()
    window_end = window_start + timedelta(days=1)
    return_shipment = Shipment.objects.create(
        organization=shipment.organization,
        reference=_ref("SHP"),
        service=service or shipment.service,
        sender=shipment.sender,
        sender_name=(
            packages[0].recipient_name if single_drop else shipment.recipient_name
        ),
        sender_phone=(
            packages[0].recipient_phone if single_drop else shipment.recipient_phone
        ),
        recipient_name=shipment.sender_name,
        recipient_phone=shipment.sender_phone,
        collection=pickup,
        destination=shipment.collection,
        package_count=len(packages),
        total_weight_kg=sum((p.weight_kg for p in packages), ZERO),
        total_volume_m3=sum((p.volume_m3 for p in packages), ZERO),
        volumetric_weight_kg=sum((p.volumetric_weight_kg for p in packages), ZERO),
        declared_value_minor=sum(
            (p.declared_value_minor for p in packages)
        ),
        handling_needs=list(shipment.handling_needs),
        service_window_start=window_start,
        service_window_end=window_end,
        quote=shipment.quote,
        measurement_basis=shipment.measurement_basis,
        measurement_snapshot=dict(shipment.measurement_snapshot),
        terms_snapshot=terms
        or {
            "reason": reason,
            "return_of": str(shipment.pk),
            "original_terms": shipment.terms_snapshot,
        },
        agreed_total_minor=0,
        is_return=True,
        return_of=shipment,
        status=Shipment.Status.BOOKED,
    )

    for package in packages:
        clone = _clone_package(return_shipment, package, is_courier=is_courier)
        package.status = Package.Status.RETURNED
        package.save(update_fields=["status", "updated_at"])
        # The exception that sent the parcel back is dealt with by the return, so
        # it closes here rather than leaving the order at "exception" forever. A
        # claim or a damage dispute is not settled by a return, so it stays open.
        for exception in ShipmentException.objects.filter(
            package=package,
            kind__in=returnable_kinds,
            status=ShipmentException.Status.OPEN,
        ):
            resolve_exception(
                exception,
                note=f"Parcel returned to sender on {return_shipment.reference}",
            )
        record_custody_event(
            package,
            event_type=CustodyEvent.EventType.RETURNED,
            giving_party=package.recipient_name or shipment.recipient_name,
            receiving_party=shipment.organization.name,
            location=pickup,
            device_event_time=_now(),
            note=f"Returned to sender on {return_shipment.reference}",
        )
        record_custody_event(
            clone,
            event_type=CustodyEvent.EventType.BOOKED,
            giving_party=shipment.organization.name,
            receiving_party=shipment.sender_name,
            location=pickup,
            device_event_time=_now(),
            note="Return consignment created",
        )

    ShipmentLeg.objects.create(
        organization=shipment.organization,
        shipment=return_shipment,
        stop=pickup,
        sequence=0,
        status=ShipmentLeg.Status.PLANNED,
        planned_at=window_start,
    )
    ShipmentLeg.objects.create(
        organization=shipment.organization,
        shipment=return_shipment,
        stop=shipment.collection,
        sequence=1,
        status=ShipmentLeg.Status.PLANNED,
        planned_at=window_end,
    )

    # The original's status is a fact about its own parcels, and one of them has
    # just moved. A courier order whose last outstanding parcel was sent back must
    # stop reading "exception" and say where it really is, or dispatch keeps
    # chasing a problem the return already answered.
    shipment.refresh_from_db()
    refresh_order_status(shipment)
    return return_shipment


def _clone_package(shipment, package, *, is_courier=False):
    """A return travels as its own package with its own label, addressed back to
    whoever sent the original.

    A parcel only carries a delivery place when the return is a courier run, so
    a returned freight consignment stays a single-stop consignment instead of
    being read as a multi-drop order needing per-parcel proof.
    """
    return Package.objects.create(
        organization=shipment.organization,
        shipment=shipment,
        label=_ref("PKG"),
        description=package.description,
        weight_kg=package.weight_kg,
        length_cm=package.length_cm,
        width_cm=package.width_cm,
        height_cm=package.height_cm,
        volume_m3=package.volume_m3,
        volumetric_weight_kg=package.volumetric_weight_kg,
        declared_value_minor=package.declared_value_minor,
        handling_flags=list(package.handling_flags),
        is_fragile=package.is_fragile,
        recipient_name=shipment.recipient_name if is_courier else "",
        recipient_phone=shipment.recipient_phone if is_courier else "",
        recipient_place=shipment.destination if is_courier else None,
        chargeable_weight_kg=package.chargeable_weight_kg,
    )
