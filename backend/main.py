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
from fastapi import FastAPI, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from database.db import engine, SessionLocal
from database import models
from sqlalchemy import text

# Create all DB tables on startup
models.Base.metadata.create_all(bind=engine)

# ── Lightweight column migrations (safe to re-run on every startup) ────────────
def _run_migrations():
    """Add new columns to existing tables without requiring Alembic."""
    with engine.connect() as conn:
        try:
            conn.execute(text(
                "ALTER TABLE registered_agents ADD COLUMN IF NOT EXISTS archived BOOLEAN DEFAULT FALSE"
            ))
            conn.commit()
        except Exception:
            pass  # Column already exists or DB doesn't support IF NOT EXISTS
        try:
            conn.execute(text(
                "ALTER TABLE model_registry ADD COLUMN IF NOT EXISTS department VARCHAR"
            ))
            conn.commit()
        except Exception:
            pass
        try:
            conn.execute(text(
                "ALTER TABLE department_budgets ADD COLUMN IF NOT EXISTS throttle_tier INTEGER DEFAULT 1"
            ))
            conn.commit()
        except Exception:
            pass
        try:
            conn.execute(text(
                "ALTER TABLE registered_agents ADD COLUMN IF NOT EXISTS min_tier INTEGER DEFAULT 1"
            ))
            conn.commit()
        except Exception:
            pass
        try:
            conn.execute(text(
                "ALTER TABLE registered_agents ADD COLUMN IF NOT EXISTS max_tier INTEGER DEFAULT 4"
            ))
            conn.commit()
        except Exception:
            pass
        try:
            conn.execute(text(
                "ALTER TABLE department_budgets ADD COLUMN IF NOT EXISTS raw_payload_logging_enabled BOOLEAN DEFAULT FALSE"
            ))
            conn.commit()
        except Exception:
            pass
        try:
            conn.execute(text(
                "ALTER TABLE department_budgets ADD COLUMN IF NOT EXISTS raw_retention_days INTEGER DEFAULT 30"
            ))
            conn.commit()
        except Exception:
            pass
        try:
            conn.execute(text(
                "ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS raw_payload TEXT"
            ))
            conn.commit()
        except Exception:
            pass
        try:
            conn.execute(text(
                "ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS raw_logged_at TIMESTAMP"
            ))
            conn.commit()
        except Exception:
            pass

_run_migrations()


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
        for t in SEED_TERMS:
            exists = db.query(SensitiveTerm).filter_by(term=t["term"]).first()
            if not exists:
                db.add(SensitiveTerm(**t))

        # ── Routing Config (single-row settings — always sync keywords from config.py) ──
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
        else:
            # Always update keywords from config.py so new keywords take effect on deploy
            exists.complexity_keywords_json = _json.dumps(COMPLEXITY_KEYWORDS)

        db.commit()
    finally:
        db.close()


_seed_on_startup()

# ── One-time config migrations (safe to re-run) ────────────────────────────────
def _patch_routing_config():
    """Push config.py defaults into the live RoutingConfig row if they differ."""
    from config import COMPLEXITY_TOKEN_THRESHOLD
    from core.routing_config import get_routing_config
    db = SessionLocal()
    try:
        cfg = get_routing_config(db)
        if cfg.complexity_token_threshold != COMPLEXITY_TOKEN_THRESHOLD:
            cfg.complexity_token_threshold = COMPLEXITY_TOKEN_THRESHOLD
            db.commit()
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

# ── API Routes (registered BEFORE static mount so they take priority) ──────────

@app.get("/health", tags=["System"])
def health_check():
    """Confirms the CostPilot backend is live."""
    from core.model_client import get_mode_info
    mode = get_mode_info()
    return {
        "status":    "ok",
        "system":    "CostPilot — FinOps Agentlake & Governance Engine",
        "version":   "0.1.0",
        "dashboard": "http://localhost:8001/",
        "docs_url":  "http://localhost:8001/docs",
        "step":      "Step 8 — Live API hooks complete",
        "model_mode": mode["mode"],
        "provider":   mode["provider"],
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

# Platform Context Enrichment — CostPilot fetches full case context directly from CRM APIs
from api import routes_enrich
app.include_router(routes_enrich.router, prefix="/api/enrich", tags=["Enrichment"])

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
    from database.models import TokenTransaction, AuditEvent, DepartmentBudget, RegisteredAgent
    from datetime import datetime
    from database.db import SessionLocal

    db = SessionLocal()
    try:
        tx_count     = db.query(TokenTransaction).delete()
        audit_count  = db.query(AuditEvent).delete()
        agent_count  = db.query(RegisteredAgent).delete()
        # Reset all department spend to $0, unthrottle
        for budget in db.query(DepartmentBudget).all():
            budget.current_spend_usd = 0.0
            budget.throttled         = False
            budget.override_granted  = False
            budget.period_start      = datetime.utcnow()
        db.commit()
        return {
            "status":              "ok",
            "transactions_cleared": tx_count,
            "audit_events_cleared": audit_count,
            "agents_cleared":       agent_count,
            "message":             "Full reset complete. All agents, transactions, and audit events cleared. Budget caps preserved.",
        }
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
frontend_path = os.path.join(os.path.dirname(__file__), "..", "frontend")
app.mount("/", StaticFiles(directory=frontend_path, html=True), name="frontend")
