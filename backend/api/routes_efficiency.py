"""
api/routes_efficiency.py — Bot Efficiency Review  [Step 11]

POST /api/reports/bot-efficiency
  Analyzes every registered agent's transaction history and uses GPT-4o
  (or the configured flagship model) to generate plain-English efficiency
  recommendations with projected savings.

Works with both live and simulated model modes:
  - live:      calls GPT-4o / Claude with real agent data
  - simulated: generates realistic rule-based recommendations without an API call
"""

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func

from database.db import get_db
from database.models import TokenTransaction, RegisteredAgent, DepartmentBudget

router = APIRouter()

FLAGSHIP_IN  = 5.00  / 1_000_000
FLAGSHIP_OUT = 15.00 / 1_000_000
MICRO_IN     = 0.50  / 1_000_000
MICRO_OUT    = 1.50  / 1_000_000


class AskCostPilotRequest(BaseModel):
    """Read-only natural-language question over CostPilot's attributed usage."""

    question: str
    days: int = 30
    workspace_id: Optional[str] = None
    date_from: Optional[datetime] = None
    date_to: Optional[datetime] = None
    project_id: Optional[str] = None
    user_external_id: Optional[str] = None
    agent_id: Optional[int] = None
    account_id: Optional[str] = None
    source_platform: Optional[str] = None
    record_type: Optional[str] = None
    charged_unit: Optional[str] = None
    business_purpose: Optional[str] = None


def _ask_intent(question: str, default_days: int) -> dict:
    """Translate common executive questions into a bounded reporting intent."""
    text = " ".join((question or "").lower().split())
    days = max(1, min(int(default_days or 30), 365))
    if "yesterday" in text:
        days = 1
    elif "last week" in text or "past week" in text:
        days = 7
    elif "last quarter" in text or "past quarter" in text:
        days = 90
    elif "last year" in text or "past year" in text:
        days = 365
    elif "last month" in text or "past month" in text:
        days = 30

    metric = "spend_usd"
    if "token" in text:
        metric = "total_tokens"
    elif any(term in text for term in ("request", "call", "volume", "used most", "usage")):
        metric = "request_count"

    entity = "overview"
    if any(term in text for term in ("employee", "person", "people", "user", "who ")):
        entity = "person"
    elif any(term in text for term in ("agent", "bot")):
        entity = "agent"
    elif any(term in text for term in ("department", "team", "business unit", "cost center")):
        entity = "department"
    elif any(term in text for term in (
        "account", "customer", "project", "matter", "opportunity", "business context", "work item"
    )):
        entity = "context"

    intent = "ranking" if entity != "overview" else "overview"
    if any(term in text for term in (
        "save money", "saving", "reduce cost", "cut cost", "optimize", "recommend", "advice"
    )):
        intent = "savings"
    elif any(term in text for term in (
        "near budget", "close to budget", "budget limit", "over budget", "budget warning"
    )):
        intent = "budget"
    elif any(term in text for term in ("overview", "summary", "where is", "breakdown")):
        intent = "overview"

    return {"intent": intent, "entity": entity, "metric": metric, "days": days}


def _ask_rank(rows: list, metric: str) -> list:
    return sorted(
        rows or [],
        key=lambda row: (
            -float(row.get(metric) or 0),
            -float(row.get("request_count") or 0),
            str(row.get("label") or ""),
        ),
    )


def _ask_metric_value(metric: str, value) -> tuple[str, str]:
    number = float(value or 0)
    if metric == "spend_usd":
        return f"${number:,.4f}", "AI spend"
    if metric == "request_count":
        return f"{int(number):,}", "governed requests"
    return f"{int(number):,}", "tokens"


def _ask_evidence(rows: list, metric: str, filter_name: str) -> list:
    evidence = []
    for row in _ask_rank(rows, metric)[:5]:
        value, metric_label = _ask_metric_value(metric, row.get(metric))
        evidence.append({
            "label": row.get("label") or "Unknown",
            "value": value,
            "metric_label": metric_label,
            "detail": (
                f"{int(row.get('request_count') or 0):,} requests · "
                f"{int(row.get('total_tokens') or 0):,} tokens · "
                f"${float(row.get('spend_usd') or 0):,.4f}"
            ),
            "filter_name": filter_name,
            "filter_value": row.get("id"),
        })
    return evidence


