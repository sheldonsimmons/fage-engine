"""Curated, versioned product knowledge for the Ask CostPilot agent."""

from typing import Optional


COSTPILOT_KNOWLEDGE = (
    {
        "id": "routing",
        "title": "AI routing and model tiers",
        "keywords": ("route", "routing", "tier", "scout", "analyst", "advisor", "strategist", "model choice"),
        "summary": "CostPilot evaluates each governed request and selects the least expensive approved model tier that can safely handle the work.",
        "details": (
            "Scout and Analyst handle routine work, while Advisor and Strategist are reserved for work that needs more capability. "
            "Policies, risk signals, request complexity, registered-model availability, and configured overrides can all affect the final route."
        ),
        "page": "/models.html",
        "action": "Open the model registry to review the models assigned to each tier.",
    },
    {
        "id": "savings",
        "title": "Savings and avoided spend",
        "keywords": ("saving", "saved", "avoided", "baseline", "would have spent", "annual savings"),
        "summary": "CostPilot measures avoided spend by comparing governed activity with its approved no-control baseline.",
        "details": (
            "Savings can come from lower-cost routing, prompt pruning, budget controls, and requests blocked before model spend. "
            "Projected annual savings extends the current measured pace; it is a projection, not booked accounting savings."
        ),
        "page": "/reports.html",
        "action": "Open reports to inspect the calculation period, evidence, and savings sources.",
    },
    {
        "id": "pruning",
        "title": "Prompt and context pruning",
        "keywords": ("prun", "junk token", "token removed", "context removed", "prompt cleanup"),
        "summary": "CostPilot removes unnecessary prompt material before the model call while preserving the content needed to complete the task.",
        "details": (
            "Typical removable material includes repeated headers, signatures, duplicated history, and stale thread content. "
            "The audit record keeps the measured token reduction and its estimated cost effect."
        ),
        "page": "/reports.html",
        "action": "Review pruning evidence in reports or inspect the governed request in the audit log.",
    },
    {
        "id": "risk",
        "title": "Risk, policy, and blocked requests",
        "keywords": ("risk", "block", "blocked", "policy", "sensitive", "governance", "collision"),
        "summary": "CostPilot evaluates governance and policy controls before allowing a request to reach a model.",
        "details": (
            "A request can be blocked or constrained because of sensitive content, policy rules, budget controls, or collision protection. "
            "The audit event is the authoritative record of which control acted and what CostPilot recorded."
        ),
        "page": "/audit.html",
        "action": "Open the supporting audit event to inspect the recorded routing and policy evidence.",
    },
    {
        "id": "budgets",
        "title": "Budgets and throttling",
        "keywords": ("budget", "cap", "throttle", "limit", "month end"),
        "summary": "CostPilot compares governed AI spend with configured workspace or department budgets and surfaces approaching or exceeded limits.",
        "details": (
            "Depending on policy, a budget signal can recommend review, downgrade eligible traffic, throttle usage, or block additional spend. "
            "Budget reporting follows the selected workspace, department, and date scope."
        ),
        "page": "/admin.html",
        "action": "Open Admin to review department caps and the policy attached to each threshold.",
    },
    {
        "id": "attribution",
        "title": "Business context and accountability",
        "keywords": ("attribution", "account", "project", "business context", "work item", "who", "department", "agentlake"),
        "summary": "CostPilot connects governed AI activity to people, agents, departments, platforms, and business records when those identifiers are supplied or discovered.",
        "details": (
            "Company totals and attribution drill-downs are alternate views of the same governed requests. Missing attribution means the source did not provide, map, or resolve that context; it does not mean the activity was absent."
        ),
        "page": "/work-items.html",
        "action": "Open Work Attribution to inspect the people, agents, and business records connected to the activity.",
    },
    {
        "id": "audit",
        "title": "Audit evidence",
        "keywords": ("audit", "evidence", "decision", "why did", "what happened", "request detail"),
        "summary": "Each governed request creates evidence describing what CostPilot evaluated, how it routed the work, what it cost, and which controls acted.",
        "details": (
            "The audit trail is the authoritative source for a specific request. Dashboard and report totals aggregate those records without replacing the underlying evidence."
        ),
        "page": "/audit.html",
        "action": "Open the request's audit detail when you need to explain one routing or policy decision.",
    },
    {
        "id": "dashboard",
        "title": "Executive dashboard",
        "keywords": ("dashboard", "metric", "chart", "executive", "this number", "this chart", "what am i looking at"),
        "summary": "The executive dashboard summarizes governed AI spend, savings, optimization, risk, budgets, and accountability for the selected scope.",
        "details": (
            "Filters change the executive slice. The dashboard should distinguish filtered calculations from all-workspace totals, and supporting evidence remains available for deeper review."
        ),
        "page": "/index.html",
        "action": "Check the active filters and date range before comparing a dashboard number with another page.",
    },
)


def search_costpilot_knowledge(question: str, page_path: Optional[str] = None, limit: int = 3) -> list[dict]:
    """Return the best curated product topics for a question and current page."""
    text = " ".join((question or "").lower().split())
    path = (page_path or "").lower()
    ranked = []
    for topic in COSTPILOT_KNOWLEDGE:
        score = sum(3 for keyword in topic["keywords"] if keyword in text)
        if topic["id"] in text:
            score += 2
        if path and topic["page"].split("?")[0] in path:
            score += 1
        if score:
            ranked.append((score, topic))
    ranked.sort(key=lambda item: (-item[0], item[1]["title"]))
    return [dict(topic) for _, topic in ranked[:max(1, min(limit, 5))]]
