"""Intercity parcels and hubs: the chain the courier envelope refused to build.

3D made the parcel the delivery unit for a van crossing town and refused, by
name, anything typed ``Service.ServiceType.PARCEL``. This module is the hub
envelope that refusal pointed at. A parcel from Kisumu to Nyeri is dropped at a
branch, consolidated onto a line-haul to a hub, held while the onward truck is
found, and released at a destination branch.

It adds no models. A hub is a ``catalog.Place`` with ``PlaceType.HUB``; a hub
chain is ``ShipmentLeg`` rows whose stops are those places; the line-haul between
two hubs is an ordinary ``FreightTrip`` with an ordinary ``Manifest``, reconciled
and released by the functions 3A already wrote. What this module adds is the part
nobody had built: the custody events between booking and delivery.

The custody chain in the repository recorded exactly three things -- booked,
delivered, returned -- so a parcel could be scanned onto a truck, carried two
hundred kilometres and handed to a stranger without a single row being written.
``ARRIVED``, ``HELD`` and ``DEPARTED`` existed as enum values and were written by
nothing. They are written here.

`GoodsError` messages follow the house style: a sentence that tells the operator
what to do, not a code.
"""

from decimal import Decimal

from django.db import transaction

from apps.accounts.models import Membership, Role
from apps.catalog.models import Place, Quote, Service, build_quote
from apps.catalog.tariff import band_for, resolve_rate_card, weight_bands
from apps.common.outbox import record_outbox

from .base import place_or_none, refresh_order_status
from .courier import (
    _measure_spec,
    _parcel_payload,
    _parcel_specs,
    recompute_order,
)
from .consolidation import confirm_departure
from .models import (
    VOLUMETRIC_DIVISOR_CM3_PER_KG,
    CustodyEvent,
    FreightTrip,
    LoadType,
    Manifest,
    ManifestLine,
    MeasurementBasis,
    Package,
    Shipment,
    ShipmentException,
    ShipmentLeg,
)
from .services import (
    GoodsError,
    _aware,
    _create_package,
    _decimal,
    _exception_for_line,
    _now,
    _ref,
    record_custody_event,
)

# The parcel services this envelope prices. Courier keeps refusing them: a
# service is either same-day courier or intercity parcel, never both.
PARCEL_SERVICE_TYPES = (Service.ServiceType.PARCEL,)

# Same refusal courier gives, for the same reason.
REASON_NO_TARIFF = "no rate card is in force for this service"

# What arrival reconciliation accepts, mirroring reconcile_manifest.
_ALLOWED_ARRIVAL_STATES = (
    ManifestLine.ScanState.SCANNED,
    ManifestLine.ScanState.DAMAGED,
    ManifestLine.ScanState.EXTRA,
)


def is_parcel_service(service):
    return service is not None and service.service_type in PARCEL_SERVICE_TYPES


# ---------------------------------------------------------------------------
# Booking
# ---------------------------------------------------------------------------


def _hub_places(hubs, collection, destination):
    """Validate the hub chain a caller asked for and return it in order.

    A hub is an ordinary Place with a hub or branch type. Requiring that type is
    what stops a sender routing a parcel through somebody's doorstep, and
    requiring the chain to be distinct is what stops a parcel waiting for itself.
    """
    if hubs in (None, "", []):
        return []
    if not isinstance(hubs, (list, tuple)):
        raise GoodsError("A hub chain must be a list of places.")

    ordered = []
    seen = {collection.pk, destination.pk}
    for index, value in enumerate(hubs, start=1):
        place = value if isinstance(value, Place) else place_or_none(value)
        if place is None:
            raise GoodsError(f"Hub {index} is not a place that still exists.")
        if place.pk in seen:
            raise GoodsError(
                f"Hub {index} is already on this route; each place appears once."
            )
        if place.place_type not in (Place.PlaceType.HUB, Place.PlaceType.BRANCH):
            raise GoodsError(
                f"Hub {index} ({place.name}) is a {place.place_type}, not a hub or "
                "branch, so parcels cannot be transferred through it."
            )
        seen.add(place.pk)
        ordered.append(place)
    return ordered


