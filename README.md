# Transport and Logistics Platform

A unified transport marketplace and operations platform for Kenya covering passenger transport (scheduled buses and shuttles, on-demand rides, corporate and charter) and goods transport (courier, intercity parcels, freight, warehousing, returns and claims) on one shared business foundation: identity, fleet, pricing, payments, tracking and finance.

The complete product and technical architecture is defined in [`Kenya_Transport_and_Logistics_Architecture.pdf`](./Kenya_Transport_and_Logistics_Architecture.pdf) (28 pages, v1.0, 18 September 2026). This README documents the current repository state and how to work on it.

## Status

Phase 1 (shared foundation), all of Phase 2 (scheduled booking, on-demand rides, corporate shuttles and charter/hire) and goods Envelopes 3A, 3B, 3C, 3D and 4A (booking and custody, consolidated shared load with the coverage floor and backhaul, mid-route insertion, local courier, then intercity parcels routed through hubs) are implemented and green:

| Component     | State                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| ------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `backend/`  | Django + DRF API running on `/api/v1/`. 12 apps: `common` (outbox), `accounts` (identity/orgs/permissions), `audit`, `fleet` (vehicles/drivers/documents/cargo compartments), `catalog` (services/places/rate cards/quotes), `orders`, `booking` (routes/departures/segment seats/holds/tickets/boarding), `rides` (ride requests/dispatch/offers/trips/incidents), `corporate` (policies/cost centres/employees/travel requests/shuttles), `charter` (hire requests/vehicle+driver reservations/deposits/extras), `goods` (shipments/packages/legs/trips/manifests/custody/evidence/exceptions/returns), `payments` (M-PESA adapter, ledger).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| `frontend/` | React + TypeScript + Vite operator back-office console: authenticated shell, counter ticket sales with a branch selector and vehicle seat map, sale documents, dispatch board, staff, fleet, routes, organisations and branch administration. Customer, driver and parent apps are not started.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| `infra/`    | Empty.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| Tests         | 560 passing, 2 skipped: operator isolation, payment idempotency/callback dedup, outbox atomicity, segment seat inventory, atomic holds, ticket-on-payment, boarding, driver eligibility, first-accept-wins assignment, trip PIN, destination change, failed matching, contact privacy, corporate eligibility/approval/budget/shuttles/roster/departure, charter quoting/interval reservation/travel+turnaround availability/deposits/permitted extras, freight quote basis/terms snapshot, cargo capacity independent of seats, per-compartment limits, manifest reconciliation and release gating, append-only custody with corrections, delivery evidence verification, exceptions with next actions, linked returns, shared-load consolidation with explained rejections, plan validation/versioning/publication, the coverage-floor departure gate, backhaul opportunities, the empty-running measure, mid-route insertion preserving completed stops, delay tolerance refusals, driver-offer version checks, manifest amendment as a new version, contiguous stop numbering across insertions, frozen prices under insertion, the nineteen courier invariants (per-parcel weight-band pricing, account rate-card resolution, per-parcel delivery proof, partial delivery, returns after a failed attempt, the mid-route insertion gate, and driver handovers), row-locked fleet reservation so two dispatchers cannot double-book a vehicle or driver, per-compartment volume as well as weight, the payment provider seam (a real Safaricom Daraja adapter with OAuth and STK push that falls back to a credential-free stub, plus amount and state validation on the callback), the append-only double-entry ledger, revenue recognition when the transport obligation is fulfilled, refunds that reverse through the ledger and can never be driven negative, and the fourteen intercity-parcel invariants (PARCEL services refused by courier, a hub booked as a leg, origin-scan custody, departure and arrival custody, missing and unmanifested arrivals as exceptions, hub holds that block onward movement and release, the hub hold query, and append-only corrections), plus branch CRUD and head-office rules, cross-branch ticket visibility, branch-scoped fleet and staff, and cash collection. |
| Blueprint     | Full architecture in the PDF. Phase 1, Phase 2 (§7–8) and Envelopes 3A–3D and 4A of §9–10 are delivered. Remaining: warehouse inventory, returns/claims depth, telemetry ingestion, and the operational and financial controls of roadmap steps 5–6.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |

## Repository layout

