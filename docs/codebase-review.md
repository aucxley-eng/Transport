# Codebase review — 17 October 2026

> **Status of this register (19 October 2026): actioned.** Every finding below
> was worked through in the commits that follow this review:
> `55d9041` (F1–F5), `4262855` (the H3/M3/M1 callback transaction), `695b070`
> (the Daraja adapter), `7bd98c4` (H1/H2/M2 reservation locking and compartment
> volume), `979fcf9` (H4/H5 order confirmation, revenue recognition, refunds)
> and `d85832e` (F6–F9 frontend), plus a housekeeping pass that removed the
> dead code the low-severity section names. The documentation-drift items in
> §3 were corrected in the same pass. Treat the finding tables below as the
> historical record of what was wrong, not as work still outstanding; see
> [`remaining-work.md`](./remaining-work.md) for what is genuinely left.

A ground-up review of the repository against the implementation, not against the
README. Written to give a second contributor (or a returning one) a reliable map
and to record the things the README does not state.

**What was read:** `backend/config/` (settings, `urls.py`, `api_urls.py`), all
twelve `backend/apps/*/models.py`, every `apps/*/urls.py`, the domain/service
modules (`booking/services.py`, `rides/services.py`, `goods/services.py`,
`goods/consolidation.py`, `goods/insertion.py`, `goods/courier.py`,
`payments/services.py`, `payments/provider.py`, `corporate/services.py`,
`charter/services.py`, `common/outbox.py`, `common/money.py`,
`audit/models.py`), the test suite (15 modules, 372 test methods), all six
`docs/phase*-scope.md` files, `docs/decisions/0001-managed-postgresql.md`, the
whole `frontend/src/`, and the frontend design PDF.

---

## 1. How the backend is organized

Django 6.1 + DRF + SimpleJWT, one codebase, mounted at `/api/v1/`.

| App | Owns |
|---|---|
| `common` | `BaseModel`, org-scoped queryset, `OutboxEvent` + outbox relay, money helpers |
| `accounts` | `User` (phone is `USERNAME_FIELD`), `Organization`, `Membership`, `Role` |
| `audit` | append-only `AuditEvent` |
| `fleet` | `Vehicle`, `Driver`, `Document`, `CargoCompartment` |
| `catalog` | `Service`, `Place`, `RateCard`/`RateLine`, `Quote`, tariff resolution |
| `orders` | `Order` — commercial state only |
| `booking` | routes, `Departure`, segments, seat holds, `Ticket`, boarding |
| `rides` | zones, shifts, `RideRequest`, `DriverOffer`, `Trip`, incidents |
| `corporate` | policy, cost centres, members, travel requests, shuttle templates/runs/rosters |
| `charter` | `HireRequest`, `CharterReservation` (interval blocking), extras, deposits |
| `goods` | shipments, packages, freight trips, manifests, custody, evidence, exceptions, consolidation plans, insertion requests |
| `payments` | `PaymentAttempt`, append-only `LedgerEntry`, M-PESA adapter |

### Cross-cutting conventions

- **Tenancy.** Every org-owned model carries `organization` FK and installs
  `OrganizationScopedQuerySet.as_manager()`; `.for_organizations(ids)` also fans
  out to child orgs. Authorization is `Membership` (user → org → role), never a
  flag on `User`.
- **Authorization is enforced inline**, not by DRF permission classes. Views call
  `_require_admin` / `_require_manager` / `_require_staff` / `_require_ops` and
  raise `PermissionDenied`. This is consistent, but it means route-level
  permission introspection (and DRF Spectacular) understates the real rules.
- **Money is integer minor units** (`apps/common/money.py`); `to_minor()` rejects
  over-precision. `UGX`/`TZS` have exponent 0.
- **Append-only history.** `LedgerEntry`, `AuditEvent`, `CustodyEvent` deliberately
  do *not* inherit `BaseModel` — they have only `created_at`, no `updated_at`.
  `BaseModel`'s docstring states this rule explicitly.
- **Row locks.** 21 `select_for_update()` call sites. The serialization points are:
  the `Departure` row (seat allocation), the `RideRequest` row (offer arbitration),
  the `InsertionRequest` row (driver acceptance), and the `OutboxEvent` row
  (publishing). Lock order is fixed — `accept_offer` always locks request before
  offer.
- **Transactional outbox.** `record_outbox()` writes in the caller's transaction;
  `publish_single()` drains with `select_for_update` and is retry-idempotent.
  Relay: `manage.py publish_outbox --loop`.
- **Versioned / snapshotted truth.** Quotes are accept-once immutable and carry
  `rate_card_name`. `Shipment.terms_snapshot` / `measurement_snapshot`,
  `Package.pricing_snapshot`, and `version`+`supersedes` on manifests and
  consolidation plans freeze what was agreed so a later tariff edit cannot rewrite it.
