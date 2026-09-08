"""
tests/test_ask_demo_workspace_comparison_exemption.py — a workspace
structurally flagged workspace_type="demo" (e.g. the historical demo
dataset) should get a real change_drivers/comparison answer even when
its traffic-scope classification is "mixed"/"simulator" (its activity
is deliberately, honestly is_simulation=True end to end) -- a real
customer workspace with the same traffic-scope mismatch must still get
the honest refusal, unchanged. Found via a live cockpit repro: asking
"Why did AI spend increase?" against the historical demo workspace
returned "CostPilot cannot determine why AI spend changed... align
live and simulator scope" instead of a real answer.
"""
from core.analytics_coverage import comparison_data_coverage
from core.analytics_periods import comparison_plan, resolve_primary_period
from database.db import Base
from database.models import Workspace
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.routes_efficiency import _ask_is_demo_workspace


def _session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _plan():
    primary = resolve_primary_period(period_key="this_month", days=31)
    return comparison_plan(primary, "previous_month")


MIXED_SUMMARY = {"request_count": 10, "live_count": 1, "simulation_count": 9}
SIMULATOR_SUMMARY = {"request_count": 8, "live_count": 0, "simulation_count": 8}


def test_is_demo_workspace_true_only_for_workspace_type_demo():
    db = _session()
    db.add_all([
        Workspace(workspace_id="WS-DEMO", name="Historical Demo", workspace_type="demo"),
        Workspace(workspace_id="WS-REAL", name="Real Customer", workspace_type="production"),
    ])
    db.commit()

    assert _ask_is_demo_workspace(db, "WS-DEMO") is True
    assert _ask_is_demo_workspace(db, "WS-REAL") is False
    assert _ask_is_demo_workspace(db, "WS-UNKNOWN") is False
    assert _ask_is_demo_workspace(None, "WS-DEMO") is False
    assert _ask_is_demo_workspace(db, None) is False


def test_demo_workspace_is_exempted_from_traffic_scope_mismatch_refusal():
    result = comparison_data_coverage(
        _plan(), MIXED_SUMMARY, SIMULATOR_SUMMARY, profile={}, is_demo_workspace=True,
    )
    assert result["comparable"] is True
    assert result["status"] == "verified_comparable_periods"
    assert result["limitation"] is None
    # The underlying scope facts are still reported, just no longer block the answer
    assert result["primary_traffic_scope"] == "mixed"
    assert result["comparison_traffic_scope"] == "simulator"


def test_real_workspace_still_gets_the_honest_refusal():
    result = comparison_data_coverage(
        _plan(), MIXED_SUMMARY, SIMULATOR_SUMMARY, profile={}, is_demo_workspace=False,
    )
    assert result["comparable"] is False
    assert result["primary_traffic_scope"] == "mixed"
    assert result["comparison_traffic_scope"] == "simulator"


def test_is_demo_workspace_defaults_to_false_when_omitted():
    # Every existing caller that doesn't pass is_demo_workspace must keep
    # today's strict behavior unchanged.
    result = comparison_data_coverage(_plan(), MIXED_SUMMARY, SIMULATOR_SUMMARY, profile={})
    assert result["comparable"] is False
