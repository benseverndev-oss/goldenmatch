//! Fused Arrow-native golden-record kernel — cluster map + decision columns ->
//! per-(cluster, column) SOURCE-ROW INDICES + confidences, in ONE FFI call.
//!
//! The kernel returns INDICES, never values: for each output column and each
//! cluster it emits `winner_idx` (the global position, in the pre-sorted frame,
//! whose value survives; `-1` = null) and `field_conf` (the field confidence).
//! Python materializes the golden frame with one `.gather(winner_idx)` per column
//! on the original typed data — so the wide `multi_df` never exists and native
//! dtypes / byte-identical values come for free. Byte-parity target:
//! `core/golden.py::build_golden_records_batch` (the exact `merge_field` path).
//!
//! Contract (enforced by the Python caller `run_golden_fused_arrow`): rows are
//! pre-sorted by `(cluster_id, row_id)`, so members of a cluster are a CONTIGUOUS
//! `row_id`-ascending span. Every order-dependent tie-break resolves to "first
//! occurrence," which the ascending order makes match the reference.
//!
//! Design: `docs/superpowers/specs/2026-07-08-fused-golden-record-kernel-design.md`.

use arrow::array::{ArrayData, Int64Array};
use arrow::datatypes::DataType;
use arrow::pyarrow::PyArrowType;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

use crate::score::StrCol;

// Strategy ids — shared with Python `_GOLDEN_STRATEGY_IDS`.
const STRAT_MOST_COMPLETE: u8 = 0;
const STRAT_MAJORITY_VOTE: u8 = 1;
const STRAT_FIRST_NON_NULL: u8 = 4;
const STRAT_LONGEST_VALUE: u8 = 5;
const STRAT_UNANIMOUS_OR_NULL: u8 = 6;

/// The non-null members of one cluster span as `(local_idx, code)`, in span
/// order. `code == -1` (the Python-side factorization null sentinel) is a null.
fn span_non_null(code: &[i64], off: usize, size: usize) -> Vec<(usize, i64)> {
    let mut v = Vec::with_capacity(size);
    for l in 0..size {
        let c = code[off + l];
        if c != -1 {
            v.push((l, c));
        }
    }
    v
}

/// Universal pre-dispatch decisions (`merge_field:76`/`:82`), on RAW-VALUE
/// (code) equality — NOT text. Returns `Some((local_idx, conf))` when it
/// resolves the field on its own: `(-1, 0.0)` for an all-null column, and
/// `(first_non_null_idx, 1.0)` when every non-null member shares one code
/// (matches the reference `set(v)` short-circuit and, on mixed-type columns,
/// stays byte-identical where a `str(v)` short-circuit would diverge). `None`
/// means "dispatch to the per-strategy branch."
fn universal_short_circuit(non_null: &[(usize, i64)]) -> Option<(i64, f64)> {
    if non_null.is_empty() {
        return Some((-1, 0.0));
    }
    let first = non_null[0].1;
    if non_null.iter().all(|&(_, c)| c == first) {
        return Some((non_null[0].0 as i64, 1.0));
    }
    None
}

/// Char length of the member at `off + local` (`str(v)` was materialized on the
/// Python side, so this is Python `len(str(v))` = Unicode code points). A null
/// text cell should never reach here (callers pass only non-null members), but
/// map it to length 0 defensively.
fn char_len(text: &StrCol, off: usize, local: usize) -> usize {
    text.get(off + local)
        .map(|s| s.chars().count())
        .unwrap_or(0)
}

/// `_most_complete` (`golden.py:125`), sans the short-circuit (handled
/// universally). Longest `str(v)`; unique-longest -> conf 1.0, else first-in-
/// order -> conf 0.7. (Quality-weight tie-break is Stage 3.)
fn most_complete(text: &StrCol, non_null: &[(usize, i64)], off: usize) -> (i64, f64) {
    let max_len = non_null
        .iter()
        .map(|&(l, _)| char_len(text, off, l))
        .max()
        .unwrap();
    let longest: Vec<usize> = non_null
        .iter()
        .filter(|&&(l, _)| char_len(text, off, l) == max_len)
        .map(|&(l, _)| l)
        .collect();
    if longest.len() == 1 {
        (longest[0] as i64, 1.0)
    } else {
        (longest[0] as i64, 0.7)
    }
}

