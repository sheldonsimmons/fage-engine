# FAGE — FinOps Agentlake & Governance Engine
## Complete Project Brief & Technical Explainer

---

## What Is FAGE?

FAGE is a fully functional enterprise AI middleware proof-of-concept (POC) built to solve three critical problems that emerge when large organizations start using AI at scale:

1. **Runaway API costs** — AI bills that explode without warning because nobody set spending limits
2. **Data corruption from agent sprawl** — Multiple AI bots overwriting each other's work on shared records
3. **Zero auditability** — No record of what the AI saw, decided, or why — a compliance nightmare

FAGE sits between a company's data systems and its AI tools. Every AI request passes through FAGE before reaching a model. FAGE cleans it, evaluates it, routes it to the right model, tracks the cost, and logs an immutable record of every decision.

---

## Technology Stack

| Layer | Technology | Why |
|---|---|---|
| Backend | Python 3.11 + FastAPI | Async, auto-generates API docs, best AI ecosystem |
| Database | SQLite + SQLAlchemy ORM | Zero setup, file-based, portable |
| Frontend | Vanilla HTML/CSS/JavaScript | No build step, runs anywhere, beginner-readable |
| Audit Logs | Append-only JSONL flat files | Simulates immutability, human-readable, exportable |
| AI Models | OpenAI (GPT-3.5-turbo + GPT-4o) | Live API calls with real token counts and costs |
| Alt Provider | Anthropic (Claude Haiku + Claude Sonnet) | Swappable via single environment variable |
| Config | python-dotenv (.env file) | Secure key management, never committed to git |

---

## The Five Core Engines

### Engine 1 — Context-Pruning Sweeper
**File:** `backend/core/pruner.py`

**What it does:**
Before sending any text to an AI model — which charges per word — FAGE runs the raw payload through a series of regex cleaning filters. It strips out everything that isn't useful to the AI:

- HTML tags, inline CSS, style blocks
- Email headers (From, To, Date, X-Mailer, MIME-Version)
- Email reply chain history (everything after "-----Original Message-----")
- Corporate legal disclaimer blocks (CONFIDENTIALITY NOTICE, GDPR/CCPA boilerplate)
- Email signatures (name, title, company, phone, address, website)
- Excessive whitespace and blank lines

**Proven result on test payload:**
- Raw email: 891 tokens (messy HTML corporate email with legal disclaimer and reply chain)
- After pruning: 325 tokens (only the meaningful content)
- Compression: 63.5% — 566 tokens eliminated
- Cost avoided on flagship model: $0.001698 per call

**Why this matters at scale:**
At 10,000 flagship model calls per day, this single filter saves approximately $16.98/day — roughly $6,200/year — before any other optimization is applied.

**Filters applied (in order):**
1. `strip_html` — removes all HTML/CSS
2. `strip_email_headers` — removes From/To/Date/MIME headers
3. `strip_reply_chains` — cuts everything after reply dividers
4. `strip_legal_disclaimers` — removes CONFIDENTIALITY NOTICE blocks
5. `strip_signatures` — removes name/title/company/phone blocks
6. `collapse_whitespace` — normalizes spacing

---

### Engine 2 — Intelligent Token Router & Model Cascader
**File:** `backend/core/router.py` + `backend/core/model_client.py`

**What it does:**
After pruning, FAGE evaluates the payload complexity and automatically selects the correct AI model tier. No human decision is required.

**Routing logic (in priority order):**

1. **Check throttle status** — if the requesting department has hit its budget cap, force micro-model regardless of complexity
2. **Keyword scan** — if any high-risk keyword is present, escalate to flagship
3. **Token count check** — if cleaned payload exceeds 150 tokens, escalate to flagship
4. **Default** — route to micro-model

**High-risk keywords that trigger flagship escalation:**
legal, compliance, lawsuit, contract, audit, fraud, critical, escalate, billing dispute, breach, regulatory, urgent, data loss, outage, enterprise, gdpr, hipaa

**Model tiers:**

| Tier | Model (OpenAI) | Input Cost | Output Cost | Used For |
|---|---|---|---|---|
| Micro (Economy) | gpt-3.5-turbo | $0.15/M tokens | $0.60/M tokens | ROUTINE requests |
| Flagship (Premium) | gpt-4o | $3.00/M tokens | $15.00/M tokens | COMPLEX requests |

**Routing decisions:**
- `ROUTINE` — micro model, cheap, fast
- `COMPLEX` — flagship model, expensive, thorough
- `THROTTLED` — department over budget cap, forced to micro regardless of complexity

**Live test results:**
- "What are your business hours?" → ROUTINE → gpt-3.5-turbo → $0.000025
- GDPR compliance audit request → COMPLEX → gpt-4o → $0.004740
- Same GDPR request when Marketing is over cap → THROTTLED → gpt-3.5-turbo → $0.000072 (98% cheaper)