- **Re-check inside the transaction.** Service functions re-validate what the
  caller asserted rather than trusting the request — most visibly
  `insertion.accept_insertion`, which re-runs the entire refusal list under lock.

---

## 2. What is built and tested

| Track | Apps / modules | Tests |
|---|---|---|
| Phase 1 foundation | accounts, fleet, catalog, orders, payments, audit, common | ~40 |
| Phase 2 — scheduled booking | `booking/` (+ `expire_holds`) | 17 |
| Envelope 2A — on-demand rides | `rides/` (+ `expire_offers`) | 23 |
| Envelope 2B — corporate | `corporate/` | 29 |
| Envelope 2C — charter | `charter/` | 33 |
| Envelope 3A — freight/manifest/custody | `goods/services.py` | 83 |
| Envelope 3B — consolidation/coverage/backhaul | `goods/consolidation.py` | 37 |
| Envelope 3C — mid-route insertion | `goods/insertion.py` | 52 |
| Envelope 3D — local courier | `goods/courier.py`, `catalog/tariff.py` | 64 |

**372 `def test_` methods**, collected as 372 tests under
`config.settings.test_postgres`. An end-to-end courier loop runs over real HTTP
in `backend/tools/courier_smoke.py` (boots a WSGI server, exercises quote → book
→ plan → deliver → failed attempt → return → cross-org isolation).

`apps/orders` and `apps.audit` have **no test modules of their own** (audit is at
least covered indirectly by `accounts/test_isolation.py`).

---

## 3. Findings the README does not state

Each was verified by reading the code, not inferred from the README.

### High severity

| # | Finding | Location | Why it matters |
|---|---|---|---|
| H1 | **Freight trip vehicle/driver reservation is an unlocked check-then-insert.** Inside `@transaction.atomic`, but the overlap test is `.exists()` with no `select_for_update` and no DB exclusion constraint. | `apps/goods/services.py:395-398` | Two concurrent dispatchers can both pass `.exists()` and double-book the same truck. The docstring at line 370 promises otherwise. |
| H2 | **Charter confirmation is the same shape, and the comment claims a lock that is not there.** Comment reads *"Lock against concurrent confirmation of the same vehicle/driver"* directly above a plain `.filter().exists()`; the docstring claims two dispatchers cannot both win. | `apps/charter/services.py:248-256` | Same double-booking race for charters. |
| H3 | **`_issue_booking_tickets` swallows `Exception` inside the payment atomic block.** Intent is *"never let booking break the collection record"*, but a DB-level failure sets `needs_rollback`, and the `record_audit()` that follows then raises `TransactionManagementError`. | `apps/payments/services.py:226-236` | The exact path meant to protect the collection can instead explode it. Business exceptions are fine; database errors are not. |
| H4 | **Payment success never advances `Order.status`.** `Order.Status.CONFIRMED` is set only by `orders/views.py` `confirm()` (a client action); nothing in the payment path calls it. | `apps/payments/services.py:156-172`, `apps/orders/models.py:60` | A paid order still reads `created`. Commercial vs payment state are *meant* to be separate, but there is currently no link between them at all. |
| H5 | **No refund / reversal path exists.** Collected funds can only be *signalled* via the outbox event `booking.rebook_or_refund_required`; there is no posting that reverses `cash.customer_collections`. `liability.unearned_revenue` is also never moved to revenue. | `apps/payments/` (grep: no reversal code) | Roadmap step 5. Money that must be returned currently has nowhere to go in the ledger. |

### Medium severity

| # | Finding | Location | Why it matters |
|---|---|---|---|
| M1 | **`provider_reference` is not unique**, only `db_index=True`, while callbacks resolve with `attempts.first()`. | `apps/payments/models.py:38`, `apps/payments/services.py:115-126` | Mitigated today (M-PESA derives the reference from the attempt UUID), but a provider-supplied receipt shared by two attempts would resolve arbitrarily. Should be a partial unique index on `(provider, provider_reference)`. |
| M2 | **Per-compartment free volume ignores usage.** `free_volume_m3` is `max(max_volume_m3, ZERO)` with no `- used`, unlike weight directly above it. `_place_in_compartments` also only fits by weight. | `apps/goods/services.py:443-446`, `:465` | Compartment *volume* limits are neither shown correctly nor enforced. Whole-vehicle volume *is* correct (line 460). |
| M3 | **`record_audit` contradicts its own docstring.** Docstring says *"never allowed to break the caller's work"*, but there is no `try/except` around `AuditEvent.objects.create(...)`. | `apps/audit/models.py:47-74` | An audit write can raise into the caller — and because it is called inside the payment atomic block, it can break that transaction too. |

### Low severity / housekeeping

- `_post_collection` account mapping is explicitly **provisional** — *"the
  accountant approves final principal/agent mapping"* (`apps/payments/services.py:182`).
- M-PESA **live mode raises `ProviderUnavailable`**; only the sandbox works
  (`apps/payments/provider.py`).
