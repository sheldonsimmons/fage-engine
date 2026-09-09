"""
tests/test_business_impact_by_department.py — Phase 2 of the Business
Impact redesign: "Business Impact by Department," the biggest gap
identified in the design assessment (WorkItemOutcome-rooted queries have
no TokenTransaction join, so the shared DIMENSIONS["department"]
expression -- charged_org_unit_name-based -- has no column to read).
core.metrics_query.department_outcome_breakdown() is a standalone query
using WorkItem.department instead, merged with a separately-queried
AI-spend-by-department total.
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


def _work_item(db, *, external_id, department, workspace_id="WS1", context_type="opportunity"):
    wi = WorkItem(external_id=external_id, workspace_id=workspace_id, name=external_id,
                  context_type=context_type, department=department)
    db.add(wi)
    db.commit()
    db.refresh(wi)
    return wi


def _outcome(db, work_item, *, workspace_id="WS1", outcome_success=None, is_closed=False, outcome_value=0.0):
    db.add(WorkItemOutcome(
        work_item_id=work_item.id, workspace_id=workspace_id, source_system="salesforce",
        source_object="Opportunity", external_id=work_item.external_id,
        outcome_success=outcome_success, is_closed=is_closed, outcome_value=outcome_value,
        last_synced_at=datetime.utcnow(),
    ))


def _tx(db, work_item, *, cost_usd, workspace_id="WS1"):
    db.add(TokenTransaction(
        department=work_item.department or "Unassigned", model_tier="Scout", input_tokens=100, output_tokens=50,
        cost_usd=cost_usd, timestamp=datetime.utcnow(), workspace_id=workspace_id,
        is_simulation=False, usage_source="estimated", routing_reason="ROUTINE",
        work_item_id=work_item.id,
    ))


def test_department_breakdown_ranks_by_ai_investment():
    client, db = _client()
    sales = _work_item(db, external_id="opp-sales", department="Sales")
    eng = _work_item(db, external_id="opp-eng", department="Engineering")
    _outcome(db, sales, outcome_success=True, is_closed=True, outcome_value=1000.0)
    _outcome(db, eng, outcome_success=True, is_closed=True, outcome_value=200.0)
    db.commit()
    _tx(db, sales, cost_usd=50.0)
    _tx(db, eng, cost_usd=5.0)
    db.commit()

    resp = client.get("/api/dashboard/business-impact/by-department", params={"workspace_id": "WS1"})
    assert resp.status_code == 200
    rows = resp.json()["rows"]

    assert rows[0]["department"] == "Sales"
    assert rows[0]["ai_investment_usd"] == 50.0
    assert rows[0]["opportunities_won"] == 1
    assert rows[0]["closed_won_value_usd"] == 1000.0
    assert rows[0]["cost_per_won_opportunity_usd"] == 50.0
    assert rows[1]["department"] == "Engineering"


def test_department_breakdown_strips_workspace_prefix():
    client, db = _client()
    prefixed = _work_item(db, external_id="opp-1", department="WS1:Support")
    _outcome(db, prefixed, outcome_success=True, is_closed=True, outcome_value=500.0)
    db.commit()
    _tx(db, prefixed, cost_usd=10.0)
    db.commit()

    resp = client.get("/api/dashboard/business-impact/by-department", params={"workspace_id": "WS1"})
    rows = resp.json()["rows"]
    assert rows[0]["department"] == "Support"


def test_department_breakdown_merges_prefixed_and_unprefixed_same_department():
    client, db = _client()
    a = _work_item(db, external_id="opp-a", department="WS1:Sales")
    b = _work_item(db, external_id="opp-b", department="Sales")
    _outcome(db, a, outcome_success=True, is_closed=True, outcome_value=100.0)
    _outcome(db, b, outcome_success=True, is_closed=True, outcome_value=200.0)
    db.commit()
    _tx(db, a, cost_usd=7.0)
    _tx(db, b, cost_usd=3.0)
    db.commit()

    resp = client.get("/api/dashboard/business-impact/by-department", params={"workspace_id": "WS1"})
    rows = resp.json()["rows"]
    assert len(rows) == 1
    assert rows[0]["department"] == "Sales"
    assert rows[0]["opportunities_won"] == 2
    assert rows[0]["ai_investment_usd"] == 10.0


def test_department_breakdown_missing_department_becomes_unassigned():
    client, db = _client()
    wi = _work_item(db, external_id="opp-none", department=None)
    _outcome(db, wi, outcome_success=True, is_closed=True, outcome_value=50.0)
    db.commit()
    _tx(db, wi, cost_usd=2.0)
    db.commit()

    resp = client.get("/api/dashboard/business-impact/by-department", params={"workspace_id": "WS1"})
    rows = resp.json()["rows"]
    assert rows[0]["department"] == "Unassigned"


def test_department_breakdown_workspace_scoped():
    client, db = _client()
    mine = _work_item(db, external_id="opp-mine", department="Sales", workspace_id="WS-A")
    theirs = _work_item(db, external_id="opp-theirs", department="Sales", workspace_id="WS-B")
    _outcome(db, mine, workspace_id="WS-A", outcome_success=True, is_closed=True, outcome_value=1.0)
    _outcome(db, theirs, workspace_id="WS-B", outcome_success=True, is_closed=True, outcome_value=999999.0)
    db.commit()
    _tx(db, mine, cost_usd=1.0, workspace_id="WS-A")
    _tx(db, theirs, cost_usd=999.0, workspace_id="WS-B")
    db.commit()

    resp = client.get("/api/dashboard/business-impact/by-department", params={"workspace_id": "WS-A"})
    rows = resp.json()["rows"]
    assert len(rows) == 1
    assert rows[0]["ai_investment_usd"] == 1.0
