"""
Ask CostPilot numeric/date accuracy audit.

Where scripts/ask_costpilot_eval.py checks BEHAVIOR ("did it answer without
crashing or inventing content"), this script checks CORRECTNESS: for every
case in ask_costpilot_accuracy_cases.py (plus a handful of real-data-driven
department cases discovered at run time), it fires the question at the real
ask_costpilot() endpoint function in-process, then independently recomputes
the expected period boundaries and metric value using the same two trusted
primitives Ask CostPilot itself is built on --
core.analytics_periods.resolve_primary_period() and
core.metrics_query.run_metrics_query() -- and flags any disagreement.

This exists because manual spot-checking of individual questions this
session repeatedly found real bugs (wrong month/week windows, fabricated
date labels) that a purely structural eval ("answer not empty", "no error")
can't catch -- the answer here can look perfectly well-formed and still be
wrong. A case's `period_key` is a hand-read expectation for what
_ask_intent() should classify that phrasing as, not something derived by
calling _ask_intent() -- so a classification bug is still caught by
comparing it against the answer's own conversation_context.period_key,
instead of being reproduced identically on both sides of the check.

Runs against whatever database backend/database/db.py's SessionLocal() is
currently bound to (DATABASE_URL) -- a local sqlite/Postgres copy, or (via
`heroku run`) live Heroku Postgres, exactly like ask_costpilot_eval.py.
No ANTHROPIC_API_KEY is required: with none set, ask_costpilot() falls
back to its deterministic path, which is graded the same way.

Usage:
    cd backend && source venv311/bin/activate
    python3 scripts/ask_costpilot_accuracy_audit.py
    python3 scripts/ask_costpilot_accuracy_audit.py --workspace-id WS-...
    python3 scripts/ask_costpilot_accuracy_audit.py --filter spend_this_month
    python3 scripts/ask_costpilot_accuracy_audit.py --verbose
    python3 scripts/ask_costpilot_accuracy_audit.py --report out.md

    # Against live Heroku data:
    heroku run python3 scripts/ask_costpilot_accuracy_audit.py --workspace-id WS-... -a <app>
"""
import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ask_costpilot_accuracy_cases import CASES  # noqa: E402

# report-summary field name -> core.metrics_query registry metric id.
# project_activity_reporting()'s summary dict and run_metrics_query()'s
# rows use different metric identifiers for the same underlying figures
# (confirmed in core/metrics_catalog.py vs. api/routes_work_items.py).
_SUMMARY_TO_REGISTRY_METRIC = {
    "spend_usd": "ai_spend",
    "total_tokens": "total_tokens",
    "input_tokens": "input_tokens",
    "output_tokens": "output_tokens",
    "request_count": "ai_requests",
}


def _dynamic_department_cases(db, workspace_id, now):
    """
    Build a couple of department-scoped cases from whatever real
    department names exist in the data this run is pointed at, instead of
    hardcoding a department name that may not exist in every dataset.
    Returns [] (not an error) if there's no department data to find --
    that's a legitimate empty-workspace result, not a harness bug.
    """
    from core.analytics_periods import resolve_primary_period
    from core.metrics_query import run_metrics_query

    period = resolve_primary_period(period_key="this_month", days=31, timezone_name="UTC", now=now)
    ranked = run_metrics_query(
        db, workspace_id, metrics=["ai_spend"], dimensions=["department"],
        timeframe={"start": period.start, "end": period.end},
        sort="ai_spend", limit=3,
    )
    cases = []
    for row in ranked.rows or []:
        dept = (row.get("dimensions") or {}).get("department")
        if not dept:
            continue
        cases.append({
            "id": f"dynamic_dept_spend_this_month::{dept}",
            "question": f"How much did {dept} spend on AI this month?",
            "period_key": "this_month",
            "metric": "ai_spend",
            "filters": {"department": dept},
        })
    if ranked.rows:
        top_dept = (ranked.rows[0].get("dimensions") or {}).get("department")
        if top_dept:
            cases.append({
                "id": "dynamic_dept_highest_spend_this_month",
                "question": "Which department has the highest AI spend this month?",
                "period_key": "this_month",
                "metric": "ai_spend",
                "expect_entity_mentioned": top_dept,
            })
    return cases


