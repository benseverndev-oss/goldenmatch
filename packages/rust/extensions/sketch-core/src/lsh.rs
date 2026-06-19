//! Banded LSH bucketing + host-side band selection. `band_hashes` mirrors
//! `goldenmatch/core/sketch.py::band_hashes` byte-for-byte; `optimal_bands`
//! mirrors the Python helper's deterministic procedure (it is NOT on the
//! byte-exact hash path — it only picks an integer `(b, r)`).

use crate::hash::base_hash;

/// Banded-LSH bucket id per band over little-endian signature bytes.
///
/// # Panics
/// Panics if `num_bands == 0` or `sig.len()` is not divisible by `num_bands`.
pub fn band_hashes(sig: &[u64], num_bands: usize) -> Vec<u64> {
    let n = sig.len();
    assert!(
        num_bands > 0 && n.is_multiple_of(num_bands),
        "num_perms {n} not divisible by num_bands {num_bands}"
    );
    let r = n / num_bands;
    let mut out = Vec::with_capacity(num_bands);
    for band in 0..num_bands {
        let mut buf = Vec::with_capacity(8 * (r + 1));
        buf.extend_from_slice(&(band as u64).to_le_bytes());
        for j in 0..r {
            buf.extend_from_slice(&sig[band * r + j].to_le_bytes());
        }
        out.push(base_hash(&buf));
    }
    out
}

/// Pick `(num_bands, rows_per_band)` whose LSH S-curve best matches `threshold`.
/// Ascending divisor scan, fixed 1000-step trapezoidal integral, strict-
/// improvement tie-break (keeps the smaller `num_bands`).
pub fn optimal_bands(num_perms: usize, threshold: f64) -> (usize, usize) {
    const STEPS: usize = 1000;
    // Collision probability for the (b, r) S-curve.
    let pc = |s: f64, r: usize, b: usize| -> f64 { 1.0 - (1.0 - s.powf(r as f64)).powf(b as f64) };
    let integral = |lo: f64, hi: f64, f: &dyn Fn(f64) -> f64| -> f64 {
        let h = (hi - lo) / STEPS as f64;
        let mut s = 0.5 * (f(lo) + f(hi));
        for i in 1..STEPS {
            s += f(lo + i as f64 * h);
        }
        s * h
    };

    let mut best: Option<(usize, usize, f64)> = None;
    for b in 1..=num_perms {
        if !num_perms.is_multiple_of(b) {
            continue;
        }
        let r = num_perms / b;
        let fp = integral(0.0, threshold, &|s| pc(s, r, b));
        let fnn = integral(threshold, 1.0, &|s| 1.0 - pc(s, r, b));
        let err = 0.5 * fp + 0.5 * fnn;
        match best {
            Some((_, _, be)) if err >= be - 1e-12 => {}
            _ => best = Some((b, r, err)),
        }
    }
    let (b, r, _) = best.expect("num_perms >= 1 always yields b=1");
    (b, r)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::minhash::signature;
    use crate::shingle::{shingle, ShingleMode};

    #[test]
    fn band_hashes_golden() {
        let sig = signature(&shingle("hello world", ShingleMode::Char, 3), 8, 42);
        assert_eq!(
            band_hashes(&sig, 4),
            vec![
                12901963457859849374,
                4306753959614852008,
                8435817867480225113,
                7834504510243305493,
            ]
        );
    }

    #[test]
    #[should_panic]
    fn band_hashes_non_divisible_panics() {
        band_hashes(&[0u64; 8], 3);
    }

    #[test]
    fn optimal_bands_golden() {
        assert_eq!(optimal_bands(128, 0.5), (32, 4));
        assert_eq!(optimal_bands(128, 0.8), (8, 16));
        assert_eq!(optimal_bands(128, 0.9), (4, 32));
    }
}
