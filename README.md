# Kenya Transport and Logistics Platform

A unified transport marketplace and operations platform for Kenya covering passenger transport (scheduled buses and shuttles, on-demand rides, corporate and charter) and goods transport (courier, intercity parcels, freight, warehousing, returns and claims) on one shared business foundation: identity, fleet, pricing, payments, tracking and finance.

The complete product and technical architecture is defined in [`Kenya_Transport_and_Logistics_Architecture.pdf`](./Kenya_Transport_and_Logistics_Architecture.pdf) (28 pages, v1.0, 18 September 2026). This README documents the current repository state and how to work on it.

## Status

Phase 1 (shared foundation) and all of Phase 2 (scheduled booking, on-demand rides, corporate shuttles and charter/hire) are implemented and green:

| Component | State |
|---|---|
| `backend/` | Django + DRF API running on `/api/v1/`. 11 apps: `common` (outbox), `accounts` (identity/orgs/permissions), `audit`, `fleet`, `catalog` (pricing/quotes), `orders`, `booking` (routes/departures/segment seats/holds/tickets/boarding), `rides` (ride requests/dispatch/offers/trips/incidents), `corporate` (policies/cost centres/employees/travel requests/shuttles), `charter` (hire requests/vehicle+driver reservations/deposits/extras), `payments` (M-PESA adapter, ledger). |
| `frontend/` | Empty React-style directory tree (auth, driver, dispatch, parent pages; api, context, components, utils). No source files. |
| `infra/` | Empty. |
| Tests | 133 passing: operator isolation, payment idempotency/callback dedup, outbox atomicity, segment seat inventory, atomic holds, ticket-on-payment, boarding, driver eligibility, first-accept-wins assignment, trip PIN, destination change, failed matching, contact privacy, corporate eligibility/approval/budget/shuttles/roster/departure, charter quoting/interval reservation/travel+turnaround availability/deposits/permitted extras. |
| Blueprint | Full architecture in the PDF. Phase 1 and Phase 2 are delivered, completing §7 and §8 (scheduled booking, on-demand rides, corporate travel, recurring shuttles, charter and vehicle hire). The goods/freight track (§9–10) is next — see [`docs/phase3a-scope.md`](./docs/phase3a-scope.md). |

## Repository layout

```
Transport/
├── Kenya_Transport_and_Logistics_Architecture.pdf  # Full architecture blueprint
├── .env.example                                     # Django environment template
├── backend/                                         # API server (Django + DRF)
│   ├── manage.py
│   ├── config/settings/  (base, development, production)
│   └── requirements/     (base, development, production)
├── frontend/                                        # Client applications
│   └── src/  (api, context, pages, components, utils)
└── infra/                                           # Infrastructure definitions (planned)
```

## Planned architecture (from the blueprint)

- **App-first clients** – Flutter native apps for customers and drivers (offline sync, GPS tracking, push notifications, evidence capture). Web is reserved for staff/operator back-office dashboards.
- **Backend** – one codebase with explicit business modules (identity, catalog and pricing, passenger, goods and warehouse, fleet, dispatch, payments and ledger, tracking, support and reporting). Currently scaffolded as Django + Django REST Framework + SimpleJWT.
- **Data** – managed PostgreSQL (optional PostGIS), Redis cache, durable queue + workers, private object storage.
- **Integrations behind adapters** – M-PESA, maps, messaging, and later eTIMS, telematics, warehouse and accounting.
- **Core correctness requirements** – atomic seat inventory and offer assignment, transactional outbox, idempotent payments and callbacks, append-only ledger and custody history, offline-first driver app.

### Implementation roadmap

1. **Discovery and design** – operating model, service catalog, data rules, integration access.
2. **Shared foundation** – identity, organizations, fleet, pricing, orders, permissions, audit, payment adapter. *(implemented)*
3. **Passenger services** – scheduled booking, segment seats, manifests, rides, corporate approvals, charter. *(scheduled booking core, Envelope 2A on-demand rides and Envelope 2B corporate shuttles implemented; charter/hire next)*
4. **Goods and hubs** – courier, packages, freight, line-haul, custody, warehouse, returns.
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

## Environment variables

See [`.env.example`](./.env.example) for the full template:

- `DJANGO_SECRET_KEY`, `DJANGO_DEBUG`, `DJANGO_ALLOWED_HOSTS`
- `FRONTEND_URL`, `CORS_ALLOWED_ORIGINS`, `CSRF_TRUSTED_ORIGINS`
- Database overrides: `DB_ENGINE`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`

Never commit real secrets; `.env` is git-ignored.

## Contributing

This is the working repository for the Kenya transport platform. Before implementing a feature, read the corresponding sections of the architecture PDF and confirm the scope with the product owner.