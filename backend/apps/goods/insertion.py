"""Mid-route insertion, once the driver has already begun the route (blueprint 11).

3B planned a truck before it left and froze that plan. This module handles the
request that arrives afterwards: a consignment that can be picked up along the
way. The blueprint's rule is narrow, and it is the whole design:

    "Save each route plan version. Once a driver begins a route, preserve
    completed stops and communicate any remaining-stop changes clearly. Do not
    automatically reorder scheduled passenger stops for parcel convenience."

So an insertion never edits history. It publishes a *new* plan version that
carries the completed stops across untouched, leaves the remaining stops in the
order they were promised, adds the new stop by its own window, and writes down
what moved, by how much and who approved it.

The product decisions this envelope records (``docs/phase3c-scope.md``):

* Delay is tolerated, not ignored. An insertion may push an already-promised
  remaining stop by up to ``Organization.insertion_grace_minutes``; beyond that
  it is refused and the refusal names the stop and the overrun.
* A dispatcher may insert directly; a driver may be offered an insertion and
  accept it, first accept wins, re-checked under a row lock.
* The inserted consignment is priced for the leg it will actually travel and
  frozen at booking like any other quote. Nobody's price moves.
"""

from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Max, Sum
from django.utils import timezone

from apps.audit.models import record_audit
from apps.common.outbox import record_outbox
from apps.goods.consolidation import (
    FreightError,
    assess_coverage,
    validate_plan,
)
from apps.goods.models import (
    ConsolidationPlan,
    ConsolidationStop,
    FreightTrip,
    InsertionRequest,
    LoadType,
    Manifest,
    ManifestLine,
    PlanShipment,
    Shipment,
    ShipmentLeg,
)
from apps.goods.services import cargo_capacity

ZERO = Decimal("0")

# Insertion refusals, so dispatch reads a cause and not a bare "no".
REASON_TRIP_NOT_RUNNING = "the trip is not on the road"
REASON_TRIP_FINISHED = "the trip has finished"
REASON_NO_REMAINING_STOPS = "every stop on this trip is already completed"
REASON_NO_PUBLISHED_PLAN = "publish a consolidation plan before inserting"
REASON_WRONG_ORGANIZATION = "the consignment belongs to another organization"
REASON_SHIPMENT_CLOSED = "the consignment is already closed"
REASON_SHIPMENT_MOVING = "the consignment is already on a trip"
REASON_FULL_TRUCKLOAD_ALONE = "a full truckload travels alone"
REASON_TOO_HEAVY = "no remaining cargo weight for this consignment"
REASON_TOO_BULKY = "no remaining cargo volume for this consignment"
REASON_NO_COMPARTMENT = "no cargo compartment can take this consignment"
REASON_WRONG_DESTINATION = "the destination is not on this trip"
REASON_MULTI_DROP_INSERTION = "a multi-drop courier order is not inserted mid-route"
REASON_ALREADY_SERVED = "the collection point has already been served on this trip"
REASON_WINDOW_PASSED = "the collection window has already passed"
REASON_TOLERANCE_EXCEEDED = "would delay an already-promised stop past the tolerance"
REASON_WORKING_HOURS = "would exceed the driver's working hours"
REASON_STALE_REQUEST = "the trip has moved on since this request was made"
REASON_ALREADY_INSERTED = "this consignment is already on the trip"
REASON_ALREADY_DECIDED = "this request has already been decided"
REASON_OTHER_DRIVER = "this insertion was offered to another driver"
REASON_MID_ROUTE_INSERTION = "Mid-route insertion"

# A stop's service time: loading or handing over one consignment. Used to price
# the time an inserted stop adds to the stops after it.
SERVICE_MINUTES = 20

# The widest tolerance the platform will honour, whatever an operator's stored
# policy says. Mirrors the validator on Organization.insertion_grace_minutes.
MAX_TOLERANCE_MINUTES = 240


def _now():
    return timezone.now()


def _route_now(trip):
    """The route clock: never behind the planned start, so a trip cannot be
    measured from a moment before it was due to leave."""
    return max(_now(), trip.planned_start)


def _expiry_at(trip, grace_minutes):
    """An offer stops being interesting once the trip could have finished."""
    return min(_now() + timedelta(minutes=grace_minutes), trip.planned_end)


