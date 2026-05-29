"""
routes_savings.py — CostPilot Savings Calculator proxy

POST /api/savings/analyze
  Accepts provider + API key, proxies to Anthropic or OpenAI usage endpoints
  server-side (avoids browser CORS), parses response, returns usage summary.
  The key is NEVER logged or persisted — used once and discarded.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
import httpx
from datetime import datetime, timedelta

router = APIRouter()

# ── Real Anthropic pricing (per token) ────────────────────────────────────────
PRICING = {
    "haiku":   {"in": 0.25  / 1_000_000, "out": 1.25  / 1_000_000},
    "sonnet":  {"in": 3.00  / 1_000_000, "out": 15.00 / 1_000_000},
    "opus":    {"in": 15.00 / 1_000_000, "out": 75.00 / 1_000_000},
    # OpenAI
    "gpt-4o-mini": {"in": 0.15  / 1_000_000, "out": 0.60  / 1_000_000},
    "gpt-4o":      {"in": 2.50  / 1_000_000, "out": 10.00 / 1_000_000},
    "o1":          {"in": 15.00 / 1_000_000, "out": 60.00 / 1_000_000},
}

def model_tier(model_id: str) -> str:
    m = model_id.lower()
    if "haiku"  in m or "mini"  in m or "3.5" in m: return "micro"
    if "opus"   in m or "o1"    in m:                return "flagship"
    return "mid"  # sonnet, gpt-4o

def model_cost_per_token(model_id: str):
    m = model_id.lower()
    if "haiku"  in m: return PRICING["haiku"]
    if "opus"   in m: return PRICING["opus"]
    if "mini"   in m: return PRICING["gpt-4o-mini"]
    if "o1"     in m: return PRICING["o1"]
    if "gpt-4o" in m: return PRICING["gpt-4o"]
    return PRICING["sonnet"]  # default


# ── Request / Response models ─────────────────────────────────────────────────

class AnalyzeRequest(BaseModel):
    provider: str          # "anthropic" | "openai"
    api_key: str
    days: int = 30

class UsageSummary(BaseModel):
    provider: str
    days: int
    total_cost_usd: float
    total_input_tokens: int
    total_output_tokens: int
    total_calls: int
    micro_pct: float        # fraction of tokens on cheap tiers
    flagship_pct: float     # fraction on premium tiers
    avg_payload_tokens: float
    model_breakdown: list   # [{ model, calls, input_tokens, output_tokens, cost_usd }]
    source: str             # "api"


# ── Anthropic usage proxy ─────────────────────────────────────────────────────

async def fetch_anthropic(api_key: str, days: int) -> UsageSummary:
    end   = datetime.utcnow()
    start = end - timedelta(days=days)

    url = (
        f"https://api.anthropic.com/v1/usage"
        f"?start_time={start.strftime('%Y-%m-%dT00:00:00Z')}"
        f"&end_time={end.strftime('%Y-%m-%dT23:59:59Z')}"
        f"&limit=1000"
    )

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(url, headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        })

    if resp.status_code == 401:
        raise ValueError("Invalid Anthropic API key.")
    if resp.status_code == 403:
        raise ValueError("This API key doesn't have usage read permissions. Use CSV export instead.")
    if resp.status_code == 404:
        raise ValueError(
            "Anthropic's usage API requires an Organization Admin key — standard API keys don't have access. "
            "Export a CSV from console.anthropic.com → Usage → Export and use the CSV path instead."
        )
    if not resp.is_success:
        raise ValueError(f"Anthropic API returned {resp.status_code}. Try the CSV path instead.")

    data = resp.json()
    rows = data.get("data") or data.get("usage") or []

    total_in = total_out = total_cost = 0
    micro_tok = flagship_tok = 0
    model_map: dict = {}

    for row in rows:
        inp  = int(row.get("input_tokens")  or 0)
        out  = int(row.get("output_tokens") or 0)
        mid  = (row.get("model") or "sonnet").lower()
        p    = model_cost_per_token(mid)
        cost = inp * p["in"] + out * p["out"]

        total_in   += inp
        total_out  += out
        total_cost += cost

        tier = model_tier(mid)
        if tier == "micro":    micro_tok    += inp + out
        elif tier == "flagship": flagship_tok += inp + out

        if mid not in model_map:
            model_map[mid] = {"model": mid, "calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
        model_map[mid]["calls"]         += 1
        model_map[mid]["input_tokens"]  += inp
        model_map[mid]["output_tokens"] += out
        model_map[mid]["cost_usd"]      += cost

    total_tok  = max(1, total_in + total_out)
    total_calls = max(1, round(total_tok / 1000))  # estimate: ~1000 tokens per call

    return UsageSummary(
        provider="Anthropic",
        days=days,
        total_cost_usd=round(total_cost, 6),
        total_input_tokens=total_in,
        total_output_tokens=total_out,
        total_calls=total_calls,
        micro_pct=micro_tok / total_tok,
        flagship_pct=flagship_tok / total_tok,
        avg_payload_tokens=total_in / total_calls,
        model_breakdown=sorted(model_map.values(), key=lambda x: -x["cost_usd"]),
        source="api",
    )


# ── OpenAI usage proxy ────────────────────────────────────────────────────────

async def fetch_openai(api_key: str, days: int) -> UsageSummary:
    import asyncio
    headers = {"Authorization": f"Bearer {api_key}"}
    end_dt   = datetime.utcnow()
    start_dt = end_dt - timedelta(days=days)
    dates    = [(end_dt - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(days)]

    async with httpx.AsyncClient(timeout=20.0) as client:
        # Validate key + fetch today's legacy usage
        probe = await client.get(
            f"https://api.openai.com/v1/usage?date={dates[0]}", headers=headers,
        )
        if probe.status_code == 401:
            raise ValueError("Invalid OpenAI API key.")
        if not probe.is_success:
            raise ValueError(f"OpenAI API returned {probe.status_code}. Try the CSV path instead.")

        # Billing endpoint — captures ALL usage types incl. Responses API (returns cents)
        billing_resp = await client.get(
            f"https://api.openai.com/dashboard/billing/usage"
            f"?start_date={start_dt.strftime('%Y-%m-%d')}&end_date={end_dt.strftime('%Y-%m-%d')}",
            headers=headers,
        )
        import logging
        logging.warning(f"[savings] billing status={billing_resp.status_code} body={billing_resp.text[:300]}")
        logging.warning(f"[savings] probe status={probe.status_code} data_len={len(probe.json().get('data') or [])}")

        # Fetch remaining days concurrently for token breakdown
        sem = asyncio.Semaphore(10)
        async def one_day(date_str: str):
            async with sem:
                r = await client.get(
                    f"https://api.openai.com/v1/usage?date={date_str}", headers=headers,
                )
                return r.json().get("data") or [] if r.is_success else []
        rest = await asyncio.gather(*[one_day(d) for d in dates[1:]])

    # ── Cost: prefer billing API (covers Responses API), fall back to token math ──
    billing_cost = 0.0
    if billing_resp.is_success:
        billing_cost = (billing_resp.json().get("total_usage") or 0) / 100  # cents → dollars

    # ── Tokens: from legacy completions endpoint ──────────────────────────────────
    all_rows = (probe.json().get("data") or []) + [row for day in rest for row in day]

    total_in = total_out = total_cost_tokens = 0
    micro_tok = flagship_tok = 0
    model_map: dict = {}

    for r in all_rows:
        inp   = int(r.get("n_context_tokens_total")   or r.get("prompt_tokens")    or 0)
        out   = int(r.get("n_generated_tokens_total") or r.get("completion_tokens") or 0)
        mid   = (r.get("snapshot_id") or r.get("model_id") or "gpt-4o").lower()
        p     = model_cost_per_token(mid)
        cost  = inp * p["in"] + out * p["out"]

        total_in          += inp
        total_out         += out
        total_cost_tokens += cost

        tier = model_tier(mid)
        if tier == "micro":      micro_tok    += inp + out
        elif tier == "flagship": flagship_tok += inp + out

        if mid not in model_map:
            model_map[mid] = {"model": mid, "calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
        model_map[mid]["calls"]         += r.get("n_requests") or 1
        model_map[mid]["input_tokens"]  += inp
        model_map[mid]["output_tokens"] += out
        model_map[mid]["cost_usd"]      += cost

    # Use billing cost when available (more accurate); else fall back to token math
    total_cost = billing_cost if billing_cost > 0 else total_cost_tokens

    # If billing cost > token cost, the gap is Responses API / other usage.
    # Estimate extra tokens at gpt-4o pricing to keep token stats meaningful.
    extra_cost = total_cost - total_cost_tokens
    if extra_cost > 0.001:
        p4o = PRICING["gpt-4o"]
        avg_tok_cost = (p4o["in"] * 0.75 + p4o["out"] * 0.25)
        extra_tok = int(extra_cost / avg_tok_cost)
        extra_in  = int(extra_tok * 0.75)
        extra_out = extra_tok - extra_in
        total_in  += extra_in
        total_out += extra_out
        flagship_tok += extra_tok
        if "gpt-4o" not in model_map:
            model_map["gpt-4o"] = {"model": "gpt-4o", "calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
        model_map["gpt-4o"]["input_tokens"]  += extra_in
        model_map["gpt-4o"]["output_tokens"] += extra_out
        model_map["gpt-4o"]["cost_usd"]      += extra_cost
        model_map["gpt-4o"]["calls"]         += max(1, round(extra_tok / 800))

    total_tok   = max(1, total_in + total_out)
    total_calls = sum(m["calls"] for m in model_map.values()) or max(1, round(total_tok / 1000))

    return UsageSummary(
        provider="OpenAI",
        days=days,
        total_cost_usd=round(total_cost, 6),
        total_input_tokens=total_in,
        total_output_tokens=total_out,
        total_calls=total_calls,
        micro_pct=micro_tok / total_tok,
        flagship_pct=flagship_tok / total_tok,
        avg_payload_tokens=total_in / total_calls,
        model_breakdown=sorted(model_map.values(), key=lambda x: -x["cost_usd"]),
        source="api",
    )


# ── Route ─────────────────────────────────────────────────────────────────────

@router.post("/analyze", response_model=UsageSummary)
async def analyze_usage(req: AnalyzeRequest):
    """
    Proxy usage data fetch to avoid browser CORS restrictions.
    API key is used once and never stored or logged.
    """
    try:
        if req.provider == "anthropic":
            return await fetch_anthropic(req.api_key, req.days)
        elif req.provider == "openai":
            return await fetch_openai(req.api_key, req.days)
        else:
            raise ValueError(f"Unknown provider: {req.provider}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
