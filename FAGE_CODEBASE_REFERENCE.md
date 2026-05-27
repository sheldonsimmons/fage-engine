# FAGE — Codebase Reference
## Complete Code & Functionality Documentation
### Version 163 — May 2026

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Repository Structure](#2-repository-structure)
3. [Application Entry Point](#3-application-entry-point)
4. [Configuration](#4-configuration)
5. [Database Layer](#5-database-layer)
6. [Core Engines](#6-core-engines)
7. [API Routes](#7-api-routes)
8. [Frontend Pages](#8-frontend-pages)
9. [Frontend JavaScript Modules](#9-frontend-javascript-modules)
10. [CSS Stylesheets](#10-css-stylesheets)
11. [Data Flow — End to End](#11-data-flow--end-to-end)
12. [Deployment](#12-deployment)

---

## 1. Project Overview

FAGE is an AI governance middleware platform. It sits between a company's existing business tools and the AI models those tools call. Every AI request passes through FAGE before reaching a provider like OpenAI or Anthropic. FAGE cleans the payload, scans it for sensitive content, routes it to the right model tier, tracks the cost against a department budget, and logs every significant decision permanently.

**Total codebase:** ~12,300 lines across 50+ files  
**Language split:** Python (backend) / Vanilla JS + HTML + CSS (frontend)  
**No frontend build step** — files served directly as static assets by FastAPI

---

## 2. Repository Structure

```
fage/
├── backend/
│   ├── main.py                        # FastAPI app entry point
│   ├── config.py                      # Central config — pricing, thresholds, departments
│   ├── requirements.txt               # Python dependencies
│   ├── .python-version                # Python 3.13
│   ├── api/                           # HTTP route handlers (15 files)
│   │   ├── routes_router.py           # Token routing pipeline
│   │   ├── routes_budget.py           # Department budget management
│   │   ├── routes_agentlake.py        # Agent registry & collision control
│   │   ├── routes_auditor.py          # Audit log read/export
│   │   ├── routes_dashboard.py        # KPI aggregation
│   │   ├── routes_keywords.py         # Sensitive term library CRUD
│   │   ├── routes_models.py           # Model registry CRUD
│   │   ├── routes_pruner.py           # Context pruning endpoint
│   │   ├── routes_reports.py          # Savings, risk, department reports
│   │   ├── routes_efficiency.py       # Bot efficiency analytics
│   │   ├── routes_agent_activity.py   # Per-agent call log
│   │   ├── routes_enrich.py           # Salesforce context enrichment
│   │   ├── routes_timeseries.py       # 30-day time-series data
│   │   ├── routes_voice.py            # Voice Guard PII redaction
│   │   └── routes_routing_config.py   # User-configurable routing rules
│   ├── core/                          # Business logic engines (9 files)
│   │   ├── router.py                  # Complexity scoring + tier routing
│   │   ├── pruner.py                  # Context sweeper
│   │   ├── keywords.py                # Sensitive term checker + PII regex
│   │   ├── budget.py                  # Spend tracking + throttle enforcement
│   │   ├── agentlake.py               # Agent registry + collision detection
│   │   ├── auditor.py                 # Black-box decision logger
│   │   ├── model_client.py            # OpenAI + Anthropic API client
│   │   ├── routing_config.py          # DB-persisted routing config
│   │   └── voice_guard.py             # NLP-powered voice PII redaction
│   └── database/
│       ├── db.py                      # SQLAlchemy engine + session
│       ├── models.py                  # 10 ORM table definitions
│       ├── seed.py                    # Base seed data
│       ├── populate_demo.py           # Demo data seeder
│       └── populate_enterprise.py     # Enterprise-scale seeder (Meridian Financial)
├── frontend/
│   ├── index.html                     # Main dashboard
│   ├── live.html                      # Live Operations Center (simulation)
│   ├── live-reports.html              # Enterprise demo reports (mock data)
│   ├── reports.html                   # Reports (real API data)
│   ├── models.html                    # Model Registry UI
│   ├── policy.html                    # Policy & Rules UI
│   ├── sandbox.html                   # Testing sandbox
│   ├── onboarding.html                # Setup wizard
│   ├── connect.html                   # Platform connection UI
│   ├── demo.html                      # Enterprise ROI demo
│   ├── roi.html                       # ROI calculator
│   ├── js/                            # 18 JavaScript modules
│   └── css/                           # 6 CSS stylesheets
├── Procfile                           # Heroku: web: uvicorn backend.main:app
└── requirements.txt                   # Root-level pip requirements for Heroku
```

---

## 3. Application Entry Point

**File:** `backend/main.py` — 260 lines

The FastAPI application starts here. On every server startup it runs three operations before accepting traffic:

### 3.1 `models.Base.metadata.create_all()`
Creates all database tables if they do not exist. This is SQLAlchemy's auto-create — safe to run repeatedly. On a fresh Heroku dyno or new PostgreSQL database, this builds the entire schema in seconds.

### 3.2 `_run_migrations()`
Runs lightweight `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` statements for new columns added after initial deployment. This is how the app handles schema evolution without Alembic migrations. Current migrations:
- `registered_agents.archived` — soft-delete flag for agents
- `model_registry.department` — department-specific model routing
- `department_budgets.throttle_tier` — configurable throttle floor tier

Each statement is wrapped in try/except so it is safe to re-run on every startup. If the column already exists, the exception is caught silently.

### 3.3 `_seed_on_startup()`
Populates essential reference data on a fresh database. Checks for existence before inserting so it never overwrites user changes. Seeds:

**Department Budgets** — 5 departments (Support, Sales, Marketing, Operations, Trips Team) with default monthly caps from `config.py`

**Model Registry** — 5 models across tiers:
- Claude Haiku 4.5 → Scout (Tier 1), $0.80/$4.00 per 1M tokens, enabled + default
- Claude Sonnet 4.6 → Advisor (Tier 3), $3.00/$15.00 per 1M tokens, enabled + default
- Claude Opus 4.6 → Strategist (Tier 4), $15.00/$75.00 per 1M tokens, enabled + default
- GPT-4o Mini → Scout (Tier 1), $0.15/$0.60 per 1M tokens, disabled
- GPT-4o → Advisor (Tier 3), $2.50/$10.00 per 1M tokens, disabled

**Sensitive Term Library** — 31 pre-built terms across 4 categories (HIPAA, financial, legal, HR) with block or escalate actions

**Routing Config** — Single-row settings table (ID=1) seeded from `config.py` with the default complexity token threshold and keyword list

### 3.4 Route Registration
All 15 API route files are registered with FastAPI using `include_router()`. The route prefix structure:

| Prefix | Module | Purpose |
|--------|--------|---------|
| `/api/prune` | routes_pruner | Context pruning |
| `/api/route` | routes_router | Token routing pipeline |
| `/api/budget` | routes_budget | Department budgets |
| `/api/agents` | routes_agentlake | Agent registry |
| `/api/audit` | routes_auditor | Audit log |
| `/api/dashboard` | routes_dashboard | KPI aggregates |
| `/api/keywords` | routes_keywords | Sensitive terms |
| `/api/models` | routes_models | Model registry |
| `/api/reports` | routes_reports | Report data |
| `/api/reports/bot-efficiency` | routes_efficiency | Bot efficiency |
| `/api/reports/agent-activity` | routes_agent_activity | Agent activity log |
| `/api/voice` | routes_voice | Voice Guard |
| `/api/routing-config` | routes_routing_config | Routing rules |
| `/api/timeseries` | routes_timeseries | Time-series data |
| `/api/enrich` | routes_enrich | Salesforce enrichment |

### 3.5 Admin Endpoints
Three demo/admin endpoints registered directly in `main.py`:
- `POST /api/admin/populate-demo` — loads 30-day demo history
- `POST /api/admin/populate-enterprise-demo` — loads Meridian Financial Group enterprise data (runs in background task)
- `POST /api/admin/reset-demo` — factory reset: clears transactions, audit events, agents; preserves caps

### 3.6 Static File Mount
The frontend is mounted last as a static file handler, serving everything in the `frontend/` directory. It must be registered after all API routes or it would intercept API calls.

---

## 4. Configuration

**File:** `backend/config.py` — 65 lines

Single source of truth for all system constants. Any parameter changed here applies immediately on next server restart without touching other files.

### Model Pricing (fallback when registry is empty)
```
MICRO_MODEL:    $0.15 input / $0.60 output per 1M tokens
FLAGSHIP_MODEL: $3.00 input / $15.00 output per 1M tokens
```

### Routing Thresholds
- `COMPLEXITY_TOKEN_THRESHOLD = 500` — payloads over 500 tokens route to Analyst or above
- `COMPLEXITY_KEYWORDS` — 35 keywords that trigger complexity escalation, including: legal, compliance, lawsuit, contract, audit, fraud, gdpr, hipaa, analyze, assessment, risk assessment, escalation, forecast, migration, architecture, and more

### Departments and Budget Caps
Five departments with default monthly caps: Support $200, Sales $300, Marketing $250, Operations $150, Trips Team $200

### Budget Thresholds
- `THROTTLE_TRIGGER_PERCENT = 100.0` — throttling activates at 100% of cap
- `WARN_TRIGGER_PERCENT = 80.0` — yellow warning state at 80%

---

## 5. Database Layer

### 5.1 Engine and Sessions

**File:** `backend/database/db.py` — 38 lines

Detects environment and configures the correct database:
- **Production (Heroku):** Reads `DATABASE_URL` environment variable (set automatically by Heroku Postgres add-on). Rewrites `postgres://` to `postgresql://` for SQLAlchemy compatibility.
- **Local development:** Falls back to `sqlite:///./fage.db` — a file-based SQLite database in the backend directory, zero setup required.

Provides `get_db()` as a FastAPI dependency that yields a session and closes it after each request.

### 5.2 ORM Models

**File:** `backend/database/models.py` — 185 lines, 10 table classes

#### `Customer`
Mock enterprise CRM contact. Fields: id, name, email, tier (free/pro/enterprise), department. Has relationships to Ticket and CRMRecord.

#### `Ticket`
Customer support ticket used as the primary AI payload source. Fields: id, customer_id (FK), subject, body, status (open/in_progress/closed), created_at.

#### `CRMRecord`
Key/value field on a customer's CRM profile. These are the shared records the Agentlake Traffic Cop protects from simultaneous writes. Fields: id, customer_id (FK), field_key, field_value, updated_at.

#### `RegisteredAgent`
An active AI digital worker. Fields: id, name (unique), department, source_platform (Salesforce/ServiceNow/HubSpot/Custom/etc.), permissions, target_table, target_record_id, status (idle/active/locked/queued), collision_policy (lock/queue/skip), locked_at, lock_reason, last_used_at, created_at, archived (soft-delete).

#### `DepartmentBudget`
Monthly AI spending cap and state per department. Fields: id, department (unique), monthly_cap_usd, current_spend_usd, period_start, throttled (bool), override_granted (bool), throttle_tier (int, 1–4 — the minimum model tier allowed when throttled, defaults to 1=Scout).

#### `TokenTransaction`
Every single AI model call. The source of truth for all financial analytics. Fields: id, department, source_platform, agent_id (FK nullable), model_tier, input_tokens, output_tokens, cost_usd, timestamp, routing_reason (ROUTINE/COMPLEX/THROTTLED/MODERATE/BLOCKED), was_pruned (bool), tokens_saved.

#### `AuditEvent`
Immutable black-box record for every high-stakes AI decision. Written once, never modified. Fields: id, event_type (ROUTING/THROTTLE/LOCK/DECISION), agent_id (FK nullable), department, model_tier, context_snapshot (JSON string — frozen budget state at decision time), prompt_payload (first 2,000 chars of what the AI saw), rationale (plain-English justification), decision_outcome, risk_level (low/medium/high/critical), timestamp.

#### `ModelRegistry`
Company-registered AI models with tier classification and cost rates. Fields: id, display_name, model_id (the exact API identifier), provider (OpenAI/Anthropic/Azure/Google/Mistral/Custom), tier (1–4), cost_input_per_1m, cost_output_per_1m, is_enabled, is_default, department (null = global, set = department-specific routing), notes, created_at.

#### `VoiceEvent`
Voice transcript processed by Voice Guard. Tracks every redaction event. Fields: id, timestamp, call_id, platform (Genesys/AWS Connect/Salesforce Voice/etc.), department, raw_transcript (stored only if no PII found), clean_transcript (redacted version), redactions_count, pii_types_found (JSON list), detection_method (rule/ai/both/none), confidence_score (0.0–1.0), flagged_for_review, processing_ms, detection_details (JSON array of per-entity details).

#### `SensitiveTerm`
Company-configured sensitive word or phrase. Fields: id, term (unique), category (legal/hipaa/financial/hr/custom), action (flag/escalate/block), department (null = global), created_at.

#### `RoutingConfig`
Single-row settings table (always ID=1). Stores the live token complexity threshold and keyword list as JSON. Has a Python `@property` that deserializes the JSON on access. Updated by the Policy & Rules page, read by the router on every call.

### 5.3 Seed Data Files

**`database/seed.py`** — Utility seed with historical token transactions and audit events for the base demo scenario. Populates 4 departments with realistic spend levels (Support 71%, Marketing 99.6%, etc.) and throttle_tier defaults.

**`database/populate_demo.py`** — Demo mode seeder. Loads 30 days of transaction history across 4 departments. Marketing is throttled (over cap). Produces the audit events shown in the dashboard demo. Accessible via `POST /api/admin/populate-demo`.

**`database/populate_enterprise.py`** — Enterprise-scale seeder simulating Meridian Financial Group. 12 named AI agents across 4 departments, 9,000+ token transactions across 30 days, 12 rich audit events covering GDPR requests, HIPAA flags, agent collisions, and budget throttle events. Marketing budget is capped. Runs as a background task to avoid HTTP timeout.

---

## 6. Core Engines

### 6.1 Context Pruning Sweeper

**File:** `backend/core/pruner.py` — 234 lines

Strips junk from AI payloads before they are sent to any model. Reduces token cost with no loss of useful content.

**Filters applied in order:**

1. **`strip_html(text)`** — Removes `<style>`, `<script>`, and all HTML tags. Decodes HTML entities (`&nbsp;`, `&amp;`, `&lt;`, `&gt;`, `&copy;`, `&#39;`, `&quot;`).

2. **`strip_email_headers(text)`** — Removes raw email header lines matching patterns: From, To, Cc, Bcc, Date, Reply-To, Message-ID, X-* headers, MIME-Version, Content-Type, Received, DKIM-Signature, and more.

3. **`strip_reply_chains(text)`** — Removes everything after common reply chain dividers: `-----Original Message-----`, `From:` at line start, `On ... wrote:`, `________________________________`, Outlook reply blocks, Gmail quoted text markers.

4. **`strip_legal_disclaimers(text)`** — Removes corporate legal boilerplate blocks matching patterns like CONFIDENTIALITY NOTICE, DISCLAIMER, This email and any attachments, GDPR notice blocks, etc.

5. **`strip_signatures(text)`** — Removes email signature blocks. Detects signatures by presence of common signature markers (Best regards, Kind regards, Sincerely, Thanks, —) followed by name/title/company/phone patterns.

6. **`collapse_whitespace(text)`** — Normalizes multiple blank lines to a single blank line. Strips leading/trailing whitespace.

**`estimate_tokens(text)`** — Estimates token count using the OpenAI standard approximation: 1 token ≈ 4 characters (`len(text) / CHARS_PER_TOKEN`).

**`prune(text)`** — Runs all filters in sequence. Returns a dict with: `cleaned_text`, `original_token_estimate`, `cleaned_token_estimate`, `tokens_saved`, `compression_pct`, and a `filters_applied` list showing which filters removed content.

**Typical result:** 40–65% compression on enterprise emails with HTML formatting, legal disclaimers, and reply chains.

---

### 6.2 Intelligent Token Router & Model Cascader

**File:** `backend/core/router.py` — 342 lines

The central routing engine. Takes a text payload and returns a complete routing decision with model selection, cost calculation, and pruning savings.

**`score_complexity(text, threshold, keywords)`** — Classifies payload as ROUTINE, MODERATE, or COMPLEX using two signals:
- Keyword match: any term from the complexity keyword list found in the text
- Token count: payload token estimate exceeds the configured threshold

Rules applied in priority order:
- Keywords AND over threshold → **COMPLEX** (routes to Advisor, Tier 3)
- Keywords OR over threshold → **MODERATE** (routes to Analyst, Tier 2)
- Neither → **ROUTINE** (routes to Scout, Tier 1)

**`_get_model_from_registry(tier_num, db, department)`** — Looks up the appropriate model from the ModelRegistry table. Priority cascade:
1. Department-specific default model for this tier
2. Department-specific any-enabled model for this tier
3. Global default model for this tier
4. Global any-enabled model for this tier
5. If nothing found: Analyst (Tier 2) cascades UP to Advisor (Tier 3); all other tiers cascade down

**`route(text, department, db, auto_prune, is_throttled, throttle_tier, force_complex)`** — Full pipeline:

1. **Tier tag detection** — Checks first 100 characters for explicit tier tags: `[scout]`, `[analyst]`, `[advisor]`, `[strategist]`. Allows source systems (e.g. Salesforce) to force-select a tier. Strips the tag before processing.
2. **Pruning** — Calls `prune()` if `auto_prune=True`
3. **Complexity scoring** — Calls `score_complexity()` with live threshold and keywords from the RoutingConfig DB table
4. **Sensitive term override** — If `force_complex=True` (set by keywords.py when an escalation term matched), forces COMPLEX regardless of content score
5. **Tier mapping** — Maps complexity to tier number, with throttle logic:
   - If `is_throttled=True`: uses `throttle_tier` (department-configured floor, clamped 1–4) as the tier ceiling
   - If explicit tag: uses that tier directly
   - If force_complex: Tier 4 (Strategist)
   - If COMPLEX: Tier 3 (Advisor)
   - If MODERATE: Tier 2 (Analyst)
   - If ROUTINE: Tier 1 (Scout)
6. **Model lookup** — Calls `_get_model_from_registry()` for the resolved tier
7. **Model call** — Calls `model_client.call_model()` with the selected model ID
8. **Cost calculation** — Multiplies actual token counts by registry rates
9. **Pruning savings** — Calculates dollar value of tokens saved by pruning

Returns a complete routing report including all of the above plus the simulated/live model response.

---

### 6.3 Departmental Budget Allocator & Auto-Throttle

**File:** `backend/core/budget.py` — 150 lines

Tracks AI spending per department and enforces monthly caps.

**`get_all_budgets(db)`** — Returns budget status for all departments, enriched with computed fields.

**`get_budget(db, department)`** — Returns budget status for one department.

**`set_cap(db, department, new_cap)`** — Updates a department's monthly cap. Creates the department if it doesn't exist yet (supports onboarding flow). Automatically clears the throttle flag if the new cap gives the department headroom below the throttle threshold.

**`grant_override(db, department)`** — Supervisor action. Sets `throttled=False` and `override_granted=True`. The department can use all model tiers again until the next cap breach.

**`revoke_override(db, department)`** — Reverses a grant. Re-evaluates whether the department should be throttled based on current spend.

**`set_throttle_tier(db, department, tier)`** — Sets the minimum model tier a department is allowed to use when throttled. Validates tier is 1, 2, 3, or 4. Saves to `DepartmentBudget.throttle_tier`.

**`reset_period(db, department)`** — Resets spend to zero, clears throttle and override flags, resets period_start to now. Simulates start of a new billing month.

**`_enrich(b)`** — Internal helper. Adds computed fields to a raw `DepartmentBudget` row:
- `used_pct` — percentage of cap consumed
- `remaining_usd` — dollars left this period
- `state` — "healthy", "warning" (≥80%), or "throttled" (≥100%)
- `throttle_tier` — the configured floor tier (read with `getattr` fallback for backwards compatibility)
- `throttle_tier_name` — human-readable tier name (Scout/Analyst/Advisor/Strategist)

---

### 6.4 Sensitive Term Library & PII Blocker

**File:** `backend/core/keywords.py` — 244 lines

Scans every incoming AI request for sensitive content before it reaches any model.

**`check_terms(db, text, department, skip_pii)`** — Main entry point. Loads active terms from the SensitiveTerm table (filtered to global terms + department-specific terms). Applies two detection layers:

**Layer 1 — Regex PII patterns:** Independent of the configured term library. Detects:
- SSN patterns: `\b\d{3}-\d{2}-\d{4}\b`, `\b\d{9}\b` with SSN context words
- Credit card numbers: 13–16 digit sequences with card-number context
- Phone numbers: standard US format patterns
- Email addresses in suspicious contexts
- Bank account and routing number patterns

**Layer 2 — Term matching:** Iterates through all active SensitiveTerm rows. Checks for case-insensitive substring match in the payload text.

**Priority logic:** If any term or pattern has action `"block"`, the entire request is rejected regardless of other matches. If only `"escalate"` terms matched, the request is allowed but marked for force-complex routing.

Returns: `triggered` (bool), `action` ("block", "escalate", or "none"), `matches` (list of matched terms), `top_match` (highest-priority match).

---

### 6.5 Agentlake Registry & Concurrency Traffic Cop

**File:** `backend/core/agentlake.py` — 284 lines

Tracks all registered AI agents and prevents concurrent write collisions on shared records.

**`infer_platform(name, explicit)`** — Infers the source platform from agent name prefix. Prefix map: `SF-` → Salesforce, `SN-` → ServiceNow, `HB-/HS-` → HubSpot, `MS-` → Microsoft, `ZD-` → Zendesk, `SAP-` → SAP. Falls back to "Custom".

**`register_agent(db, name, department, permissions, target_table, collision_policy, source_platform)`** — Registers a new agent. Raises `ValueError` if agent name already exists (unique constraint).

**`claim_record(db, agent_id, table_name, record_id)`** — Core Traffic Cop function. Before an agent writes to any record, it must claim it. Steps:
1. Load the requesting agent from DB
2. Query for any other active agent already holding a claim on `table_name + record_id`
3. If no conflict: grant claim, set agent status to "active", store target table and record ID
4. If conflict detected: lock BOTH agents immediately (set status="locked", locked_at=now, lock_reason="COLLISION DETECTED"), write a CRITICAL audit event, raise a collision exception

**`release_agent(db, agent_id)`** — Supervisor action. Clears lock state, resets status to "idle", clears target record.

**`list_agents(db, include_archived)`** — Returns all agents (excluding archived by default) serialized as dicts with status, platform, department, permissions, last_used_at.

**`_serialize(agent)`** — Converts a RegisteredAgent ORM object to a clean dict for API responses.

---

### 6.6 AI Decision Auditor — The Black Box Recorder

**File:** `backend/core/auditor.py` — 304 lines

Writes permanent, immutable records of every high-stakes AI decision to both the database and an append-only JSONL flat file.

**`write_audit_event(db, event_type, department, routing_decision, routing_reason, prompt_payload, model_tier, agent_id, matched_keywords, cost_usd, decision_outcome, context_snapshot)`** — Main entry point. Called by routes_router.py and routes_enrich.py for any COMPLEX, THROTTLED, BLOCKED, or collision event.

Steps:
1. Classify risk level based on matched_keywords and routing_decision:
   - `critical` — keywords: lawsuit, fraud, breach, gdpr, hipaa, regulatory, or BLOCKED events
   - `high` — keywords: legal, compliance, audit, contract, escalate; or LOCK/THROTTLED events
   - `medium` — COMPLEX routing with no critical/high keywords
   - `low` — everything else
2. Generate a plain-English rationale statement describing exactly what triggered the decision and what it cost
3. Write `AuditEvent` row to database
4. Append the same event as a JSON object to `audit_logs/fage_audit.jsonl` (one JSON object per line, append-only)

**`generate_rationale(event_type, department, routing_decision, routing_reason, matched_keywords, model_tier, cost_usd, context_snapshot)`** — Builds the human-readable rationale string. Different templates for ROUTING (flagship invoked), THROTTLED (budget cap enforced), BLOCKED (sensitive term), LOCK (agent collision), and OVERRIDE events.

**`get_audit_log(db, limit, offset, department, risk_level, event_type)`** — Returns paginated audit events with optional filters. Newest first.

**`export_audit_jsonl()`** — Returns the raw content of the append-only JSONL file for download. Each line is a complete, self-contained JSON object compatible with SIEM tools and log aggregators.

---

### 6.7 Model Client — Live & Simulated API Calls

**File:** `backend/core/model_client.py` — 198 lines

Unified interface for making AI model calls. Supports two modes controlled by the `FAGE_MODEL_MODE` environment variable.

**`get_mode_info()`** — Returns current mode ("live" or "simulated") and provider ("openai" or "anthropic").

**`call_model(text, model_id, fallback_tier)`** — Makes the actual model call:

**Simulated mode** (`FAGE_MODEL_MODE=simulated`): No API calls made. Generates realistic fake token counts using the text length as a seed. Returns a canned response. Used for demos without API keys.

**Live mode** (`FAGE_MODEL_MODE=live`):
- **OpenAI** (`FAGE_PROVIDER=openai`): Calls `openai.chat.completions.create()` with the specified `model_id`. Uses `OPENAI_API_KEY` from environment. System prompt instructs the model to act as a helpful enterprise AI assistant.
- **Anthropic** (`FAGE_PROVIDER=anthropic`): Calls `anthropic.messages.create()` with the specified `model_id`. Uses `ANTHROPIC_API_KEY` from environment.

Returns: `model_id` (actual model used), `input_tokens`, `output_tokens`, `response_text`, `provider`.

---

### 6.8 Voice Guard

**File:** `backend/core/voice_guard.py` — 1,013 lines

The most sophisticated engine in the codebase. Performs NLP-powered PII detection and redaction on voice call transcripts using Microsoft Presidio backed by spaCy.

**PII Entity Types Detected:**
- `PERSON` — Names of people
- `PHONE_NUMBER` — Phone numbers (any format)
- `EMAIL_ADDRESS` — Email addresses
- `CREDIT_CARD` — Credit card numbers
- `US_SSN` — US Social Security Numbers
- `IBAN_CODE` — Bank account numbers
- `US_BANK_NUMBER` — US bank routing/account numbers
- `DATE_TIME` — Dates of birth and appointments
- `LOCATION` — Addresses and locations
- `US_DRIVER_LICENSE` — Driver's license numbers
- `US_PASSPORT` — Passport numbers
- `MEDICAL_LICENSE` — Medical identifiers
- `NRP` — National Registration/Passport numbers

**`process_transcript(transcript, call_id, platform, department, sensitivity)`** — Main entry point. Steps:
1. Run Presidio analyzer against the transcript
2. Collect all detected PII entities with start/end offsets, entity type, and confidence score
3. Run custom regex patterns for patterns Presidio might miss (phone numbers in spoken format, "my SSN is", etc.)
4. Anonymize: replace each detected PII span with a labelled placeholder like `[REDACTED-PHONE]`, `[REDACTED-SSN]`
5. Calculate confidence score (weighted average of individual entity scores)
6. Write a `VoiceEvent` record to the database
7. Return the clean transcript, redaction count, PII types found, and processing time

**Sensitivity levels:** `standard` (confidence ≥ 0.7), `strict` (confidence ≥ 0.5), `maximum` (all detections).

**Fallback:** If Presidio/spaCy is unavailable, falls back to regex-only detection with patterns for SSNs, credit cards, phone numbers, and email addresses.

---

### 6.9 Routing Config

**File:** `backend/core/routing_config.py` — 79 lines

Manages the single-row `RoutingConfig` table that persists the live routing rules.

**`get_routing_config(db)`** — Returns the current RoutingConfig row (always ID=1). Creates it from `config.py` defaults if it doesn't exist.

**`set_threshold(db, threshold)`** — Updates the complexity token threshold. Validates range (50–2000).

**`add_keyword(db, keyword)`** — Adds a new complexity keyword. Prevents duplicates. Rejects keywords in the protected list (those seeded from `config.py`).

**`remove_keyword(db, keyword)`** — Removes a keyword. Blocks removal of protected keywords.

**`PROTECTED_KEYWORDS`** — The full set of keywords from `config.py`. These cannot be removed through the UI to preserve baseline routing quality.

---

## 7. API Routes

### 7.1 Token Router — `routes_router.py`

`POST /api/route` — Full routing pipeline for one payload.

**Request body:** `text`, `department`, `auto_prune` (bool), `is_test` (bool), `agent_id`, `agent_name`, `source_platform`, `voice_guard_processed` (bool).

**Processing steps:**
1. Load department budget — check throttle state and throttle_tier
2. Check sensitive terms — block or flag for escalation
3. Resolve or auto-register the calling agent
4. Call `core/router.py route()` with throttle state and throttle_tier
5. Persist TokenTransaction record
6. Update department spend — set throttled=True if cap exceeded
7. Write audit event if COMPLEX, THROTTLED, or BLOCKED
8. Return full routing report

**Response:** 450+ fields including complexity, tier, model name, token counts, cost, pruning savings, routing reason, budget position, was_throttled flag.

Returns HTTP 451 if blocked by sensitive term policy, with structured error including which term matched.

---

### 7.2 Budget Routes — `routes_budget.py`

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/budget` | All department budgets |
| `GET` | `/api/budget/{department}` | Single department |
| `POST` | `/api/budget/{department}/cap` | Set monthly cap |
| `POST` | `/api/budget/{department}/override` | Grant throttle override |
| `POST` | `/api/budget/{department}/revoke` | Revoke override |
| `POST` | `/api/budget/{department}/reset` | Reset spend to zero |
| `PATCH` | `/api/budget/{department}/throttle-tier` | Set throttle floor tier (1–4) |

**BudgetStatus response model** includes: department, monthly_cap_usd, current_spend_usd, remaining_usd, used_pct, throttled, override_granted, period_start, state, throttle_tier, throttle_tier_name.

---

### 7.3 Agentlake Routes — `routes_agentlake.py`

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/agents` | All registered agents |
| `POST` | `/api/agents` | Register new agent |
| `PATCH` | `/api/agents/{id}` | Update agent (name, department, permissions, etc.) |
| `DELETE` | `/api/agents/{id}` | Soft-delete (archive) agent |
| `POST` | `/api/agents/claim` | Agent claims a record before writing |
| `POST` | `/api/agents/{id}/release` | Supervisor releases locked agent |
| `POST` | `/api/agents/simulate-collision` | Force a collision between two agents (demo) |

---

### 7.4 Auditor Routes — `routes_auditor.py`

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/audit` | Paginated audit log (filterable by department, risk, event type, date) |
| `GET` | `/api/audit/{id}` | Single audit event with full rationale and context snapshot |
| `GET` | `/api/audit/export` | Download full JSONL audit file |

---

### 7.5 Dashboard Routes — `routes_dashboard.py`

`GET /api/dashboard` — Returns all KPIs in a single response to minimize frontend round-trips.

Returns: total_spend_today, month_spend, tokens_saved_by_pruning, pruning_cost_saved, active_agents, locked_agents, idle_agents, throttled_count, total_calls, routine_calls, complex_calls, throttled_calls, blocked_count (last 24h), per-department budget array, model tier distribution, recent audit events (last 5), savings breakdown, governance stats (requests_blocked, escalated_to_flagship, flagged_in_audit, pii_detected, budget_overruns_prevented, agent_collisions_resolved), ROI summary.

---

### 7.6 Keyword Routes — `routes_keywords.py`

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/keywords` | All sensitive terms |
| `POST` | `/api/keywords` | Add new term |
| `PATCH` | `/api/keywords/{id}` | Update term or action |
| `DELETE` | `/api/keywords/{id}` | Delete term |

---

### 7.7 Model Registry Routes — `routes_models.py`

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/models` | All models (filterable by tier, provider, enabled) |
| `POST` | `/api/models` | Register new model |
| `PATCH` | `/api/models/{id}` | Update model (pricing, tier, enabled status, department) |
| `DELETE` | `/api/models/{id}` | Delete model |
| `POST` | `/api/models/{id}/set-default` | Set as default for its tier |

---

### 7.8 Reports Routes — `routes_reports.py`

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/reports/savings` | Cost savings breakdown (pruning + routing) with date range |
| `GET` | `/api/reports/risk` | Risk events by category and timeline |
| `GET` | `/api/reports/departments` | Per-department scorecard |
| `GET` | `/api/reports/timeseries` | Daily spend and call volume for charts |

All report endpoints accept `start_date` and `end_date` query parameters.

---

### 7.9 Enrichment Routes — `routes_enrich.py`

`POST /api/enrich` — Salesforce integration endpoint. Accepts a full case context (record_id, record_type, raw_text, department, agent_name, source_platform). Runs the complete FAGE pipeline: prune → keyword scan → route → persist transaction → update budget → write audit event. Returns the same routing report as `/api/route` plus case metadata.

---

### 7.10 Time-Series Routes — `routes_timeseries.py`

`GET /api/timeseries/daily` — Returns daily spend and call volume for the last 30 days. Aggregates TokenTransaction records by day. Used for the 30-Day Spend & Activity Trends chart on the main dashboard.

---

### 7.11 Voice Guard Routes — `routes_voice.py`

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/voice/process` | Process a transcript — returns redacted version |
| `GET` | `/api/voice/events` | Recent voice events log |
| `GET` | `/api/voice/stats` | Aggregate redaction statistics |

---

### 7.12 Routing Config Routes — `routes_routing_config.py`

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/routing-config` | Current threshold and keyword list |
| `PATCH` | `/api/routing-config/threshold` | Update token complexity threshold |
| `POST` | `/api/routing-config/keywords` | Add a complexity keyword |
| `DELETE` | `/api/routing-config/keywords/{keyword}` | Remove a keyword |

---

## 8. Frontend Pages

### 8.1 Main Dashboard — `index.html`

The live command center for a real FAGE deployment. Connects to live API endpoints and updates every 15 seconds.

**Header:** FAGE brand (stacked title/subtitle), connection status indicator in center, nav links on right (Connect & Setup, Policy & Rules, Reports, Models, Sandbox, LIVE, Reset, Guide).

**KPI Strip (6 cards):**
- Total Spend Today
- Tokens Saved (Pruning) — count + estimated dollar savings
- Month Spend — billing period total
- Active Agents — count with active/locked/idle breakdown
- Throttled Depts — count, turns red when >0
- Blocked Requests — PII/sensitive term blocks in last 24h

**Secondary Stats Bar:** Scout/Analyst/Advisor/Strategist call counts with percentages, Overall Budget Used %, Pruning Saved (est.), Refresh button.

**Blocked Request Alert System:** Pulsing red banner appears automatically when blocked requests exist in the last 24h. Shows count and "Review Blocked Events" button that filters the audit log to blocked-only view.

**Panels (all collapsible, header sections are resizable/draggable):**
- **Agentlake Registry & Budget** — Agent table + department budget bars with throttle floor selectors
- **Routing Decision Feed** — Scrolling log of routing decisions with filters (risk level, tier, blocked toggle)
- **30-Day Spend & Activity Trends** — Chart.js line chart of daily spend and call volume
- **AI Decision Audit Log** — Filterable table (department, risk level, date range). Click any row to expand full rationale + budget snapshot + prompt payload. Download CSV/PDF.

**Right column:** Governance Event Stream — filterable real-time ticker of governance events.

---

### 8.2 Live Operations Center — `live.html`

Enterprise simulation dashboard for demos, presentations, and investor walkthroughs. Generates a continuous stream of realistic AI governance events automatically — no manual interaction required.

**Simulation engine:** A `tick()` function runs on a configurable interval (default ~2.5 seconds). Each tick randomly selects from 6 event types with weighted probabilities:
- `routine` (40%) — Scout tier routing, low cost
- `complex` (25%) — Advisor tier routing, keyword match, audit event generated
- `blocked` (10%) — Sensitive term or PII block, red flash overlay, CRITICAL audit event
- `collision` (5%) — Agent lock event, agents frozen
- `throttled` (10%) — Budget cap hit, department downgraded to floor tier
- `pruning` (10%) — Pruning event with token savings shown

**Demo scenario:** Meridian Financial Group. 4 departments. 12 AI agents. Simulates a realistic enterprise AI operations picture.

**Live KPI Strip:** Spend, Savings, Blocked Requests, Throttled Departments — all update in real time as events fire.

**Model Tier Bar:** Animated bar showing Scout/Analyst/Advisor/Strategist distribution.

**Agent Status Grid:** 12 agents with live status badges (active/idle/locked).

**Department Budget Bars:** Spend vs. cap per department, fills in real time.

**Routing Decision Feed:** Scrolling log of routing decisions as they happen.

**AI Decision Audit Log:** Expandable audit events with rationale.

**Governance Event Stream:** Filterable ticker with type badges (BLOCKED, COLLISION, THROTTLED, COMPLEX, PRUNING, ROUTINE).

---

### 8.3 Reports — `reports.html`

Six-tab reporting suite connected to real API data. Shared date range picker (20 presets + custom range) applies across all tabs.

**Tab 1 — Savings:** Total saved with FAGE vs. without, pruning savings by category, model routing savings by tier, daily cost timeline.

**Tab 2 — Risk & Compliance:** Blocked request volume by category (PII/Financial/Legal/HR), audit event timeline, block vs. escalate ratio, high-risk event log.

**Tab 3 — Departments:** Per-department scorecard — total calls, economy vs. premium split, spend, pruning savings, budget health.

**Tab 4 — Bot Efficiency:** Per-agent performance table — call volume, average cost per call, premium model %, pruning rate.

**Tab 5 — Agent Activity:** Full call log per agent. Filterable by department, platform, model tier, date range. Exportable to CSV.

**Tab 6 — ROI Calculator:** Input fields (number of agents, daily call volume, current cost per call) produce projected annual savings, payback period, and cost reduction percentage.

---

### 8.4 Live Reports — `live-reports.html`

Same six-tab layout as `reports.html` but 100% mock data — no API calls. Used for the Meridian Financial Group enterprise demo. Numbers are hardcoded to tell a compelling story: $8,742 saved, 94,300 calls processed, 12 compliance events, 0 data corruption incidents.

Tab buttons have IDs (`lrTabSavings`, `lrTabRisk`, etc.) for the guided tour system to target.

---

### 8.5 Model Registry — `models.html`

CRUD interface for managing which AI models FAGE can route to.

**Tier reference cards** — Four visual cards explaining Scout/Analyst/Advisor/Strategist with cost ranges and use cases.

**Registered Models table** — All models with provider, tier, API model ID, input/output cost per 1M tokens, enabled status, default flag, and edit button.

**Filters** — Filter by tier, provider, enabled status.

**Add/Edit Modal** — Form fields: display name, provider, API model ID, tier selector (radio buttons), input cost, output cost, business unit assignment (global or specific department), enabled checkbox, default checkbox, notes.

---

### 8.6 Policy & Rules — `policy.html`

Configuration center for all governance rules.

**Routing & Budget Thresholds panel:**
- Complexity threshold slider — token count at which payloads escalate
- Warning threshold — budget % that triggers yellow warning
- Throttle threshold — budget % that triggers auto-throttle
- Max context tokens — hard ceiling on request size

**Complexity Keywords panel:** Add/remove keywords that trigger routing escalation. Protected keywords (from config.py) cannot be removed. Shows full list with delete buttons.

**Sensitive Term Library:** CRUD table for blocked and escalated terms. Each row shows term, category, action, and delete button. Add term form with fields for term text, category, and action (Block/Escalate).

---

### 8.7 Sandbox — `sandbox.html`

Testing environment for FAGE's core engines in isolation.

**Pruner test:** Paste any text. See before/after token counts, compression %, dollar cost avoided at each tier. Shows which filters were applied.

**Voice Guard test:** Submit text or transcript. See redacted version with detected PII highlighted.

**Router test:** Paste payload, select department, submit. See full routing decision: complexity score, tier, model, token counts, cost, cost without FAGE, savings amount.

---

### 8.8 Connect & Setup — `onboarding.html`

Six-screen wizard for connecting FAGE to external systems.

- **Screen 1:** Company name and industry
- **Screen 2:** Department configuration — names and monthly budget caps
- **Screen 3:** Review — summary of configuration before activation
- **Screen 4:** Launch — generates Salesforce Apex code snippet and integration instructions
- **Screen 5:** Platform connect — generates connection code for selected platform (Salesforce, ServiceNow, HubSpot, etc.)
- **Screen 6:** Voice Guard — configure voice platform integration

---

### 8.9 Demo & ROI Pages — `demo.html`, `roi.html`

Enterprise presentation tools. `demo.html` shows an interactive executive dashboard with Chart.js visualizations. `roi.html` is a standalone ROI calculator showing projected annual savings based on input parameters.

---

## 9. Frontend JavaScript Modules

### 9.1 `api.js` — 121 lines
HTTP client wrapper. Provides `apiGet()`, `apiPost()`, `apiPut()`, `apiPatch()`, `apiDelete()`. All functions use the same base URL (empty string = same origin). Also exports `downloadCsv()` (builds and downloads a CSV file from arrays) and `printSection()` (clones an element into a print overlay and triggers browser print).

### 9.2 `dashboard.js` — 301 lines
Main dashboard controller. Loads data from `/api/dashboard` every 15 seconds. Functions:
- `loadDashboard()` — fetches all KPIs and calls render functions
- `renderKpis(d)` — updates all 6 KPI cards and the stat bar
- `renderStatBar(d)` — fills in tier call counts and percentages
- `checkBlockedAlert(d)` — shows/hides the pulsing red blocked request banner
- `renderDepartmentBudgets(d)` — renders per-department budget bars in the left panel
- `renderRecentEvents(d)` — populates the recent high-stakes events strip
- Panel collapse/expand logic with smooth animation
- Section drag-to-reorder via SortableJS

### 9.3 `budget.js` — 224 lines
Department budget panel. Functions:
- `loadBudgets()` — fetches `/api/budget` and re-renders
- `renderBudgets()` — builds HTML for each department: spend bar, percentage, cap input, Set Cap button, Reset Month button, Grant/Revoke Override button, and **throttle floor tier dropdown** (the new `budget-throttle-row` element with tier selector)
- `renderLiveBudgetBars()` — simplified version for the live ops strip (no cap controls)
- `updateKpiThrottled()` — updates the Throttled Depts KPI card
- `doSetCap(department)` — calls `POST /api/budget/{dept}/cap`
- `doOverride(department)` — calls `POST /api/budget/{dept}/override`
- `doRevoke(department)` — calls `POST /api/budget/{dept}/revoke`
- `doReset(department)` — calls `POST /api/budget/{dept}/reset` (with confirm dialog)
- `doSetThrottleTier(department)` — reads selected tier from dropdown, calls `PATCH /api/budget/{dept}/throttle-tier`

### 9.4 `auditor.js` — 383 lines
Audit log display and interaction. Functions:
- `loadAuditLog()` — fetches `/api/audit` with active filters applied
- `renderAuditTable(events)` — builds the audit table rows with risk badges, event type badges, and blocked row highlighting (red background + 🛡 BLOCKED badge)
- `toggleRationale(eventId)` — expands/collapses the rationale detail row inline
- `jumpToMainAuditRow(eventId)` — expands the audit panel, scrolls to the targeted row, adds `audit-row-highlight` flash animation, auto-expands rationale
- `_doJumpToAuditEntry(eventId)` — inner scroll + highlight + rationale expand logic
- `downloadAuditCsv()` — downloads current filtered view as CSV
- `printAuditPdf()` — triggers browser print for the audit section
- Filter controls: department dropdown, risk level dropdown, date range preset

### 9.5 `agentlake.js` — 339 lines
Agent registry panel. Functions:
- `loadAgents()` — fetches `/api/agents` and renders
- `renderAgentTable(agents)` — builds rows with status badges (ACTIVE/IDLE/LOCKED), platform icons, permissions, last active time
- `releaseAgent(id)` — calls `POST /api/agents/{id}/release`
- `simulateCollision()` — calls `POST /api/agents/simulate-collision`
- `archiveAgent(id)` — soft-deletes an agent
- `openAddAgentModal()` / `saveAgent()` — add new agent form and submission
- `openEditModal(id)` / `updateAgent(id)` — edit existing agent

### 9.6 `event_stream.js` — 280 lines
Governance event stream panel. Functions:
- `loadEventStream()` — fetches `/api/audit?limit=25` and renders events as styled ticker items
- `addStreamEvent(event)` — adds a single event to the top of the stream, slides older events down
- Filter buttons: All Types, Blocked, Collision, Complex, Throttled
- Filter by department dropdown
- Search box for text filtering across event content
- Auto-scroll to top on new events
- Event badge color coding: BLOCKED=red, COLLISION=orange, THROTTLED=yellow, COMPLEX=blue, ROUTINE=gray

### 9.7 `timeseries.js` — 155 lines
30-Day Spend & Activity Trends chart. Uses Chart.js. Functions:
- `loadTimeseries()` — fetches `/api/timeseries/daily`
- `renderTimeseriesChart(data)` — renders a dual-axis line chart: spend (USD, left axis) and call volume (right axis). Two lines: daily spend and total calls. Responsive, dark theme.

### 9.8 `models.js` — 308 lines
Model Registry page. Functions:
- `loadModels()` — fetches `/api/models` with active filters
- `renderTierCards()` — renders the 4 tier reference cards (Scout/Analyst/Advisor/Strategist)
- `renderModelTable(models)` — builds the registry table
- `openAddModal()` / `openEditModal(model)` — opens the add/edit modal, populates fields
- `saveModel()` — validates required fields, calls POST (add) or PATCH (edit)
- `deleteModel(id)` — calls DELETE with confirmation
- `setDefault(id)` — calls POST to set as tier default
- Filter handlers for tier, provider, and enabled status dropdowns

### 9.9 `reports.js` — 935 lines
Multi-tab reports suite. Functions per tab:
- **Savings:** `loadSavingsTab()` — fetches `/api/reports/savings`, renders KPI cards (Total Saved, Without FAGE, With FAGE, Tier Routing Saved, Pruning Saved, Call Volume) and breakdown tables
- **Risk:** `loadRiskTab()` — fetches `/api/reports/risk`, renders block/escalate breakdown by category and risk event timeline
- **Departments:** `loadDeptsTab()` — fetches `/api/reports/departments`, renders per-department scorecard table
- **Bot Efficiency:** `loadEfficiencyTab()` — fetches `/api/reports/bot-efficiency`, renders per-agent performance table with AI efficiency rating
- **Agent Activity:** `loadActivityTab()` — fetches `/api/reports/agent-activity`, renders full call log with filters
- **ROI Calculator:** `initRoiTab()` — input-driven calculator, no API call, pure client-side math

Date range picker: 20 named presets (Last 7 Days, Last 30 Days, This Month, Last Quarter, Last Year, YTD, etc.) plus custom from/to date inputs. All tabs re-load when date range changes.

### 9.10 `keywords.js` — 121 lines
Sensitive term library UI. Functions:
- `loadKeywords()` — fetches `/api/keywords` and renders table
- `renderKeywordsTable(terms)` — builds rows with term, category, action badge (BLOCK=red, ESCALATE=yellow), delete button
- `addKeyword()` — reads form fields, calls `POST /api/keywords`
- `deleteKeyword(id)` — calls `DELETE /api/keywords/{id}`

### 9.11 `routing_rules.js` — 131 lines
Complexity routing rules panel (on Policy page). Functions:
- `loadRoutingConfig()` — fetches `/api/routing-config`
- `renderKeywordList(keywords, protected)` — renders keyword table with delete buttons disabled on protected keywords
- `updateThreshold()` — reads slider value, calls `PATCH /api/routing-config/threshold`
- `addKeyword()` / `deleteKeyword(keyword)` — add/remove from live keyword list

### 9.12 `router.js` — 89 lines
Router test panel (on Sandbox page). Functions:
- `runRouter()` — reads payload text and department selection, calls `POST /api/route`
- `renderRoutingResult(result)` — displays full routing decision: complexity badge, tier badge, model used, token counts, cost with and without FAGE, routing reason, pruning savings, simulated response

### 9.13 `sandbox.js` — 280 lines
Sandbox page controller. Coordinates three test panels:
- Pruner test — calls `/api/prune`, shows before/after comparison
- Voice Guard test — calls `/api/voice/process`, shows redacted transcript
- Router test — calls `/api/route`, shows full decision report

### 9.14 `voice_guard.js` — 575 lines
Voice Guard demo UI. Functions:
- `processTranscript()` — calls `/api/voice/process` with transcript text
- `renderRedactionResult(result)` — highlights redacted spans in the output with colored labels per PII type (red=SSN, orange=credit card, blue=name, etc.)
- `loadVoiceEvents()` — fetches recent voice events log
- `renderVoiceStats(stats)` — displays aggregate redaction statistics

### 9.15 `onboarding.js` — 752 lines
Setup wizard flow. Manages 6 screen states with forward/back navigation. Functions:
- `nextScreen(n)` / `prevScreen(n)` — screen transition with slide animation
- `saveCompanyInfo()` — persists company name and industry to localStorage
- `saveDepartments()` — calls `POST /api/budget/{dept}/cap` for each configured department
- `generateApexCode()` — builds the Salesforce Apex class snippet with the correct FAGE endpoint URL embedded
- `generateIntegrationCode(platform)` — builds integration code snippets for selected platform (Salesforce/ServiceNow/HubSpot/Dynamics/Zendesk)

### 9.16 `connect.js` — 964 lines
Platform connection page. The largest frontend module. Generates ready-to-paste integration code for every supported platform.

Supported platforms and the code generated for each:
- **Salesforce** — Apex class with `@future(callout=true)` for async HTTP callout + Flow invocable method
- **ServiceNow** — REST integration script with Business Rule trigger
- **HubSpot** — Webhook workflow action configuration JSON
- **Microsoft Dynamics 365** — Power Automate custom connector definition
- **Zendesk** — Apps framework extension with trigger setup
- **Custom/Generic** — cURL example + Python requests example

### 9.17 `help.js` — 287 lines
Per-page guided tour system. Functions:
- `startTour()` — reads `window.PAGE_TOUR_STEPS` if defined (page-specific steps), falls back to the default `TOUR_STEPS` array (11 steps for the main dashboard)
- `renderTourStep(index)` — finds the target element by ID, positions the tour tooltip (top/bottom/left/right), highlights the element with a semi-transparent overlay
- `nextTourStep()` / `prevTourStep()` — navigate between steps
- `closeTour()` — cleans up overlay and tooltip

Each page defines its own `window.PAGE_TOUR_STEPS` array inline in a script tag. Tour steps reference only elements visible in the default page layout (tab buttons, not hidden tab panes) to prevent the tour from breaking midway.

### 9.18 `demo.js` — 475 lines
Demo data management. Functions:
- `resetDemoData()` — calls `POST /api/admin/reset-demo`, reloads dashboard
- `populateDemoData()` — calls `POST /api/admin/populate-demo`
- `populateEnterpriseData()` — calls `POST /api/admin/populate-enterprise-demo`, shows loading indicator while background task runs
- `renderDemoCharts()` — renders Chart.js visualizations on `demo.html`

---

## 10. CSS Stylesheets

### `styles.css` — Global design system
Dark theme foundation. Defines CSS custom properties (variables) for the entire application:
- Color tokens: `--bg-base`, `--bg-panel`, `--bg-input`, `--border`, `--text-primary`, `--text-muted`, `--accent` (blue), `--accent-green`, `--accent-yellow`, `--accent-red`
- Typography: `--font-mono` (JetBrains Mono / monospace stack)
- Header layout and all shared header components
- Budget panel components: `.budget-item`, `.budget-bar-track`, `.budget-bar-fill`, `.budget-actions`, `.budget-throttle-row`, `.budget-throttle-select`, `.budget-throttle-hint`
- Button styles: `.btn-cap`, `.btn-override`, `.btn-revoke`, `.btn-reset`
- Audit log components: `.audit-row-highlight` flash animation (blue pulse on targeted rows)
- Status dot animations (pulsing green for live connections)
- KPI card styles, stat bar, section headers
- Tour overlay and tooltip styles

### `demo.css` — Dashboard layout
Main dashboard specific overrides:
- Full-viewport layout (`height: 100vh; overflow: hidden; flex-direction: column`)
- Two-column main content grid (`1fr 340px`)
- Dash section headers with collapse/expand behavior
- Routing feed table and row styles
- Stream event ticker styles with type-colored left borders

### `reports.css` — Reports page
Tab navigation, KPI strip for reports, savings card grid, risk event timeline, department scorecard table, ROI calculator input layout, date range picker, export button group.

### `models.css` — Model Registry
Tier reference card grid, model table with hover states, add/edit modal overlay and form layout, tier option radio button cards.

### `onboarding.css` — Setup wizard
Screen transition animations, department configuration grid, platform selection cards, code snippet display with syntax highlighting hints, progress indicator.

### `connect.css` — Platform connection
Platform card grid with hover states, code display panel with copy button, platform tab selector, connection status indicator.

---

## 11. Data Flow — End to End

```
External System (Salesforce, ServiceNow, Custom Bot)
          │
          │  POST /api/route  or  POST /api/enrich
          ▼
[main.py] FastAPI receives request
          │
          ▼
[routes_router.py]
  1. Load department budget → get is_throttled, throttle_tier
  2. Call keywords.py → check sensitive terms + PII patterns
     ├── BLOCK → write CRITICAL audit event → HTTP 451
     └── ESCALATE → set force_complex=True
  3. Resolve/auto-register calling agent
          │
          ▼
[core/router.py route()]
  4. Detect tier tag ([scout]/[analyst]/[advisor]/[strategist])
  5. Run pruner.py → strip HTML, headers, chains, disclaimers, sigs
  6. Load live routing config from RoutingConfig DB table
  7. Score complexity (keywords + token count)
  8. Apply force_complex override if escalation term matched
  9. Map to tier:
     - Throttled → throttle_tier floor (1–4, per department policy)
     - Forced tag → that tier directly
     - Sensitive escalation → Tier 4 (Strategist)
     - COMPLEX → Tier 3 (Advisor)
     - MODERATE → Tier 2 (Analyst)
     - ROUTINE → Tier 1 (Scout)
 10. Look up model from ModelRegistry
     (department-specific first, then global, then cascade)
 11. Call model_client.py → live API call (OpenAI or Anthropic)
 12. Calculate cost from actual token counts × registry rates
 13. Calculate pruning savings
          │
          ▼
[routes_router.py continues]
 14. Persist TokenTransaction (department, agent, tier, tokens, cost, timestamp)
 15. Update DepartmentBudget.current_spend_usd
     → Set throttled=True if cap reached
 16. Write AuditEvent if COMPLEX, THROTTLED, BLOCKED, or COLLISION
     → DB row + append to fage_audit.jsonl
          │
          ▼
[Response returned to caller]
  Full report: routing decision, model used, tokens, cost,
  pruning savings, budget position, audit ID, was_throttled
```

---

## 12. Deployment

**Platform:** Heroku (fage-engine-21cb49fe4806.herokuapp.com)  
**Current version:** v163

**Procfile:**
```
web: uvicorn backend.main:app --host 0.0.0.0 --port $PORT
```

**Environment variables required:**
```
FAGE_MODEL_MODE=live          # or "simulated" (no API keys needed)
FAGE_PROVIDER=anthropic       # or "openai"
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...         # optional if using Anthropic
DATABASE_URL=postgresql://... # set automatically by Heroku Postgres add-on
```

**Database:** PostgreSQL (Heroku Postgres add-on). Schema auto-created on first boot via `models.Base.metadata.create_all()`. New columns added via `_run_migrations()` on every startup — no manual migration steps required.

**Deploy command:**
```bash
cd fage
git push heroku main
```

**Local development:**
```bash
cd fage/backend
source venv/bin/activate
uvicorn main:app --reload --port 8001
# Open http://localhost:8001
# API docs: http://localhost:8001/docs
```

---

*Generated May 2026. FAGE v163. 12,300+ lines across 50+ files.*
