"""
api/routes_trial.py — Free Trial Registration, Usage Pull & Status

POST /api/trial/connect-openai        — validate key + pull 30-day OpenAI usage + savings sim
POST /api/trial/validate-anthropic    — validate Anthropic key (cheap test call)
POST /api/trial/anthropic-manual      — savings sim from manual usage inputs
POST /api/trial/anthropic-csv         — savings sim from uploaded Anthropic usage CSV
POST /api/trial/register              — create trial account (email, name, key)
GET  /api/trial/status                — check trial status by workspace_id
"""

import os
import io
import csv
import uuid
import httpx
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
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

    # Pull usage day by day — the usage endpoint itself tells us if the key is valid.
    # We avoid GET /v1/models because project-scoped keys often block it even when valid.
    usage_rows = []
    key_confirmed_valid = False

    async with httpx.AsyncClient(timeout=15) as client:
        for i in range(req.days):
            date_str = (datetime.utcnow() - timedelta(days=i)).strftime("%Y-%m-%d")
            resp = await client.get(
                f"https://api.openai.com/v1/usage?date={date_str}",
                headers=headers
            )
            if resp.status_code == 401:
                raise HTTPException(status_code=401,
                    detail="OpenAI rejected this key. Double-check you copied the full key from platform.openai.com → API Keys. Project keys (sk-proj-...) and legacy keys (sk-...) both work.")
            if resp.status_code == 429:
                raise HTTPException(status_code=429,
                    detail="OpenAI rate limit hit. Please wait a moment and try again.")
            if resp.status_code == 200:
                key_confirmed_valid = True
                data = resp.json()
                for row in data.get("data", []):
                    row["date"] = date_str
                    usage_rows.append(row)
            # 403 or other = key valid but no usage permission — keep going

    # If we never got a 200 but also no 401, key might be valid but usage API is restricted
    if not key_confirmed_valid and not usage_rows:
        # Fall back: make a cheap validation call
        async with httpx.AsyncClient(timeout=10) as client:
            check = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={**headers, "content-type": "application/json"},
                json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 1},
            )
            if check.status_code == 401:
                raise HTTPException(status_code=401,
                    detail="OpenAI rejected this key. Double-check you copied the full key from platform.openai.com → API Keys.")

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


# ── Anthropic model cost table ────────────────────────────────────────────────

ANTHROPIC_MODEL_COSTS = {
    # Claude 4.x
    "claude-opus-4-8":            {"input": 15.00, "output": 75.00, "tier": "Strategist"},
    "claude-opus-4-6":            {"input": 15.00, "output": 75.00, "tier": "Strategist"},
    "claude-sonnet-4-6":          {"input": 3.00,  "output": 15.00, "tier": "Advisor"},
    "claude-haiku-4-5-20251001":  {"input": 0.80,  "output": 4.00,  "tier": "Scout"},
    # Claude 3.x
    "claude-3-opus-20240229":     {"input": 15.00, "output": 75.00, "tier": "Strategist"},
    "claude-3-5-sonnet-20241022": {"input": 3.00,  "output": 15.00, "tier": "Advisor"},
    "claude-3-5-sonnet-20240620": {"input": 3.00,  "output": 15.00, "tier": "Advisor"},
    "claude-3-5-haiku-20241022":  {"input": 0.80,  "output": 4.00,  "tier": "Scout"},
    "claude-3-haiku-20240307":    {"input": 0.25,  "output": 1.25,  "tier": "Scout"},
    "claude-3-sonnet-20240229":   {"input": 3.00,  "output": 15.00, "tier": "Advisor"},
}

ANTHROPIC_SCOUT_INPUT  = 0.25 / 1_000_000   # claude-3-haiku rates (cheapest)
ANTHROPIC_SCOUT_OUTPUT = 1.25 / 1_000_000