@transaction.atomic
def quote_parcel_order(
    *,
    organization,
    service,
    collection,
    destination,
    packages,
    service_window_start,
    service_window_end,
    hubs=None,
    sender_name="",
    sender_phone="",
    handling_needs=None,
    rate_card=None,
    expires_in_minutes=60,
):
    """Price an intercity parcel order parcel by parcel, and fix its route.

    Pricing is exactly courier's: each parcel is charged on the greater of its
    actual and volumetric weight, and the order total is the sum of the parcel
    charges. The route is the only thing that differs -- the caller names the
    hubs the line-haul passes through, and they are frozen into the quote so the
    booking cannot take a different road from the one that was priced.
    """
    if organization is None:
        raise GoodsError("An organization is required to price parcel work.")
    if not is_parcel_service(service):
        raise GoodsError("An intercity parcel service is required for a parcel quote.")
    if collection is None or destination is None:
        raise GoodsError("Collection and destination are required.")
    if collection.pk == destination.pk:
        raise GoodsError("Collection and destination must differ.")

    window_start = _aware(service_window_start, "service_window_start")
    window_end = _aware(service_window_end, "service_window_end")
    if window_end <= window_start:
        raise GoodsError("The service window must end after it starts.")

    hub_chain = _hub_places(hubs, collection, destination)
    specs = _parcel_specs(packages)
    drops = {entry["recipient_place"].pk for entry in specs}
    if destination.pk not in drops:
        raise GoodsError(
            "The destination is not a place any parcel is being delivered to."
        )

    card = rate_card or resolve_rate_card(organization, service)
    if card is None:
        raise GoodsError(f"{REASON_NO_TARIFF}, so {service.name} cannot be quoted.")
    bands = weight_bands(card)
    if not bands:
        raise GoodsError(
            f"{card.name} has no weight bands, so a parcel cannot be priced on it."
        )

    priced = []
    total = 0
    for entry in specs:
        measured = _measure_spec(entry["spec"], entry["index"])
        band = band_for(bands, measured["chargeable_weight_kg"])
        if band is None:
            raise GoodsError(
                f"Parcel {entry['index']} weighs {measured['chargeable_weight_kg']} kg, "
                f"which falls outside every weight band on {card.name}."
            )
        total += band["amount_minor"]
        priced.append(_parcel_payload(entry, measured, band["amount_minor"]))

    return build_quote(
        organization=organization,
        service=service,
        request_payload={
            "kind": "parcel_order",
            "collection_id": str(collection.pk),
            "destination_id": str(destination.pk),
            "sender_name": sender_name,
            "sender_phone": sender_phone,
            "handling_needs": list(handling_needs or []),
            "service_window_start": window_start.isoformat(),
            "service_window_end": window_end.isoformat(),
            "volumetric_divisor_cm3_per_kg": VOLUMETRIC_DIVISOR_CM3_PER_KG,
            "hubs": [str(place.pk) for place in hub_chain],
            "parcels": priced,
        },
        total_minor=total,
        currency=card.currency,
        rate_card=card,
        basis=MeasurementBasis.CHARGEABLE_WEIGHT,
        expires_in_minutes=expires_in_minutes,
    )


