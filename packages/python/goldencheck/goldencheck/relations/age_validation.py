"""Age vs DOB cross-validation profiler.

**Flip (Stage A4): kernel-authoritative.** The mismatch scan runs through the
fused native ``age_mismatch`` kernel (byte/index-parity-validated in W3,
tests/engine/test_w3_shadow.py) over Arrow arrays pulled from the Frame/Column
seam, so it drives a ``PolarsFrame`` (tests) or an Arrow-native ``ArrowFrame``
(default scan) unchanged. Date parsing + reference-date discovery use
``pyarrow.compute`` on the seam's Arrow arrays -- no Polars expression trees. A
polars-free ``pyarrow`` fallback reproduces the kernel when it is unavailable.
"""
from __future__ import annotations

import datetime
import logging

from goldencheck.core._native_loader import native_enabled, native_module
from goldencheck.core.frame import to_frame
from goldencheck.models.finding import Finding, Severity

logger = logging.getLogger(__name__)

_EPOCH = datetime.date(1970, 1, 1)
_NUMERIC = ("int", "uint", "float")

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


def _parse_date32(col):
    """Parse a seam ``Column`` to a ``pyarrow`` ``date32`` array, or ``None`` when
    the column isn't date-like. Mirrors the prior ``_try_parse_dates``:
    date/datetime -> Date; string -> ``to_date("%Y-%m-%d", strict=False)``; other
    -> not a date."""
    import pyarrow as pa
    import pyarrow.compute as pc

    cat = col.dtype
    if cat == "date":
        arr = col.to_arrow()
        return arr if arr.type == pa.date32() else pc.cast(arr, pa.date32())
    if cat == "datetime":
        return pc.cast(col.to_arrow(), pa.date32())
    if cat == "str":
        return col.str_to_date("%Y-%m-%d", strict=False).to_arrow()
    return None


def _age_mismatch(actual, dob_date32, ref_epoch_days):
    """``(count, sample_indices[:5])`` of rows where ``|actual - expected| > 2``
    years. Native kernel when available; polars-free ``pyarrow`` fallback else."""
    if native_enabled("age_mismatch"):
        return native_module().age_mismatch(actual, dob_date32, ref_epoch_days)

    import pyarrow as pa
    import pyarrow.compute as pc

    dob_days = pc.cast(dob_date32, pa.float64())  # days since epoch (float for div)
    expected = pc.divide(pc.subtract(float(ref_epoch_days), dob_days), 365.25)
    diff = pc.abs(pc.subtract(actual, expected))
    mism = pc.and_(pc.greater(diff, 2.0), pc.and_(pc.is_valid(actual), pc.is_valid(dob_date32)))
    mism = pc.fill_null(mism, False).to_pylist()
    indices = [i for i, v in enumerate(mism) if v]
    return len(indices), indices[:5]


class AgeValidationProfiler:
    """Cross-validates age columns against date-of-birth columns."""

    def profile(self, frame) -> list[Finding]:
        frame = to_frame(frame)
        import pyarrow as pa
        import pyarrow.compute as pc

        cols = frame.columns
        age_cols = [c for c in cols if _is_age_column(c)]
        dob_cols = [c for c in cols if _is_dob_column(c)]
        if not age_cols or not dob_cols:
            return []

        # Reference date: max parseable date from non-DOB date columns, <= today.
        today = datetime.date.today()
        today_scalar = pa.scalar(today, type=pa.date32())
        reference_date = today
        for col_name in cols:
            if col_name in dob_cols:
                continue
            try:
                d32 = _parse_date32(frame.column(col_name))
            except Exception:
                continue
            if d32 is None:
                continue
            nn = pc.drop_null(d32)
            if len(nn) == 0:
                continue
            keep = pc.filter(nn, pc.less_equal(nn, today_scalar))
            if len(keep) == 0:
                continue
            mx = pc.max(keep).as_py()
            if mx is not None and mx <= today:
                reference_date = mx
                break

        ref_epoch_days = (reference_date - _EPOCH).days
        findings: list[Finding] = []

        for age_col in age_cols:
            age_seam = frame.column(age_col)
            if age_seam.dtype not in _NUMERIC:
                continue
            actual_arrow = age_seam.cast("float").to_arrow()

            for dob_col in dob_cols:
                try:
                    dob_d32 = _parse_date32(frame.column(dob_col))
                except Exception:
                    continue
                if dob_d32 is None:
                    continue
                try:
                    count, indices = _age_mismatch(actual_arrow, dob_d32, ref_epoch_days)
                except Exception as e:  # noqa: BLE001 - any kernel/arrow failure -> skip pair
                    logger.debug("age_mismatch failed for %s/%s: %s", age_col, dob_col, e)
                    continue

                if count > 0:
                    sample_ages = [str(age_seam.get(i)) for i in indices[:5]]
                    findings.append(Finding(
                        severity=Severity.ERROR,
                        column=age_col,
                        check="cross_column",
                        message=(
                            f"{count} row(s) where {age_col} doesn't match "
                            f"calculated age from {dob_col} — values mismatch by more "
                            f"than 2 years"
                        ),
                        affected_rows=count,
                        sample_values=sample_ages,
                        suggestion=f"Verify {age_col} values against {dob_col}",
                        confidence=0.9,
                    ))

        return findings
