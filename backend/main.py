"""
main.py — CostPilot FastAPI application entry point.

Start the server:
    cd backend
    uvicorn main:app --reload --port 8001

Dashboard:    http://localhost:8001/
API Docs:     http://localhost:8001/docs
Health check: http://localhost:8001/health
"""

import os
from typing import Literal, Optional

from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.requests import Request

from database.db import engine, SessionLocal
from database import models
from database.migrate import create_tables, run_migrations
from sqlalchemy import text

# Create all DB tables and apply lightweight column migrations on startup.
# scripts/release.sh calls the same run_migrations() during the release
# phase so the two never drift out of sync with each other.
create_tables()
run_migrations()


def _seed_on_startup():
    """
    Seed essential reference data if the DB is fresh (e.g. after a Heroku restart).
    Safe to run on every startup — only inserts rows that don't already exist.
    """
    from config import DEFAULT_BUDGET_CAPS
    from database.models import DepartmentBudget, ModelRegistry, SensitiveTerm

    db = SessionLocal()
    try:
        # ── Department budgets ────────────────────────────────────────────────
        for dept, cap in DEFAULT_BUDGET_CAPS.items():
            exists = db.query(DepartmentBudget).filter_by(department=dept).first()
            if not exists:
                db.add(DepartmentBudget(department=dept, monthly_cap_usd=cap))

        # ── Model Registry ────────────────────────────────────────────────────
        # Anthropic Claude models are the live defaults (is_default=True).
        # GPT display models kept for reference/UI but disabled (is_enabled=False).
        SEED_MODELS = [
            # ── Anthropic — live, active defaults ────────────────────────────
            dict(display_name="Claude Haiku 4.5",  model_id="claude-haiku-4-5-20251001", provider="Anthropic",
                 tier=1, cost_input_per_1m=0.80,  cost_output_per_1m=4.00,
                 is_enabled=True, is_default=True,
                 notes="Scout tier — fast and affordable for routine tasks"),
            dict(display_name="GPT-4.1 Mini", model_id="gpt-4.1-mini", provider="OpenAI",
                 tier=2, cost_input_per_1m=0.40, cost_output_per_1m=1.60,
                 is_enabled=True, is_default=True,
                 notes="Analyst tier — balanced reasoning for everyday business tasks"),
            dict(display_name="Claude Sonnet 4.6", model_id="claude-sonnet-4-6", provider="Anthropic",
                 tier=3, cost_input_per_1m=3.00,  cost_output_per_1m=15.00,
                 is_enabled=True, is_default=True,
                 notes="Advisor tier — deep reasoning for complex and sensitive work"),
            dict(display_name="Claude Opus 4.6",   model_id="claude-opus-4-6",   provider="Anthropic",
                 tier=4, cost_input_per_1m=15.00, cost_output_per_1m=75.00,
                 is_enabled=True, is_default=True,
                 notes="Strategist tier — mission-critical decisions only"),
            # ── OpenAI — available but not default ───────────────────────────
            dict(display_name="GPT-4o Mini",  model_id="gpt-4o-mini",  provider="OpenAI",
                 tier=1, cost_input_per_1m=0.15, cost_output_per_1m=0.60,
                 is_enabled=False, is_default=False,
                 notes="OpenAI Scout option — enable to use instead of Haiku"),
            dict(display_name="GPT-4o",       model_id="gpt-4o",       provider="OpenAI",
                 tier=3, cost_input_per_1m=2.50, cost_output_per_1m=10.00,
                 is_enabled=False, is_default=False,
                 notes="OpenAI Advisor option — enable to use instead of Sonnet"),
        ]
        for m in SEED_MODELS:
            exists = db.query(ModelRegistry).filter_by(model_id=m["model_id"]).first()
            if not exists:
                db.add(ModelRegistry(**m))

        # ── Sensitive Term Library ─────────────────────────────────────────────
        SEED_TERMS = [
            # PII — block immediately, never send to AI
            dict(term="ssn",                  category="hipaa",     action="block"),
            dict(term="social security",      category="hipaa",     action="block"),
            dict(term="social security number", category="hipaa",   action="block"),
            dict(term="credit card",          category="financial", action="block"),
            dict(term="card number",          category="financial", action="block"),
            dict(term="cvv",                  category="financial", action="block"),
            dict(term="routing number",       category="financial", action="block"),
            dict(term="bank account",         category="financial", action="block"),
            dict(term="passport number",      category="hipaa",     action="block"),
            dict(term="date of birth",        category="hipaa",     action="block"),
            # Legal / compliance — escalate to senior model
            dict(term="legal",                category="legal",     action="escalate"),
            dict(term="lawsuit",              category="legal",     action="escalate"),
            dict(term="litigation",           category="legal",     action="escalate"),
            dict(term="attorney",             category="legal",     action="escalate"),
            dict(term="legal action",         category="legal",     action="escalate"),
            dict(term="breach of contract",   category="legal",     action="escalate"),
            dict(term="gdpr",                 category="legal",     action="escalate"),
            dict(term="hipaa",                category="hipaa",     action="escalate"),
            dict(term="regulatory",           category="legal",     action="escalate"),
            dict(term="audit",                category="legal",     action="escalate"),
            # HR — escalate
            dict(term="termination",          category="hr",        action="escalate"),
            dict(term="harassment",           category="hr",        action="escalate"),
            dict(term="discrimination",       category="hr",        action="escalate"),
            # PII phrase variants — catch sloppy/spoken input
            dict(term="my social",            category="hipaa",     action="block"),
            dict(term="my passport",          category="hipaa",     action="block"),
            dict(term="routing is",           category="financial", action="block"),
            dict(term="date of birth is",     category="hipaa",     action="block"),
            dict(term="passport",             category="hipaa",     action="escalate"),
            dict(term="drivers license",      category="hipaa",     action="escalate"),
            dict(term="drivers licence",      category="hipaa",     action="escalate"),
            dict(term="diagnosis code",       category="hipaa",     action="block"),
            dict(term="my diagnosis",         category="hipaa",     action="block"),
            dict(term="medical record",       category="hipaa",     action="block"),
        ]
        existing_terms = {row.term for row in db.query(SensitiveTerm).all()}
        terms_to_seed = SEED_TERMS if not existing_terms else []
        for t in terms_to_seed:
            if t["term"] not in existing_terms:
                db.add(SensitiveTerm(**t, enabled=True, is_recommended=True))
                existing_terms.add(t["term"])

        # ── Routing Config (single-row settings) ──
        import json as _json
        from database.models import RoutingConfig
        from config import COMPLEXITY_TOKEN_THRESHOLD, COMPLEXITY_KEYWORDS
        exists = db.query(RoutingConfig).filter_by(id=1).first()
        if not exists:
            db.add(RoutingConfig(
                id=1,
                complexity_token_threshold=COMPLEXITY_TOKEN_THRESHOLD,
                complexity_keywords_json=_json.dumps(COMPLEXITY_KEYWORDS),
            ))

        db.commit()
    finally:
        db.close()


