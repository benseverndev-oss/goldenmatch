//! Fused Arrow-native match stage — block + score + dedup + cluster in ONE Rust
//! call, zero intermediate Polars, zero per-stage Arrow re-conversion.
//!
//! Design: `docs/design/2026-07-08-fused-arrow-native-match-kernel.md`. Every
//! stage is an existing source-of-truth kernel: block formation
//! ([`crate::block::group_block_positions`]), scoring
//! (`score_core::score_one`, or the Fellegi-Sunter `fs_level_from_sim` +
//! `fs_normalize` shared with `score_block_pairs_fs`), dedup + clustering
//! (`graph_core::{dedup_pairs_max_score, connected_components}`).
//!
//! `match_fused` = weighted-matchkey scoring; `match_fused_fs` = probabilistic
//! (Fellegi-Sunter) scoring. Both share the block-formation + gather + dedup +
//! cluster orchestration; only the per-pair scorer differs. The RSS win: no
//! intermediate Polars frame / Python pairs-list is materialized — everything
//! stays a Rust `Vec` behind one FFI crossing.

use arrow::array::{ArrayData, Int64Array};
use arrow::datatypes::DataType;
use arrow::pyarrow::PyArrowType;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use rayon::prelude::*;

use goldenmatch_graph_core::{connected_components, dedup_pairs_max_score};
use goldenmatch_score_core::score_one;

use crate::block::{group_block_positions, read_key_cols};
use crate::score::{fs_level_from_sim, fs_normalize, StrCol};

/// Read + validate `row_ids` (Int64) and the score `field_arrays` (Utf8), shared
/// by the weighted + FS entries.
fn read_ids_and_fields(
    who: &str,
    row_ids: PyArrowType<ArrayData>,
    score_fields: Vec<PyArrowType<ArrayData>>,
    n_scorers: usize,
) -> PyResult<(Int64Array, usize, Vec<StrCol>)> {
    let row_data = row_ids.0;
    if row_data.data_type() != &DataType::Int64 {
        return Err(PyValueError::new_err(format!(
            "{who}: row_ids must be int64, got {:?}",
            row_data.data_type()
        )));
    }
    let row_ids = Int64Array::from(row_data);
    let n_rows = row_ids.len();
    let n_fields = score_fields.len();
    if n_scorers != n_fields {
        return Err(PyValueError::new_err(format!(
            "{who}: per-field param count ({n_scorers}) must match score_fields ({n_fields})"
        )));
    }
    let score_cols: Vec<StrCol> = score_fields
        .into_iter()
        .map(|p| StrCol::from_data(p.0))
        .collect::<PyResult<_>>()?;
    for (f, c) in score_cols.iter().enumerate() {
        if c.len() != n_rows {
            return Err(PyValueError::new_err(format!(
                "{who}: score field {f} length {} != row count {n_rows}",
                c.len()
            )));
        }
    }
    Ok((row_ids, n_rows, score_cols))
}

/// Block-formation + gather into block-CONTIGUOUS order (once, O(n)). Returns the
/// block-sorted row ids, the per-field block-sorted values (borrowed from the
/// Arrow buffers), and the per-block `(offset, size)` spans. The contiguous
/// layout gives the O(k^2) inner scoring loop the same cache locality the
/// pipeline gets from its Polars sort.
#[allow(clippy::type_complexity)]
fn fused_gather<'a>(
    key_cols: &[StrCol],
    score_cols: &'a [StrCol],
    n_rows: usize,
    all_ids: &[i64],
) -> (Vec<i64>, Vec<Vec<Option<&'a str>>>, Vec<(usize, usize)>) {
    let groups = group_block_positions(key_cols, n_rows);
    let n_blk: usize = groups.iter().map(|g| g.len()).sum();
    let n_fields = score_cols.len();
    let mut rid_sorted: Vec<i64> = Vec::with_capacity(n_blk);
    let mut vals: Vec<Vec<Option<&str>>> =
        (0..n_fields).map(|_| Vec::with_capacity(n_blk)).collect();
    let mut spans: Vec<(usize, usize)> = Vec::with_capacity(groups.len());
    let mut off = 0usize;
    for block in &groups {
        for &p in block {
            let p = p as usize;
            rid_sorted.push(all_ids[p]);
            for (f, col) in score_cols.iter().enumerate() {
                vals[f].push(col.get(p));
            }
        }
        spans.push((off, block.len()));
        off += block.len();
    }
    (rid_sorted, vals, spans)
}

/// Run a per-span scorer over the spans, with the #688 guarded-rayon posture:
/// sequential in the calling thread below `GOLDENMATCH_NATIVE_RAYON_MIN_PAIRS`
/// candidate pairs, fan out to rayon above it. Both paths walk spans in order, so
/// the emitted pair sequence is identical either way.
fn run_spans<F>(spans: &[(usize, usize)], score_span: F) -> Vec<(i64, i64, f64)>
where
    F: Fn(usize, usize) -> Vec<(i64, i64, f64)> + Sync + Send,
{
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
    if total_pairs >= rayon_min_pairs {
        spans
            .par_iter()
            .flat_map_iter(|&(o, s)| score_span(o, s))
            .collect()
    } else {
        spans.iter().flat_map(|&(o, s)| score_span(o, s)).collect()
    }
}