def _agent_stats(db: Session, agent: RegisteredAgent, days: int) -> dict:
    """Compute performance metrics for one agent over the given window."""
    since = datetime.utcnow() - timedelta(days=days)

    txs = db.query(TokenTransaction).filter(
        TokenTransaction.agent_id  == agent.id,
        TokenTransaction.timestamp >= since,
    ).all()

    if not txs:
        return None

    total_calls     = len(txs)
    total_cost      = sum(t.cost_usd for t in txs)
    avg_cost        = total_cost / total_calls
    ECONOMY = {"Scout", "Analyst", "micro"}
    flagship_calls  = sum(1 for t in txs if t.model_tier not in ECONOMY)
    micro_calls     = total_calls - flagship_calls
    flagship_pct    = round(flagship_calls / total_calls * 100, 1)
    micro_pct       = round(micro_calls    / total_calls * 100, 1)
    pruned_calls    = sum(1 for t in txs if t.was_pruned)
    prune_rate      = round(pruned_calls / total_calls * 100, 1)
    tokens_saved    = sum(t.tokens_saved for t in txs)
    avg_tokens_saved= round(tokens_saved / total_calls)

    # Call frequency — calls per day
    calls_per_day   = round(total_calls / days, 1)

    # Peak hour analysis (0-23)
    from collections import Counter
    hour_counts = Counter(t.timestamp.hour for t in txs)
    top_hours   = sorted(hour_counts, key=hour_counts.get, reverse=True)[:3]

    # Cost trend: first half vs second half of the period
    mid = since + timedelta(days=days // 2)
    first_half  = [t for t in txs if t.timestamp < mid]
    second_half = [t for t in txs if t.timestamp >= mid]
    first_cost  = sum(t.cost_usd for t in first_half)  / max(len(first_half), 1)
    second_cost = sum(t.cost_usd for t in second_half) / max(len(second_half), 1)
    cost_trend  = "increasing" if second_cost > first_cost * 1.1 else \
                  "decreasing" if second_cost < first_cost * 0.9 else "stable"

    # Routing reason distribution
    from collections import Counter as C2
    reasons = C2(t.routing_reason for t in txs)
    top_reason = reasons.most_common(1)[0][0] if reasons else "ROUTINE"

    # What would full flagship have cost (no routing optimization)
    avg_input  = sum(t.input_tokens  for t in txs) / total_calls
    avg_output = sum(t.output_tokens for t in txs) / total_calls
    flagship_only_cost = (avg_input * FLAGSHIP_IN + avg_output * FLAGSHIP_OUT) * total_calls
    routing_savings    = round(flagship_only_cost - total_cost, 2)

    return {
        "agent_id":        agent.id,
        "agent_name":      agent.name,
        "department":      agent.department,
        "target_table":    agent.target_table,
        "collision_policy":agent.collision_policy,
        "total_calls":     total_calls,
        "calls_per_day":   calls_per_day,
        "total_cost_usd":  round(total_cost, 4),
        "avg_cost_usd":    round(avg_cost, 6),
        "flagship_pct":    flagship_pct,
        "micro_pct":       micro_pct,
        "prune_rate":      prune_rate,
        "avg_tokens_saved":avg_tokens_saved,
        "tokens_saved":    tokens_saved,
        "cost_trend":      cost_trend,
        "top_hours":       top_hours,
        "top_reason":      top_reason,
        "routing_savings": routing_savings,
        "days":            days,
    }


def _generate_simulated_review(stats: dict) -> dict:
    """
    Rule-based efficiency recommendations — used when CostPilot is in simulated mode.
    Produces realistic, specific recommendations without an API call.
    """
    name       = stats["agent_name"]
    dept       = stats["department"]
    cpd        = stats["calls_per_day"]
    fp         = stats["flagship_pct"]
    pr         = stats["prune_rate"]
    cost       = stats["total_cost_usd"]
    avg        = stats["avg_cost_usd"]
    trend      = stats["cost_trend"]
    savings    = stats["routing_savings"]
    days       = stats["days"]

    findings   = []
    recs       = []
    proj_save  = 0.0
    grade      = "A"

    # ── Frequency analysis ────────────────────────────────────────────────────
    if cpd > 200:
        findings.append(f"Running {cpd:.0f} calls/day — extremely high frequency for a {dept} agent.")
        batch_save = round(cost * 0.60, 2)
        recs.append(f"Introduce event-driven triggering instead of polling. Batching similar requests could reduce call volume by 60%, saving ~${batch_save:,.2f} over {days} days with minimal throughput impact.")
        proj_save += batch_save
        grade = "C"
    elif cpd > 50:
        findings.append(f"Running {cpd:.0f} calls/day — moderate-to-high frequency.")
        batch_save = round(cost * 0.30, 2)
        recs.append(f"Consider batching requests into 15-minute windows during peak hours. Estimated savings: ~${batch_save:,.2f} over {days} days.")
        proj_save += batch_save
        grade = "B" if grade == "A" else grade

    # ── Flagship ratio analysis ────────────────────────────────────────────────
    if fp > 60:
        findings.append(f"{fp}% of calls escalated to flagship model — unusually high for this agent type.")
        fp_save = round(cost * 0.45, 2)
        recs.append(f"Review your sensitive term library and routing thresholds. Many COMPLEX decisions may be over-triggered. Tuning escalation criteria could shift 30-40% of calls back to micro, saving ~${fp_save:,.2f} over {days} days.")
        proj_save += fp_save
        grade = "C"
    elif fp < 10 and dept in ("Support", "Operations"):
        findings.append(f"Only {fp}% flagship usage for a {dept} agent — possibly under-escalating high-risk content.")
        recs.append("Review your sensitive term library. Support and Operations agents typically see 15-30% flagship usage. Under-escalation may expose compliance risk.")
        grade = "B" if grade == "A" else grade

    # ── Pruning analysis ──────────────────────────────────────────────────────
    if pr < 50:
        findings.append(f"Only {pr}% of payloads are being pruned — significant noise likely in upstream inputs.")
        prune_save = round(cost * 0.25, 2)
        recs.append(f"Improve upstream data quality before payloads reach CostPilot. Structured Salesforce fields instead of raw case descriptions could push pruning rate to 70%+, saving ~${prune_save:,.2f} over {days} days.")
        proj_save += prune_save
        grade = "B" if grade == "A" else grade
    elif pr > 80:
        findings.append(f"Excellent pruning rate of {pr}% — context sweeper working efficiently.")

    # ── Cost trend analysis ───────────────────────────────────────────────────
    if trend == "increasing":
        findings.append("Cost per call is trending upward — payload complexity growing over time.")
        recs.append("Monitor for prompt injection or expanding input sizes from upstream systems. Consider adding a token budget cap per call.")
        grade = "B" if grade == "A" else grade
    elif trend == "stable":
        findings.append("Cost per call is stable — consistent, predictable workload.")

    # ── Routing savings highlight ─────────────────────────────────────────────
    if savings > 0:
        findings.append(f"Smart routing saved ${savings:,.2f} vs. running all calls at flagship rates.")

    # ── Grade assignment ──────────────────────────────────────────────────────
    if not recs:
        grade = "A"
        summary = f"{name} is operating efficiently. Call volume, model routing, and pruning rates are all within optimal parameters. No changes recommended at this time."
    else:
        if grade == "A": grade = "B"
        summary = f"{name} processed {stats['total_calls']:,} calls over {days} days at ${cost:,.4f} total cost (${avg:.6f}/call). " + " ".join(findings[:2])

    return {
        "agent_name":        name,
        "department":        dept,
        "grade":             grade,
        "summary":           summary,
        "findings":          findings,
        "recommendations":   recs,
        "projected_savings": round(proj_save, 2),
        "stats":             stats,
        "generated_by":      "simulated",
    }


def _generate_live_review(stats: dict) -> dict:
    """
    Uses the configured flagship model to write a plain-English efficiency review
    for one agent based on its actual performance data.
    """
    from core.model_client import MODEL_MODE, PROVIDER, OPENAI_KEY, ANTHROPIC_KEY, \
                                   OPENAI_FLAGSHIP, ANTHROPIC_FLAGSHIP

    prompt = f"""You are a FinOps AI efficiency analyst reviewing bot performance data for an enterprise AI governance platform.

Analyze this agent's performance and provide a concise efficiency review.

Agent: {stats['agent_name']}
Department: {stats['department']}
Target system: {stats['target_table']}
Period: Last {stats['days']} days

Performance data:
- Total calls: {stats['total_calls']:,}
- Calls per day: {stats['calls_per_day']}
- Total cost: ${stats['total_cost_usd']:,.4f}
- Avg cost per call: ${stats['avg_cost_usd']:.6f}
- Flagship model usage: {stats['flagship_pct']}%
- Micro model usage: {stats['micro_pct']}%
- Payload pruning rate: {stats['prune_rate']}% of calls pruned
- Avg tokens saved per call (pruning): {stats['avg_tokens_saved']:,}
- Cost trend: {stats['cost_trend']}
- Most common routing reason: {stats['top_reason']}
- Savings vs all-flagship baseline: ${stats['routing_savings']:,.2f}

Respond in this exact JSON format:
{{
  "grade": "A|B|C|D",
  "summary": "2-3 sentence plain English summary of this agent's efficiency",
  "findings": ["finding 1", "finding 2", "finding 3"],
  "recommendations": ["specific actionable recommendation with projected impact", "..."],
  "projected_savings": <number — estimated USD savings over next 30 days if recommendations followed>
}}

Grading: A=highly efficient, B=good with minor improvements, C=significant optimization opportunity, D=urgent review needed.
Be specific. Reference actual numbers. Keep recommendations actionable."""

    try:
        if PROVIDER == "anthropic" and ANTHROPIC_KEY:
            import anthropic
            client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
            resp = client.messages.create(
                model=ANTHROPIC_FLAGSHIP,
                max_tokens=600,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = resp.content[0].text
        else:
            from openai import OpenAI
            client = OpenAI(api_key=OPENAI_KEY)
            resp = client.chat.completions.create(
                model=OPENAI_FLAGSHIP,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=600,
                temperature=0.4,
                response_format={"type": "json_object"},
            )
            raw = resp.choices[0].message.content

        import json
        parsed = json.loads(raw)
        return {
            "agent_name":        stats["agent_name"],
            "department":        stats["department"],
            "grade":             parsed.get("grade", "B"),
            "summary":           parsed.get("summary", ""),
            "findings":          parsed.get("findings", []),
            "recommendations":   parsed.get("recommendations", []),
            "projected_savings": float(parsed.get("projected_savings", 0)),
            "stats":             stats,
            "generated_by":      "ai",
        }
    except Exception as e:
        # Fall back to simulated if live call fails
        result = _generate_simulated_review(stats)
        result["generated_by"] = f"simulated (fallback: {str(e)[:60]})"
        return result


@router.post("/ask")
def ask_costpilot(
    request: AskCostPilotRequest,
    db: Session = Depends(get_db),
):
    """
    Answer executive questions using CostPilot-calculated facts.

    The natural-language layer selects a bounded reporting intent; totals,
    rankings, and evidence always come from the deterministic attribution
    report. This endpoint is read-only and cannot change routing or policy.
    """
    from api.routes_work_items import project_activity_reporting

    question = (request.question or "").strip()
    if not question:
        return {
            "title": "Ask a question about your AI usage",
            "answer": "Enter a question about spend, tokens, people, agents, departments, or business work.",
            "intent": "help",
            "evidence": [],
            "recommendations": [],
        }

    parsed = _ask_intent(question, request.days)
    has_question_period = any(
        phrase in question.lower()
        for phrase in (
            "today", "yesterday", "this week", "last week", "past week",
            "this month", "last month",
        )
    )
    report = project_activity_reporting(
        workspace_id=request.workspace_id,
        date_from=None if has_question_period else request.date_from,
        date_to=None if has_question_period else request.date_to,
        days=parsed["days"],
        project_id=request.project_id,
        user_external_id=request.user_external_id,
        agent_id=request.agent_id,
        account_id=request.account_id,
        source_platform=request.source_platform,
        record_type=request.record_type,
        charged_unit=request.charged_unit,
        business_purpose=request.business_purpose,
        activity_limit=2000,
        db=db,
    )
    summary = report.get("summary") or {}
    period = report.get("period") or {}
    context_plural = report.get("context_label_plural") or "Business Contexts"
    metric = parsed["metric"]
    intent = parsed["intent"]
    entity = parsed["entity"]
    evidence = []
    recommendations = []
    title = "AI usage overview"

    entity_config = {
        "person": ("people_breakdown", "user_external_id", "People"),
        "agent": ("agent_breakdown", "agent_id", "Agents"),
        "department": ("organizational_unit_breakdown", "charged_unit", "Departments and teams"),
        "context": ("project_breakdown", "project_id", context_plural),
    }

    if intent == "budget":
        budget_query = db.query(DepartmentBudget).filter(
            DepartmentBudget.archived.isnot(True)
        )
        budgets = budget_query.all()
        if request.workspace_id:
            workspace_prefix = f"{request.workspace_id}:"
            scoped = [
                budget for budget in budgets
                if (budget.department or "").startswith(workspace_prefix)
            ]
            budgets = scoped or [
                budget for budget in budgets
                if ":" not in str(budget.department or "")
            ]
        budget_rows = []
        for budget in budgets:
            cap = float(budget.monthly_cap_usd or 0)
            spent = float(budget.current_spend_usd or 0)
            pct = spent / cap * 100 if cap > 0 else 0
            if pct >= 70:
                label = (budget.department or "Unassigned").split(":")[-1]
                budget_rows.append({
                    "id": budget.department,
                    "label": label,
                    "pct": pct,
                    "spent": spent,
                    "cap": cap,
                    "throttled": bool(budget.throttled),
                })
        budget_rows.sort(key=lambda row: -row["pct"])
        evidence = [{
            "label": row["label"],
            "value": f"{row['pct']:.1f}%",
            "metric_label": "budget used",
            "detail": f"${row['spent']:,.2f} of ${row['cap']:,.2f}" +
                      (" · throttled" if row["throttled"] else ""),
            "filter_name": "charged_unit",
            "filter_value": row["label"],
        } for row in budget_rows[:5]]
        title = "Budget watch"
        answer = (
            f"{len(budget_rows)} department{'s are' if len(budget_rows) != 1 else ' is'} "
            "at or above 70% of its monthly AI budget."
            if budget_rows else
            "No active department is at or above 70% of its monthly AI budget."
        )
    elif intent == "savings":
        activities = report.get("activities") or []
        premium = [
            row for row in activities
            if str(row.get("model_tier") or "").lower() in {
                "advisor", "strategist", "flagship"
            }
        ]
        premium_spend = sum(float(row.get("cost_usd") or 0) for row in premium)
        premium_requests = len(premium)
        premium_scope = (
            f"Among the {len(activities):,} most recent matching requests, "
            if int(report.get("activity_count") or 0) > len(activities)
            else ""
        )
        total_spend = float(summary.get("spend_usd") or 0)
        saved_tokens = int(summary.get("tokens_saved") or 0)
        top_agents = _ask_rank(report.get("agent_breakdown") or [], "spend_usd")
        title = "Cost-saving opportunities"
        answer = (
            f"{premium_scope}CostPilot found {premium_requests:,} premium-tier requests representing "
            f"${premium_spend:,.4f} of ${total_spend:,.4f} total spend. "
            f"Pruning removed {saved_tokens:,} tokens before model calls."
        )
        if premium_requests:
            recommendations.append({
                "title": "Review premium-tier routing",
                "body": (
                    f"Inspect the {premium_requests:,} Advisor or Strategist requests before "
                    "changing thresholds. This is reviewable spend, not guaranteed savings."
                ),
            })
        if top_agents:
            recommendations.append({
                "title": f"Start with {top_agents[0].get('label') or 'the top-cost agent'}",
                "body": (
                    f"It accounts for ${float(top_agents[0].get('spend_usd') or 0):,.4f} "
                    "in this period. Compare its task mix and routing evidence before acting."
                ),
            })
        if saved_tokens == 0:
            recommendations.append({
                "title": "Check pruning coverage",
                "body": "No pruned tokens were recorded in this scope. Confirm pruning is enabled on the active agents.",
            })
        evidence = _ask_evidence(
            report.get("agent_breakdown") or [], "spend_usd", "agent_id"
        )
    elif intent == "ranking" and entity in entity_config:
        breakdown_key, filter_name, entity_label = entity_config[entity]
        ranked = _ask_rank(report.get(breakdown_key) or [], metric)
        evidence = _ask_evidence(ranked, metric, filter_name)
        title = f"Top {entity_label.lower()} by {_ask_metric_value(metric, 0)[1]}"
        if ranked:
            value, metric_label = _ask_metric_value(metric, ranked[0].get(metric))
            answer = (
                f"{ranked[0].get('label') or 'Unknown'} had the highest {metric_label} "
                f"over the last {parsed['days']} days: {value}. "
                f"That includes {int(ranked[0].get('request_count') or 0):,} governed requests."
            )
        else:
            answer = f"No {entity_label.lower()} had attributed AI activity in this period."
    else:
        context_rows = report.get("project_breakdown") or []
        title = "Where your AI usage went"
        answer = (
            f"Over the last {parsed['days']} days, CostPilot governed "
            f"{int(summary.get('request_count') or 0):,} requests using "
            f"{int(summary.get('total_tokens') or 0):,} tokens at a cost of "
            f"${float(summary.get('spend_usd') or 0):,.4f}. "
            f"{int(summary.get('people_count') or 0):,} identified people and "
            f"{int(summary.get('agent_count') or 0):,} agents contributed to that usage."
        )
        evidence = _ask_evidence(context_rows, "spend_usd", "project_id")

    return {
        "question": question,
        "title": title,
        "answer": answer,
        "intent": intent,
        "entity": entity,
        "metric": metric,
        "period": period,
        "filters": report.get("filters") or {},
        "summary": summary,
        "evidence": evidence,
        "recommendations": recommendations,
        "measurement_note": report.get("measurement_note"),
        "calculation_source": "CostPilot deterministic attribution engine",
        "read_only": True,
    }


@router.post("")
def generate_efficiency_review(
    days: int = 30,
    db: Session = Depends(get_db),
):
    """
    Analyzes all registered agents and returns AI-written efficiency reviews
    with grades, findings, recommendations, and projected savings.
    """
    from core.model_client import MODEL_MODE

    agents = db.query(RegisteredAgent).all()
    if not agents:
        return {"reviews": [], "total_agents": 0, "total_projected_savings": 0,
                "message": "No agents registered. Connect a platform and route some calls first."}

    reviews = []
    total_savings = 0.0

    for agent in agents:
        stats = _agent_stats(db, agent, days)
        if not stats:
            continue  # skip agents with no data in this period

        if MODEL_MODE == "live":
            review = _generate_live_review(stats)
        else:
            review = _generate_simulated_review(stats)

        reviews.append(review)
        total_savings += review.get("projected_savings", 0)

    # Sort: worst grade first (most improvement opportunity at top)
    grade_order = {"D": 0, "C": 1, "B": 2, "A": 3}
    reviews.sort(key=lambda r: grade_order.get(r["grade"], 2))

    # Overall fleet summary
    if reviews:
        grade_counts = {}
        for r in reviews:
            grade_counts[r["grade"]] = grade_counts.get(r["grade"], 0) + 1
        fleet_grade = min(reviews, key=lambda r: grade_order.get(r["grade"], 2))["grade"]
    else:
        grade_counts = {}
        fleet_grade  = "N/A"

    return {
        "reviews":                 reviews,
        "total_agents_analyzed":   len(reviews),
        "total_projected_savings": round(total_savings, 2),
        "fleet_grade":             fleet_grade,
        "grade_counts":            grade_counts,
        "period_days":             days,
        "generated_at":            datetime.utcnow().isoformat(),
        "generated_by":            "ai" if MODEL_MODE == "live" else "simulated",
    }
