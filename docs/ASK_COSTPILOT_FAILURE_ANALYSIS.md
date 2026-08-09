# Ask CostPilot — Failure Analysis

Every failure found during this phase's evaluation runs, classified by root cause (Phase 19's taxonomy), with what was fixed and why — or, where something wasn't fixed, why not. Ordered by how it was discovered.

## Fixed this phase

### 1. FILTER_ERROR — department scope silently dropped when ranking a different dimension
**Symptom:** "What models is Sales using the most?" answered with the company-wide model ranking (47 requests) instead of Sales' own (14 requests), while the narration still implied it was Sales-specific.
**Root cause:** The agent's `get_usage_report` tool had no `department` parameter — reporting scope came only from static, request-level filters, never from something named inside the question itself. The identical bug existed independently in the deterministic fallback's `_ask_intent`, which can only classify `entity` as one thing at a time (`model` OR `department`, never both).
**Fix:** Added a `department` tool parameter (agent path) and `_ask_named_department()` (deterministic path, matches real department names from the live data, not a hardcoded list) that overrides `charged_unit` regardless of the ranking dimension.
**Category:** FILTER_ERROR.

### 2. Same bug, different dimension — provider scope
**Symptom:** No way to answer "how much are we spending on Claude" or "compare OpenAI and Anthropic" at all — provider wasn't a tracked dimension anywhere in the reporting layer.
**Root cause:** `TokenTransaction` has no provider column; provider was never derived from `model_name`, and "provider" as a word was conflated with "platform" (source system) in `_ask_intent`'s entity classification.
**Fix:** Added `core/model_provider.py` (registry-lookup + prefix/keyword fallback resolution), a `provider_breakdown` and `provider` filter in `project_activity_reporting`, a `provider` tool parameter on the agent side, and split the "provider" vs. "platform" entity classification apart.
**Category:** UNSUPPORTED_QUERY (now supported) / ENTITY_ERROR (platform/provider conflation).

### 3. INTENT_ERROR — canonical regex pattern too permissive
**Symptom:** "Which department has the highest cost per request?" answered with a spend *ranking* instead of a *cost-per-request* metric.
**Root cause:** `department_spend_ranking`/`agent_cost_ranking`/`model_cost_ranking` canonical patterns in `core/ask_costpilot_contracts.py` had a fully **optional** trailing group (`(?:most|highest|top)?`), so the pattern matched any "which department...cost..." phrase regardless of what followed "cost" — including "cost per request," a different metric entirely.
**Fix:** Added a negative lookahead excluding "per request"/"per call" phrasing to all three patterns.
**Category:** INTENT_ERROR (systemic — same bug in 3 near-identical patterns, all fixed together, not patched one at a time).

### 4. DATE/INTENT_ERROR — missing informal change-language and metric words
**Symptom:** "Why did our bill jump?" wasn't classified as a change-analysis question at all.
**Root cause:** The change-driver detector required a change-word from a fixed list that didn't include "jump"/"jumped," and a metric word list that didn't include "bill."
**Fix:** Extended both lists; also made the metric-word clause optional entirely for "what caused the spike" style phrasing where the bot's exclusive domain (AI spend/usage) makes a metric word redundant.
**Category:** INTENT_ERROR.

### 5. CALCULATION_ERROR (near-miss) — canonical "savings" pattern was correct, my test was wrong
**Symptom:** Classification eval flagged "how much money costpilot save us" for returning `metric=spend_usd` instead of `tokens_saved`.
**Investigation result:** This was **not a bug**. The `savings_total` canonical intent deliberately returns a richer analysis (premium-tier spend breakdown + projected annual savings in dollar terms), for which `spend_usd` is the correct base metric. The eval case's expectation was wrong, not the code — fixed the test, not the implementation. Included here because "investigate before fixing" is itself a documented outcome, not just a silent no-op.
**Category:** Test defect, not a product defect.

