//! wasm-bindgen wrapper over `analysis-core`. TS analogue of the native crate:
//! thin shims delegating to analysis-core, so histogram/quantile are
//! byte-identical across Python, the native wheel, and TS WASM.
//!
//! Boundary: numeric arrays cross as Float64Array (zero-copy contiguous), once
//! per call. `histogram` is returned FLATTENED as
//! `[edge0, count0, edge1, count1, ...]` (wasm-bindgen marshals `Vec<f64>` ↔
//! `Float64Array`; counts are exact integers well within 2^53).

use analysis_core::{cluster_size_histogram, histogram, max, mean, min, quantile};

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

pub fn mean_impl(values: &[f64]) -> f64 {
    mean(values)
}

pub fn min_impl(values: &[f64]) -> f64 {
    min(values)
}

pub fn max_impl(values: &[f64]) -> f64 {
    max(values)
}

/// Cluster-size histogram flattened as a Float64Array of 4 counts `[n1, n2, n3, n4plus]`
/// (counts are exact integers well within 2^53).
pub fn cluster_size_histogram_impl(sizes: &[f64]) -> Vec<f64> {
    cluster_size_histogram(sizes).into_iter().map(|c| c as f64).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cluster_size_histogram_impl_matches_core() {
        assert_eq!(cluster_size_histogram_impl(&[1.0, 1.0, 2.0, 5.0]), vec![2.0, 1.0, 0.0, 1.0]);
        assert_eq!(cluster_size_histogram_impl(&[]), vec![0.0, 0.0, 0.0, 0.0]);
    }

    #[test]
    fn mean_matches_core() {
        assert_eq!(mean_impl(&[1.0, 2.0, 3.0]), 2.0);
        assert_eq!(mean_impl(&[]), 0.0);
    }

    #[test]
    fn min_max_impl_basic() {
        assert_eq!(min_impl(&[3.0, 1.0, 2.0]), 1.0);
        assert_eq!(max_impl(&[3.0, 1.0, 2.0]), 3.0);
        assert_eq!(min_impl(&[]), 0.0);
        assert_eq!(max_impl(&[]), 0.0);
    }

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
    use super::{
        cluster_size_histogram_impl, histogram_flat_impl, max_impl, mean_impl, min_impl,
        quantile_impl,
    };
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

    /// JS entry: arithmetic mean of `values` (empty => 0.0).
    #[wasm_bindgen]
    pub fn mean(values: &[f64]) -> f64 {
        mean_impl(values)
    }

    /// JS entry: minimum of `values` (empty => 0.0).
    #[wasm_bindgen]
    pub fn min(values: &[f64]) -> f64 {
        min_impl(values)
    }

    /// JS entry: maximum of `values` (empty => 0.0).
    #[wasm_bindgen]
    pub fn max(values: &[f64]) -> f64 {
        max_impl(values)
    }

    /// JS entry: discrete cluster-size histogram as a Float64Array `[n1,n2,n3,n4plus]`.
    #[wasm_bindgen]
    pub fn cluster_size_histogram(sizes: &[f64]) -> Vec<f64> {
        cluster_size_histogram_impl(sizes)
    }

    // --- Spike (Wave 1b unblock): frame kernels via the minimal C-Data ABI ---
    //
    // The previously-deferred frame kernels (duplicate_row_ratio / distinct_count)
    // run here with NO arrow-rs. The column-handle pattern mirrors building an
    // Arrow RecordBatch column-by-column: JS writes each column's raw buffers
    // (values + a byte-per-row validity mask; utf8 also offsets + bytes) straight
    // into wasm linear memory, and the SHARED `analysis_core` interner turns them
    // into dense u64 ids -- the exact code the native wheel will use, so parity is
    // by construction (see analysis-core's intern_* fixture tests), not a re-mirror.

    /// Accumulates interned columns of a frame, then runs `duplicate_row_ratio`.
    /// Usage from JS: `const fi = new FrameInterner(n); fi.push_f64(vals, valid);
    /// fi.push_str(offsets, bytes, valid); fi.duplicate_row_ratio();`
    #[wasm_bindgen]
    pub struct FrameInterner {
        cols: Vec<Vec<u64>>,
        n_rows: usize,
    }

    #[wasm_bindgen]
    impl FrameInterner {
        #[wasm_bindgen(constructor)]
        pub fn new(n_rows: usize) -> FrameInterner {
            FrameInterner { cols: Vec::new(), n_rows }
        }

        /// Push an f64 column (NaN / signed-zero canonicalized in the core).
        pub fn push_f64(&mut self, values: &[f64], validity: &[u8]) {
            self.cols.push(analysis_core::intern_f64(values, validity));
        }

        /// Push an i64 column (JS `BigInt64Array`).
        pub fn push_i64(&mut self, values: &[i64], validity: &[u8]) {
            self.cols.push(analysis_core::intern_i64(values, validity));
        }

        /// Push a utf8 column: Arrow-layout `offsets` (n_rows+1) + concatenated
        /// `bytes` + validity. An empty slice is a valid empty string, not null.
        pub fn push_str(&mut self, offsets: &[u32], bytes: &[u8], validity: &[u8]) {
            self.cols.push(analysis_core::intern_str(offsets, bytes, validity));
        }

        /// Fraction of rows in an exact-duplicate group (>=2) over the pushed cols.
        pub fn duplicate_row_ratio(&self) -> f64 {
            analysis_core::duplicate_row_ratio(&self.cols, self.n_rows)
        }
    }

    /// JS entry: distinct value count of one f64 column (null counts as a value).
    #[wasm_bindgen]
    pub fn distinct_count_f64(values: &[f64], validity: &[u8]) -> i64 {
        analysis_core::distinct_count(&analysis_core::intern_f64(values, validity))
    }

    /// JS entry: distinct value count of one i64 column.
    #[wasm_bindgen]
    pub fn distinct_count_i64(values: &[i64], validity: &[u8]) -> i64 {
        analysis_core::distinct_count(&analysis_core::intern_i64(values, validity))
    }

    /// JS entry: distinct value count of one utf8 column.
    #[wasm_bindgen]
    pub fn distinct_count_str(offsets: &[u32], bytes: &[u8], validity: &[u8]) -> i64 {
        analysis_core::distinct_count(&analysis_core::intern_str(offsets, bytes, validity))
    }
}
