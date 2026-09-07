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
from core.model_client import call_model, get_mode_info, ModelProviderError
from core.keywords import term_matches_text

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
    token_count = estimate_tokens(text)
    matched     = [kw for kw in _keywords if term_matches_text(kw, text)]
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

def _get_model_from_registry(tier_num: int, db, department: str = None):
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

    cascade_order = {
        1: [1],
        2: [2, 3, 1],
        3: [3, 2, 1],
        4: [4, 3, 2, 1],
    }.get(tier_num, [])

    model = None
    for candidate_tier in cascade_order:
        if department:
            # 1. Department-specific default
            model = db.query(ModelRegistry).filter(
                ModelRegistry.tier == candidate_tier,
                ModelRegistry.is_enabled == True,
                ModelRegistry.is_default == True,
                ModelRegistry.department == department,
            ).first()

            # 2. Department-specific any-enabled
            if not model:
                model = db.query(ModelRegistry).filter(
                    ModelRegistry.tier == candidate_tier,
                    ModelRegistry.is_enabled == True,
                    ModelRegistry.department == department,
                ).first()

        # 3. Global default (department IS NULL)
        if not model:
            model = db.query(ModelRegistry).filter(
                ModelRegistry.tier == candidate_tier,
                ModelRegistry.is_enabled == True,
                ModelRegistry.is_default == True,
                ModelRegistry.department == None,
            ).first()

        # 4. Global any-enabled
        if not model:
            model = db.query(ModelRegistry).filter(
                ModelRegistry.tier == candidate_tier,
                ModelRegistry.is_enabled == True,
                ModelRegistry.department == None,
            ).first()

        if model:
            break

    if not model:
        return None

    return {
        "model_id":              model.model_id,
        "display_name":          model.display_name,
        "provider":              model.provider,
        "tier":                  model.tier,
        "tier_name":             TIER_NAMES.get(model.tier, f"Tier {model.tier}"),
        "cost_input_per_million":  model.cost_input_per_1m,
        "cost_output_per_million": model.cost_output_per_1m,
        "department_scoped":     model.department is not None,
    }


def _get_alternate_model_from_registry(tier_num: int, db, exclude_model_id: str, department: str = None):
    """
    Find one other enabled model at the same tier, for use when the
    primary model's live call just failed (bad key, no credits, provider
    outage) — a different model_id, ideally on a different provider so a
    single provider's outage doesn't take down the whole tier. Does not
    cascade to other tiers; that's what the primary lookup already does
    when nothing is configured at all, which is a different situation from
    "something is configured but temporarily unreachable."
    """
    if db is None or tier_num < 1:
        return None

    from database.models import ModelRegistry

    query = db.query(ModelRegistry).filter(
        ModelRegistry.tier == tier_num,
        ModelRegistry.is_enabled == True,
        ModelRegistry.model_id != exclude_model_id,
    )
    if department:
        model = query.filter(ModelRegistry.department == department).first()
        if not model:
            model = query.filter(ModelRegistry.department == None).first()
    else:
        model = query.filter(ModelRegistry.department == None).first()
    if not model:
        return None

    return {
        "model_id":              model.model_id,
        "display_name":          model.display_name,
        "provider":              model.provider,
        "tier":                  model.tier,
        "tier_name":             TIER_NAMES.get(model.tier, f"Tier {model.tier}"),
        "cost_input_per_million":  model.cost_input_per_1m,
        "cost_output_per_million": model.cost_output_per_1m,
    }


