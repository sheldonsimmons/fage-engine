"""
core/router.py — Intelligent Token Router & Model Cascader  [Step 3 + Step 8 + Step 13]

Pipeline:
  1. Optionally prune the incoming text (calls core/pruner.py)
  2. Score complexity — keyword match OR token count above threshold
  3. Map complexity to a tier (1=Scout, 2=Analyst, 3=Advisor, 4=Strategist)
  4. Look up the default enabled model for that tier from the ModelRegistry DB table
  5. Apply budget throttle — if throttled, step down to Tier 1
  6. Call the model via model_client (live or simulated)
  7. Calculate cost using registry rates (or hardcoded fallback)
  8. Return full routing report
"""

from config import (
    MICRO_MODEL,
    FLAGSHIP_MODEL,
    COMPLEXITY_TOKEN_THRESHOLD as _DEFAULT_THRESHOLD,
    COMPLEXITY_KEYWORDS as _DEFAULT_KEYWORDS,
)
from core.pruner import prune, estimate_tokens
from core.model_client import call_model, get_mode_info

TIER_NAMES = {1: "Scout", 2: "Analyst", 3: "Advisor", 4: "Strategist"}


# ─────────────────────────────────────────────────────────────────────────────
# Complexity Scorer
# ─────────────────────────────────────────────────────────────────────────────

