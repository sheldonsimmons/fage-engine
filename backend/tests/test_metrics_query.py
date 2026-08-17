"""
tests/test_metrics_query.py — correctness check for core.metrics_query.
run_metrics_query, the Milestone 1 semantic metrics layer engine.

Covers: single-source metrics (activity-only, outcome-only), the mixed
activity+outcome case (the Acme acceptance test from the spec), dimension
grouping, filters, workspace isolation, comparison, and unknown
metric/dimension handling.
"""
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.metrics_query import run_metrics_query
from database.db import Base
from database.models import RegisteredAgent, TokenTransaction, WorkAccount, WorkItem, WorkItemOutcome


def _session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _account(db, *, name, external_id, workspace_id="WS1"):
    acct = WorkAccount(name=name, external_id=external_id, workspace_id=workspace_id)
    db.add(acct)
    db.commit()
    db.refresh(acct)
    return acct


def _work_item(db, *, external_id, account=None, workspace_id="WS1", context_type="opportunity"):
    wi = WorkItem(
        external_id=external_id, workspace_id=workspace_id, name=external_id,
        context_type=context_type, account_id=account.id if account else None,
    )
    db.add(wi)
    db.commit()
    db.refresh(wi)
    return wi


def _outcome(db, work_item, *, workspace_id="WS1", outcome_success=None, is_closed=False, outcome_value=0.0):
    db.add(WorkItemOutcome(
        work_item_id=work_item.id, workspace_id=workspace_id, source_system="salesforce",
        source_object="Opportunity", external_id=work_item.external_id,
        outcome_success=outcome_success, is_closed=is_closed, outcome_value=outcome_value,
        last_synced_at=datetime.utcnow(),
    ))


def _tx(db, *, cost_usd, work_item=None, department="WS1:Sales", agent=None,
        model_name="claude-3-5-sonnet", platform="Salesforce", tokens=100,
        workspace_id="WS1", routing_reason="ROUTINE", timestamp=None):
    db.add(TokenTransaction(
        department=department, model_tier="Analyst", model_name=model_name,
        source_platform=platform, agent_id=agent.id if agent else None,
        input_tokens=tokens, output_tokens=tokens, cost_usd=cost_usd,
        timestamp=timestamp or datetime.utcnow(), workspace_id=workspace_id,
        is_simulation=False, usage_source="estimated", routing_reason=routing_reason,
        work_item_id=work_item.id if work_item else None,
    ))


def test_activity_only_metric_grouped_by_model():
    db = _session()
    _tx(db, cost_usd=3.0, model_name="claude-3-5-sonnet")
    _tx(db, cost_usd=7.0, model_name="gpt-4o")
    db.commit()

    result = run_metrics_query(db, "WS1", metrics=["ai_spend"], dimensions=["model"])
    assert not result.errors
    by_model = {r["dimensions"]["model"]: r["ai_spend"] for r in result.rows}
    assert by_model["claude-3-5-sonnet"] == 3.0
    assert by_model["gpt-4o"] == 7.0


def test_voice_guard_prune_excluded_from_ai_spend():
    db = _session()
    _tx(db, cost_usd=5.0, routing_reason="ROUTINE")
    _tx(db, cost_usd=999.0, routing_reason="VOICE_GUARD_PRUNE")
    db.commit()

    result = run_metrics_query(db, "WS1", metrics=["ai_spend"])
    assert result.rows[0]["ai_spend"] == 5.0


def test_outcome_only_metric_won_count():
    db = _session()
    won = _work_item(db, external_id="opp-won")
    lost = _work_item(db, external_id="opp-lost")
    _outcome(db, won, outcome_success=True, is_closed=True, outcome_value=10000.0)
    _outcome(db, lost, outcome_success=False, is_closed=True, outcome_value=2000.0)
    db.commit()

    result = run_metrics_query(db, "WS1", metrics=["won_count", "lost_count", "won_value"])
    assert not result.errors
    row = result.rows[0]
    assert row["won_count"] == 1
    assert row["lost_count"] == 1
    assert row["won_value"] == 10000.0


def test_acme_acceptance_case_activity_and_outcome_combined_by_account():
    """
    'What business outcomes are associated with Acme's AI activity?' --
    the spec's core acceptance test. Filters to account=Acme, requests
    both an activity metric (ai_spend) and outcome metrics (won_count,
    won_value) in one call.
    """
    db = _session()
    acme = _account(db, name="Acme Corporation", external_id="acct-1")
    globex = _account(db, name="Globex", external_id="acct-2")
    acme_won = _work_item(db, external_id="acme-opp-won", account=acme)
    acme_open = _work_item(db, external_id="acme-opp-open", account=acme)
    globex_won = _work_item(db, external_id="globex-opp-won", account=globex)

    _outcome(db, acme_won, outcome_success=True, is_closed=True, outcome_value=1700000.0)
    _outcome(db, acme_open, outcome_success=None, is_closed=False, outcome_value=250000.0)
    _outcome(db, globex_won, outcome_success=True, is_closed=True, outcome_value=999999.0)

    _tx(db, cost_usd=3842.18, work_item=acme_won)
    _tx(db, cost_usd=999.0, work_item=globex_won)  # must not leak into Acme's numbers
    db.commit()

    result = run_metrics_query(
        db, "WS1",
        metrics=["ai_spend", "ai_requests", "won_count", "open_count", "won_value"],
        filters={"account": "Acme"},
    )
    assert not result.errors
    assert result.scope["account"] == "Acme Corporation"
    row = result.rows[0]
    assert row["ai_spend"] == 3842.18
    assert row["ai_requests"] == 1
    assert row["won_count"] == 1
    assert row["open_count"] == 1
    assert row["won_value"] == 1700000.0