_seed_on_startup()

# ── One-time config migrations (safe to re-run) ────────────────────────────────
def _patch_routing_config():
    """Ensure the persisted RoutingConfig row exists without overwriting admin edits."""
    from core.routing_config import get_routing_config
    db = SessionLocal()
    try:
        get_routing_config(db)
    except Exception:
        pass
    finally:
        db.close()

_patch_routing_config()

app = FastAPI(
    title="CostPilot — FinOps Agentlake & Governance Engine",
    description=(
        "Enterprise AI middleware POC: intelligent token routing, departmental budget "
        "tracking, context pruning, agent registry & concurrency control, and immutable "
        "AI decision auditing."
    ),
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class CSPMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "font-src 'self' data: https://cdn.jsdelivr.net;"
        )
        # Prevent HTML files from being cached so updates are picked up immediately
        content_type = response.headers.get("content-type", "")
        if "text/html" in content_type:
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
        return response

app.add_middleware(CSPMiddleware)
app.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=5)

# ── API Routes (registered BEFORE static mount so they take priority) ──────────

@app.get("/health", tags=["System"])
def health_check():
    """Confirms the CostPilot backend is live."""
    from core.model_client import get_mode_info
    mode = get_mode_info()
    import os
    demo_mode = os.environ.get("DEMO_MODE", "false").lower() == "true"
    return {
        "status":    "ok",
        "system":    "CostPilot — FinOps Agentlake & Governance Engine",
        "version":   "0.1.0",
        "dashboard": "http://localhost:8001/",
        "docs_url":  "http://localhost:8001/docs",
        "step":      "Step 8 — Live API hooks complete",
        "model_mode": mode["mode"],
        "provider":   mode["provider"],
        "demo_mode":  demo_mode,
    }

