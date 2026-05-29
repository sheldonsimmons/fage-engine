"""
api/routes_enrich.py — Platform Context Enrichment

POST /api/enrich/salesforce
  Takes a Salesforce Case ID + access token, fetches the full case context
  (description, comments, emails) directly from the Salesforce REST API in
  parallel, assembles it, then runs the full CostPilot routing pipeline.

  The Apex class only needs to send the Case ID and session token — CostPilot
  handles all data retrieval and routing decisions.
"""

import asyncio
import httpx
from datetime import datetime
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database.db import get_db
from database.models import DepartmentBudget, TokenTransaction, RegisteredAgent
from core.pruner import prune, estimate_tokens
from core.router import route
from core.auditor import write_audit_event
from core.keywords import check_terms

router = APIRouter()

SF_API_VERSION = "v59.0"


# ── Request / Response models ──────────────────────────────────────────────────

class SalesforceEnrichRequest(BaseModel):
    record_id:        str                  # Salesforce Case ID (15 or 18 char)
    sf_instance_url:  str                  # e.g. https://acme.my.salesforce.com
    sf_access_token:  str                  # UserInfo.getSessionId() from Apex
    department:       str  = "Support"
    agent_name:       Optional[str] = None
    source_platform:  str  = "Salesforce"
    max_comments:     int  = 5             # Most recent N case comments to include
    max_emails:       int  = 3             # Most recent N email messages to include
    min_tokens:       int  = 20


class EnrichResponse(BaseModel):
    # Routing fields (same as RouteResponse)
    department:                 str
    complexity:                 str
    routing_decision:           str
    routing_reason:             str
    matched_keywords:           List[str]
    model_tier:                 str
    model_name:                 str
    input_tokens:               int
    output_tokens:              int
    cost_usd:                   float
    simulated_response:         str
    was_pruned:                 bool
    tokens_saved_by_pruning:    int
    pruning_cost_saved_usd:     float
    total_cost_without_pruning: float
    budget_used_pct:            float
    budget_remaining_usd:       float
    was_throttled:              bool
    sensitive_term_triggered:   bool = False
    sensitive_term_action:      Optional[str] = None
    sensitive_term_matches:     List[str] = []
    # Enrichment metadata
    enrichment_sources:         List[str] = []   # which fields were fetched
    raw_context_tokens:         int = 0          # tokens before pruning
    clean_context_tokens:       int = 0          # tokens after pruning


# ── Salesforce fetchers ────────────────────────────────────────────────────────

async def _sf_get(client: httpx.AsyncClient, instance_url: str, token: str, path: str) -> dict:
    """Make one authenticated Salesforce REST API call."""
    url = f"{instance_url.rstrip('/')}/services/data/{SF_API_VERSION}/{path}"
    resp = await client.get(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        timeout=10.0,
    )
    if resp.status_code != 200:
        return {}
    return resp.json()


async def _fetch_sf_context(
    instance_url: str,
    token: str,
    record_id: str,
    max_comments: int,
    max_emails: int,
) -> tuple[dict, list, list]:
    """
    Fetch Case, CaseComments, and EmailMessages in parallel.
    Returns (case_data, comments, emails).
    """
    async with httpx.AsyncClient() as client:
        case_task = _sf_get(
            client, instance_url, token,
            f"sobjects/Case/{record_id}?fields=Subject,Description,Status,Priority,Origin"
        )
        comments_task = _sf_get(
            client, instance_url, token,
            f"query?q=SELECT+CommentBody,CreatedDate,IsPublished+FROM+CaseComment+"
            f"WHERE+ParentId='{record_id}'+ORDER+BY+CreatedDate+DESC+LIMIT+{max_comments}"
        )
        emails_task = _sf_get(
            client, instance_url, token,
            f"query?q=SELECT+TextBody,Subject,FromAddress,CreatedDate+FROM+EmailMessage+"
            f"WHERE+ParentId='{record_id}'+ORDER+BY+CreatedDate+DESC+LIMIT+{max_emails}"
        )

        case_data, comments_resp, emails_resp = await asyncio.gather(
            case_task, comments_task, emails_task,
            return_exceptions=True,
        )

    case_data = case_data if isinstance(case_data, dict) else {}
    comments  = (comments_resp.get("records", []) if isinstance(comments_resp, dict) else [])
    emails    = (emails_resp.get("records", [])   if isinstance(emails_resp,   dict) else [])

    return case_data, comments, emails


