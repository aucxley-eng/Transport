# Kenya Transport and Logistics Platform

A unified transport marketplace and operations platform for Kenya covering passenger transport (scheduled buses and shuttles, on-demand rides, corporate and charter) and goods transport (courier, intercity parcels, freight, warehousing, returns and claims) on one shared business foundation: identity, fleet, pricing, payments, tracking and finance.

The complete product and technical architecture is defined in [`Kenya_Transport_and_Logistics_Architecture.pdf`](./Kenya_Transport_and_Logistics_Architecture.pdf) (28 pages, v1.0, 18 September 2026). This README documents the current repository state and how to work on it.

## Status

Phase 1 (shared foundation), all of Phase 2 (scheduled booking, on-demand rides, corporate shuttles and charter/hire) and goods Envelopes 3A, 3B, 3C and 3D (booking and custody, consolidated shared load with the coverage floor and backhaul, mid-route insertion, then local courier) are implemented and green:

| Component | State |
|---|---|
| `backend/` | Django + DRF API running on `/api/v1/`. 12 apps: `common` (outbox), `accounts` (identity/orgs/permissions), `audit`, `fleet` (vehicles/drivers/documents/cargo compartments), `catalog` (services/places/rate cards/quotes), `orders`, `booking` (routes/departures/segment seats/holds/tickets/boarding), `rides` (ride requests/dispatch/offers/trips/incidents), `corporate` (policies/cost centres/employees/travel requests/shuttles), `charter` (hire requests/vehicle+driver reservations/deposits/extras), `goods` (shipments/packages/legs/trips/manifests/custody/evidence/exceptions/returns), `payments` (M-PESA adapter, ledger). |
| `frontend/` | Empty React-style directory tree (auth, driver, dispatch, parent pages; api, context, components, utils). No source files. |
| `infra/` | Empty. |
| Tests | 369 passing: operator isolation, payment idempotency/callback dedup, outbox atomicity, segment seat inventory, atomic holds, ticket-on-payment, boarding, driver eligibility, first-accept-wins assignment, trip PIN, destination change, failed matching, contact privacy, corporate eligibility/approval/budget/shuttles/roster/departure, charter quoting/interval reservation/travel+turnaround availability/deposits/permitted extras, freight quote basis/terms snapshot, cargo capacity independent of seats, per-compartment limits, manifest reconciliation and release gating, append-only custody with corrections, delivery evidence verification, exceptions with next actions, linked returns, shared-load consolidation with explained rejections, plan validation/versioning/publication, the coverage-floor departure gate, backhaul opportunities, the empty-running measure, mid-route insertion preserving completed stops, delay tolerance refusals, driver-offer version checks, manifest amendment as a new version, contiguous stop numbering across insertions, frozen prices under insertion, and the nineteen courier invariants (per-parcel weight-band pricing, account rate-card resolution, per-parcel delivery proof, partial delivery, returns after a failed attempt, the mid-route insertion gate, and driver handovers). |
| Blueprint | Full architecture in the PDF. Phase 1, Phase 2 (§7–8) and Envelopes 3A–3D of §9–10 are delivered. Remaining: intercity parcel hubs, warehouse inventory, returns/claims depth, telemetry ingestion, and the operational and financial controls of roadmap steps 5–6. |

## Repository layout

```
Transport/
├── Kenya_Transport_and_Logistics_Architecture.pdf  # Full architecture blueprint
├── .env.example                                     # Django environment template
├── backend/                                         # API server (Django + DRF)
│   ├── manage.py
│   ├── config/settings/  (base, development, production, test)
│   ├── requirements/     (base, development, production)
│   └── tools/            (courier_smoke.py — real-HTTP end-to-end check, unused_imports.py)
├── frontend/                                        # Client applications
│   └── src/  (api, context, pages, components, utils)
└── infra/                                           # Infrastructure definitions (planned)
```