@app.get("/api/config", tags=["System"])
def get_config():
    """Returns current model mode and provider configuration."""
    from core.model_client import get_mode_info
    return get_mode_info()

# Step 2 — Context Pruner
from api import routes_pruner
app.include_router(routes_pruner.router, prefix="/api/prune", tags=["Pruner"])

# Step 3 — Token Router & Model Cascader
from api import routes_router
app.include_router(routes_router.router, prefix="/api/route", tags=["Router"])

# Universal platform connector contract and capability manifests
from api import routes_integrations
app.include_router(routes_integrations.router, prefix="/api/integrations", tags=["Integrations"])

# Persistent platform connection registry and metadata discovery
from api import routes_connections
app.include_router(routes_connections.router, prefix="/api/integrations/connections", tags=["Connections"])

# Work Attribution — projects, matters, engagements, cases, and claims
from api import routes_work_items
app.include_router(routes_work_items.router, prefix="/api/work-items", tags=["Work Attribution"])

# Salesforce Agentforce proof — authenticated project attribution + governance
from api import routes_agentforce
app.include_router(
    routes_agentforce.router,
    prefix="/api/integrations/salesforce/agentforce",
    tags=["Salesforce Agentforce"],
)

# Step 4 — Budget Allocator & Throttle
from api import routes_budget
app.include_router(routes_budget.router, prefix="/api/budget", tags=["Budget"])

# Step 5 — Agentlake Registry & Traffic Cop
from api import routes_agentlake
app.include_router(routes_agentlake.router, prefix="/api/agents", tags=["Agentlake"])

# Step 6 — AI Decision Auditor
from api import routes_auditor
app.include_router(routes_auditor.router, prefix="/api/audit", tags=["Auditor"])

# Step 7 — Dashboard aggregates
from api import routes_dashboard
app.include_router(routes_dashboard.router, prefix="/api/dashboard", tags=["Dashboard"])

# Live "what stands out" signal feed — deterministic anomaly/pace checks,
# ranked by severity, first slice of the roadmap's predictive-intelligence phase
from api import routes_insights
app.include_router(routes_insights.router, prefix="/api/insights", tags=["Insights"])

# Phase 1 — Workspace registry
from api import routes_workspaces
app.include_router(routes_workspaces.router, prefix="/api/workspaces", tags=["Workspaces"])

# Step 9 — Sensitive Term Library
from api import routes_keywords
app.include_router(routes_keywords.router, prefix="/api/keywords", tags=["Keywords"])

# Step 13 — Model Registry
from api import routes_models
app.include_router(routes_models.router, prefix="/api/models", tags=["Models"])

# Step 10 — Reports
from api import routes_reports
app.include_router(routes_reports.router, prefix="/api/reports", tags=["Reports"])

# Step 11 — Bot Efficiency Review
from api import routes_efficiency
app.include_router(routes_efficiency.router, prefix="/api/reports/bot-efficiency", tags=["Efficiency"])

from api import routes_agent_activity
app.include_router(routes_agent_activity.router, prefix="/api/reports/agent-activity", tags=["Agent Activity"])

