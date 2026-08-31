# Ask CostPilot evaluation corpus

Recommendation #2 from the Ask CostPilot architecture audit — a structured
regression corpus that stores **expected intent/metric/entity/filters/period/
comparison/numeric result**, not just expected prose, so a failure can be
attributed to a specific stage:

- **interpretation** — did `_ask_intent()` parse the question correctly?
  → `tests/test_ask_eval_interpretation.py`
- **data retrieval / calculation** — given the *correct* filters, does the
  deterministic query return the right numbers against a known fixture?
  → `tests/test_ask_eval_retrieval.py`
- **narration** — does the LLM's prose accurately describe the already-computed
  facts? **Not covered here** — it needs a live model call per case and a
  judge (human or LLM) to score, which is a separate, slower-running eval
  track to build later, not a per-commit CI gate.

## Files

- `corpus.py` — the `AskEvalCase` list, organized by the 15 requested
  categories (Spend, Budget, Agents, Models, Departments, Outcomes, ROI,
  Optimization, Governance, Comparisons, Trends, WorkItems, Salesforce,
  ServiceNow, Other) plus edge-case `kind`s (typo, rephrased, ambiguous,
  follow_up, missing_data, should_reject).
- `fixtures.py` — the one deterministic seed dataset every `expected_result_path`
  in the corpus is computed against by hand (`build_eval_fixture()` returns a
  `TRUTH` dict `test_ask_eval_retrieval.py` reads from — numbers are never
  re-derived from the code under test).

## Status: v1, ~38 interpretation-testable cases (not the full 100-200 target)

Built for correct infrastructure and real category coverage first. Extending
it is just appending `AskEvalCase` entries to `corpus.py` — for a case whose
expected numeric result also needs checking, add rows to `fixtures.py` and a
mapping branch in `test_ask_eval_retrieval.py`'s `_resolve_actual()`.

## Known gaps (tracked via `xfail`, not hidden)

Building this corpus against the *actual* deterministic parser (not guessed
behavior) surfaced 9 real interpretation gaps on the first pass — each is a
`known_gap=` case in `corpus.py`, `xfail(strict=True)` in the test runner (so
if a gap is ever silently fixed, the corpus itself fails until its
`known_gap` note is removed — self-cleaning, not a silent green light):

- Typo correction doesn't fix "spned" → "spend"
- "spending cap" doesn't trigger budget intent (only the literal word "budget" does)
- "Rank X by Y" doesn't trigger ranking intent (the verb "rank" isn't in `ranking_terms`)
- "Which opportunities have we lost?" doesn't trigger ranking (same gap, "which X" pattern)
- "deals we lost" doesn't trigger outcome filtering (only "opportunit*" is recognized)
- "Show me risk events..." doesn't match (requires the exact substring "show risk events")
- "compare...to last month" doesn't set the precise `previous_month` comparison key
- "trending up or down" doesn't trigger comparison intent
- "What changed..." doesn't trigger `change_drivers` (requires "why"/"what drove"/"what caused")

None of these were fixed as part of building this corpus — that's
Recommendations #3-5's territory, explicitly out of scope until #1 and #2 are
complete per the audit's sequencing. This corpus is what makes fixing them
(and verifying the fix, and catching a future regression) possible.

## Running

```
cd backend
./venv311/bin/python -m pytest tests/test_ask_eval_interpretation.py -v -rx   # per-case pass/fail + xfail reasons
./venv311/bin/python -m pytest tests/test_ask_eval_interpretation.py::test_interpretation_accuracy_summary -s  # accuracy report
./venv311/bin/python -m pytest tests/test_ask_eval_retrieval.py -v            # data retrieval + calculation
```