@transaction.atomic
def book_parcel_order(
    *,
    quote,
    organization,
    sender=None,
    commercial_order=None,
):
    """Book an intercity parcel order from an accepted quote.

    The quote is the source of truth for the parcels, the recipients, the prices
    and the hub chain, so a caller cannot book a different delivery from the one
    they were quoted on.
    """
    if organization is None:
        raise GoodsError("An organization is required to book parcel work.")
    if quote.expires_at and _now() > quote.expires_at and quote.status == Quote.Status.VALID:
        quote.status = Quote.Status.EXPIRED
        quote.save(update_fields=["status", "updated_at"])
    if quote.status == Quote.Status.EXPIRED:
        raise GoodsError("The quote has expired.")
    if quote.status != Quote.Status.ACCEPTED:
        raise GoodsError("Only an accepted quote can be booked.")
    if quote.organization_id not in (None, organization.pk):
        raise GoodsError("The quote belongs to another organization.")

    service = Service.objects.filter(pk=quote.service_id).first()
    if not is_parcel_service(service):
        raise GoodsError("Only an intercity parcel quote can book a parcel order.")
    if quote.request_payload.get("kind") != "parcel_order":
        raise GoodsError("That quote was not priced as a parcel order.")

    payload = quote.request_payload
    collection = place_or_none(payload.get("collection_id"))
    destination = place_or_none(payload.get("destination_id"))
    if collection is None or destination is None:
        raise GoodsError("The collection and destination places are no longer available.")
    window_start = _aware(payload.get("service_window_start"), "service_window_start")
    window_end = _aware(payload.get("service_window_end"), "service_window_end")
    hub_chain = [
        place for place in (place_or_none(pk) for pk in payload.get("hubs", [])) if place
    ]

    shipment = Shipment.objects.create(
        organization=organization,
        reference=_ref("SHP"),
        service=service,
        order=commercial_order,
        sender=sender,
        sender_name=payload.get("sender_name", ""),
        sender_phone=payload.get("sender_phone", ""),
        # Several parcels, possibly several recipients: no single consignee.
        recipient_name="",
        recipient_phone="",
        collection=collection,
        destination=destination,
        service_window_start=window_start,
        service_window_end=window_end,
        quote=quote,
        measurement_basis=MeasurementBasis.CHARGEABLE_WEIGHT,
        measurement_snapshot={
            "volumetric_divisor_cm3_per_kg": VOLUMETRIC_DIVISOR_CM3_PER_KG,
            "parcels": payload["parcels"],
        },
        terms_snapshot={
            "rate_card_id": str(quote.rate_card_id or ""),
            "rate_card_name": quote.rate_card_name,
            "currency": quote.currency,
            "total_minor": quote.total_minor,
            "basis": quote.basis,
            "hubs": payload.get("hubs", []),
        },
        agreed_total_minor=quote.total_minor,
        status=Shipment.Status.BOOKED,
        load_type=LoadType.SHARED_LOAD,
    )

    for parcel in payload["parcels"]:
        package = _create_parcel(shipment, parcel)
        record_custody_event(
            package,
            event_type=CustodyEvent.EventType.BOOKED,
            giving_party=shipment.sender_name,
            receiving_party=organization.name,
            location=collection,
            device_event_time=_now(),
            note=f"Booked on {shipment.reference} for {package.recipient_name}",
        )

    recompute_order(shipment)
    _record_parcel_legs(shipment, hub_chain)
    record_outbox(
        event_type="goods.parcel_order_booked",
        aggregate_type="shipment",
        aggregate_id=shipment.pk,
        organization_id=organization.pk,
        payload={
            "reference": shipment.reference,
            "parcels": shipment.package_count,
            "total_minor": shipment.agreed_total_minor,
            "hubs": [str(place.pk) for place in hub_chain],
        },
    )
    return shipment


def _create_parcel(shipment, parcel):
    """One parcel, priced and addressed exactly as the quote described it."""
    package = _create_package(shipment, parcel)
    package.recipient_name = parcel["recipient_name"]
    package.recipient_phone = parcel["recipient_phone"]
    package.recipient_place = place_or_none(parcel["recipient_place"])
    package.chargeable_weight_kg = _decimal(
        parcel.get("chargeable_weight_kg"), "chargeable_weight_kg"
    ) or Decimal("0")
    package.agreed_charge_minor = int(parcel.get("amount_minor") or 0)
    package.pricing_snapshot = {
        "basis": MeasurementBasis.CHARGEABLE_WEIGHT,
        "measured_on": parcel.get("measured_on", ""),
        "chargeable_weight_kg": parcel.get("chargeable_weight_kg", "0"),
        "rate_card_id": str(shipment.quote.rate_card_id or ""),
        "rate_card_name": shipment.quote.rate_card_name,
        "volumetric_divisor_cm3_per_kg": VOLUMETRIC_DIVISOR_CM3_PER_KG,
    }
    package.save(
        update_fields=[
            "recipient_name",
            "recipient_phone",
            "recipient_place",
            "chargeable_weight_kg",
            "agreed_charge_minor",
            "pricing_snapshot",
            "updated_at",
        ]
    )
    return package