def current_plan(trip):
    return (
        trip.consolidation_plans.filter(status=ConsolidationPlan.Status.PUBLISHED)
        .order_by("-version")
        .first()
    )


def route_state(trip):
    """What the driver has done, what is still promised, and what the truck
    currently earns. One read for the driver's screen and dispatch's board."""
    plan = current_plan(trip)
    if plan is None:
        raise FreightError(REASON_NO_PUBLISHED_PLAN)
    stops = list(plan.stops.all())
    return {
        "trip": trip,
        "plan": plan,
        "completed_stops": [s for s in stops if s.is_completed],
        "remaining_stops": [s for s in stops if not s.is_completed],
        "shipments": list(plan.shipments.filter(accepted=True).select_related("shipment")),
        "coverage": assess_coverage(trip),
    }


# ---------------------------------------------------------------------------
# Stop progress
# ---------------------------------------------------------------------------


@transaction.atomic
def complete_stop(*, plan, stop, actor=None, note=""):
    """Mark a stop served.

    Completing a stop is a fact about the road, not an edit to a plan, so it is
    written on the published version and carried into every later version. It
    cannot be undone: that is what makes "preserve completed stops" enforceable
    rather than aspirational.
    """
    if not plan.is_published:
        raise FreightError(REASON_NO_PUBLISHED_PLAN)
    if plan.trip.started_at is None:
        raise FreightError("The driver has not begun this route yet.")
    stop = plan.stops.filter(pk=stop.pk).first()
    if stop is None:
        raise FreightError("That stop is not on the published plan.")
    if stop.is_completed:
        return stop
    stop.is_completed = True
    stop.completed_at = _now()
    stop.save(update_fields=["is_completed", "completed_at", "updated_at"])
    record_audit(
        actor=actor,
        organization=plan.organization,
        action="freight.stop_completed",
        entity_type="consolidation_stop",
        entity_id=stop.pk,
        reason=note,
        new_value={"place": stop.place.name, "sequence": stop.sequence},
    )
    return stop


# ---------------------------------------------------------------------------
# Remaining capacity along the route
# ---------------------------------------------------------------------------


def remaining_capacity(trip):
    """What the vehicle can still take, in whole-vehicle and per-compartment
    terms, from where it is now."""
    capacity = cargo_capacity(trip)
    free = {c["compartment"].pk: c["free_weight_kg"] for c in capacity["compartments"]}
    return {
        "free_weight_kg": capacity["free_weight_kg"],
        "free_volume_m3": capacity["free_volume_m3"],
        "compartments": free,
    }


def plan_exists_with_shipment(trip, shipment):
    """True when the consignment is already on the trip's published plan or on
    the vehicle's manifest: either way it is on board, once."""
    on_plan = PlanShipment.objects.filter(
        shipment=shipment,
        accepted=True,
        plan__trip=trip,
        plan__status=ConsolidationPlan.Status.PUBLISHED,
    ).exists()
    if on_plan:
        return True
    return Manifest.objects.filter(
        trip=trip, lines__package__shipment=shipment
    ).exists()


def _compartment_fits(remaining, package):
    """One compartment must take the whole package, as when it is loaded at the
    depot; a mid-route pickup is no more forgiving than a depot load."""
    for compartment_id, free in remaining["compartments"].items():
        if free >= package.weight_kg:
            return compartment_id
    return None


# ---------------------------------------------------------------------------
# Dispatcher insertion
# ---------------------------------------------------------------------------


def _grace_for(organization, tolerance_minutes=None):
    """The delay tolerance in force, never wider than the platform allows.

    The cap is a promise to the shippers behind the stops already on the plan,
    so a caller that passes a wider number does not get one.
    """
    grace = (
        organization.insertion_grace_minutes
        if tolerance_minutes is None
        else tolerance_minutes
    )
    return min(int(grace), MAX_TOLERANCE_MINUTES)


