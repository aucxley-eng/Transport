"""Outbox subscribers that settle money against delivered service (blueprint 6, 13).

Recognition is a payments concern reacting to a delivery event raised by
another bounded context (booking, goods), so it runs here as a subscriber
rather than by a direct cross-app call. Every handler is idempotent because
recognition is balance-driven: a replayed delivery finds an already-empty
unearned balance and posts nothing.

A handler only recognises when the order has actually completed. The order is
completed by the fulfilling service in the same transaction as the event, so
"the order says it is done" and "the delivery event exists" cannot disagree.
"""

from apps.common.outbox import subscribe
from apps.orders.models import Order
from apps.payments.services import recognize_revenue


@subscribe("booking.ticket_boarded")
def recognize_after_boarding(event):
    """A boarded ticket moves its order's collected money to revenue.

    The seat's obligation is satisfied when it is boarded, so this fires on
    every boarding; the last one completes the order and this recognises
    whatever unearned balance the order still holds."""
    _recognize(event.get("payload", {}).get("order_id"))


@subscribe("goods.courier_parcel_delivered")
def recognize_after_delivery(event):
    """A delivered parcel settles its order once the whole shipment is out."""
    _recognize(event.get("payload", {}).get("order_id"))


def _recognize(order_id):
    if not order_id:
        return None
    order = Order.objects.filter(pk=order_id).first()
    if order is None or order.status != Order.Status.COMPLETED:
        # Not yet fully delivered, or not an order at all: nothing is earned.
        return None
    return recognize_revenue(order=order)
