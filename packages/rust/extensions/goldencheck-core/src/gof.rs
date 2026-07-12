//! Chi-squared goodness-of-fit for the `statistical.py` Benford profiler. That
//! profiler's `_compute_benford` calls `scipy.stats.chisquare(f_obs, f_exp)` and
//! consumes BOTH returned values: the chi2 statistic (deterministic) and the
//! p-value (`round(pvalue, 6)`). This kernel reproduces both -- the statistic is
//! pure arithmetic (float-epsilon exact), the p-value is the ONE owned epsilon
//! divergence class in W4.
//!
//! The p-value is the UPPER tail of a chi-squared distribution with `df = k - 1`
//! degrees of freedom (scipy `chisquare` defaults `ddof=0`; Benford's 9 digits
//! give `df = 8`). scipy computes it as `chdtrc(df, chi2) = gammaincc(df/2,
//! chi2/2)` -- the upper regularized incomplete gamma. We call
//! `statrs::function::gamma::gamma_ur` directly (the same special function) so
//! the tail is accurate. We deliberately do NOT compute `1.0 - ChiSquared::cdf`
//! (which is `gamma_lr`): for large chi2 / small p that subtraction cancels
//! catastrophically and loses every significant figure of the tail probability.
use statrs::function::gamma::gamma_ur;

/// Chi-squared statistic + upper-tail p-value matching
/// `scipy.stats.chisquare(f_obs=observed, f_exp=expected)`. `observed` and
/// `expected` must be equal-length; the caller (the Benford profiler) guarantees
/// their sums agree (expected = Benford proportions * total). Returns
/// `chi2 = Sum (obs-exp)^2 / exp` and `p = gammaincc((k-1)/2, chi2/2)`.
///
/// Edge cases: `chi2 == 0.0 -> p = 1.0` (perfect fit); fewer than 2 categories
/// (`df <= 0`) -> p = NaN; empty or length-mismatched input -> `(NaN, NaN)`.
pub fn chi2_gof(observed: &[f64], expected: &[f64]) -> (f64, f64) {
    let n = observed.len();
    if n == 0 || n != expected.len() {
        return (f64::NAN, f64::NAN);
    }

    // Statistic: Sum (obs-exp)^2 / exp. scipy does NOT renormalize expected to
    // the observed sum -- the caller already matches the sums, so neither do we.
    let mut chi2 = 0.0f64;
    for i in 0..n {
        let diff = observed[i] - expected[i];
        chi2 += diff * diff / expected[i];
    }

    // Perfect fit: the survival function is exactly 1 at 0 (and gamma_ur is
    // 1.0 there anyway, but short-circuit to avoid any tail rounding).
    if chi2 == 0.0 {
        return (chi2, 1.0);
    }

    let df = (n as f64) - 1.0;
    if df <= 0.0 {
        return (chi2, f64::NAN);
    }
    // Upper regularized incomplete gamma = scipy chdtrc/gammaincc (NOT 1 - cdf).
    let pvalue = gamma_ur(df / 2.0, chi2 / 2.0);
    (chi2, pvalue)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn approx(a: f64, b: f64) -> bool {
        (a - b).abs() <= 1e-12 * (1.0 + a.abs().max(b.abs()))
    }

    #[test]
    fn perfect_fit_is_chi2_zero_p_one() {
        // obs == exp -> every residual 0 -> chi2 exactly 0, p exactly 1.
        let obs = [10.0, 10.0, 10.0, 10.0];
        let exp = [10.0, 10.0, 10.0, 10.0];
        let (chi2, p) = chi2_gof(&obs, &exp);
        assert_eq!(chi2, 0.0);
        assert_eq!(p, 1.0);
    }

    #[test]
    fn benford_shaped_matches_scipy() {
        // 9-digit Benford-shaped counts vs Benford*total. scipy.stats.chisquare
        // returns chi2=0.00230826940881933, p=0.9999999999999262.
        let obs = [301.0, 176.0, 125.0, 97.0, 79.0, 67.0, 58.0, 51.0, 46.0];
        let exp = [
            301.03, 176.09, 124.94, 96.91, 79.18, 66.95, 57.99, 51.15, 45.76,
        ];
        let (chi2, p) = chi2_gof(&obs, &exp);
        assert!(approx(chi2, 0.002_308_269_408_819_33), "chi2={chi2}");
        assert!(approx(p, 0.999_999_999_999_926_2), "p={p}");
    }

    #[test]
    fn skewed_matches_scipy() {
        // obs=[40,10,5,5], exp=[15,15,15,15] (equal sums, df=3). scipy:
        // chi2=56.66666666666666, p=3.0273617338160816e-12.
        let obs = [40.0, 10.0, 5.0, 5.0];
        let exp = [15.0, 15.0, 15.0, 15.0];
        let (chi2, p) = chi2_gof(&obs, &exp);
        assert!(approx(chi2, 56.666_666_666_666_66), "chi2={chi2}");
        assert!(approx(p, 3.027_361_733_816_081_6e-12), "p={p}");
    }

    #[test]
    fn empty_or_mismatched_is_nan() {
        let (c0, p0) = chi2_gof(&[], &[]);
        assert!(c0.is_nan() && p0.is_nan());
        let (c1, p1) = chi2_gof(&[1.0, 2.0], &[1.0]);
        assert!(c1.is_nan() && p1.is_nan());
    }

    #[test]
    fn single_category_df_zero_is_nan_pvalue() {
        // One category -> df=0; nonzero chi2 -> p is NaN (statistic still finite).
        let (chi2, p) = chi2_gof(&[5.0], &[10.0]);
        assert!(approx(chi2, 2.5));
        assert!(p.is_nan());
    }
}
