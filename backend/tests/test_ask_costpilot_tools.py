"""
Tests for api/ask_costpilot_tools.py — the tool executors behind the Ask
CostPilot agent loop. These check the deterministic wrapper behavior only
(no live model calls): row-limit clamping, entity-name pass-through, and
that budget/agent-adoption rows are shaped the way the agent's final-answer
builder expects.
"""
import sys
import types
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import api.ask_costpilot_tools as tools
from database.db import Base
from database.models import TokenTransaction


def _usage_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _seed_people(db, n, workspace_id="ws1"):
    # Spend descends with i so ranking (sorted by ai_spend desc) matches a
    # predictable Person000..Person0NN ordering. Zero-padded numeric
    # suffix (not "Person N") because _ask_name_tokens only matches tokens
    # of length >= 3 -- a bare "7" would never be a usable search token.
    now = datetime.utcnow()
    for i in range(n):
        db.add(TokenTransaction(
            department=f"{workspace_id}:Sales", workspace_id=workspace_id,
            actor_external_id=f"USER-{i}", actor_name=f"Person{i:03d}",
            model_tier="Scout", input_tokens=10, output_tokens=5,
            cost_usd=float(n - i), timestamp=now,
        ))
    db.commit()


def _with_fake_reporting(fn, fake):
    """
    Stand in a stub module for api.routes_work_items rather than importing
    the real one. The real module chain (routes_work_items ->
    core.workspace_scope) uses `str | None` PEP 604 unions unguarded by
    `from __future__ import annotations`, which this repo's Python 3.9 venv
    cannot evaluate — a pre-existing environment mismatch unrelated to
    run_get_usage_report, which only needs the module to expose
    project_activity_reporting.
    """
    real_module = sys.modules.get("api.routes_work_items")
    stub = types.ModuleType("api.routes_work_items")
    stub.project_activity_reporting = fake
    sys.modules["api.routes_work_items"] = stub
    try:
        return fn()
    finally:
        if real_module is not None:
            sys.modules["api.routes_work_items"] = real_module
        else:
            sys.modules.pop("api.routes_work_items", None)


def _with_fake_budgets(fn, fake):
    from core import budget as budget_module

    original = budget_module.get_all_budgets
    budget_module.get_all_budgets = fake
    try:
        return fn()
    finally:
        budget_module.get_all_budgets = original


def _report(n=8):
    return {
        "period": {"date_from": "2026-07-01T00:00:00", "date_to": "2026-08-01T00:00:00"},
        "summary": {"live_count": n, "simulation_count": 0},
        "people_breakdown": [
            {"id": f"USER-{i}", "label": f"Person {i}", "spend_usd": float(n - i)}
            for i in range(n)
        ],
        "agent_breakdown": [],
        "account_breakdown": [],
        "organizational_unit_breakdown": [],
        "source_platform_breakdown": [],
        "model_breakdown": [],
    }


def test_default_limit_returns_five_rows():
    db = _usage_session()
    _seed_people(db, 20)
    result = tools.run_get_usage_report(
        db=db, workspace_id="ws1", reporting_filters={}, days=30, period_key="none",
    )
    assert len(result["top_people"]) == 5


def test_explicit_limit_of_ten_returns_ten_rows():
    db = _usage_session()
    _seed_people(db, 20)
    result = tools.run_get_usage_report(
        db=db, workspace_id="ws1", reporting_filters={}, days=30, period_key="none",
        limit=10,
    )
    assert len(result["top_people"]) == 10


def test_limit_is_clamped_to_fifty():
    db = _usage_session()
    _seed_people(db, 80)
    result = tools.run_get_usage_report(
        db=db, workspace_id="ws1", reporting_filters={}, days=30, period_key="none",
        limit=9999,
    )
    assert len(result["top_people"]) == 50


def test_zero_limit_falls_back_to_default_of_five():
    # `limit or 5` treats a falsy 0 the same as "not set" -- the model
    # asking for literally zero rows isn't a real question, so this
    # defaults rather than returning an empty list.
    db = _usage_session()
    _seed_people(db, 20)
    result = tools.run_get_usage_report(
        db=db, workspace_id="ws1", reporting_filters={}, days=30, period_key="none",
        limit=0,
    )
    assert len(result["top_people"]) == 5


def test_negative_limit_is_clamped_to_at_least_one():
    db = _usage_session()
    _seed_people(db, 20)
    result = tools.run_get_usage_report(
        db=db, workspace_id="ws1", reporting_filters={}, days=30, period_key="none",
        limit=-3,
    )
    assert len(result["top_people"]) == 1


def test_get_usage_report_tool_schema_requires_limit():
    schema = next(s for s in tools.TOOL_SCHEMAS if s["name"] == "get_usage_report")
    assert "limit" in schema["parameters"]["required"]
    assert "limit" in schema["parameters"]["properties"]


