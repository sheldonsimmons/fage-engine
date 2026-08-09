# CostPilot — Technical Buyer Guide

**Audience:** CTOs, VP Engineering, and Technical Leads evaluating CostPilot.

**Purpose:** This document walks through every step of the CostPilot request pipeline in execution order — explaining what happens, why it happens, the latency cost of each step, and what it means for your team's operations, compliance posture, and AI spend.

---

## Table of Contents

1. [How the Pipeline Works](#how-the-pipeline-works)
2. [Step 1 — Sensitive Term & PII Governance](#step-1--sensitive-term--pii-governance)
3. [Step 2 — Context Pruner](#step-2--context-pruner)
4. [Step 3 — Token Router](#step-3--token-router)
5. [Step 4 — Agent Resolution](#step-4--agent-resolution)
6. [Step 5 — Budget Enforcement](#step-5--budget-enforcement)
7. [Step 6 — Audit Logger](#step-6--audit-logger)
8. [Step 7 — Model Registry & Live Mode](#step-7--model-registry--live-mode)
9. [Pipeline Latency Summary](#pipeline-latency-summary)
10. [Integration Checklist](#integration-checklist)

---

## How the Pipeline Works

Every AI request your application sends passes through CostPilot's middleware before reaching Anthropic or OpenAI. The pipeline executes in a fixed order — each step is designed to be fast, non-blocking, and safe to fail without dropping the request.

```
Your Application
      │
      ▼
┌─────────────────────────────────────────────┐
│           CostPilot Middleware              │
│                                             │
│  1. Sensitive Term & PII Check  ◄────────── Block / Escalate / Flag
│  2. Context Pruner (prose only)             │
│  3. Token Router                            │
│  4. Agent Resolution                        │
│  5. Model Call                              │
│  6. Budget Enforcement                      │
│  7. Audit Logger                            │
└─────────────────────────────────────────────┘
      │
      ▼
Anthropic / OpenAI / On-Premise Model
```

**The integration is a single endpoint change.** Instead of calling Anthropic or OpenAI directly, your application POSTs to `/api/route`. CostPilot handles everything downstream and returns the model response along with routing metadata.

The pipeline runs on raw, unmodified text at Step 1 — before anything touches the payload. Steps that can fail gracefully (pruner, registry lookup) have documented fallback behavior and never drop a request. Steps that must not fail silently (sensitive term block, audit write) surface errors explicitly.

---

## Step 1 — Sensitive Term & PII Governance

### What It Does

Before any other processing occurs, CostPilot scans the raw request payload against a library of sensitive terms. This is the first gate in the pipeline — it runs on the original, unmodified text so nothing is missed.

Each term in the library has one of three enforcement actions:

| Action | What Happens | HTTP Response |
|--------|-------------|---------------|
| **BLOCK** | Request is rejected immediately. No model is called. No cost is incurred. | `451 Unavailable for Legal Reasons` |
| **ESCALATE** | Request is allowed but forced to a senior model tier (Advisor/Sonnet or above). | `200 OK` — normal response, elevated tier |
| **FLAG** | Request proceeds normally. Event is logged for review. | `200 OK` — no change to routing |

### How It Works

CostPilot maintains a `SensitiveTerm` table in its database. On every request, the check runs as a case-insensitive substring scan across the payload. When a term matches:

- **BLOCK:** An audit event is written, then the request is rejected with HTTP 451. The response body includes which term triggered the block, its category, and all matched terms. The model is never called. No cost is charged.

- **ESCALATE:** A flag is set that forces the routing engine to treat this request as `COMPLEX` regardless of its actual token count or complexity score. This routes it to Tier 3 (Advisor) or higher. The caller receives a normal `200` response — the elevation is transparent.

- **FLAG:** The match is recorded in the audit log. Routing continues unchanged.

### Real-World Example

A customer support agent receives a message: *"I need help with my social security number update."*

CostPilot detects `social security` — a BLOCK-level term in the `hipaa` category. The request is rejected before it reaches any model. Your application receives:

```json
{
  "error": "BLOCKED",
  "reason": "Request contains a blocked sensitive term: 'social security'",
  "category": "hipaa",
  "matches": ["social security", "social security number"]
}
```

No data was sent to Anthropic or OpenAI. No token was billed. The event is logged with full context for your compliance team.

### Default Term Library

CostPilot ships with a pre-built library covering the most common compliance categories:

| Category | Examples | Default Action |
|----------|----------|---------------|
| **HIPAA** | `ssn`, `social security`, `date of birth`, `medical record`, `diagnosis code` | BLOCK |
| **Financial** | `credit card`, `cvv`, `routing number`, `bank account` | BLOCK |
| **Legal** | `lawsuit`, `litigation`, `gdpr`, `breach of contract`, `regulatory` | ESCALATE |
| **HR** | `termination`, `harassment`, `discrimination` | ESCALATE |

Four terms are permanently protected and cannot be removed: `fraud`, `breach`, `gdpr`, `hipaa`. All other terms can be modified or deleted.

### Customization

Your team can add, modify, or remove terms at any time via the API — no deployment required:

```http
POST /api/keywords
{
  "term": "patient_id",
  "category": "hipaa",
  "action": "block",
  "department": "Support"     // optional — scope to one department, or omit for global
}
```

Supported categories: `legal`, `hipaa`, `financial`, `hr`, `custom`
Supported actions: `block`, `escalate`, `flag`
Terms can be scoped to a specific department or applied globally.

### Latency Impact

The sensitive term check adds **< 5ms** to every request. It is a pure in-memory substring scan against a pre-loaded term list — there is no additional model call, no external API call, and no network hop. If the check triggers a BLOCK, the total request time is under 10ms (no model is called at all).

### Safety Guarantees

- Runs on **raw, unmodified text** — before pruning, before routing, before anything else touches the payload
- A BLOCK is a hard stop — no code path exists that sends a blocked request to a model
- Audit events for BLOCK and ESCALATE are written **before** the request is rejected, so the record exists even if the caller retries
- Term check failures (e.g. database error) do **not** silently allow the request through — the pipeline surfaces the error

---

## Step 2 — Context Pruner

### What It Does

Before a request reaches the router or any AI model, CostPilot strips everything from the payload that doesn't contribute to the model's answer. In enterprise environments — where AI inputs typically come from CRM tickets, email threads, and support cases — a significant portion of every payload is noise: email headers, reply chains, legal disclaimers, HTML markup, and corporate signatures.

The pruner removes that noise automatically. The model sees only the content that matters. You are only billed for tokens that matter.

### Payload Type Detection First

Before pruning runs, CostPilot inspects the payload to determine whether it is **prose or code**. Pruning is safe for email and document text but would corrupt code payloads — stripping a function signature or collapsing whitespace in a Python block would break the meaning entirely.

Detection checks the first 2,000 characters for these signals (first match wins):

| Signal | Example | Confidence |
|--------|---------|------------|
| PEM / private key header | `-----BEGIN PRIVATE KEY-----` | Very high |
| Shebang line | `#!/usr/bin/env python` | High |
| Fenced code block | ` ```python ` | High |
| Function / class / import definition | `def route():`, `SELECT * FROM` | Medium |
| High code-character density | `{}[]();=><` ratio > 5% | Medium |

If the payload is detected as code, **pruning is skipped entirely** for that request. The payload passes through to the router unchanged. This decision is logged in the audit trail with the reason.

### What Gets Stripped

When the payload is prose, six filters run in sequence:

**1. HTML & Inline CSS**
Removes all HTML tags, `<style>` blocks, `<script>` blocks, and decodes HTML entities. Common in CRM tickets that originate from web forms or email clients that embed rich text.

**2. Email Headers**
Strips `From:`, `To:`, `Date:`, `MIME-Version:`, `Content-Type:`, DKIM signatures, spam scores, and over 20 other header field types. Also removes ticket system boilerplate such as *"Your ticket has been received"* and *"Do not reply to this email."*

**3. Reply Chain History**
Truncates the payload at the first reply-chain marker — `--- Original Message ---`, `On [date] wrote:`, long underscores used as dividers, etc. When a support agent forwards a 12-email thread, only the most recent message reaches the model.

**4. Legal Disclaimers**
Removes standard corporate disclaimer blocks — `CONFIDENTIALITY NOTICE`, *"This email is intended for the named recipient only"*, copyright lines, privacy policy references, and GDPR/CCPA footers. These blocks can be 200–400 tokens on their own and add nothing to the model's task.

**5. Email Signatures**
Removes signature blocks — name/title/company patterns, phone numbers, physical addresses, and website URLs.

**6. Whitespace Collapse**
Normalizes excessive blank lines (3+ consecutive newlines reduced to one), strips trailing and leading whitespace from each line.

### What the Pruner Returns

Every pruned payload produces a detailed result used by the router and audit logger:

```json
{
  "cleaned_text":    "...",
  "raw_tokens":      847,
  "clean_tokens":    312,
  "tokens_saved":    535,
  "compression_pct": 63.2,
  "filters_applied": ["strip_html", "strip_reply_chains", "strip_legal_disclaimers"]
}
```

`tokens_saved` directly drives the **pruning savings** figure on the CostPilot dashboard — the dollar amount of tokens stripped before billing.

### Real-World Example

A Salesforce case arrives containing a 6-email thread. The raw payload is 847 tokens:

- 180 tokens: email headers and MIME boundaries
- 310 tokens: 5 previous replies in the thread
- 120 tokens: legal disclaimer footer
- 237 tokens: actual customer issue

After pruning: **237 tokens** — a 72% reduction. At Sonnet pricing ($3/MTok input), that's $0.0018 saved on a single request. Across 50,000 requests per month, that's **$90/month from pruning alone**, before any routing savings.

### Latency Impact

The pruner runs entirely in-process using compiled regular expressions. There is no external call, no model invocation, and no database query. Typical execution time is **10–50ms** depending on payload size, running faster than the network round-trip to any model API.

### Safety Guarantees

- If payload type detection misclassifies a payload (rare), the worst outcome is that the model receives slightly more tokens than necessary — the payload is never corrupted in a way that changes its meaning for prose
- Code payloads are never pruned — the detection is conservative by design, defaulting to "code" when there is ambiguity
- Pruning never removes content that could be a sensitive term — the sensitive term check in Step 1 always runs on the **raw, unpruned text**
- If the pruner raises an exception for any reason, the pipeline falls back to the raw text — requests are never dropped due to a pruning failure

---

## Step 3 — Token Router

### What It Does

After pruning, the cleaned payload reaches the routing engine. The router's job is to decide which model tier this specific request deserves — no more, no less. Sending every request to the most capable model is the default behavior of a direct API integration and the single largest source of unnecessary AI spend in enterprise deployments.

CostPilot replaces that default with a decision engine that evaluates each request on two dimensions: **what it's asking for** (keyword signals) and **how much it contains** (token volume). The result is routed to one of four tiers.

### The Four Tiers

| Tier | Default Name | Maps To | Typical Use |
|------|-------------|---------|-------------|
| **1** | Scout | Claude Haiku / GPT-3.5-equivalent | Simple lookups, single-question answers, status checks |
| **2** | Analyst | Claude Sonnet (mid) | Moderate complexity — one keyword or longer payload |
| **3** | Advisor | Claude Sonnet | Full complexity — keyword AND volume signal both triggered |
| **4** | Strategist | Claude Opus | Escalated by sensitive term policy or explicit override |

Tiers 1 and 2 are **economy**. Tiers 3 and 4 are **premium**. The routing engine's goal is to serve the request at the cheapest tier that can do the job correctly.

**Tier names are configurable.** The labels Scout / Analyst / Advisor / Strategist are defaults — your team can rename all four to match your own terminology (e.g. "Micro / Standard / Pro / Enterprise") from the Admin Panel. The underlying tier integers and all routing logic are unchanged; only the display labels update. Changes are reflected immediately across all dashboards, audit logs, and reports.

```http
PATCH /api/routing-config/tier-names
{ "tier_1": "Micro", "tier_2": "Standard", "tier_3": "Pro", "tier_4": "Enterprise" }
```

### How the Routing Decision Is Made

The router evaluates four conditions in priority order:

**1. Throttled department**
If the department has hit its monthly budget cap, all requests are capped at the department's configured throttle tier (typically Tier 1 — Scout). The original complexity score is computed but overridden. Routing decision: `THROTTLED`.

**2. Explicit tier tag**
If the payload starts with `[scout]`, `[analyst]`, `[advisor]`, or `[strategist]` (searched within the first 100 characters), that tag is stripped from the text and the specified tier is used directly. No complexity scoring runs. This is how your application can hard-pin a request to a tier when it already knows what's needed. Routing decision: `OVERRIDE`.

**3. Sensitive term escalation**
If the sensitive term check (Step 1) matched an ESCALATE-level term, the router is instructed to treat the request as `force_complex=True`. This bypasses the complexity scorer entirely and routes to Tier 4 — Strategist. Routing decision: `COMPLEX`.

**4. Complexity scoring**
If none of the above apply, the complexity scorer runs on the cleaned text. Two signals are evaluated:

- **Keyword match** — a case-insensitive scan against 48 complexity keywords covering legal, compliance, operational, and analytical domains (e.g., `legal`, `audit`, `fraud`, `gdpr`, `analyze`, `root cause`, `migration`, `incident`, `recommend`)
- **Token volume** — estimated token count compared against a configurable threshold (default: **250 tokens**)

The decision logic:

| Keyword match | Over token threshold | Result | Tier |
|---------------|---------------------|--------|------|
| Yes | Yes | COMPLEX | 3 — Advisor |
| Yes | No | MODERATE | 2 — Analyst |
| No | Yes | MODERATE | 2 — Analyst |
| No | No | ROUTINE | 1 — Scout |

### Model Registry Lookup

Once a tier number is selected, the router looks up which specific model serves that tier. CostPilot does not hardcode model names into the routing logic — it reads from a **Model Registry** table in the database, which your team controls.

Lookup priority for each tier:
1. Department-specific default model for this tier
2. Department-specific any-enabled model for this tier
3. Global default model for this tier
4. Global any-enabled model for this tier

**Cascade rules** apply when a tier has no registered model:
- Tier 2 (Analyst) — cascades **up** to Tier 3 (Advisor) if no Analyst model is registered
- All other tiers — cascade **down** to the next cheaper tier

If the Model Registry has no entries at all, the router falls back to hardcoded defaults from the configuration file.

### Cost Calculation

Cost is calculated from the actual token counts returned by the model call — not estimates:

```
cost = (input_tokens  × cost_in_per_million  / 1,000,000)
     + (output_tokens × cost_out_per_million / 1,000,000)
```

| Tier | Input (per 1M tokens) | Output (per 1M tokens) |
|------|-----------------------|------------------------|
| Economy (Scout / Analyst) | $0.15 | $0.60 |
| Premium (Advisor / Strategist) | $3.00 | $15.00 |

This 20× input / 25× output cost differential is the primary driver of CostPilot's routing savings. Every request correctly classified as ROUTINE and served by Scout instead of Advisor saves 20× on input tokens.

### Routing Savings Calculation

```
full_flagship_cost   = total_calls × $0.030  (average Opus/Sonnet call at premium rates)
routing_savings_usd  = full_flagship_cost − actual_spend_this_month
```

This answers: *what would this month's AI bill have been if every request went to the flagship model?* The difference is your routing savings.

### Real-World Example

Three cases arrive in sequence:

**Case 1:** *"Can you update the status to resolved?"*
- 12 tokens, no keywords → **ROUTINE → Scout** → ~$0.000002

**Case 2:** *"We need a full root cause analysis of the outage in the EU data center and a recommendation for preventing recurrence."*
- 38 tokens, keywords `root cause` + `recommendation`, under threshold → **MODERATE → Analyst** → ~$0.000006

**Case 3:** *"Legal has flagged this GDPR breach. I need a comprehensive compliance analysis and risk assessment covering all affected records."*
- 290 tokens (after pruning), keywords `gdpr` + `breach` + `compliance` + `risk assessment`, over threshold → **COMPLEX → Advisor** → ~$0.00090

All three handled at exactly the right tier.

### Token Threshold Configuration

The default token threshold is **250 tokens** (~1,000 characters). Adjustable at runtime via the routing configuration panel — range 150 to 2,000 tokens. No deployment required.

### Latency Impact

Complexity scoring is **< 1ms** — linear keyword scan and a character-count divide, in-process with no external calls. The dominant latency factor is the model API call itself.

### Safety Guarantees

- A `force_complex` flag set by the sensitive term engine **cannot be cleared** by the complexity scorer
- A THROTTLED department **cannot escape throttling** through explicit tier tags — throttle enforcement runs first
- The tier label in the response always reflects the **requested tier**, not the cascaded model's tier
- If the Model Registry is unavailable, the router falls back to hardcoded defaults — no request is dropped

---

## Step 4 — Agent Resolution

### What It Does

Before the model is called, CostPilot resolves which AI agent is sending this request. This enables per-agent reporting, concurrency control, and tier policy enforcement across your entire AI fleet.

An "agent" in CostPilot is any AI process that routes requests through the pipeline — a Salesforce Apex trigger, a HubSpot automation, a custom Python script, or a standalone AI assistant. CostPilot's Agentlake registry is the system of record for all of them.

### How Agent Resolution Works

**1. Look up by ID** — if `agent_id` is provided, look it up directly.

**2. Look up by name** — if no ID or no match, search by `agent_name`.

**3. Auto-register** — if no match and a name was provided, CostPilot creates a new registry entry with default settings. The first request self-registers the agent. Sandbox mode (`is_test=true`) skips registration.

Platform inference runs at registration time: `SF-` → Salesforce, `SN-` → ServiceNow, `HB-`/`HS-` → HubSpot, `ZD-` → Zendesk, `SAP-` → SAP. No explicit declaration required.

### Agent Status Lifecycle

| Status | Meaning |
|--------|---------|
| **idle** | Registered but not currently processing any request |
| **active** | Currently routing a request — highlighted in green on the dashboard |
| **queued** | Waiting to access a record held by another agent |
| **locked** | Suspended — two agents collided on the same record, supervisor action required |

After routing completes, the agent holds `active` status for 4 seconds before returning to `idle`. This window ensures the live dashboard polling cycle catches the active state.

### Concurrency Control: The Traffic Cop

When multiple AI agents process records from the same system simultaneously — a common Salesforce pattern where multiple Flows trigger at once — two agents can attempt to write the same record at the same time.

When an agent calls `claim_record`, CostPilot checks whether any other agent already holds an active or locked claim on that exact `(table, record_id)` pair. If a conflict is detected, the configured **collision policy** determines what happens:

| Policy | Behavior | Use Case |
|--------|----------|---------|
| **lock** (default) | Both agents are suspended. Supervisor must release. | High-stakes writes where data integrity is critical |
| **queue** | The arriving agent waits. Proceeds automatically when the first releases. | Sequential processing is acceptable |
| **skip** | The arriving agent abandons this record silently. | Best-effort processing where duplicates are harmless |

### Per-Agent Tier Bounds

Each agent can have a `min_tier` and `max_tier`. The routing engine's output is clamped to those bounds after complexity scoring:

- Complexity selects Tier 1, agent `min_tier` is 2 → bumped up to Tier 2
- Complexity selects Tier 3, agent `max_tier` is 2 → capped at Tier 2

Tier bound adjustments are recorded in the audit log:

```
[AGENT BOUND: capped down from Advisor to Analyst by agent policy] <original reason>
```

### Sandbox Mode

Any request sent with `is_test: true` runs the full pipeline — pruning, sensitive terms, complexity scoring, routing — but skips all database writes: no transaction recorded, no budget incremented, no audit event.

### Real-World Example

A Salesforce Flow fires twice for the same case update (a common side effect of multi-rule automation). Both calls arrive within the same second with `agent_name: "SF-Support-Triage"`.

The first call: claim granted, status set to `active`, routing runs, response returned.

The second call: `claim_record` finds that `SF-Support-Triage` already holds an active claim on that case ID. With the `lock` policy set, both instances are suspended — the collision is flagged on the dashboard for supervisor review. No duplicate response reaches the customer.

### Latency Impact

Single indexed database lookup: **< 1ms**. Auto-registration adds one write. Tier bound clamping is in-memory.

### Safety Guarantees

- A locked agent **cannot route new requests** until a supervisor releases it
- Auto-registration runs once per agent name — subsequent requests find the existing entry
- Tier bounds are applied **after** sensitive term escalation — an ESCALATE cannot be overridden by a `max_tier` cap
- Sandbox mode guarantees zero production impact

---

## Step 5 — Budget Enforcement

### What It Does

After the model returns a response, CostPilot records the cost and enforces departmental spending limits. Budget enforcement guarantees that no department can run an unbounded AI bill — every dollar is tracked against a cap, and when that cap is hit, model access is automatically constrained without dropping any requests.

The system operates per-department. A department hitting its limit has zero effect on any other department's routing.

### The Three Budget States

| State | Threshold | What It Means |
|-------|-----------|--------------|
| **healthy** | < 80% of cap used | Normal routing — all tiers available |
| **warning** | ≥ 80% of cap used | Routing unchanged, dashboard surfaces yellow alert |
| **throttled** | 100% of cap reached | All requests capped at the configured throttle tier |

### How Throttling Works

When `current_spend_usd` reaches `monthly_cap_usd`, the `throttled` flag is set in the **same database write** that records the transaction. No scheduled job. No polling. From that point forward, every request from that department is capped at the throttle tier:

```
effective_tier   = department.throttle_tier   // configurable, default: 1 (Scout)
routing_decision = "THROTTLED"
```

The model still runs. Requests are never dropped. The department's AI continues functioning — it simply uses the cheapest available tier.

### Configurable Throttle Tier

```http
PATCH /api/budget/Marketing/throttle-tier
{ "tier": 2 }
```

Default is Tier 1 (Scout). A compliance-sensitive department can be configured to never drop below Tier 3 even when throttled. Range: 1–4.

### Supervisor Override

```http
POST /api/budget/Marketing/override    // restore full access
POST /api/budget/Marketing/revoke      // re-evaluate and re-throttle if still over cap
```

Override is permanent until revoked. Revoking re-evaluates current spend and re-throttles if still over limit.

### Cap Updates

```http
POST /api/budget/Marketing/cap
{ "new_cap_usd": 500.00 }
```

Raising a cap that puts current spend below 100% automatically clears the throttle. If the department doesn't exist yet, CostPilot creates the budget record on first cap set.

### Raw Payload Logging

Controlled per-department. When enabled, the original unmodified text of any pruned request is stored alongside the audit event — only for requests where pruning actually removed tokens.

```http
PATCH /api/budget/Legal/raw-logging
{ "enabled": true, "retention_days": 90 }
```

Retention options: 30, 90, 180, 365 days, or 0 for indefinite. Off by default.

### Real-World Example

Marketing has a $250/month cap. On the 22nd, spend reaches $250.05 on one request:

- Throttle fires in the same write
- All subsequent Marketing requests route to Scout (Tier 1)
- Dashboard shows "⚠ Supervisor action required"

Supervisor grants override → full access restored immediately. At month end:

```http
POST /api/budget/Marketing/reset
```

Spend resets to $0, throttle clears, override clears, cycle begins again.

### Latency Impact

Two database operations per request: one read (check throttle state) and one write (increment spend, conditionally set throttle). Both by primary key. **< 2ms** total.

### Safety Guarantees

- Throttling fires in the **same transaction** as the spend write — no gap where an over-budget request slips through unthrottled
- A throttled department **cannot be bypassed** by explicit tier tags — throttle enforcement runs before the router checks for override tags
- Cap raises take effect **immediately** on the next request — no cache
- Spend reset is atomic — throttle and override state reset together

---

## Step 6 — Audit Logger

### What It Does

The final step in every request is the audit write. CostPilot records every routing decision to two destinations simultaneously, regardless of which tier was used, regardless of whether the request was routine, and regardless of whether it was blocked before the model was called.

The philosophy behind auditing every call:

> *PII may slip past keyword filters and reach the model. Without the payload on record, you cannot prove what data was exposed. GDPR Article 33 requires knowing exactly what data was compromised. Regulators and auditors want the complete picture, not just flagged events.*

### What Gets Written

| Field | What It Contains |
|-------|-----------------|
| `event_type` | `ROUTING`, `DECISION`, `LOCK`, or `ESCALATED` |
| `department` | Which department sent the request |
| `model_tier` | The tier actually used |
| `routing_decision` | `ROUTINE`, `MODERATE`, `COMPLEX`, `THROTTLED`, `OVERRIDE`, or `BLOCKED` |
| `risk_level` | `low`, `medium`, `high`, or `critical` — auto-classified |
| `prompt_payload` | First 2,000 characters of the cleaned text sent to the model |
| `rationale` | Plain-English explanation of the routing decision |
| `decision_outcome` | Summary: tier used, cost, special conditions |
| `matched_keywords` | Every keyword that influenced routing |
| `context_snapshot` | Frozen budget state at the exact moment of the decision |
| `cost_usd` | Actual cost of this call |
| `timestamp` | UTC timestamp, millisecond precision |

The `context_snapshot` records the department's cap, current spend, budget percentage, and throttle state at decision time — not the current state when a report runs later. If a supervisor override was active during a disputed call, the snapshot proves it.

### Risk Classification

| Risk Level | Triggers |
|-----------|---------|
| **critical** | Request BLOCKED, or keywords: `fraud`, `breach`, `gdpr`, `hipaa`, `ssn`, `social security`, `credit card`, `bank account`, `passport`, `date of birth` |
| **high** | Concurrency LOCK, or keywords: `legal`, `compliance`, `audit`, `contract`, `escalate`, `termination`, `harassment`, `discrimination` — or department THROTTLED |
| **medium** | COMPLEX routing decision, no critical keywords |
| **low** | All other calls — routine Scout or Analyst, no flagged keywords |

### Human-Readable Rationale

Every record includes a `rationale` field written for a compliance officer or external auditor, not a developer. It incorporates actual values from the decision:

**BLOCKED:**
> *REQUEST BLOCKED — SENSITIVE DATA DETECTED. The payload submitted by the Support department was rejected before reaching any AI model. Trigger: Sensitive term blocked: 'social security' (hipaa). Matched sensitive terms: "social security", "ssn". No tokens were consumed. No data was sent to OpenAI or any external provider.*

**THROTTLED:**
> *BUDGET CAP ENFORCED. The Marketing department reached 100.2% of its $250.00 monthly cap (current spend: $250.52). The payload was classified as COMPLEX, but the flagship model was blocked. Request was downgraded to the micro-model tier. A supervisor override is required to restore flagship access.*

**ROUTINE:**
> *ROUTINE CALL — Scout tier selected for Support department. No high-risk keywords detected. Department budget at time of call: 42.3% used ($84.60 of $200.00 monthly cap) — within threshold, no throttle applied. Call cost: $0.000003. Full payload retained in audit record. If PII is found in this payload, this record is the evidence that it reached the Scout model.*

### Dual-Write Architecture

**1. Database (`audit_events` table)**
Queryable and filterable. Supports full-text search, date range, risk level, and department filters. Powers the compliance dashboard and all reporting endpoints.

**2. Append-only JSONL file (`fage_audit.jsonl`)**
Each line is a complete JSON record. The file is never overwritten or deleted by the application — simulating an immutable audit trail that can be shipped to a SIEM, backed up to cold storage, or fed into a data pipeline independently of the database.

### Raw Payload Logging

For departments with raw payload logging enabled, the original unmodified text before pruning is stored — up to 5,000 characters — only for requests where pruning actually removed tokens.

Retention policy is enforced at the API read layer: when a retention period expires, `raw_payload` is returned as `null` even if the row exists. No background deletion job required.

### Blocked Request Audit Timing

For BLOCKED requests, the audit event is written **before** the HTTP 451 response is returned. If the server crashes before the response is sent, the block is already on record.

### Query API

```http
GET /api/audit?limit=50
GET /api/audit?department=Legal&risk=critical
GET /api/audit/{event_id}
GET /api/audit/export/jsonl
```

### Latency Impact

One database insert + one file append, both synchronous. **5–15ms** typical. Synchronous by design — an audit system that silently drops events under load is not an audit system. Write failures are surfaced in the application error log but do not break the routing response.

### Safety Guarantees

- **Every call is audited** — no code path through the routing pipeline skips the audit write
- **BLOCKED requests are audited before rejection** — the record exists before the caller receives the 451
- **Context snapshots are immutable** — subsequent cap changes or overrides do not alter historical records
- **Dual-write means database loss does not eliminate the audit trail**
- **Expired raw payloads are null on read** — retention enforced at the API layer

---

## Step 7 — Model Registry & Live Mode

### What It Does

The Model Registry is where your team defines which AI models serve each tier — and where CostPilot connects to Anthropic, OpenAI, or on-premise infrastructure to make the actual model call. It separates two concerns that most integrations conflate: *which tier should handle this request* (decided by the router) and *which specific model fills that tier* (decided by the registry).

This means you can swap models, negotiate new rates, or run different models for different departments without touching routing logic or application code.

### The Registry Table

| Field | Description |
|-------|-------------|
| `model_id` | The exact API identifier (`claude-sonnet-4-6`, `gpt-4o`, etc.) |
| `display_name` | Human-readable label shown in the dashboard and audit log |
| `provider` | `anthropic`, `openai`, or your on-premise identifier |
| `tier` | 1–4 (Scout / Analyst / Advisor / Strategist) |
| `cost_input_per_1m` | Input token rate in USD per million tokens |
| `cost_output_per_1m` | Output token rate in USD per million tokens |
| `is_enabled` | Whether this model is available for routing |
| `is_default` | Whether this is the primary model for its tier |
| `department` | Optional — restricts to one department; `null` = global |

### Provider Auto-Detection

The provider is inferred automatically from the model ID:

- `claude-*` → Anthropic API
- `gpt-*`, `o1`, `o3` → OpenAI API
- All others → globally configured provider (`CostPilot_PROVIDER` env var)

You can mix Anthropic and OpenAI models in the same registry — the correct API is called based on model ID alone.

### Simulated vs. Live Mode

```
CostPilot_MODEL_MODE=simulated   # default — no API keys required
CostPilot_MODEL_MODE=live        # calls the real Anthropic or OpenAI API
```

**Simulated mode** runs the entire pipeline — pruning, sensitive terms, routing, budget tracking, audit logging — but substitutes real model calls with pre-written responses. Token counts are estimated; costs are calculated at registry rates against those estimates. Everything on the dashboard reflects real pipeline behavior; only the model response text is fabricated. This lets you validate integration and show stakeholders real cost projections before committing API spend.

**Live mode** makes real API calls. Token counts come from the provider's usage response. Cost calculations use actual billed tokens. The dashboard status indicator shows `LIVE · ANTHROPIC` or `LIVE · OPENAI`.

### Switching to Live Mode

```
# .env
CostPilot_MODEL_MODE=live
CostPilot_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
```

Environment variable defaults (used when no registry entry exists for a tier):

| Variable | Default |
|----------|---------|
| `ANTHROPIC_MICRO_MODEL` | `claude-haiku-4-5-20251001` |
| `ANTHROPIC_FLAGSHIP_MODEL` | `claude-sonnet-4-6` |
| `OPENAI_MICRO_MODEL` | `gpt-3.5-turbo` |
| `OPENAI_FLAGSHIP_MODEL` | `gpt-4o` |

### Model Presets

CostPilot ships with a library of 19 known models pre-loaded in a `known_models` reference table — covering current Anthropic and OpenAI models with their published input/output token rates. From the Models page, admins can select any preset from a dropdown to pre-fill the model ID, provider, and pricing fields. No need to look up API identifiers or pricing pages manually.

```http
GET /api/models/known   // returns the preset library
```

### Managing Models

```http
// Register a new model
POST /api/models
{
  "display_name":       "Claude Haiku 4.5",
  "model_id":           "claude-haiku-4-5-20251001",
  "provider":           "anthropic",
  "tier":               1,
  "cost_input_per_1m":  0.80,
  "cost_output_per_1m": 4.00,
  "is_enabled":         true,
  "is_default":         true
}

// Toggle a model on/off without removing it
PATCH /api/models/3/toggle

// Department-scoped entry (same model, different cost record for charge-back)
POST /api/models
{
  "display_name": "Claude Opus 4.6 (Legal)",
  "model_id":     "claude-opus-4-6",
  "tier":         4,
  "department":   "Legal",
  "is_default":   true
}
```

Setting a new default automatically clears the previous default for that tier and department scope.

### On-Premise Model Support

Point the OpenAI client at an internal URL via `OPENAI_BASE_URL`. The registry entry uses whatever model ID your internal endpoint expects. No code changes required.

### Real-World Example

An enterprise customer runs:

- **Tier 1 (Scout):** `gpt-4o-mini` via OpenAI
- **Tier 2 (Analyst):** `claude-sonnet-4-6` via Anthropic
- **Tier 3 (Advisor):** `claude-opus-4-6` via Anthropic (global)
- **Tier 3 (Advisor), Legal:** `claude-opus-4-6` at a negotiated rate (department-scoped)
- **Tier 4 (Strategist):** `o3` via OpenAI

All five entries in the registry. No routing code changed.

### Safety Guarantees

- Provider selection from model ID is **deterministic** — `claude-*` always reaches Anthropic regardless of the env var
- Disabling a model removes it from routing lookup immediately — cascade rules find the next available model
- In simulated mode, **no API keys required, no external calls made**
- Anthropic API errors are surfaced as HTTP 502 — never swallowed silently

---

## Pipeline Latency Summary

| Step | Operation | Typical Overhead |
|------|-----------|-----------------|
| Step 1 — Sensitive Term Check | In-memory substring scan | < 5ms |
| Step 2 — Context Pruner | Regex filters, in-process | 10–50ms (prose) / 0ms (code) |
| Step 3 — Token Router | Keyword scan + token count | < 1ms |
| Step 4 — Agent Resolution | Indexed DB lookup | < 1ms |
| Step 5 — Budget Read | DB read by primary key | < 1ms |
| **Model API call** | **Anthropic / OpenAI** | **200ms–3,000ms** |
| Step 5 — Budget Write | DB write + conditional throttle | < 1ms |
| Step 6 — Audit Logger | DB insert + file append | 5–15ms |
| **Total CostPilot overhead** | | **~20–75ms** |

The model API call dominates total latency by 10–100×. CostPilot's full pipeline adds less than a single round-trip to any external API.

---

## Integration Checklist

Use this checklist when onboarding CostPilot in your environment.

### Before Go-Live

- [ ] Change your AI API calls to POST to `/api/route` instead of calling Anthropic/OpenAI directly
- [ ] Set `department` on each request to match your org structure
- [ ] Review the default sensitive term library — add any domain-specific terms your organization requires
- [ ] Set monthly budget caps for each department: `POST /api/budget/{department}/cap`
- [ ] Register your models in the Model Registry with accurate per-token rates from your provider contracts
- [ ] Set `min_tier` / `max_tier` on agents that have specific capability requirements
- [ ] Configure throttle tier per department — decide whether throttled departments should fall to Scout (Tier 1) or a higher floor
- [ ] Decide which departments need raw payload logging enabled and for how long
- [ ] Test using `is_test: true` to run the full pipeline without affecting production metrics

### Going Live

- [ ] Set `CostPilot_MODEL_MODE=live` in your environment
- [ ] Set `CostPilot_PROVIDER=anthropic` or `openai`
- [ ] Add your API key (`ANTHROPIC_API_KEY` or `OPENAI_API_KEY`)
- [ ] Confirm the dashboard status indicator shows `LIVE · ANTHROPIC` or `LIVE · OPENAI`
- [ ] Send one test request and verify the audit log captures it with the correct model tier and actual token counts

### Ongoing Operations

- [ ] Monitor the throttled KPI card on the dashboard — throttled departments require supervisor action
- [ ] Review critical and high-risk audit events weekly
- [ ] Adjust complexity keywords or the token threshold if routing decisions don't match your expectations
- [ ] Reset department spend at the start of each billing period: `POST /api/budget/{department}/reset`
- [ ] Export the JSONL audit log to your SIEM or cold storage on a regular schedule

---

*CostPilot — AI Cost Governance Middleware*
*For integration support, refer to the API reference or contact your implementation team.*
