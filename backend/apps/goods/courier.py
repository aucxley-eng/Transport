"""Local courier: one pickup, many parcels, many recipients.

Freight books a consignment to one consignee. A courier order cannot: one van
picks up five parcels for five people and drops at five addresses. This module
makes the parcel the delivery unit and prices each one individually, which is the
first consumer of the rate card in the repository.

It reuses the manifest, custody, capacity and plan machinery rather than
inventing a parallel one. The parcel, not the order, is what gets loaded,
delivered, proved and returned.

`GoodsError` messages follow the house style: a sentence that tells the operator
what to do, not a code.
"""

from decimal import Decimal

from django.db import transaction

from apps.catalog.models import Place, Quote, Service, build_quote
from apps.catalog.tariff import band_for, resolve_rate_card, weight_bands
from apps.common.outbox import record_outbox

from .base import place_or_none, refresh_order_status
from .models import (
    VOLUMETRIC_DIVISOR_CM3_PER_KG,
    CustodyEvent,
    LoadType,
    MeasurementBasis,
    Package,
    Shipment,
    ShipmentEvidence,
    ShipmentLeg,
)
from .services import (
    GoodsError,
    _aware,
    _create_package,
    _decimal,
    _now,
    _ref,
    record_custody_event,
    record_evidence,
    raise_exception,
)

ZERO = Decimal("0")

# The courier services this envelope prices. Intercity parcels are
# ServiceType.PARCEL and belong to a hub envelope, so they are refused here
# rather than quietly treated as same-day work.
COURIER_SERVICE_TYPES = (Service.ServiceType.COURIER,)

# What a caller gets instead of a price when there is no tariff to price from.
REASON_NO_TARIFF = "no rate card is in force for this service"


def is_courier_service(service):
    return service is not None and service.service_type in COURIER_SERVICE_TYPES


def _parcel_specs(packages):
    """Normalise the parcel list a caller supplied, keeping their order.

    Every refusal names the parcel by position, so a sender fixing a five-parcel
    order does not have to guess which one was wrong.
    """
    if not isinstance(packages, (list, tuple)) or not packages:
        raise GoodsError("A courier order needs at least one parcel.")
    specs = []
    for index, spec in enumerate(packages, start=1):
        if not isinstance(spec, dict):
            raise GoodsError(f"Parcel {index} is not a valid description.")
        recipient_name = str(spec.get("recipient_name") or "").strip()
        recipient_phone = str(spec.get("recipient_phone") or "").strip()
        place = place_or_none(spec.get("recipient_place"))
        if not recipient_name:
            raise GoodsError(f"Parcel {index} needs the name of the person it is for.")
        if not recipient_phone:
            raise GoodsError(f"Parcel {index} needs a contact for the person it is for.")
        if place is None:
            raise GoodsError(f"Parcel {index} needs a delivery place.")
        specs.append(
            {
                "index": index,
                "spec": spec,
                "recipient_name": recipient_name,
                "recipient_phone": recipient_phone,
                "recipient_place": place,
            }
        )
    return specs


def _measure_spec(spec, index):
    """Weight, volume and chargeable weight of one parcel, measured exactly the
    way the model measures them, so the quote and the booking cannot disagree.

    The chargeable weight is the greater of actual and volumetric mass, which is
    what stops a light, room-filling box being sent for the price of a dense
    one. The basis that decided it is returned alongside.
    """
    weight = _decimal(spec.get("weight_kg"), "weight_kg") or ZERO
    length = int(spec.get("length_cm") or 0)
    width = int(spec.get("width_cm") or 0)
    height = int(spec.get("height_cm") or 0)
    if min(length, width, height) < 0:
        raise GoodsError("Package dimensions cannot be negative.")
    if weight < 0:
        raise GoodsError("Package weight cannot be negative.")
    if weight <= 0 and not (length and width and height):
        raise GoodsError(f"Parcel {index} needs a weight or dimensions.")

    volume = ((Decimal(length) * Decimal(width) * Decimal(height)) / Decimal(1_000_000))
    volume = volume.quantize(Decimal("0.001"))
    volumetric = ZERO
    if length and width and height:
        volumetric = (
            (Decimal(length) * Decimal(width) * Decimal(height))
            / Decimal(VOLUMETRIC_DIVISOR_CM3_PER_KG)
        ).quantize(Decimal("0.001"))
    chargeable = max(weight, volumetric)
    return {
        "weight_kg": weight,
        "volume_m3": volume,
        "volumetric_weight_kg": volumetric,
        "chargeable_weight_kg": chargeable,
        "measured_on": "actual_weight" if weight >= volumetric else "volumetric",
    }


