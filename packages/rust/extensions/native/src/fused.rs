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
/// Arrow buffers), the per-NE-field block-sorted values (same permutation — NE
/// columns MUST ride the same gather or the per-pair NE check would read the
/// UNSORTED row index), and the per-block `(offset, size)` spans. The contiguous
/// layout gives the O(k^2) inner scoring loop the same cache locality the
/// pipeline gets from its Polars sort.
#[allow(clippy::type_complexity)]
fn fused_gather<'a>(
    key_cols: &[StrCol],
    score_cols: &'a [StrCol],
    ne_cols: &'a [StrCol],
    n_rows: usize,
    all_ids: &[i64],
) -> (
    Vec<i64>,
    Vec<Vec<Option<&'a str>>>,
    Vec<Vec<Option<&'a str>>>,
    Vec<(usize, usize)>,
) {
    let groups = group_block_positions(key_cols, n_rows);
    let n_blk: usize = groups.iter().map(|g| g.len()).sum();
    let n_fields = score_cols.len();
    let n_ne = ne_cols.len();
    let mut rid_sorted: Vec<i64> = Vec::with_capacity(n_blk);
    let mut vals: Vec<Vec<Option<&str>>> =
        (0..n_fields).map(|_| Vec::with_capacity(n_blk)).collect();
    let mut ne_vals: Vec<Vec<Option<&str>>> =
        (0..n_ne).map(|_| Vec::with_capacity(n_blk)).collect();
    let mut spans: Vec<(usize, usize)> = Vec::with_capacity(groups.len());
    let mut off = 0usize;
    for block in &groups {
        for &p in block {
            let p = p as usize;
            rid_sorted.push(all_ids[p]);
            for (f, col) in score_cols.iter().enumerate() {
                vals[f].push(col.get(p));
            }
            for (k, col) in ne_cols.iter().enumerate() {
                ne_vals[k].push(col.get(p));
            }
        }
        spans.push((off, block.len()));
        off += block.len();
    }
    (rid_sorted, vals, ne_vals, spans)
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
        let (rid_sorted, vals, _, spans) =
            fused_gather(&key_cols, &score_cols, &[], n_rows, &all_ids);
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
///
/// `level_thresholds` (optional, one entry per field) carries a field's custom
/// similarity->level banding (PR #1749): when `Some(ts)` for field `f`, its
/// level is the count of thresholds `t` with `sim >= t` (inclusive), and
/// `match_weights[f]` must have `ts.len() + 1` entries. `None` (whole kwarg or
/// per field) keeps the legacy 2/3/N-even banding. Old wheels never see this
/// kwarg (Python gates on the `FUSED_FS_SUPPORTS_LEVEL_THRESHOLDS` flag).
///
/// The `ne_*` kwargs (optional, all-or-none) carry Fellegi-Sunter negative
/// evidence: `ne_fields[k]` is NE field `k`'s POST-transform Utf8/LargeUtf8
/// column (row-aligned with the score columns; it rides the same block gather
/// so the per-pair check reads the block-sorted permutation),
/// `ne_scorer_ids`/`ne_thresholds`/`ne_weights` its scorer, firing threshold,
/// and resolved fired-weight (normally negative). Firing follows `_ne_fired`
/// (core/probabilistic.py:466) byte-for-byte: fires iff BOTH values are
/// present AND non-empty (empty string = inconclusive — the deliberate NE
/// null-handling that differs from regular fields' null -> level-0) AND
/// similarity is STRICTLY below the threshold; a fired field adds
/// `ne_weights[k]` to the pair's summed weight, otherwise it contributes
/// exactly 0. `fs_normalize` is unchanged — the caller passes NE-aware
/// `min_weight`/`weight_range`. Old wheels never see these kwargs (Python
/// gates on the `FS_SUPPORTS_NE` capability flag).
#[allow(clippy::too_many_arguments)]
#[pyfunction]
#[pyo3(signature = (
    row_ids, key_fields, score_fields, scorer_ids, levels, partial_thresholds,
    match_weights, calibrated, prior_w, min_weight, weight_range, threshold,
    level_thresholds=None,
    ne_fields=None, ne_scorer_ids=None, ne_thresholds=None, ne_weights=None,
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
    level_thresholds: Option<Vec<Option<Vec<f64>>>>,
    ne_fields: Option<Vec<PyArrowType<ArrayData>>>,
    ne_scorer_ids: Option<Vec<u8>>,
    ne_thresholds: Option<Vec<f64>>,
    ne_weights: Option<Vec<f64>>,
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

    if let Some(lt) = &level_thresholds {
        if lt.len() != n_fields {
            return Err(PyValueError::new_err(format!(
                "match_fused_fs: level_thresholds length {} != field count {n_fields}",
                lt.len()
            )));
        }
        for (f, ts) in lt.iter().enumerate() {
            if let Some(ts) = ts {
                if match_weights[f].len() != ts.len() + 1 {
                    return Err(PyValueError::new_err(format!(
                        "match_fused_fs: field {f} has {} match_weights but \
                         {} level_thresholds (need thresholds + 1 weights)",
                        match_weights[f].len(),
                        ts.len()
                    )));
                }
            }
        }
    }
    // Per-field threshold slices hoisted out of the per-pair-per-field hot loop
    // (no Option chasing / re-borrowing inside the scoring closure).
    let field_thresholds: Vec<Option<&[f64]>> = match &level_thresholds {
        Some(lt) => lt.iter().map(|ts| ts.as_deref()).collect(),
        None => vec![None; n_fields],
    };

    // Negative-evidence kwargs: all four present or all four absent.
    let n_present = [
        ne_fields.is_some(),
        ne_scorer_ids.is_some(),
        ne_thresholds.is_some(),
        ne_weights.is_some(),
    ]
    .iter()
    .filter(|&&p| p)
    .count();
    if n_present != 0 && n_present != 4 {
        return Err(PyValueError::new_err(
            "match_fused_fs: ne_fields, ne_scorer_ids, ne_thresholds and \
             ne_weights must be passed together (all four or none)",
        ));
    }
    let ne_cols: Vec<StrCol> = match ne_fields {
        Some(fields) => fields
            .into_iter()
            .map(|p| StrCol::from_data(p.0))
            .collect::<PyResult<_>>()?,
        None => Vec::new(),
    };
    if n_present == 4 {
        let n_ne = ne_cols.len();
        let ns = ne_scorer_ids.as_deref().unwrap_or(&[]);
        let nt = ne_thresholds.as_deref().unwrap_or(&[]);
        let nw = ne_weights.as_deref().unwrap_or(&[]);
        if ns.len() != n_ne || nt.len() != n_ne || nw.len() != n_ne {
            return Err(PyValueError::new_err(format!(
                "match_fused_fs: ne_* lengths differ (ne_fields {}, \
                 ne_scorer_ids {}, ne_thresholds {}, ne_weights {})",
                n_ne,
                ns.len(),
                nt.len(),
                nw.len()
            )));
        }
        for (k, col) in ne_cols.iter().enumerate() {
            if col.len() != n_rows {
                return Err(PyValueError::new_err(format!(
                    "match_fused_fs: ne_fields[{k}] length {} != row count {n_rows}",
                    col.len()
                )));
            }
        }
        for (k, &sid) in ns.iter().enumerate() {
            if sid > 3 {
                return Err(PyValueError::new_err(format!(
                    "match_fused_fs: ne_scorer_ids[{k}]={sid} out of range (valid: 0..=3)"
                )));
            }
        }
    }
    let ne_scorer_ids_v: Vec<u8> = ne_scorer_ids.unwrap_or_default();
    let ne_thresholds_v: Vec<f64> = ne_thresholds.unwrap_or_default();
    let ne_weights_v: Vec<f64> = ne_weights.unwrap_or_default();
    let n_ne = ne_cols.len();

    Ok(py.detach(|| {
        let (rid_sorted, vals, ne_vals, spans) =
            fused_gather(&key_cols, &score_cols, &ne_cols, n_rows, &all_ids);
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
                                fs_level_from_sim(
                                    sim,
                                    levels[f],
                                    partial_thresholds[f],
                                    field_thresholds[f],
                                )
                            }
                            _ => 0, // null on either side -> disagree (level 0)
                        };
                        total_w += match_weights[f][level];
                    }
                    // Negative evidence: exact `_ne_fired` semantics
                    // (core/probabilistic.py:466) — fires iff both values
                    // present AND non-empty AND similarity STRICTLY below
                    // the threshold; contributes exactly 0 otherwise. The
                    // ne_vals here are block-gathered alongside the score
                    // columns, so i/j index the same permutation.
                    for k in 0..n_ne {
                        if let (Some(a), Some(b)) = (ne_vals[k][i], ne_vals[k][j]) {
                            if !a.is_empty()
                                && !b.is_empty()
                                && score_one(ne_scorer_ids_v[k], a, b) < ne_thresholds_v[k]
                            {
                                total_w += ne_weights_v[k];
                            }
                        }
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
