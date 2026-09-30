"""End-to-end smoke test for the courier loop, over real HTTP.

The unit and API tests drive the views through the test client, which short-circuits
the middleware, the URL resolver and the WSGI stack. This runs a real server
against a throwaway SQLite database and a throwaway superuser, on a port nothing
else is using, and drives it with `urllib` — so a broken route, a bad serializer
or a middleware mistake shows up here rather than in production.

It covers the whole courier loop: quote, accept, book, propose a multi-drop plan,
refuse a passenger-service trip, refuse a route of drops that overruns the trip
window, per-parcel delivery proof, a failed attempt on a sibling, a single-parcel
return, cross-organization isolation, and the database agreeing with the API.

    python3 tools/courier_smoke.py

Exits 0 when every step passes, 1 otherwise. Safe to run repeatedly: it builds and
tears down its own database.
"""

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from decimal import Decimal

import django

# Runnable from anywhere: put the backend root (this file's parent) on the path
# before Django settings are read, the way manage.py does.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.test")
django.setup()

from django.test.utils import setup_test_environment, teardown_test_environment  # noqa: E402

setup_test_environment()
from django.test.runner import DiscoverRunner  # noqa: E402

runner = DiscoverRunner(verbosity=0, interactive=False)
old_config = runner.setup_databases()

from django.core.management import call_command  # noqa: E402
from django.utils import timezone  # noqa: E402

from apps.accounts.models import Membership, Organization, Role, User  # noqa: E402
from apps.catalog.models import Place, RateCard, RateLine, Service  # noqa: E402
from apps.fleet.models import CargoCompartment, Driver, Vehicle  # noqa: E402
from apps.goods.models import Package, Shipment  # noqa: E402

PORT = 8971
BASE = f"http://127.0.0.1:{PORT}/api/v1"
PHONE = "+254711000001"
PASSWORD = "smoke-password-1"
FAILURES = []


def step(label):
    print(f"\n== {label}")


def ok(label):
    print(f"   ok  {label}")


def call(method, path, body=None, token=None, expect=200):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(f"{BASE}{path}", data=data, method=method)
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            status, payload = response.status, response.read()
    except urllib.error.HTTPError as exc:
        status, payload = exc.code, exc.read()
    try:
        parsed = json.loads(payload) if payload else None
    except json.JSONDecodeError:
        parsed = payload[:400]
    if status != expect:
        FAILURES.append(f"{method} {path} -> {status} (wanted {expect}): {parsed}")
        print(f"   FAIL {method} {path} -> {status}, wanted {expect}")
        print(f"        {parsed}")
    else:
        ok(f"{method} {path} -> {status}")
    return parsed


def check(condition, label):
    if condition:
        ok(label)
    else:
        FAILURES.append(label)
        print(f"   FAIL {label}")


