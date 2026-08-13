"""
tests/test_business_impact.py — correctness check for GET
/api/dashboard/business-impact, the workspace-wide version of
account_profile()'s real outcome totals (opportunity won/lost/open,
pipeline value, closed-won value, resolved support cases).
"""
from datetime import datetime

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database.db import Base, get_db
from database.models import WorkItem, WorkItemOutcome
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


def _outcome(db, work_item, *, workspace_id="WS1", outcome_success=None, is_closed=False, outcome_value=0.0, source_object="Opportunity"):
    db.add(WorkItemOutcome(
        work_item_id=work_item.id, workspace_id=workspace_id, source_system="salesforce",
        source_object=source_object, external_id=work_item.external_id,
        outcome_success=outcome_success, is_closed=is_closed, outcome_value=outcome_value,
        last_synced_at=datetime.utcnow(),
    ))


def test_no_work_items_reports_no_outcome_data_not_fabricated_zeros():
    client, db = _client()
    resp = client.get("/api/dashboard/business-impact", params={"workspace_id": "WS-EMPTY"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_outcome_data"] is False
    assert body["opportunities_won"] == 0
    assert body["pipeline_value_usd"] == 0.0


def test_won_lost_open_and_pipeline_value_computed_correctly():
    client, db = _client()
    won = _work_item(db, external_id="opp-won")
    lost = _work_item(db, external_id="opp-lost")
    open_deal = _work_item(db, external_id="opp-open")
    _outcome(db, won, outcome_success=True, is_closed=True, outcome_value=50000.0)
    _outcome(db, lost, outcome_success=False, is_closed=True, outcome_value=20000.0)
    _outcome(db, open_deal, outcome_success=None, is_closed=False, outcome_value=15000.0)
    db.commit()

    resp = client.get("/api/dashboard/business-impact", params={"workspace_id": "WS1"})
    body = resp.json()

    assert body["has_outcome_data"] is True
    assert body["opportunities_won"] == 1
    assert body["opportunities_lost"] == 1
    assert body["opportunities_open"] == 1
    assert body["closed_won_value_usd"] == 50000.0
    assert body["pipeline_value_usd"] == 15000.0  # only the open deal counts as pipeline


def test_support_cases_counted_separately_from_opportunities():
    client, db = _client()
    case_resolved = _work_item(db, external_id="case-1", context_type="case")
    case_open = _work_item(db, external_id="case-2", context_type="case")
    _outcome(db, case_resolved, is_closed=True, source_object="Case")
    _outcome(db, case_open, is_closed=False, source_object="Case")
    db.commit()

    resp = client.get("/api/dashboard/business-impact", params={"workspace_id": "WS1"})
    body = resp.json()

    assert body["support_cases_total"] == 2
    assert body["support_cases_resolved"] == 1
    # Cases must not be counted as opportunities won/lost/open
    assert body["opportunities_won"] == 0
    assert body["opportunities_lost"] == 0
    assert body["opportunities_open"] == 0


def test_workspace_scoping_excludes_other_workspaces():
    client, db = _client()
    mine = _work_item(db, external_id="opp-mine", workspace_id="WS-A")
    theirs = _work_item(db, external_id="opp-theirs", workspace_id="WS-B")
    _outcome(db, mine, workspace_id="WS-A", outcome_success=True, is_closed=True, outcome_value=1000.0)
    _outcome(db, theirs, workspace_id="WS-B", outcome_success=True, is_closed=True, outcome_value=999999.0)
    db.commit()

    resp = client.get("/api/dashboard/business-impact", params={"workspace_id": "WS-A"})
    body = resp.json()
    assert body["opportunities_won"] == 1
    assert body["closed_won_value_usd"] == 1000.0
