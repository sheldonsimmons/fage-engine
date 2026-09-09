"""
tests/test_business_impact_redesign_phase1.py — Phase 1 of the Business
Impact tab redesign: per-KPI evidence labels (fixing one workspace-wide
label sized off won_count being shown next to unrelated KPIs), the
support-scope inconsistency fix (support_cost_per_resolution_usd used to
be "case" only while support_cases_total/resolved already covered
case+ticket+incident), the trend-duplication bug fix
(cost_per_successful_outcome_usd never had its own trend and silently
reused cost_per_won_opportunity_usd's), new raw won/lost/support
investment fields for the comparison sections, and the new
top-work-items endpoint.
"""
from datetime import datetime, timedelta

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


def _outcome(db, work_item, *, workspace_id="WS1", outcome_success=None, is_closed=False,
             outcome_value=0.0, source_object="Opportunity", outcome_date=None):
    db.add(WorkItemOutcome(
        work_item_id=work_item.id, workspace_id=workspace_id, source_system="salesforce",
        source_object=source_object, external_id=work_item.external_id,
        outcome_success=outcome_success, is_closed=is_closed, outcome_value=outcome_value,
        outcome_date=outcome_date, last_synced_at=datetime.utcnow(),
    ))


def _tx(db, work_item, *, cost_usd, workspace_id="WS1", timestamp=None):
    db.add(TokenTransaction(
        department="Sales", model_tier="Scout", input_tokens=100, output_tokens=50,
        cost_usd=cost_usd, timestamp=timestamp or datetime.utcnow(), workspace_id=workspace_id,
        is_simulation=False, usage_source="estimated", routing_reason="ROUTINE",
        work_item_id=work_item.id,
    ))


def test_support_cost_per_resolution_now_includes_tickets_and_incidents():
    client, db = _client()
    case = _work_item(db, external_id="case-1", context_type="case")
    ticket = _work_item(db, external_id="ticket-1", context_type="ticket")
    _outcome(db, case, is_closed=True, source_object="Case")
    _outcome(db, ticket, is_closed=True, source_object="Ticket")
    db.commit()
    _tx(db, case, cost_usd=4.0)
    _tx(db, ticket, cost_usd=6.0)
    db.commit()

    resp = client.get("/api/dashboard/business-impact", params={"workspace_id": "WS1"})
    body = resp.json()

    # Both resolved (case + ticket) count toward support_cases_resolved
    # AND support_cost_per_resolution_usd now -- previously the ticket's
    # $6 spend would have been silently excluded from the ratio's
    # numerator despite counting in the denominator-adjacent total.
    assert body["support_cases_resolved"] == 2
    assert body["support_cost_per_resolution_usd"] == 5.0  # (4+6)/2
    assert body["support_resolved_ai_investment_usd"] == 10.0


def test_evidence_by_kpi_is_sized_per_metric_not_one_shared_label():
    client, db = _client()
    # 1 won opportunity (early_signal sample size) but 40 resolved support
    # items (meaningful sample size) -- these must get DIFFERENT evidence
    # labels, not one workspace-wide label sized off won_count alone.
    won = _work_item(db, external_id="opp-won")
    _outcome(db, won, outcome_success=True, is_closed=True, outcome_value=1000.0)
    for i in range(40):
        case = _work_item(db, external_id=f"case-{i}", context_type="case")
        _outcome(db, case, is_closed=True, source_object="Case")
    db.commit()

    resp = client.get("/api/dashboard/business-impact", params={"workspace_id": "WS1"})
    body = resp.json()

    assert body["evidence_by_kpi"]["cost_per_won_opportunity_usd"] == "early_signal"
    assert body["evidence_by_kpi"]["support_cost_per_resolution_usd"] == "meaningful"


