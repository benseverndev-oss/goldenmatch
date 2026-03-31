"""Column context — shared column metadata flowing between pipeline stages."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


# ── Enums for type-safe column classification ────────────────────────────


class ColumnType(str, Enum):
    """Semantic column type. str base class allows direct string comparison."""

    NAME = "name"
    EMAIL = "email"
    PHONE = "phone"
    DATE = "date"
    GEO = "geo"
    ADDRESS = "address"
    ZIP = "zip"
    IDENTIFIER = "identifier"
    NUMERIC = "numeric"
    STRING = "string"
    DESCRIPTION = "description"


class CardinalityBand(str, Enum):
    """Where a column sits in the IQR cardinality distribution."""

    UNSET = ""
    LOW = "low"
    MID = "mid"
    HIGH = "high"
    SKIP = "skip"  # numeric/date columns excluded from IQR


# ── Constants ────────────────────────────────────────────────────────────

MIN_CONFIDENCE = 0.3  # Floor — below this, the signal is too weak to act on

# Identifier types — columns that identify an entity (used for matching)
IDENTIFIER_TYPES = {ColumnType.NAME, ColumnType.EMAIL, ColumnType.PHONE}

# Types that should never be identifiers regardless of cardinality
NEVER_IDENTIFIER_TYPES = {ColumnType.DATE, ColumnType.NUMERIC, ColumnType.IDENTIFIER}


# ── ColumnContext dataclass ──────────────────────────────────────────────


@dataclass
class ColumnContext:
    """Metadata about a single column, accumulated across pipeline stages.

    Built by GoldenCheck (scan), enriched by GoldenFlow (transform),
    consumed by GoldenMatch (auto-config) to avoid re-profiling.
    """

    name: str
    inferred_type: ColumnType = ColumnType.STRING
    null_rate: float = 0.0
    cardinality: int = 0
    is_identifier: bool = False
    transforms_applied: list[str] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)
    confidence: float = 0.5
    cardinality_band: CardinalityBand = CardinalityBand.UNSET

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("ColumnContext.name must be non-empty")
        if not (0.0 <= self.null_rate <= 1.0):
            raise ValueError(f"null_rate must be in [0, 1], got {self.null_rate}")
        if self.cardinality < 0:
            raise ValueError(f"cardinality must be >= 0, got {self.cardinality}")
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"confidence must be in [0, 1], got {self.confidence}")
        # Coerce string inferred_type to enum if needed (for backward compat)
        if isinstance(self.inferred_type, str) and not isinstance(self.inferred_type, ColumnType):
            try:
                self.inferred_type = ColumnType(self.inferred_type)
            except ValueError:
                logger.warning("Unknown column type '%s', defaulting to STRING", self.inferred_type)
                self.inferred_type = ColumnType.STRING
        if isinstance(self.cardinality_band, str) and not isinstance(self.cardinality_band, CardinalityBand):
            try:
                self.cardinality_band = CardinalityBand(self.cardinality_band)
            except ValueError:
                self.cardinality_band = CardinalityBand.UNSET


# ── Name-based classification ────────────────────────────────────────────

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


def _classify_by_name(col_name: str) -> ColumnType | None:
    """Classify column by name pattern matching."""
    if _DATE_PATTERNS.search(col_name):
        return ColumnType.DATE
    if _EMAIL_PATTERNS.search(col_name):
        return ColumnType.EMAIL
    if _ZIP_PATTERNS.search(col_name):
        return ColumnType.ZIP
    if _GEO_PATTERNS.search(col_name):
        return ColumnType.GEO
    if _ADDRESS_PATTERNS.search(col_name):
        return ColumnType.ADDRESS
    if _PHONE_PATTERNS.search(col_name):
        return ColumnType.PHONE
    if _NAME_PATTERNS.search(col_name):
        return ColumnType.NAME
    if _ID_PATTERNS.search(col_name):
        return ColumnType.IDENTIFIER
    return None


# ── Cardinality IQR banding ──────────────────────────────────────────────


def _compute_cardinality_bands(contexts: list[ColumnContext]) -> None:
    """Classify each column's cardinality as low/mid/high using IQR."""
    string_contexts = [c for c in contexts if c.inferred_type not in (ColumnType.NUMERIC, ColumnType.DATE)]
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
        if ctx.inferred_type in (ColumnType.NUMERIC, ColumnType.DATE):
            ctx.cardinality_band = CardinalityBand.SKIP
            continue

        if ctx.cardinality <= q1:
            ctx.cardinality_band = CardinalityBand.LOW
        elif ctx.cardinality >= q3:
            ctx.cardinality_band = CardinalityBand.HIGH
        else:
            ctx.cardinality_band = CardinalityBand.MID


def _apply_cardinality_signal(contexts: list[ColumnContext]) -> None:
    """Refine is_identifier using cardinality band as a second signal."""
    for ctx in contexts:
        if ctx.inferred_type in NEVER_IDENTIFIER_TYPES:
            ctx.is_identifier = False
            continue

        has_name_signal = ctx.inferred_type in IDENTIFIER_TYPES
        band = ctx.cardinality_band

        if has_name_signal and band == CardinalityBand.MID:
            ctx.is_identifier = True
            ctx.confidence = min(ctx.confidence + 0.15, 1.0)
        elif has_name_signal and band == CardinalityBand.LOW:
            ctx.is_identifier = False
            ctx.confidence = max(ctx.confidence - 0.2, MIN_CONFIDENCE)
            logger.info(
                "Downgraded %s: name pattern but low cardinality (%d unique)",
                ctx.name, ctx.cardinality,
            )
        elif has_name_signal and band == CardinalityBand.HIGH:
            ctx.is_identifier = True
            ctx.confidence = min(ctx.confidence + 0.05, 1.0)
        elif not has_name_signal and band == CardinalityBand.MID:
            if ctx.inferred_type == ColumnType.STRING:
                ctx.is_identifier = True
                ctx.confidence = 0.5
                logger.info(
                    "Promoted %s to candidate identifier: mid cardinality (%d unique)",
                    ctx.name, ctx.cardinality,
                )
        elif not has_name_signal and band == CardinalityBand.LOW:
            ctx.is_identifier = False
        elif not has_name_signal and band == CardinalityBand.HIGH:
            ctx.is_identifier = False

        if ctx.null_rate > 0.3 and ctx.is_identifier:
            ctx.confidence = max(ctx.confidence - 0.1, MIN_CONFIDENCE)


# ── Context builders ─────────────────────────────────────────────────────


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

    if not hasattr(profile, "columns") or not profile.columns:
        logger.warning(
            "build_contexts_from_check: profile has no usable 'columns' attribute (type=%s)",
            type(profile).__name__,
        )
        return []

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
            is_identifier=semantic_type in IDENTIFIER_TYPES,
            confidence=0.8 if semantic_type != ColumnType.STRING else 0.4,
        )
        contexts[col_profile.name] = ctx

    context_list = list(contexts.values())

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
            ctx.inferred_type = ColumnType.DATE
            ctx.is_identifier = False
            ctx.confidence = 0.95


def _normalize_dtype(raw_type: str) -> ColumnType:
    """Map Polars dtype to a ColumnType."""
    t = raw_type.lower().strip()
    if "int" in t or "float" in t:
        return ColumnType.NUMERIC
    if "date" in t or "time" in t:
        return ColumnType.DATE
    if "bool" in t:
        return ColumnType.STRING
    return ColumnType.STRING