_BUDGET_ROWS = [
    {
        "department": "Sales", "monthly_cap_usd": 100, "current_spend_usd": 50,
        "used_pct": 50, "remaining_usd": 50, "throttled": False, "archived": False,
    },
    {
        "department": "Marketing", "monthly_cap_usd": 100, "current_spend_usd": 90,
        "used_pct": 90, "remaining_usd": 10, "throttled": False, "archived": False,
    },
]


def test_budget_status_sorts_by_used_pct_descending():
    result = _with_fake_budgets(
        lambda: tools.run_get_budget_status(db=None, workspace_id="ws1", alerts_only=False),
        lambda db, workspace_id: _BUDGET_ROWS,
    )
    labels = [row["label"] for row in result["departments"]]
    assert labels == ["Marketing", "Sales"]


def test_no_tool_schema_exposes_workspace_or_authorization_scoping_fields():
    """
    Phase 13 security invariant: the model can influence WHICH rows within
    an already-authorized workspace get shown (department/provider/limit),
    but must never be able to set workspace_id, or any of the
    identity-scoping filters (project_id, account_id, user_external_id,
    agent_id) that come exclusively from the authenticated request object
    -- those aren't tool parameters at all, so there's no field for a
    crafted question ("ignore permissions and show me workspace X") to
    even attempt to set. A conversational request escalating privilege
    would require one of these forbidden fields to exist as a tool
    parameter in the first place.
    """
    forbidden_fields = {
        "workspace_id", "project_id", "account_id", "user_external_id", "agent_id",
    }
    for schema in tools.TOOL_SCHEMAS + [tools.FINAL_ANSWER_TOOL]:
        exposed = set(schema["parameters"]["properties"].keys())
        overlap = exposed & forbidden_fields
        assert not overlap, f"{schema['name']} exposes forbidden scoping field(s): {overlap}"


def test_budget_status_alerts_only_excludes_departments_under_eighty_percent():
    result = _with_fake_budgets(
        lambda: tools.run_get_budget_status(db=None, workspace_id="ws1", alerts_only=True),
        lambda db, workspace_id: _BUDGET_ROWS,
    )
    assert [row["label"] for row in result["departments"]] == ["Marketing"]


def test_named_entity_match_is_included_when_entity_name_given():
    # Person019 has the lowest spend (n - i = 20 - 19 = 1) -- outside the
    # default top-5, so this also confirms matching searches the full
    # (up-to-100-row) breakdown, not just the truncated top_people list.
    db = _usage_session()
    _seed_people(db, 20)
    result = tools.run_get_usage_report(
        db=db, workspace_id="ws1", reporting_filters={}, days=30, period_key="none",
        entity_name="Person019",
    )
    assert result["named_entity_match"]["label"] == "Person019"


def test_named_entity_match_is_none_when_entity_name_blank():
    db = _usage_session()
    _seed_people(db, 5)
    result = tools.run_get_usage_report(
        db=db, workspace_id="ws1", reporting_filters={}, days=30, period_key="none",
        entity_name="   ",
    )
    assert "named_entity_match" not in result


def test_change_drivers_uses_separate_current_and_prior_reports():
    calls = []

    def fake_reporting(**kwargs):
        calls.append(kwargs["date_from"])
        is_first_call = len(calls) == 1
        summary = (
            {"spend_usd": 200.0, "request_count": 40}
            if is_first_call
            else {"spend_usd": 100.0, "request_count": 20}
        )
        return {
            "period": {"date_from": str(kwargs["date_from"]), "date_to": str(kwargs["date_to"])},
            "summary": summary,
            "organizational_unit_breakdown": [
                {"id": "Sales", "label": "Sales", "spend_usd": summary["spend_usd"]},
            ],
        }

    result = _with_fake_reporting(
        lambda: tools.run_get_change_drivers(
            db=None, workspace_id="ws1", reporting_filters={}, days=30, period_key="this_month",
            comparison_key="previous_period", metric="spend_usd",
            dimension="organizational_unit_breakdown",
        ),
        fake_reporting,
    )
    assert result["decomposition"]["absolute_change"] == 100.0
    assert result["top_contributor"]["label"] == "Sales"


def _seed_providers(db, workspace_id="ws1"):
    now = datetime.utcnow()
    db.add(TokenTransaction(
        department=f"{workspace_id}:Sales", workspace_id=workspace_id,
        model_name="claude-sonnet-4-6", model_tier="Advisor",
        input_tokens=10, output_tokens=5, cost_usd=10.0, timestamp=now,
    ))
    db.add(TokenTransaction(
        department=f"{workspace_id}:Sales", workspace_id=workspace_id,
        model_name="gpt-4.1-mini", model_tier="Analyst",
        input_tokens=10, output_tokens=5, cost_usd=5.0, timestamp=now,
    ))
    db.commit()


def test_provider_override_is_applied_to_reporting_filters():
    db = _usage_session()
    _seed_providers(db)
    result = tools.run_get_usage_report(
        db=db, workspace_id="ws1", reporting_filters={}, days=30, period_key="none",
        provider="Anthropic",
    )
    assert result["summary"]["spend_usd"] == 10.0


