//! Fused age-vs-DOB mismatch scan (backs the `age_validation` relation profiler
//! in `goldencheck.relations.age_validation`).
//!
//! The Python profiler computes, per row, an EXPECTED age from a date-of-birth
//! column and a reference date, then flags rows whose ACTUAL age column differs
//! from the expected age by more than two years:
//!
//! ```text
//! expected = (reference_date - dob).dt.total_days() / 365.25
//! mismatch = (actual - expected).abs() > 2.0  &  actual.is_not_null() & expected.is_not_null()
//! ```
//!
//! (`age_validation.py:111-127`.) This kernel does ONLY that per-row arithmetic +
//! mismatch scan. The reference-date selection, the age/dob column discovery, and
//! the DOB parse to `Date` all stay in Python.
//!
//! Bit-exact: `actual` arrives already `cast(pl.Float64)` and `dob_epoch_days`
//! already parsed to `Date32` (days since 1970-01-01), Python-side. On a Date
//! difference Polars' `.dt.total_days()` is exactly the day count
//! `ref_epoch_days - dob_days`, so `(ref_epoch_days - dob_days) as f64 / 365.25`
//! is the identical f64 op Polars performs, and the `> 2.0` strict compare
//! matches value-for-value.
//!
//! `NaN` age: Polars orders `NaN` as GREATER than every value, so its
//! `diff > 2.0` is TRUE for a `NaN` diff (and `NaN` is NOT null in Polars, so the
//! `is_not_null` mask keeps the row) -- the profiler therefore COUNTS a `NaN` age
//! as a mismatch. Rust's native `NaN > 2.0` is `false`, so we replicate Polars by
//! treating a `NaN` diff as `> 2.0`. (This corrects the W3 spec note, which
//! assumed Rust's IEEE semantics; the parity test pins the real Polars behaviour.)
//! No divergence class.

use arrow::array::{Array, Date32Array, Float64Array};
use arrow::error::ArrowError;

/// The mismatch tally the `age_validation` profiler reports for one
/// `(age_col, dob_col)` pair.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct AgeStats {
    /// Number of rows where actual age mismatches the DOB-derived age by > 2 yr.
    pub mismatch_count: usize,
    /// The first-5 mismatch row indices, in ascending row order. The caller maps
    /// these to `col_series.filter(mismatch_mask).head(5)` values -- `filter` is
    /// order-preserving, so gathering these indices yields the same 5 values.
    pub sample_indices: Vec<usize>,
}