@transaction.atomic
def insert_consignment(
    *, organization, trip, shipment, actor=None, reason="", tolerance_minutes=None
):
    """Insert a consignment into a trip that is already running.

    The whole rule set runs before anything is published, exactly as 3B
    validates a proposal before publication: the trip must be on the road with
    stops still to serve, the consignment must fit what is left of the vehicle,
    and the delay it causes to any already-promised stop must stay inside the
    operator's tolerance. On success a new plan version is published and the
    change is announced. The manifest is amended separately, as its own new
    version, once the driver is at the pickup.

    The consignment is never repriced: it travels for the total it was already
    accepted at, covering only the leg from the insertion point forward.

    Returns the new published plan.
    """
    grace = _grace_for(organization, tolerance_minutes)
    refusal = _insertion_refusal(
        organization=organization,
        trip=trip,
        shipment=shipment,
        grace=grace,
    )
    if refusal is not None:
        raise FreightError(refusal)

    previous = current_plan(trip)
    tolerance_used, delayed = _tolerance_report(previous, shipment, grace)

    plan = _publish_inserted_version(
        organization=organization,
        trip=trip,
        previous=previous,
        shipment=shipment,
        actor=actor,
        reason=reason,
    )
    if plan is None:
        raise FreightError(_validation_refusal(previous, shipment))

    _announce(
        organization=organization,
        trip=trip,
        plan=plan,
        previous=previous,
        shipment=shipment,
        actor=actor,
        reason=reason,
        tolerance_minutes=grace,
        tolerance_used=tolerance_used,
        delayed=delayed,
    )
    return plan


def _insertion_refusal(*, organization, trip, shipment, grace):
    """Everything that stops the insertion, as a single explained reason."""
    if shipment.organization_id != organization.pk:
        return REASON_WRONG_ORGANIZATION
    if trip.status in (FreightTrip.Status.COMPLETED, FreightTrip.Status.CANCELLED):
        return REASON_TRIP_FINISHED
    if trip.started_at is None:
        return REASON_TRIP_NOT_RUNNING
    if shipment.status in (Shipment.Status.CANCELLED, Shipment.Status.DELIVERED):
        return REASON_SHIPMENT_CLOSED
    if shipment.legs.filter(
        status__in=(
            ShipmentLeg.Status.LOADED,
            ShipmentLeg.Status.DEPARTED,
            ShipmentLeg.Status.COMPLETED,
        )
    ).exists():
        return REASON_SHIPMENT_MOVING
    if plan_exists_with_shipment(trip, shipment):
        return REASON_ALREADY_INSERTED
    if shipment.is_multi_drop():
        # 3C inserts one consignment with one delivery end. A courier order has
        # a stop per parcel drop, and this module would publish a plan carrying
        # only the order's destination, silently promising fewer stops than the
        # order owes. Refused by name rather than half-inserted; planning a
        # multi-drop order is 3B's propose path, before the truck leaves.
        return REASON_MULTI_DROP_INSERTION
    if shipment.destination_id != trip.destination_id:
        return REASON_WRONG_DESTINATION
    if shipment.service_window_end <= _now():
        return REASON_WINDOW_PASSED

    plan = current_plan(trip)
    if plan is None:
        return REASON_NO_PUBLISHED_PLAN
    if plan.stops.filter(is_completed=True, place=shipment.collection).exists():
        # The truck has already been here; it cannot collect from here again.
        return REASON_ALREADY_SERVED
    remaining = [s for s in plan.stops.all() if not s.is_completed]
    if not remaining:
        return REASON_NO_REMAINING_STOPS
    if shipment.load_type == LoadType.FULL_TRUCKLOAD:
        return REASON_FULL_TRUCKLOAD_ALONE

    working_hours = getattr(trip.driver, "working_hours_per_day", None)
    if working_hours:
        route_hours = (trip.planned_end - _route_now(trip)).total_seconds() / 3600
        if route_hours > working_hours:
            return REASON_WORKING_HOURS

    capacity = remaining_capacity(trip)
    if shipment.total_weight_kg > capacity["free_weight_kg"]:
        return REASON_TOO_HEAVY
    if shipment.total_volume_m3 > capacity["free_volume_m3"]:
        return REASON_TOO_BULKY
    for package in shipment.packages.all():
        if _compartment_fits(capacity, package) is None:
            return f"{REASON_NO_COMPARTMENT} ({package.label})"

    _, delayed = _tolerance_report(plan, shipment, grace)
    late = [item["stop"] for item in delayed if item["minutes"] > grace]
    if late:
        return f"{REASON_TOLERANCE_EXCEEDED}: {', '.join(s.place.name for s in late)}"
    return None