# Voice Guard — PII redaction for voice transcripts
from api import routes_voice
app.include_router(routes_voice.router, prefix="/api/voice", tags=["Voice Guard"])

# Routing Rules — user-configurable token threshold + complexity keywords
from api import routes_routing_config
app.include_router(routes_routing_config.router, prefix="/api/routing-config", tags=["Routing Config"])

# Time-series — 30-day daily spend + call-volume for dashboard charts
from api import routes_timeseries
app.include_router(routes_timeseries.router, prefix="/api/timeseries", tags=["Timeseries"])

# Savings Calculator — server-side proxy for Anthropic/OpenAI usage APIs (avoids CORS)
from api import routes_savings
app.include_router(routes_savings.router, prefix="/api/savings", tags=["Savings"])

# Platform Context Enrichment — CostPilot fetches full case context directly from CRM APIs
from api import routes_enrich
app.include_router(routes_enrich.router, prefix="/api/enrich", tags=["Enrichment"])

from api import routes_known_models
app.include_router(routes_known_models.router, prefix="/api/models/known", tags=["Known Models"])

# Free Trial — OpenAI usage pull, trial registration, trial status
from api import routes_trial
app.include_router(routes_trial.router, prefix="/api/trial", tags=["Trial"])

# Customer Proxy — /v1/ws-{workspace_id}/chat/completions + /messages
from api import routes_proxy
app.include_router(routes_proxy.router, prefix="/v1", tags=["Proxy"])

# Dev/Demo — Populate dashboard with impressive demo data for screenshots
@app.post("/api/admin/populate-demo", tags=["Admin"])
def populate_demo_data():
    """
    DEV/DEMO ONLY — Loads the dashboard with rich demo data for screenshots.
    Clears transactions and audit events, then rebuilds with 30 days of history.
    Keeps departments, agents, and sensitive terms intact.
    """
    from database.populate_demo import populate
    populate()
    return {"status": "ok", "message": "Demo data loaded. Refresh your dashboard."}

# Dev/Demo — Populate dashboard with enterprise-scale demo data for CFO/CTO presentations
@app.post("/api/admin/populate-enterprise-demo", tags=["Admin"])
def populate_enterprise_demo_data(background_tasks: BackgroundTasks):
    """
    DEV/DEMO ONLY — Loads the real CostPilot dashboard with enterprise-scale data.
    12 named agents, 4 departments, 9K transactions over 30 days, Marketing throttled,
    and 12 rich audit events covering blocks, escalations, GDPR, HIPAA, and more.
    Returns immediately — data loads in the background. Refresh dashboard in ~10 seconds.
    """
    from database.populate_enterprise import populate_enterprise
    background_tasks.add_task(populate_enterprise)
    return {"status": "ok", "message": "Enterprise demo loading in background. Refresh dashboard in 10 seconds."}