def score_complexity(text: str, threshold: int = None, keywords: list = None) -> dict:
    """
    Classify a text payload as ROUTINE, MODERATE, or COMPLEX.

    Rules applied in priority order:
      1. Keywords AND tokens > threshold  → COMPLEX  (Advisor, Tier 3)
      2. Keywords OR  tokens > threshold  → MODERATE (Analyst, Tier 2)
      3. Otherwise                        → ROUTINE  (Scout,   Tier 1)

    threshold and keywords are read from the DB at runtime (via route()).
    Falls back to config.py defaults if not provided.
    """
    _threshold  = threshold if threshold is not None else _DEFAULT_THRESHOLD
    _keywords   = keywords  if keywords  is not None else _DEFAULT_KEYWORDS
    text_lower  = text.lower()
    token_count = estimate_tokens(text)
    matched     = [kw for kw in _keywords if kw in text_lower]
    over_limit  = token_count > _threshold

    if matched and over_limit:
        return {
            "complexity":       "COMPLEX",
            "reason":           f"Payload length ({token_count} tokens) exceeds threshold ({_threshold}) and complexity keyword detected: '{matched[0]}' — escalated to Advisor",
            "token_count":      token_count,
            "matched_keywords": matched,
        }

    if matched:
        return {
            "complexity":       "MODERATE",
            "reason":           f"Complexity keyword detected: '{matched[0]}' — routed to Analyst (keyword match, under token threshold)",
            "token_count":      token_count,
            "matched_keywords": matched,
        }

    if over_limit:
        return {
            "complexity":       "MODERATE",
            "reason":           f"Payload length ({token_count} tokens) exceeds threshold ({_threshold}) — routed to Analyst (length trigger, no keywords)",
            "token_count":      token_count,
            "matched_keywords": [],
        }

    return {
        "complexity":       "ROUTINE",
        "reason":           f"Payload length {token_count} tokens — no complexity keywords detected, routed to Scout",
        "token_count":      token_count,
        "matched_keywords": [],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Model Registry lookup
# ─────────────────────────────────────────────────────────────────────────────

def _get_model_from_registry(tier_num: int, db, department: str = None) -> dict | None:
    """
    Look up the default enabled model for a given tier from ModelRegistry.

    Lookup priority:
      1. Department-specific default for this tier (is_default=True, department=requested)
      2. Department-specific any-enabled for this tier
      3. Global default for this tier (department IS NULL, is_default=True)
      4. Global any-enabled for this tier (department IS NULL)

    Cascades: Analyst (tier 2) falls UP to Advisor (tier 3) if nothing registered.
    All other tiers cascade down to the next cheaper tier.
    Returns None if no models registered at all.
    """
    if db is None or tier_num < 1:
        return None

    from database.models import ModelRegistry
    from sqlalchemy import or_

    model = None

    if department:
        # 1. Department-specific default
        model = db.query(ModelRegistry).filter(
            ModelRegistry.tier == tier_num,
            ModelRegistry.is_enabled == True,
            ModelRegistry.is_default == True,
            ModelRegistry.department == department,
        ).first()

        # 2. Department-specific any-enabled
        if not model:
            model = db.query(ModelRegistry).filter(
                ModelRegistry.tier == tier_num,
                ModelRegistry.is_enabled == True,
                ModelRegistry.department == department,
            ).first()

    # 3. Global default (department IS NULL)
    if not model:
        model = db.query(ModelRegistry).filter(
            ModelRegistry.tier == tier_num,
            ModelRegistry.is_enabled == True,
            ModelRegistry.is_default == True,
            ModelRegistry.department == None,
        ).first()

    # 4. Global any-enabled
    if not model:
        model = db.query(ModelRegistry).filter(
            ModelRegistry.tier == tier_num,
            ModelRegistry.is_enabled == True,
            ModelRegistry.department == None,
        ).first()

    # Cascade: Analyst (tier 2) falls UP to Advisor (tier 3) if no Analyst model registered.
    # All other tiers cascade down to the next cheaper tier.
    if not model and tier_num == 2:
        return _get_model_from_registry(3, db, department)
    if not model and tier_num > 1:
        return _get_model_from_registry(tier_num - 1, db, department)

    if not model:
        return None

    return {
        "model_id":              model.model_id,
        "display_name":          model.display_name,
        "tier":                  model.tier,
        "tier_name":             TIER_NAMES.get(model.tier, f"Tier {model.tier}"),
        "cost_input_per_million":  model.cost_input_per_1m,
        "cost_output_per_million": model.cost_output_per_1m,
        "department_scoped":     model.department is not None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Cost Calculator
# ─────────────────────────────────────────────────────────────────────────────

def _calculate_cost(input_tokens: int, output_tokens: int, cost_in_per_m: float, cost_out_per_m: float) -> float:
    cost_in  = cost_in_per_m  / 1_000_000
    cost_out = cost_out_per_m / 1_000_000
    return round((input_tokens * cost_in) + (output_tokens * cost_out), 6)


# ─────────────────────────────────────────────────────────────────────────────
# Main Public Function
# ─────────────────────────────────────────────────────────────────────────────

def route(
    text:          str,
    department:    str,
    db=None,
    auto_prune:    bool = True,
    is_throttled:  bool = False,
    throttle_tier: int  = 1,
    force_complex: bool = False,
) -> dict:
    """
    Full routing pipeline for one payload.

    Returns a complete routing report with:
      - complexity classification and reason
      - tier selected (Scout / Analyst / Advisor / Strategist)
      - model picked from the registry (or hardcoded fallback)
      - real token counts and cost
      - cost comparison: with pruning vs. without pruning
    """
    # Step 1 — Check for explicit tier prefix tags BEFORE pruning
    # Searches within first 100 chars to handle Salesforce field-label prefixes
    # e.g. "DESCRIPTION:\n[ANALYST] ..." — prefix must be a plain word like "DESCRIPTION:"
    forced_tier = None
    tag_map = {
        "[scout]":      1,
        "[analyst]":    2,
        "[advisor]":    3,
        "[strategist]": 4,
    }
    text_head = text.strip().lower()[:100]
    for tag, tier in tag_map.items():
        idx = text_head.find(tag)
        if idx != -1:
            prefix = text_head[:idx].strip()
            # Allow only simple field-label prefixes (e.g. "description:") — no full sentences
            if prefix == "" or (len(prefix) <= 20 and prefix.rstrip(":").replace(" ", "").isalpha()):
                forced_tier = tier
                text = text.strip()[text.strip().lower().find(tag) + len(tag):].strip()
                break

    # Step 2 — Prune
    prune_result = None
    working_text = text

    if auto_prune:
        prune_result = prune(text)
        working_text = prune_result["cleaned_text"]

    # Load live routing config from DB (falls back to config.py defaults if unavailable)
    _threshold = None
    _keywords  = None
    if db is not None:
        try:
            from core.routing_config import get_routing_config
            cfg        = get_routing_config(db)
            _threshold = cfg.complexity_token_threshold
            _keywords  = cfg.complexity_keywords
        except Exception:
            pass  # DB unavailable — use config.py defaults

    # Step 2b — Score complexity
    complexity_result = score_complexity(working_text, threshold=_threshold, keywords=_keywords)
    complexity        = complexity_result["complexity"]

    # Override: sensitive term escalation forces COMPLEX
    if force_complex and complexity != "COMPLEX":
        complexity                       = "COMPLEX"
        complexity_result["complexity"]  = "COMPLEX"
        complexity_result["reason"]      = "Forced to COMPLEX by sensitive term escalation policy"

    # Step 3 — Map to tier number
    if is_throttled:
        effective_tier   = max(1, min(4, throttle_tier))   # clamp 1–4
        tier_num         = effective_tier
        floor_name       = TIER_NAMES.get(effective_tier, f"Tier {effective_tier}")
        routing_decision = "THROTTLED"
        routing_reason   = (
            f"Department '{department}' has reached its monthly budget cap. "
            f"Requests capped at {floor_name} (Tier {effective_tier}) per department policy. "
            f"Original complexity: {complexity}."
        )
    elif forced_tier is not None:
        tier_num         = forced_tier
        tier_label       = TIER_NAMES.get(forced_tier, f"Tier {forced_tier}")
        routing_decision = "OVERRIDE"
        routing_reason   = f"Explicit tier tag used — routed directly to {tier_label} (Tier {forced_tier})"
    elif force_complex:
        tier_num         = 4          # Strategist for sensitive term escalations
        routing_decision = "COMPLEX"
        routing_reason   = complexity_result["reason"]
    elif complexity == "COMPLEX":
        tier_num         = 3          # Advisor — keywords AND over token threshold
        routing_decision = "COMPLEX"
        routing_reason   = complexity_result["reason"]
    elif complexity == "MODERATE":
        tier_num         = 2          # Analyst — keyword OR over threshold (one signal)
        routing_decision = "MODERATE"
        routing_reason   = complexity_result["reason"]
    else:
        tier_num         = 1          # Scout for routine
        routing_decision = "ROUTINE"
        routing_reason   = complexity_result["reason"]

    # Step 4 — Look up model from registry (department-specific first, then global)
    registry_model = _get_model_from_registry(tier_num, db, department=department)

    if registry_model:
        model_id_to_use  = registry_model["model_id"]
        # Always use the REQUESTED tier's name — not the cascaded model's tier
        model_tier_label = TIER_NAMES.get(tier_num, registry_model["tier_name"])
        display_name     = registry_model["display_name"]
        cost_in_per_m    = registry_model["cost_input_per_million"]
        cost_out_per_m   = registry_model["cost_output_per_million"]
        fallback_tier    = "micro" if tier_num <= 2 else "flagship"
    else:
        # No models in registry — fall back to hardcoded config
        is_micro         = tier_num <= 2
        model_id_to_use  = None
        model_tier_label = "micro" if is_micro else "flagship"
        display_name     = MICRO_MODEL["display_name"] if is_micro else FLAGSHIP_MODEL["display_name"]
        cost_in_per_m    = MICRO_MODEL["input_cost_per_million"]  if is_micro else FLAGSHIP_MODEL["input_cost_per_million"]
        cost_out_per_m   = MICRO_MODEL["output_cost_per_million"] if is_micro else FLAGSHIP_MODEL["output_cost_per_million"]
        fallback_tier    = "micro" if is_micro else "flagship"

    # Step 5 — Call the model
    model_result = call_model(working_text, model_id=model_id_to_use, fallback_tier=fallback_tier)

    # Step 6 — Calculate cost from actual token counts using registry rates
    cost_usd = _calculate_cost(
        model_result["input_tokens"],
        model_result["output_tokens"],
        cost_in_per_m,
        cost_out_per_m,
    )

    # Step 7 — Calculate pruning savings
    tokens_saved_by_pruning = prune_result["tokens_saved"] if prune_result else 0
    if tokens_saved_by_pruning > 0:
        cost_per_input_token = cost_in_per_m / 1_000_000
        pruning_cost_saved   = round(tokens_saved_by_pruning * cost_per_input_token, 6)
    else:
        pruning_cost_saved = 0.0

    # Model display name (use actual model_id in live mode)
    mode_info  = get_mode_info()
    model_name = model_result["model_id"] if mode_info["mode"] == "live" else display_name

    return {
        "department":               department,
        "complexity":               complexity,
        "routing_decision":         routing_decision,
        "routing_reason":           routing_reason,
        "matched_keywords":         complexity_result["matched_keywords"],
        "model_tier":               model_tier_label,
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
