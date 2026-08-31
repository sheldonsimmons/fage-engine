"""
Ask CostPilot architecture audit Recommendation #3: generalize the
sample-size-awareness that used to live only inside
compute_cost_per_outcome() (core/metrics_query.py) so any metric with a
MetricDef.sample_size_metric gets the same evidence-label treatment
automatically from run_metrics_query() -- not just that one hand-rolled
case. These tests lock in won_value/won_count as the first wired-up pair.
"""
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.db import Base
from database.models import WorkAccount, WorkItem, WorkItemOutcome
from core.metrics_query import run_metrics_query


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


_seq = iter(range(1_000_000))


def _won_opportunity(db, workspace_id, value):
    n = next(_seq)
    account = WorkAccount(workspace_id=workspace_id, name="Acme", external_id=f"ACC-{n}")
    db.add(account); db.flush()
    item = WorkItem(
        workspace_id=workspace_id, account_id=account.id, context_type="opportunity",
        external_id=f"OPP-{n}", name=f"Opportunity {n}",
    )
    db.add(item); db.flush()
    db.add(WorkItemOutcome(
        work_item_id=item.id, workspace_id=workspace_id, source_system="salesforce",
        source_object="Opportunity", external_id=item.external_id,
        outcome_success=True, is_closed=True, outcome_value=value,
        last_synced_at=datetime.utcnow(),
    ))


def test_won_value_row_carries_early_signal_evidence_below_meaningful_threshold():
    db = _session()
    _won_opportunity(db, "WS-1", 100.0)
    db.commit()

    result = run_metrics_query(db, "WS-1", metrics=["won_value"], timeframe={})
    assert not result.errors
    row = result.rows[0]
    assert row["won_value"] == 100.0
    # won_count (the sample metric) must NOT leak into the row itself --
    # only what was requested, plus the evidence block.
    assert "won_count" not in row
    assert row["evidence"]["won_value"]["sample_size"] == 1
    assert row["evidence"]["won_value"]["evidence_label"] == "early_signal"


def test_won_value_row_carries_executive_eligible_evidence_above_threshold():
    db = _session()
    for i in range(50):
        _won_opportunity(db, "WS-1", 10.0 + i)
    db.commit()

    result = run_metrics_query(db, "WS-1", metrics=["won_value"], timeframe={})
    assert not result.errors
    row = result.rows[0]
    assert row["evidence"]["won_value"]["sample_size"] == 50
    assert row["evidence"]["won_value"]["evidence_label"] == "executive_eligible"


def test_metric_with_no_sample_size_metric_carries_no_evidence_block():
    db = _session()
    _won_opportunity(db, "WS-1", 100.0)
    db.commit()

    # won_count is itself a plain count -- no sample_size_metric configured.
    result = run_metrics_query(db, "WS-1", metrics=["won_count"], timeframe={})
    assert not result.errors
    assert "evidence" not in result.rows[0]


def test_evidence_is_computed_even_when_sample_metric_not_explicitly_requested():
    # The caller only asked for won_value -- won_count must still be
    # fetched under the hood to compute the evidence label, without
    # becoming a reported metric (see test above: "won_count" not in row).
    db = _session()
    _won_opportunity(db, "WS-1", 100.0)
    _won_opportunity(db, "WS-1", 200.0)
    db.commit()

    result = run_metrics_query(db, "WS-1", metrics=["won_value"], timeframe={})
    assert result.metrics == ["won_value"]
    assert result.rows[0]["evidence"]["won_value"]["sample_size"] == 2
