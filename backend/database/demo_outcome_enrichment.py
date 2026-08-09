"""
database/demo_outcome_enrichment.py — runnable, readable proof of the
AI Event -> Work Item -> Salesforce Outcome -> Ask CostPilot chain, using
fake data (no real Salesforce connection required).

Uses its own isolated SQLite file by default so it can't disturb your real
local dashboard data. Point it at your actual dev DB instead (e.g. to see
the outcome show up in the running app's UI) by setting DATABASE_URL first:

    DATABASE_URL=sqlite:///./fage.db python database/demo_outcome_enrichment.py

Run from the backend folder:
    cd backend
    python database/demo_outcome_enrichment.py
"""
import asyncio
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite:///./demo_outcome_enrichment.db")
os.environ.setdefault("CONNECTION_ENCRYPTION_KEY", "WMMH4wtJGK5t1mAXvaUT8Kgfk-sglB_izB-izrf7FFQ=")

from datetime import datetime, timedelta

from database.db import engine, SessionLocal
from database.models import (
    Base, IntegrationConnection, TokenTransaction, WorkItem,
    WorkItemOutcome, WorkItemOutcomeEvent,
)
from api.routes_connections import sync_outcomes, _encrypt
from api.routes_work_items import project_activity_reporting
from api.routes_efficiency import (
    _ask_narration_causal_claims,
    _ask_narration_unverified_numbers,
)

WORKSPACE_ID = "DEMO-ACME"


def line(title=""):
    print("\n" + ("─" * 70))
    if title:
        print(title)
        print("─" * 70)


def reset_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def seed(db):
    connection = IntegrationConnection(
        workspace_id=WORKSPACE_ID,
        platform="salesforce",
        display_name="Acme Salesforce (demo)",
        status="connected",
        instance_url="https://acme.my.salesforce.com",
        access_token_encrypted=_encrypt("fake-demo-token"),
    )
    db.add(connection)

    work_item = WorkItem(
        external_id="PROJECT-ACME-EXPANSION",
        name="Acme Expansion",
        context_type="opportunity",
        context_template="salesforce_opportunity",
        source_platform="Salesforce",
        source_record_type="Opportunity",
        source_record_id="006TEST123456789",
        workspace_id=WORKSPACE_ID,
        status="active",
    )
    db.add(work_item)
    db.commit()
    db.refresh(connection)
    db.refresh(work_item)

    now = datetime.utcnow()
    n, total_cost = 83, 196.0
    for i in range(n):
        db.add(TokenTransaction(
            department=f"{WORKSPACE_ID}:Sales",
            workspace_id=WORKSPACE_ID,
            source_platform="Salesforce",
            work_item_id=work_item.id,
            origin_record_id=work_item.source_record_id,
            origin_record_type="Opportunity",
            model_tier="Scout",
            model_name="claude-3-5-haiku",
            input_tokens=150,
            output_tokens=60,
            tokens_saved=25,
            cost_usd=round(total_cost / n, 6),
            timestamp=now - timedelta(hours=i),
        ))
    db.commit()
    return connection, work_item


