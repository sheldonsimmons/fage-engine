"""
Static Ask CostPilot accuracy cases -- ground-truth-checkable questions.

Unlike ask_costpilot_eval_cases.py (structural/behavioral checks: "did it
answer without crashing, without inventing content"), every case here has a
single, independently-computable correct number for a given database, drawn
from the "AI Spend Overview" and "Comparisons" sections of the 156-question
reference bank (100 Ways to Ask CostPilot). Grading calls
core.metrics_query.run_metrics_query() directly for the expected value and
core.analytics_periods.resolve_primary_period() directly for the expected
calendar boundaries -- the same two trusted, already-tested primitives Ask
CostPilot itself is built on -- rather than re-deriving either by hand.

`period_key` here is a hand-read expectation for what _ask_intent() SHOULD
classify this phrasing as, written independently of that function, not by
calling it -- so a misclassification bug in _ask_intent() itself is still
caught (comparing the case's declared period_key against the answer's own
`conversation_context.period_key`), not silently reproduced on both sides.

Department-, agent-, and person-scoped questions aren't hardcoded here
because their correct filter value (a real name) depends on whatever
dataset the harness is pointed at -- see
ask_costpilot_accuracy_audit.py's `_dynamic_department_cases()`, which
discovers real department names from the live data at run time and builds
the equivalent cases on the fly.

Metric ids below are core.metrics_query's registry ids (ai_spend,
ai_requests, total_tokens, input_tokens, output_tokens) -- NOT
project_activity_reporting's report-summary field names (spend_usd,
request_count) -- ask_costpilot_accuracy_audit.py maps between the two
when reading the answer's own `summary` block.
"""

CASES = [
    # --- AI Spend Overview: single calendar periods -----------------------
    {
        "id": "spend_today",
        "question": "What's our total AI spend today?",
        "period_key": "today",
        "metric": "ai_spend",
    },
    {
        "id": "spend_this_week",
        "question": "How much have we spent on AI this week?",
        "period_key": "this_week",
        "metric": "ai_spend",
    },
    {
        "id": "spend_last_week",
        "question": "How much did we spend on AI last week?",
        "period_key": "last_week",
        "metric": "ai_spend",
    },
    {
        "id": "spend_this_month",
        "question": "How much have we spent on AI this month?",
        "period_key": "this_month",
        "metric": "ai_spend",
    },
    {
        "id": "spend_last_month",
        "question": "What was our AI spend last month?",
        "period_key": "last_month",
        "metric": "ai_spend",
    },
    {
        "id": "spend_this_quarter",
        "question": "How much have we spent on AI this quarter?",
        "period_key": "this_quarter",
        "metric": "ai_spend",
    },
    {
        "id": "spend_last_quarter",
        "question": "What was our AI spend last quarter?",
        "period_key": "last_quarter",
        "metric": "ai_spend",
    },
    {
        "id": "spend_this_year",
        "question": "How much has AI cost us this year?",
        "period_key": "this_year",
        "metric": "ai_spend",
    },
    {
        "id": "spend_last_year",
        "question": "What was our AI spend last year?",
        "period_key": "last_year",
        "metric": "ai_spend",
    },
    {
        "id": "spend_last_7_days",
        "question": "How much have we spent on AI in the last 7 days?",
        "days": 7,
        "period_key": "rolling_days",
        "metric": "ai_spend",
    },
    {
        "id": "spend_last_90_days",
        "question": "How much have we spent on AI in the last 90 days?",
        "days": 90,
        "period_key": "rolling_days",
        "metric": "ai_spend",
    },
    {
        "id": "spend_ytd",
        "question": "What is our AI spend year to date?",
        "period_key": "this_year",
        "metric": "ai_spend",
    },

    # --- Same periods, different metrics (tokens / requests) --------------
    {
        "id": "tokens_this_month",
        "question": "How many tokens have we used this month?",
        "period_key": "this_month",
        "metric": "total_tokens",
    },
    {
        "id": "tokens_last_90_days",
        "question": "How many tokens have we used in the last 90 days?",
        "days": 90,
        "period_key": "rolling_days",
        "metric": "total_tokens",
    },
    {
        "id": "requests_this_month",
        "question": "How many AI requests have we made this month?",
        "period_key": "this_month",
        "metric": "ai_requests",
    },
    {
        "id": "requests_today",
        "question": "How many AI requests have we made today?",
        "period_key": "today",
        "metric": "ai_requests",
    },

    # --- Comparisons: both sides individually ground-truthable -------------
    {
        "id": "compare_this_month_vs_last_month_primary",
        "question": "Compare AI spend this month to last month.",
        "period_key": "this_month",
        "metric": "ai_spend",
    },
    {
        "id": "compare_this_week_vs_last_week_primary",
        "question": "How does this week compare to last week?",
        "period_key": "this_week",
        "metric": "ai_spend",
    },
]
