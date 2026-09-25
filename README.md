# Kenya Transport and Logistics Platform

A unified transport marketplace and operations platform for Kenya covering passenger transport (scheduled buses and shuttles, on-demand rides, corporate and charter) and goods transport (courier, intercity parcels, freight, warehousing, returns and claims) on one shared business foundation: identity, fleet, pricing, payments, tracking and finance.

The complete product and technical architecture is defined in [`Kenya_Transport_and_Logistics_Architecture.pdf`](./Kenya_Transport_and_Logistics_Architecture.pdf) (28 pages, v1.0, 18 September 2026). This README documents the current repository state and how to work on it.

## Status

Phase 1 (shared foundation) and the core of Phase 2 (scheduled passenger booking) are implemented and green:

| Component | State |
|---|---|
| `backend/` | Django + DRF API running on `/api/v1/`. 8 apps: `common` (outbox), `accounts` (identity/orgs/permissions), `audit`, `fleet`, `catalog` (pricing/quotes), `orders`, `booking` (routes/departures/segment seats/holds/tickets/boarding), `payments` (M-PESA adapter, ledger). |
| `frontend/` | Empty React-style directory tree (auth, driver, dispatch, parent pages; api, context, components, utils). No source files. |
| `infra/` | Empty. |
| Tests | 48 passing: operator isolation, payment idempotency/callback dedup, outbox atomicity, segment seat inventory, atomic holds, ticket-on-payment, boarding. |
| Blueprint | Full architecture in the PDF; Phase 1 delivered, Phase 2 scheduled-booking core delivered (see [`docs/phase2-scope.md`](./docs/phase2-scope.md)). On-demand rides, corporate shuttles and goods are next. |

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
3. **Passenger services** – scheduled booking, segment seats, manifests, rides, corporate approvals, charter. *(scheduled booking core implemented; rides/corporate/charter next — see [`docs/phase2-scope.md`](./docs/phase2-scope.md))*
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

## Environment variables

See [`.env.example`](./.env.example) for the full template:

- `DJANGO_SECRET_KEY`, `DJANGO_DEBUG`, `DJANGO_ALLOWED_HOSTS`
- `FRONTEND_URL`, `CORS_ALLOWED_ORIGINS`, `CSRF_TRUSTED_ORIGINS`
- Database overrides: `DB_ENGINE`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`

Never commit real secrets; `.env` is git-ignored.

## Contributing

This is the working repository for the Kenya transport platform. Before implementing a feature, read the corresponding sections of the architecture PDF and confirm the scope with the product owner.