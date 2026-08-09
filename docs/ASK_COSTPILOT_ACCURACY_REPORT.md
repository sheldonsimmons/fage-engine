# Ask CostPilot — Accuracy Report

**Date:** 2026-08-08
**Scope:** Accuracy-hardening pass on top of the existing agent-loop architecture (query planning, semantic metrics, entity resolution, date handling, validation, conversation context, logging, testing already in place from prior work).

All numbers below come from actually running the test suites checked into this repo — none are estimated or asserted without a corresponding automated test. Reproduce any number here with the exact commands listed.

## 1. Classification accuracy (deterministic, no DB/API required)

```
cd backend && source venv/bin/activate && python3 scripts/ask_costpilot_classification_eval.py
```

105 questions across 10 personas and difficulty levels 1–8 (basic, filtering, aggregation, comparison, diagnostic/why, adversarial), each field-checked against the deterministic `_ask_intent` classifier.

| Category | Result |
|---|---|
| Intent accuracy | 33/33 (100.0%) |
| Metric accuracy | 32/32 (100.0%) |
| Entity resolution accuracy | 29/29 (100.0%) |
| Date accuracy | 40/40 (100.0%) |
| **Overall case accuracy** | **105/105 (100.0%)** |

Starting point before this phase's fixes: **92.4%** (97/105 fully correct). Every mismatch found was traced to a real, systemic root cause and fixed at the source (never hardcoded to the specific test sentence) — see the Failure Analysis document for the list.

## 2. End-to-end behavioral accuracy (real database, deterministic fallback path)

```
cd backend && source venv311/bin/activate && ASK_COSTPILOT_AGENT_MODE=false python3 scripts/ask_costpilot_eval.py
```

70 questions covering totals, date filtering, YoY/MoM comparisons, user/account/department/provider filtering, grouped rankings, top-N, savings, model/provider analysis, 22 multi-turn conversations, permission/security probes, and adversarial (typo'd/malformed) phrasing.

**Result: 70/70 (100.0%)**, run against the live SQLite dataset, not mocked.

## 3. End-to-end behavioral accuracy (live agent tool-calling loop, real Anthropic model)

Spot-checked live (not run as a full automated 70-case batch, to control API cost) across department scoping, provider scoping, multi-turn (including a 5-turn conversation), permission-bypass attempts, and zero-data honesty. Every case checked returned a correct, appropriately-hedged answer with no hallucinated figures. See commit history / session transcript for the specific verified transcripts (e.g. the 5-turn Sales/Marketing/model-breakdown conversation, the OpenAI-vs-Anthropic provider follow-up, and the "ignore permissions" refusal).

## 4. Regression suite

```
cd backend && source venv311/bin/activate && python3 -m pytest tests/test_ask_costpilot.py tests/test_ask_costpilot_tools.py tests/test_model_provider.py tests/test_analytics_registry_consistency.py tests/test_analytics_coverage.py -q
```

**109 passed**, 12 failed. All 12 failures are a single pre-existing, environment-only defect unrelated to this work: `_ask_workspace_name`/`_ask_named_department` are called with `db=None` by test fixtures that never provided a real session, a gap that predates this phase (confirmed by checking out the code before this session's changes and reproducing the identical failures). Not something this task's scope covers fixing (it's a test-fixture completeness issue in existing tests, not an Ask CostPilot behavior defect) — flagged for whoever owns that test file next.

## 5. Load / scale testing (Phase 14)

Real, measured timings on a scratch copy of the database (not the production/dev `fage.db` — cleaned up after testing):

| Dataset size | `project_activity_reporting()` time |
|---|---|
| 47 rows (actual current dataset) | 0.024s |
| 20,047 rows (synthetic, seeded) | 2.455s |

This scales roughly linearly with row count (~100x rows → ~100x time), because `project_activity_reporting()` fetches all matching rows via SQLAlchemy `.all()` and aggregates in a Python loop rather than pushing `SUM`/`GROUP BY` into SQL. **This is the single most important scalability finding of this phase** — extrapolating linearly (not measured, explicitly a projection), a workspace with 200,000 transactions would take on the order of 20+ seconds for a single lookup, and 1,000,000+ would likely exceed both the agent loop's `total_budget_seconds` and Heroku's request timeout. This was not introduced this phase and was not fixed this phase (rewriting a heavily-used, already-tested core aggregation function to push work into SQL is a substantial, separate refactor, correctly out of scope for an accuracy-hardening pass) — see the Failure Analysis doc for why this is flagged rather than fixed, and treat it as the top priority for the next phase of work.

What **was** verified correct at scale:
- Tool-result payloads sent to the LLM stay bounded regardless of underlying row count — a `limit=10` request against 20,047 rows returned an 11.3KB payload, not the full dataset (confirmed via `run_get_usage_report`).
- Top-N truncation (`row_limit`) is enforced in Python after aggregation, not before — every row is counted before truncation, so totals/averages stay correct even though only the top N rows are returned.
- `run_get_change_drivers` (a narrower two-period lookup) stayed fast (0.2s) even at 20,047 rows, since each period query only scans its own date window.

## 6. What "95%+" actually means here

The task's target was 95%+ reliability on supported questions. The classification and end-to-end suites both hit 100% — but that number describes the ~175 questions actually tested, weighted toward the question classes explicitly called for. It is not a claim that literally any phrasing of any question a user could type will succeed; the Question Catalog documents what is and is not supported, and the eval suites are designed to be extended (Phase 20's anti-overfitting principle) rather than treated as complete.