def get_eligible_models_by_cost(max_tier: int, db, department: str = None, allowed_providers: list = None) -> list:
    """
    List every enabled model at or below max_tier, cheapest first (by input
    cost per 1M tokens). Unlike _get_model_from_registry(), this returns
    every candidate instead of the single best match, and does not cascade
    to a tier above max_tier — a caller asking "what's cheaper than what I
    already picked" should never get back something more expensive.

    Foundation for budget-aware routing's "route to the cheapest eligible
    alternative under budget pressure" and policy-aware routing's provider
    allow-list (see _apply_provider_policy() below, its first caller). Same
    table and filter shape as _get_model_from_registry(), department-scoped
    rows included alongside global ones (not preferred over them, since the
    caller wants the full eligible set to choose from, not a single default).

    allowed_providers: optional list — when given (non-empty), only models
    whose ModelRegistry.provider is in this list are returned.
    """
    if db is None or max_tier < 1:
        return []

    from database.models import ModelRegistry

    query = db.query(ModelRegistry).filter(
        ModelRegistry.tier <= max_tier,
        ModelRegistry.is_enabled == True,
    )
    if department:
        query = query.filter(
            (ModelRegistry.department == department) | (ModelRegistry.department == None)
        )
    else:
        query = query.filter(ModelRegistry.department == None)
    if allowed_providers:
        query = query.filter(ModelRegistry.provider.in_(allowed_providers))

    models = query.order_by(ModelRegistry.cost_input_per_1m.asc()).all()

    return [
        {
            "model_id":                model.model_id,
            "display_name":            model.display_name,
            "provider":                model.provider,
            "tier":                    model.tier,
            "tier_name":               TIER_NAMES.get(model.tier, f"Tier {model.tier}"),
            "cost_input_per_million":  model.cost_input_per_1m,
            "cost_output_per_million": model.cost_output_per_1m,
            "department_scoped":       model.department is not None,
        }
        for model in models
    ]


