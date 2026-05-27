# FAGE — FinOps Agentlake & Governance Engine
## Podcast Reference Document — Full Functionality & Enterprise Use Cases
### May 2026

---

## HOW TO USE THIS DOCUMENT

This document is written for an AI podcast host or co-host. It contains:
- Plain-English explanations of every feature built
- The problems each feature solves at the enterprise level
- Real numbers and demo scenarios
- Talking points, analogies, and example conversations
- Enterprise use cases across industries

You can ask follow-up questions, request deeper explanations of any section, or ask the AI to roleplay as a FAGE expert being interviewed.

---

## PART 1 — WHAT IS FAGE?

### The 30-Second Pitch

FAGE stands for **FinOps Agentlake and Governance Engine**. It's an AI middleware layer — a control plane that sits between a company's existing business tools and the AI models those tools call. Every AI request flows through FAGE before reaching a model like GPT or Claude.

FAGE does five things automatically on every single request:
1. **Cleans** the payload to remove junk before it hits the AI (saves money)
2. **Scans** it for sensitive data and policy violations (prevents liability)
3. **Routes** it to the right model tier based on complexity (saves more money)
4. **Tracks** the cost against the requesting department's monthly budget (prevents overruns)
5. **Logs** the decision with a plain-English rationale (creates the audit trail)

It's not an AI. It doesn't generate answers. It governs the systems that do.

---

### The Problem It Solves

When a company starts deploying AI agents at scale — customer support bots, sales enrichment agents, operations automators — three things go wrong fast:

**Problem 1: The Bill Shock**
There's no visibility into AI spend until the invoice arrives. One department runs an expensive batch job over a weekend. Legal sends 500 contract reviews to GPT-4o. The bill is $40,000. Nobody approved it. Nobody saw it coming. Finance has no breakdown by department, agent, or purpose.

**Problem 2: The Data Corruption**
Multiple AI bots run simultaneously against the same records. One Salesforce bot is updating a customer's account health score while another is rewriting their contract value. The last one to save wins. The first one's work is silently destroyed. In a company with 12 active AI agents, this happens multiple times per day and nobody catches it until data integrity audits — if they happen at all.

**Problem 3: The Compliance Gap**
The auditor walks in and asks: "Show me every AI decision that touched a customer record in the last 90 days." The answer is silence. There is no log. Nobody knows what the AI saw, what it decided, or why. In regulated industries — financial services, healthcare, insurance — this is not just embarrassing. It's a regulatory violation.

FAGE closes all three gaps simultaneously without requiring any changes to the tools teams already use.

---

### The Architecture in Plain English

Think of FAGE like a building's security desk. Every package (AI request) coming into the building passes through the security desk first. The desk:
- Checks what's in the package (scans for sensitive data)
- Decides which floor it goes to (routes to the right model tier)
- Logs who sent it, when, and what was in it (audit trail)
- Tracks the department's monthly delivery budget
- Refuses delivery if the package violates building policy

The building's residents (Salesforce, ServiceNow, HubSpot, custom bots) don't need to be rewired. They just address their packages to the new security desk instead of directly to the AI floor.

---

## PART 2 — THE SEVEN CORE ENGINES

### Engine 1: Context Pruning Sweeper

**What it does:**
Before any text is sent to an AI model — which charges by the word — FAGE strips out everything that isn't meaningful content. This includes:
- HTML tags and inline CSS from copy-pasted web content
- Email headers (From, To, Date, MIME-Version, X-Mailer)
- Reply chain history (everything after "-----Original Message-----")
- Corporate legal disclaimer blocks (CONFIDENTIALITY NOTICE, GDPR boilerplate)
- Email signatures (name, title, company, phone, address, website)
- Excessive whitespace and blank lines

**The real numbers:**
On a typical corporate support email (HTML formatted, with legal disclaimer and reply chain), FAGE achieves 60–65% compression. A payload that costs $0.006 to process costs $0.002 after pruning. Same quality response. 66% of the cost.