/// `_longest_value` (`golden.py:209`), unweighted branch. Same length pick as
/// `most_complete` but a length tie yields conf 0.5 (not 0.7).
fn longest_value(text: &StrCol, non_null: &[(usize, i64)], off: usize) -> (i64, f64) {
    let max_len = non_null
        .iter()
        .map(|&(l, _)| char_len(text, off, l))
        .max()
        .unwrap();
    let longest: Vec<usize> = non_null
        .iter()
        .filter(|&&(l, _)| char_len(text, off, l) == max_len)
        .map(|&(l, _)| l)
        .collect();
    if longest.len() == 1 {
        (longest[0] as i64, 1.0)
    } else {
        (longest[0] as i64, 0.5)
    }
}

/// `_majority_vote` (`golden.py:153`), unweighted branch. Highest code count
/// wins; a count tie resolves to the code encountered FIRST in span order (the
/// `Counter.most_common` stable-order tie-break). `conf = count / n_non_null`;
/// the winner index is the winning code's first occurrence.
fn majority_vote(non_null: &[(usize, i64)]) -> (i64, f64) {
    // (code, first_local_idx, count) in first-appearance order.
    let mut order: Vec<(i64, usize, usize)> = Vec::new();
    for &(l, c) in non_null {
        if let Some(e) = order.iter_mut().find(|e| e.0 == c) {
            e.2 += 1;
        } else {
            order.push((c, l, 1));
        }
    }
    let mut best = 0usize;
    for i in 1..order.len() {
        if order[i].2 > order[best].2 {
            best = i;
        }
    }
    let conf = order[best].2 as f64 / non_null.len() as f64;
    (order[best].1 as i64, conf)
}

/// `_unanimous_or_null` (`golden.py:237`). Exactly one distinct non-null code
/// -> that value, conf 1.0; any disagreement -> null, conf 0.0. (The unanimous
/// case is already caught by `universal_short_circuit`; kept explicit for a
/// direct call and defensive completeness.)
fn unanimous_or_null(non_null: &[(usize, i64)]) -> (i64, f64) {
    let first = non_null[0].1;
    if non_null.iter().all(|&(_, c)| c == first) {
        (non_null[0].0 as i64, 1.0)
    } else {
        (-1, 0.0)
    }
}

/// `_first_non_null` (`golden.py:198`), unweighted branch: first non-null in
/// span order, conf 0.6.
fn first_non_null(non_null: &[(usize, i64)]) -> (i64, f64) {
    (non_null[0].0 as i64, 0.6)
}

