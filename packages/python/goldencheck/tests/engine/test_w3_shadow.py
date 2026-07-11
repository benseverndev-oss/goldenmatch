"""Shadow-compute proof for the two W3 relation kernels.

The `approx_duplicate` + `age_validation` profilers now shadow-compute their
fused native kernels (`duplicate_signatures` / `age_mismatch`) alongside the
authoritative Polars path, discarding the result. This test proves the kernel
values MATCH the Polars values the profilers actually emit -- i.e. they are
ready to become authoritative at a future Flip.

It does NOT assert anything about the profilers' emitted `Finding`s beyond
reading them back as ground truth -- the existing `approx_duplicate` /
`age_validation` tests stay green unedited. Each half skips cleanly when the
relevant native kernel isn't built/enabled."""
from __future__ import annotations

import datetime

import polars as pl
import pytest
from goldencheck.core._native_loader import native_enabled, native_module
from goldencheck.relations.age_validation import AgeValidationProfiler
from goldencheck.relations.approx_duplicate import (
    ApproxDuplicateProfiler,
    _exact_signature,
    _normalized_signature,
)

_EPOCH = datetime.date(1970, 1, 1)

dup_only = pytest.mark.skipif(
    not native_enabled("duplicate_signatures"),
    reason="goldencheck native duplicate_signatures kernel not built/enabled",
)
age_only = pytest.mark.skipif(
    not native_enabled("age_mismatch"),
    reason="goldencheck native age_mismatch kernel not built/enabled",
)


# ---------------------------------------------------------------------------
# approx_duplicate: kernel counts == the profiler's Polars-derived findings.
# ---------------------------------------------------------------------------
def _duplicate_df() -> pl.DataFrame:
    # Rows 0,3 are exact duplicates ("Acme, Inc."). Rows 1,2 near-dup them (case/
    # whitespace/punct) but have no exact twin. Rows 4,5 ("Beta"/"beta") near-dup
    # each other. -> exact: 2 rows / 1 group; near: 4 rows / 2 groups.
    return pl.DataFrame(
        {"name": ["Acme, Inc.", "acme inc", "ACME  Inc", "Acme, Inc.", "Beta", "beta"]},
        schema={"name": pl.Utf8},
    )


def _profiler_dup_counts(df: pl.DataFrame) -> tuple[int, int, int, int]:
    """Read the four counts back out of the profiler's emitted findings."""
    findings = ApproxDuplicateProfiler().profile(df)
    edr = edg = ndr = ndg = 0
    for f in findings:
        if f.check == "duplicate_rows":
            edr = f.affected_rows
            edg = f.metadata["duplicate_groups"]
        elif f.check == "near_duplicate_rows":
            ndr = f.affected_rows
            ndg = f.metadata["near_duplicate_groups"]
    return (edr, edg, ndr, ndg)


@dup_only
def test_duplicate_signatures_shadow_matches_profiler() -> None:
    df = _duplicate_df()
    is_string = [dt == pl.Utf8 for dt in df.dtypes]
    kernel = tuple(
        native_module().duplicate_signatures([df[c].to_arrow() for c in df.columns], is_string)
    )
    assert kernel == _profiler_dup_counts(df)


@dup_only
def test_duplicate_signatures_shadow_runs_on_profile_path() -> None:
    """The profiler shadow-computes the kernel without raising and without
    altering its emitted findings -- exercised end-to-end via profile()."""
    df = _duplicate_df()
    findings = ApproxDuplicateProfiler().profile(df)
    # Recompute the Polars ground truth directly and confirm findings unchanged.
    work = pl.DataFrame(
        {"__norm__": _normalized_signature(df), "__exact__": _exact_signature(df)}
    )
    nc = work.group_by("__norm__").len().rename({"len": "__nc__"})
    ec = work.group_by("__exact__").len().rename({"len": "__ec__"})
    work = work.join(nc, on="__norm__").join(ec, on="__exact__")
    exact_dups = work.filter(pl.col("__ec__") >= 2)
    near_dups = work.filter((pl.col("__nc__") >= 2) & (pl.col("__ec__") < 2))
    by_check = {f.check: f for f in findings}
    assert by_check["duplicate_rows"].affected_rows == exact_dups.height
    assert by_check["near_duplicate_rows"].affected_rows == near_dups.height


# ---------------------------------------------------------------------------
# age_validation: kernel count/indices == the profiler's Polars-derived finding.
# ---------------------------------------------------------------------------
def _age_df() -> tuple[pl.DataFrame, datetime.date]:
    # No non-DOB date column -> the profiler's reference_date is today. Build DOBs
    # from a true age, then perturb a couple of the ages past the 2-year band.
    ref = datetime.date.today()

    def dob(years: float) -> datetime.date:
        return ref - datetime.timedelta(days=round(years * 365.25))

    ages = [30.0, 50.0, 45.0, 25.0, 70.0]  # rows 1,3 will be perturbed to mismatch
    true = [30.0, 30.0, 45.0, 60.0, 70.0]  # row 1 off by ~20, row 3 off by ~35
    df = pl.DataFrame(
        {
            "age": pl.Series("age", ages, dtype=pl.Float64),
            "birth_date": pl.Series("birth_date", [dob(y) for y in true], dtype=pl.Date),
        }
    )
    return df, ref


def _profiler_age_mismatch_count(df: pl.DataFrame) -> int:
    findings = AgeValidationProfiler().profile(df)
    for f in findings:
        if f.check == "cross_column" and f.column == "age":
            return f.affected_rows
    return 0


@age_only
def test_age_mismatch_shadow_matches_profiler() -> None:
    df, ref = _age_df()
    ref_epoch_days = (ref - _EPOCH).days
    actual = df["age"].cast(pl.Float64).to_arrow()
    dob_date32 = df["birth_date"].cast(pl.Date, strict=False).to_arrow()
    count, indices = native_module().age_mismatch(actual, dob_date32, ref_epoch_days)

    profiler_count = _profiler_age_mismatch_count(df)
    assert count == profiler_count
    assert count == 2  # rows 1 and 3 are the planted mismatches

    # The kernel's sample_indices gather the same age values the profiler samples.
    findings = AgeValidationProfiler().profile(df)
    finding = next(f for f in findings if f.check == "cross_column" and f.column == "age")
    kernel_sample = [str(df["age"][i]) for i in indices[:5]]
    assert kernel_sample == finding.sample_values