**Provider switching:**
The model client supports OpenAI and Anthropic. Switching providers requires changing one line in the `.env` file:
```
FAGE_PROVIDER=openai      # uses GPT-3.5-turbo + GPT-4o
FAGE_PROVIDER=anthropic   # uses Claude Haiku + Claude Sonnet
```

---

### Engine 3 — Departmental Budget Allocator & Throttle Engine
**File:** `backend/core/budget.py`

**What it does:**
Every token transaction is tracked and mapped to the department that triggered it. Each department has a monthly spending cap. The engine enforces those caps automatically.

**Database tracking:**
- Every AI call writes a `TokenTransaction` record with: department, model tier, input tokens, output tokens, cost in USD, routing reason, pruning flag, tokens saved
- Running spend totals are updated in real time on the `DepartmentBudget` table

**States a department can be in:**
- `healthy` — under 80% of cap (green bar)
- `warning` — 80-99% of cap (yellow bar)
- `throttled` — at or over 100% of cap (red bar, THROTTLED badge)

**Automatic throttle behavior:**
When a department reaches 100% of its monthly cap:
1. The `throttled` flag is set to `True` in the database
2. All subsequent COMPLEX routing decisions for that department are overridden to micro-model
3. The dashboard displays a red bar and THROTTLED badge
4. A "Grant Override" button appears for supervisor action

**Supervisor controls:**
- **Set Cap** — update the monthly dollar limit for any department
- **Grant Override** — clear the throttle, restore flagship access, log the action
- **Revoke Override** — re-apply the throttle
- **Reset Month** — zero out the spend counter (simulates new billing period)

**Seed data (starting state):**
| Department | Cap | Spend | % |
|---|---|---|---|
| Support | $200 | $142.50 | 71% (healthy) |
| Sales | $300 | $87.20 | 29% (healthy) |
| Marketing | $250 | $249.10 | 99.6% (warning) |
| Operations | $150 | $23.80 | 16% (healthy) |

---

### Engine 4 — Agentlake Registry & Concurrency Traffic Cop
**File:** `backend/core/agentlake.py`

**What it does:**
Maintains a live inventory of all registered AI digital workers (agents) and their current target records. Acts as a traffic cop to prevent two agents from writing the same database record at the same time.

**The problem it solves:**
In an enterprise with many AI automations running simultaneously, two bots might both try to update the same customer record — the account health score, the contract value, the support ticket status. The last one to write wins and silently destroys the other's work. This is called a "write collision" and it's a major source of data corruption in AI-heavy organizations.

**How the Traffic Cop works:**
1. Before writing to any record, an agent must "claim" it by calling the `/api/agents/claim` endpoint
2. FAGE checks if any other active agent already holds a claim on that exact table + record ID combination
3. If no conflict: claim is granted, agent proceeds
4. If conflict detected: **both agents are immediately locked**, neither writes anything, and a collision alert fires

**Agent states:**
- `idle` — registered but not currently working
- `active` — holds a claim on a specific record, working
- `locked` — frozen by the Traffic Cop due to collision

**Registered agents in the POC:**
| Agent | Department | Permissions | Target |
|---|---|---|---|
| SupportBot-Alpha | Support | read, write | tickets |
| SupportBot-Beta | Support | read, write | tickets |
| SalesEnrich-1 | Sales | read, write | crm_records |
| MarketingMailer-1 | Marketing | read | customers |
| OpsLogger-1 | Operations | read, write, delete | crm_records |
| BillingAuditor-1 | Support | read | token_transactions |

**Collision simulation (live demo):**
The "Simulate Collision" button forces SupportBot-Alpha and SupportBot-Beta to simultaneously claim ticket #3 (the billing dispute ticket). Both lock instantly. No data is written. The supervisor uses "Release" buttons to resolve.

---

### Engine 5 — AI Decision Auditor (The Black Box Recorder)
**File:** `backend/core/auditor.py`

**What it does:**
Every high-stakes AI decision is written to two places simultaneously — the database and an append-only JSONL flat file. The file simulates an immutable black box: records are only ever appended, never modified or deleted.

**What triggers an audit event:**
- Any `COMPLEX` routing decision (flagship model invoked)
- Any `THROTTLED` routing decision (budget cap enforced)
- Any concurrency `LOCK` event (collision detected)
- Any supervisor `OVERRIDE` granted or revoked

