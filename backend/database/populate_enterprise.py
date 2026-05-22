"""
populate_enterprise.py — Load the FAGE dashboard with 1-year enterprise-scale demo data.

Simulates: Meridian Financial Group
  - Realistic AI adoption curve over 365 days
  - Phase 1 (days 1-60):   Early rollout — 100 calls/day, 50% flagship, 60% pruned
  - Phase 2 (days 61-180): Expansion — 250 calls/day, 40% flagship, 72% pruned
  - Phase 3 (days 181-365): Full scale — 500 calls/day, 35% flagship, 76% pruned
  - ~128,500 total transactions across 4 departments
  - Marketing throttled (hits cap repeatedly in Phase 3)
  - 40 audit events spread across the year — visible in Risk tab timeline
  - All range buttons (7D / 30D / 90D / 1Y) have real data

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

random.seed(42)

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

    # ── Agents — 12 registered ─────────────────────────────────────────────────
    print("Setting up enterprise agent registry...")
    db.query(models.RegisteredAgent).delete()
    db.commit()

    agent_defs = [
        dict(name="SF-SupportBot-1",   department="Support",    permissions="read,write",        target_table="tickets",           collision_policy="lock",  status="idle"),
        dict(name="SF-SupportBot-2",   department="Support",    permissions="read,write",        target_table="tickets",           collision_policy="lock",  status="idle"),
        dict(name="SF-CaseEscalator",  department="Support",    permissions="read,write",        target_table="tickets",           collision_policy="queue", status="idle"),
        dict(name="SF-BillingAuditor", department="Support",    permissions="read",              target_table="token_transactions", collision_policy="lock",  status="idle"),
        dict(name="SF-SalesEnrich-1",  department="Sales",      permissions="read,write",        target_table="crm_records",       collision_policy="queue", status="idle"),
        dict(name="SF-SalesEnrich-2",  department="Sales",      permissions="read,write",        target_table="crm_records",       collision_policy="queue", status="idle"),
        dict(name="SF-OpportunityBot", department="Sales",      permissions="read,write",        target_table="crm_records",       collision_policy="lock",  status="idle"),
        dict(name="SF-MarketingMailer",department="Marketing",  permissions="read",              target_table="customers",         collision_policy="skip",  status="idle"),
        dict(name="SF-CampaignBot",    department="Marketing",  permissions="read,write",        target_table="crm_records",       collision_policy="skip",  status="idle"),
        dict(name="SF-OpsLogger",      department="Operations", permissions="read,write,delete", target_table="crm_records",       collision_policy="lock",  status="idle"),
        dict(name="SF-ComplianceBot",  department="Operations", permissions="read",              target_table="audit_events",      collision_policy="lock",  status="idle"),
        dict(name="SN-IncidentRouter", department="Operations", permissions="read,write",        target_table="tickets",           collision_policy="queue", status="idle"),
    ]

    agents = []
    for ad in agent_defs:
        a = models.RegisteredAgent(**ad)
        a.last_used_at = now - timedelta(minutes=random.randint(1, 480))
        a.created_at   = now - timedelta(days=random.randint(365, 400))
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

    # ── 365-day adoption curve transaction history ─────────────────────────────
    print("Building 365-day enterprise transaction history (adoption curve)...")

    # Three phases of AI adoption
    PHASES = [
        # (start_day_offset_from_today, end_day_offset, daily_calls, complex_pct, prune_pct, label)
        (364, 305, 100, 0.50, 0.60, "early"),     # days 1-60 from start = 364-305 days ago
        (304, 185, 250, 0.40, 0.72, "expansion"), # days 61-180
        (184,   0, 500, 0.35, 0.76, "fullscale"), # days 181-365
    ]

    dept_call_split = {
        "Support":    0.34,
        "Sales":      0.28,
        "Marketing":  0.22,
        "Operations": 0.16,
    }

    transactions = []

    for phase_start, phase_end, daily_calls, complex_pct, prune_pct, phase_label in PHASES:
        for day_offset in range(phase_start, phase_end - 1, -1):
            day_ts = today - timedelta(days=day_offset)

            # Marketing throttled when near/over cap (Phase 3, last 45 days of each simulated month)
            mkt_throttled = (phase_label == "fullscale") and (day_offset % 30 <= 12)

            for dept, pct in dept_call_split.items():
                dept_calls = max(1, int(daily_calls * pct))
                dept_agent_list = dept_agents[dept]

                for _ in range(dept_calls):
                    agent = random.choice(dept_agent_list)

                    if dept == "Marketing" and mkt_throttled:
                        is_complex = random.random() < 0.10
                        reason = "THROTTLED" if not is_complex else "COMPLEX"
                    else:
                        is_complex = random.random() < complex_pct
                        reason = "COMPLEX" if is_complex else "ROUTINE"

                    was_pruned   = random.random() < prune_pct
                    tokens_saved = random.randint(600, 2800) if was_pruned else 0

                    if is_complex:
                        # Scale up token counts to reflect enterprise-volume pricing
                        input_tokens  = random.randint(8000, 18000)
                        output_tokens = random.randint(2000, 5000)
                        cost = round((input_tokens * FLAGSHIP_IN) + (output_tokens * FLAGSHIP_OUT), 6)
                        tier = "flagship"
                    else:
                        input_tokens  = random.randint(1200, 3200)
                        output_tokens = random.randint(300,  900)
                        cost = round((input_tokens * MICRO_IN) + (output_tokens * MICRO_OUT), 6)
                        tier = "micro"

                    hour   = random.randint(6, 23)
                    minute = random.randint(0, 59)
                    ts     = day_ts + timedelta(hours=hour, minutes=minute)

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

    # Bulk insert all at once
    db.add_all(transactions)
    db.commit()
    print(f"   → {len(transactions):,} transactions inserted across 3 adoption phases")

    # ── 40 audit events spread across the year ────────────────────────────────
    print("Building enterprise audit events (spread across 365 days)...")

    def snap(dept, days_ago=0):
        caps  = {"Support": 8000, "Sales": 12000, "Marketing": 6000, "Operations": 4000}
        spent = {"Support": 5842.10, "Sales": 4218.60, "Marketing": 6104.80, "Operations": 1289.40}
        cap   = caps.get(dept, 5000)
        sp    = spent.get(dept, 0)
        ts    = (now - timedelta(days=days_ago)).isoformat()
        return json.dumps({
            "captured_at":      ts,
            "department":       dept,
            "budget_cap_usd":   cap,
            "budget_spent_usd": round(sp * random.uniform(0.3, 1.0) if days_ago > 30 else sp, 4),
            "budget_used_pct":  round(random.uniform(20, 101) if days_ago > 30 else (sp / cap) * 100, 1),
            "throttled":        dept == "Marketing" and days_ago < 30,
            "override_granted": False,
        })

    sa = agents[0]   # SF-SupportBot-1
    s2 = agents[1]   # SF-SupportBot-2
    ce = agents[2]   # SF-CaseEscalator
    ba = agents[3]   # SF-BillingAuditor
    se = agents[4]   # SF-SalesEnrich-1
    s6 = agents[5]   # SF-SalesEnrich-2
    ob = agents[6]   # SF-OpportunityBot
    mm = agents[7]   # SF-MarketingMailer
    cb = agents[8]   # SF-CampaignBot
    ol = agents[9]   # SF-OpsLogger
    co = agents[10]  # SF-ComplianceBot
    ir = agents[11]  # SN-IncidentRouter

    audit_events = [

        # ── PHASE 1: Early rollout (days 300-365 ago) ─────────────────────────

        models.AuditEvent(
            event_type="ROUTING", department="Support", agent_id=sa.id,
            model_tier="flagship", context_snapshot=snap("Support", 355),
            prompt_payload="Initial deployment — first enterprise support case routed through FAGE. Testing NDA review workflow.",
            rationale="FLAGSHIP MODEL INVOKED. Term 'NDA' matched escalation policy. First routing decision recorded in immutable audit log.",
            decision_outcome="flagship model used — $0.022400",
            risk_level="high", timestamp=now - timedelta(days=355, hours=10),
        ),
        models.AuditEvent(
            event_type="DECISION", department="Support", agent_id=sa.id,
            model_tier="none", context_snapshot=snap("Support", 340),
            prompt_payload="Employee SSN 372-44-1821 needs verification for the new benefits enrollment.",
            rationale="HIPAA-classified term 'SSN' matched block policy. Request rejected before reaching AI. Day 25 of deployment — first sensitive data block.",
            decision_outcome="Request blocked by sensitive term policy",
            risk_level="critical", timestamp=now - timedelta(days=340, hours=14),
        ),
        models.AuditEvent(
            event_type="ROUTING", department="Sales", agent_id=se.id,
            model_tier="flagship", context_snapshot=snap("Sales", 330),
            prompt_payload="Customer threatening breach of contract — need legal review of SLA clause 8.2 before tomorrow's call.",
            rationale="FLAGSHIP MODEL INVOKED. Terms 'breach', 'legal', 'SLA' triggered escalation. Sales phase early rollout.",
            decision_outcome="flagship model used — $0.031200",
            risk_level="high", timestamp=now - timedelta(days=330, hours=9),
        ),
        models.AuditEvent(
            event_type="LOCK", department="Support", agent_id=sa.id,
            model_tier="none", context_snapshot=snap("Support", 320),
            prompt_payload=None,
            rationale="CONCURRENCY LOCK. SF-SupportBot-1 and SF-SupportBot-2 simultaneously attempted to write ticket #103. Traffic Cop locked both agents. No data corruption. Early collision detected and resolved.",
            decision_outcome="Both agents locked — supervisor action required",
            risk_level="high", timestamp=now - timedelta(days=320, hours=11),
        ),
        models.AuditEvent(
            event_type="ROUTING", department="Operations", agent_id=ol.id,
            model_tier="flagship", context_snapshot=snap("Operations", 310),
            prompt_payload="GDPR compliance review required for EU customer data export request from Frankfurt office.",
            rationale="FLAGSHIP MODEL INVOKED. Terms 'GDPR', 'compliance' detected. EU data residency flag applied.",
            decision_outcome="flagship model used — $0.024800",
            risk_level="high", timestamp=now - timedelta(days=310, hours=15),
        ),

        # ── PHASE 2: Expansion (days 120-300 ago) ─────────────────────────────

        models.AuditEvent(
            event_type="THROTTLE", department="Marketing", agent_id=None,
            model_tier="micro", context_snapshot=snap("Marketing", 290),
            prompt_payload=None,
            rationale="BUDGET CAP ENFORCED. Marketing hit $6,000 cap for the first time — month 2 of expansion. All requests downgraded to micro model. Supervisor notified.",
            decision_outcome="Throttle engaged — micro model enforced for all Marketing requests",
            risk_level="medium", timestamp=now - timedelta(days=290, hours=16),
        ),
        models.AuditEvent(
            event_type="DECISION", department="Sales", agent_id=ob.id,
            model_tier="none", context_snapshot=snap("Sales", 275),
            prompt_payload="Process payment: Mastercard 5412-7534-8821-0043 expiry 11/26 CVV 339 — Q2 renewal $48,000.",
            rationale="PII regex matched: Credit card number (Mastercard pattern). Request blocked before AI model. Credit card protection active.",
            decision_outcome="Request blocked by sensitive term policy",
            risk_level="critical", timestamp=now - timedelta(days=275, hours=10),
        ),
        models.AuditEvent(
            event_type="ROUTING", department="Support", agent_id=ce.id,
            model_tier="flagship", context_snapshot=snap("Support", 260),
            prompt_payload="Client is threatening lawsuit over billing fraud — 18 months of transaction records requested by their legal team.",
            rationale="FLAGSHIP MODEL INVOKED. Terms 'lawsuit', 'fraud', 'legal' detected. Critical risk. Immutable record created. Legal team flagged.",
            decision_outcome="flagship model used — $0.041800",
            risk_level="critical", timestamp=now - timedelta(days=260, hours=8),
        ),
        models.AuditEvent(
            event_type="ROUTING", department="Operations", agent_id=co.id,
            model_tier="flagship", context_snapshot=snap("Operations", 245),
            prompt_payload="HIPAA audit required — patient health data from Benefits division being migrated to new compliance storage.",
            rationale="FLAGSHIP MODEL INVOKED. 'HIPAA' detected — health data classification. Escalated for compliance-grade handling.",
            decision_outcome="flagship model used — $0.019400",
            risk_level="critical", timestamp=now - timedelta(days=245, hours=13),
        ),
        models.AuditEvent(
            event_type="LOCK", department="Sales", agent_id=se.id,
            model_tier="none", context_snapshot=snap("Sales", 230),
            prompt_payload=None,
            rationale="CONCURRENCY LOCK. SF-SalesEnrich-1 and SF-OpportunityBot attempted simultaneous write on crm_records #2,847. Locked. Zero data corruption across 230 days of deployment.",
            decision_outcome="Both agents locked — supervisor action required",
            risk_level="high", timestamp=now - timedelta(days=230, hours=11),
        ),
        models.AuditEvent(
            event_type="THROTTLE", department="Marketing", agent_id=None,
            model_tier="micro", context_snapshot=snap("Marketing", 215),
            prompt_payload=None,
            rationale="BUDGET CAP ENFORCED. Marketing second consecutive month hitting $6,000 cap. Throttle auto-engaged day 18 of billing cycle. Pattern established — throttle now expected monthly.",
            decision_outcome="Throttle engaged — micro model enforced for all Marketing requests",
            risk_level="medium", timestamp=now - timedelta(days=215, hours=16),
        ),
        models.AuditEvent(
            event_type="ROUTING", department="Sales", agent_id=s6.id,
            model_tier="flagship", context_snapshot=snap("Sales", 200),
            prompt_payload="Contact Michael Chen at m.chen@globalcorp.com (415) 555-0847 — enterprise acquisition proposal due Friday.",
            rationale="FLAGSHIP MODEL INVOKED. PII: Email and phone number detected. Term 'acquisition' triggered escalation. Compliance review applied.",
            decision_outcome="flagship model used — $0.018900",
            risk_level="high", timestamp=now - timedelta(days=200, hours=9),
        ),
        models.AuditEvent(
            event_type="DECISION", department="Support", agent_id=ba.id,
            model_tier="none", context_snapshot=snap("Support", 185),
            prompt_payload="Audit log review: SSN 529-11-4772 flagged in Q2 billing record — needs HIPAA-compliant redaction before export.",
            rationale="HIPAA block: SSN pattern detected. Request blocked. HIPAA data never reached AI model. Redaction requested through secure channel.",
            decision_outcome="Request blocked by sensitive term policy",
            risk_level="critical", timestamp=now - timedelta(days=185, hours=14),
        ),

        # ── PHASE 3: Full scale (days 0-184 ago) ──────────────────────────────

        models.AuditEvent(
            event_type="THROTTLE", department="Marketing", agent_id=None,
            model_tier="micro", context_snapshot=snap("Marketing", 170),
            prompt_payload=None,
            rationale="BUDGET CAP ENFORCED. Month 7 — Marketing throttled day 14 of billing cycle. Pattern consistent. Micro model delivering 94% of campaign results at 8% of flagship cost.",
            decision_outcome="Throttle engaged — micro model enforced for all Marketing requests",
            risk_level="medium", timestamp=now - timedelta(days=170, hours=16),
        ),
        models.AuditEvent(
            event_type="ROUTING", department="Support", agent_id=sa.id,
            model_tier="flagship", context_snapshot=snap("Support", 160),
            prompt_payload="Full root-cause analysis required for 4-hour outage. Board report, SLA credit calculation per clause 12.4, and formal incident report for enterprise clients.",
            rationale="FLAGSHIP MODEL INVOKED. High complexity. Terms 'SLA', 'board', 'enterprise'. Payload pruned: 2,640 tokens saved before routing.",
            decision_outcome="flagship model used — $0.038400",
            risk_level="high", timestamp=now - timedelta(days=160, hours=8),
        ),
        models.AuditEvent(
            event_type="LOCK", department="Operations", agent_id=ol.id,
            model_tier="none", context_snapshot=snap("Operations", 148),
            prompt_payload=None,
            rationale="CONCURRENCY LOCK. SF-OpsLogger and SN-IncidentRouter simultaneously attempted write on incident #4,221. Locked and queued. Zero data loss.",
            decision_outcome="Agents locked and queued — auto-retry scheduled",
            risk_level="high", timestamp=now - timedelta(days=148, hours=12),
        ),
        models.AuditEvent(
            event_type="DECISION", department="Sales", agent_id=ob.id,
            model_tier="none", context_snapshot=snap("Sales", 140),
            prompt_payload="Amex card 3782-822463-10005 expiry 08/27 CVV 1042 — urgent payment for annual license renewal.",
            rationale="PII block: Credit card number (Amex pattern) detected. Blocked before AI. Eighth payment data block this year.",
            decision_outcome="Request blocked by sensitive term policy",
            risk_level="critical", timestamp=now - timedelta(days=140, hours=10),
        ),
        models.AuditEvent(
            event_type="ROUTING", department="Operations", agent_id=co.id,
            model_tier="flagship", context_snapshot=snap("Operations", 130),
            prompt_payload="SEC investigation subpoena received — all AI-processed communications for Q1-Q3 must be preserved and exported for legal review.",
            rationale="FLAGSHIP MODEL INVOKED. Terms 'SEC', 'subpoena', 'legal' — critical risk. Immutable audit trail being compiled for legal team. 9 months of records available.",
            decision_outcome="flagship model used — $0.044200",
            risk_level="critical", timestamp=now - timedelta(days=130, hours=9),
        ),
        models.AuditEvent(
            event_type="THROTTLE", department="Marketing", agent_id=None,
            model_tier="micro", context_snapshot=snap("Marketing", 120),
            prompt_payload=None,
            rationale="BUDGET CAP ENFORCED. Month 9 throttle — Marketing has now hit cap every month since Phase 2 expansion. CFO reviewing $6,000 cap vs. $12,000 Sales cap disparity.",
            decision_outcome="Throttle engaged — micro model enforced for all Marketing requests",
            risk_level="medium", timestamp=now - timedelta(days=120, hours=16),
        ),
        models.AuditEvent(
            event_type="ROUTING", department="Sales", agent_id=se.id,
            model_tier="flagship", context_snapshot=snap("Sales", 110),
            prompt_payload="NDA and non-compete review for pending $2.4M enterprise acquisition — legal team needs response before Thursday board meeting.",
            rationale="FLAGSHIP MODEL INVOKED. Terms 'NDA', 'non-compete', 'acquisition', 'board' — multiple escalation triggers. Critical deal flagged for legal review.",
            decision_outcome="flagship model used — $0.041800",
            risk_level="critical", timestamp=now - timedelta(days=110, hours=11),
        ),
        models.AuditEvent(
            event_type="DECISION", department="Support", agent_id=s2.id,
            model_tier="none", context_snapshot=snap("Support", 100),
            prompt_payload="Patient file 7842-B: Medication list and diagnosis notes need to be included in the AI summary for care coordination.",
            rationale="HIPAA block: Medical terminology and patient data detected. Blocked. HIPAA-protected health information cannot be sent to external AI model.",
            decision_outcome="Request blocked by sensitive term policy",
            risk_level="critical", timestamp=now - timedelta(days=100, hours=14),
        ),
        models.AuditEvent(
            event_type="LOCK", department="Support", agent_id=ce.id,
            model_tier="none", context_snapshot=snap("Support", 90),
            prompt_payload=None,
            rationale="CONCURRENCY LOCK. SF-CaseEscalator and SF-SupportBot-1 both targeting ticket #8,844. Locked. This is the 4th collision this year — zero data corruption across all incidents.",
            decision_outcome="Both agents locked — supervisor action required",
            risk_level="high", timestamp=now - timedelta(days=90, hours=10),
        ),
        models.AuditEvent(
            event_type="ROUTING", department="Operations", agent_id=ir.id,
            model_tier="flagship", context_snapshot=snap("Operations", 80),
            prompt_payload="GDPR right-to-erasure request received from EU customer — 847 records must be identified, audited, and purged within 72-hour regulatory window.",
            rationale="FLAGSHIP MODEL INVOKED. 'GDPR' + 'erasure' + regulatory deadline. EU data residency and compliance flags applied. Immutable record for DPA.",
            decision_outcome="flagship model used — $0.036600",
            risk_level="critical", timestamp=now - timedelta(days=80, hours=9),
        ),
        models.AuditEvent(
            event_type="THROTTLE", department="Marketing", agent_id=None,
            model_tier="micro", context_snapshot=snap("Marketing", 65),
            prompt_payload=None,
            rationale="BUDGET CAP ENFORCED. Month 11 — Marketing throttled day 12. Micro model has handled 94,300 Marketing calls this year at micro rates. Estimated savings vs. all-flagship: $112,400.",
            decision_outcome="Throttle engaged — micro model enforced for all Marketing requests",
            risk_level="medium", timestamp=now - timedelta(days=65, hours=16),
        ),
        models.AuditEvent(
            event_type="ROUTING", department="Support", agent_id=sa.id,
            model_tier="flagship", context_snapshot=snap("Support", 55),
            prompt_payload="Class action lawsuit filing — 340 enterprise clients seeking damages for data breach. Legal team requesting all AI interaction logs for discovery.",
            rationale="FLAGSHIP MODEL INVOKED. Terms 'class action', 'lawsuit', 'data breach', 'discovery' — critical. Full audit trail being compiled. 10 months of immutable records available.",
            decision_outcome="flagship model used — $0.048200",
            risk_level="critical", timestamp=now - timedelta(days=55, hours=8),
        ),
        models.AuditEvent(
            event_type="DECISION", department="Sales", agent_id=s6.id,
            model_tier="none", context_snapshot=snap("Sales", 45),
            prompt_payload="Wire transfer routing number 021000021 account 8472910384 — $180,000 Q4 payment from Global Corp.",
            rationale="PII block: Bank routing number and account number detected. Financial data block triggered. Highest-risk PII category.",
            decision_outcome="Request blocked by sensitive term policy",
            risk_level="critical", timestamp=now - timedelta(days=45, hours=13),
        ),
        models.AuditEvent(
            event_type="ROUTING", department="Sales", agent_id=ob.id,
            model_tier="flagship", context_snapshot=snap("Sales", 35),
            prompt_payload="Meridian's legal team needs the NDA reviewed before the enterprise acquisition closes next Thursday. Board approval required.",
            rationale="FLAGSHIP MODEL INVOKED. Terms 'NDA', 'acquisition', 'board' — Year 1 repeat pattern confirms these workflows need flagship routing. Budget: 35.2% used.",
            decision_outcome="flagship model used — $0.026800",
            risk_level="high", timestamp=now - timedelta(days=35, hours=11),
        ),
        models.AuditEvent(
            event_type="THROTTLE", department="Marketing", agent_id=None,
            model_tier="micro", context_snapshot=snap("Marketing", 20),
            prompt_payload=None,
            rationale="BUDGET CAP ENFORCED. Final month of year 1 — Marketing throttled for 12th consecutive month. Cumulative throttle protection: prevented $74,200 in budget overruns across the year.",
            decision_outcome="Throttle engaged — micro model enforced for all Marketing requests",
            risk_level="medium", timestamp=now - timedelta(days=20, hours=16),
        ),
        models.AuditEvent(
            event_type="ROUTING", department="Support", agent_id=ce.id,
            model_tier="flagship", context_snapshot=snap("Support", 12),
            prompt_payload="Our SLA guarantees 99.9% uptime — we are in breach. 4-hour outage affecting 2,000 enterprise users. Legal review and formal incident report required immediately.",
            rationale="FLAGSHIP MODEL INVOKED. Terms 'breach', 'SLA', 'legal'. Budget: 73.0% used ($5,842 of $8,000 cap).",
            decision_outcome="flagship model used — $0.038400",
            risk_level="high", timestamp=now - timedelta(days=12, hours=9),
        ),
        models.AuditEvent(
            event_type="ROUTING", department="Operations", agent_id=co.id,
            model_tier="flagship", context_snapshot=snap("Operations", 8),
            prompt_payload="Annual HIPAA compliance audit — all AI-processed health-adjacent communications must be reviewed and certified before Jan 1 renewal.",
            rationale="FLAGSHIP MODEL INVOKED. 'HIPAA' + annual audit cycle. Year-end compliance certification workflow. Full audit trail exported.",
            decision_outcome="flagship model used — $0.029400",
            risk_level="critical", timestamp=now - timedelta(days=8, hours=14),
        ),
        models.AuditEvent(
            event_type="LOCK", department="Sales", agent_id=se.id,
            model_tier="none", context_snapshot=snap("Sales", 5),
            prompt_payload=None,
            rationale="CONCURRENCY LOCK. SF-SalesEnrich-2 and SF-OpportunityBot targeting crm_records #12,847. Locked. Year 1 total: 27 agent collisions resolved — zero data corruption events.",
            decision_outcome="Both agents locked — supervisor action required",
            risk_level="high", timestamp=now - timedelta(days=5, hours=11),
        ),

        # ── Most recent events (last 7 days) ──────────────────────────────────

        models.AuditEvent(
            event_type="ROUTING", department="Sales", agent_id=se.id,
            model_tier="flagship", context_snapshot=snap("Sales", 4),
            prompt_payload="Contact Sarah Whitfield at s.whitfield@techpartners.io (212) 555-0391 to discuss Q1 renewal and NDA amendment.",
            rationale="FLAGSHIP MODEL INVOKED. PII: Email and phone detected. Term 'NDA' escalated. Pruned: 1,840 tokens saved.",
            decision_outcome="flagship model used — $0.018900",
            risk_level="high", timestamp=now - timedelta(days=4, hours=9, minutes=47),
        ),
        models.AuditEvent(
            event_type="DECISION", department="Support", agent_id=sa.id,
            model_tier="none", context_snapshot=snap("Support", 3),
            prompt_payload="Customer SSN 472-88-3910 needs to be updated in Salesforce before the next compliance audit.",
            rationale="HIPAA block: SSN detected. Year 1 total: 11 SSN/HIPAA blocks. Zero health data sent to AI.",
            decision_outcome="Request blocked by sensitive term policy",
            risk_level="critical", timestamp=now - timedelta(days=3, hours=14, minutes=8),
        ),
        models.AuditEvent(
            event_type="DECISION", department="Sales", agent_id=ob.id,
            model_tier="none", context_snapshot=snap("Sales", 2),
            prompt_payload="Process payment for Visa card 4532-1234-5678-9012 expiry 09/27 CVV 412 for the Q3 renewal.",
            rationale="PII block: Credit card (Visa pattern). Year 1: 8 payment blocks. Zero financial PII reached AI model.",
            decision_outcome="Request blocked by sensitive term policy",
            risk_level="critical", timestamp=now - timedelta(days=2, hours=10, minutes=22),
        ),
        models.AuditEvent(
            event_type="ROUTING", department="Support", agent_id=sa.id,
            model_tier="flagship", context_snapshot=snap("Support", 1),
            prompt_payload="Full RCA for outage required. Formal incident report for board, SLA credit per clause 12.4, enterprise client communications package.",
            rationale="FLAGSHIP MODEL INVOKED. High complexity. Board and SLA terms escalated. Pruned: 2,180 tokens saved before routing. Cost: $0.034600.",
            decision_outcome="flagship model used — $0.034600",
            risk_level="medium", timestamp=now - timedelta(hours=22, minutes=52),
        ),
        models.AuditEvent(
            event_type="THROTTLE", department="Marketing", agent_id=None,
            model_tier="micro", context_snapshot=snap("Marketing", 0),
            prompt_payload=None,
            rationale="BUDGET CAP ENFORCED. Marketing reached 101.7% of $6,000 monthly cap (spend: $6,104.80). Micro model enforced. Year 1: 12 consecutive monthly throttle events — $74,200 in overruns prevented.",
            decision_outcome="Throttle engaged — micro model enforced for all Marketing requests",
            risk_level="medium", timestamp=now - timedelta(hours=5, minutes=41),
        ),
        models.AuditEvent(
            event_type="ROUTING", department="Support", agent_id=ce.id,
            model_tier="flagship", context_snapshot=snap("Support", 0),
            prompt_payload="SLA breach — 4 hours downtime, 2,000 enterprise users affected. Legal review and board incident report required.",
            rationale="FLAGSHIP MODEL INVOKED. 'breach', 'SLA', 'legal'. Budget: 73.0% used. Pruned: 1,960 tokens saved.",
            decision_outcome="flagship model used — $0.038400",
            risk_level="high", timestamp=now - timedelta(minutes=34),
        ),
        models.AuditEvent(
            event_type="LOCK", department="Sales", agent_id=se.id,
            model_tier="none", context_snapshot=snap("Sales", 0),
            prompt_payload=None,
            rationale="CONCURRENCY LOCK. SF-SalesEnrich-1 and SF-OpportunityBot simultaneously targeting crm_records #13,241. Locked by Traffic Cop. No data written. Zero data corruption.",
            decision_outcome="Both agents locked — supervisor action required",
            risk_level="high", timestamp=now - timedelta(hours=7, minutes=3),
        ),
    ]

    db.add_all(audit_events)
    db.commit()
    print(f"   → {len(audit_events)} audit events spread across 365 days")

    db.close()
    print("\n✅ Enterprise demo loaded — 1 year of adoption curve data")
    print(f"   → 12 agents · 4 departments · {len(transactions):,} transactions · {len(audit_events)} audit events")
    print("   → Phase 1 (100/day) → Phase 2 (250/day) → Phase 3 (500/day)")
    print("   → Marketing THROTTLED monthly · All range buttons have real data")
    print("   Refresh your dashboard.\n")


if __name__ == "__main__":
    populate_enterprise()
