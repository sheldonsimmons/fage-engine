"""
tests/test_route_outcome_simulation_flag.py — synthetic_simulation=true
(Universal Connection's "Send Test Event" flow) must flag the written
outcome rows and be excluded from real business-impact reporting, the same
guarantee TokenTransaction.is_simulation already gives the activity side.
Regression guard for the gap identified in the Universal Outcome
Ingestion design: WorkItemOutcome/WorkItemOutcomeEvent had no such flag
before this feature.
"""
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.routes_router import RouteRequest, route_payload
from core.metrics_query import compute_outcome_coverage
from database.db import Base
from database.models import TokenTransaction, WorkItem, WorkItemOutcome, WorkItemOutcomeEvent


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _outcome_request(event_id, workspace_id="WS-1", synthetic=True, external_id="CLAIM-1"):
    return RouteRequest.model_validate({
        "mode": "outcome",
        "event_id": event_id,
        "synthetic_simulation": synthetic,
        "source": {"platform": "Custom Claims App", "workspace_id": workspace_id},
        "work": {
            "external_id": external_id, "type": "claim",
            "source_platform": "Custom Claims App", "sync_if_missing": True,
        },
        "outcome": {"status": "approved", "value": 5000.0, "success": True, "date": datetime.utcnow().isoformat()},
    })


def test_synthetic_outcome_flags_both_current_state_and_history_rows():
    db = _session()
    route_payload(_outcome_request("evt-test-1", synthetic=True), db)

    work_item = db.query(WorkItem).filter_by(external_id="WS-1:Custom-Claims-App:CLAIM-1").first()
    current = db.query(WorkItemOutcome).filter_by(work_item_id=work_item.id).first()
    event = db.query(WorkItemOutcomeEvent).filter_by(work_item_id=work_item.id).first()

    assert current.is_simulation is True
    assert event.is_simulation is True


def test_synthetic_outcome_is_excluded_from_outcome_coverage():
    db = _session()
    # A real outcome for one claim, a synthetic test outcome for another --
    # only the real one should count toward workspace-wide coverage.
    # compute_outcome_coverage() only counts a WorkItem as "touched" if it
    # has a TokenTransaction, so give both a real one (the AI-activity side
    # of "touched" isn't what's under test here -- the outcome exclusion is).
    route_payload(_outcome_request("evt-real", synthetic=False, external_id="CLAIM-REAL"), db)
    route_payload(_outcome_request("evt-synthetic", synthetic=True, external_id="CLAIM-TEST"), db)
    for external_id in ("WS-1:Custom-Claims-App:CLAIM-REAL", "WS-1:Custom-Claims-App:CLAIM-TEST"):
        work_item = db.query(WorkItem).filter_by(external_id=external_id).first()
        db.add(TokenTransaction(
            work_item_id=work_item.id, workspace_id="WS-1", department="Support", model_tier="Scout",
            input_tokens=10, output_tokens=10, cost_usd=0.01, timestamp=datetime.utcnow(),
            is_simulation=False, usage_source="estimated", routing_reason="ROUTINE",
        ))
    db.commit()

    coverage = compute_outcome_coverage(db, "WS-1")

    assert coverage["work_items_touched"] == 2
    assert coverage["outcomes_with_known_data"] == 1, (
        "the synthetic test outcome must not count toward real outcome coverage"
    )
