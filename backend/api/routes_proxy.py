"""
api/routes_proxy.py — CostPilot Customer Proxy

POST /v1/ws-{workspace_id}/chat/completions   — OpenAI-compatible proxy
POST /v1/ws-{workspace_id}/messages           — Anthropic-compatible proxy

Each request:
  1. Validates workspace_id + X-CostPilot-Key header
  2. Prunes user message content (strips HTML, email chains, disclaimers)
  3. Runs CostPilot's complexity router to pick the right tier
  4. Forwards to the real API using CostPilot's managed provider key
  5. Logs the transaction tagged to workspace_id:department
  6. Auto-registers the agent in Agent Lake if first seen
  7. Returns the real API response + X-CostPilot-* metadata headers
"""

import httpx
import json
import os
from base64 import b64decode
from datetime import datetime
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse

from database.db import SessionLocal
from database.models import TrialAccount, TokenTransaction, RegisteredAgent, AuditEvent, SensitiveTerm
from core.keywords import term_matches_text
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

COMPLEXITY_KEYWORDS_DEFAULT = [
    "analyze", "explain", "draft", "summarize", "compare", "evaluate",
    "recommend", "generate", "create", "write", "plan", "design",
    "calculate", "predict", "debug", "review", "legal", "contract",
    "urgent", "critical", "escalate", "breach", "compliance", "hipaa",
    "lawsuit", "dispute", "negotiate", "appeal", "investigate",
]

SCOUT_OPENAI    = "gpt-4o-mini"
SCOUT_ANTHROPIC = "claude-haiku-4-5-20251001"
DEFAULT_TRIAL_CALL_CAP = 500
DEFAULT_TRIAL_SPEND_CAP_USD = 10.0
TIER_NUM_BY_NAME = {"Scout": 1, "Analyst": 2, "Advisor": 3, "Strategist": 4}
TIER_NAME_BY_NUM = {1: "Scout", 2: "Analyst", 3: "Advisor", 4: "Strategist"}
OPENAI_MODEL_BY_TIER = {
    1: SCOUT_OPENAI,
    2: "gpt-4.1-mini",
    3: "gpt-4o",
    4: "gpt-4-turbo",
}
ANTHROPIC_MODEL_BY_TIER = {
    1: SCOUT_ANTHROPIC,
    2: "claude-sonnet-4-6",
    3: "claude-sonnet-4-6",
    4: "claude-opus-4-6",
}


def _check_sensitive_terms(text: str, db, department: str = None) -> dict:
    """
    Check text against SensitiveTerm table.
    Returns {"action": "none"|"flag"|"escalate"|"block", "matched": [terms]}
    Strongest action wins: block > escalate > flag > none.
    """
    terms = db.query(SensitiveTerm).filter(
        (SensitiveTerm.department == None) | (SensitiveTerm.department == department)
    ).all()
    matched_flag = []
    matched_escalate = []
    matched_block = []
    for t in terms:
        if term_matches_text(t.term, text):
            if t.action == "block":
                matched_block.append(t.term)
            elif t.action == "escalate":
                matched_escalate.append(t.term)
            else:
                matched_flag.append(t.term)
    if matched_block:
        return {"action": "block",    "matched": matched_block}
    if matched_escalate:
        return {"action": "escalate", "matched": matched_escalate}
    if matched_flag:
        return {"action": "flag",     "matched": matched_flag}
    return {"action": "none",         "matched": []}


def _get_keywords(db) -> list:
    """Merge DB routing config keywords with defaults — both always apply."""
    try:
        from core.routing_config import get_routing_config
        cfg = get_routing_config(db)
        db_kws = cfg.complexity_keywords or []
        return list(set(COMPLEXITY_KEYWORDS_DEFAULT + db_kws))
    except Exception:
        return COMPLEXITY_KEYWORDS_DEFAULT


def _complexity_check(messages: list, token_count: int, keywords: list = None) -> tuple:
    """Check ORIGINAL (unpruned) messages — keywords must not be stripped before detection."""
    kws  = keywords or COMPLEXITY_KEYWORDS_DEFAULT
    text = " ".join(
        m.get("content", "") if isinstance(m.get("content"), str)
        else " ".join(b.get("text", "") for b in m.get("content", []) if isinstance(b, dict))
        for m in messages
    )
    matched = [kw for kw in kws if term_matches_text(kw, text)]
    return (token_count > 500 or bool(matched)), matched


