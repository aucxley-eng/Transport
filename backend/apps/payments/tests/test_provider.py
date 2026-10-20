"""Daraja adapter tests (blueprint 13, 18).

Everything here runs offline. The adapter is driven through an injected
session, and every payload transcribed from the Daraja contract is the shape
Safaricom actually posts -- the point is that the parsing and the request
building are correct, not that a test reaches Kenya.
"""

import base64
import re
from unittest import mock

import requests
from django.db import DatabaseError
from rest_framework.test import APIClient, APITestCase

from apps.accounts.models import Membership, Organization, Role, User
from apps.audit.models import AuditEvent
from apps.catalog.models import Service, build_quote
from apps.orders.models import Order
from apps.payments.models import LedgerEntry, PaymentAttempt
from apps.payments.provider import (
    MpesaProvider,
    ProviderRejected,
    ProviderUnavailable,
    DarajaProvider,
    daraja_credentials,
    get_provider,
    minor_units,
    normalise_msisdn,
    stk_timestamp,
)
from apps.payments.services import create_payment_attempt, handle_payment_callback

CREDENTIALS = {
    "MPESA_ENVIRONMENT": "sandbox",
    "MPESA_CONSUMER_KEY": "consumer-key",
    "MPESA_CONSUMER_SECRET": "consumer-secret",
    "MPESA_SHORTCODE": "174379",
    "MPESA_PASSKEY": "passkey",
    "MPESA_CALLBACK_URL": "https://example.test/api/v1/webhooks/mpesa/",
}


class FakeResponse:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self.body = body if body is not None else {}

    def json(self):
        if self.body is None:
            raise ValueError("not json")
        return self.body


class RecordingSession:
    """Stands in for requests.Session: records calls, answers from a queue."""

    def __init__(self, *responses):
        self.calls = []
        self.responses = list(responses)

    def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        if not self.responses:
            raise requests.ConnectionError("no network in tests")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def daraja(session=None, **overrides):
    settings = dict(CREDENTIALS)
    settings.update(overrides)
    return DarajaProvider(
        environment=settings["MPESA_ENVIRONMENT"],
        consumer_key=settings["MPESA_CONSUMER_KEY"],
        consumer_secret=settings["MPESA_CONSUMER_SECRET"],
        shortcode=settings["MPESA_SHORTCODE"],
        passkey=settings["MPESA_PASSKEY"],
        callback_url=settings["MPESA_CALLBACK_URL"],
        session=session or RecordingSession(),
    )


# A success callback exactly as Safaricom posts it, including the float amount
# and the nested metadata list. ResultCode 0 means the subscriber paid.
DARAJA_SUCCESS = {
    "Body": {
        "stkCallback": {
            "MerchantRequestID": "29115-34620561-1",
            "CheckoutRequestID": "ws_CO_191220191020363925",
            "ResultCode": 0,
            "ResultDesc": "The service request is processed successfully.",
            "CallbackMetadata": {
                "Item": [
                    {"Name": "Amount", "Value": 300.0},
                    {"Name": "MpesaReceiptNumber", "Value": "NLJ7RT61SV"},
                    {"Name": "TransactionDate", "Value": 20191219102115},
                    {"Name": "PhoneNumber", "Value": 254708374149},
                ]
            },
        }
    }
}

# A declined callback: no CallbackMetadata at all, because no money moved.
DARAJA_DECLINED = {
    "Body": {
        "stkCallback": {
            "MerchantRequestID": "29115-34620561-2",
            "CheckoutRequestID": "ws_CO_191220191020363926",
            "ResultCode": 1032,
            "ResultDesc": "Request cancelled by user",
        }
    }
}


