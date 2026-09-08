"""
core/metrics_query.py's DIMENSIONS registry was missing "context_type"
("Kind of Work") and run_metrics_query() had no way to search within a
pivot -- both needed for the AI Activity Explorer's "break down by kind
of work" and search requests. This test locks both in.
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


def _seed(db, workspace_id="WS-1"):
    account = WorkAccount(workspace_id=workspace_id, name="Acme", external_id="ACC-1")
    db.add(account); db.flush()
    opp = WorkItem(workspace_id=workspace_id, account_id=account.id, context_type="opportunity", external_id="OPP-1", name="Acme Expansion")
    claim = WorkItem(workspace_id=workspace_id, account_id=account.id, context_type="claim", external_id="CLAIM-1", name="Claim 84721")
    ticket = WorkItem(workspace_id=workspace_id, account_id=account.id, context_type="ticket", external_id="TCK-1", name="Refund escalation")
    db.add_all([opp, claim, ticket]); db.flush()
    db.add(TokenTransaction(department=f"{workspace_id}:Sales", workspace_id=workspace_id, work_item_id=opp.id,
        model_tier="Scout", input_tokens=100, output_tokens=50, cost_usd=6.0, timestamp=datetime.utcnow()))
    db.add(TokenTransaction(department=f"{workspace_id}:Support", workspace_id=workspace_id, work_item_id=claim.id,
        model_tier="Scout", input_tokens=100, output_tokens=50, cost_usd=3.0, timestamp=datetime.utcnow()))
    db.add(TokenTransaction(department=f"{workspace_id}:Support", workspace_id=workspace_id, work_item_id=ticket.id,
        model_tier="Scout", input_tokens=100, output_tokens=50, cost_usd=1.0, timestamp=datetime.utcnow()))
    db.add(TokenTransaction(department=f"{workspace_id}:Support", workspace_id=workspace_id, work_item_id=None,
        model_tier="Scout", input_tokens=100, output_tokens=50, cost_usd=0.5, timestamp=datetime.utcnow()))
    db.commit()
    return opp, claim, ticket


def test_context_type_dimension_breaks_down_by_kind_of_work():
    db = _session()
    _seed(db)

    result = run_metrics_query(db, "WS-1", metrics=["ai_spend"], dimensions=["context_type"], timeframe={})
    assert not result.errors
    by_kind = {row["dimensions"]["context_type"]: row["ai_spend"] for row in result.rows}
    assert by_kind == {"opportunity": 6.0, "claim": 3.0, "ticket": 1.0, "Unassigned": 0.5}


def test_context_type_filter_narrows_another_dimensions_breakdown():
    db = _session()
    _seed(db)

    result = run_metrics_query(
        db, "WS-1", metrics=["ai_spend"], dimensions=["work_item"],
        filters={"context_type": "claim"}, timeframe={},
    )
    assert not result.errors
    assert len(result.rows) == 1
    assert result.rows[0]["dimensions"]["work_item"] == "Claim 84721"


def test_search_filter_matches_the_displayed_label():
    db = _session()
    _seed(db)

    result = run_metrics_query(
        db, "WS-1", metrics=["ai_spend"], dimensions=["work_item"],
        filters={"search": "claim"}, timeframe={},
    )
    assert not result.errors
    assert len(result.rows) == 1
    assert result.rows[0]["dimensions"]["work_item"] == "Claim 84721"


def test_search_filter_is_case_insensitive_and_matches_substrings():
    db = _session()
    _seed(db)

    result = run_metrics_query(
        db, "WS-1", metrics=["ai_spend"], dimensions=["work_item"],
        filters={"search": "ESCALATION"}, timeframe={},
    )
    assert not result.errors
    assert len(result.rows) == 1
    assert result.rows[0]["dimensions"]["work_item"] == "Refund escalation"


def test_search_filter_with_no_matches_returns_empty_not_an_error():
    db = _session()
    _seed(db)

    result = run_metrics_query(
        db, "WS-1", metrics=["ai_spend"], dimensions=["work_item"],
        filters={"search": "nonexistent-record-xyz"}, timeframe={},
    )
    assert not result.errors
    assert result.rows == []
