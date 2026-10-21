# Remaining work — build plan

> **Status (19 October 2026).** Since this plan was written, the review's Track A
> critical path has landed: **A1** (reservation races — row locks on `Vehicle` and
> `Driver` at both call sites, plus compartment volume enforcement), **A2**
> (`Order.confirm()` now runs inside the payment success transaction) and **A3**
> (an append-only double-entry ledger with revenue recognition on fulfilment and
> refunds that reverse through the ledger) are done. **A5** (courier money) is
> partly done: the order→shipment link exists and collections reuse
> `PaymentAttempt`, but prepaid/cash-on-delivery/account billing modes are not all
> wired. **A4** (settlement and payouts) and **A6** (reconciliation, claims,
> reporting) remain. Track B is fully done (B1–B5). A real Safaricom Daraja
> adapter replaces the stub behind the provider seam, so the "M-PESA live mode
> raises `ProviderUnavailable`" note is out of date. Track D is no longer
> scaffolded only: a dispatch console with sign-in, auth context, API client and
> error boundary is committed. Track E's PostgreSQL gate has been satisfied
> (see [`codebase-review.md`](./codebase-review.md) §4).

Companion to [`codebase-review.md`](./codebase-review.md). That file records what
exists and what is wrong; this one says how to build what is left, in what order,
and how each piece is proven.

The roadmap in the README is the frame:

1. Discovery and design — done
2. Shared foundation — done
3. Passenger services — done
4. Goods and hubs — 3A–3D done; **intercity parcels/hubs and warehouse remain**
5. **Operational and financial controls — partially started** (payment provider
   integration, ledger, revenue recognition and refunds landed; settlement,
   payouts, reconciliation and reporting remain)
6. Hardening and rollout — unstarted

---

## Guiding principles (inherit, do not reinvent)

Every piece below must follow the conventions already established in the code:

- **Service module + transaction.** Domain logic lives in `apps/<app>/services.py`
  (or `<domain>.py`), `@transaction.atomic`, re-checking its invariants inside the
  transaction rather than trusting the caller.
- **Append-only for money and custody.** New `*_minor` fields are integers; every
  financial effect is a pair of `LedgerEntry` rows sharing a `correlation_id`.
  Corrections are new rows; nothing is updated or deleted.
- **Outbox for side effects.** Anything that another bounded context must react to
  is `record_outbox(...)` in the same transaction, then a subscriber.
- **Server-authorised state.** The frontend consumes allowed operations; it never
  decides them. Disable repeated submits; keep a recovery route.
- **Prove it with a test that encodes the invariant**, named after the rule
  (the `goods` suites are the model to copy).

---

## Track A — Money and operations (roadmap step 5). Critical path.

This is the largest remaining gap and the one that gates any real money movement.
Do it in this order.

### A1. Fix the two reservation races first (see H1, H2)

Cheap, and they corrupt operational data the moment there is concurrency.

- Add a PostgreSQL **exclusion constraint** so the database itself refuses
  overlapping reservations, rather than relying on a read-then-write:
  - `charter_charterreservation` on `(vehicle_id, tstzrange(blocked_from, blocked_to))`
    and `(driver_id, ...)`.
  - A per-vehicle freight-trip exclusion on `(vehicle_id, tstzrange(planned_start, planned_end + turnaround))`.
  - Requires `btree_gist`; the ADR already chose PostgreSQL precisely for
    declarative constraints.
- Alternatively (or additionally) `select_for_update()` the `Vehicle`/`Driver`
  row before the overlap check. The exclusion constraint is the stronger fix
  because it cannot be forgotten at a call site.
- Test: fire two concurrent confirmations and assert exactly one succeeds
  (a real PostgreSQL test; SQLite cannot prove this).

### A2. Link payment to commercial state (H4)

Decide and encode the intended behaviour. Recommendation: on a successful
collection, advance the order through its own guarded method inside the same
transaction (or via an outbox subscriber), so "paid" and "confirmed" cannot drift:

- `Order.confirm()` already refuses illegal transitions; call it from the payment
  success path (or a `payment.succeeded` subscriber).
- Keep the three states separate in the API — commercial (`Order.status`), trip,
  and payment (`PaymentAttempt.state`) — but make the transition explicit and
  auditable rather than absent.

### A3. Ledger completeness (H5)

The ledger currently only ever debits cash and credits unearned revenue. It needs
the other half of the life cycle.

- **Revenue recognition:** when the service is delivered (ticket boarded, trip
  completed, parcel delivered), post unearned revenue → revenue. Add a subscriber
  on the existing `booking.ticket_issued`, `goods.courier_parcel_delivered`, etc.