**At enterprise scale:**
A company processing 10,000 AI calls per day at a 63% average compression rate saves approximately $6,000–$8,000 per year from this single engine alone — before any other optimization.

**The sandbox demo:**
FAGE has a live Sandbox page where anyone can paste any raw text and watch the pruner work in real time. It shows before/after token counts, compression percentage, and the exact dollar cost avoided for each model tier.

---

### Engine 2: Four-Tier Token Router & Model Cascader

**What it does:**
After pruning, FAGE evaluates the request's complexity and routes it to the right AI model tier automatically. The system uses four named tiers:

| Tier | Name | Intended Use | Relative Cost |
|------|------|-------------|---------------|
| 1 | Scout | FAQs, simple lookups, routine tasks | Cheapest |
| 2 | Analyst | Standard business tasks, balanced workloads | Moderate |
| 3 | Advisor | Complex analysis, sensitive documents, deep reasoning | Premium |
| 4 | Strategist | Mission-critical decisions, board-level analysis | Most Expensive |

**Why this matters:**
Without a router, every request goes to the same model. A customer asking "what are your business hours?" costs the same as a compliance attorney analyzing a GDPR data transfer agreement. That's irrational — and expensive. Smart routing fixes this automatically.

**Routing logic:**
The router applies rules in priority order:
1. Is the department over its monthly budget cap? → Force Scout regardless of complexity
2. Does the payload contain sensitive/legal keywords? → Escalate to Advisor or Strategist
3. Is the payload long (150+ tokens after pruning)? → Route to Analyst or Advisor
4. Otherwise → Scout

**Real cost impact:**
At 500 calls per day across a department, routing everything to Advisor instead of letting FAGE cascade correctly costs roughly $4,500/month more than necessary. Smart routing reduces that by 60–90% for routine traffic.

**The Model Registry:**
Administrators can configure exactly which AI model maps to each tier. Scout might be Claude Haiku one week and GPT-4o mini the next — no code change required, just a UI update in the Model Registry. This means the company can always use the most cost-effective model at each tier as the AI provider market evolves.

---

### Engine 3: Departmental Budget Allocator & Auto-Throttle

**What it does:**
Every AI call is tagged to the department that triggered it. Each department has a monthly spending cap. FAGE tracks spend in real time and enforces caps automatically.

**The three states:**
- **Healthy (green)** — Under 80% of monthly cap. Full access to all tiers.
- **Warning (yellow)** — 80–99% of cap. Supervisors are alerted.
- **Throttled (red)** — At or over 100% of cap. All requests from this department are automatically downgraded to Scout until a supervisor grants an override or the month resets.

**Supervisor controls:**
A supervisor can, from the dashboard in seconds:
- Set or adjust a department's monthly cap
- Grant a temporary override (restores full model access, logged to audit trail)
- Revoke an override
- Reset the month (zero out spend counter for a new billing period)

**Enterprise example — Financial Services:**
The Risk & Compliance department is running a month-end audit. They're generating hundreds of complex routing decisions. Their $500 cap is hit on the 22nd. With FAGE, a compliance manager clicks "Grant Override" — adds $300 of additional capacity with a reason code — and the audit continues. That override event is logged permanently: who granted it, when, why, and how much it ultimately cost. Finance sees it in the next report.

Without FAGE, there's no cap. The month-end audit runs unchecked. The bill arrives in 30 days.

---

### Engine 4: Sensitive Term Library & PII Blocker

**What it does:**
Every incoming request is scanned against a configurable library of sensitive terms and data patterns before it reaches any AI model. Two actions are possible:

- **Block** → Request rejected entirely. No AI cost incurred. Audit event created. The caller gets an error. Nothing reaches the AI.
- **Escalate** → Request allowed, but forced to a higher model tier for more careful handling.