def test_ambiguous_account_name_reported_as_error_not_crash():
    db = _session()
    _account(db, name="Acme Corp", external_id="a1")
    _account(db, name="Acme Industries", external_id="a2")
    db.commit()

    result = run_metrics_query(db, "WS1", metrics=["ai_spend"], filters={"account": "Acme"})
    assert result.errors
    assert result.errors[0]["code"] == "account_ambiguous"
    assert result.rows == []


def test_unknown_metric_reported_not_silently_dropped():
    db = _session()
    result = run_metrics_query(db, "WS1", metrics=["ai_spend", "made_up_metric"])
    assert any(e["code"] == "unknown_metric" for e in result.errors)
    assert "ai_spend" in result.metrics


def test_not_yet_computable_metric_reported_distinctly():
    db = _session()
    result = run_metrics_query(db, "WS1", metrics=["ai_spend", "savings"])
    assert "savings" in result.unsupported_metrics
    assert "ai_spend" in result.metrics


def test_mixed_dimension_unsupported_for_both_sources_errors_not_crashes():
    db = _session()
    result = run_metrics_query(
        db, "WS1", metrics=["ai_spend", "won_count"], dimensions=["model"],
    )
    # "model" only exists on TokenTransaction -- can't group an outcome
    # query by it, so this must be reported, not raise a SQL error.
    assert any(e["code"] == "dimension_unsupported_for_mixed_metrics" for e in result.errors)


def test_workspace_isolation_same_account_name_two_workspaces():
    db = _session()
    acme_a = _account(db, name="Acme", external_id="a1", workspace_id="WS-A")
    acme_b = _account(db, name="Acme", external_id="a2", workspace_id="WS-B")
    item_a = _work_item(db, external_id="wi-a", account=acme_a, workspace_id="WS-A")
    item_b = _work_item(db, external_id="wi-b", account=acme_b, workspace_id="WS-B")
    _tx(db, cost_usd=10.0, work_item=item_a, workspace_id="WS-A")
    _tx(db, cost_usd=99999.0, work_item=item_b, workspace_id="WS-B")
    db.commit()

    result = run_metrics_query(db, "WS-A", metrics=["ai_spend"], filters={"account": "Acme"})
    assert not result.errors
    assert result.rows[0]["ai_spend"] == 10.0


def test_comparison_previous_period_returns_current_previous_and_difference():
    db = _session()
    now = datetime.utcnow()
    _tx(db, cost_usd=100.0, timestamp=now - timedelta(days=2))
    _tx(db, cost_usd=40.0, timestamp=now - timedelta(days=35))
    db.commit()

    result = run_metrics_query(
        db, "WS1", metrics=["ai_spend"],
        timeframe={"start": now - timedelta(days=30), "end": now},
        compare_to="previous_period",
    )
    assert result.comparison is not None
    row = result.comparison["rows"][0]
    assert row["ai_spend"]["current"] == 100.0
    assert row["ai_spend"]["previous"] == 40.0
    assert row["ai_spend"]["difference"] == 60.0


def test_department_filter_matches_workspace_prefixed_department():
    db = _session()
    _tx(db, cost_usd=5.0, department="WS1:Engineering")
    _tx(db, cost_usd=8.0, department="WS1:Sales")
    db.commit()

    result = run_metrics_query(db, "WS1", metrics=["ai_spend"], filters={"department": "Sales"})
    assert result.rows[0]["ai_spend"] == 8.0


def test_freshness_omitted_when_no_outcome_metrics_requested():
    db = _session()
    _tx(db, cost_usd=1.0)
    db.commit()

    result = run_metrics_query(db, "WS1", metrics=["ai_spend"])
    assert result.freshness is None


def test_freshness_reports_oldest_sync_and_stale_flag():
    db = _session()
    won = _work_item(db, external_id="opp-1")
    _outcome(db, won, outcome_success=True, is_closed=True, outcome_value=1000.0)
    db.commit()
    outcome_row = db.query(WorkItemOutcome).filter(WorkItemOutcome.work_item_id == won.id).first()
    outcome_row.last_synced_at = datetime.utcnow() - timedelta(days=2)
    db.commit()

    result = run_metrics_query(db, "WS1", metrics=["won_count"])
    assert result.freshness is not None
    assert result.freshness["stale"] is True


def test_freshness_not_stale_for_recent_sync():
    db = _session()
    won = _work_item(db, external_id="opp-1")
    _outcome(db, won, outcome_success=True, is_closed=True, outcome_value=1000.0)
    db.commit()

    result = run_metrics_query(db, "WS1", metrics=["won_count"])
    assert result.freshness["stale"] is False


def test_agent_filter_scopes_active_agents_metric():
    db = _session()
    agent = RegisteredAgent(name="Support Summarizer", department="WS1:Support", source_platform="Salesforce", permissions="read")
    db.add(agent)
    db.commit()
    db.refresh(agent)
    _tx(db, cost_usd=1.0, agent=agent)
    _tx(db, cost_usd=2.0, agent=None)
    db.commit()

    result = run_metrics_query(db, "WS1", metrics=["ai_spend", "active_agents"], filters={"agent": "Support"})
    assert result.rows[0]["ai_spend"] == 1.0
    assert result.rows[0]["active_agents"] == 1
