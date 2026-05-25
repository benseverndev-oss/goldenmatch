//! Pair + candidate-estimation kernels — behavior-exact replacements for the
//! pure-Python primitives in `goldenmatch/core/pairs.py`.
//!
//! These are the "Native Core" primitives from the native-runtime roadmap:
//! canonicalization, max-score dedup, and candidate estimation. Every function
//! is bit-exact with its Python reference (integer arithmetic + a strict-`>`
//! max reduction — no float tolerance), so the `pairs` component is gated on by
//! default once the parity test passes.
use std::collections::BTreeMap;

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