**Pre-built categories:**
- Financial PII: credit card numbers, CVV codes, routing numbers, bank account numbers
- Identity PII: Social Security Numbers, passport numbers, dates of birth
- Healthcare: patient IDs, diagnosis codes, prescription details
- Legal triggers: lawsuit, litigation, breach of contract, regulatory action, attorney-client
- HR sensitive: termination, harassment, discrimination, wrongful dismissal

**How it works in practice:**
A customer support agent in Salesforce creates a case. The customer's message says: "I need to discuss my Social Security number being compromised." FAGE intercepts the case text before it goes to the AI. The SSN keyword triggers a Block action. The request is rejected, an audit event is logged as CRITICAL risk, and the support team's AI assistant never sees the data. The support rep is shown an error — and the company never has a liability event.

**Custom terms:**
Administrators can add custom terms at any time through the Policy & Rules page. No code change, no deployment. A new term takes effect immediately on the next request. Companies can add industry-specific terms: proprietary code names, competitor names, internal project names, regulatory frameworks specific to their jurisdiction.

---

### Engine 5: Agentlake Registry & Concurrency Traffic Cop

**What it does:**
Maintains a live inventory of every AI agent registered in the system — which platform it came from, which department owns it, what data it's allowed to access, and whether it's currently idle, active, or locked.

**Auto-registration:**
When a new agent calls FAGE for the first time, it is automatically added to the registry. No manual setup. The system captures the agent name, source platform, department, and permissions from the API call metadata.

**The collision problem:**
In an enterprise running multiple AI agents simultaneously, write collisions are an invisible and constant threat. Two agents both read a customer record. Both compute an updated value. Both try to write back. The last one to finish saves, silently destroying the first one's work. This is called a silent data corruption event. It happens without error, without log, and without detection — until data audits reveal inconsistencies weeks or months later.

**How FAGE prevents it:**
Before writing to any record, an agent must claim it via FAGE's API. FAGE checks whether any other active agent already holds a claim on that exact table and record ID combination. If the record is clear, the claim is granted and the agent proceeds. If another agent already holds it, FAGE immediately locks both agents — neither writes anything — and fires a collision alert on the dashboard. A supervisor reviews the locked agents and releases them in the correct order.

**Enterprise example — Salesforce + ServiceNow running simultaneously:**
A Salesforce Sales Bot is updating opportunity close probability on Account ID 8821. A ServiceNow Ops Bot starts updating the same account's contract status. FAGE detects both agents are targeting Account:8821, locks both instantly, fires a HIGH-risk audit event, and surfaces the collision on the Live Operations Center. A supervisor sees it within seconds, decides which update takes priority, releases that agent first, and logs the resolution. Zero data corruption. Full audit trail.

---

### Engine 6: AI Decision Auditor — The Black Box Recorder

**What it does:**
Every significant AI decision is logged permanently. Not just that it happened — but why. The auditor captures a complete, human-readable record of each high-stakes event.

**What triggers an audit event:**
- Any request routed to Advisor or Strategist tier (complex)
- Any request blocked by the keyword/PII scanner
- Any request throttled by the budget enforcer
- Any agent collision detected
- Any supervisor override granted or revoked

**What each audit record captures:**
1. Timestamp (precise to milliseconds)
2. Department, agent name, source platform
3. Event type (ROUTING, DECISION, BLOCK, LOCK, OVERRIDE)
4. Risk level (LOW / MEDIUM / HIGH / CRITICAL)
5. The exact prompt payload (first 2,000 characters — what the AI actually saw)
6. A plain-English rationale statement
7. A frozen budget snapshot at the moment of decision (cap, spend, % used, throttle state)
8. Dollar cost of the call

**Sample auto-generated rationale (real output):**
> "ADVISOR MODEL INVOKED. Payload routed to the premium model tier after complexity analysis for the Support department. Trigger: High-risk keyword detected: 'legal'. High-risk keywords present: 'legal', 'contract'. Budget position at time of decision: 71.3% used ($142.51 of $200.00 cap). Call cost: $0.003405. Decision: premium routing is warranted given the legal/compliance signals present."