def build_world():
    org = Organization.objects.create(name="Smoke Courier", slug="smoke-courier")
    other = Organization.objects.create(name="Smoke Rival", slug="smoke-rival")
    dispatcher = User.objects.create_user(PHONE, PASSWORD)
    Membership.objects.create(
        user=dispatcher, organization=org, role=Role.DISPATCHER
    )
    courier = Service.objects.create(
        slug="smoke-courier-service",
        name="Smoke courier",
        service_type=Service.ServiceType.COURIER,
    )
    shuttle = Service.objects.create(
        slug="smoke-shuttle", name="Smoke shuttle", service_type=Service.ServiceType.SCHEDULED
    )
    depot = Place.objects.create(
        organization=org, name="Depot", place_type=Place.PlaceType.BRANCH
    )
    market = Place.objects.create(
        organization=org, name="Market", place_type=Place.PlaceType.HUB
    )
    mill = Place.objects.create(
        organization=org, name="Mill", place_type=Place.PlaceType.BRANCH
    )
    rival_place = Place.objects.create(
        organization=other, name="Rival hall", place_type=Place.PlaceType.BRANCH
    )

    card = RateCard.objects.create(
        organization=org,
        service=courier,
        name="Smoke courier card",
        version=1,
        currency="KES",
        effective_from=date(2026, 1, 1),
    )
    for from_value, to_value, amount in ((0, 1, 300), (1, 5, 600), (5, None, 1_200)):
        RateLine.objects.create(
            rate_card=card,
            line_type=RateLine.LineType.PER_KG,
            label=f"{from_value}-{to_value or 'up'}kg",
            from_value=from_value,
            to_value=to_value,
            amount_minor=amount,
        )

    van = Vehicle.objects.create(
        organization=org,
        registration_number="KSM01A",
        vehicle_class=Vehicle.VehicleClass.VAN,
        cargo_weight_kg=900,
        cargo_volume_m3=6.0,
    )
    CargoCompartment.objects.create(
        organization=org,
        vehicle=van,
        name="load bay",
        max_weight_kg=900,
        max_volume_m3=6.0,
    )
    driver = Driver.objects.create(
        user=User.objects.create_user("+254711000002", "smoke-driver-1"),
        organization=org,
        status=Driver.Status.APPROVED,
        working_hours_per_day=8,
    )
    return {
        "org": org,
        "other": other,
        "dispatcher": dispatcher,
        "courier": courier,
        "shuttle": shuttle,
        "depot": depot,
        "market": market,
        "mill": mill,
        "rival_place": rival_place,
        "van": van,
        "driver": driver,
        "card": card,
    }


