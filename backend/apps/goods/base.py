"""Primitives shared by the goods service, courier and consolidation modules.

These live apart from ``services`` and ``courier`` because the two need each
other's work: the return flow in ``services`` refreshes a courier order's status,
and the plan's route-time check in ``consolidation`` needs the courier's
per-drop minute. Keeping the shared pieces here lets every module import them at
the top of the file instead of dodging a cycle with a function-local import.
"""

from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.catalog.models import Place
from apps.orders.models import Order

from .models import Package, Shipment, ShipmentException


# A drop costs the driver time whatever the parcel weighs, so a route of many
# drops is bounded by the clock.
DROP_SERVICE_MINUTES = 5


def place_or_none(value):
    """Look up a place by id, treating an absent or malformed id as no place.

    A caller sends ids as strings over JSON, so a blank or mistyped one has to
    come back as a refusal naming the record, not as a database error.
    """
    if not value:
        return None
    try:
        return Place.objects.filter(pk=value).first()
    except (ValueError, TypeError, ValidationError):
        return None


def refresh_order_status(order, now=None):
    """Keep the order's status honest as its parcels move.

    A courier order closes only when every parcel is delivered, returned or
    recorded missing; until then it is partially delivered, so the outstanding
    parcels stay visible (blueprint 9: "Partial deliveries remain visible at
    package level").

    An open exception outranks the parcel count: something is wrong on this
    order and the status has to say so. An exception that has been resolved
    stops hiding the progress underneath it, so a delivered parcel is still
    visible as delivered rather than the order sitting at "exception" forever.
    """
    now = now or timezone.now()
    packages = list(order.packages.all())
    if not packages:
        return order
    delivered = sum(1 for p in packages if p.status == Package.Status.DELIVERED)
    returned = sum(1 for p in packages if p.status == Package.Status.RETURNED)
    missing = sum(1 for p in packages if p.status == Package.Status.MISSING)
    moving = sum(
        1 for p in packages if p.status in (Package.Status.IN_TRANSIT, Package.Status.LOADED)
    )
    if order.exceptions.filter(status=ShipmentException.Status.OPEN).exists():
        status = Shipment.Status.EXCEPTION
    elif delivered + returned + missing == len(packages):
        status = Shipment.Status.DELIVERED
    elif delivered or returned or missing:
        status = Shipment.Status.PARTIALLY_DELIVERED
    elif moving:
        status = Shipment.Status.IN_TRANSIT
    else:
        return order
    if status != order.status:
        order.status = status
        order.save(update_fields=["status", "updated_at"])
    # A fully delivered shipment satisfies its commercial order. That is the
    # point the order's collected money is earned rather than held, so it is
    # completed here; the payments side recognises the revenue from the
    # delivery outbox event. A shipment with no order (the common case for a
    # direct courier booking) has nothing to settle.
    if status == Shipment.Status.DELIVERED and order.order_id is not None:
        if order.order.status == Order.Status.CONFIRMED:
            order.order.complete()
    return order
