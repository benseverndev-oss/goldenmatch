"""Business-shaped predefined plugins.

system_of_record, lifecycle_stage, freshness_with_max_age. Each
satisfies ``GoldenStrategyPlugin`` from ``goldenmatch.plugins.base``.

Spec: ``docs/superpowers/specs/2026-05-22-predefined-merge-plugins-design.md``
"""
from __future__ import annotations

import math
import re
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


class EnumCanonicalStrategy:
    """Map known aliases to a canonical enum value, then pick the mode.

    `rule_kwargs.alias_map` maps any alias (case-insensitive,
    trimmed) -> canonical value. For example::

       alias_map:
         "USA": "US"
         "United States": "US"
         "U.S.": "US"
         "U.S.A.": "US"

    Lookup is case-insensitive on KEYS but the VALUE is returned
    verbatim (preserves intentional casing of the canonical form).

    Values not in the alias_map pass through unchanged (no
    modification). Then the strategy picks the mode of the resulting
    set.

    Confidence: `count(winning_value) / count(non_null)`.
    """

    name = "enum_canonical"

    def merge(
        self,
        values: list,
        *,
        rule_kwargs: dict | None = None,
        **_: Any,
    ) -> Any:
        from collections import Counter

        non_null = [(i, v) for i, v in enumerate(values) if v is not None]
        if not non_null:
            return (None, 0.0)
        raw_map: dict = (rule_kwargs or {}).get("alias_map") or {}
        # Build a case-insensitive lookup -- key.lower() -> canonical.
        alias_map = {str(k).strip().lower(): canonical for k, canonical in raw_map.items()}

        def _canonicalize(v: Any) -> Any:
            key = str(v).strip().lower()
            return alias_map.get(key, v)

        normalized = [(i, _canonicalize(v)) for i, v in non_null]
        counts = Counter(c for _, c in normalized)
        winner, count = counts.most_common(1)[0]
        first_idx = next(i for i, c in normalized if c == winner)
        conf = count / len(non_null)
        return (winner, conf, first_idx)


class RegexValidatedStrategy:
    """Only accept values matching ``rule_kwargs.pattern``.

    Useful when source data is dirty and you want to filter at the
    consolidation step (e.g. only accept email-shaped values into the
    `email` field, only accept SSN-shaped values into `ssn`).

    Behavior:
    - Filter values to only those matching the regex (`re.fullmatch`).
    - If at least one matches: pick the most-common one (mode).
    - If none match: fall back per ``rule_kwargs.fallback``:
        - "first_non_null" (default): pick the first non-null
          unmatched value with low confidence (0.3)
        - "null": emit (None, 0.0)

    Missing ``rule_kwargs.pattern`` -> behaves like `first_non_null`.
    """

    name = "regex_validated"

    def merge(
        self,
        values: list,
        *,
        rule_kwargs: dict | None = None,
        **_: Any,
    ) -> Any:
        from collections import Counter

        non_null = [(i, v) for i, v in enumerate(values) if v is not None]
        if not non_null:
            return (None, 0.0)
        pattern = (rule_kwargs or {}).get("pattern")
        fallback = (rule_kwargs or {}).get("fallback", "first_non_null")
        if not pattern:
            # No pattern configured -> first non-null.
            return (non_null[0][1], 0.5, non_null[0][0])
        try:
            compiled = re.compile(pattern)
        except re.error:
            return (non_null[0][1], 0.3, non_null[0][0])
        matched = [(i, v) for i, v in non_null if compiled.fullmatch(str(v))]
        if matched:
            counts = Counter(v for _, v in matched)
            winner, count = counts.most_common(1)[0]
            first_idx = next(i for i, v in matched if v == winner)
            conf = count / len(matched)
            return (winner, conf, first_idx)
        # No matches.
        if fallback == "null":
            return (None, 0.0)
        return (non_null[0][1], 0.3, non_null[0][0])


class WeightedByRecencyStrategy:
    """Pick the value with the highest exponential-decayed recency weight.

    Each value's weight = exp(-age_days / half_life_days). The value
    with the maximum weight is returned. When multiple values share
    the same date, the first-index wins.

    `rule_kwargs.half_life_days` (default 30) controls decay rate:
    after `half_life_days` days, weight halves. A value 6 months
    older has weight ~0.001 of a fresh value (effectively ignored).

    Requires `dates` kwarg. Values without dates are dropped.
    Confidence: 1.0 when unique winner; 0.7 on date ties.

    Compare to `most_recent` (sharp cutoff at newest) and
    `freshness_with_max_age` (binary in/out). This strategy is a soft
    "newer-but-not-only-newest" picker.
    """

    name = "weighted_by_recency"

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
        half_life_days = float((rule_kwargs or {}).get("half_life_days", 30))
        if half_life_days <= 0:
            half_life_days = 30.0
        now = datetime.now(tz=UTC)
        scored: list[tuple[int, datetime, float, Any]] = []
        for i, (d, v) in enumerate(zip(dates, values)):
            if v is None:
                continue
            parsed = _parse_date(d)
            if parsed is None:
                continue
            age_days = (now - parsed).total_seconds() / 86400.0
            weight = math.exp(-age_days / half_life_days)
            scored.append((i, parsed, weight, v))
        if not scored:
            return (None, 0.0)
        scored.sort(key=lambda x: x[2], reverse=True)
        top_weight = scored[0][2]
        tied = [(i, v) for i, _, w, v in scored if w == top_weight]
        conf = 1.0 if len(tied) == 1 else 0.7
        return (tied[0][1], conf, tied[0][0])
