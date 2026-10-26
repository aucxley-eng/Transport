"""Payment orchestration: idempotent attempts, normalized callbacks, and the
double-entry postings that keep collections auditable (blueprint 13)."""

import uuid

from django.db import transaction

from apps.audit.models import record_audit
from apps.common.outbox import record_outbox
from apps.orders.models import Order
from apps.payments import ledger
from apps.payments.models import PaymentAttempt, Refund
from apps.payments.provider import (
    ProviderError,
    ProviderRejected,
    ProviderUnavailable,
    get_provider,
)

TERMINAL_CALLBACK_STATES = ("success", "failed")


class PaymentError(Exception):
    pass


def create_payment_attempt(
    *,
    organization_id,
    order_id,
    idempotency_key,
    amount_minor,
    currency="KES",
    customer_phone="",
    provider_slug="mpesa",
):
    """Create or return an existing attempt for (provider, idempotency_key).

    Replaying the same request yields the same business effect; misusing a key
    for a different amount is rejected (blueprint 23)."""
    order = Order.objects.filter(pk=order_id).first()
    if order is None:
        raise PaymentError("Order not found.")

    # The attempt must belong to the order's organization: never let a caller
    # attach a payment to a foreign organization (operator isolation).
    if organization_id is None:
        organization_id = order.organization_id
    elif order.organization_id is not None and order.organization_id != organization_id:
        raise PaymentError("The order belongs to a different organization.")

    # A collection must match the priced order total; this is what makes an
    # under-payment impossible (blueprint 13, 23).
    if amount_minor != order.total_minor or currency != order.currency:
        raise PaymentError(
            f"Attempt amount must match the order total "
            f"({order.total_minor} {order.currency})."
        )

    with transaction.atomic():
        attempt, created = PaymentAttempt.objects.get_or_create(
            provider=provider_slug,
            idempotency_key=idempotency_key,
            defaults={
                "organization_id": organization_id,
                "order": order,
                "expected_amount_minor": amount_minor,
                "currency": currency,
                "customer_phone": customer_phone,
            },
        )
        if not created:
            if (
                attempt.expected_amount_minor != amount_minor
                or attempt.currency != currency
            ):
                raise PaymentError(
                    "Idempotency key reused with a different amount or currency."
                )
            return attempt  # already initiated; nothing new to do

        provider = get_provider(provider_slug)
        try:
            result = provider.create_payment(
                amount_minor=amount_minor,
                currency=currency,
                reference=str(attempt.pk),
                customer_phone=customer_phone,
                payload={"order_id": str(order.pk)},
            )
        except ProviderUnavailable as exc:
            # Timeout is not failure: keep pending and flag for reconciliation.
            attempt.note = str(exc)
            attempt.save(update_fields=["note", "updated_at"])
            return attempt
        except ProviderRejected as exc:
            # The provider said no for good. The attempt is kept rather than
            # rolled back, so the refusal is auditable, and it is terminal: no
            # callback can arrive for a request the provider never issued.
            attempt.state = PaymentAttempt.State.FAILED
            attempt.note = str(exc)
            attempt.result_received = True
            attempt.save(
                update_fields=["state", "note", "result_received", "updated_at"]
            )
            record_audit(
                actor=None,
                organization=attempt.organization,
                action="payment.rejected",
                entity_type="payment_attempt",
                entity_id=attempt.pk,
                reason=str(exc)[:255],
            )
            return attempt

        attempt.provider_reference = result["provider_reference"]
        attempt.save(update_fields=["provider_reference", "note", "updated_at"])
        return attempt