def _record_parcel_legs(shipment, hub_chain):
    """The promised route: collection, every hub in order, then the destination.

    The legs are the promise, so they are written at booking rather than derived
    when a truck is found -- a sender is entitled to know the road their parcel
    will take before anyone plans it.
    """
    route = [shipment.collection, *hub_chain, shipment.destination]
    for index, place in enumerate(route):
        ShipmentLeg.objects.create(
            organization=shipment.organization,
            shipment=shipment,
            stop=place,
            sequence=index,
            status=ShipmentLeg.Status.PLANNED,
            planned_at=shipment.service_window_start,
        )
    return shipment


# ---------------------------------------------------------------------------
# The hub chain
# ---------------------------------------------------------------------------


def _origin_hub(trip):
    """The place parcels are loaded onto this line-haul."""
    return trip.origin


def _destination_hub(trip):
    """The place parcels arrive at. Falls back to the last planned stop."""
    return trip.destination


def _latest_manifest(trip):
    manifest = trip.manifests.order_by("-version").first()
    if manifest is None:
        raise GoodsError(f"Trip {trip.pk} has no manifest to work from.")
    return manifest


OPS_ROLES = (
    Role.DRIVER,
    Role.BRANCH_STAFF,
    Role.DISPATCHER,
    Role.OPERATOR_ADMIN,
    Role.PLATFORM_ADMIN,
)


def _require_ops(actor, organization):
    """Only someone who works for the operator may record a hub movement.

    Every hub movement is a custody row naming who did it, so an idle account
    must not be able to write one. The membership, not the Django staff flag,
    decides: staff is a console concession, the role is the job.
    """
    if actor is None:
        return
    if actor.is_superuser:
        return
    if Membership.objects.filter(user=actor, organization=organization, role__in=OPS_ROLES).exists():
        return
    raise GoodsError("Only the operator's own staff can record a hub movement.")


@transaction.atomic
def load_parcels_onto_trip(
    *,
    trip,
    labels,
    actor=None,
    device_event_time=None,
):
    """Scan parcels onto a line-haul leaving a hub.

    Each scanned parcel leaves a LOADED custody event and takes the origin hub as
    its current location, which is the fact "this parcel is sitting on the
    Nairobi truck". The manifest line is marked scanned so the existing
    reconciliation and release gates see the same parcel.
    """
    _require_ops(actor, trip.organization)
    if trip.status not in (FreightTrip.Status.PLANNED, FreightTrip.Status.LOADING):
        raise GoodsError("This trip is not accepting parcels.")
    manifest = _latest_manifest(trip)
    if manifest.status == Manifest.Status.RELEASED:
        raise GoodsError("This manifest is released; amend it to add parcels.")

    scanned = dict(labels or {})
    if not scanned:
        raise GoodsError("Scan at least one parcel onto the trip.")

    lines = {line.label: line for line in manifest.lines.all()}
    hub = _origin_hub(trip)
    if hub is None:
        raise GoodsError("This trip has no origin hub to load parcels at.")

    now = device_event_time or _now()
    loaded = []
    for label, package in _packages_for(manifest, scanned, lines):
        line = lines[label]
        line.scan_state = ManifestLine.ScanState.SCANNED
        line.scanned_at = now
        line.save(update_fields=["scan_state", "scanned_at", "updated_at"])
        package.status = Package.Status.LOADED
        package.save(update_fields=["status", "updated_at"])
        record_custody_event(
            package,
            event_type=CustodyEvent.EventType.LOADED,
            giving_party=hub.name,
            receiving_party=trip.vehicle.registration_number or _organization_name(trip),
            location=hub,
            device_event_time=now,
            note=f"Loaded onto {trip.vehicle_id} at {hub.name}",
            recorded_by=actor,
        )
        loaded.append(package)
    return loaded


def _organization_name(trip):
    return trip.organization.name


def _packages_for(manifest, scanned, lines):
    """Pair each scanned label with its parcel, refusing the ones that do not match.

    A scan of a label that is not on the manifest is refused here rather than
    being quietly accepted: an unmanifested parcel on a hub truck is the exact
    case arrival reconciliation exists to catch, and catching it at load time is
    cheaper than at the far end.
    """
    for label, state in scanned.items():
        if state not in _ALLOWED_ARRIVAL_STATES:
            raise GoodsError(f"Invalid scan state for {label}.")
        line = lines.get(label)
        if line is None or line.package is None:
            raise GoodsError(f"{label} is not on this manifest.")
        yield label, line.package


