"""
tests/test_timeseries.py — correctness check for the SQL-side aggregation
in api/routes_timeseries.py's get_timeseries(), which used to pull every
matching TokenTransaction row into Python and sum them by hand (same bug
class as project_activity_reporting() had, fixed here the same way:
GROUP BY in SQL instead of a per-row Python loop).
"""
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.db import Base
from database.models import TokenTransaction
from api.routes_timeseries import get_timeseries


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _tx(db, *, days_ago, department, model_tier, cost_usd, workspace_id=None):
    db.add(TokenTransaction(
        department=department, model_tier=model_tier, input_tokens=10, output_tokens=10,
        cost_usd=cost_usd, timestamp=datetime.utcnow() - timedelta(days=days_ago),
        workspace_id=workspace_id, is_simulation=False, usage_source="estimated",
    ))


def test_spend_and_calls_are_summed_correctly_per_day_and_department():
    db = _session()
    _tx(db, days_ago=0, department="Sales", model_tier="micro", cost_usd=1.5)
    _tx(db, days_ago=0, department="Sales", model_tier="micro", cost_usd=2.5)
    _tx(db, days_ago=0, department="Marketing", model_tier="flagship", cost_usd=0.5)
    _tx(db, days_ago=1, department="Sales", model_tier="micro", cost_usd=9.0)
    db.commit()

    result = get_timeseries(days=7, workspace_id=None, db=db)

    today = datetime.utcnow().date().isoformat()
    yesterday = (datetime.utcnow().date() - timedelta(days=1)).isoformat()
    today_row = next(r for r in result["daily_spend"] if r["date"] == today)
    yesterday_row = next(r for r in result["daily_spend"] if r["date"] == yesterday)

    assert today_row["total_usd"] == 4.5
    assert today_row["by_dept"]["Sales"] == 4.0
    assert today_row["by_dept"]["Marketing"] == 0.5
    assert yesterday_row["total_usd"] == 9.0
    assert yesterday_row["by_dept"]["Sales"] == 9.0

    today_calls = next(r for r in result["daily_calls"] if r["date"] == today)
    assert today_calls["total_calls"] == 3
    assert today_calls["by_tier"]["Scout"] == 2  # the 2 "micro" (Sales) calls alias to "Scout"
    assert today_calls["by_tier"]["Advisor"] == 1  # the 1 "flagship" (Marketing) call aliases to "Advisor"


def test_workspace_prefix_is_stripped_from_department_labels():
    db = _session()
    _tx(db, days_ago=0, department="WS-1:Sales", model_tier="micro", cost_usd=1.0)
    db.commit()

    result = get_timeseries(days=7, workspace_id=None, db=db)
    today = datetime.utcnow().date().isoformat()
    today_row = next(r for r in result["daily_spend"] if r["date"] == today)

    assert "Sales" in today_row["by_dept"]
    assert "WS-1:Sales" not in today_row["by_dept"]


def test_workspace_filter_scopes_to_one_workspace():
    db = _session()
    _tx(db, days_ago=0, department="Sales", model_tier="micro", cost_usd=5.0, workspace_id="WS-A")
    _tx(db, days_ago=0, department="Sales", model_tier="micro", cost_usd=7.0, workspace_id="WS-B")
    db.commit()

    result = get_timeseries(days=7, workspace_id="WS-A", db=db)
    today = datetime.utcnow().date().isoformat()
    today_row = next(r for r in result["daily_spend"] if r["date"] == today)

    assert today_row["total_usd"] == 5.0


def test_transactions_outside_the_window_are_excluded():
    db = _session()
    _tx(db, days_ago=0, department="Sales", model_tier="micro", cost_usd=1.0)
    _tx(db, days_ago=45, department="Sales", model_tier="micro", cost_usd=999.0)
    db.commit()

    result = get_timeseries(days=30, workspace_id=None, db=db)
    total = sum(r["total_usd"] for r in result["daily_spend"])
    assert total == 1.0


def test_empty_days_are_filled_with_zero_not_omitted():
    db = _session()
    _tx(db, days_ago=0, department="Sales", model_tier="micro", cost_usd=1.0)
    db.commit()

    result = get_timeseries(days=7, workspace_id=None, db=db)
    assert len(result["labels"]) == 7
    assert len(result["daily_spend"]) == 7
    zero_days = [r for r in result["daily_spend"] if r["total_usd"] == 0]
    assert len(zero_days) == 6
