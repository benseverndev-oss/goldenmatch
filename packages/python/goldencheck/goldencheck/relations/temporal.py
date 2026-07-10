"""Temporal order profiler — checks that start-like date columns precede end-like columns."""
from __future__ import annotations

from goldencheck.core.frame import to_frame
from goldencheck.models.finding import Finding, Severity

# Pairs of (start-pattern, end-pattern) — matched against lowercased column names
_PAIR_HEURISTICS: list[tuple[str, str]] = [
    ("start", "end"),
    ("created", "updated"),
    ("begin", "finish"),
    ("signup", "login"),
    ("signup", "last_login"),
    ("open", "close"),
    ("opened", "closed"),
    ("hire", "termination"),
    ("birth", "death"),
    ("order", "delivery"),
    ("order", "ship"),
    ("admission", "discharge"),
    ("admit", "discharge"),
    ("service", "submit"),
    ("submit", "approval"),
    ("effective", "expir"),
    ("issue", "expir"),
    ("received", "processed"),
    ("received", "complet"),
    ("placed", "fulfill"),
    ("placed", "shipped"),
    ("request", "approved"),
    ("booked", "checkin"),
    ("checkin", "checkout"),
    ("enroll", "graduat"),
    ("invoice", "payment"),
    ("prescribed", "dispensed"),
]


def _find_date_pairs(columns: list[str]) -> list[tuple[str, str]]:
    """Return (start_col, end_col) pairs found by name heuristics."""
    pairs: list[tuple[str, str]] = []
    lower_to_orig = {c.lower(): c for c in columns}
    lower_cols = list(lower_to_orig.keys())

    for start_kw, end_kw in _PAIR_HEURISTICS:
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


def _try_cast_to_date(col):
    """Attempt to cast a series to Date if stored as strings."""
    if col.dtype == "str":
        return col.str_to_date("%Y-%m-%d", strict=False)
    return col


class TemporalOrderProfiler:
    """Checks that start-like date columns are <= end-like date columns."""

    def profile(self, frame) -> list[Finding]:
        frame = to_frame(frame)
        findings: list[Finding] = []

        # Keyword-matched pairs (high confidence)
        kw_pairs = _find_date_pairs(frame.columns)
        kw_pair_set: set[tuple[str, str]] = set(kw_pairs)

        checked_pairs: set[tuple[str, str]] = set()

        for start_col, end_col in kw_pairs:
            checked_pairs.add((start_col, end_col))
            result = self._check_pair(frame, start_col, end_col, confidence=0.9)
            if result:
                findings.append(result)

        # Any-date-pair fallback: find all Date-typed columns
        # Guard: skip if >10 date columns
        date_cols = []
        for col_name in frame.columns:
            col = frame.column(col_name)
            if col.dtype in ("date", "datetime"):
                date_cols.append(col_name)
            elif col.dtype == "str":
                # Try casting to check if it's a date column
                try:
                    casted = col.str_to_date("%Y-%m-%d", strict=False)
                    if len(casted.drop_nulls()) > 0:
                        date_cols.append(col_name)
                except Exception:
                    pass

        if len(date_cols) <= 6:
            from itertools import combinations
            for col_a, col_b in combinations(date_cols, 2):
                if (col_a, col_b) not in kw_pair_set and (col_b, col_a) not in kw_pair_set:
                    if (col_a, col_b) not in checked_pairs:
                        checked_pairs.add((col_a, col_b))
                        result = self._check_pair(frame, col_a, col_b, confidence=0.4)
                        if result:
                            findings.append(result)

        return findings

    def _check_pair(
        self,
        frame,
        start_col: str,
        end_col: str,
        confidence: float,
    ) -> Finding | None:
        start_series = frame.column(start_col)
        end_series = frame.column(end_col)

        # Attempt to cast to Date if stored as strings
        try:
            start_series = _try_cast_to_date(start_series)
            end_series = _try_cast_to_date(end_series)
        except Exception:
            return None

        if start_series.dtype not in ("date", "datetime") or end_series.dtype not in ("date", "datetime"):
            return None

        # Find rows where start > end (ignoring nulls)
        violation_mask = start_series.gt_mask(end_series).fill_null(False)
        violation_count = violation_mask.sum()

        if violation_count > 0:
            sample_starts = start_series.filter_by(violation_mask).cast("str").to_list()[:3]
            sample_ends = end_series.filter_by(violation_mask).cast("str").to_list()[:3]
            samples = [f"{s} > {e}" for s, e in zip(sample_starts, sample_ends)]

            return Finding(
                severity=Severity.ERROR,
                column=f"{start_col},{end_col}",
                check="temporal_order",
                message=(
                    f"{violation_count} row(s) where '{start_col}' is later than "
                    f"'{end_col}', violating expected temporal order"
                ),
                affected_rows=violation_count,
                sample_values=samples,
                suggestion=f"Ensure '{start_col}' <= '{end_col}' for all rows.",
                confidence=confidence,
            )

        return None
