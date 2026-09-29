# Envelope 3D scope: local courier with one pickup, many recipients

## Why this envelope exists

Freight is finished: 3A books a consignment to one consignee, 3B shares a
lorry between shippers, 3C changes the plan once the truck has left. What the
platform still cannot express is the job that fills a courier van every morning
in Nairobi — one sender, one pickup, five parcels, five different recipients,
five different addresses, five different proofs of delivery.

The blocker is structural, not a missing feature. `Shipment` carries a single
order-level consignee (`recipient_name`, `recipient_phone`) and `Package` has no
consignee at all, so a multi-recipient order has nowhere to put its recipients.
Delivery evidence hangs off the shipment, not the parcel, so "the right person
signed for the right box" cannot be recorded. And `quote_shipment` takes a
client-supplied `total_minor`, so nothing in the repository can price a parcel at
all — the rate cards exist but nothing reads them.

This envelope makes courier a working loop: quote per parcel from a tariff,
book, load onto a van, deliver parcel by parcel, prove each delivery, and return
a single parcel when one fails. It is the first consumer of the rate card.

## Product decisions

### 1. The parcel is the delivery unit, not the order

A courier order is one pickup and many parcels; each parcel carries its own
recipient and its own drop place. `Package.recipient_name`,
`Package.recipient_phone` and `Package.recipient_place` are added for that, and
the order-level `Shipment.recipient_name` / `recipient_phone` become optional
because on a multi-recipient order there is no single consignee to name.

The alternative — a "recipient" free-text on the order listing several people —
was rejected: it cannot be filtered, cannot be called to confirm delivery, and
cannot be proved. Freight keeps its single order-level consignee, and a freight
booking without one is still refused.

### 2. Price each parcel on chargeable weight against a weight-band tariff

Courier is priced per parcel, and a light bulky box is charged on its size, not
its mass. Each parcel's chargeable weight is the greater of its actual weight and
its volumetric weight, the basis is recorded, and the order total is the exact
sum of the parcel charges.

The tariff is a `RateCard` of `RateLine` weight bands — "up to 1 kg: 300,
1–5 kg: 600" — which is how courier tariffs are actually written and which the
existing `from_value` / `to_value` / `amount_minor` model already expresses
exactly. A parcel whose chargeable weight falls outside every band is refused
rather than priced at the nearest band, because guessing a price is how a
courier company loses money quietly.

The lowest band doubles as the minimum charge: a 200 g parcel in a "0–1 kg: 300"
band costs 300. No separate minimum-charge concept is invented.

### 3. The account's own rate card wins; no card means no quote

`RateCard.organization` is already the per-account tariff, and null means
platform-wide. Resolution prefers the account's own active card for the courier
service, falls back to the platform card, and refuses with an explanation when
neither exists. This is the business-account answer: a company that sends fifty
parcels a day gets a `RateCard` of its own, and every later quote uses it
without a code change.

### 4. Delivery is proved per parcel, to the named recipient

A courier order with more than one recipient has one delivery record per parcel.
`ShipmentEvidence.package` is added, required for multi-recipient orders, and the
named recipient must match the parcel's recipient. The blueprint is explicit:
"Do not let a photograph alone prove that the intended recipient received the
correct package." A photo is refused as sole proof, and a signature from someone
else is refused as well — the check is on the *name*, not the presence of a
signature.

### 5. The plan, not the trip, is where courier drops live — and the drops must fit

A shared van makes many drops, so `propose_consolidation` must build a stop for
every distinct parcel drop place rather than only the order's collection and
destination. That also means the existing single-destination trip gate cannot
apply to a multi-drop order: the order's `destination` is its final drop, while
the plan is the authority on the whole sequence.

Two guards make that safe. First, the drop sequence must fit the time the van
actually has: collection plus every distinct drop costs the existing
`SERVICE_MINUTES` per stop, and the total must fit inside the trip window and the
driver's working hours, or the plan is refused naming the overrun. Second, a
courier order is refused on any trip whose service is a passenger service, so
parcel work can never ride on a bus's published timetable.

