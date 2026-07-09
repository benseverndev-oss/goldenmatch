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

/// `_most_complete` (`golden.py:125`) + the universal short-circuit
/// (`golden.py:82`), over one cluster span of the `text` column.
///
/// Returns `(local_idx, confidence)` where `local_idx` is the 0-based position
/// WITHIN the span (`-1` when every member is null). The caller maps it to the
/// global frame position. Quality-weight tie-break is Stage 3; here a length tie
/// falls to the first-in-order member at confidence 0.7.
fn most_complete(col: &StrCol, off: usize, size: usize) -> (i64, f64) {
    // (local_idx, str) for the non-null members, in span order.
    let mut non_null: Vec<(usize, &str)> = Vec::with_capacity(size);
    for l in 0..size {
        if let Some(s) = col.get(off + l) {
            non_null.push((l, s));
        }
    }
    if non_null.is_empty() {
        return (-1, 0.0);
    }
    // Universal short-circuit: all non-null identical -> that value, conf 1.0.
    let first_val = non_null[0].1;
    if non_null.iter().all(|&(_, s)| s == first_val) {
        return (non_null[0].0 as i64, 1.0);
    }
    // Longest by Python `len(str(v))` = Unicode code points (NOT bytes).
    let max_len = non_null
        .iter()
        .map(|&(_, s)| s.chars().count())
        .max()
        .unwrap();
    let longest: Vec<(usize, &str)> = non_null
        .iter()
        .copied()
        .filter(|&(_, s)| s.chars().count() == max_len)
        .collect();
    if longest.len() == 1 {
        return (longest[0].0 as i64, 1.0);
    }
    // Length tie -> first in order, conf 0.7 (quality-weight tie-break: Stage 3).
    (longest[0].0 as i64, 0.7)
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
    // code_cols is validated (int64, right length) when present; Stage 1 consumes
    // it. Empty is the Stage-0 (most_complete-only) case.
    if !code_cols.is_empty() {
        if code_cols.len() != n_output_cols {
            return Err(PyValueError::new_err(format!(
                "golden_fused: code_cols length {} != n_output_cols {n_output_cols}",
                code_cols.len()
            )));
        }
        for (c, p) in code_cols.into_iter().enumerate() {
            let d = p.0;
            if d.data_type() != &DataType::Int64 {
                return Err(PyValueError::new_err(format!(
                    "golden_fused: code col {c} must be int64, got {:?}",
                    d.data_type()
                )));
            }
            if Int64Array::from(d).len() != n_rows {
                return Err(PyValueError::new_err(format!(
                    "golden_fused: code col {c} length != row count {n_rows}"
                )));
            }
        }
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
                let (li, conf) = match strategy_ids[col] {
                    STRAT_MOST_COMPLETE => most_complete(&text[col], off, size),
                    // Unimplemented strategies decline in Python before reaching
                    // here; return the null sentinel defensively.
                    _ => (-1i64, 0.0),
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

    #[test]
    fn most_complete_unique_longest_conf_1() {
        // ["Bob","Robert","Bob"] -> Robert unique longest -> local idx 1, conf 1.0
        let col = strcol(&[Some("Bob"), Some("Robert"), Some("Bob")]);
        assert_eq!(most_complete(&col, 0, 3), (1, 1.0));
    }

    #[test]
    fn most_complete_all_identical_short_circuits() {
        let col = strcol(&[Some("x"), Some("x")]);
        assert_eq!(most_complete(&col, 0, 2), (0, 1.0));
    }

    #[test]
    fn most_complete_length_tie_first_in_order_conf_07() {
        // "aa","bb" tie at length 2 -> first, conf 0.7
        let col = strcol(&[Some("aa"), Some("bb")]);
        assert_eq!(most_complete(&col, 0, 2), (0, 0.7));
    }

    #[test]
    fn most_complete_all_null_returns_sentinel() {
        let col = strcol(&[None, None]);
        assert_eq!(most_complete(&col, 0, 2), (-1, 0.0));
    }

    #[test]
    fn most_complete_respects_span_offset() {
        // span [2,4): "z","zzz" -> local idx 1, conf 1.0
        let col = strcol(&[Some("a"), Some("a"), Some("z"), Some("zzz")]);
        assert_eq!(most_complete(&col, 2, 2), (1, 1.0));
    }
}
