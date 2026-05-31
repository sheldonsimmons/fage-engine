# CostPilot — Technical Specification
## For Technical Co-Founder Review

**Version 0.2.0 — Confidential**

---

## 1. Overview

CostPilot is an AI governance and cost optimization middleware platform. Every AI request made by an organization passes through CostPilot before reaching a model. The platform prunes the payload, scores its complexity, selects the appropriate model tier, enforces department budget policy, checks for PII and sensitive terms, executes the model call, records the cost, and writes an immutable audit entry — all in a single synchronous pipeline that adds negligible latency from the end user's perspective.

The system is built to run in two modes:

- **Simulated mode** (default) — no API keys required, realistic fake responses with real token math
- **Live mode** — drops into actual Anthropic or OpenAI API calls by changing two environment variables

This means the full governance pipeline, cost tracking, and audit log work identically in both modes. The only difference is whether a real model call happens at the end.

---

## 2. Stack

### Why These Choices

| Layer | Choice | Why |
|---|---|---|
| API framework | **FastAPI** | Native async, auto-generates OpenAPI docs at `/docs`, Pydantic validation built in, minimal boilerplate |
| ASGI server | **Uvicorn** with standard extras (`httptools`, `uvloop`, `websockets`) | Fastest Python ASGI server, production-proven on Heroku |
| ORM | **SQLAlchemy 2.x** | Mature, dialect-agnostic — same code runs on SQLite locally and PostgreSQL in production without changes |
| Database | **SQLite** (local dev) / **PostgreSQL** (Heroku prod) | SQLite requires zero setup for local dev; the DATABASE_URL env var transparently switches to Postgres on deploy |
| Migrations | **Inline `ALTER TABLE` at startup** | No Alembic required for a POC. `main.py` runs safe column additions wrapped in try/except on every boot. Columns are added only if they don't exist. This is intentional — it keeps the deploy workflow to a single `git push` |
| PII detection | **Presidio** (Microsoft) + **spaCy en_core_web_sm** | Presidio provides a structured recognizer registry; spaCy NLP adds context-aware entity recognition beyond pure regex. Both included in `requirements.txt` and downloaded at build time |
| AI providers | **Anthropic SDK** + **OpenAI SDK** | Both installed; provider selection at runtime via environment variable |
| Frontend | **Vanilla HTML/CSS/JS** | No framework by design. Faster iteration, no build step, fully inspectable source, works as static files served directly by FastAPI |
| Charts | **Chart.js 4.4.4** (CDN) | Lightweight, no build dependency |
| Deployment | **Heroku** (single dyno + Postgres addon) | Single `git push heroku main` deploys the full stack |

### Directory Structure

```
fage/
├── backend/
│   ├── main.py                  # FastAPI app, route registration, startup seed
│   ├── config.py                # All pricing constants, thresholds, department list
│   ├── requirements.txt
│   ├── Procfile                 # web: uvicorn main:app --host 0.0.0.0 --port $PORT
│   ├── .python-version          # 3.13
│   ├── core/
│   │   ├── pruner.py            # Context Sweeper — 6-filter regex pipeline
│   │   ├── router.py            # Complexity scorer + model cascader
│   │   ├── model_client.py      # Live API broker (Anthropic / OpenAI / simulated)
│   │   ├── voice_guard.py       # PII redaction — FSM + Presidio dual-layer
│   │   ├── budget.py            # Budget enforcement helpers
│   │   ├── auditor.py           # Audit event writer
│   │   ├── keywords.py          # Sensitive term matcher
│   │   ├── agentlake.py         # Agent registry + collision detection
│   │   └── routing_config.py    # DB-backed routing rule loader
│   ├── api/
│   │   ├── routes_pruner.py
│   │   ├── routes_router.py
│   │   ├── routes_budget.py
│   │   ├── routes_agentlake.py
│   │   ├── routes_auditor.py
│   │   ├── routes_dashboard.py
│   │   ├── routes_keywords.py
│   │   ├── routes_models.py
│   │   ├── routes_reports.py
│   │   ├── routes_efficiency.py
│   │   ├── routes_agent_activity.py
│   │   ├── routes_voice.py
│   │   ├── routes_routing_config.py
│   │   ├── routes_timeseries.py
│   │   └── routes_enrich.py
│   └── database/
│       ├── db.py                # SQLAlchemy engine + SessionLocal factory
│       ├── models.py            # All 10 ORM table definitions
│       ├── seed.py              # Reference data loader
│       ├── populate_demo.py     # 30-day demo data generator
│       └── populate_enterprise.py # Enterprise-scale demo data
└── frontend/
    ├── index.html               # Main dashboard
    ├── demo-crm.html            # Live demo environment
    ├── savings.html             # Savings calculator
    ├── sandbox.html             # Interactive feature sandbox
    ├── onboarding.html          # Connect & setup wizard
    ├── policy.html              # Routing rules + sensitive terms
    ├── reports.html             # Analytics reports
    ├── models.html              # Model registry management
    ├── admin.html               # Admin controls
    ├── live.html                # Live operations center
    ├── connect.html             # Platform connection flow
    ├── css/
    └── js/
        ├── dashboard.js
        ├── budget.js
        ├── help.js              # Guided tour + contextual tooltips
        ├── demo_crm.js          # Live demo case library (68 cases)
        ├── onboarding.js        # Setup wizard + code generators
        ├── tier-utils.js        # Centralised tier name resolution — all pages call getTierName() instead of hardcoded strings
        └── [feature modules]
```

---

## 3. Database Schema

Ten tables. All defined in `database/models.py` using SQLAlchemy declarative ORM.

### `token_transactions`
The financial source of truth. Every AI call writes one row.

| Column | Type | Notes |
|---|---|---|
| id | Integer PK | Auto-increment |
| department | String | e.g. "Support", "Sales" |
| source_platform | String | "Salesforce", "ServiceNow", "HubSpot", etc. |
| agent_id | Integer FK | → registered_agents |
| model_tier | String | "micro" or "flagship" |
| input_tokens | Integer | Actual tokens sent to model |
| output_tokens | Integer | Actual tokens returned |
| cost_usd | Float | Calculated at registry rates |
| timestamp | DateTime | UTC |
| routing_reason | String | "ROUTINE", "COMPLEX", "THROTTLED", "OVERRIDE" |
| was_pruned | Boolean | Whether the pruner ran |
| tokens_saved | Integer | Tokens eliminated by pruning |