def main():
    print("CostPilot Universal outcome enrichment — live demo (fake data)")
    reset_db()
    db = SessionLocal()
    connection, work_item = seed(db)

    line("STEP 1 — Before any outcome sync")
    print(f"Work item '{work_item.name}' has {work_item.token_transactions and len(work_item.token_transactions) or 0}"
          f" AI events recorded, no outcome yet.")
    print("CostPilot only knows: AI activity happened, pointed at Salesforce Opportunity "
          f"{work_item.source_record_id}. Nothing about what happened to that deal.")

    line("STEP 2 — Simulated Salesforce response")
    fake_opportunity_record = {
        "Id": work_item.source_record_id,
        "StageName": "Closed Won",
        "IsClosed": True,
        "IsWon": True,
        "Amount": 600000.0,
        "CloseDate": "2026-08-15",
        "OwnerId": "005OWNER123456789",
        "AccountId": "001ACME123456789",
        "LastModifiedDate": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000+0000"),
    }
    print("Standing in for a real Salesforce API response (no real connection used):")
    for key, value in fake_opportunity_record.items():
        print(f"  {key}: {value}")

    import api.routes_connections as routes_connections

    async def fake_salesforce_try_query(_item, _query):
        return [fake_opportunity_record], None

    routes_connections._salesforce_try_query = fake_salesforce_try_query

    line("STEP 3 — Running the outcome sync")
    result = asyncio.run(sync_outcomes(connection.id, db=db))
    print(f"Sync result: {result}")

    outcome = db.query(WorkItemOutcome).filter_by(work_item_id=work_item.id).first()
    history = db.query(WorkItemOutcomeEvent).filter_by(work_item_id=work_item.id).all()
    print(f"\nWorkItemOutcome now stored: status={outcome.outcome_status!r}, "
          f"value=${outcome.outcome_value:,.2f}, won={outcome.outcome_success}, "
          f"freshness window starts now (last_synced_at={outcome.last_synced_at})")
    print(f"WorkItemOutcomeEvent history rows: {len(history)} (append-only, grows on future changes)")

    line("STEP 4 — What the reporting layer now shows")
    report = project_activity_reporting(
        workspace_id=WORKSPACE_ID,
        date_from=datetime.utcnow() - timedelta(days=7),
        date_to=datetime.utcnow() + timedelta(days=1),
        project_id=None, user_external_id=None, agent_id=None, account_id=None,
        source_platform=None, record_type=None, model_tier=None, charged_unit=None,
        business_purpose=None, provider=None, activity_limit=500, days=30,
        db=db,
    )
    [row] = [r for r in report["project_breakdown"] if r["id"] == work_item.external_id]
    print(f"Project: {row['label']}")
    print(f"  AI requests: {row['request_count']}")
    print(f"  AI spend:    ${row['spend_usd']:,.2f}")
    print(f"  Outcome:     {row['outcome_status']} (success={row['outcome_success']})")
    print(f"  Deal value:  ${row['outcome_value']:,.2f}")
    print(f"  Freshness:   {row['outcome_freshness']}")

    line("STEP 5 — Asking the question directly through Ask CostPilot")
    os.environ["ASK_COSTPILOT_NARRATION_ENABLED"] = "false"  # no OpenAI key in this demo
    from api.routes_efficiency import ask_costpilot
    from api.routes_efficiency import AskCostPilotRequest

    for question in (
        "Which won opportunities had the highest AI spend?",
        "How much AI spend was associated with lost opportunities?",
    ):
        response = ask_costpilot(
            AskCostPilotRequest(question=question, workspace_id=WORKSPACE_ID),
            db=db,
        )
        print(f"\nQ: {question}")
        print(f"A: {response['answer']}")
        for item in response.get("evidence") or []:
            outcome = item.get("outcome")
            if outcome:
                print(f"   - {item['label']}: {item['value']} "
                      f"(outcome: {outcome['status']}, ${outcome['value']:,.0f})")

    line("STEP 6 — The safety rule: association, not causation")
    good_answer = (
        f"This ${row['outcome_value']:,.0f} Closed Won opportunity had "
        f"${row['spend_usd']:,.2f} of tracked AI activity across {row['request_count']} interactions."
    )
    bad_answer = f"AI generated ${row['outcome_value']:,.0f} in revenue for this deal."

    facts = {"summary": report["summary"], "evidence": [row]}
    for label, answer in (("SAFE phrasing", good_answer), ("UNSAFE phrasing", bad_answer)):
        unverified = _ask_narration_unverified_numbers(facts, answer)
        causal = _ask_narration_causal_claims(answer)
        verdict = "ACCEPTED" if not unverified and not causal else "REJECTED"
        print(f"\n{label}: \"{answer}\"")
        print(f"  -> {verdict}"
              + (f" (unverified numbers: {unverified})" if unverified else "")
              + (f" (causal claim: {causal})" if causal else ""))

    line()
    print("Demo complete. This used an isolated SQLite file "
          f"({os.environ['DATABASE_URL']}) — your real dev database was not touched.")


if __name__ == "__main__":
    main()