- **Refunds / reversals:** a `Refund` model (or `LedgerEntry` with a reversal
  `journal`) that posts the reverse of the collection, idempotent on a refund
  reference, and drives the `booking.rebook_or_refund_required` obligation that
  the outbox already raises. This closes the "collected with no seat" hole.
- **Account mapping sign-off:** `_post_collection` says the mapping is
  provisional. Get the real chart of accounts agreed and encode it once in one
  place (a `ledger` module), not scattered literals.

### Live M-PESA proof — Stage A done, Stage B needs a tunnel

Proven against the real sandbox (`tools/mpesa_stk_push.py`): OAuth returns a
token, and an STK push is accepted with a real `CheckoutRequestID`
(`ws_CO_211020261821027796606363`, 21 October 2026). With the server running,
`POST /api/v1/webhooks/mpesa/` answers `202`, and four tests
(`HttpDarajaCallbackTestCase`) exercise the callback → ledger → `Order.CONFIRMED`
path using the genuine `stkCallback` shape.

What blocked every attempt before that, and what still blocks Stage B:

- **`CallBackURL` must be HTTPS.** `http://localhost:8000/...` is rejected.
  Daraja's `400.002.02` is a *generic* payload error — its description in the
  docs reads "Bad Request – Invalid **XXXX**" — and it reported the failure as
  `Invalid BusinessShortCode` and later `Invalid CallBackURL` for the same
  request. Do not trust the field name it names.
- **Stage B needs a public HTTPS endpoint.** Put an `https://` URL (a tunnel;
  the reference integrations run ngrok) in `MPESA_CALLBACK_URL`, push, approve
  the prompt on the handset, and the result lands on the live webhook.
- **Back off between calls.** Daraja enforces a spike arrest of 30 messages a
  minute with a burst of 3; hammering returns `429` and wastes the window.

Two known defects, deliberately left unfixed for now and recorded here rather
than silently:

- `DarajaProvider` accepts a non-HTTPS `CallBackURL`, which is fatally wrong on
  Safaricom's side but reported as an unrelated-looking error.
- `TransactionDesc` is sent at 17 characters (documented maximum is 13) and
  `AccountReference` is not length-checked (maximum 12). Neither has been
  observed to be the cause of a rejection.

`.env` still carries `http://localhost:8000/...`; the committed tests do not
need it because the callback half is delivered in-process. And note
`config.settings.development` points at the hosted Supabase database — never
seed payment test data through a running dev server.

### A4. Settlement and payouts

- Settlement batches: eligible service items (fares earned, fees, cash offsets,
  adjustments) grouped into a draft, reviewed, approved by a *different* person,
  submitted, and reconciled against the provider's final result.
- Model the states explicitly: `draft, awaiting_approval, submitted, paid,
  failed, unresolved`. A timeout is never an automatic duplicate payout
  (same discipline as `PaymentAttempt.State.UNKNOWN`).
- Driver earnings: separate settled income from cash collected on behalf of the
  business (the design PDF, D05, requires this).

### A5. Courier money

3D quotes and freezes money but never collects it. Add the order→shipment link and
the three collection modes: prepaid, cash on delivery, account billing. This
reuses `PaymentAttempt` and the ledger from A3 — do not build a second payment path.

### A6. Reconciliation, claims, reporting

- Reconciliation views over unresolved payments, refunds and payouts by age.
- Claims workflow (the returns path already leaves damage claims open; give them
  an owner and a resolution).
- Reporting: route/service trends, utilisation, empty running (already computed in
  `consolidation.py`), punctuality, delivery success, exception age. Read-only,
  with metric definitions and freshness.

---

## Track B — Harden the completed envelopes

Small, high-value fixes to existing code (full detail in the review):

| Item | Finding | Action |
|---|---|---|
| B1 | H3 | Wrap the ticket-issue fallback in a `SAVEPOINT` (`transaction.atomic(savepoint=True)`) and catch only business exceptions, so a DB error cannot poison the payment transaction. |
| B2 | M1 | Partial unique index on `payments` `(provider, provider_reference)`; resolve callbacks with an explicit single-row lookup. |
| B3 | M2 | Track per-compartment used volume (from manifest lines) and subtract it; make `_place_in_compartments` respect the volume limit, not just weight. |
| B4 | M3 | Either wrap `record_audit` in `try/except` (matching its docstring) or make it deliberately transactional and fix the docstring. Decide; do not leave the contradiction. |
| B5 | — | Delete the orphan `test_scenario_probe` bytecode; add test modules for `orders` and `audit`. |

