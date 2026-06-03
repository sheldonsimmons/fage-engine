"""
api/routes_proxy.py — CostPilot Customer Proxy

POST /v1/ws-{workspace_id}/chat/completions   — OpenAI-compatible proxy
POST /v1/ws-{workspace_id}/messages           — Anthropic-compatible proxy

Each request:
  1. Validates workspace_id + X-CostPilot-Key header
  2. Runs CostPilot's complexity router to pick the right tier
  3. Forwards to the real API using the customer's stored key
  4. Logs the transaction tagged to workspace_id
  5. Returns the real API response transparently
"""

import httpx
import json
from base64 import b64decode
from datetime import datetime
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse

from database.db import SessionLocal
from database.models import TrialAccount, TokenTransaction

router = APIRouter()

# ── Cost rates ────────────────────────────────────────────────────────────────

OPENAI_TIERS = {
    "gpt-4o":              {"tier": "Advisor",    "input": 5.00,  "output": 15.00},
    "gpt-4o-mini":         {"tier": "Scout",      "input": 0.15,  "output": 0.60},
    "gpt-4.1":             {"tier": "Advisor",    "input": 2.00,  "output": 8.00},
    "gpt-4.1-mini":        {"tier": "Analyst",    "input": 0.40,  "output": 1.60},
    "gpt-4.1-nano":        {"tier": "Scout",      "input": 0.10,  "output": 0.40},
    "gpt-4-turbo":         {"tier": "Strategist", "input": 10.00, "output": 30.00},
    "gpt-3.5-turbo":       {"tier": "Analyst",    "input": 0.50,  "output": 1.50},
    "o3":                  {"tier": "Strategist", "input": 10.00, "output": 40.00},
    "o3-mini":             {"tier": "Analyst",    "input": 1.10,  "output": 4.40},
    "o4-mini":             {"tier": "Analyst",    "input": 1.10,  "output": 4.40},
}

ANTHROPIC_TIERS = {
    "claude-opus-4-8":           {"tier": "Strategist", "input": 15.00, "output": 75.00},
    "claude-opus-4-6":           {"tier": "Strategist", "input": 15.00, "output": 75.00},
    "claude-sonnet-4-6":         {"tier": "Advisor",    "input": 3.00,  "output": 15.00},
    "claude-haiku-4-5-20251001": {"tier": "Scout",      "input": 0.80,  "output": 4.00},
    "claude-3-5-sonnet-20241022":{"tier": "Advisor",    "input": 3.00,  "output": 15.00},
    "claude-3-haiku-20240307":   {"tier": "Scout",      "input": 0.25,  "output": 1.25},
}

COMPLEXITY_KEYWORDS = ["analyze", "explain", "draft", "summarize", "compare", "evaluate",
                        "recommend", "generate", "create", "write", "plan", "design",
                        "calculate", "predict", "debug", "review", "legal", "contract"]

SCOUT_OPENAI     = "gpt-4o-mini"
SCOUT_ANTHROPIC  = "claude-haiku-4-5-20251001"


def _is_complex(messages: list, token_count: int) -> bool:
    if token_count > 500:
        return True
    text = " ".join(
        m.get("content", "") if isinstance(m.get("content"), str)
        else " ".join(b.get("text", "") for b in m.get("content", []) if isinstance(b, dict))
        for m in messages
    ).lower()
    return any(kw in text for kw in COMPLEXITY_KEYWORDS)


