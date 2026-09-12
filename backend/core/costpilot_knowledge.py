"""Curated, versioned product knowledge for the Ask CostPilot agent."""

from typing import Optional


COSTPILOT_KNOWLEDGE = (
    {
        "id": "capabilities",
        "title": "What Ask CostPilot can do",
        "keywords": (
            "what can you do", "what can ask costpilot do", "what can costpilot do",
            "how can you help", "what can i ask", "your capabilities",
        ),
        "summary": (
            "Ask CostPilot can analyze your governed AI spend, tokens, people, agents, "
            "business contexts (projects/accounts), models, routing, pruning, budgets, and risk."
        ),
        "details": (
            "You can ask about totals, rankings, and comparisons over any date range; drill into "
            "a specific person, agent, department, account, project, platform, or model; ask why a "
            "number changed; check budget status or propose a budget cap change; and ask about "
            "CostPilot's own behavior (what data it uses, how it handles an ambiguous name, "
            "measured vs. estimated data). It answers only from your workspace's own governed "
            "activity -- never a guess or outside knowledge."
        ),
        "page": "/index.html",
        "action": "Try a specific question about spend, usage, budgets, or a named person/agent/account.",
    },
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
    {
        "id": "honesty_no_guessing",
        "title": "No guessing, no invented numbers",
        "keywords": (
            "guess", "make up a number", "estimate numbers you don't have",
            "if you don't know", "don't know the answer", "invent a number", "fabricate",
        ),
        "summary": "Ask CostPilot never guesses or invents a number it doesn't have real data for.",
        "details": (
            "Every figure comes from a governed request, a stored calculation, or an explicitly "
            "labeled estimate or projection -- never a made-up value. When there isn't enough data "
            "to answer (an unsupported time period, a missing dimension, zero matching activity), "
            "CostPilot says so directly and explains what it can answer instead, rather than "
            "substituting a plausible-looking guess."
        ),
        "page": "/index.html",
        "action": "Ask a narrower or differently scoped question if an answer comes back as unsupported.",
    },
    {
        "id": "disambiguation",
        "title": "Handling an ambiguous name",
        "keywords": (
            "name that could mean", "two different things", "ambiguous name",
            "which one did you mean", "same name",
        ),
        "summary": "When a name matches more than one real record, CostPilot asks which one you meant instead of guessing.",
        "details": (
            "If a person, account, or business record name resolves to multiple distinct matches, "
            "the answer lists the specific candidates and asks you to pick one -- it never silently "
            "picks the first match or blends them together into one number."
        ),
        "page": "/index.html",
        "action": "Reply with the exact name of the option you meant, or its filter, to continue.",
    },
    {
        "id": "measured_vs_estimated",
        "title": "Measured vs. estimated data",
        "keywords": (
            "measured and estimated", "measured or estimated", "measured vs estimated",
            "measured data", "estimated data",
        ),
        "summary": (
            "Measured data comes directly from a recorded governed AI request; estimated data is "
            "calculated or projected from that measured activity."
        ),
        "details": (
            "A measured figure (like total AI spend for a period) is summed directly from logged "
            "requests. An estimated or projected figure (like annualized savings) extrapolates from "
            "measured activity using a stated method, and CostPilot labels it as an estimate or "
            "projection rather than presenting it as an already-recorded fact."
        ),
        "page": "/reports.html",
        "action": "Check a number's evidence/calculation detail to see whether it's measured or estimated.",
    },
    {
        "id": "outcome_causation",
        "title": "Outcome association, not causation",
        "keywords": (
            "caused by ai", "just associated with it", "caused or associated",
            "did ai cause", "ai-caused",
        ),
        "summary": "CostPilot tracks AI activity associated with a business outcome, not proof that the AI caused it.",
        "details": (
            "Outcome numbers (won or lost deals, resolved cases, business value) show that AI "
            "activity occurred alongside that outcome's record -- CostPilot doesn't claim, and "
            "can't prove, that the AI activity caused the result. Answers involving outcomes say "
            "'associated with' rather than 'caused by' for exactly this reason."
        ),
        "page": "/reports.html",
        "action": "Open the account or work item's outcome evidence to see the underlying record.",
    },
    {
        "id": "data_sources",
        "title": "What data Ask CostPilot uses",
        "keywords": (
            "what data do you use", "data do you use to answer", "where does your data come from",
            "what data sources",
        ),
        "summary": "Ask CostPilot answers only from your workspace's own governed AI activity, not external knowledge or web data.",
        "details": (
            "Every answer is computed from logged AI requests, their attribution (people, agents, "
            "departments, accounts, business records), and related governance/audit events captured "
            "through your connected platforms -- never from the model's general knowledge or an "
            "internet search."
        ),
        "page": "/connections.html",
        "action": "Check Connections to see which platforms are feeding data into your workspace.",
    },
    {
        "id": "budget_change_capability",
        "title": "Requesting a budget change",
        "keywords": (
            "change a budget", "ask you to change", "propose a budget change",
            "make a budget change", "budget cap change", "can you change my budget",
        ),
        "summary": "Yes -- you can ask CostPilot to propose a department's budget cap change, but it never changes anything on its own.",
        "details": (
            "Asking to change a cap (e.g. 'increase Engineering's cap to $10,000') creates a "
            "proposal that a human must explicitly confirm before it takes effect. You can also ask "
            "a 'what if' question to simulate a cap change and see the projected impact without "
            "creating anything that needs approval."
        ),
        "page": "/admin.html",
        "action": "Confirm or reject a pending budget proposal from Admin before it takes effect.",
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
