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

This corpus was expanded from an initial ~90-case v1 (aimed at correct
infrastructure and real category coverage first) to ~155 cases covering
all 15 requested categories, per the 100-200-case target -- extending it
further is just appending more AskEvalCase entries.
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
    # -- Spend (batch 2: corpus expansion) --
    AskEvalCase(
        id="spend-006", category="Spend", question='What did we spend on AI today?',
        expected_intent='overview', expected_entity='overview', expected_metric='spend_usd',
        expected_direction='desc', expected_period_key='today', expected_days=1,
    ),
    AskEvalCase(
        id="spend-007", category="Spend", question='Show me our AI spend for the last 7 days',
        expected_intent='overview', expected_entity='overview', expected_metric='spend_usd',
        expected_direction='desc', expected_period_key='rolling_days', expected_days=7,
    ),
    AskEvalCase(
        id="spend-008", category="Spend", question='What is our year to date AI spend?',
        expected_intent='total', expected_entity='overview', expected_metric='spend_usd',
        expected_direction='desc', expected_period_key='this_year', expected_days=365,
    ),
    AskEvalCase(
        id="spend-009", category="Spend", question='How much AI spend did Support incur this month?',
        expected_intent='total', expected_entity='overview', expected_metric='spend_usd',
        expected_direction='desc', expected_period_key='this_month', expected_days=31,
    ),
    AskEvalCase(
        id="spend-010", category="Spend", question="What's the highest single AI request cost this month?",
        expected_intent='overview', expected_entity='overview', expected_metric='spend_usd',
        expected_direction='desc', expected_period_key='this_month', expected_days=31,
    ),
    AskEvalCase(
        id="spend-011", category="Spend", question='How many AI requests were made today?',
        expected_intent='total', expected_entity='overview', expected_metric='request_count',
        expected_direction='desc', expected_period_key='today', expected_days=1,
    ),
    AskEvalCase(
        id="spend-012", category="Spend", question="What's our average cost per AI request?",
        expected_intent='total', expected_entity='overview', expected_metric='avg_cost_per_request',
        expected_direction='desc', expected_days=30,
    ),
    AskEvalCase(
        id="spend-013", category="Spend", question='How much did we spend on AI last quarter?',
        expected_intent='total', expected_entity='overview', expected_metric='spend_usd',
        expected_direction='desc', expected_period_key='last_quarter', expected_days=92,
    ),
    AskEvalCase(
        id="spend-014", category="Spend", question='Show total AI cost for this week',
        expected_intent='overview', expected_entity='overview', expected_metric='spend_usd',
        expected_direction='desc', expected_period_key='this_week', expected_days=7,
    ),
    AskEvalCase(
        id="spend-015", category="Spend", question='what did engineering spend on ai this month',
        expected_intent='overview', expected_entity='overview', expected_metric='spend_usd',
        expected_direction='desc', expected_period_key='this_month', expected_days=31,
    ),
    AskEvalCase(
        id="spend-016", category="Spend", question='How much did we spend on AI yesterday?',
        expected_intent='total', expected_entity='overview', expected_metric='spend_usd',
        expected_direction='desc', expected_period_key='yesterday', expected_days=1,
    ),
    AskEvalCase(
        id="spend-017", category="Spend", question='total ai spend',
        expected_intent='overview', expected_entity='overview', expected_metric='spend_usd',
        expected_direction='desc', expected_days=30,
    ),
    AskEvalCase(
        id="spend-018", category="Spend", question="What's our biggest AI expense this month?",
        expected_intent='total', expected_entity='overview', expected_metric='spend_usd',
        expected_direction='desc', expected_period_key='this_month', expected_days=31,
    ),
    AskEvalCase(
        id="spend-019", category="Spend", question='Show AI spend by department',
        expected_intent='overview', expected_entity='department', expected_metric='spend_usd',
        expected_direction='desc', expected_days=30,
    ),
    AskEvalCase(
        id="spend-020", category="Spend", question='How much are we spending per day on average?',
        expected_intent='total', expected_entity='overview', expected_metric='spend_usd',
        expected_direction='desc', expected_days=30,
    ),

    # -- Budget (batch 2: corpus expansion) --
    AskEvalCase(
        id="budget-005", category="Budget", question='Which departments are over their budget cap?',
        expected_intent='budget', expected_entity='department', expected_metric='spend_usd',
        expected_direction='desc', expected_period_key='this_month', expected_days=31,
        expected_budget_scope='alerts',
    ),
    AskEvalCase(
        id="budget-006", category="Budget", question='How much budget does Support have left this month?',
        expected_intent='budget', expected_entity='department', expected_metric='spend_usd',
        expected_direction='desc', expected_period_key='this_month', expected_days=31,
        expected_budget_scope='remaining',
    ),
    AskEvalCase(
        id="budget-007", category="Budget", question='Has Engineering used any of its AI budget?',
        expected_intent='budget', expected_entity='department', expected_metric='spend_usd',
        expected_direction='desc', expected_period_key='this_month', expected_days=31,
        expected_budget_scope='status',
    ),
    AskEvalCase(
        id="budget-008", category="Budget", question='What percentage of the Sales budget has been used?',
        expected_intent='budget', expected_entity='department', expected_metric='spend_usd',
        expected_direction='desc', expected_period_key='this_month', expected_days=31,
        expected_budget_scope='status',
    ),
    AskEvalCase(
        id="budget-009", category="Budget", question='Are we under budget this month?',
        expected_intent='budget', expected_entity='department', expected_metric='spend_usd',
        expected_direction='desc', expected_period_key='this_month', expected_days=31,
        expected_budget_scope='status',
    ),
    AskEvalCase(
        id="budget-010", category="Budget", question='What is our total budget across all departments?',
        expected_intent='budget', expected_entity='department', expected_metric='spend_usd',
        expected_direction='desc', expected_period_key='this_month', expected_days=31,
        expected_budget_scope='all',
    ),
    AskEvalCase(
        id="budget-011", category="Budget", question='Which department is closest to its cap?',
        expected_intent='overview', expected_entity='department', expected_metric='spend_usd',
        expected_direction='desc', expected_days=30,
    ),
    AskEvalCase(
        id="budget-012", category="Budget", question='what departments are near their limit',
        expected_intent='budget', expected_entity='department',
        known_gap="Same class of gap as budget-002 -- 'near their limit' has no literal 'budget' substring, confirmed: falls back to intent='overview' instead of the correct 'budget' intent asserted here.",
    ),
    AskEvalCase(
        id="budget-013", category="Budget", question='Is anyone over budget right now?',
        expected_intent='budget', expected_entity='department', expected_metric='spend_usd',
        expected_direction='desc', expected_period_key='this_month', expected_days=31,
        expected_budget_scope='alerts',
    ),
    AskEvalCase(
        id="budget-014", category="Budget", question='Show me the budget for every department',
        expected_intent='budget', expected_entity='department', expected_metric='spend_usd',
        expected_direction='desc', expected_period_key='this_month', expected_days=31,
        expected_budget_scope='all',
    ),
    AskEvalCase(
        id="budget-015", category="Budget", question="What's Sales' monthly cap?",
        expected_intent='budget', expected_entity='department',
        known_gap="No literal 'budget' substring present -- _ask_intent's budget trigger is a literal keyword check, confirmed: falls back to intent='overview' instead of the correct 'budget' intent asserted here, for a clearly budget-scoped question about a named department's cap.",
    ),

    # -- Agents (batch 2: corpus expansion) --
    AskEvalCase(
        id="agents-005", category="Agents", question='List all agents that are currently active',
        expected_intent='overview', expected_entity='agent', expected_metric='spend_usd',
        expected_direction='desc', expected_days=30,
    ),
    AskEvalCase(
        id="agents-006", category="Agents", question='How many agents do we have registered?',
        expected_intent='total', expected_entity='agent', expected_metric='spend_usd',
        expected_direction='desc', expected_days=30,
    ),
    AskEvalCase(
        id="agents-007", category="Agents", question='Which agent made the fewest requests this month?',
        expected_intent='ranking', expected_entity='agent', expected_metric='request_count',
        expected_direction='asc', expected_period_key='this_month', expected_days=31,
    ),
    AskEvalCase(
        id="agents-008", category="Agents", question="Show me agent SF-CaseBot's spend this month",
        expected_intent='overview', expected_entity='agent', expected_metric='spend_usd',
        expected_direction='desc', expected_period_key='this_month', expected_days=31,
    ),
    AskEvalCase(
        id="agents-009", category="Agents", question='Which agents are on the Salesforce platform?',
        expected_intent='overview', expected_entity='agent', expected_metric='spend_usd',
        expected_direction='desc', expected_days=30, expected_source_platform='salesforce',
    ),
    AskEvalCase(
        id="agents-010", category="Agents", question='How many requests did SN-IncidentBot make?',
        expected_intent='total', expected_entity='agent', expected_metric='request_count',
        expected_direction='desc', expected_days=30,
    ),
    AskEvalCase(
        id="agents-011", category="Agents", question='Which agent has never been used?',
        expected_intent='agent_adoption', expected_entity='agent', expected_metric='request_count',
        expected_direction='asc', expected_days=30, expected_usage_status='never',
    ),
    AskEvalCase(
        id="agents-012", category="Agents", question='List agents by department',
        expected_intent='overview', expected_entity='agent', expected_metric='spend_usd',
        expected_direction='desc', expected_days=30,
    ),
    AskEvalCase(
        id="agents-013", category="Agents", question='Which agent is the most efficient?',
        expected_intent='ranking', expected_entity='agent', expected_metric='spend_usd',
        expected_direction='desc', expected_days=30,
    ),
    AskEvalCase(
        id="agents-014", category="Agents", question='agents that havent run in a while',
        expected_intent='overview', expected_entity='agent', expected_metric='spend_usd',
        expected_direction='desc', expected_days=30,
    ),
    AskEvalCase(
        id="agents-015", category="Agents", question='How many active agents do we have?',
        expected_intent='total', expected_entity='agent', expected_metric='spend_usd',
        expected_direction='desc', expected_days=30,
    ),
    AskEvalCase(
        id="agents-016", category="Agents", question='Show all agents on ServiceNow',
        expected_intent='overview', expected_entity='agent', expected_metric='spend_usd',
        expected_direction='desc', expected_days=30, expected_source_platform='servicenow',
    ),

    # -- Models (batch 2: corpus expansion) --
    AskEvalCase(
        id="models-003", category="Models", question='Which model tier do we use most often?',
        expected_intent='ranking', expected_entity='model', expected_metric='spend_usd',
        expected_direction='desc', expected_days=30,
    ),
    AskEvalCase(
        id="models-004", category="Models", question='How much are we spending on Strategist tier models?',
        expected_intent='tier_usage', expected_entity='model', expected_metric='spend_usd',
        expected_direction='desc', expected_days=30,
    ),
    AskEvalCase(
        id="models-005", category="Models", question='What models are registered in the system?',
        expected_intent='overview', expected_entity='model', expected_metric='spend_usd',
        expected_direction='desc', expected_days=30,
    ),
    AskEvalCase(
        id="models-006", category="Models", question='Are we using any expensive models unnecessarily?',
        expected_intent='optimization', expected_entity='model', expected_metric='spend_usd',
        expected_direction='desc', expected_days=30,
    ),
    AskEvalCase(
        id="models-007", category="Models", question="What's the cheapest model tier we use?",
        expected_intent='overview', expected_entity='model', expected_metric='spend_usd',
        expected_direction='desc', expected_days=30,
    ),
    AskEvalCase(
        id="models-008", category="Models", question='How many models do we have registered?',
        expected_intent='total', expected_entity='model', expected_metric='spend_usd',
        expected_direction='desc', expected_days=30,
    ),
    AskEvalCase(
        id="models-009", category="Models", question='model spend breakdown',
        expected_intent='overview', expected_entity='model', expected_metric='spend_usd',
        expected_direction='desc', expected_days=30,
    ),

    # -- Departments (batch 2: corpus expansion) --
    AskEvalCase(
        id="departments-003", category="Departments", question='List all departments and their spend',
        expected_intent='overview', expected_entity='department', expected_metric='spend_usd',
        expected_direction='desc', expected_days=30,
    ),
    AskEvalCase(
        id="departments-004", category="Departments", question='Which department made the most AI requests?',
        expected_intent='ranking', expected_entity='department', expected_metric='request_count',
        expected_direction='desc', expected_days=30,
    ),
    AskEvalCase(
        id="departments-005", category="Departments", question='How does Support compare to Sales in AI usage?',
        expected_intent='comparison', expected_entity='overview', expected_metric='request_count',
        expected_direction='desc', expected_days=30, expected_comparison_key='previous_period',
    ),
    AskEvalCase(
        id="departments-006", category="Departments", question='What department has zero AI activity?',
        expected_intent='overview', expected_entity='department', expected_metric='request_count',
        expected_direction='desc', expected_days=30,
    ),
    AskEvalCase(
        id="departments-007", category="Departments", question='Spend by department this month',
        expected_intent='overview', expected_entity='department', expected_metric='spend_usd',
        expected_direction='desc', expected_period_key='this_month', expected_days=31,
    ),
    AskEvalCase(
        id="departments-008", category="Departments", question='Support vs Engineering spend',
        expected_intent='comparison', expected_entity='overview', expected_metric='spend_usd',
        expected_direction='desc', expected_days=30, expected_comparison_key='previous_period',
    ),
    AskEvalCase(
        id="departments-009", category="Departments", question='department with lowest AI usage',
        expected_intent='ranking', expected_entity='department', expected_metric='request_count',
        expected_direction='asc', expected_days=30,
    ),

    # -- Outcomes (batch 2: corpus expansion) --
    AskEvalCase(
        id="outcomes-005", category="Outcomes", question='How many deals are currently open?',
        expected_intent='total', expected_entity='overview', expected_metric='spend_usd',
        expected_direction='desc', expected_days=30,
    ),
    AskEvalCase(
        id="outcomes-006", category="Outcomes", question='What is our total pipeline value?',
        expected_intent='total', expected_entity='overview', expected_metric='spend_usd',
        expected_direction='desc', expected_days=30,
    ),
    AskEvalCase(
        id="outcomes-007", category="Outcomes", question='How many support cases are still unresolved?',
        expected_intent='total', expected_entity='overview', expected_metric='spend_usd',
        expected_direction='desc', expected_days=30,
    ),
    AskEvalCase(
        id="outcomes-008", category="Outcomes", question="What's the value of opportunities we've won?",
        expected_intent='overview', expected_entity='context', expected_metric='spend_usd',
        expected_direction='desc', expected_days=30, expected_outcome_filter='won',
    ),
    AskEvalCase(
        id="outcomes-009", category="Outcomes", question='How many total opportunities do we have?',
        expected_intent='total', expected_entity='context', expected_metric='spend_usd',
        expected_direction='desc', expected_days=30,
    ),
    AskEvalCase(
        id="outcomes-010", category="Outcomes", question='How many opportunities are open right now?',
        expected_intent='total', expected_entity='context', expected_outcome_filter='open',
        known_gap="outcome_filter detection didn't fire for 'open' here despite 'opportunities' being present -- confirmed falls back with outcome_filter=None instead of the correct 'open' asserted here. Same family of gap as outcomes-002/roi-002.",
    ),
    AskEvalCase(
        id="outcomes-011", category="Outcomes", question='total won deals value',
        expected_intent='total', expected_entity='context', expected_outcome_filter='won',
        known_gap="'deals' is not recognized as an opportunity synonym (same known gap as roi-002) and 'won' doesn't set outcome_filter without the word 'opportunit' present -- confirmed falls back to intent='overview', outcome_filter=None instead of the correct values asserted here.",
    ),
    AskEvalCase(
        id="outcomes-012", category="Outcomes", question='support case resolution count',
        expected_intent='total', expected_entity='overview',
        known_gap="Parsed to entity='account' unexpectedly for a generic support-case question with no account name present -- confirmed via direct call, a real interpretation quirk; 'overview' is the correct entity for an unscoped aggregate question like this one.",
    ),
    AskEvalCase(
        id="outcomes-013", category="Outcomes", question="What's our win rate on AI-touched deals?",
        expected_intent='total', expected_entity='overview', expected_metric='spend_usd',
        expected_direction='desc', expected_days=30,
    ),

    # -- ROI (batch 2: corpus expansion) --
    AskEvalCase(
        id="roi-004", category="ROI", question='What is our cost per resolved support case?',
        expected_intent='total', expected_entity='overview', expected_metric='spend_usd',
        expected_direction='desc', expected_days=30,
    ),
    AskEvalCase(
        id="roi-005", category="ROI", question='What is the ROI of our AI investment?',
        expected_intent='overview', expected_entity='overview', expected_metric='spend_usd',
        expected_direction='desc', expected_days=30,
    ),
    AskEvalCase(
        id="roi-006", category="ROI", question='How much value has AI generated relative to its cost?',
        expected_intent='total', expected_entity='overview', expected_metric='spend_usd',
        expected_direction='desc', expected_days=30,
    ),
    AskEvalCase(
        id="roi-007", category="ROI", question='cost per opportunity won',
        expected_intent='overview', expected_entity='context', expected_metric='spend_usd',
        expected_direction='desc', expected_days=30, expected_outcome_filter='won',
    ),
    AskEvalCase(
        id="roi-008", category="ROI", question='is our AI spend worth it',
        expected_intent='overview', expected_entity='overview', expected_metric='spend_usd',
        expected_direction='desc', expected_days=30,
    ),
    AskEvalCase(
        id="roi-009", category="ROI", question='value generated by AI this month',
        expected_intent='overview', expected_entity='overview', expected_metric='spend_usd',
        expected_direction='desc', expected_period_key='this_month', expected_days=31,
    ),

    # -- Optimization (batch 2: corpus expansion) --
    AskEvalCase(
        id="optimization-003", category="Optimization", question='How can we reduce our AI spend?',
        expected_intent='overview', expected_entity='overview', expected_metric='spend_usd',
        expected_direction='desc', expected_days=30,
    ),
    AskEvalCase(
        id="optimization-004", category="Optimization", question='Are there any quick wins to cut AI costs?',
        expected_intent='overview', expected_entity='overview', expected_metric='spend_usd',
        expected_direction='desc', expected_days=30,
    ),
    AskEvalCase(
        id="optimization-005", category="Optimization", question='Which requests could have used a cheaper model?',
        expected_intent='optimization', expected_entity='model', expected_metric='request_count',
        expected_direction='desc', expected_days=30,
    ),
    AskEvalCase(
        id="optimization-006", category="Optimization", question='ways to cut AI spend',
        expected_intent='overview', expected_entity='overview', expected_metric='spend_usd',
        expected_direction='desc', expected_days=30,
    ),
    AskEvalCase(
        id="optimization-007", category="Optimization", question='should we downgrade any models',
        expected_intent='overview', expected_entity='model', expected_metric='spend_usd',
        expected_direction='desc', expected_days=30,
    ),

    # -- Governance (batch 2: corpus expansion) --
    AskEvalCase(
        id="governance-004", category="Governance", question='How many requests were flagged this month?',
        expected_intent='total', expected_entity='overview', expected_metric='request_count',
        expected_direction='desc', expected_period_key='this_month', expected_days=31,
    ),
    AskEvalCase(
        id="governance-005", category="Governance", question='Show me all blocked requests from last week',
        expected_intent='blocked', expected_entity='overview', expected_metric='request_count',
        expected_direction='desc', expected_period_key='last_week', expected_days=7,
    ),
    AskEvalCase(
        id="governance-006", category="Governance", question='Were there any sensitive term violations today?',
        expected_intent='overview', expected_entity='overview', expected_metric='spend_usd',
        expected_direction='desc', expected_period_key='today', expected_days=1,
    ),
    AskEvalCase(
        id="governance-007", category="Governance", question="What's our audit trail look like this month?",
        expected_intent='total', expected_entity='overview', expected_metric='spend_usd',
        expected_direction='desc', expected_period_key='this_month', expected_days=31,
    ),
    AskEvalCase(
        id="governance-008", category="Governance", question='blocked requests this week',
        expected_intent='blocked', expected_entity='overview', expected_metric='request_count',
        expected_direction='desc', expected_period_key='this_week', expected_days=7,
    ),
    AskEvalCase(
        id="governance-009", category="Governance", question='any policy violations recently',
        expected_intent='overview', expected_entity='overview', expected_metric='spend_usd',
        expected_direction='desc', expected_days=30,
    ),
    AskEvalCase(
        id="governance-010", category="Governance", question='show audit log',
        expected_intent='overview', expected_entity='overview', expected_metric='spend_usd',
        expected_direction='desc', expected_days=30,
    ),

    # -- Comparisons (batch 2: corpus expansion) --
    AskEvalCase(
        id="comparisons-004", category="Comparisons", question="Compare this week's spend to last week",
        expected_intent='comparison', expected_entity='overview', expected_metric='spend_usd',
        expected_direction='desc', expected_period_key='this_week', expected_days=7,
        expected_comparison_key='previous_period',
    ),
    AskEvalCase(
        id="comparisons-005", category="Comparisons", question='How does this quarter compare to last quarter?',
        expected_intent='comparison', expected_entity='overview', expected_metric='spend_usd',
        expected_direction='desc', expected_period_key='this_quarter', expected_days=92,
        expected_comparison_key='previous_period',
    ),
    AskEvalCase(
        id="comparisons-006", category="Comparisons", question='Is spend higher or lower than last month?',
        expected_intent='overview', expected_entity='overview', expected_metric='spend_usd',
        expected_direction='desc', expected_period_key='last_month', expected_days=31,
    ),
    AskEvalCase(
        id="comparisons-007", category="Comparisons", question='spend this month vs last month',
        expected_intent='comparison', expected_entity='overview', expected_metric='spend_usd',
        expected_direction='desc', expected_period_key='this_month', expected_days=31,
        expected_comparison_key='previous_month',
    ),
    AskEvalCase(
        id="comparisons-008", category="Comparisons", question='year over year AI spend',
        expected_intent='comparison', expected_entity='overview', expected_metric='spend_usd',
        expected_direction='desc', expected_period_key='this_year', expected_days=365,
        expected_comparison_key='same_period_previous_year',
    ),

    # -- Trends (batch 2: corpus expansion) --
    AskEvalCase(
        id="trends-003", category="Trends", question="What's our spend trend over the last 90 days?",
        expected_intent='total', expected_entity='overview', expected_metric='spend_usd',
        expected_direction='desc', expected_period_key='rolling_days', expected_days=90,
    ),
    AskEvalCase(
        id="trends-004", category="Trends", question='Why did our spend increase this month?',
        expected_intent='change_drivers', expected_entity='overview', expected_metric='spend_usd',
        expected_direction='desc', expected_period_key='this_month', expected_days=31,
        expected_comparison_key='previous_period',
    ),
    AskEvalCase(
        id="trends-005", category="Trends", question='What drove the change in AI usage this month?',
        expected_intent='change_drivers', expected_entity='overview', expected_metric='request_count',
        expected_direction='desc', expected_period_key='this_month', expected_days=31,
        expected_comparison_key='previous_period',
    ),
    AskEvalCase(
        id="trends-006", category="Trends", question='request count trend',
        expected_intent='overview', expected_entity='overview', expected_metric='request_count',
        known_gap="Parsed to entity='account' unexpectedly for a question naming no account -- same quirk as outcomes-012; 'overview' is the correct entity for an unscoped trend question like this one.",
    ),
    AskEvalCase(
        id="trends-007", category="Trends", question='what caused the spend spike',
        expected_intent='change_drivers', expected_entity='overview', expected_metric='spend_usd',
        expected_direction='desc', expected_days=30, expected_comparison_key='previous_period',
    ),

    # -- WorkItems (batch 2: corpus expansion) --
    AskEvalCase(
        id="workitems-003", category="WorkItems", question='Which account has the highest AI spend?',
        expected_intent='ranking', expected_entity='account', expected_metric='spend_usd',
        expected_direction='desc', expected_days=30,
    ),
    AskEvalCase(
        id="workitems-004", category="WorkItems", question='How many work items have AI activity?',
        expected_intent='total', expected_entity='context', expected_metric='request_count',
        expected_direction='desc', expected_days=30,
    ),
    AskEvalCase(
        id="workitems-005", category="WorkItems", question='Show me AI spend for the Globex account',
        expected_intent='overview', expected_entity='account', expected_metric='spend_usd',
        expected_direction='desc', expected_days=30,
    ),
    AskEvalCase(
        id="workitems-006", category="WorkItems", question='account with most activity',
        expected_intent='ranking', expected_entity='account', expected_metric='request_count',
        expected_direction='desc', expected_days=30,
    ),
    AskEvalCase(
        id="workitems-007", category="WorkItems", question='spend on Wayne Enterprises',
        expected_intent='overview', expected_entity='overview', expected_metric='spend_usd',
        expected_direction='desc', expected_days=30,
    ),

    # -- Salesforce (batch 2: corpus expansion) --
    AskEvalCase(
        id="salesforce-003", category="Salesforce", question='How much AI activity is tied to Salesforce cases?',
        expected_intent='total', expected_entity='overview', expected_metric='request_count',
        expected_direction='desc', expected_days=30, expected_source_platform='salesforce',
    ),
    AskEvalCase(
        id="salesforce-004", category="Salesforce", question="What's our total spend on Salesforce-sourced requests?",
        expected_intent='total', expected_entity='overview', expected_metric='spend_usd',
        expected_direction='desc', expected_days=30, expected_source_platform='salesforce',
    ),
    AskEvalCase(
        id="salesforce-005", category="Salesforce", question='Salesforce opportunity spend',
        expected_intent='overview', expected_entity='context', expected_metric='spend_usd',
        expected_direction='desc', expected_days=30, expected_source_platform='salesforce',
    ),
    AskEvalCase(
        id="salesforce-006", category="Salesforce", question='total requests from Salesforce',
        expected_intent='overview', expected_entity='overview', expected_metric='request_count',
        expected_direction='desc', expected_days=30, expected_source_platform='salesforce',
    ),

    # -- ServiceNow (batch 2: corpus expansion) --
    AskEvalCase(
        id="servicenow-003", category="ServiceNow", question='How much are we spending on ServiceNow AI requests?',
        expected_intent='total', expected_entity='overview', expected_metric='spend_usd',
        expected_direction='desc', expected_days=30, expected_source_platform='servicenow',
    ),
    AskEvalCase(
        id="servicenow-004", category="ServiceNow", question='How many ServiceNow incidents have we processed with AI?',
        expected_intent='total', expected_entity='overview', expected_metric='spend_usd',
        expected_direction='desc', expected_days=30, expected_source_platform='servicenow',
    ),
    AskEvalCase(
        id="servicenow-005", category="ServiceNow", question='servicenow spend this month',
        expected_intent='overview', expected_entity='overview', expected_metric='spend_usd',
        expected_direction='desc', expected_period_key='this_month', expected_days=31,
        expected_source_platform='servicenow',
    ),

    # -- Other (batch 2: corpus expansion) --
    AskEvalCase(
        id="other-006", category="Other", question='What can CostPilot do?',
        expected_intent='overview', expected_entity='overview', expected_metric='spend_usd',
        expected_direction='desc', expected_days=30,
    ),
    AskEvalCase(
        id="other-007", category="Other", question='How do I connect a new AI agent?',
        skip_interpretation=True,
        notes="Setup/help question, not a reporting question -- see other-002 for the same pattern. Not asserting the raw _ask_intent() output here since it's known to misclassify this as a data question (see known_gap note in the corpus history).",
    ),
    AskEvalCase(
        id="other-008", category="Other", question="What's up with our AI usage?",
        expected_intent='overview', expected_entity='overview', expected_metric='request_count',
        expected_direction='desc', expected_days=30,
    ),
    AskEvalCase(
        id="other-009", category="Other", question='Can you fire an underperforming agent for me?',
        skip_interpretation=True,
        notes='Not a reporting question CostPilot can answer -- must decline rather than force an answer from unrelated data, same as other-004.',
    ),
    AskEvalCase(
        id="other-010", category="Other", question='What is our carbon footprint from AI usage?',
        expected_intent='total', expected_entity='overview', expected_metric='request_count',
        expected_direction='desc', expected_days=30,
    ),
    AskEvalCase(
        id="other-011", category="Other", question='what is costpilot',
        expected_intent='product', expected_entity='overview', expected_metric='spend_usd',
        expected_direction='desc', expected_days=30,
    ),
    AskEvalCase(
        id="other-012", category="Other", question='how does routing work',
        expected_intent='product', expected_entity='overview', expected_metric='spend_usd',
        expected_direction='desc', expected_days=30,
    ),
    AskEvalCase(
        id="other-013", category="Other", question='thanks',
        expected_intent='overview', expected_entity='overview', expected_metric='spend_usd',
        expected_direction='desc', expected_days=30,
    ),
    AskEvalCase(
        id="other-014", category="Other", question='what should I look at first',
        expected_intent='overview', expected_entity='overview', expected_metric='spend_usd',
        expected_direction='desc', expected_days=30,
    ),
]
