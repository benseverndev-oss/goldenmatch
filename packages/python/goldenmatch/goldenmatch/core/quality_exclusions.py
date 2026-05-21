"""Auto-config column-exclusion detectors (GoldenCheck preflight).

Identifies columns that are *statistically attractive but
counter-productive* for matching:

  1. Audit timestamp columns (created_at, updated_at, ...)
  2. Foreign-system IDs (external_id, source_pk, etl_batch, ...)
  3. Sentinel/placeholder values (0000000000, noemail@example.com, "Unknown", ...)
  4. Soft-delete / lifecycle flags (is_active, status, deleted_at, ...)
  5. Free-text notes/comments (description, notes, memo, ...)
  6. System-generated hashes (record_hash, dedup_token, checksum, ...)

Auto-config consumes the exclusion list at the top of
``auto_configure_df`` and filters those columns out of the candidate
pool BEFORE identity/cardinality scoring runs. Excluded columns are
invisible to all downstream rules (compute_column_priors,
promote_negative_evidence, the rule chain).

Each detector is a pure function; the orchestrator
``detect_autoconfig_exclusions`` iterates them across columns and
returns a list of ``ExcludedColumn``. Cost is O(sample_size=1000) per
column, not O(rows), so 1000-column wide tables are still cheap.

Spec: docs/superpowers/specs/2026-05-21-goldencheck-autoconfig-exclusions-design.md
Issue: #404
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

import polars as pl

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExcludedColumn:
    """A column GoldenCheck recommends auto-config skip.

    Frozen so the list can be hashed, cached, and surfaced through
    immutable channels (postflight, MCP responses) without defensive
    copying.

    ``detector`` is the short name of the detector that fired
    (audit_column, sentinel_values, etc). ``reason`` is the
    user-facing string surfaced in logs + postflight. ``evidence`` is
    detector-specific structured context (e.g. sentinel hit counts).
    """

    column: str
    detector: str
    reason: str
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ColumnProfile:
    """Cheap stats used by detectors. Computed once per column by the
    orchestrator so detectors don't re-scan.

    Detectors should NOT compute additional statistics from the series
    -- if a detector needs something not in ColumnProfile, add it here
    and compute it once. The whole point of the profile struct is to
    keep per-column cost bounded.
    """

    cardinality_ratio: float  # distinct / non-null
    null_rate: float          # null / total
    distinct_count: int
    dtype: str                # pl.dtype repr
    mean_string_length: float | None  # None for non-string columns


# ---------------------------------------------------------------------------
# Sentinel sets (detector 3)
# ---------------------------------------------------------------------------

# Lowercase strings the sentinel detector treats as known-bad values
# for any column. Generic placeholders that show up across schemas.
GENERIC_STRING_SENTINELS: frozenset[str] = frozenset({
    "unknown", "n/a", "na", "none", "null", "tbd",
    "test", "test_data", "placeholder", "fixme",
    "not provided", "not available", "no data",
    "?", "??", "-", "--", "",
})

# Phone-shaped sentinels (digit strings with low entropy).
PHONE_SENTINELS: frozenset[str] = frozenset({
    "0000000000", "1111111111", "9999999999", "1234567890",
    "5555555555", "0000000", "1234567", "555-555-5555",
    "(000) 000-0000", "(555) 555-5555",
})

# Substrings that indicate a placeholder email. ONLY unambiguous
# no-reply / null / test patterns -- NOT @example.com or @example.org,
# which are RFC-reserved test domains widely used in legitimate
# fixtures and demos. False-positive rate on example.* domains was
# unacceptable (NCVR / DQbench / negative-evidence fixtures all use
# @example.com as the legitimate domain).
EMAIL_SENTINEL_SUBSTRINGS: tuple[str, ...] = (
    "noreply@", "noemail@", "no-reply@", "donotreply@", "do-not-reply@",
    "null@", "none@", "n/a@",
    "test@", "fake@", "@test.com",
)

# Zip-shaped sentinels.
ZIP_SENTINELS: frozenset[str] = frozenset({
    "00000", "99999", "12345", "00000-0000", "99999-9999",
})


# ---------------------------------------------------------------------------
# Name-pattern regexes (compiled once)
# ---------------------------------------------------------------------------

_AUDIT_NAME_RE = re.compile(
    r"^(created|updated|modified|inserted|deleted|last_seen|first_seen|sync)_(at|on|date|time|ts|timestamp)$"
    r"|^.+_(at|on|timestamp|ts)$"
    r"|^(created|updated|modified|inserted|deleted)$",
    re.IGNORECASE,
)

_FOREIGN_ID_NAME_RE = re.compile(
    r"external_id|legacy_id|source_id|etl_batch|migration_id|.+_(pk|uuid|guid)$|^(pk|uuid|guid)$",
    re.IGNORECASE,
)

_LIFECYCLE_NAME_RE = re.compile(
    r"^(is_|has_|can_|should_)"
    r"|^(deleted|active|status|state|enabled|disabled|published|archived)(_.+)?$",
    re.IGNORECASE,
)

_FREE_TEXT_NAME_RE = re.compile(
    r"^(.*_)?(notes?|comments?|description|remarks|memo|details?|free_text|narrative)(_.*)?$",
    re.IGNORECASE,
)

_HASH_NAME_RE = re.compile(
    r"hash|digest|checksum|signature|dedup_token|fingerprint|^(md5|sha1|sha256)$",
    re.IGNORECASE,
)

_HEX_VALUE_RE = re.compile(r"^[0-9a-fA-F]{16,128}$")
_BASE64_VALUE_RE = re.compile(r"^[A-Za-z0-9+/]{16,}={0,2}$")


# ---------------------------------------------------------------------------
# Detectors
#
# Each detector takes (column_name, sampled_values, profile) and returns
# ``ExcludedColumn | None``. The sampled_values list is a small subset of
# non-null values (default cap 1000); the profile carries cheap stats
# computed once by the orchestrator.
# ---------------------------------------------------------------------------


def detect_audit_column(
    column_name: str,
    sampled_values: list,
    profile: ColumnProfile,
) -> ExcludedColumn | None:
    """Audit timestamp columns. Match on name pattern + datetime-like
    dtype + high cardinality."""
    if not _AUDIT_NAME_RE.match(column_name):
        return None
    is_temporal = (
        "datetime" in profile.dtype.lower()
        or "date" in profile.dtype.lower()
        or "time" in profile.dtype.lower()
    )
    # Numeric epoch shape: int dtype + huge values, but we accept the
    # name-pattern + cardinality > 0.5 signal as enough since temporal
    # name + high uniqueness is almost always an audit column.
    if not is_temporal and profile.cardinality_ratio < 0.5:
        return None
    return ExcludedColumn(
        column=column_name,
        detector="audit_column",
        reason="audit timestamp column (high cardinality, no identity signal)",
        evidence={
            "dtype": profile.dtype,
            "cardinality_ratio": round(profile.cardinality_ratio, 3),
        },
    )


def detect_foreign_system_id(
    column_name: str,
    sampled_values: list,
    profile: ColumnProfile,
) -> ExcludedColumn | None:
    """Foreign-system IDs. Match on name pattern + cardinality > 0.95."""
    name_match = _FOREIGN_ID_NAME_RE.search(column_name)
    if not name_match:
        return None
    if profile.cardinality_ratio < 0.95:
        return None
    return ExcludedColumn(
        column=column_name,
        detector="foreign_system_id",
        reason=(
            "foreign-system ID (high cardinality, per-source -- "
            "cross-source dedupe treats as distinct)"
        ),
        evidence={
            "cardinality_ratio": round(profile.cardinality_ratio, 3),
            "name_match": name_match.group(0),
        },
    )


def detect_sentinel_values(
    column_name: str,
    sampled_values: list,
    profile: ColumnProfile,
    *,
    threshold: float = 0.10,
) -> ExcludedColumn | None:
    """Sentinel / placeholder values. THE #1 real-world poison.

    Sample non-null values, count matches against the appropriate
    sentinel set (phone-shaped, email-shaped, zip-shaped, or generic
    string). If > threshold (default 10%) match, exclude.
    """
    if not sampled_values:
        return None
    if profile.mean_string_length is None and "Utf" not in profile.dtype:
        # Sentinels only meaningful on string columns.
        return None

    hits: dict[str, int] = {}
    total = 0
    for v in sampled_values:
        if v is None:
            continue
        sv = str(v).strip()
        if not sv:
            continue
        total += 1
        sv_lower = sv.lower()

        # Generic string sentinels (case-insensitive exact).
        if sv_lower in GENERIC_STRING_SENTINELS:
            hits[sv] = hits.get(sv, 0) + 1
            continue
        # Phone-shaped (only-digits-with-separators).
        digits_only = re.sub(r"[\s\-().+]", "", sv)
        if digits_only and digits_only in PHONE_SENTINELS:
            hits[sv] = hits.get(sv, 0) + 1
            continue
        if sv in PHONE_SENTINELS:
            hits[sv] = hits.get(sv, 0) + 1
            continue
        # Email-shaped substring.
        if "@" in sv_lower and any(p in sv_lower for p in EMAIL_SENTINEL_SUBSTRINGS):
            hits[sv] = hits.get(sv, 0) + 1
            continue
        # Zip-shaped.
        if sv in ZIP_SENTINELS:
            hits[sv] = hits.get(sv, 0) + 1
            continue

    if total == 0:
        return None
    sentinel_count = sum(hits.values())
    sentinel_rate = sentinel_count / total
    if sentinel_rate < threshold:
        return None

    top_sentinels = sorted(hits.items(), key=lambda kv: -kv[1])[:5]
    return ExcludedColumn(
        column=column_name,
        detector="sentinel_values",
        reason=(
            f"sentinel/placeholder values in {int(sentinel_rate * 100)}% "
            "of records -- matching collapses these into spurious "
            "mega-clusters"
        ),
        evidence={
            "sentinel_rate": round(sentinel_rate, 3),
            "top_sentinels": top_sentinels,
            "sample_size": total,
        },
    )


def detect_soft_delete_flag(
    column_name: str,
    sampled_values: list,
    profile: ColumnProfile,
) -> ExcludedColumn | None:
    """Soft-delete / lifecycle flags. Name pattern + low cardinality
    + small distinct count.
    """
    if not _LIFECYCLE_NAME_RE.match(column_name):
        return None
    # Threshold: distinct values <= 10 AND cardinality < 10% are both
    # generous gates -- a real lifecycle column ('active'/'inactive',
    # 'true'/'false', 'pending'/'active'/'deleted') has 2-5 distinct
    # values regardless of dataset size. Anything more distinct under
    # this name prefix is a custom column we shouldn't second-guess.
    if profile.distinct_count > 10:
        return None
    if profile.cardinality_ratio >= 0.10:
        return None
    distinct_sample = list({str(v) for v in sampled_values if v is not None})[:5]
    return ExcludedColumn(
        column=column_name,
        detector="soft_delete_flag",
        reason="lifecycle/flag column (low cardinality, no identity signal)",
        evidence={
            "distinct_count": profile.distinct_count,
            "distinct_sample": distinct_sample,
            "cardinality_ratio": round(profile.cardinality_ratio, 4),
        },
    )


def detect_free_text_notes(
    column_name: str,
    sampled_values: list,
    profile: ColumnProfile,
) -> ExcludedColumn | None:
    """Free-text notes / comments. Name pattern + mean length > 50."""
    if not _FREE_TEXT_NAME_RE.match(column_name):
        return None
    if profile.mean_string_length is None or profile.mean_string_length <= 50:
        return None
    return ExcludedColumn(
        column=column_name,
        detector="free_text_notes",
        reason=(
            "free-text notes column (high mean length, routes to fuzzy "
            "text scoring with no precision signal)"
        ),
        evidence={
            "mean_length": round(profile.mean_string_length, 1),
        },
    )


def detect_system_hash(
    column_name: str,
    sampled_values: list,
    profile: ColumnProfile,
) -> ExcludedColumn | None:
    """System-generated hash. Name pattern + cardinality > 0.99 + values
    look like hex or base64.
    """
    if not _HASH_NAME_RE.search(column_name):
        return None
    if profile.cardinality_ratio < 0.99:
        return None
    if not sampled_values:
        return None
    # Check the first ~20 values for hex/base64 shape; cheap.
    checks = [str(v) for v in sampled_values[:20] if v is not None]
    if not checks:
        return None
    hex_hits = sum(1 for v in checks if _HEX_VALUE_RE.match(v))
    b64_hits = sum(1 for v in checks if _BASE64_VALUE_RE.match(v))
    # Require 75%+ of sampled values to match a hash shape.
    if hex_hits / len(checks) < 0.75 and b64_hits / len(checks) < 0.75:
        return None
    shape = "hex" if hex_hits >= b64_hits else "base64"
    return ExcludedColumn(
        column=column_name,
        detector="system_hash",
        reason="system-generated hash/digest (unique but encodes no identity)",
        evidence={
            "cardinality_ratio": round(profile.cardinality_ratio, 3),
            "value_shape": shape,
        },
    )


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


# Detector order: specific patterns first, general patterns later.
# A column matching multiple detectors keeps the first hit (the more
# specific reason wins). Order matters for ambiguous columns like
# ``created_at_external_id`` -- audit beats foreign-id here.
_DETECTORS = [
    detect_audit_column,
    detect_system_hash,
    detect_soft_delete_flag,
    detect_free_text_notes,
    detect_foreign_system_id,
    detect_sentinel_values,
]


def _build_column_profile(
    series: pl.Series,
    total_rows: int,
) -> ColumnProfile:
    """Cheap stats for one column. O(rows) once per column; downstream
    detectors operate on the profile + a 1000-row sample only.
    """
    null_count = int(series.null_count())
    non_null = total_rows - null_count
    distinct_count = int(series.n_unique())
    cardinality_ratio = (distinct_count / non_null) if non_null > 0 else 0.0
    null_rate = (null_count / total_rows) if total_rows > 0 else 0.0
    dtype_str = str(series.dtype)
    mean_string_length: float | None = None
    if series.dtype == pl.Utf8:
        try:
            non_null_lengths = (
                series.drop_nulls().str.len_chars().mean()
            )
            mean_string_length = (
                float(non_null_lengths) if non_null_lengths is not None else None
            )
        except Exception:  # pragma: no cover - defensive
            mean_string_length = None
    return ColumnProfile(
        cardinality_ratio=cardinality_ratio,
        null_rate=null_rate,
        distinct_count=distinct_count,
        dtype=dtype_str,
        mean_string_length=mean_string_length,
    )


def _sample_non_null_values(
    series: pl.Series,
    sample_size: int = 1000,
) -> list:
    """Sample up to ``sample_size`` non-null values. Deterministic head
    sample (not random) so detector results are reproducible.
    """
    non_null = series.drop_nulls()
    if non_null.len() <= sample_size:
        return non_null.to_list()
    return non_null.head(sample_size).to_list()


def detect_autoconfig_exclusions(
    df: pl.DataFrame,
    *,
    force_exclude: list[str] | None = None,
    force_include: list[str] | None = None,
    sample_size: int = 1000,
    skip_columns: set[str] | None = None,
) -> list[ExcludedColumn]:
    """Run all 6 detectors over every column in ``df``. Returns the
    final exclusion list after applying force-include/force-exclude
    overrides.

    Args:
        df: DataFrame to inspect.
        force_exclude: extra columns to mark excluded regardless of
            detector output. Caller-supplied (e.g. from QualityConfig).
        force_include: columns to rescue from any auto-detection.
            ``force_include`` wins when it conflicts with
            ``force_exclude``: the user explicitly opting back in
            takes precedence over the user's prior exclude list.
        sample_size: per-column sample cap for content detectors.
        skip_columns: columns the orchestrator should not even scan
            (e.g. internal ``__row_id__`` / ``__source__`` bookkeeping).
            These never appear in the result regardless of detector
            output; force_include cannot rescue them.

    Returns:
        list[ExcludedColumn] -- one entry per excluded column. Order
        matches df.columns order so logs read top-to-bottom by schema.
    """
    force_exclude_set = set(force_exclude or [])
    force_include_set = set(force_include or [])
    skip_set = set(skip_columns or [])

    total_rows = df.height
    excluded: list[ExcludedColumn] = []
    for col in df.columns:
        if col in skip_set:
            continue
        if col in force_include_set:
            # Explicit user opt-in beats every detector + force_exclude.
            continue
        if col in force_exclude_set:
            excluded.append(ExcludedColumn(
                column=col,
                detector="user_force_exclude",
                reason="caller-supplied force-exclude (config.quality.autoconfig_force_exclude)",
                evidence={},
            ))
            continue

        series = df[col]
        profile = _build_column_profile(series, total_rows)
        sampled = _sample_non_null_values(series, sample_size=sample_size)

        for detector in _DETECTORS:
            result = detector(col, sampled, profile)
            if result is not None:
                excluded.append(result)
                break  # first detector wins; no double-exclusion

    return excluded
