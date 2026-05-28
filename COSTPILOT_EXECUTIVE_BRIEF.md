# CostPilot
## Navigate AI Spend with Precision

**Executive Product Brief — Confidential**

---

## The Problem

Artificial intelligence is no longer experimental. It is operational infrastructure — and it is expensive.

Enterprise organizations are deploying AI agents across customer service, legal, finance, operations, and product. Each of those agents makes dozens to thousands of model calls per day. Every call costs money. Most organizations have no visibility into what those calls cost, no controls to govern which model is used for what, and no mechanism to stop a single department from blowing past its budget before anyone notices.

The result: AI spend scales with usage, not with outcomes. Companies pay flagship model rates for tasks that a fraction-of-the-cost model could handle. They send bloated, noisy payloads — filled with email chains, boilerplate, and signatures — and pay for every token of junk. And when something goes wrong — a compliance violation, a data leak, a bad AI decision — there is no record of what happened or why.

**CostPilot solves this.** It sits between your organization and your AI models, governing every request in real time: routing it to the right model, stripping the waste, enforcing budgets, protecting sensitive data, and writing an immutable record of every decision.

---

## What CostPilot Is

CostPilot is an AI governance and cost optimization middleware platform. It does not replace your AI models. It does not change how your teams work. It sits in front of every AI call your organization makes and makes three determinations in milliseconds:

1. **Is this request safe to send?** (PII check, sensitive term scan, policy enforcement)
2. **Which model should handle it?** (complexity scoring, budget status, department rules)
3. **How much of it actually needs to be sent?** (context pruning, token reduction)

Every decision is logged. Every dollar is tracked. Every department operates within its approved limit.

---

## How It Works — End to End

### Step 1 — Interception

Every AI request — whether it originates from a customer service agent, an internal automation, a voice call, or a CRM integration — is routed through CostPilot before it reaches any AI model. Nothing bypasses the layer.

### Step 2 — PII & Sensitive Term Scan

Before any content is processed, CostPilot runs two checks:

**Voice Guard** strips personally identifiable information from voice call transcripts in real time. It catches Social Security numbers, credit card numbers, bank routing numbers, dates of birth, and phone numbers — even when spoken with hesitations, filler words, or interruptions. It uses a dual-layer engine: a trigger-phrase state machine that catches intent before numbers are spoken, and Presidio AI pattern recognition for everything else. The clean transcript is what gets passed forward. The raw transcript with PII never reaches a model.

**Sensitive Term Library** scans every payload against a company-defined list of flagged terms — HIPAA keywords, legal triggers, financial terms, or custom terms your compliance team defines. When a match is found, CostPilot can flag it in the audit log, automatically escalate it to a higher-tier model for compliance review, or block the request entirely. The action is configurable per term.

If a request is blocked, it stops here. Zero tokens consumed. Zero cost. Full audit entry written.

### Step 3 — Context Pruning

Once a request passes the safety scan, CostPilot's Context Sweeper processes the payload. It strips everything that adds tokens but contributes no meaning to the AI's task:

- Email reply chains and forwarded threads
- HTML markup and formatting tags
- Email signatures and legal disclaimers
- Repeated boilerplate and headers
- Filler content from voice transcripts

**Why this matters financially:** AI models charge per token. A token is roughly four characters of text. If you send a 2,000-token support email that contains 1,200 tokens of reply chain and signature, you are paying for 1,200 tokens that do nothing. At scale, this waste is enormous.

**The math:** Enterprise payloads — email threads, case notes, documents — are typically 42% to 82% noise by token count. CostPilot eliminates that noise before the request is priced. Those tokens are never sent, never billed.

| Payload Type | Tokens Eliminated | Example |
|---|---|---|
| Short / clean requests | 20% | Simple API calls, brief queries |
| Medium (some threads) | 42% | Support tickets, internal messages |
| Large (document-heavy) | 62% | Case files, multi-turn threads |
| Heavy (HTML, long chains) | 82% | Email threads, logs, raw transcripts |

### Step 4 — Complexity Scoring & Model Routing

CostPilot scores every payload for complexity and routes it to the appropriate model tier. There are four tiers:

| Tier | Model Class | Use Case | Relative Cost |
|---|---|---|---|
| T1 — Scout | Micro (e.g. Claude Haiku) | Routine tasks, lookups, summaries | Baseline |
| T2 — Analyst | Mid-tier | Moderate reasoning, structured data | ~3–4× Scout |
| T3 — Advisor | Flagship (e.g. Claude Sonnet) | Complex reasoning, multi-step tasks | ~10–15× Scout |
| T4 — Strategist | Premium (e.g. Claude Opus) | Highest-stakes decisions, compliance | ~40–60× Scout |

**The core insight:** Most enterprise AI calls are routine. Status checks, ticket categorization, simple summarization, data lookups — these do not need a flagship model. They are landing on flagship models today because no one told them not to. CostPilot scores each request and sends it to the cheapest model that can handle it competently.