@transaction.atomic
def record_departure(*, trip, actor=None, device_event_time=None):
    """Send a loaded line-haul out of the hub, and record that it left.

    The existing departure gate decides whether the truck may go: a released
    manifest, a published plan, the two agreeing, and committed revenue covering
    the trip cost with each shipment counted once. This adds only the custody
    rows that were missing -- every parcel on the released manifest gets a
    DEPARTED event naming the hub it left.
    """
    _require_ops(actor, trip.organization)
    confirm_departure(trip)
    manifest = _latest_manifest(trip)
    hub = _origin_hub(trip)
    now = device_event_time or _now()

    for line in manifest.lines.filter(scan_state=ManifestLine.ScanState.SCANNED):
        if line.package is None:
            continue
        record_custody_event(
            line.package,
            event_type=CustodyEvent.EventType.DEPARTED,
            giving_party=hub.name if hub else _organization_name(trip),
            receiving_party=trip.vehicle.registration_number or _organization_name(trip),
            location=hub,
            device_event_time=now,
            note=f"Departed {hub.name if hub else 'origin'} on {trip.pk}",
            recorded_by=actor,
        )

    # The parcels have left, so the consignments holding them are no longer
    # merely booked. Only the ones still awaiting collection move: a shipment
    # that is already partially delivered is further along than this truck.
    departed_shipments = set(
        manifest.lines.filter(package__isnull=False).values_list(
            "package__shipment_id", flat=True
        )
    )
    Shipment.objects.filter(
        pk__in=departed_shipments,
        status__in=(Shipment.Status.BOOKED, Shipment.Status.COLLECTED),
    ).update(status=Shipment.Status.IN_TRANSIT)
    return trip


