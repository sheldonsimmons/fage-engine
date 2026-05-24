# FAGE — FinOps Agentlake & Governance Engine
## Complete Feature & Functionality Reference

---

## What Is FAGE?

FAGE is an AI governance middleware platform that sits between your existing business tools (Salesforce, ServiceNow, HubSpot, etc.) and the AI models they call. It intercepts every AI request, governs it, tracks it, and reports on it — without requiring any changes to the tools your teams already use.

**The one-line pitch:** Your AI agents keep doing what they do. FAGE makes sure they do it within budget, within policy, and with a complete audit trail.

---

## How It Works (Non-Technical)

1. A business tool (e.g. Salesforce) sends a request to an AI model
2. That request hits FAGE first instead of going directly to OpenAI
3. FAGE checks: Is this request sensitive? Does it violate policy? Is the department over budget?
4. If blocked → rejected immediately, logged, no AI cost incurred
5. If allowed → FAGE selects the right model tier, sends the request, tracks the cost, and logs the decision
6. The dashboard updates in real time

---

## Core Features Built

### 1. Context Pruning Engine
**What it does:** Automatically strips junk from AI requests before they're sent — email signatures, legal disclaimers, forwarded message chains, duplicate whitespace, boilerplate text.

**Why it matters:** Fewer tokens sent = lower cost per call. Pruning saves 20–40% on token costs with no loss of response quality.

**How it works:** Payload is scanned against pattern rules. Removed content is counted. The dashboard shows tokens saved and estimated dollar savings from pruning.

---

### 2. Token Router & Model Cascader (4-Tier System)
**What it does:** Automatically routes each request to the right AI model tier based on complexity — so routine questions don't cost the same as complex legal analysis.

**The four tiers:**

| Tier | Name | Best For | Cost (Input/Output per 1M tokens) |
|------|------|----------|-----------------------------------|
| 1 | Scout | Fast, routine tasks — FAQs, simple lookups | $0.20 / $1.25 |
| 2 | Analyst | Balanced — most business tasks | $0.75 / $4.50 |
| 3 | Advisor | Deep reasoning — complex, sensitive work | $2.50 / $15.00 |
| 4 | Strategist | Mission-critical decisions only | $5.00 / $30.00 |

**Routing logic:**
- Short, simple requests → Scout (Tier 1)
- Long or complex requests (150+ tokens) → Advisor (Tier 3)
- Requests matching sensitive/legal keywords → escalated to Advisor or Strategist
- Department over budget → forced back to Scout regardless of complexity

**Real cost impact:** Sending everything to Advisor instead of routing costs ~12x more per call. For 500 calls/day across a department, smart routing saves thousands per month.

---

### 3. Budget Allocator & Auto-Throttle
**What it does:** Gives each department a monthly AI spending cap. Tracks spend in real time. Auto-throttles departments that hit their cap.

**Departments configured:** Support, Sales, Marketing, Operations, Trips Team

**Default caps:**
- Support: $200/month
- Sales: $300/month
- Marketing: $250/month
- Operations: $150/month
- Trips Team: $200/month

**How throttling works:** When a department's spend reaches 100% of cap, all future requests from that department are automatically downgraded to Scout (cheapest tier) until a supervisor grants an override or the month resets. At 80%, a yellow warning state is triggered.

**Supervisor override:** Supervisors can grant temporary override access through the dashboard — this is logged in the audit trail.

---

### 4. Sensitive Term Library & PII Blocker
**What it does:** Scans every incoming request for sensitive words, phrases, and data patterns. Blocks or escalates based on policy — before the request ever reaches an AI model.

**Two actions:**
- **Block** → request rejected entirely. No AI cost. Audit event created. Caller receives an error.
- **Escalate** → request allowed but forced to a higher model tier for more careful handling.

**Pre-seeded terms (survive server restarts):**

| Term | Category | Action |
|------|----------|--------|
| SSN, social security number | HIPAA | Block |
| Credit card, card number, CVV | Financial | Block |
| Routing number, bank account | Financial | Block |
| Passport number, date of birth | HIPAA | Block |
| Lawsuit, litigation, attorney | Legal | Escalate |
| Legal action, breach of contract | Legal | Escalate |
| GDPR, HIPAA, regulatory, audit | Legal/HIPAA | Escalate |
| Termination, harassment, discrimination | HR | Escalate |

