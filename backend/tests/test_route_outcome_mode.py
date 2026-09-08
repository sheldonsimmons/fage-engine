"""
tests/test_route_outcome_mode.py — Universal Outcome Ingestion happy path:
POST /api/route mode="outcome" writes both WorkItemOutcome (current-state
upsert) and WorkItemOutcomeEvent (append-only history) for a WorkItem
resolved the same way an AI-activity call would resolve it. See
core/outcome_ingestion.py for the full design reasoning.
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
        event_id="evt-001",
        source={"platform": "Custom Claims App", "workspace_id": "WS-1"},
        work={
            "external_id": "CLAIM-84721",
            "type": "claim",
            "source_platform": "Custom Claims App",
            "sync_if_missing": True,
        },
        outcome={
            "status": "approved",
            "value": 12500.0,
            "date": (datetime.utcnow() - timedelta(hours=1)).isoformat(),
            "success": True,
            "is_closed": True,
        },
    )
    payload.update(overrides)
    return RouteRequest.model_validate(payload)


def test_outcome_mode_creates_workitem_and_writes_current_state_and_history():
    db = _session()

    response = route_payload(_outcome_request(), db)

    assert response.outcome_recorded is True
    assert response.current_state_updated is True
    assert response.work_item_id == "WS-1:Custom-Claims-App:CLAIM-84721"

    work_item = db.query(WorkItem).filter_by(external_id=response.work_item_id).first()
    assert work_item is not None

    current = db.query(WorkItemOutcome).filter_by(work_item_id=work_item.id).first()
    assert current is not None
    assert current.outcome_status == "approved"
    assert current.outcome_value == 12500.0
    assert current.outcome_success is True
    assert current.is_closed is True
    assert current.retrieval_method == "push"
    assert current.source_system == "Custom Claims App"
    assert current.source_object == "claim"
    assert current.is_simulation is False

    events = db.query(WorkItemOutcomeEvent).filter_by(work_item_id=work_item.id).all()
    assert len(events) == 1
    assert events[0].event_id == "evt-001"
    assert events[0].outcome_status == "approved"
    assert events[0].source_system == "Custom Claims App"


def test_outcome_mode_without_sync_if_missing_404s_when_workitem_absent():
    db = _session()

    request = _outcome_request(event_id="evt-002")
    request.work_context.sync_if_missing = False

    try:
        route_payload(request, db)
        assert False, "expected an HTTPException"
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 404


def test_outcome_mode_reuses_workitem_created_by_a_prior_activity_call():
    db = _session()

    # An activity call (mode="observe") creates the WorkItem first, the way
    # a real integration would report AI usage before later reporting an
    # outcome for the same record.
    activity = RouteRequest.model_validate({
        "mode": "observe",
        "source": {"platform": "Custom Claims App", "workspace_id": "WS-1", "agent_name": "Claims Assistant"},
        "work": {
            "external_id": "CLAIM-84721",
            "type": "claim",
            "source_platform": "Custom Claims App",
            "sync_if_missing": True,
        },
        "usage": {"model_name": "gpt-4.1-mini", "input_tokens": 500, "output_tokens": 150},
    })
    route_payload(activity, db)
    assert db.query(WorkItem).count() == 1

    response = route_payload(_outcome_request(event_id="evt-003"), db)

    assert db.query(WorkItem).count() == 1, "the outcome call must resolve the SAME WorkItem, not create a second one"
    assert response.work_item_id == "WS-1:Custom-Claims-App:CLAIM-84721"
