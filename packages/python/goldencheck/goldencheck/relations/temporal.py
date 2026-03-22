"""Temporal order profiler — checks that start-like date columns precede end-like columns."""
from __future__ import annotations

import polars as pl

from goldencheck.models.finding import Finding, Severity

# Pairs of (start-pattern, end-pattern) — matched against lowercased column names
_PAIR_HEURISTICS: list[tuple[str, str]] = [
    ("start", "end"),
    ("created", "updated"),
    ("begin", "finish"),
]


def _find_date_pairs(columns: list[str]) -> list[tuple[str, str]]:
    """Return (start_col, end_col) pairs found by name heuristics."""
    pairs: list[tuple[str, str]] = []
    lower_to_orig = {c.lower(): c for c in columns}
    lower_cols = list(lower_to_orig.keys())

    for start_kw, end_kw in _PAIR_HEURISTICS:
        start_candidates = [lc for lc in lower_cols if start_kw in lc]
        end_candidates = [lc for lc in lower_cols if end_kw in lc and end_kw != start_kw or (end_kw == start_kw and lc != lc)]

        # Rebuild: end_kw must appear in col but NOT the start_kw (to avoid "started" matching "end")
        start_candidates = [lc for lc in lower_cols if start_kw in lc]
        end_candidates = [lc for lc in lower_cols if end_kw in lc and lc not in start_candidates]

        for sc in start_candidates:
            for ec in end_candidates:
                pairs.append((lower_to_orig[sc], lower_to_orig[ec]))

    # Deduplicate while preserving order
    seen: set[tuple[str, str]] = set()
    unique: list[tuple[str, str]] = []
    for p in pairs:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    return unique


class TemporalOrderProfiler:
    """Checks that start-like date columns are <= end-like date columns."""

    def profile(self, df: pl.DataFrame) -> list[Finding]:
        findings: list[Finding] = []
        pairs = _find_date_pairs(df.columns)

        for start_col, end_col in pairs:
            start_series = df[start_col]
            end_series = df[end_col]

            # Attempt to cast to Date if stored as strings
            try:
                if start_series.dtype == pl.Utf8 or start_series.dtype == pl.String:
                    start_series = start_series.str.to_date(format="%Y-%m-%d", strict=False)
                if end_series.dtype == pl.Utf8 or end_series.dtype == pl.String:
                    end_series = end_series.str.to_date(format="%Y-%m-%d", strict=False)
            except Exception:
                # Cannot parse as dates — skip this pair
                continue

            if start_series.dtype not in (pl.Date, pl.Datetime) or end_series.dtype not in (pl.Date, pl.Datetime):
                continue

            # Find rows where start > end (ignoring nulls)
            violation_mask = (start_series > end_series).fill_null(False)
            violation_count = violation_mask.sum()

            if violation_count > 0:
                sample_starts = start_series.filter(violation_mask).head(3).cast(pl.String).to_list()
                sample_ends = end_series.filter(violation_mask).head(3).cast(pl.String).to_list()
                samples = [f"{s} > {e}" for s, e in zip(sample_starts, sample_ends)]

                findings.append(
                    Finding(
                        severity=Severity.ERROR,
                        column=f"{start_col},{end_col}",
                        check="temporal_order",
                        message=(
                            f"Column '{start_col}' has {violation_count} row(s) where its value "
                            f"is later than '{end_col}', violating expected temporal order."
                        ),
                        affected_rows=violation_count,
                        sample_values=samples,
                        suggestion=f"Ensure '{start_col}' <= '{end_col}' for all rows.",
                    )
                )

        return findings
