"""Business-shaped predefined plugins.

system_of_record, lifecycle_stage, freshness_with_max_age. Each
satisfies ``GoldenStrategyPlugin`` from ``goldenmatch.plugins.base``.

Spec: ``docs/superpowers/specs/2026-05-22-predefined-merge-plugins-design.md``
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

# Default order for `lifecycle_stage` (low -> high). Lowercased for
# case-insensitive matching.
DEFAULT_LIFECYCLE_ORDER = [
    "subscriber",
    "lead",
    "marketing_qualified_lead",
    "mql",
    "sales_qualified_lead",
    "sql",
    "opportunity",
    "customer",
    "evangelist",
]


class SystemOfRecordStrategy:
    """Pick value from authoritative source per `rule_kwargs.source_priority`.

    Semantically equivalent to the built-in `source_priority` strategy
    but with explicit "system of record" naming so the YAML config
    carries operator intent:

    .. code-block:: yaml

       golden_rules:
         field_rules:
           account_status:
             strategy: "custom:system_of_record"
             source_priority: ["salesforce", "hubspot", "netsuite"]

    Falls back to first non-null when no priority source has a value.
    """

    name = "system_of_record"

    def merge(
        self,
        values: list,
        *,
        sources: list[str] | None = None,
        rule_kwargs: dict | None = None,
        **_: Any,
    ) -> Any:
        non_null = [(i, v) for i, v in enumerate(values) if v is not None]
        if not non_null:
            return (None, 0.0)
        priority = (rule_kwargs or {}).get("source_priority") or []
        if sources is None or not priority:
            # No priority config -> first non-null with low confidence
            # (the YAML intent says "system of record" but no SoR is
            # configured, so this is a fallback).
            return (non_null[0][1], 0.5, non_null[0][0])
        for src in priority:
            for i, (s, v) in enumerate(zip(sources, values)):
                if s == src and v is not None:
                    # Confidence decays with priority rank
                    rank = priority.index(src)
                    conf = max(0.5, 1.0 - rank * 0.1)
                    return (v, conf, i)
        # No source in priority list had a value -> first non-null.
        return (non_null[0][1], 0.4, non_null[0][0])


class LifecycleStageStrategy:
    """Pick the most-advanced lifecycle stage from the cluster.

    Default order (low -> high): subscriber, lead, mql, sql,
    opportunity, customer, evangelist. Override via
    `rule_kwargs.lifecycle_order` (list of stage names; comparison
    is lowercase-insensitive).

    Unknown stages are ranked below the lowest known stage
    (effectively ignored when ANY known stage is present).

    Confidence: 1.0 when a unique maximum stage exists; 0.7 on ties.
    """

    name = "lifecycle_stage"

    def merge(
        self,
        values: list,
        *,
        rule_kwargs: dict | None = None,
        **_: Any,
    ) -> Any:
        non_null = [(i, v) for i, v in enumerate(values) if v is not None]
        if not non_null:
            return (None, 0.0)
        order = (rule_kwargs or {}).get("lifecycle_order") or DEFAULT_LIFECYCLE_ORDER
        order_map = {str(stage).strip().lower(): idx for idx, stage in enumerate(order)}
        # Unknown values: rank = -1 (below all known stages).
        ranked = [
            (order_map.get(str(v).strip().lower(), -1), i, v)
            for i, v in non_null
        ]
        max_rank = max(r for r, _, _ in ranked)
        tied = [(i, v) for r, i, v in ranked if r == max_rank]
        conf = 1.0 if len(tied) == 1 else 0.7
        return (tied[0][1], conf, tied[0][0])


def _parse_date(value: Any) -> datetime | None:
    """Best-effort datetime coercion. Handles common ISO + epoch."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(float(value), tz=UTC)
        except (OSError, OverflowError, ValueError):
            return None
    s = str(value).strip()
    if not s:
        return None
    # Try ISO 8601 variants. Python 3.11+ handles most.
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except ValueError:
        pass
    # Common fallback patterns.
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%d/%m/%Y", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


class FreshnessWithMaxAgeStrategy:
    """Like `most_recent` but emits NULL if no value is fresh enough.

    `rule_kwargs.max_age_days` (default 365) defines the cutoff.
    Values whose accompanying date is older than cutoff are dropped.
    If no values remain after filtering, emits (None, 0.0).

    Use when compliance / data-quality requires "no stale data" --
    e.g. KYC address must be < 90 days old, else missing-data
    follow-up triggers.

    Requires `dates` kwarg (parallel to values). Without dates,
    behaves as if every value is stale (emits None).
    """

    name = "freshness_with_max_age"

    def merge(
        self,
        values: list,
        *,
        dates: list | None = None,
        rule_kwargs: dict | None = None,
        **_: Any,
    ) -> Any:
        if dates is None:
            return (None, 0.0)
        max_age_days = float((rule_kwargs or {}).get("max_age_days", 365))
        now = datetime.now(tz=UTC)
        cutoff = now - timedelta(days=max_age_days)
        candidates: list[tuple[int, datetime, Any]] = []
        for i, (d, v) in enumerate(zip(dates, values)):
            if v is None:
                continue
            parsed = _parse_date(d)
            if parsed is None or parsed < cutoff:
                continue
            candidates.append((i, parsed, v))
        if not candidates:
            return (None, 0.0)
        # Sort newest-first.
        candidates.sort(key=lambda x: x[1], reverse=True)
        top_dt = candidates[0][1]
        tied = [(i, v) for i, dt, v in candidates if dt == top_dt]
        conf = 1.0 if len(tied) == 1 else 0.7
        return (tied[0][1], conf, tied[0][0])