**The compliance conversation:**
The auditor's output answers three questions that every regulated enterprise struggles with:
1. "What did the AI see?" → The prompt payload snapshot
2. "What did it decide and why?" → The rationale statement
3. "What did it cost and who authorized it?" → The budget snapshot and routing reason

**Dual storage:**
Every event is written to both the relational database (queryable, filterable) and an append-only JSONL flat file (one JSON object per line, downloadable at any time). The flat file format is compatible with SIEM tools, Splunk, AWS CloudWatch, and standard compliance aggregators.

---

### Engine 7: The Live Operations Center

**What it does:**
A real-time simulation dashboard showing FAGE operating at enterprise scale. Unlike the main dashboard (which reflects real API calls), the Live Operations Center runs a continuous automated simulation of a large company's AI operations — generating routing decisions, budget pressure, agent collisions, compliance events, and audit log entries at machine speed.

**The demo scenario — Meridian Financial Group:**
The live demo represents a fictional enterprise: Meridian Financial Group. Four departments. Twelve AI agents. A 30-day operation period with $8,742 in routing savings demonstrated. The simulation runs continuously: agents fire requests, the budget bars fill, the routing feed scrolls, blocked events trigger red alerts, collisions lock agents, and the audit log grows — all live.

**What the Live Operations Center shows:**
- KPI strip: Live Spend, FAGE Savings, Blocked Requests, Throttled Departments
- Model Tier Distribution: real-time bar showing Scout/Analyst/Advisor/Strategist split
- Agent Status Grid: all 12 agents with real-time state (active, idle, locked)
- Department Budget Bars: spend vs. cap per department, color-coded
- Routing Decision Feed: scrolling live log of every routing decision with department, tier, model, cost, and rationale
- AI Decision Audit Log: every high-stakes event as it happens, expandable
- Governance Event Stream: filterable event ticker (blocked, collision, throttled, complex, pruning, routine)

**Why this matters for demos:**
Most enterprise software demos show static screenshots or staged data. The FAGE Live Operations Center runs continuously and shows a realistic picture of what AI governance looks like at scale — the constant stream of routing decisions, the budget pressure building, the occasional compliance event firing, the collision being detected and resolved. It makes the abstract concrete.

---

## PART 3 — THE FULL PLATFORM TOUR

### Page 1: Main Dashboard (index.html)

The main dashboard is the command center for a real FAGE deployment connected to live APIs. It shows real data from real API calls.

**KPI Strip (6 cards):**
- Total Spend Today
- Tier Routing Saved (cost difference between actual model used vs. sending everything to Strategist)
- FAGE Total Savings (pruning + routing combined)
- Active Agents
- Throttled Departments
- Blocked Requests (in last 24 hours)

**Secondary Stats Bar:**
Model tier distribution, budget utilization %, month-to-date spend, total call volume.

**Panels (collapsible and resizable):**
- Department Budgets — per-department spend bars with supervisor controls
- Agentlake Registry — live agent table with lock/release controls
- Routing Decision Feed — scrolling log of routing decisions
- AI Decision Audit Log — filterable, expandable audit events

**Blocked Request Alert System:**
When blocked requests exist in the last 24 hours, a pulsing red alert banner appears at the top of the dashboard. It shows the count and a "Review Blocked Events" button that filters the audit log to blocked-only. This is the first thing a compliance officer sees when they open the dashboard in the morning.

---

### Page 2: Live Operations Center (live.html)

The enterprise simulation dashboard. Runs automatically. Shows what FAGE looks like at scale without requiring a real production environment.

Used for: sales demos, investor presentations, executive briefings, conference presentations, proof-of-concept walkthroughs.

---

### Page 3: Reports (reports.html + live-reports.html)

Six-tab reporting suite. Two versions: one connected to real API data (reports.html), one with mock enterprise data for demos (live-reports.html / Meridian Financial Group).

