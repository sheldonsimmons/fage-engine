# Extending Ask CostPilot — adding a new metric, dimension, or question type

## Adding a new metric

1. Add a `MetricDefinition` to `core/analytics_metrics.py`'s `METRIC_REGISTRY` — give it a real `formula`/`source`, not a placeholder.
2. Add its natural-language phrases to `METRIC_KEYWORD_ALIASES` in the same file, ordered most-specific first (a metric with no aliases is registered but unreachable from a question — `tests/test_analytics_registry_consistency.py` won't catch that specific gap, so check manually).
3. If it needs its own aggregation logic beyond `SUM`/`COUNT` (e.g. a new ratio), add it to whichever breakdown builder computes it (`aggregate()` in `api/routes_work_items.py` for token_transactions-sourced metrics, or `core/budget.py` for budget-sourced ones).
4. Add cases to both eval datasets: a few classification cases in `backend/scripts/ask_costpilot_classification_cases.py` (fast, free) and at least one end-to-end case in `backend/scripts/ask_costpilot_eval_cases.py`.

## Adding a new dimension

1. Add a `DimensionDefinition` to `core/analytics_dimensions.py`'s `DIMENSION_REGISTRY`, listing which metrics it actually supports (`supported_metrics`) — don't list a metric it can't really compute.
2. Add the matching breakdown to `project_activity_reporting()` in `api/routes_work_items.py` if it doesn't already exist (mirror `provider_breakdown` — the most recently added one — as a template: compute it once, add a filter clause to `matches()`, add it to the returned dict).
3. Add the entity to `_ASK_ENTITY_CONFIG_STATIC` in `api/routes_efficiency.py` (deterministic path) and to `_ASK_ENTITIES`.
4. Add keyword detection for it in `_ask_intent`'s entity classification if-chain — be careful about ordering (each branch is `elif`, so a more specific check must come before a more general one it could otherwise be shadowed by; see the provider-vs-platform split for a recent example of getting this wrong the first time and having to fix it).
5. If the model should be able to filter/scope by it directly (like `department` and `provider`), add a parameter to the relevant tool schema(s) in `api/ask_costpilot_tools.py`, thread it through the executor function, and update the system prompt instructions in `_ask_costpilot_agent` telling the model when to set it.
6. Run `tests/test_analytics_registry_consistency.py` — it will fail loudly if the registry and the real wiring disagree.

## Adding a new question type / intent

1. Decide which existing intent bucket it actually belongs to (see the taxonomy table in `ASK_COSTPILOT_QUESTION_CATALOG.md`) before assuming it needs a new one — most "new" question phrasings are an existing intent with different metric/entity/date values, not a genuinely new intent.
2. If it's genuinely new: add it to `_ASK_INTENTS` in `api/routes_efficiency.py`, add classification logic in `_ask_intent`, add a response-building branch in `_ask_costpilot_answer`, and add a corresponding tool (or extend an existing one) in `api/ask_costpilot_tools.py` for the agent path.
3. Add an `Answer Contract` check in `core/ask_costpilot_contracts.py`'s `validate_ask_answer_contract()` if the new intent has a specific shape that's easy to get subtly wrong (see how `agents_never_used` validates that evidence only contains agent rows).
4. Write the golden test cases *before* wiring the implementation if practical — this phase's biggest source of real bugs was writing an end-to-end test for a scenario nobody had tested before (see Failure Analysis #8), not re-running existing tests.

## Running the eval suites

```bash
cd backend

# Fast, free, no DB or API key -- classification accuracy only
source venv/bin/activate  # or your Python 3.10+ venv
python3 scripts/ask_costpilot_classification_eval.py [--verbose] [--category metric]

# End-to-end against real data, deterministic path only (free)
ASK_COSTPILOT_AGENT_MODE=false python3 scripts/ask_costpilot_eval.py [--verbose] [--filter <substring>]

# End-to-end against the live agent (costs real API calls; needs a valid ANTHROPIC_API_KEY)
ASK_COSTPILOT_AGENT_MODE=true python3 scripts/ask_costpilot_eval.py

# Full regression suite
source venv311/bin/activate  # needs Python 3.10+; the committed venv is 3.9 and can't even import some modules
python3 -m pytest tests/test_ask_costpilot.py tests/test_ask_costpilot_tools.py tests/test_model_provider.py tests/test_analytics_registry_consistency.py tests/test_analytics_coverage.py -q
```

## Debugging a specific wrong answer

Set `ASK_COSTPILOT_DEBUG=true` (and, for the agent path, `ASK_COSTPILOT_AGENT_MODE=true`) — every stage of the agent loop logs a `ASK_COSTPILOT_TRACE[stage]` line via `logger.warning` (not `.info` — nothing in this app configures logging, so `.info` is silently dropped by the default root level; this was itself a bug fixed this phase). Stages include `question_received`, `calling_model`, `tool_call` (with full args and results), `final_answer`, `response`, and every abort reason (`abort_no_api_key`, `abort_in_cooldown`, `abort_budget_preflight`, `abort_no_tool_use_block`, `abort_max_tool_calls`, `abort_loop_exhausted`, `agent_fallback_to_deterministic`). This trace is never returned to end users.