Research and live usage data show that approximately **65% of calls currently hitting flagship models can be safely re-routed to Scout tier** without any degradation in output quality for the task at hand.

**Additional routing rules:**
- If a department has hit its monthly budget cap, all its calls automatically drop to Scout tier so work continues — at a fraction of the cost — until a supervisor grants an override
- If a payload contains a flagged sensitive term that requires compliance review, it automatically escalates to Advisor or Strategist regardless of complexity score
- Every routing decision — the score, the model selected, the reason — is logged

### Step 5 — Execution & Response

The request goes to the selected model. The response comes back. CostPilot passes it through to the requesting application. From the end user's perspective, nothing has changed. The agent gets its answer. The employee gets their result.

### Step 6 — Audit & Governance Record

Every request — routed, escalated, blocked, or throttled — generates an immutable audit entry:

- Timestamp
- Department and agent ID
- Payload hash (not the raw content)
- Model selected and why
- Tokens sent, tokens pruned
- Cost of the call
- Any policy flags triggered
- Final disposition

This record is written once and cannot be modified. It is the compliance black box. If your organization faces a legal dispute, a regulatory audit, or an internal review, you have a timestamped, tamper-proof record of every AI decision your company made.

---

## Core Features

### Real-Time Cost Dashboard

The CostPilot dashboard gives leadership a live view of AI spend across the entire organization:

- **Total spend today** — updated in real time, to the fraction of a cent
- **Tokens saved by pruning** — running counter of tokens eliminated before billing
- **Active agents** — every AI digital worker connected to CostPilot, with live status
- **Throttled departments** — departments currently operating in budget-protection mode
- **Blocked requests** — requests stopped before reaching any model, with zero cost

### Department Budget Controls

Every department gets its own monthly AI spend cap. CostPilot tracks spend against that cap in real time. The budget bar fills from green to yellow to red as spend accumulates.

When a department hits its cap:
- All calls automatically drop to Scout tier — work does not stop
- The department is flagged on the dashboard
- A supervisor can grant an override to restore full access

Caps can be adjusted, reset, or overridden at any time from the dashboard. No engineering work required.

### Agent Registry

Every AI agent — customer service bots, internal automations, CRM integrations, voice agents — registers with CostPilot automatically on first contact. No manual configuration. The registry shows:

- Agent name and ID
- Current status (idle, active, locked, queued)
- Department assignment
- Real-time activity

**Collision detection:** If two agents attempt to update the same record simultaneously, CostPilot's Traffic Cop detects the conflict, locks both agents, and flags it for supervisor resolution. No silent overwrites. No data corruption.

### Sensitive Term Library

Compliance and legal teams can maintain a library of flagged terms directly in CostPilot — no engineering involvement. Terms can be:

- **Flagged** — request proceeds, audit entry written, compliance team notified
- **Escalated** — request automatically routed to flagship model for careful handling
- **Blocked** — request stopped entirely, zero tokens consumed, reason logged

Categories include HIPAA keywords, legal holds, financial triggers, PII patterns, and any custom terms the organization defines.

### Voice Guard

Voice call centers are a significant compliance risk. Customers speak sensitive information — account numbers, Social Security numbers, payment details — and that information flows into AI systems for processing.

CostPilot's Voice Guard intercepts call transcripts before they reach any model and strips the sensitive content. The AI sees the clean transcript. The raw spoken PII never enters the AI pipeline.

The engine handles real-world speech: numbers spoken with pauses, filler words ("um, it's four... four... one..."), corrections, and interruptions. It catches what a simple regex would miss.

### Savings Calculator

Before any commitment, CostPilot can show a prospect their exact projected savings using their own data. Three paths:

**API Key** — connect directly to your Anthropic or OpenAI account. CostPilot queries read-only usage endpoints from the browser — the key never touches CostPilot's servers. Real usage data, real savings projection, in under 60 seconds.

**CSV Upload** — export your usage file from the Anthropic Console or OpenAI Platform and upload it. No key required. CostPilot parses your actual token volumes, model mix, and spend and projects the savings against both cost levers.

**Manual Entry** — enter monthly spend, call volume, payload size, and complexity mix. CostPilot projects savings from those inputs. Results in under 30 seconds.

All three paths feed the same savings engine and produce a detailed breakdown of projected savings by lever.

### Live Demo Environment

CostPilot includes a live demonstration environment that allows prospective customers and business partners to submit real AI requests and watch the governance layer work in real time. They can select a platform (Salesforce, ServiceNow, HubSpot, Zendesk, Microsoft Dynamics, or custom), choose a scenario type, and submit a case. The result panel shows exactly what happened: which model was selected, why, how many tokens were pruned, what the request cost, and what it would have cost without CostPilot.

---

## The Financial Case

### Why the savings are real