def test_cost_per_successful_outcome_gets_its_own_distinct_trend():
    client, db = _client()
    now = datetime.utcnow()
    recent = now - timedelta(days=5)
    older = now - timedelta(days=45)

    # Opportunity-only successful outcome, recent period: $10 spend / 1 won.
    opp = _work_item(db, external_id="opp-1")
    _outcome(db, opp, outcome_success=True, is_closed=True, outcome_value=100.0, outcome_date=recent)
    db.commit()
    _tx(db, opp, cost_usd=10.0, timestamp=recent)

    # A non-opportunity successful outcome (e.g. a claim) in the SAME
    # recent window -- counts toward cost_per_successful_outcome_usd's
    # generic (any context_type) trend, but NOT toward
    # cost_per_won_opportunity_usd's opportunity-only trend. If the two
    # trends were still incorrectly sharing one computation, this claim's
    # spend would have no way to make them diverge.
    claim = _work_item(db, external_id="claim-1", context_type="claim")
    _outcome(db, claim, outcome_success=True, is_closed=True, outcome_value=50.0, outcome_date=recent)
    db.commit()
    _tx(db, claim, cost_usd=30.0, timestamp=recent)

    # Prior-period opportunity outcome so the trend has a baseline to diff against.
    opp_prior = _work_item(db, external_id="opp-0")
    _outcome(db, opp_prior, outcome_success=True, is_closed=True, outcome_value=100.0, outcome_date=older)
    db.commit()
    _tx(db, opp_prior, cost_usd=5.0, timestamp=older)
    db.commit()

    resp = client.get("/api/dashboard/business-impact", params={"workspace_id": "WS1"})
    trend = resp.json()["trend_pct_change"]

    assert "cost_per_successful_outcome_usd" in trend
    assert trend["cost_per_successful_outcome_usd"] != trend["cost_per_won_opportunity_usd"], (
        "cost_per_successful_outcome_usd must have its own trend, not silently reuse "
        "cost_per_won_opportunity_usd's (the bug this test guards against)"
    )


def test_won_and_lost_ai_investment_exposed_as_raw_numbers():
    client, db = _client()
    won = _work_item(db, external_id="opp-won")
    lost = _work_item(db, external_id="opp-lost")
    _outcome(db, won, outcome_success=True, is_closed=True, outcome_value=1000.0)
    _outcome(db, lost, outcome_success=False, is_closed=True, outcome_value=500.0)
    db.commit()
    _tx(db, won, cost_usd=12.0)
    _tx(db, lost, cost_usd=3.0)
    db.commit()

    resp = client.get("/api/dashboard/business-impact", params={"workspace_id": "WS1"})
    body = resp.json()
    assert body["won_ai_investment_usd"] == 12.0
    assert body["lost_ai_investment_usd"] == 3.0


def test_top_work_items_endpoint_ranks_by_spend():
    client, db = _client()
    a = _work_item(db, external_id="opp-a")
    b = _work_item(db, external_id="opp-b")
    _tx(db, a, cost_usd=50.0)
    _tx(db, b, cost_usd=5.0)
    db.commit()

    resp = client.get("/api/dashboard/business-impact/top-work-items", params={"workspace_id": "WS1"})
    assert resp.status_code == 200
    rows = resp.json()["rows"]
    assert rows[0]["work_item_id"] == "opp-a"
    assert rows[0]["ai_spend_usd"] == 50.0


def test_top_work_items_endpoint_filters_by_outcome_status():
    client, db = _client()
    won = _work_item(db, external_id="opp-won")
    lost = _work_item(db, external_id="opp-lost")
    _outcome(db, won, outcome_success=True, is_closed=True)
    _outcome(db, lost, outcome_success=False, is_closed=True)
    db.commit()
    _tx(db, won, cost_usd=8.0)
    _tx(db, lost, cost_usd=20.0)
    db.commit()

    resp = client.get("/api/dashboard/business-impact/top-work-items", params={
        "workspace_id": "WS1", "outcome_status": "lost",
    })
    rows = resp.json()["rows"]
    assert len(rows) == 1
    assert rows[0]["work_item_id"] == "opp-lost"
