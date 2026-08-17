"""
tests/test_query_metrics_tool.py — correctness check for
api.ask_costpilot_tools.run_query_metrics, the thin Ask CostPilot tool
wrapper around core.metrics_query.run_metrics_query (Milestone 3).

Focus here is the tool-boundary concerns core.metrics_query's own test
suite doesn't cover: "" filter sentinels being cleaned to None, days/
period_key resolving to a real timeframe, and the result being JSON-
serializable (a dataclass would not survive the real agent loop's
response handling).
"""
import json
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.ask_costpilot_tools import run_query_metrics
from database.db import Base
from database.models import TokenTransaction


def _session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _tx(db, *, cost_usd, department="WS1:Sales", workspace_id="WS1"):
    db.add(TokenTransaction(
        department=department, model_tier="Analyst", model_name="claude-3-5-sonnet",
        source_platform="Salesforce", input_tokens=10, output_tokens=10, cost_usd=cost_usd,
        timestamp=datetime.utcnow(), workspace_id=workspace_id, is_simulation=False,
        usage_source="estimated", routing_reason="ROUTINE",
    ))


def test_result_is_json_serializable():
    db = _session()
    _tx(db, cost_usd=5.0)
    db.commit()

    result = run_query_metrics(db, "WS1", metrics=["ai_spend"], dimensions=[], filters={})
    json.dumps(result)  # raises if anything non-serializable slipped through
    assert result["rows"][0]["ai_spend"] == 5.0


def test_empty_string_filters_treated_as_no_filter():
    db = _session()
    _tx(db, cost_usd=5.0, department="WS1:Sales")
    db.commit()

    result = run_query_metrics(
        db, "WS1", metrics=["ai_spend"],
        filters={"account": "", "department": "", "agent": "", "platform": "", "model": "", "outcome_status": ""},
    )
    assert not result["errors"]
    assert result["rows"][0]["ai_spend"] == 5.0


def test_period_key_resolves_to_real_timeframe():
    db = _session()
    _tx(db, cost_usd=5.0)
    db.commit()

    result = run_query_metrics(db, "WS1", metrics=["ai_spend"], period_key="this_month", days=30)
    assert result["timeframe"]["start"] is not None
    assert result["timeframe"]["end"] is not None
    assert result["rows"][0]["ai_spend"] == 5.0


def test_unknown_metric_surfaces_as_error_through_the_tool_boundary():
    db = _session()
    result = run_query_metrics(db, "WS1", metrics=["not_a_real_metric"])
    assert any(e["code"] == "unknown_metric" for e in result["errors"])
