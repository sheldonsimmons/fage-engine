from datetime import date

from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.analytics_coverage import workspace_collection_profile
from database.db import Base
from database.models import (
    AuditEvent, HistoricalDemoSeedState, TokenTransaction,
    WorkItem, WorkUser, WorkspaceAnalyticsSettings,
)
from database.seed_historical_demo import MARKER, reset_historical_demo, seed_historical_demo


def _session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def test_historical_seed_is_attributed_deterministic_and_idempotent():
    db = _session()
    result = seed_historical_demo(
        db, "HISTORY-TEST", date(2025, 12, 1), date(2026, 2, 28)
    )
    db.commit()

    assert result["status"] == "seeded"
    assert result["transactions"] > 800
    assert db.query(TokenTransaction).count() == result["transactions"]
    assert db.query(AuditEvent).count() == result["audits"]
    assert db.query(WorkItem).count() == 12
    assert db.query(WorkUser).count() == 12
    assert db.query(TokenTransaction).filter(
        TokenTransaction.is_simulation.is_(True),
        TokenTransaction.usage_source == MARKER,
        TokenTransaction.work_item_id.isnot(None),
        TokenTransaction.work_user_id.isnot(None),
    ).count() == result["transactions"]
    assert db.query(func.count(func.distinct(TokenTransaction.governed_request_id))).scalar() == result["transactions"]

    second = seed_historical_demo(
        db, "HISTORY-TEST", date(2025, 12, 1), date(2026, 2, 28)
    )
    assert second["status"] == "already_seeded"
    assert db.query(TokenTransaction).count() == result["transactions"]

    profile = workspace_collection_profile(db, "HISTORY-TEST")
    assert profile["settings_configured"] is True
    assert profile["collection_started_at"].startswith("2025-12-01")
    assert profile["latest_complete_at"].startswith("2026-03-01")


def test_historical_seed_reset_is_scoped_and_reversible():
    db = _session()
    seed_historical_demo(db, "HISTORY-RESET", date(2026, 1, 1), date(2026, 1, 31))
    db.add(TokenTransaction(
        governed_request_id="live-request",
        workspace_id="HISTORY-RESET",
        department="HISTORY-RESET:Sales",
        model_tier="Scout",
        input_tokens=10,
        output_tokens=5,
        usage_source="provider_reported",
        cost_usd=.01,
        is_simulation=False,
    ))
    db.commit()

    result = reset_historical_demo(db, "HISTORY-RESET")
    db.commit()

    assert result["transactions_deleted"] > 0
    assert db.query(TokenTransaction).count() == 1
    assert db.query(TokenTransaction).one().governed_request_id == "live-request"
    assert db.query(AuditEvent).count() == 0
    assert db.query(WorkItem).count() == 0
    assert db.query(WorkUser).count() == 0
    assert db.query(WorkspaceAnalyticsSettings).count() == 0
    assert db.query(HistoricalDemoSeedState).count() == 0