class CredentialSelectionTestCase(APITestCase):
    """The environment, and only the environment, picks the adapter."""

    def setUp(self):
        from apps.payments import provider

        provider._PROVIDERS.clear()

    def tearDown(self):
        from apps.payments import provider

        provider._PROVIDERS.clear()

    def _without_credentials(self):
        empty = {name: "" for name in CREDENTIALS}
        return mock.patch.dict("os.environ", empty, clear=False)

    def test_no_credentials_leaves_the_provider_on_the_local_stub(self):
        with self._without_credentials():
            self.assertIsNone(daraja_credentials())
            provider = get_provider("mpesa")
        self.assertIsInstance(provider, MpesaProvider)

    def test_a_complete_credential_set_selects_the_live_adapter(self):
        with mock.patch.dict("os.environ", CREDENTIALS, clear=False):
            self.assertIsNotNone(daraja_credentials())
            provider = get_provider("mpesa")
        self.assertIsInstance(provider, DarajaProvider)
        self.assertEqual(provider.base_url, DarajaProvider.SANDBOX_BASE)

    def test_production_environment_switches_the_host(self):
        with mock.patch.dict(
            "os.environ", dict(CREDENTIALS, MPESA_ENVIRONMENT="production")
        ):
            provider = get_provider("mpesa")
        self.assertEqual(provider.base_url, DarajaProvider.LIVE_BASE)

    def test_a_partly_configured_set_is_treated_as_unconfigured(self):
        # One missing field must not move us off the stub onto requests that
        # are bound to fail. MPESA_ENVIRONMENT is deliberately not required:
        # an unset environment means sandbox.
        for missing in (
            "MPESA_CONSUMER_KEY",
            "MPESA_CONSUMER_SECRET",
            "MPESA_SHORTCODE",
            "MPESA_PASSKEY",
            "MPESA_CALLBACK_URL",
        ):
            incomplete = {k: v for k, v in CREDENTIALS.items() if k != missing}
            with mock.patch.dict(
                "os.environ", {**incomplete, missing: ""}, clear=False
            ):
                self.assertIsNone(daraja_credentials(), missing)

    def test_an_unset_environment_defaults_to_sandbox(self):
        with mock.patch.dict(
            "os.environ", dict(CREDENTIALS, MPESA_ENVIRONMENT=""), clear=False
        ):
            provider = get_provider("mpesa")
        self.assertEqual(provider.base_url, DarajaProvider.SANDBOX_BASE)

    def test_the_suite_default_is_still_the_stub(self):
        # Guards the offline guarantee: with no MPESA_* set at all, nothing in
        # the suite can reach the network.
        with self._without_credentials():
            self.assertIsNone(daraja_credentials())


