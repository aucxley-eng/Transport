"""Chart of accounts and balanced posting helpers (blueprint 13, 17).

Every financial effect is a pair of :class:`LedgerEntry` rows that share a
``correlation_id``: one debit and one credit of equal amount. Nothing here
updates or deletes an entry -- a correction is another pair. The account names
live in one place so a mapping change is a single edit rather than a search for
string literals; the accountant approves the final principal/agent mapping (the
names below are the provisional set the review recorded).
"""

import uuid

from django.db.models import Q, Sum

from apps.payments.models import LedgerEntry

# Cash the customer has paid: an asset, increased by debits.
CASH_COLLECTIONS = "cash.customer_collections"
# Money held before the service is delivered: a liability, increased by credits.
UNEARNED_REVENUE = "liability.unearned_revenue"
# Money earned once the service is delivered: revenue, increased by credits.
SERVICE_REVENUE = "revenue.service"

# Accounts whose normal balance is a debit. Everything else (liabilities,
# revenue) is credit-normal.
_DEBIT_NORMAL_ACCOUNTS = frozenset({CASH_COLLECTIONS})


def post_pair(
    *,
    journal,
    debit_account,
    credit_account,
    amount_minor,
    currency="KES",
    order=None,
    payment_attempt=None,
    reference="",
    correlation_id=None,
):
    """Append one balanced debit/credit pair.

    The single place money movements are written, so every effect is balanced
    by construction and every pair is linked by a shared ``correlation_id``.
    Refuses a non-positive amount: a zero or negative posting is a caller bug,
    not a correction (corrections are their own positive pair).
    """
    if amount_minor is None or amount_minor <= 0:
        raise ValueError("A ledger posting must be for a positive amount.")
    correlation_id = correlation_id or uuid.uuid4()
    shared = {
        "journal": journal,
        "amount_minor": amount_minor,
        "currency": currency,
        "order": order,
        "payment_attempt": payment_attempt,
        "reference": reference,
        "correlation_id": correlation_id,
    }
    return LedgerEntry.objects.bulk_create(
        [
            LedgerEntry(account=debit_account, entry_type="debit", **shared),
            LedgerEntry(account=credit_account, entry_type="credit", **shared),
        ]
    )


def account_balance(*, order, account, payment_attempt=None):
    """The balance of one account for an order, in accounting sign.

    A positive result means the account carries its normal side: cash held for
    an asset, revenue earned for revenue, and an amount still owed to the
    customer for unearned revenue. Zero when nothing has been posted.

    Reading a balance instead of a running total is what makes recognition and
    refund idempotent: once the money has moved, the account it left is empty,
    so a repeated call posts nothing.
    """
    rows = LedgerEntry.objects.filter(order=order, account=account)
    if payment_attempt is not None:
        rows = rows.filter(payment_attempt=payment_attempt)
    totals = rows.aggregate(
        debit=Sum("amount_minor", filter=Q(entry_type="debit")),
        credit=Sum("amount_minor", filter=Q(entry_type="credit")),
    )
    debit = totals["debit"] or 0
    credit = totals["credit"] or 0
    if account in _DEBIT_NORMAL_ACCOUNTS:
        return debit - credit
    return credit - debit
