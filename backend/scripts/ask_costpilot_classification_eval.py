"""
Fast, deterministic classification-accuracy eval for Ask CostPilot
(Phase 15/18). Calls only _ask_intent() -- no database, no API key, no
network -- so all 100+ cases run in well under a second and can be part of
routine CI, unlike the end-to-end eval which needs real data and (for the
agent path) a live model call.

Usage:
    python3 scripts/ask_costpilot_classification_eval.py
    python3 scripts/ask_costpilot_classification_eval.py --verbose
    python3 scripts/ask_costpilot_classification_eval.py --category metric
"""
import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ask_costpilot_classification_cases import CASES  # noqa: E402

# Maps a case's `expected` key to the reporting category buckets used in
# the accuracy report (Phase 18 asks for these specific buckets).
_FIELD_TO_CATEGORY = {
    "intent": "intent_accuracy",
    "entity": "entity_resolution_accuracy",
    "metric": "metric_accuracy",
    "period_key": "date_accuracy",
    "days": "date_accuracy",
    "direction": "intent_accuracy",
    "result_limit": "intent_accuracy",
    "provider": "entity_resolution_accuracy",
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--category", default=None, help="Only show mismatches for this expected field.")
    args = parser.parse_args()

    from api.routes_efficiency import _ask_intent

    category_totals = defaultdict(int)
    category_correct = defaultdict(int)
    difficulty_totals = defaultdict(int)
    difficulty_correct = defaultdict(int)
    persona_totals = defaultdict(int)
    persona_correct = defaultdict(int)
    mismatches = []
    case_pass, case_fail = 0, 0

    for case in CASES:
        expected = case["expected"]
        actual = _ask_intent(case["question"], 30)
        case_ok = True
        for field, expected_value in expected.items():
            if args.category and field != args.category:
                continue
            category = _FIELD_TO_CATEGORY.get(field, "other")
            category_totals[category] += 1
            actual_value = actual.get(field)
            if actual_value == expected_value:
                category_correct[category] += 1
            else:
                case_ok = False
                mismatches.append({
                    "id": case["id"], "question": case["question"], "field": field,
                    "expected": expected_value, "actual": actual_value,
                })

        difficulty_totals[case["difficulty"]] += 1
        persona_totals[case["persona"]] += 1
        if case_ok:
            case_pass += 1
            difficulty_correct[case["difficulty"]] += 1
            persona_correct[case["persona"]] += 1
        else:
            case_fail += 1

        if args.verbose:
            print(f"[{case['id']}] {case['question']!r} -> {actual}")

    total_cases = case_pass + case_fail
    print("=" * 78)
    print(f"Ask CostPilot classification eval: {total_cases} cases, "
          f"{case_pass} fully correct, {case_fail} with at least one field wrong")
    print("=" * 78)

    print("\nPer-category accuracy (only fields each case actually asserts):")
    for category in sorted(category_totals):
        total = category_totals[category]
        correct = category_correct[category]
        print(f"  {category:32s} {correct:3d}/{total:3d}  ({correct / total * 100:5.1f}%)")

    print("\nPer-difficulty-level case accuracy:")
    for level in sorted(difficulty_totals):
        total = difficulty_totals[level]
        correct = difficulty_correct[level]
        print(f"  Level {level}: {correct:3d}/{total:3d}  ({correct / total * 100:5.1f}%)")

    print("\nPer-persona case accuracy:")
    for persona in sorted(persona_totals):
        total = persona_totals[persona]
        correct = persona_correct[persona]
        print(f"  {persona:14s} {correct:3d}/{total:3d}  ({correct / total * 100:5.1f}%)")

    if mismatches:
        print(f"\nMismatches ({len(mismatches)}):")
        for m in mismatches:
            print(f"  [{m['id']}] {m['question']!r}")
            print(f"      {m['field']}: expected {m['expected']!r}, got {m['actual']!r}")

    overall = case_pass / total_cases * 100 if total_cases else 0.0
    print(f"\nOverall case accuracy: {overall:.1f}%")
    sys.exit(0 if not mismatches else 1)


if __name__ == "__main__":
    main()