def _tolerance_report(plan, shipment, grace):
    """How far the insertion pushes the stops after it, and which ones.

    The new stop is placed at the earliest point in the remaining order where
    its own window can be served. Stops already passed are untouched. Stops
    after the new one wait for the insertion's service time, and that wait is
    measured against the tolerance the operator set.
    """
    remaining = [s for s in plan.stops.all() if not s.is_completed]
    insert_at = len(remaining)
    for index, stop in enumerate(remaining):
        if stop.window_start > shipment.service_window_start:
            insert_at = index
            break

    # The driver cannot be in two places: the insertion is served from the later
    # of its own window start and the end of the stop before it.
    served_from = max(
        shipment.service_window_start,
        remaining[insert_at - 1].window_end if insert_at else _route_now(plan.trip),
    )
    served_to = served_from + timedelta(minutes=SERVICE_MINUTES)

    delayed = []
    worst = ZERO
    for stop in remaining[insert_at:]:
        pushed = served_to - stop.window_end
        if pushed.total_seconds() > 0:
            minutes = Decimal(pushed.total_seconds() / 60).quantize(Decimal("1"))
            delayed.append({"stop": stop, "minutes": minutes})
            worst = max(worst, minutes)
    return worst, delayed


def _publish_inserted_version(
    *, organization, trip, previous, shipment, actor, reason
):
    """Build, validate and publish the next version with the consignment added.

    Completed stops are copied across exactly as they stand: same place, same
    sequence, same window, same completed marker. Remaining stops keep their
    order. The new stop is inserted by its own window and everything after it
    shifts by the insertion's service time, which is what the tolerance measured.
    """
    remaining = [s for s in previous.stops.all() if not s.is_completed]
    completed = [s for s in previous.stops.all() if s.is_completed]

    insert_at = len(remaining)
    for index, stop in enumerate(remaining):
        if stop.window_start > shipment.service_window_start:
            insert_at = index
            break

    plan = ConsolidationPlan.objects.create(
        organization=organization,
        trip=trip,
        version=(previous.version + 1),
        supersedes=previous,
        status=ConsolidationPlan.Status.PROPOSED,
        proposed_by=actor,
        change_reason=reason or REASON_MID_ROUTE_INSERTION,
    )

    served_from = max(
        shipment.service_window_start,
        remaining[insert_at - 1].window_end if insert_at else _route_now(trip),
    )
    served_to = served_from + timedelta(minutes=SERVICE_MINUTES)

    for stop in completed:
        ConsolidationStop.objects.create(
            organization=organization,
            plan=plan,
            place=stop.place,
            sequence=stop.sequence,
            window_start=stop.window_start,
            window_end=stop.window_end,
            is_completed=True,
            completed_at=stop.completed_at or _now(),
        )

    # The remaining order: the old stops where they were, the new one between
    # them, renumbered from the end of the completed run. A pickup at a place
    # the route already stops at widens that stop instead of duplicating it.
    entries = [
        (stop.place, stop.window_start, stop.window_end) for stop in remaining
    ]
    if shipment.collection_id in {stop.place_id for stop in remaining}:
        widened = next(
            index
            for index, entry in enumerate(entries)
            if entry[0].pk == shipment.collection_id
        )
        place, window_start, window_end = entries[widened]
        entries[widened] = (
            place,
            min(window_start, served_from),
            max(window_end, served_to),
        )
    else:
        entries.insert(insert_at, (shipment.collection, served_from, served_to))

    for index, (place, window_start, window_end) in enumerate(entries):
        if index > insert_at:
            # The driver waited for the new stop, so the promise moves with him.
            window_start = served_to
            window_end = max(window_end, served_to)
        ConsolidationStop.objects.create(
            organization=organization,
            plan=plan,
            place=place,
            # The completed run keeps its own numbers; the rest continues from
            # there, the way 3B numbers stops from zero.
            sequence=len(completed) + index,
            window_start=window_start,
            window_end=window_end,
        )

    # The loading order follows the route, so the new consignment is picked up
    # where its stop falls and the consignments behind it move down one. The stop
    # sequence is the authority; this keeps the two telling the same story.
    place_order = {place.pk: index for index, (place, _, _) in enumerate(entries)}

    def _loading_key(consignment):
        index = place_order.get(consignment.collection_id)
        # A consignment collected where the truck has already been is on board.
        return (0, 0) if index is None else (1, index)

    carried = [
        (entry.shipment, entry.reason, entry.revenue_minor)
        for entry in previous.shipments.filter(accepted=True).select_related("shipment")
        if entry.shipment_id != shipment.pk
    ]
    carried.sort(key=lambda row: _loading_key(row[0]))
    at = sum(1 for row in carried if _loading_key(row[0]) < _loading_key(shipment))
    carried.insert(
        at, (shipment, "Inserted mid-route", shipment.agreed_total_minor)
    )
    for index, (consignment, entry_reason, revenue) in enumerate(carried, start=1):
        PlanShipment.objects.create(
            organization=organization,
            plan=plan,
            shipment=consignment,
            accepted=True,
            sequence=index,
            reason=entry_reason,
            revenue_minor=revenue,
        )

    # The delivery end of the inserted consignment, unless the trip already
    # stops there: the driver is not going to the market twice.
    if trip.destination_id is not None and shipment.destination_id != shipment.collection_id:
        if shipment.destination_id not in {
            stop.place_id for stop in previous.stops.all()
        }:
            ConsolidationStop.objects.create(
                organization=organization,
                plan=plan,
                place=shipment.destination,
                sequence=len(completed) + len(entries),
                window_start=shipment.service_window_start,
                window_end=shipment.service_window_end,
            )

    plan = validate_plan(
        plan,
        loaded_shipment_ids=[
            entry.shipment_id
            for entry in previous.shipments.filter(accepted=True)
        ],
    )
    if plan.status != ConsolidationPlan.Status.VALIDATED:
        return None
    plan.status = ConsolidationPlan.Status.PUBLISHED
    plan.published_at = _now()
    plan.save(update_fields=["status", "published_at", "updated_at"])
    previous.status = ConsolidationPlan.Status.SUPERSEDED
    previous.save(update_fields=["status", "updated_at"])
    return plan


