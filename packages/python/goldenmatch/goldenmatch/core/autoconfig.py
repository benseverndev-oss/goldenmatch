"""Auto-configuration engine for GoldenMatch zero-config mode."""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

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
        if cardinality_ratio >= 0.95:
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

    profiles = []
    for col_name in columns:
        # Skip internal columns
        if col_name.startswith("__"):
            continue

        dtype = str(df[col_name].dtype)

        # Get non-null string values for profiling
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

    # LLM correction pass for ambiguous columns
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


def build_matchkeys(
    profiles: list[ColumnProfile], df: pl.DataFrame | None = None,
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
        # must be plausibly unique. Requiring cardinality_ratio >= 0.5 ensures
        # at least half the values are distinct before the column can back an
        # exact matchkey. This catches low-cardinality numeric columns that
        # get misclassified by upstream transforms — e.g. a 4-digit year
        # reshaped into an ISO date can look phone-shaped to the phone
        # classifier, collapsing every row sharing that year into one cluster.
        # TODO(autoconfig): replace this blanket threshold with per-type
        # cardinality thresholds once we have empirical data for each col_type.
        if scorer == "exact" and p.cardinality_ratio > 0 and p.cardinality_ratio < 0.5:
            reason = (
                f"cardinality_ratio={p.cardinality_ratio:.4f} < 0.5 "
                f"-- lacks identifier-level uniqueness"
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

        mf = MatchkeyField(
            field=p.name,
            scorer=scorer,
            weight=weight,
            transforms=transforms,
        )

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
    #   - admit `identifier` (and the high-cardinality `email`/`phone` types)
    #     ONLY when the column genuinely GROUPS records: non-singleton blocks
    #     (`_max_block_size > 1`), `cardinality_ratio < 1.0`, and a non-null
    #     distinct ratio below the blocking gate (rejects surrogate keys like
    #     npi/phone/email whose non-null values are ~unique per record and
    #     whose only large block is the null bucket);
    #   - relax the single-key null ceiling to 0.6 for the component role so a
    #     ~50%-null zip5 qualifies.
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
    _high_card_types = ("identifier", "email", "phone")

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


def _check_source_overlap(df: pl.DataFrame, col: str) -> float:
    """Compute value overlap ratio for a column across sources.

    Returns |intersection| / |union| of unique values per source.
    Returns 1.0 if no __source__ column or only one source (no check needed).
    """
    if "__source__" not in df.columns:
        return 1.0

    sources = df["__source__"].unique().to_list()
    if len(sources) < 2:
        return 1.0

    value_sets = []
    for src in sources:
        vals = set(
            df.filter(pl.col("__source__") == src)[col]
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


# ─────────────────────────────────────────────────────────────────────────────


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

    # #491: ANN blocking is a FALLBACK, not a preempt. ANN is emitted ONLY
    # when an embedding-bearing column exists, the dataset is at scale, AND no
    # bounded exact blocking key is available. A strong exact identifier still
    # wins over ANN when present — we evaluate the exact/safe-exact path first
    # (below) and only fall through to ANN when that path produced no usable
    # key, before the name/compound fallback.
    #
    # STRICT invariant preserved: ANN is never emitted without an embedding-
    # bearing column. ANNBlocker needs a vector column (blocker.py raises
    # ValueError if ``ann_column`` is unset). The embedding signal is a profile
    # with ``col_type == "description"`` — those columns become
    # ``record_embedding`` scorers in build_matchkeys (line ~852) and carry the
    # vectors ANN embeds. No embedding column => never ann.
    #
    # ANN_MIN_ROWS default 100_000; env-overridable via
    # GOLDENMATCH_ANN_MIN_ROWS (mirrors the env-threshold pattern used by
    # GOLDENMATCH_AUTOCONFIG_BACKEND_THRESHOLD below).
    _ANN_MIN_ROWS_DEFAULT = 100_000
    _ann_raw = os.environ.get("GOLDENMATCH_ANN_MIN_ROWS")
    if _ann_raw is not None:
        try:
            ann_min_rows = int(_ann_raw)
        except ValueError:
            logger.warning(
                "GOLDENMATCH_ANN_MIN_ROWS=%r is not an int; ignoring and "
                "using default %d.", _ann_raw, _ANN_MIN_ROWS_DEFAULT,
            )
            ann_min_rows = _ANN_MIN_ROWS_DEFAULT
    else:
        ann_min_rows = _ANN_MIN_ROWS_DEFAULT

    _embedding_cols = [p for p in profiles if p.col_type == "description"]
    _ann_eligible = bool(_embedding_cols) and effective_n_full >= ann_min_rows

    def _projected_ratio(p: ColumnProfile) -> float:
        """Sample-corrected cardinality_ratio for the gate."""
        if effective_n_full <= sample_n:
            return p.cardinality_ratio
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

    def _max_block_size(col_name: str) -> int:
        """Largest group size when blocking on this column."""
        return int(df.group_by(col_name).len().get_column("len").max() or 0)  # pyright: ignore[reportArgumentType]  # polars max() typed as PythonLiteral; "len" column is int64 at runtime

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
    # pairs -> an 18-min run. project_max_block_size with full_n == df.height
    # is the identity (v0 path uses exact block sizes -> correct).
    from goldenmatch.core.blocking_candidates import project_max_block_size

    def _projected_block(fields: list[str]) -> int:
        try:
            sample_mb = int(df.group_by(fields).len().get_column("len").max() or 0)  # pyright: ignore[reportArgumentType]  # polars max() typed as PythonLiteral; "len" is int64 at runtime
        except Exception:  # pragma: no cover -- defensive
            return effective_n_full  # fail-safe: treat unprojectable key as maximally oversized -> dropped
        return project_max_block_size(sample_mb, df.height, effective_n_full)

    def _pass_is_bounded(key: BlockingKeyConfig) -> bool:
        return _projected_block(key.fields) <= max_safe_block

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
        candidates = exact_cols_sorted[:5]
        # Filter out columns that create oversized blocks
        safe_exact = [p for p in candidates if _max_block_size(p.name) <= max_safe_block]
        if safe_exact:
            best = max(safe_exact, key=lambda p: df[p.name].n_unique())
            transforms = ["lowercase", "strip"] if best.col_type == "email" else ["strip"]
            return BlockingConfig(
                keys=[BlockingKeyConfig(fields=[best.name], transforms=transforms)],
            )
        # All exact columns create oversized blocks — fall through
        logger.warning(
            "Exact blocking columns all produce oversized blocks (>%d), "
            "falling through to name-based blocking",
            max_safe_block,
        )

    # #491: ANN fallback. We only reach here when no bounded exact blocking
    # key was found (the exact path above returns whenever it had a usable
    # safe_exact key). If embeddings are present at scale, prefer ANN over the
    # name/compound fallback below.
    if _ann_eligible:
        ann_col = _embedding_cols[0].name
        logger.info(
            "Auto-selecting ANN blocking (fallback): no bounded exact "
            "blocking key available, embedding column %r present and "
            "n_rows=%d >= ANN_MIN_ROWS=%d. See #491.",
            ann_col, effective_n_full, ann_min_rows,
        )
        return BlockingConfig(strategy="ann", ann_column=ann_col)

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


# ── Scale-aware backend selection ─────────────────────────────────────────
#
# Zero-config promise: users run `gm.dedupe_df(big_df)` and get a working
# pipeline. The default polars-direct backend OOMs around 5M on 16 GB; the
# duckdb pair-store backend fits 5M in ~12 GB and is the recorded
# scale-audit baseline (CLAUDE.md lines 90-92). Selecting it automatically
# at large N is the smallest change that makes "zero-config at 5M" a thing
# that exists.
#
# Override knobs:
#   GOLDENMATCH_AUTOCONFIG_BACKEND=0       → disable auto-selection entirely
#   GOLDENMATCH_AUTOCONFIG_BACKEND=duckdb  → force duckdb regardless of N
#   GOLDENMATCH_AUTOCONFIG_BACKEND_THRESHOLD=<int>
#       → override the row-count cutoff (default 1_000_000)

_AUTOCONFIG_BACKEND_DEFAULT_THRESHOLD = 1_000_000


def _scale_aware_backend(row_count: int) -> str | None:  # pyright: ignore[reportUnusedFunction]
    """DEPRECATED: frozen PR-#239 shim, kept for one release.

    The controller v3 planner (``apply_planner_rules`` in
    ``core/autoconfig_planner.py``) is now the source of truth for
    backend selection during ``auto_configure_df``. This helper is
    frozen at PR-#239 behavior (single-threshold ``"duckdb"`` if
    ``row_count >= GOLDENMATCH_AUTOCONFIG_BACKEND_THRESHOLD``) and does
    NOT reflect the full v3 rule table -- external callers consuming
    the shim get stable behavior across the deprecation window. Will
    be removed in v2.0.

    Spec: docs/superpowers/specs/2026-05-15-controller-v3-planner-design.md
    §Backward compatibility.
    """
    import warnings

    warnings.warn(
        "_scale_aware_backend is deprecated; the v3 planner "
        "(goldenmatch.core.autoconfig_planner.apply_planner_rules) is "
        "now the source of truth. Will be removed in v2.0.",
        DeprecationWarning,
        stacklevel=2,
    )
    override = os.environ.get("GOLDENMATCH_AUTOCONFIG_BACKEND")
    if override is not None:
        token = override.strip().lower()
        if token in ("0", "false", "disabled", ""):
            return None
        if token in ("none", "null"):
            return None
        # Pass through explicit backend names ("duckdb", "ray").
        return token

    raw = os.environ.get("GOLDENMATCH_AUTOCONFIG_BACKEND_THRESHOLD")
    if raw is not None:
        try:
            threshold = int(raw)
        except ValueError:
            logger.warning(
                "GOLDENMATCH_AUTOCONFIG_BACKEND_THRESHOLD=%r is not an int; "
                "ignoring and using default.", raw,
            )
            threshold = _AUTOCONFIG_BACKEND_DEFAULT_THRESHOLD
    else:
        threshold = _AUTOCONFIG_BACKEND_DEFAULT_THRESHOLD

    if row_count >= threshold:
        return "duckdb"
    return None


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
    }
    config, profile, history = controller.run(
        df,
        reference=reference,
        v0_kwargs=v0_kw,
        skip_finalize=_skip_finalize,
        confidence_required=confidence_required,
        allow_red_config=allow_red_config,
        planning_effort=effort,
    )
    # Surface the resolved tier on the committed config for observability
    # (telemetry, YAML round-trip). No-op for the default "normal".
    try:
        config.planning_effort = effort  # type: ignore[assignment]
    except Exception:
        pass

    # Backend selection is now driven by the controller v3 planner inside
    # AutoConfigController.run -- it captures RuntimeProfile, extrapolates
    # the committed BlockingProfile to full-row count, and writes the
    # selected backend onto config via ExecutionPlan.apply_to. The legacy
    # _scale_aware_backend env-var path (PR #239) is preserved as a frozen
    # shim for external callers during the deprecation window but is no
    # longer consulted from this entry point.

    _LAST_CONTROLLER_RUN.set((profile, history))
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
    matchkeys = build_matchkeys(profiles, df=df)

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
