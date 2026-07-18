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

// ── Arrow-free column interning (shared C-Data ABI, #1788) ──────────────────
//
// The frame kernels (`duplicate_row_ratio`, `distinct_count`) already take
// pre-interned dense `u64` columns, so they are arrow-free. Interning -- turning
// column values into dense value-ids -- was the ONLY arrow-coupled step, living
// in `analysis-native::intern_column` over `arrow::ArrayData`. That coupling is
// why the wasm frame kernels were deferred (Wave 1b): bridging needed either
// arrow-rs in the wasm (heavy) or a SECOND intern impl in TS (a drift surface).
//
// Moving interning here -- over plain typed buffers, not Arrow -- lets every
// surface intern through the SAME code: native adapts its Arrow arrays to these
// buffers, a future wasm/JS surface writes typed arrays straight into linear
// memory, a SQL surface passes flat arrays. Semantics match `intern_column`
// EXACTLY: null -> id 0, non-null -> dense ids from 1, floats canonicalized so
// `-0.0`/`+0.0` fold and every `NaN` collapses to one id (Polars equality). Only
// the equality PARTITION matters to the kernels, never the specific id numbers,
// so this is parity by construction, not a re-derived mirror.
//
// `validity`: one byte per row, 0 = null, non-zero = valid. (A packed Arrow
// validity bitmap is the production refinement; byte-per-row keeps the ABI
// trivial from JS -- just a `Uint8Array`.)
//
// Spike-validated on branch `spike/analysis-wasm-cdata-abi` (PR #1691); this
// lands the shared interner + adversarial parity tests. Wiring `analysis-native`
// to delegate here (retiring its Arrow `intern_column`) and a wasm/TS surface
// are tracked follow-ups on #1788.

/// Canonical f64 bit pattern: one id for all `NaN`, one for `-0.0`/`+0.0`.
/// Mirrors `analysis-native::canon_f64_bits`.
#[inline]
pub fn canon_f64_bits(x: f64) -> u64 {
    if x.is_nan() {
        0x7ff8_0000_0000_0000 // one canonical NaN
    } else if x == 0.0 {
        0.0f64.to_bits() // -0.0 and +0.0 fold (x == 0.0 catches both)
    } else {
        x.to_bits()
    }
}

#[inline]
fn is_null(validity: &[u8], i: usize) -> bool {
    validity.get(i).is_some_and(|&b| b == 0)
}

/// Intern an f64 column (canonicalizing NaN / signed-zero) to dense u64 ids.
pub fn intern_f64(values: &[f64], validity: &[u8]) -> Vec<u64> {
    let mut map: HashMap<u64, u64> = HashMap::new();
    let mut ids = Vec::with_capacity(values.len());
    let mut next: u64 = 1;
    for (i, &v) in values.iter().enumerate() {
        if is_null(validity, i) {
            ids.push(0);
            continue;
        }
        let id = *map.entry(canon_f64_bits(v)).or_insert_with(|| {
            let n = next;
            next += 1;
            n
        });
        ids.push(id);
    }
    ids
}

/// Intern an i64 column to dense u64 ids. Signed/unsigned ints of any width and
/// booleans reach this by promotion (a `u64` is bit-cast to `i64`, which is
/// bijective, so the equality partition is preserved).
pub fn intern_i64(values: &[i64], validity: &[u8]) -> Vec<u64> {
    let mut map: HashMap<i64, u64> = HashMap::new();
    let mut ids = Vec::with_capacity(values.len());
    let mut next: u64 = 1;
    for (i, &v) in values.iter().enumerate() {
        if is_null(validity, i) {
            ids.push(0);
            continue;
        }
        let id = *map.entry(v).or_insert_with(|| {
            let n = next;
            next += 1;
            n
        });
        ids.push(id);
    }
    ids
}

