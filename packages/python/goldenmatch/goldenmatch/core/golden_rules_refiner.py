"""Post-cluster golden-rules refinement (#golden-strategies, v1.18).

Runs between ``build_clusters`` and ``build_golden_records`` when
``GoldenRulesConfig.adaptive=True``. Reads cluster output + column
profiles and emits a refined ``GoldenRulesConfig`` with per-field
strategies informed by:

- Within-cluster value spread (high spread → ``confidence_majority``)
- Per-source completeness ranking (one source dominates → ``source_priority``)
- Date column inference (full timestamp coverage → ``most_recent``)
- ``col_type`` + ``avg_len`` (free-text + long → ``longest_value``)
- ``null_rate`` (mostly-NULL → ``first_non_null`` fast path)

Spec: ``docs/superpowers/specs/2026-05-22-intelligent-golden-rules-design.md``
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import polars as pl

    from goldenmatch.config.schemas import GoldenRulesConfig
    from goldenmatch.core.autoconfig import ColumnProfile

logger = logging.getLogger(__name__)


# Thresholds (env-overridable; defaults from spec).
# Spread = avg distinct values per cluster, for clusters with size >= 2.
HIGH_SPREAD_THRESHOLD = 2.0
FREE_TEXT_SPREAD_THRESHOLD = 1.5
FREE_TEXT_AVG_LEN_THRESHOLD = 20.0
HIGH_NULL_RATE_THRESHOLD = 0.5
SOURCE_DOMINANCE_THRESHOLD = 1.5  # top source must be > median * this

# Identity-column threshold for the unanimous_or_null override (#smarter-refiner).
IDENTITY_CARDINALITY_THRESHOLD = 0.9

# Sibling-timestamp coverage threshold: a candidate timestamp column
# must have non-null values for >80% of clusters to be considered a
# reliable date_column for OTHER fields.
SIBLING_TIMESTAMP_COVERAGE_THRESHOLD = 0.8

# Compliance-shaped column-name patterns. Fields matching these get
# `unanimous_or_null` regardless of other signals -- a chosen-by-
# heuristic value is worse than a missing value for these.
import re as _re

# Letter-boundary lookarounds so the patterns match `patient_ssn`
# (underscore is `\w`, which breaks `\b` between underscore + letter).
# Allows underscore, hyphen, digit, start/end of string as boundaries.
_LB = r"(?<![a-z])"
_LA = r"(?![a-z])"

_COMPLIANCE_NAME_PATTERNS = [
    _re.compile(p, _re.IGNORECASE) for p in [
        rf"{_LB}ssn{_LA}",
        rf"{_LB}sin{_LA}",                # Canadian SIN
        rf"{_LB}ein{_LA}",                # employer ID
        rf"{_LB}tax[_-]?id{_LA}",
        rf"{_LB}npi{_LA}",
        rf"{_LB}dea[_-]?number{_LA}",
        rf"{_LB}license[_-]?(no|num|number)?{_LA}",
        rf"{_LB}passport[_-]?(no|num|number)?{_LA}",
        rf"{_LB}drivers?[_-]?license{_LA}",
        rf"{_LB}(date[_-]?of[_-]?birth|dob|birthdate){_LA}",
        rf"{_LB}mrn{_LA}",                # medical record number
        rf"{_LB}hipaa[_-]?id{_LA}",
        rf"{_LB}patient[_-]?id{_LA}",
        rf"{_LB}medicaid[_-]?(no|num|number|id)?{_LA}",
        rf"{_LB}medicare[_-]?(no|num|number|id)?{_LA}",
        rf"{_LB}cusip{_LA}",
        rf"{_LB}lei{_LA}",                # Legal Entity Identifier
        rf"{_LB}isin{_LA}",               # International Securities ID
    ]
]

# Sibling-timestamp column-name patterns. Used to detect THE dataset's
# primary timestamp column for cross-field `most_recent` picks.
# Order matters: more-specific names first; first match wins.
_TIMESTAMP_NAME_PATTERNS = [
    _re.compile(p, _re.IGNORECASE) for p in [
        r"^updated[_-]?at$",
        r"^modified[_-]?at$",
        r"^last[_-]?modified$",
        r"^last[_-]?updated$",
        r"^update[_-]?(time|date|ts)$",
        r"^modify[_-]?(time|date|ts)$",
        r"^created[_-]?at$",
        r"^create[_-]?(time|date|ts)$",
        r"^date[_-]?modified$",
        r"^date[_-]?created$",
        r"^updated$",
        r"^created$",
        r"^timestamp$",
        r"^last[_-]?seen$",
    ]
]

# Mutable-shaped fields (col_type or name hint) that benefit from
# `most_recent` when a sibling timestamp is present. Things like name,
# DOB shouldn't change over time; address, phone, email do.
_MUTABLE_NAME_PATTERNS = [
    _re.compile(p, _re.IGNORECASE) for p in [
        r"\baddress\b",
        r"\bstreet\b",
        r"\bcity\b",
        r"\bstate\b",
        r"\bzip\b",
        r"\bpostal[_-]?code\b",
        r"\bphone\b",
        r"\btelephone\b",
        r"\bmobile\b",
        r"\bemail\b",
        r"\bemployer\b",
        r"\bjob[_-]?title\b",
        r"\boccupation\b",
        r"\bcompany\b",
        r"\bspecialty\b",
        r"\bdepartment\b",
        r"\bsalary\b",
    ]
]


def _is_compliance_name(field: str) -> bool:
    return any(p.search(field) for p in _COMPLIANCE_NAME_PATTERNS)


def _is_mutable_field_name(field: str, col_type: str) -> bool:
    if col_type in {"address", "phone", "email"}:
        return True
    return any(p.search(field) for p in _MUTABLE_NAME_PATTERNS)


def _pick_sibling_timestamp(
    column_profiles: list[ColumnProfile],
    date_column_coverage: dict[str, float],
) -> str | None:
    """Pick THE dataset's primary timestamp column for cross-field
    `most_recent` picks. Returns the column name or None.

    Selection order:
    1. Among columns whose col_type=='date': those matching the
       _TIMESTAMP_NAME_PATTERNS (more-specific first).
    2. Tiebreak by coverage descending (more present = more reliable).
    3. Skip columns with coverage < SIBLING_TIMESTAMP_COVERAGE_THRESHOLD.
    """
    date_cols = [p for p in column_profiles if p.col_type == "date"]
    if not date_cols:
        return None
    # Score by (pattern_rank, -coverage). Lower pattern_rank = better.
    scored: list[tuple[int, float, str]] = []
    for p in date_cols:
        coverage = date_column_coverage.get(p.name, 0.0)
        if coverage < SIBLING_TIMESTAMP_COVERAGE_THRESHOLD:
            continue
        # Find best matching pattern rank.
        best_rank = len(_TIMESTAMP_NAME_PATTERNS)
        for i, pat in enumerate(_TIMESTAMP_NAME_PATTERNS):
            if pat.search(p.name):
                best_rank = i
                break
        scored.append((best_rank, -coverage, p.name))
    if not scored:
        return None
    scored.sort()
    return scored[0][2]


@dataclass(frozen=True)
class RefinementSignals:
    """Per-field signals computed from clusters + column profiles.

    ``within_cluster_spread[field]`` is the average distinct value count
    across multi-member clusters, for that field. 1.0 = unanimous;
    > 2 = high disagreement.

    ``per_source_completeness[field][source]`` is the non-null rate of
    ``field`` in rows tagged with ``source``. Computed only when the
    ``__source__`` column is present.

    ``date_column_coverage[field]`` is the fraction of multi-member
    clusters where every member has a non-null date value in ``field``.
    Used to detect timestamp-shaped fields suitable for ``most_recent``.

    ``col_type`` / ``avg_len`` / ``null_rate`` are carried forward from
    pre-cluster ``ColumnProfile``.
    """

    within_cluster_spread: dict[str, float]
    per_source_completeness: dict[str, dict[str, float]]
    date_column_coverage: dict[str, float]
    col_type: dict[str, str]
    avg_len: dict[str, float]
    null_rate: dict[str, float]


def compute_refinement_signals(
    clusters: dict[int, dict],
    prepared_df: pl.DataFrame,
    column_profiles: list[ColumnProfile],
) -> RefinementSignals:
    """Compute per-field signals from clusters + prepared frame.

    Cheap aggregations (one polars groupby per signal, no per-cluster
    loops in Python). Skipped fields default to neutral values so the
    rule table sees explicit zeros instead of KeyErrors.
    """
    import polars as pl

    # Build a member-id -> cluster-id map by expanding cluster members.
    cluster_id_per_row: dict[int, int] = {}
    multi_cluster_member_ids: list[int] = []
    for cid, info in clusters.items():
        members = info.get("members") or []
        if len(members) < 2:
            continue
        for m in members:
            cluster_id_per_row[m] = cid
            multi_cluster_member_ids.append(m)

    if not multi_cluster_member_ids:
        # No multi-member clusters -- every signal is neutral.
        empty_str: dict[str, float] = {}
        empty_src: dict[str, dict[str, float]] = {}
        return RefinementSignals(
            within_cluster_spread=empty_str,
            per_source_completeness=empty_src,
            date_column_coverage=empty_str,
            col_type={p.name: p.col_type for p in column_profiles},
            avg_len={p.name: float(p.avg_len) for p in column_profiles},
            null_rate={p.name: float(p.null_rate) for p in column_profiles},
        )

    # Filter prepared_df to multi-cluster members (eager; the set is
    # small relative to N) + attach cluster_id.
    if "__row_id__" in prepared_df.columns:
        ids_col = "__row_id__"
    else:
        prepared_df = prepared_df.with_row_index("__row_id__")
        ids_col = "__row_id__"

    cluster_id_series = pl.DataFrame({
        ids_col: list(cluster_id_per_row.keys()),
        "__cluster_id__": list(cluster_id_per_row.values()),
    })
    multi_df = prepared_df.join(cluster_id_series, on=ids_col, how="inner")

    user_cols = [
        c for c in multi_df.columns
        if not c.startswith("__") and c not in {ids_col, "__cluster_id__"}
    ]

    # Within-cluster spread: avg distinct values per cluster per field.
    within_cluster_spread: dict[str, float] = {}
    for col in user_cols:
        # n_unique per cluster, then mean across clusters.
        agg = (
            multi_df.lazy()
            .group_by("__cluster_id__")
            .agg(pl.col(col).n_unique().alias("__distinct__"))
            .select(pl.col("__distinct__").mean().alias("mean_distinct"))
            .collect()
        )
        mean_distinct = agg["mean_distinct"][0] if agg.height > 0 else 1.0
        within_cluster_spread[col] = float(mean_distinct or 1.0)

    # Per-source completeness: non-null rate per (source, field).
    per_source_completeness: dict[str, dict[str, float]] = {}
    if "__source__" in prepared_df.columns:
        for col in user_cols:
            per_source: dict[str, float] = {}
            agg = (
                prepared_df.lazy()
                .group_by("__source__")
                .agg(
                    pl.col(col).is_not_null().mean().alias("non_null_rate"),
                )
                .collect()
            )
            for row in agg.iter_rows(named=True):
                per_source[str(row["__source__"])] = float(row["non_null_rate"] or 0.0)
            if per_source:
                per_source_completeness[col] = per_source

    # Date-column coverage: fraction of multi-member clusters where
    # every member has a non-null value in this field AND the field's
    # dtype is a date/datetime/string-castable-to-date.
    date_column_coverage: dict[str, float] = {}
    profile_by_name = {p.name: p for p in column_profiles}
    for col in user_cols:
        p = profile_by_name.get(col)
        if p is None or p.col_type not in ("date",):
            continue
        # Count clusters where the field is non-null for every member.
        agg = (
            multi_df.lazy()
            .group_by("__cluster_id__")
            .agg(pl.col(col).is_not_null().all().alias("__all_present__"))
            .select(pl.col("__all_present__").mean().alias("coverage"))
            .collect()
        )
        if agg.height > 0:
            date_column_coverage[col] = float(agg["coverage"][0] or 0.0)

    return RefinementSignals(
        within_cluster_spread=within_cluster_spread,
        per_source_completeness=per_source_completeness,
        date_column_coverage=date_column_coverage,
        col_type={p.name: p.col_type for p in column_profiles},
        avg_len={p.name: float(p.avg_len) for p in column_profiles},
        null_rate={p.name: float(p.null_rate) for p in column_profiles},
    )


def _pick_strategy_for_field(
    field: str,
    signals: RefinementSignals,
    sibling_timestamp: str | None = None,
    cardinality_ratio: float = 0.0,
) -> tuple[str, dict] | None:
    """Apply the rule table to one field; return (strategy_name, kwargs)
    or None to fall through to the base default.

    Rule order (first match wins) per
    ``docs/superpowers/specs/2026-05-22-intelligent-golden-rules-design.md``.

    v1.18 ``smarter-refiner`` additions:
    - PRE-rules (safety): compliance column names + identity columns
      get ``unanimous_or_null`` before any spread/source signals fire.
    - Sibling-timestamp rule: mutable-shaped fields with a dataset-
      level primary timestamp get ``most_recent`` via that timestamp.
    """
    col_type = signals.col_type.get(field, "unknown")
    avg_len = signals.avg_len.get(field, 0.0)
    null_rate = signals.null_rate.get(field, 0.0)
    spread = signals.within_cluster_spread.get(field, 1.0)
    date_cov = signals.date_column_coverage.get(field, 0.0)

    # PRE-RULE 1: compliance-shaped column names (ssn, npi, license,
    # dob, etc) ALWAYS get unanimous_or_null. A chosen-by-heuristic
    # value is worse than a missing value for compliance-grade fields.
    if _is_compliance_name(field):
        return "unanimous_or_null", {}

    # PRE-RULE 2: high-cardinality identity columns also get
    # unanimous_or_null. Identifier-shaped values where most rows have
    # a unique value -> trust unanimity over heuristics.
    if (
        col_type == "identifier"
        and cardinality_ratio > IDENTITY_CARDINALITY_THRESHOLD
    ):
        return "unanimous_or_null", {}

    # Rule 1: date column with full timestamp coverage -> most_recent
    # on itself.
    if col_type == "date" and date_cov > 0.5:
        return "most_recent", {"date_column": field}

    # Rule 1b (NEW): mutable-shaped field + dataset has a high-coverage
    # sibling timestamp column -> most_recent on the sibling. Catches
    # the common case of `address`, `phone`, `email` columns that
    # change over time + a dataset-wide `updated_at` / `modified_at`.
    if (
        sibling_timestamp is not None
        and sibling_timestamp != field
        and _is_mutable_field_name(field, col_type)
    ):
        return "most_recent", {"date_column": sibling_timestamp}

    # Rule 2: source_priority when one source clearly dominates.
    per_source = signals.per_source_completeness.get(field)
    if per_source and len(per_source) >= 2:
        sorted_sources = sorted(
            per_source.items(), key=lambda kv: kv[1], reverse=True,
        )
        _top_source, top_rate = sorted_sources[0]
        # Median of all sources.
        rates = sorted([r for _, r in sorted_sources])
        median = rates[len(rates) // 2]
        if median > 0 and top_rate > median * SOURCE_DOMINANCE_THRESHOLD:
            ordered = [s for s, _ in sorted_sources]
            return "source_priority", {"source_priority": ordered}

    # Rule 3: long free-text field with disagreement -> longest_value.
    if (
        col_type in ("string", "address", "description")
        and avg_len > FREE_TEXT_AVG_LEN_THRESHOLD
        and spread > FREE_TEXT_SPREAD_THRESHOLD
    ):
        return "longest_value", {}

    # Rule 4: mostly-NULL field -> first_non_null fast path.
    if null_rate > HIGH_NULL_RATE_THRESHOLD:
        return "first_non_null", {}

    # Rule 5: high within-cluster disagreement -> confidence_majority.
    if spread > HIGH_SPREAD_THRESHOLD:
        return "confidence_majority", {}

    # Otherwise: defer to base rules' default.
    return None


def refine_golden_rules(
    base_rules: GoldenRulesConfig,
    clusters: dict[int, dict],
    prepared_df: pl.DataFrame,
    column_profiles: list[ColumnProfile],
) -> GoldenRulesConfig:
    """Refine ``base_rules`` based on cluster + column signals.

    Returns a NEW ``GoldenRulesConfig`` with ``field_rules`` populated.
    Does NOT mutate ``base_rules``. When ``base_rules.adaptive`` is
    False (default), returns ``base_rules`` unchanged.
    """
    from goldenmatch.config.schemas import GoldenFieldRule

    if not base_rules.adaptive:
        return base_rules

    signals = compute_refinement_signals(clusters, prepared_df, column_profiles)

    # #smarter-refiner: detect THE dataset's primary timestamp column
    # ONCE; reused as date_column for any mutable-shaped field.
    sibling_timestamp = _pick_sibling_timestamp(
        column_profiles, signals.date_column_coverage,
    )
    if sibling_timestamp:
        logger.info(
            "Refiner detected sibling timestamp column: %r "
            "(used as date_column for mutable fields)",
            sibling_timestamp,
        )

    # Build a per-field cardinality_ratio lookup for the identity-column
    # rule (which can't use within_cluster_spread because high-cardinality
    # identifiers may not appear in multi-member clusters).
    cardinality_by_field: dict[str, float] = {
        p.name: float(p.cardinality_ratio) for p in column_profiles
    }

    # Consider every column the refiner has signals for AND every column
    # in the profiles list. Pre-rules (compliance / identity) need to
    # fire even on fields that don't appear in multi-member clusters.
    fields_to_consider: set[str] = set(signals.within_cluster_spread.keys())
    fields_to_consider |= {p.name for p in column_profiles}

    new_field_rules: dict[str, GoldenFieldRule] = dict(base_rules.field_rules)
    for field in fields_to_consider:
        if field in new_field_rules:
            # Caller-provided rule wins; don't override.
            continue
        result = _pick_strategy_for_field(
            field, signals,
            sibling_timestamp=sibling_timestamp,
            cardinality_ratio=cardinality_by_field.get(field, 0.0),
        )
        if result is None:
            continue
        strategy, kwargs = result
        try:
            new_field_rules[field] = GoldenFieldRule(strategy=strategy, **kwargs)
            logger.info(
                "Refined golden rule: field=%r -> strategy=%s %s",
                field, strategy, kwargs or "",
            )
        except Exception as exc:  # pragma: no cover -- defensive
            logger.warning(
                "Refiner skipped field=%r strategy=%s: %s",
                field, strategy, exc,
            )

    refined = base_rules.model_copy(update={"field_rules": new_field_rules})
    return refined