class RequestBuildingTestCase(APITestCase):
    """What the adapter sends to Daraja, before any money is involved."""

    def _session(self, *responses):
        return RecordingSession(*responses)

    def test_an_stk_push_carries_the_fields_daraja_requires(self):
        session = self._session(
            FakeResponse(body={"access_token": "token", "expires_in": 3599}),
            FakeResponse(
                body={
                    "ResponseCode": "0",
                    "ResponseDescription": "Success",
                    "MerchantRequestID": "m-1",
                    "CheckoutRequestID": "ws_CO_1",
                    "CustomerMessage": "Enter your PIN",
                }
            ),
        )
        provider = daraja(session)
        result = provider.create_payment(
            amount_minor=30000,
            currency="KES",
            reference="attempt-1",
            customer_phone="0722000099",
            payload={},
        )

        self.assertEqual(result["provider_reference"], "ws_CO_1")
        oauth, stk = session.calls
        self.assertEqual(oauth["method"], "GET")
        self.assertEqual(oauth["params"], {"grant_type": "client_credentials"})
        self.assertEqual(oauth["auth"], ("consumer-key", "consumer-secret"))

        body = stk["json"]
        self.assertEqual(stk["url"], f"{DarajaProvider.SANDBOX_BASE}/mpesa/stkpush/v1/processrequest")
        self.assertEqual(body["BusinessShortcode"], "174379")
        self.assertEqual(body["Amount"], 300)  # whole shillings, not minor
        self.assertEqual(body["PartyA"], "254722000099")
        self.assertEqual(body["PhoneNumber"], "254722000099")
        self.assertEqual(body["PartyB"], "174379")
        self.assertEqual(body["TransactionType"], "CustomerPayBillOnline")
        self.assertEqual(body["CallBackURL"], CREDENTIALS["MPESA_CALLBACK_URL"])
        self.assertEqual(body["AccountReference"], "attempt-1")
        self.assertRegex(body["Timestamp"], r"^\d{14}$")
        self.assertEqual(
            stk["auth"], "Bearer token", "the push must carry the OAuth token"
        )

    def test_the_password_is_base64_of_shortcode_passkey_and_timestamp(self):
        session = self._session(
            FakeResponse(body={"access_token": "token"}),
            FakeResponse(body={"ResponseCode": "0", "CheckoutRequestID": "ws_CO_2"}),
        )
        provider = daraja(session)
        provider.create_payment(
            amount_minor=10000,
            currency="KES",
            reference="attempt-2",
            customer_phone="0722000099",
            payload={},
        )
        body = session.calls[1]["json"]
        expected = base64.b64encode(
            f"174379passkey{body['Timestamp']}".encode()
        ).decode()
        self.assertEqual(body["Password"], expected)

    def test_a_fractional_shilling_amount_is_refused_not_silently_truncated(self):
        provider = daraja(self._session())
        with self.assertRaises(ProviderRejected):
            provider.create_payment(
                amount_minor=30050,
                currency="KES",
                reference="attempt-3",
                customer_phone="0722000099",
                payload={},
            )

    def test_a_non_kenyan_amount_is_refused(self):
        provider = daraja(self._session())
        with self.assertRaises(ProviderRejected):
            provider.create_payment(
                amount_minor=30000,
                currency="USD",
                reference="attempt-4",
                customer_phone="0722000099",
                payload={},
            )

    def test_a_number_that_is_not_a_subscriber_number_is_refused(self):
        provider = daraja(self._session())
        with self.assertRaises(ProviderRejected):
            provider.create_payment(
                amount_minor=30000,
                currency="KES",
                reference="attempt-5",
                customer_phone="12345",
                payload={},
            )

    def test_a_transport_failure_reads_as_unavailable_and_not_as_a_refusal(self):
        session = self._session(
            FakeResponse(body={"access_token": "token"}),
            requests.ConnectionError("name or service not known"),
        )
        provider = daraja(session)
        with self.assertRaises(ProviderUnavailable):
            provider.create_payment(
                amount_minor=30000,
                currency="KES",
                reference="attempt-6",
                customer_phone="0722000099",
                payload={},
            )

    def test_a_server_error_reads_as_unavailable(self):
        session = self._session(
            FakeResponse(body={"access_token": "token"}),
            FakeResponse(status_code=503, body=None),
        )
        provider = daraja(session)
        with self.assertRaises(ProviderUnavailable):
            provider.create_payment(
                amount_minor=30000,
                currency="KES",
                reference="attempt-7",
                customer_phone="0722000099",
                payload={},
            )

    def test_a_rejected_status_from_daraja_reads_as_rejected(self):
        session = self._session(
            FakeResponse(body={"access_token": "token"}),
            FakeResponse(
                status_code=400,
                body={"errorCode": "400.002.02", "errorMessage": "Bad Request"},
            ),
        )
        provider = daraja(session)
        with self.assertRaises(ProviderRejected):
            provider.create_payment(
                amount_minor=30000,
                currency="KES",
                reference="attempt-8",
                customer_phone="0722000099",
                payload={},
            )

    def test_the_token_is_reused_until_it_is_near_expiry(self):
        session = self._session(
            FakeResponse(body={"access_token": "token", "expires_in": 3599}),
            FakeResponse(body={"ResponseCode": "0", "CheckoutRequestID": "a"}),
            FakeResponse(body={"ResponseCode": "0", "CheckoutRequestID": "b"}),
        )
        provider = daraja(session)
        for _ in range(2):
            provider.create_payment(
                amount_minor=10000,
                currency="KES",
                reference="r",
                customer_phone="0722000099",
                payload={},
            )
        oauth_calls = [c for c in session.calls if c["method"] == "GET"]
        self.assertEqual(len(oauth_calls), 1, "the token must be cached")


