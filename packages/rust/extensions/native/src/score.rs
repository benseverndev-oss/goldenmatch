//! String scorers backed by the `rapidfuzz` Rust crate — the same algorithms
//! the Python `rapidfuzz` bindings use, for the Phase 2 native block-scorer.
//!
//! Replaces the earlier hand-rolled scorers, which were ~2x slower than
//! rapidfuzz on representative shapes (they allocated a `Vec<char>` per
//! comparison and used naive O(n*m) inner loops). rapidfuzz-rs uses the same
//! bit-parallel algorithms as rapidfuzz-cpp, so per-comparison cost matches the
//! Python path while the kernel removes the per-pair Python interpreter
//! overhead. Parity is asserted (within float tolerance) in
//! tests/test_native_parity.py. All functions operate on Unicode chars
//! (codepoints), matching rapidfuzz.
use pyo3::prelude::*;
use rapidfuzz::distance::{jaro_winkler, levenshtein};
use rapidfuzz::fuzz;
use rayon::prelude::*;
use std::collections::HashSet;

/// `rapidfuzz.fuzz.token_sort_ratio` preprocessing: split on whitespace, sort
/// the tokens, rejoin with a single space. (Then `fuzz::ratio` on the result.)
fn token_sort_string(s: &str) -> String {
    let mut toks: Vec<&str> = s.split_whitespace().collect();
    toks.sort_unstable();
    toks.join(" ")
}

// ---- PyO3 surface (scale matches score_buckets._resolve_score_pair_callable:
//      jaro_winkler/levenshtein on 0-1, token_sort_ratio on 0-100) ----

#[pyfunction]
pub fn jaro_winkler_similarity(a: &str, b: &str) -> f64 {
    // rapidfuzz JaroWinkler default prefix_weight = 0.1.
    jaro_winkler::normalized_similarity(a.chars(), b.chars())
}

#[pyfunction]
pub fn levenshtein_similarity(a: &str, b: &str) -> f64 {
    // rapidfuzz Levenshtein default uniform weights (1, 1, 1).
    levenshtein::normalized_similarity(a.chars(), b.chars())
}

/// token_sort_ratio on the 0-100 scale (score_field divides by 100).
#[pyfunction]
pub fn token_sort_ratio(a: &str, b: &str) -> f64 {
    let sa = token_sort_string(a);
    let sb = token_sort_string(b);
    // rapidfuzz-rs fuzz::ratio returns [0, 1]; Python fuzz.ratio is [0, 100].
    fuzz::ratio(sa.chars(), sb.chars()) * 100.0
}

/// Scorer dispatch matching `score_buckets._resolve_score_pair_callable`'s
/// fast-path scale, all on [0, 1]. ids: 0=jaro_winkler, 1=levenshtein,
/// 2=token_sort, 3=exact.
fn score_one(scorer_id: u8, a: &str, b: &str) -> f64 {
    match scorer_id {
        0 => jaro_winkler::normalized_similarity(a.chars(), b.chars()),
        1 => levenshtein::normalized_similarity(a.chars(), b.chars()),
        2 => {
            let sa = token_sort_string(a);
            let sb = token_sort_string(b);
            fuzz::ratio(sa.chars(), sb.chars())
        }
        3 => {
            if a == b {
                1.0
            } else {
                0.0
            }
        }
        _ => 0.0,
    }
}

/// Native port of `score_buckets._score_one_bucket_fast`'s per-pair loop.
///
/// `field_values[f][r]` is the (already transform-applied) value of field `f`
/// for row `r`, in the bucket's block-sorted order. `block_sizes` are the
/// consecutive block lengths in that same order. Emits canonical (min,max)
/// pairs whose weighted score (sum(score*weight) / total_weight, with None
/// values skipped) meets `threshold`, excluding `exclude`.
///
/// Blocks are scored in parallel with `rayon` under `allow_threads` (the GIL is
/// released for the whole compute — no Python objects are touched inside), so
/// the kernel uses every core instead of relying on the caller's per-bucket
/// thread pool. `par_iter().flat_map(...).collect()` preserves block order, so
/// output ordering matches the sequential Python loop.
///
/// The scorers are rapidfuzz-rs (same algorithms as Python rapidfuzz), so
/// scores match the Python path to within float tolerance, not bit-for-bit; the
/// emitted pair set could differ only for a pair whose weighted score sits
/// within that tolerance of `threshold`.
#[allow(clippy::too_many_arguments)]
#[pyfunction]
pub fn score_block_pairs(
    py: Python<'_>,
    row_ids: Vec<i64>,
    block_sizes: Vec<usize>,
    field_values: Vec<Vec<Option<String>>>,
    scorer_ids: Vec<u8>,
    weights: Vec<f64>,
    total_weight: f64,
    threshold: f64,
    exclude: Vec<(i64, i64)>,
) -> Vec<(i64, i64, f64)> {
    let exclude: HashSet<(i64, i64)> = exclude.into_iter().collect();
    let n_fields = scorer_ids.len();

    // Precompute each block's (offset, size) span so blocks are independent
    // units of parallel work.
    let mut spans: Vec<(usize, usize)> = Vec::with_capacity(block_sizes.len());
    let mut offset = 0usize;
    for &size in &block_sizes {
        spans.push((offset, size));
        offset += size;
    }

    py.allow_threads(|| {
        spans
            .par_iter()
            .flat_map_iter(|&(offset, size)| {
                let mut local: Vec<(i64, i64, f64)> = Vec::new();
                if size >= 2 {
                    let end = offset + size;
                    for i in offset..end - 1 {
                        let ri = row_ids[i];
                        for j in (i + 1)..end {
                            let rj = row_ids[j];
                            let pair_key = if ri < rj { (ri, rj) } else { (rj, ri) };
                            if exclude.contains(&pair_key) {
                                continue;
                            }
                            let mut score_sum = 0.0_f64;
                            let mut weight_sum = 0.0_f64;
                            for f in 0..n_fields {
                                if let (Some(a), Some(b)) =
                                    (&field_values[f][i], &field_values[f][j])
                                {
                                    score_sum += score_one(scorer_ids[f], a, b) * weights[f];
                                    weight_sum += weights[f];
                                }
                            }
                            if weight_sum > 0.0 {
                                let combined = score_sum / total_weight;
                                if combined >= threshold {
                                    local.push((pair_key.0, pair_key.1, combined));
                                }
                            }
                        }
                    }
                }
                local
            })
            .collect()
    })
}
