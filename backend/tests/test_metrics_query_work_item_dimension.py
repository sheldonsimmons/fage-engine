"""
core/metrics_query.py's DIMENSIONS registry was missing "work_item" --
needed for the AI Activity Explorer's View By / Break Down By pivot
(Reporting Tableau-style Explorer, Phase 1). This test locks in the new
dimension before the Explorer relies on it.
"""
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.db import Base
from database.models import TokenTransaction, WorkAccount, WorkItem
from core.metrics_query import run_metrics_query


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def test_work_item_dimension_groups_by_linked_work_item():
    db = _session()
    account = WorkAccount(workspace_id="WS-1", name="Acme", external_id="ACC-1")
    db.add(account); db.flush()
    item = WorkItem(
        workspace_id="WS-1", account_id=account.id, context_type="opportunity",
        external_id="OPP-1", name="Acme Expansion",
    )
    db.add(item); db.flush()
    db.add(TokenTransaction(
        department="WS-1:Sales", workspace_id="WS-1", work_item_id=item.id,
        model_tier="Scout", input_tokens=100, output_tokens=50, cost_usd=3.0, timestamp=datetime.utcnow(),
    ))
    db.commit()

    result = run_metrics_query(db, "WS-1", metrics=["ai_spend"], dimensions=["work_item"], timeframe={})
    assert not result.errors
    assert len(result.rows) == 1
    assert result.rows[0]["dimensions"]["work_item"] == "Acme Expansion"
    assert result.rows[0]["ai_spend"] == 3.0


def test_work_item_dimension_falls_back_to_unassigned_without_work_item():
    db = _session()
    db.add(TokenTransaction(
        department="WS-1:Sales", workspace_id="WS-1", work_item_id=None,
        model_tier="Scout", input_tokens=100, output_tokens=50, cost_usd=2.0, timestamp=datetime.utcnow(),
    ))
    db.commit()

    result = run_metrics_query(db, "WS-1", metrics=["ai_spend"], dimensions=["work_item"], timeframe={})
    assert not result.errors
    assert result.rows[0]["dimensions"]["work_item"] == "Unassigned work item"
    assert result.rows[0]["ai_spend"] == 2.0


def test_work_item_dimension_respects_filters_and_timeframe():
    db = _session()
    account = WorkAccount(workspace_id="WS-1", name="Acme", external_id="ACC-2")
    db.add(account); db.flush()
    item = WorkItem(
        workspace_id="WS-1", account_id=account.id, context_type="opportunity",
        external_id="OPP-2", name="Globex Renewal",
    )
    db.add(item); db.flush()
    db.add(TokenTransaction(
        department="WS-1:Support", workspace_id="WS-1", work_item_id=item.id,
        model_tier="Scout", input_tokens=100, output_tokens=50, cost_usd=5.0, timestamp=datetime.utcnow(),
    ))
    db.commit()

    result = run_metrics_query(
        db, "WS-1", metrics=["ai_spend"], dimensions=["work_item"],
        filters={"department": "Support"}, timeframe={},
    )
    assert not result.errors
    assert result.rows[0]["dimensions"]["work_item"] == "Globex Renewal"

    result_no_match = run_metrics_query(
        db, "WS-1", metrics=["ai_spend"], dimensions=["work_item"],
        filters={"department": "Sales"}, timeframe={},
    )
    assert result_no_match.rows == []


def test_rows_expose_dimension_ids_alongside_labels():
    db = _session()
    account = WorkAccount(workspace_id="WS-1", name="Acme", external_id="ACC-1")
    db.add(account); db.flush()
    item = WorkItem(
        workspace_id="WS-1", account_id=account.id, context_type="opportunity",
        external_id="OPP-1", name="Acme Expansion",
    )
    db.add(item); db.flush()
    db.add(TokenTransaction(
        department="WS-1:Sales", workspace_id="WS-1", work_item_id=item.id,
        model_tier="Scout", input_tokens=100, output_tokens=50, cost_usd=3.0, timestamp=datetime.utcnow(),
    ))
    db.commit()

    result = run_metrics_query(db, "WS-1", metrics=["ai_spend"], dimensions=["work_item"], timeframe={})
    row = result.rows[0]
    assert row["dimensions"]["work_item"] == "Acme Expansion"
    assert row["dimension_ids"]["work_item"] == "OPP-1"


def test_work_item_filter_matches_exact_external_id():
    db = _session()
    account = WorkAccount(workspace_id="WS-1", name="Acme", external_id="ACC-1")
    db.add(account); db.flush()
    item_a = WorkItem(workspace_id="WS-1", account_id=account.id, context_type="opportunity", external_id="OPP-1", name="Deal A")
    item_b = WorkItem(workspace_id="WS-1", account_id=account.id, context_type="opportunity", external_id="OPP-2", name="Deal B")
    db.add_all([item_a, item_b]); db.flush()
    db.add(TokenTransaction(
        department="WS-1:Sales", workspace_id="WS-1", work_item_id=item_a.id,
        model_tier="Scout", input_tokens=100, output_tokens=50, cost_usd=4.0, timestamp=datetime.utcnow(),
    ))
    db.add(TokenTransaction(
        department="WS-1:Sales", workspace_id="WS-1", work_item_id=item_b.id,
        model_tier="Scout", input_tokens=100, output_tokens=50, cost_usd=9.0, timestamp=datetime.utcnow(),
    ))
    db.commit()

    result = run_metrics_query(db, "WS-1", metrics=["ai_spend"], filters={"work_item": "OPP-1"}, timeframe={})
    assert not result.errors
    assert len(result.rows) == 1
    assert result.rows[0]["ai_spend"] == 4.0
