//! Benford leading-digit histogram.
//!
//! Behaviour-exact replacement for `_extract_leading_digits` +
//! `Counter(...)` in `goldencheck/baseline/statistical.py` (and the identical
//! loop in `goldencheck/drift/detector.py`). The Python reference, per value:
//!
//! ```python
//! if v <= 0 or not math.isfinite(v):
//!     continue
//! exp = math.floor(math.log10(v))
//! normalised = v / (10 ** exp)
//! d = int(normalised)            # truncates toward zero; v > 0
//! if 1 <= d <= 9:
//!     digits.append(d)
//! ```
//!
//! We return the per-digit counts for 1..=9 directly (the `Counter` the Python
//! caller builds), so the caller's chi-squared step is unchanged.
//!
//! Parity subtlety: Python divides by `10 ** exp`, where `exp` is an `int`, so
//! the divisor is the *correctly-rounded* f64 nearest to 10^exp (Python forms an
//! exact bignum for exp >= 0, then rounds to f64 on the division). Rust's
//! `10f64.powi(exp)` instead accumulates rounding and disagrees at large
//! exponents (e.g. 1e300 -> the quotient drifts off 1.0, dropping a digit-1
//! count). We therefore divide by a precomputed table of correctly-rounded
//! powers of ten -- `"1e{exp}".parse::<f64>()` yields the exact same f64 as
//! Python's `10 ** exp` for every reachable exponent (verified -323..=308). The
//! `log10().floor()` step already agrees with `math.log10` (shared libm), so
//! this makes the histogram byte-identical; the goldencheck parity test asserts
//! it on random + adversarial (powers-of-ten, sub-normal-magnitude) data.

use std::sync::OnceLock;

// f64 exponent range is roughly 1e-323 .. 1e308; index = exp + OFFSET.
const POW10_MIN_EXP: i32 = -323;
const POW10_MAX_EXP: i32 = 308;
const POW10_LEN: usize = (POW10_MAX_EXP - POW10_MIN_EXP + 1) as usize;

/// Correctly-rounded f64 powers of ten, indexed by `exp - POW10_MIN_EXP`.
/// Built once by parsing the decimal literal `1e{exp}` (the same correctly-
/// rounded conversion Python applies to `10 ** exp`).
fn pow10_table() -> &'static [f64; POW10_LEN] {
    static TABLE: OnceLock<[f64; POW10_LEN]> = OnceLock::new();
    TABLE.get_or_init(|| {
        let mut t = [0.0f64; POW10_LEN];
        for (i, slot) in t.iter_mut().enumerate() {
            let exp = i as i32 + POW10_MIN_EXP;
            *slot = format!("1e{exp}")
                .parse::<f64>()
                .expect("decimal power of ten parses");
        }
        t
    })
}

/// The correctly-rounded f64 value of 10^exp, matching Python's `10 ** exp`.
fn pow10(exp: i32) -> f64 {
    let idx = exp - POW10_MIN_EXP;
    if (0..POW10_LEN as i32).contains(&idx) {
        pow10_table()[idx as usize]
    } else {
        // Outside the representable normal range (extreme sub-normals); fall
        // back rather than panic. Such magnitudes never appear in a real
        // Benford column.
        10f64.powi(exp)
    }
}

/// Leading-digit (1..=9) counts for the Benford conformance check.
///
/// `out[i]` is the number of finite, strictly-positive values whose leading
/// significant digit is `i + 1`. Non-positive and non-finite values are
/// skipped, exactly as the Python reference does.
pub fn benford_leading_digits(values: &[f64]) -> [u64; 9] {
    let mut counts = [0u64; 9];
    for &v in values {
        if v <= 0.0 || !v.is_finite() {
            continue;
        }
        let exp = v.log10().floor() as i32;
        let normalised = v / pow10(exp);
        // `as i32` truncates toward zero; `normalised` is > 0 here.
        let d = normalised as i32;
        if (1..=9).contains(&d) {
            counts[(d - 1) as usize] += 1;
        }
    }
    counts
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn basic_digits() {
        // 1.x, 9.x, 19 -> 1, 200 -> 2, 0 and -5 skipped, NaN/inf skipped.
        let v = [1.5, 9.9, 19.0, 200.0, 0.0, -5.0, f64::NAN, f64::INFINITY];
        let c = benford_leading_digits(&v);
        assert_eq!(c[0], 2); // digit 1: 1.5, 19.0
        assert_eq!(c[1], 1); // digit 2: 200.0
        assert_eq!(c[8], 1); // digit 9: 9.9
        assert_eq!(c.iter().sum::<u64>(), 4);
    }

    #[test]
    fn empty_is_all_zero() {
        assert_eq!(benford_leading_digits(&[]), [0u64; 9]);
    }
}
