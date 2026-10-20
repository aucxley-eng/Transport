# Envelope 2A scope — on-demand rides (dispatch and assignment)

Source of truth: `Kenya_Transport_and_Logistics_Architecture.pdf`, **§8 On
demand rides and corporate transport** (p. 10), the §4 driver-app and
customer-journey screens, and the data-model inventory (p. 12) which names
*ride requests*, *assignments, trips, stops, offers*, *shifts* and *zones*.
Every invariant below is quoted from that blueprint; nothing here is invented.

> Filename note: the file is called `phase2b-scope.md` but this document scopes
> **Envelope 2A** (on-demand rides). It is kept under the original name so
> existing links and the README continue to resolve.

This is the second envelope. Envelope 1 (scheduled passenger booking) is
delivered as of the writing of this document; corporate shuttles (2B) and
charter/hire (2C) were delivered afterwards and never received scope
documents of their own — see the two entries under "Explicitly out of scope"
below for where their record actually lives.

## Goal

Deliver the on-demand ride lifecycle end to end: **quote and ride request →
eligible driver offers → atomic driver acceptance → arrival and pickup
verification → active trip → complete and settle fare**, with the blueprint's
matching, privacy and failed-matching guarantees.

## Blueprint invariants being implemented (§8)

1. **Eligibility filtering.** "Dispatch filters approved drivers by
   availability, vehicle class, service zone and document status." A driver is
   eligible only if `Driver.status = APPROVED` **and** required documents are
   `VERIFIED` and not expired. An expired PSV licence or insurance must
   remove the driver from the pool immediately.
2. **Time-limited offers.** Dispatch "sends time-limited offers" to eligible
   drivers.
3. **First valid acceptance wins.** "The first valid acceptance wins through
   an atomic assignment operation; later or expired acceptances are
   rejected." Implemented with a row lock on the request, exactly like the
   seat-hold pattern from Envelope 1.
4. **Acceptance and start are separate events.** "Offer acceptance and trip
   start are separate events" (Figure 4) — accepting an offer never starts
   the trip; the trip starts on verified pickup.
5. **Pickup verification.** "Confirm pickup through an agreed method such as
   a trip PIN." A short PIN, verified by the passenger at the vehicle.
6. **Fare from real events.** "Start time, route events and approved waiting
   rules support fare calculation."
7. **Destination change.** "Destination changes require a revised estimate and
   customer acceptance where appropriate."
8. **Cancellation and no-show.** "Record cancellations and no-shows with
   reasons, evidence and the applicable price policy" — a cancellation carries
   a reason and a fee per the quoted price policy.
9. **Failed matching is explicit.** "If no driver accepts, show the customer
   a clear retry, scheduled alternative or cancellation outcome and release
   any reservation or authorization according to the payment policy."
10. **Safety surfaces state reality.** "An emergency button must state its
    actual response path." Trip sharing, incident reporting and a documented
    escalation path are modelled, not decorative.
11. **Contact privacy.** "Customer tracking access is limited to the relevant
    journey and expires. Drivers see contacts only for assigned [trips]."
    A driver can read passenger contact details only while the trip is
    assigned to them; tracking links are scoped and expire.

## Scope — new `apps/rides`

### Models
- `ServiceZone` — named polygon/centroid + radius; the "service zone" filter.
- `DriverShift` — driver availability window and status (available, on trip,
  off duty). Powers the "availability" filter.
- `RideRequest` — customer, pickup/destination `Place`, accepted `Quote`,
  required vehicle class, status (`requested → assigned → enroute → arrived →
  in_progress → completed`, plus `cancelled`, `no_show`, `unmatched`).
- `DriverOffer` — request, driver, `expires_at`, state (`pending`,
  `accepted`, `declined`, `expired`). Time-limited.
- `Trip` — one per fulfilled request: assignment, driver, vehicle, trip PIN,
  state (`assigned → arrived → in_progress → completed`), started/ended
  timestamps, distance/time totals.
