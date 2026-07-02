"""Auto-configuration engine for GoldenMatch zero-config mode."""

from __future__ import annotations

import contextlib
import logging
import math
import os
import re
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import polars as pl

if TYPE_CHECKING:
    from goldenmatch.config.schemas import StandardizationConfig
    from goldenmatch.core.autoconfig_memory import AutoConfigMemory

from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    BudgetConfig,
    GoldenMatchConfig,
    GoldenRulesConfig,
    LLMScorerConfig,
    MatchkeyConfig,
    MatchkeyField,
    MemoryConfig,
    OutputConfig,
)
from goldenmatch.core.autoconfig_discriminative import (
    should_demote_attribute_field,
    should_veto_exact,
)
from goldenmatch.core.complexity_profile import DataProfile
from goldenmatch.core.profile_emitter import _emitter_stack, current_emitter
from goldenmatch.core.profiler import _guess_type

logger = logging.getLogger(__name__)

# Refdata-aware matchkey-field refinement. Hoisted to module-top so the
# per-iteration loop in build_matchkeys / build_probabilistic_matchkeys
# doesn't pay the `from X import Y` lookup cost or re-run the try/except
# block per column. Falls back to a no-op identity when the refdata
# package is unimportable OR its top-level import raises for any reason
# (corrupt bundled data file, transitively-broken pack, etc.) — the
# autoconfig path must never fail because a refdata pack is unhealthy.
try:
    from goldenmatch.refdata.autoconfig_hooks import (
        refine_matchkey_field as _refdata_refine_matchkey_field,
    )
except Exception as _refdata_import_exc:  # noqa: BLE001 — see comment above
    logger.debug(
        "refdata.autoconfig_hooks unavailable (%s); refinements disabled.",
        _refdata_import_exc,
    )

    def _refdata_refine_matchkey_field(
        column_name: str,
        scorer: str,
        transforms: list[str],
        col_type: str | None = None,
    ) -> tuple[str, list[str]]:
        return scorer, transforms


