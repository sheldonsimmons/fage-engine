"""
tests/test_dashboard_headline_spend.py — correctness check for GET
/api/dashboard's headline spend_today/spend_month KPI tile, now routed
through the canonical metrics registry (core/metrics_query.py's
run_metrics_query) instead of an independent func.sum() query. This was
one instance of a wider finding this session: the Executive Dashboard's
headline number, get_dashboard_changes/get_top_models/get_business_impact
(same file), Reports, Business Profile, and WorkItem Profile were each
computing "AI spend" a different way. This test locks down that the
migrated headline tile produces the correct number, not just that it
doesn't crash.
"""
from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database.db import Base, get_db
from database.models import TokenTransaction
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


def _tx(db, *, hours_ago=None, days_ago=None, cost_usd, workspace_id="WS1"):
    delta = timedelta(hours=hours_ago) if hours_ago is not None else timedelta(days=days_ago)
    db.add(TokenTransaction(
        department="Sales", model_tier="Scout", input_tokens=10, output_tokens=10,
        cost_usd=cost_usd, timestamp=datetime.utcnow() - delta,
        workspace_id=workspace_id, is_simulation=False, usage_source="estimated",
        routing_reason="ROUTINE",
    ))


def test_headline_spend_today_and_month_match_hand_summed_totals():
    client, db = _client()

    # Today: two calls, 1 hour and 5 hours ago -> both count toward spend_today
    _tx(db, hours_ago=1, cost_usd=2.50)
    _tx(db, hours_ago=5, cost_usd=1.25)
    # Earlier this month (day 3), not today -> counts toward spend_month only
    _tx(db, days_ago=3, cost_usd=4.00)
    # Last month (day 40) -> excluded from both
    _tx(db, days_ago=40, cost_usd=999.00)
    db.commit()

    resp = client.get("/api/dashboard", params={"workspace_id": "WS1"})
    assert resp.status_code == 200
    data = resp.json()

    assert data["spend_today_usd"] == 3.75  # 2.50 + 1.25
    assert data["spend_month_usd"] == 7.75  # 2.50 + 1.25 + 4.00

    app.dependency_overrides.clear()


def test_headline_spend_is_workspace_scoped():
    client, db = _client()
    _tx(db, hours_ago=1, cost_usd=10.00, workspace_id="WS1")
    _tx(db, hours_ago=1, cost_usd=500.00, workspace_id="WS2")
    db.commit()

    resp = client.get("/api/dashboard", params={"workspace_id": "WS1"})
    data = resp.json()
    assert data["spend_today_usd"] == 10.00
    assert data["spend_month_usd"] == 10.00

    app.dependency_overrides.clear()


def test_headline_spend_zero_when_no_transactions():
    client, db = _client()
    resp = client.get("/api/dashboard", params={"workspace_id": "EMPTY-WS"})
    data = resp.json()
    assert data["spend_today_usd"] == 0.0
    assert data["spend_month_usd"] == 0.0
    app.dependency_overrides.clear()
