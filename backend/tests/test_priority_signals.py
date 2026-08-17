"""
tests/test_priority_signals.py — correctness check for
api.ask_costpilot_tools.run_get_priority_signals, the Ask CostPilot tool
for "what should I pay attention to today" style questions. Combines
core.budget.get_all_budgets (budget risk) with query_metrics's now-ranked
comparison (biggest spend swings) into one pre-ranked structured list.
"""
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.ask_costpilot_tools import run_get_priority_signals
from database.db import Base
from database.models import DepartmentBudget, TokenTransaction


def _session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _budget(db, *, department, monthly_cap_usd, current_spend_usd, throttled=False, workspace_id="WS1"):
    db.add(DepartmentBudget(
        department=department, monthly_cap_usd=monthly_cap_usd,
        current_spend_usd=current_spend_usd, throttled=throttled, workspace_id=workspace_id,
    ))


def _tx(db, *, cost_usd, department="WS1:Sales", timestamp=None, workspace_id="WS1"):
    db.add(TokenTransaction(
        department=department, model_tier="Analyst", input_tokens=10, output_tokens=10,
        cost_usd=cost_usd, timestamp=timestamp or datetime.utcnow(), workspace_id=workspace_id,
        is_simulation=False, usage_source="estimated", routing_reason="ROUTINE",
    ))


def test_no_signals_when_nothing_needs_attention():
    db = _session()
    db.commit()
    result = run_get_priority_signals(db, "WS1")
    assert result["signals"] == []


def test_over_budget_department_flagged_critical():
    db = _session()
    _budget(db, department="WS1:Engineering", monthly_cap_usd=100.0, current_spend_usd=120.0, throttled=True)
    db.commit()

    result = run_get_priority_signals(db, "WS1")
    signal = next(s for s in result["signals"] if s["type"] == "budget_risk")
    assert signal["severity"] == "critical"
    assert "Engineering" in signal["detail"]


def test_near_budget_department_flagged_warning_not_critical():
    db = _session()
    _budget(db, department="WS1:Marketing", monthly_cap_usd=100.0, current_spend_usd=85.0, throttled=False)
    db.commit()

    result = run_get_priority_signals(db, "WS1")
    signal = next(s for s in result["signals"] if s["type"] == "budget_risk")
    assert signal["severity"] == "warning"


def test_department_under_80_percent_not_flagged():
    db = _session()
    _budget(db, department="WS1:Finance", monthly_cap_usd=100.0, current_spend_usd=10.0)
    db.commit()

    result = run_get_priority_signals(db, "WS1")
    assert result["signals"] == []


def test_large_spend_swing_flagged_as_spend_change():
    db = _session()
    now = datetime.utcnow()
    _tx(db, cost_usd=100.0, department="WS1:Support", timestamp=now - timedelta(days=2))
    _tx(db, cost_usd=10.0, department="WS1:Support", timestamp=now - timedelta(days=10))
    db.commit()

    result = run_get_priority_signals(db, "WS1", days=7)
    signal = next(s for s in result["signals"] if s["type"] == "spend_change")
    assert "Support" in signal["detail"]
    assert "increased" in signal["detail"]


def test_small_spend_swing_not_flagged():
    db = _session()
    now = datetime.utcnow()
    _tx(db, cost_usd=10.5, department="WS1:Legal", timestamp=now - timedelta(days=2))
    _tx(db, cost_usd=10.0, department="WS1:Legal", timestamp=now - timedelta(days=10))
    db.commit()

    result = run_get_priority_signals(db, "WS1", days=7)
    assert result["signals"] == []


def test_critical_signals_ranked_before_spend_changes():
    db = _session()
    now = datetime.utcnow()
    _budget(db, department="WS1:Ops", monthly_cap_usd=100.0, current_spend_usd=150.0, throttled=True)
    _tx(db, cost_usd=100.0, department="WS1:Support", timestamp=now - timedelta(days=2))
    _tx(db, cost_usd=10.0, department="WS1:Support", timestamp=now - timedelta(days=10))
    db.commit()

    result = run_get_priority_signals(db, "WS1", days=7)
    assert result["signals"][0]["type"] == "budget_risk"
    assert result["signals"][0]["severity"] == "critical"


def test_workspace_scoping_excludes_other_workspaces():
    db = _session()
    _budget(db, department="WS-B:Ops", monthly_cap_usd=100.0, current_spend_usd=150.0, throttled=True, workspace_id="WS-B")
    db.commit()

    result = run_get_priority_signals(db, "WS-A")
    assert result["signals"] == []