def _assemble_context(case: dict, comments: list, emails: list) -> tuple[str, list]:
    """
    Build a clean, structured text payload from all fetched Salesforce data.
    Returns (assembled_text, sources_used).
    """
    parts   = []
    sources = []

    if case.get("Subject"):
        parts.append(f"SUBJECT: {case['Subject']}")
        sources.append("subject")

    if case.get("Priority"):
        parts.append(f"PRIORITY: {case['Priority']}")

    if case.get("Status"):
        parts.append(f"STATUS: {case['Status']}")

    if case.get("Description"):
        parts.append(f"\nDESCRIPTION:\n{case['Description']}")
        sources.append("description")

    if comments:
        parts.append("\nCASE COMMENTS (most recent first):")
        for c in comments:
            body = (c.get("CommentBody") or "").strip()
            if body:
                parts.append(f"- {body}")
        sources.append(f"comments ({len(comments)})")

    if emails:
        parts.append("\nEMAIL THREAD (most recent first):")
        for e in emails:
            body = (e.get("TextBody") or "").strip()
            subj = e.get("Subject", "")
            frm  = e.get("FromAddress", "")
            if body:
                header = f"From: {frm}" if frm else ""
                if subj:
                    header += f" | Subject: {subj}"
                if header:
                    parts.append(header)
                parts.append(body[:1000])  # cap each email body at 1000 chars
        sources.append(f"emails ({len(emails)})")

    return "\n".join(parts), sources


# ── Main endpoint ──────────────────────────────────────────────────────────────

