"""Money helpers. All stored amounts are exact integer minor units
(KES cents) to avoid floating point drift (blueprint 17)."""

from decimal import Decimal

CURRENCY_EXPONENT = {
    "KES": 2,
    "UGX": 0,
    "TZS": 0,
    "USD": 2,
    "EUR": 2,
}


class MoneyError(ValueError):
    pass


def to_minor(amount: Decimal, currency: str = "KES") -> int:
    """Convert a major-unit Decimal to exact minor units."""
    exponent = CURRENCY_EXPONENT.get(currency.upper(), 2)
    scaled = amount.scaleb(exponent)
    if scaled != scaled.to_integral_value():
        raise MoneyError(f"Amount {amount} has more precision than {currency} supports.")
    return int(scaled)


def from_minor(amount_minor: int, currency: str = "KES") -> Decimal:
    exponent = CURRENCY_EXPONENT.get(currency.upper(), 2)
    return (Decimal(amount_minor) / (Decimal(10) ** exponent)).normalize()