"""
Ask CostPilot evaluation corpus — Stage 1: interpretation accuracy.

Tests _ask_intent() directly (the pure, deterministic, offline keyword
parser) against tests/ask_eval/corpus.py's expected structured fields.
Deliberately does NOT call _resolve_ask_intent() (which can invoke
OpenAI for ambiguous cases) or the Claude agent loop -- both are
non-deterministic, network-dependent, and cost money per call, which
would make this suite flaky and slow to run in CI. This suite measures
the deterministic fallback's accuracy, which is also the floor every
other path (OpenAI-assisted classification, the Claude agent) has to
clear, since it's what the product falls back to when either is
unavailable.

Narration accuracy (does the LLM's prose correctly describe already-
computed facts) is explicitly OUT of scope here too -- it requires a
live model call per case and a judge (human or LLM) to score, which is
a separate, slower-running eval track to build later, not a per-commit
CI gate. What this suite CAN catch without any of that: every
misinterpreted intent/entity/metric/filter/period, which is the most
common real failure mode (a wrong answer computed correctly from the
wrong inputs looks identical to a right answer at the prose level).
"""
import pytest

from api.routes_efficiency import _ask_intent
from tests.ask_eval.corpus import CASES

_FIELD_MAP = {
    "expected_intent": "intent",
    "expected_entity": "entity",
    "expected_metric": "metric",
    "expected_direction": "direction",
    "expected_period_key": "period_key",
    "expected_days": "days",
    "expected_comparison_key": "comparison_key",
    "expected_source_platform": "source_platform",
    "expected_model_tier": "model_tier",
    "expected_result_limit": "result_limit",
    "expected_usage_status": "usage_status",
    "expected_budget_scope": "budget_scope",
    "expected_outcome_filter": "outcome_filter",
}

_TESTABLE = [c for c in CASES if not c.skip_interpretation]


def _case_param(case):
    if case.known_gap:
        return pytest.param(case, id=case.id, marks=pytest.mark.xfail(
            reason=case.known_gap, strict=True,
        ))
    return pytest.param(case, id=case.id)


@pytest.mark.parametrize("case", [_case_param(c) for c in _TESTABLE])
def test_interpretation_field_accuracy(case):
    parsed = _ask_intent(case.question, default_days=30)
    mismatches = []
    for expected_attr, parsed_key in _FIELD_MAP.items():
        expected_value = getattr(case, expected_attr)
        if expected_value is None:
            continue  # this case doesn't make a claim about this field
        actual_value = parsed.get(parsed_key)
        if actual_value != expected_value:
            mismatches.append(f"{parsed_key}: expected {expected_value!r}, got {actual_value!r}")
    assert not mismatches, (
        f"[{case.id}] {case.category}/{case.kind} {case.question!r} — "
        f"{'; '.join(mismatches)}"
    )


def test_interpretation_accuracy_summary(capsys):
    """
    Not a pass/fail gate -- a per-field accuracy report, run with
    `pytest -s` to see it. This is the "measure Ask CostPilot accuracy"
    deliverable (audit item 8) for the interpretation stage specifically:
    intent/entity/metric/filter/period accuracy, broken out per field
    rather than one pass/fail blob, so a regression in (say) period-key
    parsing doesn't hide behind an otherwise-fine intent accuracy number.
    """
    known_gap_cases = [c for c in _TESTABLE if c.known_gap]
    clean_cases = [c for c in _TESTABLE if not c.known_gap]

    field_totals = {key: 0 for key in _FIELD_MAP.values()}
    field_correct = {key: 0 for key in _FIELD_MAP.values()}
    category_totals: dict[str, int] = {}
    category_correct: dict[str, int] = {}

    for case in clean_cases:
        parsed = _ask_intent(case.question, default_days=30)
        category_totals[case.category] = category_totals.get(case.category, 0) + 1
        case_ok = True
        for expected_attr, parsed_key in _FIELD_MAP.items():
            expected_value = getattr(case, expected_attr)
            if expected_value is None:
                continue
            field_totals[parsed_key] += 1
            if parsed.get(parsed_key) == expected_value:
                field_correct[parsed_key] += 1
            else:
                case_ok = False
        if case_ok:
            category_correct[case.category] = category_correct.get(case.category, 0) + 1

    print("\n\n=== Ask CostPilot interpretation accuracy (Stage 1, deterministic parser) ===")
    print(f"Cases evaluated: {len(clean_cases)} clean + {len(known_gap_cases)} known-gap "
          f"(xfail, tracked separately) of {len(CASES)} total corpus entries "
          f"({len(CASES) - len(_TESTABLE)} skipped: help/product/follow-up/named-entity, "
          f"which need context or the OpenAI/Claude layer, not this offline parser)\n")
    print("By field (clean cases only -- known gaps excluded so they don't dilute the number "
          "that's supposed to represent 'accuracy on cases the parser is expected to get right'):")
    for key in _FIELD_MAP.values():
        total = field_totals[key]
        if total == 0:
            continue
        pct = 100.0 * field_correct[key] / total
        print(f"  {key:20s} {field_correct[key]:3d}/{total:3d}  {pct:5.1f}%")
    print("\nBy category (clean cases only):")
    for category in sorted(category_totals):
        total = category_totals[category]
        correct = category_correct.get(category, 0)
        pct = 100.0 * correct / total
        print(f"  {category:15s} {correct:3d}/{total:3d}  {pct:5.1f}%")
    print(f"\nKnown gaps tracked (xfail, run `pytest -rx` to list reasons): {len(known_gap_cases)}")
    for case in known_gap_cases:
        print(f"  [{case.id}] {case.question!r}")
    print()
