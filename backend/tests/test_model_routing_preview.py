from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.routes_models import preview_model_routing
from database.db import Base
from database.models import ModelRegistry


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
