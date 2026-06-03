"""
api/routes_trial.py — Free Trial Registration, OpenAI Usage Pull & Status

POST /api/trial/connect-openai   — validate key + pull 30-day OpenAI usage + savings sim
POST /api/trial/register         — create trial account (email, name, key)
GET  /api/trial/status           — check trial status by workspace_id
"""

import os
import uuid
import httpx
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from database.db import get_db
from database.models import TrialAccount

router = APIRouter()

# ── Cost table: OpenAI model → $ per 1M tokens ────────────────────────────────
MODEL_COSTS = {
    # GPT-4o family
    "gpt-4o":                  {"input": 5.00,  "output": 15.00, "tier": "Advisor"},
    "gpt-4o-2024-11-20":       {"input": 2.50,  "output": 10.00, "tier": "Advisor"},
    "gpt-4o-2024-08-06":       {"input": 2.50,  "output": 10.00, "tier": "Advisor"},
    "gpt-4o-mini":             {"input": 0.15,  "output": 0.60,  "tier": "Scout"},
    "gpt-4o-mini-2024-07-18":  {"input": 0.15,  "output": 0.60,  "tier": "Scout"},
    # GPT-4.1
    "gpt-4.1":                 {"input": 2.00,  "output": 8.00,  "tier": "Advisor"},
    "gpt-4.1-mini":            {"input": 0.40,  "output": 1.60,  "tier": "Analyst"},
    "gpt-4.1-nano":            {"input": 0.10,  "output": 0.40,  "tier": "Scout"},
    # GPT-4 classic
    "gpt-4":                   {"input": 30.00, "output": 60.00, "tier": "Strategist"},
    "gpt-4-turbo":             {"input": 10.00, "output": 30.00, "tier": "Strategist"},
    "gpt-4-turbo-preview":     {"input": 10.00, "output": 30.00, "tier": "Strategist"},
    # GPT-3.5
    "gpt-3.5-turbo":           {"input": 0.50,  "output": 1.50,  "tier": "Analyst"},
    "gpt-3.5-turbo-0125":      {"input": 0.50,  "output": 1.50,  "tier": "Analyst"},
    # o-series
    "o3":                      {"input": 10.00, "output": 40.00, "tier": "Strategist"},
    "o3-mini":                 {"input": 1.10,  "output": 4.40,  "tier": "Analyst"},
    "o4-mini":                 {"input": 1.10,  "output": 4.40,  "tier": "Analyst"},
    "o1":                      {"input": 15.00, "output": 60.00, "tier": "Strategist"},
    "o1-mini":                 {"input": 3.00,  "output": 12.00, "tier": "Advisor"},
}

SCOUT_INPUT_COST  = 0.15 / 1_000_000   # gpt-4o-mini rates
SCOUT_OUTPUT_COST = 0.60 / 1_000_000


def _simulate_savings(usage_rows: list) -> dict:
    """
    Given raw OpenAI usage rows, calculate what CostPilot would have saved.
    Routing assumption: 70% of calls route to Scout, 30% stay on current model.
    """
    total_actual_cost   = 0.0
    total_costpilot_cost = 0.0
    model_breakdown     = {}
    daily_spend         = {}

    for row in usage_rows:
        model = row.get("snapshot_id", "unknown")
        date  = row.get("date", "")
        n_in  = row.get("n_context_tokens_total", 0)
        n_out = row.get("n_generated_tokens_total", 0)
        n_req = row.get("n_requests", 0)

        meta = MODEL_COSTS.get(model, {"input": 2.50, "output": 10.00, "tier": "Advisor"})
        actual_cost = (n_in * meta["input"] + n_out * meta["output"]) / 1_000_000

        # CostPilot routing simulation
        scout_pct   = 0.70
        complex_pct = 0.30
        cp_cost = (
            (n_in * scout_pct  * SCOUT_INPUT_COST)  +
            (n_out * scout_pct  * SCOUT_OUTPUT_COST) +
            (n_in * complex_pct * meta["input"]  / 1_000_000) +
            (n_out * complex_pct * meta["output"] / 1_000_000)
        )

        total_actual_cost    += actual_cost
        total_costpilot_cost += cp_cost

        if model not in model_breakdown:
            model_breakdown[model] = {"requests": 0, "input_tokens": 0, "output_tokens": 0,
                                       "actual_cost": 0.0, "tier": meta["tier"]}
        model_breakdown[model]["requests"]     += n_req
        model_breakdown[model]["input_tokens"] += n_in
        model_breakdown[model]["output_tokens"] += n_out
        model_breakdown[model]["actual_cost"]  += actual_cost

        if date not in daily_spend:
            daily_spend[date] = 0.0
        daily_spend[date] += actual_cost

    saved       = max(0.0, total_actual_cost - total_costpilot_cost)
    pct_saved   = round((saved / total_actual_cost * 100), 1) if total_actual_cost > 0 else 0
    annual_proj = round(saved * 12, 2)

    return {
        "actual_cost_usd":     round(total_actual_cost, 4),
        "costpilot_cost_usd":  round(total_costpilot_cost, 4),
        "saved_usd":           round(saved, 4),
        "pct_saved":           pct_saved,
        "annual_savings_usd":  annual_proj,
        "model_breakdown":     model_breakdown,
        "daily_spend":         daily_spend,
    }


