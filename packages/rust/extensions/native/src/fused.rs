//! Fused Arrow-native match stage — block + score + dedup + cluster in ONE Rust
//! call, zero intermediate Polars, zero per-stage Arrow re-conversion.
//!
//! This is increment 2 of the fused-match design
//! (`docs/design/2026-07-08-fused-arrow-native-match-kernel.md`). Every stage it
//! runs is an existing source-of-truth kernel:
//!   - block formation: [`crate::block::group_block_positions`] (increment 1)
//!   - scoring: `goldenmatch_score_core::score_one` (the exact per-pair weighted
//!     average `score_block_pairs_arrow` uses — byte-identical scoring)
//!   - dedup: `goldenmatch_graph_core::dedup_pairs_max_score`
//!   - clustering: `goldenmatch_graph_core::connected_components`
//!
//! The bet (measured, not assumed): fusing them into one call removes the
//! Polars group/materialize + the three Arrow round-trips between stages that
//! capped every prior "Arrow everywhere" spike. `match_fused` returns the
//! connected components (clusters); the caller compares them to the per-stage
//! pipeline for byte-parity and times both.

use arrow::array::{ArrayData, Int64Array};
use arrow::datatypes::DataType;
use arrow::pyarrow::PyArrowType;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use rayon::prelude::*;

use goldenmatch_graph_core::{connected_components, dedup_pairs_max_score};
use goldenmatch_score_core::score_one;

use crate::block::{group_block_positions, read_key_cols};
use crate::score::StrCol;

