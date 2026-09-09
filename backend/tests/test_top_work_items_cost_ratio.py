"""
tests/test_top_work_items_cost_ratio.py — Phase 2 of the Business Impact
redesign: ranking WorkItems by AI spend as a fraction of their own
outcome value (cost_ratio), not just raw spend. "work_item" is a
transaction-only dimension in the shared metrics registry, so this can't
run through run_metrics_query() in one call (same constraint as
department_outcome_breakdown()) — backed by the standalone
core.metrics_query.work_items_by_cost_ratio().
"""
from datetime import datetime

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database.db import Base, get_db
from database.models import TokenTransaction, WorkItem, WorkItemOutcome
from main import app


def _client():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app), TestingSessionLocal()


def _work_item(db, *, external_id, workspace_id="WS1", context_type="opportunity"):
    wi = WorkItem(external_id=external_id, workspace_id=workspace_id, name=external_id, context_type=context_type)
    db.add(wi)
    db.commit()
    db.refresh(wi)
    return wi


def _outcome(db, work_item, *, workspace_id="WS1", outcome_success=True, is_closed=True, outcome_value):
    db.add(WorkItemOutcome(
        work_item_id=work_item.id, workspace_id=workspace_id, source_system="salesforce",
        source_object="Opportunity", external_id=work_item.external_id,
        outcome_success=outcome_success, is_closed=is_closed, outcome_value=outcome_value,
        last_synced_at=datetime.utcnow(),
    ))


def _tx(db, work_item, *, cost_usd, workspace_id="WS1"):
    db.add(TokenTransaction(
        department="Sales", model_tier="Scout", input_tokens=100, output_tokens=50,
        cost_usd=cost_usd, timestamp=datetime.utcnow(), workspace_id=workspace_id,
        is_simulation=False, usage_source="estimated", routing_reason="ROUTINE",
        work_item_id=work_item.id,
    ))


def test_ranks_worst_cost_ratio_first():
    client, db = _client()
    cheap = _work_item(db, external_id="opp-cheap")
    expensive = _work_item(db, external_id="opp-expensive")
    _outcome(db, cheap, outcome_value=10000.0)
    _outcome(db, expensive, outcome_value=100.0)
    db.commit()
    _tx(db, cheap, cost_usd=10.0)     # ratio 0.001
    _tx(db, expensive, cost_usd=50.0)  # ratio 0.5
    db.commit()

    resp = client.get(
        "/api/dashboard/business-impact/top-work-items",
        params={"workspace_id": "WS1", "rank_by": "cost_ratio"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["rank_by"] == "cost_ratio"
    rows = body["rows"]
    assert rows[0]["work_item_id"] == "opp-expensive"
    assert rows[0]["cost_ratio"] == 0.5
    assert rows[1]["work_item_id"] == "opp-cheap"


def test_excludes_work_items_with_no_outcome_value():
    client, db = _client()
    valued = _work_item(db, external_id="opp-valued")
    zero_value = _work_item(db, external_id="opp-zero")
    _outcome(db, valued, outcome_value=500.0)
    _outcome(db, zero_value, outcome_value=0.0)
    db.commit()
    _tx(db, valued, cost_usd=5.0)
    _tx(db, zero_value, cost_usd=5.0)
    db.commit()

    resp = client.get(
        "/api/dashboard/business-impact/top-work-items",
        params={"workspace_id": "WS1", "rank_by": "cost_ratio"},
    )
    rows = resp.json()["rows"]
    assert len(rows) == 1
    assert rows[0]["work_item_id"] == "opp-valued"


def test_outcome_status_filter_applies_to_cost_ratio():
    client, db = _client()
    won = _work_item(db, external_id="opp-won")
    lost = _work_item(db, external_id="opp-lost")
    _outcome(db, won, outcome_success=True, is_closed=True, outcome_value=1000.0)
    _outcome(db, lost, outcome_success=False, is_closed=True, outcome_value=1000.0)
    db.commit()
    _tx(db, won, cost_usd=10.0)
    _tx(db, lost, cost_usd=10.0)
    db.commit()

    resp = client.get(
        "/api/dashboard/business-impact/top-work-items",
        params={"workspace_id": "WS1", "rank_by": "cost_ratio", "outcome_status": "won"},
    )
    rows = resp.json()["rows"]
    assert len(rows) == 1
    assert rows[0]["work_item_id"] == "opp-won"


def test_default_rank_by_is_spend_and_unaffected():
    client, db = _client()
    wi = _work_item(db, external_id="opp-1")
    _outcome(db, wi, outcome_value=100.0)
    db.commit()
    _tx(db, wi, cost_usd=5.0)
    db.commit()

    resp = client.get("/api/dashboard/business-impact/top-work-items", params={"workspace_id": "WS1"})
    body = resp.json()
    assert body["rank_by"] == "spend"
    assert body["rows"][0]["ai_spend_usd"] == 5.0
    assert "cost_ratio" not in body["rows"][0]