## Planned architecture (from the blueprint)

- **App-first clients** – Flutter native apps for customers and drivers (offline sync, GPS tracking, push notifications, evidence capture). Web is reserved for staff/operator back-office dashboards.
- **Backend** – one codebase with explicit business modules (identity, catalog and pricing, passenger, goods and warehouse, fleet, dispatch, payments and ledger, tracking, support and reporting). Currently scaffolded as Django + Django REST Framework + SimpleJWT.
- **Data** – managed PostgreSQL (optional PostGIS), Redis cache, durable queue + workers, private object storage. PostgreSQL chosen and recorded in [`docs/decisions/0001-managed-postgresql.md`](./docs/decisions/0001-managed-postgresql.md); Supabase is the first host.
- **Integrations behind adapters** – M-PESA, maps, messaging, and later eTIMS, telematics, warehouse and accounting.
- **Core correctness requirements** – atomic seat inventory and offer assignment, transactional outbox, idempotent payments and callbacks, append-only ledger and custody history, offline-first driver app.

### Implementation roadmap

1. **Discovery and design** – operating model, service catalog, data rules, integration access.
2. **Shared foundation** – identity, organizations, fleet, pricing, orders, permissions, audit, payment adapter. *(implemented)*
3. **Passenger services** – scheduled booking, segment seats, manifests, rides, corporate approvals, charter. *(implemented: scheduled booking core, Envelope 2A on-demand rides, Envelope 2B corporate shuttles, Envelope 2C charter/hire)*
4. **Goods and hubs** – courier, packages, freight, line-haul, custody, warehouse, returns. *(Envelope 3A implemented: single-owner freight booking, manifest reconciliation, custody chain, evidence, exceptions, linked returns. Envelope 3B implemented: consolidated shared load, validated and versioned plans, the coverage-floor departure gate, backhaul opportunities and the empty-running measure. Envelope 3C implemented: mid-route insertion that preserves completed stops, holds to a configured delay tolerance, and the driver-offer and manifest-amendment paths. Envelope 3D implemented: local courier with one pickup and many parcels, priced per parcel on weight bands and proved per parcel. Intercity parcel hubs and warehouse inventory remain — see [`docs/phase3a-scope.md`](./docs/phase3a-scope.md), [`docs/phase3b-scope.md`](./docs/phase3b-scope.md), [`docs/phase3c-scope.md`](./docs/phase3c-scope.md) and [`docs/phase3d-scope.md`](./docs/phase3d-scope.md))*
5. **Operational and financial controls** – reconciliation, refunds, settlement, maintenance, incidents, reporting.
6. **Hardening and rollout** – load/security/restore tests, field pilot, gradual expansion.

Planning envelope: roughly 8–14 months with an experienced multidisciplinary team; a limited production pilot can arrive earlier.

## Getting started (backend)

Prerequisites: Python 3.12+, Django 6.x.

```bash
cd backend
pip install -r requirements/development.txt      # or --break-system-packages under PEP 668
cp ../.env.example .env                          # adjust values as needed
python manage.py migrate
python manage.py seed_dev                        # sample org, superuser, services, rate cards
python manage.py runserver
```