- `IsOrganizationMember` permission class appears **unused** (dead code).
- Orphan `apps/booking/tests/__pycache__/test_scenario_probe.cpython-312.pyc` with
  no matching source file — a deleted test still compiled.

### Documentation drift

1. `README.md` says **369** tests; **372** exist.
2. `README.md` says the frontend is *"Empty … No source files"*; it has **13**
   source files (login, dispatch shell, auth client/context) and a working Vite build.
3. `docs/phase3c-scope.md` still says *"ready to implement"* — 3C is implemented
   and has 52 tests.
4. `docs/phase2b-scope.md` defers charter as *"Envelope 3"* and corporate as
   *"Envelope 2B"*, yet **both are delivered**, and **no scope document exists**
   for either (only README sections).

---

## 4. PostgreSQL run (ADR 0001)

ADR 0001 records the standing risk: the suite runs on SQLite, which silently
ignores `select_for_update()` and skips `CheckConstraint`, so the SQLite run
cannot prove the row-locking invariants the system depends on. **A PostgreSQL run
is required before the system holds money.**

### Command

```bash
cd backend
.venv/bin/python manage.py test --settings=config.settings.test_postgres
```

`config.settings.test_postgres` must be asked for by name and refuses a
placeholder `DB_HOST`. It creates a temporary database named `test_supabase` on
the configured server, runs, and drops it.

### Environment notes

- `.env` is configured for the Supabase **session pooler** on port 5432
  (`aws-1-eu-west-1.pooler.supabase.com`, IPv6/NAT64 only).
- **Non-interactive runs need `--noinput`.** Otherwise a leftover test database
  triggers an interactive `input()` prompt and dies with
  `EOFError: EOF when reading a line`.
- **A killed run leaves a session behind**, which then blocks recreation with
  `database "test_supabase" is being accessed by other users`. Clear it first:

```python
# terminate stale sessions before a re-run
SELECT count(*) FROM (
  SELECT pg_terminate_backend(pid) FROM pg_stat_activity
  WHERE datname = 'test_supabase' AND pid <> pg_backend_pid()
) t;
```

- The full suite against the remote pooler is latency-bound (every query crosses
  an ocean), so expect a materially slower run than the SQLite suite.

### Result (17 October 2026)

**PASS — the whole suite is green on real PostgreSQL.**

```
Found 372 test(s).
Ran 369 tests in 62.993s
OK (skipped=1)
```

The one skip is
`apps.common.tests.test_settings.TestDatabaseIsAlwaysLocalTestCase`, a harness
guard that deliberately stands down when the run is opted into PostgreSQL (its
`setUpClass` raises `SkipTest`). No failures, no errors. The locking and
constraint invariants that SQLite cannot exercise **do hold** on the engine the
system will actually run on.

### Run it locally, not against the hosted server

Against the hosted Supabase pooler the same suite took **~100 minutes and then
stalled** — every one of tens of thousands of statements pays a full network
round-trip to `eu-west-1`, and the session pooler at one point left the client
blocked in `poll()` on the socket while the server showed *idle in transaction*.
That is distance, not a logic hang: the identical suite finishes in **~63 seconds**
on a local server.

The machine has no root, no Docker, and no local PostgreSQL, so use the rootless
**`pgserver`** package (it ships a PostgreSQL binary and runs as the user):

```bash
# one-time, into a plain directory (new venvs are unavailable: no ensurepip)
.venv/bin/pip install --target /home/aucxley/pgtools pgserver

# start a server on a unix socket and keep it up (see /tmp/keeper.py for the loop)
PYTHONPATH=/home/aucxley/pgtools .venv/bin/python -c \
  "import pgserver,time; pgserver.get_server('/home/aucxley/pgdata_pgserver', cleanup_mode=None); time.sleep(10**9)"
```

Then point the suite at it. `manage.py` loads `.env` with `override=False`, so
exported variables win over the hosted credentials:

```bash
cd backend
export DB_HOST=/home/aucxley/pgdata_pgserver DB_PORT=5432 DB_NAME=postgres \
       DB_USER=postgres DB_PASSWORD= DB_SSLMODE=disable \
       DB_ENGINE=django.db.backends.postgresql
.venv/bin/python manage.py test --settings=config.settings.test_postgres --noinput --keepdb
```

Cold run (creates and migrates `test_supabase`) ≈ 90 s wall; `--keepdb` skips the
migration step on later runs. **This should be the development loop for anything
touching locking, constraints or money** — ADR 0001's requirement is now
satisfiable in seconds, not hours.

---

## 5. Summary

The domain design is strong and unusually well tested: segment-level seat
inventory, first-accept-wins dispatch, the coverage-floor departure gate,
mid-route insertion that carries completed stops forward, and parcel-level
courier pricing that refuses rather than guesses. The gaps are concentrated in
exactly two places — **concurrency at the two resource-reservation sites** and
**money completeness at step 5** — plus documentation drift. See
[`remaining-work.md`](./remaining-work.md) for the build plan.
