"""
tests/test_route_outcome_idempotency.py — Universal Outcome Ingestion's
event_id is MANDATORY (unlike /api/route's activity modes, where it's
optional) and idempotency is key-based, scoped to (workspace_id, event_id)
-- never content-diffing, which is what caused a real production bug
(1,594 duplicate WorkItemOutcomeEvent rows). See core/outcome_ingestion.py.
"""
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.routes_router import RouteRequest, route_payload
from database.db import Base
from database.models import WorkItem, WorkItemOutcome, WorkItemOutcomeEvent


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _outcome_request(**overrides):
    payload = dict(
        mode="outcome",
        event_id="evt-idem-1",
        source={"platform": "Custom Claims App", "workspace_id": "WS-1"},
        work={"external_id": "CLAIM-1", "type": "claim", "source_platform": "Custom Claims App", "sync_if_missing": True},
        outcome={"status": "approved", "value": 500.0, "date": datetime.utcnow().isoformat(), "success": True},
    )
    payload.update(overrides)
    return RouteRequest.model_validate(payload)


def test_missing_event_id_is_rejected():
    db = _session()
    request = _outcome_request(event_id=None)
    try:
        route_payload(request, db)
        assert False, "expected an HTTPException"
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 422


def test_duplicate_event_id_does_not_write_a_second_event_or_change_value():
    db = _session()
    route_payload(_outcome_request(), db)
    work_item = db.query(WorkItem).filter_by(external_id="WS-1:Custom-Claims-App:CLAIM-1").first()
    assert db.query(WorkItemOutcomeEvent).filter_by(work_item_id=work_item.id).count() == 1

    # Same event_id, but a DIFFERENT value -- simulating a caller retrying
    # with a slightly different payload construction, not just a byte-
    # identical resend. Idempotency is on event_id alone, not content.
    duplicate = _outcome_request(outcome={
        "status": "approved", "value": 999999.0, "date": datetime.utcnow().isoformat(), "success": True,
    })
    response = route_payload(duplicate, db)

    assert response.outcome_recorded is True
    assert db.query(WorkItemOutcomeEvent).filter_by(work_item_id=work_item.id).count() == 1, (
        "a resubmitted event_id must not append a second history row"
    )
    current = db.query(WorkItemOutcome).filter_by(work_item_id=work_item.id).first()
    assert current.outcome_value == 500.0, "a resubmitted event_id must not change current state"


def test_two_workspaces_can_safely_reuse_the_same_event_id_string():
    db = _session()
    route_payload(_outcome_request(
        source={"platform": "Custom Claims App", "workspace_id": "WS-A"},
        work={"external_id": "CLAIM-1", "type": "claim", "source_platform": "Custom Claims App", "sync_if_missing": True},
    ), db)
    response = route_payload(_outcome_request(
        source={"platform": "Custom Claims App", "workspace_id": "WS-B"},
        work={"external_id": "CLAIM-1", "type": "claim", "source_platform": "Custom Claims App", "sync_if_missing": True},
    ), db)

    assert response.outcome_recorded is True
    assert response.current_state_updated is True
    # WS-B's event_id="evt-idem-1" must be treated as a fresh write, not a
    # collision with WS-A's identical event_id string.
    ws_b_item = db.query(WorkItem).filter_by(external_id="WS-B:Custom-Claims-App:CLAIM-1").first()
    assert db.query(WorkItemOutcomeEvent).filter_by(work_item_id=ws_b_item.id).count() == 1
