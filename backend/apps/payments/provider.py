"""Payment provider adapters (blueprint 13, 18).

Every external provider sits behind a small adapter so provider changes and
test stubs do not touch business logic. Adapters translate internal request/
response into provider format and back. Production capabilities depend on
approved onboarding (M-PESA STK push, business payments, refunds).
"""

import abc


class ProviderError(Exception):
    pass


class ProviderUnavailable(ProviderError):
    """Timeout or temporary connectivity failure. NOT proof of payment failure."""


class ProviderRejected(ProviderError):
    """Provider definitively rejected the payment request."""


class PaymentProvider(abc.ABC):
    name = "base"

    @abc.abstractmethod
    def create_payment(self, *, amount_minor, currency, reference, customer_phone, payload):
        """Initiate a payment. Return a dict carrying at least
        ``provider_reference``. Raises ProviderUnavailable or ProviderRejected."""

    @abc.abstractmethod
    def query_payment(self, provider_reference):
        """Return the current provider state normalized to one of
        success / failed / pending / unknown."""

    @abc.abstractmethod
    def parse_callback(self, provider_payload):
        """Normalize a provider webhook into:
        (provider_reference, state, amount_minor, currency, provider_payload)."""


class MpesaProvider(PaymentProvider):
    """Safaricom M-PESA adapter. Sandbox mode keeps local records so the full
    payment lifecycle can be tested without live money (blueprint 25). Live
    integration requires approved products and credentials."""

    name = "mpesa"

    def __init__(self, *, sandbox=True):
        self.sandbox = sandbox
        self._sandbox_ledger = {}

    def create_payment(self, *, amount_minor, currency, reference, customer_phone, payload):
        if not self.sandbox:
            raise ProviderUnavailable(
                "M-PESA live integration is not enabled yet: onboarding and "
                "provider credentials are required."
            )
        provider_reference = f"MP{reference.replace('-', '').upper()[:24]}"
        self._sandbox_ledger[provider_reference] = {
            "amount_minor": amount_minor,
            "currency": currency,
            "state": "pending",
        }
        return {"provider_reference": provider_reference, "sandbox": True}

    def query_payment(self, provider_reference):
        record = self._sandbox_ledger.get(provider_reference, {})
        return record.get("state", "unknown")

    def parse_callback(self, provider_payload):
        ref = provider_payload.get("provider_reference")
        if not ref:
            raise ProviderError("Callback is missing provider_reference.")
        state = provider_payload.get("status", "unknown")  # sandbox sends status directly
        amount_minor = provider_payload.get("amount_minor", 0)
        currency = provider_payload.get("currency", "KES")
        return ref, state, amount_minor, currency, provider_payload

    def simulate_result(self, provider_reference, state):
        """Testing hook to drive a sandbox payment to a terminal state."""
        if provider_reference in self._sandbox_ledger:
            self._sandbox_ledger[provider_reference]["state"] = state


_PROVIDERS = {}


def get_provider(slug, **kwargs):
    """Return a cached provider instance, creating it on first use."""
    if slug not in _PROVIDERS:
        provider = MpesaProvider(**kwargs) if slug == "mpesa" else None
        if provider is None:
            raise ProviderError(f"Unknown payment provider: {slug}")
        _PROVIDERS[slug] = provider
    return _PROVIDERS[slug]