There are two independent cost reduction levers. They stack.

**Lever 1 — Model Downgrade**

Current state: AI calls land on whatever model the developer hardcoded or the default the API provides. For most organizations, that is a flagship model for every request.

CostPilot state: Every request is scored. Routine requests — approximately 65% of the total — route to Scout tier.

The price difference between flagship and Scout:
- Input tokens: $3.00 per million (flagship) vs. $0.80 per million (Scout) — **3.75× cheaper**
- Output tokens: $15.00 per million (flagship) vs. $4.00 per million (Scout) — **3.75× cheaper**

For every 1,000 calls re-routed from flagship to Scout, the savings on input alone are $2.20 per million tokens — before pruning.

**Lever 2 — Context Pruning**

Current state: The full payload — noise and all — goes to the model. Every token is billed.

CostPilot state: Noise is stripped before billing. Those tokens are never sent.

Pruning eliminates 20% to 82% of input tokens depending on payload type. The savings are calculated at flagship rates — because that is what the organization would have paid.

### The Uber-Scale Illustration

Uber has publicly reported a monthly AI token usage approaching **2 trillion tokens**. Here is what that volume costs at current list rates and what CostPilot saves:

**Current monthly spend (estimated at list rates):**

| Model | Role | Tokens/Month | Monthly Cost |
|---|---|---|---|
| Claude Haiku | Routing, lookups, summaries | ~1.26T | ~$3.0M |
| Claude Sonnet | Complex reasoning, operations | ~0.54T | ~$4.9M |
| Claude Opus | Compliance, legal, edge cases | ~0.20T | ~$6.4M |
| **Total** | | **~2.0T tokens** | **~$14.3M/month** |

**CostPilot savings projection:**

*Downgrade savings:* 65% of Sonnet and Opus calls re-routed to Haiku at the rate differential of $2.20–$11.20 per million input tokens → **~$4.5M/month**

*Pruning savings:* Enterprise payloads at 62% average noise rate, priced at flagship input rates → **~$2.8M/month**

**Total projected savings: ~$7.3M/month — a 51% reduction on a $14.3M bill.**

Annualized: **$87.6M in savings** on an operation that currently spends $171.6M per year on AI model costs alone.

These are conservative projections. They do not account for blocked requests (zero tokens, zero cost) or the compounding effect of budget throttling preventing overage charges.

---

## Security & Compliance

- **No model training on your data** — CostPilot does not send your payloads to any third party beyond the AI model provider you have already authorized
- **PII never reaches models** — Voice Guard and sensitive term detection operate before any model call is made
- **Immutable audit log** — written once, never modified, exportable to CSV or PDF for legal holds and regulatory audits
- **API keys handled client-side** — the Savings Calculator runs API key queries directly from the user's browser; keys never transit CostPilot servers
- **On-premises deployment available** — CostPilot can be deployed entirely within your infrastructure, with no external data transmission beyond your existing model provider connections
- **Role-based access** — department managers, supervisors, and administrators have differentiated access levels

---

## Deployment

CostPilot is designed to be operational in hours, not months.

**Cloud deployment:** CostPilot runs as a hosted service. Connect via API. Point your existing AI calls through the CostPilot endpoint. No infrastructure changes required on your side.

**On-premises deployment:** For organizations with strict data residency or compliance requirements, CostPilot deploys fully within your existing infrastructure. It runs on standard server hardware, connects to your internal network, and routes to your authorized model providers. No data leaves your environment.

**Integration:** CostPilot connects to Salesforce, ServiceNow, HubSpot, Zendesk, Microsoft Dynamics 365, and custom platforms via standard API. Most integrations are live within a single business day.

**No rip-and-replace:** CostPilot does not replace your AI models, your CRM, or your workflows. It inserts between what you have and makes it governed, auditable, and significantly cheaper.

---

## Summary

| Capability | What It Does | Who It Serves |
|---|---|---|
| Model Router | Routes every AI call to the right-cost model | CFO, CTO |
| Context Pruner | Strips token waste before billing | CFO |
| Budget Controls | Per-department monthly caps with auto-throttle | CFO, COO |
| Agent Registry | Tracks every AI agent, detects conflicts | CTO, COO |
| Voice Guard | Strips PII from call transcripts before AI sees them | CLO, CCO, CTO |
| Sensitive Term Library | Flags, escalates, or blocks policy-sensitive content | CLO, CCO |
| Audit Log | Immutable record of every AI decision | CLO, Board |
| Governance Event Stream | Live feed of all AI activity across the organization | CTO, COO |
| Savings Calculator | Proves ROI before purchase, using the prospect's own data | CFO, CEO |
| Live Demo Environment | Real-time demonstration of the full governance layer | All |

---

**CostPilot — Navigate AI Spend with Precision**

*For demonstrations, access requests, or partnership inquiries, contact the CostPilot team.*