Dev settings are active by default (`config.settings.development`). SQLite is used unless `DB_ENGINE`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT` are set in the environment.

Run the test suite with the fast-hasher settings module:

```bash
python manage.py test --settings=config.settings.test
```

`config.settings.test` differs from development only in using a cheap password hasher; every test builds organizations, staff, drivers and senders, and PBKDF2 made the suite take minutes rather than seconds.

API docs are served at `/api/docs/` (DRF Spectacular).

Seeded login: superuser `+254700000001` / `admin-password-1`, operator `+254700000002` / `operator-password-1`. Change these before any non-local use.

### Phase 1 API surface

| Area | Endpoints |
|---|---|
| Auth | `POST /api/v1/auth/register/`, `POST /api/v1/auth/token/`, `GET /api/v1/me/` |
| Organizations | `/api/v1/organizations/`, `/api/v1/memberships/` |
| Fleet | `/api/v1/vehicles/`, `/api/v1/drivers/`, `/api/v1/documents/` |
| Catalog | `/api/v1/services/`, `/api/v1/places/`, `/api/v1/rate-cards/`, `/api/v1/quotes/` (with `/quotes/{id}/accept/`) |
| Orders | `/api/v1/orders/` (with `/orders/{id}/confirm/`) |
| Payments | `/api/v1/payments/`, `/api/v1/ledger/entries/`, `POST /api/v1/webhooks/mpesa/` |
| Audit | `/api/v1/audit/events/` |

### Phase 1 invariants enforced

- **Operator isolation**: every org-owned query is scoped to the caller's memberships.
- **Immutable accepted quotes** with the rate-card name snapshot preserved.
- **Idempotent payments**: one attempt per (provider, idempotency-key); wrong amount on replay rejected.
- **Callback dedup**: a repeated provider webhook cannot create a second balance effect.
- **Transactional outbox**: `publish_outbox --loop` relays committed events to subscribers.
- **Append-only ledger** with balanced double entries; **append-only audit trail** for every write.

### Phase 2 API surface (scheduled passenger booking)

| Area | Endpoints |
|---|---|
| Search | `GET /api/v1/booking/search/?from=&to=&date=` – available departures + per-leg seats |
| Hold | `POST /api/v1/booking/holds/` – atomic timed seat hold (one or many) |
| Checkout | `POST /api/v1/booking/checkout/` – held seats → pay-ready order |
| Boarding | `POST /api/v1/booking/board/` – scan ticket, mark boarded |
| Routes | `/api/v1/routes/`, `/api/v1/departures/` (with `/departures/{id}/substitute_vehicle/`) |
| Tickets | `/api/v1/tickets/` |

### Phase 2 invariants enforced

- **Segment-level seat inventory**: a seat is held against a departure *and* the occupied route segments, so Nairobi→Nakuru and Nakuru→Eldoret can reuse the same seat while overlapping reservations are rejected.
- **Atomic timed holds**: seat acquisition locks the departure row inside a transaction, so simultaneous clicks cannot both win the same seat.
- **Group bookings acquire together** or return a clear partial-availability response before payment.
- **Ticket issued only on successful payment**, in the same transaction as the ledger postings; a hold that lapsed first yields no ticket and queues a rebook/refund exception instead of silently keeping the fare.
- **Boarding checks** departure, ticket state and previous boarding (duplicate board rejected).
- **Vehicle substitution with fewer seats** creates a reassignment exception requiring customer communication — never a silent removal.
- **Operator isolation** on route/departure writes.

### Envelope 2A API surface (on-demand rides and dispatch)

| Area | Endpoints |
|---|---|
| Rides | `/api/v1/rides/` (`POST` request a ride, `/{id}/send-offers/` dispatch, `/{id}/fail/` failed matching) |
| Offers | `/api/v1/offers/` (driver-scoped, with `/{id}/accept/` and `/{id}/decline/`) |
| Trips | `/api/v1/trips/{id}/` with `arrive/`, `verify_pin/`, `complete/`, `change_destination/`, `accept_destination/`, `incident/` |
| Shifts / zones | `/api/v1/driver/shifts/`, `/api/v1/service-zones/` |

### Envelope 2A invariants enforced

- **Dispatch filters** approved drivers by availability (open shift), vehicle class, service zone **and** document status — an expired or unverified PSV licence removes a driver from the pool immediately.
- **Time-limited offers** to every eligible driver; `expire_offers` sweeps lapsed offers.
- **First valid acceptance wins** through an atomic, row-locked assignment; later or expired acceptances are rejected and the competing offers close.
- **Offer acceptance and trip start are separate events**: accepting creates a trip in `assigned` state, and only a verified trip PIN starts it.
- **Destination changes** require a revised estimate and explicit customer acceptance before the destination is updated.
- **Cancellations and no-shows** record reason, evidence and the applicable fee.
- **Failed matching** gives the customer an explicit retry / scheduled alternative / cancel outcome instead of silence.
- **Contact privacy**: a driver sees passenger contact details only for trips assigned to them.

### Envelope 2B API surface (corporate travel and shuttles)

| Area | Endpoints |
|---|---|
| Policy | `/api/v1/corporate/policy/` — permitted services, approval threshold, budget period |
| Cost centres | `/api/v1/corporate/cost-centers/` — budget, derived spend and remaining |
| Employees | `/api/v1/corporate/members/` (+ `/{id}/deactivate/`) |
| Travel requests | `/api/v1/corporate/requests/` (+ `/{id}/approve/`, `/{id}/reject/`) |
| Shuttles | `/api/v1/corporate/shuttle-templates/` (+ `/{id}/generate/`), `/api/v1/corporate/shuttle-runs/{id}/roster/`, `/unroster/` |

### Envelope 2B invariants enforced

- **Company-defined rules**: permitted services, approval threshold, cost centres and budgets gate every request; a request over budget is refused rather than silently overspending.
- **Approval before dispatch**: requests at or above the threshold stay `pending_approval` until a corporate travel manager decides; below-threshold requests auto-approve. Self-approval is refused, and employees get 403 on the decision endpoints.
- **Budgets are derived from history**, not a mutable counter, so spend cannot drift and a leaver cannot change what a cost centre already committed.
- **Schedule template → dated departures**: a template generates one real `booking.Departure` per matching weekday, with segments built so the seats are actually bookable. Generation is idempotent — a second run over the same range creates nothing.
- **Roster respects capacity** and rejects ineligible, duplicate or cross-company members.
- **Departing employee loses future access without losing history**: deactivation blocks new requests and cancels not-yet-departed roster entries, while past rosters, travel requests and orders are retained as evidence and invoices.
- **Corporate travel composes on the existing seat inventory** rather than duplicating it.

### Envelope 2C API surface (charter and vehicle hire)

| Area | Endpoints |
|---|---|
| Hire requests | `/api/v1/charter/requests/` (+ `/{id}/quote/`, `/confirm/`, `/deposit/`, `/extras/`, `/complete/`, `/cancel/`) |
| Availability | `GET /api/v1/charter/availability/?start_at=&end_at=&vehicle_class=` |
| Reservations | `/api/v1/charter/reservations/` |

### Envelope 2C invariants enforced

- **The hire request states the terms**: dates, itinerary, vehicle class, passenger count and included hours or distance — one of hours/distance is required.
- **Availability blocks a wider interval than the hire**: travel-to-pickup and turnaround are added around the window, so a crew cannot be promised to two hires that touch.
- **Both vehicle and driver are held** for the whole interval; a driver without verified, in-date documents is not offered.
- **Quote before confirm**: an unquoted request cannot be confirmed, and the vehicle must match the class, carry the passengers and be active.
- **Deposits accumulate** against the hire and are only accepted once it is confirmed.
- **Only permitted extras finalize** into the total; a non-permitted extra stays on record and is never charged.
- **Cancelling releases the reservation**, returning the interval to availability.

### Envelope 3A API surface (freight booking, manifest and custody)

| Area | Endpoints |
|---|---|
| Shipments | `POST /api/v1/shipments/quote/`, `POST /api/v1/shipments/`, `/{id}/custody/`, `/{id}/packages/`, `/{id}/evidence/`, `/{id}/exceptions/`, `/{id}/return/` |
| Packages | `/api/v1/packages/` with `POST /api/v1/packages/{id}/custody/` (append-only) |
| Trips | `/api/v1/freight-trips/` — with the remaining cargo capacity and per-compartment allowance |
| Manifests | `POST /api/v1/manifests/build/`, `/{id}/reconcile/`, `/{id}/release/` |
| Exceptions | `/api/v1/shipment-exceptions/{id}/resolve/` |

### Envelope 3A invariants enforced

- **The accepted quote and its basis are frozen onto the shipment**: weight, volume, declared value and measurement basis are snapshotted, and the agreed terms survive a later tariff change.
- **Expired or unaccepted quotes cannot be booked.**
- **Cargo capacity is independent of passenger capacity**: a 40-seat bus with no cargo allowance cannot carry freight, and seats are never read as kilograms. Cargo is its own ledger of weight, volume and compartments.
- **Compartment restrictions are enforced**: a fragile or hazardous package will not be placed in a compartment that does not accept it, and no compartment is loaded past its own limit.
- **The manifest is authoritative**: loading scans must reconcile against it before release, and a missing, unlisted or damaged line raises an exception instead of being silently accepted.
- **Release is gated**: an unreconciled manifest, an open discrepancy or an unresolved exception blocks the vehicle.
- **Custody history is append-only**, with device event time and server receipt time both recorded, so offline capture stays distinguishable. Corrections are new events that name the event they correct and must give a reason; there is no update or delete route at all, and branch staff cannot write custody.
- **A photo alone does not prove delivery**: delivery evidence requires recipient verification (OTP, signature, or a name with an identity check). Collection and delivery evidence are separate rows.
- **Partial delivery stays visible at package level**, and failed attempts carry an explicit next action: retry, hold at branch, return or claim.
- **A return is a linked reverse shipment** with its own legs, packages and custody history, leaving the original consignment and its chain intact.

### Envelope 3B API surface (consolidated shared load, coverage floor, backhaul)

| Area | Endpoints |
|---|---|
| Consolidation | `POST /api/v1/freight-trips/{id}/consolidation/propose/`, `GET /api/v1/freight-trips/{id}/consolidation/` |
| Plan versions | `POST /api/v1/consolidation-plans/{id}/validate/`, `/{id}/publish/`, `/{id}/supersede/` |
| Departure | `GET /api/v1/freight-trips/{id}/coverage/`, `POST /api/v1/freight-trips/{id}/depart/` |
| Backhaul | `GET /api/v1/backhaul/opportunities/` |
| Empty running | `GET /api/v1/reports/empty-running/` |

### Envelope 3B invariants enforced

- **Each shipper keeps its own frozen quote**: sharing a truck never moves anyone's price. See [`docs/phase3b-scope.md`](./docs/phase3b-scope.md).
- **The coverage floor is the product decision**: a trip departs only when the distinct shipments physically on its released manifest together cover `trip_cost_minor`. A shortfall blocks departure and is reported; nothing is apportioned or repriced.
- **The truck carries what the plan promised**: departure also requires a released manifest that matches the published plan, so a manifest or plan changed after sign-off cannot slip through.
- **A full truckload travels alone**: offering one to a consolidation keeps the shared consignments off, and validation refuses to publish a plan that smuggles a shared load in behind a full truckload.
- **Assignment is rule-based and transparent**: consignments are ordered by service window, then admitted only if they fit the vehicle's remaining weight, volume and the driver's working day. Everything that cannot be carried is recorded with its reason, never dropped.
- **Infeasibility is explained**: a trip that can carry nothing returns the reasons to dispatch rather than an empty plan.
- **Plan versions are saved, not edited**: every proposal is a new version pointing at the one it supersedes, publication requires a prior validation pass, and replacing a published plan demands a reason.
- **A departed trip is frozen**: once the driver starts, no replanning and no superseding; completed stops stay on record.
- **Empty running is the blueprint's measure**: distance without the defined revenue load divided by measured distance, over completed trips.

### Envelope 3C API surface (mid-route insertion, remaining-stop changes)

| Area | Endpoints |
|---|---|
| Route state | `GET /api/v1/freight-trips/{id}/route/` — current published version, completed and remaining stops, coverage |
| Dispatcher insertion | `POST /api/v1/freight-trips/{id}/insertion/` |
| Driver offers | `GET/POST /api/v1/insertion-requests/`, `POST /api/v1/freight-trips/{id}/insertion-requests/`, `POST /api/v1/insertion-requests/{id}/accept/`, `/{id}/decline/` |
| Stop progress | `POST /api/v1/consolidation-plans/{id}/stops/{stop_id}/complete/` |
| Manifest amendment | `POST /api/v1/manifests/{id}/amend/` — new manifest version for the mid-route pickup |

### Envelope 3C invariants enforced

- **A completed stop is immutable**: same place, same sequence, same window, still completed, in every later version. Insertion only touches what has not happened yet, and only the trip's own driver or a dispatcher may record that a stop is done.
- **Remaining stops keep their order**: the new stop is placed by its own service window; the stops already promised to other shippers are never silently reordered. See [`docs/phase3c-scope.md`](./docs/phase3c-scope.md).
- **Insertion runs inside a configured delay tolerance, never beyond it**: `Organization.insertion_grace_minutes` (default 30, capped at 240). Within it, the insertion publishes; beyond it, it is refused and the refusal names the stop that would be missed.
- **A dispatcher inserts directly, a driver accepts an offer**: first accept wins, in a transaction that locks the request row and re-checks capacity and the plan version, because a screen that was valid a minute ago is not proof. An offer only the assigned driver can answer, and only while the trip could still act on it.
- **The inserted consignment keeps its own frozen total** for the leg it will travel, quoted and accepted on the normal booking path. Nobody else is repriced, and coverage is re-reported with the new revenue.
- **Every insertion is a new plan version** pointing at the one it supersedes, with a change reason and a summary of what moved, by how much, and who approved it. The change is announced on the outbox and written to the audit trail.
- **The manifest is amended, not edited**: a mid-route pickup publishes a new manifest version that still requires reconciliation and re-release, and the previous released version stays readable.
- **Every refusal explains itself**, in the API response and on the stored request, so unassigned work stays visible.

### Envelope 3D API surface (local courier, one pickup and many recipients)

| Area | Endpoints |
|---|---|
| Courier quoting | `POST /api/v1/shipments/courier-quote/` — per-parcel breakdown against the resolved rate card |
| Courier booking | `POST /api/v1/shipments/` with a courier quote — one collection, N parcels |
| Rate cards | `/api/v1/rate-cards/` — now load-bearing rather than descriptive |
| Per-parcel delivery | `POST /api/v1/packages/{id}/deliver/` |
| Failed attempt | `POST /api/v1/packages/{id}/failed-attempt/`, or `POST /api/v1/shipments/{id}/exceptions/` with a `package` |
| Per-parcel evidence | `POST /api/v1/shipments/{id}/evidence/` with a `package` |
| Returns | `POST /api/v1/shipments/{id}/return/` with the parcels to reverse |

### Envelope 3D invariants enforced

- **The parcel is the delivery unit**: a courier order is one pickup and many parcels, and each parcel names its own recipient and drop place. The order-level consignee became optional for that reason, and freight still refuses a booking without one. A parcel missing its recipient or its place is refused by position, so a sender fixing a five-parcel order knows which one was wrong.
- **Each parcel is charged on chargeable weight** — the greater of its actual mass and its volumetric weight at 4000 cm³/kg — against a weight-band rate card, and the basis that priced it is frozen on the parcel at booking.
- **A parcel outside every band is refused, not priced at the nearest band**, and an account with no card in force is refused with an explanation rather than quoted at zero. Guessing a price is how a courier company loses money quietly.
- **The account's own rate card wins** over the platform card, and a later rate-card edit changes neither an accepted quote nor a booked order's money.
- **The order total is exactly the sum of the per-parcel charges**, so the two can never disagree.
- **The plan, not the trip, sequences the drops**: a stop for every distinct parcel place, and a route of drops that will not fit the trip window is refused naming the overrun. Freight keeps its single-destination gate.
- **Passenger and goods operations stay separate**: goods work is refused on any trip whose service is a passenger service, so a parcel route can never ride a bus's published timetable.
- **Delivery is proved per parcel, to the named recipient**: a photo alone is refused, and so is proof from anyone other than that parcel's recipient. The check is on the name, not on the presence of a signature.
- **Partial delivery stays visible**: delivering one parcel moves the order to `partially_delivered` with the rest outstanding; only when every parcel is settled does the order close. A failed attempt raises an exception against the parcel and leaves it undelivered.
- **A failed parcel can be returned on its own**, collected from that parcel's own drop rather than the order's destination, with the rest of the order left alone. The failed-attempt exception that sent it back is closed by the return — a damage claim on the same parcel is a separate decision and stays open — and the original order's status is re-derived, so it does not sit at `exception` waiting on a problem already answered.
- **A multi-drop courier order is refused by name as a mid-route insertion.** 3C inserts one consignment with one delivery end, so inserting a multi-drop order would publish a plan carrying only its destination. It is planned by the proposal path instead, before the truck leaves.
- **The trip's own driver may record a parcel's delivery or failed attempt**, exactly as 3C lets a trip's driver complete its own stops; a driver of another trip is refused.
- **Money is quoted and frozen here, collected in step 5**: prepaid, cash on delivery and account billing need an order-to-shipment link and remain roadmap step 5's work.

## Environment variables

See [`.env.example`](./.env.example) for the full template. Copy it to `.env` at
the repository root; `manage.py` reads the root file first and `backend/.env`
second, so a machine-local override still works.

- `DJANGO_SECRET_KEY`, `DJANGO_DEBUG`, `DJANGO_ALLOWED_HOSTS`
- `FRONTEND_URL`, `CORS_ALLOWED_ORIGINS`, `CSRF_TRUSTED_ORIGINS`
- Database overrides: `DB_ENGINE`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`, `DB_CONN_MAX_AGE`, `DB_CONNECT_TIMEOUT`, `DB_SSLMODE`

