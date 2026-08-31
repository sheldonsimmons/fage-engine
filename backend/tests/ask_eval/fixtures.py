"""
tests/ask_eval/fixtures.py — the one deterministic dataset the Ask
CostPilot evaluation corpus (corpus.py) is written against.

Every expected numeric result in the corpus is computable by hand from
TRUTH below, not re-derived at test time from the same code being
tested (that would just be testing the code against itself). Dates are
relative to "now" (computed at build time), not hardcoded, so the
fixture stays valid whenever the suite actually runs -- same convention
already used by the rest of this test suite.

Shape: one workspace ("WS-EVAL"), 3 departments, 4 agents (one
deliberately never used, for agent-adoption questions), 5 WorkItems
covering won/lost/open opportunities and resolved/unresolved support
cases, and a two-month TokenTransaction spread (this month + last
month) so trend/comparison questions have a real answer to check
against.
"""
from datetime import datetime, timedelta

from database.models import (
    DepartmentBudget, RegisteredAgent, TokenTransaction, WorkAccount,
    WorkItem, WorkItemOutcome,
)

WORKSPACE_ID = "WS-EVAL"


def build_eval_fixture(db) -> dict:
    now = datetime.utcnow()
    this_month = now - timedelta(days=5)      # safely inside "this_month"/"last_30_days"
    last_month = now - timedelta(days=40)     # safely inside "last_month", outside "this_month"

    # ── Departments / budgets ────────────────────────────────────────────
    db.add_all([
        DepartmentBudget(department=f"{WORKSPACE_ID}:Sales", monthly_cap_usd=100.0,
                          current_spend_usd=20.0, workspace_id=WORKSPACE_ID),
        DepartmentBudget(department=f"{WORKSPACE_ID}:Support", monthly_cap_usd=50.0,
                          current_spend_usd=6.5, workspace_id=WORKSPACE_ID),
        DepartmentBudget(department=f"{WORKSPACE_ID}:Engineering", monthly_cap_usd=30.0,
                          current_spend_usd=0.0, workspace_id=WORKSPACE_ID),
    ])

    # ── Agents ───────────────────────────────────────────────────────────
    agent_opp = RegisteredAgent(name="SF-OpportunityBot", department=f"{WORKSPACE_ID}:Sales",
                                 permissions="read,write", source_platform="Salesforce",
                                 workspace_id=WORKSPACE_ID, status="idle",
                                 last_used_at=this_month)
    agent_case = RegisteredAgent(name="SF-CaseBot", department=f"{WORKSPACE_ID}:Support",
                                  permissions="read,write", source_platform="Salesforce",
                                  workspace_id=WORKSPACE_ID, status="idle",
                                  last_used_at=this_month)
    agent_incident = RegisteredAgent(name="SN-IncidentBot", department=f"{WORKSPACE_ID}:Support",
                                      permissions="read,write", source_platform="ServiceNow",
                                      workspace_id=WORKSPACE_ID, status="idle",
                                      last_used_at=this_month)
    # Deliberately never used -- the fixture's one "inactive agent" /
    # "never used" case, registered but with zero transactions.
    agent_unused = RegisteredAgent(name="Custom-DevAgent", department=f"{WORKSPACE_ID}:Engineering",
                                    permissions="read,write", source_platform="Custom",
                                    workspace_id=WORKSPACE_ID, status="idle", last_used_at=None)
    db.add_all([agent_opp, agent_case, agent_incident, agent_unused])
    db.flush()

    # ── Accounts / WorkItems / Outcomes ─────────────────────────────────
    acc_acme = WorkAccount(external_id="ACC-ACME", name="Acme Corp", workspace_id=WORKSPACE_ID)
    acc_globex = WorkAccount(external_id="ACC-GLOBEX", name="Globex Inc", workspace_id=WORKSPACE_ID)
    acc_initech = WorkAccount(external_id="ACC-INITECH", name="Initech", workspace_id=WORKSPACE_ID)
    acc_umbrella = WorkAccount(external_id="ACC-UMBRELLA", name="Umbrella Co", workspace_id=WORKSPACE_ID)
    acc_wayne = WorkAccount(external_id="ACC-WAYNE", name="Wayne Enterprises", workspace_id=WORKSPACE_ID)
    db.add_all([acc_acme, acc_globex, acc_initech, acc_umbrella, acc_wayne])
    db.flush()

    wi_acme = WorkItem(external_id="WI-ACME-1", name="Acme Corp — Renewal", account_id=acc_acme.id,
                        workspace_id=WORKSPACE_ID, context_type="opportunity",
                        department=f"{WORKSPACE_ID}:Sales")
    wi_globex = WorkItem(external_id="WI-GLOBEX-1", name="Globex Inc — New Business", account_id=acc_globex.id,
                          workspace_id=WORKSPACE_ID, context_type="opportunity",
                          department=f"{WORKSPACE_ID}:Sales")
    wi_initech = WorkItem(external_id="WI-INITECH-1", name="Initech — Expansion", account_id=acc_initech.id,
                           workspace_id=WORKSPACE_ID, context_type="opportunity",
                           department=f"{WORKSPACE_ID}:Sales")
    wi_umbrella = WorkItem(external_id="WI-UMBRELLA-1", name="Umbrella Co — Case #1", account_id=acc_umbrella.id,
                            workspace_id=WORKSPACE_ID, context_type="case",
                            department=f"{WORKSPACE_ID}:Support")
    wi_wayne = WorkItem(external_id="WI-WAYNE-1", name="Wayne Ent — Case #1", account_id=acc_wayne.id,
                         workspace_id=WORKSPACE_ID, context_type="case",
                         department=f"{WORKSPACE_ID}:Support")
    db.add_all([wi_acme, wi_globex, wi_initech, wi_umbrella, wi_wayne])
    db.flush()

    db.add_all([
        WorkItemOutcome(work_item_id=wi_acme.id, workspace_id=WORKSPACE_ID,
                         source_system="Salesforce", source_object="Opportunity", external_id="OPP-ACME",
                         outcome_status="Closed Won", outcome_success=True, is_closed=True, outcome_value=50000.0),
        WorkItemOutcome(work_item_id=wi_globex.id, workspace_id=WORKSPACE_ID,
                         source_system="Salesforce", source_object="Opportunity", external_id="OPP-GLOBEX",
                         outcome_status="Closed Lost", outcome_success=False, is_closed=True, outcome_value=0.0),
        WorkItemOutcome(work_item_id=wi_initech.id, workspace_id=WORKSPACE_ID,
                         source_system="Salesforce", source_object="Opportunity", external_id="OPP-INITECH",
                         outcome_status="Negotiation", outcome_success=None, is_closed=False, outcome_value=20000.0),
        WorkItemOutcome(work_item_id=wi_umbrella.id, workspace_id=WORKSPACE_ID,
                         source_system="Salesforce", source_object="Case", external_id="CASE-UMBRELLA",
                         outcome_status="Resolved", outcome_success=True, is_closed=True),
        WorkItemOutcome(work_item_id=wi_wayne.id, workspace_id=WORKSPACE_ID,
                         source_system="ServiceNow", source_object="Incident", external_id="INC-WAYNE",
                         outcome_status="In Progress", outcome_success=None, is_closed=False),
    ])

    # ── Token transactions ──────────────────────────────────────────────
    def _txns(agent, work_item, department, platform, count, unit_cost, when):
        for _ in range(count):
            db.add(TokenTransaction(
                department=department, workspace_id=WORKSPACE_ID, agent_id=agent.id,
                work_item_id=work_item.id if work_item else None,
                source_platform=platform, model_tier="Scout",
                input_tokens=100, output_tokens=50, cost_usd=unit_cost, timestamp=when,
            ))

    # This month: Sales $20 (10 on Acme@$1, 6 on Globex@$1, 4 on Initech@$1);
    # Support $6.50 (5 on Umbrella@$0.50, 8 on Wayne@$0.50).
    _txns(agent_opp, wi_acme, f"{WORKSPACE_ID}:Sales", "Salesforce", 10, 1.00, this_month)
    _txns(agent_opp, wi_globex, f"{WORKSPACE_ID}:Sales", "Salesforce", 6, 1.00, this_month)
    _txns(agent_opp, wi_initech, f"{WORKSPACE_ID}:Sales", "Salesforce", 4, 1.00, this_month)
    _txns(agent_case, wi_umbrella, f"{WORKSPACE_ID}:Support", "Salesforce", 5, 0.50, this_month)
    _txns(agent_incident, wi_wayne, f"{WORKSPACE_ID}:Support", "ServiceNow", 8, 0.50, this_month)

    # Last month: Sales $8 (8 calls on Acme @ $1) -- for month-over-month
    # comparison questions (+150% spend change this month vs last month).
    _txns(agent_opp, wi_acme, f"{WORKSPACE_ID}:Sales", "Salesforce", 8, 1.00, last_month)

    db.commit()

    # TRUTH: hand-computable expected aggregates, referenced by corpus.py
    # instead of re-deriving them there. Keep this in sync with the seed
    # data above by construction, not by re-running the code under test.
    return {
        "workspace_id": WORKSPACE_ID,
        "this_month": {
            "total_spend_usd": 26.50,          # 20.00 + 6.50
            "total_requests": 33,               # 20 + 5 + 8
            "sales_spend_usd": 20.00,
            "sales_requests": 20,
            "support_spend_usd": 6.50,
            "support_requests": 13,
            "engineering_spend_usd": 0.0,
            "engineering_requests": 0,
        },
        "last_month": {
            "sales_spend_usd": 8.00,
            "sales_requests": 8,
        },
        "month_over_month": {
            "sales_spend_pct_change": 150.0,    # (20 - 8) / 8 * 100
        },
        "outcomes": {
            "won_count": 1,
            "lost_count": 1,
            "open_count": 1,
            "won_value_usd": 50000.0,
            "pipeline_value_usd": 20000.0,
            "support_cases_total": 2,
            "support_cases_resolved": 1,
            "ai_spend_on_won_usd": 10.00,        # 10 calls @ $1 on Acme, THIS MONTH only
            "ai_spend_on_lost_usd": 6.00,        # 6 calls @ $1 on Globex
            # compute_cost_per_outcome() is intentionally all-time, not
            # scoped to "this month" (see core/metrics_query.py's
            # comment on why: dividing a windowed spend number by an
            # all-time outcome count would silently understate cost-per-
            # outcome). Acme also has 8 calls @ $1 LAST month (same
            # fixture, used by the month-over-month comparison cases),
            # so the true all-time cost-per-won-opportunity is
            # (10 + 8) / 1 = 18.00, not just this month's 10.00.
            "cost_per_won_opportunity_usd": 18.00,
            "support_cost_per_resolution_usd": 2.50,  # 5 calls @ $0.50 on Umbrella
        },
        "budgets": {
            "sales_cap_usd": 100.0, "sales_used_pct": 20.0,
            "support_cap_usd": 50.0, "support_used_pct": 13.0,
            "engineering_cap_usd": 30.0, "engineering_used_pct": 0.0,
        },
        "agents": {
            "unused_agent_name": "Custom-DevAgent",
            "top_spend_agent_name": "SF-OpportunityBot",
            "top_spend_agent_usd": 20.00,
        },
    }
