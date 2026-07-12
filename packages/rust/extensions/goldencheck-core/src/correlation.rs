//! Deterministic correlation statistics for the `correlation.py` baseline
//! profiler. That profiler consumes only the STATISTIC each scipy call returns
//! (never the p-value): `_pearson_entry` uses `pearsonr(a, b)[0]` (the Pearson
//! `r`) and `_cramers_v` uses `chi2_contingency(matrix)[0]` (the Pearson chi2
//! statistic) before a pure-arithmetic Cramer's-V bias correction. Both are pure
//! arithmetic, so this kernel reproduces them float-epsilon-exact (Rust = source
//! of truth), shadow-wired: the authoritative finding stays scipy until the Flip.
//!
//! Two scipy behaviours are load-bearing here:
//!   - `pearsonr` CLAMPS `r` into `[-1, 1]` (after the normalised dot product),
//!     so perfectly-correlated data returns exactly `1.0`/`-1.0` rather than
//!     `1.0000000002`. We clamp identically.
//!   - `chi2_contingency(correction=True)` (the default) applies Yates'
//!     continuity correction ONLY for 2x2 tables. It adjusts each observed cell
//!     TOWARD its expected value by `min(0.5, |obs-exp|)`, so a cell's residual
//!     is `max(0, |obs-exp| - 0.5)` -- a cell with `|obs-exp| < 0.5` contributes
//!     EXACTLY 0. Naively squaring `(|obs-exp| - 0.5)` would turn such a
//!     near-expected cell into a spurious positive; we clip at 0 to match scipy.
use arrow::array::{Array, Float64Array};

/// Pearson correlation coefficient of two equal-length numeric columns, matching
/// `scipy.stats.pearsonr(x, y)[0]`. Both arrays must downcast to `Float64Array`
/// (the Python caller casts the already-numeric, null-dropped pair to float64
/// before crossing the boundary). Computed as the mean-centred cross product over
/// `sqrt(ss_x * ss_y)`, then CLAMPED into `[-1, 1]` exactly as scipy does.
/// Returns `NaN` for empty or length-mismatched input.
pub fn pearson_r(x: &dyn Array, y: &dyn Array) -> f64 {
    let xa = x
        .as_any()
        .downcast_ref::<Float64Array>()
        .expect("pearson_r expects a Float64 x array");
    let ya = y
        .as_any()
        .downcast_ref::<Float64Array>()
        .expect("pearson_r expects a Float64 y array");
    let n = xa.len();
    if n == 0 || n != ya.len() {
        return f64::NAN;
    }
    let nf = n as f64;

    let mut mean_x = 0.0f64;
    let mut mean_y = 0.0f64;
    for i in 0..n {
        mean_x += xa.value(i);
        mean_y += ya.value(i);
    }
    mean_x /= nf;
    mean_y /= nf;

    let mut sxy = 0.0f64;
    let mut sxx = 0.0f64;
    let mut syy = 0.0f64;
    for i in 0..n {
        let dx = xa.value(i) - mean_x;
        let dy = ya.value(i) - mean_y;
        sxy += dx * dy;
        sxx += dx * dx;
        syy += dy * dy;
    }

    let r = sxy / (sxx * syy).sqrt();
    // scipy clamps r into [-1, 1] so perfect correlation returns exactly +/-1.0.
    // `.max(-1.0).min(1.0)` (NOT `.clamp`): kept deliberately for its float
    // semantics -- and the caller pre-guards zero-variance so denom > 0 here.
    #[allow(clippy::manual_clamp)]
    let clamped = r.max(-1.0).min(1.0);
    clamped
}

/// Pearson chi-squared STATISTIC from a row-major flattened contingency table,
/// matching `scipy.stats.chi2_contingency(matrix)[0]` with the default
/// `correction=True`. `values` holds `nrows * ncols` observed counts in
/// row-major order. Expected counts are `row_sum[i] * col_sum[j] / total`.
///
/// For 2x2 tables ONLY, Yates' continuity correction applies: each cell's
/// residual is `max(0, |obs-exp| - 0.5)` (clip at 0, so a near-expected cell
/// contributes 0). All other shapes use the uncorrected `(obs-exp)^2 / exp`.
/// Returns `NaN` for empty or malformed dimensions.
pub fn chi2_contingency_stat(values: &[f64], nrows: usize, ncols: usize) -> f64 {
    if nrows == 0 || ncols == 0 || values.len() != nrows * ncols {
        return f64::NAN;
    }

    let mut row_sums = vec![0.0f64; nrows];
    let mut col_sums = vec![0.0f64; ncols];
    let mut total = 0.0f64;
    for i in 0..nrows {
        for j in 0..ncols {
            let v = values[i * ncols + j];
            row_sums[i] += v;
            col_sums[j] += v;
            total += v;
        }
    }

    let yates = nrows == 2 && ncols == 2;
    let mut chi2 = 0.0f64;
    for i in 0..nrows {
        for j in 0..ncols {
            let obs = values[i * ncols + j];
            let exp = row_sums[i] * col_sums[j] / total;
            let diff = (obs - exp).abs();
            // Yates (2x2 only): clip the residual at 0 so a cell with
            // |obs-exp| < 0.5 contributes EXACTLY 0 (scipy adjusts observed by
            // min(0.5, |diff|)). Do NOT square (|diff|-0.5) unclipped.
            let residual = if yates { (diff - 0.5).max(0.0) } else { diff };
            chi2 += residual * residual / exp;
        }
    }
    chi2
}