### `audit_events`
Immutable black box. Written once, never modified.

| Column | Type | Notes |
|---|---|---|
| id | Integer PK | |
| event_type | String | "ROUTING", "THROTTLE", "LOCK", "DECISION" |
| agent_id | Integer FK | → registered_agents |
| department | String | |
| model_tier | String | |
| context_snapshot | Text | JSON — frozen system state at time of decision |
| prompt_payload | Text | Exact pruned text sent to model |
| rationale | Text | Plain-English routing justification |
| decision_outcome | String | |
| risk_level | String | "low", "medium", "high", "critical" |
| timestamp | DateTime | UTC |

### `department_budgets`

| Column | Type | Notes |
|---|---|---|
| id | Integer PK | |
| department | String UNIQUE | |
| monthly_cap_usd | Float | Set by admin |
| current_spend_usd | Float | Accumulated from token_transactions |
| period_start | DateTime | When the current billing period started |
| throttled | Boolean | True when cap is hit |
| override_granted | Boolean | True when supervisor manually restores access |
| throttle_tier | Integer | The maximum tier allowed when throttled — set per department via the Admin Panel (default: 1 = Scout, but configurable to any tier 1–4) |

### `registered_agents`

| Column | Type | Notes |
|---|---|---|
| id | Integer PK | |
| name | String UNIQUE | e.g. "SF-SupportBot" |
| department | String | |
| source_platform | String | |
| permissions | String | "read,write" |
| target_table | String | The CRM table the agent acts on |
| target_record_id | Integer | Current record being locked |
| status | String | "idle", "active", "locked", "queued" |
| collision_policy | String | "lock", "queue", or "skip" |
| locked_at | DateTime | When the collision lock was acquired |
| lock_reason | String | Human-readable reason |
| min_tier | Integer | Floor — routing never goes below this tier for this agent |
| max_tier | Integer | Ceiling — routing never goes above this tier |
| archived | Boolean | Soft-delete — hidden from live grid, history preserved |

### `model_registry`

| Column | Type | Notes |
|---|---|---|
| id | Integer PK | |
| display_name | String | "Claude Haiku 4.5" |
| model_id | String | "claude-haiku-4-5-20251001" (the actual API identifier) |
| provider | String | "Anthropic", "OpenAI", "Azure", "Google", "Custom" |
| tier | Integer | 1=Scout, 2=Analyst, 3=Advisor, 4=Strategist |
| cost_input_per_1m | Float | $ per million input tokens |
| cost_output_per_1m | Float | $ per million output tokens |
| is_enabled | Boolean | |
| is_default | Boolean | Default choice for this tier |
| department | String nullable | NULL = global; set to restrict a model to one department |

### `voice_events`
Every Voice Guard transcript processing run.

| Column | Type | Notes |
|---|---|---|
| id | Integer PK | |
| call_id | String | ID from upstream ASR platform |
| platform | String | "Genesys", "AWS Connect", "Salesforce Voice", etc. |
| department | String | |
| raw_transcript | Text | Stored only if no PII found |
| clean_transcript | Text | The redacted output passed downstream |
| redactions_count | Integer | |
| pii_types_found | String | JSON list: `["SSN", "CREDIT_CARD"]` |
| detection_method | String | "rule", "ai", "both", "none" |
| confidence_score | Float | 0.0 – 1.0 |
| processing_ms | Integer | Wall-clock time for the full pipeline |
| detection_details | Text | JSON array of per-finding detail objects |

### Additional Tables
- `sensitive_terms` — term, category (legal/hipaa/financial/hr/custom), action (flag/escalate/block), optional department scope
- `routing_configs` — single-row table (id=1), stores `complexity_token_threshold`, `complexity_keywords_json`, and `tier_names_json`; seeded from config.py, updated via UI. The `tier_names_json` column stores a JSON object like `{"1":"Scout","2":"Analyst","3":"Advisor","4":"Strategist"}` — null means use defaults. Admins can rename all four tiers from the Admin Panel without any code changes.
- `known_models` — pre-populated reference table of 19 well-known Anthropic and OpenAI models with their current published pricing. Used to populate the "Add from preset" dropdown on the Models page so admins don't need to look up model IDs and rates manually.
- `customers` — mock CRM contacts with name, email, tier, department
- `tickets` — support tickets linked to customers, used as payload source in demo
- `crm_records` — key/value fields on customer profiles (the records agents compete over)

---

## 4. Startup Sequence

On every boot, `main.py` runs three initialization phases before registering any routes:

1. **`models.Base.metadata.create_all()`** — creates all tables if they don't exist (idempotent)
2. **`_run_migrations()`** — runs `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` for columns added after initial schema creation, wrapped in try/except so they silently no-op if the column already exists
3. **`_seed_on_startup()`** — inserts default departments, model registry entries, sensitive terms, and routing config if they don't already exist; checks by primary key/unique key before inserting, so safe to call on every restart

This design means there is no migration runner, no schema version tracking, and no deployment step beyond `git push`. The tradeoff is that column renames and deletions require manual SQL. For a POC, this is the right call.

---

## 5. The Request Pipeline — End to End

Every AI governance request flows through `POST /api/route`. This is the core of the product.