**Tab 1 — Savings Report:**
- What FAGE actually saved vs. what it would have cost without governance
- Side-by-side: "With FAGE" vs. "Without FAGE" cost projections
- Pruning savings breakdown by type (HTML stripping, signature removal, reply chains, etc.)
- Model routing savings breakdown by tier
- Daily cost trend line

**Tab 2 — Risk & Compliance:**
- Blocked request volume by category (PII, Legal, Financial, HR)
- Audit event timeline — when compliance events spiked and why
- Block vs. escalate ratio
- High-risk event log summary

**Tab 3 — Departments:**
- Per-department scorecard: total calls, economy vs. premium model split, spend, pruning savings, budget health rating
- Which departments are the most AI-intensive, most compliant, most cost-efficient

**Tab 4 — Bot Efficiency:**
- Per-agent performance: call volume, average cost per call, premium model usage %, pruning rate
- Identifies underperforming agents (high cost, low efficiency) and high-efficiency agents (high pruning rate, smart routing)

**Tab 5 — Agent Activity:**
- Full call log per agent
- Filterable by department, platform, model tier, date range
- 20 named date presets (Last 7 Days, Last 30 Days, This Quarter, Last Year, etc.) plus custom range inputs

**Tab 6 — ROI Calculator:**
- Input: number of agents, daily call volume, current per-call cost, department count
- Output: projected annual savings with FAGE, payback period, cost reduction percentage
- Used in executive presentations to quantify the business case before deployment

---

### Page 4: Policy & Rules (policy.html)

Configuration center for the governance rules that FAGE enforces.

**Routing & Budget Thresholds:**
- Complexity threshold slider — at what token count does FAGE escalate to a higher tier?
- Warning threshold — at what budget % does the yellow warning trigger?
- Throttle threshold — at what budget % does auto-throttling engage?
- Max context tokens — hard ceiling on any single AI request

**Sensitive Term Library:**
- Full CRUD table of blocked and escalated terms
- Add custom terms with action (Block or Escalate), category, and severity
- Pre-seeded with 15+ enterprise-grade terms across Financial, HIPAA, Legal, HR categories
- Changes take effect immediately — no deployment, no restart

---

### Page 5: Model Registry (models.html)

The catalog of every AI model FAGE can route to, organized by tier.

**What's managed:**
- Display name and provider (OpenAI, Anthropic, Azure, Google, Mistral, Custom)
- API Model ID (the exact identifier passed to the API)
- Tier assignment (Scout/Analyst/Advisor/Strategist)
- Input and output cost per million tokens
- Business unit assignment (global, or restricted to one department)
- Enabled/disabled status
- Default model flag per tier

**Department-specific routing:**
A model can be restricted to a specific business unit. Example: Legal requires all requests to route through an Azure-hosted GPT-4o instance (for data residency compliance). FAGE routes Legal department requests to the Azure model automatically. All other departments hit the standard OpenAI endpoint. No code change. UI-only configuration.

**Why this matters:**
As the AI model market evolves (new models launch quarterly), companies need to update their routing strategy without engineering work. The Model Registry makes this a configuration task, not a development task.

---

### Page 6: Sandbox (sandbox.html)

A testing environment for trying FAGE's core engines in isolation.

**Pruner Test:**
Paste any text. Click Run. See token count before and after, compression percentage, and dollar cost avoided at each model tier.

**Voice Guard Test:**
Test voice input governance — submit audio or transcribed text and see how FAGE would classify and handle it.

**Router Test:**
Paste a payload, select a department, submit. See the full routing decision: complexity score, tier assigned, model selected, real token counts, cost, cost without FAGE, savings.

Used for: onboarding new IT teams, validating configuration changes before applying to production, customer demos, developer integration testing.

---

### Page 7: Connect & Setup (onboarding.html)

The wizard-style setup interface for connecting FAGE to real systems.

