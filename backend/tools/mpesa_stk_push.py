"""Stage A of a live M-PESA proof: a real Daraja OAuth + STK push.

This exists because Safaricom cannot POST a callback to localhost, and because
Safaricom's Imperva edge rejects a scripted request to the OAuth path from some
networks (it returned an empty HTTP 400 to every shape we tried from one
host). Run it from a connection Imperva accepts and paste the output back; the
callback half is then delivered by hand to the live webhook endpoint.

    python3 tools/mpesa_stk_push.py --phone 0796606363 --amount 1
    python3 tools/mpesa_stk_push.py --query ws_CO_...

Credentials come from the environment or .env, read the same way manage.py
reads them. Nothing secret is printed: not the consumer key or secret, not the
passkey, not the bearer token, not the STK password.

Exits 0 when Daraja accepts the request, 1 when it refuses it (a rejection of
this payment), and 2 when it could not be asked (unreachable or credentials).
"""

import argparse
import json
import os
import sys

# Runnable from anywhere: put the backend root on the path the way manage.py
# does, before anything under apps/ is imported.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.env import load_env  # noqa: E402

load_env()

from apps.payments.provider import (  # noqa: E402
    DarajaProvider,
    ProviderRejected,
    ProviderUnavailable,
    daraja_credentials,
    normalise_msisdn,
)


def build_provider():
    credentials = daraja_credentials()
    if not credentials:
        missing = [
            name
            for name in (
                "MPESA_CONSUMER_KEY",
                "MPESA_CONSUMER_SECRET",
                "MPESA_SHORTCODE",
                "MPESA_PASSKEY",
                "MPESA_CALLBACK_URL",
            )
            if not os.environ.get(name, "").strip()
        ]
        raise SystemExit(f"Missing MPESA_* credentials: {', '.join(missing)}")
    return DarajaProvider(
        environment=credentials["MPESA_ENVIRONMENT"] or "sandbox",
        consumer_key=credentials["MPESA_CONSUMER_KEY"],
        consumer_secret=credentials["MPESA_CONSUMER_SECRET"],
        shortcode=credentials["MPESA_SHORTCODE"],
        passkey=credentials["MPESA_PASSKEY"],
        callback_url=credentials["MPESA_CALLBACK_URL"],
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--phone", help="Kenyan number: 07..., 7... or 254...")
    parser.add_argument(
        "--amount", type=int, default=1, help="Whole shillings. Default 1."
    )
    parser.add_argument(
        "--reference", default="PROOF", help="Account reference, max 12 chars."
    )
    parser.add_argument(
        "--query", help="CheckoutRequestID of a push already made, to ask its state."
    )
    args = parser.parse_args()

    provider = build_provider()
    print(f"adapter: DarajaProvider  base_url: {provider.base_url}", file=sys.stderr)
    print(f"shortcode: {provider.shortcode}", file=sys.stderr)

    try:
        # Reaches Daraja for a bearer token. Credentials are never printed.
        provider._access_token()
    except ProviderUnavailable as exc:
        print(f"Daraja could not be reached: {exc}", file=sys.stderr)
        return 2
    print("oauth: accepted", file=sys.stderr)

    if args.query:
        try:
            state = provider.query_payment(args.query)
        except ProviderUnavailable as exc:
            print(f"Daraja could not be reached: {exc}", file=sys.stderr)
            return 2
        print(json.dumps({"CheckoutRequestID": args.query, "state": state}, indent=2))
        return 0

    if not args.phone:
        parser.error("--phone is required unless --query is given")

    msisdn = normalise_msisdn(args.phone)
    if not msisdn:
        parser.error(f"{args.phone!r} is not a Kenyan subscriber number")

    try:
        result = provider.create_payment(
            amount_minor=args.amount * 100,
            currency="KES",
            reference=args.reference,
            customer_phone=msisdn,
            payload={},
        )
    except ProviderRejected as exc:
        print(f"Daraja refused the STK push: {exc}", file=sys.stderr)
        return 1
    except ProviderUnavailable as exc:
        print(f"Daraja could not be reached: {exc}", file=sys.stderr)
        return 2

    print(
        json.dumps(
            {
                "CheckoutRequestID": result["provider_reference"],
                "MerchantRequestID": result["merchant_request_id"],
                "CustomerMessage": result["customer_message"],
                "Amount": args.amount,
                "PhoneNumber": msisdn,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