def _apply_provider_policy(registry_model: dict, tier_num: int, db, department: str, allowed_providers: list):
    """
    Policy-aware routing (Routing 2.0, Phase 2). If registry_model's
    provider isn't in allowed_providers, look for the cheapest enabled,
    allowed-provider model at or below tier_num (via
    get_eligible_models_by_cost()) rather than hard-blocking the request —
    same "cascade to something usable" philosophy as the existing tier
    cascade, not a new failure mode. Returns (model_dict_or_None, note),
    where note is a routing_reason fragment describing what happened, or
    None if no policy applies / the model was already allowed.
    """
    if not allowed_providers or not registry_model:
        return registry_model, None
    if registry_model.get("provider") in allowed_providers:
        return registry_model, None

    candidates = get_eligible_models_by_cost(tier_num, db, department=department, allowed_providers=allowed_providers)
    if candidates:
        chosen = candidates[0]
        note = (
            f"[AGENT PROVIDER POLICY: '{registry_model['display_name']}' ({registry_model.get('provider')}) "
            f"not approved for this agent — substituted '{chosen['display_name']}' ({chosen['provider']})]"
        )
        return chosen, note

    note = (
        f"[AGENT PROVIDER POLICY: '{registry_model['display_name']}' ({registry_model.get('provider')}) "
        f"not approved for this agent, and no approved-provider alternative is registered at or below "
        f"{TIER_NAMES.get(tier_num, tier_num)} — request proceeded on the unapproved model]"
    )
    return registry_model, note


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
    agent_min_tier: int = None,
    agent_max_tier: int = None,
    force_simulated_model: bool = False,
    budget_context: dict = None,
    work_item_context: dict = None,
    agent_allowed_providers: list = None,
) -> dict:
    """
    Full routing pipeline for one payload.

    Returns a complete routing report with:
      - complexity classification and reason
      - tier selected (Scout / Analyst / Advisor / Strategist)
      - model picked from the registry (or hardcoded fallback)
      - real token counts and cost
      - cost comparison: with pruning vs. without pruning

    budget_context / work_item_context: accepted but NOT used in today's
    decision logic — wiring-only, added ahead of the Routing 2.0 assessment
    (budget-aware / business-context-aware routing phases) so future work
    doesn't need a second pass to thread this data into the call site.
    budget_context is the same dict api/routes_router.py already computes
    via effective_budget_context() (utilization %, cap, spend); is_throttled
    /throttle_tier remain the only budget signal actually enforced here.
    work_item_context is a light dict describing the resolved WorkItem, if
    any (see api/routes_router.py's call site) — the WorkItem itself is
    already resolved before route() runs today, just never passed in.

    agent_allowed_providers: policy-aware routing (Routing 2.0, Phase 2) —
    a non-empty list restricts this agent to those ModelRegistry.provider
    values (e.g. ["anthropic"]). None/empty = unrestricted, same as today.
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
    _budget_pressure_threshold = None
    if db is not None:
        try:
            from core.routing_config import get_routing_config
            cfg        = get_routing_config(db)
            _threshold = cfg.complexity_token_threshold
            _keywords  = cfg.complexity_keywords
            _budget_pressure_threshold = cfg.budget_pressure_threshold_pct
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
        # An explicit model-tier instruction is not a human budget override.
        # Keep the selected tier unchanged while recording the decision accurately.
        routing_decision = "TIER_OVERRIDE"
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

    # Budget-aware routing (Routing 2.0, Phase 1) — a soft precaution, not a
    # hard cap: only touches requests that got here on their own merits
    # (not already throttled, not an explicit tier tag, not a sensitive-term
    # escalation) and only downgrades LOW-complexity requests (would-be
    # Scout/Analyst) — a genuinely COMPLEX request is never silently
    # downgraded for budget reasons alone. Distinct from is_throttled above,
    # which is the existing hard 100%+ cap; this fires earlier, at an
    # admin-configured pressure threshold (default 80%), using the same
    # budget_context the caller already computed for this request.
    if (
        not is_throttled
        and forced_tier is None
        and not force_complex
        and tier_num in (1, 2)
        and budget_context
        and _budget_pressure_threshold is not None
    ):
        _utilization_pct = budget_context.get("budget_used_pct")
        if _utilization_pct is not None and _utilization_pct >= _budget_pressure_threshold and tier_num != 1:
            original_tier    = tier_num
            tier_num         = 1
            routing_decision = "BUDGET_PRESSURE"
            routing_reason   = (
                f"Department '{department}' is at {_utilization_pct:.0f}% of its monthly AI budget "
                f"(pressure threshold {_budget_pressure_threshold:.0f}%) — low-complexity request "
                f"downgraded from {TIER_NAMES.get(original_tier, original_tier)} to Scout as a precaution. "
                f"{routing_reason}"
            )

    if agent_min_tier is not None or agent_max_tier is not None:
        agent_min = max(1, min(4, agent_min_tier or 1))
        agent_max = max(1, min(4, agent_max_tier or 4))
        original_tier = tier_num
        tier_num = max(agent_min, min(agent_max, tier_num))
        if tier_num != original_tier:
            direction = "bumped up" if tier_num > original_tier else "capped down"
            routing_reason = (
                f"[AGENT BOUND: {direction} from {TIER_NAMES.get(original_tier, original_tier)} "
                f"to {TIER_NAMES.get(tier_num, tier_num)} by agent policy] {routing_reason}"
            )

    # Step 4 — Look up model from registry (department-specific first, then global)
    registry_model = _get_model_from_registry(tier_num, db, department=department)

    # Policy-aware routing (Routing 2.0, Phase 2) — substitute if this
    # agent's provider allow-list excludes the model the tier lookup picked
    if registry_model and agent_allowed_providers:
        registry_model, _policy_note = _apply_provider_policy(
            registry_model, tier_num, db, department, agent_allowed_providers
        )
        if _policy_note:
            routing_reason = f"{_policy_note} {routing_reason}"

    if registry_model:
        model_id_to_use  = registry_model["model_id"]
        # Always use the REQUESTED tier's name — not the cascaded model's tier
        model_tier_label = TIER_NAMES.get(tier_num, registry_model["tier_name"])
        resolved_model_tier = registry_model["tier_name"]
        display_name     = registry_model["display_name"]
        cost_in_per_m    = registry_model["cost_input_per_million"]
        cost_out_per_m   = registry_model["cost_output_per_million"]
        fallback_tier    = "micro" if tier_num <= 2 else "flagship"
        model_source     = "registry"
    else:
        # No models in registry — fall back to hardcoded config
        is_micro         = tier_num <= 2
        model_id_to_use  = None
        model_tier_label = TIER_NAMES.get(tier_num, "Scout" if is_micro else "Advisor")
        resolved_model_tier = model_tier_label
        display_name     = MICRO_MODEL["display_name"] if is_micro else FLAGSHIP_MODEL["display_name"]
        cost_in_per_m    = MICRO_MODEL["input_cost_per_million"]  if is_micro else FLAGSHIP_MODEL["input_cost_per_million"]
        cost_out_per_m   = MICRO_MODEL["output_cost_per_million"] if is_micro else FLAGSHIP_MODEL["output_cost_per_million"]
        fallback_tier    = "micro" if is_micro else "flagship"
        model_source     = "built_in_fallback"

    # Step 5 — Call the model, with one same-tier fallback attempt and a
    # clean governed "degraded" result if nothing reachable is left. A live
    # provider outage or an exhausted API key used to surface as a raw,
    # unhandled 502 all the way through to callers like the Salesforce
    # Agentforce integration — this governs the request either way instead
    # of hard-failing it.
    provider_unavailable = False
    provider_error_detail = None
    try:
        model_result = call_model(
            working_text,
            model_id=model_id_to_use,
            fallback_tier=fallback_tier,
            force_simulated=force_simulated_model,
        )
    except ModelProviderError as primary_error:
        alternate = _get_alternate_model_from_registry(
            tier_num, db, exclude_model_id=model_id_to_use, department=department
        ) if model_id_to_use else None
        if alternate:
            try:
                model_result = call_model(
                    working_text,
                    model_id=alternate["model_id"],
                    fallback_tier=fallback_tier,
                    force_simulated=force_simulated_model,
                )
                model_id_to_use = alternate["model_id"]
                display_name    = alternate["display_name"]
                cost_in_per_m    = alternate["cost_input_per_million"]
                cost_out_per_m   = alternate["cost_output_per_million"]
                routing_reason  += f" [Fell back to {alternate['display_name']} after the primary model was unavailable: {primary_error}]"
            except ModelProviderError as fallback_error:
                provider_unavailable = True
                provider_error_detail = f"{primary_error}; fallback to {alternate['display_name']} also failed: {fallback_error}"
        else:
            provider_unavailable = True
            provider_error_detail = str(primary_error)

    if provider_unavailable:
        mode_info = get_mode_info()
        return {
            "department":               department,
            "complexity":               complexity,
            "routing_decision":         "PROVIDER_UNAVAILABLE",
            "routing_reason":           (
                f"Request was governed and logged, but no AI response could be generated: "
                f"{provider_error_detail}"
            ),
            "matched_keywords":         complexity_result["matched_keywords"],
            "model_tier":               model_tier_label,
            "resolved_model_tier":      resolved_model_tier,
            "model_source":             model_source,
            "routing_cascaded":         False,
            "model_name":               "unavailable",
            "input_tokens":             0,
            "output_tokens":            0,
            "usage_source":             "unavailable",
            "cost_usd":                 0.0,
            "simulated_response":       (
                "CostPilot governed this request, but the configured AI provider was "
                "unavailable and no response was generated. No cost was incurred."
            ),
            "was_pruned":               auto_prune,
            "tokens_saved_by_pruning":  prune_result["tokens_saved"] if prune_result else 0,
            "pruning_filter_details":   prune_result["filter_details"] if prune_result else [],
            "pruning_cost_saved_usd":   0.0,
            "total_cost_without_pruning": 0.0,
            "provider":                 "unavailable",
            "model_mode":               "simulated" if force_simulated_model else mode_info["mode"],
            "execution_status":         "failed",
        }

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
    mode_info = get_mode_info()
    effective_model_mode = "simulated" if force_simulated_model else mode_info["mode"]
    model_name = model_result["model_id"] if effective_model_mode == "live" else display_name

    return {
        "department":               department,
        "complexity":               complexity,
        "routing_decision":         routing_decision,
        "routing_reason":           routing_reason,
        "matched_keywords":         complexity_result["matched_keywords"],
        "model_tier":               model_tier_label,
        "resolved_model_tier":      resolved_model_tier,
        "model_source":             model_source,
        "routing_cascaded":         resolved_model_tier != model_tier_label,
        "model_name":               model_name,
        "input_tokens":             model_result["input_tokens"],
        "output_tokens":            model_result["output_tokens"],
        "usage_source":             model_result.get("usage_source", "estimated"),
        "cost_usd":                 cost_usd,
        "simulated_response":       model_result["response_text"],
        "was_pruned":               auto_prune,
        "tokens_saved_by_pruning":  tokens_saved_by_pruning,
        "pruning_filter_details":   prune_result["filter_details"] if prune_result else [],
        "pruning_cost_saved_usd":   pruning_cost_saved,
        "total_cost_without_pruning": round(cost_usd + pruning_cost_saved, 6),
        "provider":                 model_result["provider"],
        "model_mode":               effective_model_mode,
        "execution_status":         "succeeded",
    }
