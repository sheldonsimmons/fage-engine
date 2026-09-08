"""
tests/test_route_outcome_workspace_isolation.py — one workspace must never
be able to see or update another workspace's WorkItems/outcomes through
Universal Outcome Ingestion.
"""
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.routes_router import RouteRequest, route_payload
from database.db import Base
from database.models import WorkItem, WorkItemOutcome


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _outcome_request(workspace_id, event_id, status="approved"):
    return RouteRequest.model_validate({
        "mode": "outcome",
        "event_id": event_id,
        "source": {"platform": "Custom Claims App", "workspace_id": workspace_id},
        "work": {
            "external_id": "CLAIM-1", "type": "claim",
            "source_platform": "Custom Claims App", "sync_if_missing": True,
        },
        "outcome": {"status": status, "value": 100.0, "date": datetime.utcnow().isoformat()},
    })


def test_same_external_id_in_two_workspaces_resolves_to_two_distinct_workitems():
    db = _session()
    route_payload(_outcome_request("WS-A", "evt-1", status="approved"), db)
    route_payload(_outcome_request("WS-B", "evt-1", status="denied"), db)

    items = db.query(WorkItem).all()
    assert len(items) == 2
    assert {i.workspace_id for i in items} == {"WS-A", "WS-B"}

    ws_a_item = db.query(WorkItem).filter_by(workspace_id="WS-A").first()
    ws_b_item = db.query(WorkItem).filter_by(workspace_id="WS-B").first()
    ws_a_outcome = db.query(WorkItemOutcome).filter_by(work_item_id=ws_a_item.id).first()
    ws_b_outcome = db.query(WorkItemOutcome).filter_by(work_item_id=ws_b_item.id).first()

    assert ws_a_outcome.outcome_status == "approved"
    assert ws_b_outcome.outcome_status == "denied", "WS-B's outcome must not be affected by WS-A's write"