def _parcel_payload(entry, measured, amount_minor):
    """The frozen description of one priced parcel, stored on the quote and
    copied verbatim onto the parcel at booking."""
    spec = entry["spec"]
    return {
        "parcel": entry["index"],
        "description": spec.get("description", ""),
        "recipient_name": entry["recipient_name"],
        "recipient_phone": entry["recipient_phone"],
        "recipient_place": str(entry["recipient_place"].pk),
        "weight_kg": str(measured["weight_kg"]),
        "volume_m3": str(measured["volume_m3"]),
        "volumetric_weight_kg": str(measured["volumetric_weight_kg"]),
        "chargeable_weight_kg": str(measured["chargeable_weight_kg"]),
        "measured_on": measured["measured_on"],
        "length_cm": int(spec.get("length_cm") or 0),
        "width_cm": int(spec.get("width_cm") or 0),
        "height_cm": int(spec.get("height_cm") or 0),
        "declared_value_minor": int(spec.get("declared_value_minor") or 0),
        "handling_flags": list(spec.get("handling_flags") or []),
        "is_fragile": bool(spec.get("is_fragile", False)),
        "amount_minor": amount_minor,
    }


@transaction.atomic
def quote_courier_order(
    *,
    organization,
    service,
    collection,
    destination,
    packages,
    service_window_start,
    service_window_end,
    sender_name="",
    sender_phone="",
    handling_needs=None,
    rate_card=None,
    expires_in_minutes=60,
):
    """Price a courier order parcel by parcel against a weight-band tariff.

    Each parcel is charged on the greater of its actual and volumetric weight,
    and the order total is exactly the sum of the parcel charges. A parcel that
    falls outside every band on the card is refused rather than priced at the
    nearest band, and an account with no card in force is refused rather than
    quoted at zero.
    """
    if organization is None:
        raise GoodsError("An organization is required to price courier work.")
    if not is_courier_service(service):
        raise GoodsError("A courier service is required for a courier quote.")
    if collection is None or destination is None:
        raise GoodsError("Collection and destination are required.")
    if collection.pk == destination.pk:
        raise GoodsError("Collection and destination must differ.")

    window_start = _aware(service_window_start, "service_window_start")
    window_end = _aware(service_window_end, "service_window_end")
    if window_end <= window_start:
        raise GoodsError("The service window must end after it starts.")

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
        priced.append(
            _parcel_payload(
                entry,
                measured,
                band["amount_minor"],
            )
        )

    return build_quote(
        organization=organization,
        service=service,
        request_payload={
            "kind": "courier_order",
            "collection_id": str(collection.pk),
            "destination_id": str(destination.pk),
            "sender_name": sender_name,
            "sender_phone": sender_phone,
            "handling_needs": list(handling_needs or []),
            "service_window_start": window_start.isoformat(),
            "service_window_end": window_end.isoformat(),
            "volumetric_divisor_cm3_per_kg": VOLUMETRIC_DIVISOR_CM3_PER_KG,
            "parcels": priced,
        },
        total_minor=total,
        currency=card.currency,
        rate_card=card,
        basis=MeasurementBasis.CHARGEABLE_WEIGHT,
        expires_in_minutes=expires_in_minutes,
    )


