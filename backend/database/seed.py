"""
seed.py — Populates the FAGE SQLite database with realistic mock CRM data.

Run once before starting the server:
    cd backend
    python database/seed.py

Safe to re-run — clears and rebuilds all tables each time.
"""

import sys
import os

# Allow running from anywhere inside the project
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta
from database.db import engine, SessionLocal
from database import models


def seed():
    # Create all tables if they don't exist yet
    models.Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    print("🗑  Clearing existing data...")
    db.query(models.AuditEvent).delete()
    db.query(models.TokenTransaction).delete()
    db.query(models.DepartmentBudget).delete()
    db.query(models.RegisteredAgent).delete()
    db.query(models.CRMRecord).delete()
    db.query(models.Ticket).delete()
    db.query(models.Customer).delete()
    db.commit()

    # ── Customers ──────────────────────────────────────────────────────────────
    customers = [
        models.Customer(name="Aria Fontaine",    email="aria.fontaine@acmecorp.com",       tier="enterprise", department="Support"),
        models.Customer(name="Marcus Webb",       email="marcus.webb@globaltec.io",          tier="pro",        department="Sales"),
        models.Customer(name="Lena Hargrove",     email="lena.hargrove@nexusltd.com",        tier="enterprise", department="Marketing"),
        models.Customer(name="Derek Osei",        email="derek.osei@vertexco.net",           tier="free",       department="Operations"),
        models.Customer(name="Priya Nair",        email="priya.nair@cloudbase.ai",           tier="pro",        department="Support"),
        models.Customer(name="Theo Blackwell",    email="theo.blackwell@pinnaclegroup.com",  tier="enterprise", department="Sales"),
        models.Customer(name="Camille Durand",    email="camille.durand@stratospheric.io",   tier="pro",        department="Marketing"),
        models.Customer(name="Sam Okafor",        email="sam.okafor@ironwalltech.com",       tier="free",       department="Operations"),
        models.Customer(name="Nadia Volkov",      email="nadia.volkov@spectrumfin.com",      tier="enterprise", department="Support"),
        models.Customer(name="Jordan Liu",        email="jordan.liu@blueridgeventures.com",  tier="pro",        department="Sales"),
    ]
    db.add_all(customers)
    db.commit()

    # ── Support Tickets ────────────────────────────────────────────────────────
    tickets = [
        models.Ticket(
            customer_id=1,
            subject="Cannot access enterprise dashboard — production down",
            body=(
                "Our entire team has been locked out of the dashboard since 9am. "
                "This is a critical production issue affecting 200+ users. "
                "We need immediate escalation to your senior engineering team. "
                "Our SLA guarantees 99.9% uptime and we are now in breach. "
                "Please treat this as a P0 incident."
            ),
            status="open",
            created_at=datetime.utcnow() - timedelta(hours=2),
        ),
        models.Ticket(
            customer_id=5,
            subject="API rate limit question",
            body="Hi, what is the rate limit for the v2 API on a Pro plan? Thanks.",
            status="open",
            created_at=datetime.utcnow() - timedelta(hours=5),
        ),
        models.Ticket(
            customer_id=9,
            subject="Billing discrepancy — urgent compliance review needed",
            body=(
                "We were charged $4,200 this month but our enterprise contract "
                "caps monthly usage at $2,000. This is a contractual breach. "
                "I need this escalated immediately to your billing and legal compliance "
                "team. We will be filing a formal dispute under clause 8.3 of our MSA "
                "if this is not resolved within 24 hours."
            ),
            status="in_progress",
            created_at=datetime.utcnow() - timedelta(hours=1),
        ),
        models.Ticket(
            customer_id=2,
            subject="Demo request for Q3",
            body="We'd love to schedule a product demo for next week if possible.",
            status="open",
            created_at=datetime.utcnow() - timedelta(days=1),
        ),
        models.Ticket(
            customer_id=6,
            subject="Enterprise contract renewal — updated SLA terms required",
            body=(
                "We are approaching our August renewal date and need to negotiate "
                "updated SLA terms including data residency requirements (EU-only), "
                "volume pricing adjustments, GDPR data processing addendum updates, "
                "and a revised escalation matrix for P0 incidents."
            ),
            status="open",
            created_at=datetime.utcnow() - timedelta(hours=6),
        ),
    ]
    db.add_all(tickets)
    db.commit()

    # ── CRM Records ────────────────────────────────────────────────────────────
    crm_records = [
        models.CRMRecord(customer_id=1, field_key="contract_value",       field_value="$48,000/yr"),
        models.CRMRecord(customer_id=1, field_key="renewal_date",         field_value="2025-09-01"),
        models.CRMRecord(customer_id=1, field_key="account_health",       field_value="at_risk"),
        models.CRMRecord(customer_id=1, field_key="csm_owner",            field_value="Rachel Torres"),
        models.CRMRecord(customer_id=5, field_key="contract_value",       field_value="$12,000/yr"),
        models.CRMRecord(customer_id=5, field_key="account_health",       field_value="healthy"),
        models.CRMRecord(customer_id=9, field_key="contract_value",       field_value="$96,000/yr"),
        models.CRMRecord(customer_id=9, field_key="billing_dispute_open", field_value="true"),
        models.CRMRecord(customer_id=9, field_key="account_health",       field_value="critical"),
        models.CRMRecord(customer_id=9, field_key="csm_owner",            field_value="James Okoye"),
        models.CRMRecord(customer_id=6, field_key="contract_value",       field_value="$72,000/yr"),
        models.CRMRecord(customer_id=6, field_key="renewal_date",         field_value="2025-08-15"),
    ]
    db.add_all(crm_records)
    db.commit()

    # ── Registered AI Agents ───────────────────────────────────────────────────
    agents = [
        models.RegisteredAgent(name="SupportBot-Alpha",   department="Support",    permissions="read,write",        target_table="tickets",            status="idle"),
        models.RegisteredAgent(name="SupportBot-Beta",    department="Support",    permissions="read,write",        target_table="tickets",            status="idle"),
        models.RegisteredAgent(name="SalesEnrich-1",      department="Sales",      permissions="read,write",        target_table="crm_records",        status="idle"),
        models.RegisteredAgent(name="MarketingMailer-1",  department="Marketing",  permissions="read",              target_table="customers",          status="idle"),
        models.RegisteredAgent(name="OpsLogger-1",        department="Operations", permissions="read,write,delete", target_table="crm_records",        status="idle"),
        models.RegisteredAgent(name="BillingAuditor-1",   department="Support",    permissions="read",              target_table="token_transactions",  status="idle"),
    ]
    db.add_all(agents)
    db.commit()

    # ── Department Budgets ─────────────────────────────────────────────────────
    period_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    budgets = [
        # Support is at 71% — approaching warn threshold (80%)
        models.DepartmentBudget(department="Support",    monthly_cap_usd=200.00, current_spend_usd=142.50, period_start=period_start, throttled=False),
        # Sales is comfortable at 29%
        models.DepartmentBudget(department="Sales",      monthly_cap_usd=300.00, current_spend_usd=87.20,  period_start=period_start, throttled=False),
        # Marketing is at 99.6% — nearly throttled
        models.DepartmentBudget(department="Marketing",  monthly_cap_usd=250.00, current_spend_usd=249.10, period_start=period_start, throttled=False),
        # Operations is well under budget at 15%
        models.DepartmentBudget(department="Operations", monthly_cap_usd=150.00, current_spend_usd=23.80,  period_start=period_start, throttled=False),
    ]
    db.add_all(budgets)
    db.commit()

    # ── Historical Token Transactions ──────────────────────────────────────────
    now = datetime.utcnow()
    transactions = [
        models.TokenTransaction(department="Support",    agent_id=1, model_tier="micro",    input_tokens=450,  output_tokens=120, cost_usd=0.000139, routing_reason="ROUTINE",  was_pruned=True,  tokens_saved=380,  timestamp=now - timedelta(hours=3)),
        models.TokenTransaction(department="Support",    agent_id=1, model_tier="flagship", input_tokens=2100, output_tokens=680, cost_usd=0.016500, routing_reason="COMPLEX",  was_pruned=True,  tokens_saved=1200, timestamp=now - timedelta(hours=2)),
        models.TokenTransaction(department="Sales",      agent_id=3, model_tier="micro",    input_tokens=320,  output_tokens=90,  cost_usd=0.000102, routing_reason="ROUTINE",  was_pruned=False, tokens_saved=0,    timestamp=now - timedelta(hours=5)),
        models.TokenTransaction(department="Marketing",  agent_id=4, model_tier="flagship", input_tokens=3400, output_tokens=1200,cost_usd=0.028200, routing_reason="COMPLEX",  was_pruned=True,  tokens_saved=2800, timestamp=now - timedelta(hours=1)),
        models.TokenTransaction(department="Operations", agent_id=5, model_tier="micro",    input_tokens=180,  output_tokens=60,  cost_usd=0.000063, routing_reason="ROUTINE",  was_pruned=False, tokens_saved=0,    timestamp=now - timedelta(hours=4)),
        models.TokenTransaction(department="Support",    agent_id=2, model_tier="micro",    input_tokens=290,  output_tokens=85,  cost_usd=0.000095, routing_reason="THROTTLED", was_pruned=True, tokens_saved=210,  timestamp=now - timedelta(minutes=45)),
    ]
    db.add_all(transactions)
    db.commit()

    db.close()

    print("\n✅ FAGE database seeded successfully.")
    print(f"   → {len(customers)} customers")
    print(f"   → {len(tickets)} support tickets")
    print(f"   → {len(crm_records)} CRM records")
    print(f"   → {len(agents)} registered agents")
    print(f"   → {len(budgets)} department budgets")
    print(f"   → {len(transactions)} historical token transactions")
    print("\n   Database file: backend/fage.db")
    print("   Start the server: uvicorn main:app --reload --port 8000\n")


if __name__ == "__main__":
    seed()
