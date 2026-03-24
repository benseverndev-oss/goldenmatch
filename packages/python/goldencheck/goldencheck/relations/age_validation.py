"""Age vs DOB cross-validation profiler."""
from __future__ import annotations

import datetime

import polars as pl

from goldencheck.models.finding import Finding, Severity

# Words that contain "age" but are NOT age columns
_AGE_EXCLUSIONS = ("stage", "page", "usage", "mileage", "dosage", "voltage")


def _is_age_column(name: str) -> bool:
    lower = name.lower()
    if "age" not in lower:
        return False
    for exc in _AGE_EXCLUSIONS:
        if exc in lower:
            return False
    return True


def _is_dob_column(name: str) -> bool:
    lower = name.lower()
    return any(kw in lower for kw in ("birth", "dob", "born"))


def _try_parse_dates(series: pl.Series) -> pl.Series:
    """Attempt to cast a series to Date."""
    if series.dtype in (pl.Date, pl.Datetime):
        return series.cast(pl.Date)
    if series.dtype in (pl.Utf8, pl.String):
        return series.str.to_date(format="%Y-%m-%d", strict=False)
    return series


class AgeValidationProfiler:
    """Cross-validates age columns against date-of-birth columns."""

    def profile(self, df: pl.DataFrame) -> list[Finding]:
        findings: list[Finding] = []

        age_cols = [c for c in df.columns if _is_age_column(c)]
        dob_cols = [c for c in df.columns if _is_dob_column(c)]

        if not age_cols or not dob_cols:
            return findings

        # Find reference date: max date from non-DOB date columns, <= today
        today = datetime.date.today()
        reference_date = today

        date_cols_for_ref = [
            c for c in df.columns
            if c not in dob_cols
        ]
        for col_name in date_cols_for_ref:
            try:
                parsed = _try_parse_dates(df[col_name])
                if parsed.dtype != pl.Date:
                    continue
                valid = parsed.drop_nulls().filter(parsed.drop_nulls() <= today)
                if len(valid) > 0:
                    max_date = valid.max()
                    if max_date and max_date <= today:
                        reference_date = max_date
                        break
            except Exception:
                continue

        for age_col in age_cols:
            # Age column must be numeric
            col_series = df[age_col]
            if col_series.dtype not in (
                pl.Int8, pl.Int16, pl.Int32, pl.Int64,
                pl.UInt8, pl.UInt16, pl.UInt32, pl.UInt64,
                pl.Float32, pl.Float64,
            ):
                continue

            for dob_col in dob_cols:
                try:
                    dob_series = _try_parse_dates(df[dob_col])
                    if dob_series.dtype != pl.Date:
                        continue
                except Exception:
                    continue

                # Calculate expected age using DataFrame select for proper evaluation
                try:
                    result = df.select(
                        actual=pl.col(age_col).cast(pl.Float64),
                        expected=(
                            (pl.lit(reference_date).cast(pl.Date) - pl.col(dob_col).str.to_date(format="%Y-%m-%d", strict=False))
                            .dt.total_days()
                            / 365.25
                        ) if dob_series.dtype in (pl.Utf8, pl.String) else (
                            (pl.lit(reference_date).cast(pl.Date) - pl.col(dob_col).cast(pl.Date))
                            .dt.total_days()
                            / 365.25
                        ),
                    )

                    actual = result["actual"]
                    expected = result["expected"]
                    diff = (actual - expected).abs()

                    non_null_mask = actual.is_not_null() & expected.is_not_null()
                    mismatch_mask = (diff > 2.0) & non_null_mask

                    mismatch_count = int(mismatch_mask.sum())
                    if mismatch_count > 0:
                        sample_ages = col_series.filter(mismatch_mask).head(5).to_list()
                        findings.append(Finding(
                            severity=Severity.ERROR,
                            column=age_col,
                            check="cross_column",
                            message=(
                                f"{mismatch_count} row(s) where {age_col} doesn't match "
                                f"calculated age from {dob_col} — values mismatch by more "
                                f"than 2 years"
                            ),
                            affected_rows=mismatch_count,
                            sample_values=[str(v) for v in sample_ages],
                            suggestion=f"Verify {age_col} values against {dob_col}",
                            confidence=0.9,
                        ))
                except Exception:
                    continue

        return findings
