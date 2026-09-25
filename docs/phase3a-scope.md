# Envelope 3A scope — freight booking, manifest and custody chain

Source of truth: `Kenya_Transport_and_Logistics_Architecture.pdf`, **§9 Goods
delivery and freight** (p. 12), **§10 Hubs warehouses and custody** (p. 13),
**§11 Dispatch routing and fleet availability** (p. 13), the service catalog
(p. 5) and the data-model inventory (p. 18). Every invariant below is quoted
from that blueprint; nothing here is invented.

This is a new major phase, not a continuation of Phase 2. Envelopes 1 and 2A
are delivered; corporate shuttles (2B) remain open in Phase 2. Freight is
blueprint roadmap step 4 ("goods and hubs"), reached early because the booking,
quoting and custody machinery reuses the Phase-1/2 seams almost entirely.

## Goal

Deliver the single-owner freight movement that a shipper actually buys:
**quote → shipment → packages and manifest → custody chain with receipts →
delivery evidence → exception or linked return**. One shipper, one shipment,
many packages.

## Deliberately excluded from this envelope

The consolidated/shared-load vision — filling a lorry's spare capacity with
other farmers' cargo, backhaul to kill empty running, and opportunistic
mid-route insertion — is **not** here. It is 3B and 3C, and it is excluded on
purpose, not by accident. See "Why consolidation is deferred" at the end.

## Blueprint invariants being implemented (§§9–11)

1. **Shipment detail is explicit.** "A goods booking records sender, recipient,
   collection and destination, package count, dimensions, weight, declared
   value, handling needs and service window."
2. **Quote basis is captured, not recomputed.** "Quotes use the applicable
   tariff; a business may charge by actual weight, volumetric weight, distance,
   zone or vehicle capacity. **Save the accepted quote and measurement basis.**"
   The accepted `Quote` *and* the basis it was computed on are stored on the
   shipment, because the tariff may change later.
3. **Freight terms are preserved before dispatch.** "For full-truckload or
   shared-load freight, include equipment type, loading access, weight and volume
   limits, loading dates, delivery windows and agreed charges… **Preserve the
   accepted carrier offer and terms before dispatch.**" The terms snapshot is
   frozen onto the shipment; later tariff edits must not rewrite history.
4. **Cargo capacity is independent of passenger capacity.** "**A passenger seat
   is never treated as interchangeable with cargo weight**" and "Passenger
   capacity and cargo capacity remain independent. Model seats, baggage
   allowance, cargo weight, volume and compartment restrictions." Freight
   capacity is a separate ledger keyed on weight + volume + compartment, not a
   reuse of `SeatHold`/`Segment`.
5. **The manifest is authoritative at the vehicle.** "A departure manifest
   lists the packages assigned to a trip. **Loading scans must reconcile against
   the manifest before release.**" A load that does not reconcile must not be
   releasable.
6. **Custody history is append-only with a correction trail.** "A custody
   record identifies the package, giving party, receiving party, location, event
   type, **device event time, server receipt time** and evidence. **Corrections
   add a new event with a reason.**" Both timestamps are kept, so offline
   capture and server arrival are distinguishable. No correction ever mutates or
   deletes an earlier event.
7. **Discrepancies raise exceptions, never silent acceptance.** "Missing, extra
   or damaged packages create exceptions rather than being silently accepted."
8. **Collection and delivery evidence are captured separately.** "Capture
   collection evidence and delivery evidence **separately**," and "**Do not let
   a photograph alone prove that the intended recipient received the correct
   package.**" Delivery release requires recipient verification (name/OTP/
   signature/delegate check), with a photo as supporting evidence only.
9. **Failed attempts carry a next action.** "Failed attempts create an exception
   with the next action: **retry, hold at branch, return or claim**."
10. **Returns are a linked reverse movement.** "Returns create a linked reverse
    shipment with its own legs and custody history" — a return is a new
    shipment referencing the original packages, not a flag on the old one.
11. **Partial delivery stays visible at package level.** "Partial deliveries
    remain visible at package level" — package status is never collapsed into a
    single shipment status.
12. **Branch staff cannot rewrite custody.** "Cannot change recorded custody
    history" (role table) — enforced in the service layer, not the UI.

## Scope — new `apps/goods`

### Models
- `Shipment` — sender, recipient, collection/destination `Place`, package
  count, dimensions, weight, volumetric weight, declared value, handling needs,
  service window, status, and the **frozen** accepted `Quote` + `measurement_basis`
  + carrier `terms` snapshot.
- `Package` — label id, weight, size, handling flags, per-package status. Package
  identity survives every leg and hub.
- `ShipmentLeg` — pickup and dropoff stop, planned order, status. "Shipment legs
  connect freight to actual vehicle trips **without losing package identity**"
  (Figure 10).