class CallbackParsingTestCase(APITestCase):
    """Both callback shapes land on the same four-value contract."""

    def _provider(self):
        return daraja(RecordingSession())

    def test_a_paid_callback_parses_to_a_successful_collection(self):
        ref, state, amount_minor, currency, _ = self._provider().parse_callback(
            DARAJA_SUCCESS
        )
        self.assertEqual(ref, "ws_CO_191220191020363925")
        self.assertEqual(state, "success")
        self.assertEqual(amount_minor, 30000)
        self.assertEqual(currency, "KES")

    def test_the_receipt_is_kept_for_reconciliation(self):
        *_, normalized = self._provider().parse_callback(DARAJA_SUCCESS)
        self.assertEqual(normalized["mpesa_receipt_number"], "NLJ7RT61SV")
        self.assertEqual(normalized["result_code"], 0)

    def test_a_declined_callback_parses_as_a_failure(self):
        ref, state, amount_minor, _, _ = self._provider().parse_callback(
            DARAJA_DECLINED
        )
        self.assertEqual(ref, "ws_CO_191220191020363926")
        self.assertEqual(state, "failed")
        # Daraja sends no Amount when nothing moved; a failure has no amount
        # to compare against.
        self.assertEqual(amount_minor, 0)

    def test_a_callback_without_a_checkout_id_is_refused(self):
        from apps.payments.provider import ProviderError

        with self.assertRaises(ProviderError):
            self._provider().parse_callback(
                {"Body": {"stkCallback": {"ResultCode": 0}}}
            )

    def test_the_local_sandbox_shape_still_parses(self):
        ref, state, amount_minor, currency, _ = self._provider().parse_callback(
            {
                "provider_reference": "MPABC",
                "status": "success",
                "amount_minor": 30000,
                "currency": "KES",
            }
        )
        self.assertEqual((ref, state, amount_minor, currency), ("MPABC", "success", 30000, "KES"))


class CallbackReachesTheLedgerTestCase(APITestCase):
    """A Daraja callback, posted as Safaricom posts it, settles the order."""

    def setUp(self):
        from apps.payments import provider

        provider._PROVIDERS.clear()
        self.org = Organization.objects.create(name="Delta Haulage", slug="delta")
        self.user = User.objects.create_user("0733000011", "pass-Y-1234")
        Membership.objects.create(
            user=self.user, organization=self.org, role=Role.OPERATOR_ADMIN
        )
        service = Service.objects.create(
            slug="stk-parcel", name="STK Parcel", service_type="courier"
        )
        quote = build_quote(
            service=service,
            request_payload={"route": "nairobi-mombasa"},
            total_minor=30000,
            currency="KES",
            organization=self.org,
        )
        quote.accept()
        self.order = Order.objects.create(
            organization=self.org,
            customer=self.user,
            service=service,
            quote=quote,
            currency="KES",
            total_minor=30000,
        )

    def _settle(self, payload):
        """Deliver a callback with Daraja configured, as production would.

        The attempt was created with no credentials (the stub), so the cache
        has to be dropped before the credentials are put in place or the
        callback would still be parsed by the stub.
        """
        from apps.payments import provider

        provider._PROVIDERS.clear()
        with mock.patch.dict("os.environ", CREDENTIALS, clear=False):
            return handle_payment_callback("mpesa", payload)

    def test_a_successful_daraja_callback_settles_and_confirms(self):
        attempt = create_payment_attempt(
            organization_id=self.org.pk,
            order_id=self.order.pk,
            idempotency_key="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            amount_minor=30000,
            currency="KES",
            customer_phone="0733000011",
            provider_slug="mpesa",
        )
        # The adapter names the attempt by its CheckoutRequestID; mirror the
        # value Daraja would have issued for it.
        attempt.provider_reference = "ws_CO_191220191020363925"
        attempt.save(update_fields=["provider_reference", "updated_at"])

        _, applied = self._settle(DARAJA_SUCCESS)

        self.assertTrue(applied)
        attempt.refresh_from_db()
        self.assertEqual(attempt.state, PaymentAttempt.State.SUCCESS)
        self.assertEqual(LedgerEntry.objects.filter(payment_attempt=attempt).count(), 2)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.CONFIRMED)

    def test_a_declined_daraja_callback_is_a_failure_not_an_unknown_one(self):
        attempt = create_payment_attempt(
            organization_id=self.org.pk,
            order_id=self.order.pk,
            idempotency_key="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            amount_minor=30000,
            currency="KES",
            customer_phone="0733000011",
            provider_slug="mpesa",
        )
        attempt.provider_reference = "ws_CO_191220191020363926"
        attempt.save(update_fields=["provider_reference", "updated_at"])

        _, applied = self._settle(DARAJA_DECLINED)

        self.assertTrue(applied)
        attempt.refresh_from_db()
        # Before the amount check was scoped to collections, the absent Amount
        # read as a mismatch and every declined payment became "unknown",
        # sending operators to reconcile a payment that never happened.
        self.assertEqual(attempt.state, PaymentAttempt.State.FAILED)
        self.assertFalse(LedgerEntry.objects.filter(payment_attempt=attempt).exists())
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.CREATED)


