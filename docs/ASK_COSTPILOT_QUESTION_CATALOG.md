# Ask CostPilot — Question Catalog & Intent Taxonomy

Companion to [`EXECUTIVE_ANALYTICS_AGENT_STANDARD_V1.md`](./EXECUTIVE_ANALYTICS_AGENT_STANDARD_V1.md). This document catalogs the natural-language questions Ask CostPilot is expected to answer, classifies them into a reusable intent taxonomy, and states plainly which realistic questions are **not** answerable from the current data model.

The machine-readable source of truth for what's actually computable is:

- `backend/core/analytics_metrics.py` — `METRIC_REGISTRY`, `METRIC_KEYWORD_ALIASES`
- `backend/core/analytics_dimensions.py` — `DIMENSION_REGISTRY`, `UNSUPPORTED_DIMENSIONS`

This document explains those registries in prose; the registries are authoritative if the two ever disagree (enforced by `tests/test_analytics_registry_consistency.py`).

## 1. Intent taxonomy

Every question Ask CostPilot answers reduces to one of these intents. The deterministic classifier (`_ask_intent` in `api/routes_efficiency.py`) and the live agent's tool-calling loop (`api/ask_costpilot_tools.py`) both resolve to this same set — see the mapping table below for how each intent is currently implemented.

| Intent | Meaning | Example | Current implementation |
|---|---|---|---|
| TOTAL | A single aggregate number over a period/scope | "How much did we spend last month?" | `_ask_intent` intent=`total`, or agent `get_usage_report` |
| BREAKDOWN | Split a metric across a dimension | "Show AI spend by department." | `_ask_intent` intent=`overview`/ranking with a dimension |
| RANKING / TOP_N / BOTTOM_N | Ordered list, optionally limited | "Top 5 users by spend", "who spent the least" | intent=`ranking`, `direction`, `result_limit` |
| COMPARISON | Two periods, same scope | "This month vs last month" | intent=`comparison`, `core/analytics_periods.py` |
| CROSS_ENTITY_COMPARISON | Two *entities*, same period | "Sales vs Marketing", "OpenAI vs Anthropic" | **Partial** — see §4 |
| TREND / TIME_SERIES | A metric over multiple sub-periods | "Show spend over the last 12 months" | **Partial** — see §4 |
| CHANGE_ANALYSIS | Deterministic attribution of a change | "Why did spend increase?" | intent=`change_drivers`, `core/analytics_drivers.py` |
| CONTRIBUTION_ANALYSIS | Which dimension values drove a change | "What drove the increase?" | `dimension_contributors()` |
| ENTITY_LOOKUP | Facts about one named subject | "How much did Sheldon spend?" | `_ask_named_entity`, `entity_name` tool arg |
| BUDGET_STATUS | Cap / spend / remaining / throttle state | "How much budget is left?" | intent=`budget`, `core/budget.py` |
| VARIANCE | Actual vs. budget delta | "Was the change within budget?" | `budget_scope="variance"` |
| SAVINGS / EFFICIENCY | Pruning, cost-per-request | "How much has pruning saved us?" | intent=`pruning`, metric=`tokens_saved`/`avg_cost_per_request` |
| USAGE | Volume/activity, not $ | "How much activity today?" | metric=`request_count` |
| MODEL_ANALYSIS | Grouped/filtered by MODEL | "Which model costs the most?" | entity=`model` |
| PROVIDER_ANALYSIS | Grouped/filtered by PROVIDER (vendor) | "How much on Claude?" | entity=`provider` (added this phase) |
| USER / DEPARTMENT / ACCOUNT / PROJECT / AGENT _ANALYSIS | Grouped/filtered by that dimension | "Which department...", "Show accounts Sheldon worked on" | entity=`person`/`department`/`account`/`context`/`agent` |
| ANOMALY | Deviation from a baseline | "Anything unusual today?" | **Not implemented** — see §4 |
| FOLLOW_UP / DRILL_DOWN | Inherits prior turn's scope, changes one axis | "What about last year?", "Break that down by model" | `AskCostPilotContext`, `_ask_is_follow_up`, `_ask_fallback_intent` |
| GOVERNANCE / RISK | Blocked requests, risk events, policy | "Were any requests blocked?" | intent=`blocked`/`risk_events` |
| PRODUCT | About CostPilot itself, not the data | "How does pruning work?" | intent=`product`, `core/costpilot_knowledge.py` |
| CLARIFICATION_REQUIRED | Ambiguous subject, must not guess | "How much did Chris spend?" (2 Chrises) | `_ask_named_entity` abstain-on-tie |
| UNSUPPORTED | Cannot be answered from current data | "What's our AI ROI?" | See §4 |

## 2. Question catalog by persona

Every question below currently resolves through the deterministic classifier and/or the live agent tool loop. Questions are phrased the way a real user would type them, not in CostPilot terminology.

### Executive
- How much are we spending on AI? · Is spend increasing or decreasing? · What did we spend this quarter vs last quarter? · Which department is driving our spend? · Are we on pace to exceed budget? · How much has CostPilot saved us? · What changed this month? · What should I be worried about? (→ budget_flag + proactive_note, not a dedicated "worry" intent)

### CFO / Finance
- Show AI spend by department. · Spend for the last 12 months. · Compare actual spend against budget. · Which departments are over/near budget? · Month-over-month spend. · What's our cost per request? · Which accounts generated the most cost? · How much did optimization/pruning save us? · What would our bill have been without pruning? · OpenAI vs Anthropic spend.

