//! Pair + candidate-estimation kernels — behavior-exact replacements for the
//! pure-Python primitives in `goldenmatch/core/pairs.py`.
//!
//! These are the "Native Core" primitives from the native-runtime roadmap:
//! canonicalization, max-score dedup, and candidate estimation. Every function
//! is bit-exact with its Python reference (integer arithmetic + a strict-`>`
//! max reduction — no float tolerance), so the `pairs` component is gated on by
//! default once the parity test passes.
use arrow::array::ArrayData;
use arrow::pyarrow::PyArrowType;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

/// Canonicalize each pair to `(min, max, score)`, preserving input order and
/// duplicates. Mirrors the project-wide `(min(a, b), max(a, b))` invariant.
#[pyfunction]
pub fn canonicalize_pairs(pairs: Vec<(i64, i64, f64)>) -> Vec<(i64, i64, f64)> {
    pairs
        .into_iter()
        .map(|(a, b, s)| if a <= b { (a, b, s) } else { (b, a, s) })
        .collect()
}

/// Canonicalize, then keep the maximum score per canonical pair. Output is
/// sorted ascending by `(a, b)` (a `BTreeMap` keeps keys ordered), matching the
/// Python reference's `sorted(best)`. The max uses a strict `>` so the FIRST
/// occurrence wins on ties — identical to the Python `if s > best[key]` guard.
#[pyfunction]
pub fn dedup_pairs_max_score(pairs: Vec<(i64, i64, f64)>) -> Vec<(i64, i64, f64)> {
    goldenmatch_graph_core::dedup_pairs_max_score(&pairs)
}

/// Total candidate comparisons across blocks: `sum(n*(n-1)/2)`. Accumulates in
/// `i128` so a single huge block (n up to ~2^63) can't overflow the per-block
/// product before the divide; the sum is returned as `i64` (realistic block
/// shapes stay well within range — 200M total comparisons is ~2e16).
#[pyfunction]
pub fn candidate_pair_count(block_sizes: Vec<i64>) -> i64 {
    let mut total: i128 = 0;
    for n in block_sizes {
        if n >= 2 {
            let n = n as i128;
            total += n * (n - 1) / 2;
        }
    }
    total as i64
}

/// Arrow-native roadmap Phase 3 (#625): `dedup_pairs_max_score` over
/// Arrow C Data Interface arrays.
///
/// Reads `id_a`, `id_b` (Int64) and `score` (Float64) directly from the
/// PyArrow array buffers -- no per-tuple pyo3 marshalling. The same
/// `BTreeMap<(i64, i64), f64>` reduction as the dict-shaped kernel,
/// emitted back as three Arrow arrays sorted ascending by `(a, b)`.
///
/// The dict-shaped kernel benched at 1.19x speedup vs the Python loop
/// (capped by per-tuple marshalling); this Arrow path bypasses that
/// floor entirely. At 200M-pair / 5M-row reference shapes the dict
/// kernel paid ~80 bytes Python overhead per pair; this path reads the
/// raw Arrow i64/f64 buffers and pays only the BTreeMap update.
///
/// Pairs with `NULL` in any column are silently dropped (defensive --
/// the upstream pair stream shouldn't contain nulls; we don't want a
/// null to crash the reduce loop).
#[pyfunction]
pub fn dedup_pairs_arrow(
    id_a: PyArrowType<ArrayData>,
    id_b: PyArrowType<ArrayData>,
    score: PyArrowType<ArrayData>,
) -> PyResult<(PyArrowType<ArrayData>, PyArrowType<ArrayData>, PyArrowType<ArrayData>)> {
    let (a, b, s) = goldenmatch_graph_core::dedup_pairs_arrow_data(id_a.0, id_b.0, score.0)
        .map_err(PyValueError::new_err)?;
    Ok((PyArrowType(a), PyArrowType(b), PyArrowType(s)))
}

/// `(count, total_records, max, p50, p95, p99)` over the block-size
/// distribution. Percentiles use the project's nearest-rank definition
/// (`core/cluster.py::percentile`: `idx = clamp(ceil(q*len) - 1)`), so the
/// values are actual observed block sizes. Empty input yields all zeros.
#[pyfunction]
pub fn block_histogram(block_sizes: Vec<i64>) -> (usize, i64, i64, i64, i64, i64) {
    let mut sizes = block_sizes;
    sizes.sort_unstable();
    let count = sizes.len();
    if count == 0 {
        return (0, 0, 0, 0, 0, 0);
    }
    let total: i64 = sizes.iter().sum();
    let max = sizes[count - 1];
    let pct = |q: f64| -> i64 {
        // Mirror Python: int(math.ceil(q * len)) - 1, clamped to [0, len-1].
        let raw = (q * count as f64).ceil() as i64 - 1;
        let idx = raw.clamp(0, count as i64 - 1) as usize;
        sizes[idx]
    };
    (count, total, max, pct(0.5), pct(0.95), pct(0.99))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn canonicalize_pairs_orders_min_max_preserving_score() {
        let got = canonicalize_pairs(vec![(2, 1, 0.5), (1, 3, 0.9)]);
        assert_eq!(got, vec![(1, 2, 0.5), (1, 3, 0.9)]);
    }

    #[test]
    fn candidate_pair_count_sums_n_choose_2() {
        assert_eq!(candidate_pair_count(vec![3]), 3);
        assert_eq!(candidate_pair_count(vec![4]), 6);
        assert_eq!(candidate_pair_count(vec![3, 4]), 9);
        assert_eq!(candidate_pair_count(vec![1, 0]), 0);
    }

    #[test]
    fn candidate_pair_count_large_block_does_not_overflow_i64_via_i128() {
        assert_eq!(candidate_pair_count(vec![1_000_000]), 499_999_500_000);
    }

    #[test]
    fn block_histogram_nearest_rank_percentiles() {
        assert_eq!(block_histogram(vec![4, 1, 3, 2]), (4, 10, 4, 2, 4, 4));
        assert_eq!(block_histogram(vec![]), (0, 0, 0, 0, 0, 0));
    }
}
