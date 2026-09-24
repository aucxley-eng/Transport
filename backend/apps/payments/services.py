"""Payment orchestration: idempotent attempts, normalized callbacks, and the
double-entry postings that keep collections auditable (blueprint 13)."""

import uuid

from django.db import transaction

from apps.audit.models import record_audit
from apps.orders.models import Order
from apps.payments.models import LedgerEntry, PaymentAttempt
from apps.payments.provider import (
    ProviderError,
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

    attempts = PaymentAttempt.objects.filter(provider=provider_slug, provider_reference=ref)
    if not attempts.exists():
        record_audit(
            actor=actor,
            action="payment.callback_unmatched",
            entity_type="payment_attempt",
            reason="Callback for unknown provider reference.",
            new_value={"provider": provider_slug, "provider_reference": ref},
        )
        raise PaymentError("Unmatched provider callback.")

    attempt = attempts.first()
    if attempt.is_terminal():
        # Stale or duplicate callback: no second business effect.
        return attempt, False

    if amount_minor != attempt.expected_amount_minor or currency != attempt.currency:
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


def _post_collection(attempt):
    """Standard balanced postings on a successful collection. Account mapping
    is provisional; the accountant approves final principal/agent mapping
    (blueprint 13, 17)."""
    correlation = uuid.uuid4()
    debit_account = "cash.customer_collections"
    credit_account = "liability.unearned_revenue"
    LedgerEntry.objects.create(
        journal="collections",
        account=debit_account,
        amount_minor=attempt.expected_amount_minor,
        currency=attempt.currency,
        entry_type="debit",
        order=attempt.order,
        payment_attempt=attempt,
        reference=str(attempt.provider_reference or ""),
        correlation_id=correlation,
    )
    LedgerEntry.objects.create(
        journal="collections",
        account=credit_account,
        amount_minor=attempt.expected_amount_minor,
        currency=attempt.currency,
        entry_type="credit",
        order=attempt.order,
        payment_attempt=attempt,
        reference=str(attempt.provider_reference or ""),
        correlation_id=correlation,
    )