class RejectedAttemptTestCase(APITestCase):
    """A provider that says no gets recorded, not raised to the caller."""

    def setUp(self):
        self.org = Organization.objects.create(name="Zeta Cargo", slug="zeta")
        self.user = User.objects.create_user("0755000011", "pass-W-1234")
        Membership.objects.create(
            user=self.user, organization=self.org, role=Role.OPERATOR_ADMIN
        )
        service = Service.objects.create(
            slug="reject-parcel", name="Reject Parcel", service_type="courier"
        )
        quote = build_quote(
            service=service,
            request_payload={"route": "nairobi-kisumu"},
            total_minor=30000,
            currency="KES",
            organization=self.org,
        )
        quote.accept()
        self.order = Order.objects.create(
            organization=self.org,
            customer=self.user,
            service=service,
            quote=quote,
            currency="KES",
            total_minor=30000,
        )

    def _create(self):
        return create_payment_attempt(
            organization_id=self.org.pk,
            order_id=self.order.pk,
            idempotency_key="cccccccc-cccc-cccc-cccc-cccccccccccc",
            amount_minor=30000,
            currency="KES",
            customer_phone="0755000011",
            provider_slug="mpesa",
        )

    def test_a_definitive_refusal_is_a_recorded_failure(self):
        with mock.patch("apps.payments.services.get_provider") as get_provider:
            get_provider.return_value.create_payment.side_effect = ProviderRejected(
                "M-PESA STK Push collects whole shillings."
            )
            attempt = self._create()

        self.assertEqual(attempt.state, PaymentAttempt.State.FAILED)
        self.assertTrue(attempt.result_received)
        self.assertIn("whole shillings", attempt.note)
        # Nothing was collected, so no postings and no confirmation.
        self.assertFalse(LedgerEntry.objects.filter(payment_attempt=attempt).exists())
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.CREATED)
        self.assertTrue(
            AuditEvent.objects.filter(
                action="payment.rejected", entity_id=attempt.pk
            ).exists()
        )

    def test_a_timeout_leaves_the_attempt_pending_not_failed(self):
        with mock.patch("apps.payments.services.get_provider") as get_provider:
            get_provider.return_value.create_payment.side_effect = ProviderUnavailable(
                "Daraja is unreachable."
            )
            attempt = self._create()

        self.assertEqual(attempt.state, PaymentAttempt.State.PENDING)
        self.assertFalse(attempt.result_received)
        self.assertIn("unreachable", attempt.note)


class HttpEndToEndTestCase(APITestCase):
    """The whole path over HTTP: order, attempt, webhook, ledger, confirmation.

    The frontend has no payment screen yet, so the API surface is the contract.
    """

    def setUp(self):
        from apps.payments import provider

        provider._PROVIDERS.clear()
        self.org = Organization.objects.create(name="Epsilon Freight", slug="epsilon")
        self.user = User.objects.create_user("0744000011", "pass-Z-1234")
        Membership.objects.create(
            user=self.user, organization=self.org, role=Role.OPERATOR_ADMIN
        )
        service = Service.objects.create(
            slug="http-parcel", name="HTTP Parcel", service_type="courier"
        )
        quote = build_quote(
            service=service,
            request_payload={"route": "nairobi-eldoret"},
            total_minor=30000,
            organization=self.org,
        )
        quote.accept()
        self.order = Order.objects.create(
            organization=self.org,
            customer=self.user,
            service=service,
            quote=quote,
            currency="KES",
            total_minor=30000,
        )
        from rest_framework_simplejwt.tokens import RefreshToken

        self.client = APIClient()
        self.token = f"Bearer {RefreshToken.for_user(self.user).access_token}"

    def test_order_to_webhook_to_ledger(self):
        created = self.client.post(
            "/api/v1/payments/",
            {
                "order": str(self.order.pk),
                "amount_minor": 30000,
                "phone": "0744000011",
            },
            format="json",
            HTTP_AUTHORIZATION=self.token,
        )
        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(created.data["state"], "pending")
        reference = created.data["provider_reference"]

        settled = self.client.post(
            "/api/v1/webhooks/mpesa/",
            {
                "provider_reference": reference,
                "status": "success",
                "amount_minor": 30000,
                "currency": "KES",
            },
            format="json",
        )
        self.assertEqual(settled.status_code, 200, settled.data)
        self.assertTrue(settled.data["applied"])
        self.assertEqual(settled.data["attempt_state"], "success")

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.CONFIRMED)

        ledger = self.client.get(
            "/api/v1/ledger/entries/", HTTP_AUTHORIZATION=self.token
        )
        self.assertEqual(ledger.status_code, 200)
        postings = [row for row in ledger.data if row["order"] == str(self.order.pk)]
        self.assertEqual(len(postings), 2)
        self.assertEqual({row["entry_type"] for row in postings}, {"debit", "credit"})
        self.assertEqual(
            postings[0]["correlation_id"], postings[1]["correlation_id"]
        )
        self.assertEqual(
            sum(row["amount_minor"] for row in postings if row["entry_type"] == "debit")
            - sum(
                row["amount_minor"]
                for row in postings
                if row["entry_type"] == "credit"
            ),
            0,
            "a collection must post a balanced pair",
        )

    def test_an_order_that_was_paid_over_the_api_has_an_audit_trail(self):
        created = self.client.post(
            "/api/v1/payments/",
            {"order": str(self.order.pk), "amount_minor": 30000, "phone": "0744000011"},
            format="json",
            HTTP_AUTHORIZATION=self.token,
        )
        self.client.post(
            "/api/v1/webhooks/mpesa/",
            {
                "provider_reference": created.data["provider_reference"],
                "status": "success",
                "amount_minor": 30000,
                "currency": "KES",
            },
            format="json",
        )
        actions = set(AuditEvent.objects.values_list("action", flat=True))
        self.assertIn("payment.success", actions)


