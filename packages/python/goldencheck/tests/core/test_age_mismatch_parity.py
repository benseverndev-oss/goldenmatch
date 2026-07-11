"""Parity: the native ``age_mismatch`` kernel must produce the SAME mismatch
count and sample rows the ``age_validation`` relation profiler derives from
Polars (``(actual - expected).abs() > 2.0 & non_null`` where
``expected = (reference_date - dob).dt.total_days() / 365.25``).

Per the W3 spec this is BIT-EXACT (register empty). ``(ref_epoch_days -
dob_epoch_days) as f64 / 365.25`` is the identical f64 op Polars performs on a
Date difference, and the strict ``> 2.0`` compare matches value-for-value. Polars
orders ``NaN`` GREATER than everything (and ``NaN`` is not null), so a ``NaN`` age
IS counted as a mismatch -- the kernel replicates that (Rust's native
``NaN > 2.0`` is false, so a ``NaN`` diff is treated as a match).
``sample_indices`` are the first-5 mismatch row indices in ascending row order;
``filter`` is order-preserving, so they gather the same 5 values as
``col_series.filter(mask).head(5)``.

Skips cleanly when the native extension isn't built (pure-Python-only env)."""
from __future__ import annotations

import datetime
import random

import polars as pl
import pytest
from goldencheck.core._native_loader import native_available, native_module

native_only = pytest.mark.skipif(
    not native_available(), reason="goldencheck native extension not built"
)

_EPOCH = datetime.date(1970, 1, 1)


