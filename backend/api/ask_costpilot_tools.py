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
                "entity_name": {
                    "type": "string",
                    "description": (
                        "The exact name of a specific person, account, department, agent, "
                        "platform, or model the question names (e.g. 'BluePeak Consulting', "
                        "'Maya Chen'). When set, the result includes a direct lookup for that "
                        "entity even if it would not otherwise appear in the top 5 lists — use "
                        "this instead of guessing from the top 5 whenever the question names a "
                        "specific subject. Empty string for general/overview questions."
                    ),
                },
            },
            "required": ["days", "period_key", "entity_name"],
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
    {
        "type": "function",
        "name": "get_agent_adoption",
        "description": (
            "List registered agents by usage status — which ones are active, "
            "which have never been used, and which have gone quiet recently. "
            "This is the ONLY tool that knows about agents with zero activity — "
            "get_usage_report only ever returns agents that have activity, so it "
            "cannot answer 'which agents are inactive/unused/never used'."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["all", "never", "recently_inactive", "unused", "low", "active"],
                    "description": (
                        "'unused' = never used OR recently inactive (combined). "
                        "'never' = zero lifetime requests, ever. 'recently_inactive' = "
                        "has history but nothing in the current period. 'low' = active "
                        "but under the usage threshold. 'active' = at or above threshold. "
                        "'all' = every agent with its status."
                    ),
                },
                "usage_threshold": {
                    "type": "integer",
                    "description": "Requests-in-period floor between 'low' and 'active'. Default 10.",
                },
                "days": {
                    "type": "integer",
                    "description": "Rolling window in days ending now that defines 'this period', used only if period_key is 'none'.",
                },
                "period_key": {
                    "type": "string",
                    "enum": [
                        "none", "today", "yesterday", "this_week", "last_week",
                        "this_month", "last_month", "this_quarter", "last_quarter",
                        "this_year", "last_year",
                    ],
                    "description": "A named calendar period defining 'this period'. Use 'none' to fall back to a rolling `days` window.",
                },
            },
            "required": ["status", "usage_threshold", "days", "period_key"],
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


def scope_from_summary(summary: dict) -> str:
    live = int(summary.get("live_count") or 0)
    simulation = int(summary.get("simulation_count") or 0)
    if live and simulation:
        return "mixed"
    if simulation:
        return "simulator"
    if live:
        return "live"
    return "no_activity"


def run_get_usage_report(
    db, workspace_id: Optional[str], reporting_filters: dict, days: int, period_key: str,
    entity_name: Optional[str] = None,
) -> dict:
    from api.routes_efficiency import _ask_named_entity
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
    result = {
        "period": report.get("period"),
        "summary": summary,
        "top_people": (report.get("people_breakdown") or [])[:5],
        "top_agents": (report.get("agent_breakdown") or [])[:5],
        # "Account" = the business/customer entity (e.g. a company record
        # in Salesforce) — distinct from "top_people" (individual human
        # users). Keep these separate; don't let the model substitute one
        # for the other.
        "top_accounts": (report.get("account_breakdown") or [])[:5],
        "top_departments": (report.get("organizational_unit_breakdown") or [])[:5],
        "top_platforms": (report.get("source_platform_breakdown") or [])[:5],
        "top_models": (report.get("model_breakdown") or [])[:5],
        "data_scope": scope_from_summary(summary),
    }
    # A named person/account/department/etc. is often outside the top 5 by
    # spend — without this, the model had no way to answer "how much did X
    # use" except by guessing from the truncated top lists or falling back
    # to the overall total. Match against the FULL (untruncated) breakdowns
    # so any named entity is found regardless of rank.
    if entity_name and entity_name.strip():
        match = _ask_named_entity(entity_name.strip(), report)
        result["named_entity_match"] = (
            {
                "entity_type": match["entity"],
                "label": match["row"].get("label"),
                "row": match["row"],
            }
            if match else None
        )
    return result


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
        "period": current_report.get("period"),
        "period_comparison": plan.contract(),
        "decomposition": decomposition,
        "top_contributors": contributors,
        "top_contributor": top_contributor(contributors),
        "data_scope": scope_from_summary(current_summary),
        "summary": current_summary,
    }


