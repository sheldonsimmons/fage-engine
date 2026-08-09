"""
Tests for api/ask_costpilot_tools.py — the tool executors behind the Ask
CostPilot agent loop. These check the deterministic wrapper behavior only
(no live model calls): row-limit clamping, entity-name pass-through, and
that budget/agent-adoption rows are shaped the way the agent's final-answer
builder expects.
"""
import sys
import types

import api.ask_costpilot_tools as tools


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
    result = _with_fake_reporting(
        lambda: tools.run_get_usage_report(
            db=None, workspace_id="ws1", reporting_filters={}, days=30, period_key="none",
        ),
        lambda **kwargs: _report(20),
    )
    assert len(result["top_people"]) == 5


def test_explicit_limit_of_ten_returns_ten_rows():
    result = _with_fake_reporting(
        lambda: tools.run_get_usage_report(
            db=None, workspace_id="ws1", reporting_filters={}, days=30, period_key="none",
            limit=10,
        ),
        lambda **kwargs: _report(20),
    )
    assert len(result["top_people"]) == 10


def test_limit_is_clamped_to_fifty():
    result = _with_fake_reporting(
        lambda: tools.run_get_usage_report(
            db=None, workspace_id="ws1", reporting_filters={}, days=30, period_key="none",
            limit=9999,
        ),
        lambda **kwargs: _report(80),
    )
    assert len(result["top_people"]) == 50


def test_zero_limit_falls_back_to_default_of_five():
    # `limit or 5` treats a falsy 0 the same as "not set" -- the model
    # asking for literally zero rows isn't a real question, so this
    # defaults rather than returning an empty list.
    result = _with_fake_reporting(
        lambda: tools.run_get_usage_report(
            db=None, workspace_id="ws1", reporting_filters={}, days=30, period_key="none",
            limit=0,
        ),
        lambda **kwargs: _report(20),
    )
    assert len(result["top_people"]) == 5


def test_negative_limit_is_clamped_to_at_least_one():
    result = _with_fake_reporting(
        lambda: tools.run_get_usage_report(
            db=None, workspace_id="ws1", reporting_filters={}, days=30, period_key="none",
            limit=-3,
        ),
        lambda **kwargs: _report(20),
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
    report = _report(20)

    def fake_named_entity(name, rep):
        assert name == "Person 7"
        return {"entity": "people", "row": rep["people_breakdown"][7]}

    from api import routes_efficiency
    original = routes_efficiency._ask_named_entity
    routes_efficiency._ask_named_entity = fake_named_entity
    try:
        result = _with_fake_reporting(
            lambda: tools.run_get_usage_report(
                db=None, workspace_id="ws1", reporting_filters={}, days=30, period_key="none",
                entity_name="Person 7",
            ),
            lambda **kwargs: report,
        )
    finally:
        routes_efficiency._ask_named_entity = original
    assert result["named_entity_match"]["label"] == "Person 7"


def test_named_entity_match_is_none_when_entity_name_blank():
    result = _with_fake_reporting(
        lambda: tools.run_get_usage_report(
            db=None, workspace_id="ws1", reporting_filters={}, days=30, period_key="none",
            entity_name="   ",
        ),
        lambda **kwargs: _report(5),
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


def test_provider_override_is_applied_to_reporting_filters():
    captured = {}

    def fake_reporting(**kwargs):
        captured.update(kwargs)
        return _report(3)

    _with_fake_reporting(
        lambda: tools.run_get_usage_report(
            db=None, workspace_id="ws1", reporting_filters={}, days=30, period_key="none",
            provider="Anthropic",
        ),
        fake_reporting,
    )
    assert captured.get("provider") == "Anthropic"


def test_department_and_provider_overrides_combine():
    captured = {}

    def fake_reporting(**kwargs):
        captured.update(kwargs)
        return _report(3)

    _with_fake_reporting(
        lambda: tools.run_get_usage_report(
            db=None, workspace_id="ws1", reporting_filters={}, days=30, period_key="none",
            department="Sales", provider="OpenAI",
        ),
        fake_reporting,
    )
    assert captured.get("charged_unit") == "Sales"
    assert captured.get("provider") == "OpenAI"


def test_no_provider_override_leaves_reporting_filters_unchanged():
    captured = {}

    def fake_reporting(**kwargs):
        captured.update(kwargs)
        return _report(3)

    _with_fake_reporting(
        lambda: tools.run_get_usage_report(
            db=None, workspace_id="ws1", reporting_filters={"business_purpose": "support"},
            days=30, period_key="none",
        ),
        fake_reporting,
    )
    assert "provider" not in captured
    assert captured.get("business_purpose") == "support"


def test_get_usage_report_result_includes_top_providers(monkeypatch):
    report = _report(3)
    report["provider_breakdown"] = [
        {"id": "Anthropic", "label": "Anthropic", "spend_usd": 10.0},
        {"id": "OpenAI", "label": "OpenAI", "spend_usd": 5.0},
    ]
    result = _with_fake_reporting(
        lambda: tools.run_get_usage_report(
            db=None, workspace_id="ws1", reporting_filters={}, days=30, period_key="none",
        ),
        lambda **kwargs: report,
    )
    assert result["top_providers"][0]["label"] == "Anthropic"