@transaction.atomic
def book_courier_order(
    *,
    quote,
    organization,
    sender=None,
    load_type=None,
    commercial_order=None,
):
    """Book a courier order from an accepted quote.

    The quote is the source of truth for the parcels, the recipients, the
    weights and the prices, so a caller cannot book a different delivery from
    the one they were quoted on. Nothing here is read from the request beyond
    who is sending.
    """
    if organization is None:
        raise GoodsError("An organization is required to book courier work.")
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
    if not is_courier_service(service):
        raise GoodsError("Only a courier quote can book a courier order.")
    if quote.request_payload.get("kind") != "courier_order":
        raise GoodsError("That quote was not priced as a courier order.")
    if load_type is not None and load_type != LoadType.SHARED_LOAD:
        raise GoodsError(
            "A courier order rides alongside other parcels; it is never a full "
            "truckload."
        )

    payload = quote.request_payload
    collection = place_or_none(payload.get("collection_id"))
    destination = place_or_none(payload.get("destination_id"))
    if collection is None or destination is None:
        raise GoodsError("The collection and destination places are no longer available.")
    window_start = _aware(payload.get("service_window_start"), "service_window_start")
    window_end = _aware(payload.get("service_window_end"), "service_window_end")

    order = Shipment.objects.create(
        organization=organization,
        reference=_ref("SHP"),
        service=service,
        order=commercial_order,
        sender=sender,
        sender_name=payload.get("sender_name", ""),
        sender_phone=payload.get("sender_phone", ""),
        # One pickup, several recipients: the order names no single consignee.
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
        },
        agreed_total_minor=quote.total_minor,
        status=Shipment.Status.BOOKED,
        load_type=LoadType.SHARED_LOAD,
    )

    for parcel in payload["parcels"]:
        package = _create_package(order, parcel)
        package.recipient_name = parcel["recipient_name"]
        package.recipient_phone = parcel["recipient_phone"]
        package.recipient_place = place_or_none(parcel["recipient_place"])
        package.chargeable_weight_kg = _decimal(
            parcel.get("chargeable_weight_kg"), "chargeable_weight_kg"
        ) or ZERO
        package.agreed_charge_minor = int(parcel.get("amount_minor") or 0)
        package.pricing_snapshot = {
            "basis": MeasurementBasis.CHARGEABLE_WEIGHT,
            "measured_on": parcel.get("measured_on", ""),
            "chargeable_weight_kg": parcel.get("chargeable_weight_kg", "0"),
            "rate_card_id": str(quote.rate_card_id or ""),
            "rate_card_name": quote.rate_card_name,
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
        record_custody_event(
            package,
            event_type=CustodyEvent.EventType.BOOKED,
            giving_party=order.sender_name,
            receiving_party=organization.name,
            location=collection,
            device_event_time=_now(),
            note=f"Booked on {order.reference} for {package.recipient_name}",
        )

    recompute_order(order)
    record_order_legs(order)
    record_outbox(
        event_type="goods.courier_order_booked",
        aggregate_type="shipment",
        aggregate_id=order.pk,
        organization_id=organization.pk,
        payload={
            "reference": order.reference,
            "parcels": order.package_count,
            "total_minor": order.agreed_total_minor,
        },
    )
    return order


def recompute_order(order):
    """Roll the parcel numbers up onto the order.

    The order's money is the sum of its parcels' charges, so the two can never
    disagree. Freight does the same for weight and volume.
    """
    packages = list(order.packages.all())
    order.package_count = len(packages)
    order.total_weight_kg = sum((p.weight_kg for p in packages), ZERO)
    order.total_volume_m3 = sum((p.volume_m3 for p in packages), ZERO)
    order.volumetric_weight_kg = sum((p.volumetric_weight_kg for p in packages), ZERO)
    order.declared_value_minor = sum(
        (p.declared_value_minor for p in packages), 0
    )
    order.agreed_total_minor = sum((p.agreed_charge_minor for p in packages))
    order.save(
        update_fields=[
            "package_count",
            "total_weight_kg",
            "total_volume_m3",
            "volumetric_weight_kg",
            "declared_value_minor",
            "agreed_total_minor",
            "updated_at",
        ]
    )
    return order


def record_order_legs(order):
    """The legs of a courier order: the pickup, then every distinct drop.

    Freight records a collection leg and a destination leg. A courier order
    visits each parcel's place, and its destination is one of those places, so
    there is no separate destination leg. The plan is what sequences the drops;
    the legs record the places the order promised to be served at.
    """
    ShipmentLeg.objects.create(
        organization=order.organization,
        shipment=order,
        stop=order.collection,
        sequence=0,
        status=ShipmentLeg.Status.PLANNED,
        planned_at=order.service_window_start,
    )
    drops = (
        Place.objects.filter(
            pk__in=order.packages.values_list("recipient_place_id", flat=True)
        )
        .exclude(pk=order.collection_id)
        .order_by("name", "pk")
    )
    for index, place in enumerate(drops, start=1):
        ShipmentLeg.objects.create(
            organization=order.organization,
            shipment=order,
            stop=place,
            sequence=index,
            status=ShipmentLeg.Status.PLANNED,
            planned_at=order.service_window_start,
        )
    return order


def drop_places(shipment):
    """Every place this order stops at: the collection, then each parcel's drop.

    This is the set a plan must cover. A multi-drop order therefore needs a stop
    per parcel place, not one stop at the order's destination.
    """
    drops = list(
        Place.objects.filter(
            pk__in=shipment.packages.values_list("recipient_place_id", flat=True)
        ).order_by("name", "pk")
    )
    return [shipment.collection] + [p for p in drops if p.pk != shipment.collection_id]


@transaction.atomic
def deliver_package(
    package,
    *,
    recipient_name,
    recipient_id_type="",
    recipient_id_last4="",
    otp_verified=False,
    signature_ref="",
    is_delegate=False,
    photos=None,
    notes="",
    device_event_time=None,
    recorded_by=None,
):
    """Deliver one parcel and prove it to the person it was addressed to.

    A photo alone is refused, and so is proof from anyone other than the parcel's
    recipient: blueprint 9 says a photograph must not be allowed to prove that
    the intended recipient received the correct package, and a signature from a
    bystander is the same failure in a different form.
    """
    order = package.shipment
    if package.status == Package.Status.DELIVERED:
        raise GoodsError(f"{package.label} has already been delivered.")
    if package.status in (Package.Status.RETURNED, Package.Status.MISSING):
        raise GoodsError(f"{package.label} is {package.status} and cannot be delivered.")
    if not package.recipient_name:
        raise GoodsError(
            f"{package.label} names no recipient, so there is nobody to prove "
            "delivery to."
        )
    if str(recipient_name or "").strip() != package.recipient_name:
        raise GoodsError(
            f"{package.label} is addressed to {package.recipient_name}, not "
            f"{recipient_name or 'anyone else'}."
        )
    if order.status in (Shipment.Status.CANCELLED, Shipment.Status.DELIVERED):
        raise GoodsError("This order is closed.")

    # Marked first so the evidence check sees the parcel as delivered; the whole
    # thing is one transaction, so a refused proof leaves nothing behind.
    package.status = Package.Status.DELIVERED
    package.save(update_fields=["status", "updated_at"])
    evidence = record_evidence(
        order,
        kind=ShipmentEvidence.Kind.DELIVERY,
        package=package,
        photos=photos,
        notes=notes,
        recipient_name=recipient_name,
        recipient_id_type=recipient_id_type,
        recipient_id_last4=recipient_id_last4,
        otp_verified=otp_verified,
        signature_ref=signature_ref,
        is_delegate=is_delegate,
        device_event_time=device_event_time,
        recorded_by=recorded_by,
    )
    record_custody_event(
        package,
        event_type=CustodyEvent.EventType.DELIVERED,
        giving_party=order.organization.name,
        receiving_party=package.recipient_name,
        location=package.recipient_place,
        device_event_time=device_event_time or _now(),
        evidence={"evidence": str(evidence.pk)},
        note=f"Delivered on {order.reference}",
    )
    refresh_order_status(order)
    record_outbox(
        event_type="goods.courier_parcel_delivered",
        aggregate_type="package",
        aggregate_id=package.pk,
        organization_id=order.organization_id,
        payload={
            "order": order.reference,
            "shipment_id": str(order.pk),
            "order_id": str(order.order_id) if order.order_id else None,
            "parcel": package.label,
            "recipient": package.recipient_name,
        },
    )
    return package


def fail_delivery_attempt(package, *, kind, next_action, detail="", raised_by=None):
    """Record a failed attempt against one parcel.

    The parcel stays undelivered. A failed attempt is an exception with a next
    action, not a delivery, so the parcel remains visible on the order.
    """
    if package.status == Package.Status.DELIVERED:
        raise GoodsError(f"{package.label} has already been delivered.")
    exception = raise_exception(
        package.shipment,
        kind=kind,
        next_action=next_action,
        package=package,
        detail=detail,
        raised_by=raised_by,
    )
    # The order carries the open exception in its status, so dispatch sees the
    # problem on the consignment and not only on the parcel.
    refresh_order_status(package.shipment)
    return exception