def _validation_refusal(previous, shipment):
    return (
        f"{shipment.reference} cannot be inserted: the plan failed validation "
        f"against the vehicle, the working day or the promised windows."
    )


def _announce(
    *,
    organization,
    trip,
    plan,
    previous,
    shipment,
    actor,
    reason,
    tolerance_minutes,
    tolerance_used,
    delayed,
):
    """"Communicate any remaining-stop changes clearly."

    One outbox event and one audit entry, both naming the new stop, every stop
    that moved, by how much, and who approved it -- the numbers dispatch will
    read to the driver and the affected shippers.
    """
    summary = {
        "trip": str(trip.pk),
        "version": plan.version,
        "supersedes": previous.version,
        "inserted": {
            "shipment": shipment.reference,
            "stop": shipment.collection.name,
            "window": [shipment.service_window_start.isoformat(), shipment.service_window_end.isoformat()],
        },
        "tolerance_minutes": tolerance_minutes,
        "tolerance_used_minutes": int(tolerance_used),
        "delayed_stops": [
            {
                "stop": row["stop"].place.name,
                "sequence": row["stop"].sequence,
                "delay_minutes": int(row["minutes"]),
            }
            for row in delayed
        ],
        "approved_by": getattr(actor, "phone", None),
        "reason": reason,
    }
    record_outbox(
        event_type="freight.route_changed",
        aggregate_type="freight_trip",
        aggregate_id=trip.pk,
        organization_id=organization.pk,
        payload=summary,
    )
    record_audit(
        actor=actor,
        organization=organization,
        action="goods.consignment_inserted",
        entity_type="consolidation_plan",
        entity_id=plan.pk,
        reason=reason,
        previous_value={"version": previous.version},
        new_value=summary,
    )
    return summary


# ---------------------------------------------------------------------------
# Manifest amendment
# ---------------------------------------------------------------------------