```
Client Request
     │
     ▼
POST /api/route
  {
    "text": "...",
    "department": "Support",
    "agent_id": 3,
    "auto_prune": true,
    "voice_guard_processed": false
  }
     │
     ├─► [1] Sensitive Term Check (core/keywords.py)
     │         Scan text against sensitive_terms table
     │         action=block  → HTTP 451, audit written, pipeline stops
     │         action=escalate → force_complex=True, pipeline continues
     │         action=flag    → note in audit, pipeline continues
     │
     ├─► [2] Context Pruner (core/pruner.py)
     │         if auto_prune=True:
     │           strip_html()
     │           strip_email_headers()
     │           strip_reply_chains()
     │           strip_legal_disclaimers()
     │           strip_signatures()
     │           collapse_whitespace()
     │         Returns: cleaned_text, tokens_saved, compression_pct
     │
     ├─► [3] Complexity Scorer (core/router.py → score_complexity())
     │         Load threshold + keywords from routing_configs table (fallback: config.py)
     │         keyword match AND over threshold → COMPLEX  (→ Tier 3 Advisor)
     │         keyword match OR  over threshold → MODERATE (→ Tier 2 Analyst)
     │         neither                          → ROUTINE  (→ Tier 1 Scout)
     │         force_complex override           → COMPLEX  (→ Tier 4 Strategist)
     │
     ├─► [4] Explicit Tier Tag Check
     │         Checks first 100 chars for [scout], [analyst], [advisor], [strategist]
     │         If found, routes directly to that tier regardless of complexity score
     │
     ├─► [5] Budget Enforcement (core/budget.py)
     │         Query department_budgets for this department
     │         If throttled=True and override_granted=False:
     │           effective_tier = throttle_tier (set per department in Admin Panel, default: 1)
     │           Any request scoring above throttle_tier is capped DOWN to throttle_tier
     │           Requests already at or below throttle_tier are unaffected
     │           routing_decision = "THROTTLED"
     │
     ├─► [6] Model Registry Lookup (core/router.py → _get_model_from_registry())
     │         Priority order:
     │           1. Department-specific default for this tier
     │           2. Department-specific any-enabled for this tier
     │           3. Global default for this tier
     │           4. Global any-enabled for this tier
     │         Cascade: Tier 2 (Analyst) falls UP to Tier 3 if no Analyst registered
     │                  All others cascade DOWN to next cheaper tier
     │         Fallback: hardcoded config.py constants if registry empty
     │
     ├─► [7] Model Call (core/model_client.py → call_model())
     │         MODE=simulated → random canned response, estimated tokens
     │         MODE=live + model_id starts "claude-" → Anthropic SDK
     │         MODE=live + model_id starts "gpt-"    → OpenAI SDK
     │         Provider auto-detected from model_id prefix — env var is just a fallback
     │
     ├─► [8] Cost Calculation
     │         cost = (input_tokens × cost_input_per_1m / 1,000,000)
     │               + (output_tokens × cost_output_per_1m / 1,000,000)
     │         pruning_cost_saved = tokens_saved × (cost_input_per_1m / 1,000,000)
     │
     ├─► [9] Write token_transactions row
     │
     ├─► [10] Update department_budgets.current_spend_usd
     │          Check if new total hits cap → flip throttled=True if so
     │
     └─► [11] Write audit_events row (for COMPLEX, THROTTLED, OVERRIDE decisions)

Response:
  {
    "department": "Support",
    "complexity": "COMPLEX",
    "routing_decision": "COMPLEX",
    "routing_reason": "Payload length (312 tokens) exceeds threshold (250) and keyword 'lawsuit' detected — escalated to Advisor",
    "model_tier": "Advisor",
    "model_name": "claude-sonnet-4-6",
    "input_tokens": 312,
    "output_tokens": 147,
    "cost_usd": 0.003146,
    "was_pruned": true,
    "tokens_saved_by_pruning": 184,
    "pruning_cost_saved_usd": 0.000552,
    "total_cost_without_pruning": 0.003698,
    "simulated_response": "...",
    "provider": "anthropic",
    "model_mode": "live"
  }
```

---

## 6. Feature Modules

### 6.1 Context Pruner — `core/pruner.py`

Six sequential regex filters applied in order. Each filter only modifies the text if it produces a shorter result — a filter that produces no change is recorded as not applied.

| Filter | What It Removes |
|---|---|
| `strip_html()` | `<style>`, `<script>` blocks, all HTML tags, common HTML entities |
| `strip_email_headers()` | RFC headers (From, To, Cc, Date, X-Mailer, DKIM-Signature, etc.), MIME boundaries, auto-reply boilerplate |
| `strip_reply_chains()` | Everything after reply-chain markers: "-----Original Message-----", "On [date] wrote:", forwarded message headers, long underscores |
| `strip_legal_disclaimers()` | CONFIDENTIALITY NOTICE blocks, "This email is intended for..." blocks, copyright lines, privacy policy references (up to 2,000 chars per match) |
| `strip_signatures()` | "Best regards / Name / Title" blocks, phone number lines, physical address lines, website URLs |
| `collapse_whitespace()` | 3+ consecutive blank lines collapsed to one, leading/trailing whitespace stripped |

**Token estimation:** `len(text) // 4` — the OpenAI standard heuristic of 1 token ≈ 4 characters. Accurate enough for cost estimation; actual token counts come from the model API in live mode.

**Return value:**
```python
{
  "cleaned_text":    str,
  "raw_tokens":      int,
  "clean_tokens":    int,
  "tokens_saved":    int,
  "compression_pct": float,   # 0.0 – 100.0
  "filters_applied": list[str]
}
```

### 6.2 Token Router — `core/router.py`

**Complexity scoring** runs on the pruned text. Rules applied in priority order:

1. keyword match **AND** token count > threshold → **COMPLEX** → Tier 3 (Advisor)
2. keyword match **OR** token count > threshold → **MODERATE** → Tier 2 (Analyst)
3. Neither → **ROUTINE** → Tier 1 (Scout)
4. `force_complex=True` (from sensitive term escalation) → **COMPLEX** → Tier 4 (Strategist)

Default complexity token threshold: **250 tokens**. Configurable at runtime via the Policy panel — stored in `routing_configs` table and loaded fresh on each request.

Default complexity keywords (32 terms): legal, compliance, lawsuit, contract, audit, fraud, critical, billing dispute, breach, regulatory, data loss, outage, gdpr, hipaa, analyze, analysis, assessment, evaluate, evaluation, root cause, integration, migration, architecture, performance review, optimization, forecast, strategy, risk assessment, incident, escalate, investigate, recommendation.

**Tier mapping:**

| Complexity | Tier | Name | Default Model |
|---|---|---|---|
| ROUTINE | 1 | Scout | Claude Haiku 4.5 |
| MODERATE | 2 | Analyst | (cascades up to Advisor if no T2 registered) |
| COMPLEX | 3 | Advisor | Claude Sonnet 4.6 |
| SENSITIVE TERM ESCALATION | 4 | Strategist | Claude Opus 4.6 |

**Explicit tier override tags:** A payload can begin with `[scout]`, `[analyst]`, `[advisor]`, or `[strategist]` to force a specific tier. The tag is stripped before the text reaches the model. Checked in the first 100 characters to accommodate Salesforce's `DESCRIPTION:` field label prefix pattern.

### 6.3 Model Client — `core/model_client.py`

Single entry point for all model calls. Provider is auto-detected from the model ID prefix — no environment variable change needed when swapping models in the registry.

