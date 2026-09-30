"""Tariff resolution: which rate card prices a given account's work.

Catalog owns `RateCard`, so catalog answers the question. A business account
that sends fifty parcels a day gets a rate card of its own and every later quote
uses it without a code change; everything else falls back to the platform card.
This is the first place in the repository that reads a rate card rather than
merely storing one.
"""

from datetime import date

from django.db.models import Q

from .models import RateCard, RateLine

ZERO = 0


def resolve_rate_card(organization, service, on=None):
    """The card that prices this account's work for this service.

    The account's own active card wins; the platform card is the fallback. The
    newest effective card wins within each, so a re-issued tariff takes over
    without the old one being deleted. `None` means neither exists, which the
    caller must report as a refusal rather than price at zero.
    """
    when = on or date.today()

    def _newest(cards):
        return cards.order_by("-effective_from", "-version").first()

    def _in_force(cards):
        return cards.filter(
            is_active=True,
            effective_from__lte=when,
        ).filter(Q(effective_to__isnull=True) | Q(effective_to__gte=when))

    own = _in_force(RateCard.objects.filter(organization=organization, service=service))
    if (card := _newest(own)) is not None:
        return card
    return _newest(_in_force(RateCard.objects.filter(organization__isnull=True, service=service)))


def weight_bands(rate_card):
    """The weight bands on a card, lowest first.

    A band is a `RateLine` of type `per_kg` whose `from_value`/`to_value` bracket
    a chargeable weight, priced at a flat `amount_minor`. That is how courier
    tariffs read — "up to 1 kg: 300, 1-5 kg: 600" — and the existing band
    columns express it without a new model. The lowest band doubles as the
    minimum charge.
    """
    lines = rate_card.lines.filter(line_type=RateLine.LineType.PER_KG).order_by(
        "from_value", "id"
    )
    return [
        (line.from_value, line.to_value, line.amount_minor, line.label)
        for line in lines
    ]


def band_for(bands, chargeable_kg):
    """The band that covers this weight, or `None`.

    A parcel that falls outside every band is refused by the caller rather than
    priced at the nearest band: guessing a price is how a courier company loses
    money quietly.
    """
    for from_value, to_value, amount_minor, label in bands:
        if from_value is not None and chargeable_kg < from_value:
            continue
        if to_value is not None and chargeable_kg > to_value:
            continue
        return {"from_value": from_value, "to_value": to_value,
                "amount_minor": amount_minor, "label": label}
    return None
