"""Payment provider adapters (blueprint 13, 18).

Every external provider sits behind a small adapter so provider changes and
test stubs do not touch business logic. Adapters translate internal request/
response into provider format and back. Production capabilities depend on
approved onboarding (M-PESA STK push, business payments, refunds).

Two M-PESA adapters are available and the environment chooses between them:

* :class:`MpesaProvider` -- the local stub. It never opens a socket, invents a
  reference, and answers a callback from its own memory. This is the default
  whenever credentials are absent, which is what keeps ``manage.py test``
  reproducible on a machine with no network and no Safaricom account.
* :class:`DarajaProvider` -- the real Safaricom Daraja adapter, selected only
  when the five required credentials are present (``MPESA_ENVIRONMENT`` is
  optional and defaults to sandbox). Half a credential set leaves the provider
  on the stub rather than starting requests that cannot succeed.
"""

import abc
import base64
import os
import re
import time
from datetime import datetime, timedelta, timezone

import requests

# Kenya keeps UTC+3 all year, so this needs no tz database to be correct.
EAT = timezone(timedelta(hours=3))


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
        """Initiate a payment. Return a dict carrying at least ``provider_reference``.
        Raises ProviderUnavailable or ProviderRejected."""

    @abc.abstractmethod
    def query_payment(self, provider_reference):
        """Return the current provider state normalized to one of
        success / failed / pending / unknown."""

    @abc.abstractmethod
    def parse_callback(self, provider_payload):
        """Normalize a provider webhook into:
        (provider_reference, state, amount_minor, currency, provider_payload)."""


def daraja_credentials():
    """The Daraja credential set, or None if it is not complete.

    All-or-nothing on purpose: a partly configured set would move the provider
    off the local stub and start issuing requests that cannot succeed, which is
    a worse place to land than never having been configured at all.
    """
    read = {name: os.environ.get(name, "").strip() for name in (
        "MPESA_ENVIRONMENT",
        "MPESA_CONSUMER_KEY",
        "MPESA_CONSUMER_SECRET",
        "MPESA_SHORTCODE",
        "MPESA_PASSKEY",
        "MPESA_CALLBACK_URL",
    )}
    required = (
        "MPESA_CONSUMER_KEY",
        "MPESA_CONSUMER_SECRET",
        "MPESA_SHORTCODE",
        "MPESA_PASSKEY",
        "MPESA_CALLBACK_URL",
    )
    if not all(read[name] for name in required):
        return None
    return read


def stk_timestamp(moment=None):
    """Daraja's Timestamp field: YYYYMMDDHHmmss in East Africa Time.

    A naive datetime is taken to already be in East Africa Time. Calling
    ``astimezone`` on one would instead read it as local time and silently
    shift the password by the machine's UTC offset, which Safaricom rejects.
    """
    when = moment or datetime.now(EAT)
    if when.tzinfo is None:
        when = when.replace(tzinfo=EAT)
    return when.astimezone(EAT).strftime("%Y%m%d%H%M%S")


def normalise_msisdn(phone):
    """A dialable ``254...`` subscriber number, or "" if it is not one.

    Daraja rejects anything else outright, so this is the difference between a
    request the provider understood and a 400 back to our caller.
    """
    digits = re.sub(r"\D", "", phone or "")
    if digits.startswith("254") and len(digits) == 12:
        return digits
    if digits.startswith("0") and len(digits) == 10:
        return "254" + digits[1:]
    if digits.startswith("7") and len(digits) == 9:
        return "254" + digits
    return ""


def minor_units(amount):
    """Convert a Daraja amount (whole or fractional shillings) to minor units.

    JSON hands back ``1.00`` as a float, and ``int(1.00 * 100)`` is not the
    problem -- ``0.29 * 100`` is ``28.999999999999996``. Rounding through
    decimal-free integer arithmetic on the string form keeps a KES 0.29 parcel
    from being collected as 28 cents.
    """
    text = str(amount).strip()
    if "." in text:
        whole, _, fraction = text.partition(".")
        minor = int(whole or "0") * 100 + int((fraction + "00")[:2])
    else:
        minor = int(text or "0") * 100
    return minor


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