| Env Var | Values | Notes |
|---|---|---|
| `CostPilot_MODEL_MODE` | `simulated` (default), `live` | |
| `CostPilot_PROVIDER` | `anthropic`, `openai` | Fallback only — model_id prefix takes precedence |
| `ANTHROPIC_API_KEY` | `sk-ant-...` | Required for live Anthropic calls |
| `OPENAI_API_KEY` | `sk-proj-...` | Required for live OpenAI calls |

**Live Anthropic call:** `client.messages.create(model=model_id, max_tokens=400, system=..., messages=[...])` — returns `usage.input_tokens` and `usage.output_tokens` directly from the API response.

**Live OpenAI call:** `client.chat.completions.create(model=model_id, max_completion_tokens=400, messages=[...])` — returns `usage.prompt_tokens` and `usage.completion_tokens`.

**Simulated call:** Returns a random response from a canned list, token counts estimated from text length. Identical pipeline otherwise — all cost math, DB writes, and audit entries fire exactly as in live mode.

### 6.4 Model Pricing (Seeded at Startup)

| Model | API ID | Tier | Input ($/1M) | Output ($/1M) | Active |
|---|---|---|---|---|---|
| Claude Haiku 4.5 | `claude-haiku-4-5-20251001` | T1 Scout | $0.80 | $4.00 | Yes |
| Claude Sonnet 4.6 | `claude-sonnet-4-6` | T3 Advisor | $3.00 | $15.00 | Yes |
| Claude Opus 4.6 | `claude-opus-4-6` | T4 Strategist | $15.00 | $75.00 | Yes |
| GPT-4o Mini | `gpt-4o-mini` | T1 Scout | $0.15 | $0.60 | No (available) |
| GPT-4o | `gpt-4o` | T3 Advisor | $2.50 | $10.00 | No (available) |

### 6.5 Voice Guard — `core/voice_guard.py`

Three-layer PII detection pipeline for voice call transcripts.

**Layer 1 — Normalization**
Converts spoken number formats to digit strings before pattern matching. Handles: "four four one two three" → "44123", hesitations ("uh... four... four..."), self-corrections ("no wait, it's five"), and ASR artifacts.

**Layer 2 — Trigger-Phrase State Machine**
Scans for PII context phrases before the number appears. When a trigger is detected, the engine enters a collection window and aggregates digits that follow. Triggers and their associated PII types:

- SSN triggers: "social security", "social security number", "my social", "ssn is", "social is"
- Credit card triggers: "credit card", "card number", "visa", "mastercard", "amex"
- Bank routing triggers: "routing number", "routing is", "aba number"
- Account triggers: "account number", "bank account", "checking account"
- Date of birth triggers: "date of birth", "birthday", "born on", "dob"
- Phone triggers: "phone number", "call me at", "my number is"

**Layer 3 — Presidio AI**
Runs `AnalyzerEngine` backed by spaCy `en_core_web_sm` on the full normalized transcript. Catches PII that has no spoken trigger phrase. Custom recognizers supplement Presidio's built-ins for: Member/Policy IDs (6–12 digit patterns), ABA routing numbers (leading zero), spoken email formats ("derek dot morrison at acme dot com"), and IPv4 addresses.

**Confidence scoring:** Each finding is assigned a confidence score (0.0–1.0). The two layers are merged with the most restrictive finding winning. Findings below 0.40 are discarded. The clean transcript replaces all detected PII spans with `[REDACTED-TYPE]` placeholders.

**HTTP endpoint:** `POST /api/voice/transcript`
```json
{
  "transcript": "My social security number is 4 4 1 uh... 2 3 1 2 3 4",
  "call_id": "CALL-001",
  "platform": "Genesys",
  "department": "Support"
}
```
Response includes `clean_transcript`, `redactions_count`, `pii_types_found`, `detection_method`, `confidence_score`, `processing_ms`.

### 6.6 Sensitive Term Library — `core/keywords.py`

At request time, the full `sensitive_terms` table is loaded and every term is checked against the lowercased payload text. Matching is substring-based (not word-boundary) to catch partial matches in spoken or typed text.

**Actions:**
- `block` — return HTTP 451 immediately, write audit entry, no model call made
- `escalate` — set `force_complex=True`, pipeline continues to Tier 4
- `flag` — note in audit entry, pipeline continues normally

**Default seeded terms (33 terms):**
- Block: ssn, social security, credit card, card number, cvv, routing number, bank account, passport number, date of birth, my social, my passport, routing is, date of birth is, diagnosis code, my diagnosis, medical record
- Escalate: lawsuit, litigation, attorney, legal action, breach of contract, gdpr, hipaa, regulatory, audit, termination, harassment, discrimination, passport, drivers license, drivers licence
- Flag: (configurable by admin)

Terms are scoped to department optionally — a term with `department=NULL` applies globally; a term with `department="Legal"` only fires for Legal department requests.

### 6.7 Budget Enforcement — `core/budget.py`

Budget state is stored in `department_budgets` and checked on every routed request.

**Throttle logic:**
1. After each transaction, `current_spend_usd` is incremented by `cost_usd`
2. If `current_spend_usd / monthly_cap_usd >= THROTTLE_TRIGGER_PERCENT` (default: 100%), `throttled` is set to `True`
3. On subsequent requests from a throttled department, any request that scores **above** `throttle_tier` is capped down to `throttle_tier`. Requests that already score at or below `throttle_tier` are routed normally — they are not affected.
4. `throttle_tier` is configured per department in the Admin Panel. Default is Tier 1 (Scout), but an admin can set it to Tier 2 (Analyst), Tier 3 (Advisor), or Tier 4 (Strategist). This means a department can be throttled to any model — not just the cheapest. For example, a Legal department could be throttled to Advisor (Tier 3) rather than Scout, ensuring quality is maintained even under budget pressure.
5. A supervisor can set `override_granted=True` to restore full access without resetting spend
6. At period reset, `current_spend_usd = 0`, `throttled = False`, `override_granted = False`, `period_start = now()`

Warning threshold is at 80% — the dashboard displays a yellow warning before the throttle triggers. This threshold is currently global across all departments. Per-department configurable warning thresholds are on the roadmap.

### 6.8 Agent Registry & Collision Detection — `core/agentlake.py`

Agents auto-register on first contact. No manual setup required. The `registered_agents` table stores the agent's current status, the table it targets, and the record ID it is currently acting on.

