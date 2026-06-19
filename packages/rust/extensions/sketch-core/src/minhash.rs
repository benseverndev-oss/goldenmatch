//! MinHash signatures. Mirrors `goldenmatch/core/sketch.py::signature` /
//! `estimate_jaccard` exactly, including the `u128` modular multiply.

use crate::hash::splitmix64;

/// Mersenne prime 2^61 - 1, the permutation field modulus.
const MERSENNE_P: u64 = (1 << 61) - 1;

/// Derive the `(a, b)` permutation coefficients from `seed` via a splitmix64
/// stream. `a in [1, P-1]`, `b in [0, P-1]`. Coefficients may repeat — do not
/// deduplicate.
fn coefficients(num_perms: usize, seed: u64) -> (Vec<u64>, Vec<u64>) {
    let mut a = Vec::with_capacity(num_perms);
    let mut b = Vec::with_capacity(num_perms);
    let mut state = seed;
    for _ in 0..num_perms {
        let (v, s) = splitmix64(state);
        state = s;
        a.push((v % (MERSENNE_P - 1)) + 1);
        let (v, s) = splitmix64(state);
        state = s;
        b.push(v % MERSENNE_P);
    }
    (a, b)
}

/// MinHash signature of a shingle set. An empty set yields all `u64::MAX`.
pub fn signature(shingles: &[u64], num_perms: usize, seed: u64) -> Vec<u64> {
    let (a, b) = coefficients(num_perms, seed);
    let p = MERSENNE_P as u128;
    let mut sig = vec![u64::MAX; num_perms];
    for i in 0..num_perms {
        let (ai, bi) = (a[i] as u128, b[i] as u128);
        let mut m = u64::MAX;
        for &x in shingles {
            let xr = (x % MERSENNE_P) as u128;
            let val = ((ai * xr + bi) % p) as u64;
            if val < m {
                m = val;
            }
        }
        sig[i] = m;
    }
    sig
}

/// Estimated Jaccard similarity = fraction of equal signature positions.
pub fn estimate_jaccard(sig_a: &[u64], sig_b: &[u64]) -> f64 {
    if sig_a.is_empty() {
        return 0.0;
    }
    let eq = sig_a.iter().zip(sig_b).filter(|(x, y)| x == y).count();
    eq as f64 / sig_a.len() as f64
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::shingle::{shingle, ShingleMode};

    #[test]
    fn signature_golden() {
        let sh = shingle("hello world", ShingleMode::Char, 3);
        assert_eq!(
            signature(&sh, 8, 42),
            vec![
                17041167395646177,
                77277049784527919,
                186077308732231195,
                564709922545612565,
                113913446168519210,
                82732991858855180,
                16713511289126713,
                83663724776489692,
            ]
        );
    }

    #[test]
    fn empty_is_all_max() {
        assert_eq!(signature(&[], 8, 42), vec![u64::MAX; 8]);
    }

    #[test]
    fn jaccard_self_is_one() {
        let sh = shingle("the quick brown fox jumps", ShingleMode::Word, 2);
        let sig = signature(&sh, 128, 7);
        assert_eq!(estimate_jaccard(&sig, &sig), 1.0);
    }

    #[test]
    fn large_coefficient_no_overflow() {
        // Drive the a*xr+b product near the u128 ceiling (a, xr ~ 2^61 => ~2^122).
        // A large num_perms + non-trivial shingles exercises many coefficients.
        let sh = shingle(&"word ".repeat(40), ShingleMode::Word, 2);
        let sig = signature(&sh, 256, 0xDEADBEEFCAFEF00D);
        assert!(sig.iter().all(|&v| v < MERSENNE_P)); // all reduced into the field
    }
}