class DarajaProvider(PaymentProvider):
    """The real Safaricom Daraja adapter: OAuth, STK Push, and STK query.

    Host is chosen from ``MPESA_ENVIRONMENT``; every call carries a timeout and
    maps transport and 5xx failures to :class:`ProviderUnavailable`, because a
    request we never heard the answer to is not proof that money did not move
    (blueprint 13: "a timeout does not prove failure").

    ``provider_reference`` is Daraja's ``CheckoutRequestID``. That is the only
    value the provider both returns to us synchronously and repeats in the
    callback, so it is the one key that can join the two sides.
    """

    name = "mpesa"
    SANDBOX_BASE = "https://sandbox.safaricom.co.ke"
    LIVE_BASE = "https://api.safaricom.co.ke"
    TOKEN_LIFETIME = 3599
    # Refresh a little early: a token that expires mid-request reads as a 401
    # and would be reported as bad credentials rather than as a retry.
    TOKEN_REFRESH_MARGIN = 60

    def __init__(
        self,
        *,
        environment,
        consumer_key,
        consumer_secret,
        shortcode,
        passkey,
        callback_url,
        session=None,
        timeout=30,
    ):
        self.base_url = (
            self.SANDBOX_BASE if environment.lower() != "production" else self.LIVE_BASE
        )
        self.consumer_key = consumer_key
        self.consumer_secret = consumer_secret
        self.shortcode = shortcode
        self.passkey = passkey
        self.callback_url = callback_url
        self.timeout = timeout
        self._session = session or requests.Session()
        self._token = None
        self._token_expires_at = 0.0

    # -- transport ---------------------------------------------------------

    def _request(self, method, path, *, json_body=None, auth=None, params=None):
        url = f"{self.base_url}{path}"
        try:
            response = self._session.request(
                method,
                url,
                json=json_body,
                params=params,
                auth=auth,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise ProviderUnavailable(f"Daraja is unreachable: {exc}") from exc

        if response.status_code >= 500:
            raise ProviderUnavailable(f"Daraja returned HTTP {response.status_code}.")
        if response.status_code in (401, 403):
            # Bad or expired credentials. Not a rejection of this payment: the
            # money question was never asked.
            raise ProviderUnavailable(
                f"Daraja refused authentication (HTTP {response.status_code}); "
                "check the MPESA_* credentials."
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderUnavailable(
                f"Daraja returned HTTP {response.status_code} with a non-JSON body."
            ) from exc
        if response.status_code >= 400:
            message = body.get("errorMessage") or body.get("RequestID") or body
            raise ProviderRejected(f"Daraja rejected the request: {message}")
        return body

    def _access_token(self):
        now = time.monotonic()
        if self._token and now < self._token_expires_at:
            return self._token
        body = self._request(
            "GET",
            "/oauth/v1/generate",
            params={"grant_type": "client_credentials"},
            auth=(self.consumer_key, self.consumer_secret),
        )
        token = body.get("access_token")
        if not token:
            raise ProviderUnavailable("Daraja returned no access token.")
        expires_in = int(body.get("expires_in", self.TOKEN_LIFETIME))
        self._token = token
        self._token_expires_at = now + max(
            0, expires_in - self.TOKEN_REFRESH_MARGIN
        )
        return token

    def _stk_credentials(self, timestamp):
        raw = f"{self.shortcode}{self.passkey}{timestamp}".encode()
        return base64.b64encode(raw).decode()

    # -- PaymentProvider ---------------------------------------------------

    def create_payment(self, *, amount_minor, currency, reference, customer_phone, payload):
        if currency.upper() != "KES":
            raise ProviderRejected("M-PESA collects in KES only.")
        # STK Push takes whole shillings. Dividing a fractional amount would
        # silently take less than the order total, and the collection would
        # never match, so refuse it where the customer can still be told.
        if amount_minor % 100:
            raise ProviderRejected(
                "M-PESA STK Push collects whole shillings; this order total "
                "is not a whole-shilling amount."
            )
        msisdn = normalise_msisdn(customer_phone)
        if not msisdn:
            raise ProviderRejected(
                "M-PESA needs a Kenyan subscriber number (07..., 7... or 254...)."
            )

        timestamp = stk_timestamp()
        body = {
            "BusinessShortcode": self.shortcode,
            "Password": self._stk_credentials(timestamp),
            "Timestamp": timestamp,
            "TransactionType": "CustomerPayBillOnline",
            "Amount": amount_minor // 100,
            "PartyA": msisdn,
            "PartyB": self.shortcode,
            "PhoneNumber": msisdn,
            "CallBackURL": self.callback_url,
            "AccountReference": str(reference)[:12],
            "TransactionDesc": "Transport payment",
        }
        result = self._request(
            "POST",
            "/mpesa/stkpush/v1/processrequest",
            json_body=body,
            auth=f"Bearer {self._access_token()}",
        )
        if str(result.get("ResponseCode", "")) != "0":
            raise ProviderRejected(
                result.get("ResponseDescription") or "Daraja refused the STK push."
            )
        checkout_id = result.get("CheckoutRequestID")
        if not checkout_id:
            raise ProviderUnavailable("Daraja accepted the push without a CheckoutRequestID.")
        return {
            "provider_reference": checkout_id,
            "sandbox": False,
            "customer_message": result.get("CustomerMessage", ""),
            "merchant_request_id": result.get("MerchantRequestID", ""),
        }

    def query_payment(self, provider_reference):
        timestamp = stk_timestamp()
        body = {
            "BusinessShortcode": self.shortcode,
            "Password": self._stk_credentials(timestamp),
            "Timestamp": timestamp,
            "CheckoutRequestID": provider_reference,
        }
        result = self._request(
            "POST",
            "/mpesa/stkpushquery/v1/query",
            json_body=body,
            auth=f"Bearer {self._access_token()}",
        )
        if "errorCode" in result:
            # The provider could not answer. That says nothing about the money,
            # so it is unknown rather than failed.
            return "unknown"
        # Once Daraja reports a ResultCode the request has concluded: either
        # the subscriber paid, or they cancelled, timed out or were refused.
        return "success" if str(result.get("ResultCode")) == "0" else "failed"

    def parse_callback(self, provider_payload):
        stk = (provider_payload.get("Body") or {}).get("stkCallback")
        if stk is None:
            # The local sandbox shape, kept so the suite needs no network.
            ref = provider_payload.get("provider_reference")
            if not ref:
                raise ProviderError("Callback is missing provider_reference.")
            return (
                ref,
                provider_payload.get("status", "unknown"),
                provider_provider_amount(provider_payload),
                provider_payload.get("currency", "KES"),
                provider_payload,
            )

        checkout_id = stk.get("CheckoutRequestID")
        if not checkout_id:
            raise ProviderError("Callback is missing CheckoutRequestID.")
        result_code = stk.get("ResultCode")
        state = "success" if str(result_code) == "0" else "failed"
        items = {
            item.get("Name"): item.get("Value")
            for item in ((stk.get("CallbackMetadata") or {}).get("Item") or [])
        }
        amount_minor = minor_units(items["Amount"]) if "Amount" in items else 0

        # Only what the provider actually sent, flattened so the rest of the
        # system keeps reading a plain dict. The receipt is what an operator
        # reconciles against, so it is kept alongside the raw result.
        normalized = {
            "provider_reference": checkout_id,
            "result_code": result_code,
            "result_desc": stk.get("ResultDesc", ""),
            "amount_minor": amount_minor,
            "mpesa_receipt_number": items.get("MpesaReceiptNumber"),
            "phone": items.get("PhoneNumber"),
            "transaction_date": items.get("TransactionDate"),
            "merchant_request_id": stk.get("MerchantRequestID"),
        }
        return checkout_id, state, amount_minor, "KES", normalized


def provider_provider_amount(provider_payload):
    """Minor units from the sandbox callback, which already speaks in them."""
    return provider_payload.get("amount_minor", 0)


_PROVIDERS = {}


def get_provider(slug, **kwargs):
    """Return a cached provider instance, creating it on first use.

    Credentials are read here rather than at import time so a test can change
    the environment and clear ``_PROVIDERS`` to exercise either adapter.
    """
    if slug not in _PROVIDERS:
        if slug != "mpesa":
            provider = None
        else:
            credentials = daraja_credentials()
            provider = (
                DarajaProvider(
                    environment=credentials["MPESA_ENVIRONMENT"] or "sandbox",
                    consumer_key=credentials["MPESA_CONSUMER_KEY"],
                    consumer_secret=credentials["MPESA_CONSUMER_SECRET"],
                    shortcode=credentials["MPESA_SHORTCODE"],
                    passkey=credentials["MPESA_PASSKEY"],
                    callback_url=credentials["MPESA_CALLBACK_URL"],
                    **kwargs,
                )
                if credentials
                else MpesaProvider(**kwargs)
            )
        if provider is None:
            raise ProviderError(f"Unknown payment provider: {slug}")
        _PROVIDERS[slug] = provider
    return _PROVIDERS[slug]