**Collision detection:**
When an agent attempts to act on a record (`target_table` + `target_record_id` combination), the system checks for any other agent with `status=active` on the same record.

If a collision is found, the response depends on `collision_policy`:
- `lock` — both agents set to `status=locked`, `locked_at` timestamped, `lock_reason` written, supervisor notification triggered
- `queue` — incoming agent set to `status=queued`, waits for the active agent to complete
- `skip` — incoming request skipped, logged

**Agent Tier Bounds — How Min and Max Tier Actually Work**

Every registered agent has two optional routing constraints stored in the database:

- `min_tier` — the floor. Routing will never go *below* this tier for this agent, regardless of what the complexity scorer decides.
- `max_tier` — the ceiling. Routing will never go *above* this tier for this agent, regardless of what the complexity scorer decides.

Both default to the full range (min=1, max=4), meaning no constraints applied unless explicitly set.

**Where the clamping happens:**

The complexity scorer runs first and produces a tier recommendation based on keyword matching and token count. The budget throttle check runs next and may step the tier down. After both of those, the agent bounds are applied as a final clamp:

```python
clamped_num = max(agent_min, min(agent_max, current_num))
```

This means:
- If complexity scores ROUTINE (Tier 1) but the agent has `min_tier=3`, the request is bumped up to Advisor (Tier 3). The routing reason in the response records this: `[AGENT BOUND: bumped up from Scout to Advisor by agent policy]`.
- If complexity scores COMPLEX (Tier 3) but the agent has `max_tier=1`, the request is capped down to Scout (Tier 1). The routing reason records: `[AGENT BOUND: capped down from Advisor to Scout by agent policy]`.
- If the department is throttled AND the agent has a `min_tier` above the department's `throttle_tier`, the agent bound wins — the request goes to the agent's minimum. Agent policy takes precedence over department throttle ceiling.

When the tier is clamped, the cost is recalculated at the clamped tier's actual rates from the model registry — the cost figure in the response always reflects what was actually used, not what was originally selected.

**How to set tier bounds:**

Via the API:
```
PATCH /api/agents/{agent_id}/tier-bounds
Body: { "min_tier": 2, "max_tier": 3 }
```

Validation: both values must be between 1 and 4, and `min_tier` cannot exceed `max_tier`. Returns HTTP 422 if either constraint is violated.

**Practical use cases:**

| Scenario | Setting | Effect |
|---|---|---|
| Compliance agent — must always use Advisor or better | `min_tier=3` | Routine requests bump up to Advisor. Never uses Scout. |
| Read-only lookup bot — simple queries only, cost controlled | `max_tier=1` | Even a complex-scoring payload stays on Scout. Never escalates. |
| HR agent — sensitive content, controlled ceiling | `min_tier=2`, `max_tier=3` | Always at least Analyst, never reaches Strategist. |
| Unrestricted agent — default behavior | `min_tier=1`, `max_tier=4` | Full routing logic applies with no override. |

### 6.9 AI Decision Audit Log — `core/auditor.py`

Audit events are written for every routing decision — including ROUTINE Scout calls. This is intentional: if PII slips past the keyword filter, the full payload and routing context are preserved in the audit record so the incident can be reconstructed exactly. ROUTINE entries are written with `risk_level=low`; COMPLEX, BLOCK, and THROTTLE entries are written with appropriate risk levels.

The `context_snapshot` column stores a JSON object with the frozen system state at decision time: department budget utilization, agent status, active terms, and routing config values. This allows the decision to be reconstructed even if state changes later.

The `prompt_payload` column stores the exact pruned text that was sent to the model — not the raw input. This ensures the audit reflects what the AI actually saw.

**Export:** The auditor route supports CSV export (`GET /api/audit/export?format=csv`) and print-to-PDF via the browser's native print dialog, triggered by the frontend.

### 6.10 Real-Time Dashboard

The dashboard (`/`) polls five backend endpoints on a 3-second interval:

| Endpoint | Data |
|---|---|
| `GET /api/dashboard/kpis` | Total spend today, tokens saved, active agents, throttled departments, blocked requests |
| `GET /api/dashboard/budgets` | All departments with spend, cap, utilization %, throttle state |
| `GET /api/dashboard/agents` | All registered agents with status, last activity |
| `GET /api/dashboard/routing-feed` | Last 50 routing decisions |
| `GET /api/audit/events` | Last 20 audit events |

No WebSocket — polling at 3-second intervals is sufficient for the POC and avoids connection management complexity. Upgrading to WebSocket is a single change to the dashboard JS.

### 6.11 Governance Event Stream

Live feed of all governance events across all departments. `GET /api/dashboard/events` returns the last 100 events with type, department, agent, timestamp, and detail. Events are generated by the routing pipeline and written to `audit_events` with appropriate `event_type` values. The frontend filters by type (BLOCK, ESCALATION, THROTTLE, ROUTINE) and department using client-side filtering on the fetched payload.

### 6.12 Reports — `api/routes_reports.py`

Four report tabs backed by SQL aggregation queries against `token_transactions` and `audit_events`:

- **ROI Tab** — total spend, tokens saved, cost avoided, projected annual savings
- **Savings Tab** — daily spend with vs. without CostPilot comparison bars
- **Departments Tab** — per-department spend breakdown, call volume, average cost per call
- **Risk & Compliance Tab** — blocked requests by department, sensitive term hit frequency, escalation rate

All report queries accept a `days` parameter (default: 30) and support CSV export.

### 6.13 Savings Calculator — `frontend/savings.html`

Three independent input paths, all feeding the same `calcSavings()` math engine. Runs entirely in the browser — no backend call is made for the calculation itself.

**Path A — API Key:** Browser-side `fetch()` to `api.anthropic.com/v1/usage` or `api.openai.com/v1/usage` with the key in the Authorization header. Key is held in a JS variable for one request and discarded. Never transits the CostPilot server.

**Path B — CSV Upload:** `FileReader` API reads the file client-side. Parser handles both Anthropic and OpenAI export column name formats, normalizes to a 30-day monthly equivalent using the actual date range detected in the file.

**Path C — Manual:** Monthly spend, call count, complexity %, and payload size inputs feed directly into the savings engine.

**Savings math:**