- `TripEvent` — immutable ordered log (offered, accepted, arrived, pin
  verified, started, destination changed, completed, cancelled). Feeds fare.
- `RideCancellation` — reason, evidence note, applicable price policy and fee.
- `TripIncident` — incident report with escalation path and status.

### Services (transactional, reuse Envelope-1 patterns)
- `eligible_drivers(ride_request)` — approved + on shift + vehicle class +
  zone + **document status valid**. One query path, easy to test.
- `dispatch_offers(ride_request, window_minutes)` — create expiring offers to
  the eligible pool; emit outbox events.
- `accept_offer(offer)` — **atomic**: row-lock the request, reject if already
  assigned or the offer expired, otherwise create the `Trip` and mark all
  other offers expired. First valid acceptance wins.
- `verify_pickup(trip, pin)` — constant-time PIN compare, start the trip
  (separate from acceptance), emit event.
- `change_destination(trip, place)` — produces a **revised estimate**
  (`Quote`) requiring explicit customer acceptance before it applies.
- `settle_fare(trip)` — compute from start time, events and waiting rules;
  reuse the payments seam.
- `fail_matching(ride_request)` — mark unmatched and offer the customer the
  explicit choices: **retry**, **scheduled alternative** (a `booking`
  `Departure` on the same corridor), or **cancel + release authorization** per
  the payment policy.
- `expire_offers()` — sweep expired offers (management command).

### API
- `GET /api/v1/rides/quote/` — pickup/destination → versioned `Quote`.
- `POST /api/v1/rides/` — submit a ride request.
- `POST /api/v1/rides/{id}/dispatch/` — create offers (dispatcher/admin).
- `POST /api/v1/offers/{id}/accept/` and `/decline/` — driver.
- `POST /api/v1/trips/{id}/arrive/`, `/verify-pin/`, `/complete/`.
- `POST /api/v1/trips/{id}/change-destination/`, `/cancel/`.
- `POST /api/v1/rides/{id}/fail/` — failed matching → retry/alternative/cancel.
- `GET /api/v1/trips/{id}/` — tracking view, scoped to the journey and expiring.
- Driver availability: `POST /api/v1/driver/shift/`.

## Explicitly out of scope (deferred)
- **Corporate travel and recurring shuttles (§8 second half)** — deferred from
  this envelope to 2B, and later delivered as `apps/corporate` (employee
  eligibility, cost centres, budgets, approval thresholds, travel requests,
  shuttles, recurring roster and schedule templates producing dated
  departures). No scope document was written for 2B; the implementation, its
  tests and the README status table are the record.
- **Charter and vehicle hire** — deferred from this envelope and later
  delivered as Envelope 2C, `apps/charter` (hire requests, vehicle+driver
  reservations covering travel and turnaround buffers, deposits, permitted
  extras). No scope document was written for 2C; same note as above.
- **Live GPS ingestion and background location tracking** (§14) — the model
  and read endpoints land here; the sampling/streaming pipeline does not.
- **Push/SMS notification delivery** — outbox events are emitted; the actual
  gateway workers follow the existing outbox relay pattern.
- **Offline driver-app sync queue** (§14).
- **Live M-PESA callback signature verification** — still gated on live
  credentials (carried from Phase 1).

## Definition of done
- `apps/rides` with the models/services/API above, org-scoped via the existing
  `IsOrganizationAdmin` + `_OrgScopedViewSet` pattern.
- Tests: expired/suspended/expired-document driver excluded from dispatch;
  first-accept-wins with a second acceptance rejected; expired offer rejected;
  acceptance does **not** start the trip; wrong PIN rejected; destination
  change requires acceptance; cancellation records reason + fee; failed
  matching offers the three explicit outcomes; driver cannot read contact
  details for unassigned trips.
- Full suite green, `check` clean, no pending migrations, README updated.
- One commit.