def run_get_budget_status(db, workspace_id: Optional[str], alerts_only: bool) -> dict:
    # Delegates spend/cap computation entirely to core.budget.get_all_budgets
    # — the same function Admin > Budgets uses — instead of re-deriving the
    # production-vs-recomputed branch here. Three independent copies of this
    # branch (this one, the Admin page, and Ask CostPilot's deterministic
    # answer) is exactly how the budget numbers ended up disagreeing across
    # surfaces; this is one of them retired in favor of the shared function.
    from core.budget import get_all_budgets

    rows = []
    for budget in get_all_budgets(db, workspace_id):
        cap = float(budget["monthly_cap_usd"] or 0)
        if cap <= 0 or budget.get("archived"):
            continue
        spend = float(budget["current_spend_usd"] or 0)
        pct = float(budget["used_pct"] or 0)
        if alerts_only and pct < 80:
            continue
        rows.append({
            "id": budget["department"],
            "label": (budget["department"] or "Unassigned").split(":")[-1],
            "monthly_cap_usd": cap,
            "current_spend_usd": round(spend, 6),
            "remaining_usd": budget["remaining_usd"],
            "used_pct": pct,
            "throttled": bool(budget["throttled"]),
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


def run_get_agent_adoption(
    db, workspace_id: Optional[str], status: str, usage_threshold: int,
    days: int = 30, period_key: str = "none",
) -> dict:
    """
    Classify every registered agent by usage status. Mirrors the
    deterministic agent_adoption intent in routes_efficiency.py exactly —
    same classification rule and same two-count model (lifetime activity
    from AuditEvent decides "never" vs "has history"; CURRENT-PERIOD
    activity from project_activity_reporting decides "recently_inactive"
    vs "low"/"active") — so this tool and that older code path can't
    disagree, and "not used in this period" is actually answerable (a
    lifetime-only count can't tell "never used" apart from "was active
    once, quiet now").
    """
    from sqlalchemy import func
    from database.models import AuditEvent, RegisteredAgent
    from api.routes_work_items import project_activity_reporting

    agent_query = db.query(RegisteredAgent).filter(RegisteredAgent.archived.isnot(True))
    if workspace_id:
        agent_query = agent_query.filter(RegisteredAgent.department.like(f"{workspace_id}:%"))
    agents = agent_query.all()

    lifetime_query = db.query(
        AuditEvent.agent_id, func.count(AuditEvent.id), func.max(AuditEvent.timestamp),
    ).filter(AuditEvent.agent_id.isnot(None))
    if workspace_id:
        lifetime_query = lifetime_query.filter(AuditEvent.workspace_id == workspace_id)
    lifetime_by_agent = {
        str(agent_id): {"request_count": int(count or 0), "last_used_at": last_used}
        for agent_id, count, last_used in lifetime_query.group_by(AuditEvent.agent_id).all()
    }

    date_from, date_to = _period_bounds(days, period_key)
    report = project_activity_reporting(
        workspace_id=workspace_id, date_from=date_from, date_to=date_to, days=days,
        project_id=None, user_external_id=None, agent_id=None, account_id=None,
        source_platform=None, record_type=None, model_tier=None, charged_unit=None,
        business_purpose=None, activity_limit=2000, db=db,
    )
    current_by_agent = {
        str(row.get("id")): int(row.get("request_count") or 0)
        for row in (report.get("agent_breakdown") or [])
        if row.get("id") is not None
    }

    threshold = max(1, int(usage_threshold or 10))
    rows = []
    counts = {"never": 0, "recently_inactive": 0, "low": 0, "active": 0}
    for agent in agents:
        lifetime = lifetime_by_agent.get(str(agent.id)) or {}
        lifetime_count = int(lifetime.get("request_count") or 0)
        current_count = current_by_agent.get(str(agent.id), 0)
        last_used_at = lifetime.get("last_used_at") or agent.last_used_at

        if lifetime_count == 0:
            row_status = "never"
        elif current_count == 0:
            row_status = "recently_inactive"
        elif current_count < threshold:
            row_status = "low"
        else:
            row_status = "active"
        counts[row_status] += 1

        wanted = (
            {"never", "recently_inactive"} if status == "unused"
            else {status} if status != "all" else None
        )
        if wanted is not None and row_status not in wanted:
            continue

        rows.append({
            "id": agent.id,
            "label": agent.name or "Unnamed agent",
            "status": row_status,
            "platform": agent.source_platform or "Unknown platform",
            "department": str(agent.department or "Unassigned").split(":")[-1],
            "current_period_requests": current_count,
            "lifetime_requests": lifetime_count,
            "last_used_at": last_used_at.isoformat() if last_used_at else None,
        })

    return {
        "agents": rows,
        "total_agents": len(agents),
        "status_counts": counts,
        "threshold": threshold,
        "period": report.get("period"),
    }


EXECUTORS = {
    "get_usage_report": run_get_usage_report,
    "get_change_drivers": run_get_change_drivers,
    "get_budget_status": run_get_budget_status,
    "get_product_help": run_get_product_help,
    "get_agent_adoption": run_get_agent_adoption,
}