### 6. A return after a failed attempt is a first-class path

`create_return_shipment` currently only accepts parcels that were delivered,
missing or damaged, because it was written for freight. The common courier case
is the opposite: the recipient was absent, the parcel was never delivered, and it
is going back to the sender. A parcel with an open failed-attempt exception is
therefore returnable, the reverse consignment is collected from *that parcel's*
drop rather than the order's destination, and the rest of the order is untouched.

### 7. Money is quoted and frozen here, collected in step 5

The order's agreed total is computed and frozen at booking, per parcel, against a
named rate card. Actually taking it — prepaid, cash on delivery, or monthly
account billing — needs an order-to-shipment link and is roadmap step 5's
financial controls. This envelope does not pretend to have solved collection.

## Blueprint rules this envelope enforces

- **Package labels.** "Package labels, proof of collection and proof of delivery" (§4). The existing unique `PKG-` label is the scan key on the manifest, and collection evidence stays order-level while delivery evidence becomes per parcel.
- **Charging basis saved.** "Save the accepted quote and measurement basis" (§9). Each parcel stores its chargeable weight, the band that priced it, and its charge; the order stores the basis and the rate card that produced them.
- **Partial delivery stays visible.** "Partial deliveries remain visible at package level" (§9). One parcel delivered moves the order to `partially_delivered`; only when every parcel is delivered does the order close.
- **Discrepancies raise exceptions.** "Failed attempts create an exception with the next action: retry, hold at branch, return or claim" (§9). The exception names the parcel, and the parcel stays undelivered.
- **Returns link to the original.** "Returns create a linked reverse shipment with its own legs and custody history" (§9). One parcel can be returned on its own, from its own drop.
- **Mixed operations stay separate.** "Passenger capacity and cargo capacity remain independent… Parcel pickup windows must not override the published passenger timetable" (§10). Cargo capacity is still the 3B ledger, and goods work is refused on a passenger-service trip.
- **Discrepancies at the vehicle.** "Loading scans must reconcile against the manifest before release" (§10) — unchanged; every parcel of a courier order is on the manifest.
- **Explain infeasibility.** "If no feasible plan exists, return an explanation to dispatch rather than hiding unassigned work" (§11). Every refusal here names its cause, as 3B and 3C do.
- **Direct delivery skips hubs.** "Direct deliveries skip hubs; intercity parcels retain a custody event at every transfer" (§9). Same-day courier is a direct delivery; no hub model is invented for it.

## Data model

No new app. Courier lives in `apps/goods/courier.py` beside `consolidation.py`
and `insertion.py`, because it is the same goods domain reusing the same
manifest, custody, capacity and plan machinery.

- `Package.recipient_name`, `Package.recipient_phone` — the parcel's own consignee (blank for freight, which keeps its order-level consignee).
- `Package.recipient_place` (FK `catalog.Place`) — where this parcel is dropped. Null for freight; required for every parcel of a courier order.
- `Package.chargeable_weight_kg`, `Package.agreed_charge_minor`, `Package.pricing_snapshot` (JSON) — the money and the basis, frozen at booking, so a later rate-card edit cannot rewrite a booked order.
- `Shipment.recipient_name`, `Shipment.recipient_phone` — now optional; freight still refuses a booking without them.
- `ShipmentEvidence.package` (nullable FK `Package`) — which parcel this evidence is about.
- `MeasurementBasis.CHARGEABLE_WEIGHT` — the order-level basis when parcels are priced individually, since each parcel may be charged on a different basis.
- `apps/catalog/tariff.py` — `resolve_rate_card(organization, service)`: the account's own active card, else the platform card, else `None` with the reason. Catalog owns `RateCard`, so catalog resolves it.

## Invariants under test