**Custom terms:** Admins can add, edit, or remove terms through the Setup UI at any time.

---

### 5. Agentlake Registry & Traffic Cop
**What it does:** Tracks every AI agent (bot) registered in the system — what platform it came from, what department it belongs to, what data it can access, and whether it's currently active, idle, or locked.

**Auto-registration:** When a new agent calls FAGE for the first time (e.g. a new Salesforce bot), it is automatically registered in the registry without any manual setup.

**Collision prevention:** If two agents try to write to the same record simultaneously, FAGE detects the conflict, locks both agents, and prevents silent data corruption. A supervisor must review and release the lock. Every collision is logged.

**Supported platforms tracked:** Salesforce, ServiceNow, HubSpot, Dynamics 365, Zendesk, Custom

---

### 6. AI Decision Auditor (Immutable Audit Log)
**What it does:** Every significant AI decision is logged permanently — routing decisions, blocked requests, budget throttles, agent collisions, supervisor overrides.

**What each audit record captures:**
- Timestamp
- Department and agent
- Event type (ROUTING, DECISION, LOCK, OVERRIDE)
- Risk level (LOW / MEDIUM / HIGH / CRITICAL)
- The exact prompt payload (first 2,000 characters)
- A plain-English rationale explaining why the decision was made
- Budget snapshot at the time of decision
- Dollar cost of the call

**Risk levels:**
- CRITICAL → blocked PII requests, GDPR/HIPAA keyword matches
- HIGH → legal keywords, agent collisions, budget throttle events
- MEDIUM → complex routing decisions
- LOW → routine routing

**Dual storage:** Every event is written to both the database (queryable) and an append-only JSONL flat file (downloadable, tamper-evident).

**When the auditor calls:** Download the full JSONL audit file directly from the dashboard. Every decision has a timestamp, rationale, and dollar amount attached.

---

### 7. Executive Dashboard
**What it shows:**

**Top KPI Cards:**
- Total Spend Today / This Month
- Tokens Saved by Pruning (estimated dollar value)
- Active Agents (active / locked / idle breakdown)
- Throttled Departments (with supervisor action alert)

**Secondary Stats Bar:**
- Total Calls / Micro Calls / Flagship Calls with percentages
- Overall Budget Used %
- Pruning Savings
- Month Spend

**Department Budgets Panel:**
- Per-department spend vs. cap with progress bar
- Color coded: green (healthy) → yellow (80%+ warning) → red (throttled)
- Set Cap and Reset Month buttons per department

**Agentlake Registry Panel:**
- All registered agents with platform, department, target table, collision policy, status, last active
- Release locked agents
- Simulate collision (demo mode)

**Governance & Compliance Panel:**
- Requests Blocked
- Escalated to Flagship
- Flagged in Audit Log
- PII Detected
- Budget Overruns Prevented
- Agent Collisions Resolved

**Executive Summary ROI Panel:**
- Projected Annual Savings
- AI Cost Reduction %
- Compliance Events Logged
- Time to Deploy
- Tokens Saved
- Data Corruption Events

**Recent High-Stakes Events Strip:**
- Last 5 audit events with type, department, risk level, outcome

**Blocked Event Alert System:**
- Pulsing red banner appears automatically when blocked requests exist in the last 24 hours
- Shows count: "🚨 3 requests blocked in the last 24 hours"
- "Review Blocked Events" button filters the audit table to blocked-only view
- "BLOCKED ONLY" badge appears in the audit log header when filter is active
- Toggle back to "Show All Events" with one click

**Audit Log Table:**
- Last 20 events, auto-refreshes every 15 seconds
- Blocked rows: red background, red left border stripe, 🛡 BLOCKED badge
- Click any row to expand full rationale + budget snapshot + prompt payload
- Download full JSONL audit file

---

### 8. Reports (4 Tabs)

**Savings Report:**
- Token pruning savings over time (chart)
- Model downgrade savings (what routing saved vs. sending everything to flagship)
- Daily cost timeline
- Total cost vs. what it would have cost without FAGE

**Risk Report:**
- Sensitive term hits by category
- Audit event timeline
- Block vs. escalate breakdown

