# Decision 0001: managed PostgreSQL, starting on Supabase

- Status: accepted
- Date: 29 September 2026
- Decided by: product owner
- Applies to: every environment except the local SQLite test database

## Decision

The platform runs on managed PostgreSQL. Supabase is the first host, because it
is managed PostgreSQL reached over the ordinary wire protocol; Neon, RDS or a
VPS remain drop-in replacements. Firebase is not used.

## Why

The correctness of this system rests on PostgreSQL features the rest of the
toolchain depends on, and switching away from them would be a rewrite rather
than a configuration change.

- **Row locking.** Twenty-one `select_for_update()` call sites carry the
  seat-hold expiry in `apps/booking`, first-accept-wins driver assignment in
  `apps/rides` and `apps/goods`, and the outbox drain in `apps/common`. These
  are the guarantees that stop two drivers taking the same seat or the same
  consignment. No document store offers row-level locking, so each of those
  sites would need redesign rather than a driver swap.
- **Declarative constraints.** Twenty-two `UniqueConstraint` and
  `CheckConstraint` declarations currently enforce invariants in the database,
  under concurrency, including the one plan version per trip number. Moving to
  a store without constraints pushes those rules into application code, which is
  the class of defect that only appears when two people act at once.
- **A relational core.** Trips, plans, stops, shipments, packages, manifest
  lines, organization membership and rate cards are joined constantly. A
  document store means denormalising that graph and paying for it on every
  query.
- **Transaction scope.** The service layer wraps each business decision in
  `transaction.atomic()` and relies on rollback to keep refusals from writing
  partial state.

Supabase was chosen over plain PostgreSQL hosting for the operational tooling
around the same database: point-in-time recovery, branching for staging, and a
readable dashboard for a small team. PostGIS is available when routing and
geofencing arrive, as the blueprint's optional extension.

## Consequences

- Local unit tests keep using SQLite for speed. SQLite silently ignores
  `select_for_update()`, so the locking guarantees above are unproven by that
  suite. A PostgreSQL test run is required before this system holds money.
- Django connects directly to PostgreSQL. Supabase's Data API and Realtime are
  not in the request path.
- Because the connection is ordinary PostgreSQL, changing host later is a
  hostname and credential change with no code change.

## How to connect

Use the **session pooler**, not the direct host: the direct host
`db.<ref>.supabase.co` resolves to IPv6 only in several regions.

| Purpose | Host | Port |
|---|---|---|
| Application runtime | `aws-0-<region>.pooler.supabase.com` | 5432 |
| Migrations and tooling | same pooler, or the direct host from a network with IPv6 | 5432 / 5432 |

Transaction-mode pooling (port 6543) is not used: it breaks prepared
statements. The free tier is not used for anything reachable by users, since it
pauses after about a week of inactivity.

Connection settings live in `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST` and
`DB_PORT`; see `.env.example`. `DB_ENGINE` is what switches the project off
SQLite entirely, so it must be set in any deployed environment.

## Rejected

**Firebase.** Firestore has no row-level locking and no declarative
constraints, both of which this codebase depends on for correctness. Its
document model would force denormalising a relational core and abandoning the
Django ORM. Firebase Authentication was also redundant: identity is phone-based
and already issued through SimpleJWT.
