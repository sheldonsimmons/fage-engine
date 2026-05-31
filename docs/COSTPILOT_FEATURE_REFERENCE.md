# CostPilot — Full Feature Reference
**Version 0.1.0 · Last Updated: May 2026**

This document covers every page, panel, control, metric, and API endpoint in CostPilot. Use it as a product reference, onboarding guide, or integration spec.

---

## Table of Contents

1. [Product Overview](#1-product-overview)
2. [Executive Summary Dashboard](#2-executive-summary-dashboard-indexhtml)
3. [Operations Dashboard](#3-operations-dashboard-operatehtml)
4. [Admin — Budget Controls & Agent Management](#4-admin--budget-controls--agent-management-adminhtml)
5. [Reports Dashboard](#5-reports-dashboard-reportshtml)
   - [Tab 1: Savings](#tab-1--savings)
   - [Tab 2: Risk & Compliance](#tab-2--risk--compliance)
   - [Tab 3: Departments](#tab-3--departments)
   - [Tab 4: Bot Efficiency](#tab-4--bot-efficiency)
   - [Tab 5: Agent Activity](#tab-5--agent-activity)
   - [Tab 6: ROI Calculator](#tab-6--roi-calculator)
6. [Policy & Rules](#6-policy--rules-policyhtml)
7. [Model Registry](#7-model-registry-model-registryhtml)
8. [Onboarding Wizard](#8-onboarding-wizard-onboardinghtml)
9. [Sandbox](#9-sandbox-sandboxhtml)
10. [Live Demo Landing](#10-live-demo-landing-live-landinghtml)
11. [API Reference](#11-api-reference)
    - [POST /api/route](#post-apiroute)
    - [GET /api/dashboard](#get-apidashboard)
    - [Agent Endpoints /api/agents](#agent-endpoints-apiagents)
    - [Report Endpoints /api/reports](#report-endpoints-apireports)
    - [Policy Endpoints /api/routing & /api/sensitive-terms](#policy-endpoints)
    - [Audit Endpoints /api/audit](#audit-endpoints-apiaudit)
12. [Data Models](#12-data-models)
13. [UI Patterns & Conventions](#13-ui-patterns--conventions)
14. [Architecture Quick Reference](#14-architecture-quick-reference)

---

## 1. Product Overview

CostPilot is an AI cost governance middleware platform. It sits between your CRM/support platform and your AI provider, enforcing routing rules, budget caps, context pruning, PII detection, and agent collision controls — all without changing your existing workflows.

**Core value**: Route AI calls to the cheapest model that can handle them, prune redundant context before billing, cap departments at their monthly budgets, and produce a full audit trail for compliance.

**Tech stack**: Python/FastAPI backend, plain HTML/JS/CSS frontend, PostgreSQL via SQLAlchemy, deployed on Heroku.

**Key concepts**:
- **Agent**: A named AI caller registered in Agentlake (e.g., SupportBot-Alpha, FinanceAdvisor)
- **Department**: A billing unit (e.g., Support, Finance, Engineering) with its own monthly cap
- **Model Tier**: Scout (T1, cheapest) → Analyst (T2) → Advisor (T3) → Strategist (T4, flagship)
- **Pruning**: Stripping redundant tokens from context before routing to reduce input cost
- **Collision**: Two agents trying to write the same CRM record simultaneously — CostPilot locks one out

---

## 2. Executive Summary Dashboard (`index.html`)

**Audience**: Executives, finance leads, anyone who needs a one-glance ROI view.

**Data source**: `GET /api/dashboard` (refreshes every 15 seconds)

---

### Header

| Element | Description |
|---------|-------------|
| Logo | "◈ CostPilot" with tagline: AI Cost Control · CRM · Support · Engineering · Finance |
| Status indicator | Online/offline pill — green pulse when API is reachable |
| Navigation | Dashboard · Reports · Admin · Savings Calc · Live Demo · LIVE |

---

### Hero Row

**Projected Annual Savings Card** (green accent, left column)

| Field | Source |
|-------|--------|
| Headline savings figure | `projected_annual_savings` × 12 |
| Monthly savings | `total_savings_usd` for current month |
| Routing efficiency % | `routing_efficiency_pct` |
| Prevented spend | `routing_savings_usd + pruning_savings_usd` |
| Trend badge | Percentage improvement vs. all-flagship baseline |

**Spend vs. Baseline Chart** (right column)

A line chart showing two datasets over time:
- **All-Flagship (dashed)** — what you would have spent routing everything to the top tier
- **Actual (solid green)** — what CostPilot actually spent

The shaded area between the lines is the savings band. Tooltip shows daily cost for both scenarios.

---

### Metric Cards (3-column grid)

| Card | Metric | Detail |
|------|--------|--------|
| Monthly Spend | `spend_month_usd` | Budget cap % tag + utilization bar |
| Routing Efficiency | `routing_efficiency_pct` | % of calls handled by economy tiers (Scout + Analyst) |
| Requests Governed | `total_calls` | Total AI calls managed; incident count badge (color-coded by risk) |

---

### Savings Breakdown Card

Title: "Where the savings came from"

| Row | What it measures |
|-----|-----------------|
| Economy routing savings | Cost difference vs. routing all calls to flagship |
| Context pruning savings | Token reduction × micro model rate |
| Budget caps & throttling | Spend prevented by throttle enforcement |
| Blocked requests | Calls stopped entirely (PII, sensitive terms) |

Each row has a horizontal bar showing relative contribution to total savings.

---

### Footer Stats Bar

| Stat | Source field |
|------|-------------|
| Active agents | `agents_active` |
| Departments | Count of department budget records |
| Collision locked | `agents_locked` |
| Audit events logged | `compliance_events_total` |

Button: **"Open Operate →"** (green) — navigates to the Operations Dashboard.

---

## 3. Operations Dashboard (`operate.html`)

**Audience**: Ops leads, AI engineers, anyone monitoring live AI traffic.

**Data source**: `GET /api/dashboard` + `GET /api/agents` + `GET /api/audit` (refreshes every 15 seconds)

---

### KPI Strip (always visible, 6 columns)

| Column | Metric | Alert behavior |
|--------|--------|----------------|
| Total Spend Today | `spend_today_usd` | — |
| Tokens Saved (Pruning) | `tokens_saved_today` | — |
| Month Spend | `spend_month_usd` | — |
| Active Agents | `agents_active` | — |
| Throttled Depts | `throttled_count` | Red styling if > 0 |
| Blocked Requests | `blocked_count` | Red styling if > 0 |

---

### CEO Savings Banner (always visible)

Large green display showing real-time ROI summary:

| Field | Description |
|-------|-------------|
| Total AI Spend Avoided | `routing_savings_usd + pruning_savings_usd` |
| Economy Routing % | `routing_efficiency_pct` — share of calls at lower cost |
| Requests Governed | `total_calls` |
| Context Pruning Saved | Dollar amount + token count |
| Projected Annual Savings | Monthly savings × 12 |
| Badge | "LIVE SAVINGS" (green) |

---

### Secondary Stat Bar

Per-tier call counts + summary efficiency metrics:

| Stat | Description |
|------|-------------|
| Scout (T1) | `scout_calls` |
| Analyst (T2) | `analyst_calls` |
| Advisor (T3) | `advisor_calls` |
| Strategist (T4) | `strategist_calls` |
| Budget Used % | `overall_budget_pct` |
| Pruning Saved | `pruning_savings_usd` |

Controls: **Refresh** button (manual poll).

---

### Main Content — Left Column

#### Agentlake Registry & Budget Panel

**Agent Cards Grid** — one card per registered agent (6-column grid):

| Badge element | Value |
|--------------|-------|
| Agent name | `name` |
| Department | `department` |
| Platform | `source_platform` (badge) |
| Status | `status`: idle (gray) · active (green) · locked (red) · queued (yellow) |

**Department Budget Utilization** — one bar per department:

| Element | Value |
|---------|-------|
| Name | Department name |
| Spend | `current_spend_usd` / `monthly_cap_usd` |
| Bar | Proportional fill; turns red when throttled |
| % | `used_pct` |

Link: "Manage budgets & agents → Admin"

---

#### 30-Day Spend & Activity Trends Panel (collapsible)

Two charts side-by-side when expanded:
1. **Daily Spend (USD) by Department** — stacked area chart, one series per department
2. **Daily AI Calls by Model Tier** — stacked bar chart, one bar per day colored by tier

---

#### Routing Decision Feed Panel (collapsible)

Real-time table of recent routing decisions pulled from the audit log.

**Filters**:
| Filter | Options |
|--------|---------|
| Risk | Critical · High · Medium · Low |
| Tier | All · Scout · Analyst · Advisor · Strategist |
| Blocked | Toggle (show blocked-only) |
| Refresh | Manual poll button |

**Table columns**:
| Column | Description |
|--------|-------------|
| Time | ISO timestamp |
| Agent | Agent name |
| Dept | Department |
| Model Tier | Tier badge (color-coded) |
| Risk | Risk level badge |
| Outcome | ROUTED · BLOCKED · THROTTLED · SKIPPED |

---

#### AI Decision Audit Log Panel (collapsible)

Full searchable audit log.

**Alert banner**: If blocked events exist → "🛡 N blocked requests — Review Blocked Events ↓"

**Filters**:
| Filter | Type |
|--------|------|
| Department | Dropdown |
| Risk level | Dropdown |
| Time window | Dropdown |
| Blocked Only | Toggle |
| Clear | Button |

**Export buttons**: CSV · PDF

**Table columns**:
| Column | Description |
|--------|-------------|
| Timestamp | ISO datetime |
| Event Type | routing, block, throttle, collision, etc. |
| Department | Department name |
| Model Tier | Tier badge |
| Risk Level | Color-coded badge |
| Outcome | Decision result |

---

### Main Content — Right Column: Governance Event Stream (fixed, always visible)

Live scrolling feed of governance events.

**Header**: "▸ Governance Event Stream" with "● LIVE" badge.

**Filters**:
| Filter | Options |
|--------|---------|
| Type | Blocked · Throttled · Complex · Pruning · Routine |
| Department | Dropdown of all departments |
| Search | Free text |
| Refresh | Manual poll |

Each event entry shows: timestamp, type badge (color-coded), department, and a short description. The stream auto-scrolls as new events arrive.

---

## 4. Admin — Budget Controls & Agent Management (`admin.html`)

**Audience**: Admins, IT leads, finance ops.

---

### Demo Controls Bar

| Control | Action |
|---------|--------|
| "Reset Demo Data" (red) | Wipes demo transactions and regenerates enterprise seed data |

---

### Department Budgets Panel (left column)

Loaded from `GET /api/budgets`. One row per department:

| Field | Description |
|-------|-------------|
| Department name | Label |
| Monthly cap | Editable number input (`PATCH /api/budgets/{dept}`) |
| Current spend | Read-only |
| Budget bar | Visual utilization fill (green → yellow → red) |
| % used | Calculated from cap and spend |
| Throttle toggle | Enable/disable auto-throttle at threshold |
| Throttle % | Input: percentage of cap at which throttle fires |
| Status | Under cap · Warning · Over cap (badge) |

---

### Agentlake Registry Panel (right column)

Full agent management table.

**Filters**:
| Filter | Options |
|--------|---------|
| Platform | Salesforce · ServiceNow · HubSpot · Dynamics365 · Zendesk · Custom |
| Status | Idle · Active · Locked · Queued |
| Department | All departments |
| Search | Free text (name match) |

**Table columns**:
| Column | Description |
|--------|-------------|
| Agent | Name + platform badge |
| Department | Department name |
| Platform | Source platform |
| Target | Object/table the agent writes to |
| Policy | Collision handling: Lock · Queue · Skip |
| Status | Color-coded status badge |
| Last Active | Relative timestamp |
| Tier Bounds | Min tier dropdown + Max tier dropdown — constrains routing range for this agent |
| Pruner | PRUNE ON / PRUNE OFF toggle button — green when on, muted when off |
| Action | Dropdown: Archive · Release · Simulate Collision |

**Controls under table**:
| Button | Action |
|--------|--------|
| "⚠ Simulate Collision" | Forces two agents to conflict on the same record (demo) |
| "Show Archived" | Toggle to reveal soft-deleted agents |

**Collision alert banner**: Appears when a collision is simulated or detected in real traffic.

---

### Register New Agent Form

| Field | Type | Options |
|-------|------|---------|
| Agent Name | Text input | — |
| Source Platform | Dropdown | Infer from name · Salesforce · ServiceNow · HubSpot · Microsoft · Zendesk · SAP · Custom |
| Department | Dropdown | All active departments |
| Target Table | Dropdown | tickets · crm_records · customers · token_transactions |
| Permissions | Dropdown | read · read+write · read+write+delete |
| Collision Policy | Dropdown | Lock — require supervisor · Queue — auto-retry · Skip — abandon |

Submit button: **"+ Register Agent"** → `POST /api/agents/register`

---

### Agent Spend Intelligence Panel

Subtitle: "Per-agent cost breakdown — all time"

**Refresh button**: Manual poll → `GET /api/agents/spend`

**Table columns**:
| Column | Description |
|--------|-------------|
| Agent | Name (color-coded by department) |
| Department | Department name |
| Platform | Source platform |
| Calls | Total API calls made by this agent |
| Top Tier | Most-used model tier (badge) |
| Input Tokens | Formatted integer |
| Output Tokens | Formatted integer |
| Tokens Saved | Green — tokens removed by pruner |
| Total Cost | Blue accent — with proportional cost bar |
| Last Active | Relative timestamp |

---

## 5. Reports Dashboard (`reports.html`)

**Header controls**:

| Control | Type | Options |
|---------|------|---------|
| Time Range | Dropdown | Today · Yesterday · This Week/Month/Quarter/Year · Last 7/14/30/60/90/120 days · 6 months · Custom (date range inputs) |
| Randomize | Button | Shuffles draggable card order |
| Export | CSV · PDF buttons (per tab) |

All reports call `GET /api/reports/{type}?days={N}` with the selected range.

---

### Tab 1 — 💰 Savings

**Today Counter** (live, 10-second refresh):

| Field | Description |
|-------|-------------|
| Calls | Real-time call count for today |
| Cost | Spend today |
| Tokens Saved | Pruning savings today |
| Last updated | HH:MM:SS timestamp |

**KPI Row** (6 metrics):
| Metric | Source field |
|--------|-------------|
| Total Saved | `total_saved_usd` |
| Without CostPilot | `cost_if_no_fage_usd` |
| Actual Cost | `total_cost_usd` |
| Pruning Saved | `pruning_saved_usd` (with token count) |
| Model Downgrade Saved | `downgrade_saved_usd` (with economy % note) |
| Total Calls | `total_calls` (with micro/flagship split) |

**Charts** (draggable cards):
- Card 1: **Daily Spend** (line chart, full width) + **Model Tier Split** (doughnut)
- Card 2: **Daily Tokens Pruned** (bar chart) + **Savings Breakdown** (pie: pruning vs. routing vs. blocks)

---

### Tab 2 — 🛡 Risk & Compliance

**KPI Row** (6 metrics):
| Metric | Source field |
|--------|-------------|
| Total Events | `total_events` |
| Critical | `critical` (red alert badge) |
| High Risk | `high` (yellow badge) |
| Blocked Requests | `blocked` (red badge) |
| Agent Collisions | `locks` (yellow badge) |
| Term Library | `term_library.total` with block/escalate split |

**Charts & Tables** (draggable cards):
- Card 1: **Daily Risk Events** (time series by severity) + **Risk Level Breakdown** (pie)
- Card 2: **Governance & Compliance Activity** table — Category · Count · Trend
- Card 3: **Executive Summary — AI Governance ROI** — risk events prevented, audit events logged, cost of risk avoidance
- Card 4: **Recent High-Stakes Events** table — Timestamp · Type · Department · Risk · Outcome (last 25 events from `recent_events`)

---

### Tab 3 — 🏢 Departments

**Charts & Tables** (draggable cards):
- Card 1: **Department Scorecard** table

| Column | Description |
|--------|-------------|
| Department | Name |
| Calls | `total_calls` |
| Micro % | `micro_pct` |
| Actual Cost | `total_cost_usd` |
| Pruning Saved | `pruning_saved_usd` |
| Budget Used | `budget_used_pct` bar |
| Cap | `monthly_cap_usd` |
| Status | Under cap · Warning · Over cap |

- Card 2: **Daily Spend by Department** (stacked area chart) + **Cost by Department** (horizontal bar chart)

---

### Tab 4 — 🤖 Bot Efficiency

AI-generated efficiency review for every registered agent.

**Generate Bar**:
| Control | Description |
|---------|-------------|
| Days selector | Last 30 days · 90 days · 1 year |
| "⚡ Generate Review" | Calls backend, returns AI-written per-agent recommendations |

**Fleet Summary Bar** (appears after generation):
| Field | Description |
|-------|-------------|
| Fleet Grade | Letter grade (A–F) for overall agent fleet |
| Agents Analyzed | Count of agents reviewed |
| Projected 30-Day Savings | Estimated savings if recommendations applied |
| Generated By | Model name used to generate the review |

**Efficiency Grid** — one card per agent:
| Element | Description |
|---------|-------------|
| Agent name + department | Header |
| Status badge | Current agent status |
| Efficiency grade | A–F letter |
| Call volume | Count with trend indicator |
| Top tier | Most-used model tier |
| Recommended actions | Bulleted list of suggested changes |
| Projected savings | Estimated monthly savings if applied |
| "Apply Recommendations" | Button to auto-apply tier bounds and pruning settings |

---

### Tab 5 — 📡 Agent Activity

Per-agent transaction detail with expandable rows.

**Filter Bar**:
| Filter | Options |
|--------|---------|
| Platform | All platforms |
| Agent | All agents |
| Department | All departments |
| Tier | All Tiers · Scout · Analyst · Advisor · Strategist |
| Reset | Clears all filters |

**Summary Bar** (4 chips):
| Chip | Value |
|------|-------|
| Total Calls | Count |
| Total Cost | Currency |
| Agents | Count |
| Platforms | Count |

**Agent Activity Table**:
| Column | Description |
|--------|-------------|
| (expand) | Click to show transaction log for this agent |
| Agent | Name |
| Platform | Source platform |
| Department | Department name |
| Status | Status badge |
| Calls | Call count |
| Total Cost | Currency |
| Avg/Call | Average cost per call |
| Flagship % | Percentage routed to premium tiers |
| Pruned % | Percentage of calls where pruning fired |
| Last Active | Relative timestamp |

Expanded rows show the full transaction log for that agent (timestamp, tier, tokens, cost, outcome).

---

### Tab 6 — 📈 ROI Calculator

Interactive calculator — no API call needed, all math runs client-side.

**Input Form** (left column):
| Field | Type | Range / Options |
|-------|------|-----------------|
| Industry | Dropdown | Custom · Insurance · Healthcare · Financial · Legal · Retail · Logistics · SaaS |
| Total AI Calls/Month | Slider + number | 1K – 1M |
| Routine Calls % | Slider + number | 10 – 95% |
| Avg Input Tokens/Call | Slider + number | 200 – 20,000 |
| Avg Output Tokens/Call | Slider + number | 50 – 5,000 |
| Current Model | Dropdown | Advisor · Strategist · Analyst |
| CostPilot Plan | Dropdown | $299 Starter · $599 Growth · $1,499 Business · $4,999 Enterprise |

**Results Panel** (right column):

**Payback Card** — green checkmark if savings exceed plan cost; red warning if not:
- "CostPilot pays for itself in X months" (or inverse message)

**KPI Cards Grid** (3 × 2):
| Card | Description |
|------|-------------|
| Monthly Without CostPilot | Baseline all-flagship cost |
| Monthly With CostPilot | Actual projected cost |
| Monthly Net Savings | Difference |
| Annual Net Savings | Monthly × 12 |
| Cost/Call Before | Per-call baseline |
| Cost/Call After | Per-call with CostPilot |

**Cost Breakdown Table**:
| Column | Description |
|--------|-------------|
| Tier | Scout (routine) · [Current model] (complex) · CostPilot Fee · Total · Net Savings |
| Calls | Count per tier |
| Cost/Call | Rate |
| Monthly Total | Dollar amount |

**Risk Value Box** (yellow accent):
- HIPAA/PII violation costs avoided
- Audit-ready AI call log
- Agent collision prevention
- Budget auto-throttle value

**Actions**: **Copy Summary** · **Export as PDF**

---

## 6. Policy & Rules (`policy.html`)

Two-column full-height layout. Controls the routing brain and compliance guard.

---

### Left Column — Routing Rules

**Token Routing Threshold**:

| Element | Description |
|---------|-------------|
| Slider | 150–2000 tokens; controls when payloads escalate to higher tiers |
| Value display | Large, blue-accented; updates live as slider moves |
| Range labels | "150 — aggressive escalation" / "2000 — minimal escalation" |
| "Save Threshold" | `PUT /api/routing/threshold` |

Logic: a payload must exceed **both** the token threshold **and** match a complexity keyword to escalate. Either condition alone is insufficient.

**Complexity Keywords**:

| Element | Description |
|---------|-------------|
| Keyword badges | Each keyword shown as dismissible badge |
| 🔒 badges | Core compliance keywords — cannot be removed |
| Text input + "Add" | `POST /api/routing/keywords` |
| Dismiss (×) | `DELETE /api/routing/keywords/{keyword}` |

---

### Right Column — Sensitive Term Library

Controls what happens when flagged words appear in a payload.

**Add Term Form**:
| Field | Options |
|-------|---------|
| Term or Phrase | Text input (comma-separated for bulk add) |
| Category | Legal · HIPAA/Health · Financial · HR · Custom |
| Action | Flag — mark high-risk · Escalate — force flagship tier · Block — reject entirely |

Submit: **"+ Add Term"** → `POST /api/sensitive-terms`

**Active Terms Table**:
| Column | Description |
|--------|-------------|
| Term | The word or phrase |
| Category | Color-coded category badge |
| Action | Flag · Escalate · Block (badge) |
| Scope | Which departments or agents this applies to |
| Remove | × button → `DELETE /api/sensitive-terms/{id}` |

**Action behaviors**:
- **Flag**: Request proceeds; risk level set to HIGH; event logged in audit
- **Escalate**: Request forced to Strategist (T4) regardless of routing score
- **Block**: Request rejected with `routing_decision: BLOCKED`; nothing sent to any AI model

---

## 7. Model Registry (`model-registry.html`)

Manages the four model tiers and their Live Mode configuration.

**Four tiers**:

| Tier | Label | Default Model | Use case |
|------|-------|---------------|----------|
| T1 | Scout | claude-haiku-4-5 | Routine, FAQ, simple queries |
| T2 | Analyst | claude-sonnet-4-6 | Structured data, moderate complexity |
| T3 | Advisor | claude-opus-4-6 | Legal, financial, multi-step reasoning |
| T4 | Strategist | claude-opus-4-6 (flagship) | Critical, regulated, executive decisions |

**Per-tier configuration**:
| Field | Description |
|-------|-------------|
| Model name | Dropdown of available models |
| Cost (input $/MTok) | Rate used for budget accounting |
| Cost (output $/MTok) | Rate used for budget accounting |
| Live Mode toggle | Enable real API calls for this tier |
| API key field | Provider key for live mode (masked) |

**Cascade rules**:
- Tier 2 (Analyst): cascades **UP** to Tier 3 (Advisor) if no Analyst model is set
- All other tiers: cascade **DOWN** to the next cheaper available tier

**Live Mode**:
When enabled on any tier, real API calls are made to the configured provider. When disabled, CostPilot returns a simulated response. Both modes produce identical billing records.

---

## 8. Onboarding Wizard (`onboarding.html`)

Guided 6-step setup for new CostPilot instances.

**Progress bar**: 6 steps — Company · Departments · Review · Launch · Connect · Voice Guard

---

### Step 1 — Company Setup

| Field | Type | Description |
|-------|------|-------------|
| Company Name | Text | Display name |
| Industry | Dropdown | Sets default complexity keyword presets |
| AI Provider | Card grid | OpenAI or Anthropic |
| Voice Guard | Toggle | Enable PII redaction layer (adds step 6) |
| Monthly AI Budget | Currency input | Total across all departments |

---

### Step 2 — Department Setup

| Element | Description |
|---------|-------------|
| Department list | Dynamic list with name + budget input + remove button |
| "+ Add Department" | Adds a new row |
| Budget summary | Allocated / Remaining / Total (live update as inputs change) |

Validation: sum of department budgets cannot exceed total budget from step 1.

---

### Step 3 — Review

Read-only summary of the configuration before launch:
- Company name, AI provider, total budget
- List of departments with their individual caps

---

### Step 4 — Launch

Spinner animation while backend creates departments and budget caps. After completion:

| Element | Description |
|---------|-------------|
| Step progress list | Shows each setup step as it completes |
| Platform picker | 6 platform cards: Salesforce · ServiceNow · HubSpot · Dynamics 365 · Zendesk · Custom |
| "Connect a Platform →" | Proceeds to step 5 |
| "Skip — Go to Dashboard" | Skips to dashboard |

---

### Step 5 — Connect a Platform

| Element | Description |
|---------|-------------|
| Platform grid | 6 selectable cards |
| Object/Record Type | Dropdown (tickets, crm_records, etc.) |
| Department | Dropdown |
| Agent Name | Text input |
| "Generate Setup Code →" | Produces platform-specific integration code |
| Code output area | Displays Apex, Flow, Business Rule, etc. with copy button |

---

### Step 6 — Voice Guard (if enabled)

| Element | Description |
|---------|-------------|
| How it works | 3-column diagram: Caller Speaks → CostPilot Redacts → Clean Text to AI |
| Live demo textarea | Paste or speak a transcript |
| "🎙 Speak" | Activates mic recording |
| "▶ Scan for PII" | Runs redaction on textarea content |
| "Load Example" | Fills textarea with sample PII-laden transcript |
| Result box | Shows redacted transcript + PII count + confidence + method |
| Integration note | Points to `POST /api/voice/transcript` endpoint |

---

## 9. Sandbox (`sandbox.html`)

Isolated test environment. All calls run the full pipeline but **nothing is written to the database** — no budget impact, no audit log entries, no spend accumulation.

**Notice banner**: "⚗ Sandbox mode active — all test calls run the full AI pipeline but are excluded from production KPIs, budgets, and audit logs."

---

### Session Stats Strip (5 columns, always visible)

| Stat | Description |
|------|-------------|
| Route Calls This Session | Count (sandbox only, resets on page reload) |
| Tokens Processed | Input + output tokens |
| Simulated Cost | Dollar amount (not deducted from any budget) |
| Tokens Saved (Pruning) | By context sweeper |
| Last Tier Routed | Tier badge for most recent call |

---

### Tool 1 — 🎙 Voice Guard (PII Redaction)

| Control | Description |
|---------|-------------|
| Textarea | Voice transcript input |
| "🎙 Speak" | Activates microphone |
| "▶ Scan for PII" | Runs PII detection on input |
| "Load Example" | Fills with sample PII transcript |
| Result: PII Found | Count (green) |
| Result: Confidence | Percentage |
| Result: Method | Detection approach used |
| Result: Clean Transcript | Redacted text in scrollable box |
| Result: Types Identified | Bulleted list of PII categories found |

---

### Tool 2 — ✂ Context-Pruning Sweeper

| Control | Description |
|---------|-------------|
| Textarea | Raw payload (email, ticket body, log) |
| "▶ Run Sweeper" | Prunes the input and shows results |
| "→ Send to Router" | Pipes pruned output directly into Tool 3 |
| Result: Before | Original token count |
| Result: After | Post-pruning count (green) |
| Result: Saved | Tokens removed (purple) |
| Result: Compression % | Reduction percentage |
| Cost line | "Saved $X by pruning Y tokens" |
| Cleaned Output | Pruned text in scrollable box |

---

### Tool 3 — ⚡ Token Router & Model Cascader

| Control | Description |
|---------|-------------|
| Textarea | Payload to route; supports prefix overrides: `[ANALYST]`, `[ADVISOR]`, `[STRATEGIST]` |
| Department | Dropdown — selects which department's rules and budget context apply |
| Payload Type | Text/Email · Code · Transcript |
| Auto-prune toggle | Run pruner before routing |
| Code notice | Shown when Code type selected: "Code lane active — pruner is bypassed. Secrets detection still runs..." |
| "▶ Route Payload" | Calls `POST /api/route` with `is_test: true` |
| "Load Code Example" | Fills textarea with sample code snippet |
| Result: Tier badge | Color-coded tier (Scout/Analyst/Advisor/Strategist) |
| Result: Decision | ROUTED · BLOCKED · THROTTLED · SKIPPED |
| Result: Sensitive | Badge if sensitive term triggered |
| Result: Reason | Plain-English explanation of the routing decision |
| Result: Model | Full model name |
| Result: Input/Output Tokens | Token counts |
| Result: Cost | Dollar amount (green) |
| Result: Pruning Saved | Token and dollar savings from pruner (purple) |
| Model Response | Simulated or live AI response in scrollable box |

**Payload type behaviors**:
| Type | Pruner | Secrets Detection |
|------|--------|-------------------|
| Text/Email | Runs (if auto-prune on) | No |
| Code | Skipped | Yes |
| Transcript | Runs | PII detection |

---

### Row 2 — Config Tools

Same controls as [Policy & Rules](#6-policy--rules-policyhtml) — changes here apply globally, not just to sandbox.

---

## 10. Live Demo Landing (`live-landing.html`)

**Purpose**: Shareable public-facing demo showing CostPilot in action at a fictional enterprise.

**Company**: Meridian Financial Group

| Element | Description |
|---------|-------------|
| Demo badge | "● DEMO" pill |
| Data source | `GET /api/dashboard` (refreshes every 15 seconds) |
| Last updated | "Updated HH:MM:SS" timestamp in footer |
| Pricing note | Shows actual model rates: claude-opus-4 $15/$75 · claude-sonnet $3/$15 · claude-haiku $0.25/$1.25 per MTok |

Layout and panels are identical to the Executive Summary Dashboard. All numbers are live from the production database (not static), but the company name and branding are demo-specific.

---

## 11. API Reference

**Base URL**: `/api`  
**Format**: JSON request/response  
**Auth**: No session auth; assumes single-tenant or reverse-proxy API key protection

---

### POST /api/route

The core routing endpoint. Every AI call goes through here.

**Request body**:
```json
{
  "text": "string — the payload to route",
  "department": "Support",
  "auto_prune": true,
  "agent_id": 42,
  "agent_name": "SupportBot-Alpha",
  "source_platform": "Salesforce",
  "voice_guard_processed": false,
  "min_tokens": 3,
  "is_test": false,
  "payload_type": "text"
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `text` | string | required | Raw payload |
| `department` | string | "Support" | Which budget to charge |
| `auto_prune` | bool | true | Run context pruner |
| `agent_id` | int | null | Calling agent's ID |
| `agent_name` | string | null | Agent name (auto-registers if unknown) |
| `source_platform` | string | null | Inferred from agent name if omitted |
| `voice_guard_processed` | bool | false | PII already redacted — skip keyword block |
| `min_tokens` | int | 3 | Skip routing if pruned payload is below this |
| `is_test` | bool | false | Sandbox mode — no DB writes |
| `payload_type` | string | "text" | "text" · "code" · "transcript" |

**Pipeline execution order**:
1. Resolve or auto-register agent
2. Detect payload type (code auto-detection if `payload_type = "text"`)
3. Check minimum token threshold → `SKIPPED` if below
4. Check department throttle → `THROTTLED` if over budget
5. Check sensitive terms → `BLOCKED` if action is "block"; forced escalation if "escalate"
6. Run context pruner (if `auto_prune = true`, not code, not agent-disabled)
7. Score complexity (token count + keyword match)
8. Select model tier (apply agent tier bounds)
9. Call model (live or simulated)
10. Record `TokenTransaction`, update `DepartmentBudget.current_spend`, write `AuditEvent`
11. Return response

**Response**:
```json
{
  "department": "Support",
  "complexity": "ROUTINE",
  "routing_decision": "ROUTED",
  "routing_reason": "Short payload, no complexity keywords matched — routed to Scout tier.",
  "matched_keywords": [],
  "model_tier": "Scout",
  "model_name": "Claude Haiku 4.5",
  "input_tokens": 312,
  "output_tokens": 84,
  "cost_usd": 0.000102,
  "simulated_response": "...",
  "was_pruned": true,
  "tokens_saved_by_pruning": 140,
  "pruning_cost_saved_usd": 0.000035,
  "total_cost_without_pruning": 0.000137,
  "budget_used_pct": 34.2,
  "budget_remaining_usd": 658.00,
  "was_throttled": false,
  "sensitive_term_triggered": false,
  "sensitive_term_action": null,
  "sensitive_term_matches": []
}
```

| Field | Description |
|-------|-------------|
| `routing_decision` | ROUTED · BLOCKED · THROTTLED · SKIPPED |
| `complexity` | ROUTINE · COMPLEX |
| `model_tier` | Scout · Analyst · Advisor · Strategist |
| `was_pruned` | Whether the context pruner ran and removed tokens |
| `sensitive_term_triggered` | True if any active term matched |
| `budget_used_pct` | Department's current spend as % of monthly cap |

---

### GET /api/dashboard

Single call that returns all data needed by the Executive and Operations dashboards.

**Response fields** (grouped):

**Spend**:
| Field | Type | Description |
|-------|------|-------------|
| `spend_today_usd` | float | Total spend since midnight UTC |
| `spend_month_usd` | float | Total spend this calendar month |

**Tokens**:
| Field | Type | Description |
|-------|------|-------------|
| `tokens_saved_today` | int | Pruning savings today |
| `tokens_saved_total` | int | All-time pruning savings |
| `pruning_savings_usd` | float | Pruning savings in dollars |

**Calls**:
| Field | Type | Description |
|-------|------|-------------|
| `calls_today` | int | Calls since midnight UTC |
| `total_calls` | int | All-time call count |
| `micro_calls` | int | Calls routed to Scout or Analyst |
| `flagship_calls` | int | Calls routed to Advisor or Strategist |
| `micro_pct` | float | Economy tier percentage |
| `scout_calls` | int | T1 calls |
| `analyst_calls` | int | T2 calls |
| `advisor_calls` | int | T3 calls |
| `strategist_calls` | int | T4 calls |

**Agents**:
| Field | Type | Description |
|-------|------|-------------|
| `agents_total` | int | All registered agents |
| `agents_active` | int | Currently active |
| `agents_locked` | int | Collision-locked |
| `agents_idle` | int | Available |

**Budgets**:
| Field | Type | Description |
|-------|------|-------------|
| `throttled_count` | int | Departments at or over cap |
| `total_cap_usd` | float | Sum of all department caps |
| `overall_budget_pct` | float | Total spend / total cap |
| `budget_summaries` | list | Per-department: `{department, monthly_cap_usd, current_spend_usd, used_pct, throttled, override_granted}` |

**Governance**:
| Field | Type | Description |
|-------|------|-------------|
| `blocked_count` | int | Requests blocked by sensitive term or policy |
| `escalated_count` | int | Requests force-escalated to flagship |
| `flagged_count` | int | Requests flagged (high-risk, not blocked) |
| `pii_count` | int | PII-related events |
| `collision_count` | int | Agent collision events |
| `compliance_events_total` | int | All governance events |

**ROI**:
| Field | Type | Description |
|-------|------|-------------|
| `projected_annual_savings` | float | Monthly total savings × 12 |
| `routing_savings_usd` | float | Cost diff vs. all-flagship routing |
| `total_savings_usd` | float | Routing + pruning savings |
| `routing_efficiency_pct` | float | % of calls on economy tiers |
| `cost_reduction_pct` | float | % reduction vs. all-flagship baseline |

**Recent events**:
| Field | Type | Description |
|-------|------|-------------|
| `recent_audits` | list | Last 5 audit events: `{id, event_type, department, risk_level, timestamp}` |

---

### Agent Endpoints `/api/agents`

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/agents` | List all agents; add `?include_archived=true` to include soft-deleted |
| GET | `/api/agents/{id}` | Single agent detail |
| POST | `/api/agents/register` | Register new agent (body: `RegisterRequest`) |
| DELETE | `/api/agents/{id}` | Hard-delete agent |
| POST | `/api/agents/{id}/archive` | Soft-delete (preserves spend history) |
| POST | `/api/agents/{id}/unarchive` | Restore archived agent |
| POST | `/api/agents/{id}/release` | Release a collision-locked agent back to idle |
| PATCH | `/api/agents/{id}/tier-bounds` | Set min/max routing tier (body: `{min_tier: 1, max_tier: 4}`) |
| PATCH | `/api/agents/{id}/pruning` | Enable/disable pruning (body: `{enabled: true}`) |
| GET | `/api/agents/spend` | Per-agent spend aggregation (all-time) |
| POST | `/api/agents/claim` | Traffic Cop: agent claims exclusive write lock on a record |
| POST | `/api/agents/simulate-collision` | Demo: force two agents to collide on the same record |

**Tier Bounds**: `min_tier` and `max_tier` are integers 1–4. The router will not route below `min_tier` or above `max_tier` for this agent, regardless of complexity score.

**Pruning toggle**: `enabled: false` skips **both** code auto-detection **and** the pruning pipeline entirely for this agent. "Off means off."

---

### Report Endpoints `/api/reports`

All report endpoints accept `?days={N}` (default 30, max 365).

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/reports/savings` | Savings report: pruning + model downgrade |
| GET | `/api/reports/risk` | Risk report: sensitive term hits, blocks, collisions |
| GET | `/api/reports/departments` | Department scorecard |
| GET | `/api/reports/timeline` | Daily spend + call volume bucketed by day |

**Savings report response** (key fields):
| Field | Description |
|-------|-------------|
| `total_saved_usd` | Total savings for period |
| `pruning_saved_usd` | Context pruning savings |
| `downgrade_saved_usd` | Model routing savings |
| `cost_if_no_fage_usd` | Hypothetical all-flagship cost |
| `timeline` | Daily: `{date, cost, tokens_saved, calls, flagship, micro}` |

**Risk report response** (key fields):
| Field | Description |
|-------|-------------|
| `total_events` | Governance events in period |
| `critical` / `high` / `medium` / `low` | Counts by severity |
| `blocked` | Outright rejected requests |
| `term_library` | `{total, block, escalate}` |
| `recent_events` | Last 25 high-stakes events |

**Department scorecard response** (per department):
| Field | Description |
|-------|-------------|
| `total_calls` | Calls in period |
| `micro_pct` | Economy routing % |
| `total_cost_usd` | Spend |
| `pruning_saved_usd` | Pruning savings |
| `budget_used_pct` | % of monthly cap used |
| `throttled` | Currently throttled flag |

---

### Policy Endpoints

**Routing thresholds and keywords**:

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/routing/threshold` | Get current token threshold |
| PUT | `/api/routing/threshold` | Update threshold (body: `{threshold: 500}`) |
| GET | `/api/routing/keywords` | List complexity keywords |
| POST | `/api/routing/keywords` | Add keyword (body: `{keyword: "acquisition"}`) |
| DELETE | `/api/routing/keywords/{keyword}` | Remove keyword (core 🔒 terms rejected) |

**Sensitive terms**:

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/sensitive-terms` | List all active terms |
| POST | `/api/sensitive-terms` | Add term (body: `{term, category, action, scope}`) |
| DELETE | `/api/sensitive-terms/{id}` | Remove term |

---

### Audit Endpoints `/api/audit`

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/audit` | List audit events; filters: `?dept=`, `?risk=`, `?days=`, `?blocked_only=true` |
| GET | `/api/audit/{id}` | Single audit event detail |
| POST | `/api/audit/export/csv` | Download filtered events as CSV |
| POST | `/api/audit/export/pdf` | Download filtered events as PDF |

---

### Other Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check: `{version, demo_mode, model_mode, provider}` |
| GET | `/api/budgets` | List all department budgets |
| PATCH | `/api/budgets/{department}` | Update monthly cap or throttle settings |
| POST | `/api/voice/transcript` | Voice Guard: submit transcript for PII redaction |

---

## 12. Data Models

### RegisteredAgent

| Column | Type | Description |
|--------|------|-------------|
| id | Integer (PK) | Auto-increment |
| name | String | Agent display name |
| department | String | Owning department |
| source_platform | String | Salesforce · ServiceNow · HubSpot · etc. |
| permissions | String | read · read+write · read+write+delete |
| target_table | String | CRM object being written |
| target_record_id | String (nullable) | Current record being worked on |
| status | String | idle · active · locked · queued |
| collision_policy | String | lock · queue · skip |
| locked_at | DateTime (nullable) | When collision lock was acquired |
| lock_reason | String (nullable) | Description of the collision |
| archived | Boolean | Soft-delete flag |
| min_tier | Integer (nullable) | Minimum routing tier (1–4) |
| max_tier | Integer (nullable) | Maximum routing tier (1–4) |
| pruning_enabled | Boolean | Default true; false = skip pruner entirely |
| last_used_at | DateTime (nullable) | Last API call timestamp |

---

### TokenTransaction

| Column | Type | Description |
|--------|------|-------------|
| id | Integer (PK) | Auto-increment |
| department | String | Charged department |
| source_platform | String | Origin platform |
| agent_id | Integer (FK, nullable) | Calling agent |
| model_tier | String | Scout · Analyst · Advisor · Strategist |
| input_tokens | Integer | Tokens in |
| output_tokens | Integer | Tokens out |
| cost_usd | Float | Actual cost |
| timestamp | DateTime | UTC timestamp |
| routing_reason | String | Plain-English explanation |
| was_pruned | Boolean | Pruner ran flag |
| tokens_saved | Integer | Tokens removed by pruner |

---

### DepartmentBudget

| Column | Type | Description |
|--------|------|-------------|
| department | String (PK) | Department name |
| monthly_cap_usd | Float | Monthly spending limit |
| current_spend_usd | Float | Spend so far this month (resets monthly) |
| throttled | Boolean | Currently throttling flag |
| throttle_tier | String (nullable) | Tier cap when throttled |
| override_granted | Boolean | Admin override — allow spend over cap |
| raw_payload_logging_enabled | Boolean | Store raw prompt text in audit log |
| raw_retention_days | Integer | How long to retain raw payloads |

---

### AuditEvent

| Column | Type | Description |
|--------|------|-------------|
| id | Integer (PK) | Auto-increment |
| event_type | String | routing · block · throttle · collision · flag · escalate |
| timestamp | DateTime | UTC timestamp |
| department | String | Department |
| risk_level | String | critical · high · medium · low |
| routing_decision | String | ROUTED · BLOCKED · THROTTLED · SKIPPED |
| routing_reason | String | Plain-English explanation |
| prompt_payload | String (nullable) | Truncated input (if logging enabled) |
| decision_outcome | String | Final outcome description |
| model_tier | String | Tier used |
| agent_id | Integer (nullable) | Calling agent |
| matched_keywords | String | JSON list of complexity keywords matched |
| cost_usd | Float | Call cost |
| tokens_saved | Integer | Pruning savings |
| raw_tokens | Integer (nullable) | Pre-pruning token count |
| clean_tokens | Integer (nullable) | Post-pruning token count |
| raw_payload | Text (nullable) | Full prompt if raw logging enabled |

---

### SensitiveTerm

| Column | Type | Description |
|--------|------|-------------|
| id | Integer (PK) | Auto-increment |
| term | String | Word or phrase |
| category | String | legal · hipaa · financial · hr · custom |
| action | String | flag · escalate · block |
| scope | String | all · or specific department/agent |
| created_at | DateTime | UTC timestamp |

---

## 13. UI Patterns & Conventions

### Color System

| Variable | Color | Usage |
|----------|-------|-------|
| `--accent-green` | Green | Savings, efficiency, Scout/Analyst, economy routing, PRUNE ON |
| `--accent` | Blue | Advisor tier, primary actions, highlights |
| `--accent-yellow` | Yellow | Warnings, Strategist tier, budget concerns |
| `--accent-red` | Red | Blocks, critical risk, PII, errors, throttled |
| `--accent-purple` | Purple | Pruning savings, secondary actions |
| `--text-muted` | Gray | Labels, secondary info, PRUNE OFF state |
| `--border` | Dark gray | Table borders, input outlines |

### Number Formatting

| Type | Format | Example |
|------|--------|---------|
| Currency | $X.XX (sub-dollar) or $X,XXX.XX | $0.0034 · $1,247.83 |
| Large numbers | X.XK or X.XM | 4.2K · 1.3M |
| Percentages | X.X% | 72.4% |
| Tokens | Comma-separated integers | 1,247,832 |
| Timestamps | ISO 8601 or locale time | 2026-05-29T14:32:00Z |
| Model tiers | Capitalized name + color badge | Scout (green) |

### Interaction Patterns

| Pattern | Behavior |
|---------|----------|
| PRUNE ON / PRUNE OFF button | Green border/text when on; muted border when off; row outline flash on toggle |
| Tier Bounds dropdowns | Save on change; router respects bounds on next call |
| Collapsible panels | Click header to expand/collapse; state resets on page reload |
| Draggable report cards | Grip handle (⠿) in header; drag to reorder |
| Budget bars | Green < 70%, yellow 70–90%, red > 90% |
| Alerts | Full-width banners; red for blocked events; yellow for warnings |
| Auto-refresh | 15s on Executive Dashboard and Live Landing; 10s on Savings today counter |

### Common Table Features

| Feature | Description |
|---------|-------------|
| Sortable headers | Click column header to sort |
| Expandable rows | Click row to see transaction detail (Agent Activity tab) |
| Export | CSV and PDF from most report tables |
| Pagination | Not used — most tables load all results with virtual scroll |
| Empty state | Placeholder text shown when no data matches filters |

---

## 14. Architecture Quick Reference

```
Your CRM/Platform
       │
       ▼
POST /api/route
       │
  ┌────▼────────────────────────────────────────────────┐
  │                 Routing Pipeline                     │
  │                                                      │
  │  1. Resolve Agent (auto-register if new)             │
  │  2. Detect Payload Type (text / code / transcript)   │
  │  3. Min Token Check  ──► SKIPPED if < threshold      │
  │  4. Budget Throttle  ──► THROTTLED if over cap       │
  │  5. Sensitive Terms  ──► BLOCKED / ESCALATED         │
  │  6. Context Pruner   ──► strip redundant tokens      │
  │     (skipped if agent.pruning_enabled = False)       │
  │  7. Complexity Score ──► ROUTINE or COMPLEX          │
  │  8. Tier Selection   ──► apply agent tier bounds     │
  │  9. Model Call       ──► live API or simulation      │
  │ 10. Record + Audit   ──► DB + JSONL append-only log  │
  └─────────────────────────────────────────────────────┘
       │
       ▼
    Response
  (tier, cost, savings, budget remaining, simulated response)
```

**Database tables**: `registered_agents` · `token_transactions` · `department_budgets` · `audit_events` · `sensitive_terms`

**Migrations**: All column additions use `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` in `scripts/release.sh` (runs on every Heroku deploy — idempotent).

**Dual-write audit**: Every governance event writes to both the `audit_events` DB table (queryable) and an append-only JSONL file (immutable backup).

**Demo mode**: Controlled by `DEMO_MODE=true` env var. When set, `scripts/release.sh` auto-populates enterprise demo data (Meridian Financial Group) on every deploy.

---

*CostPilot v0.1.0 — Full Feature Reference*  
*For integration support or questions, see the Technical Buyer Guide in `fage/docs/COSTPILOT_TECHNICAL_BUYER_GUIDE.md`.*
