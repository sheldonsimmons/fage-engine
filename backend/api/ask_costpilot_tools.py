"""
api/ask_costpilot_tools.py — tool definitions for the Ask CostPilot agent loop.

Each tool thinly wraps an existing, already-deterministic computation
function — it does not reimplement any math. The agent loop in
routes_efficiency.py (_ask_costpilot_agent) lets the model call these
iteratively, then write a final answer that can only reference facts these
tools returned. The model never sees raw DB access and never supplies a
number of its own.
"""

from datetime import datetime, timedelta
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
                        "entity even if it would not otherwise appear in the top-N lists — use "
                        "this instead of guessing from the top-N whenever the question names a "
                        "specific subject. Empty string for general/overview questions."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": (
                        "How many rows to return per breakdown list (people/agents/accounts/"
                        "departments/platforms/models). Default 5. Set this to match the "
                        "question — e.g. use 10 for 'top 10 users', 1 for 'who spent the most'. "
                        "Never rely on the default and then guess at rows beyond what was "
                        "returned; ask for the number you actually need, up to 50."
                    ),
                },
                "department": {
                    "type": "string",
                    "description": (
                        "Scope every number in the result to a single department/team (e.g. "
                        "'Sales', 'Marketing'). Set this whenever the question names a "
                        "department, even to ask about a dimension other than department itself "
                        "— for example 'what models is Sales using' needs department='Sales' so "
                        "the model breakdown only reflects Sales activity, not the whole company. "
                        "Empty string for company-wide questions."
                    ),
                },
                "provider": {
                    "type": "string",
                    "enum": ["", "Anthropic", "OpenAI", "Google", "Mistral", "Meta"],
                    "description": (
                        "Scope every number in the result to a single AI provider/vendor -- "
                        "NOT the source platform (Salesforce/ServiceNow/etc). 'Claude' or "
                        "'Anthropic' in the question means provider='Anthropic'; 'GPT' or "
                        "'OpenAI' means provider='OpenAI'. Set this for questions like 'how "
                        "much are we spending on Claude' or 'compare OpenAI and Anthropic "
                        "spend' (call this tool twice, once per provider, for that one). "
                        "Empty string for provider-agnostic questions."
                    ),
                },
            },
            "required": ["days", "period_key", "entity_name", "limit", "department", "provider"],
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
                    "enum": [
                        "organizational_unit_breakdown", "agent_breakdown",
                        "source_platform_breakdown", "provider_breakdown",
                    ],
                    "description": "Which breakdown to attribute the change against.",
                },
                "department": {
                    "type": "string",
                    "description": (
                        "Scope the comparison to a single department/team (e.g. 'Sales') "
                        "if the question names one. Empty string for company-wide."
                    ),
                },
                "provider": {
                    "type": "string",
                    "enum": ["", "Anthropic", "OpenAI", "Google", "Mistral", "Meta"],
                    "description": (
                        "Scope the comparison to a single AI provider/vendor if the question "
                        "names one (e.g. 'why did Claude spend increase'). Empty for company-wide."
                    ),
                },
            },
            "required": [
                "days", "period_key", "comparison_key", "metric", "dimension",
                "department", "provider",
            ],
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
    {
        "type": "function",
        "name": "get_account_outcomes",
        "description": (
            "Get business outcomes (won/lost/open Opportunities, pipeline value, "
            "closed-won value, resolved support cases) and the AI spend/tokens tied "
            "to those outcomes. This is the ONLY tool that knows about Opportunities, "
            "deal outcomes, or win/loss -- get_usage_report only returns spend and "
            "call counts, never outcome data, even for a named account. Use this for "
            "any question about a named account's business results, or about won vs "
            "lost deals company-wide. Set entity_name to scope to one account (e.g. "
            "'Acme'); leave it empty for a company-wide/workspace-wide answer."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "entity_name": {
                    "type": "string",
                    "description": (
                        "The exact name of a specific account/company the question "
                        "names (e.g. 'Acme', 'BluePeak Consulting'). Empty string for "
                        "a company-wide/workspace-wide answer."
                    ),
                },
            },
            "required": ["entity_name"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "get_cost_per_outcome",
        "description": (
            "Get Cost per Outcome: AI spend associated with successful work "
            "divided by the count of successful outcomes -- e.g. cost per Closed "
            "Won opportunity, cost per resolved case, cost per hire, cost per "
            "shipped feature. Works for ANY work type, not just Opportunities. "
            "Use this for any 'cost per X' or 'return on AI spend' question. The "
            "result always reports an evidence_label (early_signal / meaningful / "
            "executive_eligible) based on sample size -- always state that label "
            "in the answer, and never present a result labeled early_signal as a "
            "confident finding. This measures association between AI activity and "
            "outcomes, never causation -- never say AI caused these outcomes."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "context_type": {
                    "type": "string",
                    "description": (
                        "Narrow to one work type: 'opportunity', 'case', 'ticket', "
                        "'incident', 'project', or any other context_type the "
                        "workspace uses. Empty string for every work type combined."
                    ),
                },
                "entity_name": {
                    "type": "string",
                    "description": (
                        "The exact name of a specific account/company to scope to. "
                        "Empty string for a company-wide/workspace-wide answer."
                    ),
                },
            },
            "required": ["context_type", "entity_name"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "get_data_coverage",
        "description": (
            "Check which source platforms (Salesforce, ServiceNow, HubSpot) are "
            "connected for this workspace, which objects are tracked, and how "
            "recently outcome data was synced. Call this BEFORE answering any "
            "question that names a specific platform (e.g. 'across Salesforce, "
            "HubSpot, and ServiceNow'), or before presenting outcome/business data "
            "as complete -- never assume a platform is connected or that data is "
            "current without checking. If a named platform is not connected, say so "
            "plainly instead of answering as if it were."
        ),
        "strict": True,
        "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    },
    {
        "type": "function",
        "name": "query_metrics",
        "description": (
            "General-purpose analytics: request any combination of CostPilot's "
            "defined metrics, grouped by any combination of its defined "
            "dimensions, filtered, over a timeframe, optionally compared to a "
            "prior period. This is the PREFERRED tool for ordinary reporting "
            "questions -- ranking, breakdowns, filtered totals, comparisons -- "
            "instead of get_usage_report or get_change_drivers, which exist "
            "mainly for backward compatibility. Every metric has one fixed "
            "definition (see the metric_definitions field in the result); you "
            "never need to guess what a number means or compute one yourself. "
            "Metrics come from two sources that get merged automatically: "
            "activity metrics (ai_spend, ai_requests, tokens, active_agents, "
            "work_items_touched, accounts_touched) and outcome metrics "
            "(won_count, lost_count, open_count, won_value, pipeline_value, "
            "support_cases_total, support_cases_resolved). You can request both "
            "kinds together (e.g. ai_spend + won_value) but when you do, only "
            "the 'account' dimension can be used to group them -- request them "
            "separately if you need another dimension. Requesting a metric this "
            "tool doesn't support yet returns it in unsupported_metrics with a "
            "reason instead of an error -- read that back to the user honestly "
            "rather than substituting a different number."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "metrics": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "One or more of: ai_spend, ai_requests, input_tokens, output_tokens, "
                        "total_tokens, work_items_touched, accounts_touched, active_agents, "
                        "savings, pruning_savings, downgrade_savings, won_count, lost_count, "
                        "open_count, won_value, pipeline_value, support_cases_total, "
                        "support_cases_resolved. 'savings' is what routing/pruning already saved "
                        "vs. flagship-rate cost -- use it for 'where can we save money' style "
                        "questions about savings ALREADY achieved, not future opportunities."
                    ),
                },
                "dimensions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Zero or more of: account, department, agent, platform, model, outcome_status.",
                },
                "filters": {
                    "type": "object",
                    "properties": {
                        "account": {"type": "string", "description": "Named account, e.g. 'Acme'. Empty string for none."},
                        "department": {"type": "string", "description": "Named department. Empty string for none."},
                        "agent": {"type": "string", "description": "Named agent. Empty string for none."},
                        "platform": {"type": "string", "description": "Named source platform. Empty string for none."},
                        "model": {"type": "string", "description": "Named model. Empty string for none."},
                        "outcome_status": {
                            "type": "string", "enum": ["", "won", "lost", "open"],
                            "description": "Restrict outcome metrics to this status. Empty string for none.",
                        },
                    },
                    "required": ["account", "department", "agent", "platform", "model", "outcome_status"],
                    "additionalProperties": False,
                },
                "days": {"type": "integer", "description": "Rolling window in days ending now, used only if period_key is 'none'."},
                "period_key": {
                    "type": "string",
                    "enum": [
                        "none", "today", "yesterday", "this_week", "last_week",
                        "this_month", "last_month", "this_quarter", "last_quarter",
                        "this_year", "last_year",
                    ],
                    "description": "A named calendar period. Use 'none' to fall back to a rolling `days` window. Ignored for outcome-only metric requests, which are not time-windowed.",
                },
                "compare_to": {
                    "type": "string",
                    "enum": ["none", "previous_period", "same_period_previous_year", "previous_month", "previous_quarter"],
                    "description": (
                        "Compare the primary timeframe to a prior one. 'none' for no comparison. "
                        "When set with `dimensions`, the result's comparison.rows come back already "
                        "ranked by the size of the change (largest absolute difference first) and "
                        "capped at `limit` -- use this directly for 'which department drove the "
                        "increase' style questions instead of computing the ranking yourself."
                    ),
                },
                "sort": {
                    "type": "string",
                    "description": "Which requested metric to sort rows by, descending. Empty string to sort by the first requested metric.",
                },
                "limit": {"type": "integer", "description": "Max rows to return, ranked by `sort`. Default 20."},
            },
            "required": ["metrics", "dimensions", "filters", "days", "period_key", "compare_to", "sort", "limit"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "get_priority_signals",
        "description": (
            "Get a short, pre-ranked list of what deserves attention right now -- "
            "departments at or over their budget cap, and the biggest spend swings "
            "vs the prior period of equal length. Use this for open-ended questions "
            "like 'what should I pay attention to today', 'is anything unusual "
            "happening', or 'what are the top things I should know about' -- this "
            "tool does the ranking; narrate the list it returns, don't invent your "
            "own priorities or reorder them by your own judgment."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "Window (in days, ending now) used for the spend-change comparison. Default 7.",
                },
            },
            "required": ["days"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "get_decision_history",
        "description": (
            "Search past governance decisions and their plain-English rationale -- "
            "why a specific model was selected, why a budget action fired, why a "
            "collision lock was applied. Use this for 'why did we...', 'why are we "
            "using...', or 'what's our decision history on...' questions. This is "
            "different from a single pinned decision the user already has an id "
            "for -- this searches across many past events by agent, model, "
            "keyword, or event type."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "agent_name": {
                    "type": "string",
                    "description": "Filter to decisions involving this agent (partial match). Empty string for no filter.",
                },
                "model_name": {
                    "type": "string",
                    "description": "Filter to decisions involving this model name (partial match). Empty string for no filter.",
                },
                "keyword": {
                    "type": "string",
                    "description": "Search this keyword/phrase within the decision rationale text. Empty string for no filter.",
                },
                "event_type": {
                    "type": "string",
                    "description": "Filter to one event type, e.g. 'ROUTING', 'BUDGET', 'DECISION', 'LOCK'. Empty string for no filter.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of decisions to return. Default 10.",
                },
            },
            "required": ["agent_name", "model_name", "keyword", "event_type", "limit"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "propose_budget_cap_change",
        "description": (
            "Propose changing a department's monthly AI budget cap. This does NOT "
            "change anything yet -- it creates a proposal that a human must "
            "explicitly confirm before it takes effect. Only call this when the "
            "user has clearly asked to change a budget cap, or has explicitly "
            "accepted a recommendation you just made to do so -- never "
            "speculatively, and never for a 'what if' question. Call "
            "simulate_budget_cap_change instead for any hypothetical question "
            "('what if we raised X to $Y', 'would $Y be enough') -- it answers "
            "the same question without creating anything a human has to act on."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "department": {
                    "type": "string",
                    "description": "The exact department name the user named, e.g. 'Finance' or 'Support'.",
                },
                "new_cap_usd": {
                    "type": "number",
                    "description": "The proposed new monthly cap in US dollars.",
                },
                "reason": {
                    "type": "string",
                    "description": "A short, plain-English reason for this change, grounded in data already retrieved this conversation.",
                },
            },
            "required": ["department", "new_cap_usd", "reason"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "simulate_budget_cap_change",
        "description": (
            "Answer a hypothetical 'what if we changed department X's budget cap "
            "to $Y' question -- read-only, creates nothing, never requires "
            "confirmation. Returns the department's real current-period spend, "
            "its run-rate projection to month end, and whether the proposed cap "
            "would be exceeded and around when. Use this for any 'what if', "
            "'would $Y be enough', or 'should we raise the cap' question; use "
            "propose_budget_cap_change instead only once the user actually wants "
            "to make the change."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "department": {
                    "type": "string",
                    "description": "The exact department name the user named, e.g. 'Finance' or 'Support'.",
                },
                "new_cap_usd": {
                    "type": "number",
                    "description": "The hypothetical monthly cap in US dollars to test.",
                },
            },
            "required": ["department", "new_cap_usd"],
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
            "title": {
                "type": "string",
                "description": (
                    "A short (<=80 char) headline for the answer. Use the resolved entity's "
                    "real name/label from tool results, not the user's literal wording -- a "
                    "voice question may contain a misheard word that a tool result corrects."
                ),
            },
            "answer": {
                "type": "string",
                "description": (
                    "The full answer in plain English, grounded only in retrieved tool results. "
                    "Use the resolved entity's real name/label, not a misheard word from the question."
                ),
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


def _with_department_override(
    reporting_filters: dict, department: Optional[str], provider: Optional[str] = None,
) -> dict:
    """
    Let the model scope a lookup to a specific department and/or AI
    provider for THIS call, overriding whatever static filters the request
    carried in from page context (or the lack of them). Without this, a
    question like "what models is Sales using" had no way to actually
    filter to Sales — the tool could only read the unfiltered,
    company-wide top-5 breakdown and the model would narrate it as if it
    were Sales-scoped, silently mixing an unfiltered total with a
    department-specific-sounding answer. Provider (Anthropic/OpenAI/...)
    has the identical failure mode for "how much are we spending on
    Claude" -- see core/model_provider.py for how it's resolved.
    """
    overrides = {}
    if department and department.strip():
        overrides["charged_unit"] = department.strip()
    if provider and provider.strip():
        overrides["provider"] = provider.strip()
    return {**reporting_filters, **overrides} if overrides else reporting_filters


# Every metric run_get_usage_report needs, in one request per dimension --
# run_metrics_query cross-tabs a COMBINED dimension set rather than
# producing independent breakdowns, so 7 separate breakdowns (person,
# agent, account, department, platform, model, provider) require 7 calls,
# not one with dimensions=[...7 keys...].
_USAGE_REPORT_METRICS = [
    "ai_spend", "ai_requests", "input_tokens", "output_tokens", "total_tokens",
    "tokens_saved_count", "simulation_count", "live_count",
]
_USAGE_REPORT_SUMMARY_METRICS = _USAGE_REPORT_METRICS + ["people_touched", "active_agents", "work_items_touched"]

# (result key, registry dimension key, ordinal id source in dim_key)
_USAGE_REPORT_DIMENSIONS = (
    ("top_people", "person"),
    ("top_agents", "agent"),
    ("top_accounts", "account"),
    ("top_departments", "department"),
    ("top_platforms", "platform"),
    ("top_models", "model"),
    ("top_providers", "provider"),
)


def _shape_usage_row(row: dict, dim_key: str) -> dict:
    """Reshapes one run_metrics_query row into project_activity_reporting()'s
    per-bucket shape (id/label/request_count/... ) so downstream callers
    (Ask CostPilot's model-facing payload, _ask_named_entity) don't need to
    know which code path produced the row."""
    dims = row.get("dimensions") or {}
    label = dims.get(dim_key)
    request_count = int(row.get("ai_requests") or 0)
    simulation_count = int(row.get("simulation_count") or 0)
    return {
        "id": label,
        "label": label if label is not None else "Unknown",
        "request_count": request_count,
        "input_tokens": int(row.get("input_tokens") or 0),
        "output_tokens": int(row.get("output_tokens") or 0),
        "tokens_saved": int(row.get("tokens_saved_count") or 0),
        "spend_usd": round(float(row.get("ai_spend") or 0.0), 6),
        "simulation_count": simulation_count,
        "total_tokens": int(row.get("total_tokens") or 0),
        "live_count": int(row.get("live_count") if "live_count" in row else request_count - simulation_count),
    }


def _usage_report_filters(reporting_filters: dict) -> dict:
    """
    Maps project_activity_reporting()'s kwarg names onto run_metrics_query's
    filters dict -- both take the same values, just under different keys
    for the ones that differ (charged_unit is already the shared name).
    """
    key_map = {
        "user_external_id": None,  # not needed: person filtering isn't used by any get_usage_report call site today
        "agent_id": "agent_id",
        "source_platform": "platform",
        "model_tier": "model",
        "charged_unit": "charged_unit",
        "provider": "provider",
        "record_type": None,  # no registry equivalent; not used by any get_usage_report call site today
        "business_purpose": None,  # no registry equivalent; not used by any get_usage_report call site today
    }
    filters = {}
    for src_key, dst_key in key_map.items():
        value = reporting_filters.get(src_key)
        if value and dst_key:
            filters[dst_key] = value
    return filters


def run_get_usage_report(
    db, workspace_id: Optional[str], reporting_filters: dict, days: int, period_key: str,
    entity_name: Optional[str] = None, limit: Optional[int] = None,
    department: Optional[str] = None, provider: Optional[str] = None,
) -> dict:
    from api.routes_efficiency import _ask_named_entity
    from core.metrics_query import run_metrics_query

    # Clamp rather than trust the model's number outright: too low silently
    # drops requested rows, too high blows up the tool-result payload sent
    # back into the model's context for no benefit past a top-50 answer.
    row_limit = max(1, min(int(limit or 5), 50))
    reporting_filters = _with_department_override(reporting_filters, department, provider)
    filters = _usage_report_filters(reporting_filters)

    date_from, date_to = _period_bounds(days, period_key)
    # Matches project_activity_reporting()'s own fallback exactly: a rolling
    # `days`-day window ending now, even when date_from/date_to are None
    # (period_key == "none") -- NOT "no time filter at all". An earlier
    # version of this migration left timeframe={} here, which run_metrics_
    # query treats as "unbounded", silently pulling all-time data instead
    # of the intended rolling window; caught by comparing against
    # production output before deploy (SIM-HISTORICAL-2Y: 3,263 requests/
    # 30 days vs. 11,945 with the bug pulling all 2 years).
    period_end = date_to or datetime.utcnow()
    period_start = date_from or (period_end - timedelta(days=days))
    timeframe = {"start": period_start.isoformat(), "end": period_end.isoformat()}

    summary_result = run_metrics_query(
        db, workspace_id, metrics=_USAGE_REPORT_SUMMARY_METRICS, filters=filters, timeframe=timeframe, limit=1,
    )
    summary_row = summary_result.rows[0] if summary_result.rows else {}
    request_count = int(summary_row.get("ai_requests") or 0)
    simulation_count = int(summary_row.get("simulation_count") or 0)
    summary = {
        "request_count": request_count,
        "input_tokens": int(summary_row.get("input_tokens") or 0),
        "output_tokens": int(summary_row.get("output_tokens") or 0),
        "total_tokens": int(summary_row.get("total_tokens") or 0),
        "tokens_saved": int(summary_row.get("tokens_saved_count") or 0),
        "spend_usd": round(float(summary_row.get("ai_spend") or 0.0), 6),
        "people_count": int(summary_row.get("people_touched") or 0),
        "agent_count": int(summary_row.get("active_agents") or 0),
        "project_count": int(summary_row.get("work_items_touched") or 0),
        "simulation_count": simulation_count,
        "live_count": request_count - simulation_count,
    }

    breakdowns: dict = {}
    result = {
        "period": {
            "date_from": period_start.isoformat(), "date_to": period_end.isoformat(),
            "days": max(1, (period_end - period_start).days),
        },
        "summary": summary,
        "data_scope": scope_from_summary(summary),
    }
    for result_key, dim_key in _USAGE_REPORT_DIMENSIONS:
        # "provider" is the AI vendor (Anthropic/OpenAI/...) derived from
        # the recorded model name -- distinct from "platform"/top_platforms
        # (the source system, e.g. Salesforce, a request came from).
        # "account" = the business/customer entity, distinct from
        # top_people (individual human users) -- keep separate.
        # run_metrics_query caps at 100 rows regardless of what's passed --
        # ask for the max when matching a named entity so it's found
        # anywhere in that cap, not just the requested top N.
        dim_result = run_metrics_query(
            db, workspace_id, metrics=_USAGE_REPORT_METRICS, dimensions=[dim_key],
            filters=filters, timeframe=timeframe, sort="ai_spend", limit=100 if entity_name else row_limit,
        )
        rows = [_shape_usage_row(r, dim_key) for r in dim_result.rows]
        breakdowns[dim_key] = rows
        result[result_key] = rows[:row_limit]

    # A named person/account/department/etc. is often outside the top 5 by
    # spend — without this, the model had no way to answer "how much did X
    # use" except by guessing from the truncated top lists or falling back
    # to the overall total. Match against the FULL (untruncated) breakdowns
    # so any named entity is found regardless of rank.
    #
    # No "project"/context breakdown is included here -- the metrics
    # registry has no dimension for WorkItem-level context entities today
    # (only project_activity_reporting()'s richer report has one), so a
    # named lookup that would have matched a project/WorkItem by name
    # specifically won't resolve through this tool. Documented gap, not a
    # silent one: get_usage_report never exposed project_breakdown to the
    # model as a top_* field anyway, so this only affects entity_name
    # matching against that one entity type.
    if entity_name and entity_name.strip():
        pseudo_report = {
            "people_breakdown": breakdowns.get("person"),
            "agent_breakdown": breakdowns.get("agent"),
            "account_breakdown": breakdowns.get("account"),
            "organizational_unit_breakdown": breakdowns.get("department"),
            "source_platform_breakdown": breakdowns.get("platform"),
            "model_breakdown": breakdowns.get("model"),
        }
        match = _ask_named_entity(entity_name.strip(), pseudo_report)
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
    department: Optional[str] = None, provider: Optional[str] = None,
) -> dict:
    from api.routes_work_items import project_activity_reporting

    reporting_filters = _with_department_override(reporting_filters, department, provider)
    primary_period = resolve_primary_period(
        period_key=None if period_key in (None, "none") else period_key,
        days=days,
    )
    plan = comparison_plan(primary_period, comparison_key or "previous_period")
    current_report = project_activity_reporting(
        workspace_id=workspace_id, date_from=plan.primary.start, date_to=plan.primary.end,
        days=days, **reporting_filters, activity_limit=500, exclude_prune_only_rows=True, db=db,
    )
    prior_report = project_activity_reporting(
        workspace_id=workspace_id, date_from=plan.comparison.start, date_to=plan.comparison.end,
        days=days, **reporting_filters, activity_limit=500, exclude_prune_only_rows=True, db=db,
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


def run_get_budget_status(
    db, workspace_id: Optional[str], alerts_only: bool, department_scope: Optional[str] = None,
) -> dict:
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
        label = (budget["department"] or "Unassigned").split(":")[-1]
        # Phase 2 slice 3: get_all_budgets() itself stays unscoped -- it's
        # shared with the Admin > Budgets page -- so a department-scoped
        # user's own department is filtered here instead, at this tool's
        # own row-building step.
        if department_scope and label != department_scope:
            continue
        spend = float(budget["current_spend_usd"] or 0)
        pct = float(budget["used_pct"] or 0)
        if alerts_only and pct < 80:
            continue
        rows.append({
            "id": budget["department"],
            "label": label,
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
    days: int = 30, period_key: str = "none", department_scope: Optional[str] = None,
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
    if workspace_id and department_scope:
        # Phase 2 slice 3: exact match on the one department, instead of
        # the wildcard "every department in the workspace" match below.
        agent_query = agent_query.filter(RegisteredAgent.department == f"{workspace_id}:{department_scope}")
    elif workspace_id:
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
        source_platform=None, record_type=None, model_tier=None, charged_unit=department_scope,
        business_purpose=None, activity_limit=2000, exclude_prune_only_rows=True, db=db,
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


def run_get_account_outcomes(
    db, workspace_id: Optional[str], entity_name: Optional[str] = None,
    department_scope: Optional[str] = None,
) -> dict:
    """
    Business outcomes (Opportunity won/lost/open, pipeline value, closed-won
    value, resolved support cases) plus the AI spend/tokens tied to those
    outcomes -- the same WorkItemOutcome aggregation that powers Business
    Profile's per-account panel and the Cockpit's workspace-wide Business
    Impact card (api/routes_dashboard.py's get_business_impact), just
    reachable from the agent loop and optionally narrowed to one named
    account. No other tool in this file touches WorkItemOutcome at all --
    without this, the agent had no way to answer any won/lost/pipeline
    question, named account or not.

    Milestone 4: won/lost/support numbers now come from the shared
    core.metrics_query catalog instead of a bespoke query duplicated a
    fourth time here, plus the catalog's generic (any context_type)
    successful/unsuccessful/open outcome metrics, so recruiting/engineering/
    finance work items -- not just Salesforce Opportunities and support
    cases -- are answerable through this same tool.
    """
    from core.metrics_query import run_metrics_query
    from core.workspace_scope import workspace_filter
    from database.models import WorkAccount, WorkItem

    account = None
    matched_accounts = []
    name = (entity_name or "").strip()
    if name:
        account_query = db.query(WorkAccount).filter(WorkAccount.name.ilike(f"%{name}%"))
        acct_scope = workspace_filter(WorkAccount, workspace_id)
        if acct_scope is not None:
            account_query = account_query.filter(acct_scope)
        matched_accounts = account_query.limit(6).all()
        if not matched_accounts:
            return {
                "entity_name": name,
                "found": False,
                "message": f"No account matching '{name}' was found.",
            }
        # A duplicate WorkAccount row is not a second real account -- e.g.
        # a workspace with the same customer connected via two Salesforce
        # orgs ends up with two "Dickenson plc" rows, one already marked
        # merged_into_work_account_id pointing at the other (the account
        # profile UI already treats these as one entity). Without folding
        # merged rows into their canonical target first, this looked
        # ambiguous even when every match ultimately resolves to the same
        # account -- reproduced live: "Dickenson plc" existed as two
        # WorkAccount rows in the same workspace, one merged into the
        # other, and this tool asked "which one?" for a name that only
        # ever meant one real account.
        canonical_ids: set[int] = set()
        canonical_by_id: dict[int, WorkAccount] = {}
        for candidate in matched_accounts:
            canonical = candidate
            seen_ids = set()
            while canonical.merged_into_work_account_id and canonical.id not in seen_ids:
                seen_ids.add(canonical.id)
                target = db.query(WorkAccount).filter(
                    WorkAccount.id == canonical.merged_into_work_account_id
                ).first()
                if target is None:
                    break
                canonical = target
            canonical_ids.add(canonical.id)
            canonical_by_id[canonical.id] = canonical
        if len(canonical_ids) > 1:
            return {
                "entity_name": name,
                "found": False,
                "ambiguous": True,
                "candidates": [a.name for a in canonical_by_id.values()],
                "message": (
                    f"More than one account matches '{name}': "
                    f"{', '.join(a.name for a in canonical_by_id.values())}. Ask which one."
                ),
            }
        account = next(iter(canonical_by_id.values()))

    outcome_metrics = [
        "won_count", "lost_count", "open_count", "won_value", "pipeline_value",
        "support_cases_total", "support_cases_resolved",
        "successful_outcomes", "unsuccessful_outcomes", "open_outcomes",
        "successful_outcome_value", "outcomes_with_data",
    ]
    outcome_filters = {}
    if account is not None:
        outcome_filters["account"] = account.name
    if department_scope:
        outcome_filters["charged_unit"] = department_scope
    outcome_result = run_metrics_query(
        db, workspace_id, metrics=outcome_metrics,
        filters=outcome_filters or None,
    )
    o = outcome_result.rows[0] if outcome_result.rows else {m: 0 for m in outcome_metrics}

    won_count, lost_count, open_count = int(o["won_count"]), int(o["lost_count"]), int(o["open_count"])
    pipeline_value, closed_won_value = float(o["pipeline_value"]), float(o["won_value"])
    support_total, support_resolved = int(o["support_cases_total"]), int(o["support_cases_resolved"])
    successful_outcomes, unsuccessful_outcomes = int(o["successful_outcomes"]), int(o["unsuccessful_outcomes"])
    open_outcomes = int(o["open_outcomes"])
    successful_outcome_value = float(o["successful_outcome_value"])
    outcomes_with_data = int(o["outcomes_with_data"])
    has_outcome_data = bool(outcomes_with_data)

    # AI spend/tokens split by won vs lost, tied only to work items that
    # actually have a synced outcome -- answers "compare AI activity on won
    # vs lost opportunities" directly instead of making the model subtract.
    from sqlalchemy import and_, case, func, or_
    from database.models import WorkItemOutcome, TokenTransaction

    work_item_scope = workspace_filter(WorkItem, workspace_id)

    def _scoped(query):
        q = query.filter(work_item_scope) if work_item_scope is not None else query
        if account is not None:
            q = q.filter(WorkItem.account_id == account.id)
        if department_scope:
            # Phase 2 slice 4: this raw won/lost spend split has its own
            # separate query from outcome_result above and never touched
            # department at all -- WorkItem is already in scope from the
            # join below, same tolerant match as _run_outcome_query.
            q = q.filter(or_(
                WorkItem.department == department_scope,
                WorkItem.department.like(f"%:{department_scope}"),
            ))
        return q

    is_won = and_(WorkItem.context_type == "opportunity", WorkItemOutcome.outcome_success.is_(True))
    is_lost = and_(
        WorkItem.context_type == "opportunity",
        WorkItemOutcome.outcome_success.is_(False), WorkItemOutcome.is_closed.is_(True),
    )
    won_spend, won_tokens, lost_spend, lost_tokens = _scoped(
        db.query(
            func.coalesce(func.sum(case((is_won, TokenTransaction.cost_usd), else_=0.0)), 0.0),
            func.coalesce(func.sum(case((is_won, TokenTransaction.input_tokens + TokenTransaction.output_tokens), else_=0)), 0),
            func.coalesce(func.sum(case((is_lost, TokenTransaction.cost_usd), else_=0.0)), 0.0),
            func.coalesce(func.sum(case((is_lost, TokenTransaction.input_tokens + TokenTransaction.output_tokens), else_=0)), 0),
        )
        .select_from(TokenTransaction)
        .join(WorkItem, TokenTransaction.work_item_id == WorkItem.id)
        .join(WorkItemOutcome, WorkItemOutcome.work_item_id == WorkItem.id)
    ).first()

    return {
        "entity_name": account.name if account else None,
        "found": True,
        "scope": "account" if account else "workspace",
        "has_outcome_data": has_outcome_data,
        "opportunities_won": won_count,
        "opportunities_lost": lost_count,
        "opportunities_open": open_count,
        "pipeline_value_usd": round(float(pipeline_value or 0.0), 2),
        "closed_won_value_usd": round(float(closed_won_value or 0.0), 2),
        "support_cases_total": int(support_total or 0),
        "support_cases_resolved": support_resolved,
        "ai_spend_on_won_opportunities_usd": round(float(won_spend or 0.0), 6),
        "ai_tokens_on_won_opportunities": int(won_tokens or 0),
        "ai_spend_on_lost_opportunities_usd": round(float(lost_spend or 0.0), 6),
        "ai_tokens_on_lost_opportunities": int(lost_tokens or 0),
        # Generic fields -- work for any context_type (recruiting, engineering,
        # finance, custom), not just Salesforce Opportunities/support cases.
        "successful_outcomes": successful_outcomes,
        "unsuccessful_outcomes": unsuccessful_outcomes,
        "open_outcomes": open_outcomes,
        "successful_outcome_value_usd": round(successful_outcome_value, 2),
        "outcomes_with_known_data": outcomes_with_data,
    }


def run_get_cost_per_outcome(
    db, workspace_id: Optional[str],
    context_type: Optional[str] = None, entity_name: Optional[str] = None,
    department_scope: Optional[str] = None,
) -> dict:
    from core.metrics_query import compute_cost_per_outcome

    ct = (context_type or "").strip() or None
    name = (entity_name or "").strip() or None
    return compute_cost_per_outcome(db, workspace_id, context_type=ct, account_name=name, department_scope=department_scope)


def run_get_data_coverage(db, workspace_id: Optional[str]) -> dict:
    from core.data_coverage import get_data_coverage

    result = get_data_coverage(db, workspace_id)
    return {
        "connected_platforms": result.connected_platforms,
        "not_connected_platforms": result.not_connected_platforms,
        "platforms": result.platforms,
    }


def run_query_metrics(
    db, workspace_id: Optional[str], metrics: list, dimensions: Optional[list] = None,
    filters: Optional[dict] = None, days: int = 30, period_key: str = "none",
    compare_to: Optional[str] = None, sort: Optional[str] = None, limit: int = 20,
) -> dict:
    from core.metrics_query import run_metrics_query

    # Clean "" sentinels (the strict tool schema requires every filter key
    # present, so the model sends empty string for "not filtering on this")
    # into the None the reporting layer actually expects.
    clean_filters = {k: v for k, v in (filters or {}).items() if v}

    date_from, date_to = _period_bounds(days, "none" if period_key in (None, "none") else period_key)
    if date_from is None:
        period = resolve_primary_period(period_key="none", days=int(days or 30))
        date_from, date_to = period.start, period.end
    timeframe = {"start": date_from, "end": date_to}

    result = run_metrics_query(
        db, workspace_id,
        metrics=metrics or [],
        dimensions=dimensions or [],
        filters=clean_filters,
        timeframe=timeframe,
        compare_to=None if compare_to in (None, "none") else compare_to,
        sort=sort or None,
        limit=int(limit or 20),
    )
    out = {
        "rows": result.rows,
        "metrics": result.metrics,
        "dimensions": result.dimensions,
        "metric_definitions": result.metric_definitions,
        "scope": result.scope,
        "filters_applied": result.filters_applied,
        "timeframe": {
            "start": timeframe["start"].isoformat() if timeframe["start"] else None,
            "end": timeframe["end"].isoformat() if timeframe["end"] else None,
        },
        "errors": result.errors,
        "unsupported_metrics": result.unsupported_metrics,
    }
    if result.comparison is not None:
        out["comparison"] = result.comparison
    if result.freshness is not None:
        out["freshness"] = result.freshness
    return out


def run_get_priority_signals(
    db, workspace_id: Optional[str], days: int = 7, department_scope: Optional[str] = None,
) -> dict:
    """
    Combines the same two real sources the Cockpit's own Recommendations
    panel already combines client-side (core.budget.get_all_budgets for
    budget risk, cockpit/src/components/Recommendations.tsx) with the
    now-ranked query_metrics comparison (core/metrics_query.py) for
    biggest movers -- surfaced here as one structured, pre-ranked tool so
    Ask CostPilot can answer "what deserves attention" without inventing
    its own priority list.
    """
    from core.budget import get_all_budgets
    from core.metrics_query import run_metrics_query

    signals = []

    for budget in get_all_budgets(db, workspace_id):
        pct = float(budget.get("used_pct") or 0)
        if pct < 80:
            continue
        dept = str(budget.get("department") or "Unassigned").split(":")[-1]
        # Phase 2 slice 3: same row-level filter as run_get_budget_status.
        if department_scope and dept != department_scope:
            continue
        cap = float(budget.get("monthly_cap_usd") or 0)
        severity = "critical" if (budget.get("throttled") or pct >= 100) else "warning"
        signals.append({
            "type": "budget_risk",
            "severity": severity,
            "label": dept,
            "detail": f"{dept} is at {pct}% of its ${cap:,.0f}/mo AI budget"
                      + (" and is being throttled" if budget.get("throttled") else ""),
        })

    window_days = max(1, int(days or 7))
    now = datetime.utcnow()
    result = run_metrics_query(
        db, workspace_id, metrics=["ai_spend"], dimensions=["department"],
        filters={"charged_unit": department_scope} if department_scope else None,
        timeframe={"start": now - timedelta(days=window_days), "end": now},
        compare_to="previous_period", limit=5,
    )
    if result.comparison:
        for row in result.comparison["rows"]:
            spend = row.get("ai_spend") or {}
            pct_diff = spend.get("pct_difference")
            diff = spend.get("difference") or 0
            if pct_diff is None or abs(pct_diff) < 15:
                continue
            dept = str((row.get("dimensions") or {}).get("department") or "Unassigned").split(":")[-1]
            direction = "increased" if diff >= 0 else "decreased"
            signals.append({
                "type": "spend_change",
                "severity": "warning" if abs(pct_diff) >= 50 else "info",
                "label": dept,
                "detail": f"{dept} AI spend {direction} {abs(pct_diff):.1f}% (${abs(diff):,.2f}) vs the prior {days} days",
            })

    severity_rank = {"critical": 0, "warning": 1, "info": 2}
    signals.sort(key=lambda s: severity_rank.get(s["severity"], 3))
    return {"signals": signals[:5], "period_days": days}


def run_get_decision_history(
    db, workspace_id: Optional[str],
    agent_name: Optional[str] = None, model_name: Optional[str] = None,
    keyword: Optional[str] = None, event_type: Optional[str] = None,
    limit: int = 10, department_scope: Optional[str] = None,
) -> dict:
    """
    Phase 2 slice 6 (decision memory): searches AuditEvent.rationale --
    already real, auto-generated plain-English text (core/auditor.py's
    _build_rationale) written on every routing/budget/lock decision --
    across agent/model/keyword/event_type, instead of requiring an
    already-known audit_event_id the way _ask_decision_response does.
    """
    from sqlalchemy import or_
    from core.workspace_scope import workspace_filter
    from database.models import AuditEvent, RegisteredAgent

    query = db.query(AuditEvent)
    scope = workspace_filter(AuditEvent, workspace_id)
    if scope is not None:
        query = query.filter(scope)
    if department_scope:
        query = query.filter(or_(
            AuditEvent.department == department_scope,
            AuditEvent.department.like(f"%:{department_scope}"),
        ))

    name = (agent_name or "").strip()
    if name:
        agent_ids = [
            row[0] for row in db.query(RegisteredAgent.id)
            .filter(RegisteredAgent.name.ilike(f"%{name}%")).all()
        ]
        query = query.filter(AuditEvent.agent_id.in_(agent_ids or [-1]))

    model = (model_name or "").strip()
    if model:
        query = query.filter(AuditEvent.selected_model_name.ilike(f"%{model}%"))

    word = (keyword or "").strip()
    if word:
        query = query.filter(AuditEvent.rationale.ilike(f"%{word}%"))

    etype = (event_type or "").strip()
    if etype:
        query = query.filter(AuditEvent.event_type.ilike(etype))

    rows = (
        query.order_by(AuditEvent.timestamp.desc())
        .limit(max(1, min(int(limit or 10), 50)))
        .all()
    )

    return {
        "decisions": [
            {
                "timestamp": row.timestamp.isoformat() if row.timestamp else None,
                "department": str(row.department or "Unassigned").split(":")[-1],
                "selected_model_name": row.selected_model_name,
                "selected_model_tier": row.selected_model_tier,
                "event_type": row.event_type,
                "decision_outcome": row.decision_outcome,
                "risk_level": row.risk_level,
                "rationale": row.rationale,
                "governed_request_id": row.governed_request_id,
            }
            for row in rows
        ],
        "count": len(rows),
    }


def run_propose_budget_cap_change(
    db, workspace_id: Optional[str], department: str, new_cap_usd: float, reason: str,
    department_scope: Optional[str] = None, user_id: Optional[int] = None,
) -> dict:
    """
    Action Proposals slice 1: creates a real ActionProposal row via
    core/action_proposals.py -- this tool never mutates the budget itself,
    it only proposes. A human confirms via POST /api/ask/actions/{id}/confirm
    (routes_action_proposals.py), which independently re-checks permission
    and is the only thing that actually calls core.budget.set_cap().

    department_scope enforcement matches every other Phase 2 tool: a
    scoped user can only propose a change to their own department, even if
    they name a different one.
    """
    from core.budget import get_all_budgets
    from core.action_proposals import create_proposal, serialize_proposal

    requested = (department or "").strip()
    if department_scope and requested and requested != department_scope:
        requested = department_scope
    elif department_scope and not requested:
        requested = department_scope

    budgets = get_all_budgets(db, workspace_id)
    match = next(
        (b for b in budgets if str(b.get("department") or "").split(":")[-1].lower() == requested.lower()),
        None,
    )
    if match is None:
        return {
            "found": False,
            "message": f"No budget found for department '{requested}'.",
        }

    current_cap = float(match["monthly_cap_usd"] or 0)
    delta = round(float(new_cap_usd) - current_cap, 2)
    simulation_result = _simulate_cap_change(match, float(new_cap_usd))
    proposal = create_proposal(
        db,
        workspace_id=workspace_id, department=match["department"],
        action_type="BUDGET_CAP_SET", target_type="budget_department", target_id=match["department"],
        current_value={"monthly_cap_usd": current_cap},
        proposed_value={"new_cap_usd": float(new_cap_usd)},
        reason=reason or "",
        estimated_impact={
            "label": "Estimated",
            "monthly_cap_delta_usd": delta,
            "note": "This is a cap change, not a guaranteed change in actual spend.",
        },
        simulation_result=simulation_result,
        risk_level="medium" if abs(delta) >= current_cap * 0.25 else "low",
        required_permission="manage_budgets",
        user_id=user_id,
    )
    return {"found": True, "proposal": serialize_proposal(proposal), "simulation": simulation_result}


def _simulate_cap_change(budget_row: dict, new_cap_usd: float) -> dict:
    """
    Shared by run_simulate_budget_cap_change (read-only what-if) and
    run_propose_budget_cap_change (a real proposal's genuine projected
    impact, replacing the old cap-delta-only "estimated_impact"). budget_row
    is one row from core.budget.get_all_budgets() -- current_spend_usd there
    is already reconciled against the real ledger (sync_current_spend_from_ledger),
    unlike the raw DepartmentBudget column, so it's correct even for
    backfilled/simulated workspaces where the raw column reads $0 forever.
    """
    from core.budget import project_department_spend

    period_start = datetime.fromisoformat(budget_row["period_start"]) if budget_row.get("period_start") else datetime.utcnow()
    return project_department_spend(
        current_spend_usd=float(budget_row.get("current_spend_usd") or 0),
        period_start=period_start,
        new_cap_usd=new_cap_usd,
    )


def run_simulate_budget_cap_change(
    db, workspace_id: Optional[str], department: str, new_cap_usd: float,
    department_scope: Optional[str] = None,
) -> dict:
    """
    Read-only "what if" projection -- never creates an ActionProposal, never
    requires confirmation. Shares its projection math with
    run_propose_budget_cap_change via _simulate_cap_change so a real
    proposal's numbers and a hypothetical's numbers can never disagree for
    the same department/cap pair.
    """
    from core.budget import get_all_budgets

    requested = (department or "").strip()
    if department_scope and requested != department_scope:
        requested = department_scope

    budgets = get_all_budgets(db, workspace_id)
    match = next(
        (b for b in budgets if str(b.get("department") or "").split(":")[-1].lower() == requested.lower()),
        None,
    )
    if match is None:
        return {
            "found": False,
            "message": f"No budget found for department '{requested}'.",
        }

    simulation = _simulate_cap_change(match, float(new_cap_usd))
    return {
        "found": True,
        "department": str(match["department"]).split(":")[-1],
        "current_cap_usd": float(match["monthly_cap_usd"] or 0),
        **simulation,
    }


EXECUTORS = {
    "get_usage_report": run_get_usage_report,
    "get_change_drivers": run_get_change_drivers,
    "get_budget_status": run_get_budget_status,
    "get_product_help": run_get_product_help,
    "get_data_coverage": run_get_data_coverage,
    "get_agent_adoption": run_get_agent_adoption,
    "get_account_outcomes": run_get_account_outcomes,
    "get_cost_per_outcome": run_get_cost_per_outcome,
    "query_metrics": run_query_metrics,
    "get_priority_signals": run_get_priority_signals,
    "get_decision_history": run_get_decision_history,
    "propose_budget_cap_change": run_propose_budget_cap_change,
    "simulate_budget_cap_change": run_simulate_budget_cap_change,
}
