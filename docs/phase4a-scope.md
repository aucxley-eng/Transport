# Envelope 4A scope: intercity parcels and hubs

Status: proposed — scope agreed first, implementation and its tests become the record.
Blueprint: `Kenya_Transport_and_Logistics_Architecture.pdf` v1.0 — §10 hubs and mixed operations, §9 freight procurement
Predecessor: `docs/phase3d-scope.md` (local courier: one pickup, many parcels)
Next: C2 warehouse inventory (`docs/remaining-work.md` Track C)

## Why this envelope exists

3D made the parcel the delivery unit for a van going across town. It refused, on
purpose, anything typed `Service.ServiceType.PARCEL`, with the comment that those
"belong to a hub envelope". That envelope is this one.

A parcel from Kisumu to Nyeri does not travel on one vehicle. It is dropped at a
branch, consolidated onto a line-haul to a hub, held while the onward truck is
found, and released at a destination branch. The platform already has every
primitive that movement needs — `ShipmentLeg`, `Manifest`, `ManifestLine`,
`CustodyEvent`, `reconcile_manifest`, the coverage-floor departure gate — but
nothing joins them into that chain, and the custody chain itself only ever records
three events: booked, delivered, returned. A parcel can be scanned onto a truck,
carried two hundred kilometres and handed to a stranger without a single custody
row being written.

So 4A does two things: it makes `PARCEL` real, and it fills in the custody chain
between booking and delivery.

## Product decisions

### 1. A hub is a Place. No new hub model.

Chosen: **a hub is a `catalog.Place` with `PlaceType.HUB` (or `BRANCH`), and the
hub chain is a sequence of `ShipmentLeg` rows whose stops are those places.**

- The repository already models every physical point this needs. Adding a `Hub`
  model would duplicate `Place` and force a second join onto every leg, manifest
  and custody event for no new fact.
- A parcel shipment's legs are: `COLLECTED` at the origin branch, zero or more
  `HUB` legs for the line-haul legs, and `DESTINATION` at the final branch. A
  three-leg Kisumu → Nairobi hub → Nyeri parcel and a one-leg courier drop differ
  only in how many legs were booked.
- Cut-off times and hub opening hours are **not** in C1. They belong with C2's
  warehouse and its operating calendar, and inventing them here would freeze a
  policy nobody has agreed.

### 2. Every leg of the line-haul is a real trip with a real manifest

Chosen: **each hub-to-hub movement is an ordinary `FreightTrip` carrying an
ordinary `Manifest`, reconciled and released by the existing service functions.**

- `reconcile_manifest` is already generic over "expected lines versus scanned
  lines" and already raises missing, extra and damaged as exceptions with a next
  action. A hub transfer needs a caller, not a second reconciliation
  implementation.
- The existing departure gate stays: a line-haul between hubs only leaves once
  its released manifest reconciles, its plan is published, and its committed
  revenue covers `trip_cost_minor`. A hub is a place cargo waits; it is not a
  way to run an unprofitable truck.
- What is new is **what happens on arrival**. Today arrival is not recorded at
  all.

### 3. The custody chain is written by the scan, not only by delivery

Chosen: **arriving at a hub writes `ARRIVED`; waiting for the onward truck
writes `HELD`; departing writes `DEPARTED`.**

- `CustodyEvent.EventType.ARRIVED`, `HELD` and `DEPARTED` already exist and are
  not written by any code path in the repository. `HELD`'s own label is "Held at
  branch or hub", which is exactly this case.
- `Package.current_location` already exists and is not populated by the freight
  path. It becomes the "this parcel is sitting at hub X" marker, set by the same
  transaction that writes the custody event.
- Custody stays append-only and correction-only, exactly as 3A built it: a
  wrong scan is a new `CORRECTION` row naming what it corrects and why. Nothing
  is ever edited.

### 4. Arrival is reconciled against the manifest it departed on

Chosen: **the receiving hub reconciles what actually arrived against the
manifest of the arriving trip, and an unreconciled arrival cannot be released
onward.**

- This is the branch receipt reconciliation the register asked to extend to hub
  transfers. A parcel that was scanned on at Kisumu and is missing at Nairobi is
  an exception with a reason and a next action, not a silent gap.
- A parcel that arrives without being on any manifest is an `EXTRA` line and an
  exception at the receiving hub, held rather than pushed onward — the same
  treatment 3A gives an extra package at a branch.

### 5. Release at the destination branch reuses courier's per-parcel proof

Chosen: **a parcel is released to its recipient only with the evidence 3D already
demands, per parcel, and a held or excepted parcel is not released.**

- `ShipmentEvidence` with an OTP or signature, and the rule that a photo alone is
  never proof, carry over unchanged. Intercity does not weaken local delivery.
- An open exception on a parcel blocks its release. `refresh_order_status` in
  `apps/goods/base.py` already lets an open exception outrank parcel counts; C1
  does not invent a second partial-visibility rule.

### 6. Pricing is the courier tariff, applied per parcel

Chosen: **`PARCEL` services resolve a rate card and price each parcel on its
chargeable weight, exactly as `COURIER` does.**

- The tariff machinery is already the first consumer of the rate card in this
  repository. A Kisumu-to-Nyeri parcel is priced by the same bands as a town one;
  only the rate card differs.
- `COURIER_SERVICE_TYPES` in `apps/goods/courier.py` is **not** loosened. Courier
  keeps refusing `PARCEL`; the parcel path is a sibling, and a service is either
  one or the other.