@transaction.atomic
def amend_manifest(*, trip, packages, actor=None, reason=""):
    """Add mid-route packages as a new manifest version.

    The released manifest is evidence of what the vehicle was checked against,
    so it is superseded rather than edited: a departure manifest lists the
    packages assigned to a trip (blueprint 10), and a truck that picked up
    something new must have that reconciled and re-released like any other load.
    """
    if trip.started_at is None:
        raise FreightError("Only a trip on the road can be amended.")
    packages = list(packages or [])
    if not packages:
        raise FreightError("An amendment needs at least one package.")
    if Manifest.objects.filter(trip=trip, lines__package__in=packages).exists():
        raise FreightError(REASON_ALREADY_INSERTED)
    current = Manifest.objects.filter(trip=trip).order_by("-version").first()
    if current is None or current.status != Manifest.Status.RELEASED:
        raise FreightError("A released manifest is required before amending.")

    capacity = remaining_capacity(trip)
    placements = {}
    for package in sorted(packages, key=lambda p: p.weight_kg, reverse=True):
        compartment_id = _compartment_fits(capacity, package)
        if compartment_id is None:
            raise FreightError(f"{REASON_NO_COMPARTMENT} ({package.label})")
        placements[package.pk] = compartment_id
        capacity["compartments"][compartment_id] -= package.weight_kg

    amended = Manifest.objects.create(
        organization=trip.organization,
        trip=trip,
        version=current.version + 1,
        supersedes=current,
        status=Manifest.Status.DRAFT,
        built_by=actor,
    )
    existing = list(current.lines.select_related("package", "compartment").all())
    carried = [
        {
            "package": line.package,
            "compartment_id": line.compartment_id,
            "label": line.label,
            "expected_weight_kg": line.expected_weight_kg,
            # The previous version was released, so its scan states are facts
            # about the load; the amendment does not rewrite them.
            "scan_state": line.scan_state,
        }
        for line in existing
    ]
    for package in packages:
        carried.append(
            {
                "package": package,
                "compartment_id": placements[package.pk],
                "label": package.label,
                "expected_weight_kg": package.weight_kg,
                # Handed over at the gate is not the same as scanned against the
                # manifest, and the release gate is where that gets proved.
                "scan_state": ManifestLine.ScanState.PENDING,
            }
        )
    _write_lines(amended, carried)
    record_audit(
        actor=actor,
        organization=trip.organization,
        action="freight.manifest_amended",
        entity_type="manifest",
        entity_id=amended.pk,
        reason=reason,
        previous_value={"version": current.version},
        new_value={
            "version": amended.version,
            "added": [p.label for p in packages],
        },
    )
    return amended


def _write_lines(manifest, rows):
    for row in rows:
        ManifestLine.objects.create(
            organization=manifest.organization,
            manifest=manifest,
            package=row["package"],
            compartment_id=row.get("compartment_id"),
            label=row["label"],
            expected_weight_kg=row["expected_weight_kg"],
            scan_state=row["scan_state"],
        )
    return manifest


# ---------------------------------------------------------------------------
# Driver offers
# ---------------------------------------------------------------------------


@transaction.atomic
def request_insertion(
    *, organization, trip, shipment, requested_by=None, tolerance_minutes=None, reason=""
):
    """Offer a consignment to the driver of a running trip.

    The request records what was free and which plan version the offer was
    measured against, so the acceptance can re-check both instead of trusting a
    screen the dispatcher saw a moment earlier.
    """
    grace = _grace_for(organization, tolerance_minutes)
    refusal = _insertion_refusal(
        organization=organization, trip=trip, shipment=shipment, grace=grace
    )
    if refusal is not None:
        raise FreightError(refusal)
    if InsertionRequest.objects.filter(trip=trip, shipment=shipment).exists():
        raise FreightError(REASON_ALREADY_INSERTED)

    plan = current_plan(trip)
    return InsertionRequest.objects.create(
        organization=organization,
        trip=trip,
        shipment=shipment,
        requested_by=requested_by,
        status=InsertionRequest.Status.PENDING,
        reason=reason,
        tolerance_minutes=grace,
        free_weight_kg=remaining_capacity(trip)["free_weight_kg"],
        plan_version=plan.version,
    )


class _Decision(Exception):
    """A refusal that still has to be recorded.

    A row status written inside the transaction that then raises would roll back
    with it, and a lost "refused" is worse than none: the offer would look open
    forever. So the decision travels out of the transaction and is written
    afterwards, in its own commit.
    """

    def __init__(self, status, reason):
        self.status = status
        self.reason = reason
        super().__init__(reason)


