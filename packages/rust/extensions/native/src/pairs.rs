//! Pair + candidate-estimation kernels — behavior-exact replacements for the
//! pure-Python primitives in `goldenmatch/core/pairs.py`.
//!
//! These are the "Native Core" primitives from the native-runtime roadmap:
//! canonicalization, max-score dedup, and candidate estimation. Every function
//! is bit-exact with its Python reference (integer arithmetic + a strict-`>`
//! max reduction — no float tolerance), so the `pairs` component is gated on by
//! default once the parity test passes.
use std::collections::BTreeMap;

use arrow::array::{Array, ArrayData, Float64Array, Int64Array};
use arrow::datatypes::DataType;
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
    let mut best: BTreeMap<(i64, i64), f64> = BTreeMap::new();
    for (a, b, s) in pairs {
        let key = if a <= b { (a, b) } else { (b, a) };
        match best.get(&key) {
            Some(&cur) if s <= cur => {}
            _ => {
                best.insert(key, s);
            }
        }
    }
    best.into_iter().map(|((a, b), s)| (a, b, s)).collect()
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
    let id_a_data = id_a.0;
    let id_b_data = id_b.0;
    let score_data = score.0;

    if id_a_data.data_type() != &DataType::Int64 {
        return Err(PyValueError::new_err(format!(
            "dedup_pairs_arrow: id_a must be int64, got {:?}",
            id_a_data.data_type()
        )));
    }
    if id_b_data.data_type() != &DataType::Int64 {
        return Err(PyValueError::new_err(format!(
            "dedup_pairs_arrow: id_b must be int64, got {:?}",
            id_b_data.data_type()
        )));
    }
    if score_data.data_type() != &DataType::Float64 {
        return Err(PyValueError::new_err(format!(
            "dedup_pairs_arrow: score must be float64, got {:?}",
            score_data.data_type()
        )));
    }

    let id_a = Int64Array::from(id_a_data);
    let id_b = Int64Array::from(id_b_data);
    let score = Float64Array::from(score_data);

    let n = id_a.len();
    if id_b.len() != n || score.len() != n {
        return Err(PyValueError::new_err(format!(
            "dedup_pairs_arrow: array lengths differ -- id_a={}, id_b={}, score={}",
            n, id_b.len(), score.len(),
        )));
    }

    // Reduce: same algorithm as dedup_pairs_max_score. Strict `>`
    // first-occurrence-wins on ties keeps it bit-identical with the
    // dict-shaped kernel at the output value layer.
    let mut best: BTreeMap<(i64, i64), f64> = BTreeMap::new();
    for i in 0..n {
        if id_a.is_null(i) || id_b.is_null(i) || score.is_null(i) {
            continue;
        }
        let a = id_a.value(i);
        let b = id_b.value(i);
        let s = score.value(i);
        let key = if a <= b { (a, b) } else { (b, a) };
        match best.get(&key) {
            Some(&cur) if s <= cur => {}
            _ => {
                best.insert(key, s);
            }
        }
    }

    // Emit sorted output as three Arrow arrays. BTreeMap iter yields
    // ascending key order, so the result is already sorted by (a, b).
    let n_out = best.len();
    let mut out_a: Vec<i64> = Vec::with_capacity(n_out);
    let mut out_b: Vec<i64> = Vec::with_capacity(n_out);
    let mut out_s: Vec<f64> = Vec::with_capacity(n_out);
    for ((a, b), s) in best {
        out_a.push(a);
        out_b.push(b);
        out_s.push(s);
    }

    let a_array = Int64Array::from(out_a);
    let b_array = Int64Array::from(out_b);
    let s_array = Float64Array::from(out_s);

    Ok((
        PyArrowType(a_array.to_data()),
        PyArrowType(b_array.to_data()),
        PyArrowType(s_array.to_data()),
    ))
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
