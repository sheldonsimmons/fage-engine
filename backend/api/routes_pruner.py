"""
api/routes_pruner.py — Context-Pruning Sweeper API routes  [Step 2]

POST /api/prune
  Accepts a raw text payload, runs it through the pruning pipeline,
  and returns the cleaned text plus compression analytics.
"""

from typing import List, Literal, Optional
from fastapi import APIRouter
from pydantic import BaseModel
from core.pruner import detect_payload_type, estimate_tokens, prune
from config import MICRO_MODEL, FLAGSHIP_MODEL

router = APIRouter()

# Cost per single token (convert from per-million pricing)
MICRO_COST_PER_TOKEN    = MICRO_MODEL["input_cost_per_million"]    / 1_000_000
FLAGSHIP_COST_PER_TOKEN = FLAGSHIP_MODEL["input_cost_per_million"] / 1_000_000


class PruneRequest(BaseModel):
    text: str                        # Raw input payload
    department: str = "Support"      # Which department triggered this (for logging)


class PruneResponse(BaseModel):
    cleaned_text:          str
    raw_tokens:            int
    clean_tokens:          int
    tokens_saved:          int
    compression_pct:       float
    filters_applied:       List[str]
    department:            str
    micro_cost_saved_usd:  float     # $ avoided on micro-model tier
    flagship_cost_saved_usd: float   # $ avoided on flagship-model tier


class PrunePreviewRequest(BaseModel):
    text: str
    payload_type: Literal["auto", "text", "code", "transcript"] = "auto"
    pruning_enabled: bool = True


class PrunePreviewResponse(BaseModel):
    cleaned_text: str
    raw_tokens: int
    clean_tokens: int
    tokens_saved: int
    compression_pct: float
    filters_applied: List[str]
    detected_payload_type: str
    pruning_applied: bool
    decision: str
    reason: str
    micro_cost_saved_usd: float
    flagship_cost_saved_usd: float


@router.post("", response_model=PruneResponse)
def prune_payload(req: PruneRequest):
    """
    Run the Context-Pruning Sweeper on a raw text payload.

    Strips HTML, email headers, reply chains, legal disclaimers, and signatures.
    Returns the cleaned text, token savings, and estimated cost savings per model tier.
    """
    result = prune(req.text)
    tokens_saved = result["tokens_saved"]

    return PruneResponse(
        cleaned_text=result["cleaned_text"],
        raw_tokens=result["raw_tokens"],
        clean_tokens=result["clean_tokens"],
        tokens_saved=tokens_saved,
        compression_pct=result["compression_pct"],
        filters_applied=result["filters_applied"],
        department=req.department,
        micro_cost_saved_usd=round(tokens_saved * MICRO_COST_PER_TOKEN, 6),
        flagship_cost_saved_usd=round(tokens_saved * FLAGSHIP_COST_PER_TOKEN, 6),
    )


@router.get("/policy")
def pruning_policy():
    """Describe the existing runtime pruning contract without changing it."""
    return {
        "algorithm_read_only": True,
        "request_default_enabled": True,
        "minimum_useful_tokens": 3,
        "supported_payload_types": ["auto", "text", "code", "transcript"],
        "filters": [
            "HTML and hidden markup",
            "Email and MIME headers",
            "Reply-chain history",
            "Legal disclaimers",
            "Signatures and footer blocks",
            "Repeated boilerplate",
            "Excess whitespace",
        ],
        "lanes": [
            {
                "type": "text",
                "behavior": "prune",
                "description": "Standard text uses the existing context-pruning pipeline.",
            },
            {
                "type": "code",
                "behavior": "bypass",
                "description": "Explicit or automatically detected code bypasses pruning to preserve structure.",
            },
            {
                "type": "transcript",
                "behavior": "prune",
                "description": "Transcripts use pruning before separate Voice Guard processing.",
            },
        ],
    }


@router.post("/preview", response_model=PrunePreviewResponse)
def preview_pruning(req: PrunePreviewRequest):
    """Preview the current pruning decision without routing, spend, or database writes."""
    detected_type, detected_reason = detect_payload_type(req.text)
    effective_type = detected_type if req.payload_type == "auto" else req.payload_type
    bypass_reason: Optional[str] = None
    if not req.pruning_enabled:
        bypass_reason = "Pruning is disabled for this preview, matching an agent-level override."
    elif effective_type == "code":
        bypass_reason = (
            detected_reason
            if req.payload_type == "auto"
            else "Payload is explicitly marked as code, so pruning is bypassed to preserve structure."
        )

    if bypass_reason:
        tokens = estimate_tokens(req.text)
        result = {
            "cleaned_text": req.text,
            "raw_tokens": tokens,
            "clean_tokens": tokens,
            "tokens_saved": 0,
            "compression_pct": 0.0,
            "filters_applied": [],
        }
        decision = "bypassed"
        reason = bypass_reason
        pruning_applied = False
    else:
        result = prune(req.text)
        decision = "applied"
        reason = (
            "Transcript lane selected; the existing pruning pipeline runs before Voice Guard."
            if effective_type == "transcript"
            else "Text lane selected; the existing pruning pipeline is applied."
        )
        pruning_applied = True

    tokens_saved = result["tokens_saved"]
    return PrunePreviewResponse(
        **result,
        detected_payload_type=effective_type,
        pruning_applied=pruning_applied,
        decision=decision,
        reason=reason,
        micro_cost_saved_usd=round(tokens_saved * MICRO_COST_PER_TOKEN, 6),
        flagship_cost_saved_usd=round(tokens_saved * FLAGSHIP_COST_PER_TOKEN, 6),
    )