#[pyfunction]
#[pyo3(signature = (
    row_ids, cluster_ids, n_output_cols, strategy_ids, text_cols, code_cols,
))]
pub fn golden_fused(
    py: Python<'_>,
    row_ids: PyArrowType<ArrayData>,
    cluster_ids: PyArrowType<ArrayData>,
    n_output_cols: usize,
    strategy_ids: Vec<u8>,
    text_cols: Vec<PyArrowType<ArrayData>>,
    code_cols: Vec<PyArrowType<ArrayData>>,
) -> PyResult<(Vec<Vec<i64>>, Vec<Vec<f64>>, Vec<i64>)> {
    // `row_ids` is validated (int64, right length) to enforce the caller's
    // (cluster_id, row_id) pre-sort contract, but Stage 0 does NOT read its
    // values: spans are formed from `cluster_ids` alone, and winner indices are
    // GLOBAL positions in the pre-sorted frame. Stage 8 (provenance) reads it to
    // map winner index -> source __row_id__.
    let row_data = row_ids.0;
    if row_data.data_type() != &DataType::Int64 {
        return Err(PyValueError::new_err(format!(
            "golden_fused: row_ids must be int64, got {:?}",
            row_data.data_type()
        )));
    }
    let n_rows = Int64Array::from(row_data).len();

    let cl_data = cluster_ids.0;
    if cl_data.data_type() != &DataType::Int64 {
        return Err(PyValueError::new_err(format!(
            "golden_fused: cluster_ids must be int64, got {:?}",
            cl_data.data_type()
        )));
    }
    let cluster_ids = Int64Array::from(cl_data);
    if cluster_ids.len() != n_rows {
        return Err(PyValueError::new_err(format!(
            "golden_fused: cluster_ids length {} != row count {n_rows}",
            cluster_ids.len()
        )));
    }
    if strategy_ids.len() != n_output_cols {
        return Err(PyValueError::new_err(format!(
            "golden_fused: strategy_ids length {} != n_output_cols {n_output_cols}",
            strategy_ids.len()
        )));
    }
    if text_cols.len() != n_output_cols {
        return Err(PyValueError::new_err(format!(
            "golden_fused: text_cols length {} != n_output_cols {n_output_cols}",
            text_cols.len()
        )));
    }
    let text: Vec<StrCol> = text_cols
        .into_iter()
        .map(|p| StrCol::from_data(p.0))
        .collect::<PyResult<_>>()?;
    for (c, col) in text.iter().enumerate() {
        if col.len() != n_rows {
            return Err(PyValueError::new_err(format!(
                "golden_fused: text col {c} length {} != row count {n_rows}",
                col.len()
            )));
        }
    }
    // code_cols carries the per-column value-factorization (`_factorize_codes`):
    // one int64 code per row, `-1` = null. It is REQUIRED (one per output col) --
    // the universal short-circuit + majority/unanimous run on raw-value equality
    // (codes), never text. Copy each column's values into an owned `Vec<i64>` to
    // move into the detached closure (as with `cluster_vals`).
    if code_cols.len() != n_output_cols {
        return Err(PyValueError::new_err(format!(
            "golden_fused: code_cols length {} != n_output_cols {n_output_cols}",
            code_cols.len()
        )));
    }
    let mut code_vals: Vec<Vec<i64>> = Vec::with_capacity(n_output_cols);
    for (c, p) in code_cols.into_iter().enumerate() {
        let d = p.0;
        if d.data_type() != &DataType::Int64 {
            return Err(PyValueError::new_err(format!(
                "golden_fused: code col {c} must be int64, got {:?}",
                d.data_type()
            )));
        }
        let arr = Int64Array::from(d);
        if arr.len() != n_rows {
            return Err(PyValueError::new_err(format!(
                "golden_fused: code col {c} length {} != row count {n_rows}",
                arr.len()
            )));
        }
        code_vals.push(arr.values().to_vec());
    }

    let cluster_vals: Vec<i64> = cluster_ids.values().to_vec();

    Ok(py.detach(|| {
        // Group pre-sorted rows into contiguous per-cluster spans.
        let mut spans: Vec<(usize, usize, i64)> = Vec::new(); // (offset, size, cluster_id)
        let mut i = 0usize;
        while i < n_rows {
            let cid = cluster_vals[i];
            let start = i;
            while i < n_rows && cluster_vals[i] == cid {
                i += 1;
            }
            spans.push((start, i - start, cid));
        }

        let n_clusters = spans.len();
        let mut winner_idx: Vec<Vec<i64>> = (0..n_output_cols)
            .map(|_| Vec::with_capacity(n_clusters))
            .collect();
        let mut field_conf: Vec<Vec<f64>> = (0..n_output_cols)
            .map(|_| Vec::with_capacity(n_clusters))
            .collect();
        let cluster_out: Vec<i64> = spans.iter().map(|&(_, _, c)| c).collect();

        for &(off, size, _) in &spans {
            for col in 0..n_output_cols {
                let non_null = span_non_null(&code_vals[col], off, size);
                // Universal decisions first (all-null / all-agree), on codes.
                let (li, conf) = if let Some(sc) = universal_short_circuit(&non_null) {
                    sc
                } else {
                    match strategy_ids[col] {
                        STRAT_MOST_COMPLETE => most_complete(&text[col], &non_null, off),
                        STRAT_MAJORITY_VOTE => majority_vote(&non_null),
                        STRAT_FIRST_NON_NULL => first_non_null(&non_null),
                        STRAT_LONGEST_VALUE => longest_value(&text[col], &non_null, off),
                        STRAT_UNANIMOUS_OR_NULL => unanimous_or_null(&non_null),
                        // Strategies not yet ported (source_priority/most_recent/
                        // confidence_majority) decline in Python before reaching
                        // here; return the null sentinel defensively.
                        _ => (-1i64, 0.0),
                    }
                };
                let global = if li < 0 { -1 } else { off as i64 + li };
                winner_idx[col].push(global);
                field_conf[col].push(conf);
            }
        }
        (winner_idx, field_conf, cluster_out)
    }))
}

#[cfg(test)]
mod tests {
    use super::*;
    use arrow::array::{Array, StringArray};

    fn strcol(vals: &[Option<&str>]) -> StrCol {
        StrCol::from_data(StringArray::from(vals.to_vec()).into_data()).unwrap()
    }

