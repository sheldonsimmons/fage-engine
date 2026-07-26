import json
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.routes_models import (
    get_model_routing_outcome_detail,
    get_model_routing_outcomes,
    preview_model_routing,
)
from core.router import route
from database.db import Base
from database.models import AuditEvent, ModelRegistry, TokenTransaction


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _model(name, model_id, tier, *, default=False, department=None, enabled=True):
    return ModelRegistry(
        display_name=name,
        model_id=model_id,
        provider="Test",
        tier=tier,
        cost_input_per_1m=1.25,
        cost_output_per_1m=5.0,
        is_enabled=enabled,
        is_default=default,
        department=department,
    )


def test_preview_uses_department_default_before_global_default():
    db = _session()
    db.add_all([
        _model("Global Scout", "global-scout", 1, default=True),
        _model("Legal Scout", "legal-scout", 1, default=True, department="Legal"),
    ])
    db.commit()

    result = preview_model_routing(tier=1, department="Legal", db=db)

    assert result["source"] == "registry"
    assert result["model_id"] == "legal-scout"
    assert result["scope"] == "department"
    assert result["cascaded"] is False


def test_preview_matches_existing_tier_two_upward_cascade():
    db = _session()
    db.add(_model("Global Advisor", "global-advisor", 3, default=True))
    db.commit()

    result = preview_model_routing(tier=2, department=None, db=db)

    assert result["model_id"] == "global-advisor"
    assert result["requested_tier"] == 2
    assert result["resolved_tier"] == 3
    assert result["cascaded"] is True


def test_preview_ignores_disabled_default():
    db = _session()
    db.add_all([
        _model("Disabled Default", "disabled", 4, default=True, enabled=False),
        _model("Enabled Strategist", "enabled", 4),
    ])
    db.commit()

    result = preview_model_routing(tier=4, department=None, db=db)

    assert result["model_id"] == "enabled"
    assert result["source"] == "registry"


def test_preview_bounds_empty_tier_two_and_three_before_scout():
    db = _session()
    db.add(_model("Global Scout", "global-scout", 1, default=True))
    db.commit()

    result = preview_model_routing(tier=2, department=None, db=db)

    assert result["model_id"] == "global-scout"
    assert result["resolved_tier"] == 1
    assert result["cascaded"] is True


def test_preview_uses_built_in_fallback_when_registry_is_empty():
    db = _session()

    result = preview_model_routing(tier=2, department=None, db=db)

    assert result["source"] == "built_in_fallback"
    assert result["model_id"] == "micro-model-v1"


def test_router_records_resolved_tier_after_bounded_cascade():
    db = _session()
    db.add(_model("Scout Exact", "scout-exact", 1, default=True))
    db.commit()

    result = route(
        "Summarize this short customer note.",
        "Sales",
        db=db,
        auto_prune=False,
    )

    assert result["model_name"] == "Scout Exact"
    assert result["model_tier"] == "Analyst"
    assert result["resolved_model_tier"] == "Scout"
    assert result["model_source"] == "registry"
    assert result["routing_cascaded"] is True


def test_routing_outcomes_separates_exact_and_inferred_history():
    db = _session()
    db.add_all([
        _model("Global Scout", "global-scout", 1, default=True),
        _model("Global Advisor", "global-advisor", 3, default=True),
        _model("Unused Strategist", "unused-strategist", 4, default=True),
        TokenTransaction(
            department="Sales",
            model_tier="Scout",
            model_name="global-scout",
            resolved_model_tier="Scout",
            model_source="registry",
            routing_cascaded=False,
            input_tokens=100,
            output_tokens=20,
            cost_usd=0.2,
            routing_reason="ROUTINE",
            timestamp=datetime.utcnow(),
        ),
        TokenTransaction(
            department="Legal",
            model_tier="flagship",
            model_name=None,
            input_tokens=200,
            output_tokens=40,
            cost_usd=0.3,
            routing_reason="COMPLEX",
            timestamp=datetime.utcnow(),
        ),
        TokenTransaction(
            department="Support",
            model_tier="Analyst",
            model_name="global-advisor",
            resolved_model_tier="Advisor",
            model_source="registry",
            routing_cascaded=True,
            input_tokens=150,
            output_tokens=30,
            cost_usd=0.4,
            routing_reason="MODERATE",
            timestamp=datetime.utcnow(),
        ),
        TokenTransaction(
            department="Legal",
            model_tier="Blocked",
            model_name="BLOCKED",
            resolved_model_tier="Blocked",
            model_source="provider_proxy",
            routing_cascaded=False,
            input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
            routing_reason="BLOCKED",
            timestamp=datetime.utcnow(),
        ),
        AuditEvent(
            event_type="ROUTING",
            department="Support",
            model_tier="Advisor",
            context_snapshot=json.dumps({"model_name": "global-advisor"}),
            decision_outcome="Advisor model used",
            risk_level="medium",
            timestamp=datetime.utcnow(),
        ),
        AuditEvent(
            event_type="ROUTING",
            department="Legal",
            model_tier="flagship",
            context_snapshot=json.dumps({}),
            decision_outcome="Flagship model used",
            risk_level="medium",
            timestamp=datetime.utcnow(),
        ),
    ])
    db.commit()

    result = get_model_routing_outcomes(days=30, db=db)

    assert result["total_calls"] == 3
    assert result["recorded_calls"] == 2
    assert result["inferred_calls"] == 1
    assert result["telemetry_coverage_pct"] == 66.7
    assert result["telemetry_coverage_pct_precise"] == 66.6667
    assert result["cascaded_calls"] == 1
    assert result["fallback_calls"] == 0
    assert result["unused_eligible_count"] == 1
    assert result["unused_eligible"][0]["model_id"] == "unused-strategist"
    assert result["spend_concentration_pct"] == 77.8
    assert {alert["code"] for alert in result["alerts"]} == {
        "routing_cascade",
        "eligible_unused",
    }
    advisor = next(row for row in result["models"] if row["model_key"] == "global-advisor")
    assert advisor["calls"] == 2
    assert advisor["telemetry"] == "mixed"
    assert advisor["top_departments"] == [
        {"department": "Legal", "calls": 1},
        {"department": "Support", "calls": 1},
    ]

    detail = get_model_routing_outcome_detail(
        model_key="global-advisor",
        days=30,
        db=db,
    )
    assert detail["total_calls"] == 2
    assert detail["exact_calls"] == 1
    assert detail["inferred_calls"] == 1
    assert detail["cascaded_calls"] == 1
    assert [row["department"] for row in detail["departments"]] == ["Legal", "Support"]
    assert {row["telemetry"] for row in detail["audit_events"]} == {"exact", "tier_related"}
    assert detail["optimization"]["confidence"] == "mixed"
    assert detail["optimization"]["top_agent"]["agent_name"] == "Unassigned"
    assert detail["optimization"]["top_department"]["department"] == "Support"
    assert detail["optimization"]["review_candidate_calls"] == 1
    assert detail["optimization"]["routing_reasons"] == [
        {"reason": "MODERATE", "calls": 1, "spend_usd": 0.4, "share_pct": 50.0},
        {"reason": "COMPLEX", "calls": 1, "spend_usd": 0.3, "share_pct": 50.0},
    ]
    assert detail["optimization"]["scenario"]["candidate_display_name"] == "Global Scout"
    assert detail["optimization"]["scenario"]["disclaimer"].startswith("Illustrative cost scenario only.")