@transaction.atomic
def handle_payment_callback(provider_slug, provider_payload, *, actor=None):
    """Normalize and apply a provider callback exactly once per result.

    Atomic: the attempt state transition, any ledger postings, and the audit
    record commit together or not at all (blueprint 17).

    Returns (attempt, applied) where applied is False for duplicate/stale
    callbacks that must not create a second business effect."""
    provider = get_provider(provider_slug)
    try:
        ref, state, amount_minor, currency, payload = provider.parse_callback(provider_payload)
    except ProviderError as exc:
        raise PaymentError(str(exc))

    # Lookup by provider reference, the one field that ties a provider's
    # result back to an attempt. Refusing anything unmatched or ambiguous is
    # what keeps a callback from being applied to an attempt nobody intended.
    try:
        attempt = PaymentAttempt.objects.get(provider=provider_slug, provider_reference=ref)
    except PaymentAttempt.DoesNotExist:
        record_audit(
            actor=actor,
            action="payment.callback_unmatched",
            entity_type="payment_attempt",
            reason="Callback for unknown provider reference.",
            new_value={"provider": provider_slug, "provider_reference": ref},
        )
        raise PaymentError("Unmatched provider callback.")
    except PaymentAttempt.MultipleObjectsReturned:
        # The unique constraint on (provider, provider_reference) makes this
        # unreachable in normal operation. If it is ever reached the data no
        # longer says which collection the provider means, so the callback is
        # refused instead of applied to an arbitrary attempt.
        record_audit(
            actor=actor,
            action="payment.callback_ambiguous",
            entity_type="payment_attempt",
            reason="Provider reference matches more than one attempt.",
            new_value={"provider": provider_slug, "provider_reference": ref},
        )
        raise PaymentError("Provider reference matches more than one attempt.")
    if attempt.is_terminal():
        # Stale or duplicate callback: no second business effect.
        return attempt, False

    # Only a claimed collection can mismatch. A failed callback carries no
    # amount at all -- Daraja omits CallbackMetadata when ResultCode is not
    # zero -- so treating a missing amount as a mismatch would park every
    # declined payment in "unknown" instead of recording it as failed.
    if state == "success" and (
        amount_minor != attempt.expected_amount_minor or currency != attempt.currency
    ):
        attempt.state = PaymentAttempt.State.UNKNOWN
        attempt.provider_payload = payload
        attempt.note = "Amount/currency mismatch between expected and callback."
        attempt.save(update_fields=["state", "provider_payload", "note", "updated_at"])
        record_audit(
            actor=actor,
            organization=attempt.organization,
            action="payment.callback_mismatch",
            entity_type="payment_attempt",
            entity_id=attempt.pk,
            reason=attempt.note,
            previous_value={"expected": attempt.expected_amount_minor, "currency": attempt.currency},
            new_value={"received": amount_minor, "currency": currency},
        )
        return attempt, True

    if state in TERMINAL_CALLBACK_STATES:
        new_state = (
            PaymentAttempt.State.SUCCESS if state == "success" else PaymentAttempt.State.FAILED
        )
        attempt.state = new_state
        attempt.provider_payload = payload
        attempt.result_received = True
        attempt.save(update_fields=["state", "provider_payload", "result_received", "updated_at"])
        if new_state == PaymentAttempt.State.SUCCESS:
            _post_collection(attempt)
            # Money received means the purchase stands. The order leaves
            # "created" for "confirmed" in the same transaction as the ledger
            # postings, so an order is confirmed if and only if it was
            # collected, and a rolled-back callback leaves no confirmed order
            # behind (blueprint 1, 13).
            _confirm_order(attempt.order)
            # Seat availability and payment success are evaluated together
            # before a confirmed ticket is issued (blueprint 7). This runs in
            # the same transaction as the ledger postings: the ticket exists if
            # and only if the collection committed.
            _issue_booking_tickets(attempt)
        record_audit(
            actor=actor,
            organization=attempt.organization,
            action=f"payment.{new_state}",
            entity_type="payment_attempt",
            entity_id=attempt.pk,
            previous_value={"state": "pending"},
            new_value={"state": new_state, "provider_reference": ref},
        )
        return attempt, True

    # Still pending/unknown per provider: record the raw result, do not settle.
    attempt.provider_payload = payload
    attempt.save(update_fields=["provider_payload", "updated_at"])
    return attempt, True


def _confirm_order(order):
    """Move a paid order out of "created" when its collection succeeds.

    Guarded on the current status rather than on the terminal-callback check
    alone, so even a callback path that reached here twice could not confirm a
    second time. An order that needs no payment has no successful collection
    to arrive here on; it is confirmed through its own path, so this does not
    inspect ``is_payment_required``.

    A confirmed order with no ticket is not a contradiction: that is the case
    where the seat hold lapsed, and it is exactly what the refund/rebook
    exception below owes the customer.
    """
    if order is None or order.status != Order.Status.CREATED:
        return
    order.confirm()


def _post_collection(attempt):
    """Standard balanced postings on a successful collection.

    The money is not yet revenue: it is a liability (unearned revenue) until
    the service is delivered, at which point :func:`recognize_revenue` moves it
    across. The account names are the shared chart of accounts in
    :mod:`apps.payments.ledger`."""
    ledger.post_pair(
        journal="collections",
        debit_account=ledger.CASH_COLLECTIONS,
        credit_account=ledger.UNEARNED_REVENUE,
        amount_minor=attempt.expected_amount_minor,
        currency=attempt.currency,
        order=attempt.order,
        payment_attempt=attempt,
        reference=str(attempt.provider_reference or ""),
    )


