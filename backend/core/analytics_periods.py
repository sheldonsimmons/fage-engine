"""Deterministic period planning for executive analytics comparisons."""

from calendar import monthrange
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@dataclass(frozen=True)
class AnalyticalPeriod:
    key: str
    start: datetime
    end: datetime
    timezone_name: str

    def contract(self) -> dict:
        result = asdict(self)
        result["start"] = self.start.isoformat()
        result["end"] = self.end.isoformat()
        result["interval"] = "half_open"
        zone = _workspace_zone(self.timezone_name)
        result["label"] = period_label(
            _from_utc_naive(self.start, zone),
            _from_utc_naive(self.end, zone),
        )
        return result


@dataclass(frozen=True)
class ComparisonPlan:
    mode: str
    primary: AnalyticalPeriod
    comparison: AnalyticalPeriod
    rule_version: str = "1.0"

    def contract(self) -> dict:
        return {
            "mode": self.mode,
            "primary": self.primary.contract(),
            "comparison": self.comparison.contract(),
            "rule_version": self.rule_version,
        }


def _workspace_zone(timezone_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone_name or "UTC")
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown workspace timezone: {timezone_name}") from exc


def _to_utc_naive(value: datetime, zone: ZoneInfo) -> datetime:
    localized = value.replace(tzinfo=zone) if value.tzinfo is None else value.astimezone(zone)
    return localized.astimezone(timezone.utc).replace(tzinfo=None)


def _from_utc_naive(value: datetime, zone: ZoneInfo) -> datetime:
    utc_value = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    return utc_value.astimezone(zone).replace(tzinfo=None)


def _shift_year(value: datetime, years: int) -> datetime:
    year = value.year + years
    day = min(value.day, monthrange(year, value.month)[1])
    return value.replace(year=year, day=day)


