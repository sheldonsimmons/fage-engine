# Ask CostPilot — Numeric/Date Accuracy Audit

This is a different check than the existing eval harnesses documented in
`ASK_COSTPILOT_ACCURACY_REPORT.md`. Those check **behavior**: did the
answer avoid crashing, stay non-empty, keep follow-up context, avoid
inventing content. They can't catch a well-formed answer that states the
*wrong number* or *wrong date range* — exactly the class of bug repeated
manual spot-checking turned up (wrong months, wrong weeks, fabricated date
labels).

`scripts/ask_costpilot_accuracy_audit.py` checks **correctness** instead:
for each question, it independently recomputes the expected period
boundaries (`core.analytics_periods.resolve_primary_period`) and the
expected metric value (`core.metrics_query.run_metrics_query`) — the same
two trusted primitives Ask CostPilot itself is built on — and flags any
disagreement with the real answer from `ask_costpilot()`.

## Running it

No `ANTHROPIC_API_KEY` is required — with none set, `ask_costpilot()`
falls back to its deterministic path, which is graded the same way.

```bash
cd backend && source venv311/bin/activate

# Against whatever DATABASE_URL currently points at (defaults to backend/fage.db):
python3 scripts/ask_costpilot_accuracy_audit.py

# Scoped to one workspace, with per-case output:
python3 scripts/ask_costpilot_accuracy_audit.py --workspace-id WS-... --verbose

# Just the static cases, skipping the real-data-driven department cases:
python3 scripts/ask_costpilot_accuracy_audit.py --skip-dynamic

# Write a markdown report:
python3 scripts/ask_costpilot_accuracy_audit.py --report /tmp/ask_accuracy_report.md
```

Against live Heroku data (same pattern `ask_costpilot_eval.py` already
uses — `database/db.py`'s `SessionLocal()` binds to whatever
`DATABASE_URL` the dyno already has set):

```bash
heroku run python3 backend/scripts/ask_costpilot_accuracy_audit.py --workspace-id WS-... -a <app>
```

## What it checks per question

1. **Period classification** — the answer's own `conversation_context.period_key`
   must match the case's hand-declared expectation (e.g. "last month" ->
   `last_month`), not whatever `_ask_intent()` happens to produce — so a
   classification regression is caught, not silently reproduced on both
   sides of the check.
2. **Period boundaries** — the answer's `period.date_from`/`date_to` must
   match `resolve_primary_period()`'s independently-computed start/end for
   that period_key.
3. **The number itself** — every number in the answer is extracted with
   `_ask_extract_numbers()` (the same guardrail already used for the
   agent loop's narration-fidelity check) and at least one must match the
   `run_metrics_query()` ground truth within tolerance. A small fallback
   (`_small_number_present`) covers request counts and genuinely-zero
   figures, which `_ask_extract_numbers()` deliberately excludes (it's
   built to catch large fabricated numbers, not verify small real ones).

## What's covered today, what isn't

`scripts/ask_costpilot_accuracy_cases.py` covers the "AI Spend Overview"
and simple "Comparisons" categories of the 156-question reference bank
(`/tmp/costpilot-question-bank.html`, "100 Ways to Ask CostPilot") — every
question with a single number and period that can be independently
recomputed. `ask_costpilot_accuracy_audit.py` also builds a few
department-scoped cases at run time from whatever real department names
exist in the pointed-at data (`_dynamic_department_cases`), so the same
suite works unmodified against any workspace's real data, not just one
hardcoded dataset.

Not yet covered (same categories the existing behavioral eval, not this
one, is responsible for): agents, budget/risk, business outcomes,
optimization/savings, governance, and any open-ended/advisory question
with no single correct numeric answer ("what should I review first?").
Extending coverage means adding cases the same way — a question, its
expected `period_key`, and the `run_metrics_query()` metric/filters that
independently compute its answer.
