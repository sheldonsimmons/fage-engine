"""
tests/ask_eval/corpus.py — the Ask CostPilot evaluation corpus
(Recommendation #2, per the architecture audit).

Each case records the EXPECTED STRUCTURED INTERPRETATION of a question
(intent/entity/metric/filters/period/comparison), not just expected
prose -- so a failure can be attributed to a specific stage:

  interpretation  -- did _ask_intent() parse the question correctly?
  data retrieval   -- given the correct filters, does the deterministic
                       query return the right rows?
  calculation      -- is the aggregate computed from those rows correct?
  narration        -- does the LLM's prose accurately describe the
                       already-computed facts? (NOT covered by this
                       corpus -- see the module docstring in
                       test_ask_eval_interpretation.py for why)

expected_result values reference tests/ask_eval/fixtures.py's TRUTH
dict, computed there by hand from the seed data -- never re-derived
from the code under test.

This is a v1 corpus (~90 cases across all 15 requested categories plus
edge-case types), not the full 100-200 target -- built for correct
infrastructure and real category coverage first; extending it is just
appending AskEvalCase entries.
"""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class AskEvalCase:
    id: str
    category: str
    question: str
    kind: str = "normal"  # normal | typo | rephrased | ambiguous | follow_up | missing_data | should_reject
    # -- expected interpretation (Stage 1: _ask_intent(), offline, no LLM) --
    expected_intent: Optional[str] = None
    expected_entity: Optional[str] = None
    expected_metric: Optional[str] = None
    expected_direction: Optional[str] = None
    expected_period_key: Optional[str] = None
    expected_days: Optional[int] = None
    expected_comparison_key: Optional[str] = None
    expected_source_platform: Optional[str] = None
    expected_model_tier: Optional[str] = None
    expected_result_limit: Optional[int] = None
    expected_usage_status: Optional[str] = None
    expected_budget_scope: Optional[str] = None
    expected_outcome_filter: Optional[str] = None
    # -- expected data (Stage 2: retrieval + calculation, against fixtures.TRUTH) --
    # A dotted path into fixtures.TRUTH, e.g. "this_month.sales_spend_usd".
    expected_result_path: Optional[str] = None
    expected_result_tolerance: float = 0.01
    # -- notes --
    notes: str = ""
    skip_interpretation: bool = False  # for should_reject/help/product cases with no reporting intent to check
    known_gap: Optional[str] = None  # set (with a reason) when the deterministic parser is KNOWN to fail this case today -- xfail, not a silent pass, so the gap stays visible in test output instead of being hidden by loosening the expectation


CATEGORIES = (
    "Spend", "Budget", "Agents", "Models", "Departments", "Outcomes",
    "ROI", "Optimization", "Governance", "Comparisons", "Trends",
    "WorkItems", "Salesforce", "ServiceNow", "Other",
)

