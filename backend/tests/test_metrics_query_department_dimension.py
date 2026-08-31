"""
core/metrics_query.py's "department" dimension must match
project_activity_reporting()'s organizational_unit_breakdown exactly --
charged_org_unit_name preferred when set, else department -- confirmed
via production data to affect ~1% of TokenTransaction rows (Ask CostPilot
architecture audit Recommendation #4). These tests lock in that fallback
before get_usage_report's top_departments relies on it.
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


def test_department_dimension_prefers_charged_org_unit_name_when_set():
    db = _session()
    db.add(TokenTransaction(
        department="WS-1:Sales", workspace_id="WS-1", charged_org_unit_name="Field Ops",
        model_tier="Scout", input_tokens=100, output_tokens=50, cost_usd=1.0, timestamp=datetime.utcnow(),
    ))
    db.commit()

    result = run_metrics_query(db, "WS-1", metrics=["ai_spend"], dimensions=["department"], timeframe={})
    assert not result.errors
    assert result.rows[0]["dimensions"]["department"] == "Field Ops"


def test_department_dimension_falls_back_to_department_stripped_of_workspace_prefix():
    db = _session()
    db.add(TokenTransaction(
        department="WS-1:Sales", workspace_id="WS-1", charged_org_unit_name="  ",
        model_tier="Scout", input_tokens=100, output_tokens=50, cost_usd=1.0, timestamp=datetime.utcnow(),
    ))
    db.add(TokenTransaction(
        department="WS-1:Support", workspace_id="WS-1", charged_org_unit_name=None,
        model_tier="Scout", input_tokens=100, output_tokens=50, cost_usd=2.0, timestamp=datetime.utcnow(),
    ))
    db.commit()

    # Matches project_activity_reporting()'s (department or "").split(":")[-1]
    # exactly -- the department-fallback label never keeps its workspace
    # prefix, same as organizational_unit_breakdown.
    result = run_metrics_query(db, "WS-1", metrics=["ai_spend"], dimensions=["department"], timeframe={})
    assert not result.errors
    labels = {row["dimensions"]["department"] for row in result.rows}
    assert labels == {"Sales", "Support"}


def test_department_dimension_merges_prefixed_and_unprefixed_rows_of_the_same_department():
    db = _session()
    db.add(TokenTransaction(
        department="WS-1:Support", workspace_id="WS-1",
        model_tier="Scout", input_tokens=100, output_tokens=50, cost_usd=1.0, timestamp=datetime.utcnow(),
    ))
    db.add(TokenTransaction(
        department="Support", workspace_id="WS-1",
        model_tier="Scout", input_tokens=100, output_tokens=50, cost_usd=2.0, timestamp=datetime.utcnow(),
    ))
    db.commit()

    result = run_metrics_query(db, "WS-1", metrics=["ai_spend"], dimensions=["department"], timeframe={})
    assert not result.errors
    assert len(result.rows) == 1
    assert result.rows[0]["dimensions"]["department"] == "Support"
    assert result.rows[0]["ai_spend"] == 3.0


def test_department_dimension_does_not_split_a_colon_inside_charged_org_unit_name():
    # Real production edge case: a charged_org_unit_name value can itself
    # contain a colon (e.g. it was stored workspace-prefixed) -- unlike
    # the department-fallback branch, this must be used verbatim, not
    # split, since project_activity_reporting()'s own split only ever
    # applies to `department`, never to charged_org_unit_name.
    db = _session()
    db.add(TokenTransaction(
        department="WS-1:Sales", workspace_id="WS-1", charged_org_unit_name="WS-1:Sales",
        model_tier="Scout", input_tokens=100, output_tokens=50, cost_usd=1.0, timestamp=datetime.utcnow(),
    ))
    db.commit()

    result = run_metrics_query(db, "WS-1", metrics=["ai_spend"], dimensions=["department"], timeframe={})
    assert not result.errors
    assert result.rows[0]["dimensions"]["department"] == "WS-1:Sales"
