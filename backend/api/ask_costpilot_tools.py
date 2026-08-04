"""
api/ask_costpilot_tools.py — tool definitions for the Ask CostPilot agent loop.

Each tool thinly wraps an existing, already-deterministic computation
function — it does not reimplement any math. The agent loop in
routes_efficiency.py (_ask_costpilot_agent) lets the model call these
iteratively, then write a final answer that can only reference facts these
tools returned. The model never sees raw DB access and never supplies a
number of its own.
"""

from datetime import datetime
from typing import Optional

from core.analytics_drivers import change_decomposition, dimension_contributors, top_contributor
from core.analytics_periods import comparison_plan, resolve_primary_period
from core.costpilot_knowledge import search_costpilot_knowledge
from database.models import DepartmentBudget


TOOL_SCHEMAS = [
    {
        "type": "function",
        "name": "get_usage_report",
        "description": (
            "Get attributed AI spend/usage totals and breakdowns (by person, agent, "
            "department, context, platform, model) for a date range. This is the "
            "primary lookup for 'how much', 'who', 'what', and ranking questions."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "Rolling window in days ending now, used only if period_key is 'none'.",
                },
                "period_key": {
                    "type": "string",
                    "enum": [
                        "none", "today", "yesterday", "this_week", "last_week",
                        "this_month", "last_month", "this_quarter", "last_quarter",
                        "this_year", "last_year",
                    ],
                    "description": "A named calendar period. Use 'none' to fall back to a rolling `days` window.",
                },
            },
            "required": ["days", "period_key"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "get_change_drivers",
        "description": (
            "Compare a metric (spend_usd, total_tokens, request_count) between the "
            "current period and a comparison period, and attribute the change to "
            "specific departments/agents/platforms. Use this for 'why did X change' "
            "or 'what's driving' questions."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "Length of the primary period in days."},
                "period_key": {
                    "type": "string",
                    "enum": [
                        "none", "today", "this_week", "this_month", "this_quarter", "this_year",
                    ],
                },
                "comparison_key": {
                    "type": "string",
                    "enum": ["previous_period", "previous_month", "previous_quarter", "same_period_previous_year"],
                },
                "metric": {
                    "type": "string",
                    "enum": ["spend_usd", "total_tokens", "request_count"],
                },
                "dimension": {
                    "type": "string",
                    "enum": ["organizational_unit_breakdown", "agent_breakdown", "source_platform_breakdown"],
                    "description": "Which breakdown to attribute the change against.",
                },
            },
            "required": ["days", "period_key", "comparison_key", "metric", "dimension"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "get_budget_status",
        "description": (
            "Get monthly AI budget caps, current spend, and remaining budget per "
            "department. Use for budget, throttle, and 'on track' questions."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "alerts_only": {
                    "type": "boolean",
                    "description": "If true, return only departments at or above 80% of cap.",
                },
            },
            "required": ["alerts_only"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "get_product_help",
        "description": (
            "Look up curated CostPilot product documentation — what a term means, "
            "how a metric is calculated, or how a feature works. Use for questions "
            "about CostPilot itself, not the customer's data."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "The product question or term to look up."},
            },
            "required": ["topic"],
            "additionalProperties": False,
        },
    },
]

FINAL_ANSWER_TOOL = {
    "type": "function",
    "name": "final_answer",
    "description": (
        "Give the final answer to the user. Only reference numbers and evidence "
        "already returned by a prior tool call — never state a figure you have not "
        "retrieved via a tool in this conversation."
    ),
    "strict": True,
    "parameters": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "A short (<=80 char) headline for the answer."},
            "answer": {
                "type": "string",
                "description": "The full answer in plain English, grounded only in retrieved tool results.",
            },
            "evidence_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Evidence row ids (from get_usage_report/get_change_drivers results) cited in the answer.",
            },
        },
        "required": ["title", "answer", "evidence_ids"],
        "additionalProperties": False,
    },
}


def to_anthropic_tools(schemas: list[dict]) -> list[dict]:
    """Convert an OpenAI-style function tool schema list to Anthropic's tool format."""
    return [
        {
            "name": schema["name"],
            "description": schema["description"],
            "input_schema": schema["parameters"],
        }
        for schema in schemas
    ]


def _period_bounds(days: int, period_key: str) -> tuple[Optional[datetime], Optional[datetime]]:
    """Resolve a named period (or a rolling `days` window) to UTC bounds."""
    if period_key in (None, "none"):
        return None, None
    period = resolve_primary_period(period_key=period_key, days=days)
    return period.start, period.end


def run_get_usage_report(db, workspace_id: Optional[str], reporting_filters: dict, days: int, period_key: str) -> dict:
    from api.routes_work_items import project_activity_reporting

    date_from, date_to = _period_bounds(days, period_key)
    report = project_activity_reporting(
        workspace_id=workspace_id,
        date_from=date_from,
        date_to=date_to,
        days=days,
        **reporting_filters,
        activity_limit=500,
        db=db,
    )
    summary = report.get("summary") or {}
    return {
        "period": report.get("period"),
        "summary": summary,
        "top_people": (report.get("people_breakdown") or [])[:5],
        "top_agents": (report.get("agent_breakdown") or [])[:5],
        "top_departments": (report.get("organizational_unit_breakdown") or [])[:5],
        "top_platforms": (report.get("source_platform_breakdown") or [])[:5],
        "top_models": (report.get("model_breakdown") or [])[:5],
        "data_scope": (
            "mixed" if summary.get("live_count") and summary.get("simulation_count")
            else "simulator" if summary.get("simulation_count")
            else "live" if summary.get("live_count")
            else "no_activity"
        ),
    }


