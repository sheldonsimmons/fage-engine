"""
core/router.py — Intelligent Token Router & Model Cascader  [Step 3 + Step 8]

Pipeline:
  1. Optionally prune the incoming text (calls core/pruner.py)
  2. Score complexity — keyword match OR token count above threshold
  3. Check budget throttle flag (passed in from the API layer)
  4. Select model tier: micro (ROUTINE) or flagship (COMPLEX)
  5. Call the model via model_client (live or simulated based on .env)
  6. Calculate real costs and return full routing report
"""

from config import (
    MICRO_MODEL,
    FLAGSHIP_MODEL,
    COMPLEXITY_TOKEN_THRESHOLD,
    COMPLEXITY_KEYWORDS,
)
from core.pruner import prune, estimate_tokens
from core.model_client import call_model, get_mode_info


# ─────────────────────────────────────────────────────────────────────────────
# Complexity Scorer
# ─────────────────────────────────────────────────────────────────────────────

def score_complexity(text: str) -> dict:
    """
    Classify a text payload as ROUTINE or COMPLEX.

    Rules applied in priority order:
      1. Any COMPLEXITY_KEYWORD present  → COMPLEX (keyword trigger)
      2. Token count > threshold         → COMPLEX (length trigger)
      3. Otherwise                       → ROUTINE
    """
    text_lower  = text.lower()
    token_count = estimate_tokens(text)
    matched     = [kw for kw in COMPLEXITY_KEYWORDS if kw in text_lower]

    if matched:
        return {
            "complexity":       "COMPLEX",
            "reason":           f"High-risk keyword detected: '{matched[0]}'",
            "token_count":      token_count,
            "matched_keywords": matched,
        }

    if token_count > COMPLEXITY_TOKEN_THRESHOLD:
        return {
            "complexity":       "COMPLEX",
            "reason":           f"Payload length ({token_count} tokens) exceeds routing threshold ({COMPLEXITY_TOKEN_THRESHOLD})",
            "token_count":      token_count,
            "matched_keywords": [],
        }

    return {
        "complexity":       "ROUTINE",
        "reason":           f"Payload length {token_count} tokens — within threshold, no high-risk keywords",
        "token_count":      token_count,
        "matched_keywords": [],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Cost calculator
# ─────────────────────────────────────────────────────────────────────────────

def _calculate_cost(input_tokens: int, output_tokens: int, tier: str) -> float:
    """
    Calculate real cost from token counts using pricing config.
    Works for both live (real token counts from API) and simulated calls.
    """
    if tier == "micro":
        cost_in  = MICRO_MODEL["input_cost_per_million"]  / 1_000_000
        cost_out = MICRO_MODEL["output_cost_per_million"] / 1_000_000
    else:
        cost_in  = FLAGSHIP_MODEL["input_cost_per_million"]  / 1_000_000
        cost_out = FLAGSHIP_MODEL["output_cost_per_million"] / 1_000_000
    return round((input_tokens * cost_in) + (output_tokens * cost_out), 6)


# ─────────────────────────────────────────────────────────────────────────────
# Main Public Function
# ─────────────────────────────────────────────────────────────────────────────

def route(
    text:         str,
    department:   str,
    auto_prune:   bool = True,
    is_throttled: bool = False,
) -> dict:
    """
    Full routing pipeline for one payload.

    Returns a complete routing report with:
      - complexity classification and reason
      - model tier selected and why
      - real token counts and cost (from API when live)
      - model response
      - cost comparison: with pruning vs. without pruning
      - current mode (live/simulated) and provider
    """
    # Step 1 — Prune
    prune_result = None
    working_text = text

    if auto_prune:
        prune_result = prune(text)
        working_text = prune_result["cleaned_text"]

    # Step 2 — Score complexity
    complexity_result = score_complexity(working_text)
    complexity        = complexity_result["complexity"]

    # Step 3 — Apply throttle override
    if is_throttled and complexity == "COMPLEX":
        model_tier       = "micro"
        routing_decision = "THROTTLED"
        routing_reason   = (
            f"Department '{department}' has reached its monthly budget cap. "
            f"Forced down to micro-model tier. Original complexity: COMPLEX."
        )
    elif complexity == "COMPLEX":
        model_tier       = "flagship"
        routing_decision = "COMPLEX"
        routing_reason   = complexity_result["reason"]
    else:
        model_tier       = "micro"
        routing_decision = "ROUTINE"
        routing_reason   = complexity_result["reason"]

    # Step 4 — Call the model (live or simulated)
    model_result = call_model(working_text, model_tier)

    # Step 5 — Calculate cost from actual token counts
    cost_usd = _calculate_cost(
        model_result["input_tokens"],
        model_result["output_tokens"],
        model_tier,
    )

    # Step 6 — Calculate pruning savings
    tokens_saved_by_pruning = prune_result["tokens_saved"] if prune_result else 0
    if tokens_saved_by_pruning > 0:
        cost_per_token = (
            MICRO_MODEL["input_cost_per_million"] if model_tier == "micro"
            else FLAGSHIP_MODEL["input_cost_per_million"]
        ) / 1_000_000
        pruning_cost_saved = round(tokens_saved_by_pruning * cost_per_token, 6)
    else:
        pruning_cost_saved = 0.0

    # Model display name
    mode_info  = get_mode_info()
    model_name = model_result["model_id"]
    if model_tier == "micro" and mode_info["mode"] == "simulated":
        model_name = MICRO_MODEL["display_name"]
    elif model_tier == "flagship" and mode_info["mode"] == "simulated":
        model_name = FLAGSHIP_MODEL["display_name"]

    return {
        "department":               department,
        "complexity":               complexity,
        "routing_decision":         routing_decision,
        "routing_reason":           routing_reason,
        "matched_keywords":         complexity_result["matched_keywords"],
        "model_tier":               model_tier,
        "model_name":               model_name,
        "input_tokens":             model_result["input_tokens"],
        "output_tokens":            model_result["output_tokens"],
        "cost_usd":                 cost_usd,
        "simulated_response":       model_result["response_text"],
        "was_pruned":               auto_prune,
        "tokens_saved_by_pruning":  tokens_saved_by_pruning,
        "pruning_cost_saved_usd":   pruning_cost_saved,
        "total_cost_without_pruning": round(cost_usd + pruning_cost_saved, 6),
        "provider":                 model_result["provider"],
        "model_mode":               mode_info["mode"],
    }