def _anthropic_savings_from_rows(rows: list) -> dict:
    """Shared savings simulation for both manual and CSV paths."""
    total_actual = 0.0
    total_cp     = 0.0
    model_breakdown = {}

    for row in rows:
        model   = row.get("model", "claude-3-5-sonnet-20241022")
        n_in    = row.get("input_tokens", 0)
        n_out   = row.get("output_tokens", 0)
        meta    = ANTHROPIC_MODEL_COSTS.get(model,
                    {"input": 3.00, "output": 15.00, "tier": "Advisor"})

        actual = (n_in * meta["input"] + n_out * meta["output"]) / 1_000_000
        cp     = (
            (n_in  * 0.70 * ANTHROPIC_SCOUT_INPUT)  +
            (n_out * 0.70 * ANTHROPIC_SCOUT_OUTPUT) +
            (n_in  * 0.30 * meta["input"]  / 1_000_000) +
            (n_out * 0.30 * meta["output"] / 1_000_000)
        )
        total_actual += actual
        total_cp     += cp

        if model not in model_breakdown:
            model_breakdown[model] = {"requests": 0, "input_tokens": 0,
                                       "output_tokens": 0, "actual_cost": 0.0,
                                       "tier": meta["tier"]}
        model_breakdown[model]["input_tokens"]  += n_in
        model_breakdown[model]["output_tokens"] += n_out
        model_breakdown[model]["actual_cost"]   += actual

    saved     = max(0.0, total_actual - total_cp)
    pct_saved = round(saved / total_actual * 100, 1) if total_actual > 0 else 0

    return {
        "actual_cost_usd":    round(total_actual, 4),
        "costpilot_cost_usd": round(total_cp, 4),
        "saved_usd":          round(saved, 4),
        "pct_saved":          pct_saved,
        "annual_savings_usd": round(saved * 12, 2),
        "model_breakdown":    model_breakdown,
    }


# ── 4. Validate Anthropic Key ─────────────────────────────────────────────────

class ValidateAnthropicRequest(BaseModel):
    api_key: str

@router.post("/validate-anthropic")
async def validate_anthropic(req: ValidateAnthropicRequest):
    key = req.api_key.strip()
    if not key.startswith("sk-ant-"):
        raise HTTPException(status_code=400,
            detail="Anthropic keys start with 'sk-ant-'. Please check you copied the correct key from console.anthropic.com.")

    # Cheapest possible call to verify the key is live
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={"model": "claude-haiku-4-5-20251001", "max_tokens": 1,
                  "messages": [{"role": "user", "content": "hi"}]},
        )

    if resp.status_code == 401:
        raise HTTPException(status_code=401,
            detail="Anthropic rejected this key. Please check you copied the full key from console.anthropic.com → API Keys.")
    if resp.status_code not in (200, 201):
        raise HTTPException(status_code=502,
            detail="Could not reach Anthropic to validate the key. Please try again.")

    return {
        "valid":    True,
        "provider": "anthropic",
        "message":  "Key verified.",
        "why_no_autopull": (
            "Anthropic doesn't expose a usage history API — unlike OpenAI, there's no endpoint "
            "we can query to pull your past spend automatically. Your usage data lives only inside "
            "the Anthropic Console. To show your real savings we need one extra step: either tell "
            "us your approximate usage, or upload the CSV export from your Console."
        ),
    }


# ── 5. Anthropic Savings — Manual Input ───────────────────────────────────────

class AnthropicManualRequest(BaseModel):
    monthly_spend_usd: float
    primary_model:     str = "claude-3-5-sonnet-20241022"
    calls_per_month:   int = 0    # optional — used to show call split

