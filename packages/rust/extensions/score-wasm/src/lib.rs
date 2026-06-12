//! wasm-bindgen wrapper over `goldenmatch-score-core`. The TS analogue of the
//! `native` pyo3 crate: thin shims delegating to `score-core` so the scorers
//! are byte-identical across Python, the FFI UDFs, and TS WASM.
//!
//! Slice-1 covered scorer ids (must match the TS backend): 0=jaro_winkler,
//! 1=levenshtein, 3=exact. id=2 (token_sort) is deliberately NOT wired on the
//! TS side in slice 1 (normalization parity unresolved — see the design spec).
//!
//! Boundary design: the batch `score_matrix` entry crosses the JS<->WASM boundary
//! ONCE per NxN block (values arrive as one separator-joined string), never per
//! pair — per the perf-audit lesson that boundary cost dwarfs a single scorer.

use goldenmatch_score_core::score_one;

/// Full row-major NxN similarity matrix for `values` under `scorer_id`.
/// Diagonal = 0.0 and the matrix is symmetric, matching the pure-TS
/// `scoreMatrix` (which fills the upper triangle, mirrors it, and leaves the
/// diagonal 0). NULL handling is done JS-side (this sees only strings).
pub fn score_matrix_impl(values: &[&str], scorer_id: u8) -> Vec<f64> {
    let n = values.len();
    let mut out = vec![0.0_f64; n * n];
    for i in 0..n {
        for j in (i + 1)..n {
            let s = score_one(scorer_id, values[i], values[j]);
            out[i * n + j] = s;
            out[j * n + i] = s;
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn matrix_is_symmetric_zero_diagonal() {
        // jaro_winkler id=0. "abc"/"abc" on the diagonal stays 0 (diagonal is
        // never scored); off-diagonal is the real score and mirrored.
        let vals = ["abc", "abd", "xyz"];
        let m = score_matrix_impl(&vals, 0);
        assert_eq!(m.len(), 9);
        assert_eq!(m[0], 0.0); // diagonal
        assert_eq!(m[1], m[3]); // symmetric (0,1)==(1,0)
        assert!(m[1] > 0.0 && m[1] < 1.0); // abc~abd is a partial match
    }

    #[test]
    fn exact_id3_is_one_or_zero() {
        let vals = ["a", "a", "b"];
        let m = score_matrix_impl(&vals, 3);
        assert_eq!(m[1], 1.0); // (0,1) a==a
        assert_eq!(m[2], 0.0); // (0,2) a!=b
    }
}

#[cfg(target_arch = "wasm32")]
mod wasm {
    use super::score_matrix_impl;
    use wasm_bindgen::prelude::*;

    /// JS entry: `values` is one string with fields joined by `sep` (a 1-char
    /// separator the caller guarantees is absent from the data, e.g. U+001E).
    /// Returns the flat row-major NxN matrix as a Float64Array.
    #[wasm_bindgen]
    pub fn score_matrix(values: &str, sep: &str, scorer_id: u8) -> Vec<f64> {
        let parts: Vec<&str> = if values.is_empty() {
            Vec::new()
        } else {
            values.split(sep).collect()
        };
        score_matrix_impl(&parts, scorer_id)
    }
}
