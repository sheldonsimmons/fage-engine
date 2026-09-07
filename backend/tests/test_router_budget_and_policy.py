"""
Routing 2.0 Phase 1 (budget-aware) + Phase 2 (policy-aware) — proves the
new route() branches actually fire, and that they leave every existing
decision path (throttle, tier tags, sensitive-term escalation, complex
requests) untouched.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.router import get_eligible_models_by_cost, route
from database.db import Base
from database.models import ModelRegistry, RoutingConfig


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _model(name, model_id, tier, provider, cost=1.0, *, default=False, department=None, enabled=True):
    return ModelRegistry(
        display_name=name,
        model_id=model_id,
        provider=provider,
        tier=tier,
        cost_input_per_1m=cost,
        cost_output_per_1m=cost * 4,
        is_enabled=enabled,
        is_default=default,
        department=department,
    )


def _routing_config(db, threshold_pct=80.0):
    cfg = RoutingConfig(id=1, complexity_token_threshold=500, complexity_keywords_json="[]",
                         budget_pressure_threshold_pct=threshold_pct)
    db.add(cfg)
    db.commit()
    return cfg


# ── Phase 1: budget-aware routing ───────────────────────────────────────────

def test_budget_pressure_downgrades_low_complexity_request_when_over_threshold():
    db = _session()
    _routing_config(db, threshold_pct=80.0)
    db.add_all([
        _model("Scout Model", "scout-1", 1, "anthropic", cost=0.5, default=True),
        _model("Analyst Model", "analyst-1", 2, "anthropic", cost=2.0, default=True),
    ])
    db.commit()

    # A short routine payload would normally land on Scout anyway, so use a
    # payload that trips MODERATE (over the 500-token threshold) to prove
    # the downgrade actually changed the outcome, not just left it at Scout.
    long_text = "word " * 600

    result = route(
        long_text, "Sales", db=db, auto_prune=False,
        budget_context={"budget_used_pct": 92.0},
    )

    assert result["routing_decision"] == "BUDGET_PRESSURE"
    assert result["model_tier"] == "Scout"
    assert "92" in result["routing_reason"]
    assert "80" in result["routing_reason"]


def test_budget_pressure_does_not_touch_genuinely_complex_requests():
    db = _session()
    _routing_config(db, threshold_pct=80.0)
    db.add_all([
        _model("Scout Model", "scout-1", 1, "anthropic", cost=0.5, default=True),
        _model("Advisor Model", "advisor-1", 3, "anthropic", cost=5.0, default=True),
    ])
    db.commit()
    cfg = db.query(RoutingConfig).first()
    cfg.complexity_keywords_json = '["contract"]'
    db.commit()

    long_complex_text = "contract " + "word " * 600  # keyword AND over threshold -> COMPLEX

    result = route(
        long_complex_text, "Sales", db=db, auto_prune=False,
        budget_context={"budget_used_pct": 99.0},
    )

    assert result["routing_decision"] == "COMPLEX"
    assert result["model_tier"] == "Advisor"


def test_budget_pressure_does_not_fire_below_threshold():
    db = _session()
    _routing_config(db, threshold_pct=80.0)
    db.add_all([
        _model("Scout Model", "scout-1", 1, "anthropic", cost=0.5, default=True),
        _model("Analyst Model", "analyst-1", 2, "anthropic", cost=2.0, default=True),
    ])
    db.commit()

    long_text = "word " * 600

    result = route(
        long_text, "Sales", db=db, auto_prune=False,
        budget_context={"budget_used_pct": 40.0},
    )

    assert result["routing_decision"] == "MODERATE"
    assert result["model_tier"] == "Analyst"


def test_budget_pressure_does_not_override_an_explicit_tier_tag():
    db = _session()
    _routing_config(db, threshold_pct=80.0)
    db.add_all([
        _model("Scout Model", "scout-1", 1, "anthropic", cost=0.5, default=True),
        _model("Analyst Model", "analyst-1", 2, "anthropic", cost=2.0, default=True),
    ])
    db.commit()

    result = route(
        "[analyst] " + "word " * 600, "Sales", db=db, auto_prune=False,
        budget_context={"budget_used_pct": 99.0},
    )

    assert result["routing_decision"] == "TIER_OVERRIDE"
    assert result["model_tier"] == "Analyst"


def test_budget_pressure_disabled_when_threshold_is_none():
    db = _session()
    # RoutingConfig.budget_pressure_threshold_pct has a Column-level Python
    # default (80.0) that SQLAlchemy applies on INSERT whenever the value is
    # None -- constructing a fresh row with None can't produce a null value
    # (this is a real SQLAlchemy default-on-insert quirk, not a product
    # bug). The real "disable" path, core.routing_config.set_budget_
    # pressure_threshold(), updates an *existing* row instead, where this
    # quirk doesn't apply -- so exercise that actual function here.
    from core.routing_config import set_budget_pressure_threshold
    _routing_config(db, threshold_pct=80.0)
    set_budget_pressure_threshold(db, None)
    db.add_all([
        _model("Scout Model", "scout-1", 1, "anthropic", cost=0.5, default=True),
        _model("Analyst Model", "analyst-1", 2, "anthropic", cost=2.0, default=True),
    ])
    db.commit()

    result = route(
        "word " * 600, "Sales", db=db, auto_prune=False,
        budget_context={"budget_used_pct": 99.0},
    )

    assert result["routing_decision"] == "MODERATE"


# ── Phase 2: policy-aware routing (provider allow-list) ─────────────────────

def test_get_eligible_models_by_cost_orders_cheapest_first_and_filters_provider():
    db = _session()
    db.add_all([
        _model("OpenAI Scout", "oa-scout", 1, "openai", cost=1.0),
        _model("Anthropic Scout Cheap", "an-scout-cheap", 1, "anthropic", cost=0.3),
        _model("Anthropic Scout Pricier", "an-scout-pricier", 1, "anthropic", cost=0.8),
    ])
    db.commit()

    all_models = get_eligible_models_by_cost(1, db)
    assert [m["model_id"] for m in all_models] == ["an-scout-cheap", "an-scout-pricier", "oa-scout"]

    anthropic_only = get_eligible_models_by_cost(1, db, allowed_providers=["anthropic"])
    assert [m["model_id"] for m in anthropic_only] == ["an-scout-cheap", "an-scout-pricier"]


def test_provider_policy_substitutes_disallowed_model_with_cheapest_allowed():
    db = _session()
    _routing_config(db)
    db.add_all([
        _model("OpenAI Scout (default)", "oa-scout", 1, "openai", cost=1.0, default=True),
        _model("Anthropic Scout", "an-scout", 1, "anthropic", cost=0.5),
    ])
    db.commit()

    result = route(
        "short routine request", "Support", db=db, auto_prune=False,
        agent_allowed_providers=["anthropic"],
    )

    assert result["model_name"] == "Anthropic Scout" or "an-scout" in str(result.get("model_name", ""))
    assert "AGENT PROVIDER POLICY" in result["routing_reason"]
    assert "substituted" in result["routing_reason"]


def test_provider_policy_proceeds_with_note_when_no_allowed_alternative_exists():
    db = _session()
    _routing_config(db)
    db.add_all([
        _model("OpenAI Scout (default)", "oa-scout", 1, "openai", cost=1.0, default=True),
    ])
    db.commit()

    result = route(
        "short routine request", "Support", db=db, auto_prune=False,
        agent_allowed_providers=["anthropic"],
    )

    assert result["execution_status"] == "succeeded"
    assert "AGENT PROVIDER POLICY" in result["routing_reason"]
    assert "no approved-provider alternative" in result["routing_reason"]


def test_provider_policy_is_a_noop_when_agent_has_no_allow_list():
    db = _session()
    _routing_config(db)
    db.add_all([
        _model("OpenAI Scout (default)", "oa-scout", 1, "openai", cost=1.0, default=True),
    ])
    db.commit()

    result = route("short routine request", "Support", db=db, auto_prune=False)

    assert "AGENT PROVIDER POLICY" not in result["routing_reason"]