def _emit_data_profile(df: pl.DataFrame) -> None:
    """Emit DataProfile from the input DataFrame. No-op when null emitter."""
    if not _emitter_stack.get():
        return
    user_cols = [c for c in df.columns if not c.startswith("__")]
    column_types: dict[str, str] = {}
    cardinality_ratio: dict[str, float] = {}
    null_rate: dict[str, float] = {}
    value_length_p50: dict[str, int] = {}
    value_length_p99: dict[str, int] = {}
    n_rows = df.height
    for col in user_cols:
        ser = df[col]
        non_null = ser.drop_nulls()
        n_non_null = non_null.len()
        cardinality_ratio[col] = (non_null.n_unique() / n_non_null) if n_non_null else 0.0
        null_rate[col] = 1 - (n_non_null / n_rows) if n_rows else 0.0
        dtype = str(ser.dtype).lower()
        if "utf" in dtype or "str" in dtype:
            column_types[col] = "text"
        elif "int" in dtype or "float" in dtype:
            column_types[col] = "numeric"
        elif "date" in dtype or "time" in dtype:
            column_types[col] = "date"
        else:
            column_types[col] = "unknown"
        if column_types[col] == "text" and n_non_null:
            try:
                lens = sorted(non_null.cast(pl.Utf8).str.len_chars().to_list())
                if lens:
                    value_length_p50[col] = int(lens[len(lens) // 2])
                    value_length_p99[col] = int(lens[max(0, int(0.99 * len(lens)) - 1)])
            except Exception:
                pass
    current_emitter().set_data(DataProfile(
        n_rows=n_rows,
        n_cols=len(user_cols),
        column_types=column_types,  # pyright: ignore[reportArgumentType]  # runtime values match ColumnType literal set

        cardinality_ratio=cardinality_ratio,
        null_rate=null_rate,
        value_length_p50=value_length_p50,
        value_length_p99=value_length_p99,
    ))

# ── Column name heuristics ─────────────────────────────────────────────────

_NAME_PATTERNS = re.compile(
    r"(^name$|first.?name|last.?name|full.?name|fname|lname|surname|given.?name)",
    re.IGNORECASE,
)
_EMAIL_PATTERNS = re.compile(r"(email|e.?mail|email.?addr)", re.IGNORECASE)
_PHONE_PATTERNS = re.compile(r"(phone|tel|mobile|fax|cell)", re.IGNORECASE)
_ZIP_PATTERNS = re.compile(r"(zip|postal|postcode|zip.?code)", re.IGNORECASE)
_PRICE_PATTERNS = re.compile(r"(price|cost|amount|revenue|salary|fee|charge|total|balance)", re.IGNORECASE)
_ADDRESS_PATTERNS = re.compile(r"(address|street|addr|line.?1|line.?2)", re.IGNORECASE)
_GEO_PATTERNS = re.compile(r"((?<![a-z])city|^state$|state.?cd|^country$|province|region|(?<![a-z])county)", re.IGNORECASE)
_DATE_PATTERNS = re.compile(r"(date|_dt$|_date$|registr|created|updated|birth.?d|dob)", re.IGNORECASE)
_YEAR_PATTERNS = re.compile(r"(^|_)(year|yr)(_|$)", re.IGNORECASE)
_ID_PATTERNS = re.compile(
    r"^(?i:id|key|code|sku)$"                      # whole-name matches
    r"|_(?i:id|key)$"                              # *_id / *_key suffix
    r"|(?<=[a-zA-Z])(?:ID|Id)$"                    # CamelCase ID suffix (e.g. recordID)
    r"|(?i:^uuid$|^guid$|_uuid$|_guid$)"           # uuid/guid bounded
    r"|(?i:^uuid_|^guid_)"                         # uuid_*/guid_* prefix (e.g. guid_col)
    r"|^(?i:account_no|account_num)$"              # whole-name account identifiers
    r"|_(?i:ref|ref_num|reg_num|account_no|account_num|account)$"  # targeted ID-suffixes
)


@dataclass
class ColumnProfile:
    """Profile of a single column for auto-configuration."""

    name: str
    dtype: str
    col_type: str  # email, name, phone, zip, address, geo, identifier, description, numeric, date, string
    confidence: float  # 0.0 to 1.0
    sample_values: list[str] = field(default_factory=list)
    null_rate: float = 0.0  # fraction of nulls (0-1)
    cardinality_ratio: float = 0.0  # unique values / total rows (0-1)
    avg_len: float = 0.0  # average string length


@dataclass
class AutoConfigDecisions:
    """Fields marked 'not read yet' are wired up in subsequent tasks; they must not be read before then.

    Captures the *choices* auto_configure_df makes from profiled data.

    Separating decisions from the GoldenMatchConfig enables future iterative
    tuning: a future loop can nudge these decisions without re-profiling and
    then rebuild the config via `_rebuild_from_decisions`.

    Populated only by auto_configure_df; not persisted to YAML.
    """

    blocking_strategy: str
    blocking_keys: list[BlockingKeyConfig]
    blocking_passes: list[BlockingKeyConfig]
    matchkeys: list[MatchkeyConfig]
    threshold: float                # TODO(autoconfig-verify): consumed by postflight threshold nudge — not read yet
    domain_mode: str | None         # populated from detected DomainProfile.name; not read yet
    llm_enabled: bool               # TODO(autoconfig-verify): preflight Check 5 input — not read yet
    allow_remote_assets: bool       # TODO(autoconfig-verify): preflight Check 5 input — not read yet


def _classify_by_name(col_name: str) -> str | None:
    """Phase 1: classify column by name pattern matching.

    Order matters: ID and price before phone/zip to prevent data profiling
    from overriding name-based classification (e.g., 7-digit IDs as phones,
    5-digit prices as zips).
    """
    if _DATE_PATTERNS.search(col_name):
        return "date"
    if _YEAR_PATTERNS.search(col_name):
        return "year"
    if _EMAIL_PATTERNS.search(col_name):
        return "email"
    if _ID_PATTERNS.search(col_name):
        return "identifier"
    if _PRICE_PATTERNS.search(col_name):
        return "numeric"
    if _ZIP_PATTERNS.search(col_name):
        return "zip"
    if _GEO_PATTERNS.search(col_name):
        return "geo"
    if _ADDRESS_PATTERNS.search(col_name):
        return "address"
    if _PHONE_PATTERNS.search(col_name):
        return "phone"
    if _NAME_PATTERNS.search(col_name):
        return "name"
    return None


def _classify_by_data(values: list[str]) -> tuple[str, float]:
    """Phase 2: classify column by data profiling. Returns (type, confidence)."""
    if not values:
        return "string", 0.0

    data_type = _guess_type(values)

    # Cardinality guard: near-unique numeric-looking columns (phone/zip
    # lookalikes) are almost certainly identifiers. Scoping to numeric-shaped
    # types avoids reclassifying long text columns (titles, descriptions,
    # distinct names) as identifiers. Require a non-trivial sample (>=10)
    # so a handful of genuinely-unique zip/phone rows don't trip the guard.
    if data_type in ("phone", "zip", "numeric") and len(values) >= 10:
        cardinality_ratio = len(set(values)) / len(values)
        # S2a (spec 2026-06-22): identifier floor max(0.95, 1 - 1/sqrt(n)). At
        # scale the floor RISES above the old fixed 0.95 (a 10k-row 0.95-card
        # column is a high-entropy name, not an ID), and never drops below 0.95
        # so small-n behavior is unchanged (a looser small-n floor reclassified
        # moderately-unique phone/numeric columns and broke established matchkey
        # behavior). math.sqrt is correctly-rounded IEEE 754 -> bit-identical to
        # Rust's .sqrt() (do NOT use n ** 0.5 -- pow isn't correctly-rounded).
        floor = max(0.95, 1.0 - 1.0 / math.sqrt(len(values)))
        if cardinality_ratio >= floor:
            return "identifier", 0.9

    # Year detection: 4-digit integers in 1900..2100. Cheap blocking signal
    # for bibliographic / birth-year data (not full dates).
    def _is_year(v: str) -> bool:
        """True if v looks like a 4-digit year in 1900-2100, tolerating
        float-promoted integer columns (e.g. '1999.0')."""
        v = v.strip()
        try:
            n = int(float(v))
        except (ValueError, TypeError, OverflowError):
            # OverflowError: float('1e500') -> inf; int(inf) raises OverflowError.
            return False
        if not (1900 <= n <= 2100):
            return False
        # Round-trip check: stringified int must match v (with optional '.0').
        # Prevents '1.999e3' / '2001abc' from sneaking through.
        return str(n) == v.replace(".0", "").strip()

    if values and all(_is_year(v) for v in values):
        return "year", 0.9

    # Map profiler types to our types
    type_map = {
        "email": "email",
        "phone": "phone",
        "zip": "zip",
        "state": "geo",
        "numeric": "numeric",
        "name": "name",
        "address": "address",
        "date": "date",
        "text": "string",
    }

    col_type = type_map.get(data_type, "string")

    # Multi-value name detection: comma/semicolon-delimited text with
    # substantive length is almost always a co-author / multi-name field.
    # Catches this before the generic description branch so it gets routed
    # to token_sort rather than the embedding pathway.
    if col_type == "string":
        rows_with_delim = sum(1 for v in values if "," in v or ";" in v)
        delim_ratio = rows_with_delim / max(len(values), 1)
        if rows_with_delim > 0:
            avg_delims_in_delim_rows = sum(
                (v.count(",") + v.count(";")) for v in values if ("," in v or ";" in v)
            ) / rows_with_delim
        else:
            avg_delims_in_delim_rows = 0
        avg_len = sum(len(v) for v in values) / max(len(values), 1)
        if avg_len > 30 and delim_ratio >= 0.7 and avg_delims_in_delim_rows >= 2:
            return "multi_name", 0.7

    # Check for description (long freetext)
    if col_type == "string":
        avg_len = sum(len(v) for v in values) / len(values) if values else 0
        if avg_len > 50:
            col_type = "description"

    # Confidence based on how strongly data matches the type
    confidence = 0.7 if col_type != "string" else 0.3
    return col_type, confidence


def profile_columns(
    df: pl.DataFrame, sample_size: int = 1000, max_columns: int = 40,
    llm_provider: str | None = None,
) -> list[ColumnProfile]:
    """Classify columns by type using name heuristics + data profiling.

    Samples randomly to avoid bias from header-adjacent rows.
    Wide datasets (>max_columns) are trimmed: columns matching known patterns
    (name, email, phone, zip, address) are prioritized, then remaining columns
    fill up to the cap.
    """
    # Sample randomly
    if df.height > sample_size:
        sample = df.sample(sample_size, seed=42)
    else:
        sample = df

    # For wide datasets, prioritize columns likely useful for matching
    columns = [c for c in df.columns if not c.startswith("__")]
    if len(columns) > max_columns:
        # Phase 1: keep columns matching known patterns
        priority = []
        rest = []
        for col_name in columns:
            if _classify_by_name(col_name) is not None:
                priority.append(col_name)
            else:
                rest.append(col_name)
        # Fill remaining slots from unmatched columns
        remaining_slots = max(0, max_columns - len(priority))
        columns = priority + rest[:remaining_slots]
        logger.info(
            "Wide dataset (%d columns), auto-configure limited to %d columns "
            "(%d pattern-matched, %d additional)",
            len(df.columns), len(columns), len(priority), remaining_slots,
        )

    # Collect per-column stats using Polars (shared by both the native and
    # pure-Python paths — the native path delegates ONLY the classify step).
    col_stats: list[tuple[str, str, list[str], float, float, float]] = []
    for col_name in columns:
        if col_name.startswith("__"):
            continue

        dtype = str(df[col_name].dtype)

        col_series = sample[col_name]
        total_rows = col_series.len()
        null_count = col_series.null_count()
        null_rate = null_count / total_rows if total_rows > 0 else 0.0

        values = [
            str(v) for v in col_series.drop_nulls().to_list()
            if v is not None and str(v).strip()
        ]

        cardinality_ratio = len(set(values)) / total_rows if total_rows > 0 else 0.0
        avg_len = sum(len(v) for v in values) / len(values) if values else 0.0

        col_stats.append((col_name, dtype, values, null_rate, cardinality_ratio, avg_len))

    from goldenmatch.core._native_loader import native_enabled, native_module  # noqa: PLC0415

    _use_native = native_enabled("autoconfig")
    _nm = native_module() if _use_native else None
    _native_classify_available = (
        _use_native and _nm is not None and hasattr(_nm, "autoconfig_classify_columns")
    )

    if _native_classify_available:
        # Native batch path: send all column stats to Rust, reconstruct profiles.
        from goldenmatch.core.autoconfig_native import (  # noqa: PLC0415
            column_profiles_from_json,
            column_stats_to_json,
        )
        stats_dicts = [
            {
                "name": col_name,
                "dtype": dtype,
                "sample_values": values,  # FULL non-null list
                "null_rate": null_rate,
                "cardinality_ratio": cardinality_ratio,
                "avg_len": avg_len,
            }
            for col_name, dtype, values, null_rate, cardinality_ratio, avg_len in col_stats
        ]
        names_to_sample_values = {
            col_name: values
            for col_name, _dtype, values, _nr, _cr, _al in col_stats
        }
        native_json = _nm.autoconfig_classify_columns(  # type: ignore[union-attr]
            column_stats_to_json(stats_dicts)
        )
        profiles: list[ColumnProfile] = column_profiles_from_json(
            native_json, names_to_sample_values
        )
    else:
        # Pure-Python classification path (unchanged).
        profiles = []
        for col_name, dtype, values, null_rate, cardinality_ratio, avg_len in col_stats:
            # Phase 1: name heuristics
            name_type = _classify_by_name(col_name)

            # Phase 2: data profiling
            data_type, data_confidence = _classify_by_data(values)

            # Combine: name heuristics are authoritative for structural types
            # (date, geo) because data profiling frequently misclassifies them
            # (e.g., ISO dates look like phone numbers, city names look like person names).
            # For other types, Phase 2 (data) wins when it contradicts Phase 1 (name).
            _name_authoritative = {"date", "geo", "identifier", "numeric", "year"}
            if name_type and name_type in _name_authoritative:
                # Name pattern is authoritative for date/geo — trust it
                col_type = name_type
                confidence = 0.9
            elif name_type and data_type != "string":
                # Both have opinions — Phase 2 wins if types differ
                if name_type == data_type:
                    col_type = name_type
                    confidence = min(data_confidence + 0.2, 1.0)
                else:
                    col_type = data_type
                    confidence = data_confidence
            elif name_type:
                col_type = name_type
                confidence = 0.6
            else:
                col_type = data_type
                confidence = data_confidence

            profiles.append(ColumnProfile(
                name=col_name,
                dtype=dtype,
                col_type=col_type,
                confidence=confidence,
                sample_values=values[:5],
                null_rate=null_rate,
                cardinality_ratio=cardinality_ratio,
                avg_len=avg_len,
            ))

    # LLM correction pass for ambiguous columns (runs AFTER native classify,
    # on the same filtered set the Python path uses — unchanged).
    if llm_provider and profiles:
        profiles = _llm_classify_columns(profiles, llm_provider)

    return profiles


def _llm_classify_columns(
    profiles: list[ColumnProfile], provider: str,
) -> list[ColumnProfile]:
    """Use LLM to correct ambiguous column classifications and rank match fields.

    Only sends columns with low confidence or generic types (string, numeric).
    High-confidence classifications (date, geo, email, identifier) are trusted.
    """
    import json as _json
    import urllib.error

    # Filter to ambiguous profiles
    high_confidence_types = {"date", "geo", "email", "identifier"}
    ambiguous = [
        p for p in profiles
        if p.confidence < 0.8 or p.col_type in ("string", "numeric")
        if p.col_type not in high_confidence_types
    ]

    if not ambiguous:
        return profiles

    # Build prompt
    col_lines = []
    for p in ambiguous:
        samples = ", ".join(p.sample_values[:5]) if p.sample_values else "no samples"
        col_lines.append(f'  "{p.name}": [{samples}]')

    all_col_names = [p.name for p in profiles if p.col_type not in high_confidence_types]

    prompt = (
        "You are classifying database columns for entity matching/deduplication.\n\n"
        "For each column below, provide:\n"
        '1. "type": one of: identifier, name, description, numeric, date, geo, '
        "email, phone, zip, address, price, string\n"
        '2. "match_rank": rank the top 5 columns most useful for entity matching '
        "(1=most useful). Only rank columns that would help identify duplicate records.\n\n"
        "Columns with sample values:\n"
        + "\n".join(col_lines)
        + "\n\nAll columns available for ranking: " + ", ".join(all_col_names)
        + '\n\nRespond in JSON: {"classifications": {"col_name": "type", ...}, '
        '"match_ranking": ["col1", "col2", "col3", "col4", "col5"]}'
    )

    try:
        raw = _call_llm_for_blocking(prompt, provider)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError, KeyError) as e:
        logger.warning("LLM column classification failed: %s. Using heuristics only.", e)
        return profiles

    # Parse response
    try:
        # Extract JSON from response (may be wrapped in markdown)
        text = raw.strip()
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        data = _json.loads(text)
    except (ValueError, IndexError) as e:
        logger.warning(
            "LLM column classification returned unparseable response (error: %s). "
            "Raw response (first 200 chars): %.200s", e, raw,
        )
        return profiles

    # Normalize type aliases
    _type_aliases = {
        "id": "identifier", "ids": "identifier",
        "desc": "description", "text": "string",
        "location": "geo", "city": "geo", "state": "geo",
        "postal": "zip", "postcode": "zip",
        "cost": "numeric", "price": "numeric", "amount": "numeric",
        "tel": "phone", "telephone": "phone",
    }
    valid_types = {
        "identifier", "name", "description", "numeric", "date", "geo",
        "email", "phone", "zip", "address", "string",
    }

    # Apply type corrections
    classifications = data.get("classifications", {})
    profile_by_name = {p.name: p for p in profiles}
    for col_name, llm_type in classifications.items():
        if col_name not in profile_by_name:
            continue
        if not isinstance(llm_type, str):
            continue
        p = profile_by_name[col_name]
        # Only correct ambiguous columns
        if p.col_type in high_confidence_types:
            continue
        normalized = _type_aliases.get(llm_type.lower(), llm_type.lower())
        if normalized in valid_types:
            logger.info("LLM reclassified '%s': %s -> %s", col_name, p.col_type, normalized)
            p.col_type = normalized
            p.confidence = 0.85

    # Apply match ranking (stored as metadata for build_matchkeys to use)
    match_ranking = data.get("match_ranking", [])
    if match_ranking:
        # Store ranking as a special attribute on profiles
        for rank, col_name in enumerate(match_ranking[:5]):
            if col_name in profile_by_name:
                # Use a high utility boost so LLM-ranked fields sort first
                p = profile_by_name[col_name]
                p.cardinality_ratio = max(p.cardinality_ratio, 0.9 - rank * 0.1)
                p.avg_len = max(p.avg_len, 40 - rank * 5)
        logger.info("LLM match ranking: %s", match_ranking[:5])

    return profiles


# ── Scorer and matchkey generation ─────────────────────────────────────────

_SCORER_MAP = {
    "email": ("exact", 1.0, ["lowercase", "strip"]),
    "phone": ("exact", 0.8, ["digits_only"]),
    "zip": ("exact", 0.5, ["strip"]),
    "name": ("ensemble", 1.0, ["lowercase", "strip"]),
    "address": ("token_sort", 0.8, ["lowercase", "strip"]),
    "identifier": ("exact", 1.0, ["strip"]),
    "geo": ("exact", 0.3, ["lowercase", "strip"]),
    "string": ("token_sort", 0.5, ["lowercase", "strip"]),
}

# ── Fellegi-Sunter auto-config v2 (comparison-set + blocking curation) ──
# Scoped to the PROBABILISTIC path only (auto_configure_probabilistic_df); the
# weighted/DQbench path is untouched. Default ON; GOLDENMATCH_FS_AUTOCONFIG_V2=0
# restores the legacy field set. See build_probabilistic_matchkeys docstring +
# the historical_50k PII-parity audit for rationale.
_PROB_FUZZY_CARD_FLOOR = 0.01

_ATOMIC_GIVEN_NAMES = frozenset({
    "first_name", "firstname", "first", "given_name", "givenname", "given",
    "forename", "fname",
})
_ATOMIC_FAMILY_NAMES = frozenset({
    "surname", "last_name", "lastname", "last", "family_name", "familyname",
    "family", "lname",
})
# Person-name composites that duplicate the atomic given/family signal. Dropped
# only when BOTH an atomic given and family field are present (so a dataset with
# just `full_name` keeps it).
_COMPOSITE_NAME_FIELDS = frozenset({
    "full_name", "fullname", "name", "first_and_surname", "firstandsurname",
    "first_and_last", "name_full", "complete_name", "whole_name",
    "display_name", "displayname", "first_last", "given_and_surname",
})


def _norm_colname(name: str) -> str:
    """Normalize a column name for atomic/composite matching."""
    return name.strip().lower().replace(" ", "_").replace("-", "_")


def _fs_autoconfig_v2_enabled() -> bool:
    """FS auto-config v2 comparison-set + blocking curation.

    **Default ON (2026-06-09).** ``GOLDENMATCH_FS_AUTOCONFIG_V2=0`` (or
    ``false``/``off``/``no``/``disabled``) restores the legacy field set. Flipped
    on after the curated set beat Splink on every measurable dataset and the
    DBLP-ACM mega-match was fixed (lever #4) — no remaining measured regression.

    MEASURED (scripts/bench_er_headtohead, GM probabilistic vs Splink, one
    evaluator) — v2 beats Splink on every PII set AND unbreaks bibliographic:
      historical_50k F1 0.624 -> 0.779 (Splink 0.757)
      febrl3         F1 0.983 -> 0.991 (Splink 0.965)
      synthetic      F1 0.972 -> 0.998 (Splink 0.996)
      dblp_acm       F1 0.003 -> 0.879 (auto-config was a venue-only mega-match)
    The `venue` low-card-floor worry did NOT materialize (venue card 0.010 >
    floor 0.01). The panel-v1-v2 lane in bench-probabilistic.yml is the standing
    v1-vs-v2 regression check.
    """
    return os.environ.get("GOLDENMATCH_FS_AUTOCONFIG_V2", "1").lower() not in (
        "0", "false", "off", "no", "disabled",
    )



# Domain-extracted column scorer mapping.
# These columns are added by extract_features() and start with __.
_DOMAIN_SCORER_MAP = {
    # Electronics
    "__brand__": ("exact", 0.8, ["lowercase", "strip"]),
    "__model__": ("exact", 1.0, ["strip"]),
    "__model_norm__": ("exact", 1.0, []),
    "__color__": ("exact", 0.2, ["lowercase"]),
    "__specs__": ("token_sort", 0.3, ["strip"]),
    # Software
    "__sw_name__": ("token_sort", 1.0, ["lowercase", "strip"]),
    "__sw_version__": ("exact", 0.5, ["strip"]),
    "__sw_edition__": ("exact", 0.3, ["lowercase"]),
    "__sw_platform__": ("exact", 0.3, ["lowercase"]),
    "__sw_part_num__": ("exact", 1.0, ["strip"]),
    # Bibliographic
    "__title_key__": ("exact", 0.8, ["lowercase"]),
}


# Free-text col_types where data-entry corruption is common and `token_sort`
# (word-order-robust but character-noise-fragile) underperforms. Surfaced by the
# NCVR regression: res_street_address scored with token_sort gave F1 0.871;
# jaro_winkler recovered 0.981. See #662.
_NOISE_PRONE_FUZZY_TYPES = frozenset({"address", "string"})

# Benchmark-confirmed upgrade target (#662). The sweep tied jaro_winkler with
# ensemble on NCVR-high F1; jaro_winkler is chosen as the cheaper of the tie
# (single-pass char similarity vs the multi-scorer ensemble) and is the
# NCVR-validated lever.
_NOISE_AWARE_TARGET_SCORER = "jaro_winkler"


def _noise_aware_target_scorer() -> str:
    """The scorer token_sort upgrades to on noise-prone col_types. Benchmark-only
    env override (GOLDENMATCH_NOISE_AWARE_TARGET) lets the harness sweep
    jaro_winkler/ensemble without code edits; unset -> the committed constant."""
    return os.environ.get("GOLDENMATCH_NOISE_AWARE_TARGET") or _NOISE_AWARE_TARGET_SCORER


def _noise_aware_scorers_enabled() -> bool:
    """Whether to upgrade noise-fragile token_sort assignments on free-text
    col_types. Default ON (#662: jaro_winkler gave +0.48pp NCVR-high F1,
    precision-driven, no Febrl3 regression; #528 CI gate guards clean precision).
    Kill-switch: GOLDENMATCH_NOISE_AWARE_SCORERS=0 (or "false"/"disabled",
    case-insensitive)."""
    return os.environ.get("GOLDENMATCH_NOISE_AWARE_SCORERS", "1").lower() not in (
        "0", "false", "disabled",
    )


def _tf_name_weighting_enabled() -> bool:
    """Whether auto-config populates ``MatchkeyField.tf_freqs`` for the
    data-driven name-frequency downweight on ``name_freq_weighted_jw`` fields
    (#1207 PR2a). Default ON. Without a populated table the scorer falls back to
    the static US-Census surname path, so this is the seam that makes the
    per-dataset downweight actually fire. Kill-switch:
    GOLDENMATCH_TF_NAME_WEIGHTING=0 (or false/no/off, case-insensitive)."""
    return os.environ.get("GOLDENMATCH_TF_NAME_WEIGHTING", "1").strip().lower() not in {
        "0", "false", "no", "off",
    }


def _route_to_probabilistic_enabled() -> bool:
    """Auto-route a probabilistic-shaped dataset (no strong-identity exact matchkey
    + multiple weak fuzzy fields) to the Fellegi-Sunter path instead of the default
    exact+weighted matchkeys. Default OFF (2026-06-23) -- a behavior change pending
    the dual-strategy corpus proof. Enable:
    GOLDENMATCH_AUTOCONFIG_ROUTE_PROBABILISTIC=1 (or true/yes/on/enabled)."""
    return os.environ.get("GOLDENMATCH_AUTOCONFIG_ROUTE_PROBABILISTIC", "0").lower() in (
        "1", "true", "yes", "on", "enabled",
    )


# Exact matchkeys on these col_types are a strong identity claim — when one
# SURVIVES into the config, exact matching carries the dedup and the probabilistic
# path tends to lose (verified on anchor_person_match: clean-email exact beats FS).
# Broader than just "identifier": the dual-strategy harness showed email/phone exact
# matchkeys are equally strong. `zip` is NOT here (a blocking signal, not identity).
_STRONG_EXACT_TYPES = ("identifier", "email", "phone")


def _is_probabilistic_shape(
    matchkeys: list[MatchkeyConfig], profiles: list[ColumnProfile]
) -> bool:
    """Probabilistic shape = no SURVIVING exact matchkey backed by a strong-identity
    column (identifier/email/phone) + >=2 fuzzy (weighted) fields for EM to weight.
    Keys on the EMITTED matchkeys (not raw profiles), so a ceiling-excluded id column
    (a perfectly-unique surrogate) correctly counts as 'no surviving strong key'."""
    col_type = {p.name: p.col_type for p in profiles}
    # f.field is str | None (embedding fields have None); drop None for the lookup.
    exact_fields = [f.field for mk in matchkeys if mk.type == "exact" for f in mk.fields if f.field]
    has_strong_id = any(col_type.get(fld) in _STRONG_EXACT_TYPES for fld in exact_fields)
    fuzzy_field_count = sum(len(mk.fields) for mk in matchkeys if mk.type == "weighted")
    return (not has_strong_id) and fuzzy_field_count >= 2


def _noise_aware_scorer(col_type: str, scorer: str) -> str:
    """Upgrade a noise-fragile token_sort to the noise-aware target scorer for
    free-text col_types prone to character-level corruption. No-op unless the gate
    is on AND the assignment is exactly token_sort on a noise-prone col_type — so
    a non-default scorer chosen upstream (refdata/embedding/ensemble/qgram) is
    never overridden."""
    if (
        _noise_aware_scorers_enabled()
        and col_type in _NOISE_PRONE_FUZZY_TYPES
        and scorer == "token_sort"
    ):
        return _noise_aware_target_scorer()
    return scorer


def _is_short_code(p: ColumnProfile) -> bool:
    """#491: detect short alphanumeric *code* columns (SKU, part-no, plan-code).

    These collapse under the generic string scorers (``token_sort`` token-bag
    similarity is meaningless on a 6-char opaque code), so they get routed to
    the char-n-gram ``qgram`` scorer instead. Deliberately conservative — a
    real name/word column must NOT match, or we'd silently swap proven name
    scoring for qgram. The letter+digit-mix requirement is the main guard:
    English names/words are letters-only, so they fail it.
    """
    if not (3 <= p.avg_len <= 12):
        return False
    if p.cardinality_ratio < 0.3:
        return False
    # Only generic string-ish columns are candidates; named identity types
    # (name/email/phone/zip/geo/date/numeric) keep their tuned scorers.
    if p.col_type not in ("string", "identifier"):
        return False

    samples = [s for s in (p.sample_values or []) if s]
    if not samples:
        return False

    # Code-like signal: the value carries BOTH a letter and a digit
    # (e.g. ``A1B2C3``, ``SKU-9921``). This is the discriminating test that
    # excludes pure-alpha names/words and pure-numeric IDs (already handled
    # as identifiers/numeric upstream). Require a clear majority to look
    # code-like before overriding.
    def _is_code_like(s: str) -> bool:
        has_alpha = any(c.isalpha() for c in s)
        has_digit = any(c.isdigit() for c in s)
        return has_alpha and has_digit

    code_like = sum(1 for s in samples if _is_code_like(s))
    return code_like >= max(1, (len(samples) + 1) // 2)


def _adaptive_threshold(fields: list[MatchkeyField]) -> float:
    """Compute threshold based on field types in the matchkey."""
    exact_scorers = {"exact"}
    embedding_scorers = {"embedding", "record_embedding"}

    scorers = {f.scorer for f in fields if f.scorer}

    if scorers <= exact_scorers:
        return 0.95
    if scorers & embedding_scorers:
        return 0.70
    if len(fields) == 1:
        return 0.85
    return 0.80


# #858: distinguish real person-name columns from "name"-typed SHARED ATTRIBUTE
# columns (company, job_title, department, ...). The profiler's name heuristic
# classifies an organization/role column as col_type="name", which then enters
# the weighted matchkey as a full-weight person-name fuzzy field. Under
# multi-source that collapses distinct people who share an employer / title (the
# crm_multisource_realistic over-merge: bare F1 0.13 -> 0.77 once company +
# job_title are demoted). The org/attribute check runs FIRST so a name-bearing
# org column (e.g. "company_name") is still recognized as a shared attribute.
_ORG_ATTR_COLUMN_RE = re.compile(
    r"(?i)(?:^|[^a-z])("
    r"company|employer|organi[sz]ation|org|firm|business|corp|"
    r"job|title|role|position|occupation|department|dept|division|team|group|"
    r"category|segment|industry|sector|brand|product"
    r")(?:[^a-z]|$)"
)
_PERSON_NAME_COLUMN_RE = re.compile(
    r"(?i)(?:^|[^a-z])("
    r"first|last|given|family|sur|surname|middle|maiden|forename|fore|"
    r"nick|nickname|preferred|legal|full|display|fname|lname|mname|name|"
    r"person|contact|individual|party|customer|client|member|patient|applicant"
    r")(?:[^a-z]|$)"
)


def _is_person_name_column(column: str) -> bool:
    """True when a ``col_type='name'`` column is plausibly a PERSON name (so it
    earns a positive weighted-matchkey feature), False when it's a shared
    organization/role attribute (company, job_title, department). The org check
    wins, so ``company_name`` reads as a shared attribute, not a person name."""
    if _ORG_ATTR_COLUMN_RE.search(column):
        return False
    return bool(_PERSON_NAME_COLUMN_RE.search(column))


def _exact_matchkey_floor_py(col_type: str) -> float:
    """Pure-Python oracle for the S3 per-type exact-matchkey cardinality floor.

    phone 0.30 (legitimately shared -> permissive); everything else, INCLUDING
    email, keeps the historical 0.50 default. A shared email (cardinality 0.5) is
    a genuine identity signal this codebase keeps as an exact matchkey, and the
    matchkey-guard tests pin email's floor to 0.50; the spec's initial email=0.70
    demoted those and was corrected. Mirrors the Rust `exact_matchkey_floor`
    kernel (golden-vector parity). Closes the TODO at issue #715.
    """
    if col_type == "phone":
        return 0.30
    return 0.50


def exact_matchkey_floor(col_type: str) -> float:
    """S3 per-type exact-matchkey floor. Dispatches to the shared native core
    when enabled (byte-identical to the pure-Python oracle), else pure Python."""
    from goldenmatch.core._native_loader import (  # noqa: PLC0415
        native_enabled,
        native_module,
    )

    if native_enabled("autoconfig"):
        _nm = native_module()
        if hasattr(_nm, "autoconfig_exact_matchkey_floor"):
            import json  # noqa: PLC0415

            out = _nm.autoconfig_exact_matchkey_floor(
                json.dumps({"col_type": col_type})
            )
            return float(json.loads(out)["floor"])
    return _exact_matchkey_floor_py(col_type)


def build_matchkeys(
    profiles: list[ColumnProfile], df: pl.DataFrame | None = None,
    *, multi_source: bool = False,
) -> list[MatchkeyConfig]:
    """Generate matchkeys from column profiles."""
    # Separate exact and fuzzy columns
    exact_fields = []
    fuzzy_fields = []
    description_columns = []

    # Track why each exact-eligible column was skipped, so the aggregate
    # warning below can explain *which* columns were lost and *why* instead
    # of just a count. This is the difference between a notebook user
    # noticing their config silently degraded and not.
    skipped_exact: list[tuple[str, str]] = []  # (column, reason)

    # Person-name columns are the co-agreement anchor for the attribute-demotion
    # check in the loop below: a workplace/locality attribute is one whose
    # shared-value records do NOT share a person NAME. Built once (name-typed AND
    # person-name only, so an org/company "name" field is never in the anchor).
    _person_name_basket: list[tuple[str, bool]] = [
        (p.name, True)
        for p in profiles
        if getattr(p, "col_type", None) in ("name", "multi_name")
        and _is_person_name_column(p.name)
    ]

    for p in profiles:
        # identifier columns ARE matchable: a real shared identifier
        # (NPI/SSN/MRN) backs an exact matchkey, gated below by the
        # cardinality band. Per-record surrogate keys (card==1.0) are
        # excluded by the upper bound. See #715.
        if p.col_type in ("numeric", "date", "year"):
            continue  # year is blocking-only

        if p.col_type == "description":
            fuzzy_fields.append(MatchkeyField(
                field=p.name,
                scorer="token_sort",
                weight=1.5,  # higher weight ensures survival past max_fuzzy_fields truncation
                transforms=["lowercase", "strip"],
            ))
            description_columns.append(p)
            continue

        if p.col_type == "multi_name":
            fuzzy_fields.append(MatchkeyField(
                field=p.name,
                scorer="token_sort",
                weight=1.0,
                transforms=["lowercase", "strip"],
            ))
            continue

        scorer_info = _SCORER_MAP.get(p.col_type)
        if not scorer_info:
            continue

        scorer, weight, transforms = scorer_info

        # Refdata hook: swap scorer / prepend transforms when the column
        # name signals a refdata-handled shape (last_name, first_name,
        # company name, address). No-op when goldenmatch.refdata isn't
        # imported or the relevant pack's data file is missing — the
        # module-top fallback at line ~38 wires a pass-through stub for
        # that case. Lift numbers per refdata pack are in CHANGELOG
        # entries for slices #2-#5. ``p.col_type`` gates each refinement
        # on the profiled data shape so a column literally named
        # ``last_name`` but holding non-name data isn't silently swapped.
        scorer, transforms = _refdata_refine_matchkey_field(
            p.name, scorer, transforms, p.col_type,
        )

        # #491: short opaque codes (SKU, part-no) score badly under the
        # generic string scorers — token_sort/ensemble assume word-ish text.
        # Route them to the char-n-gram qgram scorer instead. Gated on
        # _is_short_code (letter+digit mix, short, high-cardinality) so real
        # names/words/identifiers keep their tuned scorers untouched.
        if scorer in ("token_sort", "ensemble") and _is_short_code(p):
            scorer = "qgram"

        # #662: noise-aware refinement (opt-in, default OFF). Upgrade token_sort
        # -> jaro_winkler/ensemble on corruption-prone free-text col_types. Runs
        # AFTER the qgram short-code guard so a code-like string keeps qgram (the
        # guard already rewrote it; this no-ops on non-token_sort). See
        # _noise_aware_scorer.
        scorer = _noise_aware_scorer(p.col_type, scorer)

        # Workplace-attribute demotion (single-source counterpart to the #858
        # multi-source demotion; gated, default OFF). A workplace / locality
        # attribute -- a clinic ``address``, a shared clinic ``phone`` line, or
        # an employer / org ``company`` name -- shared by colleagues is NOT a
        # person-identity claim: as an exact matchkey (exact_phone) OR a
        # full-weight fuzzy feature it collapses distinct people at one practice
        # into a mega-cluster (the DERM over-merge). Applies to BOTH exact and
        # weighted uses. The verdict is data-measured against the PERSON NAME
        # (not the broad #1351 identity basket, which is polluted here by
        # constant dataset-metadata columns mis-typed as ``identifier`` and by
        # the shared line itself), and scoped so a real person-name / structured
        # identity field is never eligible -- so it is a no-op where the attribute
        # is genuinely identity-correlated (a personal cell whose shared-value
        # records DO co-agree on name is kept). Column stays a blocking candidate.
        if should_demote_attribute_field(
            df, p.name, p.col_type, _person_name_basket,
            is_person_name=_is_person_name_column(p.name),
        ):
            logger.info(
                "Demoting %s field '%s' to blocking-only (discriminative-power: "
                "records sharing its value do not co-agree on the person name -- "
                "shared workplace/locality attribute, not an identity claim). "
                "Column remains a blocking candidate.",
                "exact" if scorer == "exact" else "weighted", p.name,
            )
            continue

        # #858: when multi-source, phone is a blocking signal, not an identity
        # claim -- an exact match on phone collapses distinct people who share a
        # work / household phone. Demote to blocking-only (this continue skips
        # both exact_fields and fuzzy_fields; phone remains a build_blocking
        # candidate, which is unchanged).
        if multi_source and scorer == "exact" and p.col_type == "phone":
            skipped_exact.append((
                p.name,
                "multi-source: phone is a blocking signal, not an identity claim",
            ))
            continue

        # Geo and zip are blocking signals, NOT identity claims. An exact
        # matchkey on a city column asserts "two records sharing a city are
        # the same entity", which collapses every record per city into one
        # mega-cluster. These columns still drive blocking via build_blocking;
        # they just cannot back matchkeys themselves.
        if scorer == "exact" and p.col_type in ("zip", "geo"):
            reason = f"col_type={p.col_type} is a blocking signal, not an identity claim"
            logger.warning(
                "Skipping exact matchkey for '%s' (%s). "
                "Column remains a blocking candidate.",
                p.name, reason,
            )
            skipped_exact.append((p.name, reason))
            continue

        # Exact matchkeys assert identity equivalence, so the backing column
        # must be plausibly unique. S3 (spec 2026-06-22, issue #715): the floor
        # is per-type via the shared exact_matchkey_floor kernel -- emails are
        # near-unique (0.70), phones are legitimately shared (0.30), everything
        # else keeps the historical 0.50. This catches low-cardinality numeric
        # columns misclassified by upstream transforms (e.g. a 4-digit year
        # reshaped into an ISO date looking phone-shaped) that would collapse
        # every row sharing that value into one mega-cluster.
        _exact_floor = exact_matchkey_floor(p.col_type)
        if scorer == "exact" and p.cardinality_ratio > 0 and p.cardinality_ratio < _exact_floor:
            reason = (
                f"cardinality_ratio={p.cardinality_ratio:.4f} < {_exact_floor:.2f} "
                f"(col_type={p.col_type}) -- lacks identifier-level uniqueness"
            )
            logger.warning(
                "Skipping exact matchkey for '%s' (%s). "
                "Exact match would create spurious mega-clusters.",
                p.name, reason,
            )
            skipped_exact.append((p.name, reason))
            continue

        # Exact matchkeys are a Polars hash self-join (find_exact_matches),
        # not a nested loop, and do not pass through fuzzy blocking. Their
        # cost is the number of emitted equal-pairs, bounded by cardinality:
        # a high-cardinality column emits few pairs and is both cheap and
        # mega-cluster-safe. So there is NO row-count guard here (the old
        # df.height > 10000 guard mismodeled the cost and orphaned real
        # identifiers -- see #715). The mega-cluster risk is the OPPOSITE
        # shape (low cardinality), already caught by the >= 0.5 gate above.
        #
        # Upper bound: a perfectly-unique column (card == 1.0) is a
        # per-record surrogate key (e.g. a row PK). It is never shared, so an
        # exact match emits zero pairs and asserts no real identity. Exclude
        # it for config hygiene.
        if scorer == "exact" and p.cardinality_ratio >= 1.0:
            reason = (
                f"cardinality_ratio={p.cardinality_ratio:.4f} >= 1.0 "
                f"-- perfectly-unique surrogate key, no shared identity to match"
            )
            logger.info(
                "Skipping exact matchkey for '%s' (%s).", p.name, reason,
            )
            skipped_exact.append((p.name, reason))
            continue

        # #1351: discriminative-power veto. A column that clears the cardinality
        # gates can still be a shared LOCALITY attribute (e.g. a zip mis-promoted
        # to "identifier") rather than an identity key. Veto its exact matchkey
        # when records sharing its value don't co-agree on other identity fields.
        # Fail-safe keep (df is None / thin support / empty basket) is handled
        # inside should_veto_exact, so near-unique identity keys are unaffected.
        if scorer == "exact" and should_veto_exact(df, p.name, profiles):
            reason = "discriminative-power veto: shared-value records do not co-agree on identity fields"
            logger.warning("Skipping exact matchkey for '%s' (%s).", p.name, reason)
            skipped_exact.append((p.name, reason))
            continue

        # #858: under multi-source, a low-cardinality "name"-typed field whose
        # column is NOT a person name (company, job_title, department, ...) is a
        # shared workplace/categorical attribute, NOT an identity claim. As a
        # full-weight positive feature in the weighted matchkey it collapses
        # distinct people who share an employer / title -- the dominant driver of
        # the multi-source over-merge (crm_multisource_realistic: bare F1 0.13;
        # demoting company + job_title -> 0.77). Demote to blocking-only, exactly
        # like the phone demotion above (real person-name fields and
        # high-cardinality fields are kept). Gated on the multi_source detection,
        # so single-source autoconfig is byte-identical.
        if (
            multi_source
            and scorer != "exact"
            and p.col_type == "name"
            and 0 < p.cardinality_ratio < 0.5
            and not _is_person_name_column(p.name)
        ):
            logger.info(
                "Demoting weighted field '%s' to blocking-only "
                "(multi-source: low-cardinality non-person-name shared attribute, "
                "cardinality_ratio=%.4f). Avoids collapsing distinct people who "
                "share this value; column remains a blocking candidate.",
                p.name, p.cardinality_ratio,
            )
            continue

        mf = MatchkeyField(
            field=p.name,
            scorer=scorer,
            weight=weight,
            transforms=transforms,
        )

        # #1207 PR2a: arm the data-driven name-frequency downweight. The
        # name_freq_weighted_jw scorer applies a per-dataset common-value
        # downweight ONLY when handed a frequency table; without this it falls
        # back to the static US-Census surname path. Build the table from the
        # SAME (df, transforms) the scorer sees at score time: find_fuzzy_matches
        # scores apply_transforms(value, field.transforms), and these transforms
        # include `lowercase`, which neutralizes the `name_proper` standardizer
        # auto-config also emits (title-case only) -- so raw-df-plus-transforms
        # keys align byte-for-byte with the post-standardization scored values.
        # Skip when the table has no signal (<2 distinct values) or df is absent.
        if (
            scorer == "name_freq_weighted_jw"
            and df is not None
            and _tf_name_weighting_enabled()
        ):
            from goldenmatch.core.tf_tables import value_frequencies  # noqa: PLC0415

            _tf = value_frequencies(df, p.name, transforms)
            if len(_tf) >= 2:
                mf.tf_freqs = _tf

        if scorer == "exact":
            exact_fields.append(mf)
        else:
            fuzzy_fields.append(mf)

    # Aggregate warning: if every exact-eligible column was filtered out,
    # explain which ones and why. This is the load-bearing surface that tells
    # a notebook user their auto-config silently degraded to fuzzy-only.
    _exact_eligible = [
        p for p in profiles
        if p.col_type not in ("numeric", "date", "description")
        and _SCORER_MAP.get(p.col_type, (None,))[0] == "exact"
    ]
    if _exact_eligible and not exact_fields:
        if skipped_exact:
            detail = "; ".join(f"{col} ({why})" for col, why in skipped_exact)
        else:
            # All exact-eligible columns were filtered before reaching the
            # named skip paths above — e.g. by a source-overlap check, a
            # dropped profile, or a scorer_info lookup miss. Surface this
            # shape loudly so a future refactor that starts dropping columns
            # silently can be noticed.
            eligible_names = ", ".join(p.name for p in _exact_eligible)
            detail = (
                f"no per-column reason captured — eligible columns "
                f"({eligible_names}) were filtered before reaching the "
                f"exact-matchkey skip paths"
            )
        logger.warning(
            "All %d exact-eligible columns were excluded by auto-config guards "
            "(%s). Falling back to fuzzy-only matchkeys — if any of these "
            "columns actually are identifiers, provide an explicit config.",
            len(_exact_eligible), detail,
        )

    matchkeys = []

    # Exact matchkey from exact fields
    if exact_fields:
        for f in exact_fields:
            matchkeys.append(MatchkeyConfig(
                name=f"exact_{f.field}",
                type="exact",
                fields=[MatchkeyField(
                    field=f.field,
                    transforms=f.transforms,
                )],
            ))

    # Composite "strong identity" matchkey (NCVR recall fix).
    # Individual name/year columns rarely clear the cardinality>=0.5 exact gate
    # (and year is blocking-only), so person data degrades to a single weighted
    # matchkey that averages every field — one corrupted field (e.g. an
    # abbreviated address) then sinks an otherwise-clean true pair. Their
    # COMBINATION is highly unique, so an exact matchkey on name+dob recovers
    # those pairs WITHOUT loosening any fuzzy scorer (keeps DQbench precision).
    # Matchkeys are OR'd, so this only adds candidate pairs.
    _name_fields = sorted(
        [p for p in profiles if p.col_type == "name" and p.null_rate < 0.3],
        key=lambda p: p.cardinality_ratio, reverse=True,
    )[:2]
    _date_fields = [
        p for p in profiles
        if p.col_type in ("year", "date") and p.null_rate < 0.3
    ]
    # Gate on a DOB/year anchor: a name+date combination is a real identity
    # signal, but names ALONE collide heavily (measured on DQbench tier3:
    # first+last keys pile up to max-group-13 / 85% of rows in multi-record
    # groups, vs name+DOB on NCVR at max-group-4). Requiring a date anchor
    # keeps the composite from manufacturing false merges on name-collision
    # -heavy / adversarial data that has no DOB to disambiguate.
    if len(_name_fields) >= 1 and len(_date_fields) >= 1:
        composite_fields = [
            MatchkeyField(field=p.name, transforms=["lowercase", "strip"])
            for p in (_name_fields + _date_fields)
        ]
        matchkeys.append(MatchkeyConfig(
            name="exact_identity",
            type="exact",
            fields=composite_fields,
        ))
        # Phonetic sibling (#491 heuristic path): same name+DOB composite but
        # with a `soundex` transform on the name fields, so phonetically-equal
        # spellings (Smith/Smyth, Catherine/Katherine) that the EXACT composite
        # misses still match — provided the DOB anchor agrees. name-only soundex
        # keys collide far too much to be an identity claim, but soundex(name)+DOB
        # stays specific (adversarial data without a DOB gets neither composite).
        # OR'd in, so it only adds candidate pairs.
        #
        # SCALE GATE (#510): require a SPECIFIC anchor (enough distinct values),
        # not just a date-typed one. soundex collapses the name's cardinality to a
        # bounded code space, so anchoring on a low-cardinality YEAR (~65-130
        # distinct values) leaves soundex(name)+year non-specific. The spurious
        # cross-cluster matches it manufactures grow like n_rows^2 / selectivity,
        # so on a year anchor it stays invisible on a small sample but DEGRADES
        # PRECISION and explodes soundex block sizes (-> OOM) as the dataset grows
        # (#510 audit: phonetic+year precision fell 0.91->0.82 over 1K->1M and
        # OOM'd at 25M). A full DOB stays specific. The name classifier labels a
        # year column "date" too (the column name matches the date pattern), so
        # col_type alone can't tell year from DOB -- gate on the anchor's distinct
        # count (via the sample df; cardinality_ratio fallback when df is None).
        # The EXACT-name composite above is unaffected (real names stay specific);
        # year-anchored data still gets exact_identity + the fuzzy matchkey, just
        # not the unscalable soundex identity claim.
        def _anchor_specific(p: ColumnProfile) -> bool:
            if df is not None and p.name in df.columns:
                return int(df[p.name].n_unique()) >= 150
            return p.cardinality_ratio >= 0.2  # sample year ~0.05, DOB ~0.5+
        _phonetic_anchor = [p for p in _date_fields if _anchor_specific(p)]
        if _phonetic_anchor:
            phonetic_fields = [
                MatchkeyField(field=p.name, transforms=["lowercase", "strip", "soundex"])
                for p in _name_fields
            ] + [
                MatchkeyField(field=p.name, transforms=["lowercase", "strip"])
                for p in _phonetic_anchor
            ]
            matchkeys.append(MatchkeyConfig(
                name="phonetic_identity",
                type="exact",
                fields=phonetic_fields,
            ))

    # Dual-composite: also emit a name+DOB composite keyed on person-name-PATTERN
    # columns (given_name/surname/first/last) when they exist and differ from the
    # cardinality pick above. The data classifier sometimes mislabels address
    # columns as col_type="name" (Febrl3: address_1/address_2 outrank the real
    # names by cardinality), so the cardinality composite can key on addresses.
    # Different datasets corrupt different fields (Febrl3 mangles names, NCVR
    # mangles addresses), so anchoring on BOTH field-sets means a true pair clean
    # on EITHER matches. Same DOB gate; OR'd, so it only adds candidate pairs.
    _pattern_name_fields = sorted(
        [p for p in profiles if p.null_rate < 0.3 and _classify_by_name(p.name) == "name"],
        key=lambda p: p.cardinality_ratio, reverse=True,
    )[:2]
    if (_date_fields and _pattern_name_fields
            and {p.name for p in _pattern_name_fields} != {p.name for p in _name_fields}):
        matchkeys.append(MatchkeyConfig(
            name="exact_identity_name",
            type="exact",
            fields=[
                MatchkeyField(field=p.name, transforms=["lowercase", "strip"])
                for p in (_pattern_name_fields + _date_fields)
            ],
        ))

    # Weighted matchkey from fuzzy fields
    all_weighted = list(fuzzy_fields)

    # Add description columns as record_embedding
    if description_columns:
        all_weighted.append(MatchkeyField(
            scorer="record_embedding",
            columns=[p.name for p in description_columns],
            weight=1.0,
            model=None,  # auto-selected later
        ))

    # Limit fuzzy fields to prevent OOM on wide datasets
    # Rank by match utility: cardinality * completeness * string length
    max_fuzzy_fields = 5
    if len(all_weighted) > max_fuzzy_fields:
        profile_lookup = {p.name: p for p in profiles}

        def _field_utility(f: MatchkeyField) -> float:
            if not f.field or f.field not in profile_lookup:
                return f.weight or 0.0
            p = profile_lookup[f.field]
            return p.cardinality_ratio * (1 - p.null_rate) * min(p.avg_len / 20, 1.0)

        all_weighted.sort(key=_field_utility, reverse=True)
        dropped = [f.field for f in all_weighted[max_fuzzy_fields:] if f.field]
        all_weighted = all_weighted[:max_fuzzy_fields]
        logger.info(
            "Truncated fuzzy fields from %d to %d. Dropped: %s",
            len(all_weighted) + len(dropped), max_fuzzy_fields, dropped,
        )

    # Confidence-gated weighting: when a profile's classification confidence
    # is low (<0.5), cap the weight at 0.3 so noisy/ambiguous columns can't
    # dominate a weighted matchkey. Profile lookup is by column name.
    # Ordering note: this cap runs AFTER field-utility truncation above.
    # _field_utility uses f.weight only as a fallback when a profile is
    # missing; more importantly, we don't want the cap to skew the utility
    # ranking used by the truncation step. Reorder at your peril.
    _profile_lookup = {p.name: p for p in profiles}
    for f in all_weighted:
        if f.field is None:
            continue
        prof = _profile_lookup.get(f.field)
        if prof is not None and prof.confidence < 0.5:
            if (f.weight or 0) > 0.3:
                f.weight = 0.3

    if all_weighted:
        threshold = _adaptive_threshold(all_weighted)
        matchkeys.append(MatchkeyConfig(
            name="fuzzy_match",
            type="weighted",
            threshold=threshold,
            fields=all_weighted,
        ))

    # Fallback: if nothing was generated, use all string columns with token_sort
    if not matchkeys:
        string_cols = [p for p in profiles if p.dtype.startswith("String") or p.dtype.startswith("Utf8")]
        if string_cols:
            fields = [
                MatchkeyField(
                    field=p.name,
                    scorer="token_sort",
                    weight=1.0,
                    transforms=["lowercase", "strip"],
                )
                for p in string_cols[:3]  # limit to first 3
            ]
            matchkeys.append(MatchkeyConfig(
                name="fallback_fuzzy",
                type="weighted",
                threshold=0.80,
                fields=fields,
            ))

    return matchkeys


# ── Strong-identifier blocking-union (#1207) ──────────────────────────────
# Coverage is restored by the OR across passes, so a per-id pass is admitted on
# a minimal non-null population floor (NOT a null ceiling) — that keeps high-null
# phone/zip passes that each block only the rows that *have* that id. Scale-safety
# is enforced by the caller via _gate_passes, so it is NOT checked here. Strong-id
# col_types reuse the module-level `_STRONG_EXACT_TYPES` (identifier/email/phone).
_BLOCKING_UNION_COVERAGE_TARGET = 0.95
_UNION_PASS_MIN_NONNULL = 0.02  # a pass must block more than a trivial handful


def _union_coverage(df: pl.DataFrame, pass_field_lists: list[list[str]]) -> float:
    """Fraction of rows non-null on at least one pass's fields (OR across passes).
    A multi-field pass requires ALL its fields non-null (it can't block a row
    missing any component)."""
    if df.height == 0:
        return 0.0
    covered = pl.repeat(False, df.height, eager=True)
    for fields in pass_field_lists:
        present = pl.repeat(True, df.height, eager=True)
        for f in fields:
            if f not in df.columns:
                present = pl.repeat(False, df.height, eager=True)
                break
            present = present & df[f].is_not_null()
        covered = covered | present
    return float(covered.sum()) / df.height


def _build_strong_identifier_union(
    profiles: list[ColumnProfile],
    df: pl.DataFrame,
    *,
    n_rows_full: int | None = None,
) -> BlockingConfig | None:
    """Emit a multi_pass UNION of one pass per strong id + name+geo, or None.

    Returns None unless >=1 strong-id pass is present AND >=2 distinct passes
    survive AND their OR-coverage clears _BLOCKING_UNION_COVERAGE_TARGET. The
    >=1 strong-id requirement keeps this from emitting a name-only "strong-id
    union" (a name-only shape is left to the existing name-fallback path).
    Caller (build_blocking) is responsible for invoking this only on the
    fall-through (no single key passed the strict 0.20 ceiling).

    `n_rows_full` is reserved for call-site signature parity; scale-safety is
    enforced by the caller via `_gate_passes`, not here."""
    def _nonnull(col: str) -> float:
        return 1.0 - (df[col].null_count() / df.height) if df.height else 0.0

    candidate_passes: list[list[str]] = []

    # one pass per strong-identifier field, above the non-null population floor.
    # No scale-safety check here — _gate_passes at the call site enforces #715.
    strong_id_passes = 0
    for p in profiles:
        if p.col_type in _STRONG_EXACT_TYPES and p.name in df.columns:
            if _nonnull(p.name) < _UNION_PASS_MIN_NONNULL:
                continue
            # #876 surrogate guard: a perfect-surrogate id (card_ratio >= 1.0)
            # makes singleton blocks (0 pairs) — exclude. NOTE: do NOT apply
            # blocking_max_ratio here; the union exists precisely to use
            # near-unique-but-repeating ids the single-key gate rejects.
            if (p.cardinality_ratio or 0.0) >= 1.0:
                continue
            candidate_passes.append([p.name])
            strong_id_passes += 1

    # require >=1 strong-id pass; a name-only shape belongs to the name fallback.
    if strong_id_passes < 1:
        return None

    # name+geo passes for rows missing every strong id
    name_cols_local = [p for p in profiles if _classify_by_name(p.name) == "name"]
    first = next((p.name for p in name_cols_local if "first" in p.name.lower()), None)
    last = next((p.name for p in name_cols_local if "last" in p.name.lower()
                 or "surname" in p.name.lower()), None)
    geo = next((p.name for p in profiles if p.col_type in ("zip", "geo")), None)
    if first and last:
        candidate_passes.append([first, last])
    if last and geo:
        candidate_passes.append([last, geo])

    if len(candidate_passes) < 2:
        return None
    if _union_coverage(df, candidate_passes) < _BLOCKING_UNION_COVERAGE_TARGET:
        return None

    def _transforms_for(fields: list[str]) -> list[str]:
        prof = next((p for p in profiles if p.name == fields[0]), None)
        return ["lowercase", "strip"] if prof and prof.col_type == "email" else ["strip"]

    passes = [BlockingKeyConfig(fields=f, transforms=_transforms_for(f))
              for f in candidate_passes]
    return BlockingConfig(
        keys=[passes[0]],
        strategy="multi_pass",
        passes=passes,
        skip_oversized=True,
    )


# ── Compound blocking helpers ─────────────────────────────────────────────


def _build_compound_blocking(
    profiles: list[ColumnProfile],
    df: pl.DataFrame,
    max_safe_block: int,
    max_null_rate: float,
) -> BlockingConfig | None:
    """Try to build compound blocking keys when single columns are all oversized.

    Uses greedy refinement: pick the best single column, then find the second
    column that reduces max block size the most. Generates multi-pass compound
    keys for recall.

    Returns None if no compound pair brings blocks below max_safe_block.
    """
    def _null_rate(col_name: str) -> float:
        return df[col_name].null_count() / df.height if df.height > 0 else 0.0

    def _max_block_size(col_name: str) -> int:
        """Largest group size when blocking on this column."""
        return int(df.group_by(col_name).len().get_column("len").max() or 0)  # pyright: ignore[reportArgumentType]  # polars max() typed as PythonLiteral; "len" column is int64 at runtime

    def _nonnull_ratio(col_name: str) -> float:
        """Distinct/non-null ratio -- the TRUE per-record uniqueness, not the
        null-deflated ColumnProfile.cardinality_ratio. A near-1.0 value means a
        surrogate-key-like column (npi/phone/email) whose only big "block" is
        its null bucket -- useless as a blocking component."""
        nn = df[col_name].drop_nulls()
        n = nn.len()
        return (nn.n_unique() / n) if n > 0 else 1.0

    # #715: judge compound COMPONENTS by whether they BOUND block size (i.e.
    # actually group records) and aren't surrogate keys -- NOT by col_type.
    # A sparse zip5 reclassifies `numeric -> identifier` and (at ~50% null)
    # exceeds the single-key null ceiling, so the old col_type/null filters
    # doubly excluded it, leaving only oversized name columns. As a compound
    # COMPONENT, a high-null column is fine: the multi_pass config's other
    # passes cover the null rows. So:
    #   - keep `numeric`/`date` excluded;
    #   - admit `identifier`, `zip`, and the high-cardinality `email`/`phone`
    #     types ONLY when the column genuinely GROUPS records: non-singleton
    #     blocks (`_max_block_size > 1`), `cardinality_ratio < 1.0`, and a
    #     non-null distinct ratio below the blocking gate (rejects surrogate keys
    #     like npi/phone/email whose non-null values are ~unique per record and
    #     whose only large block is the null bucket);
    #   - relax the single-key null ceiling to 0.6 for the component role so a
    #     ~50%-null zip5 qualifies.
    # `zip` is admitted here (not just `identifier`) so the choice doesn't depend
    # on whether the sampling artifact promoted the column to `identifier`: the
    # MEASURED guards decide. A moderate-cardinality zip (true distinct ratio
    # ~0.3) that pairs with a name to bound the block is a strong compound
    # component regardless of its type label; a near-unique surrogate zip is
    # still rejected by the non-null-ratio guard. This decouples blocking-key
    # selection from the over-eager identifier classification (S2a corrected the
    # latter; this aligns blocking with the comment's stated intent: judge by
    # whether a component GROUPS records, NOT by col_type).
    # NOTE: `_nonnull_ratio` / `_max_block_size` are computed on `df` directly.
    # On the v0 non-distributed path `df` IS the full dataset (controller passes
    # the full frame to `_initial_config`), so these are exact. If a SAMPLED df
    # is ever fed here (distributed path), the unprojected non-null ratio is
    # sample-inflated and would wrongly reject a mid-cardinality column -- such a
    # caller must Chao1-project the ratio (see scale_cardinality_ratio_to_full_population).
    # Tracked as a distributed-path follow-up; out of scope for #715 (single-node).
    from goldenmatch.core.blocking_candidates import _blocking_max_ratio
    _grouping_ratio_max = _blocking_max_ratio()
    _component_null_ceiling = max(max_null_rate, 0.6)
    _high_card_types = ("identifier", "zip", "email", "phone")

    def _is_admissible(p: ColumnProfile) -> bool:
        if p.col_type in ("numeric", "date"):
            return False
        if _check_source_overlap(df, p.name) <= 0.0:
            return False
        if p.col_type in _high_card_types:
            # Surrogate-key / near-unique guard: must actually group records.
            if not (
                _max_block_size(p.name) > 1
                and p.cardinality_ratio < 1.0
                and _nonnull_ratio(p.name) < _grouping_ratio_max
            ):
                return False
            # High-null is OK for a compound component (other passes cover nulls).
            return _null_rate(p.name) <= _component_null_ceiling
        # Low-cardinality types (name/string/geo/...): keep the stricter ceiling.
        return _null_rate(p.name) <= max_null_rate

    candidates = [p for p in profiles if _is_admissible(p)]
    if len(candidates) < 2:
        return None

    # Sort by cardinality descending — best single column first
    candidates.sort(key=lambda p: df[p.name].n_unique(), reverse=True)
    best = candidates[0]

    # Test compound pairs: best + each other candidate (up to 5)
    pair_results: list[tuple[ColumnProfile, int]] = []
    for other in candidates[1:6]:
        try:
            max_block = int(df.group_by([best.name, other.name]).len().get_column("len").max() or 0)  # pyright: ignore[reportArgumentType]  # polars max() typed as PythonLiteral; "len" column is int64 at runtime
            pair_results.append((other, max_block))
            logger.debug(
                "Compound pair [%s, %s]: max_block=%d",
                best.name, other.name, max_block,
            )
        except Exception:
            continue

    if not pair_results:
        return None

    # Sort by max block ascending — smallest (safest) first
    pair_results.sort(key=lambda x: x[1])
    winner, winner_block = pair_results[0]

    if winner_block > max_safe_block:
        logger.info(
            "Best compound pair [%s, %s] still produces blocks of %d (> %d). "
            "No compound key is safe enough.",
            best.name, winner.name, winner_block, max_safe_block,
        )
        return None

    logger.info(
        "Compound blocking: [%s, %s] -> max_block=%d",
        best.name, winner.name, winner_block,
    )

    # Build multi-pass config for recall
    passes = [
        # Pass 1: winning compound pair
        BlockingKeyConfig(fields=[best.name, winner.name], transforms=["lowercase", "strip"]),
    ]

    # Pass 2: runner-up compound pair (if different and safe)
    if len(pair_results) > 1:
        runner_up, runner_up_block = pair_results[1]
        if runner_up_block <= max_safe_block and runner_up.name != winner.name:
            passes.append(
                BlockingKeyConfig(fields=[best.name, runner_up.name], transforms=["lowercase", "strip"]),
            )

    # Pass 3: recall-focused single-column soundex (relies on skip_oversized)
    passes.append(
        BlockingKeyConfig(fields=[best.name], transforms=["lowercase", "soundex"]),
    )

    return BlockingConfig(
        keys=[passes[0]],
        strategy="multi_pass",
        passes=passes,
        max_block_size=max_safe_block,
        skip_oversized=True,
    )


def _call_llm_for_blocking(prompt: str, provider: str) -> str:
    """Call LLM API for blocking key suggestion. Returns raw response text.

    Uses stdlib urllib (same pattern as llm_scorer.py) — no external deps.
    """
    import json as _json
    import os
    import urllib.request

    _MODELS = {"openai": "gpt-4o-mini", "anthropic": "claude-haiku-4-5-20251001"}
    model = os.environ.get("GOLDENMATCH_LLM_MODEL", _MODELS.get(provider, ""))

    if provider == "openai":
        api_key = os.environ.get("OPENAI_API_KEY", "")
        body = _json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "max_tokens": 500,
            "response_format": {"type": "json_object"},
        }).encode()
        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=body,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = _json.loads(resp.read())
        return data["choices"][0]["message"]["content"]

    elif provider == "anthropic":
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        body = _json.dumps({
            "model": model,
            "max_tokens": 500,
            "messages": [{"role": "user", "content": prompt}],
        }).encode()
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=body,
            headers={
                "x-api-key": api_key,
                "content-type": "application/json",
                "anthropic-version": "2023-06-01",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = _json.loads(resp.read())
        return data["content"][0]["text"]

    raise ValueError(f"Unknown provider: {provider}")


def _llm_suggest_blocking_keys(
    profiles: list[ColumnProfile],
    df: pl.DataFrame,
    provider: str,
    max_safe_block: int,
) -> BlockingConfig | None:
    """Ask LLM to suggest compound blocking keys, then validate.

    Returns a validated BlockingConfig or None if suggestions are invalid.
    """
    # Build prompt with cardinality stats (all non-numeric columns, including date)
    col_stats = []
    for p in profiles:
        if p.col_type == "numeric":
            continue
        n_unique = df[p.name].n_unique()
        max_block = df.group_by(p.name).len().get_column("len").max()
        col_stats.append(
            f"  {p.name}: type={p.col_type}, {n_unique:,} unique / {df.height:,} rows, "
            f"max_block={max_block:,}"
        )

    prompt = (
        "You are a data deduplication expert. Given these column profiles with cardinality stats:\n"
        + "\n".join(col_stats)
        + f"\n\nDataset: {df.height:,} rows. Max safe block size: {max_safe_block:,}.\n"
        "Suggest 2-3 multi-pass compound blocking key combinations.\n"
        "Each pass: 2 columns that together keep max block under the safe limit.\n"
        "Prioritize recall — different passes should cover different match scenarios "
        "(e.g., same model different location vs same model different year).\n\n"
        'Return JSON: {"passes": [{"fields": ["col_a", "col_b"], "reason": "..."}, ...]}'
    )

    try:
        raw = _call_llm_for_blocking(prompt, provider)
    except Exception as e:
        logger.warning("LLM blocking key suggestion failed: %s", e)
        return None

    # Parse JSON
    import json as _json
    try:
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        data = _json.loads(text)
    except (ValueError, KeyError) as e:
        logger.warning("LLM returned invalid JSON for blocking keys: %s", e)
        return None

    suggested_passes = data.get("passes", [])
    if not suggested_passes:
        logger.warning("LLM returned empty passes list")
        return None

    # Validate each suggestion
    valid_columns = set(df.columns)
    validated_passes: list[BlockingKeyConfig] = []

    for suggestion in suggested_passes:
        fields = suggestion.get("fields", [])
        reason = suggestion.get("reason", "")

        if not all(f in valid_columns for f in fields):
            bad = [f for f in fields if f not in valid_columns]
            logger.info("LLM suggestion rejected — unknown columns: %s", bad)
            continue

        try:
            max_block = int(df.group_by(fields).len().get_column("len").max() or 0)  # pyright: ignore[reportArgumentType]  # polars max() typed as PythonLiteral; "len" column is int64 at runtime
        except Exception:
            logger.info("LLM suggestion rejected — group_by failed for %s", fields)
            continue

        if max_block > max_safe_block:
            logger.info(
                "LLM suggestion [%s] rejected — max_block=%d > %d. Reason: %s",
                fields, max_block, max_safe_block, reason,
            )
            continue

        logger.info(
            "LLM suggestion accepted: [%s] -> max_block=%d. Reason: %s",
            fields, max_block, reason,
        )
        validated_passes.append(
            BlockingKeyConfig(fields=fields, transforms=["lowercase", "strip"])
        )

    if not validated_passes:
        logger.info("All LLM blocking key suggestions were rejected")
        return None

    return BlockingConfig(
        keys=[validated_passes[0]],
        strategy="multi_pass",
        passes=validated_passes,
        max_block_size=max_safe_block,
        skip_oversized=True,
    )


# ── Cross-source overlap ──────────────────────────────────────────────────


def _check_source_overlap(
    df: pl.DataFrame, col: str, partition_col: str = "__source__"
) -> float:
    """Compute value overlap ratio for a column across source partitions.

    Returns |intersection| / |union| of unique values per partition.
    Returns 1.0 if ``partition_col`` is absent or has only one partition
    (no check needed). ``partition_col`` defaults to the internal
    ``__source__`` so existing callers are unchanged; #858 passes a detected
    user source column when there is no ``__source__``.
    """
    if partition_col not in df.columns:
        return 1.0

    sources = df[partition_col].unique().to_list()
    if len(sources) < 2:
        return 1.0

    value_sets = []
    for src in sources:
        vals = set(
            df.filter(pl.col(partition_col) == src)[col]
            .drop_nulls()
            .cast(pl.Utf8)
            .to_list()
        )
        value_sets.append(vals)

    intersection = value_sets[0]
    union = value_sets[0]
    for vs in value_sets[1:]:
        intersection = intersection & vs
        union = union | vs

    if not union:
        return 1.0

    return len(intersection) / len(union)


# ── #858: multi-source over-merge guard ──────────────────────────────────────

def _source_disjoint(df: pl.DataFrame, col: str, partition_col: str) -> bool:
    """True iff no value of ``col`` appears under 2+ distinct partitions.

    The "fully source-specific" signal #858 uses to identify provenance /
    per-source surrogate columns (the source label, per-source ids). Unlike
    ``_check_source_overlap`` (all-partition intersection, which is 0 even for a
    real identifier shared between only SOME of >2 sources), this is a
    max-pairwise test: a genuine shared identifier (the same person appearing in
    two sources with the same value) is NOT disjoint, so it is kept.
    """
    if partition_col not in df.columns:
        return False
    sub = df.select([col, partition_col]).drop_nulls()
    if sub.height == 0:
        return False
    per_value = sub.group_by(col).agg(
        pl.col(partition_col).n_unique().alias("_np")
    )
    max_parts = cast("int | None", per_value["_np"].max())
    return max_parts is None or max_parts <= 1


# Bounded source-indicator name patterns (case-insensitive). NOT a general
# provenance regex (name-regex was rejected as the primary mechanism, spec §1);
# this only LOCATES a user source partition when there is no ``__source__``.
# Deliberately excludes business-attribute-ish names (channel / system / crm)
# that are plausibly real low-card match signal, not data origin -- they were a
# false-positive vector flagged in spec review.
_SOURCE_NAME_RE = re.compile(
    r"(?i)^(source|origin|src|data_source|record_source|lead_source)$|_source$"
)


def _detect_source_partition(
    df: pl.DataFrame, profiles: list[ColumnProfile]
) -> str | None:
    """Return the column that partitions records by origin, or ``None``.

    ``None`` => single-source / match mode / killed => the whole #858 feature is
    a no-op (the regression firewall). See the design spec §0-§1.
    """
    if not _multisource_autoconfig_enabled():
        return None
    if _AUTOCONFIG_MATCH_MODE.get():
        return None
    # 1) internal __source__ with >= 2 distinct values (multi-file dedupe).
    if "__source__" in df.columns and df["__source__"].n_unique() >= 2:
        return "__source__"
    # 2) a name-pattern user column: low cardinality + >= 2 distinct + a
    #    disjoint-id co-signature (>= 1 other column 0-overlap vs it). The
    #    co-signature is the real discriminator; the cardinality cap (absolute
    #    floor of 20, else 10% of rows) excludes free-text "source"-ish fields
    #    while staying robust on small frames.
    other_cols = [p.name for p in profiles if not p.name.startswith("__")]
    card_cap = max(20, int(0.1 * df.height))
    for p in profiles:
        if p.name.startswith("__") or not _SOURCE_NAME_RE.search(p.name):
            continue
        n_unique = df[p.name].n_unique()
        if n_unique < 2 or n_unique > card_cap:
            continue
        has_cosig = any(
            other != p.name and _source_disjoint(df, other, p.name)
            for other in other_cols
        )
        if has_cosig:
            return p.name
    return None


def _source_correlated_exclusions(
    df: pl.DataFrame,
    profiles: list[ColumnProfile],
    partition: str | None,
) -> set[str]:
    """Columns to exclude from ALL match features when multi-source (spec §3).

    = {the user source-indicator column} U {columns 0-overlap across sources}.
    Computed on the FULL frame so ``== 0.0`` is exact. Empty when no partition.
    ``__source__`` is never added (``profile_columns`` already skips dunder
    columns, so it never reaches the matchkeys).
    """
    if partition is None:
        return set()
    exclude: set[str] = set()
    if not partition.startswith("__"):
        exclude.add(partition)
    for p in profiles:
        col = p.name
        if col.startswith("__") or col == partition:
            continue
        if _source_disjoint(df, col, partition):
            exclude.add(col)
    return exclude


# ── Blocking generation ────────────────────────────────────────────────────

def _make_quality_column_profile(
    autoconfig_profile: ColumnProfile,
    n_rows: int,
) -> Any:
    """Bridge ``autoconfig.ColumnProfile`` -> ``quality_exclusions.ColumnProfile``.

    The two dataclasses share ``cardinality_ratio`` + ``null_rate`` +
    ``dtype`` but autoconfig's version doesn't carry ``distinct_count``
    or ``mean_string_length``. Project them from the available fields.

    Used to feed ``classify_column_role`` / ``find_composite_blocking_keys``
    from inside ``build_blocking`` without a cross-module refactor.
    """
    from goldenmatch.core.quality_exclusions import (
        ColumnProfile as _QualityColumnProfile,
    )
    distinct = max(int(autoconfig_profile.cardinality_ratio * max(n_rows, 1)), 1)
    return _QualityColumnProfile(
        cardinality_ratio=autoconfig_profile.cardinality_ratio,
        null_rate=autoconfig_profile.null_rate,
        distinct_count=distinct,
        dtype=autoconfig_profile.dtype,
        mean_string_length=(
            autoconfig_profile.avg_len if autoconfig_profile.avg_len > 0 else None
        ),
    )


def _pick_date_blocking_col(
    profiles: list[ColumnProfile],
    null_rate_fn: Callable[[str], float],
    *,
    max_null_rate: float = 0.20,
) -> str | None:
    """#438: pick a date column suitable as a blocking pass.

    Returns the first profile whose ``col_type == "date"`` (or
    name-classified as a date by `_classify_by_name`) and whose null
    rate is <= `max_null_rate`. Date-only blocking catches pairs that
    share birthdates but disagree on geo+name fields, which is the
    dominant recall-loss case on synthetic-typo data (Febrl3).

    Returns None when no date column qualifies.
    """
    for p in profiles:
        is_date_by_type = getattr(p, "col_type", None) == "date"
        is_date_by_name = _classify_by_name(p.name) == "date"
        if not (is_date_by_type or is_date_by_name):
            continue
        try:
            if null_rate_fn(p.name) > max_null_rate:
                continue
        except Exception:
            continue
        return p.name
    return None


def _degenerate_blocking_config(max_safe_block: int) -> BlockingConfig:
    """Empty-keys blocking config: the #715 degenerate signal.

    Emitted when every candidate blocking key/pass projects oversized at
    full N. Empty ``keys`` is exactly what the controller's #417 degenerate
    guard inspects (``not blocking.keys`` at ``>= REFUSE_AT_N``), so it
    refuses loudly rather than shipping a candidate-pair bomb. ``auto_suggest``
    keeps the model valid (the validator bypasses the keys-required check for
    auto_suggest) and matches the established empty-keys pattern used by the
    CLI / preview paths.

    Note: the #417 guard refuses on empty keys only when the committed profile
    is RED (``_no_blocking_keys AND _profile_red``); a GREEN/YELLOW profile
    with empty keys would not refuse on that branch (unconditional RED refusal
    is handled separately by the ``allow_red_config`` work).
    """
    return BlockingConfig(
        keys=[],
        auto_suggest=True,
        max_block_size=max_safe_block,
        skip_oversized=True,
    )


# --- Quality-aware blocking (GoldenCheck -> GoldenMatch door #1) -------------
# Spec: docs/design/2026-06-07-quality-aware-blocking-design.md
#
# Edit-distance value variants that survive lowercase/strip ("Californa" vs
# "California") shard true duplicates into different exact blocks -> recall lost
# before scoring. GoldenCheck's per-column fuzziness (via core.quality.
# blocking_risk) lets us ADD a fuzzy-tolerant pass for an affected blocking key
# so the variants co-block. Purely additive (the original key is retained), so
# recall can only rise; precision is unchanged (scoring still decides matches).

# A column is "fuzzy enough to matter" at >= 2% variant rows.
_BLOCKING_RISK_NORMALIZE = 0.02
# Transforms already tolerant of edit-distance variants (don't double up).
_FUZZY_TOLERANT_TRANSFORMS = ("soundex", "metaphone")


def _quality_aware_blocking_enabled() -> bool:
    """v1 is OFF by default (spec §7) until the Febrl/DBLP-ACM/NCVR sweep proves
    recall-up / no-precision-regression. Opt in with
    ``GOLDENMATCH_QUALITY_AWARE_BLOCKING=1`` (the #662 kill-switch pattern)."""
    val = os.environ.get("GOLDENMATCH_QUALITY_AWARE_BLOCKING")
    return val is not None and val.lower() in ("1", "true", "yes", "on")


def _has_fuzzy_tolerant_transform(transforms: list[str]) -> bool:
    return any(
        t in _FUZZY_TOLERANT_TRANSFORMS or t.startswith("substring:") for t in transforms
    )


def _fuzzy_pass_transforms(col_type: str) -> list[str]:
    # Phonetic for names (catches early-character typos like Jon/John); a prefix
    # block for everything else (catches typos after a shared prefix and reuses
    # an existing transform).
    if col_type == "name":
        return ["lowercase", "soundex"]
    return ["lowercase", "strip", "substring:0:6"]


def apply_quality_aware_blocking(
    blocking: BlockingConfig | None,
    profiles: list[ColumnProfile],
    df: pl.DataFrame,
    *,
    enabled: bool | None = None,
) -> BlockingConfig | None:
    """Augment a blocking config with fuzzy-tolerant passes for columns
    GoldenCheck flags as edit-distance-fuzzy. Fail-open + additive: returns the
    config unchanged when disabled, goldencheck is absent, the data is clean, or
    the strategy isn't one we can safely extend (only ``static`` / ``multi_pass``
    -- the common auto-config outputs)."""
    if blocking is None:
        return blocking
    if enabled is None:
        enabled = _quality_aware_blocking_enabled()
    if not enabled or blocking.strategy not in ("static", "multi_pass"):
        return blocking

    from goldenmatch.core.quality import blocking_risk

    risk = blocking_risk(df)
    if not risk:
        return blocking

    col_type = {p.name: p.col_type for p in profiles}
    existing = list(blocking.passes or []) + list(blocking.keys or [])
    if not existing:
        return blocking

    # Fields already covered by a fuzzy-tolerant transform need no extra pass.
    already_tolerant = {
        f for kc in existing if _has_fuzzy_tolerant_transform(kc.transforms) for f in kc.fields
    }

    new_passes: list[BlockingKeyConfig] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for kc in existing:
        for fld in kc.fields:
            if fld in already_tolerant or risk.get(fld, 0.0) < _BLOCKING_RISK_NORMALIZE:
                continue
            transforms = _fuzzy_pass_transforms(col_type.get(fld, "string"))
            sig = (fld, tuple(transforms))
            if sig in seen:
                continue
            seen.add(sig)
            new_passes.append(BlockingKeyConfig(fields=[fld], transforms=transforms))

    if not new_passes:
        return blocking

    # Convert to an explicit multi_pass union: original keys/passes become passes
    # (so they survive `auto_select`, which otherwise picks a single key), plus
    # the fuzzy-tolerant passes. Every other config field is preserved.
    return blocking.model_copy(update={
        "strategy": "multi_pass",
        "passes": existing + new_passes,
        "keys": [],
    })


# ── Auto-enabled semantic blocking (door: GOLDENMATCH_AUTO_SEMANTIC_BLOCKING) ──
#
# #1090: when the data is text-heavy (a long free-text column the lexical /
# structured blocking keys under-cover) AND a semantic embedder is reachable,
# route blocking to SimHash over embeddings -- ANN-style candidate generation
# that co-blocks records that mean the same thing but share little surface text.
# Honest fallback: when no embedder is reachable this is a NO-OP (the lexical /
# structured scheme stands), never a silent degrade to a broken semantic path.
# DEFAULT ON (#1090): it fires for text-heavy data WHEN an embedder is reachable;
# absent an embedder the no-op fallback means a user without the in-house model /
# a configured provider still sees byte-identical output. Disable with
# GOLDENMATCH_AUTO_SEMANTIC_BLOCKING=0; tune recall with
# GOLDENMATCH_SEMANTIC_BLOCKING_THRESHOLD (the SimHash band/row split is derived
# from it). The SimHash band-hashing itself runs on the native Rust sketch kernel
# (the source of truth) by default -- see core/sketch.py + _native_loader.

# A column must average at least this many characters to count as "text-heavy"
# (free-text / description-like, where embeddings beat lexical keys). Short
# identity fields (name / email / zip) never qualify.
_SEMANTIC_TEXT_MIN_AVG_LEN = 40.0

# Strategies that ARE already a semantic / near-dup candidate generator -- never
# override these (they were chosen deliberately upstream).
_ALREADY_SEMANTIC_STRATEGIES = frozenset({"simhash", "ann", "ann_pairs", "lsh"})


@dataclass
class SemanticBlockingDecision:
    """Why auto semantic blocking did (not) fire -- surfaced for telemetry."""

    enabled: bool
    column: str | None
    reason: str
    embeddings_available: bool


def _text_heavy_columns(profiles: list[ColumnProfile]) -> list[ColumnProfile]:
    """Free-text columns long enough that semantic blocking helps."""
    return [
        p
        for p in profiles
        if p.col_type in ("description", "string", "multi_name")
        and p.avg_len >= _SEMANTIC_TEXT_MIN_AVG_LEN
    ]


def _auto_semantic_blocking_enabled() -> bool:
    """Default ON (#1090): text-heavy data routes to SimHash-over-embeddings
    blocking unless explicitly disabled via ``GOLDENMATCH_AUTO_SEMANTIC_BLOCKING``
    in ``{0, false, no, off, disabled}``.

    Default-on is safe because the decision still no-ops whenever no embedder is
    reachable (``decide_semantic_blocking`` -> ``embeddings_unavailable``), so a
    user without the in-house model or a configured provider sees byte-identical
    auto-config output -- the flip only fires when an embedder genuinely exists
    AND the data is text-heavy, which is exactly when semantic blocking helps.
    """
    val = os.environ.get("GOLDENMATCH_AUTO_SEMANTIC_BLOCKING", "").strip().lower()
    return val not in ("0", "false", "no", "off", "disabled")


# Default SimHash recall threshold for auto-enabled semantic blocking (#1090).
# A LOWER threshold -> more bands -> more candidate pairs -> higher recall (more
# permissive co-blocking of records that mean the same thing); HIGHER -> fewer,
# tighter candidates. 0.6 is the recall-leaning default for free-text near-dup
# blocking. The (bands, rows) split is derived from it by SimHashKeyConfig +
# sketch.optimal_bands -- so this is the user-facing recall knob #1090 exposes,
# replacing the old hardcoded num_bands.
_SEMANTIC_BLOCKING_THRESHOLD = 0.6


def _semantic_blocking_threshold() -> float:
    """Recall threshold (in ``(0, 1)``) for auto semantic blocking.

    Env override ``GOLDENMATCH_SEMANTIC_BLOCKING_THRESHOLD``; an absent, malformed
    or out-of-range value falls back to :data:`_SEMANTIC_BLOCKING_THRESHOLD`.
    """
    raw = os.environ.get("GOLDENMATCH_SEMANTIC_BLOCKING_THRESHOLD")
    if raw is None:
        return _SEMANTIC_BLOCKING_THRESHOLD
    try:
        val = float(raw)
    except ValueError:
        return _SEMANTIC_BLOCKING_THRESHOLD
    return val if 0.0 < val < 1.0 else _SEMANTIC_BLOCKING_THRESHOLD


def decide_semantic_blocking(
    profiles: list[ColumnProfile],
    config: GoldenMatchConfig | None = None,
    *,
    enabled: bool | None = None,
) -> SemanticBlockingDecision:
    """Decide whether to auto-enable semantic (ANN / SimHash) blocking.

    Returns a decision carrying an honest ``reason``. ``enabled`` is the door
    gate (defaults to the ``GOLDENMATCH_AUTO_SEMANTIC_BLOCKING`` env flag); the
    embedder-availability check is the honest fallback -- text-heavy data with no
    reachable embedder yields ``enabled=False, reason="embeddings_unavailable"``
    rather than committing a semantic plan that can't run.
    """
    if enabled is None:
        enabled = _auto_semantic_blocking_enabled()
    available = _embedder_available(config)
    if not enabled:
        return SemanticBlockingDecision(False, None, "disabled", available)
    text_cols = _text_heavy_columns(profiles)
    if not text_cols:
        return SemanticBlockingDecision(False, None, "not_text_heavy", available)
    col = max(text_cols, key=lambda p: p.avg_len).name
    if not available:
        return SemanticBlockingDecision(False, col, "embeddings_unavailable", False)
    return SemanticBlockingDecision(True, col, "text_heavy_with_embeddings", True)


def apply_auto_semantic_blocking(
    blocking: BlockingConfig | None,
    profiles: list[ColumnProfile],
    df: pl.DataFrame | None = None,
    config: GoldenMatchConfig | None = None,
    *,
    enabled: bool | None = None,
) -> BlockingConfig | None:
    """Route blocking to SimHash over embeddings when the data is text-heavy and
    an embedder is reachable; otherwise return ``blocking`` unchanged.

    Default OFF (``GOLDENMATCH_AUTO_SEMANTIC_BLOCKING``) -- a no-op then, so the
    auto-config output is byte-identical. Never overrides a scheme that is
    already a semantic / near-dup generator (simhash / ann / lsh).
    """
    decision = decide_semantic_blocking(profiles, config, enabled=enabled)
    if not decision.enabled or decision.column is None:
        return blocking
    if blocking is not None and blocking.strategy in _ALREADY_SEMANTIC_STRATEGIES:
        return blocking
    from goldenmatch.config.schemas import SimHashKeyConfig

    return BlockingConfig(
        strategy="simhash",
        simhash=SimHashKeyConfig(
            column=decision.column,
            num_planes=256,
            threshold=_semantic_blocking_threshold(),
            seed=0,
        ),
    )


# ── #876: scale-invariant blocking helpers ────────────────────────────────────
# These are module-level (not closures) so they're importable by tests and by
# any caller that wants to reason about the budget without instantiating a full
# build_blocking run.

def _blocking_pairs_per_row_budget() -> int:
    """K: max candidate pairs per row a blocking option may project at full N.

    Constant (does NOT scale with N) — keeps the total pair count linear.  The
    scale-invariance knob; kept separate from ``max_safe_block`` (per-block OOM)
    so #715's scorer-matrix protection is untouched.

    Default: 50 (projected avg block ≤ ~101 rows).
    Override: ``GOLDENMATCH_BLOCKING_PAIRS_PER_ROW`` env var.
    """
    import os
    try:
        return max(1, int(os.environ.get("GOLDENMATCH_BLOCKING_PAIRS_PER_ROW", "50")))
    except ValueError:
        return 50


def _project_pairs_per_row(proj_block: int) -> int:
    """Pairs/row a (near-)uniform key contributes at full N.

    A block of B rows makes C(B, 2) pairs spread over B rows → (B-1)/2 per row.
    Uses the projected MAX block size (conservative on skew — over-counts rather
    than under-counts, so the budget gate stays safe).
    """
    return max(0, (int(proj_block) - 1) // 2)


# #876: a blocking key's block size at full N depends on whether its cardinality
# is BOUNDED (a closed domain → block grows ∝ N) or UNBOUNDED (an identifier-like
# column whose cardinality grows with N → block stays ~constant). A sample can't
# tell the two apart statistically (both look the same in a small complete
# sample, and a sparse sample of an unbounded key is all-singletons → Chao1
# undefined). So we use the column's SEMANTIC TYPE: a `zip` caps at ~100K
# (5-digit), a `year` at ~300, etc.; names / emails / strings are unbounded.
# This is the same shape of fix as the phonetic-anchor gate (#510): type-aware,
# not statistical. Types NOT listed are treated as unbounded (block constant) —
# the conservative-for-recall default; only KNOWN-bounded domains grow.
_BLOCKING_DOMAIN_CAP: dict[str, int] = {
    "zip": 100_000,   # 5-digit US zip
    "year": 300,      # ~1800-2100
    "month": 12,
    "boolean": 2,
}


def _typed_projected_block(
    col_types: dict[str, str], fields: list[str], sample_block: int, full_n: int,
    sample_n: int, sample_distinct: int,
) -> int:
    """Project a blocking key's full-N max block size.

    Three regimes, chosen so the projection is right for BOTH a genuinely
    high-cardinality key (block stays constant) AND a concentrated / null-heavy
    key (block grows ∝N) — the two failure modes a type-only rule can't tell apart:

    - **All-BOUNDED components** (every field a known-bounded domain — zip / year /
      month / boolean in ``_BLOCKING_DOMAIN_CAP``) → the domain saturates at the
      *product* of the per-column caps, so the block grows only as
      ``ceil(full_n / domain)``. The leniency that lets ``[zip, birth_year]``
      (joint domain ~30M) stay near-constant where a ∝N projector over-rejects it.
    - **Unbounded component, but the key is NEAR-UNIQUE on the sample** (joint
      distinct ratio ≥ the blocking-max-ratio, i.e. it spreads rows thin — a strong
      identifier like ``member_id``, or a unique-per-cluster name): cardinality
      grows ∝N, so the block stays at its (small) sample size. Constant.
    - **Unbounded component AND concentrated** (joint distinct ratio < the ratio —
      a 50-surname ``last_name``, a 50%-null ``zip5`` whose null bucket is a hot
      partition): the hot/null bucket's MAX block grows ∝N → use the **legacy ∝N
      projection** (``project_max_block_size``). This is the validated #715 OOM
      guard. (#876: a type-only rule returned the constant ``sample_block`` for ANY
      unbounded component, masking a real #715 regression — `[zip5, last_name]`
      emitted oversized; the cardinality split fixes it without re-breaking the
      #491 ``member_id``-wins-over-ANN case that ∝N-for-all over-rejected.)

    ``sample_block`` / ``sample_distinct`` are the max group size and the distinct
    group count on the (sub)sample of ``sample_n`` rows (one group_by yields both).
    """
    from goldenmatch.core.blocking_candidates import (
        _blocking_max_ratio,
        project_max_block_size,
    )
    domain = 1
    all_bounded = True
    for f in fields:
        cap = _BLOCKING_DOMAIN_CAP.get(col_types.get(f, ""))
        if cap is None:
            all_bounded = False
            break
        domain *= cap
    if all_bounded and domain > 0:
        return max(int(sample_block), -(-int(full_n) // domain))  # ceil(full_n/domain)
    # Unbounded component: near-unique ⇒ constant; concentrated ⇒ ∝N.
    joint_ratio = (sample_distinct / sample_n) if sample_n > 0 else 1.0
    if joint_ratio >= _blocking_max_ratio():
        return int(sample_block)  # spreads rows thin → block stays ~constant
    return project_max_block_size(int(sample_block), int(sample_n), int(full_n))


def _scale_safe_bounded_compound(
    candidates: list[ColumnProfile],
    is_scale_safe: Callable[[list[str]], bool],
) -> BlockingKeyConfig | None:
    """AND bounded-domain exact keys into the smallest scale-safe compound.

    #876: called when no SINGLE exact key is scale-safe. Considers only the
    BOUNDED candidates (those with a ``_BLOCKING_DOMAIN_CAP`` entry — zip / year /
    month / boolean); an unbounded key that's scale-safe alone was already kept as
    ``safe_exact`` (an unbounded key whose sample block already exceeds the OOM
    guard isn't bounded-compoundable here either — the name path handles it).
    Adds bounded keys most-selective-first
    (largest domain cap leads, so the compound is dominated by the discriminating
    key) until the running compound passes ``is_scale_safe``. Returns the compound
    ``BlockingKeyConfig`` or None if even the full bounded set doesn't bound the
    block (caller then falls through to the name path).

    Numeric-ish bounded keys: ``["lowercase", "strip"]`` is a safe transform
    chain (lowercase is a no-op on digit strings, normalizes a boolean's case).
    """
    bounded = [p for p in candidates
               if _BLOCKING_DOMAIN_CAP.get(p.col_type) is not None]
    if len(bounded) < 2:
        return None  # need >=2 bounded keys to form a joint-domain compound
    # Largest domain cap first → the most-selective bounded key leads.
    bounded.sort(key=lambda p: _BLOCKING_DOMAIN_CAP[p.col_type], reverse=True)
    fields: list[str] = []
    for p in bounded:
        fields.append(p.name)
        if len(fields) >= 2 and is_scale_safe(fields):
            return BlockingKeyConfig(fields=list(fields), transforms=["lowercase", "strip"])
    return None


def _is_text_corpus(profiles: list[ColumnProfile]) -> bool:
    """True when the data reads as a free-text corpus, not structured records.

    #1082 Phase A: a text corpus is a dataset whose identity signal lives in a
    long free-text (``description``) column with NO usable structured blocking
    key. A high-cardinality ``name``/``multi_name`` column (cardinality_ratio
    >= 0.1) is a usable structured blocking key, so its presence means this is
    structured data that happens to carry a free-text field — NOT a text
    corpus — and the normal exact/name blocking paths handle it. A low-
    cardinality name (a label, a category) can't block usefully, so a
    description-bearing dataset still reads as a corpus.
    """
    has_description = any(p.col_type == "description" for p in profiles)
    if not has_description:
        return False
    has_blockable_name = any(
        p.col_type in ("name", "multi_name") and p.cardinality_ratio >= 0.1
        for p in profiles
    )
    return not has_blockable_name


def _embedder_available(config: GoldenMatchConfig | None = None) -> bool:
    """True when a semantic embedder is reachable for text-corpus blocking.

    Gates the semantic (SimHash/embedding) branch of ``_text_corpus_blocking``:
    True when the in-house embedding model is importable OR the caller configured
    an embedding provider. #1082 Phase A routes both the lexical and the
    embedder-available case to lexical LSH; Phase B fills in the semantic branch.
    """
    from goldenmatch.core.embedder import inhouse_embedding_available
    if inhouse_embedding_available():
        return True
    return bool(getattr(getattr(config, "embedding", None), "provider", None))


def _auto_build_lsh_config(profiles: list[ColumnProfile]) -> BlockingConfig:
    """Build a MinHash/LSH blocking config over the longest description column.

    #1082 Phase A: word-shingle MinHash/LSH (k=2, 128 perms, Jaccard
    threshold 0.5) on the description column with the largest average length —
    the field most likely to carry the near-duplicate lexical signal.
    """
    from goldenmatch.config.schemas import LSHKeyConfig
    descs = [p for p in profiles if p.col_type == "description"]
    col = max(descs, key=lambda p: p.avg_len).name
    return BlockingConfig(strategy="lsh", lsh=LSHKeyConfig(
        column=col, mode="word", k=2, num_perms=128, threshold=0.5, seed=0))


def _text_corpus_blocking(
    profiles: list[ColumnProfile], df: pl.DataFrame | None = None,
    config: GoldenMatchConfig | None = None,
) -> BlockingConfig:
    """Pick the text-corpus blocking strategy.

    Semantic (SimHash over embeddings) when an embedder is reachable, else
    lexical (MinHash/LSH). SimHash buckets cosine-near embeddings, catching
    near-duplicates that share meaning but little surface text — the lexical
    fallback only catches shared shingles. See #1082.
    """
    if _embedder_available(config):
        from goldenmatch.config.schemas import BlockingConfig, SimHashKeyConfig

        col = max(
            (p for p in profiles if p.col_type == "description"),
            key=lambda p: p.avg_len,
        ).name
        return BlockingConfig(
            strategy="simhash",
            simhash=SimHashKeyConfig(column=col, num_planes=256, num_bands=32, seed=0),
        )
    return _auto_build_lsh_config(profiles)


# ─────────────────────────────────────────────────────────────────────────────


# Column-DATA types that are structured (not free text) and can't be sketched.
_NON_SKETCHABLE_COL_TYPES = frozenset({"numeric", "date", "year", "zip", "phone", "geo"})


def sketchable_text_cols(profiles: list[ColumnProfile]) -> list[ColumnProfile]:
    """Columns the throughput tier can sketch on.

    Prefers the semantic text types (description / string / name / multi_name).
    But heterogeneous corpus text (web crawl) is routinely MIS-classified by the
    semantic col_type heuristics -- a long FineWeb/C4 document that embeds street
    names / emails / unique ids lands as "address" / "email" / "identifier", not
    "description" (measured on real FineWeb: a ~3k-char doc column classified
    "address"). So when no semantic text column exists, fall back to any column
    holding substantial free text -- keyed on ``avg_len``, NOT the semantic label
    -- excluding only columns whose DATA is structured. A short identifier
    (``doc_id``) is filtered by the length floor; a long mis-labelled text column
    is not.

    Shared by ``_throughput_blocking`` and ``auto_configure_df``'s early
    validation so the two cannot diverge.
    """
    semantic = [
        p for p in profiles
        if p.col_type in ("description", "string", "name", "multi_name")
    ]
    if semantic:
        return semantic
    return [
        p for p in profiles
        if p.col_type not in _NON_SKETCHABLE_COL_TYPES and p.avg_len >= 50
    ]


def _throughput_blocking(
    profiles: list[ColumnProfile], config: GoldenMatchConfig | None = None
) -> BlockingConfig:
    """Force sketch-then-verify (LSH or SimHash) blocking on the best text column.

    Accepts any text column type (description, string, name, multi_name).
    Routes to ``_text_corpus_blocking`` when description columns are present;
    otherwise builds LSH/SimHash directly on the longest text column.

    Raises ``ThroughputNotApplicableError`` when no text column is found --
    the throughput tier cannot operate without a sketch target.
    """
    from goldenmatch.core.throughput_verify import ThroughputNotApplicableError
    text_cols = sketchable_text_cols(profiles)
    if not text_cols:
        raise ThroughputNotApplicableError(
            "throughput tier requires a text column to sketch on; none found "
            f"(columns: {[(p.name, p.col_type, round(p.avg_len)) for p in profiles]})"
        )
    if any(p.col_type == "description" for p in profiles):
        return _text_corpus_blocking(profiles, None, config)
    if _embedder_available(config):
        from goldenmatch.config.schemas import SimHashKeyConfig
        col = max(text_cols, key=lambda p: p.avg_len).name
        return BlockingConfig(
            strategy="simhash",
            simhash=SimHashKeyConfig(column=col, num_planes=256, num_bands=32, seed=0),
        )
    from goldenmatch.config.schemas import LSHKeyConfig
    col = max(text_cols, key=lambda p: p.avg_len).name
    return BlockingConfig(strategy="lsh", lsh=LSHKeyConfig(
        column=col, mode="word", k=2, num_perms=128, threshold=0.5, seed=0))


def build_blocking(
    profiles: list[ColumnProfile],
    df: pl.DataFrame,
    llm_provider: str | None = None,
    *,
    n_rows_full: int | None = None,
) -> BlockingConfig:
    """Generate blocking config from column profiles.

    Args:
        profiles: per-column profiles from ``profile_columns``.
        df: the (typically sample) frame.
        llm_provider: optional LLM provider for blocking suggestions.
        n_rows_full: full-population row count for the input data.
            When set AND larger than ``df.height``, drives Chao1 sample-
            size correction in the blocking-candidate gate (#410). When
            None, defaults to ``df.height`` (backward-compat for direct
            callers like tests that pass a full-table df).
    """
    # Filter out high-null columns (>20% null) — they create oversized null blocks
    # that cause O(N^2) comparison explosions
    max_null_rate = 0.20

    def _null_rate(col_name: str) -> float:
        return df[col_name].null_count() / df.height if df.height > 0 else 0.0

    # Tier 2 (autoconfig-tier1-tier2): null-aware v0 blocking selection.
    # Columns with null_rate > NULL_RATE_CEILING are skipped as blocking keys:
    # records with null values in the blocking field can't appear in any block,
    # structurally capping recall regardless of how good the scoring is.
    # NOTE: max_null_rate serves as NULL_RATE_CEILING here (value = 0.20).
    NULL_RATE_CEILING = max_null_rate  # 0.20 — explicit alias for Tier 2 intent

    # #408: cardinality-based blocking-candidate gate. The original
    # `cardinality_ratio < 0.95` let near-unique columns (NPI, federal IDs,
    # any column with >86% unique per record) through, producing singleton
    # blocks. The new gate (default 0.5) filters those out; env-overridable
    # via GOLDENMATCH_BLOCKING_MAX_RATIO. NPI keeps being picked as a
    # matchkey upstream — only its blocking role is denied.
    from goldenmatch.core.blocking_candidates import (
        _blocking_max_ratio,
        scale_cardinality_ratio_to_full_population,
    )
    blocking_max_ratio = _blocking_max_ratio()
    # #410: project sample cardinality to full-population. Auto-config
    # profiles run on a small sample; at sample N=1000, real
    # mid-cardinality columns (zip) look near-unique. Chao1 correction
    # uses the sample's distinct count + sample_n vs full_n to project.
    effective_n_full = n_rows_full if n_rows_full is not None else df.height
    sample_n = max(df.height, 1)

    # #1082: ANN is no longer AUTO-selected for description columns. A free-
    # text corpus (description column, no blockable structured key) is routed
    # to MinHash/LSH near-duplicate blocking via ``_is_text_corpus`` /
    # ``_text_corpus_blocking`` below (after the exact-key path). Explicit
    # ``ann``/``ann_pairs`` configs still work, and blocker.py's ANN sub-block
    # fallback inside oversized blocks is unchanged. The old #491 auto-ANN
    # path (ANN_MIN_ROWS gate + embedding-column detection here) was removed.

    def _projected_ratio(p: ColumnProfile) -> float:
        """Sample-corrected cardinality_ratio for the #408/#410 blocking gate.

        #876: TYPE-AWARE projection (same fix as ``_typed_projected_block`` for
        block size). ``scale_cardinality_ratio_to_full_population`` is a Chao1
        unseen-species estimator — it assumes a CLOSED domain and projects the
        cardinality ratio DOWN as N grows (more rows fill in the fixed domain →
        proportionally fewer distinct). That's valid only for a BOUNDED-domain
        key (zip/year/month/boolean). For an UNBOUNDED key (email/name/
        identifier/string) the domain grows WITH N, so a near-unique key stays
        near-unique at scale; Chao1 wrongly drives its ratio toward 0 (email
        0.56 → ~0.00 at 200M), letting a near-surrogate slip past the
        ``blocking_max_ratio`` gate and get picked as the SOLE blocking key —
        which blocks into near-singletons and tanks recall (#876: QIS email
        blocking recall 0.39). Keep an unbounded key at its sample ratio so the
        gate sees it for what it is. BOUNDED keys still Chao1-project (zip's true
        ratio really does fall as the 100K domain saturates)."""
        if effective_n_full <= sample_n:
            return p.cardinality_ratio
        if _BLOCKING_DOMAIN_CAP.get(p.col_type) is None:
            return p.cardinality_ratio  # unbounded domain: ratio does not fall with N
        sample_distinct = max(int(p.cardinality_ratio * sample_n), 1)
        return scale_cardinality_ratio_to_full_population(
            sample_distinct=sample_distinct,
            sample_n_rows=sample_n,
            full_n_rows=effective_n_full,
        )

    exact_cols = [
        p for p in profiles
        if p.col_type in ("email", "phone", "zip", "identifier", "year")
        and _null_rate(p.name) <= NULL_RATE_CEILING  # Tier 2: skip high-null candidates
        and _projected_ratio(p) <= blocking_max_ratio
        and _check_source_overlap(df, p.name) > 0.0
    ]
    # Log columns rejected by the cardinality gate (#408 / #410).
    for p in profiles:
        if p.col_type not in ("email", "phone", "zip", "identifier", "year"):
            continue
        projected = _projected_ratio(p)
        if projected > blocking_max_ratio and projected < 1.0:
            logger.info(
                "Blocking candidate rejected: %r (projected_cardinality=%.3f > %.2f, "
                "sample_n=%d, full_n=%d); would produce near-singleton blocks. "
                "Kept for matchkey consideration. See #408/#410.",
                p.name, projected, blocking_max_ratio, sample_n, effective_n_full,
            )
    # Log skipped columns (cross-source overlap).
    for p in profiles:
        if (p.col_type in ("email", "phone", "zip", "identifier", "year")
                and _null_rate(p.name) <= max_null_rate
                and _projected_ratio(p) <= blocking_max_ratio
                and _check_source_overlap(df, p.name) == 0.0):
            sources = df["__source__"].unique().to_list() if "__source__" in df.columns else []
            logger.warning(
                "Blocking key '%s' has 0%% overlap between sources %s -- skipping",
                p.name, ", ".join(str(s) for s in sources),
            )
    name_cols = [
        p for p in profiles
        if p.col_type == "name"
        and _check_source_overlap(df, p.name) > 0.0
    ]
    text_cols = [p for p in profiles if p.col_type in ("description", "string", "address")]

    # Auto-config's "is this blocking key safe?" threshold. Scales with
    # total_rows so the autoconfig's tolerance for block size grows with
    # the dataset.
    #
    # History: was 1000 historically, a margin set against float64
    # ensemble scorers OOM-ing on big block matrices. PR #173's float32
    # scoring brings a 5K block matrix down to ~100 MB. The fixed 1000
    # caused issue #199 at 2M+: the (state, name) compound block at 2M is
    # ~1.9K rows, "unsafe" under the old threshold, so autoconfig fell
    # through to a single-column soundex fallback. Soundex collapses
    # different surnames into one code, producing ~50K-sized blocks at
    # full scale that the pipeline filtered at max_block_size=5000 —
    # leaving no candidate pairs and ~99% singleton output.
    #
    # Row-count-aware scaling: total_rows // 200, clamped to [1000, 10000].
    # Preserves existing behavior at 200K and below (where 1000 always
    # wins the clamp), bumps to 5000 at 1M (matches the pipeline default),
    # and headroom to 10K for 2M+. Cap at 10K because a 10K-row block
    # under float32 ensemble is ~400 MB per scorer call which is the
    # practical OOM ceiling on a 16 GB runner.
    max_safe_block = max(1000, min(10_000, int(df.height) // 200))

    # #715: gate every emitted blocking key/pass by its PROJECTED full-N max
    # block size. build_blocking runs on a sample (or, in the v0 path, the
    # full df) and the emitted single-column soundex(name) passes had no
    # block-size guard. On a sparse-zip5 healthcare shape, zip5 reclassifies
    # to `identifier` and drops out of the compound, leaving single-name
    # passes whose max block projects to ~50K rows at 1M -> ~39.6M candidate
    # pairs -> an 18-min run. #876 keeps the legacy ∝N projector for keys with an
    # unbounded component (hot-bucket-safe) and adds a type-aware domain-product
    # path for ALL-BOUNDED-domain compounds (see _typed_projected_block).

    _col_types = {p.name: p.col_type for p in profiles}

    def _sample_block_and_distinct(fields: list[str]) -> tuple[int, int]:
        """(max group size, distinct group count) on the sample — one group_by.

        The distinct count drives the #876 cardinality split in
        ``_typed_projected_block`` (near-unique key ⇒ constant block; concentrated
        ⇒ ∝N). On error, return ``(effective_n_full, 1)`` so the key is treated as
        maximally oversized AND concentrated (dropped) — fail-safe.
        """
        try:
            g = df.group_by(fields).len()
            mb = int(g.get_column("len").max() or 0)  # pyright: ignore[reportArgumentType]  # polars max() typed as PythonLiteral; "len" is int64 at runtime
            return mb, int(g.height)
        except Exception:  # pragma: no cover -- defensive
            return effective_n_full, 1

    def _projected_block(fields: list[str]) -> int:
        sample_mb, sample_distinct = _sample_block_and_distinct(fields)
        # #876: ALL-BOUNDED-domain key (zip×year) grows only as full_n/domain (the
        # leniency that lets [zip, birth_year] pass); an unbounded component is
        # constant if near-unique on the sample, else legacy ∝N (hot-bucket-safe).
        return _typed_projected_block(
            _col_types, fields, sample_mb, effective_n_full, sample_n, sample_distinct)

    def _is_scale_safe(fields: list[str]) -> bool:
        # #876: a key is scale-safe iff its candidate-pair count stays LINEAR in
        # N. Regimes from the projection (see _typed_projected_block):
        #   - ALL-BOUNDED-domain key (typed block grows ∝ N/domain, e.g. zip×year):
        #     pairs grow ∝ N^2/domain -> must satisfy the constant pairs-per-row
        #     budget K (block <= ~2K+1). Rejects sole-zip at 100M; admits the
        #     [zip, birth_year] compound (domain ~30M -> tiny block).
        #   - NEAR-UNIQUE unbounded key (member_id): constant small block -> linear
        #     pairs -> safe at any sample block size (per-block OOM guard aside).
        #   - CONCENTRATED unbounded key (50-surname last_name, 50%-null zip5): the
        #     legacy ∝N projection applies and a hot/null bucket projects oversized
        #     -> rejected here (the #715 guard).
        sb, sd = _sample_block_and_distinct(fields)
        pb = _typed_projected_block(
            _col_types, fields, sb, effective_n_full, sample_n, sd)
        if pb > max_safe_block:
            return False  # per-block OOM guard (#715), also kills huge-N bounded keys
        if pb > sb:  # block grows with N (bounded-cardinality / concentrated key)
            return _project_pairs_per_row(pb) <= _blocking_pairs_per_row_budget()
        return True

    def _pass_is_bounded(key: BlockingKeyConfig) -> bool:
        return _is_scale_safe(key.fields)

    def _gate_passes(
        primary: BlockingKeyConfig,
        passes: list[BlockingKeyConfig],
    ) -> tuple[BlockingKeyConfig | None, list[BlockingKeyConfig]]:
        """Drop oversized passes; pick a bounded primary key (#715).

        Returns ``(primary_or_None, surviving_passes)``. The chosen primary is
        the original primary if it is bounded, else the first bounded pass.
        When NOTHING is bounded, the primary is ``None`` (caller emits an
        empty/degenerate config so the controller refuses rather than
        shipping a candidate-pair bomb).
        """
        bounded = [p for p in passes if _pass_is_bounded(p)]
        dropped = [p for p in passes if not _pass_is_bounded(p)]
        if dropped:
            logger.info(
                "Dropping %d oversized blocking pass(es) by projected full-N "
                "block size (> %d, full_n=%d): %s. See #715.",
                len(dropped), max_safe_block, effective_n_full,
                [(p.fields, p.transforms) for p in dropped],
            )
        if _pass_is_bounded(primary):
            return primary, bounded
        if bounded:
            logger.info(
                "Primary blocking key %s projects oversized (> %d); promoting "
                "first bounded pass %s to primary. See #715.",
                primary.fields, max_safe_block, bounded[0].fields,
            )
            return bounded[0], bounded
        logger.warning(
            "All name-fallback blocking keys/passes project oversized at "
            "full_n=%d (> %d max_safe_block); emitting empty (degenerate) "
            "blocking config so the controller refuses. See #715.",
            effective_n_full, max_safe_block,
        )
        return None, []

    # Best case: block on highest-cardinality exact column (with low null rate + safe block size)
    if exact_cols:
        # Pre-filter: only evaluate top 5 by cardinality to avoid expensive group_by on all columns
        exact_cols_sorted = sorted(exact_cols, key=lambda p: df[p.name].n_unique(), reverse=True)
        # #876: drop a unique-per-row SURROGATE (id) before picking the best
        # exact key.  A surrogate creates singleton blocks (block size 1 → 0
        # candidate pairs → finds nothing) and was the degenerate fallback. Gate
        # ONLY on cardinality_ratio >= 1.0 (mirrors the exact-matchkey surrogate
        # guard at ~line 813). Do NOT also drop on projected_block<=1: a unique
        # email in a tiny sample (card_ratio < 1.0) is a legitimate exact
        # blocking key (it repeats in real data / match mode) — dropping it
        # regressed test_blocks_on_exact_column.
        exact_cols_sorted = [
            p for p in exact_cols_sorted
            if (p.cardinality_ratio or 0.0) < 1.0
        ]
        candidates = exact_cols_sorted[:5]
        # #876: keep only scale-safe exact keys (the linear/pairs gate applies to
        # GROWING bounded keys only — see _is_scale_safe). At 100M a `zip` key
        # (bounded ~100K domain) projects a block of ~1000 that GROWS with N, so
        # it's rejected; an unbounded `email`/`name` key with a constant block is
        # kept.
        safe_exact = [p for p in candidates if _is_scale_safe([p.name])]
        if safe_exact:
            best = max(safe_exact, key=lambda p: df[p.name].n_unique())
            transforms = ["lowercase", "strip"] if best.col_type == "email" else ["strip"]
            return BlockingConfig(
                keys=[BlockingKeyConfig(fields=[best.name], transforms=transforms)],
            )
        # #876: no SINGLE exact key is scale-safe. Before dropping these exact
        # discriminators for the name path, try to AND the BOUNDED ones into a
        # compound whose JOINT domain bounds the block under the pairs budget.
        # Two bounded keys that each explode alone (zip ∝N/100K, year ∝N/300) have
        # a joint domain of ~100K·300 = 30M, so their compound's block stays
        # ~constant and the pair count linear. The compound preserves what a sole
        # `zip` gives but can't scale: it co-locates a cluster's variants (both
        # components stable within a cluster) AND separates near-duplicate clusters
        # that share names but differ on the bounded keys (the QIS twin shape;
        # blocking on names alone collides those twins → over-merge). This is the
        # bounded-compound of the #876 design, instantiated as a refinement of the
        # rejected bounded keys rather than a greedy highest-cardinality pick.
        bounded_compound = _scale_safe_bounded_compound(candidates, _is_scale_safe)
        if bounded_compound is not None:
            logger.info(
                "Scale-safe bounded compound blocking: %s (no single exact key "
                "is scale-safe at full_n=%d; ANDed bounded exact keys to bound "
                "the block). See #876.",
                bounded_compound.fields, effective_n_full,
            )
            return BlockingConfig(keys=[bounded_compound], strategy="static")
        # All exact columns create oversized blocks — fall through
        logger.warning(
            "Exact blocking columns all produce oversized blocks (>%d), "
            "falling through to name-based blocking",
            max_safe_block,
        )

    # #1082: text-corpus fallback. We only reach here when no bounded exact
    # blocking key was found (the exact path above returns whenever it had a
    # usable safe_exact key). When the data reads as a free-text corpus (a
    # description column and no blockable structured key), route to MinHash/LSH
    # near-duplicate blocking rather than the name/compound fallback below.
    #
    # NOTE: this REPLACES the prior #491 ANN auto-selection for description
    # columns. ANN is no longer auto-picked here -- a text corpus now gets
    # lexical LSH blocking; explicit ann/ann_pairs configs still work, and the
    # ANN sub-block fallback inside oversized blocks (blocker.py) is unchanged.
    if _is_text_corpus(profiles):
        text_blk = _text_corpus_blocking(profiles, df)
        logger.info(
            "Auto-selecting %s blocking (text corpus): no bounded exact "
            "blocking key and no blockable structured column; routing the "
            "description corpus to near-duplicate blocking. See #1082.",
            text_blk.strategy,
        )
        return text_blk

    # #1207: per-identifier blocking-union. We reach here only when no single
    # exact key passed the strict NULL_RATE_CEILING (0.20) gate — exactly the
    # null-sparse multi-source shape where the compound fallback would build a
    # single-strong-id compound ([last_name, npi]) that caps recall at the id's
    # population. Prefer a UNION of one pass per strong id + name+geo, whose
    # OR-coverage restores the population the single key drops. Emitted before
    # the compound fallback so it wins; falls through unchanged when it can't
    # reach the coverage target or <2 passes survive.
    def _id_pass_scale_safe_nonnull(field: str) -> bool:
        """#1207: scale-safety for a strong-id SINGLETON pass, measured on the
        NON-NULL subframe.

        The runtime static blocker drops null block keys, so a high-null id's
        null bucket (which dominates its raw max block) never actually forms.
        The default ``_pass_is_bounded`` gate measures block size via a
        null-INCLUSIVE ``group_by`` — that null bucket falsely rejects exactly
        the null-sparse strong ids this union exists to add. We still apply the
        SAME full-N projection + ``max_safe_block`` guard (just over the
        non-null rows), so a bounded id (zip) or a mistyped low-cardinality
        ``identifier`` with a genuinely large non-null block is still dropped.
        """
        try:
            sub = df.filter(pl.col(field).is_not_null())
            if sub.height == 0:
                return False
            g = sub.group_by(field).len()
            sb = int(g.get_column("len").max() or 0)  # pyright: ignore[reportArgumentType]
            sd = int(g.height)
        except Exception:  # pragma: no cover -- defensive
            return False
        # Full-N projection unchanged: sample_n / effective_n_full stay the FULL
        # row counts; only the measured block/distinct exclude the null bucket.
        pb = _typed_projected_block(_col_types, [field], sb, effective_n_full, sample_n, sd)
        if pb > max_safe_block:
            return False
        if pb > sb:  # block grows with N (bounded-domain / concentrated key)
            return _project_pairs_per_row(pb) <= _blocking_pairs_per_row_budget()
        return True

    union_cfg = _build_strong_identifier_union(profiles, df, n_rows_full=n_rows_full)
    if union_cfg is not None:
        id_passes: list[BlockingKeyConfig] = []
        other_passes: list[BlockingKeyConfig] = []
        for p in union_cfg.passes or []:
            if len(p.fields) == 1 and _col_types.get(p.fields[0]) in _STRONG_EXACT_TYPES:
                id_passes.append(p)
            else:
                other_passes.append(p)
        # Strong-id singletons: gate on the NON-NULL projected block (the static
        # blocker drops null keys). Name/geo passes: standard #715/#876 gate.
        surviving_ids = [p for p in id_passes if _id_pass_scale_safe_nonnull(p.fields[0])]
        surviving_other = [p for p in other_passes if _pass_is_bounded(p)]
        survivors = surviving_ids + surviving_other
        if surviving_ids and len(survivors) >= 2:
            primary = survivors[0]
            logger.info(
                "Auto-selecting strong-identifier blocking UNION (%d passes, "
                "%d strong-id) on null-sparse data: no single exact key cleared "
                "the 0.20 null ceiling; union OR-coverage restores the dropped "
                "population. Strong-id passes gated on non-null block size "
                "(null keys are dropped at runtime). See #1207.",
                len(survivors), len(surviving_ids),
            )
            return BlockingConfig(
                keys=[primary],
                strategy="multi_pass",
                passes=survivors,
                max_block_size=max_safe_block,
                skip_oversized=True,
            )

    # ── Check if name-based fallback would also be oversized ──
    # #715: the name path picks ONE primary name column (pattern_names[0], else
    # name_cols[0]) and blocks on it. If THAT primary is oversized, single-name
    # blocking is degraded (it gets demoted to soundex/secondary or dropped) --
    # so we should try a bounded compound first, even if some OTHER name column
    # happens to be bounded on its own. Gating on "is the name path's primary
    # oversized" (by the projected full-N size, consistent with _gate_passes)
    # rather than "is EVERY name col oversized" lets the sparse-zip shape reach
    # zip5+last_name instead of degrading to a bare last_name block.
    def _name_path_primary() -> str | None:
        if not name_cols:
            return None
        pattern_names = [p for p in name_cols if _classify_by_name(p.name) == "name"]
        return (pattern_names[0] if pattern_names else name_cols[0]).name

    _primary_name = _name_path_primary()
    _all_single_oversized = True
    if _primary_name is not None:
        try:
            if _projected_block([_primary_name]) <= max_safe_block:
                _all_single_oversized = False
        except Exception:
            pass

    if _all_single_oversized and (name_cols or text_cols):
        # All single columns produce oversized blocks — try compound blocking
        if llm_provider:
            llm_config = _llm_suggest_blocking_keys(profiles, df, llm_provider, max_safe_block)
            if llm_config is not None:
                logger.info("Using LLM-suggested compound blocking keys")
                return llm_config
            logger.info("LLM suggestions invalid or unavailable — trying greedy compound")

        compound_config = _build_compound_blocking(profiles, df, max_safe_block, max_null_rate)
        if compound_config is not None:
            # #715: project the compound's keys/passes to full N before emitting.
            # _build_compound_blocking selects on SAMPLE block size, but a
            # high-null component (e.g. a ~50%-null zip5) hides a large null
            # bucket that scales linearly: zip5+last_name is ~323/block on a
            # 30K sample but projects to ~10K at 1M. Gate it through the same
            # projected guard as the name path; if nothing survives, fall
            # through to single-column fallbacks rather than ship a bomb.
            c_primary = (compound_config.keys or [None])[0]
            c_passes = compound_config.passes or list(compound_config.keys or [])
            if c_primary is not None:
                gated_primary, gated_passes = _gate_passes(c_primary, c_passes)
                if gated_primary is not None:
                    return BlockingConfig(
                        keys=[gated_primary],
                        strategy="multi_pass",
                        passes=gated_passes,
                        max_block_size=max_safe_block,
                        skip_oversized=True,
                    )
            logger.info(
                "Compound blocking config projects oversized at full_n=%d -- "
                "falling through to single-column fallbacks. See #715.",
                effective_n_full,
            )
        else:
            logger.info("Compound blocking failed — falling through to single-column fallbacks")

    # Name columns: use multi-pass with soundex + substring
    # Prefer columns matched by name pattern (person names) over data-profiled names
    if name_cols:
        pattern_names = [p for p in name_cols if _classify_by_name(p.name) == "name"]
        best_name = (pattern_names[0] if pattern_names else name_cols[0]).name

        # Check for geo columns to compound with name — prevents cross-region
        # false positives (e.g., same hospital name in different states)
        geo_cols = [
            p for p in profiles
            if p.col_type == "geo"
            and _null_rate(p.name) <= max_null_rate
        ]
        best_geo = None
        if geo_cols:
            # Pick the geo column that reduces max block size the most
            geo_results: list[tuple[ColumnProfile, int]] = []
            for g in geo_cols:
                try:
                    max_block = df.group_by([g.name, best_name]).len().get_column("len").max()
                    if max_block is not None:
                        geo_results.append((g, int(max_block)))  # pyright: ignore[reportArgumentType]  # polars max() returns PythonLiteral
                except Exception:
                    continue
            if geo_results:
                geo_results.sort(key=lambda x: x[1])
                candidate, candidate_block = geo_results[0]
                if candidate_block <= max_safe_block:
                    best_geo = candidate.name
                    logger.info(
                        "Geo-compound blocking: [%s, %s] -> max_block=%d",
                        best_geo, best_name, candidate_block,
                    )

        # #435: when multiple name columns exist (e.g. Febrl3 has both
        # given_name + surname), add a soundex pass for the SECOND one.
        # Without this, ~24pp of recall is lost on synthetic-typo data
        # where given_name was corrupted but surname stayed intact.
        secondary_name = None
        if len(name_cols) >= 2:
            secondary_candidates = [
                p for p in name_cols if p.name != best_name
            ]
            if secondary_candidates:
                secondary_name = secondary_candidates[0].name

        # #438: when a date column exists with low null rate, add it as
        # an extra blocking pass. Catches pairs that share DOB but
        # disagree on geo+name (typo'd name vs intact DOB) -- this is
        # the dominant recall-loss case on Febrl3-shape synthetic data.
        # Recall: 0.7229 -> 0.95+ on Febrl3 in local measurement.
        date_block_col = _pick_date_blocking_col(profiles, _null_rate)

        # #438: extra surname-substring pass complements the soundex
        # pass on the secondary name. Soundex collapses typos but is
        # noisy on short names; substring catches first-N-letter
        # corruption patterns ("Smith" -> "Smiht").
        secondary_name_substring = (
            secondary_name if secondary_name else None
        )

        if best_geo:
            extra_passes = []
            if secondary_name:
                extra_passes.append(BlockingKeyConfig(
                    fields=[secondary_name], transforms=["lowercase", "soundex"],
                ))
            if secondary_name_substring:
                extra_passes.append(BlockingKeyConfig(
                    fields=[secondary_name_substring],
                    transforms=["lowercase", "substring:0:5"],
                ))
            if date_block_col:
                extra_passes.append(BlockingKeyConfig(
                    fields=[date_block_col],
                    transforms=["lowercase", "strip"],
                ))
            geo_primary = BlockingKeyConfig(
                fields=[best_geo, best_name], transforms=["lowercase", "strip"]
            )
            geo_passes = [
                BlockingKeyConfig(fields=[best_geo, best_name], transforms=["lowercase", "strip"]),
                BlockingKeyConfig(fields=[best_geo, best_name], transforms=["lowercase", "substring:0:5"]),
                BlockingKeyConfig(fields=[best_name], transforms=["lowercase", "soundex"]),
                *extra_passes,
            ]
            primary, gated_passes = _gate_passes(geo_primary, geo_passes)
            if primary is None:
                return _degenerate_blocking_config(max_safe_block)
            return BlockingConfig(
                keys=[primary],
                strategy="multi_pass",
                passes=gated_passes,
                max_block_size=max_safe_block,
                skip_oversized=True,
            )

        extra_passes = []
        if secondary_name:
            extra_passes.append(BlockingKeyConfig(
                fields=[secondary_name], transforms=["lowercase", "soundex"],
            ))
        if secondary_name_substring:
            extra_passes.append(BlockingKeyConfig(
                fields=[secondary_name_substring],
                transforms=["lowercase", "substring:0:5"],
            ))
        if date_block_col:
            extra_passes.append(BlockingKeyConfig(
                fields=[date_block_col],
                transforms=["lowercase", "strip"],
            ))
        name_primary = BlockingKeyConfig(
            fields=[best_name], transforms=["lowercase", "soundex"]
        )
        name_passes = [
            BlockingKeyConfig(fields=[best_name], transforms=["lowercase", "substring:0:5"]),
            BlockingKeyConfig(fields=[best_name], transforms=["lowercase", "soundex"]),
            BlockingKeyConfig(fields=[best_name], transforms=["lowercase", "token_sort", "substring:0:8"]),
            *extra_passes,
        ]
        primary, gated_passes = _gate_passes(name_primary, name_passes)
        if primary is None:
            return _degenerate_blocking_config(max_safe_block)
        return BlockingConfig(
            keys=[primary],
            strategy="multi_pass",
            passes=gated_passes,
            max_block_size=max_safe_block,
            skip_oversized=True,
        )

    # #410: composite-blocking fallback. After exact_cols / name_cols
    # produce no winner, try a 2-column composite from mid-cardinality
    # blocking candidates (zip + last_name on healthcare data). This
    # beats the text_cols canopy / first_string fallback for any
    # structured dataset.
    import dataclasses as _dc

    from goldenmatch.core.blocking_candidates import (
        classify_column_role,
        find_composite_blocking_keys,
    )
    column_roles = []
    for p in profiles:
        if _check_source_overlap(df, p.name) <= 0.0:
            continue
        qcp = _make_quality_column_profile(p, effective_n_full)
        role = classify_column_role(
            qcp,
            sample_n_rows=sample_n,
            full_n_rows=effective_n_full,
        )
        column_roles.append(_dc.replace(role, name=p.name))

    composite = find_composite_blocking_keys(df, column_roles)
    if composite is not None:
        try:
            joint_card = int(df.select(composite).n_unique())
        except Exception:  # pragma: no cover -- defensive
            joint_card = 1
        avg_block = max(effective_n_full // max(joint_card, 1), 1)
        logger.info(
            "Composite blocking: %s -> ~%d rows/block (joint cardinality %d). See #410.",
            composite, avg_block, joint_card,
        )
        return BlockingConfig(
            keys=[BlockingKeyConfig(
                fields=list(composite), transforms=["lowercase"],
            )],
            max_block_size=max_safe_block,
            skip_oversized=True,
        )

    # Last resort: canopy on best text column
    if text_cols:
        from goldenmatch.config.schemas import CanopyConfig
        best_text = text_cols[0].name
        return BlockingConfig(
            keys=[BlockingKeyConfig(fields=[best_text], transforms=["lowercase", "substring:0:5"])],
            strategy="canopy",
            canopy=CanopyConfig(fields=[best_text], loose_threshold=0.3, tight_threshold=0.7),
            skip_oversized=True,
        )

    # Absolute fallback
    first_string = next(
        (p for p in profiles if p.col_type != "numeric"),
        profiles[0] if profiles else None,
    )
    if first_string:
        return BlockingConfig(
            keys=[BlockingKeyConfig(fields=[first_string.name], transforms=["lowercase", "substring:0:5"])],
            skip_oversized=True,
        )

    return BlockingConfig(keys=[BlockingKeyConfig(fields=[profiles[0].name])], skip_oversized=True)


def _maybe_promote_blocking_to_adaptive(
    blocking: BlockingConfig | None,
    n_rows: int,
    *,
    threshold: int = 1_000_000,
) -> BlockingConfig | None:
    """Promote ``strategy="static"`` to ``"adaptive"`` for large datasets.

    Adaptive blocking recursively sub-partitions oversized blocks via
    ``core/blocker.py::_sub_block`` and falls back to
    ``_auto_split_block`` when ``sub_block_keys`` aren't configured
    (zero-config path). Without this promotion, ``build_blocking``
    always emits ``strategy="static"`` (or ``"multi_pass"`` / ``"canopy"``
    for specific shape paths). At full scale, oversized buckets — e.g.
    the Smith surname block at 5M+ scaling to ~57K rows — are scored
    as one giant block instead of being recursively sub-partitioned.

    Triggers when:

    - ``n_rows >= threshold`` (default 1M).
    - Current strategy is ``"static"`` only. ``multi_pass``, ``canopy``,
      ``ann``, ``learned``, ``sorted_neighborhood`` each have their own
      escape hatches; don't second-guess them.

    This is the eager half of the adaptive-blocking work. The
    controller-rule counterpart (``rule_blocking_adaptive_on_p99_outlier``)
    fires reactively when the measured block-size distribution
    shows a heavy P99 tail — useful when the eager threshold isn't
    reached but the data is pathologically skewed at smaller N.
    """
    if blocking is None or n_rows < threshold:
        return blocking
    if blocking.strategy != "static":
        return blocking
    return blocking.model_copy(update={"strategy": "adaptive"})


def _maybe_prune_blocking_passes(
    blocking: BlockingConfig | None,
    df: pl.DataFrame,
) -> BlockingConfig | None:
    """Opt-in weak-positive-aware pruning of multi-pass blocking passes.

    Default OFF. Enable via ``GOLDENMATCH_BLOCKING_PRUNE_PASSES=1``. The floor
    is ``GOLDENMATCH_BLOCKING_PASS_MIN_WEAKPOS`` (default 1 = drop only
    fully-redundant / all-noise passes, recall-safe). Higher floors trade a
    little recall for fewer candidates while protecting high-precision passes
    (the selector ranks by likely-match yield, NOT raw new-pair count, so a
    sparse-but-precise pass like exact date-of-birth survives).

    Runs on the auto-config sample (``df``); the surviving passes are applied to
    the full dataset. No-op unless the strategy is ``multi_pass`` with >1 pass.
    """
    if blocking is None or blocking.strategy != "multi_pass":
        return blocking
    if os.environ.get("GOLDENMATCH_BLOCKING_PRUNE_PASSES", "").strip().lower() not in (
        "1", "true", "yes", "on", "enabled",
    ):
        return blocking
    passes = blocking.passes or []
    if len(passes) <= 1:
        return blocking
    try:
        floor = int(os.environ.get("GOLDENMATCH_BLOCKING_PASS_MIN_WEAKPOS", "1"))
    except ValueError:
        floor = 1
    try:
        from goldenmatch.core.blocking_pass_selection import select_passes

        result = select_passes(df, list(passes), min_marginal_weak_positive=floor)
    except Exception:
        logger.warning("blocking pass selection failed; keeping all passes", exc_info=True)
        return blocking
    if not result.dropped or not result.kept:
        return blocking
    logger.info(
        "blocking pass selection: kept %d/%d passes (dropped %s)",
        len(result.kept), len(passes), [list(p.fields) for p in result.dropped],
    )
    return blocking.model_copy(update={"passes": result.kept})


# ── Model selection ────────────────────────────────────────────────────────

def select_model(row_count: int, has_embedding_columns: bool, threshold: int = 50000) -> str | None:
    """Select embedding model. Returns None if no embedding columns needed."""
    if not has_embedding_columns:
        return None
    if row_count < threshold:
        return "gte-base-en-v1.5"
    return "all-MiniLM-L6-v2"


# ── Main entry point ──────────────────────────────────────────────────────

# ContextVar populated by auto_configure_df after each successful controller
# run. Holds (ComplexityProfile, RunHistory) so PostflightReport can pick up
# the profile + history without threading them through every call site.
_LAST_CONTROLLER_RUN: ContextVar = ContextVar("_LAST_CONTROLLER_RUN", default=None)

# ContextVar populated by auto_configure_df with the GoldenCheck exclusion
# list (#404). PostflightReport reads this to render the "Auto-config
# exclusions" section so users see the audit trail without grep-ing logs.
_LAST_AUTOCONFIG_EXCLUSIONS: ContextVar = ContextVar(
    "_LAST_AUTOCONFIG_EXCLUSIONS", default=None,
)

# ContextVar set by dedupe_df / match_df with the kwarg-supplied
# exclude_columns list (#unified-exclusions). Layered ADDITIVELY with
# config.exclude_columns and detector-derived exclusions inside
# auto_configure_df. Cleared after each call via context-bound semantics
# (callers do `.set(...)` then rely on the ContextVar value living only
# for the duration of their own frame).
_RUNTIME_EXCLUDE_COLUMNS: ContextVar = ContextVar(
    "_RUNTIME_EXCLUDE_COLUMNS", default=None,
)

# #858: set by the match pipeline (`_run_match_pipeline`) and `match_df` around
# their `auto_configure_df` call so the multi-source guard is suppressed in
# match mode (cross-source linking is the goal there). Dedupe paths never set it.
_AUTOCONFIG_MATCH_MODE: ContextVar = ContextVar(
    "_AUTOCONFIG_MATCH_MODE", default=False,
)


@contextlib.contextmanager
def _match_mode_autoconfig():  # pyright: ignore[reportUnusedFunction]  # used cross-module (pipeline / _api) via local imports
    """Suppress the #858 multi-source guard for the duration (match mode)."""
    token = _AUTOCONFIG_MATCH_MODE.set(True)
    try:
        yield
    finally:
        _AUTOCONFIG_MATCH_MODE.reset(token)


def _multisource_autoconfig_enabled() -> bool:
    """#858 multi-source zero-config guard. Default ON; disable with
    ``GOLDENMATCH_MULTISOURCE_AUTOCONFIG=0`` (or false/disabled/off/no)."""
    return os.environ.get(
        "GOLDENMATCH_MULTISOURCE_AUTOCONFIG", "1"
    ).strip().lower() not in {"0", "false", "disabled", "off", "no"}


def _env_force_exclude() -> list[str]:
    raw = os.environ.get("GOLDENMATCH_AUTOCONFIG_FORCE_EXCLUDE", "")
    return [c.strip() for c in raw.split(",") if c.strip()]


def _env_force_include() -> list[str]:
    raw = os.environ.get("GOLDENMATCH_AUTOCONFIG_FORCE_INCLUDE", "")
    return [c.strip() for c in raw.split(",") if c.strip()]


def _resolve_effective_exclusion_overrides(
    config: Any | None = None,
) -> tuple[list[str], list[str]]:
    """Combine every exclusion source into (force_exclude, force_include).

    Sources, layered additively for force_exclude:
      1. ``config.exclude_columns`` -- new top-level field (this PR).
      2. ``_RUNTIME_EXCLUDE_COLUMNS`` ContextVar -- kwarg from
         ``dedupe_df`` / ``match_df``.
      3. ``config.quality.autoconfig_force_exclude`` -- #404 sub-field.
      4. ``GOLDENMATCH_AUTOCONFIG_FORCE_EXCLUDE`` env var -- #404 V1 path.

    Sources for force_include (RESCUE -- beats every opt-out path):
      a. ``config.quality.autoconfig_force_include`` -- #404 sub-field.
      b. ``GOLDENMATCH_AUTOCONFIG_FORCE_INCLUDE`` env var -- #404 V1 path.

    Order in the final set is irrelevant (it's a union); ordering here
    just makes the log line predictable.
    """
    fe: list[str] = []
    fi: list[str] = []
    if config is not None:
        # New top-level field.
        cfg_excl = getattr(config, "exclude_columns", None) or []
        fe.extend(cfg_excl)
        # Legacy #404 sub-field on QualityConfig (still supported).
        qc = getattr(config, "quality", None)
        if qc is not None:
            fe.extend(getattr(qc, "autoconfig_force_exclude", None) or [])
            fi.extend(getattr(qc, "autoconfig_force_include", None) or [])
    runtime_excl = _RUNTIME_EXCLUDE_COLUMNS.get()
    if runtime_excl:
        fe.extend(runtime_excl)
    fe.extend(_env_force_exclude())
    fi.extend(_env_force_include())
    # De-dupe while preserving first-occurrence order for log readability.
    fe = list(dict.fromkeys(fe))
    fi = list(dict.fromkeys(fi))
    return fe, fi



# Tier 4: cross-run memory.  Set GOLDENMATCH_AUTOCONFIG_MEMORY=0 (or "false"
# or "disabled") to opt out (useful in CI or when disk I/O should be minimal).
_AUTOCONFIG_MEMORY_DISABLED: bool = (
    os.environ.get("GOLDENMATCH_AUTOCONFIG_MEMORY", "1").lower()
    in ("0", "false", "disabled")
)

# Tier 3: LLM fallback policy.  Set GOLDENMATCH_AUTOCONFIG_LLM=1 (or "true"
# or "enabled") to enable LLM-assisted config proposals when the heuristic
# rule table is exhausted but the profile is still RED/YELLOW.  Requires
# OPENAI_API_KEY.  Default OFF to avoid unexpected API spend.
_AUTOCONFIG_LLM_ENABLED: bool = (
    os.environ.get("GOLDENMATCH_AUTOCONFIG_LLM", "0").lower()
    in ("1", "true", "enabled")
)

# Module-level default memory singleton (uses ~/.goldenmatch/autoconfig_memory.db).
# Lazily initialised on first use to avoid creating the directory at import time.
_DEFAULT_MEMORY: AutoConfigMemory | None = None


def _get_default_memory() -> AutoConfigMemory | None:
    """Return the default AutoConfigMemory, or None when disabled via env var."""
    global _DEFAULT_MEMORY
    if _AUTOCONFIG_MEMORY_DISABLED:
        return None
    if _DEFAULT_MEMORY is None:
        try:
            from goldenmatch.core.autoconfig_memory import AutoConfigMemory
            _DEFAULT_MEMORY = AutoConfigMemory()
        except Exception as exc:
            logger.warning(
                "auto-config: could not initialise default memory store: %s", exc
            )
            return None
    return _DEFAULT_MEMORY


def _field_group_detection_enabled(config: Any) -> bool:
    """Return True when field-group detection is switched on (default OFF).

    Two opt-in paths:
    - ``golden_rules.field_group_detection = True`` on the incoming config
    - env var ``GOLDENMATCH_FIELD_GROUP_SURVIVORSHIP`` set to 1/true/yes/on
    """
    gr = getattr(config, "golden_rules", None)
    if gr is not None and getattr(gr, "field_group_detection", False):
        return True
    return os.environ.get("GOLDENMATCH_FIELD_GROUP_SURVIVORSHIP", "").lower() in (
        "1", "true", "yes", "on"
    )


def _maybe_active_domain_pack(config: Any):
    """Return a goldencheck-types DomainPack if one is clearly in play, else None.

    v1: best-effort -- return None (detection falls back to the heuristic).
    Kept minimal on purpose; infermap-fed detection is opportunistic.
    """
    return None


def _maybe_detect_field_groups(df: Any, config: Any) -> None:
    """Gated, fail-open hook: when enabled, detect field groups and write them
    onto ``config.golden_rules.field_groups``.

    Default OFF; explicit groups always kept. infermap is optional (its import
    lives inside ``build_field_groups``). Any exception leaves the config
    untouched so auto-config is never broken by optional detection.
    """
    enabled = _field_group_detection_enabled(config)
    explicit = []
    gr = getattr(config, "golden_rules", None)
    if gr is not None:
        explicit = list(getattr(gr, "field_groups", []) or [])
    if not enabled and not explicit:
        # Nothing to do; leave config byte-identical.
        return
    # Field-group detection is column-name-based and needs a Polars frame.
    # On the distributed path `df` is a Ray Dataset; skip silently (collecting
    # to detect would be wrong here) rather than letting build_field_groups
    # fail-open with a misleading warning.
    try:
        from goldenmatch.distributed import is_ray_dataset
        if is_ray_dataset(df):
            return
    except Exception:
        pass  # distributed utils not importable -> proceed; outer try/except still guards
    try:
        from goldenmatch.core.survivorship.groups import build_field_groups
        pack = _maybe_active_domain_pack(config)
        detected = build_field_groups(df, pack=pack, explicit=explicit, enabled=enabled)
    except Exception as exc:
        # Fail-open: never break auto-config over optional detection.
        logger.debug("field-group detection hook skipped: %s", exc)
        return
    if not detected:
        return
    # Ensure golden_rules exists so we can attach field_groups.
    if gr is None:
        from goldenmatch.config.schemas import GoldenRulesConfig as _GRC
        gr = _GRC(default_strategy="most_complete")
        config.golden_rules = gr
    gr.field_groups = detected


def auto_configure_df(
    df: pl.DataFrame | pl.LazyFrame,
    llm_provider: str | None = None,
    domain_config: Any = None,
    llm_auto: bool = False,
    strict: bool = False,
    allow_remote_assets: bool = False,
    *,
    reference: pl.DataFrame | pl.LazyFrame | None = None,
    _skip_finalize: bool = False,
    confidence_required: bool = True,
    allow_red_config: bool = False,
    planning_effort: str | None = None,
    n_rows_full: int | None = None,
    throughput: Any | None = None,
) -> GoldenMatchConfig:
    """Public auto-configuration entry point (controller-backed).

    Runs an iterative refit loop:
      1. Compute v0 config via the legacy heuristic (or retrieve from memory)
      2. Run blocking → score → cluster on a stratified sample under profile_capture
      3. Read the ComplexityProfile, ask the policy for a refit
      4. Loop until green/converged/budget
      5. Run the full pipeline once and return the committed config

    The committed config is what's returned. Profile + history are stashed
    on the ``_LAST_CONTROLLER_RUN`` ContextVar so the calling pipeline can
    surface them via PostflightReport.

    Cross-run memory (Tier 4): when a previous successful run used the same
    data shape, the cached config is returned as the starting point, bypassing
    the legacy heuristic. Set ``GOLDENMATCH_AUTOCONFIG_MEMORY=0`` to disable.

    Args:
        df: target DataFrame (or LazyFrame, which will be collected).
        reference: optional reference DataFrame for cross-source ``match_df``
            mode. When None, dedupe mode (single-source).
        llm_provider, domain_config, llm_auto, strict, allow_remote_assets:
            unchanged from the legacy signature; threaded into v0 only.

    Raises:
        TypeError: when ``df`` or ``reference`` is not a polars DataFrame/LazyFrame.
        ConfigValidationError: from the controller, when input is unworkable
            (empty, all-null, etc.).
    """
    # Throughput tier (#1083): early validation -- check text column exists BEFORE
    # the expensive controller run to give a clean ThroughputNotApplicableError.
    if throughput is not None:
        import polars as _pl_tp

        from goldenmatch.core.throughput_verify import ThroughputNotApplicableError
        if isinstance(df, _pl_tp.DataFrame):
            _early_profiles = profile_columns(df)
            # Same predicate as _throughput_blocking (shared helper) so the early
            # gate and the blocking builder cannot diverge — heterogeneous corpus
            # text mis-classified as "address"/"identifier" must pass here too.
            if not sketchable_text_cols(_early_profiles):
                raise ThroughputNotApplicableError(
                    "throughput tier requires a text column to sketch on; none found "
                    f"(columns: {[(p.name, p.col_type, round(p.avg_len)) for p in _early_profiles]})"
                )

    # Coerce + validate input types.
    # Phase 2: also accept ray.data.Dataset on the distributed path.
    from goldenmatch.distributed._utils import is_ray_dataset as _is_ray_dataset
    if _is_ray_dataset(df):
        # Dataset stays as-is; controller.run() handles it natively.
        _n_rows_for_budget: int = df.count()  # type: ignore[union-attr]
    elif isinstance(df, pl.LazyFrame):
        df = df.collect()
        _n_rows_for_budget = df.height
    elif isinstance(df, pl.DataFrame):
        _n_rows_for_budget = df.height
    else:
        raise TypeError(
            f"auto_configure_df requires pl.DataFrame, pl.LazyFrame, or "
            f"ray.data.Dataset, got {type(df).__name__}"
        )
    if reference is not None:
        if isinstance(reference, pl.LazyFrame):
            reference = reference.collect()
        elif not isinstance(reference, pl.DataFrame):
            raise TypeError(
                f"reference requires pl.DataFrame or pl.LazyFrame, "
                f"got {type(reference).__name__}"
            )

    # ── GoldenCheck auto-config exclusions (#404) ──
    # Run the column-exclusion detectors BEFORE the controller starts.
    # Drop excluded columns from df so v0, sample iterations, and the
    # committed config never reference them. The user's original df
    # downstream still has every column (golden records aren't affected);
    # we only filter the matchkey/blocking candidate pool.
    #
    # Skipped when df is a Ray Dataset (distributed path) -- those have
    # their own column-selection story; exclusions land at the per-
    # partition kernel layer separately.
    _ms_partition: str | None = None  # #858 source partition (None => feature off)
    if not _is_ray_dataset(df) and isinstance(df, pl.DataFrame):
        from goldenmatch.core.quality_exclusions import (
            detect_autoconfig_exclusions,
        )
        # Combine every exclusion source: top-level config.exclude_columns
        # (this PR), dedupe_df / match_df kwarg via _RUNTIME_EXCLUDE_COLUMNS
        # ContextVar, QualityConfig.autoconfig_force_{exclude,include}
        # (#404 sub-field), env vars. force_include rescues from every
        # opt-out path. auto_configure_df has no input config -- only
        # the ContextVar + env vars are visible here; the dedupe_df
        # shim writes both before calling us.
        force_exclude_list, force_include_list = (
            _resolve_effective_exclusion_overrides(config=None)
        )
        # #858: multi-source source-correlated over-merge guard (spec §0-§4).
        # Detect the source partition on the FULL frame, then exclude the
        # source-indicator column + every 0-cross-source-overlap column from
        # match features by folding them into force_exclude (dropped below).
        _ms_profiles = profile_columns(df)
        _ms_partition = _detect_source_partition(df, _ms_profiles)
        _ms_exclude = _source_correlated_exclusions(df, _ms_profiles, _ms_partition)
        if _ms_exclude:
            force_exclude_list = list(force_exclude_list) + [
                c for c in _ms_exclude if c not in force_exclude_list
            ]
        # Internal bookkeeping columns are invisible to detectors.
        skip = {"__row_id__", "__source__"}
        for col in df.columns:
            if col.startswith("__") and col.endswith("__"):
                skip.add(col)
        exclusions = detect_autoconfig_exclusions(
            df,
            force_exclude=force_exclude_list,
            force_include=force_include_list,
            skip_columns=skip,
        )
        if exclusions:
            cols_to_drop = [
                ec.column for ec in exclusions if ec.column in df.columns
            ]
            for ec in exclusions:
                logger.info(
                    "Auto-config exclusion: %r (detector=%s) -- %s",
                    ec.column, ec.detector, ec.reason,
                )
            if cols_to_drop:
                df = df.drop(cols_to_drop)
            _LAST_AUTOCONFIG_EXCLUSIONS.set(list(exclusions))
        else:
            _LAST_AUTOCONFIG_EXCLUSIONS.set([])

    # Lazy import to avoid circular imports (controller imports back here)
    from goldenmatch.core.autoconfig_controller import (
        AutoConfigController,
        ControllerBudget,
        resolve_planning_effort,
    )
    from goldenmatch.core.autoconfig_policy import HeuristicRefitPolicy, LLMRefitPolicy

    # Spec 2026-06-06 §Phase 0: resolve the planning-effort tier (explicit
    # kwarg → GOLDENMATCH_PLANNING_EFFORT env → "normal"). It scales the
    # controller budget and (at thinking+) flips the planner to measured
    # blocking.
    effort = resolve_planning_effort(planning_effort)

    memory = _get_default_memory()
    if _AUTOCONFIG_LLM_ENABLED:
        policy: HeuristicRefitPolicy | LLMRefitPolicy = LLMRefitPolicy(HeuristicRefitPolicy())
    else:
        policy = HeuristicRefitPolicy()
    controller = AutoConfigController(
        policy=policy,
        budget=ControllerBudget.for_dataset(_n_rows_for_budget, effort),
        memory=memory,
    )
    v0_kw = {
        "llm_provider": llm_provider,
        "domain_config": domain_config,
        "llm_auto": llm_auto,
        "strict": strict,
        "allow_remote_assets": allow_remote_assets,
        # #858: full-frame source-partition detection result, threaded to
        # build_matchkeys via _legacy_auto_configure_v0 for phone demotion.
        "multi_source": _ms_partition is not None,
    }
    # #876: let a caller configure FOR a larger target population than `df` (e.g.
    # build a frozen config from a small oracle but FOR 200M rows, so
    # build_blocking's scale gate projects to the real scale). _initial_config
    # forwards v0_kwargs["n_rows_full"] to the v0 heuristic (its guard lets the
    # caller's value win over the controller's df.height default).
    if n_rows_full is not None:
        v0_kw["n_rows_full"] = n_rows_full
    # Resolve throughput config now so the controller can thread it through
    # the plan-build/apply_to site (Task 9). None when throughput is not requested.
    _resolved_tp_cfg = None
    if throughput is not None:
        from goldenmatch.core.throughput_verify import resolve_throughput_config as _rtc
        _resolved_tp_cfg = _rtc(throughput, None)
    config, profile, history = controller.run(
        df,
        reference=reference,
        v0_kwargs=v0_kw,
        skip_finalize=_skip_finalize,
        confidence_required=confidence_required,
        allow_red_config=allow_red_config,
        planning_effort=effort,
        throughput=_resolved_tp_cfg,
    )
    # Surface the resolved tier on the committed config for observability
    # (telemetry, YAML round-trip). No-op for the default "normal".
    try:
        config.planning_effort = effort  # type: ignore[assignment]
    except Exception:
        pass

    # Backend selection is driven by the controller v3 planner inside
    # AutoConfigController.run -- it captures RuntimeProfile, extrapolates
    # the committed BlockingProfile to full-row count, and writes the
    # selected backend onto config via ExecutionPlan.apply_to.

    _LAST_CONTROLLER_RUN.set((profile, history))

    # Throughput tier (#1083): ensure blocking + _throughput_plan are set on the
    # returned config. The controller handles this when it reaches the plan-apply
    # site; for early-return paths (1-column, 1-row) we do it here.
    if _resolved_tp_cfg is not None and _resolved_tp_cfg.enabled:
        import polars as _pl_tp2
        _tp_profiles2 = profile_columns(df) if isinstance(df, _pl_tp2.DataFrame) else []
        if _tp_profiles2:
            try:
                _tp_blk2 = _throughput_blocking(_tp_profiles2, config)
                config.blocking = _tp_blk2
                from goldenmatch.core.throughput_verify import metric_and_signature_len
                _tp_metric2, _tp_siglen2 = metric_and_signature_len(_tp_blk2)
                from goldenmatch.core.autoconfig_planner import apply_throughput_overlay as _ato
                from goldenmatch.core.execution_plan import ExecutionPlan as _EP
                _base_plan2 = config._throughput_plan or _EP()
                if getattr(_base_plan2, "verify_mode", "full") == "full":
                    config._throughput_plan = _ato(
                        _base_plan2, _resolved_tp_cfg,
                        metric=_tp_metric2, signature_len=_tp_siglen2,
                    )
            except Exception:
                pass
        config.throughput = _resolved_tp_cfg

    # F1: optional field-group detection (default OFF, fail-open).
    # Runs after the controller has committed the config so explicit
    # golden_rules.field_groups (if any) are already in place.
    _maybe_detect_field_groups(df, config)

    # Perceptual media-as-evidence wiring (ADR 0022, default OFF, fail-open).
    # When enabled, detect fixed-width-hex perceptual-hash columns (image pHash /
    # audio fingerprint) and append a phash/audio_fp matchkey (+ perceptual
    # blocking for an image column when nothing else blocks). Byte-identical when
    # the flag is off.
    if os.environ.get("GOLDENMATCH_PERCEPTUAL_AUTOCONFIG", "0") == "1" and isinstance(
        df, pl.DataFrame
    ):
        try:
            from goldenmatch.core.perceptual_autoconfig import apply_perceptual_autoconfig

            config = apply_perceptual_autoconfig(config, df)
        except Exception:  # noqa: BLE001 - additive + fail-open, never break auto-config
            logger.debug(
                "perceptual auto-config hook failed; leaving config unchanged",
                exc_info=True,
            )

    return config


def _legacy_auto_configure_v0(  # pyright: ignore[reportUnusedFunction]  # kept for historical reference
    df: pl.DataFrame,
    *,
    reference: pl.DataFrame | None = None,  # ignored for v1; Task 5.1 plumbs it
    llm_provider: str | None = None,
    domain_config: Any = None,
    llm_auto: bool = False,
    strict: bool = False,
    allow_remote_assets: bool = False,
    n_rows_full: int | None = None,
    multi_source: bool = False,
) -> GoldenMatchConfig:
    """Legacy column-profiling + rule-based auto-config heuristic (the v0
    starting point for the controller). Implementation unchanged from the
    pre-Task-4.x ``auto_configure_df`` — just renamed and given a private
    underscore prefix so the controller can call it without recursing.

    Args:
        df: input frame (may be the controller's sub-sample, not the
            full population). Profile + rule decisions made against
            this frame; the candidate-pool gate uses ``n_rows_full``
            below to project sample cardinalities to full scale.
        n_rows_full: full-population row count. When the controller
            sub-samples a large frame, this carries the original row
            count so Chao1 sample-correction has the right denominator
            (#410). When ``None`` (direct callers, tests passing a
            full frame), defaults to ``df.height``.
    """
    # Initialized up front so the preflight-wiring block at the bottom can
    # safely test `if domain_profile is not None` even when the domain
    # branch below is skipped (e.g. user-provided domain_config).
    domain_profile = None
    # Preserve the raw input df for preflight. The in-function `df` variable
    # gets enriched with __row_id__ / domain-extracted columns; preflight
    # needs to check against the shape the pipeline will see.
    df_input = df
    # #410: total_rows is the FULL population, not the sample. When the
    # controller passes a 5K sample of a 1.13M-row frame, df.height = 5K
    # but the gate needs 1.13M to scale via Chao1. Caller threads the
    # true count via n_rows_full; falls back to df.height for direct
    # callers (tests / non-controller paths) that pass full frames.
    total_rows = n_rows_full if n_rows_full is not None else df.height

    logger.info("Auto-configuring %d rows, %d columns", total_rows, len(df.columns))

    _emit_data_profile(df)

    # Profile columns
    profiles = profile_columns(df, llm_provider=llm_provider)

    logger.info(
        "Detected column types: %s",
        {p.name: p.col_type for p in profiles},
    )

    # ── Domain detection + conditional extraction ──
    extracted_columns = []

    if domain_config is not None:
        # Manual override: skip auto-detection. Synthesize a profile-like
        # object so preflight Check 1 can still auto-repair domain-extracted
        # column refs in the manual-override path. Without this, passing an
        # explicit DomainConfig silently defeats the preflight repair.
        from goldenmatch.core.domain import DomainProfile
        domain_profile = DomainProfile(
            name=domain_config.mode or "manual",
            confidence=1.0,
            text_columns=[],  # unknown; not used by preflight's repair path
        )
        logger.info("Domain config provided manually, skipping auto-detection")
    else:
        from goldenmatch.core.domain import detect_domain, extract_features

        user_cols = [c for c in df.columns if not c.startswith("__")]
        domain_profile = detect_domain(user_cols)

        if domain_profile.confidence > 0.7:
            original_cols = set(df.columns)
            # extract_features requires __row_id__ column
            if "__row_id__" not in df.columns:
                df = df.with_row_index("__row_id__")
            df, _low_conf_ids = extract_features(df, domain_profile)
            extracted_columns = [c for c in df.columns if c.startswith("__") and c not in original_cols]
            logger.info(
                "Domain '%s' detected (confidence=%.2f), extracted %d feature columns",
                domain_profile.name, domain_profile.confidence, len(extracted_columns),
            )
        else:
            logger.info(
                "Domain '%s' (confidence=%.2f) below threshold, skipping extraction",
                domain_profile.name, domain_profile.confidence,
            )

    # Build matchkeys
    matchkeys = build_matchkeys(profiles, df=df, multi_source=multi_source)

    # Probabilistic routing (gated, default-off): a probabilistic-shaped dataset
    # (no surviving identifier/email/phone exact matchkey + >=2 fuzzy fields) is
    # better served by the Fellegi-Sunter path. Delegate to
    # auto_configure_probabilistic_df (it builds the diversified FS blocking that
    # lifts recall) and return directly. NOTE (v1 scoping): this fires before the
    # domain-extracted matchkeys are appended below, so a domain-extracted identifier
    # is not seen by the trigger; and auto_configure_probabilistic_df re-profiles df
    # (excluding __-prefixed cols), so any already-extracted domain feature is dropped
    # on the routed path. Acceptable while default-off; revisit if a domain-heavy
    # dataset misroutes or needs its domain features under FS.
    if (not multi_source and _route_to_probabilistic_enabled()
            and _is_probabilistic_shape(matchkeys, profiles)):
        return auto_configure_probabilistic_df(df, llm_provider=llm_provider)

    # ── Add domain-extracted fields to matchkeys ──
    if extracted_columns:
        domain_exact = []
        domain_fuzzy = []
        for col in extracted_columns:
            if col not in _DOMAIN_SCORER_MAP:
                continue
            scorer, weight, transforms = _DOMAIN_SCORER_MAP[col]
            null_rate = df[col].null_count() / df.height if df.height > 0 else 0
            cardinality_ratio = df[col].n_unique() / df.height if df.height > 0 else 0
            if null_rate > 0.5:
                continue
            if scorer == "exact" and cardinality_ratio < 0.01:
                continue
            mf = MatchkeyField(field=col, scorer=scorer, weight=weight, transforms=transforms)
            if scorer == "exact":
                domain_exact.append(mf)
            else:
                domain_fuzzy.append(mf)

        # Add domain exact matchkeys
        for f in domain_exact:
            matchkeys.append(MatchkeyConfig(
                name=f"domain_exact_{f.field.strip('_')}",
                type="exact",
                fields=[MatchkeyField(field=f.field, transforms=f.transforms)],
            ))

        # Add domain fuzzy fields to existing weighted matchkey (or create one)
        if domain_fuzzy:
            weighted = [mk for mk in matchkeys if mk.type == "weighted"]
            if weighted:
                weighted[0].fields.extend(domain_fuzzy)
            else:
                matchkeys.append(MatchkeyConfig(
                    name="domain_fuzzy",
                    type="weighted",
                    threshold=0.80,
                    fields=domain_fuzzy,
                ))

    # Check if embeddings are needed
    has_embeddings = any(
        f.scorer in ("embedding", "record_embedding")
        for mk in matchkeys
        for f in mk.fields
    )

    # Select model and apply to embedding fields
    model = select_model(total_rows, has_embeddings)
    if model:
        for mk in matchkeys:
            for f in mk.fields:
                if f.scorer in ("embedding", "record_embedding") and not f.model:
                    f.model = model

    # ── Add domain columns to blocking candidate profiles ──
    if extracted_columns:
        for col in extracted_columns:
            if col not in _DOMAIN_SCORER_MAP:
                continue
            scorer, _weight, _transforms = _DOMAIN_SCORER_MAP[col]
            if scorer != "exact":
                continue
            null_rate = df[col].null_count() / df.height if df.height > 0 else 0
            cardinality_ratio = df[col].n_unique() / df.height if df.height > 0 else 0
            if null_rate > 0.5:
                continue
            profiles.append(ColumnProfile(
                name=col, dtype="Utf8", col_type="email",
                confidence=0.9, null_rate=null_rate,
                cardinality_ratio=cardinality_ratio, avg_len=0,
            ))

    # Build blocking (required for weighted/probabilistic matchkeys).
    # #410: thread total_rows so the cardinality gate + composite search
    # can sample-correct via Chao1 when the controller passed us a sub-
    # sample of a much larger frame.
    has_fuzzy = any(mk.type in ("weighted", "probabilistic") for mk in matchkeys)
    blocking = build_blocking(
        profiles, df,
        llm_provider=llm_provider,
        n_rows_full=total_rows,
    ) if has_fuzzy else None
    # Quality-aware blocking (door #1): add fuzzy-tolerant passes for columns
    # GoldenCheck flags as edit-distance-fuzzy. Fail-open + additive; OFF unless
    # GOLDENMATCH_QUALITY_AWARE_BLOCKING=1. Runs before adaptive promotion.
    blocking = apply_quality_aware_blocking(blocking, profiles, df)
    # Auto-enabled semantic blocking (door, #1090): route text-heavy data to
    # SimHash-over-embeddings; honest no-op when embeddings are unavailable. OFF
    # unless GOLDENMATCH_AUTO_SEMANTIC_BLOCKING=1, so default output is identical.
    blocking = apply_auto_semantic_blocking(blocking, profiles, df)
    # At 1M+ rows, swap static blocking for adaptive so blocker.py's
    # _sub_block / _auto_split_block paths bound oversized buckets at
    # runtime. No-op for multi_pass / canopy / ann / learned / sorted_neighborhood.
    blocking = _maybe_promote_blocking_to_adaptive(blocking, df.height)
    # Opt-in (GOLDENMATCH_BLOCKING_PRUNE_PASSES=1): drop redundant/all-noise
    # multi-pass passes by likely-match yield.
    blocking = _maybe_prune_blocking_passes(blocking, df)

    # ── Data-driven strategy selection ──

    # 1. Learned blocking for large datasets.
    #
    # Gated at >= 50K rows because the learner needs two things the sample
    # cap below cannot provide on smaller inputs:
    #
    #   a) held-out rows to generalize to — `learned_sample_size` caps the
    #      training sample at 25% of the dataset, max 5K. Below 50K that cap
    #      is tight enough (<=12.5K training / 37.5K held-out) to produce
    #      predicates that generalize instead of memorizing the input.
    #
    #   b) enough rows to amortize training cost — below 50K, static or
    #      multi_pass blocking is usually faster and comparable in quality.
    if blocking is not None and total_rows >= 50_000:
        blocking.strategy = "learned"
        blocking.learned_sample_size = min(total_rows // 4, 5000)
        blocking.learned_min_recall = 0.95
        blocking.skip_oversized = True
        logger.info(
            "Upgraded to learned blocking (dataset has %d rows, sample_size=%d)",
            total_rows, blocking.learned_sample_size,
        )

    # 2. Reranking for multi-field matchkeys
    for mk in matchkeys:
        if mk.type == "weighted" and len(mk.fields) >= 3:
            mk.rerank = True
            logger.info("Enabled reranking for matchkey '%s' (%d fields)", mk.name, len(mk.fields))

    # 3. Adaptive threshold from data quality
    for mk in matchkeys:
        if mk.type == "weighted" and mk.threshold is not None:
            fuzzy_field_names = {f.field for f in mk.fields if f.field}
            fuzzy_profiles = [p for p in profiles if p.name in fuzzy_field_names]
            if fuzzy_profiles:
                avg_null = sum(p.null_rate for p in fuzzy_profiles) / len(fuzzy_profiles)
                avg_len = sum(p.avg_len for p in fuzzy_profiles) / len(fuzzy_profiles)
                original = mk.threshold
                if avg_null > 0.15:
                    mk.threshold = max(mk.threshold - 0.05, 0.50)
                elif avg_len < 5:
                    mk.threshold = min(mk.threshold + 0.05, 0.95)
                if mk.threshold != original:
                    logger.info(
                        "Adjusted threshold for '%s': %.2f -> %.2f (avg_null=%.2f, avg_len=%.1f)",
                        mk.name, original, mk.threshold, avg_null, avg_len,
                    )

    # ── LLM auto-config ──
    llm_scorer_config = None
    if llm_auto:
        import os
        _provider = None
        if os.environ.get("ANTHROPIC_API_KEY"):
            _provider = "anthropic"
        elif os.environ.get("OPENAI_API_KEY"):
            _provider = "openai"
        if _provider:
            llm_scorer_config = LLMScorerConfig(
                enabled=True,
                candidate_lo=0.60,
                candidate_hi=0.90,
                auto_threshold=0.90,
                budget=BudgetConfig(max_cost_usd=0.05),
            )
            logger.info("LLM scorer auto-enabled (provider=%s, budget=$0.05)", _provider)
        else:
            logger.info("llm_auto=True but no API key found")

    memory_config = MemoryConfig(enabled=True) if llm_auto else None

    # Auto-detect standardization rules (Change 1, 2026-05-07)
    # When the column-profile classifier identifies a typed column (phone,
    # email, name, address, zip, geo), emit a corresponding StandardizationConfig
    # rule. The hand-tuned dqbench adapter does this manually; auto-config
    # now matches it without explicit user intervention.
    #
    # StandardizationConfig.rules schema: {column_name: [standardizer_names]}
    # VALID_STANDARDIZERS: email, phone, zip5, address, state, name_proper,
    # name_upper, name_lower, strip, trim_whitespace.
    _std_rules: dict[str, list[str]] = {}  # {col_name: [std_name, ...]}
    _email_typed_cols: set[str] = set()  # guard: skip address detection on these
    for _p in profiles:
        if _p.col_type == "email":
            _email_typed_cols.add(_p.name)
            _std_rules[_p.name] = ["email"]
        elif _p.col_type == "phone":
            _std_rules[_p.name] = ["phone"]
        elif _p.col_type == "zip":
            _std_rules[_p.name] = ["zip5"]
        elif _p.col_type == "geo":
            # state-shaped columns (state_cd, province, country) → state rule
            _cl = _p.name.lower()
            if any(pat in _cl for pat in ("state", "province", "country")):
                _std_rules[_p.name] = ["state"]
        elif _p.col_type == "name":
            # Route all name columns to name_proper (title-case + strip).
            _std_rules[_p.name] = ["name_proper"]
        elif _p.col_type == "address":
            # Guard: skip if column was already typed as email (e.g. email_address)
            if _p.name not in _email_typed_cols:
                _std_rules[_p.name] = ["address"]

    # Build the StandardizationConfig only if we detected something.
    # Return None rather than StandardizationConfig(rules={}) to avoid passing
    # an empty config into the pipeline.
    _standardization = None
    if _std_rules:
        from goldenmatch.config.schemas import StandardizationConfig
        _standardization = StandardizationConfig(rules=_std_rules)
        logger.info(
            "auto-config: StandardizationConfig auto-detected %d column rules: %s",
            len(_std_rules), sorted(_std_rules.keys()),
        )

    # Capture choices in a decisions object so a future iterative-tuning loop
    # can nudge them without re-profiling. Matchkeys and blocking strategy/
    # keys/passes all flow through decisions; non-decision runtime attributes
    # on the transient `blocking` object (learned_sample_size, min_recall,
    # skip_oversized, etc.) are preserved in the rebuild step below.
    decisions = AutoConfigDecisions(
        blocking_strategy=blocking.strategy if blocking is not None else "none",
        blocking_keys=list(blocking.keys) if (blocking is not None and blocking.keys) else [],
        blocking_passes=list(blocking.passes) if (blocking is not None and blocking.passes) else [],
        matchkeys=matchkeys,
        threshold=0.0,  # populated in a later task
        # domain_mode mirrors the detected domain profile (when present).
        # Demonstrates the field's contract even though no consumer reads it
        # yet — populating now means future iterative tuners can inspect it
        # without us also having to backfill the call site.
        domain_mode=(domain_profile.name if domain_profile is not None else None),
        llm_enabled=llm_scorer_config is not None,
        allow_remote_assets=False,
    )

    config = _rebuild_from_decisions(
        profiles,
        decisions,
        transient_blocking=blocking,
        llm_scorer_config=llm_scorer_config,
        memory_config=memory_config,
        standardization_config=_standardization,
    )

    # ── Preflight verification ──
    #
    # Stash the domain profile so preflight Check 1 can auto-repair
    # `config.domain` when the generated config references
    # domain-extracted columns (e.g. __title_key__, __model_norm__).
    # This fixes the DBLP-ACM crash where auto-config emitted
    # references to columns produced by a disabled pipeline step.
    from goldenmatch.core.autoconfig_verify import ConfigValidationError, preflight

    if domain_profile is not None and domain_profile.confidence > 0.7:
        config._domain_profile = domain_profile
    config._strict_autoconfig = strict

    report = preflight(
        df_input, config, profiles=profiles,
        allow_remote_assets=allow_remote_assets,
    )
    if report.has_errors:
        raise ConfigValidationError(report=report)
    config._preflight_report = report
    return config


def _rebuild_from_decisions(
    _profiles: list[ColumnProfile],
    decisions: AutoConfigDecisions,
    *,
    transient_blocking: BlockingConfig | None,
    llm_scorer_config: LLMScorerConfig | None,
    memory_config: MemoryConfig | None,
    standardization_config: StandardizationConfig | None = None,
) -> GoldenMatchConfig:
    """Assemble a GoldenMatchConfig from decisions (+ runtime hand-offs).

    Pure function of (profiles, decisions) plus non-decision runtime values:
      - `transient_blocking` carries non-decision attributes (learned_sample_size,
        learned_min_recall, skip_oversized, ...) that survive the rebuild.
      - `llm_scorer_config` / `memory_config` are runtime plumbing, not decisions.
      - `standardization_config` is auto-detected from column types (Change 1).

    Splitting this out lets a future iterative-tuning loop mutate `decisions`
    and re-call `_rebuild_from_decisions` without re-running profile_columns /
    build_matchkeys / build_blocking.

    ``_profiles`` is reserved (underscore-prefix unused parameter) for future
    iterative-tuning hooks that may re-examine column stats without rethreading
    them through the call chain.
    """
    # Rebuild final blocking from decisions, preserving runtime-only attrs
    # (learned_sample_size, learned_min_recall, skip_oversized, etc.) from
    # the transient blocking object produced above.
    final_blocking: BlockingConfig | None
    if transient_blocking is None:
        final_blocking = None
    else:
        final_blocking = transient_blocking.model_copy(update={
            "strategy": decisions.blocking_strategy,
            "keys": decisions.blocking_keys,
            "passes": decisions.blocking_passes,
        })

    return GoldenMatchConfig(
        matchkeys=decisions.matchkeys,
        blocking=final_blocking,
        golden_rules=GoldenRulesConfig(default_strategy="most_complete"),
        output=OutputConfig(),
        llm_scorer=llm_scorer_config,
        memory=memory_config,
        standardization=standardization_config,
    )


def auto_configure(files: list[tuple[str, str]]) -> GoldenMatchConfig:
    """Auto-generate a GoldenMatchConfig from input files.

    Args:
        files: List of (path, source_name) tuples.

    Returns:
        A fully populated GoldenMatchConfig ready for pipeline execution.
    """
    # Load and combine files
    dfs = []
    for path, _source_name in files:
        p = Path(path)
        if p.suffix.lower() in (".xlsx", ".xls"):
            df = pl.read_excel(p, engine="openpyxl")
        elif p.suffix.lower() == ".parquet":
            df = pl.read_parquet(p)
        else:
            df = pl.read_csv(p, encoding="utf8-lossy", infer_schema_length=10000, ignore_errors=True)
        dfs.append(df)

    combined = pl.concat(dfs, how="diagonal") if len(dfs) > 1 else dfs[0]
    return auto_configure_df(combined)


def build_probabilistic_matchkeys(profiles: list[ColumnProfile]) -> list[MatchkeyConfig]:
    """Generate Fellegi-Sunter probabilistic matchkeys from column profiles.

    Produces a single probabilistic matchkey using all matchable columns
    with appropriate comparison levels and partial thresholds.

    FS-autoconfig v2 (default ON since 2026-06-09; ``GOLDENMATCH_FS_AUTOCONFIG_V2=0``
    restores the legacy field set) curates a cleaner comparison set — the
    dominant scoring lever on error-heavy PII data (audit: historical_50k) and
    the fix for bibliographic data (DBLP-ACM). Four changes vs v1:
      1. **Admit date columns** (e.g. ``dob``) as ``levenshtein`` comparison
         fields. Birth date is the strongest person-identity discriminator;
         Splink leans on it (DamerauLevenshtein). v1 skipped all dates, scoring
         identity with no birth-date signal.
      2. **Drop redundant person-name composites** (``full_name`` /
         ``first_and_surname`` ...) when atomic given + family fields are both
         present. Correlated name comparisons violate FS conditional
         independence — N copies of one name (dis)agreement get N-counted,
         sinking corrupted-name true pairs below threshold.
      3. **Floor fuzzy fields at low cardinality** (drop a ``gender``-like field
         at cardinality ~0.002): negligible identity signal + unstable
         (non-monotonic) EM weights. Exact-scorer identifiers keep the #721
         no-floor admission (FS self-regulates them via u).
      4. **Admit free-text + multi-name fields** (``description`` /
         ``multi_name``) as ``token_sort`` comparison fields. v1 dropped both, so
         on bibliographic data (DBLP-ACM: title=description, authors=multi_name)
         only a near-constant ``venue`` survived → F-S mega-matched (P~0).
         DBLP-ACM via auto-config: F1 ~0.003 -> 0.879.
    """
    v2 = _fs_autoconfig_v2_enabled()

    # Atomic-name presence is computed once: composites are dropped only when
    # BOTH an atomic given-name and an atomic family-name field exist, so a
    # dataset carrying only ``full_name`` keeps it.
    if v2:
        names_norm = {_norm_colname(p.name) for p in profiles}
        atomic_name_present = bool(names_norm & _ATOMIC_GIVEN_NAMES) and bool(
            names_norm & _ATOMIC_FAMILY_NAMES
        )
    else:
        atomic_name_present = False

    fields = []
    for p in profiles:
        # #721: identifiers ARE admitted to the probabilistic (Fellegi-Sunter)
        # path. Unlike the exact path's cardinality BAND (#715), F-S needs no
        # lower floor: a weak (low-cardinality) identifier self-regulates because
        # its higher u (agreement among non-matches) yields a smaller EM weight,
        # so EM down-weights it rather than mega-clustering. m/u estimation and
        # blocking-field exclusion are EM's job at train time (train_em
        # blocking_fields=...), not this builder's.

        # Lever #1: admit date columns as edit-distance comparison fields.
        if v2 and p.col_type == "date":
            if p.cardinality_ratio >= 1.0:
                continue  # per-record timestamp surrogate: no shared-identity signal
            fields.append(MatchkeyField(
                field=p.name,
                scorer="levenshtein",
                transforms=["strip"],
                levels=3,
                partial_threshold=0.8,
            ))
            continue

        # Lever #4 (bibliographic): admit free-text + multi-name comparison
        # fields. v1 dropped `description` (the skip-list below) and `multi_name`
        # (absent from _SCORER_MAP), so on bibliographic data (DBLP-ACM:
        # title=description, authors=multi_name) only a near-constant `venue`
        # survived → F-S mega-matched (precision ~0). token_sort mirrors the
        # weighted builder's choice for these col_types. No cardinality gate:
        # a near-unique fuzzy field is HIGH-discrimination for F-S (agreement is
        # rare among non-matches → large EM weight), unlike an exact surrogate.
        if v2 and p.col_type in ("description", "multi_name"):
            fields.append(MatchkeyField(
                field=p.name,
                scorer="token_sort",
                transforms=["lowercase", "strip"],
                levels=3,
                partial_threshold=0.8,
            ))
            continue

        if p.col_type in ("numeric", "date", "description"):
            continue

        scorer_info = _SCORER_MAP.get(p.col_type)
        if not scorer_info:
            continue

        scorer, _weight, transforms = scorer_info

        # The one hard gate, applied uniformly to every exact-scorer field
        # (identifier/email/phone): a perfectly-unique column (card == 1.0) is a
        # per-record surrogate key -- it is never shared, so an agreement carries
        # zero shared-identity signal. Exclude it for config hygiene. (Mirrors the
        # exact path's >= 1.0 upper bound; previously the prob path gated none.)
        if scorer == "exact" and p.cardinality_ratio >= 1.0:
            continue

        # Lever #2a: drop redundant person-name composites when atomic parts exist.
        if v2 and p.col_type == "name" and atomic_name_present and (
            _norm_colname(p.name) in _COMPOSITE_NAME_FIELDS
        ):
            continue

        # Lever #2b: floor fuzzy (non-exact) fields at low cardinality. A field
        # like `gender` (~0.002) carries no identity signal and gives EM unstable
        # non-monotonic weights. Exact identifiers are exempt (#721 no-floor).
        if v2 and scorer != "exact" and p.cardinality_ratio < _PROB_FUZZY_CARD_FLOOR:
            continue

        # Refdata hook (mirrors build_matchkeys); see module-top fallback
        # for the unavailable-refdata case.
        scorer, transforms = _refdata_refine_matchkey_field(
            p.name, scorer, transforms, p.col_type,
        )

        # Determine comparison levels based on scorer type
        if scorer == "exact":
            levels = 2
            partial_threshold = 0.9
        else:
            levels = 3
            partial_threshold = 0.8

        fields.append(MatchkeyField(
            field=p.name,
            scorer=scorer,
            transforms=transforms,
            levels=levels,
            partial_threshold=partial_threshold,
        ))

    if not fields:
        return []

    return [MatchkeyConfig(
        name="probabilistic_auto",
        type="probabilistic",
        fields=fields,
    )]


def auto_configure_probabilistic_df(
    df: pl.DataFrame | pl.LazyFrame,
    llm_provider: str | None = None,
) -> GoldenMatchConfig:
    """Build a Fellegi-Sunter *probabilistic* config straight from a DataFrame.

    A reachable auto-config entry point for the probabilistic matchkey type.
    The heuristic / iterative ``auto_configure_df`` only emits exact + weighted
    matchkeys (its controller refit rules are weighted-specific), so the
    probabilistic lever was previously unreachable from the auto surface. This
    profiles the columns, builds blocking, and emits a single
    ``type="probabilistic"`` matchkey via ``build_probabilistic_matchkeys``.
    The m/u probabilities are EM-trained at dedupe time (``core/probabilistic.py``);
    this only produces the config.

    Non-iterative by design: it does NOT run the ``AutoConfigController`` (the
    EM training is the "fit", not the heuristic refit loop). Use
    ``auto_configure_df`` for the weighted/iterative path. The agentic config
    optimizer (spec ``2026-05-25-agentic-config-optimizer-design``) is the
    intended place to *search* weighted-vs-probabilistic empirically.
    """
    if isinstance(df, pl.LazyFrame):
        df = df.collect()

    profiles = profile_columns(df, llm_provider=llm_provider)
    matchkeys = build_probabilistic_matchkeys(profiles)
    if not matchkeys:
        raise ValueError(
            "No probabilistic matchkeys could be built: no matchable columns "
            "found (probabilistic skips numeric/date/description columns and "
            "perfectly-unique surrogate keys; provide name/address/email/phone/"
            "identifier-like fields)."
        )
    blocking = build_blocking(profiles, df, llm_provider=llm_provider)
    blocking = _maybe_prune_blocking_passes(blocking, df)
    blocking = apply_quality_aware_blocking(blocking, profiles, df)
    # Lever #3: diversify onto orthogonal stable keys (date-year, postcode/zip,
    # identifier) so the FS candidate set isn't gated entirely on (corrupted)
    # name keys. Purely additive — recall ceiling can only rise.
    blocking = _diversify_probabilistic_blocking(blocking, profiles)
    return GoldenMatchConfig(
        matchkeys=matchkeys,
        blocking=blocking,
        golden_rules=GoldenRulesConfig(default_strategy="most_complete"),
        output=OutputConfig(),
    )


def _diversify_probabilistic_blocking(
    blocking: BlockingConfig | None, profiles: list[ColumnProfile]
) -> BlockingConfig | None:
    """Add orthogonal stable-key blocking passes for the probabilistic path.

    Auto-config blocking tends to key entirely on the name column(s); on
    error-heavy PII data that caps the candidate ceiling (audit: historical_50k
    blocking_recall 0.585 — 42% of true pairs never co-block because their names
    are corrupted even though dob/postcode agree). This appends single-field
    passes on orthogonal anchors — a date column's YEAR (``substring:0:4``) and
    postcode/zip/identifier columns — mirroring Splink's rule union. Purely
    additive (recall can only rise; scoring still decides precision). Skips
    high-null and perfectly-unique columns, and any (field, transforms) signature
    already present. Default ON; ``GOLDENMATCH_FS_AUTOCONFIG_V2=0`` disables it.
    """
    if blocking is None or not _fs_autoconfig_v2_enabled():
        return blocking

    existing: set[tuple] = set()
    for k in list(blocking.keys or []) + list(blocking.passes or []):
        existing.add((tuple(k.fields), tuple(k.transforms or [])))

    new_passes: list[BlockingKeyConfig] = []

    def _add(fields: list[str], transforms: list[str]) -> None:
        sig = (tuple(fields), tuple(transforms))
        if sig not in existing:
            new_passes.append(BlockingKeyConfig(fields=fields, transforms=transforms))
            existing.add(sig)

    for p in profiles:
        # Additive passes tolerate higher null rates than a primary key: the
        # static blocker filters null/sentinel block keys (blocker.py ~272), so a
        # null-valued row is simply absent from THIS pass (no giant null block)
        # while still covered by the name passes. Error-heavy PII (historical_50k
        # dob/postcode ~24% null) is exactly where these keys matter most.
        if p.null_rate > 0.6:
            continue
        if p.col_type == "date":
            _add([p.name], ["substring:0:4"])  # birth YEAR — tolerant of day/month errors
        elif p.col_type in ("zip", "identifier", "phone") and p.cardinality_ratio < 1.0:
            _add([p.name], ["strip"])

    if not new_passes:
        return blocking

    # Fold into a multi_pass union; keep the original keys/passes as passes.
    base_passes = list(blocking.passes or []) or list(blocking.keys or [])
    return blocking.model_copy(update={
        "strategy": "multi_pass",
        "passes": base_passes + new_passes,
        "auto_select": False,
    })