/// Intern a UTF-8 column to dense u64 ids. `offsets` has `n_rows + 1` entries
/// (Arrow utf8 layout): value `i` is `bytes[offsets[i]..offsets[i+1]]`. An empty
/// slice is a valid empty string (distinct from null, which is `validity[i]==0`).
pub fn intern_str(offsets: &[u32], bytes: &[u8], validity: &[u8]) -> Vec<u64> {
    let n = offsets.len().saturating_sub(1);
    let mut map: HashMap<&[u8], u64> = HashMap::new();
    let mut ids = Vec::with_capacity(n);
    let mut next: u64 = 1;
    for i in 0..n {
        if is_null(validity, i) {
            ids.push(0);
            continue;
        }
        let (lo, hi) = (offsets[i] as usize, offsets[i + 1] as usize);
        let id = *map.entry(&bytes[lo..hi]).or_insert_with(|| {
            let nn = next;
            next += 1;
            nn
        });
        ids.push(id);
    }
    ids
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

    // ── Intern-ABI parity vs frame_kernels_adversarial.json (#1788) ──────────
    // The frames are built in code (JSON can't hold NaN/-0.0); the asserted
    // dup_ratio / distinct values are the SAME numbers committed in
    // packages/{python,typescript}/goldenanalysis/tests/fixtures/
    // frame_kernels_adversarial.json. Proves this arrow-free interner produces
    // the same equality partition as analysis-native's Arrow `intern_column`
    // (and the Python/TS reference), so the kernels are surface-agnostic.
    const NULL: u8 = 0;
    const OK: u8 = 1;

    #[test]
    fn intern_float_nan_null_matches_fixture() {
        let nan = f64::NAN;
        // f = [-0.0, 0.0, NaN, NaN, null, 1.0, 1.0]
        let f = intern_f64(
            &[-0.0, 0.0, nan, nan, 0.0, 1.0, 1.0],
            &[OK, OK, OK, OK, NULL, OK, OK],
        );
        assert_eq!(distinct_count(&f), 4); // {-0/+0, NaN, null, 1.0} (fixture distinct.f == 4)
        assert!((duplicate_row_ratio(&[f], 7) - 6.0 / 7.0).abs() < 1e-12); // fixture dup_ratio == 6/7
    }

    #[test]
    fn intern_typed_numeric_matches_fixture() {
        // i = [5,5,3,null,5]   g = [5.0,5.0,3.0,null,5.0]
        let i = intern_i64(&[5, 5, 3, 0, 5], &[OK, OK, OK, NULL, OK]);
        let g = intern_f64(&[5.0, 5.0, 3.0, 0.0, 5.0], &[OK, OK, OK, NULL, OK]);
        assert_eq!(distinct_count(&i), 3); // fixture: distinct.i == 3
        assert_eq!(distinct_count(&g), 3); // fixture: distinct.g == 3
        assert!((duplicate_row_ratio(&[i, g], 5) - 0.6).abs() < 1e-12); // fixture: dup_ratio == 0.6
    }

    #[test]
    fn intern_string_empty_null_matches_fixture() {
        // s = ["a","a","",null,"a","b",null]  -- empty-string is NOT null.
        // bytes "aaab", offsets slice each row; the null rows (3,6) have empty spans.
        let offsets = [0u32, 1, 2, 2, 2, 3, 4, 4];
        let s = intern_str(&offsets, b"aaab", &[OK, OK, OK, NULL, OK, OK, NULL]);
        assert_eq!(distinct_count(&s), 4); // {a, "", b, null}  (fixture: distinct.s == 4)
        assert!((duplicate_row_ratio(&[s], 7) - 5.0 / 7.0).abs() < 1e-12); // fixture: dup_ratio == 5/7
    }

    #[test]
    fn intern_mixed_frame_matches_fixture() {
        let nan = f64::NAN;
        let f = intern_f64(
            &[-0.0, 0.0, nan, nan, 0.0, 1.0, 1.0],
            &[OK, OK, OK, OK, NULL, OK, OK],
        );
        let i = intern_i64(&[5, 5, 3, 3, 0, 5, 5], &[OK, OK, OK, OK, NULL, OK, OK]);
        let offsets = [0u32, 1, 2, 2, 2, 3, 4, 4];
        let s = intern_str(&offsets, b"aaab", &[OK, OK, OK, NULL, OK, OK, NULL]);
        // Fixture `mixed`: distinct f/i/s == 4/3/4; dup_ratio == 2/7 (only rows 0,1
        // are identical across all three columns).
        assert_eq!(distinct_count(&f), 4);
        assert_eq!(distinct_count(&i), 3);
        assert_eq!(distinct_count(&s), 4);
        assert!((duplicate_row_ratio(&[f, i, s], 7) - 2.0 / 7.0).abs() < 1e-12);
    }
}