def run_get_change_drivers(
    db, workspace_id: Optional[str], reporting_filters: dict,
    days: int, period_key: str, comparison_key: str, metric: str, dimension: str,
) -> dict:
    from api.routes_work_items import project_activity_reporting

    primary_period = resolve_primary_period(
        period_key=None if period_key in (None, "none") else period_key,
        days=days,
    )
    plan = comparison_plan(primary_period, comparison_key or "previous_period")
    current_report = project_activity_reporting(
        workspace_id=workspace_id, date_from=plan.primary.start, date_to=plan.primary.end,
        days=days, **reporting_filters, activity_limit=500, db=db,
    )
    prior_report = project_activity_reporting(
        workspace_id=workspace_id, date_from=plan.comparison.start, date_to=plan.comparison.end,
        days=days, **reporting_filters, activity_limit=500, db=db,
    )
    current_summary = current_report.get("summary") or {}
    prior_summary = prior_report.get("summary") or {}
    decomposition = change_decomposition(current_summary, prior_summary, metric)
    contributors = dimension_contributors(
        current_report.get(dimension) or [], prior_report.get(dimension) or [],
        metric, dimension, decomposition["absolute_change"], limit=5,
    )
    return {
        "period_comparison": plan.contract(),
        "decomposition": decomposition,
        "top_contributors": contributors,
        "top_contributor": top_contributor(contributors),
    }


def run_get_budget_status(db, workspace_id: Optional[str], alerts_only: bool) -> dict:
    from sqlalchemy import or_
    from api.routes_work_items import project_activity_reporting

    query = db.query(DepartmentBudget).filter(
        or_(DepartmentBudget.archived == False, DepartmentBudget.archived.is_(None))  # noqa: E712
    )
    if workspace_id:
        query = query.filter(DepartmentBudget.department.like(f"{workspace_id}:%"))
    else:
        # Without this, every workspace's budget rows for the same department
        # name (e.g. "Engineering") would all be returned together.
        query = query.filter(~DepartmentBudget.department.like("%:%"))

    # DepartmentBudget.current_spend_usd is only incremented by live request
    # routing — backfilled/simulated history never touches it, so it reads
    # $0.00 forever for a workspace like the historical demo even though real
    # activity exists. Recompute month-to-date spend from the actual ledger
    # instead, the same way the rest of the app (dashboard, Ask CostPilot's
    # deterministic budget intent) already does.
    month_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    report = project_activity_reporting(
        workspace_id=workspace_id, date_from=month_start, date_to=datetime.utcnow(), days=31,
        project_id=None, user_external_id=None, agent_id=None, account_id=None,
        source_platform=None, record_type=None, model_tier=None, charged_unit=None,
        business_purpose=None, activity_limit=2000, db=db,
    )
    spend_by_department: dict[str, float] = {}
    for activity_row in report.get("organizational_unit_breakdown") or []:
        raw_department = str(activity_row.get("id") or activity_row.get("label") or "").strip()
        if workspace_id and raw_department.startswith(f"{workspace_id}:"):
            raw_department = raw_department[len(workspace_id) + 1:]
        elif ":" in raw_department:
            raw_department = raw_department.rsplit(":", 1)[-1]
        key = raw_department.casefold()
        if key:
            spend_by_department[key] = spend_by_department.get(key, 0.0) + float(activity_row.get("spend_usd") or 0)

    rows = []
    for budget in query.all():
        cap = float(budget.monthly_cap_usd or 0)
        if cap <= 0:
            continue
        label = (budget.department or "Unassigned").split(":")[-1]
        spend = spend_by_department.get(label.casefold(), 0.0)
        pct = round(spend / cap * 100, 1)
        if alerts_only and pct < 80:
            continue
        rows.append({
            "id": budget.department,
            "label": label,
            "monthly_cap_usd": cap,
            "current_spend_usd": round(spend, 6),
            "remaining_usd": max(cap - spend, 0),
            "used_pct": pct,
            "throttled": bool(budget.throttled),
        })
    rows.sort(key=lambda row: -row["used_pct"])
    return {"departments": rows}


def run_get_product_help(topic: str) -> dict:
    topics = search_costpilot_knowledge(topic, limit=3)
    return {
        "topics": [
            {"id": t["id"], "summary": t["summary"], "details": t["details"]}
            for t in topics
        ]
    }


EXECUTORS = {
    "get_usage_report": run_get_usage_report,
    "get_change_drivers": run_get_change_drivers,
    "get_budget_status": run_get_budget_status,
    "get_product_help": run_get_product_help,
}