    /// (local_idx, code) for the non-null members of `[off, off+size)`.
    fn nn(code: &[i64], off: usize, size: usize) -> Vec<(usize, i64)> {
        span_non_null(code, off, size)
    }

    // ── universal short-circuit ──────────────────────────────────────────────

    #[test]
    fn short_circuit_all_null_is_sentinel() {
        assert_eq!(
            universal_short_circuit(&nn(&[-1, -1], 0, 2)),
            Some((-1, 0.0))
        );
    }

    #[test]
    fn short_circuit_all_agree_is_first_conf_1() {
        // codes [7,7] (raw-equal) -> first non-null local idx 0, conf 1.0.
        assert_eq!(universal_short_circuit(&nn(&[7, 7], 0, 2)), Some((0, 1.0)));
    }

    #[test]
    fn short_circuit_disagree_declines_to_dispatch() {
        assert_eq!(universal_short_circuit(&nn(&[0, 1], 0, 2)), None);
    }

    // ── most_complete ────────────────────────────────────────────────────────

    #[test]
    fn most_complete_unique_longest_conf_1() {
        // ["Bob","Robert","Bob"] -> "Robert" unique longest -> local idx 1, conf 1.0
        let col = strcol(&[Some("Bob"), Some("Robert"), Some("Bob")]);
        assert_eq!(most_complete(&col, &nn(&[0, 1, 0], 0, 3), 0), (1, 1.0));
    }

    #[test]
    fn most_complete_length_tie_first_in_order_conf_07() {
        // "aa","bb" tie at length 2 -> first, conf 0.7
        let col = strcol(&[Some("aa"), Some("bb")]);
        assert_eq!(most_complete(&col, &nn(&[0, 1], 0, 2), 0), (0, 0.7));
    }

    #[test]
    fn most_complete_respects_span_offset() {
        // span [2,4): "z","zzz" -> local idx 1, conf 1.0
        let col = strcol(&[Some("a"), Some("a"), Some("z"), Some("zzz")]);
        assert_eq!(most_complete(&col, &nn(&[0, 0, 1, 2], 2, 2), 2), (1, 1.0));
    }

    #[test]
    fn most_complete_skips_null_members() {
        // "a", null, "bbb" -> "bbb" unique longest at local idx 2, conf 1.0.
        let col = strcol(&[Some("a"), None, Some("bbb")]);
        assert_eq!(most_complete(&col, &nn(&[0, -1, 1], 0, 3), 0), (2, 1.0));
    }

    // ── longest_value (tie conf 0.5, else 1.0) ───────────────────────────────

    #[test]
    fn longest_value_unique_longest_conf_1() {
        let col = strcol(&[Some("z"), Some("zzz")]);
        assert_eq!(longest_value(&col, &nn(&[0, 1], 0, 2), 0), (1, 1.0));
    }

    #[test]
    fn longest_value_length_tie_first_conf_05() {
        let col = strcol(&[Some("aa"), Some("bb")]);
        assert_eq!(longest_value(&col, &nn(&[0, 1], 0, 2), 0), (0, 0.5));
    }

    // ── majority_vote ────────────────────────────────────────────────────────

    #[test]
    fn majority_vote_clear_winner() {
        // codes [x,x,y] -> x wins 2/3 at first-occurrence idx 0.
        assert_eq!(majority_vote(&nn(&[5, 5, 9], 0, 3)), (0, 2.0 / 3.0));
    }

    #[test]
    fn majority_vote_count_tie_first_appearance() {
        // codes [a,b,a,b] tie 2/2 -> first-appearance code `a` at idx 0, conf 0.5.
        assert_eq!(majority_vote(&nn(&[3, 8, 3, 8], 0, 4)), (0, 0.5));
    }

    // ── unanimous_or_null ────────────────────────────────────────────────────

    #[test]
    fn unanimous_or_null_disagree_is_sentinel() {
        assert_eq!(unanimous_or_null(&nn(&[0, 1], 0, 2)), (-1, 0.0));
    }

    #[test]
    fn unanimous_or_null_agree_conf_1() {
        assert_eq!(unanimous_or_null(&nn(&[4, 4], 0, 2)), (0, 1.0));
    }

    // ── first_non_null ───────────────────────────────────────────────────────

    #[test]
    fn first_non_null_leading_null_picks_first_present() {
        // null, "b", "c" -> first present at local idx 1, conf 0.6.
        assert_eq!(first_non_null(&nn(&[-1, 1, 2], 0, 3)), (1, 0.6));
    }
}
