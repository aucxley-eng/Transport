# Envelope 3B scope — consolidated shared-load, coverage floor and backhaul

Source of truth: `Kenya_Transport_and_Logistics_Architecture.pdf`, **§9 Goods
delivery and freight** (p. 12), **§10 Hubs warehouses and custody** (p. 13),
**§11 Dispatch routing and fleet availability** (p. 13) and the measures table
(p. 23). Every invariant below is quoted from that blueprint.

Builds directly on Envelope 3A (`docs/phase3a-scope.md`, commit `432f0ef`),
which owns quote, shipment, packages, manifest, custody, evidence, exceptions and
returns. 3A is deliberately one shipper per consignment. This envelope adds the
consolidation that 3A left out.

## Decision taken by the product owner

**How is a shared trip's revenue split between shippers?**

> **Each shipper pays their own frozen quote, and the trip only departs once the
> confirmed shipments together cover the cost of running the vehicle.**

This was chosen over per-shipment charging with no floor (which can run a truck
below cost) and over pro-rata cost apportionment (which makes a shipper's price
depend on whoever else is aboard, breaking the "save the accepted quote and
measurement basis" rule 3A already enforces).

Consequences, all deliberate:

- A shipper's `agreed_total_minor` never moves. The quote frozen in 3A is the
  price; consolidation cannot reprice it.
- Coverage is a **departure gate**, not a price. The operator decides whether to
  run a partly loaded truck; the system refuses to release one that cannot pay
  for itself.
- The revenue-allocation table pro-rata pricing would have required is **not
  built**. If the model changes later, the change lands on the coverage floor
  alone.

## Goal

Let one vehicle carry several shippers' cargo under a single manifest, prove the
plan against the operator's own rules before anyone publishes it, refuse a trip
that cannot cover its cost, and find cargo for a vehicle that would otherwise
run home empty.

## Blueprint invariants being implemented

1. **Shared-load is a first-class product.** "For full-truckload or shared-load
   freight, include equipment type, loading access, weight and volume limits,
   loading dates, delivery windows and agreed charges." A consignment declares
   `full_truckload` or `shared_load`; the terms are already frozen in 3A.
2. **Planning is dispatcher-assisted and rule-based.** "Start with
   dispatcher-assisted assignment and transparent rules." The proposal is built
   from declared constraints, not from a black box.
3. **A proposal is subject to the declared constraints.** "For delivery batches,
   a route optimizer may propose vehicle assignments and stop order subject to
   time windows, capacity and working hours."
4. **A proposal is validated before publication.** "Validate its proposed result
   against your own business rules before publication." `validate_plan` re-checks
   every rule from the database; publication requires a clean validation.
5. **Plans are versioned.** "Save each route plan version." Each proposal is an
   immutable version; publishing supersedes the previous one and keeps it.
6. **Infeasible work is explained, not hidden.** "If no feasible plan exists,
   return an explanation to dispatch rather than hiding unassigned work." An
   infeasible proposal carries the reason per rejected consignment.
7. **Departures are not silently reordered once underway.** "Once a driver
   begins a route, preserve completed stops and communicate any remaining-stop
   changes clearly." A published plan cannot be rewritten mid-route; a change
   after departure is a new version with an explicit reason.
8. **Capacity and working hours are hard limits.** "subject to time windows,
   capacity and working hours" — a plan that overruns the driver's daily working
   hours or the vehicle's cargo allowance is invalid, not merely inefficient.
9. **A trip must pay for itself.** The coverage floor is the owner's decision
   above, applied as a departure gate.
10. **Empty running is measured, not asserted.** "Empty running: distance
    without the defined revenue load divided by measured distance" (p. 23).
    Backhaul opportunities are reported against that measure.

## Scope — extends `apps/goods`

### Models
- `Shipment.load_type` — `full_truckload` | `shared_load`.
- `FreightTrip.trip_cost_minor` — what it costs the operator to run this
  movement. The coverage floor.
- `ConsolidationPlan` — organization, trip, version, status
  (`proposed`/`validated`/`published`/`superseded`/`rejected`), the ordered
  stops, the validation verdict, the infeasibility explanation, and the
  `supersedes` pointer. Immutable once published.
- `ConsolidationStop` — the plan's stop order, with the place, sequence and the
  per-consignment arrival window.
- `PlanShipment` — which shipment rides on which plan, at what sequence, with
  the reason it was accepted or rejected.

### Services
- `propose_consolidation(organization, trip, shipments)` — build a candidate
  version: sequence the stops, admit what fits the vehicle and the windows, and
  record *why* each consignment was rejected. Never raises for a merely
  infeasible candidate; the explanation is the result.
- `validate_plan(plan)` — re-check time windows, capacity, working hours and
  load-type compatibility against the database. Returns the verdict.
- `publish_plan(plan)` — publish a validated plan, superseding the previous
  published version for that trip. Refuses an unvalidated or infeasible plan.
- `supersede_plan(plan, reason)` — a new version replaces a published one; the
  old version is retained.
- `assess_coverage(trip)` — committed revenue vs `trip_cost_minor`.
- `confirm_departure(trip)` — the gate: manifest released, plan published, and
  coverage met. Otherwise a reason.
- `find_backhaul_opportunities(organization, origin, destination, window)` —
  vehicles that will be free to move from `destination` back to `origin` in the
  window, with their remaining cargo allowance, so an operator can offer the
  empty return leg to a shipper.
- `empty_running_report(organization, since)` — the p.23 measure: distance
  without a revenue load over measured distance.

### API
- `POST /api/v1/freight-trips/{id}/consolidation/propose/`
- `GET /api/v1/freight-trips/{id}/consolidation/` — plan history, all versions.
- `POST /api/v1/consolidation-plans/{id}/validate/`
- `POST /api/v1/consolidation-plans/{id}/publish/`
- `POST /api/v1/freight-trips/{id}/coverage/` — the coverage assessment.
- `POST /api/v1/freight-trips/{id}/depart/` — the departure gate.
- `GET /api/v1/backhaul/opportunities/` — empty return legs.
- `GET /api/v1/reports/empty-running/`
- Dispatcher/operations roles only; org-scoped like 3A.

## Explicitly out of scope (deferred)

- **Opportunistic mid-route insertion (3C)** — booking cargo onto a vehicle
  already en route. §11 warns against automatic reordering, and this envelope
  deliberately keeps plans immutable once published.
- **Hub and warehouse inventory** (§10 second half) — bins, stock, picking.
- **Automatic route optimization** — the blueprint permits it ("a route
  optimizer *may* propose"), but requires our own validation first. This
  envelope implements the transparent rules and the validation hook; plugging in
  an optimizer later is a replacement for the proposer, not a rewrite.
- **Carrier offer marketplace** — "request carrier offers" implies an external
  procurement flow; not built here.
- **Temperature-controlled and dangerous goods** operations — the blueprint says
  these "should be disabled until the business is prepared to operate them."
  Hazardous-goods compartment flags exist; the operating flow does not.
- **Settlement and payout** — roadmap step 5.
- **Cross-border, customs and multimodal partner legs.**

## Definition of done
- `load_type` on shipments; full-truckload and shared-load behave differently in
  a proposal.
- A proposal admits only what fits weight, volume, compartments, time windows and
  the driver's working hours, and records a reason for every rejection.
- Publication is refused without a clean validation; versions are retained.
- A plan cannot be edited after publication; changing it creates a new version
  with a reason.
- A trip that cannot cover `trip_cost_minor` cannot depart, and the shortfall is
  reported.
- A covered, manifest-released, plan-published trip departs.
- Backhaul opportunities list vehicles with genuine remaining allowance.
- Empty running is computed as the p.23 definition.
- Full suite green, `check` clean, no pending migrations, README updated, one
  commit.