---

## Track C — Missing domains

### C1. Intercity parcels and hubs

`Service.ServiceType.PARCEL` is unused. Build on the courier/3D primitives:
branch-to-branch and mixed service, line-haul manifests between hubs, an origin
scan → line-haul → destination scan → release chain, all on top of the existing
`Manifest`/`CustodyEvent` append-only model. Branch receipt reconciliation already
exists in the manifest code; extend it to hub transfers.

### C2. Warehouse inventory

New app. `StockItem` (owner, SKU, quantity, unit, bin), `Location`, movement
records (append-only), states `available / reserved / quarantined / damaged`.
Receive → put away → reserve → pick → pack → dispatch, with discrepancy resolution
mirroring `reconcile_manifest`. Returns pass inspection before stock becomes
available again.

### C3. Maintenance and incidents

There is a `rides.TripIncident` but no maintenance workflow. Model: inspections
(open issues survive a later pass — Fleetio's documented behaviour), work orders
(priority, parts, labour, repair evidence), and an authorised sign-off before a
blocked vehicle returns to service. `Vehicle.status` already has `blocked`.

### C4. Notifications

Outbox events exist; delivery does not. Add subscribers for booking confirmation,
driver assignment, material delay, ready-for-collection, delivery, refund
decision, payout outcome. In-app + push + SMS fallback, **deduplicated**, each
opening the relevant record. A failed SMS must never invalidate a booking.

### C5. Telemetry / GPS

ADR notes PostGIS "available when routing arrives". Start read-only: ingest
position pings, store with source and freshness, and never let a phone and an
installed tracker disagree smoothly — surface the uncertainty (design PDF, M03).
No new build dependency is needed for the read path.

---

## Track D — Frontend (`frontend/`, console started)

The design PDF specifies **React + TypeScript web portals** and **Flutter**
customer/driver apps, sharing design tokens and API schemas.

**Committed so far:** operator sign-in behind an authenticated-redirect gate, an
auth context with token refresh, a typed API client that clears tokens only on a
definitive 401, the dispatch shell, and a global error boundary.

1. **Design tokens first.** The PDF fixes the palette (navy `#152A3A`, teal
   `#006B60`, background `#F5F7F9`), a 4 px spacing scale, 48 px touch targets,
   and a shared component set (location picker, quote summary, seat selector,
   ticket, timeline, evidence uploader, money breakdown, sync indicator,
   exception panel). The current `styles.css` is dark-themed; reconcile it.
2. **Typed API clients** per module beside `api/client.ts`, mirroring the DRF
   serialisers, plus the five mandated list states (loading, empty, no-results,
   failed, stale).
3. **Web portals, release-ordered** (PDF Release 1→4): operator Overview/Work/
   Fleet/People/Statements, dispatch board, branch/hub, finance, admin.
4. **Flutter apps** for customer and driver/courier, offline-first with the
   four sync states (saved / waiting / confirmed / needs attention).
5. Every screen must separate what the user requested from what the server
   confirmed (design PDF, p.20).

---

## Track E — Infrastructure (`infra/`, empty)

- **Before any real money: a passing PostgreSQL test run** (ADR 0001; see the
  review's §4) and a migration/deploy pipeline.
- Durable queue/workers for the outbox relay (`publish_outbox --loop` is a start,
  but needs supervision and metrics).
- Private object storage for documents and evidence photos (currently local `MEDIA_ROOT`).
- Environments, secrets management, backups and a restore test (roadmap step 6).

---

## Sequencing

```
A1 ─ B1..B5 ─ A2 ─ A3 ─ A4 ─ A5 ─ A6
              │
              └─ (PostgreSQL suite green) ─ before A4 touches real payouts

C4 notifications  ─ can start any time; unblocks UX everywhere
C1 parcels        ─ after A5 (shares collection)
C2 warehouse, C3 maintenance ─ after C1
D frontend        ─ in parallel, tokens first; web before Flutter
E infra           ─ pipeline + Postgres run first, storage before launch
```

## Definition of done (per item)

Copy the existing bar:

- An invariant test in the owning app's `tests/`, named after the rule.
- The concurrency-sensitive items (A1, and the reservation races) additionally
  proven **on PostgreSQL**, not SQLite.
- A scope doc under `docs/` for any new envelope, in the style of
  `docs/phase3d-scope.md`, with an explicit "not in scope" list.
- No change to a frozen snapshot of an existing record; corrections are new rows.
