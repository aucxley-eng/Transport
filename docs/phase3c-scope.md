# Envelope 3C scope: mid-route insertion and remaining-stop changes

Status: delivered (Envelope 3C). Written when this was still "ready to implement"; the implementation and its tests are now the record. See the README status table.
Blueprint: `Kenya_Transport_and_Logistics_Architecture.pdf` v1.0 — §9 freight procurement, §10 hubs and mixed operations, §11 dispatch routing
Predecessor: `docs/phase3b-scope.md` (consolidated shared load, coverage floor, backhaul)

## Why this envelope exists

3B froze a truck's plan before it left. The road does not stand still: a farmer at a roadside stop has a pallet that must reach the market today, and the truck that is already passing through is the cheapest way to move it. Rejecting that request is a business decision the platform should not make by default, and accepting it carelessly breaks promises already paid for.

The blueprint's instruction is narrow and unambiguous (§11):

> Save each route plan version. Once a driver begins a route, preserve completed stops and communicate any remaining-stop changes clearly. Do not automatically reorder scheduled passenger stops for parcel convenience.

So 3C adds insertion **inside the existing version discipline**: every insertion is a new plan version, completed stops are untouchable, existing remaining stops are never silently moved, and every change is explained to dispatch and the affected shippers.

## Product decisions

### 1. Delay tolerance: insert only within a tolerance, refuse beyond it

Chosen: **allow the insertion only if the delay it causes to any already-promised remaining stop stays inside a configured tolerance; refuse it, with the reason, when it does not.**

- `Organization.insertion_grace_minutes` (default 30) is the policy knob, set by the operator, and capped so a lenient policy cannot quietly become a breach of the service promise.
- The refusal names the stop that would be missed, by how much, and the tolerance it was measured against. Dispatch then re-offers on a later stop order or a later trip; nothing is silently re-timed.
- This is deliberately *not* the "refuse any delay" rule and *not* the "notify and accept the delay" rule. A 12-minute detour to a farm gate is service, not damage. A 90-minute detour is a broken promise, and the platform says no.

### 2. Who may insert: dispatcher directly, driver by offer

Chosen: **a dispatcher may insert a validated consignment immediately; a driver may be offered one and accept it, first-accept-wins.**

- Dispatcher insertion is one atomic, validated action: no proposal/validate/publish round trip in the field, but the same rule set runs before anything is published.
- The driver path follows the 2A pattern the codebase already trusts (offer → first accept wins → losers are told), with the blueprint's allocation-correctness rule applied: accept runs in a transaction, locks the request row, and re-checks capacity at accept time because a screen that was valid a minute ago is not proof.
- Shipper self-service insertion is out of scope. It would expose a price that moves with the vehicle's live position, and that belongs to dispatch.

### 3. Pricing: the remaining leg, frozen at insertion

An inserted consignment is priced for **the leg it will actually travel** — from the insertion point forward — quoted and accepted on the normal 3A path, with the accepted total and basis snapshotted onto the shipment exactly as any other booking.

- No pro-rata of an already-accepted full-leg quote; no repricing of existing shippers.
- The new revenue is added to the same coverage-floor assessment from 3B: a truck already on the road has already paid its cost, so insertion can only improve coverage. Coverage is re-reported so dispatch sees the effect.

## Blueprint rules this envelope enforces

- **Preserve completed stops.** A completed stop is immutable in every later version: same sequence, same window, never moved, never dropped. Insertion only affects what has not happened yet.
- **Never silently reorder.** The existing remaining stops keep their order. The new stop is placed by its own service window among them; it does not shuffle the stops already promised to other shippers. (§11: "Do not automatically reorder … for parcel convenience".)
- **Validate before publication.** The insertion runs the same rule re-derivation as 3B validation — capacity, compartments, working hours, windows, frozen revenue — before the new version is published.
- **Save every version.** Each insertion is a new plan version pointing at the one it supersedes, with a mandatory change reason and a change summary listing what moved, by how much, and who approved it.
- **Communicate clearly.** A published insertion emits an outbox event and an audit entry naming the affected stops, the revised window for each affected shipment, and the approver, so the dispatcher can tell the affected shippers and the driver can see the new order.
- **Allocate under a transaction and version check.** Driver acceptance locks the request and re-checks remaining capacity; the previous version's numbers are not trusted.
- **Explain infeasibility.** Every refusal carries a reason, in the API response and in the stored request, so unassigned work stays visible.

## Data model

- `Organization.insertion_grace_minutes` — per-operator delay policy (default 30, cap 240).
- `Manifest.version` + `Manifest.supersedes` — a mid-route pickup amends the manifest by publishing a **new version** rather than editing the released one; the released manifest stays on record. Replaces the one-manifest-per-trip constraint with one manifest version per trip.
- `ConsolidationStop.completed_at` — the immutable-fact marker that separates "done" from "still promised".
- `InsertionRequest` — the driver-offer record: trip, shipment, requested by, offered to, status (`pending` / `accepted` / `declined` / `expired` / `refused`), tolerance snapshot, remaining-capacity snapshot at request time, reason, decision timestamps, and the plan version the acceptance produced.

## Invariants under test

1. A completed stop is byte-identical in the next version: same sequence, same window, still completed.
2. Existing remaining stops keep their relative order; the new stop is added by its own window.
3. An insertion that would push a remaining stop past its window by more than the organization tolerance is refused, and the refusal names the stop and the overrun.
4. An insertion within tolerance publishes, supersedes the previous version, and records the change reason and summary.
5. An insertion that does not fit the remaining vehicle or compartment allowance is refused.
6. An insertion on a trip with no remaining stops is refused.
7. An insertion never changes an existing shipper's agreed total, and coverage is re-reported with the new revenue included.
8. The amended manifest is a new version, still requires reconciliation and re-release, and the previous released version remains readable.
9. First driver accept wins; a second accept, or an accept of a stale request whose capacity no longer holds, is refused.
10. Cross-organization requests, and requests from callers without an operations role, are refused.

## API surface

| Area | Endpoints |
|---|---|
| Route state | `GET /api/v1/freight-trips/{id}/route/` — current published version, completed and remaining stops, coverage |
| Dispatcher insertion | `POST /api/v1/freight-trips/{id}/insertion/` |
| Manifest amendment | `POST /api/v1/manifests/{id}/amend/` — add mid-route packages, new manifest version |
| Driver offers | `GET/POST /api/v1/insertion-requests/`, `POST /api/v1/insertion-requests/{id}/accept/`, `/{id}/decline/` |
| Stop progress | `POST /api/v1/consolidation-plans/{id}/stops/{stop_id}/complete/` |

## Explicitly out of scope

- Reassignment and breakdown recovery with explicit transfers (§11) — a separate envelope; it changes the vehicle, which is a larger promise than adding a stop.
- Automatic stop re-optimization for a mid-route pickup. The new stop is placed by window; the platform does not re-run an optimizer over a route in progress.
- Shipper-facing self-service insertion and any live position-dependent public price.
- Courier and parcel hub operations, warehouse inventory and fulfillment (§10) — untouched by 3C.
- Mixed passenger/parcel work on the same vehicle, where parcel windows must not override the published passenger timetable (§10). 3C is freight-only; the passenger timetable guard is asserted in tests but the mixed-operation model is not built here.
- Settlement, payouts, claims and refunds.

## Definition of done

- The ten invariants above are covered by tests in `apps/goods/tests/test_insertion.py`.
- Full suite green, `manage.py check` clean, no migration drift, no unused imports.
- README updated with the 3C API surface and invariants.
