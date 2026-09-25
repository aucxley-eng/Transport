# Phase 2 scope — scheduled passenger booking

Source of truth: `Kenya_Transport_and_Logistics_Architecture.pdf`,
**§7 Scheduled passenger booking** (pp. 9–10) and the parts of **§8 On demand
rides and corporate transport** (p. 10) that the blueprint merges into the
same booking/inventory model. Where this doc states an invariant, it is
quoting that blueprint verbatim; nothing here is an invention.

## Goal

Deliver the **scheduled passenger booking** flow end to end: search → quote →
**atomic timed seat hold** → pay → confirmed ticket → boarding, with correct
**segment-level seat inventory**, strict single-seat-uniqueness, explicit
handling of every exception the blueprint names, and **corporate travel +
recurring shuttles** as the §8 portion that shares the same model. On-demand
ride dispatch internals (driver offer/deadline/atomic-acceptance, trip
tracking, per-ride fare settlement) and the §9 goods/freight track stay out
of this phase (they are Phase 3+).

## Why now

Phase 1 already ships the exact seams booking needs: org-isolated
`Organization`/`Membership`/`Role` and phone identity; `catalog.Quote` as the
versioned pricing snapshot; `orders.Order`; an atomic, amount-validated
payment callback with ledger postings and outbox emission. Phase 2 lays the
seat-inventory and ticket model **on top of** those atomic seams rather than
building a parallel booking/payment path — this is the blueprint's core demand
that seat availability and payment success are evaluated together.

## Blueprint invariants being implemented (§§7–8)

1. **Seat inventory is held against the departure and occupied route
   segments** (pp. 9–10, "Inventory is held against the departure and
   occupied route segments"). A seat on Nairobi→Nakuru→Eldoret may serve
   different passengers on **non-overlapping segments**; it must be rejected
   when segments overlap.
2. **Atomic, timed seat hold.** The server creates a hold atomically with an
   expiry; after payment success the hold is converted to a confirmed ticket.
   "Payment success and seat availability are evaluated together before
   issuing a confirmed ticket" (p. 9).
3. **Single-seat uniqueness under concurrency.** "It must reject overlapping
   segment reservations even if two customers click at the same instant.
   PostgreSQL supports row locking and transactional control" (p. 9). Phase 1
   already demonstrates `select_for_update` + `transaction.atomic` in
   payments; the seat hold uses the same pattern.
4. **Group bookings acquire together or return clear partial availability
   before payment** (p. 9). Never a transaction that silently confirms fewer
   seats than the party asked to hold.
5. **Tickets carry a random identifier or signed token**, the departure
   reference, passenger details required for operation, and boarding
   information (p. 9).
6. **Boarding/scan** checks departure, ticket state und previous boarding;
   offline manifests are possible but "disconnected scanners cannot guarantee
   immediate duplicate detection across devices" (p. 9) — documented, not
   silently ignored.
7. **Every named exception is explicit** (p. 9): cancellations, rescheduling,
   vehicle substitution, seat-map changes, no-shows, delayed departures —
   and specifically: "A replacement vehicle with fewer seats creates an
   exception requiring reassignment and customer communication; it must never
   silently remove passengers."
8. **Corporate travel** (§8): company defines eligible employees, cost
   centers, budgets, approval thresholds and permitted services; a request
   may require approval before dispatch; an employee leaving must lose future
   access **without deleting historical invoices**.
9. **Recurring shuttles** (§8): a schedule template creates dated departures
   and maintains a passenger roster.

## Scope — new `apps/booking`

### Models
- `Route` — operator-owned ordered stops (reuses `catalog.Place`).
- `Departure` — a route date, operator, vehicle class, vehicle, fare
  schedule; seat capacity derived from the vehicle.
- `SeatHold` — atomic timed hold keyed to (departure, segment(s)); states
  `valid / expired / converted`. Row-locked on create/convert/expire.
- `Ticket` — random identifier + signed token, departure reference, passenger
  details, boarding info; state `issued / boarded / cancelled / rescheduled /
  substituted`.
- `BoardingScan` — immutable scan event (departure, ticket, scanner device,
  timestamp, boarding flag).
- `CorporateBooker`/`CorporateShuttle` — the §8 employee eligibility,
  cost-center, budget, approval-threshold and roster model, plus schedule
  templates producing dated departures (Phase-2 portion only; no expense
  settlement yet).
- `SeatHoldException` — records cancel/reschedule/substitution/no-show and
  the blueprint-mandated *reassignment + customer communication* event on
  vehicle substitution.

### Services (transactional, reusing Phase-1 atomic seams)
- `acquire_hold(departure, segments, passenger, expires_secs)` —
  `executes_atomic(:: `transaction.atomic` + `select_for_update` over the
  departure row and target segment gate rows) → rejects any overlap, commits
  the hold.
- `convert_hold_to_ticket(hold)` — called **inside** the payment
  callback's existing atomic block, so confirmation and ledger postings land
  together.
- `board_from_scan(departure, ticket_token, device)` — validates departure,
  ticket state and previous boarding; records the scan event.
- `handle_vehicle_substitution(departure, new_vehicle)` — if fewer seats,
  materializes the reassignment exception + communication record, never a
  silent removal (blueprint invariant 7).
- `expire_expired_holds` — timed sweep command (outbox-parallel pattern).

### API (blueprint 7 customer + §8 corporate)
- `GET /api/v1/search?from&to&date&passengers` → available departures with
  segment seats.
- `POST /api/v1/holds/` (departure, segments, passenger) → hold id + expiry;
  group vs partial-availability response before payment.
- `POST /api/v1/orders/` (existing) referencing the hold → pay → ticket issued
  in the same atomic callback (Phase-1 seam, extended).
- `POST /api/v1/board/` (scanner) and `GET /api/v1/tickets/<id>` (mobile).
- `POST /api/v1/corporate/` — employee lists, budgets, approval thresholds,
  schedule templates, dated departure generation, roster.
- Permission: org-admin for write/dispatch; authenticated passenger for
  self-booking; passenger identity via the established CA phone model.

## Explicitly out of scope (deferred)
- §8 on-demand ride **dispatch internals**: driver eligibility filtering,
  time-limited offers, atomic first-accept, trip tracking, per-ride fare
  settlement, M-PESA driver payout integration.
- §9 goods/freight/warehouse custody track.
- Corporate expense settlement/delegated approvals beyond the approval
  threshold flag, recurring-billing engine for shuttles.
- Live M-PESA callback HMAC/signature verification (carried from Phase 1;
  gated until live credentials exist).

## Definition of done
- New `apps/booking` with the models/services/API above, fully
  org-scoped (existing `IsOrganizationAdmin` + `_OrgScopedViewSet`).
- Tests: segment-overlap rejection under concurrent clicks
  (`select_for_update` chained), hold expiry sweep, group partial
  availability, ticket issued only inside successful payment callback
  (atomic with ledger), boarding duplicate detection, vehicle
  substitution-with-fewer-seats exception + communication, corporate access
  revocation keeps invoices. Full suite green, no migrations pending.
- One commit.