/// Fused match: block (on `key_fields`) -> score (weighted `score_fields`) ->
/// dedup -> connected-components cluster, one call. Byte-parity contract: the
/// candidate pairs equal `build_blocks` + `score_block_pairs_arrow`, so the
/// clusters equal the per-stage pipeline on a covered config.
///
/// Scoring is byte-identical to `score_block_pairs_arrow::score_span`: per pair,
/// `score_sum += score_one(scorer_ids[f], a, b) * weights[f]` over fields where
/// BOTH values are present, emit `(min, max, score_sum / total_weight)` when
/// `weight_sum > 0 && combined >= threshold`.
#[allow(clippy::too_many_arguments)]
#[pyfunction]
#[pyo3(signature = (
    row_ids, key_fields, score_fields, scorer_ids, weights, total_weight, threshold,
))]
pub fn match_fused(
    py: Python<'_>,
    row_ids: PyArrowType<ArrayData>,
    key_fields: Vec<PyArrowType<ArrayData>>,
    score_fields: Vec<PyArrowType<ArrayData>>,
    scorer_ids: Vec<u8>,
    weights: Vec<f64>,
    total_weight: f64,
    threshold: f64,
) -> PyResult<Vec<Vec<i64>>> {
    let row_data = row_ids.0;
    if row_data.data_type() != &DataType::Int64 {
        return Err(PyValueError::new_err(format!(
            "match_fused: row_ids must be int64, got {:?}",
            row_data.data_type()
        )));
    }
    let row_ids = Int64Array::from(row_data);
    let n_rows = row_ids.len();

    let (key_cols, key_n) = read_key_cols(key_fields, "match_fused key")?;
    if key_n != n_rows {
        return Err(PyValueError::new_err(format!(
            "match_fused: key field length {key_n} != row count {n_rows}"
        )));
    }

    let n_fields = score_fields.len();
    if scorer_ids.len() != n_fields || weights.len() != n_fields {
        return Err(PyValueError::new_err(format!(
            "match_fused: scorer_ids ({}) / weights ({}) must match score_fields ({n_fields})",
            scorer_ids.len(),
            weights.len()
        )));
    }
    let score_cols: Vec<StrCol> = score_fields
        .into_iter()
        .map(|p| StrCol::from_data(p.0))
        .collect::<PyResult<_>>()?;
    for (f, c) in score_cols.iter().enumerate() {
        if c.len() != n_rows {
            return Err(PyValueError::new_err(format!(
                "match_fused: score field {f} length {} != row count {n_rows}",
                c.len()
            )));
        }
    }

    let all_ids: Vec<i64> = row_ids.values().to_vec();

    // Everything below is pure-Rust and touches no Python object -> release the
    // GIL for the whole fused pipeline (mirrors score_block_pairs_arrow).
    let clusters = py.detach(|| {
        // 1. Block formation (positions per block, singletons already dropped).
        let groups = group_block_positions(&key_cols, n_rows);

        // 2. Gather row_ids + each score field into block-CONTIGUOUS order, once
        //    (O(n)). The pipeline pays this via a Polars sort so scoring reads
        //    contiguous memory; the fused kernel does the same gather in Rust so
        //    the O(k^2) inner scoring loop has the same cache locality (scattered
        //    per-position `.get()` in the hot loop cost ~1.4x at coarse/big-block
        //    shapes — the gather removes it).
        let n_blk: usize = groups.iter().map(|g| g.len()).sum();
        let mut rid_sorted: Vec<i64> = Vec::with_capacity(n_blk);
        let mut vals: Vec<Vec<Option<&str>>> =
            (0..n_fields).map(|_| Vec::with_capacity(n_blk)).collect();
        for block in &groups {
            for &p in block {
                let p = p as usize;
                rid_sorted.push(all_ids[p]);
                for f in 0..n_fields {
                    vals[f].push(score_cols[f].get(p));
                }
            }
        }

        // 3. Score intra-block pairs over the contiguous block-sorted buffers.
        //    Byte-identical to score_block_pairs_arrow::score_span, and the SAME
        //    guarded-rayon posture (#688): sequential in the calling thread below
        //    GOLDENMATCH_NATIVE_RAYON_MIN_PAIRS candidate pairs, fan out to rayon
        //    above it. Scoring is the dominant term (~57% of the stage), so this
        //    is the hot path — both paths must be parallel to compare fairly.
        let mut spans: Vec<(usize, usize)> = Vec::with_capacity(groups.len());
        let mut off = 0usize;
        for block in &groups {
            spans.push((off, block.len()));
            off += block.len();
        }
        let score_span = |offset: usize, size: usize| -> Vec<(i64, i64, f64)> {
            let mut local: Vec<(i64, i64, f64)> = Vec::new();
            let end = offset + size;
            for i in offset..end - 1 {
                let ri = rid_sorted[i];
                for j in (i + 1)..end {
                    let rj = rid_sorted[j];
                    let (a_id, b_id) = if ri < rj { (ri, rj) } else { (rj, ri) };
                    let mut score_sum = 0.0_f64;
                    let mut weight_sum = 0.0_f64;
                    for f in 0..n_fields {
                        if let (Some(a), Some(b)) = (vals[f][i], vals[f][j]) {
                            score_sum += score_one(scorer_ids[f], a, b) * weights[f];
                            weight_sum += weights[f];
                        }
                    }
                    if weight_sum > 0.0 {
                        let combined = score_sum / total_weight;
                        if combined >= threshold {
                            local.push((a_id, b_id, combined));
                        }
                    }
                }
            }
            local
        };
        let total_pairs: u128 = spans
            .iter()
            .map(|&(_, s)| {
                let s = s as u128;
                s * (s - 1) / 2
            })
            .sum();
        let rayon_min_pairs: u128 = std::env::var("GOLDENMATCH_NATIVE_RAYON_MIN_PAIRS")
            .ok()
            .and_then(|v| v.parse().ok())
            .unwrap_or(20_000_000);
        let pairs: Vec<(i64, i64, f64)> = if total_pairs >= rayon_min_pairs {
            spans
                .par_iter()
                .flat_map_iter(|&(o, s)| score_span(o, s))
                .collect()
        } else {
            spans.iter().flat_map(|&(o, s)| score_span(o, s)).collect()
        };

        // 4. Dedup (canonical (min,max), max score) + 5. connected components.
        let deduped = dedup_pairs_max_score(&pairs);
        connected_components(&deduped, &all_ids)
    });

    Ok(clusters)
}
