//! wasm-bindgen wrapper over `analysis-core`. TS analogue of the native crate:
//! thin shims delegating to analysis-core, so histogram/quantile are
//! byte-identical across Python, the native wheel, and TS WASM.
//!
//! Boundary: numeric arrays cross as Float64Array (zero-copy contiguous), once
//! per call. `histogram` is returned FLATTENED as
//! `[edge0, count0, edge1, count1, ...]` (wasm-bindgen marshals `Vec<f64>` ↔
//! `Float64Array`; counts are exact integers well within 2^53).

use analysis_core::{histogram, quantile};

/// Flatten analysis-core's `Vec<(f64, i64)>` histogram to `[edge, count, ...]`.
/// `bins` is `i32` (a JS `number` may be 0/negative; keep it SIGNED so
/// analysis-core's `bins < 1 => []` guard fires instead of wrapping under an
/// unsigned cast).
pub fn histogram_flat_impl(values: &[f64], bins: i32) -> Vec<f64> {
    let pairs = histogram(values, bins as i64);
    let mut out = Vec::with_capacity(pairs.len() * 2);
    for (edge, count) in pairs {
        out.push(edge);
        out.push(count as f64);
    }
    out
}

pub fn quantile_impl(values: &[f64], q: f64) -> f64 {
    quantile(values, q)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn histogram_flat_matches_pairs() {
        // 0,1,2,3 into 2 bins -> edges 0.0 and 1.5, counts 2 and 2.
        let f = histogram_flat_impl(&[0.0, 1.0, 2.0, 3.0], 2);
        // [edge0, count0, edge1, count1]
        assert_eq!(f.len(), 4);
        assert_eq!(f[0], 0.0); // lo edge
        assert_eq!(f[1], 2.0); // first-bin count
        assert_eq!(f[3], 2.0); // second-bin count
        assert_eq!(f[0] + f[1] + f[3], 4.0); // total count preserved (2+2)
    }

    #[test]
    fn histogram_bins_lt_1_is_empty() {
        assert!(histogram_flat_impl(&[1.0, 2.0], 0).is_empty());
        assert!(histogram_flat_impl(&[1.0, 2.0], -3).is_empty());
    }

    #[test]
    fn histogram_empty_input_is_empty() {
        assert!(histogram_flat_impl(&[], 4).is_empty());
    }

    #[test]
    fn quantile_median_interpolates() {
        assert_eq!(quantile_impl(&[1.0, 2.0, 3.0, 4.0], 0.5), 2.5);
    }

    #[test]
    fn quantile_empty_is_zero() {
        assert_eq!(quantile_impl(&[], 0.5), 0.0);
    }
}

#[cfg(target_arch = "wasm32")]
mod wasm {
    use super::{histogram_flat_impl, quantile_impl};
    use wasm_bindgen::prelude::*;

    /// JS entry: equal-width histogram of `values` into `bins`, returned flat as
    /// a Float64Array `[edge0, count0, edge1, count1, ...]`.
    #[wasm_bindgen]
    pub fn histogram(values: &[f64], bins: i32) -> Vec<f64> {
        histogram_flat_impl(values, bins)
    }

    /// JS entry: linear-interpolation quantile of `values` at `q`.
    #[wasm_bindgen]
    pub fn quantile(values: &[f64], q: f64) -> f64 {
        quantile_impl(values, q)
    }
}
