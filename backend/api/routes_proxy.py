"""
api/routes_proxy.py — CostPilot Customer Proxy

POST /v1/ws-{workspace_id}/chat/completions   — OpenAI-compatible proxy
POST /v1/ws-{workspace_id}/messages           — Anthropic-compatible proxy

Each request:
  1. Validates workspace_id + X-CostPilot-Key header
  2. Prunes user message content (strips HTML, email chains, disclaimers)
  3. Runs CostPilot's complexity router to pick the right tier
  4. Forwards to the real API using the customer's key (passed through)
  5. Logs the transaction tagged to workspace_id:department
  6. Auto-registers the agent in Agent Lake if first seen
  7. Returns the real API response + X-CostPilot-* metadata headers
"""

import httpx
import json
from base64 import b64decode
from datetime import datetime
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse

from database.db import SessionLocal
from database.models import TrialAccount, TokenTransaction, RegisteredAgent
from core.pruner import prune

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


def _prune_messages(messages: list) -> tuple:
    """Run the pruner on user message content. Returns (pruned_messages, total_tokens_saved)."""
    pruned   = []
    saved    = 0
    for msg in messages:
        if msg.get("role") != "user":
            pruned.append(msg)
            continue
        content = msg.get("content", "")
        if isinstance(content, str) and content.strip():
            result   = prune(content)
            saved   += result["tokens_saved"]
            pruned.append({**msg, "content": result["cleaned_text"]})
        elif isinstance(content, list):
            # Multi-part content (text + images)
            new_parts = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    result  = prune(part.get("text", ""))
                    saved  += result["tokens_saved"]
                    new_parts.append({**part, "text": result["cleaned_text"]})
                else:
                    new_parts.append(part)
            pruned.append({**msg, "content": new_parts})
        else:
            pruned.append(msg)
    return pruned, saved


def _ensure_agent(db, workspace_id: str, department: str, platform: str):
    """Auto-register an agent for this workspace+department on first call."""
    agent_name = f"{platform.capitalize()} · {department}"
    dept_key   = f"{workspace_id}:{department}"
    existing   = db.query(RegisteredAgent).filter_by(
        name=agent_name, department=dept_key
    ).first()
    if existing:
        existing.last_used_at = datetime.utcnow()
        existing.status       = "active"
        db.commit()
        return existing
    agent = RegisteredAgent(
        name             = agent_name,
        department       = dept_key,
        source_platform  = platform,
        permissions      = "read,write",
        status           = "active",
        collision_policy = "lock",
        last_used_at     = datetime.utcnow(),
        pruning_enabled  = True,
        min_tier         = 1,
        max_tier         = 4,
    )
    db.add(agent)
    db.commit()
    return agent


def _log_transaction(db, workspace_id: str, department: str, model: str,
                     tier: str, input_tokens: int, output_tokens: int,
                     cost_usd: float, routing_reason: str,
                     tokens_saved: int = 0):
    dept_key = f"{workspace_id}:{department}"
    txn = TokenTransaction(
        department     = dept_key,
        source_platform= "trial-proxy",
        model_tier     = tier,
        input_tokens   = input_tokens,
        output_tokens  = output_tokens,
        cost_usd       = cost_usd,
        timestamp      = datetime.utcnow(),
        routing_reason = routing_reason,
        was_pruned     = tokens_saved > 0,
        tokens_saved   = tokens_saved,
    )
    db.add(txn)
    db.commit()





# ── Workspace status page (GET) ──────────────────────────────────────────────