```
Transport/
├── Kenya_Transport_and_Logistics_Architecture.pdf  # Full architecture blueprint
├── .env.example                                     # Django environment template
├── backend/                                         # API server (Django + DRF)
│   ├── manage.py
│   ├── config/settings/  (base, development, production, test, test_postgres)
│   ├── requirements/     (base, development, production)
│   └── tools/            (courier_smoke.py — real-HTTP end-to-end check; mpesa_stk_push.py — live Daraja Stage A; unused_imports.py)
├── frontend/                                        # Client applications
│   └── src/  (api, context, components, pages, styles)
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
4. **Goods and hubs** – courier, packages, freight, line-haul, custody, warehouse, returns. *(Envelope 3A implemented: single-owner freight booking, manifest reconciliation, custody chain, evidence, exceptions, linked returns. Envelope 3B implemented: consolidated shared load, validated and versioned plans, the coverage-floor departure gate, backhaul opportunities and the empty-running measure. Envelope 3C implemented: mid-route insertion that preserves completed stops, holds to a configured delay tolerance, and the driver-offer and manifest-amendment paths. Envelope 3D implemented: local courier with one pickup and many parcels, priced per parcel on weight bands and proved per parcel. Envelope 4A implemented: intercity parcels booked through a hub chain and carried by line-hauls, with the custody events between booking and delivery that the courier envelope refused to build. Warehouse inventory remains — see [`docs/phase3a-scope.md`](./docs/phase3a-scope.md), [`docs/phase3b-scope.md`](./docs/phase3b-scope.md), [`docs/phase3c-scope.md`](./docs/phase3c-scope.md), [`docs/phase3d-scope.md`](./docs/phase3d-scope.md) and [`docs/phase4a-scope.md`](./docs/phase4a-scope.md))*
5. **Operational and financial controls** – reconciliation, refunds, settlement, maintenance, incidents, reporting. *(partially implemented: a payment provider seam with a real Safaricom Daraja adapter, an append-only double-entry ledger, revenue recognition when the obligation is fulfilled, and refunds that reverse through the ledger. Reconciliation, settlement, maintenance, incidents and reporting remain.)*
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

`config.settings.test` pins the database to SQLite and uses a cheap password hasher; every test builds organizations, staff, drivers and senders, and PBKDF2 made the suite take minutes rather than seconds. The SQLite pin also stops a developer with production credentials in `.env` from running the suite against the hosted database.

API docs are served at `/api/docs/` (DRF Spectacular).

Seeded login: superuser `+254700000001` / `admin-password-1`, operator `+254700000002` / `operator-password-1`. Change these before any non-local use.

### Phase 1 API surface

| Area          | Endpoints                                                                                                                |
| ------------- | ------------------------------------------------------------------------------------------------------------------------ |
| Auth          | `POST /api/v1/auth/register/`, `POST /api/v1/auth/token/`, `GET /api/v1/me/`                                       |
| Organizations | `/api/v1/organizations/`, `/api/v1/memberships/`                                                                     |
| Fleet         | `/api/v1/vehicles/`, `/api/v1/drivers/`, `/api/v1/documents/`                                                      |
| Catalog       | `/api/v1/services/`, `/api/v1/places/`, `/api/v1/rate-cards/`, `/api/v1/quotes/` (with `/quotes/{id}/accept/`) |
| Orders        | `/api/v1/orders/` (with `/orders/{id}/confirm/`)                                                                     |
| Payments      | `/api/v1/payments/`, `POST /api/v1/payments/cash/`, `/api/v1/ledger/entries/`, `POST /api/v1/webhooks/mpesa/` |
| Audit         | `/api/v1/audit/events/`                                                                                                |

### Phase 1 invariants enforced

- **Operator isolation**: every org-owned query is scoped to the caller's memberships.
- **Immutable accepted quotes** with the rate-card name snapshot preserved.
- **Idempotent payments**: one attempt per (provider, idempotency-key); wrong amount on replay rejected.
- **Callback dedup**: a repeated provider webhook cannot create a second balance effect.
- **Transactional outbox**: `publish_outbox --loop` relays committed events to subscribers.
- **Append-only ledger** with balanced double entries; **append-only audit trail** for every write.

### Phase 2 API surface (scheduled passenger booking)

| Area     | Endpoints                                                                                    |
| -------- | -------------------------------------------------------------------------------------------- |
| Search   | `GET /api/v1/booking/search/?from=&to=&date=` – available departures + per-leg seats      |
| Hold     | `POST /api/v1/booking/holds/` – atomic timed seat hold (one or many)                      |
| Checkout | `POST /api/v1/booking/checkout/` – held seats → pay-ready order                          |
| Boarding | `POST /api/v1/booking/board/` – scan ticket, mark boarded                                 |
| Routes   | `/api/v1/routes/`, `/api/v1/departures/` (with `/departures/{id}/substitute_vehicle/`) |
| Tickets  | `/api/v1/tickets/`                                                                         |

### Phase 2 invariants enforced

- **Segment-level seat inventory**: a seat is held against a departure *and* the occupied route segments, so Nairobi→Nakuru and Nakuru→Eldoret can reuse the same seat while overlapping reservations are rejected.
- **Atomic timed holds**: seat acquisition locks the departure row inside a transaction, so simultaneous clicks cannot both win the same seat.
- **Group bookings acquire together** or return a clear partial-availability response before payment.
- **Ticket issued only on successful payment**, in the same transaction as the ledger postings; a hold that lapsed first yields no ticket and queues a rebook/refund exception instead of silently keeping the fare.
- **Boarding checks** departure, ticket state and previous boarding (duplicate board rejected).
- **Vehicle substitution with fewer seats** creates a reassignment exception requiring customer communication — never a silent removal.
- **Operator isolation** on route/departure writes.

### Back-office, branches and counter sales

The React console at `frontend/` is the staff-facing back office. It is not a
customer app: counter staff sell seats and intake parcels, dispatchers track
runs, managers administer staff and fleet, and administrators configure
organisations and branches.

| Area      | Endpoints                                                                                                   |
| --------- | ----------------------------------------------------------------------------------------------------------- |
| Branches  | `/api/v1/branches/` – organisation-scoped list/retrieve; administrators create and edit, including head office |
| Staff     | `/api/v1/staff/` – hire, update and offboard branch-scoped staff                                            |
| Ticket    | `/api/v1/booking/search/`, `/holds/`, `/checkout/`, `/tickets/` with `selling_branch`                       |
| Cash      | `POST /api/v1/payments/cash/` – record a counter cash collection and issue the ticket                        |
| Dispatch  | `/api/v1/departures/` – branch-filtered departures, vehicle and driver readiness used by the board              |

Rules that are enforced:

- **A branch is an operating unit, not a map stop.** Scheduled buses, ticket
  sales, staff, vehicles and drivers belong to an `accounts.Branch`; stages,
  hubs and landmarks remain `catalog.Place` rows. Company setup no longer
  creates operating branches.
- **One head office per active organisation.** An organisation with one active
  branch may leave it as the head office; once it has more than one, one branch
  must be head office, and promoting a branch demotes the previous one.
- **Membership uniqueness respects scope.** A person can hold a head-office
  membership plus branch-scoped memberships, but cannot hold the same role
  twice at the same branch.
- **Selling branch and operating branch are separate.** A ticket may be sold by
  one branch for a departure operated by another; the sale, ticket documents
  and cash attempt record the selling branch while seat inventory stays with
  the departure.
- **Visibility follows the role, not just the organisation.** Branch staff see
  their branch's sales plus their own customer tickets; the operations view
  filters by operating branch. Operators with mixed roles do not inherit
  network-wide access in an organisation where they are only branch staff.
- **Fleet reads are branch-scoped.** Branch managers see their branch's
  vehicles plus shared vehicles; they cannot see or manage another branch's
  fleet.
- **The counter seat map is the only vehicle visual.** It is a 2D seat selector
  at Counter → Issue ticket once a departure and boarding/alighting stops are
  chosen. There is no 3D bus simulator in the current build.

### Envelope 2A API surface (on-demand rides and dispatch)

| Area           | Endpoints                                                                                                                                 |
| -------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| Rides          | `/api/v1/rides/` (`POST` request a ride, `/{id}/send-offers/` dispatch, `/{id}/fail/` failed matching)                            |
| Offers         | `/api/v1/offers/` (driver-scoped, with `/{id}/accept/` and `/{id}/decline/`)                                                        |
| Trips          | `/api/v1/trips/{id}/` with `arrive/`, `verify_pin/`, `complete/`, `change_destination/`, `accept_destination/`, `incident/` |
| Shifts / zones | `/api/v1/driver/shifts/`, `/api/v1/service-zones/`                                                                                    |

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

| Area            | Endpoints                                                                                                                         |
| --------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| Policy          | `/api/v1/corporate/policy/` — permitted services, approval threshold, budget period                                            |
| Cost centres    | `/api/v1/corporate/cost-centers/` — budget, derived spend and remaining                                                        |
| Employees       | `/api/v1/corporate/members/` (+ `/{id}/deactivate/`)                                                                          |
| Travel requests | `/api/v1/corporate/requests/` (+ `/{id}/approve/`, `/{id}/reject/`)                                                         |
| Shuttles        | `/api/v1/corporate/shuttle-templates/` (+ `/{id}/generate/`), `/api/v1/corporate/shuttle-runs/{id}/roster/`, `/unroster/` |

### Envelope 2B invariants enforced

- **Company-defined rules**: permitted services, approval threshold, cost centres and budgets gate every request; a request over budget is refused rather than silently overspending.
- **Approval before dispatch**: requests at or above the threshold stay `pending_approval` until a corporate travel manager decides; below-threshold requests auto-approve. Self-approval is refused, and employees get 403 on the decision endpoints.
- **Budgets are derived from history**, not a mutable counter, so spend cannot drift and a leaver cannot change what a cost centre already committed.
- **Schedule template → dated departures**: a template generates one real `booking.Departure` per matching weekday, with segments built so the seats are actually bookable. Generation is idempotent — a second run over the same range creates nothing.
- **Roster respects capacity** and rejects ineligible, duplicate or cross-company members.
- **Departing employee loses future access without losing history**: deactivation blocks new requests and cancels not-yet-departed roster entries, while past rosters, travel requests and orders are retained as evidence and invoices.
- **Corporate travel composes on the existing seat inventory** rather than duplicating it.

### Envelope 2C API surface (charter and vehicle hire)

| Area          | Endpoints                                                                                                                    |
| ------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| Hire requests | `/api/v1/charter/requests/` (+ `/{id}/quote/`, `/confirm/`, `/deposit/`, `/extras/`, `/complete/`, `/cancel/`) |
| Availability  | `GET /api/v1/charter/availability/?start_at=&end_at=&vehicle_class=`                                                       |
| Reservations  | `/api/v1/charter/reservations/`                                                                                            |

### Envelope 2C invariants enforced

- **The hire request states the terms**: dates, itinerary, vehicle class, passenger count and included hours or distance — one of hours/distance is required.
- **Availability blocks a wider interval than the hire**: travel-to-pickup and turnaround are added around the window, so a crew cannot be promised to two hires that touch.
- **Both vehicle and driver are held** for the whole interval; a driver without verified, in-date documents is not offered.
- **Quote before confirm**: an unquoted request cannot be confirmed, and the vehicle must match the class, carry the passengers and be active.
- **Deposits accumulate** against the hire and are only accepted once it is confirmed.
- **Only permitted extras finalize** into the total; a non-permitted extra stays on record and is never charged.
- **Cancelling releases the reservation**, returning the interval to availability.

### Envelope 3A API surface (freight booking, manifest and custody)

| Area       | Endpoints                                                                                                                                                              |
| ---------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Shipments  | `POST /api/v1/shipments/quote/`, `POST /api/v1/shipments/`, `/{id}/custody/`, `/{id}/packages/`, `/{id}/evidence/`, `/{id}/exceptions/`, `/{id}/return/` |
| Packages   | `/api/v1/packages/` with `POST /api/v1/packages/{id}/custody/` (append-only)                                                                                       |
| Trips      | `/api/v1/freight-trips/` — with the remaining cargo capacity and per-compartment allowance                                                                          |
| Manifests  | `POST /api/v1/manifests/build/`, `/{id}/reconcile/`, `/{id}/release/`                                                                                            |
| Exceptions | `/api/v1/shipment-exceptions/{id}/resolve/`                                                                                                                          |

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

| Area          | Endpoints                                                                                                     |
| ------------- | ------------------------------------------------------------------------------------------------------------- |
| Consolidation | `POST /api/v1/freight-trips/{id}/consolidation/propose/`, `GET /api/v1/freight-trips/{id}/consolidation/` |
| Plan versions | `POST /api/v1/consolidation-plans/{id}/validate/`, `/{id}/publish/`, `/{id}/supersede/`                 |
| Departure     | `GET /api/v1/freight-trips/{id}/coverage/`, `POST /api/v1/freight-trips/{id}/depart/`                     |
| Backhaul      | `GET /api/v1/backhaul/opportunities/`                                                                       |
| Empty running | `GET /api/v1/reports/empty-running/`                                                                        |

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

| Area                 | Endpoints                                                                                                                                                               |
| -------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Route state          | `GET /api/v1/freight-trips/{id}/route/` — current published version, completed and remaining stops, coverage                                                         |
| Dispatcher insertion | `POST /api/v1/freight-trips/{id}/insertion/`                                                                                                                          |
| Driver offers        | `GET/POST /api/v1/insertion-requests/`, `POST /api/v1/freight-trips/{id}/insertion-requests/`, `POST /api/v1/insertion-requests/{id}/accept/`, `/{id}/decline/` |
| Stop progress        | `POST /api/v1/consolidation-plans/{id}/stops/{stop_id}/complete/`                                                                                                     |
| Manifest amendment   | `POST /api/v1/manifests/{id}/amend/` — new manifest version for the mid-route pickup                                                                                 |

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

| Area                | Endpoints                                                                                                         |
| ------------------- | ----------------------------------------------------------------------------------------------------------------- |
| Courier quoting     | `POST /api/v1/shipments/courier-quote/` — per-parcel breakdown against the resolved rate card                  |
| Courier booking     | `POST /api/v1/shipments/` with a courier quote — one collection, N parcels                                     |
| Rate cards          | `/api/v1/rate-cards/` — now load-bearing rather than descriptive                                               |
| Per-parcel delivery | `POST /api/v1/packages/{id}/deliver/`                                                                           |
| Failed attempt      | `POST /api/v1/packages/{id}/failed-attempt/`, or `POST /api/v1/shipments/{id}/exceptions/` with a `package` |
| Per-parcel evidence | `POST /api/v1/shipments/{id}/evidence/` with a `package`                                                      |
| Returns             | `POST /api/v1/shipments/{id}/return/` with the parcels to reverse                                               |

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

### Envelope 4A (intercity parcels and hubs)

3D made the parcel the delivery unit for a van crossing town and refused, by name, anything typed `Service.ServiceType.PARCEL`. 4A is the envelope that refusal pointed at: a parcel from Kisumu to Nyeri is dropped at a branch, consolidated onto a line-haul to a hub, held while the onward truck is found, and released at a destination branch.

It adds no models. A hub is a `catalog.Place` with `PlaceType.HUB`; a hub chain is `ShipmentLeg` rows whose stops are those places; the line-haul between two hubs is an ordinary `FreightTrip` with an ordinary `Manifest`, reconciled and released by the functions 3A already wrote. What it adds is the part nobody had built — the custody events between booking and delivery. The chain recorded exactly three things (booked, delivered, returned), so a parcel could be scanned onto a truck, carried two hundred kilometres and handed to a stranger without a single row being written. `ARRIVED`, `HELD` and `DEPARTED` existed as enum values and were written by nothing. They are written here.

The work lives in [`backend/apps/goods/parcels.py`](./backend/apps/goods/parcels.py), beside `courier.py`, and is under test in [`backend/apps/goods/tests/test_parcels.py`](./backend/apps/goods/tests/test_parcels.py). It is a service layer for now: the API surface for hub scans arrives with the dispatch console in step 5.

### Envelope 4A invariants enforced

- **A PARCEL service is priced by the parcel path and still refused by courier.** 3D's refusal is kept by name; 4A answers it with its own quote and booking path rather than by loosening 3D's gate.
- **A hub is an ordinary place of type hub or branch**, so a sender cannot route a parcel through somebody's doorstep, and a chain is refused if it repeats a place — a parcel must not wait for itself.
- **The hub chain is booked as legs at booking time**, not derived when a truck is found: collection, every hub in order, then the destination. A sender is entitled to know the road their parcel will take before anyone plans it.
- **Scanning a parcel onto a line-haul leaves a LOADED custody event** and takes the origin hub as its current location — the fact "this parcel is sitting on the Nairobi truck", written rather than inferred.
- **Departing a line-haul leaves a DEPARTED event for every parcel on the released manifest**, and moves those consignments off `booked` onto `in_transit`.
- **Arriving at a hub leaves an ARRIVED event and reconciles what actually came in**, using the branch receipt reconciliation 3A already wrote.
- **A parcel that never arrives is held and excepted**, not left to vanish: it is marked `missing`, given a HELD event at the receiving hub and an open `missing` exception with `hold_branch` as its next action.
- **A parcel that arrives unmanifested is an EXTRA line and an exception**, held at the hub and never pushed onward.
- **A damaged arrival is a claim**: the parcel is marked `damaged` and given an open `damaged` exception whose next action is `claim`.
- **Every parcel at a hub carries a HELD event**, so "held at this hub, and why" is a query rather than an inference, and the hold clears only when the parcel is loaded onward, delivered or returned.
- **An open exception stops a parcel moving onward** — `place_onward_load` refuses it, and resolving the exception is what lets it go.
- **Release reuses courier's delivery proof unchanged**, adding two hub rules: the parcel must be standing at the place it was addressed to, and it must not carry an open exception. A photo alone is still refused.
- **The line-haul departure gate is not bypassed.** `record_departure` runs the same `confirm_departure` 3B wrote — a released reconciled manifest, a published plan, the two agreeing, and committed revenue covering the trip cost with each shipment counted once. 4A only adds the custody rows that were missing.
- **Hub scans are guarded by the operator's own membership**, not the Django staff flag: a membership with a driver, branch-staff, dispatcher or administrator role in that operator, and nobody else, may record a movement.
- **Custody stays append-only.** A correction is a new `correction` event naming the row it corrects and why; no custody row is updated or deleted, ever.

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

Always keep `--settings=config.settings.test`: it pins the database to
in-memory SQLite **and** removes live `MPESA_*` values loaded from `.env`. Running
without it can select the real Daraja adapter and turn offline payment tests
into a live subscriber request.

### M-PESA STK push checks

The Daraja adapter is tested offline by injecting a recording HTTP session; no
money and no phone prompt are involved:

```bash
cd backend
.venv/bin/python manage.py test \
  apps.payments.tests.test_provider.RequestBuildingTestCase \
  apps.payments.tests.test_provider.CredentialSelectionTestCase \
  --settings=config.settings.test
