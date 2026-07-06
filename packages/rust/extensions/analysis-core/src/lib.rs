//! Pyo3-free aggregation kernels for GoldenAnalysis.
//!
//! Byte-identical Rust mirror of the pure-Python loops in
//! `goldenanalysis/core/aggregate.py` (`histogram`, `quantile`). The pure-Python
//! path stays the reference + fallback; these kernels back the optional
//! `analysis-native` abi3 wheel. No pyo3, no Arrow -- plain slices in, plain
//! values out -- so the same logic can later back a SQL surface.
//!
//! Inputs are assumed finite (cluster sizes / scores). NaN/inf are out of the
//! parity contract: the Python reference (`min`/`max`/`sorted`) is undefined on
//! them too.

use std::collections::{HashMap, HashSet};

/// Equal-width histogram over `[min, max]`, mirroring `aggregate.histogram`.
///
/// Returns `[(left_edge, count), ...]` with `bins` entries; the right edge is
/// inclusive (the max lands in the last bin); all-equal input collapses to a
/// single `[(value, count)]` bin; empty input or `bins < 1` => `[]`. Uses the
/// SAME float op order as the Python loop so the edges + bucket assignment are
/// bit-identical.
pub fn histogram(values: &[f64], bins: i64) -> Vec<(f64, i64)> {
    if values.is_empty() || bins < 1 {
        return Vec::new();
    }
    let bins = bins as usize;
    let mut lo = values[0];
    let mut hi = values[0];
    for &v in values {
        if v < lo {
            lo = v;
        }
        if v > hi {
            hi = v;
        }
    }
    if hi == lo {
        return vec![(lo, values.len() as i64)];
    }
    let width = (hi - lo) / bins as f64;
    let mut counts = vec![0i64; bins];
    for &v in values {
        // int((v - lo) / width): truncates toward zero == Python int() for >= 0.
        let mut idx = ((v - lo) / width) as usize;
        if idx >= bins {
            idx = bins - 1; // right-edge inclusive
        }
        counts[idx] += 1;
    }
    (0..bins).map(|i| (lo + i as f64 * width, counts[i])).collect()
}

/// Linear-interpolation quantile (numpy default), mirroring `aggregate.quantile`.
///
/// Empty input => `0.0`. Single value => that value. Otherwise interpolates
/// between the two order statistics straddling `q*(n-1)`, the same op order as
/// the Python loop. Inputs assumed finite.
pub fn quantile(values: &[f64], q: f64) -> f64 {
    if values.is_empty() {
        return 0.0;
    }
    let mut vals = values.to_vec();
    vals.sort_by(|a, b| a.total_cmp(b));
    if vals.len() == 1 {
        return vals[0];
    }
    let pos = q * (vals.len() - 1) as f64;
    let lo_idx = pos as usize; // int(pos); pos >= 0 for q in [0, 1]
    let frac = pos - lo_idx as f64;
    if lo_idx + 1 < vals.len() {
        vals[lo_idx] + (vals[lo_idx + 1] - vals[lo_idx]) * frac
    } else {
        vals[lo_idx]
    }
}

/// Fraction of rows in an exact-duplicate group (size >= 2). Empty => 0.0.
/// `columns`: interned u64 ids, one Vec per column, each of length `n_rows`.
/// Row identity is the tuple of per-column ids (columns interned independently).
pub fn duplicate_row_ratio(columns: &[Vec<u64>], n_rows: usize) -> f64 {
    if n_rows == 0 {
        return 0.0;
    }
    let mut counts: HashMap<Vec<u64>, usize> = HashMap::new();
    for i in 0..n_rows {
        let row: Vec<u64> = columns.iter().map(|c| c[i]).collect();
        *counts.entry(row).or_insert(0) += 1;
    }
    let dup: usize = counts.values().copied().filter(|&c| c >= 2).sum();
    dup as f64 / n_rows as f64
}

/// Distinct value count for one interned column (null id counts as a value),
/// matching polars `n_unique`.
pub fn distinct_count(column: &[u64]) -> i64 {
    column.iter().copied().collect::<HashSet<u64>>().len() as i64
}

/// Arithmetic mean. Empty => 0.0 (matches `quantile`'s empty convention).
///
/// NAIVE left-to-right summation (`iter().sum()` folds from 0.0) to byte-match the
/// Python reference `sum(values)/len(values)`. Do NOT swap to a pairwise/SIMD sum:
/// it would reorder the additions and break byte-parity with `_mean_pure`.
pub fn mean(values: &[f64]) -> f64 {
    if values.is_empty() {
        return 0.0;
    }
    values.iter().sum::<f64>() / values.len() as f64
}

/// Minimum over finite values. Empty => 0.0. (NaN-ignoring via `f64::min`; the wired
/// callers pass finite values -- see the spec's min/max-NaN note.)
pub fn min(values: &[f64]) -> f64 {
    if values.is_empty() {
        return 0.0;
    }
    values.iter().copied().fold(f64::INFINITY, f64::min)
}

/// Maximum over finite values. Empty => 0.0.
pub fn max(values: &[f64]) -> f64 {
    if values.is_empty() {
        return 0.0;
    }
    values.iter().copied().fold(f64::NEG_INFINITY, f64::max)
}

