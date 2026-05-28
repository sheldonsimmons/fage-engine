"""
populate_demo.py — Load the CostPilot dashboard with impressive demo data for screenshots.

Does NOT wipe departments, agents, or sensitive terms.
Clears only transactions and audit events, then rebuilds with rich data.

Run from the backend folder:
    cd backend
    python database/populate_demo.py
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta
import random
from database.db import engine, SessionLocal
from database import models

random.seed(42)

def populate():
    models.Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    now = datetime.utcnow()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    period_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    # ── Clear transactions and audit events only ───────────────────────────────
    print("Clearing transactions and audit events...")
    db.query(models.AuditEvent).delete()
    db.query(models.TokenTransaction).delete()
    db.commit()

    # ── Reset / upsert department budgets ──────────────────────────────────────
    print("Setting department budgets...")
    budget_data = [
        dict(department="Support",    monthly_cap_usd=500.00,  current_spend_usd=342.80,  throttled=False, throttle_tier=2),
        dict(department="Sales",      monthly_cap_usd=750.00,  current_spend_usd=218.40,  throttled=False, throttle_tier=2),
        dict(department="Marketing",  monthly_cap_usd=400.00,  current_spend_usd=401.20,  throttled=True,  throttle_tier=1),
        dict(department="Operations", monthly_cap_usd=300.00,  current_spend_usd=89.60,   throttled=False, throttle_tier=1),
    ]
    for bd in budget_data:
        b = db.query(models.DepartmentBudget).filter_by(department=bd["department"]).first()
        if b:
            b.monthly_cap_usd   = bd["monthly_cap_usd"]
            b.current_spend_usd = bd["current_spend_usd"]
            b.throttled         = bd["throttled"]
            b.throttle_tier     = bd["throttle_tier"]
            b.period_start      = period_start
        else:
            db.add(models.DepartmentBudget(period_start=period_start, **bd))
    db.commit()

    # ── Reset agents to look active and varied ─────────────────────────────────
    print("Setting agent states...")
    agents = db.query(models.RegisteredAgent).all()
    if not agents:
        agents = [
            models.RegisteredAgent(name="SupportBot-Alpha",  department="Support",    permissions="read,write",        target_table="tickets",           collision_policy="lock",  status="idle"),
            models.RegisteredAgent(name="SupportBot-Beta",   department="Support",    permissions="read,write",        target_table="tickets",           collision_policy="lock",  status="idle"),
            models.RegisteredAgent(name="SalesEnrich-1",     department="Sales",      permissions="read,write",        target_table="crm_records",       collision_policy="queue", status="idle"),
            models.RegisteredAgent(name="MarketingMailer-1", department="Marketing",  permissions="read",              target_table="customers",         collision_policy="skip",  status="idle"),
            models.RegisteredAgent(name="OpsLogger-1",       department="Operations", permissions="read,write,delete", target_table="crm_records",       collision_policy="lock",  status="idle"),
            models.RegisteredAgent(name="BillingAuditor-1",  department="Support",    permissions="read",              target_table="token_transactions", collision_policy="lock",  status="idle"),
        ]
        db.add_all(agents)
        db.commit()
        agents = db.query(models.RegisteredAgent).all()

    # Set last_used_at for all agents
    for i, agent in enumerate(agents):
        agent.last_used_at = now - timedelta(minutes=random.randint(5, 120))
        agent.status = "idle"
    db.commit()

    # ── Build historical transactions (past 30 days) ───────────────────────────
    print("Building 30 days of transaction history...")

    departments = ["Support", "Sales", "Marketing", "Operations"]
    dept_agents = {
        "Support":    [a.id for a in agents if a.department == "Support"],
        "Sales":      [a.id for a in agents if a.department == "Sales"],
        "Marketing":  [a.id for a in agents if a.department == "Marketing"],
        "Operations": [a.id for a in agents if a.department == "Operations"],
    }

    transactions = []

    # Historical (days 1-29 ago) — 8 to 20 calls per day
    for day_offset in range(29, 0, -1):
        day_ts = today - timedelta(days=day_offset)
        calls_this_day = random.randint(8, 20)
        for _ in range(calls_this_day):
            dept  = random.choice(departments)
            agent_ids = dept_agents.get(dept, [None])
            agent_id  = random.choice(agent_ids) if agent_ids else None

            is_complex = random.random() < 0.35
            was_pruned = random.random() < 0.70
            tokens_saved = random.randint(200, 1800) if was_pruned else 0

            if is_complex:
                input_tokens  = random.randint(1200, 4000)
                output_tokens = random.randint(400, 1200)
                cost_usd      = round((input_tokens * 0.005 + output_tokens * 0.015) / 1000, 6)
                tier          = "flagship"
                reason        = "COMPLEX"
            else:
                input_tokens  = random.randint(150, 600)
                output_tokens = random.randint(50, 200)
                cost_usd      = round((input_tokens * 0.0004 + output_tokens * 0.0016) / 1000, 6)
                tier          = "micro"
                reason        = "ROUTINE"

            ts = day_ts + timedelta(hours=random.randint(7, 22), minutes=random.randint(0, 59))
            transactions.append(models.TokenTransaction(
                department     = dept,
                agent_id       = agent_id,
                model_tier     = tier,
                input_tokens   = input_tokens,
                output_tokens  = output_tokens,
                cost_usd       = cost_usd,
                routing_reason = reason,
                was_pruned     = was_pruned,
                tokens_saved   = tokens_saved,
                timestamp      = ts,
            ))

    # Today — 22 calls, mix of micro and flagship
    today_calls = [
        # ROUTINE micro calls
        dict(dept="Support",    tier="micro",    input=420,  output=110, reason="ROUTINE",   pruned=True,  saved=380),
        dict(dept="Support",    tier="micro",    input=310,  output=90,  reason="ROUTINE",   pruned=True,  saved=260),
        dict(dept="Sales",      tier="micro",    input=280,  output=80,  reason="ROUTINE",   pruned=False, saved=0),
        dict(dept="Sales",      tier="micro",    input=350,  output=100, reason="ROUTINE",   pruned=True,  saved=290),
        dict(dept="Operations", tier="micro",    input=190,  output=55,  reason="ROUTINE",   pruned=False, saved=0),
        dict(dept="Operations", tier="micro",    input=240,  output=70,  reason="ROUTINE",   pruned=True,  saved=180),
        dict(dept="Support",    tier="micro",    input=380,  output=105, reason="THROTTLED", pruned=True,  saved=310),
        dict(dept="Marketing",  tier="micro",    input=290,  output=85,  reason="THROTTLED", pruned=True,  saved=240),
        # COMPLEX flagship calls
        dict(dept="Support",    tier="flagship", input=2400, output=780, reason="COMPLEX",   pruned=True,  saved=1420),
        dict(dept="Support",    tier="flagship", input=3100, output=960, reason="COMPLEX",   pruned=True,  saved=1850),
        dict(dept="Sales",      tier="flagship", input=1800, output=620, reason="COMPLEX",   pruned=True,  saved=980),
        dict(dept="Sales",      tier="flagship", input=2600, output=840, reason="COMPLEX",   pruned=True,  saved=1560),
        dict(dept="Marketing",  tier="flagship", input=3400, output=1200,reason="COMPLEX",   pruned=True,  saved=2100),
        dict(dept="Marketing",  tier="flagship", input=2900, output=890, reason="COMPLEX",   pruned=True,  saved=1740),
        dict(dept="Operations", tier="flagship", input=1600, output=540, reason="COMPLEX",   pruned=False, saved=0),
        dict(dept="Support",    tier="flagship", input=2200, output=710, reason="COMPLEX",   pruned=True,  saved=1300),
        dict(dept="Sales",      tier="micro",    input=410,  output=120, reason="ROUTINE",   pruned=True,  saved=350),
        dict(dept="Operations", tier="micro",    input=220,  output=65,  reason="ROUTINE",   pruned=False, saved=0),
        dict(dept="Support",    tier="flagship", input=1950, output=640, reason="COMPLEX",   pruned=True,  saved=1120),
        dict(dept="Sales",      tier="flagship", input=2750, output=890, reason="COMPLEX",   pruned=True,  saved=1680),
        dict(dept="Marketing",  tier="micro",    input=330,  output=95,  reason="THROTTLED", pruned=True,  saved=270),
        dict(dept="Operations", tier="flagship", input=1700, output=580, reason="COMPLEX",   pruned=True,  saved=990),
    ]

    for i, c in enumerate(today_calls):
        agent_ids = dept_agents.get(c["dept"], [None])
        agent_id  = random.choice(agent_ids) if agent_ids else None

        if c["tier"] == "flagship":
            cost = round((c["input"] * 0.005 + c["output"] * 0.015) / 1000, 6)
        else:
            cost = round((c["input"] * 0.0004 + c["output"] * 0.0016) / 1000, 6)

        ts = today + timedelta(hours=random.randint(7, 22), minutes=random.randint(0, 59))
        transactions.append(models.TokenTransaction(
            department     = c["dept"],
            agent_id       = agent_id,
            model_tier     = c["tier"],
            input_tokens   = c["input"],
            output_tokens  = c["output"],
            cost_usd       = cost,
            routing_reason = c["reason"],
            was_pruned     = c["pruned"],
            tokens_saved   = c["saved"],
            timestamp      = ts,
        ))

    db.add_all(transactions)
    db.commit()
    print(f"   → {len(transactions)} total transactions")

    # ── Audit Events ───────────────────────────────────────────────────────────
    print("Building audit events...")
    import json
    def snap(dept, throttled=False, spent=0.0, cap=500.0):
        return json.dumps({
            "captured_at": now.isoformat(),
            "department": dept,
            "budget_cap_usd": cap,
            "budget_spent_usd": round(spent, 4),
            "budget_used_pct": round((spent / cap) * 100, 1) if cap else 0,
            "throttled": throttled,
            "override_granted": False,
        })

    audit_events = [
        models.AuditEvent(
            event_type       = "ROUTING",
            department       = "Support",
            agent_id         = agents[0].id if agents else None,
            model_tier       = "flagship",
            context_snapshot = snap("Support", spent=342.80, cap=500.00),
            prompt_payload   = "Our SLA is in breach. We need legal review immediately. This is a critical production incident affecting 200+ enterprise users.",
            rationale        = 'FLAGSHIP MODEL INVOKED. Sensitive term "legal" triggered escalation policy. High-risk keywords detected: "legal", "breach", "escalate". Budget position: 68.6% used ($342.80 of $500.00 cap). Call cost: $0.018500.',
            decision_outcome = "flagship model used — $0.018500",
            risk_level       = "high",
            timestamp        = now - timedelta(hours=1, minutes=12),
        ),
        models.AuditEvent(
            event_type       = "ROUTING",
            department       = "Sales",
            agent_id         = agents[2].id if len(agents) > 2 else None,
            model_tier       = "flagship",
            context_snapshot = snap("Sales", spent=218.40, cap=750.00),
            prompt_payload   = "Please send the NDA and enterprise contract for legal review before the acquisition closes.",
            rationale        = 'FLAGSHIP MODEL INVOKED. Sensitive term "NDA" triggered escalation. High-risk keywords: "nda", "contract", "acquisition". Budget position: 29.1% used ($218.40 of $750.00 cap). Call cost: $0.014200.',
            decision_outcome = "flagship model used — $0.014200",
            risk_level       = "high",
            timestamp        = now - timedelta(hours=2, minutes=34),
        ),
        models.AuditEvent(
            event_type       = "DECISION",
            department       = "Marketing",
            agent_id         = agents[3].id if len(agents) > 3 else None,
            model_tier       = "none",
            context_snapshot = snap("Marketing", throttled=True, spent=401.20, cap=400.00),
            prompt_payload   = "Customer SSN 123-45-6789 needs to be updated in the CRM before the campaign launches.",
            rationale        = "HIPAA-classified sensitive term 'SSN' matched block policy. Request rejected immediately. No data was sent to any AI model. Audit record created for compliance review.",
            decision_outcome = "Request blocked by sensitive term policy",
            risk_level       = "critical",
            timestamp        = now - timedelta(hours=3, minutes=8),
        ),
        models.AuditEvent(
            event_type       = "THROTTLE",
            department       = "Marketing",
            agent_id         = None,
            model_tier       = "micro",
            context_snapshot = snap("Marketing", throttled=True, spent=401.20, cap=400.00),
            prompt_payload   = None,
            rationale        = "BUDGET CAP ENFORCED. Marketing department reached 100.3% of its $400.00 monthly cap (current spend: $401.20). All requests downgraded to micro-model tier. Supervisor override required to restore flagship access.",
            decision_outcome = "Throttle engaged — micro model enforced for all Marketing requests",
            risk_level       = "medium",
            timestamp        = now - timedelta(hours=4, minutes=55),
        ),
        models.AuditEvent(
            event_type       = "ROUTING",
            department       = "Support",
            agent_id         = agents[1].id if len(agents) > 1 else None,
            model_tier       = "flagship",
            context_snapshot = snap("Support", spent=342.80, cap=500.00),
            prompt_payload   = "Contact aria.fontaine@acmecorp.com or call (312) 555-0192 to discuss the account situation.",
            rationale        = "FLAGSHIP MODEL INVOKED. PII regex patterns matched: Email Address and US Phone Number detected in payload. Escalated for compliance review. Budget position: 68.6% used. Call cost: $0.021800.",
            decision_outcome = "flagship model used — $0.021800",
            risk_level       = "high",
            timestamp        = now - timedelta(minutes=28),
        ),
        models.AuditEvent(
            event_type       = "ROUTING",
            department       = "Operations",
            agent_id         = agents[4].id if len(agents) > 4 else None,
            model_tier       = "flagship",
            context_snapshot = snap("Operations", spent=89.60, cap=300.00),
            prompt_payload   = "Migrate all customer records from the legacy system to the new platform before end of quarter. Ensure GDPR compliance throughout.",
            rationale        = "FLAGSHIP MODEL INVOKED. Complexity threshold exceeded. Keywords: 'migrate', 'gdpr', 'compliance'. Budget position: 29.9% used ($89.60 of $300.00). Call cost: $0.011400.",
            decision_outcome = "flagship model used — $0.011400",
            risk_level       = "medium",
            timestamp        = now - timedelta(hours=5, minutes=41),
        ),
    ]
    db.add_all(audit_events)
    db.commit()
    print(f"   → {len(audit_events)} audit events")

    db.close()

    print("\n✅ Demo data loaded successfully!")
    print("   Refresh your dashboard to see the updated KPIs.")
    print("   Marketing is throttled — shows the red alert card.")
    print("   Audit log has 6 high-stakes events including a BLOCK.\n")


if __name__ == "__main__":
    populate()
