"""
tests/test_route_outcome_conflict_rules.py — the 6 conflict/stale-event
scenarios from the Universal Outcome Ingestion design: WorkItem missing,
older-arrives-after-newer, two sources disagree, duplicate arrival
(covered in test_route_outcome_idempotency.py), value changes, and
reopened-after-closed. Core invariant under test: WorkItemOutcome's
current-state row is only ever overwritten by an event whose outcome.date
is the same age or newer than what's already stored -- a stale event still
appends to WorkItemOutcomeEvent but never regresses current state.
"""
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.routes_router import RouteRequest, route_payload
from database.db import Base
from database.models import WorkItem, WorkItemOutcome, WorkItemOutcomeEvent

NOW = datetime.utcnow()


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _outcome_request(event_id, status, date, source_platform="Custom Claims App", **outcome_overrides):
    outcome = {"status": status, "date": date.isoformat()}
    outcome.update(outcome_overrides)
    return RouteRequest.model_validate({
        "mode": "outcome",
        "event_id": event_id,
        "source": {"platform": source_platform, "workspace_id": "WS-1"},
        "work": {
            "external_id": "CLAIM-1", "type": "claim",
            "source_platform": "Custom Claims App", "sync_if_missing": True,
        },
        "outcome": outcome,
    })


def _work_item(db):
    return db.query(WorkItem).filter_by(external_id="WS-1:Custom-Claims-App:CLAIM-1").first()


def test_older_event_arriving_after_newer_does_not_overwrite_current_state():
    db = _session()
    route_payload(_outcome_request("evt-1", "approved", NOW), db)
    response = route_payload(_outcome_request("evt-2-stale", "under_review", NOW - timedelta(days=2)), db)

    assert response.outcome_recorded is True
    assert response.current_state_updated is False

    work_item = _work_item(db)
    current = db.query(WorkItemOutcome).filter_by(work_item_id=work_item.id).first()
    assert current.outcome_status == "approved", "a stale event must not regress current state"

    events = db.query(WorkItemOutcomeEvent).filter_by(work_item_id=work_item.id).order_by(WorkItemOutcomeEvent.id).all()
    assert [e.event_id for e in events] == ["evt-1", "evt-2-stale"], "the stale event must still be appended to history"


def test_two_sources_disagreeing_both_keep_their_own_history_row():
    db = _session()
    route_payload(_outcome_request("evt-sf", "closed_won", NOW - timedelta(hours=2), source_platform="Salesforce"), db)
    route_payload(_outcome_request("evt-cc", "under_review", NOW - timedelta(hours=1), source_platform="Custom Claims App"), db)

    work_item = _work_item(db)
    events = db.query(WorkItemOutcomeEvent).filter_by(work_item_id=work_item.id).order_by(WorkItemOutcomeEvent.id).all()
    assert len(events) == 2
    assert {e.source_system for e in events} == {"Salesforce", "Custom Claims App"}

    # Later outcome.date wins current state, regardless of which source sent it.
    current = db.query(WorkItemOutcome).filter_by(work_item_id=work_item.id).first()
    assert current.outcome_status == "under_review"
    assert current.source_system == "Custom Claims App"


def test_value_change_is_a_normal_write():
    db = _session()
    route_payload(_outcome_request("evt-1", "proposal", NOW - timedelta(days=3), value=1000.0), db)
    response = route_payload(_outcome_request("evt-2", "closed_won", NOW, value=5000.0), db)

    assert response.current_state_updated is True
    current = db.query(WorkItemOutcome).filter_by(work_item_id=_work_item(db).id).first()
    assert current.outcome_status == "closed_won"
    assert current.outcome_value == 5000.0


def test_reopen_after_closed_is_treated_as_a_normal_transition():
    db = _session()
    route_payload(_outcome_request("evt-1", "closed", NOW - timedelta(days=1), is_closed=True), db)
    response = route_payload(_outcome_request("evt-2", "reopened", NOW, is_closed=False), db)

    assert response.current_state_updated is True
    current = db.query(WorkItemOutcome).filter_by(work_item_id=_work_item(db).id).first()
    assert current.outcome_status == "reopened"
    assert current.is_closed is False


def test_reopen_with_an_older_date_does_not_clobber_newer_closed_state():
    db = _session()
    route_payload(_outcome_request("evt-1", "closed", NOW, is_closed=True), db)
    response = route_payload(_outcome_request("evt-2-late", "reopened", NOW - timedelta(days=5), is_closed=False), db)

    assert response.current_state_updated is False
    current = db.query(WorkItemOutcome).filter_by(work_item_id=_work_item(db).id).first()
    assert current.outcome_status == "closed"
    assert current.is_closed is True