def _estimate_tokens(messages: list) -> int:
    total = 0
    for m in messages:
        content = m.get("content", "")
        if isinstance(content, str):
            total += len(content) // 4
        elif isinstance(content, list):
            total += sum(len(b.get("text", "")) // 4 for b in content if isinstance(b, dict))
    return total


def _get_account(workspace_id: str, secret_key: str, db):
    account = db.query(TrialAccount).filter_by(workspace_id=workspace_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Workspace not found.")
    if not account.is_active:
        raise HTTPException(status_code=403, detail="This workspace trial has expired.")
    if account.secret_key != secret_key:
        raise HTTPException(status_code=401, detail="Invalid X-CostPilot-Key.")
    return account


def _decode_key(account: TrialAccount) -> str:
    return b64decode(account.api_key_enc.encode()).decode()


def _log_transaction(db, workspace_id: str, department: str, model: str,
                     tier: str, input_tokens: int, output_tokens: int,
                     cost_usd: float, routing_reason: str):
    txn = TokenTransaction(
        department     = f"{workspace_id}:{department}",
        source_platform= "trial-proxy",
        model_tier     = tier,
        input_tokens   = input_tokens,
        output_tokens  = output_tokens,
        cost_usd       = cost_usd,
        timestamp      = datetime.utcnow(),
        routing_reason = routing_reason,
        was_pruned     = False,
        tokens_saved   = 0,
    )
    db.add(txn)
    db.commit()


# ── OpenAI-compatible proxy ───────────────────────────────────────────────────

@router.post("/ws-{workspace_id}/chat/completions")
async def proxy_openai(workspace_id: str, request: Request):
    secret_key = request.headers.get("X-CostPilot-Key", "")
    department = request.headers.get("X-Department", "default")

    db = SessionLocal()
    try:
        account = _get_account(workspace_id, secret_key, db)
        api_key = _decode_key(account)

        body     = await request.json()
        messages = body.get("messages", [])
        tokens   = _estimate_tokens(messages)
        complex_ = _is_complex(messages, tokens)

        # Route: complex → requested model, routine → Scout
        requested_model = body.get("model", "gpt-4o")
        routed_model    = requested_model if complex_ else SCOUT_OPENAI
        routing_reason  = "COMPLEX" if complex_ else "ROUTINE"

        # Forward to OpenAI
        forward_body = {**body, "model": routed_model}
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}",
                         "Content-Type": "application/json"},
                json=forward_body,
            )

        resp_data    = resp.json()
        usage        = resp_data.get("usage", {})
        in_tokens    = usage.get("prompt_tokens", tokens)
        out_tokens   = usage.get("completion_tokens", 100)
        tier_meta    = OPENAI_TIERS.get(routed_model, {"tier": "Advisor", "input": 5.0, "output": 15.0})
        cost         = (in_tokens * tier_meta["input"] + out_tokens * tier_meta["output"]) / 1_000_000

        _log_transaction(db, workspace_id, department, routed_model,
                         tier_meta["tier"], in_tokens, out_tokens, cost, routing_reason)

        return JSONResponse(content=resp_data, status_code=resp.status_code)

    finally:
        db.close()


# ── Anthropic-compatible proxy ────────────────────────────────────────────────

@router.post("/ws-{workspace_id}/messages")
async def proxy_anthropic(workspace_id: str, request: Request):
    secret_key = request.headers.get("X-CostPilot-Key", "")
    department = request.headers.get("X-Department", "default")

    db = SessionLocal()
    try:
        account = _get_account(workspace_id, secret_key, db)
        api_key = _decode_key(account)

        body     = await request.json()
        messages = body.get("messages", [])
        tokens   = _estimate_tokens(messages)
        complex_ = _is_complex(messages, tokens)

        requested_model = body.get("model", "claude-sonnet-4-6")
        routed_model    = requested_model if complex_ else SCOUT_ANTHROPIC
        routing_reason  = "COMPLEX" if complex_ else "ROUTINE"

        forward_body = {**body, "model": routed_model}
        forward_headers = {
            "x-api-key":          api_key,
            "anthropic-version":  request.headers.get("anthropic-version", "2023-06-01"),
            "content-type":       "application/json",
        }
        if "anthropic-beta" in request.headers:
            forward_headers["anthropic-beta"] = request.headers["anthropic-beta"]

        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers=forward_headers,
                json=forward_body,
            )

        resp_data = resp.json()
        usage     = resp_data.get("usage", {})
        in_tokens = usage.get("input_tokens", tokens)
        out_tokens= usage.get("output_tokens", 100)
        tier_meta = ANTHROPIC_TIERS.get(routed_model,
                        {"tier": "Advisor", "input": 3.0, "output": 15.0})
        cost      = (in_tokens * tier_meta["input"] + out_tokens * tier_meta["output"]) / 1_000_000

        _log_transaction(db, workspace_id, department, routed_model,
                         tier_meta["tier"], in_tokens, out_tokens, cost, routing_reason)

        return JSONResponse(content=resp_data, status_code=resp.status_code)

    finally:
        db.close()
