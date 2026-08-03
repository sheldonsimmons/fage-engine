from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.routes_reports import dept_scorecard, risk_report, savings_report
from database.db import Base
from database.models import AuditEvent, TokenTransaction


def _session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def test_report_endpoints_use_explicit_inclusive_calendar_window_and_workspace():
    db = _session()
    db.add_all([
        TokenTransaction(
            workspace_id="DATE-TEST", department="DATE-TEST:Sales",
            model_tier="Scout", input_tokens=100, output_tokens=20,
            tokens_saved=25, was_pruned=True, cost_usd=.01,
            timestamp=datetime(2025, 7, 15),
        ),
        TokenTransaction(
            workspace_id="DATE-TEST", department="DATE-TEST:Sales",
            model_tier="Advisor", input_tokens=200, output_tokens=40,
            cost_usd=.05, timestamp=datetime(2026, 7, 15),
        ),
        TokenTransaction(
            workspace_id="OTHER", department="OTHER:Sales",
            model_tier="Scout", input_tokens=999, output_tokens=99,
            cost_usd=.50, timestamp=datetime(2025, 7, 15),
        ),
        AuditEvent(
            workspace_id="DATE-TEST", department="DATE-TEST:Sales",
            event_type="BLOCK", risk_level="high", decision_outcome="BLOCKED",
            timestamp=datetime(2025, 7, 20),
        ),
        AuditEvent(
            workspace_id="DATE-TEST", department="DATE-TEST:Sales",
            event_type="ROUTING", risk_level="low", decision_outcome="ROUTED",
            timestamp=datetime(2026, 7, 20),
        ),
    ])
    db.commit()

    start = datetime(2025, 7, 1)
    end = datetime(2025, 8, 1)
    savings = savings_report(31, "DATE-TEST", start, end, db)
    risk = risk_report(31, "DATE-TEST", start, end, db)
    departments = dept_scorecard(31, "DATE-TEST", start, end, db)

    assert savings["total_calls"] == 1
    assert savings["total_cost_usd"] == .01
    assert len(savings["timeline"]) == 31
    assert risk["total_events"] == 1
    assert risk["blocked"] == 1
    assert len(risk["timeline"]) == 31
    assert len(departments["scorecards"]) == 1
    assert departments["scorecards"][0]["display_department"] == "Sales"
    assert departments["scorecards"][0]["total_calls"] == 1
