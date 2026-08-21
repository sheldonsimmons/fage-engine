from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.routes_efficiency import (
    AskCostPilotRequest,
    WorkspaceAnalyticsSettingsRequest,
    ask_costpilot,
    get_workspace_analytics_settings,
    update_workspace_analytics_settings,
)
from api.routes_trial import (
    AiEnvironmentEstimateRequest,
    RegisterTrialRequest,
    estimate_ai_environment,
    register_trial,
)
from core.analytics_coverage import (
    comparison_data_coverage,
    period_coverage,
    workspace_collection_profile,
)
from core.analytics_periods import comparison_plan, resolve_primary_period
from database.db import Base
from database.models import TokenTransaction, TrialAccount, Workspace


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _trial(workspace_id: str, created_at: datetime) -> TrialAccount:
    return TrialAccount(
        email=f"{workspace_id.lower()}@example.com",
        name="Coverage Test",
        api_key_enc="test",
        workspace_id=workspace_id,
        trial_end=datetime(2027, 1, 1),
        created_at=created_at,
    )


def test_workspace_settings_are_persisted_and_validated():
    db = _session()
    saved = update_workspace_analytics_settings(
        WorkspaceAnalyticsSettingsRequest(
            workspace_id="WS-COVERAGE",
            timezone_name="America/Chicago",
            week_starts_on=0,
            fiscal_year_start_month=7,
            default_window_days=45,
            collection_started_at=datetime(2026, 6, 1),
            latest_complete_at=datetime(2026, 8, 1),
        ),
        db,
    )
    loaded = get_workspace_analytics_settings("WS-COVERAGE", db)

    assert saved["configured"] is True
    assert loaded["timezone_name"] == "America/Chicago"
    assert loaded["fiscal_year_start_month"] == 7
    assert loaded["collection_started_at"] == datetime(2026, 6, 1)


def test_trial_registration_persists_timezone_and_collection_start():
    db = _session()
    result = register_trial(
        RegisterTrialRequest(
            email="new-workspace@example.com",
            name="New Workspace",
            timezone_name="America/Chicago",
        ),
        db,
    )
    settings = get_workspace_analytics_settings(result["workspace_id"], db)

    assert settings["configured"] is True
    assert settings["timezone_name"] == "America/Chicago"
    assert settings["collection_started_at"] is not None


def test_trial_registration_creates_real_workspace_row():
    db = _session()
    result = register_trial(
        RegisterTrialRequest(
            email="workspace-row@example.com",
            name="Workspace Row Test",
            company="Acme Co",
        ),
        db,
    )

    ws = db.query(Workspace).filter_by(workspace_id=result["workspace_id"]).first()

    assert ws is not None
    assert ws.name == "Acme Co"
    assert ws.source == "trial_signup"
    assert ws.workspace_type == "production"


def test_trial_registration_backfills_workspace_row_for_returning_account():
    db = _session()
    first = register_trial(
        RegisterTrialRequest(email="returning@example.com", name="Returning User"),
        db,
    )
    # Simulate a pre-existing account created before the Workspace table backfill.
    db.query(Workspace).filter_by(workspace_id=first["workspace_id"]).delete()
    db.commit()

    register_trial(
        RegisterTrialRequest(email="returning@example.com", name="Returning User"),
        db,
    )

    ws = db.query(Workspace).filter_by(workspace_id=first["workspace_id"]).first()
    assert ws is not None


def test_estimate_ai_environment_requires_no_inputs():
    result = estimate_ai_environment(AiEnvironmentEstimateRequest())

    assert result["is_estimate"] is True
    assert result["savings_estimate"] is None
    assert len(result["opportunities"]) >= 2


def test_estimate_ai_environment_labels_savings_as_estimate():
    result = estimate_ai_environment(
        AiEnvironmentEstimateRequest(
            monthly_spend_usd=1000,
            providers=["anthropic", "openai"],
            agent_count=5,
            monthly_requests=20000,
        )
    )

    assert result["is_estimate"] is True
    assert "estimate" in result["estimate_disclaimer"].lower()
    assert result["savings_estimate"]["estimated_monthly_savings_usd"] > 0
    assert any(o["area"] == "governance" for o in result["opportunities"])


def test_workspace_creation_proves_earlier_history_is_unavailable():
    db = _session()
    db.add(_trial("WS-NEW", datetime(2026, 6, 1)))
    db.commit()
    profile = workspace_collection_profile(db, "WS-NEW")
    primary = resolve_primary_period(
        period_key=None,
        days=30,
        timezone_name="UTC",
        date_from=datetime(2026, 7, 1),
        date_to=datetime(2026, 8, 1),
    )
    plan = comparison_plan(primary, "same_period_previous_year")
    coverage = comparison_data_coverage(
        plan,
        {"request_count": 10, "live_count": 10, "simulation_count": 0},
        {"request_count": 0, "live_count": 0, "simulation_count": 0},
        profile,
    )

    assert profile["collection_start_source"] == "workspace_created_at"
    assert coverage["status"] == "comparison_history_unavailable"
    assert coverage["comparable"] is False
    assert coverage["comparison_period"]["status"] == "unavailable_before_collection"


def test_zero_is_verified_only_inside_explicit_complete_collection_window():
    period = resolve_primary_period(
        period_key=None,
        days=30,
        timezone_name="UTC",
        date_from=datetime(2026, 7, 1),
        date_to=datetime(2026, 8, 1),
    )
    coverage = period_coverage(
        period,
        0,
        {
            "collection_started_at": "2026-01-01T00:00:00",
            "latest_complete_at": "2026-08-02T00:00:00",
        },
    )

    assert coverage["status"] == "verified_zero_activity"
    assert coverage["verified_zero"] is True


def test_ask_costpilot_withholds_change_when_prior_period_predates_workspace():
    db = _session()
    workspace_id = "WS-ASK-COVERAGE"
    db.add(_trial(workspace_id, datetime(2026, 6, 1)))
    db.add(TokenTransaction(
        workspace_id=workspace_id,
        department="Support",
        model_tier="analyst",
        input_tokens=800,
        output_tokens=200,
        cost_usd=0.02,
        timestamp=datetime(2026, 7, 15),
        is_simulation=False,
    ))
    db.commit()

    answer = ask_costpilot(
        AskCostPilotRequest(
            question="Compare token usage with around this time last year.",
            workspace_id=workspace_id,
            timezone_name="UTC",
            date_from=datetime(2026, 7, 1),
            date_to=datetime(2026, 8, 1),
        ),
        db,
    )

    assert answer["contract_status"] == "passed"
    assert answer["data_provenance"]["coverage"]["status"] == "comparison_history_unavailable"
    assert answer["calculation"]["absolute_change"] is None
    assert answer["calculation"]["percent_change"] is None
    assert "valid comparison" in answer["answer"]
    assert "collection began" in answer["answer"]