@router.get("/ws-{workspace_id}", response_class=HTMLResponse)
def workspace_status(workspace_id: str):
    db = SessionLocal()
    try:
        account = db.query(TrialAccount).filter_by(workspace_id=workspace_id).first()
        if not account:
            return HTMLResponse(content="""
                <html><body style="font-family:sans-serif;max-width:500px;margin:60px auto;color:#333">
                <h2>❌ Workspace not found</h2>
                <p>No workspace with this ID exists. Check your Getting Started page for the correct URL.</p>
                </body></html>""", status_code=404)

        from datetime import datetime
        days_left = max(0, (account.trial_end - datetime.utcnow()).days)
        status_color = "#22c55e" if days_left > 7 else "#f59e0b" if days_left > 0 else "#ef4444"
        status_text  = f"{days_left} days remaining" if days_left > 0 else "Trial expired"

        return HTMLResponse(content=f"""
<!DOCTYPE html>
<html><head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>CostPilot — Workspace {workspace_id}</title>
<style>
  body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0d1117;color:#e6edf3;margin:0;display:flex;align-items:center;justify-content:center;min-height:100vh}}
  .card{{background:#161b22;border:1px solid #30363d;border-radius:12px;padding:40px;max-width:480px;width:100%;text-align:center}}
  .logo{{font-size:32px;margin-bottom:8px}}
  h1{{font-size:22px;font-weight:800;margin:0 0 4px;color:#fff}}
  .sub{{font-size:13px;color:#8b949e;margin-bottom:28px}}
  .badge{{display:inline-block;padding:4px 14px;border-radius:20px;font-size:12px;font-weight:700;background:{status_color}22;color:{status_color};border:1px solid {status_color}44;margin-bottom:24px}}
  .row{{display:flex;justify-content:space-between;align-items:center;padding:10px 14px;background:#0d1117;border-radius:8px;margin-bottom:8px;text-align:left}}
  .row-label{{font-size:10px;text-transform:uppercase;letter-spacing:.07em;color:#8b949e;font-weight:700}}
  .row-value{{font-family:monospace;font-size:13px;color:#58a6ff}}
  .endpoints{{background:#0d1117;border-radius:8px;padding:14px;margin:16px 0;text-align:left}}
  .endpoint{{font-family:monospace;font-size:11px;color:#3fb950;padding:3px 0}}
  .method{{color:#d29922;margin-right:8px}}
  a{{color:#58a6ff;text-decoration:none;font-size:13px}}
</style>
</head><body>
<div class="card">
  <div class="logo">◈</div>
  <h1>CostPilot Proxy</h1>
  <div class="sub">Your workspace is active and ready to route API calls</div>
  <div class="badge">{status_text}</div>
  <div class="row">
    <div class="row-label">Workspace ID</div>
    <div class="row-value">{workspace_id}</div>
  </div>
  <div class="row">
    <div class="row-label">Account</div>
    <div class="row-value" style="font-family:sans-serif;font-size:13px;color:#e6edf3">{account.name}</div>
  </div>
  <div class="row">
    <div class="row-label">Provider</div>
    <div class="row-value" style="font-family:sans-serif">{account.provider.capitalize()}</div>
  </div>
  <div class="endpoints">
    <div style="font-size:10px;text-transform:uppercase;letter-spacing:.07em;color:#8b949e;font-weight:700;margin-bottom:8px">Proxy endpoints</div>
    <div class="endpoint"><span class="method">POST</span>/v1/ws-{workspace_id}/chat/completions</div>
    <div class="endpoint"><span class="method">POST</span>/v1/ws-{workspace_id}/messages</div>
  </div>
  <p style="font-size:12px;color:#8b949e;line-height:1.6">
    This URL is your proxy base. API calls go to the endpoints above — this page confirms your workspace is live.
  </p>
  <a href="/getting-started.html">← Back to Getting Started</a>
</div>
</body></html>""")
    finally:
        db.close()


# ── OpenAI-compatible proxy ───────────────────────────────────────────────────