def _small_number_present(result: dict, expected_value: float) -> bool:
    """
    _ask_extract_numbers() deliberately ignores any bare integer 0-9
    (list positions, "top 5", etc. -- see its docstring) because it's
    built to catch a narration inventing a large fabricated figure, not
    to verify a small one is present. That exclusion makes it blind to
    exactly the values a request-count or a genuinely-zero-spend period
    produces (confirmed while building this harness: a correct "0
    requests" / "2 governed requests" answer registered as a
    NUMERIC_MISMATCH purely because 0 and 2 are excluded from the
    narration-fidelity extractor's output, not because the answer was
    wrong). Only used as a fallback for small values -- normal-size
    figures still go through the shared, well-tested extractor above.
    """
    if not (abs(expected_value) < 10 and expected_value == int(expected_value)):
        return False
    # Deliberately scoped to just the prose answer, not the full response
    # JSON -- the JSON is full of small integers with nothing to do with
    # the claimed metric (ids, unrelated zero counts, list lengths), and
    # matching against all of it would let an unrelated coincidental "0"
    # or "1" elsewhere in the payload paper over a genuine mismatch.
    haystack = str(result.get("answer") or "")
    token = str(int(expected_value))
    if re.search(rf"(?<!\d)(?<!\.){re.escape(token)}(?!\.\d)(?!\d)", haystack):
        return True
    # A genuinely-zero (or other small) figure is usually written as a
    # formatted decimal -- "$0.0000" -- not a bare integer. The
    # whole-token regex above deliberately rejects a digit followed by
    # ".<digit>" (so "12" in "12.5" isn't misread as a bare 12), but that
    # same guard also rejects "0" in "0.0000" -- exactly the correct,
    # zero-decimal representation of the expected value itself.
    # Confirmed live: "Ai Spend was $0.0000 for Sep 6-12" registered as a
    # NUMERIC_MISMATCH for an expected value of 0.0 even though the
    # answer states the correct figure outright. Check a few common
    # decimal renderings directly instead of the bare-integer regex.
    decimal_variants = {
        f"{expected_value:.4f}", f"{expected_value:.2f}", f"{expected_value:.1f}",
        f"{expected_value:.0f}.0", str(float(expected_value)),
    }
    return any(variant in haystack for variant in decimal_variants)


def _run_case(ask_costpilot, AskCostPilotRequest, db, workspace_id, case, now):
    from api.routes_efficiency import _ask_extract_numbers
    from core.analytics_periods import resolve_primary_period
    from core.metrics_query import run_metrics_query

    findings = []
    request = AskCostPilotRequest(
        question=case["question"], days=case.get("days", 30), workspace_id=workspace_id,
    )
    result = ask_costpilot(request, db=db)
    if not isinstance(result, dict) or not (result.get("answer") or "").strip():
        return ["EMPTY_OR_INVALID: no usable answer returned"], result

    conversation_context = result.get("conversation_context") or {}
    actual_period_key = conversation_context.get("period_key")
    expected_period_key = case["period_key"]
    if actual_period_key != expected_period_key:
        findings.append(
            f"PERIOD_KEY_MISMATCH: expected period_key={expected_period_key!r}, "
            f"got {actual_period_key!r}"
        )

    expected_period = resolve_primary_period(
        period_key=expected_period_key, days=case.get("days", 30),
        timezone_name="UTC", now=now,
    )
    answer_period = result.get("period") or {}
    answer_date_from = answer_period.get("date_from")
    answer_date_to = answer_period.get("date_to")
    if answer_date_from and answer_date_to:
        try:
            actual_start = datetime.fromisoformat(answer_date_from)
            actual_end = datetime.fromisoformat(answer_date_to)
        except ValueError:
            actual_start = actual_end = None
        if actual_start is None or abs((actual_start - expected_period.start).total_seconds()) > 2:
            findings.append(
                f"PERIOD_START_MISMATCH: expected {expected_period.start.isoformat()}, "
                f"got {answer_date_from!r}"
            )
        if actual_end is None or abs((actual_end - expected_period.end).total_seconds()) > 2:
            findings.append(
                f"PERIOD_END_MISMATCH: expected {expected_period.end.isoformat()}, "
                f"got {answer_date_to!r}"
            )

    expect_entity = case.get("expect_entity_mentioned")
    if expect_entity:
        haystack = (str(result.get("answer") or "") + " " + str(result.get("evidence") or "")).lower()
        if expect_entity.lower() not in haystack:
            findings.append(
                f"ENTITY_MISMATCH: expected {expect_entity!r} to be named in the answer/evidence, "
                f"but it wasn't"
            )

    metric = case["metric"]
    ground_truth = run_metrics_query(
        db, workspace_id, metrics=[metric],
        filters=case.get("filters") or {},
        timeframe={"start": expected_period.start, "end": expected_period.end},
    )
    if ground_truth.errors:
        findings.append(f"GROUND_TRUTH_ERROR: {ground_truth.errors}")
        return findings, result
    expected_value = round(float(ground_truth.rows[0].get(metric, 0.0)), 6) if ground_truth.rows else 0.0

    try:
        extracted = _ask_extract_numbers(result)
    except Exception as exc:
        findings.append(f"NUMBER_EXTRACTION_ERROR: {exc!r}")
        extracted = set()

    tolerance = case.get("tolerance", max(0.01, abs(expected_value) * 0.005))
    numeric_match = any(abs(value - expected_value) <= tolerance for value in extracted)
    if not numeric_match and _small_number_present(result, expected_value):
        numeric_match = True
    if not numeric_match:
        findings.append(
            f"NUMERIC_MISMATCH: expected {metric}={expected_value} (±{tolerance:.4g}), "
            f"answer contained {sorted(extracted)}"
        )

    return findings, result