.venv/bin/python manage.py test apps.payments --settings=config.settings.test
```

The first command checks OAuth, STK payload fields, shilling conversion, phone
normalisation and token reuse. The second runs the full payment, cash and ledger
suite. A live Stage A push is available only when real sandbox credentials and a
public HTTPS callback are configured:

```bash
cd backend
.venv/bin/python tools/mpesa_stk_push.py --phone 07... --amount 1
```

This sends a real STK prompt to that phone and exits 0 when Daraja accepts it.
Safaricom cannot call back to localhost, so the callback half needs a public
HTTPS tunnel or deployment.

## Getting started (web back-office)

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173/` with the API running on
`http://localhost:8000/`. The seeded operator account is
`+254700000002` / `operator-password-1`; the platform administrator is
`+254700000001` / `admin-password-1`.

The staff console covers:

- **Counter** – choose a selling branch, search scheduled departures, select a
  2D vehicle seat map, sell a ticket, take cash or M-PESA, and print/issue the
  ticket and receipt.
- **Dispatch** – filter the day's departures by branch and route, and see
  unassigned, assigned, and in-transit runs.
- **Branches** – list/edit branches, set the head office, and see branch
  staffing, fleet, routes and counter-sales counts.
- **People** – hire and offboard branch staff and managers.
- **Fleet** – administrators create vehicles; branch managers get read-only,
  branch-scoped visibility.
- **Routes and setup** – create routes, departures, organisations, stages,
  hubs and landmarks.

There is **no 3D bus simulator**. The vehicle visual is the 2D seat map in
Counter → Issue ticket, shown after a departure and boarding/alighting stops are
selected. A full bus-cabin simulation would be a separate front-end feature.

## Contributing

This is the working repository for the Kenya transport platform. Before implementing a feature, read the corresponding sections of the architecture PDF and confirm the scope with the product owner.