1. A courier order books one collection and N parcels, each with its own recipient and drop place; a parcel missing either is refused by name.
2. The order's destination is a place at least one parcel is destined for; a destination where nothing is going is refused.
3. A courier order is a shared load; a full truckload is refused on a van.
4. A freight booking still requires an order-level consignee.
5. Each parcel's chargeable weight is the greater of actual and volumetric, and the basis that priced it is recorded on the parcel.
6. A parcel whose chargeable weight falls outside every band on the card is refused, not priced at the nearest band.
7. The order total is exactly the sum of the per-parcel charges, and each parcel's charge is the band that covers it.
8. The account's own rate card wins over the platform card; with neither, the quote is refused with an explanation rather than priced at zero.
9. A later rate-card edit changes neither an accepted quote nor a booked order's money.
10. The plan carries a stop for every distinct parcel drop place, and the drop sequence that exceeds the trip's window or the driver's working hours is refused with the overrun named.
11. A courier order is refused on a trip whose service is a passenger service, and freight keeps its single-destination gate.
12. Delivering one parcel of a multi-parcel order moves it to `partially_delivered` with the remaining parcels visible and untouched; delivering them all closes it.
13. Delivering a parcel twice is refused, and evidence for a parcel that is not delivered is refused.
14. Delivery evidence on a multi-recipient order must name the parcel, must prove the recipient, and is refused when the named recipient is not that parcel's recipient.
15. A failed attempt raises an exception against the parcel, and the parcel is not delivered by being failed at.
16. A parcel that failed delivery can be returned to the original sender from its own drop, while the rest of the order is left alone.
17. Cargo weight, volume and compartment limits still count every parcel, and cross-organization access to courier orders is refused.

## API surface

| Area | Endpoints |
|---|---|
| Courier quoting | `POST /api/v1/shipments/courier-quote/` — per-parcel breakdown against the resolved rate card |
| Courier booking | `POST /api/v1/shipments/` with a courier service and parcel specs carrying recipients and drops |
| Rate cards | `GET/POST /api/v1/rate-cards/` — existing, now load-bearing |
| Per-parcel delivery | `POST /api/v1/packages/{id}/deliver/` |
| Per-parcel evidence | `POST /api/v1/shipments/{id}/evidence/` with `package` |
| Failed attempt | `POST /api/v1/shipments/{id}/exceptions/` with `package` |
| Returns | `POST /api/v1/shipments/{id}/return/` with the parcels to reverse |

## Explicitly out of scope

- **Intercity parcels, hubs, line-haul and hub transfers** (§10). A second envelope: hub zones and bins, a custody event at every transfer, and intercity manifests. `Service.ServiceType.PARCEL` stays unused.
- **Warehouse inventory and fulfillment** (§10) — owner, SKU, lot, quantity, bin, reservation, picking, packing. Its own domain, its own envelope.
- **Zone pricing.** The blueprint lists zone as one permissible basis, not a requirement, and there is no zone-to-place mapping in the catalog. Inventing one for same-day city work is a bigger decision than this envelope. Weight bands only.
- **Collecting the money.** Prepaid, cash on delivery and account billing need an order-to-shipment link and belong to roadmap step 5.
- **Vehicle-level service approval.** §10 says "Only vehicles approved for the relevant services can receive mixed work", and `Vehicle` has no approved-services field. Adding it touches every dispatch path, so the guard here is the trip-level one (goods never joins a passenger-service trip); vehicle approval arrives with the fleet envelope.
- **External route optimization** (§11). Drop order is deterministic — the plan's, by service window — and no external solver is called.
- **Sender self-service, notifications, label printing, driver app.**
- **Special handling** (§10): dangerous goods and temperature control stay disabled until the business can operate them.

## Definition of done

- The seventeen invariants above are covered by tests in `apps/goods/tests/test_courier.py`.
- Full suite green, `manage.py check` clean, no migration drift, no unused imports.
- README updated with the 3D API surface and invariants.