```
Downgrade savings:
  flagship_input  = total_input_tokens  × flagship_pct
  flagship_output = total_output_tokens × flagship_pct
  rerouted_input  = flagship_input  × 0.65   (65% re-routeable to Scout)
  rerouted_output = flagship_output × 0.65
  downgrade_saved = rerouted_input  × ($3.00 - $0.80) / 1M
                  + rerouted_output × ($15.00 - $4.00) / 1M

Pruning savings:
  pruning_rate (by payload size):
    small  = 20%   (< 400 tokens avg)
    medium = 42%   (400–1,200 tokens avg)
    large  = 62%   (1,200–3,000 tokens avg)
    heavy  = 82%   (> 3,000 tokens avg)
  tokens_pruned = total_input_tokens × pruning_rate
  pruning_saved = tokens_pruned × $3.00 / 1M   (billed at flagship rate)

Total monthly savings = downgrade_saved + pruning_saved
```

### 6.14 Live Demo Environment — `frontend/demo-crm.html`

Pre-built demonstration environment for business partners and prospects. Does not require login. Submits real requests to the live `/api/route` pipeline.

- 6 platform tiles (Salesforce, ServiceNow, HubSpot, Zendesk, Dynamics 365, Custom)
- 5 fixed scenario presets (Routine, Complex, Compliance, Blocked, Voice)
- 68 hand-crafted case library with Fisher-Yates shuffle queue (no repeats until all cases shown)
- Free-text subject and description fields
- Voice Guard toggle (auto-enables for voice scenarios)
- Result panel shows: tier badge, routing reason, pruning compression bar, cost with/without pruning, budget bar, keyword chips, links to audit log and dashboard

HTTP 451 (blocked request) renders a full-stop red panel with $0 cost and $0 tokens messaging.

### 6.15 Platform Integration & Code Generation — `frontend/js/onboarding.js`

The Connect & Setup wizard generates working integration code for 6 platforms automatically. Each generator outputs syntactically correct code specific to the platform's native API.

| Platform | Language / API |
|---|---|
| Salesforce | Apex — REST callout to `/api/route` from Salesforce Flow or Process Builder |
| ServiceNow | JavaScript — Business Rule making `gs.getUserID()` → REST call |
| HubSpot | Node.js — Workflow action webhook with `axios` to CostPilot endpoint |
| Zendesk | Ruby — Trigger webhook → Sinatra handler → CostPilot POST |
| Microsoft Dynamics 365 | C# — Plugin via `IOrganizationService` → `HttpClient` POST |
| Custom | Python — Generic `httpx` async POST to `/api/route` |

Generated code includes the actual Heroku endpoint URL and correct field mapping for each platform's native data structure.

### 6.16 Custom Tier Names

Tier names — Scout, Analyst, Advisor, Strategist — are admin-configurable display labels. The underlying tier integers (1–4) and all routing logic remain fixed; only what gets shown to end users changes.

**Storage:** `tier_names_json TEXT` column on the `routing_configs` row. A null value means all four defaults are in effect. A partial JSON object merges stored values with defaults — if only tier 1 is customized, tiers 2–4 fall back to defaults.

**API:**
```http
PATCH /api/routing-config/tier-names
{
  "tier_1": "Micro",
  "tier_2": "Standard",
  "tier_3": "Pro",
  "tier_4": "Enterprise"
}
```
Validation: each label must be non-empty and under 30 characters. Returns the merged result including any tiers that were not overridden.

**Reset to defaults:** send the endpoint with all four tiers omitted, or send empty strings — the stored JSON is cleared and defaults resume.

**Frontend:** `frontend/js/tier-utils.js` is the single source of truth for tier name resolution across all pages. It calls `GET /api/routing-config` on every page boot and writes to `window.TIER_NAMES`. All page scripts call `getTierName(tier)` — no page has hardcoded tier label strings. CSS badge classes (`.badge-scout`, `.badge-analyst`, etc.) are fixed color hooks that never change; only the text labels update.

