"""
api/routes_pruner.py — Context-Pruning Sweeper API routes  [Step 2]

POST /api/prune
  Accepts a raw text payload, runs it through the pruning pipeline,
  and returns the cleaned text plus compression analytics.
"""

from typing import List
from fastapi import APIRouter
from pydantic import BaseModel
from core.pruner import prune
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