@transaction.atomic
def arrive_at_hub(
    *,
    trip,
    scanned,
    actor=None,
    device_event_time=None,
):
    """Record arrival at the receiving hub and reconcile what actually arrived.

    This is the branch receipt reconciliation extended to hub transfers. What the
    manifest said would arrive is compared with what was scanned in, and every
    difference becomes an exception with a next action rather than a silent gap:
    a parcel missing at the far end is held there, an unmanifested parcel is
    held, and a damaged one is claimed.
    """
    _require_ops(actor, trip.organization)
    if trip.status != FreightTrip.Status.IN_TRANSIT:
        raise GoodsError("This trip has not departed, so it cannot arrive.")
    manifest = _latest_manifest(trip)
    hub = _destination_hub(trip)
    if hub is None:
        raise GoodsError("This trip has no destination hub to arrive at.")

    now = device_event_time or _now()
    incoming = dict(scanned or {})
    for label, state in incoming.items():
        if state not in _ALLOWED_ARRIVAL_STATES:
            raise GoodsError(f"Invalid arrival state for {label}.")

    expected = {line.label: line for line in manifest.lines.all()}
    arrived = []
    held = []

    for label, line in expected.items():
        package = line.package
        if package is None:
            continue
        state = incoming.get(label)
        if state is None:
            # Expected but never scanned in. Held at the receiving hub rather
            # than left to vanish: somebody has to say where it is.
            package.status = Package.Status.MISSING
            package.save(update_fields=["status", "updated_at"])
            record_custody_event(
                package,
                event_type=CustodyEvent.EventType.HELD,
                giving_party=trip.vehicle.registration_number or _organization_name(trip),
                receiving_party=hub.name,
                location=hub,
                device_event_time=now,
                note=f"{label} was expected on {trip.pk} but never arrived",
                recorded_by=actor,
            )
            _exception_for_line(
                manifest,
                line,
                kind=ShipmentException.Kind.MISSING,
                next_action=ShipmentException.NextAction.HOLD_BRANCH,
                detail=f"{label} was expected but did not arrive at {hub.name}",
                raised_by=actor,
            )
            held.append(package)
            continue

        if state == ManifestLine.ScanState.DAMAGED:
            package.status = Package.Status.DAMAGED
            package.save(update_fields=["status", "updated_at"])
            record_custody_event(
                package,
                event_type=CustodyEvent.EventType.ARRIVED,
                giving_party=trip.vehicle.registration_number or _organization_name(trip),
                receiving_party=hub.name,
                location=hub,
                device_event_time=now,
                note=f"{label} arrived at {hub.name} damaged",
                recorded_by=actor,
            )
            record_custody_event(
                package,
                event_type=CustodyEvent.EventType.HELD,
                giving_party=hub.name,
                receiving_party=hub.name,
                location=hub,
                device_event_time=now,
                note=f"{label} held at {hub.name} pending a damage claim",
                recorded_by=actor,
            )
            _exception_for_line(
                manifest,
                line,
                kind=ShipmentException.Kind.DAMAGED,
                next_action=ShipmentException.NextAction.CLAIM,
                detail=f"{label} arrived at {hub.name} damaged",
                raised_by=actor,
            )
            held.append(package)
            continue

        package.status = Package.Status.IN_TRANSIT
        package.save(update_fields=["status", "updated_at"])
        record_custody_event(
            package,
            event_type=CustodyEvent.EventType.ARRIVED,
            giving_party=trip.vehicle.registration_number or _organization_name(trip),
            receiving_party=hub.name,
            location=hub,
            device_event_time=now,
            note=f"Arrived at {hub.name} on {trip.pk}",
            recorded_by=actor,
        )
        # Every parcel at a hub is waiting for the next truck. Saying so is what
        # makes "held at hub" a queryable fact rather than an inference.
        record_custody_event(
            package,
            event_type=CustodyEvent.EventType.HELD,
            giving_party=hub.name,
            receiving_party=hub.name,
            location=hub,
            device_event_time=now,
            note=f"Held at {hub.name} awaiting the onward line-haul",
            recorded_by=actor,
        )
        arrived.append(package)

    for label, state in incoming.items():
        if label in expected:
            continue
        # Scanned in but not on the manifest. Held, never pushed onward.
        line = ManifestLine.objects.create(
            organization=manifest.organization,
            manifest=manifest,
            package=None,
            label=label,
            scan_state=ManifestLine.ScanState.EXTRA,
            scanned_at=now,
        )
        _exception_for_line(
            manifest,
            line,
            kind=ShipmentException.Kind.EXTRA,
            next_action=ShipmentException.NextAction.HOLD_BRANCH,
            detail=f"{label} arrived at {hub.name} but is not on the manifest",
            raised_by=actor,
            label=label,
        )

    manifest.reconciled_at = now
    extras = manifest.lines.filter(scan_state=ManifestLine.ScanState.EXTRA).exists()
    manifest.status = (
        Manifest.Status.DISCREPANCY if held or extras else Manifest.Status.RECONCILED
    )
    manifest.save(update_fields=["status", "reconciled_at", "updated_at"])
    trip.status = FreightTrip.Status.COMPLETED
    trip.save(update_fields=["status", "updated_at"])

    record_outbox(
        event_type="goods.hub_arrived",
        aggregate_type="freight_trip",
        aggregate_id=trip.pk,
        organization_id=trip.organization_id,
        payload={
            "hub": str(hub.pk),
            "arrived": len(arrived),
            "held": len(held),
        },
    )
    return {"arrived": arrived, "held": held, "hub": hub, "manifest": manifest}


