"""
core/metrics_query.py's filters dict was missing charged_unit and
provider -- both used by Ask CostPilot's get_usage_report to let the
model scope a lookup ("what is Sales spending", "how much on Claude")
via _with_department_override, blocking that tool's migration onto the
registry (Recommendation #4). These tests lock in both filters.
"""
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.db import Base
from database.models import TokenTransaction
from core.metrics_query import run_metrics_query


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def test_charged_unit_filter_matches_org_unit_name_over_department():
    db = _session()
    db.add(TokenTransaction(
        department="WS-1:Sales", workspace_id="WS-1", charged_org_unit_name="Field Ops",
        model_tier="Scout", input_tokens=100, output_tokens=50, cost_usd=1.0, timestamp=datetime.utcnow(),
    ))
    db.add(TokenTransaction(
        department="WS-1:Support", workspace_id="WS-1",
        model_tier="Scout", input_tokens=100, output_tokens=50, cost_usd=5.0, timestamp=datetime.utcnow(),
    ))
    db.commit()

    result = run_metrics_query(
        db, "WS-1", metrics=["ai_spend"], filters={"charged_unit": "Field Ops"}, timeframe={},
    )
    assert not result.errors
    assert len(result.rows) == 1
    assert result.rows[0]["ai_spend"] == 1.0


def test_charged_unit_filter_falls_back_to_department_suffix_match():
    db = _session()
    db.add(TokenTransaction(
        department="WS-1:Support", workspace_id="WS-1",
        model_tier="Scout", input_tokens=100, output_tokens=50, cost_usd=3.0, timestamp=datetime.utcnow(),
    ))
    db.add(TokenTransaction(
        department="WS-1:Sales", workspace_id="WS-1",
        model_tier="Scout", input_tokens=100, output_tokens=50, cost_usd=7.0, timestamp=datetime.utcnow(),
    ))
    db.commit()

    result = run_metrics_query(
        db, "WS-1", metrics=["ai_spend"], filters={"charged_unit": "Support"}, timeframe={},
    )
    assert not result.errors
    assert len(result.rows) == 1
    assert result.rows[0]["ai_spend"] == 3.0


def test_provider_filter_scopes_to_matching_model_names():
    db = _session()
    db.add(TokenTransaction(
        department="WS-1:Sales", workspace_id="WS-1", model_name="claude-sonnet-4-6",
        model_tier="Analyst", input_tokens=100, output_tokens=50, cost_usd=2.0, timestamp=datetime.utcnow(),
    ))
    db.add(TokenTransaction(
        department="WS-1:Sales", workspace_id="WS-1", model_name="gpt-4.1-mini",
        model_tier="Analyst", input_tokens=100, output_tokens=50, cost_usd=9.0, timestamp=datetime.utcnow(),
    ))
    db.commit()

    result = run_metrics_query(
        db, "WS-1", metrics=["ai_spend"], filters={"provider": "Anthropic"}, timeframe={},
    )
    assert not result.errors
    assert len(result.rows) == 1
    assert result.rows[0]["ai_spend"] == 2.0
