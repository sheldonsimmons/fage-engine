"""
main.py — FAGE FastAPI application entry point.

Start the server:
    cd backend
    uvicorn main:app --reload --port 8001

Dashboard:    http://localhost:8001/
API Docs:     http://localhost:8001/docs
Health check: http://localhost:8001/health
"""

import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from database.db import engine
from database import models

# Create all DB tables on startup
models.Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="FAGE — FinOps Agentlake & Governance Engine",
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
    """Confirms the FAGE backend is live."""
    from core.model_client import get_mode_info
    mode = get_mode_info()
    return {
        "status":    "ok",
        "system":    "FAGE — FinOps Agentlake & Governance Engine",
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

# ── Serve frontend as static files (MUST be last — catches everything else) ────
frontend_path = os.path.join(os.path.dirname(__file__), "..", "frontend")
app.mount("/", StaticFiles(directory=frontend_path, html=True), name="frontend")