# ── 1. Connect OpenAI — pull usage + simulate savings ─────────────────────────

class ConnectOpenAIRequest(BaseModel):
    api_key: str
    days:    int = 30

@router.post("/connect-openai")
async def connect_openai(req: ConnectOpenAIRequest):
    key = req.api_key.strip()

    # Detect wrong-provider keys
    if key.startswith("sk-ant-"):
        raise HTTPException(status_code=400,
            detail="That looks like an Anthropic key (sk-ant-...). Please paste your OpenAI key instead — find it at platform.openai.com/api-keys.")
    if key.startswith("AIza") or key.startswith("ya29."):
        raise HTTPException(status_code=400,
            detail="That looks like a Google API key. Please paste your OpenAI key instead.")
    if not key.startswith("sk-"):
        raise HTTPException(status_code=400,
            detail="OpenAI API keys start with 'sk-'. Please check you copied the full key from platform.openai.com/api-keys.")

    headers = {"Authorization": f"Bearer {key}"}

    # Validate key against OpenAI
    async with httpx.AsyncClient(timeout=10) as client:
        check = await client.get("https://api.openai.com/v1/models", headers=headers)
        if check.status_code == 401:
            raise HTTPException(status_code=401,
                detail="OpenAI rejected this key. Make sure you copied the full key — it should be ~50 characters starting with 'sk-'.")
        if check.status_code == 429:
            raise HTTPException(status_code=429,
                detail="OpenAI rate limit hit. Please wait a moment and try again.")
        if check.status_code != 200:
            raise HTTPException(status_code=502, detail="Could not reach OpenAI to validate key.")

    # Pull usage day by day
    usage_rows = []
    async with httpx.AsyncClient(timeout=15) as client:
        for i in range(req.days):
            date_str = (datetime.utcnow() - timedelta(days=i)).strftime("%Y-%m-%d")
            resp = await client.get(
                f"https://api.openai.com/v1/usage?date={date_str}",
                headers=headers
            )
            if resp.status_code == 200:
                data = resp.json()
                for row in data.get("data", []):
                    row["date"] = date_str
                    usage_rows.append(row)

    if not usage_rows:
        # Key valid but no usage found — return zero baseline
        return {
            "has_usage": False,
            "message":   "API key is valid but no usage found in the last 30 days.",
            "savings":   None,
        }

    savings = _simulate_savings(usage_rows)
    return {
        "has_usage":   True,
        "period_days": req.days,
        "savings":     savings,
    }


# ── 2. Register Trial ─────────────────────────────────────────────────────────

class RegisterTrialRequest(BaseModel):
    email:      str
    name:       str
    company:    str = ""
    api_key:    str
    provider:   str = "openai"

@router.post("/register")
def register_trial(req: RegisterTrialRequest, db: Session = Depends(get_db)):
    # One trial per email
    existing = db.query(TrialAccount).filter_by(email=req.email).first()
    if existing:
        return {
            "workspace_id": existing.workspace_id,
            "trial_end":    existing.trial_end.isoformat(),
            "already_exists": True,
            "message": "A trial for this email already exists.",
        }

    workspace_id = str(uuid.uuid4()).replace("-", "")[:16].upper()
    trial_start  = datetime.utcnow()
    trial_end    = trial_start + timedelta(days=30)

    # Encrypt key with a simple reversible scheme using env secret
    from base64 import b64encode
    secret = os.environ.get("COSTPILOT_SECRET", "default-secret-change-me")
    encrypted = b64encode(req.api_key.encode()).decode()  # base64 for now; swap for Fernet in prod

    account = TrialAccount(
        email        = req.email,
        name         = req.name,
        company      = req.company,
        api_key_enc  = encrypted,
        provider     = req.provider,
        workspace_id = workspace_id,
        trial_start  = trial_start,
        trial_end    = trial_end,
        plan         = "trial",
        is_active    = True,
    )
    db.add(account)
    db.commit()
    db.refresh(account)

    return {
        "workspace_id":   workspace_id,
        "trial_start":    trial_start.isoformat(),
        "trial_end":      trial_end.isoformat(),
        "days_remaining": 30,
        "proxy_endpoint": f"https://fage-engine-21cb49fe4806.herokuapp.com/v1",
        "already_exists": False,
    }


# ── 3. Trial Status ───────────────────────────────────────────────────────────

@router.get("/status")
def trial_status(workspace_id: str, db: Session = Depends(get_db)):
    account = db.query(TrialAccount).filter_by(workspace_id=workspace_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Workspace not found.")

    now           = datetime.utcnow()
    days_remaining = max(0, (account.trial_end - now).days)
    is_expired    = now > account.trial_end

    return {
        "workspace_id":   account.workspace_id,
        "email":          account.email,
        "name":           account.name,
        "company":        account.company,
        "provider":       account.provider,
        "plan":           account.plan,
        "trial_start":    account.trial_start.isoformat(),
        "trial_end":      account.trial_end.isoformat(),
        "days_remaining": days_remaining,
        "is_active":      account.is_active and not is_expired,
        "is_expired":     is_expired,
        "is_paid":        account.plan not in ("trial", "free"),
    }