## Blueprint rules this envelope enforces

- **The parcel survives every leg and hub.** `Package.label` and identity are
  unchanged from origin to delivery; legs and custody events point at it, the
  parcel is not re-created at a hub. (§10)
- **Custody is recorded at every handover.** Booking, collection, loading,
  departure, arrival, holding and delivery each leave an append-only row naming
  who gave, who received, and where. (§10)
- **Nothing moves unreconciled.** A line-haul between hubs reconciles against its
  own manifest before it can be released onward. (§9)
- **Exceptions carry a next action.** Missing, extra and damaged at a hub are
  held, claimed or returned — never silently forwarded. (§10)
- **Every shipment's revenue is counted once.** The coverage floor uses distinct
  shipments, so a five-parcel sender cannot cover a truck five times over. (§9)
- **No frozen snapshot is edited.** Terms, prices and agreed totals are frozen at
  booking; corrections are new rows. (§3A, and the repository bar)
- **Courier is not weakened.** The `PARCEL` refusal stays where it is.

## Data model

No new models. C1 adds fields and behaviour to what exists:

- `ShipmentLeg.status` gains the meaning already in its choices for hub legs;
  `stop` is the hub `Place`.
- `Package.current_location` is set on every custody event that has a place.
- `Shipment.status` reaches `IN_TRANSIT` across a hub chain and
  `PARTIALLY_DELIVERED` when some parcels of a multi-parcel shipment are held at
  a hub and others have been delivered — the same rule 3D already applies.
- A `ShipmentException` raised at a hub names the hub as its location, so
  dispatch can see what is stranded where.

## Invariants under test

1. A `PARCEL` service is accepted by the parcel path and still refused by courier.
2. A three-leg parcel shipment books with the hub as an intermediate stop, and
   the legs are ordered by sequence.
3. Scanning a parcel onto a line-haul writes a `LOADED` custody event and sets
   `Package.current_location` to the origin hub.
4. Departing a line-haul writes a `DEPARTED` event per parcel and moves the
   parcels to `IN_TRANSIT`.
5. Arriving writes an `ARRIVED` event per parcel and sets `current_location` to
   the receiving hub.
6. A parcel that does not arrive raises a `MISSING` exception at the receiving
   hub with `HOLD_BRANCH`, and cannot be released onward.
7. A parcel that arrives unmanifested raises an `EXTRA` exception and is held.
8. A parcel scanned damaged raises a `DAMAGED` exception with `CLAIM`.
9. While awaiting the onward truck the parcel carries a `HELD` event, and it
   cannot be released while that hold is unresolved.
10. Release to the recipient requires per-parcel evidence; a photo alone is not
    proof; an open exception blocks release.
11. A line-haul between hubs cannot depart without a released, reconciled
    manifest and coverage of its trip cost, counting each shipment once.
12. A held or excepted parcel does not silently disappear from the shipment:
    `PARTIALLY_DELIVERED` and the order status reflect what actually happened.
13. Cross-organization scans, and scans by a caller without an operations role,
    are refused.
14. Corrections are new rows that name what they correct; no custody row is ever
    updated or deleted.

## API surface

| Area | Endpoints |
|---|---|
| Parcel booking | `POST /api/v1/goods/parcels/quote/`, `POST /api/v1/goods/parcels/` (sibling to courier; `PARCEL` services only) |
| Hub loading | `POST /api/v1/goods/manifests/{id}/load/` — scan parcels onto the line-haul |
| Hub arrival | `POST /api/v1/goods/freight-trips/{id}/arrive/` — reconcile the arriving manifest, write `ARRIVED`, hold what is missing |
| Hub release onward | `POST /api/v1/goods/freight-trips/{id}/depart-hub/` — write `DEPARTED`, refuse without a reconciled manifest |
| Held at hub | `GET /api/v1/goods/hubs/{place_id}/holding/` — what is stranded at this hub and why |
| Release to recipient | `POST /api/v1/goods/packages/{id}/deliver/` — already exists; C1 makes it enforce the hub chain |

## Explicitly out of scope

- **Warehouse inventory (C2).** Stock items, bins, reservations, put-away and
  discrepancy resolution are a separate app and a separate envelope. 4A moves
  parcels; it does not count stock.
- **Hub cut-off times, opening hours and capacity limits.** No policy has been
  agreed, and freezing one here would be inventing an operational promise.
- **Hub-to-hub pricing as a product.** The line-haul is priced as an ordinary
  freight trip against `trip_cost_minor`; there is no separate hub tariff.
- **Returns and claims depth.** 3A's return machinery is reused as-is; the claims
  workflow the register mentions remains open at Track A.
- **Settlement and payouts.** Any revenue movement is A4's, not 4A's.
- **Mixed passenger and parcel work on one vehicle.** 3C asserted the passenger
  timetable guard but did not build mixed operations; 4A does not either.
- **Telematics, notifications, eTIMS.** Unchanged, still Track C.

## Definition of done

- The fourteen invariants above are covered by tests in
  `apps/goods/tests/test_parcels.py`, each class naming the rule it enforces.
- `Service.ServiceType.PARCEL` is implemented and courier's refusal of it is
  still asserted by an existing test.
- Full suite green, `manage.py check` clean, no migration drift, no unused
  imports.
- README status table updated to record 4A as delivered.
