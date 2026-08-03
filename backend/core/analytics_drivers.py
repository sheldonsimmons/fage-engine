"""Deterministic change decomposition for executive analytics."""

from typing import Optional


def _value(row: dict, metric: str) -> float:
    if metric == "avg_cost_per_request":
        requests = float(row.get("request_count") or 0)
        return float(row.get("spend_usd") or 0) / requests if requests else 0.0
    return float(row.get(metric) or 0)


def change_decomposition(current: dict, prior: dict, metric: str) -> dict:
    """Split a total change into request-volume and per-request intensity effects."""
    current_requests = float(current.get("request_count") or 0)
    prior_requests = float(prior.get("request_count") or 0)
    current_value = _value(current, metric)
    prior_value = _value(prior, metric)
    current_intensity = current_value / current_requests if current_requests else 0.0
    prior_intensity = prior_value / prior_requests if prior_requests else 0.0
    volume_effect = (current_requests - prior_requests) * (
        current_intensity + prior_intensity
    ) / 2
    intensity_effect = (current_intensity - prior_intensity) * (
        current_requests + prior_requests
    ) / 2
    change = current_value - prior_value
    percent_change = change / prior_value * 100 if prior_value else None
    return {
        "metric": metric,
        "current_value": current_value,
        "comparison_value": prior_value,
        "absolute_change": change,
        "percent_change": percent_change,
        "current_request_count": int(current_requests),
        "comparison_request_count": int(prior_requests),
        "current_per_request": current_intensity,
        "comparison_per_request": prior_intensity,
        "request_volume_effect": volume_effect,
        "per_request_effect": intensity_effect,
        "method": "two-factor Shapley decomposition",
    }


def dimension_contributors(
    current_rows: list,
    prior_rows: list,
    metric: str,
    dimension: str,
    total_change: float,
    limit: int = 5,
) -> list[dict]:
    """Rank measured entity deltas using the same metric in both periods."""
    merged = {}
    for period_name, rows in (("current", current_rows or []), ("comparison", prior_rows or [])):
        for row in rows:
            identity = str(row.get("id") if row.get("id") is not None else row.get("label") or "Unknown")
            item = merged.setdefault(identity, {
                "id": row.get("id"),
                "label": row.get("label") or "Unknown",
                "dimension": dimension,
                "current_value": 0.0,
                "comparison_value": 0.0,
            })
            item[f"{period_name}_value"] = _value(row, metric)
    contributors = []
    for item in merged.values():
        delta = item["current_value"] - item["comparison_value"]
        if delta == 0:
            continue
        item["absolute_change"] = delta
        item["net_change_contribution_pct"] = (
            delta / total_change * 100 if total_change else None
        )
        contributors.append(item)
    contributors.sort(key=lambda item: (-abs(item["absolute_change"]), item["label"]))
    return contributors[:max(1, min(int(limit), 20))]


def top_contributor(contributors: list[dict]) -> Optional[dict]:
    return contributors[0] if contributors else None