def _estimate_tokens(messages: list) -> int:
    total = 0
    for m in messages:
        content = m.get("content", "")
        if isinstance(content, str):
            total += len(content) // 4
        elif isinstance(content, list):
            total += sum(len(b.get("text", "")) // 4 for b in content if isinstance(b, dict))
    return total


def _apply_agent_tier_bounds(model_id: str, tier_table: dict, default_models: dict, agent: RegisteredAgent = None) -> tuple:
    """Clamp a selected model to the registered agent's min/max tier policy."""
    if not agent:
        return model_id, ""

    meta = tier_table.get(model_id, {})
    current_tier = TIER_NUM_BY_NAME.get(meta.get("tier"), 3)
    agent_min = max(1, min(4, agent.min_tier or 1))
    agent_max = max(1, min(4, agent.max_tier or 4))
    final_tier = max(agent_min, min(agent_max, current_tier))
    if final_tier == current_tier:
        return model_id, ""

    bounded_model = default_models.get(final_tier, model_id)
    direction = "bumped up" if final_tier > current_tier else "capped down"
    note = (
        f"[AGENT BOUND: {direction} from {TIER_NAME_BY_NUM.get(current_tier, current_tier)} "
        f"to {TIER_NAME_BY_NUM.get(final_tier, final_tier)} by agent policy]"
    )
    return bounded_model, note


def _get_account(workspace_id: str, secret_key: str, db):
    account = db.query(TrialAccount).filter_by(workspace_id=workspace_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Workspace not found.")
    if not account.is_active or datetime.utcnow() > account.trial_end:
        account.is_active = False
        db.commit()
        raise HTTPException(status_code=403, detail="This workspace trial has expired. Upgrade to continue routing AI requests through CostPilot.")
    if account.secret_key != secret_key:
        raise HTTPException(status_code=401, detail="Invalid X-CostPilot-Key.")
    return account


def _trial_caps(account: TrialAccount) -> tuple[int, float]:
    return int(account.trial_call_cap or DEFAULT_TRIAL_CALL_CAP), float(account.trial_spend_cap_usd or DEFAULT_TRIAL_SPEND_CAP_USD)


def _workspace_usage(db, workspace_id: str) -> dict:
    prefix = f"{workspace_id}:"
    txns = db.query(TokenTransaction).filter(
        TokenTransaction.department.like(f"{prefix}%")
    ).all()
    return {
        "calls": len(txns),
        "spend_usd": round(sum(t.cost_usd or 0.0 for t in txns), 6),
    }


def _enforce_trial_limits(account: TrialAccount, db):
    if account.plan not in ("trial", "free"):
        return
    usage = _workspace_usage(db, account.workspace_id)
    call_cap, spend_cap = _trial_caps(account)
    if usage["calls"] >= call_cap or usage["spend_usd"] >= spend_cap:
        raise HTTPException(
            status_code=402,
            detail={
                "error": "trial_limit_reached",
                "message": "This trial workspace has reached its usage limit. Upgrade to continue routing AI requests through CostPilot.",
                "usage": usage,
                "limits": {"call_cap": call_cap, "spend_cap_usd": spend_cap},
            },
        )


def _stored_provider_key(account: TrialAccount) -> str:
    try:
        return b64decode((account.api_key_enc or "").encode()).decode()
    except Exception:
        return ""


def _managed_provider_key(provider: str) -> str:
    if provider == "anthropic":
        return os.getenv("ANTHROPIC_API_KEY", "")
    return os.getenv("OPENAI_API_KEY", "")


def _is_placeholder_provider_key(value: str) -> bool:
    token = (value or "").replace("Bearer", "").strip().lower()
    return token in {
        "",
        "costpilot-managed",
        "cp-managed",
        "sk-costpilot-managed",
        "not-used",
        "managed-by-costpilot",
    }


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


def _ensure_agent(db, workspace_id: str, department: str, platform: str, agent_name: str = None):
    """Auto-register an agent for this workspace+department on first call."""
    agent_name = (agent_name or "").strip() or f"{platform.capitalize()} · {department}"
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
    db.refresh(agent)
    return agent


def _schedule_agent_idle(agent_id: int, delay_seconds: int = 4):
    """Return an active agent to idle shortly after the request finishes."""
    if not agent_id:
        return
    import threading
    import time

    def _reset():
        time.sleep(delay_seconds)
        _db = SessionLocal()
        try:
            _agent = _db.query(RegisteredAgent).filter_by(id=agent_id).first()
            if _agent and (_agent.status or "").lower() == "active":
                _agent.status = "idle"
                _agent.target_record_id = None
                _db.commit()
        finally:
            _db.close()

    threading.Thread(target=_reset, daemon=True).start()


def _log_transaction(db, workspace_id: str, department: str, model: str,
                     tier: str, input_tokens: int, output_tokens: int,
                     cost_usd: float, routing_reason: str,
                     tokens_saved: int = 0, matched_keywords: list = None,
                     prompt_text: str = "", risk_override: str = None,
                     agent_id: int = None, usage_source: str = "estimated"):
    from database.models import DepartmentBudget
    import json

    dept_key = f"{workspace_id}:{department}"
    now = datetime.utcnow()

    # Token transaction
    txn = TokenTransaction(
        department     = dept_key,
        source_platform= "trial-proxy",
        agent_id       = agent_id,
        model_tier     = tier,
        input_tokens   = input_tokens,
        output_tokens  = output_tokens,
        usage_source   = usage_source,
        cost_usd       = cost_usd,
        timestamp      = now,
        routing_reason = routing_reason,
        was_pruned     = tokens_saved > 0,
        tokens_saved   = tokens_saved,
    )
    db.add(txn)

    # Budget context — try workspace-prefixed key first, then plain dept name
    budget = (
        db.query(DepartmentBudget).filter_by(department=dept_key).first() or
        db.query(DepartmentBudget).filter_by(department=department).first()
    )
    budget_ctx = {
        "department": dept_key,
        "budget_spent_usd": None,
        "budget_cap_usd": None,
        "budget_used_pct": None,
        "throttled": None,
        "override_granted": None,
        "captured_at": now.isoformat(),
    }
    if budget:
        budget.current_spend_usd = round((budget.current_spend_usd or 0.0) + cost_usd, 6)
        if budget.current_spend_usd >= budget.monthly_cap_usd and not budget.override_granted:
            budget.throttled = True
        used_pct = round(budget.current_spend_usd / budget.monthly_cap_usd * 100, 1) if budget.monthly_cap_usd else 0
        budget_ctx.update({
            "department":       budget.department,
            "budget_spent_usd": round(budget.current_spend_usd, 4),
            "budget_cap_usd":   budget.monthly_cap_usd,
            "budget_used_pct":  used_pct,
            "throttled":        budget.throttled,
            "override_granted": budget.override_granted,
        })
    if tokens_saved or input_tokens:
        raw_tokens = int((input_tokens or 0) + (tokens_saved or 0))
        budget_ctx.update({
            "raw_tokens": raw_tokens,
            "clean_tokens": int(input_tokens or 0),
            "tokens_saved": int(tokens_saved or 0),
            "compression_pct": round((tokens_saved / raw_tokens) * 100, 1) if raw_tokens > 0 else 0.0,
            "usage_source": usage_source,
        })

    # Audit event — feeds Governance Event Stream + decision ledger
    is_routine = routing_reason.startswith("ROUTINE") and not routing_reason == "BLOCKED"
    risk_level = risk_override or ("low" if is_routine else "medium")
    kw_str     = ", ".join(matched_keywords) if matched_keywords else ""

    rationale  = (
        f"{'Routine' if is_routine else 'Complex'} call routed to {tier} tier ({model})."
        + (f" Complexity keywords matched: {kw_str}." if kw_str else "")
        + (f" {tokens_saved:,} tokens pruned before sending." if tokens_saved > 0 else "")
    )
    outcome = f"ROUTED_TO_{tier.upper().replace(' ', '_')}"

    audit = AuditEvent(
        event_type            = "ROUTING",
        agent_id              = agent_id,
        department            = dept_key,
        model_tier            = tier,
        rationale             = rationale,
        decision_outcome      = outcome,
        risk_level            = risk_level,
        timestamp             = now,
        context_snapshot      = json.dumps(budget_ctx),
        prompt_payload        = prompt_text[:400] if prompt_text else None,
        matched_keywords_json = json.dumps(matched_keywords or []),
    )
    db.add(audit)
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
    agent_name = request.headers.get("X-Agent-Name", "")
    auth_header = request.headers.get("Authorization", "")

    db = SessionLocal()
    agent = None
    try:
        account = _get_account(workspace_id, secret_key, db)
        _enforce_trial_limits(account, db)

        # CostPilot-owned proxy: customer integrations authenticate with
        # X-CostPilot-Key; provider credentials stay server-side.
        if _is_placeholder_provider_key(auth_header):
            provider_key = _stored_provider_key(account) or _managed_provider_key("openai")
            auth_header = f"Bearer {provider_key}" if provider_key else ""
        if not auth_header:
            raise HTTPException(status_code=401,
                detail="CostPilot-managed OpenAI key is not configured for this trial workspace.")
        body     = await request.json()
        messages = body.get("messages", [])
        keywords = _get_keywords(db)

        # Extract full text for checks
        full_text = " ".join(
            m.get("content", "") if isinstance(m.get("content"), str) else ""
            for m in messages if m.get("role") == "user"
        )

        # 1. Sensitive term check — runs before routing
        st = _check_sensitive_terms(full_text, db, department)
        if st["action"] == "block":
            _log_transaction(db, workspace_id, department, "BLOCKED", "Blocked",
                             0, 0, 0.0, "BLOCKED", 0, st["matched"], full_text[:400])
            return JSONResponse(
                status_code=403,
                content={"error": "blocked", "detail": f"Request blocked — sensitive terms detected: {', '.join(st['matched'])}"},
                headers={"X-CostPilot-Routing": "BLOCKED", "X-CostPilot-Workspace": workspace_id}
            )

        # 2. Complexity check on ORIGINAL messages
        tokens               = _estimate_tokens(messages)
        complex_, matched_kw = _complexity_check(messages, tokens, keywords)

        # Force Advisor tier if sensitive term escalation triggered
        if st["action"] == "escalate":
            complex_   = True
            matched_kw = matched_kw + [f"[escalate:{t}]" for t in st["matched"]]

        # 3. Prune for forwarding
        pruned_messages, tokens_saved = _prune_messages(messages)
        prompt_text = " ".join(
            m.get("content", "") if isinstance(m.get("content"), str) else ""
            for m in pruned_messages if m.get("role") == "user"
        )

        requested_model = body.get("model", "gpt-4o")
        routed_model    = requested_model if complex_ else SCOUT_OPENAI
        routing_reason  = "COMPLEX" if complex_ else "ROUTINE"
        if st["action"] in ("flag", "escalate"):
            routing_reason = f"{routing_reason}_FLAGGED"

        agent = _ensure_agent(db, workspace_id, department, platform, agent_name)
        routed_model, bound_note = _apply_agent_tier_bounds(routed_model, OPENAI_TIERS, OPENAI_MODEL_BY_TIER, agent)
        if bound_note:
            routing_reason = f"{routing_reason} {bound_note}"

        # 4. Forward with pruned messages
        forward_body = {**body, "model": routed_model, "messages": pruned_messages}
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": auth_header, "Content-Type": "application/json"},
                json=forward_body,
            )

        resp_data  = resp.json()
        usage      = resp_data.get("usage", {})
        usage_source = "provider_reported" if usage else "estimated"
        in_tokens  = usage.get("prompt_tokens", tokens)
        out_tokens = usage.get("completion_tokens", 100)
        tier_meta  = OPENAI_TIERS.get(routed_model, {"tier": "Advisor", "input": 5.0, "output": 15.0})
        cost       = round((in_tokens * tier_meta["input"] + out_tokens * tier_meta["output"]) / 1_000_000, 8)
        risk       = "high" if st["action"] in ("flag","escalate") else ("medium" if complex_ else "low")

        # 5. Log the completed request
        _log_transaction(db, workspace_id, department, routed_model,
                         tier_meta["tier"], in_tokens, out_tokens, cost,
                         routing_reason, tokens_saved,
                         matched_kw + st["matched"], prompt_text, risk_override=risk,
                         agent_id=agent.id if agent else None, usage_source=usage_source)

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
        if agent:
            _schedule_agent_idle(agent.id)
        db.close()


# ── Anthropic-compatible proxy ────────────────────────────────────────────────

@router.post("/ws-{workspace_id}/messages")
async def proxy_anthropic(workspace_id: str, request: Request):
    secret_key    = request.headers.get("X-CostPilot-Key", "")
    department    = request.headers.get("X-Department", "default")
    platform      = request.headers.get("X-Platform", "api")
    agent_name    = request.headers.get("X-Agent-Name", "")
    anthropic_key = request.headers.get("x-api-key", "")

    db = SessionLocal()
    agent = None
    try:
        account = _get_account(workspace_id, secret_key, db)
        _enforce_trial_limits(account, db)

        # CostPilot-owned proxy: customer integrations authenticate with
        # X-CostPilot-Key; provider credentials stay server-side.
        if _is_placeholder_provider_key(anthropic_key):
            anthropic_key = _stored_provider_key(account) or _managed_provider_key("anthropic")
        if not anthropic_key:
            raise HTTPException(status_code=401,
                detail="CostPilot-managed Anthropic key is not configured for this trial workspace.")
        body     = await request.json()
        messages = body.get("messages", [])
        keywords = _get_keywords(db)

        full_text = " ".join(
            m.get("content", "") if isinstance(m.get("content"), str) else ""
            for m in messages if m.get("role") == "user"
        )

        # 1. Sensitive term check
        st = _check_sensitive_terms(full_text, db, department)
        if st["action"] == "block":
            _log_transaction(db, workspace_id, department, "BLOCKED", "Blocked",
                             0, 0, 0.0, "BLOCKED", 0, st["matched"], full_text[:400])
            return JSONResponse(
                status_code=403,
                content={"error": "blocked", "detail": f"Request blocked — sensitive terms detected: {', '.join(st['matched'])}"},
                headers={"X-CostPilot-Routing": "BLOCKED", "X-CostPilot-Workspace": workspace_id}
            )

        # 2. Complexity check on ORIGINAL messages
        tokens               = _estimate_tokens(messages)
        complex_, matched_kw = _complexity_check(messages, tokens, keywords)

        if st["action"] == "escalate":
            complex_   = True
            matched_kw = matched_kw + [f"[escalate:{t}]" for t in st["matched"]]

        # 3. Prune for forwarding
        pruned_messages, tokens_saved = _prune_messages(messages)
        prompt_text = " ".join(
            m.get("content", "") if isinstance(m.get("content"), str) else ""
            for m in pruned_messages if m.get("role") == "user"
        )

        requested_model = body.get("model", "claude-sonnet-4-6")
        routed_model    = requested_model if complex_ else SCOUT_ANTHROPIC
        routing_reason  = "COMPLEX" if complex_ else "ROUTINE"
        if st["action"] in ("flag", "escalate"):
            routing_reason = f"{routing_reason}_FLAGGED"
        risk = "high" if st["action"] in ("flag","escalate") else ("medium" if complex_ else "low")

        forward_headers = {
            "x-api-key":         anthropic_key,
            "anthropic-version": request.headers.get("anthropic-version", "2023-06-01"),
            "content-type":      "application/json",
        }
        if "anthropic-beta" in request.headers:
            forward_headers["anthropic-beta"] = request.headers["anthropic-beta"]

        agent = _ensure_agent(db, workspace_id, department, platform, agent_name)
        routed_model, bound_note = _apply_agent_tier_bounds(routed_model, ANTHROPIC_TIERS, ANTHROPIC_MODEL_BY_TIER, agent)
        if bound_note:
            routing_reason = f"{routing_reason} {bound_note}"

        # 3. Forward with pruned messages
        forward_body    = {**body, "model": routed_model, "messages": pruned_messages}
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers=forward_headers,
                json=forward_body,
            )

        resp_data  = resp.json()
        usage      = resp_data.get("usage", {})
        usage_source = "provider_reported" if usage else "estimated"
        in_tokens  = usage.get("input_tokens", tokens)
        out_tokens = usage.get("output_tokens", 100)
        tier_meta  = ANTHROPIC_TIERS.get(routed_model,
                         {"tier": "Advisor", "input": 3.0, "output": 15.0})
        cost       = round((in_tokens * tier_meta["input"] + out_tokens * tier_meta["output"]) / 1_000_000, 8)

        # 4. Log the completed request
        _log_transaction(db, workspace_id, department, routed_model,
                         tier_meta["tier"], in_tokens, out_tokens, cost,
                         routing_reason, tokens_saved,
                         matched_kw + st["matched"], prompt_text, risk_override=risk,
                         agent_id=agent.id if agent else None, usage_source=usage_source)

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
        if agent:
            _schedule_agent_idle(agent.id)
        db.close()