**What each audit record captures:**
1. **Event type** — ROUTING, LOCK, OVERRIDE
2. **Frozen context snapshot** — exact budget position at the moment of the decision (cap, spend, % used, throttle state)
3. **Prompt payload** — the first 2,000 characters of what was sent to the model
4. **Plain-English rationale** — a generated justification statement explaining the decision logic in terms a compliance officer or lawyer can read
5. **Risk level** — low / medium / high / critical (based on keyword analysis)
6. **Decision outcome** — what model was used, what it cost

**Risk classification:**
- `critical` — keywords: lawsuit, fraud, breach, gdpr, hipaa, regulatory
- `high` — keywords: legal, compliance, audit, contract, escalate — OR any LOCK event — OR any THROTTLED event
- `medium` — COMPLEX routing with no critical/high keywords
- `low` — routine events

**Sample rationale (auto-generated for a COMPLEX routing event):**
> "FLAGSHIP MODEL INVOKED. Payload routed to the premium model tier after complexity analysis for the Support department. Trigger: High-risk keyword detected: 'legal'. High-risk keywords detected: "legal", "contract". Budget position at time of decision: 71.3% used ($142.51 of $200.00 cap). Call cost: $0.003405. Decision: flagship routing is warranted given the legal/compliance signals present."

**Export:**
The full JSONL audit file is downloadable at any time from the dashboard. Each line is a complete, self-contained JSON object. Format is compatible with SIEM tools, Splunk, and standard log aggregators.

**Audit log file location:** `backend/audit_logs/fage_audit.jsonl`

---

## The Mock CRM Database

**File:** `backend/database/models.py` + `backend/database/seed.py`

Seven SQLite tables simulate a real enterprise CRM:

| Table | Purpose |
|---|---|
| `customers` | 10 mock enterprise contacts across 4 departments |
| `tickets` | 5 support tickets (2 complex, 3 routine) |
| `crm_records` | Key/value CRM fields — the records agents fight over |
| `registered_agents` | Live agent registry with status and lock state |
| `department_budgets` | Monthly caps, running spend, throttle flags |
| `token_transactions` | Every AI call with full cost and routing data |
| `audit_events` | Immutable high-stakes decision log |

---

## The Executive Dashboard

**Files:** `frontend/index.html`, `frontend/css/styles.css`, `frontend/js/`

A single-page dark-theme web dashboard served directly by FastAPI. No build step required.

**Sections:**

**KPI Cards (top row)**
- Total Spend Today — live from DB, updates every 15 seconds
- Tokens Saved (Pruning) — cumulative compressed tokens + estimated dollar value
- Active Agents — total registered + active/locked/idle breakdown
- Throttled Departments — count, turns red when > 0

**Stats Bar**
- Total calls, micro vs flagship split with percentages
- Overall budget used % across all departments
- Estimated pruning savings in dollars
- Month-to-date spend

**Department Budgets Panel**
- Live progress bars per department (green/yellow/red)
- Set Cap, Reset Month, Grant Override, Revoke Override controls

**Agentlake Registry Table**
- All registered agents with real-time status badges
- Release button for locked agents
- Simulate Collision button for live demo

**Context-Pruning Sweeper Panel**
- Paste any raw text, click Run Sweeper
- Side-by-side before/after with token counts and compression %
- Cost avoided shown for both micro and flagship tiers
- "Use Pruned Text" sends raw payload to Router for full pipeline test

**Token Router & Model Cascader Panel**
- Paste payload, select department, click Route Payload
- Returns: complexity badge, model used, real token counts, actual cost, cost without pruning, pruning savings, budget %, simulated/live model response

**AI Decision Audit Log**
- Live table of all high-stakes decisions, newest first
- Click any row to expand: full rationale, context snapshot, prompt payload preview
- Download full JSONL audit file link

---

## Data Flow (End to End)

```
Raw Payload (email, ticket, log)
         │
         ▼
[PRUNER] Strip HTML, headers, chains, disclaimers, signatures
         │ Reports: before/after tokens, compression %, cost avoided
         ▼
[BUDGET CHECK] Is this department over its monthly cap?
         │
    ┌────┴────┐
    │ Over cap │ Under cap
    ▼          ▼
[THROTTLE]  [ROUTER] Score complexity
Force micro   │
              ├── ROUTINE → Micro model (gpt-3.5-turbo)
              └── COMPLEX → Flagship model (gpt-4o)
                       │
                       ▼
              [AGENTLAKE] Is another agent writing the same record?
                       │
              ┌────────┴────────┐
              │ Collision        │ Clear
              ▼                  ▼
          [LOCK both]     [Proceed with call]
                                 │
                                 ▼
                         [MODEL CLIENT]
                         Live API call → OpenAI or Anthropic
                         Real token counts returned
                                 │
                                 ▼
                         [BUDGET] Record spend
                         [AUDITOR] Write audit event (if COMPLEX/THROTTLED)
                                 │
                                 ▼
                         Response + full routing report returned
```