def _polars_mismatch(
    age: pl.Series, dob: pl.Series, reference_date: datetime.date
) -> tuple[int, list]:
    """Ground truth: replicate the profiler's Polars mismatch compute + sampling
    (age_validation.py:111-129)."""
    df = pl.DataFrame({"age": age, "dob": dob})
    result = df.select(
        actual=pl.col("age").cast(pl.Float64),
        expected=(
            (pl.lit(reference_date).cast(pl.Date) - pl.col("dob").cast(pl.Date, strict=False))
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
    sample = age.filter(mismatch_mask).head(5).to_list()
    return mismatch_count, sample


def _native_mismatch(
    age: pl.Series, dob: pl.Series, reference_date: datetime.date
) -> tuple[int, list]:
    ref_epoch_days = (reference_date - _EPOCH).days
    actual = age.cast(pl.Float64)
    dob_date32 = dob.cast(pl.Date, strict=False)
    count, indices = native_module().age_mismatch(
        actual.to_arrow(), dob_date32.to_arrow(), ref_epoch_days
    )
    # The caller gathers the ORIGINAL age values at the mismatch indices, exactly
    # as the profiler does with filter(mask).head(5) (order-preserving).
    sample = [age[i] for i in indices]
    return count, sample


def _norm(result: tuple[int, list]) -> tuple[int, list[str]]:
    # The profiler stores sample_values as [str(v) ...]; stringify so a NaN age
    # (str -> "nan") compares equal on both sides (raw nan != nan breaks ==).
    count, sample = result
    return (count, [str(v) for v in sample])


def _check(age: pl.Series, dob: pl.Series, reference_date: datetime.date) -> None:
    nat = _norm(_native_mismatch(age, dob, reference_date))
    pol = _norm(_polars_mismatch(age, dob, reference_date))
    assert nat == pol, (age.to_list(), dob.to_list(), reference_date, nat, pol)


_REF = datetime.date(2020, 1, 1)


def _dob_years_before(years: float, ref: datetime.date = _REF) -> datetime.date:
    return ref - datetime.timedelta(days=round(years * 365.25))


# ---------------------------------------------------------------------------
# Adversarial hand-built fixtures (each isolates one behaviour).
# ---------------------------------------------------------------------------
@native_only
def test_matching_ages_no_mismatch() -> None:
    age = pl.Series("age", [30.0, 50.0], dtype=pl.Float64)
    dob = pl.Series("dob", [_dob_years_before(30.0), _dob_years_before(50.0)], dtype=pl.Date)
    _check(age, dob, _REF)


@native_only
def test_off_by_more_than_two_is_mismatch() -> None:
    age = pl.Series("age", [40.0, 30.0], dtype=pl.Float64)
    dob = pl.Series("dob", [_dob_years_before(30.0), _dob_years_before(30.0)], dtype=pl.Date)
    count, _ = _native_mismatch(age, dob, _REF)
    assert count == 1  # row 0 off by ~10; row 1 matches
    _check(age, dob, _REF)


@native_only
def test_nulls_excluded() -> None:
    age = pl.Series("age", [None, 99.0], dtype=pl.Float64)
    dob = pl.Series("dob", [_dob_years_before(30.0), None], dtype=pl.Date)
    _check(age, dob, _REF)


@native_only
def test_boundary_exactly_two_not_mismatch() -> None:
    # DOB == reference date -> expected age exactly 0.0; actual == 2.0 -> diff is
    # exactly 2.0 -> NOT a mismatch (strict > 2.0). And 2.01 IS a mismatch.
    age = pl.Series("age", [2.0, 2.01], dtype=pl.Float64)
    dob = pl.Series("dob", [_REF, _REF], dtype=pl.Date)
    count, _ = _native_mismatch(age, dob, _REF)
    assert count == 1  # only the 2.01 row exceeds 2.0
    _check(age, dob, _REF)


@native_only
def test_nan_age_is_a_mismatch() -> None:
    # Polars orders NaN greater than everything, so (nan - expected).abs() > 2.0
    # is True and NaN is not null -> the profiler COUNTS a NaN age as a mismatch.
    # The kernel must match (corrects the spec's IEEE-semantics assumption).
    age = pl.Series("age", [float("nan"), 40.0], dtype=pl.Float64)
    dob = pl.Series("dob", [_dob_years_before(30.0), _dob_years_before(40.0)], dtype=pl.Date)
    count, _ = _native_mismatch(age, dob, _REF)
    assert count == 1  # the NaN row; row 1 matches its DOB
    _check(age, dob, _REF)


@native_only
def test_empty() -> None:
    age = pl.Series("age", [], dtype=pl.Float64)
    dob = pl.Series("dob", [], dtype=pl.Date)
    count, sample = _native_mismatch(age, dob, _REF)
    assert count == 0
    assert sample == []


@native_only
def test_string_dob_parsed_python_side() -> None:
    # DOB as %Y-%m-%d strings parsed to Date Python-side (mirrors the profiler's
    # str.to_date path). Row 1's actual is off by >2 from the DOB-derived age.
    age = pl.Series("age", [30.0, 60.0], dtype=pl.Float64)
    dob_str = pl.Series(
        "dob",
        [_dob_years_before(30.0).isoformat(), _dob_years_before(30.0).isoformat()],
        dtype=pl.Utf8,
    )
    dob = dob_str.str.to_date(format="%Y-%m-%d", strict=False)
    count, _ = _native_mismatch(age, dob, _REF)
    assert count == 1
    _check(age, dob, _REF)


@native_only
def test_sample_gathers_same_five_values() -> None:
    # >5 mismatches with distinct age values -> the first-5 sample must match
    # filter(mask).head(5) value-for-value.
    ages = [90.0, 91.0, 20.0, 92.0, 93.0, 94.0, 95.0]  # all off by >2 from 30yr DOB
    age = pl.Series("age", ages, dtype=pl.Float64)
    dob = pl.Series("dob", [_dob_years_before(30.0)] * len(ages), dtype=pl.Date)
    _check(age, dob, _REF)


# ---------------------------------------------------------------------------
# Randomized fuzz — planted matches + mismatches + nulls.
# ---------------------------------------------------------------------------
@native_only
@pytest.mark.parametrize("seed", range(25))
def test_random(seed: int) -> None:
    rng = random.Random(seed)
    n = rng.randint(0, 60)
    ages: list[float | None] = []
    dobs: list[datetime.date | None] = []
    for _ in range(n):
        true_age = rng.uniform(0, 95)
        dob = _dob_years_before(true_age)
        # Sometimes plant a mismatch, sometimes a null.
        roll = rng.random()
        if roll < 0.15:
            ages.append(None)
        elif roll < 0.30:
            ages.append(true_age + rng.choice([-20, -5, 5, 20]))  # likely mismatch
        elif roll < 0.40:
            ages.append(float("nan"))
        else:
            ages.append(round(true_age))  # near-match (rounding within 2yr)
        dobs.append(None if rng.random() < 0.1 else dob)
    age = pl.Series("age", ages, dtype=pl.Float64)
    dob = pl.Series("dob", dobs, dtype=pl.Date)
    _check(age, dob, _REF)