# Dev/Demo — Full factory reset (clears everything except departments, terms, and budget caps)
@app.post("/api/admin/reset-demo", tags=["Admin"])
def reset_demo_data(db=None):
    """
    DEV/DEMO ONLY — Full factory reset.
    Clears all transactions, audit events, and registered agents.
    Resets department spend to $0 but preserves user-set budget caps.
    """
    from database.db import SessionLocal
    from database.reset_demo import reset_demo_records

    db = SessionLocal()
    try:
        result = reset_demo_records(db)
        db.commit()
        return {
            "status": "ok",
            **result,
            "message": (
                "Full reset complete. All agents, agent assignments, transactions, "
                "audit events, and review checkpoints cleared. Business contexts "
                "and budget caps preserved."
            ),
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


class WorkspaceResetRequest(BaseModel):
    scope: Literal["usage", "simulator", "workspace"] = "usage"
    workspace_id: Optional[str] = None
    confirmation: Optional[str] = None


@app.post("/api/admin/reset", tags=["Admin"])
def reset_workspace_data(payload: WorkspaceResetRequest):
    """Perform an explicit, scoped reset selected by a CostPilot administrator."""
    if payload.scope == "workspace":
        if not payload.workspace_id:
            raise HTTPException(
                status_code=400,
                detail="A workspace ID is required to reset an entire workspace.",
            )
        if payload.confirmation != "RESET WORKSPACE":
            raise HTTPException(
                status_code=400,
                detail='Type "RESET WORKSPACE" to confirm the destructive reset.',
            )

    from database.db import SessionLocal
    from database.reset_demo import reset_workspace_records

    db = SessionLocal()
    try:
        result = reset_workspace_records(
            db,
            scope=payload.scope,
            workspace_id=(payload.workspace_id or "").strip() or None,
        )
        db.commit()
        messages = {
            "usage": (
                "Usage data reset. Calls, cost, tokens, audit history, and risk "
                "events were cleared; connected business records and identities remain."
            ),
            "simulator": (
                "Simulator data reset. Generated usage, business records, users, "
                "and orphaned simulator agents were removed."
            ),
            "workspace": (
                "Workspace reset. Usage, business context, identities, orphaned "
                "agents, organizational units, and platform connections were removed."
            ),
        }
        return {"status": "ok", **result, "message": messages[payload.scope]}
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

# Dev/Debug — Inspect tag detection without a full route call
@app.post("/api/admin/debug-tag", tags=["Admin"])
def debug_tag(body: dict):
    """
    DEV ONLY — Shows what the router sees for tag detection.
    POST {"text": "[ANALYST] your text here"}
    """
    text = body.get("text", "")
    tag_map = {"[scout]": 1, "[analyst]": 2, "[advisor]": 3, "[strategist]": 4}
    text_start = text.strip().lower()[:50]
    forced_tier = None
    matched_tag = None
    for tag, tier in tag_map.items():
        if text_start.startswith(tag):
            forced_tier = tier
            matched_tag = tag
            break
    from core.pruner import prune, estimate_tokens
    pruned = prune(text)
    return {
        "raw_first_50_chars":    repr(text.strip()[:50]),
        "lowercased_first_50":   repr(text.strip().lower()[:50]),
        "matched_tag":           matched_tag,
        "forced_tier":           forced_tier,
        "raw_token_estimate":    estimate_tokens(text),
        "pruned_token_estimate": estimate_tokens(pruned["cleaned_text"]),
        "pruned_first_100":      pruned["cleaned_text"][:100],
    }

# ── Serve frontend as static files (MUST be last — catches everything else) ────
frontend_path = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "frontend"))

# Explicit routes for HTML pages so they're always read fresh from disk and
# bypass any StaticFiles path-resolution ambiguity.
from fastapi.responses import FileResponse as _FileResponse, RedirectResponse

@app.get("/reports.html")
def serve_reports_html():
    return _FileResponse(os.path.join(frontend_path, "reports.html"), media_type="text/html")

@app.get("/live-reports.html")
def serve_live_reports_html():
    return _FileResponse(os.path.join(frontend_path, "live-reports.html"), media_type="text/html")

# The Executive Cockpit (React/Vite/shadcn) is a separate frontend project
# that only talks to the same REST APIs everything else here uses -- built
# with `npm run build` in cockpit/, producing cockpit/dist/. Mounted before
# the catch-all "/" mount below so /cockpit/* resolves here first. If the
# build hasn't been run (e.g. a fresh checkout before `npm install && npm
# run build`), this is skipped rather than failing app startup.
cockpit_dist_path = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "cockpit", "dist"))
if os.path.isdir(cockpit_dist_path):
    @app.get("/cockpit")
    def _redirect_cockpit_root():
        # StaticFiles mounted at "/cockpit" only matches "/cockpit/..." --
        # without this, typing the bare path (no trailing slash, the
        # natural thing to type) 404s instead of loading the app.
        return RedirectResponse(url="/cockpit/")

    app.mount("/cockpit", StaticFiles(directory=cockpit_dist_path, html=True), name="cockpit")

app.mount("/", StaticFiles(directory=frontend_path, html=True), name="frontend")