- API key management (OpenAI, Anthropic, Azure OpenAI)
- Provider selection
- Model mode toggle (simulated vs. live)
- Integration connection setup for external platforms

---

## PART 4 — ENTERPRISE USE CASES

### Use Case 1: Financial Services — Regulatory Compliance

**Company type:** Regional bank, insurance carrier, or investment management firm

**The problem:**
These companies operate under SOC 2, PCI-DSS, GDPR, and sometimes HIPAA. Any AI model interaction that touches customer financial data creates a potential regulatory exposure. Today, AI is being used for loan application summarization, claims processing, customer service — and there is zero audit trail.

**What FAGE provides:**
- Every request involving financial data is scanned for PII before reaching any AI model
- Credit card numbers, routing numbers, SSNs, and account numbers are blocked automatically
- Every complex routing decision is logged with a plain-English rationale that a compliance officer can read and export for regulatory review
- The JSONL audit file is compatible with the bank's existing SIEM infrastructure
- When the OCC or CFPB auditor walks in, the compliance team downloads the audit file and answers every question

**Specific scenario:**
A mortgage processor uses an AI assistant to summarize loan applications. FAGE routes short, simple summaries to Scout (cheap). When an application includes legal language (easement disputes, prior foreclosure, legal judgment), FAGE detects the legal keywords and routes to Advisor. If the loan officer's summary accidentally includes the applicant's SSN, FAGE blocks the request entirely, logs a CRITICAL audit event, and the SSN never reaches the AI model — or any AI provider's servers.

---

### Use Case 2: Healthcare — HIPAA Compliance & Data Minimization

**Company type:** Hospital system, insurance payer, healthcare technology company

**The problem:**
HIPAA requires that PHI (Protected Health Information) be handled with documented controls. AI models are increasingly used for clinical documentation, insurance prior authorization, and patient communication. There is currently no systematic control preventing PHI from being sent to external AI APIs without authorization.

**What FAGE provides:**
- Pre-built HIPAA term library blocks patient identifiers, diagnosis codes, and prescription details
- Context pruning removes boilerplate from clinical documents before they reach the AI, reducing the footprint of data exposed
- Every interaction is logged with a risk level — CRITICAL for any HIPAA-adjacent event
- The audit trail demonstrates to HHS auditors that controls exist and operate automatically

**Specific scenario:**
A patient care coordinator uses an AI assistant to draft prior authorization letters. The patient's chart note is pasted in. FAGE strips the letter template boilerplate (reduces tokens by 40%), scans for PHI identifiers, escalates any request containing diagnosis codes to Advisor tier for careful handling, and logs every decision. The healthcare organization can demonstrate HIPAA-compliant AI use without redesigning their workflow.

---

### Use Case 3: Enterprise SaaS — Multi-Tenant Cost Management

**Company type:** Large software company with multiple product teams using AI

**The problem:**
Engineering, Product, Support, Sales, and Marketing all use AI tools. Each team has different usage patterns and different budgets. There's no per-team visibility. The AI bill is a single line item. Finance can't allocate cost to cost centers. Teams don't know how much they're spending.

**What FAGE provides:**
- Per-department budget caps enforced automatically
- Real-time per-team spend dashboards
- Auto-throttling prevents any one team from running an uncapped batch job
- Department Scorecard in Reports shows per-team AI efficiency (who's routing smart, who's sending everything to the expensive model)
- The ROI Calculator quantifies the governance value for the CFO

**Specific scenario:**
Marketing runs a campaign personalization job that sends 50,000 requests over a weekend. Without FAGE, this could consume the entire company's AI budget. With FAGE, Marketing's $500/month cap is hit on Friday night. All subsequent requests are automatically throttled to Scout. The job continues but at Scout pricing. On Monday, a director grants a $200 override with a reason code. Finance sees the override in the audit log. The campaign finishes. The bill is $180 instead of $3,000.

---

### Use Case 4: Salesforce-Heavy Organization — Agent Governance at CRM Scale

**Company type:** Sales-driven enterprise with Salesforce as the system of record