- `Manifest` / `ManifestLine` — the packages assigned to one trip, with expected
  vs scanned reconciliation and a release flag.
- `CustodyEvent` — package, giving party, receiving party, location, event type,
  **device event time + server receipt time**, evidence, and a
  `corrects_event` pointer for corrections. Insert-only.
- `ShipmentEvidence` — `kind` in {collection, delivery} with recipient
  verification, supporting photos and notes. Separate rows for the two moments.
- `ShipmentException` — kind (missing, extra, damaged, failed_attempt, refused,
  absent), next action (retry, hold, return, claim), status, resolution note.

### Services (transactional, reuse Envelope 1/2A patterns)
- `quote_shipment(...)` — `build_quote` with a freight basis
  (`actual_weight` | `volumetric` | `distance` | `zone` | `vehicle_capacity`)
  and the measurement inputs snapshotted on the quote.
- `create_shipment(quote, ...)` — snapshot terms + basis; refuse an
  expired/invalid quote.
- `cargo_capacity_for(trip)` — remaining weight + volume + compartment
  allowance for a vehicle/trip, as a **separate ledger from seats**.
- `build_manifest(trip)` — package lines for a trip.
- `record_custody_event(package, *, giving, receiving, location, kind,
  device_time, evidence, corrects=None)` — insert-only; a correction requires
  `corrects` and a reason.
- `reconcile_manifest(manifest, scanned_ids)` — expected vs received; any
  missing/extra/damaged line creates an exception and **blocks release**.
- `release_manifest(manifest)` — refuses while unreconciled exceptions are open.
- `record_evidence(shipment, kind, ...)` — collection or delivery; delivery
  requires recipient verification (photo alone is insufficient).
- `raise_exception(shipment, kind, next_action)`.
- `create_return_shipment(shipment, packages, ...)` — linked reverse shipment
  with its own legs; original custody history is left intact.

### API
- `POST /api/v1/shipments/quote/` — shipment detail → `Quote` + basis.
- `POST /api/v1/shipments/` — create from an accepted quote.
- `GET /api/v1/shipments/`, `/{id}/`, `/{id}/custody/`, `/{id}/packages/`.
- `POST /api/v1/packages/{id}/custody/` — append a custody event.
- `GET/POST /api/v1/manifests/`, `/{id}/reconcile/`, `/{id}/release/`.
- `POST /api/v1/shipments/{id}/evidence/` — `kind=collection|delivery`.
- `POST /api/v1/shipments/{id}/exceptions/`.
- `POST /api/v1/shipments/{id}/return/` — linked reverse shipment.
- Org-scoped via the existing `IsOrganizationAdmin` + `_OrgScopedViewSet` pattern.

## Explicitly out of scope (deferred)

- **Consolidated / shared-load selling (3B)** — multiple shippers on one trip.
- **Backhaul and empty-running reduction (3B)** — the blueprint names *Empty
  running* (p. 23) as a freight/fleet measure, but not the matching feature.
- **Opportunistic mid-route insertion (3C)** — booking cargo onto a vehicle
  already en route. Deferred partly because it is hard and partly because
  **§11 warns against it**: "Save each route plan version. Once a driver begins
  a route, preserve completed stops and communicate any remaining-stop changes
  clearly."
- **Hub and warehouse inventory** — bins, stock, reservations, picking (§10
  second half). A later envelope; custody and manifests land here.
- **Temperature-controlled and dangerous goods** — the blueprint says these
  "should be disabled until the business is prepared to operate them."
- **Cross-border, customs and multimodal partner legs** — "access to those
  services is not assumed."
- **Linehaul/telematics ingestion** — the model and read endpoints only.
- **Shared-trip settlement / split pricing** — blocked on an open decision
  (below).

## Open decision (blocks 3B, not this envelope)

**How is a shared trip's revenue split between shippers** — per-shipment
charge, or per-vehicle-share apportionment? This drives the ledger design and
is awkward to change later, because a shared trip is N shipments on 1 physical
trip. 3A avoids the question by having exactly one shipper per shipment; the
decision can be taken before 3B is designed.

## Definition of done
- `apps/goods` with the models/services/API above, org-scoped.
- Tests: quote stores basis and terms; expired quote refused; custody events are
  append-only and a correction requires a pointer and reason; manifest release
  blocked by a discrepancy; missing/extra/damaged create exceptions; delivery
  evidence without recipient verification is insufficient; return creates a
  linked reverse shipment with its own custody history; cargo capacity is
  checked independently of seat capacity; branch staff cannot mutate custody
  history.
- Full suite green, `check` clean, no pending migrations, README updated.
- One commit.