/// Discrete cluster-size histogram: counts of sizes equal to 1, 2, 3, and >= 4.
/// Returns exactly 4 buckets `[n1, n2, n3, n4plus]`. Empty => `[0, 0, 0, 0]`.
///
/// Input is exact-integer cluster sizes as f64 (reuses the numeric Float64 bridge).
/// Sizes are always integers >= 1 in practice; a fractional/<=0 value that matches no
/// bucket is simply not counted -- it cannot occur for real cluster sizes.
pub fn cluster_size_histogram(sizes: &[f64]) -> Vec<i64> {
    let mut buckets = [0i64; 4];
    for &s in sizes {
        if s == 1.0 {
            buckets[0] += 1;
        } else if s == 2.0 {
            buckets[1] += 1;
        } else if s == 3.0 {
            buckets[2] += 1;
        } else if s >= 4.0 {
            buckets[3] += 1;
        }
    }
    buckets.to_vec()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cluster_size_histogram_basic() {
        // sizes 1,1,2,3,4,5,1 -> n1=3, n2=1, n3=1, n4plus=2
        assert_eq!(cluster_size_histogram(&[1.0, 1.0, 2.0, 3.0, 4.0, 5.0, 1.0]), vec![3, 1, 1, 2]);
    }

    #[test]
    fn cluster_size_histogram_empty() {
        assert_eq!(cluster_size_histogram(&[]), vec![0, 0, 0, 0]);
    }

    #[test]
    fn cluster_size_histogram_boundary() {
        assert_eq!(cluster_size_histogram(&[3.0, 4.0]), vec![0, 0, 1, 1]);
        assert_eq!(cluster_size_histogram(&[1.0, 1.0, 1.0]), vec![3, 0, 0, 0]);
    }

    #[test]
    fn mean_basic() {
        assert_eq!(mean(&[1.0, 2.0, 3.0]), 2.0);
        assert_eq!(mean(&[5.0]), 5.0);
    }

    #[test]
    fn mean_empty_is_zero() {
        assert_eq!(mean(&[]), 0.0);
    }

    #[test]
    fn mean_naive_left_to_right_sum() {
        // 1e16 + 1.0 == 1e16 in f64, so a naive left-to-right sum absorbs the small
        // values and the mean is 0.0/n. A pairwise/SIMD sum would recover them -- this
        // pins the naive summation order against the Python `sum()` reference.
        let mut v = vec![1e16];
        v.extend(std::iter::repeat(1.0).take(100));
        v.push(-1e16);
        let expected = v.iter().sum::<f64>() / v.len() as f64;
        assert_eq!(mean(&v), expected);
    }

    #[test]
    fn min_max_basic() {
        assert_eq!(min(&[3.0, 1.0, 2.0]), 1.0);
        assert_eq!(max(&[3.0, 1.0, 2.0]), 3.0);
        assert_eq!(min(&[-1.5]), -1.5);
    }

    #[test]
    fn min_max_empty_is_zero() {
        assert_eq!(min(&[]), 0.0);
        assert_eq!(max(&[]), 0.0);
    }

    #[test]
    fn histogram_empty_or_no_bins() {
        assert_eq!(histogram(&[], 10), Vec::new());
        assert_eq!(histogram(&[1.0, 2.0], 0), Vec::new());
        assert_eq!(histogram(&[1.0, 2.0], -1), Vec::new());
    }

    #[test]
    fn histogram_all_equal_collapses() {
        assert_eq!(histogram(&[5.0], 3), vec![(5.0, 1)]);
        assert_eq!(histogram(&[2.0, 2.0, 2.0], 4), vec![(2.0, 3)]);
    }

    #[test]
    fn histogram_right_edge_inclusive() {
        // 0..=10 over 10 bins (width 1): the max (10) lands in the last bin with 9.
        let vals: Vec<f64> = (0..=10).map(|i| i as f64).collect();
        let got = histogram(&vals, 10);
        let expected: Vec<(f64, i64)> = (0..10)
            .map(|i| (i as f64, if i == 9 { 2 } else { 1 }))
            .collect();
        assert_eq!(got, expected);
    }

    #[test]
    fn quantile_edges_and_interpolation() {
        // cluster sizes [1, 1, 3, 2] -> sorted [1, 1, 2, 3]
        let sizes = [1.0, 1.0, 3.0, 2.0];
        assert_eq!(quantile(&sizes, 0.5), 1.5); // 1 + (2-1)*0.5
        assert!((quantile(&sizes, 0.95) - 2.85).abs() < 1e-12); // 2 + (3-2)*0.85
        assert_eq!(quantile(&sizes, 0.0), 1.0); // min
        assert_eq!(quantile(&sizes, 1.0), 3.0); // max
    }

    #[test]
    fn quantile_empty_and_single() {
        assert_eq!(quantile(&[], 0.5), 0.0);
        assert_eq!(quantile(&[7.0], 0.5), 7.0);
    }

    #[test]
    fn dup_ratio_empty_is_zero() {
        assert_eq!(duplicate_row_ratio(&[], 0), 0.0);
        assert_eq!(duplicate_row_ratio(&[vec![]], 0), 0.0);
    }

    #[test]
    fn dup_ratio_group_of_three_counts_all_members() {
        // 2 columns, 4 rows; rows 0,1,2 identical (ids (1,10)), row 3 unique.
        let cols = vec![vec![1, 1, 1, 2], vec![10, 10, 10, 20]];
        assert_eq!(duplicate_row_ratio(&cols, 4), 3.0 / 4.0);
    }

    #[test]
    fn dup_ratio_no_duplicates_is_zero() {
        let cols = vec![vec![1, 2, 3]];
        assert_eq!(duplicate_row_ratio(&cols, 3), 0.0);
    }

    #[test]
    fn distinct_count_counts_unique_ids_including_null_id() {
        assert_eq!(distinct_count(&[1, 1, 2, 0]), 3); // {1,2,0} — null id 0 counts
        assert_eq!(distinct_count(&[]), 0);
        assert_eq!(distinct_count(&[5, 5, 5]), 1);
    }
}