def smoke():
    world = build_world()
    org, dispatcher = world["org"], world["dispatcher"]

    start = timezone.make_aware(datetime(2026, 6, 1, 8, 0))
    end = timezone.make_aware(datetime(2026, 6, 1, 16, 0))

    step("auth")
    token = call("POST", "/auth/token/", {"phone": PHONE, "password": PASSWORD})["access"]
    call("GET", "/shipments/", expect=401)

    step("courier quote prices each parcel on its own band")
    quote = call(
        "POST",
        "/shipments/courier-quote/",
        {
            "service": str(world["courier"].pk),
            "collection": str(world["depot"].pk),
            "destination": str(world["market"].pk),
            "sender_name": "Grace Wanjiku",
            "sender_phone": "0722000099",
            "service_window_start": start.isoformat(),
            "service_window_end": end.isoformat(),
            "parcels": [
                {
                    "recipient_name": "Achieng Otieno",
                    "recipient_phone": "0722000001",
                    "recipient_place": str(world["market"].pk),
                    "description": "Books",
                    "weight_kg": 0.8,
                    "length_cm": 20,
                    "width_cm": 15,
                    "height_cm": 10,
                },
                {
                    "recipient_name": "Brian Kiptoo",
                    "recipient_phone": "0722000002",
                    "recipient_place": str(world["mill"].pk),
                    "description": "Spare parts",
                    "weight_kg": 3.0,
                    "length_cm": 30,
                    "width_cm": 20,
                    "height_cm": 15,
                },
                {
                    "recipient_name": "Carol Mwangi",
                    "recipient_phone": "0722000003",
                    "recipient_place": str(world["market"].pk),
                    "description": "Clothes",
                    "weight_kg": 9.0,
                    "length_cm": 40,
                    "width_cm": 30,
                    "height_cm": 25,
                },
            ],
        },
        token=token,
        expect=201,
    )
    check(quote["total_minor"] == 300 + 600 + 1_200, "total is 2100 minor units")
    check(
        [p["amount_minor"] for p in quote["parcels"]] == [300, 600, 1_200],
        "each parcel priced on its own band",
    )
    check(quote["basis"] == "chargeable_weight", "basis is chargeable weight")

    step("a destination no parcel goes to is refused")
    call(
        "POST",
        "/shipments/courier-quote/",
        {
            "service": str(world["courier"].pk),
            "collection": str(world["depot"].pk),
            "destination": str(world["rival_place"].pk),
            "service_window_start": start.isoformat(),
            "service_window_end": end.isoformat(),
            "parcels": [
                {
                    "recipient_name": "Achieng Otieno",
                    "recipient_phone": "0722000001",
                    "recipient_place": str(world["market"].pk),
                    "weight_kg": 1,
                    "length_cm": 10,
                    "width_cm": 10,
                    "height_cm": 10,
                }
            ],
        },
        token=token,
        expect=400,
    )

    step("accept and book the courier quote")
    call("POST", f"/quotes/{quote['quote']}/accept/", {}, token=token)
    order = call("POST", "/shipments/", {"quote": str(quote["quote"])}, token=token, expect=201)
    check(order["package_count"] == 3, "order carries three parcels")
    check(order["is_multi_drop"] is True, "order is flagged multi-drop")
    check(sorted(order["drop_place_names"]) == ["Market", "Mill"], "drops are Market and Mill")
    check(order["recipient_name"] == "", "order names no single consignee")
    check(order["agreed_total_minor"] == 2_100, "booked total is frozen at 2100")

    step("a later rate-card edit changes no booked money")
    RateLine.objects.filter(rate_card=world["card"]).update(amount_minor=1)
    refetched = call("GET", f"/shipments/{order['id']}/", token=token)
    check(refetched["agreed_total_minor"] == 2_100, "booked money is unchanged")

    step("parcels show their own recipient, drop and frozen charge")
    packages = call("GET", f"/shipments/{order['id']}/packages/", token=token)
    by_name = {p["recipient_name"]: p for p in packages}
    check(len(packages) == 3, "three parcels listed")
    check(
        by_name["Brian Kiptoo"]["agreed_charge_minor"] == 600,
        "Brian's parcel carries its own charge",
    )
    check(
        str(by_name["Brian Kiptoo"]["chargeable_weight_kg"]) == "3.000",
        "Brian's parcel is charged on 3.000 kg",
    )
    ach = by_name["Achieng Otieno"]["id"]
    brian = by_name["Brian Kiptoo"]["id"]
    carol = by_name["Carol Mwangi"]["id"]

    step("a plan carries a stop for every distinct drop")
    trip = call(
        "POST",
        "/freight-trips/",
        {
            "vehicle": str(world["van"].pk),
            "driver": str(world["driver"].pk),
            "service": str(world["courier"].pk),
            "origin": str(world["depot"].pk),
            "destination": str(world["market"].pk),
            "planned_start": start.isoformat(),
            "planned_end": end.isoformat(),
        },
        token=token,
        expect=201,
    )
    plan = call(
        "POST",
        f"/freight-trips/{trip['id']}/consolidation/propose/",
        {"shipments": [order["id"]]},
        token=token,
        expect=201,
    )
    check(plan["status"] in ("proposed", "draft"), f"plan is {plan['status']}")
    stops = call("GET", f"/consolidation-plans/{plan['id']}/", token=token)
    check(len(stops["stops"]) == 3, f"plan has 3 stops (got {len(stops['stops'])})")

    step("a courier order is refused on a passenger-service trip")
    shuttle_trip = call(
        "POST",
        "/freight-trips/",
        {
            "vehicle": str(world["van"].pk),
            "driver": str(world["driver"].pk),
            "service": str(world["shuttle"].pk),
            "origin": str(world["depot"].pk),
            "destination": str(world["market"].pk),
            "planned_start": (start + timedelta(days=1)).isoformat(),
            "planned_end": (end + timedelta(days=1)).isoformat(),
        },
        token=token,
        expect=201,
    )
    call(
        "POST",
        f"/freight-trips/{shuttle_trip['id']}/consolidation/propose/",
        {"shipments": [order["id"]]},
        token=token,
        expect=201,
    )
    step("the passenger trip refuses the courier order and says why")
    (shuttle_plan,) = call(
        "GET", f"/freight-trips/{shuttle_trip['id']}/consolidation/", token=token
    )
    (shuttle_rejected,) = shuttle_plan["shipments"]
    check(shuttle_rejected["accepted"] is False, "the courier order was not admitted")
    check(
        "passenger" in shuttle_rejected["reason"],
        f"refusal names the passenger service: {shuttle_rejected['reason']!r}",
    )

    step("a route of drops that overruns the trip window is refused, naming the overrun")
    crowded = []
    for index in range(10):
        place = Place.objects.create(
            organization=org, name=f"Crowded {index}", place_type=Place.PlaceType.BRANCH
        )
        crowded.append(
            {
                "recipient_name": f"Crowded {index}",
                "recipient_phone": "0722000099",
                "recipient_place": str(place.pk),
                "weight_kg": 0.5,
                "length_cm": 10,
                "width_cm": 10,
                "height_cm": 10,
            }
        )
    # The order's own window is the short trip's window, so the refusal being
    # tested is the drops, not the window.
    short_start = start + timedelta(days=2)
    short_end = short_start + timedelta(minutes=30)
    crowded_quote = call(
        "POST",
        "/shipments/courier-quote/",
        {
            "service": str(world["courier"].pk),
            "collection": str(world["depot"].pk),
            "destination": crowded[0]["recipient_place"],
            "service_window_start": short_start.isoformat(),
            "service_window_end": short_end.isoformat(),
            "parcels": crowded,
        },
        token=token,
        expect=201,
    )
    call("POST", f"/quotes/{crowded_quote['quote']}/accept/", {}, token=token)
    crowded_order = call(
        "POST", "/shipments/", {"quote": str(crowded_quote["quote"])}, token=token, expect=201
    )
    second_van = Vehicle.objects.create(
        organization=org,
        registration_number="KSM02A",
        vehicle_class=Vehicle.VehicleClass.VAN,
        cargo_weight_kg=900,
        cargo_volume_m3=6.0,
    )
    CargoCompartment.objects.create(
        organization=org,
        vehicle=second_van,
        name="load bay",
        max_weight_kg=900,
        max_volume_m3=6.0,
    )
    short_trip = call(
        "POST",
        "/freight-trips/",
        {
            "vehicle": str(second_van.pk),
            "driver": str(world["driver"].pk),
            "service": str(world["courier"].pk),
            "origin": str(world["depot"].pk),
            "destination": str(world["market"].pk),
            "planned_start": short_start.isoformat(),
            "planned_end": short_end.isoformat(),
        },
        token=token,
        expect=201,
    )
    call(
        "POST",
        f"/freight-trips/{short_trip['id']}/consolidation/propose/",
        {"shipments": [crowded_order["id"]]},
        token=token,
        expect=201,
    )
    (crowded_plan,) = call(
        "GET", f"/freight-trips/{short_trip['id']}/consolidation/", token=token
    )
    (crowded_rejected,) = crowded_plan["shipments"]
    check(crowded_rejected["accepted"] is False, "the crowded order was not admitted")
    check(
        "drops" in crowded_rejected["reason"],
        f"refusal names the drops: {crowded_rejected['reason']!r}",
    )
    check(
        "11 drops" in crowded_rejected["reason"],
        f"refusal counts the drops: {crowded_rejected['reason']!r}",
    )

    step("delivery proof must name the parcel's own recipient")
    call(
        "POST",
        f"/packages/{ach}/deliver/",
        {
            "recipient_name": "Brian Kiptoo",
            "recipient_id_type": "national_id",
            "recipient_id_last4": "4321",
            "photos": ["https://files.example/parcel.jpg"],
        },
        token=token,
        expect=400,
    )
    call(
        "POST",
        f"/packages/{ach}/deliver/",
        {"recipient_name": "Achieng Otieno", "photos": ["https://files.example/parcel.jpg"]},
        token=token,
        expect=400,
    )
    after = call("GET", f"/shipments/{order['id']}/", token=token)
    check(after["status"] == "booked", "refused proofs left the order untouched")

    step("a good delivery moves the order to partially_delivered")
    call(
        "POST",
        f"/packages/{ach}/deliver/",
        {
            "recipient_name": "Achieng Otieno",
            "recipient_id_type": "national_id",
            "recipient_id_last4": "4321",
            "photos": ["https://files.example/parcel.jpg"],
        },
        token=token,
    )
    after = call("GET", f"/shipments/{order['id']}/", token=token)
    check(after["status"] == "partially_delivered", f"order is {after['status']}")

    step("a failed attempt raises an exception and leaves the parcel out")
    call(
        "POST",
        f"/packages/{brian}/failed-attempt/",
        {
            "kind": "absent",
            "next_action": "return",
            "detail": "Nobody at the gate",
        },
        token=token,
        expect=201,
    )
    after = call("GET", f"/shipments/{order['id']}/", token=token)
    check(after["status"] == "exception", f"order is {after['status']}")
    check(after["open_exceptions"] == 1, "one open exception on the order")

    step("a failed parcel is returned on its own, from its own drop")
    returned = call(
        "POST",
        f"/shipments/{order['id']}/return/",
        {"packages": [brian], "reason": "Recipient absent"},
        token=token,
        expect=201,
    )
    check(returned["is_return"] is True, "return is flagged as a return")
    check(str(returned["collection"]) == str(world["mill"].pk), "collected from Brian's drop")
    check(str(returned["destination"]) == str(world["depot"].pk), "goes back to the depot")
    check(returned["recipient_name"] == "Grace Wanjiku", "addressed back to the sender")

    step("the rest of the order carries on")
    call(
        "POST",
        f"/packages/{carol}/deliver/",
        {
            "recipient_name": "Carol Mwangi",
            "recipient_id_type": "national_id",
            "recipient_id_last4": "7788",
            "photos": ["https://files.example/parcel.jpg"],
        },
        token=token,
    )
    final = call("GET", f"/shipments/{order['id']}/", token=token)
    check(final["status"] == "delivered", f"order closed as {final['status']}")
    check(final["open_exceptions"] == 0, "no exception left open")

    step("cross-organization access is refused")
    call("GET", f"/shipments/{order['id']}/", expect=401)
    rival = User.objects.create_user("+254799000001", "rival-password-1")
    Membership.objects.create(
        user=rival, organization=world["other"], role=Role.DISPATCHER
    )
    rival_token = call(
        "POST",
        "/auth/token/",
        {"phone": "+254799000001", "password": "rival-password-1"},
    )["access"]
    call("GET", f"/shipments/{order['id']}/", token=rival_token, expect=404)

    step("database state agrees with the API")
    stored = Shipment.objects.get(pk=order["id"])
    check(stored.package_count == 3, "three parcels in the database")
    check(
        stored.packages.filter(status=Package.Status.RETURNED).count() == 1,
        "one parcel returned in the database",
    )
    check(
        stored.packages.filter(status=Package.Status.DELIVERED).count() == 2,
        "two parcels delivered in the database",
    )
    check(
        sum(p.agreed_charge_minor for p in stored.packages.all()) == 2_100,
        "per-parcel charges still sum to the frozen total",
    )
    check(
        RateCard.objects.get(pk=world["card"].pk).lines.count() == 3,
        "the tariff was edited but the order was not",
    )


def main():
    from django.core.servers.basehttp import WSGIServer
    from django.core.servers.basehttp import run as serve_http

    class Quiet(WSGIServer):
        allow_reuse_address = True

    import config.wsgi as wsgi

    def serve():
        serve_http(
            "127.0.0.1",
            PORT,
            wsgi.application,
            ipv6=False,
            threading=True,
            server_cls=Quiet,
        )

    server = threading.Thread(target=serve, daemon=True)
    server.start()
    for _ in range(60):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/docs/", timeout=2)
            break
        except Exception:
            time.sleep(0.5)
    try:
        smoke()
    finally:
        teardown_test_environment()
        runner.teardown_databases(old_config)

    print()
    if FAILURES:
        print(f"SMOKE FAILED: {len(FAILURES)} problem(s)")
        for failure in FAILURES:
            print(f"  - {failure}")
        sys.exit(1)
    print("SMOKE OK")


if __name__ == "__main__":
    main()