Never commit real secrets; `.env` is git-ignored.

Omit `DB_ENGINE` and the backend runs on a local SQLite file. Set it and it runs
on PostgreSQL.

### Connecting to Supabase

Use the **session pooler** on port 5432, not the direct host and not the
transaction pooler:

- The direct host `db.<ref>.supabase.co` resolves to an IPv6-only address in
  several regions and fails with `Network is unreachable` from a machine without
  an IPv6 route. `python3 tools/db_probe.py <host>` reports the address family
  and whether anything answers, without authenticating.
- The transaction pooler (port 6543) does not support the prepared statements
  Django and psycopg use, so it breaks transactions rather than merely being
  slower.
- `DB_SSLMODE` defaults to `require`, because psycopg's own default (`prefer`)
  will happily accept a plaintext server.

### Running the tests

The suite always runs on an in-memory SQLite database, whatever `DB_ENGINE` says.
`config/settings/test.py` pins the engine deliberately: with production
credentials in `.env`, the test runner would otherwise create `test_postgres` on
the hosted database. `apps/common/tests/test_settings.py` fails if that pin is
ever dropped.

```bash
cd backend
python3 -m virtualenv .venv          # or: python3 -m venv .venv
.venv/bin/python -m pip install -r requirements/development.txt
.venv/bin/python manage.py test --settings=config.settings.test
.venv/bin/python tools/courier_smoke.py    # the courier loop over real HTTP
```

## Contributing

This is the working repository for the Kenya transport platform. Before implementing a feature, read the corresponding sections of the architecture PDF and confirm the scope with the product owner.