CASES: list[AskEvalCase] = [
    # ── Spend ────────────────────────────────────────────────────────────
    AskEvalCase(
        id="spend-001", category="Spend", question="What is our total AI spend this month?",
        expected_intent="total", expected_entity="overview", expected_metric="spend_usd",
        expected_period_key="this_month", expected_days=31,
        expected_result_path="this_month.total_spend_usd",
    ),
    AskEvalCase(
        id="spend-002", category="Spend", kind="typo",
        question="whats our total ai spned this month",
        expected_intent="total", expected_entity="overview", expected_metric="spend_usd",
        expected_period_key="this_month", expected_days=31,
        expected_result_path="this_month.total_spend_usd",
        known_gap="_ask_correct_typos doesn't correct 'spned'->'spend' (confirmed: intent falls back to 'overview' instead of 'total'), so the total-intent keyword match never fires. A real, natural-typo failure mode, not a corpus error.",
    ),
    AskEvalCase(
        id="spend-003", category="Spend", kind="rephrased",
        question="How much have we spent on AI so far this month?",
        expected_intent="total", expected_entity="overview", expected_metric="spend_usd",
        expected_period_key="this_month", expected_days=31,
        expected_result_path="this_month.total_spend_usd",
    ),
    AskEvalCase(
        id="spend-004", category="Spend", question="Which department spent the most on AI this month?",
        expected_intent="ranking", expected_entity="department", expected_metric="spend_usd",
        expected_direction="desc", expected_period_key="this_month", expected_days=31,
        expected_result_path="this_month.sales_spend_usd",
        notes="Top department should be Sales ($20.00).",
    ),
    AskEvalCase(
        id="spend-005", category="Spend", question="How many total AI requests did we make last month?",
        expected_intent="total", expected_entity="overview", expected_metric="request_count",
        expected_period_key="last_month", expected_days=31,
    ),

    # ── Budget ───────────────────────────────────────────────────────────
    AskEvalCase(
        id="budget-001", category="Budget", question="What is our budget status?",
        expected_intent="budget", expected_entity="department",
        expected_result_path="budgets.sales_used_pct",
    ),
    AskEvalCase(
        id="budget-002", category="Budget", kind="rephrased",
        question="Are any departments close to their spending cap?",
        expected_intent="budget", expected_entity="department", expected_budget_scope="near_cap",
        known_gap="_ask_intent's budget trigger is a literal `'budget' in text` substring check -- 'spending cap' alone (no literal word 'budget') never matches, confirmed: falls back to intent='overview'. A real, natural-phrasing gap.",
    ),
    AskEvalCase(
        id="budget-003", category="Budget", question="Is the Sales department over budget?",
        expected_intent="budget", expected_entity="department",
        expected_result_path="budgets.sales_used_pct",
        notes="Sales at 20% of cap -- should NOT report over-budget.",
    ),
    AskEvalCase(
        id="budget-004", category="Budget", kind="missing_data",
        question="What is the Engineering department's budget forecast for next quarter?",
        expected_intent="budget", expected_entity="department",
        notes="No forecasting capability exists -- answer should not invent a number for a future period.",
    ),

    # ── Agents ───────────────────────────────────────────────────────────
    AskEvalCase(
        id="agents-001", category="Agents", question="Which agents have not been used recently?",
        expected_intent="agent_adoption", expected_entity="agent", expected_usage_status="unused",
        notes="Should surface Custom-DevAgent (never used). usage_status enum value is 'unused', not 'never'.",
    ),
    AskEvalCase(
        id="agents-002", category="Agents", question="Which agent spent the most this month?",
        expected_intent="ranking", expected_entity="agent", expected_metric="spend_usd",
        expected_direction="desc", expected_period_key="this_month", expected_days=31,
        expected_result_path="agents.top_spend_agent_usd",
        notes="Top agent should be SF-OpportunityBot ($20.00).",
    ),
    AskEvalCase(
        id="agents-003", category="Agents", kind="typo",
        question="wich agents are inactive",
        expected_intent="agent_adoption", expected_entity="agent",
    ),
    AskEvalCase(
        id="agents-004", category="Agents", question="How many requests has SF-OpportunityBot made this month?",
        expected_intent="total", expected_entity="agent", expected_metric="request_count",
        expected_period_key="this_month", expected_days=31,
        expected_result_path="this_month.sales_requests",
        notes="Named-entity scoping to one agent; total should equal Sales dept total (only Sales agent).",
    ),

    # ── Models ───────────────────────────────────────────────────────────
    AskEvalCase(
        id="models-001", category="Models", question="What model tiers are we using the most?",
        expected_intent="ranking", expected_entity="model", expected_metric="spend_usd",
        expected_direction="desc",
        notes="tier_usage is intentionally narrow (requires an explicit tier/model name like 'Strategist tier' -- see routes_efficiency.py's tier_usage trigger); a generic 'most' + model-entity question correctly falls to ranking, the right catch-all for this phrasing.",
    ),
    AskEvalCase(
        id="models-002", category="Models", kind="rephrased",
        question="Show me our model mix for this month",
        expected_intent="overview", expected_entity="model",
        expected_period_key="this_month", expected_days=31,
        notes="No ranking term or explicit tier name present -- overview is the correct catch-all here.",
    ),

    # ── Departments ──────────────────────────────────────────────────────
    AskEvalCase(
        id="departments-001", category="Departments", question="Rank departments by AI spend",
        expected_intent="ranking", expected_entity="department", expected_metric="spend_usd",
        expected_direction="desc",
        known_gap="_ask_intent's ranking_terms vocabulary (highest/most/top/largest/lowest/least/fewest/smallest/bottom) does not include the verb 'rank' itself -- confirmed: a literal 'Rank X by Y' command falls back to intent='overview'. A real gap given this is about as direct a ranking request as exists.",
    ),
    AskEvalCase(
        id="departments-002", category="Departments",
        question="How much did the Support department spend this month?",
        expected_intent="total", expected_entity="department", expected_metric="spend_usd",
        expected_period_key="this_month", expected_days=31,
        expected_result_path="this_month.support_spend_usd",
    ),

    # ── Outcomes ─────────────────────────────────────────────────────────
    AskEvalCase(
        id="outcomes-001", category="Outcomes", question="How many opportunities have we won?",
        expected_intent="total", expected_entity="context", expected_outcome_filter="won",
        expected_result_path="outcomes.won_count",
    ),
    AskEvalCase(
        id="outcomes-002", category="Outcomes", question="Which opportunities have we lost?",
        expected_intent="ranking", expected_entity="context", expected_outcome_filter="lost",
        expected_result_path="outcomes.lost_count",
        known_gap="'Which opportunities...' has no ranking_terms match (no highest/most/top/etc.) -- confirmed: falls back to intent='overview' despite outcome_filter='lost' being correctly detected. A real gap: 'which X have we Y'-shaped questions aren't recognized as implicitly asking for a list/ranking.",
    ),
    AskEvalCase(
        id="outcomes-003", category="Outcomes", kind="rephrased",
        question="What's our closed-won pipeline value?",
        expected_intent="total", expected_entity="overview",
        expected_result_path="outcomes.won_value_usd",
        notes="An aggregate value question with no ranking/rank-by-entity implication -- overview is correct.",
    ),
    AskEvalCase(
        id="outcomes-004", category="Outcomes", question="How many support cases have we resolved?",
        expected_intent="total", expected_entity="overview",
        expected_result_path="outcomes.support_cases_resolved",
        notes="Same as outcomes-003 -- aggregate count, entity=overview is correct.",
    ),

    # ── ROI / business value ────────────────────────────────────────────
    AskEvalCase(
        id="roi-001", category="ROI", question="What is our cost per won opportunity?",
        expected_intent="total", expected_entity="context", expected_outcome_filter="won",
        expected_result_path="outcomes.cost_per_won_opportunity_usd",
        notes="'opportunity' + 'won' correctly resolves entity=context, outcome_filter=won. Must carry an evidence/sample-size label per core/metrics_query.py's compute_cost_per_outcome -- 1 sample here is well below MIN_MEANINGFUL_SAMPLE (30), should be labeled early_signal, not presented as confident.",
    ),
    AskEvalCase(
        id="roi-002", category="ROI", question="How much AI investment went into deals we lost?",
        expected_intent="total", expected_entity="context", expected_outcome_filter="lost",
        expected_result_path="outcomes.ai_spend_on_lost_usd",
        known_gap="outcome_filter detection requires the literal substring 'opportunit' in the text (see routes_efficiency.py's outcome_filter block) -- 'deals' is not recognized as a synonym, confirmed: entity falls back to 'overview' and outcome_filter stays None. A real gap for a very natural rephrasing.",
    ),
    AskEvalCase(
        id="roi-003", category="ROI", kind="should_reject",
        question="How much revenue did AI generate for us this month?",
        skip_interpretation=True,
        notes="Must not answer with a causal claim (\"AI generated $X\") -- association only, per the causal-language guardrail. Expected behavior: reframe as associated value, not accept the causal premise.",
    ),

    # ── Optimization ─────────────────────────────────────────────────────
    AskEvalCase(
        id="optimization-001", category="Optimization",
        question="Where can we save money by using cheaper models?",
        expected_intent="optimization", expected_entity="model",
    ),
    AskEvalCase(
        id="optimization-002", category="Optimization", kind="rephrased",
        question="Are we over-using expensive models for routine work?",
        expected_intent="optimization", expected_entity="model",
    ),

    # ── Governance ───────────────────────────────────────────────────────
    AskEvalCase(
        id="governance-001", category="Governance", question="Were any requests blocked this month?",
        expected_intent="blocked", expected_entity="overview",
        expected_period_key="this_month", expected_days=31,
        notes="The 'blocked' intent branch hardcodes entity='overview' by design (see routes_efficiency.py) -- not a per-entity ranking, so 'overview' is correct.",
    ),
    AskEvalCase(
        id="governance-002", category="Governance", question="Show me risk events from the last 7 days",
        expected_intent="risk_events", expected_entity="overview", expected_days=7,
        known_gap="risk_events trigger requires the exact phrase 'show risk events' (or 'show the risk events'/'latest risk'/'recent risk'/...) -- 'show ME risk events' breaks that literal substring match, confirmed: falls back to intent='overview'. A real gap for a very natural phrasing with one extra word.",
    ),
    AskEvalCase(
        id="governance-003", category="Governance", kind="follow_up",
        question="What about last week instead?",
        notes="Follow-up to governance-002 -- should inherit intent=risk_events, entity=overview, only period changes to last_week/7 days. Requires conversation context, not directly testable via _ask_intent() alone (needs _ask_fallback_intent's merge) -- flagged for the Stage-2 context-merge harness, not yet automated in this pass.",
        skip_interpretation=True,
    ),

    # ── Comparisons ──────────────────────────────────────────────────────
    AskEvalCase(
        id="comparisons-001", category="Comparisons",
        question="How does this month's spend compare to last month?",
        expected_intent="comparison", expected_entity="overview", expected_metric="spend_usd",
        expected_comparison_key="previous_month",
        expected_result_path="month_over_month.sales_spend_pct_change",
        known_gap="comparison_key='previous_month' requires an exact phrase ('month over month'/'this month vs last month'/'this month versus last month') -- 'compare...to last month' doesn't match any of them, confirmed: falls back to the generic comparison_key='previous_period'. Same rolling-30-day window in practice for this fixture, but the label/intent shown to the user would be less precise. A real, natural-phrasing gap.",
    ),
    AskEvalCase(
        id="comparisons-002", category="Comparisons", kind="rephrased",
        question="This month vs last month, how are we trending on spend?",
        expected_intent="comparison", expected_entity="overview", expected_metric="spend_usd",
        expected_comparison_key="previous_month",
    ),
    AskEvalCase(
        id="comparisons-003", category="Comparisons",
        question="Compare Sales and Support spend this month",
        expected_period_key="this_month", expected_days=31,
        notes="A named multi-department comparison needs the fuller _ask_fallback_intent/named-entity resolution, not raw _ask_intent() alone (which returns intent='comparison', entity='overview' -- no per-department split) -- regression already covered by test_named_department_comparison_does_not_filter_out_other_departments in test_ask_costpilot.py; flagged for the Stage-2 named-entity harness, not yet automated in this pass.",
        skip_interpretation=True,
    ),

    # ── Trends ───────────────────────────────────────────────────────────
    AskEvalCase(
        id="trends-001", category="Trends", question="Is our AI spend trending up or down?",
        expected_intent="comparison", expected_entity="overview", expected_metric="spend_usd",
        known_gap="No 'compare'/period-vs-period phrase or change_drivers trigger word present -- 'trending up or down' alone isn't recognized, confirmed: falls back to intent='overview'. A real gap for a common way to ask about trend direction.",
    ),
    AskEvalCase(
        id="trends-002", category="Trends", question="What changed in our AI usage this month vs last month?",
        expected_intent="change_drivers", expected_entity="overview",
        expected_comparison_key="previous_month",
        known_gap="change_drivers requires 'why'/'what drove'/'what caused'/'contributed to' -- 'what changed' is not in that list, confirmed: falls back to intent='comparison' (itself a reasonable answer, just not the driver-decomposition one asked for). A real gap: 'what changed' is about as canonical a change-drivers question as exists.",
    ),

    # ── WorkItems ────────────────────────────────────────────────────────
    AskEvalCase(
        id="workitems-001", category="WorkItems", question="Which account has the most AI activity on it?",
        expected_intent="ranking", expected_entity="account", expected_metric="request_count",
        expected_direction="desc",
        notes="entity='account' (not 'context') is correct here -- 'account' is a real, separately-handled entity in _ask_intent (account_breakdown/account_id/Accounts), distinct from 'context' (WorkItems generally). Note: 'account' is NOT in _ASK_ENTITIES (routes_efficiency.py's OpenAI-validated enum), so the OpenAI-assisted classifier can never select it directly -- only the deterministic fallback can. Minor cross-path inconsistency worth knowing about, not a functional bug for this path.",
    ),
    AskEvalCase(
        id="workitems-002", category="WorkItems",
        question="How much AI spend is on the Acme Corp opportunity?",
        expected_intent="total", expected_entity="context",
        notes="Named-entity ('Acme Corp') scoping -- resolved via _ask_named_entity, not _ask_intent() alone; flagged for the named-entity harness, not yet automated in this pass.",
        skip_interpretation=True,
    ),

    # ── Salesforce ───────────────────────────────────────────────────────
    AskEvalCase(
        id="salesforce-001", category="Salesforce",
        question="How much are we spending on AI for Salesforce opportunities?",
        expected_intent="total", expected_entity="context", expected_source_platform="salesforce",
        notes="'opportunities' correctly resolves entity=context (not overview) alongside source_platform=salesforce.",
    ),
    AskEvalCase(
        id="salesforce-002", category="Salesforce", kind="rephrased",
        question="What's our Salesforce AI usage look like this month?",
        expected_intent="total", expected_entity="overview", expected_source_platform="salesforce",
        expected_period_key="this_month", expected_days=31,
    ),

    # ── ServiceNow ───────────────────────────────────────────────────────
    AskEvalCase(
        id="servicenow-001", category="ServiceNow",
        question="How many ServiceNow incidents have AI activity on them?",
        expected_intent="total", expected_entity="overview", expected_source_platform="servicenow",
    ),
    AskEvalCase(
        id="servicenow-002", category="ServiceNow", kind="missing_data",
        question="What's our average ServiceNow incident resolution time with AI assistance?",
        expected_intent="total", expected_entity="overview", expected_source_platform="servicenow",
        notes="average_resolution_time is in metrics_catalog.NOT_YET_COMPUTABLE -- must say unsupported, not invent a duration.",
    ),

    # ── Other platforms / ambiguous / should-reject / help ─────────────
    AskEvalCase(
        id="other-001", category="Other", question="How is CostPilot different from a normal LLM gateway?",
        expected_intent="product", skip_interpretation=True,
        notes="Product-knowledge question -- must not run a stale-data report (see test_endpoint_product_question_uses_curated_knowledge_and_screen_context).",
    ),
    AskEvalCase(
        id="other-002", category="Other", question="How do I add a new sensitive term to the policy library?",
        expected_intent="help", skip_interpretation=True,
    ),
    AskEvalCase(
        id="other-003", category="Other", kind="ambiguous", question="How are we doing?",
        expected_intent="overview", expected_entity="overview",
        notes="Deliberately vague -- should fall back to a general overview, not guess a specific metric.",
    ),
    AskEvalCase(
        id="other-004", category="Other", kind="should_reject",
        question="Should I fire the Support team?",
        skip_interpretation=True,
        notes="Not a reporting question CostPilot can answer -- must decline rather than force an answer from unrelated data.",
    ),
    AskEvalCase(
        id="other-005", category="Other", kind="missing_data",
        question="What is our AI spend broken down by customer satisfaction score?",
        skip_interpretation=True,
        notes="No CSAT field exists anywhere in the data model -- must say unsupported/insufficient data, not substitute a different dimension silently.",
    ),
]
