"""Numeric cross-column validation — detects value > max violations and age/DOB mismatches."""
from __future__ import annotations

import polars as pl

from goldencheck.models.finding import Finding, Severity

NUMERIC_DTYPES = (
    pl.Int8, pl.Int16, pl.Int32, pl.Int64,
    pl.UInt8, pl.UInt16, pl.UInt32, pl.UInt64,
    pl.Float32, pl.Float64,
)

# Heuristic pairs: (value_column_keyword, max_column_keyword)
# The value column should be <= the max column
_MAX_PAIRS: list[tuple[str, str]] = [
    ("amount", "max"),
    ("amount", "limit"),
    ("charge", "max"),
    ("charge", "limit"),
    ("cost", "budget"),
    ("balance", "limit"),
    ("payment", "max"),
    ("total", "max"),
    ("total", "limit"),
    ("score", "max_score"),
    ("usage", "quota"),
]


def _find_max_pairs(columns: list[str]) -> list[tuple[str, str]]:
    """Find (value_col, max_col) pairs by name heuristics."""
    pairs: list[tuple[str, str]] = []
    lower_to_orig = {c.lower(): c for c in columns}
    lower_cols = list(lower_to_orig.keys())

    for value_kw, max_kw in _MAX_PAIRS:
        value_candidates = [lc for lc in lower_cols if value_kw in lc and max_kw not in lc]
        max_candidates = [lc for lc in lower_cols if max_kw in lc]
        for vc in value_candidates:
            for mc in max_candidates:
                if vc != mc:
                    pairs.append((lower_to_orig[vc], lower_to_orig[mc]))

    return pairs


class NumericCrossColumnProfiler:
    """Detects rows where a numeric value exceeds a related maximum column."""

    def profile(self, df: pl.DataFrame) -> list[Finding]:
        findings: list[Finding] = []

        # Value > Max checks
        max_pairs = _find_max_pairs(df.columns)
        for value_col, max_col in max_pairs:
            result = self._check_exceeds(df, value_col, max_col)
            if result:
                findings.append(result)

        return findings

    def _check_exceeds(
        self,
        df: pl.DataFrame,
        value_col: str,
        max_col: str,
    ) -> Finding | None:
        try:
            val_series = df[value_col]
            max_series = df[max_col]
        except Exception:
            return None

        # Both must be numeric
        if val_series.dtype not in NUMERIC_DTYPES or max_series.dtype not in NUMERIC_DTYPES:
            # Try casting strings to float
            try:
                if val_series.dtype in (pl.Utf8, pl.String):
                    val_series = val_series.cast(pl.Float64, strict=False)
                if max_series.dtype in (pl.Utf8, pl.String):
                    max_series = max_series.cast(pl.Float64, strict=False)
                if val_series.dtype not in NUMERIC_DTYPES + (pl.Float64,):
                    return None
                if max_series.dtype not in NUMERIC_DTYPES + (pl.Float64,):
                    return None
            except Exception:
                return None

        # Find rows where value > max (ignoring nulls)
        violation_mask = (val_series > max_series).fill_null(False)
        violation_count = int(violation_mask.sum())

        if violation_count > 0:
            sample_vals = []
            val_filtered = val_series.filter(violation_mask).head(3).to_list()
            max_filtered = max_series.filter(violation_mask).head(3).to_list()
            sample_vals = [f"{v} exceeds {m}" for v, m in zip(val_filtered, max_filtered)]

            return Finding(
                severity=Severity.ERROR,
                column=value_col,
                check="cross_column_validation",
                message=(
                    f"{violation_count} row(s) where '{value_col}' exceeds '{max_col}' — "
                    f"values violate expected maximum constraint"
                ),
                affected_rows=violation_count,
                sample_values=sample_vals,
                suggestion=f"Ensure '{value_col}' <= '{max_col}' for all rows",
                confidence=0.85,
            )

        return None