def accept_insertion(*, request, driver=None, reason=""):
    """First accept wins, under a lock, with the rules re-checked.

    "Use a transaction and version check when accepting an offer or assigning a
    trip. Lock or constrain the relevant resource interval. A previously valid
    availability screen is not proof that the vehicle is still free when the
    assignment reaches the server."
    """
    try:
        with transaction.atomic():
            return _accept_locked(request=request, driver=driver, reason=reason)
    except _Decision as decision:
        with transaction.atomic():
            locked = (
                InsertionRequest.objects.select_for_update()
                .filter(pk=request.pk)
                .first()
            )
            if locked is not None and locked.status == InsertionRequest.Status.PENDING:
                locked.status = decision.status
                locked.reason = decision.reason
                locked.decided_at = _now()
                locked.save(
                    update_fields=["status", "reason", "decided_at", "updated_at"]
                )
        raise FreightError(decision.reason)


def _require_assigned_driver(row, driver):
    """Only the driver holding the offer may decide it.

    An offer is addressed to one person on one vehicle; a colleague in the same
    organization answering on their behalf would be a decision nobody made.
    """
    if driver is None or row.trip.driver_id is None:
        return
    if row.trip.driver.user_id != driver.pk:
        raise FreightError(REASON_OTHER_DRIVER)


def _accept_locked(*, request, driver=None, reason=""):
    locked = (
        InsertionRequest.objects.select_for_update()
        .filter(pk=request.pk)
        .first()
    )
    if locked is None:
        raise FreightError(REASON_ALREADY_DECIDED)
    if locked.status != InsertionRequest.Status.PENDING:
        raise FreightError(REASON_ALREADY_DECIDED)
    _require_assigned_driver(locked, driver)
    # An offer only stands while the trip could still act on it.
    expiry = _expiry_at(locked.trip, locked.tolerance_minutes)
    if _now() > expiry:
        raise _Decision(InsertionRequest.Status.EXPIRED, REASON_STALE_REQUEST)

    plan = current_plan(locked.trip)
    if plan is None or plan.version != locked.plan_version:
        raise _Decision(InsertionRequest.Status.REFUSED, REASON_STALE_REQUEST)

    refusal = _insertion_refusal(
        organization=locked.organization,
        trip=locked.trip,
        shipment=locked.shipment,
        grace=locked.tolerance_minutes,
    )
    if refusal is not None:
        raise _Decision(InsertionRequest.Status.REFUSED, refusal)

    published = insert_consignment(
        organization=locked.organization,
        trip=locked.trip,
        shipment=locked.shipment,
        actor=driver,
        reason=reason or "Driver accepted a mid-route insertion",
        tolerance_minutes=locked.tolerance_minutes,
    )
    locked.status = InsertionRequest.Status.ACCEPTED
    locked.decided_by = driver
    locked.decided_at = _now()
    locked.reason = reason
    locked.resulting_plan = published
    locked.save(
        update_fields=[
            "status",
            "decided_by",
            "decided_at",
            "reason",
            "resulting_plan",
            "updated_at",
        ]
    )
    return published


@transaction.atomic
def decline_insertion(*, request, driver=None, reason=""):
    locked = (
        InsertionRequest.objects.select_for_update()
        .filter(pk=request.pk)
        .first()
    )
    if locked is None or locked.status != InsertionRequest.Status.PENDING:
        raise FreightError(REASON_ALREADY_DECIDED)
    _require_assigned_driver(locked, driver)
    locked.status = InsertionRequest.Status.DECLINED
    locked.decided_by = driver
    locked.decided_at = _now()
    locked.reason = reason
    locked.save(
        update_fields=["status", "decided_by", "decided_at", "reason", "updated_at"]
    )
    return locked


def insertion_summary(plan):
    """The numbers the change summary is built from, for the API."""
    return {
        "version": plan.version,
        "supersedes": plan.supersedes.version if plan.supersedes else None,
        "stops": [
            {
                "place": stop.place.name,
                "sequence": stop.sequence,
                "window": [stop.window_start, stop.window_end],
                "completed": stop.is_completed,
            }
            for stop in plan.stops.all()
        ],
        "committed_revenue_minor": plan.shipments.filter(accepted=True).aggregate(
            total=Sum("revenue_minor")
        )["total"]
        or 0,
        "next_version": plan.trip.consolidation_plans.aggregate(
            top=Max("version")
        )["top"]
        + 1,
    }
