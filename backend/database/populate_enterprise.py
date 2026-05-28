"""
populate_enterprise.py — Load the CostPilot dashboard with 1-year enterprise-scale demo data.

Simulates: Meridian Financial Group
  - Realistic AI adoption curve over 365 days
  - Phase 1 (days 1-60):   Early rollout — 100 calls/day, 50% flagship, 60% pruned
  - Phase 2 (days 61-180): Expansion — 250 calls/day, 40% flagship, 72% pruned
  - Phase 3 (days 181-365): Full scale — 500 calls/day, 35% flagship, 76% pruned
  - ~139,000 total transactions across 5 departments (incl. Engineering)
  - Marketing throttled (hits cap repeatedly in Phase 3)
  - Engineering added: code lane, secrets blocks, runaway loop throttle
  - 50 audit events spread across the year — visible in Risk tab timeline
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
        # Support: raw payload logging ON (30-day retention) — partners can see View Original immediately
        dict(department="Support",    monthly_cap_usd=8000.00,  current_spend_usd=5842.10,  throttled=False, override_granted=False, throttle_tier=2, raw_payload_logging_enabled=True,  raw_retention_days=30),
        dict(department="Sales",      monthly_cap_usd=12000.00, current_spend_usd=4218.60,  throttled=False, override_granted=False, throttle_tier=2, raw_payload_logging_enabled=False, raw_retention_days=30),
        dict(department="Marketing",  monthly_cap_usd=6000.00,  current_spend_usd=6104.80,  throttled=True,  override_granted=False, throttle_tier=1, raw_payload_logging_enabled=False, raw_retention_days=30),
        dict(department="Operations",  monthly_cap_usd=4000.00,  current_spend_usd=1289.40,  throttled=False, override_granted=False, throttle_tier=1, raw_payload_logging_enabled=False, raw_retention_days=30),
        # Engineering: code lane active, secrets detection, raw logging on for forensics
        dict(department="Engineering", monthly_cap_usd=200.00,   current_spend_usd=171.40,   throttled=False, override_granted=False, throttle_tier=3, raw_payload_logging_enabled=True,  raw_retention_days=90),
    ]
    for bd in budget_data:
        b = db.query(models.DepartmentBudget).filter_by(department=bd["department"]).first()
        if b:
            b.monthly_cap_usd              = bd["monthly_cap_usd"]
            b.current_spend_usd            = bd["current_spend_usd"]
            b.throttled                    = bd["throttled"]
            b.override_granted             = bd["override_granted"]
            b.throttle_tier                = bd["throttle_tier"]
            b.raw_payload_logging_enabled  = bd["raw_payload_logging_enabled"]
            b.raw_retention_days           = bd["raw_retention_days"]
            b.period_start                 = period_start
        else:
            db.add(models.DepartmentBudget(period_start=period_start, **bd))
    db.commit()

    # ── Agents — 12 registered ─────────────────────────────────────────────────
    print("Setting up enterprise agent registry...")
    db.query(models.RegisteredAgent).delete()
    db.commit()

    agent_defs = [
        dict(name="SF-SupportBot-1",   source_platform="Salesforce",   department="Support",    permissions="read,write",        target_table="tickets",            collision_policy="lock",  status="idle"),
        dict(name="SF-SupportBot-2",   source_platform="Salesforce",   department="Support",    permissions="read,write",        target_table="tickets",            collision_policy="lock",  status="idle"),
        dict(name="SF-CaseEscalator",  source_platform="Salesforce",   department="Support",    permissions="read,write",        target_table="tickets",            collision_policy="queue", status="idle"),
        dict(name="SF-BillingAuditor", source_platform="Salesforce",   department="Support",    permissions="read",              target_table="token_transactions",  collision_policy="lock",  status="idle"),
        dict(name="SF-SalesEnrich-1",  source_platform="Salesforce",   department="Sales",      permissions="read,write",        target_table="crm_records",        collision_policy="queue", status="idle"),
        dict(name="SF-SalesEnrich-2",  source_platform="Salesforce",   department="Sales",      permissions="read,write",        target_table="crm_records",        collision_policy="queue", status="idle"),
        dict(name="SF-OpportunityBot", source_platform="Salesforce",   department="Sales",      permissions="read,write",        target_table="crm_records",        collision_policy="lock",  status="idle"),
        dict(name="SF-MarketingMailer",source_platform="Salesforce",   department="Marketing",  permissions="read",              target_table="customers",          collision_policy="skip",  status="idle"),
        dict(name="SF-CampaignBot",    source_platform="Salesforce",   department="Marketing",  permissions="read,write",        target_table="crm_records",        collision_policy="skip",  status="idle"),
        dict(name="SF-OpsLogger",      source_platform="Salesforce",   department="Operations", permissions="read,write,delete", target_table="crm_records",        collision_policy="lock",  status="idle"),
        dict(name="SF-ComplianceBot",  source_platform="Salesforce",   department="Operations", permissions="read",              target_table="audit_events",       collision_policy="lock",  status="idle"),
        dict(name="SN-IncidentRouter",  source_platform="ServiceNow",   department="Operations", permissions="read,write",        target_table="tickets",            collision_policy="queue", status="idle"),
        # Engineering agents — coding assistants and CI pipeline bots
        dict(name="cursor-jsmith",       source_platform="Custom",        department="Engineering", permissions="read,write",        target_table="tickets",            collision_policy="lock",  status="idle"),
        dict(name="devin-prod-agent",    source_platform="Custom",        department="Engineering", permissions="read,write",        target_table="tickets",            collision_policy="queue", status="idle"),
        dict(name="github-copilot-ci",   source_platform="GitHub",        department="Engineering", permissions="read",              target_table="tickets",            collision_policy="skip",  status="idle"),
        dict(name="claude-code-backend", source_platform="Custom",        department="Engineering", permissions="read,write",        target_table="tickets",            collision_policy="lock",  status="idle"),
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
        "Support":     [a for a in agents if a.department == "Support"],
        "Sales":       [a for a in agents if a.department == "Sales"],
        "Marketing":   [a for a in agents if a.department == "Marketing"],
        "Operations":  [a for a in agents if a.department == "Operations"],
        "Engineering": [a for a in agents if a.department == "Engineering"],
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
        "Support":     0.30,
        "Sales":       0.25,
        "Marketing":   0.20,
        "Operations":  0.15,
        "Engineering": 0.10,   # code lane — higher tier mix, lower volume
    }

    BATCH_SIZE = 500   # commit every 500 rows — keeps memory low on Heroku
    batch = []
    total_inserted = 0

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
                    elif dept == "Engineering":
                        # Engineering agents use flagship more — code review / complex tasks
                        is_complex = random.random() < min(complex_pct + 0.20, 0.70)
                        reason = "COMPLEX" if is_complex else "ROUTINE"
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

                    batch.append(models.TokenTransaction(
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

                    if len(batch) >= BATCH_SIZE:
                        db.add_all(batch)
                        db.commit()
                        total_inserted += len(batch)
                        batch = []

    # Flush any remaining
    if batch:
        db.add_all(batch)
        db.commit()
        total_inserted += len(batch)

    print(f"   → {total_inserted:,} transactions inserted across 3 adoption phases")

    # ── Today's transactions — always included so spend_today is non-zero ─────
    print("Inserting today's transactions...")
    today_data = [
        ("Support",    agents[0].id, "flagship", 12400, 3200, True,  1840, "COMPLEX"),
        ("Support",    agents[1].id, "micro",     1800,  420, True,   640, "ROUTINE"),
        ("Support",    agents[2].id, "flagship",  9800, 2600, True,  2100, "COMPLEX"),
        ("Sales",      agents[4].id, "flagship", 14200, 3800, True,  2240, "COMPLEX"),
        ("Sales",      agents[5].id, "micro",     2100,  580, True,   720, "ROUTINE"),
        ("Sales",      agents[6].id, "flagship", 11600, 3100, True,  1980, "COMPLEX"),
        ("Marketing",  agents[7].id, "micro",     1600,  380, True,   510, "THROTTLED"),
        ("Marketing",  agents[8].id, "micro",     1900,  440, True,   590, "THROTTLED"),
        ("Operations", agents[9].id, "flagship",  8400, 2200, True,  1640, "COMPLEX"),
        ("Operations", agents[10].id,"micro",     1400,  320, False,    0, "ROUTINE"),
        ("Support",     agents[0].id,  "micro",     2200,  510, True,   780, "ROUTINE"),
        ("Sales",       agents[4].id,  "micro",     1700,  390, True,   430, "ROUTINE"),
        # Engineering today — code reviews and architecture queries, no pruning (code lane)
        ("Engineering", agents[12].id, "flagship", 10400, 2800, False,    0, "COMPLEX"),
        ("Engineering", agents[13].id, "flagship", 13200, 3600, False,    0, "COMPLEX"),
        ("Engineering", agents[14].id, "micro",     2800,  620, False,    0, "ROUTINE"),
        ("Engineering", agents[15].id, "flagship",  9100, 2400, False,    0, "COMPLEX"),
    ]
    today_batch = []
    for dept, agent_id, tier, inp, out, pruned, saved, reason in today_data:
        if tier == "flagship":
            cost = round((inp * FLAGSHIP_IN) + (out * FLAGSHIP_OUT), 6)
        else:
            cost = round((inp * MICRO_IN) + (out * MICRO_OUT), 6)
        hour   = random.randint(6, 14)
        minute = random.randint(0, 59)
        ts     = today + timedelta(hours=hour, minutes=minute)
        today_batch.append(models.TokenTransaction(
            department     = dept,
            agent_id       = agent_id,
            model_tier     = tier,
            input_tokens   = inp,
            output_tokens  = out,
            cost_usd       = cost,
            routing_reason = reason,
            was_pruned     = pruned,
            tokens_saved   = saved,
            timestamp      = ts,
        ))
    db.add_all(today_batch)
    db.commit()
    total_inserted += len(today_batch)
    print(f"   → {len(today_batch)} transactions inserted for today")

    # ── 40 audit events spread across the year ────────────────────────────────
    print("Building enterprise audit events (spread across 365 days)...")

    def snap(dept, days_ago=0):
        caps  = {"Support": 8000, "Sales": 12000, "Marketing": 6000, "Operations": 4000, "Engineering": 200}
        spent = {"Support": 5842.10, "Sales": 4218.60, "Marketing": 6104.80, "Operations": 1289.40, "Engineering": 171.40}
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
    cj = agents[12]  # cursor-jsmith
    dv = agents[13]  # devin-prod-agent
    gh = agents[14]  # github-copilot-ci
    cc = agents[15]  # claude-code-backend

    audit_events = [

        # ── PHASE 1: Early rollout (days 300-365 ago) ─────────────────────────

        models.AuditEvent(
            event_type="ROUTING", department="Support", agent_id=sa.id,
            model_tier="flagship", context_snapshot=snap("Support", 355),
            prompt_payload="Initial deployment — first enterprise support case routed through CostPilot. Testing NDA review workflow.",
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

        # ── Engineering lane events ────────────────────────────────────────────

        models.AuditEvent(
            event_type="DECISION", department="Engineering", agent_id=cj.id,
            model_tier="none", context_snapshot=snap("Engineering", 280),
            prompt_payload="def connect_db():\n    conn = psycopg2.connect(host='prod-db.internal', user='admin', password='Sup3rS3cr3t!', database='orders')\n    return conn",
            rationale="CODE SECRETS BLOCK. Keyword match: 'password=' detected in code payload. Hardcoded database credential intercepted before reaching AI model. cursor-jsmith agent — day 85 of Engineering rollout. Developer notified to use environment variables.",
            decision_outcome="Request blocked by sensitive term policy",
            risk_level="critical", timestamp=now - timedelta(days=280, hours=11),
        ),
        models.AuditEvent(
            event_type="ROUTING", department="Engineering", agent_id=cc.id,
            model_tier="flagship", context_snapshot=snap("Engineering", 240),
            prompt_payload="Review this Python service for architecture issues — handles payment processing, async queue consumers, and database connection pooling across 3 microservices.",
            rationale="CODE LANE — auto-detected: AUTO-DETECTED as code: code definition pattern matched — pruner bypassed. FLAGSHIP MODEL INVOKED. Complex multi-service architecture review. Pruner bypassed to preserve code structure.",
            decision_outcome="flagship model used — $0.031800",
            risk_level="medium", timestamp=now - timedelta(days=240, hours=14),
        ),
        models.AuditEvent(
            event_type="DECISION", department="Engineering", agent_id=dv.id,
            model_tier="none", context_snapshot=snap("Engineering", 195),
            prompt_payload="AKIAIOSFODNN7EXAMPLE — use this AWS key to deploy the Lambda function to prod. Access key ID above, secret: wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            rationale="CODE SECRETS BLOCK. Regex match: AWS Access Key ID pattern (AKIA...) detected. Secret access key also present. Both credentials intercepted. devin-prod-agent blocked before AI call. AWS key rotation recommended immediately.",
            decision_outcome="Request blocked by sensitive term policy",
            risk_level="critical", timestamp=now - timedelta(days=195, hours=9),
        ),
        models.AuditEvent(
            event_type="THROTTLE", department="Engineering", agent_id=dv.id,
            model_tier="micro", context_snapshot=snap("Engineering", 155),
            prompt_payload=None,
            rationale="BUDGET CAP ENFORCED — RUNAWAY LOOP DETECTED. devin-prod-agent submitted 847 code review requests in 4 hours — agent entered recursive self-improvement loop. Engineering throttled at Advisor ceiling. Loop broken at 89.2% of $200 monthly cap. On-call engineer paged.",
            decision_outcome="Throttle engaged — Advisor model ceiling enforced for all Engineering requests",
            risk_level="critical", timestamp=now - timedelta(days=155, hours=3),
        ),
        models.AuditEvent(
            event_type="DECISION", department="Engineering", agent_id=gh.id,
            model_tier="none", context_snapshot=snap("Engineering", 118),
            prompt_payload="CI pipeline config: STRIPE_SECRET=sk_live_4eC39HqLyjWDarjtT7vdqe8 — inject into Lambda environment before deployment.",
            rationale="CODE SECRETS BLOCK. Regex match: Stripe secret key (sk_live_...) pattern detected. Live production Stripe key intercepted in CI config. github-copilot-ci blocked. Key immediately invalidated per incident response procedure.",
            decision_outcome="Request blocked by sensitive term policy",
            risk_level="critical", timestamp=now - timedelta(days=118, hours=16),
        ),
        models.AuditEvent(
            event_type="ROUTING", department="Engineering", agent_id=cj.id,
            model_tier="flagship", context_snapshot=snap("Engineering", 72),
            prompt_payload="SELECT u.id, u.email, o.total FROM users u JOIN orders o ON u.id = o.user_id WHERE o.created_at > NOW() - INTERVAL '30 days' AND o.status = 'failed' ORDER BY o.total DESC LIMIT 100;",
            rationale="CODE LANE — auto-detected: AUTO-DETECTED as code: code definition pattern matched (SELECT) — pruner bypassed. FLAGSHIP MODEL INVOKED. SQL query optimization and index recommendation. Pruner bypassed to preserve query syntax.",
            decision_outcome="flagship model used — $0.019400",
            risk_level="low", timestamp=now - timedelta(days=72, hours=10),
        ),
        models.AuditEvent(
            event_type="DECISION", department="Engineering", agent_id=cc.id,
            model_tier="none", context_snapshot=snap("Engineering", 38),
            prompt_payload="Header: Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkFkbWluVXNlciIsImlhdCI6MTUxNjIzOTAyMn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c — use this prod JWT to test the endpoint.",
            rationale="CODE SECRETS BLOCK. Regex match: JWT token (eyJ...) pattern detected. Production admin JWT token intercepted. claude-code-backend blocked. Token revoked and re-issued. Prod credential hygiene violation logged.",
            decision_outcome="Request blocked by sensitive term policy",
            risk_level="critical", timestamp=now - timedelta(days=38, hours=13),
        ),
        models.AuditEvent(
            event_type="ROUTING", department="Engineering", agent_id=gh.id,
            model_tier="micro", context_snapshot=snap("Engineering", 14),
            prompt_payload="Unit test coverage report: 47% → 68% after refactor. All 214 tests passing. Review the attached diff for the payment service and confirm the mocking strategy is correct.",
            rationale="CODE LANE — explicit payload_type=code declared by caller. MICRO MODEL — routine code review. Test coverage analysis and mock strategy validation. No secrets detected. Pruner bypassed (code lane).",
            decision_outcome="micro model used — $0.001840",
            risk_level="low", timestamp=now - timedelta(days=14, hours=11),
        ),
        models.AuditEvent(
            event_type="ROUTING", department="Engineering", agent_id=dv.id,
            model_tier="flagship", context_snapshot=snap("Engineering", 2),
            prompt_payload="Full architecture review: event-driven microservices migration plan, Kafka topic design, consumer group strategy, and dead-letter queue configuration for the order processing pipeline.",
            rationale="CODE LANE — auto-detected: AUTO-DETECTED as code: high code-character density — pruner bypassed. FLAGSHIP MODEL INVOKED. Complex architecture design. Budget: 85.7% used ($171.40 of $200 cap). Approaching cap — throttle pending.",
            decision_outcome="flagship model used — $0.038200",
            risk_level="medium", timestamp=now - timedelta(days=2, hours=15, minutes=22),
        ),
    ]

    db.add_all(audit_events)
    db.commit()
    print(f"   → {len(audit_events)} audit events spread across 365 days")

    db.close()
    print("\n✅ Enterprise demo loaded — 1 year of adoption curve data")
    print(f"   → 16 agents · 5 departments · {total_inserted:,} transactions · {len(audit_events)} audit events")
    print("   → Phase 1 (100/day) → Phase 2 (250/day) → Phase 3 (500/day)")
    print("   → Marketing THROTTLED monthly · Engineering code lane active · All range buttons have real data")
    print("   Refresh your dashboard.\n")


if __name__ == "__main__":
    populate_enterprise()