def main():
    parser = argparse.ArgumentParser(description="Run the Ask CostPilot numeric/date accuracy audit.")
    parser.add_argument("--filter", default=None, help="Only run cases whose id contains this substring.")
    parser.add_argument("--workspace-id", default=None, help="Scope every question to this workspace.")
    parser.add_argument("--skip-dynamic", action="store_true", help="Skip real-data-driven department cases.")
    parser.add_argument("--verbose", action="store_true", help="Print every answer, not just failures.")
    parser.add_argument("--report", default=None, help="Write a markdown report to this path.")
    args = parser.parse_args()

    from api.routes_efficiency import ask_costpilot, AskCostPilotRequest
    from database.db import SessionLocal

    db = SessionLocal()
    now = datetime.utcnow()
    passed, failed, errored = 0, 0, 0
    results_report = []

    cases = list(CASES)
    if not args.skip_dynamic:
        try:
            cases += _dynamic_department_cases(db, args.workspace_id, now)
        except Exception as exc:
            print(f"warning: could not build dynamic department cases: {exc!r}", file=sys.stderr)
    if args.filter:
        cases = [c for c in cases if args.filter in c["id"]]

    try:
        for case in cases:
            case_id = case["id"]
            try:
                findings, result = _run_case(ask_costpilot, AskCostPilotRequest, db, args.workspace_id, case, now)
            except Exception as exc:
                errored += 1
                results_report.append((case_id, "ERROR", [f"raised an exception: {exc!r}"], None))
                continue

            if findings:
                failed += 1
                results_report.append((case_id, "FAIL", findings, (result or {}).get("answer")))
            else:
                passed += 1
                results_report.append((case_id, "PASS", [], (result or {}).get("answer")))

            if args.verbose:
                status = results_report[-1][1]
                print(f"[{case_id}] {status}")
                print(f"  Q: {case['question']}")
                print(f"  A: {(result or {}).get('answer')}")
                for finding in findings:
                    print(f"    - {finding}")
                print()
    finally:
        db.close()

    total = passed + failed + errored
    print("=" * 70)
    print(f"Ask CostPilot accuracy audit: {passed}/{total} passed ({errored} errored)")
    print("=" * 70)
    if failed or errored:
        print("\nFailures:")
        for case_id, status, findings, _ in results_report:
            if status == "PASS":
                continue
            print(f"  [{case_id}] {status}")
            for finding in findings:
                print(f"    - {finding}")

    accuracy = (passed / total * 100) if total else 0.0
    print(f"\nAccuracy: {accuracy:.1f}%")

    if args.report:
        lines = [
            "# Ask CostPilot Accuracy Audit", "",
            f"Run at {now.isoformat()}Z against workspace `{args.workspace_id or '(default/global)'}`.", "",
            f"**{passed}/{total} passed** ({errored} errored) -- {accuracy:.1f}% accuracy.", "",
        ]
        for case_id, status, findings, answer in results_report:
            lines.append(f"## [{status}] {case_id}")
            lines.append(f"- Answer: {answer!r}")
            for finding in findings:
                lines.append(f"- {finding}")
            lines.append("")
        Path(args.report).write_text("\n".join(lines))
        print(f"\nMarkdown report written to {args.report}")

    sys.exit(0 if not failed and not errored else 1)


if __name__ == "__main__":
    main()
