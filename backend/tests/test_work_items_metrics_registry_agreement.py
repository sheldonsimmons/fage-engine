"""
tests/test_work_items_metrics_registry_agreement.py — locks in that
Business Profile (account_profile()) and WorkItem Profile
(_work_item_json()) agree with the metrics registry on the core spend
figure, even though (like Reports) they can't fully migrate onto
run_metrics_query() outright:

- account_profile() computes 9+ things the registry has no equivalent
  for yet (per-agent/per-person spend ranking, business-function/journey/
  stage breakdowns) and is already deliberately-optimized SQL aggregation,
  not a naive Python loop -- there's no performance win from forcing it
  onto the registry, only a consistency question, which this test answers
  directly instead.
- _work_item_json() similarly returns fields (last_activity_at, distinct
  model tiers/platforms touched, agent/user rosters) the registry doesn't
  track.

Investigating this surfaced a real question worth recording: a WorkItem
merge (POST .../merge) REASSIGNS every TokenTransaction.work_item_id to
the target (api/routes_work_items.py's merge_work_items(), a real UPDATE,
not a duplication) -- so an archived, merged-away WorkItem always has
zero transactions of its own afterward. That means the registry's account/
work_item filters (which join through WorkItem but don't explicitly
exclude merged_into_work_item_id) do NOT double-count spend, despite
account_profile() applying that exclusion explicitly when building its
own work-item-id list. Confirmed by tracing merge_work_items(), not
assumed -- worth knowing if either code path is touched again.
"""
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.routes_work_items import _work_item_json, account_profile
from core.metrics_query import run_metrics_query
from database.db import Base
from database.models import TokenTransaction, WorkAccount, WorkItem


def _session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def test_account_profile_ai_investment_matches_the_registry():
    db = _session()
    account = WorkAccount(external_id="ACC-1", name="Acme Corp", workspace_id="WS-BP")
    db.add(account)
    db.commit()
    db.refresh(account)

    wi1 = WorkItem(external_id="WI-1", name="Renewal", account_id=account.id, workspace_id="WS-BP")
    wi2 = WorkItem(external_id="WI-2", name="Support Case", account_id=account.id, workspace_id="WS-BP")
    db.add_all([wi1, wi2])
    db.commit()
    db.refresh(wi1)
    db.refresh(wi2)

    now = datetime.utcnow()
    db.add_all([
        TokenTransaction(
            workspace_id="WS-BP", department="Sales", model_tier="Advisor",
            work_item_id=wi1.id, input_tokens=100, output_tokens=50, cost_usd=1.75,
            timestamp=now - timedelta(hours=2), is_simulation=False, usage_source="estimated",
            routing_reason="COMPLEX",
        ),
        TokenTransaction(
            workspace_id="WS-BP", department="Support", model_tier="Scout",
            work_item_id=wi2.id, input_tokens=40, output_tokens=10, cost_usd=0.02,
            timestamp=now - timedelta(hours=1), is_simulation=False, usage_source="estimated",
            routing_reason="ROUTINE",
        ),
    ])
    db.commit()

    profile = account_profile(
        identifier="ACC-1", workspace_id="WS-BP", date_from=None, date_to=None, days=90, db=db,
    )

    registry = run_metrics_query(
        db, "WS-BP", metrics=["ai_spend"], filters={"account": "ACC-1"},
        timeframe={"start": now - timedelta(days=90), "end": now + timedelta(minutes=1)},
    )
    registry_spend = round(registry.rows[0]["ai_spend"], 6) if registry.rows else 0.0

    assert profile["kpis"]["ai_investment_usd"] == registry_spend
    assert profile["kpis"]["ai_investment_usd"] == 1.77  # 1.75 + 0.02, sanity check on the real number


def test_work_item_json_spend_matches_the_registry():
    db = _session()
    wi = WorkItem(external_id="WI-SOLO", name="Solo Matter", workspace_id="WS-WIP")
    db.add(wi)
    db.commit()
    db.refresh(wi)

    now = datetime.utcnow()
    db.add_all([
        TokenTransaction(
            workspace_id="WS-WIP", department="Legal", model_tier="Advisor",
            work_item_id=wi.id, input_tokens=200, output_tokens=80, cost_usd=3.10,
            timestamp=now - timedelta(hours=3), is_simulation=False, usage_source="estimated",
            routing_reason="COMPLEX",
        ),
    ])
    db.commit()

    result = _work_item_json(wi, db, include_stats=True)

    registry = run_metrics_query(
        db, "WS-WIP", metrics=["ai_spend"], filters={"work_item": "WI-SOLO"},
    )
    registry_spend = round(registry.rows[0]["ai_spend"], 6) if registry.rows else 0.0

    assert result["spend_usd"] == registry_spend
    assert result["spend_usd"] == 3.10