---

## API Endpoints

All endpoints auto-documented at `http://localhost:8001/docs`

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/health` | System status, model mode, provider |
| GET | `/api/config` | Current model mode and provider config |
| POST | `/api/prune` | Run context pruner, returns compression stats |
| POST | `/api/route` | Full routing pipeline, returns model response + costs |
| GET | `/api/budget` | All department budgets |
| POST | `/api/budget/{dept}/cap` | Update monthly cap |
| POST | `/api/budget/{dept}/override` | Grant throttle override |
| POST | `/api/budget/{dept}/reset` | Reset spend to zero |
| GET | `/api/agents` | All registered agents |
| POST | `/api/agents/claim` | Agent claims a record |
| POST | `/api/agents/simulate-collision` | Force collision demo |
| POST | `/api/agents/{id}/release` | Release locked agent |
| GET | `/api/audit` | Paginated audit log |
| GET | `/api/audit/{id}` | Single audit event with full rationale |
| GET | `/api/audit/export` | Download JSONL audit file |
| GET | `/api/dashboard` | All KPIs in one call |

---

## Environment Configuration

**File:** `backend/.env` (never committed to git)

```
FAGE_MODEL_MODE=live          # "simulated" or "live"
FAGE_PROVIDER=openai          # "openai" or "anthropic"

OPENAI_API_KEY=sk-...
OPENAI_MICRO_MODEL=gpt-3.5-turbo
OPENAI_FLAGSHIP_MODEL=gpt-4o

ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MICRO_MODEL=claude-haiku-4-5-20251001
ANTHROPIC_FLAGSHIP_MODEL=claude-sonnet-4-6
```

Setting `FAGE_MODEL_MODE=simulated` disables all API calls and runs entirely on local mock responses. No API keys needed in simulated mode.

---

## How To Run Locally

```bash
# 1. Navigate to backend
cd fage/backend

# 2. Activate virtual environment
source venv/bin/activate

# 3. Start the server
uvicorn main:app --reload --port 8001

# 4. Open dashboard in browser
# http://localhost:8001

# 5. API docs
# http://localhost:8001/docs
```

---

## Proven Test Results (Live GPT-4o)

| Test | Payload | Decision | Model | Cost |
|---|---|---|---|---|
| 1 | "What are your business hours?" | ROUTINE | gpt-3.5-turbo | $0.000025 |
| 2 | GDPR compliance audit request | COMPLEX | gpt-4o | $0.004740 |
| 3 | Long pipeline review (no keywords) | COMPLEX | gpt-4o | token count trigger |
| 4 | GDPR request, Marketing over cap | THROTTLED | gpt-3.5-turbo | $0.000072 (98% cheaper) |
| 5 | Two agents → same ticket | COLLISION | Both locked | $0 (no call made) |
| 6 | Raw HTML email → Sweeper → Router | COMPLEX + pruned | gpt-4o | $0.004740 vs $0.006438 without pruning |

---

## Future Roadmap

**Near-term (next build phase):**
- Push to GitHub as open-source repository
- Deploy to Heroku for a live shareable URL
- 30-day rolling cost graphs using Chart.js
- Email/Slack alerts when budget thresholds are hit
- Role-based access control (supervisor vs read-only)

**Platform Integration Targets:**
- **Salesforce** — Named Credentials + Apex callouts + Connected App (Heroku-hosted)
- **HubSpot** — REST API webhooks + workflow actions
- **Microsoft Dynamics 365** — Azure-hosted + Power Automate connectors
- **ServiceNow** — REST integration for ticket routing
- **Zendesk** — Apps framework + webhook triggers
- **SAP, Oracle, Workday** — via respective integration cloud platforms
- **Slack** — Bolt SDK app for Slack-based AI governance

**Integration pattern:** FAGE runs as a hosted service. Each platform connects via its native webhook or API callout mechanism. No rewrite needed per platform — only a connector layer on top.

---

## Key Value Propositions

**For the CFO:**
- Real-time spending caps per department with automatic enforcement
- Proven cost reduction: 63.5% compression on input tokens + automatic routing to cheaper models
- Full dollar-denominated audit trail for every AI expense

**For the CTO:**
- Platform-agnostic middleware — works with any LLM provider
- Prevents data corruption via concurrency collision detection
- Production-ready architecture: swap SQLite → PostgreSQL, simulated → live with single config changes

**For Legal & Compliance:**
- Immutable audit trail for every high-stakes AI decision
- Plain-English rationale statements ready for regulatory review
- Exportable JSONL format compatible with SIEM and compliance tools

**For the Board:**
- Single dashboard showing all AI activity, costs, and risk events
- Throttle and override controls give executives direct governance authority
- Demonstrates responsible AI deployment with provable controls
