"""
tests/test_top_models.py — correctness check for GET /api/dashboard/top-models,
the real per-model spend breakdown behind the Executive Cockpit's
"Top AI Models by Spend" panel.
"""
from datetime import datetime, timedelta

import pytest
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


def _tx(db, *, days_ago, cost_usd, model_tier="Scout", model_name=None, workspace_id="WS1"):
    db.add(TokenTransaction(
        department="Sales", model_tier=model_tier, model_name=model_name,
        input_tokens=10, output_tokens=10, cost_usd=cost_usd,
        timestamp=datetime.utcnow() - timedelta(days=days_ago),
        workspace_id=workspace_id, is_simulation=False, usage_source="estimated",
        routing_reason="ROUTINE",
    ))


def test_groups_by_real_model_name_and_ranks_by_spend():
    client, db = _client()
    _tx(db, days_ago=1, cost_usd=5.0, model_name="claude-3-5-sonnet")
    _tx(db, days_ago=2, cost_usd=3.0, model_name="claude-3-5-sonnet")
    _tx(db, days_ago=1, cost_usd=1.0, model_name="claude-haiku-4-5")
    db.commit()

    resp = client.get("/api/dashboard/top-models", params={"workspace_id": "WS1", "days": 30})
    assert resp.status_code == 200
    body = resp.json()

    assert body["total_spend_usd"] == 9.0
    assert body["models"][0]["model"] == "claude-3-5-sonnet"
    assert body["models"][0]["spend_usd"] == 8.0
    assert body["models"][0]["calls"] == 2
    assert body["models"][0]["pct_of_total"] == pytest.approx(88.9, abs=0.1)
    assert body["models"][1]["model"] == "claude-haiku-4-5"


def test_rows_without_model_name_fall_back_to_tier_and_are_flagged():
    client, db = _client()
    _tx(db, days_ago=1, cost_usd=2.0, model_tier="Scout", model_name=None)
    db.commit()

    resp = client.get("/api/dashboard/top-models", params={"workspace_id": "WS1", "days": 30})
    models = resp.json()["models"]
    assert len(models) == 1
    assert models[0]["model"] == "Scout"
    assert models[0]["is_tier_only"] is True


def test_real_model_name_is_not_flagged_as_tier_only():
    client, db = _client()
    _tx(db, days_ago=1, cost_usd=2.0, model_name="claude-3-5-sonnet")
    db.commit()

    resp = client.get("/api/dashboard/top-models", params={"workspace_id": "WS1", "days": 30})
    models = resp.json()["models"]
    assert models[0]["is_tier_only"] is False


def test_limit_caps_returned_models():
    client, db = _client()
    for i in range(8):
        _tx(db, days_ago=1, cost_usd=float(i + 1), model_name=f"model-{i}")
    db.commit()

    resp = client.get("/api/dashboard/top-models", params={"workspace_id": "WS1", "days": 30, "limit": 3})
    models = resp.json()["models"]
    assert len(models) == 3
    # Highest spend first
    assert models[0]["model"] == "model-7"


def test_workspace_scoping_excludes_other_workspaces():
    client, db = _client()
    _tx(db, days_ago=1, cost_usd=1.0, model_name="claude-3-5-sonnet", workspace_id="WS-A")
    _tx(db, days_ago=1, cost_usd=99.0, model_name="claude-3-5-sonnet", workspace_id="WS-B")
    db.commit()

    resp = client.get("/api/dashboard/top-models", params={"workspace_id": "WS-A", "days": 30})
    body = resp.json()
    assert body["total_spend_usd"] == 1.0
