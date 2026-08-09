"""
Runnable Ask CostPilot evaluation harness.

Fires every case in ask_costpilot_eval_cases.py at the real ask_costpilot()
endpoint function in-process, against whatever database this backend is
currently pointed at (defaults to the local backend/fage.db). Whichever
internal path answers (the agent tool loop, if ASK_COSTPILOT_AGENT_MODE is
on and ANTHROPIC_API_KEY is valid, or the deterministic fallback otherwise)
is graded the same way -- this measures the user-visible behavior, not one
specific implementation path.

Usage:
    cd backend && source venv311/bin/activate   # needs Python 3.10+
    python3 scripts/ask_costpilot_eval.py
    python3 scripts/ask_costpilot_eval.py --verbose
    python3 scripts/ask_costpilot_eval.py --filter department

This does NOT require a live ANTHROPIC_API_KEY to run -- with an invalid or
missing key, every case still runs against the deterministic fallback path
and is graded the same way. Set a valid ANTHROPIC_API_KEY to also exercise
the agent tool loop.
"""
import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ask_costpilot_eval_cases import CASES  # noqa: E402


def _run_turn(ask_costpilot, AskCostPilotRequest, AskCostPilotMessage, AskCostPilotContext, db, conversation, prior_context, question, days):
    request = AskCostPilotRequest(
        question=question,
        days=days,
        conversation=[
            AskCostPilotMessage(role=role, content=content) for role, content in conversation
        ],
        context=AskCostPilotContext(**prior_context) if prior_context else None,
    )
    result = ask_costpilot(request, db=db)
    conversation.append(("user", question))
    if isinstance(result, dict):
        conversation.append(("assistant", str(result.get("answer") or "")))
        next_context = result.get("conversation_context") or {}
    else:
        next_context = {}
    return result, conversation, next_context


def _check(result: dict, checks: dict) -> list[str]:
    failures = []
    answer = str((result or {}).get("answer") or "")
    answer_lower = answer.lower()

    if checks.get("no_error") and (result is None or "error" in result and result.get("error")):
        failures.append("result contained an error / was None")

    if checks.get("answer_not_empty") and not answer.strip():
        failures.append("answer was empty")

    for phrase_group in [checks.get("answer_contains_any")] if checks.get("answer_contains_any") else []:
        if not any(phrase.lower() in answer_lower for phrase in phrase_group):
            failures.append(f"answer did not contain any of {phrase_group!r}")

    for phrase in checks.get("answer_excludes") or []:
        if phrase.lower() in answer_lower:
            failures.append(f"answer unexpectedly contained {phrase!r}")

    for number in checks.get("answer_excludes_numbers") or []:
        # Whole-number token match only (avoid false hits inside decimals).
        if re.search(rf"(?<!\d)(?<!\.){re.escape(str(number))}(?!\.\d)(?!\d)", answer):
            failures.append(f"answer unexpectedly contained the number {number}")

    max_rows = checks.get("evidence_max_rows")
    if max_rows is not None:
        evidence = (result or {}).get("evidence") or []
        if len(evidence) > max_rows:
            failures.append(f"evidence had {len(evidence)} rows, expected at most {max_rows}")

    expected_charged_unit = checks.get("filters_charged_unit_equals")
    if expected_charged_unit is not None:
        # Only the deterministic path's payload exposes a structured
        # `filters` dict -- the agent path proves the same thing (that
        # department scope carried through a follow-up) by naming the
        # department in its own answer text instead. Accept either signal;
        # a check tied to one path's payload shape would fail the other
        # path for being right in a different way.
        actual = ((result or {}).get("filters") or {}).get("charged_unit")
        if actual != expected_charged_unit and expected_charged_unit.lower() not in answer_lower:
            failures.append(
                f"neither filters.charged_unit ({actual!r}) nor the answer text "
                f"mentioned the expected department {expected_charged_unit!r}"
            )

    return failures


def main():
    parser = argparse.ArgumentParser(description="Run the Ask CostPilot eval suite.")
    parser.add_argument("--filter", default=None, help="Only run cases whose id contains this substring.")
    parser.add_argument("--verbose", action="store_true", help="Print every answer, not just failures.")
    args = parser.parse_args()

    from api.routes_efficiency import (
        ask_costpilot, AskCostPilotRequest, AskCostPilotMessage, AskCostPilotContext,
    )
    from database.db import SessionLocal

    db = SessionLocal()
    passed, failed, errored = 0, 0, 0
    failures_report = []

    cases = [c for c in CASES if not args.filter or args.filter in c["id"]]

    try:
        for case in cases:
            case_id = case["id"]
            checks = case["checks"]
            try:
                if "turns" in case:
                    conversation: list = []
                    context: dict = {}
                    result = None
                    for turn in case["turns"]:
                        result, conversation, context = _run_turn(
                            ask_costpilot, AskCostPilotRequest, AskCostPilotMessage,
                            AskCostPilotContext, db, conversation, context,
                            turn["question"], turn.get("days", 30),
                        )
                else:
                    request = AskCostPilotRequest(question=case["question"], days=case.get("days", 30))
                    result = ask_costpilot(request, db=db)
            except Exception as exc:
                errored += 1
                failures_report.append((case_id, [f"raised an exception: {exc!r}"], None))
                continue

            case_failures = _check(result, checks)
            mode = (result or {}).get("assistant_mode")
            if case_failures:
                failed += 1
                failures_report.append((case_id, case_failures, mode))
            else:
                passed += 1

            if args.verbose:
                print(f"[{case_id}] mode={mode}")
                print(f"  Q: {case.get('question') or case.get('turns')}")
                print(f"  A: {(result or {}).get('answer')}")
                print()
    finally:
        db.close()

    total = passed + failed + errored
    print("=" * 70)
    print(f"Ask CostPilot eval: {passed}/{total} passed ({errored} errored)")
    print("=" * 70)
    if failures_report:
        print("\nFailures:")
        for case_id, reasons, mode in failures_report:
            print(f"  [{case_id}] mode={mode}")
            for reason in reasons:
                print(f"    - {reason}")

    accuracy = (passed / total * 100) if total else 0.0
    print(f"\nAccuracy: {accuracy:.1f}%")
    sys.exit(0 if not failed and not errored else 1)


if __name__ == "__main__":
    main()