class HttpDarajaCallbackTestCase(APITestCase):
    """A real Daraja `stkCallback`, delivered over the live webhook endpoint.

    The two halves of a live proof are deliberately separable. Safaricom cannot
    reach a callback URL on localhost and its Imperva edge rejects a scripted
    STK push, so the push half cannot be exercised from a machine like this one.
    The delivery half can: the payload below is posted through the real view,
    the real Daraja parser, the real amount check and the real ledger, so the
    only thing that is manual is the transport -- which is what a live proof
    against a callback URL Safaricom could not reach would have been anyway.
    """

    def setUp(self):
        from apps.payments import provider

        provider._PROVIDERS.clear()
        self.org = Organization.objects.create(name="Echo Haulage", slug="echo")
        self.user = User.objects.create_user("0733000012", "pass-Z-1234")
        Membership.objects.create(
            user=self.user, organization=self.org, role=Role.OPERATOR_ADMIN
        )
        service = Service.objects.create(
            slug="daraja-parcel", name="Daraja Parcel", service_type="courier"
        )
        quote = build_quote(
            service=service,
            request_payload={"route": "nairobi-mombasa"},
            total_minor=30000,
            currency="KES",
            organization=self.org,
        )
        quote.accept()
        self.order = Order.objects.create(
            organization=self.org,
            customer=self.user,
            service=service,
            quote=quote,
            currency="KES",
            total_minor=30000,
        )
        self.checkout_id = "ws_CO_191220191020363925"
        self.payload = DARAJA_SUCCESS

    def tearDown(self):
        from apps.payments import provider

        provider._PROVIDERS.clear()

    def _attempt_pending(self):
        """A pending attempt carrying the CheckoutRequestID Daraja would issue."""
        attempt = create_payment_attempt(
            organization_id=self.org.pk,
            order_id=self.order.pk,
            idempotency_key="cccccccc-cccc-cccc-cccc-cccccccccccc",
            amount_minor=30000,
            currency="KES",
            customer_phone="0733000012",
            provider_slug="mpesa",
        )
        attempt.provider_reference = self.checkout_id
        attempt.save(update_fields=["provider_reference", "updated_at"])
        return attempt

    def _post(self, payload=None):
        # Credentials are absent while the attempt is created so the stub
        # answers; the cache is then dropped so the callback is parsed by the
        # real Daraja adapter, as it would be in production.
        from apps.payments import provider

        with mock.patch.dict("os.environ", CREDENTIALS, clear=False):
            provider._PROVIDERS.clear()
            return self.client.post(
                "/api/v1/webhooks/mpesa/",
                payload if payload is not None else self.payload,
                format="json",
            )

    def test_a_real_stk_callback_shape_settles_the_order_over_http(self):
        attempt = self._attempt_pending()
        self.assertEqual(attempt.state, PaymentAttempt.State.PENDING)

        settled = self._post()

        self.assertEqual(settled.status_code, 200, settled.data)
        self.assertTrue(settled.data["applied"])
        self.assertEqual(settled.data["attempt_state"], "success")

        attempt.refresh_from_db()
        self.assertEqual(attempt.state, PaymentAttempt.State.SUCCESS)
        self.assertTrue(attempt.result_received)
        self.assertEqual(attempt.provider_payload["mpesa_receipt_number"], "NLJ7RT61SV")

        postings = LedgerEntry.objects.filter(payment_attempt=attempt)
        self.assertEqual(postings.count(), 2)
        self.assertEqual({row.entry_type for row in postings}, {"debit", "credit"})
        self.assertEqual(len({row.correlation_id for row in postings}), 1)

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.CONFIRMED)

    def test_replaying_the_same_stk_callback_costs_nothing_twice(self):
        attempt = self._attempt_pending()

        first = self._post()
        second = self._post()

        self.assertTrue(first.data["applied"])
        self.assertFalse(second.data["applied"])
        self.assertEqual(second.data["attempt_state"], "success")
        self.assertEqual(
            LedgerEntry.objects.filter(payment_attempt=attempt).count(),
            2,
            "a replayed callback must not post a second collection",
        )

    def test_a_declined_stk_callback_over_http_leaves_the_order_alone(self):
        attempt = self._attempt_pending()
        self.payload = DARAJA_DECLINED
        attempt.provider_reference = DARAJA_DECLINED["Body"]["stkCallback"][
            "CheckoutRequestID"
        ]
        attempt.save(update_fields=["provider_reference", "updated_at"])

        settled = self._post(DARAJA_DECLINED)

        self.assertEqual(settled.status_code, 200, settled.data)
        self.assertTrue(settled.data["applied"])
        self.assertEqual(settled.data["attempt_state"], "failed")
        attempt.refresh_from_db()
        self.assertEqual(attempt.state, PaymentAttempt.State.FAILED)
        self.assertFalse(LedgerEntry.objects.filter(payment_attempt=attempt).exists())
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.CREATED)

    def test_the_amount_in_the_callback_must_match_the_order(self):
        attempt = self._attempt_pending()
        mismatched = {
            "Body": {
                "stkCallback": {
                    **self.payload["Body"]["stkCallback"],
                    "CallbackMetadata": {
                        "Item": [
                            {"Name": "Amount", "Value": 1.0},
                            {"Name": "MpesaReceiptNumber", "Value": "NLJ7RT61SW"},
                            {"Name": "TransactionDate", "Value": 20191219102115},
                            {"Name": "PhoneNumber", "Value": 254708374149},
                        ]
                    },
                }
            }
        }

        settled = self._post(mismatched)

        self.assertEqual(settled.status_code, 200, settled.data)
        self.assertTrue(settled.data["applied"])
        attempt.refresh_from_db()
        self.assertEqual(attempt.state, PaymentAttempt.State.UNKNOWN)
        self.assertFalse(LedgerEntry.objects.filter(payment_attempt=attempt).exists())
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.CREATED)


class HelperTestCase(APITestCase):
    def test_minor_units_survive_binary_floating_point(self):
        self.assertEqual(minor_units("0.29"), 29)
        self.assertEqual(minor_units(0.29), 29)
        self.assertEqual(minor_units("300"), 30000)
        self.assertEqual(minor_units("1.00"), 100)
        self.assertEqual(minor_units(300.0), 30000)

    def test_msisdn_normalisation_accepts_the_three_common_forms(self):
        self.assertEqual(normalise_msisdn("0722000099"), "254722000099")
        self.assertEqual(normalise_msisdn("722000099"), "254722000099")
        self.assertEqual(normalise_msisdn("+254722000099"), "254722000099")
        self.assertEqual(normalise_msisdn("254722000099"), "254722000099")
        self.assertEqual(normalise_msisdn("12345"), "")
        self.assertEqual(normalise_msisdn(""), "")

    def test_the_stk_timestamp_is_east_africa_time_and_fourteen_digits(self):
        from datetime import datetime

        fixed = datetime(2026, 10, 19, 21, 5, 9)
        self.assertEqual(stk_timestamp(fixed), "20261019210509")
        self.assertRegex(stk_timestamp(), r"^\d{14}$")