@router.post("/ws-{workspace_id}/chat/completions")
async def proxy_openai(workspace_id: str, request: Request):
    secret_key = request.headers.get("X-CostPilot-Key", "")
    department = request.headers.get("X-Department", "default")
    platform   = request.headers.get("X-Platform", "api")
    auth_header = request.headers.get("Authorization", "")

    if not auth_header:
        raise HTTPException(status_code=401,
            detail="Missing Authorization header. Your OpenAI API key should be in your code as normal — CostPilot just routes it.")

    db = SessionLocal()
    try:
        account  = _get_account(workspace_id, secret_key, db)
        body     = await request.json()
        messages = body.get("messages", [])

        # 1. Prune user messages
        pruned_messages, tokens_saved = _prune_messages(messages)

        # 2. Complexity routing on pruned content
        tokens   = _estimate_tokens(pruned_messages)
        complex_ = _is_complex(pruned_messages, tokens)

        requested_model = body.get("model", "gpt-4o")
        routed_model    = requested_model if complex_ else SCOUT_OPENAI
        routing_reason  = "COMPLEX" if complex_ else "ROUTINE"

        # 3. Forward with pruned messages
        forward_body = {**body, "model": routed_model, "messages": pruned_messages}
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": auth_header, "Content-Type": "application/json"},
                json=forward_body,
            )

        resp_data  = resp.json()
        usage      = resp_data.get("usage", {})
        in_tokens  = usage.get("prompt_tokens", tokens)
        out_tokens = usage.get("completion_tokens", 100)
        tier_meta  = OPENAI_TIERS.get(routed_model, {"tier": "Advisor", "input": 5.0, "output": 15.0})
        cost       = round((in_tokens * tier_meta["input"] + out_tokens * tier_meta["output"]) / 1_000_000, 8)

        # 4. Log transaction
        _log_transaction(db, workspace_id, department, routed_model,
                         tier_meta["tier"], in_tokens, out_tokens, cost,
                         routing_reason, tokens_saved)

        # 5. Auto-register agent
        _ensure_agent(db, workspace_id, department, platform)

        # 6. Return response + CostPilot metadata headers
        return JSONResponse(
            content=resp_data,
            status_code=resp.status_code,
            headers={
                "X-CostPilot-Model":        routed_model,
                "X-CostPilot-Tier":         tier_meta["tier"],
                "X-CostPilot-Routing":      routing_reason,
                "X-CostPilot-Cost":         str(cost),
                "X-CostPilot-Tokens-Saved": str(tokens_saved),
                "X-CostPilot-Workspace":    workspace_id,
            }
        )

    finally:
        db.close()


# ── Anthropic-compatible proxy ────────────────────────────────────────────────

@router.post("/ws-{workspace_id}/messages")
async def proxy_anthropic(workspace_id: str, request: Request):
    secret_key    = request.headers.get("X-CostPilot-Key", "")
    department    = request.headers.get("X-Department", "default")
    platform      = request.headers.get("X-Platform", "api")
    anthropic_key = request.headers.get("x-api-key", "")

    if not anthropic_key:
        raise HTTPException(status_code=401,
            detail="Missing x-api-key header. Your Anthropic API key should be in your code as normal — CostPilot just routes it.")

    db = SessionLocal()
    try:
        account  = _get_account(workspace_id, secret_key, db)
        body     = await request.json()
        messages = body.get("messages", [])

        # 1. Prune user messages
        pruned_messages, tokens_saved = _prune_messages(messages)

        # 2. Complexity routing
        tokens   = _estimate_tokens(pruned_messages)
        complex_ = _is_complex(pruned_messages, tokens)

        requested_model = body.get("model", "claude-sonnet-4-6")
        routed_model    = requested_model if complex_ else SCOUT_ANTHROPIC
        routing_reason  = "COMPLEX" if complex_ else "ROUTINE"

        # 3. Forward with pruned messages
        forward_body    = {**body, "model": routed_model, "messages": pruned_messages}
        forward_headers = {
            "x-api-key":         anthropic_key,
            "anthropic-version": request.headers.get("anthropic-version", "2023-06-01"),
            "content-type":      "application/json",
        }
        if "anthropic-beta" in request.headers:
            forward_headers["anthropic-beta"] = request.headers["anthropic-beta"]

        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers=forward_headers,
                json=forward_body,
            )

        resp_data  = resp.json()
        usage      = resp_data.get("usage", {})
        in_tokens  = usage.get("input_tokens", tokens)
        out_tokens = usage.get("output_tokens", 100)
        tier_meta  = ANTHROPIC_TIERS.get(routed_model,
                         {"tier": "Advisor", "input": 3.0, "output": 15.0})
        cost       = round((in_tokens * tier_meta["input"] + out_tokens * tier_meta["output"]) / 1_000_000, 8)

        # 4. Log + 5. Auto-register agent
        _log_transaction(db, workspace_id, department, routed_model,
                         tier_meta["tier"], in_tokens, out_tokens, cost,
                         routing_reason, tokens_saved)
        _ensure_agent(db, workspace_id, department, platform)

        # 6. Return response + CostPilot metadata headers
        return JSONResponse(
            content=resp_data,
            status_code=resp.status_code,
            headers={
                "X-CostPilot-Model":        routed_model,
                "X-CostPilot-Tier":         tier_meta["tier"],
                "X-CostPilot-Routing":      routing_reason,
                "X-CostPilot-Cost":         str(cost),
                "X-CostPilot-Tokens-Saved": str(tokens_saved),
                "X-CostPilot-Workspace":    workspace_id,
            }
        )

    finally:
        db.close()
