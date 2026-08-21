"""
tests/test_effective_budget_context_workspace_aware.py — regression tests
for the live-throttle-uses-stale-spend bug found via a real production
screenshot: a department at 13-34% of its real budget was getting
"BUDGET CAP ENFORCED" on every request because the raw
DepartmentBudget.current_spend_usd/.throttled columns are never updated
for non-production (demo/simulation/legacy) workspaces -- only the
dashboard's display value was ever corrected via recomputed_department_spend.

effective_budget_context(db, department, workspace_id=...) now applies the
same workspace-type branch get_all_budgets() already uses for display, so
the live throttle decision and the dashboard can never disagree again.
"""
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.budget import effective_budget_context
from database.db import Base
from database.models import DepartmentBudget, TokenTransaction, Workspace


def _session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _workspace(db, workspace_id, workspace_type):
    db.add(Workspace(workspace_id=workspace_id, name=workspace_id, workspace_type=workspace_type))


def _budget(db, *, department, cap, raw_spend, throttled, override_granted=False, workspace_id=None):
    db.add(DepartmentBudget(
        department=department, monthly_cap_usd=cap, current_spend_usd=raw_spend,
        throttled=throttled, override_granted=override_granted, workspace_id=workspace_id,
    ))


def _tx(db, *, department, cost_usd, workspace_id):
    db.add(TokenTransaction(
        department=department, model_tier="Analyst", input_tokens=10, output_tokens=10,
        cost_usd=cost_usd, timestamp=datetime.utcnow(), workspace_id=workspace_id,
        is_simulation=False, usage_source="estimated", routing_reason="ROUTINE",
    ))


def test_production_workspace_still_uses_raw_column_even_if_recompute_would_disagree():
    """Zero behavior change for real customer workspaces -- confirmed by
    making the raw column and the real transaction ledger disagree, and
    asserting the raw column still wins."""
    db = _session()
    _workspace(db, "WS-PROD", "production")
    _budget(db, department="WS-PROD:Legal", cap=10.0, raw_spend=1.0, throttled=True)
    # Real activity says Legal is nowhere near its cap -- must be ignored
    # for a production workspace, since the raw column is authoritative there.
    _tx(db, department="WS-PROD:Legal", cost_usd=0.01, workspace_id="WS-PROD")
    db.commit()

    context = effective_budget_context(db, "WS-PROD:Legal", workspace_id="WS-PROD")
    assert context["throttled"] is True
    assert context["budget_spent_usd"] == 1.0


def test_non_production_workspace_clears_stale_throttle_when_real_spend_is_under_cap():
    """The exact bug: raw throttled=True, real recomputed spend says 13%."""
    db = _session()
    _workspace(db, "WS-DEMO", "demo")
    _budget(db, department="WS-DEMO:Legal", cap=10.0, raw_spend=0.0, throttled=True)
    _tx(db, department="WS-DEMO:Legal", cost_usd=1.36, workspace_id="WS-DEMO")
    db.commit()

    context = effective_budget_context(db, "WS-DEMO:Legal", workspace_id="WS-DEMO")
    assert context["throttled"] is False
    assert context["budget_spent_usd"] == 1.36
    assert context["budget_used_pct"] == 13.6


def test_non_production_workspace_still_throttles_when_genuinely_over_cap():
    """Not just 'always false now' -- a real overage still throttles."""
    db = _session()
    _workspace(db, "WS-DEMO2", "demo")
    _budget(db, department="WS-DEMO2:Support", cap=10.0, raw_spend=0.0, throttled=False)
    _tx(db, department="WS-DEMO2:Support", cost_usd=12.0, workspace_id="WS-DEMO2")
    db.commit()

    context = effective_budget_context(db, "WS-DEMO2:Support", workspace_id="WS-DEMO2")
    assert context["throttled"] is True


def test_override_granted_still_suppresses_throttle_in_recompute_path():
    db = _session()
    _workspace(db, "WS-DEMO3", "demo")
    _budget(db, department="WS-DEMO3:Support", cap=10.0, raw_spend=0.0, throttled=False, override_granted=True)
    _tx(db, department="WS-DEMO3:Support", cost_usd=12.0, workspace_id="WS-DEMO3")
    db.commit()

    context = effective_budget_context(db, "WS-DEMO3:Support", workspace_id="WS-DEMO3")
    assert context["throttled"] is False
    assert context["override_granted"] is True


def test_legacy_default_workspace_uses_recompute_path_too():
    """The 'default' workspace is typed 'legacy', not 'production' -- the
    exact workspace the live bug was confirmed on."""
    db = _session()
    _workspace(db, "default", "legacy")
    _budget(db, department="Legal", cap=1.0, raw_spend=1.069955, throttled=True, workspace_id="default")
    db.commit()

    context = effective_budget_context(db, "Legal", workspace_id="default")
    assert context["budget_spent_usd"] == 0.0
    assert context["throttled"] is False


def test_workspace_id_omitted_preserves_exact_prior_behavior():
    """Backward compatibility: no workspace_id argument -- always raw
    columns, matching the one pre-existing caller in
    test_audit_trust_semantics.py that never passes it."""
    db = _session()
    _budget(db, department="WS-X:Ops", cap=10.0, raw_spend=5.0, throttled=True)
    db.commit()

    context = effective_budget_context(db, "WS-X:Ops")
    assert context["throttled"] is True
    assert context["budget_spent_usd"] == 5.0


def test_unqualified_department_name_does_not_leak_other_workspaces_cap():
    """
    Regression test for a real bug found live: calling
    effective_budget_context(db, "Legal", workspace_id="default") returned
    budget_cap_usd=10.0 by blending in OTHER workspaces' same-named "Legal"
    budget rows (via related_budget_rows()'s cross-workspace name
    matching, meant for a different use case), when the "default"
    workspace's real cap is $1.00. Uses DepartmentBudget.workspace_id (a
    real, always-populated column) to scope instead.
    """
    db = _session()
    _workspace(db, "default", "legacy")
    _workspace(db, "WS-OTHER", "demo")
    _budget(db, department="Legal", cap=1.0, raw_spend=0.0, throttled=False, workspace_id="default")
    _budget(db, department="WS-OTHER:Legal", cap=10.0, raw_spend=0.0, throttled=False, workspace_id="WS-OTHER")
    db.commit()

    context = effective_budget_context(db, "Legal", workspace_id="default")
    assert context["budget_cap_usd"] == 1.0
