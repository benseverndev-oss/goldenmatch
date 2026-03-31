"""Column context — shared column metadata flowing between pipeline stages."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ColumnContext:
    """Metadata about a single column, accumulated across pipeline stages.

    Built by GoldenCheck (scan), enriched by GoldenFlow (transform),
    consumed by GoldenMatch (auto-config) to avoid re-profiling.
    """

    name: str
    inferred_type: str = "string"
    # One of: name, email, phone, date, geo, address, zip, identifier, numeric, string, description
    null_rate: float = 0.0
    cardinality: int = 0
    is_identifier: bool = False
    # True for columns used for matching (name, email, phone)
    # False for attribute columns (address, city, gender, date)
    transforms_applied: list[str] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)
    confidence: float = 0.5
    cardinality_band: str = ""
    # One of: "", "low", "mid", "high", "skip" (skip = numeric/date, excluded from IQR)

MIN_CONFIDENCE = 0.3  # Floor for confidence — below this, the signal is too weak to act on


# Identifier types — columns that identify an entity (used for matching)
_IDENTIFIER_TYPES = {"name", "email", "phone"}

# Attribute types — columns that describe an entity (not used for matching)
_ATTRIBUTE_TYPES = {"date", "geo", "address", "numeric", "identifier", "string", "description"}

# Types that should never be identifiers regardless of cardinality
_NEVER_IDENTIFIER_TYPES = {"date", "numeric", "identifier"}

# ── Name-based classification (same patterns as GoldenMatch autoconfig) ──

_NAME_PATTERNS = re.compile(
    r"(^name$|first.?name|last.?name|full.?name|fname|lname|surname|given.?name|middle)",
    re.IGNORECASE,
)
_EMAIL_PATTERNS = re.compile(r"(email|e.?mail|email.?addr)", re.IGNORECASE)
_PHONE_PATTERNS = re.compile(r"(phone|tel|mobile|fax|cell)", re.IGNORECASE)
_ZIP_PATTERNS = re.compile(r"(zip|postal|postcode|zip.?code)", re.IGNORECASE)
_ADDRESS_PATTERNS = re.compile(r"(address|street|addr|line.?1|line.?2)", re.IGNORECASE)
_GEO_PATTERNS = re.compile(r"(city|^state$|state.?cd|^country$|province|region|county)", re.IGNORECASE)
_DATE_PATTERNS = re.compile(r"(date|_dt$|_date$|registr|created|updated|birth.?d|dob)", re.IGNORECASE)
_ID_PATTERNS = re.compile(r"(^id$|^key$|^code$|^sku$|_id$|_key$)", re.IGNORECASE)


def _classify_by_name(col_name: str) -> str | None:
    """Classify column by name pattern matching."""
    if _DATE_PATTERNS.search(col_name):
        return "date"
    if _EMAIL_PATTERNS.search(col_name):
        return "email"
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
    if _ID_PATTERNS.search(col_name):
        return "identifier"
    return None


def _compute_cardinality_bands(contexts: list[ColumnContext]) -> None:
    """Classify each column's cardinality as low/mid/high using IQR.

    The interquartile range (IQR) identifies the middle band where matching
    columns typically live — high enough cardinality to be discriminating
    (not enums like gender), low enough to have natural duplicates (not
    unique IDs like SSN).

    Modifies contexts in place.
    """
    # Only consider string-typed columns (numerics and dates don't match by cardinality)
    string_contexts = [c for c in contexts if c.inferred_type not in ("numeric", "date")]
    if len(string_contexts) < 3:
        logger.debug(
            "Skipping IQR cardinality bands: only %d string-type columns (need >= 3)",
            len(string_contexts),
        )
        return

    cardinalities = sorted(c.cardinality for c in string_contexts)
    n = len(cardinalities)
    q1 = cardinalities[n // 4]
    q3 = cardinalities[3 * n // 4]

    logger.info("Cardinality IQR: Q1=%d, Q3=%d (from %d columns)", q1, q3, n)

    for ctx in contexts:
        if ctx.inferred_type in ("numeric", "date"):
            ctx.cardinality_band = "skip"
            continue

        if ctx.cardinality <= q1:
            ctx.cardinality_band = "low"
        elif ctx.cardinality >= q3:
            ctx.cardinality_band = "high"
        else:
            ctx.cardinality_band = "mid"


def _apply_cardinality_signal(contexts: list[ColumnContext]) -> None:
    """Refine is_identifier using cardinality band as a second signal.

    Rules:
    - Name pattern + mid cardinality → high confidence identifier (confirmed)
    - Name pattern + low cardinality → downgrade (e.g., "name_prefix" with 4 values)
    - Name pattern + high cardinality → keep identifier, slight confidence boost
    - No name pattern + mid cardinality → candidate identifier (lower confidence)
    - No name pattern + low cardinality → definitely an attribute
    - No name pattern + high cardinality → likely unique ID or freetext, not for matching
    - Any type in _NEVER_IDENTIFIER_TYPES → never an identifier
    - High null rate (>30%) → reduce identifier confidence
    """
    for ctx in contexts:
        # Types that are never identifiers
        if ctx.inferred_type in _NEVER_IDENTIFIER_TYPES:
            ctx.is_identifier = False
            continue

        has_name_signal = ctx.inferred_type in _IDENTIFIER_TYPES
        band = ctx.cardinality_band

        if has_name_signal and band == "mid":
            # Name heuristic confirmed by cardinality — strong identifier
            ctx.is_identifier = True
            ctx.confidence = min(ctx.confidence + 0.15, 1.0)
        elif has_name_signal and band == "low":
            # Name pattern but too few values — probably not a real name column
            ctx.is_identifier = False
            ctx.confidence = max(ctx.confidence - 0.2, MIN_CONFIDENCE)
            logger.info(
                "Downgraded %s: name pattern but low cardinality (%d unique)",
                ctx.name, ctx.cardinality,
            )
        elif has_name_signal and band == "high":
            # Name/email/phone with very high cardinality (near-unique) — still identifier
            ctx.is_identifier = True
            ctx.confidence = min(ctx.confidence + 0.05, 1.0)
        elif not has_name_signal and band == "mid":
            # No name pattern but cardinality suggests it could be an identifier
            if ctx.inferred_type == "string":
                ctx.is_identifier = True
                ctx.confidence = 0.5  # lower confidence — cardinality-only signal
                logger.info(
                    "Promoted %s to candidate identifier: mid cardinality (%d unique)",
                    ctx.name, ctx.cardinality,
                )
        elif not has_name_signal and band == "low":
            # No name pattern, low cardinality — definitely an attribute
            ctx.is_identifier = False
        elif not has_name_signal and band == "high":
            # No name pattern, very high cardinality — likely unique ID or freetext
            ctx.is_identifier = False

        # High null rate reduces identifier confidence
        if ctx.null_rate > 0.3 and ctx.is_identifier:
            ctx.confidence = max(ctx.confidence - 0.1, MIN_CONFIDENCE)


def build_contexts_from_check(findings: list, profile) -> list[ColumnContext]:
    """Build ColumnContexts from GoldenCheck scan results.

    Three signals are combined:
    1. Column name heuristics (regex patterns for name, email, phone, etc.)
    2. GoldenCheck profile data (null rate, cardinality, dtype)
    3. Cardinality IQR bands (identifies likely matching columns by distribution)
    """
    contexts: dict[str, ColumnContext] = {}

    if profile is None:
        logger.warning(
            "build_contexts_from_check: profile is None — column context pipeline "
            "will not produce contexts. GoldenMatch will fall back to auto-configure."
        )
        return []

    if not hasattr(profile, "columns"):
        logger.warning(
            "build_contexts_from_check: profile has no 'columns' attribute (type=%s)",
            type(profile).__name__,
        )
        return []

    if profile and hasattr(profile, "columns"):
        for col_profile in profile.columns:
            semantic_type = _classify_by_name(col_profile.name)
            if not semantic_type:
                semantic_type = _normalize_dtype(
                    col_profile.inferred_type if hasattr(col_profile, "inferred_type") else "string"
                )

            ctx = ColumnContext(
                name=col_profile.name,
                inferred_type=semantic_type,
                null_rate=col_profile.null_pct if hasattr(col_profile, "null_pct") else 0.0,
                cardinality=col_profile.unique_count if hasattr(col_profile, "unique_count") else 0,
                is_identifier=semantic_type in _IDENTIFIER_TYPES,
                confidence=0.8 if semantic_type != "string" else 0.4,
            )
            contexts[col_profile.name] = ctx

    context_list = list(contexts.values())

    # Compute cardinality bands and refine is_identifier
    _compute_cardinality_bands(context_list)
    _apply_cardinality_signal(context_list)

    # Enrich with findings
    for f in findings:
        col_name = f.get("column") if isinstance(f, dict) else getattr(f, "column", None)
        check = f.get("check") if isinstance(f, dict) else getattr(f, "check", "")
        message = f.get("message") if isinstance(f, dict) else getattr(f, "message", "")

        if not col_name or col_name not in contexts:
            continue

        ctx = contexts[col_name]
        ctx.findings.append(f"{check}: {str(message)[:80]}")

    return context_list


def enrich_contexts_from_flow(contexts: list[ColumnContext], manifest) -> None:
    """Enrich ColumnContexts with GoldenFlow transform information."""
    if not manifest:
        return
    if not hasattr(manifest, "records"):
        logger.warning(
            "enrich_contexts_from_flow: manifest has no 'records' attribute (type=%s)",
            type(manifest).__name__,
        )
        return

    ctx_lookup = {c.name: c for c in contexts}

    for record in manifest.records:
        col_name = record.column if hasattr(record, "column") else None
        transform = record.transform if hasattr(record, "transform") else None
        affected = record.affected_rows if hasattr(record, "affected_rows") else 0

        if not col_name or col_name not in ctx_lookup:
            continue

        ctx = ctx_lookup[col_name]
        if affected > 0 and transform is not None:
            ctx.transforms_applied.append(transform)

        # Date transforms confirm the column is a date
        if transform and "date" in transform.lower():
            ctx.inferred_type = "date"
            ctx.is_identifier = False
            ctx.confidence = 0.95


def _normalize_dtype(raw_type: str) -> str:
    """Map Polars dtype to a basic category."""
    t = raw_type.lower().strip()
    if "int" in t or "float" in t:
        return "numeric"
    if "date" in t or "time" in t:
        return "date"
    if "bool" in t:
        return "string"
    return "string"