### AI FinOps
- Which models cost the most? · Which agents have unusual token consumption? · What are our biggest token consumers? · Which departments have declining pruning efficiency? (→ requires a trend comparison of `tokens_saved` over two periods, supported via two `get_usage_report`/`get_change_drivers` calls, not a single canonical intent yet)

### Department Manager
- How much has my department spent this month? · Who on my team uses the most AI? · Which models does my team use? · How much budget do I have left? · Are we on track? · Who had the largest increase in usage? · What caused our spend to increase?

### Account / Project Manager
- What was the AI cost for Account X? · Which users worked on this account? · Which models were used on this account? · How much did pruning save this project? · Show all accounts Sheldon worked on and the token spend for each.

### Developer / Technical Lead
- Which agent generated the most requests? · Which model has the highest average token count? · Show request volume by application/platform. · Which agent saw the biggest increase this week? · Average request cost by model.

### Governance / Risk
- Which users accessed AI the most? · Which departments have the most AI activity? · Show all activity for a specific user during a date range. · Which requests were blocked, and why? · Show the latest risk events.

## 3. Auto-generated combinations (Phase 16)

For every `(metric, dimension)` pair where `core.analytics_dimensions.supports(metric, dimension)` is `True`, Ask CostPilot supports: a TOTAL, a BREAKDOWN, a RANKING/TOP_N, and — for the token_transactions-sourced metrics — a COMPARISON across two periods. Concretely, that's 7 metrics × 9 dimensions (minus the deliberately-excluded budget-metric × non-department pairs) ≈ 55 supported (metric, dimension) combinations, each answerable as a total, a ranking, or a period comparison. Rather than hand-listing all ~165 resulting question forms, call `core.analytics_dimensions.DIMENSION_REGISTRY` and `core.analytics_metrics.METRIC_REGISTRY` directly — that enumeration *is* the catalog, and it can't drift out of sync with the prose the way a hand-written list would.

## 4. UNSUPPORTED_CURRENTLY

Honest per Phase 1's instruction — these are realistic questions Ask CostPilot cannot currently answer, and why:

| Question class | Why unsupported | What would be needed |
|---|---|---|
| "What's our AI ROI / cost-to-business-value?" | No revenue, deal, or outcome data exists anywhere in the schema. | A linkage between `work_items`/`work_accounts` and a revenue or outcome system. |
| "Which project was most profitable after AI cost?" | Same as above — profitability requires revenue. | Same. |
| "Show activity involving sensitive data." | `sensitive_terms` configures *policy*, but no column on `TokenTransaction` records whether a specific call's content matched a term. | A per-request sensitive-match flag written at ingestion time. |
| "Which applications are calling unapproved models?" | `ModelRegistry.is_enabled` exists, but no report currently cross-references it against `TokenTransaction.model_name` to flag violations. | A dedicated policy-compliance breakdown (straightforward to add — registry data already exists). |
| "Are there users generating unusual amounts of activity?" (statistical anomaly) | No baseline/threshold/z-score computation exists yet. | See §5 below — deterministic anomaly detection is designed but not implemented this phase. |
| "Show spend trend over the last 12 months" as one call | `resolve_primary_period`/`comparison_plan` compute exactly two periods (current vs. one comparison), not an arbitrary N-bucket series. | A `TREND`/`TIME_SERIES` tool that loops `get_usage_report` per month and returns the series — mechanically straightforward, not yet wired as one tool call. |
| "Compare Sales vs Marketing" as one canonical query | The deterministic COMPARISON intent compares **periods**, not two named entities. The live agent handles this correctly today by calling `get_usage_report` twice and comparing manually (verified in this phase's live testing) — but there's no structured `CROSS_ENTITY_COMPARISON` contract enforcing that shape, so its correctness depends on the model, not a deterministic contract. | A dedicated `compare_entities` tool wrapping two scoped `project_activity_reporting` calls with a shared, validated diff shape (mirrors `get_change_drivers`'s existing period-diff pattern). |
| "Which workloads have the highest input-to-output token ratio?" | No breakdown currently computes a per-row ratio; `input_tokens`/`output_tokens` are independently rankable, but not their ratio. | A derived metric `input_output_ratio` in the metric registry + a small aggregation change. |

## 5. Anomaly detection (Phase 10) — design note, not implemented this phase

Phase 10 explicitly requires *deterministic* thresholds, not an LLM guessing what's "unusual." A safe, buildable design for a future phase:

- **Baseline**: trailing 7-period (day/week, matching the question's grain) mean and standard deviation per dimension value, computed the same way `dimension_contributors()` already computes period-over-period deltas.
- **Threshold**: flag a value where `current > mean + 2*stddev` **and** `current - mean` exceeds a minimum absolute floor (avoids flagging a jump from $0.02 to $0.08 as "anomalous" just because it's statistically large relative to a tiny baseline).
- **Output**: same shape as `get_change_drivers`'s `top_contributors` — reuses existing evidence-pool/citation plumbing in `_ask_agent_final_payload` rather than inventing a new response shape.

Not implemented this phase because it requires a genuine new deterministic computation (not just wiring an existing one), and doing it well needs a real decision on what "prior baseline" window and threshold constants are appropriate for this business — a business-definition question, not something safe to invent unilaterally per this task's own instructions on when to stop and ask.