### 6. Adversarial / no typo tolerance
**Symptom:** "top ppl by toknes" failed to classify (no known keyword matched "ppl" or "toknes").
**Fix:** Added a bounded typo-correction pass (`_ask_correct_typos`) using `difflib` against a small domain vocabulary, plus a literal abbreviation table (`ppl`→`people`, `dept`→`department`, etc.) for intentional shorthand vs. genuine misspellings.
**Regression caught during this fix:** The first version of this corrector turned the correctly-spelled word "uses" into "users" (edit-distance to "users" is *closer* than "toknes" is to "tokens" — 0.889 vs. 0.833 — so distance alone can't tell a typo from a valid word). Fixed by adding a small protected-words list for known false-positive collisions, and this exact case is now a regression test (`test_named_entity...` — see `_ASK_TYPO_PROTECTED_WORDS`).
**Category:** PARSING (typo tolerance) / self-caught regression before merge.

### 7. CONTEXT_ERROR (partial) — named department not classified as the `entity` field
**Symptom:** "What did Sales spend last month?" resolved `entity="overview"` instead of `entity="department"`.
**Root cause:** `_ask_intent` is deliberately pure/DB-free — it cannot look up whether "Sales" is a real department name, only whether the literal word "department" appears.
**Fix:** Not fixed in `_ask_intent` (architecturally can't be, without giving the fast classifier database access, which would compromise its speed and testability). Instead, `_ask_costpilot_answer` — the layer that already has DB access via `_ask_named_department` — now promotes `entity` to `"department"` after resolving a real department name, when no other ranking dimension was named. Verified correct via a targeted unit test, not just relaxing the classification eval's expectation, since the *user-visible* numbers were already correct (verified live) — what was wrong was internal classification metadata used for narration/title text.
**Category:** CONTEXT_ERROR (architectural boundary, not a bug in either layer individually).

### 8. RESPONSE_ERROR — dangerous silent-total on ambiguous names
**Symptom (found via live-fixture testing, not the automated eval suites — see below):** "How much did Chris spend?" with two equally-matching "Chris" people in the data silently answered with the **unfiltered company-wide total**, presented as if it had resolved the name. This is the single most serious finding of this phase — a confident, wrong answer, exactly what Phase 21's design principles exist to prevent.
**Root cause:** `_ask_named_entity` already correctly abstained (returned `None`) on ties — but nothing checked *why* it returned `None` before falling through to a generic, unscoped answer.
**Fix:** Added `_ask_named_entity_ambiguity()` (returns the tied candidates) and a clarification early-return in `_ask_costpilot_answer`: when a name is ambiguous, the response is now `{"intent": "clarification_required", "answer": "I found 2 matches for that name: Chris Johnson, Chris Smith. Which one did you mean?"}` instead of a number. A name that matches *nothing* (as opposed to matching two things) is a different, lower-risk case and still falls through to the normal overview answer, since there's no real ambiguity to clarify.
**How this was found:** Not caught by either automated eval suite as originally written — both happened to test single-Chris-style fixtures elsewhere in the existing suite (`test_ambiguous_name_is_not_guessed`, which only checked that `_ask_named_entity` itself returns `None`, not what the *end-to-end answer* does with that `None`). Found by deliberately constructing an end-to-end two-Chris fixture per Phase 6's explicit instruction to test this scenario. This is the clearest example in this phase of why end-to-end testing catches a class of bug that unit-testing one function in isolation cannot.
**Category:** RESPONSE_ERROR / missing clarification path.

### 9. A latent crash risk, not a wrong answer — `db=None` handling
**Symptom:** Writing the regression test for #8 revealed that `_ask_named_department` crashes outright (`AttributeError`) if ever called without a database session.
**Fix:** Added a defensive `db is None` guard. In production this can never actually happen (FastAPI's `Depends(get_db)` always supplies a real session), but the defensive check is free and correct regardless.
**Category:** Defensive robustness, not a behavioral bug.

## Found, documented, deliberately not fixed this phase

### 10. Scalability — `project_activity_reporting` doesn't aggregate in SQL
**Measured:** 0.024s at 47 rows, 2.455s at 20,047 rows (see Accuracy Report §5). Scales roughly linearly with row count because aggregation happens in a Python loop over every fetched row, not in the database.
**Why not fixed:** This is a core, heavily-used, already-tested function underlying every report in the product, not just Ask CostPilot. Rewriting its aggregation strategy is a substantial, cross-cutting refactor with real regression risk, and does not fit "make the safest reasonable engineering decision and continue" — it's the kind of change that deserves its own reviewed plan, not a rider on an accuracy-hardening pass. Flagged as the top priority for future work.

### 11. `CROSS_ENTITY_COMPARISON` has no deterministic contract
**Symptom:** "Compare Sales vs Marketing" or "OpenAI vs Anthropic" works correctly today, but only because the live agent calls the lookup tool twice and reasons about the difference itself — there's no dedicated tool enforcing that shape the way `get_change_drivers` enforces a period-over-period diff.
**Why not fixed:** Verified safe in practice (the agent handles it correctly and honestly, confirmed live), and building a new `compare_entities` tool with its own validation contract is a real feature addition, not a bug fix — appropriately scoped as a documented gap (see Question Catalog §4) rather than rushed in.

### 12. `.filter(workspace_filter(...))` footgun in 5 unrelated files
**Found while building department filtering** (`workspace_filter()` returns `None` to mean "don't filter," but `.filter(None)` in SQLAlchemy builds a `WHERE NULL` clause that matches *zero* rows — the opposite of the intended behavior). Fixed in the one place this phase's new code touched (`_ask_named_department`). The identical pre-existing pattern exists in `core/auditor.py:422`, `api/routes_auditor.py:86`, and `api/routes_reports.py:68/139/249` — outside Ask CostPilot, so not touched, but flagged since it's the same bug class and could be silently zeroing out reports for any caller that passes a falsy `workspace_id`.

### 13. Twelve pre-existing test failures (`db=None` fixture gap)
Not an Ask CostPilot behavior defect — a set of existing tests in `tests/test_ask_costpilot.py` call the endpoint with `db=None`, which was already fragile before this phase (confirmed by reverting local changes and reproducing the identical failures). Not fixed because it's a test-fixture completeness issue in code this task's scope didn't ask to touch, and because two of my own new tests hit the identical gap and were fixed by supplying a proper fake DB rather than changing the shared fixture helper other tests still rely on.

## Anti-overfitting check (Phase 20)

Every fix above targeted a *pattern*, not a sentence:
- The regex fix (#3) corrected 3 near-identical patterns at once, not just the one that appeared in a test.
- The typo corrector (#6) is a general vocabulary-distance mechanism, not a lookup table of the specific adversarial test strings.
- The department/provider scoping fixes (#1, #2) work for any department/provider name present in the data, not just "Sales" or "Anthropic."
- The ambiguity fix (#8) works for any tied name collision, not specifically "Chris."

No test passes because its exact input string is special-cased in the implementation.