**The problem:**
Sales teams are deploying AI bots to enrich leads, score accounts, draft outreach, and update CRM records. Multiple bots run against the same Salesforce objects simultaneously. Data quality degrades. Nobody can prove which AI generated which update or why. Sales leadership has no AI spend visibility at the team or territory level.

**What FAGE provides (live and working):**
FAGE has a working Salesforce integration. A Salesforce Flow triggers on Case or Opportunity create/update. An Apex class sends the record text to FAGE's API. FAGE processes the request and returns a response within 2–3 seconds. The dashboard reflects the call in real time.

- Sales department budget tracked separately from Support, Operations, etc.
- Every Salesforce AI call logged with the Case ID or Opportunity ID it touched
- Agent collisions prevented — if two Salesforce bots target the same account simultaneously, FAGE locks both and alerts
- Complex sales scenarios (contract negotiations, legal escalations) automatically routed to higher model tiers
- Simple prospect lookups routed to Scout — 90% cheaper

---

### Use Case 5: Regulated Manufacturing — Operational AI Governance

**Company type:** Aerospace, pharmaceutical, or defense manufacturer

**The problem:**
AI is being used for quality control documentation, compliance reporting, and process optimization. These industries have FDA, FAA, or DoD audit requirements. The company must demonstrate that AI-assisted decisions were reviewed, logged, and made within policy.

**What FAGE provides:**
- Custom sensitive term library with regulatory terms specific to the industry (FDA, 21 CFR, FAA Order, MIL-SPEC, etc.)
- All complex routing decisions logged with the full context of what triggered them
- The audit file is downloadable in JSONL format and can be imported directly into quality management systems
- Operational teams can run AI on process documents without risking unauthorized disclosure of proprietary formulations or specifications (Block action on custom IP terms)

---

## PART 5 — THE BUSINESS CASE

### What FAGE Saves (Quantified)

**Savings Source 1 — Context Pruning**
Average compression on enterprise email/document payloads: 40–65%
At 10,000 calls/day at Advisor tier: approximately $6,000–$12,000/year saved
At 50,000 calls/day across a large enterprise: $30,000–$60,000/year

**Savings Source 2 — Tier Routing**
Routing 70% of requests to Scout instead of Advisor: ~85% cost reduction on those calls
Example: 1,000 calls/day, 700 routed to Scout instead of Advisor → saves approximately $18,000/year at current model pricing

**Savings Source 3 — Budget Caps Preventing Overruns**
One uncapped batch job can consume $5,000–$50,000 of AI budget in hours
FAGE prevents this by design — not by policy memo

**Savings Source 4 — Compliance Liability Avoided**
One GDPR or HIPAA enforcement action averages $1.5M–$4.5M in fines
One data breach involving AI-processed PII averages $4.9M in total costs (IBM 2024)
FAGE's PII blocker eliminates the most common vector: accidental exposure via AI prompt

### The CFO Summary

**Without FAGE:**
- AI costs are invisible until the invoice arrives
- No department knows what they're spending or why
- One complex legal query costs the same as 60 routine support tickets
- A bot sending customer SSNs to an AI provider is indistinguishable from a routine request
- When the auditor asks "show me every AI decision from last quarter" — there is no answer

**With FAGE:**
- Every dollar of AI spend is tracked by department, agent, and call
- Budget caps auto-enforce — departments cannot overspend without a supervisor override that is itself logged
- Smart routing cuts per-call costs by 60–90% for routine requests
- PII and sensitive data are blocked before they ever reach an AI model
- Every decision has a timestamp, plain-English rationale, and dollar amount — audit-ready at any time

---

## PART 6 — TECHNICAL REALITY

### What's Actually Built and Running

FAGE is a working, deployed application — not a concept or a slide deck. As of May 2026:

- Deployed live on Heroku
- Python 3.13 backend, FastAPI framework
- PostgreSQL database (production), SQLite (local development)
- Real OpenAI and Anthropic API integration (live mode makes actual API calls)
- Salesforce integration working end-to-end (Apex class + Flow + FAGE API)
- 7 HTML pages with full interactive functionality
- 18 backend API route files
- 9 core engine files
- 19 frontend JavaScript modules
- 6 CSS files
- Automated enterprise demo data seeder (Meridian Financial Group scenario)
- Full ? Guide tour system on every page (10-step interactive walkthrough per page)
- 161 git commits deployed to production

### What Comes Next

**Near-term build:**
- Real-time push notifications (WebSocket) instead of 15-second polling
- Email and Slack alerts when budgets hit warning thresholds
- Role-based access control (supervisor vs. read-only vs. admin)
- Expanded chart library — 30-day rolling cost graphs, burn rate projections

**Integration roadmap:**
- ServiceNow (REST integration / MID Server)
- HubSpot (webhook workflow actions)
- Microsoft Dynamics 365 (Power Automate connectors)
- Zendesk (Apps framework + webhooks)
- SAP (Integration Suite)
- Microsoft Teams / Slack (notification layer)

**Enterprise product path:**
FAGE is architected as a middleware service. Every enterprise platform connects via its native integration mechanism — an Apex class for Salesforce, a Power Automate action for Dynamics, a workflow trigger for HubSpot. The core FAGE engine requires no modification per platform. Only the connector layer changes. This means one FAGE deployment can govern AI across an entire enterprise technology stack simultaneously.

---

## PART 7 — PODCAST TALKING POINTS & QUESTIONS

**To open:**
- "Most companies right now are in a race to deploy AI agents. What's the hidden cost of doing that without governance?"
- "Walk me through what actually happens when two AI bots fight over the same database record."
- "What does a GDPR auditor actually ask about when it comes to AI decisions?"

**On cost:**
- "How much is the average enterprise wasting on AI because they don't have tier routing?"
- "What's the most expensive single mistake FAGE's pruning engine prevents?"
- "If I'm a CFO who just got my first AI bill and it's double what I expected — what do I do?"

**On compliance:**
- "What's the difference between an AI governance policy and an AI governance system?"
- "Has any company actually been fined for what an AI model saw in a prompt?"
- "What does 'audit-ready AI' actually mean in practice?"

**On the technology:**
- "Why middleware instead of just building governance into the AI tools themselves?"
- "What's the hardest problem FAGE solves — the cost problem, the compliance problem, or the collision problem?"
- "How does the four-tier routing model compare to how most companies handle model selection today?"

**On the future:**
- "When every major enterprise platform has 10 AI agents running simultaneously, what does the world look like without something like FAGE?"
- "Is AI governance a feature or a product category?"
- "Who owns AI governance in the enterprise — IT, Legal, Finance, or the AI team?"

---

## APPENDIX — KEY NUMBERS FOR THE PODCAST

| Metric | Value |
|--------|-------|
| Average pruning compression | 40–65% per payload |
| Token savings on a typical enterprise email | ~566 tokens (63.5% of 891) |
| Cost of a Scout call vs. Strategist call | ~95% cheaper |
| Daily savings at 10,000 calls + 63% pruning | ~$17–$33 |
| Annual pruning savings at that volume | $6,000–$12,000 |
| Average GDPR enforcement fine | $1.5M–$4.5M |
| Average cost of an AI-related data breach | $4.9M (IBM 2024) |
| Time to add a new sensitive term | < 60 seconds, no code |
| Time to swap an AI model in the registry | < 60 seconds, no code |
| Time for a Salesforce callout to appear in FAGE | ~2–3 seconds |
| FAGE production commits deployed | 161 |
| Pages in the platform | 7 |
| API routes | 15+ |
| Supported AI providers | OpenAI, Anthropic, Azure OpenAI |

---

*Document prepared May 2026. FAGE v161 deployed. Live at: https://fage-engine-21cb49fe4806.herokuapp.com/*