@router.post("/salesforce", response_model=EnrichResponse)
async def enrich_and_route_salesforce(
    req: SalesforceEnrichRequest,
    db: Session = Depends(get_db),
):
    """
    Fetch full Salesforce case context and route through CostPilot in one call.

    Steps:
      1. Fetch Case + CaseComments + EmailMessages in parallel from Salesforce API
      2. Assemble into structured text
      3. Prune noise (headers, signatures, reply chains, disclaimers)
      4. Check sensitive terms — block or escalate as needed
      5. Route to appropriate model tier
      6. Write transaction + audit event
      7. Return routing result + enrichment metadata
    """
    # ── Step 1+2: Fetch and assemble ──────────────────────────────────────────
    try:
        case_data, comments, emails = await _fetch_sf_context(
            req.sf_instance_url,
            req.sf_access_token,
            req.record_id,
            req.max_comments,
            req.max_emails,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Salesforce API error: {str(e)}")

    if not case_data:
        raise HTTPException(status_code=404, detail=f"Case {req.record_id} not found or access denied.")

    raw_text, sources = _assemble_context(case_data, comments, emails)

    if not raw_text.strip():
        raise HTTPException(status_code=422, detail="Case has no readable content.")

    # ── Step 3: Prune ─────────────────────────────────────────────────────────
    pruned         = prune(raw_text)
    clean_text     = pruned["cleaned_text"]
    raw_tokens     = pruned["raw_tokens"]
    clean_tokens   = pruned["clean_tokens"]

    if estimate_tokens(clean_text) < req.min_tokens:
        return EnrichResponse(
            department="", complexity="SKIPPED", routing_decision="SKIPPED",
            routing_reason=f"Case {req.record_id} has no meaningful content after pruning ({clean_tokens} tokens). Likely created before description was filled in.",
            matched_keywords=[], model_tier="none", model_name="none",
            input_tokens=0, output_tokens=0, cost_usd=0.0, simulated_response="",
            was_pruned=True, tokens_saved_by_pruning=pruned["tokens_saved"],
            pruning_cost_saved_usd=0.0, total_cost_without_pruning=0.0,
            budget_used_pct=0.0, budget_remaining_usd=0.0, was_throttled=False,
            enrichment_sources=sources, raw_context_tokens=raw_tokens, clean_context_tokens=clean_tokens,
        )

    # ── Step 4: Sensitive term check ──────────────────────────────────────────
    term_result  = check_terms(db, clean_text, req.department)
    budget        = db.query(DepartmentBudget).filter_by(department=req.department).first()
    is_throttled  = budget.throttled if budget else False
    throttle_tier = getattr(budget, "throttle_tier", 1) or 1 if budget else 1

    if term_result["triggered"] and term_result["action"] == "block":
        write_audit_event(
            db               = db,
            event_type       = "DECISION",
            department       = req.department,
            routing_decision = "BLOCKED",
            routing_reason   = f"Sensitive term blocked: '{term_result['top_match']['term']}' ({term_result['top_match']['category']})",
            prompt_payload   = clean_text[:2000],
            model_tier       = "none",
            agent_id         = None,
            matched_keywords = [m["term"] for m in term_result["matches"]],
            cost_usd         = 0.0,
            decision_outcome = "Request blocked by sensitive term policy",
        )
        raise HTTPException(
            status_code=451,
            detail={
                "error":    "BLOCKED",
                "reason":   f"Case contains a blocked sensitive term: '{term_result['top_match']['term']}'",
                "category": term_result["top_match"]["category"],
                "matches":  [m["term"] for m in term_result["matches"]],
                "case_id":  req.record_id,
            }
        )

    force_complex = term_result["triggered"] and term_result["action"] == "escalate"

    # ── Resolve / register agent ───────────────────────────────────────────────
    agent = None
    if req.agent_name:
        agent = db.query(RegisteredAgent).filter_by(name=req.agent_name).first()
        if not agent:
            from core.agentlake import infer_platform
            agent = RegisteredAgent(
                name             = req.agent_name,
                department       = req.department,
                source_platform  = req.source_platform,
                permissions      = "read,write",
                target_table     = "cases",
                collision_policy = "lock",
                status           = "idle",
            )
            db.add(agent)
            db.commit()
            db.refresh(agent)

    if agent:
        agent.status       = "active"
        agent.last_used_at = datetime.utcnow()
        db.commit()

    # ── Step 5: Route ─────────────────────────────────────────────────────────
    # Pass clean_text with auto_prune=False — we already pruned above
    result = route(
        clean_text, req.department,
        db=db,
        auto_prune=False,
        is_throttled=is_throttled,
        throttle_tier=throttle_tier,
        force_complex=force_complex,
    )

    # ── Step 6: Persist transaction ───────────────────────────────────────────
    tx = TokenTransaction(
        department      = req.department,
        source_platform = req.source_platform,
        agent_id        = agent.id if agent else None,
        model_tier      = result["model_tier"],
        input_tokens    = result["input_tokens"],
        output_tokens   = result["output_tokens"],
        cost_usd        = result["cost_usd"],
        timestamp       = datetime.utcnow(),
        routing_reason  = result["routing_decision"],
        was_pruned      = True,
        tokens_saved    = pruned["tokens_saved"],
    )
    db.add(tx)

    if budget:
        budget.current_spend_usd = round(budget.current_spend_usd + result["cost_usd"], 6)
        if budget.current_spend_usd >= budget.monthly_cap_usd and not budget.override_granted:
            budget.throttled = True

    if agent:
        import threading
        db.commit()
        def _reset_agent_status(agent_id):
            import time
            time.sleep(4)
            from database.db import SessionLocal
            from database.models import RegisteredAgent
            _db = SessionLocal()
            try:
                _a = _db.query(RegisteredAgent).filter_by(id=agent_id).first()
                if _a and _a.status == "active":
                    _a.status = "idle"
                    _db.commit()
            finally:
                _db.close()
        threading.Thread(target=_reset_agent_status, args=(agent.id,), daemon=True).start()
    else:
        db.commit()

    # ── Audit event ───────────────────────────────────────────────────────────
    all_matched = result["matched_keywords"] + [m["term"] for m in term_result.get("matches", [])]
    if result["routing_decision"] in ("COMPLEX", "THROTTLED") or term_result["triggered"]:
        try:
            write_audit_event(
                db               = db,
                event_type       = "ROUTING",
                department       = req.department,
                routing_decision = result["routing_decision"],
                routing_reason   = (
                    f"[SENSITIVE TERM: {term_result['top_match']['term']} → {term_result['action']}] "
                    + result["routing_reason"]
                ) if term_result["triggered"] else result["routing_reason"],
                prompt_payload   = clean_text[:2000],
                model_tier       = result["model_tier"],
                agent_id         = agent.id if agent else None,
                matched_keywords = all_matched,
                cost_usd         = result["cost_usd"],
                decision_outcome = f"{result['model_tier']} model used — ${result['cost_usd']:.6f} | Case {req.record_id} | Sources: {', '.join(sources)}",
            )
        except Exception:
            pass

    # ── Budget stats ──────────────────────────────────────────────────────────
    if budget and budget.monthly_cap_usd > 0:
        budget_used_pct      = round((budget.current_spend_usd / budget.monthly_cap_usd) * 100, 1)
        budget_remaining_usd = round(budget.monthly_cap_usd - budget.current_spend_usd, 4)
    else:
        budget_used_pct      = 0.0
        budget_remaining_usd = 0.0

    return EnrichResponse(
        **result,
        budget_used_pct           = budget_used_pct,
        budget_remaining_usd      = budget_remaining_usd,
        was_throttled             = is_throttled,
        sensitive_term_triggered  = term_result["triggered"],
        sensitive_term_action     = term_result.get("action"),
        sensitive_term_matches    = [m["term"] for m in term_result.get("matches", [])],
        enrichment_sources        = sources,
        raw_context_tokens        = raw_tokens,
        clean_context_tokens      = clean_tokens,
    )