#[cfg(test)]
mod tests {
    use super::*;
    use arrow::array::Float64Array;

    fn approx(a: f64, b: f64) -> bool {
        (a - b).abs() <= 1e-9 * (1.0 + a.abs().max(b.abs()))
    }

    #[test]
    fn pearson_perfect_positive_is_exactly_one() {
        let x = Float64Array::from(vec![1.0, 2.0, 3.0, 4.0]);
        let y = Float64Array::from(vec![2.0, 4.0, 6.0, 8.0]);
        // Exactly 1.0 thanks to the clamp (raw dot would be 1.0000000002).
        assert_eq!(pearson_r(&x, &y), 1.0);
    }

    #[test]
    fn pearson_perfect_negative_is_exactly_minus_one() {
        let x = Float64Array::from(vec![1.0, 2.0, 3.0, 4.0]);
        let y = Float64Array::from(vec![8.0, 6.0, 4.0, 2.0]);
        assert_eq!(pearson_r(&x, &y), -1.0);
    }

    #[test]
    fn pearson_zero_correlation() {
        let x = Float64Array::from(vec![1.0, 2.0, 3.0]);
        let y = Float64Array::from(vec![1.0, 2.0, 1.0]);
        assert!(approx(pearson_r(&x, &y), 0.0));
    }

    #[test]
    fn pearson_known_fixture() {
        // x=[1,2,3,4], y=[1,3,2,5]: r = 5.5 / sqrt(5 * 8.75) = 0.8315218406203
        // (matches scipy.stats.pearsonr).
        let x = Float64Array::from(vec![1.0, 2.0, 3.0, 4.0]);
        let y = Float64Array::from(vec![1.0, 3.0, 2.0, 5.0]);
        assert!(approx(pearson_r(&x, &y), 0.831_521_840_620_3));
    }

    #[test]
    fn pearson_empty_is_nan() {
        let x = Float64Array::from(Vec::<f64>::new());
        let y = Float64Array::from(Vec::<f64>::new());
        assert!(pearson_r(&x, &y).is_nan());
    }

    #[test]
    fn chi2_3col_no_correction() {
        // 2x3 table (non-2x2 => no Yates). All expected == 20; residuals
        // 10,0,10,10,0,10 => chi2 = 5+0+5+5+0+5 = 20.0 exactly.
        let m = [10.0, 20.0, 30.0, 30.0, 20.0, 10.0];
        assert!(approx(chi2_contingency_stat(&m, 2, 3), 20.0));
    }

    #[test]
    fn chi2_2x2_with_yates() {
        // [[10,20],[30,40]]: expected 12,18,28,42; every |obs-exp| = 2 =>
        // Yates residual 1.5 => chi2 = 2.25*(1/12+1/18+1/28+1/42) = 0.446428...
        let m = [10.0, 20.0, 30.0, 40.0];
        assert!(approx(chi2_contingency_stat(&m, 2, 2), 0.446_428_571_428_571_4));
    }

    #[test]
    fn chi2_2x2_yates_clips_small_diff_to_zero() {
        // [[5,5],[5,6]]: every |obs-exp| = 0.238... < 0.5, so Yates clips each
        // residual to 0 => chi2 is EXACTLY 0.0 (not a spurious positive).
        let m = [5.0, 5.0, 5.0, 6.0];
        assert_eq!(chi2_contingency_stat(&m, 2, 2), 0.0);
    }

    #[test]
    fn chi2_2x2_yates_strong() {
        // [[1,9],[9,1]]: expected all 5; |obs-exp| = 4; Yates residual 3.5 =>
        // 12.25/5 per cell * 4 = 9.8.
        let m = [1.0, 9.0, 9.0, 1.0];
        assert!(approx(chi2_contingency_stat(&m, 2, 2), 9.8));
    }

    #[test]
    fn chi2_malformed_is_nan() {
        assert!(chi2_contingency_stat(&[1.0, 2.0, 3.0], 2, 2).is_nan());
    }
}