**Admin Panel:** The compact Tier Names bar at the top of `admin.html` shows four color-coded inputs (one per tier, each bordered in that tier's color) with Save and Reset buttons. Changes are reflected across all pages on the next load.

---

## 7. How the Governance Layer Works — No AI Involved

This is a critical point that is easy to misunderstand: **CostPilot itself does not use AI to make any of its decisions.** Every governance action — routing, pruning, budget enforcement, PII detection, sensitive term matching, collision detection, tier clamping — is deterministic code. Rules in, decision out. No model call, no inference, no black box.

Here is exactly what each component uses:

| Function | Technology Used | What It Is |
|---|---|---|
| Context Pruning | Regular expressions (Python `re` module) | Pattern matching against known junk patterns — HTML tags, email headers, reply chain markers, legal boilerplate, signatures |
| Complexity Scoring | Keyword list substring match + token count comparison | A hardcoded list of 32 words checked against the lowercased payload, and a token count compared to a configurable integer threshold |
| Sensitive Term Detection | Database table substring match | Every term in the `sensitive_terms` table is checked against the lowercased payload text using Python's `in` operator |
| Budget Enforcement | Arithmetic | `current_spend_usd >= monthly_cap_usd` — a float comparison |
| Agent Tier Clamping | `max(min_tier, min(max_tier, result_tier))` | A two-line clamp function |
| Collision Detection | Database query | `SELECT * FROM registered_agents WHERE target_table=X AND target_record_id=Y AND status='active'` |
| Voice Guard — Rule Layer | Regular expressions + finite state machine | Trigger phrases are matched with regex; digits after a trigger are collected in a Python state machine |
| Voice Guard — AI Layer | Presidio + spaCy `en_core_web_sm` | This is the one component that uses a model — but it is a small, locally-running NLP model for named entity recognition, not a language model. It runs on the server in-process, makes no external API call, and adds ~50–100ms of processing time |
| Audit Log Write | SQLAlchemy ORM insert | A database write — no computation |

**Why this matters:**

Every governance decision CostPilot makes can be fully explained. There is no "the model decided." When a request is blocked, the exact term that triggered the block is in the response. When a request escalates, the exact keyword or token count that caused the escalation is in the routing reason. When an agent is clamped to a different tier, the direction and original tier are recorded in the routing reason string.

This is not a limitation — it is a design principle. AI governance infrastructure needs to be auditable. If the governance layer itself used AI to make decisions, you would need a second governance layer to govern that. CostPilot's governance decisions are rules that humans configured, executing exactly as written.

The only place an AI model is called is at the end of the pipeline, when the actual work task is sent to the selected model (Claude Haiku, Sonnet, Opus, or a GPT model). CostPilot governs that call — it does not make it.

---

## 8. Latency — The Honest Picture

CostPilot inserts itself between the requesting application and the AI model. That means it adds processing time. Here is an honest breakdown of where time is spent:

### CostPilot-owned processing time (before the model call)

| Step | Typical Time | Notes |
|---|---|---|
| Sensitive term check | < 5ms | In-memory string matching against the terms loaded from DB |
| Context pruning | 5–25ms | Regex pipeline against the payload text; scales with payload size |
| Complexity scoring | < 1ms | Keyword list iteration + token count |
| Budget DB read | 5–15ms | Single indexed `SELECT` on `department_budgets` |
| Model registry lookup | 5–15ms | Single indexed `SELECT` on `model_registry` |
| Agent lookup / auto-register | 5–20ms | Indexed `SELECT`, occasionally an `INSERT` |
| Tier clamping | < 1ms | Arithmetic |
| DB writes (transaction + audit) | 10–30ms | Two `INSERT` statements, one `UPDATE` |
| Voice Guard — rule layer | 10–40ms | Regex + FSM on transcript text |
| Voice Guard — Presidio/spaCy | 50–150ms | In-process NLP; no external call |

**Total CostPilot overhead (standard request, no Voice Guard):** approximately **30–100ms**

**Total CostPilot overhead (with Voice Guard):** approximately **80–250ms**

### The model call itself

This is where the time actually goes. CostPilot has no control over this:

| Model | Typical response latency |
|---|---|
| Claude Haiku 4.5 | 400–900ms |
| Claude Sonnet 4.6 | 1,500–3,500ms |
| Claude Opus 4.6 | 3,000–8,000ms |
| GPT-4o Mini | 300–800ms |
| GPT-4o | 1,000–3,000ms |

These are Anthropic and OpenAI's own published and observed latency ranges. They vary with payload size, server load, and output length.

### What this means in practice

On a typical enterprise support ticket routed to Scout (Haiku), the end-to-end time is approximately:

```
CostPilot overhead:   ~50ms
Haiku model call:     ~600ms
Total:                ~650ms
```

Without CostPilot, the same ticket hitting Haiku directly would take ~600ms. CostPilot adds roughly 50ms — under 10% overhead — while saving 20–82% of the tokens that would have been billed, routing to the cheapest competent model, enforcing budget policy, checking for PII and sensitive terms, and writing an immutable audit record.

**What CostPilot does not do that would add lag:**

- It does not make any external API call for governance decisions (all rules are local)
- It does not queue requests (unless collision policy is set to `queue` for a specific agent)
- It does not retry failed calls (the first result is returned immediately)
- It does not stream — it waits for the full model response before returning

**Honest caveat:** CostPilot currently runs on a single Heroku dyno. Under high concurrent load, database connection pool contention will add latency beyond the figures above. Production deployment on dedicated infrastructure with connection pooling (PgBouncer) and a read replica for dashboard queries would keep governance overhead under 50ms at enterprise call volumes. That is a deployment concern, not a code concern.

---

## 9. API Endpoints

Full OpenAPI docs auto-generated at `/docs` (Swagger UI).

| Method | Path | Function |
|---|---|---|
| GET | `/health` | System status, model mode, provider |
| GET | `/api/config` | Current model mode and provider config |
| POST | `/api/prune` | Context pruner — returns cleaned text and token savings |
| POST | `/api/route` | Full routing pipeline — the core endpoint |
| GET | `/api/budget/` | All department budgets |
| POST | `/api/budget/set-cap` | Set monthly cap for a department |
| POST | `/api/budget/grant-override` | Grant supervisor override |
| POST | `/api/budget/reset` | Reset period spend to $0 |
| GET | `/api/agents/` | All registered agents |
| POST | `/api/agents/register` | Register a new agent |
| POST | `/api/agents/update-status` | Update agent status |
| POST | `/api/agents/unlock` | Resolve a collision lock |
| GET | `/api/audit/events` | Audit log entries (paginated) |
| GET | `/api/audit/export` | Export audit log as CSV |
| GET | `/api/dashboard/kpis` | Dashboard KPI aggregates |
| GET | `/api/dashboard/budgets` | Budget utilization summary |
| GET | `/api/dashboard/agents` | Agent registry summary |
| GET | `/api/dashboard/routing-feed` | Recent routing decisions |
| GET | `/api/keywords/` | All sensitive terms |
| POST | `/api/keywords/` | Add a sensitive term |
| DELETE | `/api/keywords/{id}` | Remove a sensitive term |
| GET | `/api/models/` | Model registry |
| POST | `/api/models/` | Add a model |
| PUT | `/api/models/{id}` | Update model (cost, tier, enabled state) |
| GET | `/api/reports/summary` | Aggregated report data |
| GET | `/api/reports/bot-efficiency` | Per-agent efficiency metrics |
| GET | `/api/reports/agent-activity` | Agent activity timeline |
| POST | `/api/voice/transcript` | Voice Guard PII redaction |
| GET | `/api/routing-config` | Current routing rules and tier names |
| PUT | `/api/routing-config` | Update threshold and keywords |
| PATCH | `/api/routing-config/tier-names` | Update custom tier display names (tier_1 through tier_4) |
| GET | `/api/timeseries/daily` | 30-day daily spend time series |
| POST | `/api/enrich` | CRM context enrichment |
| POST | `/api/admin/populate-demo` | Load demo data |
| POST | `/api/admin/populate-enterprise-demo` | Load enterprise demo data (background task) |
| POST | `/api/admin/reset-demo` | Full factory reset |
| POST | `/api/admin/debug-tag` | Debug tier tag detection |

---

## 10. Deployment

### Production (Heroku)

```
Procfile:  web: uvicorn main:app --host 0.0.0.0 --port $PORT
           (runs from the /backend directory — set via Heroku buildpack config)
```

Add-ons: Heroku Postgres (any tier). The `DATABASE_URL` environment variable is set automatically by Heroku and picked up by SQLAlchemy's engine creation.

Environment variables required for live model calls:
```
CostPilot_MODEL_MODE=live
CostPilot_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
```

### Local Development

```bash
cd backend
pip install -r requirements.txt
python -m spacy download en_core_web_sm   # required for Voice Guard
uvicorn main:app --reload --port 8001
```

No `.env` required to run in simulated mode. All features functional including full governance pipeline, audit log, budget tracking, and dashboard.

### On-Premises Deployment

The stack has no cloud-only dependencies. Replacing Heroku Postgres with any PostgreSQL-compatible database (RDS, Cloud SQL, Azure Database, self-hosted) requires only a `DATABASE_URL` change. The application server can run on any Linux host with Python 3.13. Voice Guard requires the `en_core_web_sm` spaCy model to be available — this can be packaged into a Docker image for air-gapped environments.

---

## 11. Future Enhancements — Coming Soon

*CostPilot is actively in development. The capabilities below are on the roadmap. The current build is a fully functional proof of concept — every feature listed in this document works today. What follows is what gets built next.*

### Infrastructure
| Enhancement | Current State | Coming |
|---|---|---|
| Horizontal scaling | Single Heroku dyno | Load balancer + multiple workers |
| Database connection pooling | Direct SQLAlchemy connections | PgBouncer in front of Postgres |
| Read replica | Dashboard + routing hit same DB | Separate read replica for analytics queries |
| Automated DB tier scaling | Manual Heroku addon upgrade | Auto-scale based on connection count |

### Budget & Throttle
| Enhancement | Current State | Coming |
|---|---|---|
| Warning threshold | Global 80% across all departments | Per-department configurable warning percentage |
| Throttle tier setting | Set via Admin Panel, applies when cap is hit | Scheduled throttle — set a tier ceiling in advance before cap is reached |

### Authentication & Security
| Enhancement | Current State | Coming |
|---|---|---|
| Login system | No authentication — URL access only | Full SSO / OAuth 2.0 support |
| API key management | No key issuance or revocation | Per-integration key lifecycle management |
| Role-based access control | Single access level for all users | Read-only, Supervisor, and Admin roles |
| CORS policy | Wide open (`allow_origins=["*"]`) | Scoped to approved domains only |

### Routing & Governance
| Enhancement | Current State | Coming |
|---|---|---|
| Pre-call token counting | Estimated via `len(text) // 4` heuristic | Exact token count before billing using model tokenizer |
| Complexity scoring | Keyword list + token count threshold | ML-assisted scoring with confidence output |
| Retry / escalation | No retry on poor responses | Automatic escalation if confidence is low |
| Tier 2 (Analyst) model | Cascades up to Advisor by default | Native Analyst model registration and routing |

### Pruning
| Enhancement | Current State | Coming |
|---|---|---|
| Pruning logic | Regex pattern matching | Meaning-aware pruning — strips by semantic redundancy, not just pattern |
| Language support | English only | Multi-language payload support |
| Pruning preview | No preview before send | Pre-send diff showing what will be stripped |

### Voice Guard
| Enhancement | Current State | Coming |
|---|---|---|
| Speech model | spaCy `en_core_web_sm` — general purpose | Call-center-tuned model with domain-specific vocabulary |
| Processing mode | End-of-call batch | Real-time streaming redaction |
| Dialect support | Standard English optimized | Multi-accent and dialect coverage |

### Multi-Tenancy
| Enhancement | Current State | Coming |
|---|---|---|
| Tenant isolation | Single tenant — one DB, one admin | Full multi-tenant architecture with isolated workspaces per customer |
| Organization management | No concept of organizations | Organization-level admin, billing, and user management |

### Audit Log
| Enhancement | Current State | Coming |
|---|---|---|
| Immutability | Application-layer only — DB admin can modify rows | Cryptographic hash chaining — each entry hashes the previous |
| Entry signing | No signing | Per-entry cryptographic signature for legal-grade tamper evidence |

### General
| Enhancement | Current State | Coming |
|---|---|---|
| Webhook delivery | Events logged only — no push | Real-time push to Slack, PagerDuty, email, and custom webhooks |
| Failed call retry | No retry logic | Configurable retry with exponential backoff |
| Automated tests | No test suite | Full unit + integration test coverage |
| Budget period rollover | Manual reset only | Automated monthly rollover on configurable period start date |
| WebSocket push | Dashboard polls every 3 seconds | WebSocket for true real-time dashboard updates |
| Schema migrations | Inline `ALTER TABLE` at startup | Alembic migration runner for production schema management |

---

## 12. Document Index — Everything This Spec Covers

1. System Overview
2. Full Technology Stack — every tool and why it was chosen
3. Directory Structure — every file and folder explained
4. Database Schema — all 10 tables, every column, type, and purpose
5. Startup Sequence — table creation, column migrations, seed data
6. The Full Request Pipeline — end to end, every step with code-level detail
7. Context Pruner — all 6 filters, token estimation, return values
8. Token Router & Complexity Scorer — keyword matching, token threshold, tier mapping
9. Model Client — live vs. simulated mode, Anthropic and OpenAI integration
10. Model Pricing — all 5 registered models with exact input/output token rates
11. Voice Guard — normalization layer, trigger-phrase state machine, Presidio/spaCy AI layer
12. Sensitive Term Library — matching logic, flag/escalate/block actions, department scoping
13. Budget Enforcement — throttle logic, override flow, warning thresholds
14. Agent Registry & Collision Detection — auto-registration, lock/queue/skip policies
15. Agent Tier Bounds — min/max tier clamping, order of operations, interaction with throttle, practical use cases, API endpoint
16. AI Decision Audit Log — what gets written, when, why, and export formats
17. Real-Time Dashboard — polling intervals, all 5 data endpoints
18. Governance Event Stream — event types, client-side filtering
19. Reports — all 4 report tabs, query logic, export formats
20. Savings Calculator — all 3 input paths, full savings math formula with rates
21. Live Demo Environment — 68-case library, Fisher-Yates shuffle, result states, HTTP 451 handling
22. Platform Integration & Code Generation — all 6 platforms, languages, generated code structure
22a. Custom Tier Names — configurable display labels, storage format, API, frontend resolution via tier-utils.js
23. How the Governance Layer Works — No AI Used — every decision mapped to its actual technology
24. Latency — CostPilot overhead breakdown, model call latency ranges, honest single-dyno caveat
25. All API Endpoints — all 35 endpoints with method, path, and description
26. Deployment — Heroku production, local development, on-premises options
27. Future Enhancements — full roadmap organized by category with current state and coming state
28. This Index

---

**CostPilot v0.2.0 — Navigate AI Spend with Precision**
*Actively in development. Core governance pipeline fully operational.*
