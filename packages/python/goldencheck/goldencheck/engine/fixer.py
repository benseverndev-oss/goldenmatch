"""Auto-fix engine — applies automated data quality fixes."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

import polars as pl

from goldencheck.models.finding import Finding

__all__ = ["apply_fixes", "FixReport", "FixEntry"]


@dataclass
class FixEntry:
    column: str
    fix_type: str
    rows_affected: int
    sample_before: list[str] = field(default_factory=list)
    sample_after: list[str] = field(default_factory=list)


@dataclass
class FixReport:
    entries: list[FixEntry] = field(default_factory=list)

    @property
    def total_rows_fixed(self) -> int:
        return sum(e.rows_affected for e in self.entries)


# ---------------------------------------------------------------------------
# Individual fix functions — receive Series, return Series (immutable)
# ---------------------------------------------------------------------------

_INVISIBLE_CHARS = re.compile(r"[\u200b\u200c\u200d\uFEFF\u00AD\u2060]")

_SMART_QUOTES = {
    "\u201c": '"', "\u201d": '"',
    "\u2018": "'", "\u2019": "'",
    "\u2013": "-", "\u2014": "-",
    "\u2026": "...",
}


def _trim_whitespace(s: pl.Series) -> pl.Series:
    if s.dtype not in (pl.Utf8, pl.String):
        return s
    return s.str.strip_chars()


def _remove_invisible_chars(s: pl.Series) -> pl.Series:
    if s.dtype not in (pl.Utf8, pl.String):
        return s
    return s.map_elements(
        lambda v: _INVISIBLE_CHARS.sub("", v) if isinstance(v, str) else v,
        return_dtype=pl.String,
    )


def _normalize_unicode(s: pl.Series) -> pl.Series:
    if s.dtype not in (pl.Utf8, pl.String):
        return s
    return s.map_elements(
        lambda v: unicodedata.normalize("NFC", v) if isinstance(v, str) else v,
        return_dtype=pl.String,
    )


def _fix_smart_quotes(s: pl.Series) -> pl.Series:
    if s.dtype not in (pl.Utf8, pl.String):
        return s

    def _replace(v):
        if not isinstance(v, str):
            return v
        for old, new in _SMART_QUOTES.items():
            v = v.replace(old, new)
        return v

    return s.map_elements(_replace, return_dtype=pl.String)


def _standardize_case(s: pl.Series, findings: list[Finding], column: str) -> pl.Series:
    """Match dominant casing for low-cardinality columns."""
    if s.dtype not in (pl.Utf8, pl.String):
        return s
    non_null = s.drop_nulls()
    if len(non_null) == 0 or non_null.n_unique() > 20:
        return s
    lowered = non_null.str.to_lowercase()
    pairs = pl.DataFrame({"original": non_null, "lowered": lowered})
    dominant = (
        pairs.group_by("lowered")
        .agg(pl.col("original").mode().first().alias("dominant"))
    )
    mapping = dict(zip(dominant["lowered"].to_list(), dominant["dominant"].to_list()))
    return s.map_elements(
        lambda v: mapping.get(v.lower(), v) if isinstance(v, str) else v,
        return_dtype=pl.String,
    )


def _strip_control_chars(s: pl.Series) -> pl.Series:
    """Remove control characters (except newline/tab)."""
    if s.dtype not in (pl.Utf8, pl.String):
        return s
    return s.map_elements(
        lambda v: re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', v) if isinstance(v, str) else v,
        return_dtype=pl.String,
    )


def _coerce_numeric(s: pl.Series) -> pl.Series:
    """Attempt to cast string column to numeric."""
    if s.dtype not in (pl.Utf8, pl.String):
        return s
    try:
        return s.cast(pl.Float64, strict=False)
    except Exception:
        return s


def _fill_nulls_with_mode(s: pl.Series) -> pl.Series:
    """Fill null values with the column's mode (most frequent value)."""
    if s.null_count() == 0:
        return s
    mode_val = s.drop_nulls().mode()
    if len(mode_val) == 0:
        return s
    return s.fill_null(mode_val[0])


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

_SAFE_FIXES = [
    ("trim_whitespace", _trim_whitespace),
    ("remove_invisible_chars", _remove_invisible_chars),
    ("normalize_unicode", _normalize_unicode),
    ("fix_smart_quotes", _fix_smart_quotes),
]


def apply_fixes(
    df: pl.DataFrame,
    findings: list[Finding],
    mode: str = "safe",
    *,
    force: bool = False,
) -> tuple[pl.DataFrame, FixReport]:
    """Apply fixes to a DataFrame. Returns (fixed_df, report)."""
    if mode == "aggressive" and not force:
        raise ValueError(
            "Aggressive mode modifies data (drops rows, coerces types). "
            "Pass force=True or use --force on the CLI to confirm."
        )

    report = FixReport()
    result = df.clone()

    for col_name in result.columns:
        col = result[col_name]

        # Safe fixes (always run)
        for fix_name, fix_fn in _SAFE_FIXES:
            fixed = fix_fn(col)
            changed = (col.cast(pl.String).fill_null("") != fixed.cast(pl.String).fill_null(""))
            n_changed = int(changed.sum())
            if n_changed > 0:
                before = col.filter(changed).head(3).cast(pl.String).to_list()
                after = fixed.filter(changed).head(3).cast(pl.String).to_list()
                report.entries.append(FixEntry(
                    column=col_name,
                    fix_type=fix_name,
                    rows_affected=n_changed,
                    sample_before=[str(v) for v in before],
                    sample_after=[str(v) for v in after],
                ))
                result = result.with_columns(fixed.alias(col_name))
                col = result[col_name]

        # Moderate fixes
        if mode in ("moderate", "aggressive"):
            for fix_name, fix_fn in [
                ("standardize_case", lambda c: _standardize_case(c, findings, col_name)),
                ("strip_control_chars", _strip_control_chars),
            ]:
                fixed = fix_fn(col)
                changed = (col.cast(pl.String).fill_null("") != fixed.cast(pl.String).fill_null(""))
                n_changed = int(changed.sum())
                if n_changed > 0:
                    before = col.filter(changed).head(3).cast(pl.String).to_list()
                    after = fixed.filter(changed).head(3).cast(pl.String).to_list()
                    report.entries.append(FixEntry(
                        column=col_name,
                        fix_type=fix_name,
                        rows_affected=n_changed,
                        sample_before=[str(v) for v in before],
                        sample_after=[str(v) for v in after],
                    ))
                    result = result.with_columns(fixed.alias(col_name))
                    col = result[col_name]

        # Aggressive fixes
        if mode == "aggressive":
            # Coerce string → numeric
            fixed = _coerce_numeric(col)
            if fixed.dtype != col.dtype:
                report.entries.append(FixEntry(
                    column=col_name,
                    fix_type="coerce_numeric",
                    rows_affected=len(col),
                ))
                result = result.with_columns(fixed.alias(col_name))
                col = result[col_name]

            # Fill nulls with mode
            if col.null_count() > 0:
                fixed = _fill_nulls_with_mode(col)
                filled = col.null_count() - fixed.null_count()
                if filled > 0:
                    report.entries.append(FixEntry(
                        column=col_name,
                        fix_type="fill_nulls",
                        rows_affected=filled,
                    ))
                    result = result.with_columns(fixed.alias(col_name))

    return result, report