fn cluster_pairs(pairs: Vec<(i64, i64, f64)>, all_ids: &[i64]) -> Vec<Vec<i64>> {
    let deduped = dedup_pairs_max_score(&pairs);
    connected_components(&deduped, all_ids)
}

/// Fused weighted-matchkey match: block -> weighted-average score -> dedup ->
/// connected-components cluster, one call. Scoring is byte-identical to
/// `score_block_pairs_arrow`: per pair, `score_sum += score_one(id,a,b)*w[f]`
/// over fields where BOTH values are present, emit `(min,max, score_sum/total_w)`
/// when `weight_sum > 0 && combined >= threshold`.
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
    if weights.len() != scorer_ids.len() {
        return Err(PyValueError::new_err(
            "match_fused: weights must match scorer_ids",
        ));
    }
    let (row_ids, n_rows, score_cols) =
        read_ids_and_fields("match_fused", row_ids, score_fields, scorer_ids.len())?;
    let (key_cols, key_n) = read_key_cols(key_fields, "match_fused key")?;
    if key_n != n_rows {
        return Err(PyValueError::new_err(format!(
            "match_fused: key field length {key_n} != row count {n_rows}"
        )));
    }
    let all_ids: Vec<i64> = row_ids.values().to_vec();
    let n_fields = score_cols.len();

    Ok(py.detach(|| {
        let (rid_sorted, vals, spans) = fused_gather(&key_cols, &score_cols, n_rows, &all_ids);
        let score_span = |offset: usize, size: usize| -> Vec<(i64, i64, f64)> {
            let mut local = Vec::new();
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
        let pairs = run_spans(&spans, score_span);
        cluster_pairs(pairs, &all_ids)
    }))
}

/// Fused Fellegi-Sunter match: block -> probabilistic score -> dedup -> cluster,
/// one call. Scoring is byte-identical to `score_block_pairs_fs` (shares
/// `fs_level_from_sim` + `fs_normalize`): per pair, per field map the similarity
/// to a comparison `level` (null on either side -> level 0), sum
/// `match_weights[f][level]`, normalize (calibrated posterior or linear), emit
/// `(min,max, normalized)` when `normalized >= threshold`. `levels` /
/// `partial_thresholds` / `match_weights` / `calibrated` / `prior_w` /
/// `min_weight` / `weight_range` come from the host's EM-trained model.
#[allow(clippy::too_many_arguments)]
#[pyfunction]
#[pyo3(signature = (
    row_ids, key_fields, score_fields, scorer_ids, levels, partial_thresholds,
    match_weights, calibrated, prior_w, min_weight, weight_range, threshold,
))]
pub fn match_fused_fs(
    py: Python<'_>,
    row_ids: PyArrowType<ArrayData>,
    key_fields: Vec<PyArrowType<ArrayData>>,
    score_fields: Vec<PyArrowType<ArrayData>>,
    scorer_ids: Vec<u8>,
    levels: Vec<u8>,
    partial_thresholds: Vec<f64>,
    match_weights: Vec<Vec<f64>>,
    calibrated: bool,
    prior_w: f64,
    min_weight: f64,
    weight_range: f64,
    threshold: f64,
) -> PyResult<Vec<Vec<i64>>> {
    let n = scorer_ids.len();
    if levels.len() != n || partial_thresholds.len() != n || match_weights.len() != n {
        return Err(PyValueError::new_err(
            "match_fused_fs: scorer_ids/levels/partial_thresholds/match_weights must be same length",
        ));
    }
    let (row_ids, n_rows, score_cols) =
        read_ids_and_fields("match_fused_fs", row_ids, score_fields, n)?;
    let (key_cols, key_n) = read_key_cols(key_fields, "match_fused_fs key")?;
    if key_n != n_rows {
        return Err(PyValueError::new_err(format!(
            "match_fused_fs: key field length {key_n} != row count {n_rows}"
        )));
    }
    let all_ids: Vec<i64> = row_ids.values().to_vec();
    let n_fields = score_cols.len();

    Ok(py.detach(|| {
        let (rid_sorted, vals, spans) = fused_gather(&key_cols, &score_cols, n_rows, &all_ids);
        let score_span = |offset: usize, size: usize| -> Vec<(i64, i64, f64)> {
            let mut local = Vec::new();
            let end = offset + size;
            for i in offset..end - 1 {
                let ri = rid_sorted[i];
                for j in (i + 1)..end {
                    let rj = rid_sorted[j];
                    let (a_id, b_id) = if ri < rj { (ri, rj) } else { (rj, ri) };
                    let mut total_w = 0.0_f64;
                    for f in 0..n_fields {
                        let level = match (vals[f][i], vals[f][j]) {
                            (Some(a), Some(b)) => {
                                let sim = score_one(scorer_ids[f], a, b);
                                // Fused stays declined for level_thresholds via
                                // match_fused_fs_ready (Python side); port the
                                // kwarg when the fused path goes live.
                                fs_level_from_sim(sim, levels[f], partial_thresholds[f], None)
                            }
                            _ => 0, // null on either side -> disagree (level 0)
                        };
                        total_w += match_weights[f][level];
                    }
                    let normalized =
                        fs_normalize(total_w, calibrated, prior_w, min_weight, weight_range);
                    if normalized >= threshold {
                        local.push((a_id, b_id, normalized));
                    }
                }
            }
            local
        };
        let pairs = run_spans(&spans, score_span);
        cluster_pairs(pairs, &all_ids)
    }))
}