@transaction.atomic
def recognize_revenue(*, order, actor=None, reference=""):
    """Move an order's collected money from unearned revenue to revenue.

    Called when the performance obligation is satisfied -- the service was
    delivered -- not when the money arrived. Balance-driven and therefore
    idempotent: it moves whatever is still sitting in unearned revenue and,
    once that is empty, a repeat call posts nothing. An order that was never
    collected, or was already refunded, has no unearned balance and is left
    alone.
    """
    if order is None:
        return None
    unearned = ledger.account_balance(order=order, account=ledger.UNEARNED_REVENUE)
    if unearned <= 0:
        return None
    reference = reference or f"recognize:{order.pk}"
    ledger.post_pair(
        journal="recognition",
        debit_account=ledger.UNEARNED_REVENUE,
        credit_account=ledger.SERVICE_REVENUE,
        amount_minor=unearned,
        currency=order.currency,
        order=order,
        reference=reference,
    )
    record_audit(
        actor=actor,
        organization=order.organization,
        action="payment.revenue_recognized",
        entity_type="order",
        entity_id=order.pk,
        new_value={"amount_minor": unearned, "currency": order.currency},
        reason="Service delivered; unearned revenue recognised.",
    )
    record_outbox(
        event_type="payment.revenue_recognized",
        aggregate_type="order",
        aggregate_id=order.pk,
        organization_id=order.organization_id,
        payload={"order_id": str(order.pk), "amount_minor": unearned},
    )
    return unearned


@transaction.atomic
def refund_collection(
    *, order, amount_minor, reference, reason="", actor=None, payment_attempt=None
):
    """Reverse a collection, posting the balanced entries that give it back.

    The refund unwinds whatever value the order still holds: first the part
    still sitting in unearned revenue (the customer cancelled before travelling)
    and, if that is not enough, the rest out of recognised revenue (money
    clawed back after the service was delivered). It refuses to refund more
    than the order still holds, so the ledger can never be driven negative.

    Idempotent on ``reference``: a replayed refund returns the existing row and
    posts nothing further.
    """
    order_obj = order
    if order_obj is None:
        raise PaymentError("A refund must name an order.")
    if amount_minor is None or amount_minor <= 0:
        raise PaymentError("A refund must be for a positive amount.")

    existing = Refund.objects.filter(reference=reference).first()
    if existing is not None:
        if existing.order_id != order_obj.pk or existing.amount_minor != amount_minor:
            raise PaymentError(
                "Refund reference reused for a different order or amount."
            )
        return existing

    unearned = ledger.account_balance(order=order_obj, account=ledger.UNEARNED_REVENUE)
    recognised = ledger.account_balance(order=order_obj, account=ledger.SERVICE_REVENUE)
    available = unearned + recognised
    if amount_minor > available:
        raise PaymentError(
            "A refund cannot exceed what the order still holds "
            f"({available} {order_obj.currency})."
        )

    from_unearned = min(amount_minor, unearned)
    from_revenue = amount_minor - from_unearned
    correlation = uuid.uuid4()
    for amount, account in (
        (from_unearned, ledger.UNEARNED_REVENUE),
        (from_revenue, ledger.SERVICE_REVENUE),
    ):
        if amount <= 0:
            continue
        ledger.post_pair(
            journal="refunds",
            debit_account=account,
            credit_account=ledger.CASH_COLLECTIONS,
            amount_minor=amount,
            currency=order_obj.currency,
            order=order_obj,
            payment_attempt=payment_attempt,
            reference=str(reference),
            correlation_id=correlation,
        )

    refund = Refund.objects.create(
        organization=order_obj.organization,
        order=order_obj,
        payment_attempt=payment_attempt,
        amount_minor=amount_minor,
        currency=order_obj.currency,
        reference=str(reference),
        reason=reason,
        from_unearned_minor=from_unearned,
        from_revenue_minor=from_revenue,
        correlation_id=correlation,
        recorded_by=actor,
    )
    record_audit(
        actor=actor,
        organization=order_obj.organization,
        action="payment.refunded",
        entity_type="refund",
        entity_id=refund.pk,
        reason=reason,
        new_value={
            "order": str(order_obj.pk),
            "amount_minor": amount_minor,
            "from_unearned_minor": from_unearned,
            "from_revenue_minor": from_revenue,
            "reference": reference,
        },
    )
    record_outbox(
        event_type="payment.refunded",
        aggregate_type="refund",
        aggregate_id=refund.pk,
        organization_id=order_obj.organization_id,
        payload={
            "refund_id": str(refund.pk),
            "order_id": str(order_obj.pk),
            "amount_minor": amount_minor,
            "reference": reference,
        },
    )
    return refund