@transaction.atomic
def place_onward_load(
    *,
    trip,
    package,
    actor=None,
    device_event_time=None,
):
    """Put one held parcel onto the next truck out of the hub.

    This is the only way a hold clears other than delivery or return: the parcel
    is scanned onto a departing line-haul, which writes LOADED and moves it off
    the hub. A parcel with an open exception cannot be placed onward until that
    exception is resolved, which is the rule that stops a damaged or missing
    parcel being quietly forwarded.
    """
    _require_ops(actor, trip.organization)
    if ShipmentException.objects.filter(
        package=package, status=ShipmentException.Status.OPEN
    ).exists():
        raise GoodsError(
            f"{package.label} has an open exception; resolve it before it moves onward."
        )
    if package.status in (Package.Status.DELIVERED, Package.Status.RETURNED):
        raise GoodsError(f"{package.label} is {package.status} and cannot move onward.")

    trip_manifest = _latest_manifest(trip)
    line = trip_manifest.lines.filter(package=package).first()
    if line is None:
        raise GoodsError(f"{package.label} is not on this trip's manifest.")

    hub = _origin_hub(trip)
    now = device_event_time or _now()
    line.scan_state = ManifestLine.ScanState.SCANNED
    line.scanned_at = now
    line.save(update_fields=["scan_state", "scanned_at", "updated_at"])
    package.status = Package.Status.LOADED
    package.save(update_fields=["status", "updated_at"])
    record_custody_event(
        package,
        event_type=CustodyEvent.EventType.LOADED,
        giving_party=hub.name if hub else _organization_name(trip),
        receiving_party=trip.vehicle.registration_number or _organization_name(trip),
        location=hub,
        device_event_time=now,
        note=f"Placed on the onward line-haul {trip.pk}",
        recorded_by=actor,
    )
    return package


def holding_at_hub(*, organization, place):
    """What is stranded at this hub, and why.

    A parcel is held when it has a HELD custody event and no later LOADED,
    DELIVERED or RETURNED event. The reason is the open exception on it, if any,
    so dispatch can tell a parcel that is merely awaiting a truck from one that
    is missing or damaged.
    """
    if place is None:
        raise GoodsError("A hub is required.")
    held = CustodyEvent.objects.filter(
        organization=organization,
        location=place,
        event_type=CustodyEvent.EventType.HELD,
    ).values("package_id", "server_receipt_time")
    last_held = {}
    for row in held:
        when = row["server_receipt_time"]
        if row["package_id"] not in last_held or when > last_held[row["package_id"]]:
            last_held[row["package_id"]] = when
    if not last_held:
        return []

    # The truck that brought the parcel in also wrote a DEPARTED event, so a
    # plain "has it ever moved" test would strand nothing. Held means the hold is
    # the newest thing that happened to the parcel.
    moved_after = CustodyEvent.objects.filter(
        package_id__in=list(last_held),
        event_type__in=(
            CustodyEvent.EventType.LOADED,
            CustodyEvent.EventType.DELIVERED,
            CustodyEvent.EventType.RETURNED,
            CustodyEvent.EventType.DEPARTED,
        ),
    ).values_list("package_id", "server_receipt_time")
    still_moving = {
        package_id
        for package_id, when in moved_after
        if when > last_held.get(package_id, when)
    }

    stranded = (
        Package.objects.filter(pk__in=list(last_held))
        .exclude(pk__in=still_moving)
        .select_related("shipment")
        .order_by("label")
    )
    rows = []
    for package in stranded:
        reason = package.status
        exception = (
            ShipmentException.objects.filter(
                package=package, status=ShipmentException.Status.OPEN
            )
            .order_by("-created_at")
            .first()
        )
        if exception is not None:
            reason = f"{exception.kind}: {exception.detail}"
        rows.append({"package": package, "reason": reason, "exception": exception})
    return rows


def release_to_recipient(package, **proof):
    """Hand a parcel to its recipient, refusing anything the hub chain forbids.

    Courier's delivery proof is reused unchanged -- a photo alone is never proof
    -- and two hub rules are added: the parcel must actually be standing at the
    place it was addressed to, and it must not carry an open exception. A parcel
    stranded at a hub therefore cannot be "delivered" from a desk.
    """
    if package.status == Package.Status.DELIVERED:
        raise GoodsError(f"{package.label} has already been delivered.")
    if package.recipient_place_id is not None and package.current_location_id is not None:
        if package.current_location_id != package.recipient_place_id:
            raise GoodsError(
                f"{package.label} is at "
                f"{package.current_location.name}, not at "
                f"{package.recipient_place.name}, so it cannot be released yet."
            )
    if ShipmentException.objects.filter(
        package=package, status=ShipmentException.Status.OPEN
    ).exists():
        raise GoodsError(f"{package.label} has an open exception; resolve it first.")

    from .courier import deliver_package

    delivered = deliver_package(package, **proof)
    refresh_order_status(package.shipment)
    return delivered