/// Fused age-vs-DOB mismatch scan.
///
/// - `actual`: the age column already `cast(pl.Float64)` (Python-side).
/// - `dob_epoch_days`: the DOB column already parsed to `Date32` (days since
///   1970-01-01, Python-side).
/// - `ref_epoch_days`: `(reference_date - 1970-01-01).days` (offset-free).
///
/// Per row `i`: `both_present = actual[i].is_some() && dob[i].is_some()`;
/// `expected = (ref_epoch_days - dob_days[i]) as f64 / 365.25`;
/// `mismatch = both_present && (actual[i] - expected).abs() > 2.0`.
pub fn age_mismatch(
    actual: &dyn Array,
    dob_epoch_days: &dyn Array,
    ref_epoch_days: i64,
) -> Result<AgeStats, ArrowError> {
    let actual = actual
        .as_any()
        .downcast_ref::<Float64Array>()
        .ok_or_else(|| {
            ArrowError::InvalidArgumentError(
                "age_mismatch: `actual` must be a Float64 array (cast(pl.Float64) Python-side)"
                    .into(),
            )
        })?;
    let dob = dob_epoch_days
        .as_any()
        .downcast_ref::<Date32Array>()
        .ok_or_else(|| {
            ArrowError::InvalidArgumentError(
                "age_mismatch: `dob_epoch_days` must be a Date32 array (parsed to Date Python-side)"
                    .into(),
            )
        })?;

    let n_rows = actual.len();
    if dob.len() != n_rows {
        return Err(ArrowError::InvalidArgumentError(
            "age_mismatch: `actual` and `dob_epoch_days` differ in length".into(),
        ));
    }

    let mut mismatch_count = 0usize;
    let mut sample_indices: Vec<usize> = Vec::new();
    for i in 0..n_rows {
        if actual.is_null(i) || dob.is_null(i) {
            continue;
        }
        let expected = (ref_epoch_days - dob.value(i) as i64) as f64 / 365.25;
        // Polars orders NaN greater than everything, so `NaN > 2.0` is TRUE there;
        // Rust's native `NaN > 2.0` is false. Replicate Polars: NaN diff -> match.
        let diff = (actual.value(i) - expected).abs();
        if diff.is_nan() || diff > 2.0 {
            mismatch_count += 1;
            if sample_indices.len() < 5 {
                sample_indices.push(i);
            }
        }
    }

    Ok(AgeStats {
        mismatch_count,
        sample_indices,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Arc;

    // Reference date = 2020-01-01. Days since 1970-01-01 = 18262.
    const REF: i64 = 18262;

    // Helper: a DOB `N` years before REF (approx), as epoch days.
    fn dob_days_years_before(years: f64) -> i32 {
        (REF as f64 - years * 365.25) as i32
    }

    fn f(v: Vec<Option<f64>>) -> Arc<Float64Array> {
        Arc::new(Float64Array::from(v))
    }
    fn d(v: Vec<Option<i32>>) -> Arc<Date32Array> {
        Arc::new(Date32Array::from(v))
    }

    #[test]
    fn matching_ages_no_mismatch() {
        // Two rows whose actual age equals the DOB-derived age exactly.
        let actual = f(vec![Some(30.0), Some(50.0)]);
        let dob = d(vec![
            Some(dob_days_years_before(30.0)),
            Some(dob_days_years_before(50.0)),
        ]);
        let r = age_mismatch(actual.as_ref(), dob.as_ref(), REF).unwrap();
        assert_eq!(r.mismatch_count, 0);
        assert!(r.sample_indices.is_empty());
    }

    #[test]
    fn off_by_more_than_two_is_mismatch() {
        // Actual 40 but DOB implies ~30 -> off by ~10 -> mismatch.
        let actual = f(vec![Some(40.0)]);
        let dob = d(vec![Some(dob_days_years_before(30.0))]);
        let r = age_mismatch(actual.as_ref(), dob.as_ref(), REF).unwrap();
        assert_eq!(r.mismatch_count, 1);
        assert_eq!(r.sample_indices, vec![0]);
    }

    #[test]
    fn nulls_excluded() {
        // null actual, null dob -> both excluded even though the other side would
        // mismatch.
        let actual = f(vec![None, Some(99.0)]);
        let dob = d(vec![Some(dob_days_years_before(30.0)), None]);
        let r = age_mismatch(actual.as_ref(), dob.as_ref(), REF).unwrap();
        assert_eq!(r.mismatch_count, 0);
    }

    #[test]
    fn boundary_exactly_two_is_not_mismatch() {
        // diff == 2.0 exactly -> NOT a mismatch (strict `> 2.0`). Derive the
        // expected age from the same formula, then set actual = expected + 2.0 so
        // (actual - expected) is exactly 2.0.
        // DOB == reference date -> expected age is exactly 0.0; actual == 2.0 ->
        // diff is exactly 2.0.
        let dob_val = REF as i32;
        let expected = (REF - dob_val as i64) as f64 / 365.25;
        assert_eq!(expected, 0.0);
        let actual = f(vec![Some(2.0)]);
        let dob = d(vec![Some(dob_val)]);
        let r = age_mismatch(actual.as_ref(), dob.as_ref(), REF).unwrap();
        assert_eq!(r.mismatch_count, 0);
    }

    #[test]
    fn nan_age_is_a_mismatch() {
        // NaN actual: Polars orders NaN greater than everything so its
        // `diff > 2.0` is TRUE (and NaN is not null) -> the profiler counts it as
        // a mismatch. The kernel replicates that (native `NaN > 2.0` is false, so
        // we treat a NaN diff as a match).
        let actual = f(vec![Some(f64::NAN)]);
        let dob = d(vec![Some(dob_days_years_before(30.0))]);
        let r = age_mismatch(actual.as_ref(), dob.as_ref(), REF).unwrap();
        assert_eq!(r.mismatch_count, 1);
        assert_eq!(r.sample_indices, vec![0]);
    }

    #[test]
    fn null_age_is_still_excluded() {
        // A genuine NULL age (not NaN) is excluded by the both-present guard.
        let actual = f(vec![None]);
        let dob = d(vec![Some(dob_days_years_before(30.0))]);
        let r = age_mismatch(actual.as_ref(), dob.as_ref(), REF).unwrap();
        assert_eq!(r.mismatch_count, 0);
    }

    #[test]
    fn empty_input() {
        let actual = f(vec![]);
        let dob = d(vec![]);
        let r = age_mismatch(actual.as_ref(), dob.as_ref(), REF).unwrap();
        assert_eq!(r, AgeStats::default());
    }

    #[test]
    fn sample_indices_first_five_ascending() {
        // Seven mismatches -> only the first 5 row indices, ascending.
        let actual = f(vec![Some(80.0); 7]);
        let dob = d(vec![Some(dob_days_years_before(30.0)); 7]);
        let r = age_mismatch(actual.as_ref(), dob.as_ref(), REF).unwrap();
        assert_eq!(r.mismatch_count, 7);
        assert_eq!(r.sample_indices, vec![0, 1, 2, 3, 4]);
    }
}
