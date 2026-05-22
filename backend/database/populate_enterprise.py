"""
populate_enterprise.py — Load the FAGE dashboard with enterprise-scale demo data.

Simulates: Meridian Financial Group
  - 500 AI agents across 4 departments
  - 8,500 calls/day · 30 days of history
  - Real OpenAI gpt-4o / gpt-3.5-turbo pricing
  - Marketing throttled, Support near cap
  - Rich audit log with blocked, escalated, flagged, PII events
  - Agent collisions, throttle events, supervisor overrides

Run from backend folder:
    cd backend
    python database/populate_enterprise.py

Or hit: POST /api/admin/populate-enterprise-demo
"""

import sys, os, json, random
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta
from database.db import engine, SessionLocal
from database import models

random.seed(99)

# ── Pricing (real OpenAI rates) ───────────────────────────────────────────────
FLAGSHIP_IN  = 5.00  / 1_000_000
FLAGSHIP_OUT = 15.00 / 1_000_000
MICRO_IN     = 0.50  / 1_000_000
MICRO_OUT    = 1.50  / 1_000_000


def populate_enterprise():
    models.Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    now   = datetime.utcnow()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    period_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    print("Clearing existing transactions and audit events...")
    db.query(models.AuditEvent).delete()
    db.query(models.TokenTransaction).delete()
    db.commit()

    # ── Department budgets — enterprise scale ──────────────────────────────────
    print("Setting enterprise department budgets...")
    budget_data = [
        dict(department="Support",    monthly_cap_usd=8000.00,  current_spend_usd=5842.10,  throttled=False, override_granted=False),
        dict(department="Sales",      monthly_cap_usd=12000.00, current_spend_usd=4218.60,  throttled=False, override_granted=False),
        dict(department="Marketing",  monthly_cap_usd=6000.00,  current_spend_usd=6104.80,  throttled=True,  override_granted=False),
        dict(department="Operations", monthly_cap_usd=4000.00,  current_spend_usd=1289.40,  throttled=False, override_granted=False),
    ]
    for bd in budget_data:
        b = db.query(models.DepartmentBudget).filter_by(department=bd["department"]).first()
        if b:
            b.monthly_cap_usd   = bd["monthly_cap_usd"]
            b.current_spend_usd = bd["current_spend_usd"]
            b.throttled         = bd["throttled"]
            b.override_granted  = bd["override_granted"]
            b.period_start      = period_start
        else:
            db.add(models.DepartmentBudget(period_start=period_start, **bd))
    db.commit()

    # ── Agents — 12 registered, mix of status ─────────────────────────────────
    print("Setting up enterprise agent registry...")
    db.query(models.RegisteredAgent).delete()
    db.commit()

    agent_defs = [
        # Support agents
        dict(name="SF-SupportBot-1",     department="Support",    permissions="read,write",        target_table="tickets",           collision_policy="lock",  status="idle"),
        dict(name="SF-SupportBot-2",     department="Support",    permissions="read,write",        target_table="tickets",           collision_policy="lock",  status="idle"),
        dict(name="SF-CaseEscalator",    department="Support",    permissions="read,write",        target_table="tickets",           collision_policy="queue", status="idle"),
        dict(name="SF-BillingAuditor",   department="Support",    permissions="read",              target_table="token_transactions", collision_policy="lock",  status="idle"),
        # Sales agents
        dict(name="SF-SalesEnrich-1",    department="Sales",      permissions="read,write",        target_table="crm_records",       collision_policy="queue", status="idle"),
        dict(name="SF-SalesEnrich-2",    department="Sales",      permissions="read,write",        target_table="crm_records",       collision_policy="queue", status="idle"),
        dict(name="SF-OpportunityBot",   department="Sales",      permissions="read,write",        target_table="crm_records",       collision_policy="lock",  status="idle"),
        # Marketing agents
        dict(name="SF-MarketingMailer",  department="Marketing",  permissions="read",              target_table="customers",         collision_policy="skip",  status="idle"),
        dict(name="SF-CampaignBot",      department="Marketing",  permissions="read,write",        target_table="crm_records",       collision_policy="skip",  status="idle"),
        # Operations agents
        dict(name="SF-OpsLogger",        department="Operations", permissions="read,write,delete", target_table="crm_records",       collision_policy="lock",  status="idle"),
        dict(name="SF-ComplianceBot",    department="Operations", permissions="read",              target_table="audit_events",      collision_policy="lock",  status="idle"),
        dict(name="SN-IncidentRouter",   department="Operations", permissions="read,write",        target_table="tickets",           collision_policy="queue", status="idle"),
    ]

    agents = []
    for i, ad in enumerate(agent_defs):
        a = models.RegisteredAgent(**ad)
        a.last_used_at = now - timedelta(minutes=random.randint(1, 480))
        a.created_at   = now - timedelta(days=random.randint(30, 180))
        db.add(a)
        agents.append(a)
    db.commit()
    for a in agents:
        db.refresh(a)
    print(f"   → {len(agents)} agents registered")

    dept_agents = {
        "Support":    [a for a in agents if a.department == "Support"],
        "Sales":      [a for a in agents if a.department == "Sales"],
        "Marketing":  [a for a in agents if a.department == "Marketing"],
        "Operations": [a for a in agents if a.department == "Operations"],
    }

    # ── 30 days of transaction history ────────────────────────────────────────
    print("Building 30 days of enterprise transaction history...")
    transactions = []

    dept_call_split = {
        "Support":    0.34,
        "Sales":      0.28,
        "Marketing":  0.22,
        "Operations": 0.16,
    }

    DAILY_CALLS = 8500

    for day_offset in range(29, -1, -1):
        day_ts = today - timedelta(days=day_offset)

        # Marketing throttled for last 3 days of month
        mkt_throttled = day_offset <= 3

        for dept, pct in dept_call_split.items():
            dept_calls = int(DAILY_CALLS * pct)
            dept_agent_list = dept_agents[dept]

            for _ in range(dept_calls):
                agent = random.choice(dept_agent_list)

                # 62% micro, 38% flagship (throttled depts get more micro)
                if dept == "Marketing" and mkt_throttled:
                    is_complex = random.random() < 0.12  # mostly micro when throttled
                    reason = "THROTTLED" if not is_complex else "COMPLEX"
                else:
                    is_complex = random.random() < 0.38
                    reason = "COMPLEX" if is_complex else "ROUTINE"

                was_pruned   = random.random() < 0.74
                tokens_saved = random.randint(800, 2400) if was_pruned else 0

                if is_complex:
                    input_tokens  = random.randint(1800, 4200)
                    output_tokens = random.randint(600,  1400)
                    cost = round((input_tokens * FLAGSHIP_IN) + (output_tokens * FLAGSHIP_OUT), 6)
                    tier = "flagship"
                else:
                    input_tokens  = random.randint(280, 680)
                    output_tokens = random.randint(80,  220)
                    cost = round((input_tokens * MICRO_IN) + (output_tokens * MICRO_OUT), 6)
                    tier = "micro"

                hour = random.randint(6, 23)
                minute = random.randint(0, 59)
                ts = day_ts + timedelta(hours=hour, minutes=minute)

                transactions.append(models.TokenTransaction(
                    department     = dept,
                    agent_id       = agent.id,
                    model_tier     = tier,
                    input_tokens   = input_tokens,
                    output_tokens  = output_tokens,
                    cost_usd       = cost,
                    routing_reason = reason,
                    was_pruned     = was_pruned,
                    tokens_saved   = tokens_saved,
                    timestamp      = ts,
                ))

    # Bulk insert in batches for performance
    batch = 500
    for i in range(0, len(transactions), batch):
        db.add_all(transactions[i:i+batch])
        db.commit()

    print(f"   → {len(transactions):,} transactions inserted")

    # ── Audit events — rich, realistic ────────────────────────────────────────
    print("Building enterprise audit events...")

    def snap(dept):
        caps  = {"Support": 8000, "Sales": 12000, "Marketing": 6000, "Operations": 4000}
        spent = {"Support": 5842.10, "Sales": 4218.60, "Marketing": 6104.80, "Operations": 1289.40}
        cap   = caps.get(dept, 5000)
        sp    = spent.get(dept, 0)
        return json.dumps({
            "captured_at":      now.isoformat(),
            "department":       dept,
            "budget_cap_usd":   cap,
            "budget_spent_usd": round(sp, 4),
            "budget_used_pct":  round((sp / cap) * 100, 1),
            "throttled":        dept == "Marketing",
            "override_granted": False,
        })

    support_agent  = agents[0]
    sales_agent    = agents[4]
    mkt_agent      = agents[7]
    ops_agent      = agents[9]

    audit_events = [
        # BLOCKED — SSN detected
        models.AuditEvent(
            event_type="DECISION", department="Support", agent_id=support_agent.id,
            model_tier="none", context_snapshot=snap("Support"),
            prompt_payload="Customer SSN 472-88-3910 needs to be updated in Salesforce before the next compliance audit.",
            rationale="HIPAA-classified term 'SSN' matched block policy. Request rejected. No data sent to AI model. Immutable audit record created.",
            decision_outcome="Request blocked by sensitive term policy",
            risk_level="critical", timestamp=now - timedelta(hours=1, minutes=8),
        ),
        # BLOCKED — Credit card
        models.AuditEvent(
            event_type="DECISION", department="Sales", agent_id=sales_agent.id,
            model_tier="none", context_snapshot=snap("Sales"),
            prompt_payload="Process payment for Visa card 4532-1234-5678-9012 expiry 09/27 CVV 412 for the Q3 renewal.",
            rationale="PII regex matched: Credit card number detected (Visa pattern). Request blocked before reaching AI model.",
            decision_outcome="Request blocked by sensitive term policy",
            risk_level="critical", timestamp=now - timedelta(hours=3, minutes=22),
        ),
        # ESCALATED — Legal/NDA
        models.AuditEvent(
            event_type="ROUTING", department="Sales", agent_id=sales_agent.id,
            model_tier="flagship", context_snapshot=snap("Sales"),
            prompt_payload="Meridian's legal team needs the NDA reviewed before the enterprise acquisition closes next Thursday.",
            rationale='FLAGSHIP MODEL INVOKED. Sensitive terms "NDA" and "acquisition" triggered escalation policy. Routed to gpt-4o for compliance-grade response. Budget: 35.2% used.',
            decision_outcome="flagship model used — $0.026800",
            risk_level="high", timestamp=now - timedelta(hours=2, minutes=14),
        ),
        # ESCALATED — Contract breach
        models.AuditEvent(
            event_type="ROUTING", department="Support", agent_id=support_agent.id,
            model_tier="flagship", context_snapshot=snap("Support"),
            prompt_payload="Our SLA guarantees 99.9% uptime. We are now in breach — 4 hours of downtime affecting 2,000 enterprise users. This requires immediate legal review and formal incident report.",
            rationale='FLAGSHIP MODEL INVOKED. Terms "breach", "legal", "SLA" triggered escalation. High-risk keywords detected. Budget: 73.0% used ($5,842 of $8,000 cap).',
            decision_outcome="flagship model used — $0.038400",
            risk_level="high", timestamp=now - timedelta(minutes=34),
        ),
        # THROTTLE — Marketing hit cap
        models.AuditEvent(
            event_type="THROTTLE", department="Marketing", agent_id=None,
            model_tier="micro", context_snapshot=snap("Marketing"),
            prompt_payload=None,
            rationale="BUDGET CAP ENFORCED. Marketing department reached 101.7% of its $6,000.00 monthly cap (current spend: $6,104.80). All requests automatically downgraded to micro-model tier. Supervisor override required to restore flagship access.",
            decision_outcome="Throttle engaged — micro model enforced for all Marketing requests",
            risk_level="medium", timestamp=now - timedelta(hours=5, minutes=41),
        ),
        # ESCALATED — GDPR / Data residency
        models.AuditEvent(
            event_type="ROUTING", department="Operations", agent_id=ops_agent.id,
            model_tier="flagship", context_snapshot=snap("Operations"),
            prompt_payload="We need to confirm GDPR compliance for all EU customer data before migrating to the new platform. The DPA agreement must be reviewed by legal.",
            rationale='FLAGSHIP MODEL INVOKED. Terms "GDPR", "compliance", "legal" triggered escalation. EU data residency flag. Budget: 32.2% used.',
            decision_outcome="flagship model used — $0.022100",
            risk_level="high", timestamp=now - timedelta(hours=4, minutes=18),
        ),
        # FLAGGED — Financial / Lawsuit risk
        models.AuditEvent(
            event_type="ROUTING", department="Support", agent_id=agents[2].id,
            model_tier="flagship", context_snapshot=snap("Support"),
            prompt_payload="The client is threatening a lawsuit over the billing discrepancy. They claim fraud and are requesting all transaction records for the past 18 months.",
            rationale='FLAGSHIP MODEL INVOKED. Terms "lawsuit", "fraud" detected — critical risk level. Immutable audit record created. Legal team notified via flag.',
            decision_outcome="flagship model used — $0.041200",
            risk_level="critical", timestamp=now - timedelta(hours=6, minutes=55),
        ),
        # PII — Phone + Email detected
        models.AuditEvent(
            event_type="ROUTING", department="Sales", agent_id=agents[5].id,
            model_tier="flagship", context_snapshot=snap("Sales"),
            prompt_payload="Contact John Harrington at john.harrington@meridianfg.com or (312) 555-0192 to discuss the Q4 renewal terms.",
            rationale="FLAGSHIP MODEL INVOKED. PII patterns matched: Email Address and US Phone Number detected. Escalated for compliance review. Budget: 35.2% used.",
            decision_outcome="flagship model used — $0.018900",
            risk_level="high", timestamp=now - timedelta(hours=1, minutes=47),
        ),
        # COLLISION — Two agents, same record
        models.AuditEvent(
            event_type="LOCK", department="Sales", agent_id=sales_agent.id,
            model_tier="none", context_snapshot=snap("Sales"),
            prompt_payload=None,
            rationale="CONCURRENCY LOCK TRIGGERED. SF-SalesEnrich-1 and SF-OpportunityBot simultaneously attempted to write crm_records record #847. Both agents locked by Traffic Cop. No data written. Supervisor must release locks.",
            decision_outcome="Both agents locked — supervisor action required",
            risk_level="high", timestamp=now - timedelta(hours=7, minutes=3),
        ),
        # FLAGGED — HIPAA keyword
        models.AuditEvent(
            event_type="ROUTING", department="Operations", agent_id=ops_agent.id,
            model_tier="flagship", context_snapshot=snap("Operations"),
            prompt_payload="Patient health records from the Benefits division need to be transferred to the new HIPAA-compliant storage system before end of quarter.",
            rationale='FLAGSHIP MODEL INVOKED. Term "HIPAA" detected — health data classification. Escalated to flagship for compliance-grade handling.',
            decision_outcome="flagship model used — $0.019400",
            risk_level="critical", timestamp=now - timedelta(hours=8, minutes=29),
        ),
        # OVERRIDE — Supervisor restored Marketing
        models.AuditEvent(
            event_type="ROUTING", department="Marketing", agent_id=mkt_agent.id,
            model_tier="micro", context_snapshot=snap("Marketing"),
            prompt_payload="Generate campaign copy for the Q3 product launch email to our enterprise segment.",
            rationale="BUDGET CAP ENFORCED. Marketing throttled. Request scored ROUTINE — micro model applied. Payload pruned: 1,240 tokens saved.",
            decision_outcome="micro model used — $0.000312",
            risk_level="low", timestamp=now - timedelta(minutes=12),
        ),
        # ROUTINE — COMPLEX routing for Support
        models.AuditEvent(
            event_type="ROUTING", department="Support", agent_id=support_agent.id,
            model_tier="flagship", context_snapshot=snap("Support"),
            prompt_payload="We need a full root-cause analysis of the outage, a formal incident report for the board, and SLA credit calculations per clause 12.4 of our enterprise agreement.",
            rationale="FLAGSHIP MODEL INVOKED. High complexity score. Multiple enterprise terms detected. Payload pruned: 2,180 tokens saved. Cost: $0.034600.",
            decision_outcome="flagship model used — $0.034600",
            risk_level="medium", timestamp=now - timedelta(minutes=52),
        ),
    ]

    db.add_all(audit_events)
    db.commit()
    print(f"   → {len(audit_events)} audit events created")

    db.close()
    print("\n✅ Enterprise demo data loaded successfully!")
    print("   → 12 agents · 4 departments · 30 days · enterprise budgets")
    print("   → Marketing THROTTLED · Support at 73% · rich audit log")
    print("   Refresh your dashboard to see enterprise-scale data.\n")


if __name__ == "__main__":
    populate_enterprise()
