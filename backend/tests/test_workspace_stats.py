"""
tests/test_workspace_stats.py — correctness check for the SQL-side
aggregation in api/routes_trial.py's workspace_stats() / _workspace_usage(),
which used to pull every matching TokenTransaction row into Python and also
scoped only via the legacy "workspace_id:Dept" department-prefix string
instead of the real workspace_id column (same bug class already fixed in
get_timeseries()).
"""
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.db import Base
from database.models import TokenTransaction, TrialAccount
from api.routes_trial import workspace_stats, _workspace_usage


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _account(db, workspace_id):
    account = TrialAccount(
        email=f"{workspace_id}@example.com", name="Test", api_key_enc="x",
        workspace_id=workspace_id, trial_end=datetime.utcnow() + timedelta(days=14),
    )
    db.add(account)
    db.commit()
    return account


def _tx(db, *, department, model_tier, cost_usd, input_tokens=1000, output_tokens=1000,
        was_pruned=False, tokens_saved=0, workspace_id=None):
    db.add(TokenTransaction(
        department=department, model_tier=model_tier, input_tokens=input_tokens,
        output_tokens=output_tokens, cost_usd=cost_usd, timestamp=datetime.utcnow(),
        was_pruned=was_pruned, tokens_saved=tokens_saved, workspace_id=workspace_id,
        is_simulation=False, usage_source="estimated",
    ))


def test_real_workspace_id_column_is_used_not_just_legacy_prefix():
    # Activity written through /api/route sets the real workspace_id column
    # but leaves department unprefixed -- the old LIKE-prefix-only filter
    # silently excluded this traffic entirely.
    db = _session()
    _account(db, "WS-1")
    _tx(db, department="Sales", model_tier="micro", cost_usd=2.0, workspace_id="WS-1")
    db.commit()

    result = workspace_stats(workspace_id="WS-1", db=db)
    assert result["total_calls"] == 1
    assert result["total_cost_usd"] == 2.0


def test_legacy_prefixed_department_still_counted():
    db = _session()
    _account(db, "WS-2")
    _tx(db, department="WS-2:Marketing", model_tier="micro", cost_usd=3.0)
    db.commit()

    result = workspace_stats(workspace_id="WS-2", db=db)
    assert result["total_calls"] == 1
    assert result["departments"]["Marketing"]["calls"] == 1


def test_other_workspaces_are_excluded():
    db = _session()
    _account(db, "WS-A")
    _tx(db, department="Sales", model_tier="micro", cost_usd=1.0, workspace_id="WS-A")
    _tx(db, department="Sales", model_tier="micro", cost_usd=99.0, workspace_id="WS-B")
    db.commit()

    result = workspace_stats(workspace_id="WS-A", db=db)
    assert result["total_calls"] == 1
    assert result["total_cost_usd"] == 1.0


def test_department_breakdown_sums_calls_and_cost_per_department():
    db = _session()
    _account(db, "WS-3")
    _tx(db, department="Sales", model_tier="micro", cost_usd=1.0, workspace_id="WS-3")
    _tx(db, department="Sales", model_tier="micro", cost_usd=2.0, workspace_id="WS-3")
    _tx(db, department="Support", model_tier="flagship", cost_usd=5.0, workspace_id="WS-3")
    db.commit()

    result = workspace_stats(workspace_id="WS-3", db=db)
    assert result["departments"]["Sales"] == {"calls": 2, "cost": 3.0}
    assert result["departments"]["Support"] == {"calls": 1, "cost": 5.0}


def test_tokens_saved_only_counts_pruned_calls():
    db = _session()
    _account(db, "WS-4")
    _tx(db, department="Sales", model_tier="micro", cost_usd=1.0, was_pruned=True, tokens_saved=100, workspace_id="WS-4")
    _tx(db, department="Sales", model_tier="micro", cost_usd=1.0, was_pruned=False, tokens_saved=50, workspace_id="WS-4")
    db.commit()

    result = workspace_stats(workspace_id="WS-4", db=db)
    assert result["tokens_saved"] == 100


def test_economy_pct_and_recent_calls():
    db = _session()
    _account(db, "WS-5")
    _tx(db, department="Sales", model_tier="Scout", cost_usd=1.0, workspace_id="WS-5")
    _tx(db, department="Sales", model_tier="Strategist", cost_usd=1.0, workspace_id="WS-5")
    db.commit()

    result = workspace_stats(workspace_id="WS-5", db=db)
    assert result["economy_pct"] == 50.0
    assert len(result["recent_calls"]) == 2


def test_workspace_usage_matches_workspace_stats_total_cost():
    db = _session()
    _account(db, "WS-6")
    _tx(db, department="Sales", model_tier="micro", cost_usd=4.25, workspace_id="WS-6")
    db.commit()

    usage = _workspace_usage(db, "WS-6")
    assert usage == {"calls": 1, "spend_usd": 4.25}