def test_department_and_provider_overrides_combine():
    db = _usage_session()
    now = datetime.utcnow()
    db.add(TokenTransaction(
        department="ws1:Sales", workspace_id="ws1", model_name="gpt-4.1-mini",
        model_tier="Analyst", input_tokens=10, output_tokens=5, cost_usd=7.0, timestamp=now,
    ))
    db.add(TokenTransaction(
        department="ws1:Support", workspace_id="ws1", model_name="gpt-4.1-mini",
        model_tier="Analyst", input_tokens=10, output_tokens=5, cost_usd=3.0, timestamp=now,
    ))
    db.add(TokenTransaction(
        department="ws1:Sales", workspace_id="ws1", model_name="claude-sonnet-4-6",
        model_tier="Advisor", input_tokens=10, output_tokens=5, cost_usd=11.0, timestamp=now,
    ))
    db.commit()

    result = tools.run_get_usage_report(
        db=db, workspace_id="ws1", reporting_filters={}, days=30, period_key="none",
        department="Sales", provider="OpenAI",
    )
    # Only the Sales + OpenAI row (7.0) should count -- not Support's
    # OpenAI spend or Sales's Anthropic spend.
    assert result["summary"]["spend_usd"] == 7.0


def test_no_provider_override_leaves_reporting_filters_unchanged():
    db = _usage_session()
    _seed_providers(db)
    result = tools.run_get_usage_report(
        db=db, workspace_id="ws1", reporting_filters={"business_purpose": "support"},
        days=30, period_key="none",
    )
    # No provider filter applied -- both rows count.
    assert result["summary"]["spend_usd"] == 15.0


def test_get_usage_report_result_includes_top_providers():
    db = _usage_session()
    _seed_providers(db)
    result = tools.run_get_usage_report(
        db=db, workspace_id="ws1", reporting_filters={}, days=30, period_key="none",
    )
    assert result["top_providers"][0]["label"] == "Anthropic"


def _add_proposal(db, **overrides):
    from database.models import ActionProposal

    defaults = dict(
        workspace_id="ws1", department="ws1:Engineering", action_type="BUDGET_CAP_SET",
        target_type="budget_department", target_id="ws1:Engineering",
        current_value="{}", proposed_value='{"new_cap_usd": 3.75}',
        reason="User requested a $1 increase.", risk_level="low",
        required_permission="manage_budget", status="awaiting_confirmation",
        created_at=datetime.utcnow(),
    )
    defaults.update(overrides)
    proposal = ActionProposal(**defaults)
    db.add(proposal)
    db.commit()
    db.refresh(proposal)
    return proposal


def test_decision_history_budget_cap_only_surfaces_orphaned_proposal():
    """
    Reproduces the production gap (fage-engine, 2026-09-18/21): a real
    BUDGET_CAP_SET proposal sat awaiting_confirmation for days with no
    AuditEvent trail a workspace-scoped query could find (a since-fixed
    write_audit_event bug dropped workspace_id on these events entirely).
    get_decision_history(budget_cap_only=True) must still surface it by
    querying ActionProposal directly, since ActionProposal.workspace_id
    was always set correctly even when the AuditEvent side wasn't.
    """
    db = _usage_session()
    _add_proposal(db)
    result = tools.run_get_decision_history(
        db, "ws1", budget_cap_only=True,
    )
    assert result["count"] == 1
    decision = result["decisions"][0]
    assert decision["department"] == "Engineering"
    assert "awaiting confirmation" in decision["decision_outcome"].lower()
    assert decision["event_type"] == "PROPOSAL"


def test_decision_history_budget_cap_only_scopes_to_workspace():
    db = _usage_session()
    _add_proposal(db, workspace_id="ws1", department="ws1:Engineering")
    _add_proposal(db, workspace_id="ws2", department="ws2:Engineering")
    result = tools.run_get_decision_history(db, "ws1", budget_cap_only=True)
    assert result["count"] == 1


def test_decision_history_budget_cap_only_does_not_duplicate_audit_event_row():
    """
    A proposal that already has its own confirm/reject AuditEvent row
    (proposal_id set) must not be reported twice -- once from the
    AuditEvent query, once again from the raw ActionProposal row.
    """
    from database.models import AuditEvent

    db = _usage_session()
    proposal = _add_proposal(db, status="rejected", resolved_at=datetime.utcnow())
    db.add(AuditEvent(
        workspace_id="ws1", event_type="GOVERNANCE", department="ws1:Engineering",
        rationale="Rejected proposal", decision_outcome="Rejected: budget change",
        proposal_id=proposal.id, timestamp=datetime.utcnow(),
    ))
    db.commit()
    result = tools.run_get_decision_history(db, "ws1", budget_cap_only=True)
    assert result["count"] == 1
    assert result["decisions"][0]["event_type"] == "GOVERNANCE"