def _shift_month(value: datetime, months: int) -> datetime:
    month_index = value.year * 12 + value.month - 1 + months
    year, zero_based_month = divmod(month_index, 12)
    month = zero_based_month + 1
    day = min(value.day, monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def period_label(start: datetime, end: datetime) -> str:
    """Human label for a half-open interval, shown as inclusive calendar dates."""
    display_end = end - timedelta(microseconds=1)
    return f"{start.date().isoformat()} through {display_end.date().isoformat()}"


def format_date_range(start, end) -> str:
    """
    Human-readable calendar range, e.g. "Jul 8 – Aug 6, 2026" instead of
    "2026-07-08 through 2026-08-06" — accepts date or datetime objects.
    Single source of truth for Ask CostPilot's displayed date range (both
    the answer text and the frontend's "Date range" field read the same
    backend-computed string, so this one function fixes both).
    """
    start_date = start.date() if hasattr(start, "date") else start
    end_date = end.date() if hasattr(end, "date") else end
    if start_date == end_date:
        return start_date.strftime("%b %-d, %Y")
    if start_date.year == end_date.year:
        return f"{start_date.strftime('%b %-d')} – {end_date.strftime('%b %-d, %Y')}"
    return f"{start_date.strftime('%b %-d, %Y')} – {end_date.strftime('%b %-d, %Y')}"


def resolve_primary_period(
    *,
    period_key: Optional[str],
    days: int,
    timezone_name: str = "UTC",
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    now: Optional[datetime] = None,
) -> AnalyticalPeriod:
    """Resolve one approved period into UTC-naive database boundaries."""
    zone = _workspace_zone(timezone_name)
    utc_now = now or datetime.utcnow()
    local_now = _from_utc_naive(utc_now, zone)
    today = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    # today.weekday() is ISO (Monday=0..Sunday=6) -- using it directly made
    # "this_week"/"last_week" Monday-anchored here, while the frontend's own
    # resolveDatePreset("this_week"/"last_week") (frontend/js/reports.js)
    # is Sunday-anchored (JS Date.getDay(), Sunday=0), matching the
    # US-week convention the reports page's own "Last Week: Aug 30 - Sep 5"
    # label already shows users. Confirmed live 2026-09-11: Ask CostPilot
    # answered "the week of August 31 to September 7" (Monday-anchored) for
    # a person the reports page's Sunday-anchored "Last Week" filter showed
    # real activity for -- same nominal period, two different date ranges,
    # so a real answer looked wrong. (days_since_sunday + 1) % 7 converts
    # weekday() to "days since the most recent Sunday" instead.
    this_week = today - timedelta(days=(today.weekday() + 1) % 7)
    this_month = today.replace(day=1)
    this_quarter = today.replace(month=((today.month - 1) // 3) * 3 + 1, day=1)
    this_year = today.replace(month=1, day=1)

    if not period_key and date_from and date_to:
        start = _from_utc_naive(date_from, zone)
        end = _from_utc_naive(date_to, zone)
        key = "custom"
    elif period_key == "today":
        start, end, key = today, local_now, "today"
    elif period_key == "yesterday":
        start, end, key = today - timedelta(days=1), today, "yesterday"
    elif period_key == "this_week":
        start, end, key = this_week, local_now, "this_week"
    elif period_key == "last_week":
        start, end, key = this_week - timedelta(days=7), this_week, "last_week"
    elif period_key == "this_month":
        start, end, key = this_month, local_now, "this_month"
    elif period_key == "last_month":
        end = this_month
        start = (end - timedelta(days=1)).replace(day=1)
        key = "last_month"
    elif period_key == "this_quarter":
        start, end, key = this_quarter, local_now, "this_quarter"
    elif period_key == "last_quarter":
        end = this_quarter
        prior_day = end - timedelta(days=1)
        start = prior_day.replace(
            month=((prior_day.month - 1) // 3) * 3 + 1,
            day=1,
        )
        key = "last_quarter"
    elif period_key == "last_2q":
        # frontend/js/reports.js's "Last 2 Quarters" preset had no backend
        # equivalent at all -- a question scoped to it fell through to the
        # plain rolling_days fallback below instead, an actual mismatch,
        # not just a boundary drift. Combined year*4+quarter-index lets
        # "2 quarters back" subtract cleanly across a year boundary
        # without the off-by-one a day-stepping loop invites.
        end = this_quarter
        total_quarters = end.year * 4 + (end.month - 1) // 3
        start_quarters = total_quarters - 2
        start = end.replace(
            year=start_quarters // 4, month=(start_quarters % 4) * 3 + 1, day=1,
        )
        key = "last_2q"
    elif period_key == "this_year":
        start, end, key = this_year, local_now, "this_year"
    elif period_key == "last_year":
        start, end, key = this_year.replace(year=this_year.year - 1), this_year, "last_year"
    elif period_key == "all_time":
        # Accepted as a valid period_key (_ASK_PERIOD_KEYS) but never had a
        # branch here -- silently fell through to the rolling_days
        # fallback below, becoming a plain 30-day window (or whatever
        # `days` happened to be) despite the name promising literally
        # everything. A fixed far-past constant, not a real "since
        # inception" lookup (this function does pure date math, no db/
        # workspace_id access) -- 2000-01-01 predates every real or demo
        # dataset in this app by a wide margin, so it can't clip real data.
        start, end, key = today.replace(year=2000, month=1, day=1), today + timedelta(days=1), "all_time"
    elif period_key == "same_range_last_year":
        # Same rolling window size as a normal "days" lookup, just anchored
        # to end exactly one year ago instead of now — for ranking
        # questions scoped to "this time last year" rather than a
        # this-year-vs-last-year delta comparison (see comparison_plan's
        # same_period_previous_year mode for that case).
        end = _shift_year(local_now, -1)
        start = end - timedelta(days=max(1, int(days or 30)))
        key = "same_range_last_year"
    elif period_key == "same_date_last_year":
        start = _shift_year(today, -1)
        end = start + timedelta(days=1)
        key = "same_date_last_year"
    else:
        # This is the default path for almost every Ask CostPilot question
        # (no explicit period_key, just days=N) -- was end=local_now, a
        # rolling N*24-hour window ending at this exact moment, while the
        # frontend's own "Last N Days" presets (frontend/js/reports.js's
        # resolveDatePreset) are calendar-midnight-aligned: N full local
        # days plus all of today, ending at tomorrow's midnight. Mid-day,
        # the old version cut into part of what should have counted as
        # "N days ago" and included part of the day before that instead --
        # a smaller, sub-day version of the same this_week/last_week
        # anchor mismatch fixed above. today+1 day matches every other
        # "assume no future data exists" boundary this function already
        # uses (this_week/this_month/this_year all end at local_now or a
        # forward boundary, never mid-day-anchored).
        end = today + timedelta(days=1)
        start = end - timedelta(days=max(1, int(days or 30)))
        key = "rolling_days"

    start_utc = _to_utc_naive(start, zone)
    end_utc = _to_utc_naive(end, zone)
    if start_utc >= end_utc:
        raise ValueError("Analytical period start must be before end")
    return AnalyticalPeriod(key, start_utc, end_utc, timezone_name or "UTC")


def comparison_plan(
    primary: AnalyticalPeriod,
    mode: str,
) -> ComparisonPlan:
    """Create the second period without changing the primary period's scope."""
    zone = _workspace_zone(primary.timezone_name)
    local_start = _from_utc_naive(primary.start, zone)
    local_end = _from_utc_naive(primary.end, zone)
    if mode == "same_period_previous_year":
        prior_start = _shift_year(local_start, -1)
        prior_end = _shift_year(local_end, -1)
    elif mode == "previous_month":
        prior_start = _shift_month(local_start, -1)
        prior_end = _shift_month(local_end, -1)
    elif mode == "previous_quarter":
        prior_start = _shift_month(local_start, -3)
        prior_end = _shift_month(local_end, -3)
    elif mode == "previous_period":
        span = primary.end - primary.start
        return ComparisonPlan(
            mode=mode,
            primary=primary,
            comparison=AnalyticalPeriod(
                "previous_period",
                primary.start - span,
                primary.start,
                primary.timezone_name,
            ),
        )
    else:
        raise ValueError(f"Unsupported comparison mode: {mode}")
    return ComparisonPlan(
        mode=mode,
        primary=primary,
        comparison=AnalyticalPeriod(
            mode,
            _to_utc_naive(prior_start, zone),
            _to_utc_naive(prior_end, zone),
            primary.timezone_name,
        ),
    )


def comparison_coverage(primary_summary: dict, comparison_summary: dict) -> dict:
    """Report observed activity without claiming ingestion completeness."""
    primary_rows = int(primary_summary.get("request_count") or 0)
    comparison_rows = int(comparison_summary.get("request_count") or 0)
    def traffic_scope(summary: dict) -> str:
        live = int(summary.get("live_count") or 0)
        simulator = int(summary.get("simulation_count") or 0)
        if live and simulator:
            return "mixed"
        if live:
            return "live"
        if simulator:
            return "simulator"
        return "no_activity"

    primary_scope = traffic_scope(primary_summary)
    comparison_scope = traffic_scope(comparison_summary)
    if primary_rows and comparison_rows:
        status = "observed_both_periods"
        comparable = True
        limitation = "Activity exists in both periods; ingestion completeness is not yet independently verified."
    elif not primary_rows and not comparison_rows:
        status = "no_activity_both_periods"
        comparable = False
        limitation = "No matching activity was recorded in either period."
    else:
        status = "activity_missing_one_period"
        comparable = False
        limitation = "One period has no matching activity; collection coverage must be verified before interpreting the change."
    if primary_rows and comparison_rows and primary_scope != comparison_scope:
        status = "traffic_scope_mismatch"
        comparable = False
        limitation = (
            f"The primary period is {primary_scope}, while the comparison period is "
            f"{comparison_scope}; align live and simulator scope before interpreting the change."
        )
    return {
        "status": status,
        "comparable": comparable,
        "primary_request_count": primary_rows,
        "comparison_request_count": comparison_rows,
        "primary_traffic_scope": primary_scope,
        "comparison_traffic_scope": comparison_scope,
        "completeness": "not_independently_verified",
        "limitation": limitation,
    }
