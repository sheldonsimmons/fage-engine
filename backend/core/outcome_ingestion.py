"""
core/outcome_ingestion.py — Universal Outcome Ingestion.

The canonical internal service for recording what happened to a WorkItem
("closed won", "resolved", "claim approved", ...) reported by ANY external
system through the standard contract, without CostPilot needing per-platform
code. Completes the loop the pull-based adapters (core/outcome_adapters/*.py)
only cover for Salesforce/ServiceNow today: Universal Connection -> AI
Activity -> Business Work -> Business Outcome -> Business Impact.

Deliberately factored out of api/routes_router.py rather than inlined into
route_payload(): POST /api/route mode="outcome" is meant to stay the one
public entry point, but the actual resolution/idempotency/write/conflict
logic lives here so it's callable from anywhere (a future direct endpoint,
an SDK's track_outcome(), an internal batch job) without duplicating it --
the same reason _resolve_work_item() and _sync_object_outcomes() are
already plain importable functions rather than endpoint-only code.

Design constraints, all deliberate (see the design doc this was built
from for the full reasoning):
  - event_id is MANDATORY here (unlike /api/route's activity modes, where
    it's optional) -- outcome data drives business-value reporting, and
    retried webhook delivery duplicating a "closed won" would double-count
    real dollars in every surface that reads WorkItemOutcome.
  - Idempotency is key-based (workspace_id, event_id), never
    content-diffing -- content-diffing is what caused a real production
    bug (1,594 duplicate WorkItemOutcomeEvent rows from a naive/aware
    datetime comparison always evaluating "changed"; see
    scripts/cleanup_duplicate_outcome_events.py).
  - WorkItemOutcome's current-state row is only ever overwritten by an
    event whose outcome.date is the same age or newer than what's already
    stored. A stale/out-of-order event still gets appended to
    WorkItemOutcomeEvent (history is never lossy) but never regresses
    current state.
  - WorkItem resolution reuses api/routes_router.py's _resolve_work_item()
    verbatim (imported lazily inside the function below, to avoid a
    module-load-time circular import with routes_router importing this
    module) rather than a second implementation -- this codebase has twice
    been bitten by two independently-computed "same thing"s silently
    diverging (the datetime bug above; a documented 1727%-coverage bug
    from two independently-computed totals, see
    core/metrics_query.py::compute_outcome_coverage()'s docstring).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from core.outcome_contract import MAX_OUTCOME_STRING_FIELD_LENGTH, RETRIEVAL_METHOD_PUSH
from database.models import WorkItemOutcome, WorkItemOutcomeEvent

MIN_OUTCOME_DATE = datetime(2000, 1, 1)
MAX_FUTURE_SKEW = timedelta(days=1)


def _to_naive_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """
    Normalize outcome.date to a naive UTC datetime, matching this
    codebase's convention everywhere else (datetime.utcnow(), no tzinfo).
    Real, live-reproducing bug found via Playwright verification of
    connector-manager.html's "Also send a test outcome" button: the
    browser's `new Date().toISOString()` produces a "Z"-suffixed string,
    which pydantic parses into a TIMEZONE-AWARE datetime -- comparing that
    against the naive datetimes used everywhere below (MIN_OUTCOME_DATE,
    datetime.utcnow(), a stored outcome_date) raised
    "TypeError: can't compare offset-naive and offset-aware datetimes",
    a 500 on every real caller whose client library timestamps in ISO
    8601 with an explicit offset (the norm, not the exception). Normalized
    once, immediately on entry, rather than special-cased at each
    comparison site -- the same discipline this module's own docstring
    already asks for around outcome.date semantics.
    """
    if dt is None or dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _validate_payload(req) -> None:
    outcome = req.outcome_context
    string_fields = {
        "outcome.status": outcome.status,
        "outcome.owner": outcome.owner,
        "outcome.source_object": outcome.source_object,
        "outcome.external_id": outcome.external_id,
    }
    for field_name, value in string_fields.items():
        if value is not None and len(value) > MAX_OUTCOME_STRING_FIELD_LENGTH:
            raise HTTPException(
                status_code=422,
                detail=f"{field_name} exceeds the {MAX_OUTCOME_STRING_FIELD_LENGTH}-character limit.",
            )
    if outcome.date is not None:
        now = datetime.utcnow()
        if outcome.date < MIN_OUTCOME_DATE or outcome.date > now + MAX_FUTURE_SKEW:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"outcome.date must be between {MIN_OUTCOME_DATE.date()} and "
                    f"{MAX_FUTURE_SKEW.days} day(s) in the future."
                ),
            )


def ingest_outcome(db: Session, req) -> dict:
    """
    Record a business outcome for the WorkItem identified by req.work_context.
    `req` is a RouteRequest already run through _normalize_universal_request()
    (mode == "outcome", work_context and outcome_context both present --
    enforced there; re-validated here defensively since this function is
    meant to be callable from more than just that one entry point).

    Returns a plain dict (not api.routes_router.RouteResponse, deliberately
    -- keeps this module decoupled from that endpoint-specific schema):
    {governed_request_id, department, work_item_id, outcome_recorded,
     current_state_updated, duplicate}
    """
    if not req.outcome_context:
        raise HTTPException(status_code=422, detail="mode='outcome' requires an outcome object.")
    if not req.work_context:
        raise HTTPException(status_code=422, detail="mode='outcome' requires a work object.")
    if not req.event_id:
        raise HTTPException(
            status_code=422,
            detail=(
                "event_id is required for mode='outcome'. Outcome data drives business-value "
                "reporting -- an unprotected retry would double-count real outcomes."
            ),
        )

    # Normalize BEFORE validation -- _validate_payload() and every ordering
    # comparison below assume a naive UTC datetime. See _to_naive_utc()'s
    # docstring for the real bug this closes.
    req.outcome_context.date = _to_naive_utc(req.outcome_context.date)

    _validate_payload(req)

    workspace_id = (req.actor_workspace_id or "default").strip() or "default"
    outcome = req.outcome_context

    # Idempotency: a resubmission of the same event_id (retry, at-least-once
    # webhook delivery) must not append a second history row or move
    # current state again. Key-based, not content-diffing -- see module
    # docstring for why.
    existing_event = db.query(WorkItemOutcomeEvent).filter(
        WorkItemOutcomeEvent.event_id == req.event_id,
        WorkItemOutcomeEvent.workspace_id == workspace_id,
    ).first()
    if existing_event:
        return {
            "governed_request_id": None,
            "department": req.work_department or req.department or "Support",
            "work_item_id": None,
            "outcome_recorded": True,
            "current_state_updated": None,  # not recomputed on replay
            "duplicate": True,
        }

    from core.governed_requests import new_governed_request_id
    from api.routes_router import _resolve_department, _resolve_work_item

    governed_request_id = new_governed_request_id()
    department = _resolve_department(db, req)
    work_item = _resolve_work_item(db, req, department)
    if work_item is None:
        # _resolve_work_item() only returns None when neither work_item_id
        # nor work_context was supplied -- already guarded against above --
        # but stay defensive rather than assume.
        raise HTTPException(status_code=422, detail="Could not resolve a work item for this outcome.")

    source_system = (
        req.source_platform
        or (req.work_context.source_platform if req.work_context else None)
        or "custom"
    ).strip() or "custom"
    source_object = (outcome.source_object or req.work_context.type or "custom").strip() or "custom"
    external_id = (outcome.external_id or req.work_context.external_id).strip()

    current = db.query(WorkItemOutcome).filter_by(work_item_id=work_item.id).first()

    # Never let a stale or out-of-order event regress current state. No
    # baseline to compare against (no existing row, or the existing row has
    # no date) or no incoming date to compare with -> treat as newer.
    if current is None or current.outcome_date is None or outcome.date is None:
        current_state_updated = True
    else:
        current_state_updated = outcome.date >= current.outcome_date

    if current_state_updated:
        if current is None:
            current = WorkItemOutcome(work_item_id=work_item.id, workspace_id=workspace_id)
            db.add(current)
        current.outcome_status = outcome.status
        current.outcome_value = outcome.value
        current.outcome_date = outcome.date
        current.outcome_success = outcome.success
        current.is_closed = outcome.is_closed
        current.owner = outcome.owner
        current.source_system = source_system
        current.source_object = source_object
        current.external_id = external_id
        current.source_modified_at = outcome.date
        current.last_synced_at = datetime.utcnow()
        current.retrieval_method = RETRIEVAL_METHOD_PUSH
        current.is_simulation = bool(req.synthetic_simulation)

    # Append-only history -- always written, regardless of whether this
    # event moved current state, so a stale/out-of-order/disagreeing event
    # is never silently lost, only prevented from overwriting more-recent
    # truth.
    db.add(WorkItemOutcomeEvent(
        work_item_id=work_item.id,
        workspace_id=workspace_id,
        outcome_status=outcome.status,
        outcome_value=outcome.value,
        outcome_date=outcome.date,
        outcome_success=outcome.success,
        is_closed=outcome.is_closed,
        retrieval_method=RETRIEVAL_METHOD_PUSH,
        source_system=source_system,
        connection_key=req.connection_key,
        event_id=req.event_id,
        is_simulation=bool(req.synthetic_simulation),
        recorded_at=datetime.utcnow(),
    ))
    db.commit()

    return {
        "governed_request_id": governed_request_id,
        "department": department,
        "work_item_id": work_item.external_id,
        "outcome_recorded": True,
        "current_state_updated": current_state_updated,
        "duplicate": False,
    }