**Department Scorecard:**
- Per-department: total calls, economy vs. premium split, spend, pruning savings, budget health

**Agent Activity Report:**
- Per-agent call volume, cost, premium model %, pruning rate, last active
- Full call log per agent (last 100 transactions)
- Filters: platform, department, agent, model tier, date range (20 named presets + custom)

**Date Range Picker:** Shared across all report tabs — 20 named presets (Last 7 Days, Last 30 Days, This Month, Last Month, Last Quarter, Last Year, etc.) plus custom date range inputs.

---

### 9. Model Registry
**What it does:** Lets administrators manage which AI models are available in FAGE, at what cost, and at which tier. The router reads from this registry at call time — no code changes needed to swap models.

**Four tier system:**
- Scout (Tier 1) — Economy: fast, affordable, routine
- Analyst (Tier 2) — Balanced: most business tasks
- Advisor (Tier 3) — Premium: complex, sensitive work
- Strategist (Tier 4) — Elite: mission-critical only

**Models pre-configured (GPT-5.x family):**
- GPT-5.4 Nano → Scout
- GPT-5.4 Mini → Analyst
- GPT-5.4 → Advisor
- GPT-5.5 → Strategist

**Admin controls:** Add new models, edit pricing, enable/disable models, set default per tier, delete models — all through the UI with no code changes.

---

### 10. Salesforce Integration (Live & Working)
**How it works:**
- A Salesforce Flow triggers on Case create/update
- An Apex `@future(callout=true)` class sends the Case Description to FAGE's `/api/route` endpoint
- FAGE processes the request, routes it to the correct model, tracks the cost, updates the department budget, and logs the decision
- The dashboard reflects the call within seconds

**What Salesforce sends to FAGE:**
- `text` — the Case Description
- `department` — e.g. "Trips Team"
- `agent_name` — e.g. "SF-Trips Team Agent"
- `auto_prune` — true (FAGE prunes junk before routing)

**Zero infrastructure changes required on Salesforce side** — just a Remote Site Setting (already configured) and one Apex class.

---

## Live Deployment

**Platform:** Heroku (fage-engine-21cb49fe4806.herokuapp.com)
**AI Provider:** OpenAI (live mode, real API calls, real costs)
**Database:** SQLite with startup seeder (budgets, models, and sensitive terms auto-restore on every dyno restart)
**Version:** v50 (as of May 24, 2026)

**Important note on Heroku free dynos:** Dynos sleep after 30 minutes of inactivity. The first Salesforce callout after a sleep period may time out while the dyno wakes. Open the dashboard first to wake it, then trigger Salesforce calls. Upgrading to Heroku Basic ($7/month) eliminates sleep entirely.

---

## Integration Targets (Planned)

| Platform | Integration Method |
|----------|-------------------|
| Salesforce | Apex callout + Flow — LIVE |
| ServiceNow | REST integration / MID Server |
| HubSpot | Webhook workflow actions |
| Microsoft Dynamics 365 | Power Automate connectors |
| Zendesk | Apps framework + webhooks |
| SAP | Integration Suite / API Management |
| Workday | Workday Studio / Extend |
| Slack | Bolt SDK |
| Microsoft Teams | Power Platform connectors |

---

## What FAGE Is NOT

- Not an AI model — it doesn't generate responses, it governs the ones that do
- Not a replacement for Salesforce, ServiceNow, or any existing tool
- Not a monitoring tool that watches after the fact — it intercepts before the call
- Not difficult to deploy — one Apex class, one Flow action, one afternoon

---

## The CFO Summary

Without FAGE:
- AI costs are invisible until the invoice arrives
- No department knows what they're spending or why
- One complex legal query costs the same budget as 60 routine support tickets
- A bot sending customer SSNs to an AI model is indistinguishable from a routine support request
- When the auditor asks "show me every AI decision from last quarter" — there is no answer

With FAGE:
- Every dollar of AI spend is tracked by department, by agent, by call
- Budget caps auto-enforce — departments cannot overspend without a supervisor override
- Smart routing cuts per-call costs by 60-90% for routine requests
- PII and sensitive data are blocked before they reach any AI model — zero liability exposure
- Every decision has a timestamp, rationale, and dollar amount — audit-ready at any time
