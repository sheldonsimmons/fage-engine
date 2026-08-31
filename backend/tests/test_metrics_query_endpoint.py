"""
tests/test_metrics_query_endpoint.py — POST /api/metrics/query, the new
public HTTP surface for core.metrics_query.run_metrics_query() built for
the AI Activity Explorer's View By / Break Down By pivot (Phase 1 of the
approved reporting plan). Previously this engine was only reachable
internally (Ask CostPilot's tool loop); this is the first frontend caller.
"""
from datetime import datetime

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


def test_metrics_query_endpoint_returns_grouped_rows():
    client, db = _client()
    db.add(TokenTransaction(
        department="WS-1:Sales", workspace_id="WS-1",
        model_tier="Scout", input_tokens=100, output_tokens=50, cost_usd=5.0, timestamp=datetime.utcnow(),
    ))
    db.commit()

    resp = client.post("/api/metrics/query", json={
        "workspace_id": "WS-1", "metrics": ["ai_spend"], "dimensions": ["department"],
    })
    assert resp.status_code == 200
    body = resp.json()
    assert not body["errors"]
    assert body["rows"][0]["ai_spend"] == 5.0
    assert body["rows"][0]["dimensions"]["department"] == "Sales"


def test_metrics_query_endpoint_reports_unknown_metric_as_structured_error():
    client, db = _client()
    resp = client.post("/api/metrics/query", json={
        "workspace_id": "WS-1", "metrics": ["not_a_real_metric"],
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["errors"]
    assert body["errors"][0]["code"] == "unknown_metric"


def test_metrics_query_endpoint_strips_empty_string_filter_values():
    client, db = _client()
    db.add(TokenTransaction(
        department="WS-1:Sales", workspace_id="WS-1",
        model_tier="Scout", input_tokens=100, output_tokens=50, cost_usd=3.0, timestamp=datetime.utcnow(),
    ))
    db.commit()

    resp = client.post("/api/metrics/query", json={
        "workspace_id": "WS-1", "metrics": ["ai_spend"],
        "filters": {"department": "", "platform": ""},
    })
    assert resp.status_code == 200
    body = resp.json()
    assert not body["errors"]
    assert body["rows"][0]["ai_spend"] == 3.0


def test_metrics_query_endpoint_applies_rolling_days_window_not_unbounded():
    """
    Same bug class caught and fixed for get_usage_report's migration this
    session -- period_key="none" must still apply a real rolling window,
    never "no time filter at all."
    """
    client, db = _client()
    db.add(TokenTransaction(
        department="WS-1:Sales", workspace_id="WS-1",
        model_tier="Scout", input_tokens=100, output_tokens=50, cost_usd=7.0,
        timestamp=datetime(2020, 1, 1),
    ))
    db.commit()

    resp = client.post("/api/metrics/query", json={
        "workspace_id": "WS-1", "metrics": ["ai_spend"], "days": 30, "period_key": "none",
    })
    body = resp.json()
    # No dimension requested -> one aggregate row (SQL aggregates with no
    # GROUP BY always return one row), but its value must be 0 -- the
    # 2020 transaction must be excluded by the rolling window, not just
    # "no rows at all" (which a no-dimension query never produces anyway).
    assert body["rows"][0]["ai_spend"] == 0.0