def _issue_booking_tickets(attempt):
    """Turn a paid booking order's held seats into confirmed tickets.

    Runs inside the payment callback's transaction, so the ticket is committed
    exactly when the collection is (blueprint 7). If a hold lapsed before the
    callback settled, no ticket is issued; instead a refund/rebook exception is
    queued on the outbox so the collected funds are never silently kept with no
    seat (blueprint 7: "Expired or reassigned hold -> Rebook or refund
    exception")."""
    from apps.booking.services import issue_ticket_for_order

    order = attempt.order
    if order is None:
        return []

    # A failure here must not cost us the collection: the money has already
    # left the customer's wallet, and the ledger entry saying so has to commit
    # (blueprint 7's rule is that the collection is recorded even when no seat
    # can be honoured, so a refund can be owed against it rather than lost).
    #
    # The booking work therefore runs on a SAVEPOINT of this callback's
    # transaction. A failure inside it -- critically including a database
    # error, which poisons the surrounding transaction -- is rolled back on its
    # own while the outer block stays usable. Without that isolation the audit
    # record below would be the first statement executed against an aborted
    # transaction, would raise "current transaction is aborted", and would take
    # the collection down with it: the precise outcome the original comment
    # promised to prevent.
    try:
        with transaction.atomic():
            tickets, expired_holds = issue_ticket_for_order(order)
    except Exception as exc:
        record_audit(
            organization=attempt.organization,
            action="payment.ticket_issue_failed",
            entity_type="order",
            entity_id=order.pk,
            reason="Seat hold could not be converted to a ticket.",
            new_value={"error": type(exc).__name__, "detail": str(exc)[:200]},
        )
        return []

    for ticket in tickets:
        record_outbox(
            event_type="booking.ticket_issued",
            aggregate_type="ticket",
            aggregate_id=ticket.pk,
            organization_id=attempt.organization_id,
            payload={
                "ticket_id": str(ticket.pk),
                "reference": ticket.reference,
                "departure_id": str(ticket.departure_id),
                "seat_number": ticket.seat_number,
                "order_id": str(order.pk),
            },
        )
        record_audit(
            organization=attempt.organization,
            action="booking.ticket_issued",
            entity_type="ticket",
            entity_id=ticket.pk,
            new_value={"reference": ticket.reference, "seat_number": ticket.seat_number},
        )

    for hold in expired_holds:
        record_outbox(
            event_type="booking.rebook_or_refund_required",
            aggregate_type="seat_hold",
            aggregate_id=hold.pk,
            organization_id=attempt.organization_id,
            payload={
                "hold_id": str(hold.pk),
                "order_id": str(order.pk),
                "reason": "Seat hold expired before payment settled.",
            },
        )
        record_audit(
            organization=attempt.organization,
            action="booking.hold_expired_after_payment",
            entity_type="seat_hold",
            entity_id=hold.pk,
            reason="Seat hold expired before payment settled; refund/rebook required.",
        )
    return tickets


@transaction.atomic
def record_cash_collection(
    *,
    order,
    actor,
    amount_minor=None,
    currency=None,
    idempotency_key=None,
    note="",
):
    """Settle an order paid in cash at a counter.

    Cash is not a provider: there is no external reference to wait for, so the
    collection is recorded and applied in one transaction. The amount must
    match the order exactly, and the order must still be awaiting payment --
    otherwise a second counter clerk could collect twice for one order. A
    repeated idempotency key returns the first attempt unchanged.
    """
    if order is None:
        raise PaymentError("Order not found.")
    amount = order.total_minor if amount_minor is None else amount_minor
    unit = currency or order.currency
    if amount != order.total_minor or unit != order.currency:
        raise PaymentError(
            f"Cash amount must match the order total "
            f"({order.total_minor} {order.currency})."
        )

    key = idempotency_key or uuid.uuid4()
    existing = PaymentAttempt.objects.filter(provider="cash", idempotency_key=key).first()
    if existing is not None:
        if existing.order_id != order.pk:
            raise PaymentError("Idempotency key belongs to a different order.")
        return existing, False

    if order.status != Order.Status.CREATED:
        raise PaymentError("This order is already paid or closed.")

    attempt = PaymentAttempt.objects.create(
        organization=order.organization,
        branch=order.branch,
        order=order,
        provider="cash",
        idempotency_key=key,
        expected_amount_minor=amount,
        currency=unit,
        provider_reference=f"CASH-{uuid.uuid4().hex[:12].upper()}",
        state=PaymentAttempt.State.SUCCESS,
        result_received=True,
        note=note,
    )
    _post_collection(attempt)
    _confirm_order(order)
    _issue_booking_tickets(attempt)
    record_audit(
        actor=actor,
        organization=attempt.organization,
        action="payment.cash_recorded",
        entity_type="payment_attempt",
        entity_id=attempt.pk,
        new_value={
            "order": str(order.pk),
            "amount_minor": amount,
            "currency": unit,
            "reference": attempt.provider_reference,
        },
    )
    return attempt, True