@router.post("/anthropic-manual")
def anthropic_manual(req: AnthropicManualRequest):
    meta = ANTHROPIC_MODEL_COSTS.get(req.primary_model,
               {"input": 3.00, "output": 15.00, "tier": "Advisor"})

    # Reverse-engineer token counts from monthly spend
    avg_cost_per_call = (meta["input"] * 2000 + meta["output"] * 500) / 1_000_000
    estimated_calls   = req.calls_per_month or max(1, int(req.monthly_spend_usd / avg_cost_per_call))
    avg_in_tokens     = 2000
    avg_out_tokens    = 500

    rows = [{
        "model":         req.primary_model,
        "input_tokens":  avg_in_tokens  * estimated_calls,
        "output_tokens": avg_out_tokens * estimated_calls,
    }]

    # Scale to match stated spend exactly
    savings = _anthropic_savings_from_rows(rows)
    scale   = req.monthly_spend_usd / savings["actual_cost_usd"] if savings["actual_cost_usd"] > 0 else 1.0
    savings["actual_cost_usd"]    = round(req.monthly_spend_usd, 4)
    savings["costpilot_cost_usd"] = round(savings["costpilot_cost_usd"] * scale, 4)
    savings["saved_usd"]          = round(max(0, savings["actual_cost_usd"] - savings["costpilot_cost_usd"]), 4)
    savings["annual_savings_usd"] = round(savings["saved_usd"] * 12, 2)
    savings["pct_saved"]          = round(savings["saved_usd"] / savings["actual_cost_usd"] * 100, 1) if savings["actual_cost_usd"] > 0 else 0
    savings["estimated_calls"]    = estimated_calls
    savings["data_source"]        = "manual"

    return {"has_usage": True, "savings": savings}


# ── 6. Anthropic Savings — CSV Upload ─────────────────────────────────────────

@router.post("/anthropic-csv")
async def anthropic_csv(file: UploadFile = File(...)):
    if not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="Please upload a .csv file.")

    content = await file.read()
    try:
        text = content.decode("utf-8-sig")  # handle BOM from Excel exports
    except Exception:
        raise HTTPException(status_code=400, detail="Could not read file. Please upload a UTF-8 CSV.")

    reader  = csv.DictReader(io.StringIO(text))
    headers = [h.lower().strip() for h in (reader.fieldnames or [])]

    # Flexible column name mapping
    def find_col(candidates):
        for c in candidates:
            for h in (reader.fieldnames or []):
                if c in h.lower():
                    return h
        return None

    col_model  = find_col(["model"])
    col_in     = find_col(["input token", "input_token", "prompt token"])
    col_out    = find_col(["output token", "output_token", "completion token", "generated token"])
    col_cost   = find_col(["total cost", "cost"])

    rows = []
    total_cost_from_csv = 0.0

    for row in reader:
        try:
            model   = row.get(col_model, "claude-3-5-sonnet-20241022").strip() if col_model else "claude-3-5-sonnet-20241022"
            n_in    = int(str(row.get(col_in,  0) or 0).replace(",", "")) if col_in  else 0
            n_out   = int(str(row.get(col_out, 0) or 0).replace(",", "")) if col_out else 0
            cost    = float(str(row.get(col_cost, 0) or 0).replace(",", "").replace("$", "")) if col_cost else 0.0
            total_cost_from_csv += cost
            if n_in > 0 or n_out > 0:
                rows.append({"model": model, "input_tokens": n_in, "output_tokens": n_out})
        except Exception:
            continue

    if not rows:
        raise HTTPException(status_code=400,
            detail="Could not parse usage data from this CSV. Make sure it's the export from console.anthropic.com → Usage → Export CSV.")

    savings = _anthropic_savings_from_rows(rows)

    # If CSV had cost column, use it as ground truth
    if total_cost_from_csv > 0:
        scale = total_cost_from_csv / savings["actual_cost_usd"] if savings["actual_cost_usd"] > 0 else 1.0
        savings["actual_cost_usd"]    = round(total_cost_from_csv, 4)
        savings["costpilot_cost_usd"] = round(savings["costpilot_cost_usd"] * scale, 4)
        savings["saved_usd"]          = round(max(0, savings["actual_cost_usd"] - savings["costpilot_cost_usd"]), 4)
        savings["annual_savings_usd"] = round(savings["saved_usd"] * 12, 2)
        savings["pct_saved"]          = round(savings["saved_usd"] / savings["actual_cost_usd"] * 100, 1) if savings["actual_cost_usd"] > 0 else 0

    savings["data_source"] = "csv"
    savings["rows_parsed"] = len(rows)
    return {"has_usage": True, "savings": savings}


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
