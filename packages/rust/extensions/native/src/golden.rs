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
const STRAT_SOURCE_PRIORITY: u8 = 2;
const STRAT_MOST_RECENT: u8 = 3;
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

/// Shared "longest `str(v)` wins" pick for `most_complete` / `longest_value`.
/// Unique longest -> conf 1.0; a length tie -> first-in-order member at
/// `tie_conf` (0.7 for most_complete, 0.5 for longest_value -- the ONLY
/// difference between the two strategies on the unweighted path). Quality-weight
/// tie-break is Stage 3.
fn longest_pick(text: &StrCol, non_null: &[(usize, i64)], off: usize, tie_conf: f64) -> (i64, f64) {
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
        (longest[0] as i64, tie_conf)
    }
}

/// `_most_complete` (`golden.py:125`), sans the short-circuit (handled
/// universally): longest `str(v)`, length tie -> first-in-order at conf 0.7.
fn most_complete(text: &StrCol, non_null: &[(usize, i64)], off: usize) -> (i64, f64) {
    longest_pick(text, non_null, off, 0.7)
}

/// `_longest_value` (`golden.py:209`), unweighted branch: same pick as
/// `most_complete` but a length tie yields conf 0.5 (not 0.7).
fn longest_value(text: &StrCol, non_null: &[(usize, i64)], off: usize) -> (i64, f64) {
    longest_pick(text, non_null, off, 0.5)
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
    // First-appearance tie-break: strict `>` keeps the EARLIEST-appearing code
    // on a count tie (matching `Counter.most_common`'s stable order). Do NOT
    // rewrite to `max_by_key` -- it returns the LAST max, silently flipping the
    // tie to the last occurrence and picking the wrong representative index.
    #[allow(clippy::needless_range_loop)]
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

/// `_source_priority` (`golden.py:142`). Records the FIRST row per source
/// (regardless of null value), then walks `priority` (a source-code list); the
/// first source whose first-occurrence value is non-null wins.
/// `conf = max(0.1, 1.0 - idx*0.1)`; no match -> `(-1, 0.0)`.
///
/// Precise null handling (matches the reference `source_val[src] = val` /
/// `if val is not None`): the winner value is specifically the FIRST row of the
/// winning source. If that first row's value is null, the source is skipped even
/// if a LATER row of the same source has a non-null value — we only ever look at
/// the first row's `value_code`.
///
/// `source_code[i] < 0` (null `__source__`) rows are never a priority target (a
/// priority list holds strings, never None), and an ABSENT priority source is
/// encoded as a negative code in Python — so both are excluded by the `< 0`
/// guard, which also prevents an absent-priority sentinel from colliding with
/// the null-source group. Winner index is the LOCAL span index.
fn source_priority(
    source_code: &[i64],
    value_code: &[i64],
    priority: &[i64],
    off: usize,
    size: usize,
) -> (i64, f64) {
    // First-occurrence (source_code >= 0) in span order: (source_code, local).
    let mut first: Vec<(i64, usize)> = Vec::new();
    for l in 0..size {
        let sc = source_code[off + l];
        if sc < 0 {
            continue;
        }
        if !first.iter().any(|&(s, _)| s == sc) {
            first.push((sc, l));
        }
    }
    for (idx, &pc) in priority.iter().enumerate() {
        if pc < 0 {
            continue; // absent priority source (or reserved sentinel)
        }
        if let Some(&(_, first_local)) = first.iter().find(|&&(s, _)| s == pc) {
            if value_code[off + first_local] != -1 {
                let conf = (1.0 - idx as f64 * 0.1).max(0.1);
                return (first_local as i64, conf);
            }
        }
    }
    (-1, 0.0)
}

/// `_most_recent` (`golden.py:166`). Eligible rows = value non-null AND date
/// non-null. Python `sort(key=date, reverse=True)` is STABLE, so among rows tied
/// on the top date the FIRST-occurring (lowest local index) wins — replicated
/// here as "first eligible row holding the max date" (NOT a reversed comparator,
/// which would pick the last). `conf = 0.5` when >=2 eligible rows share the top
/// date, else `1.0`; none eligible -> `(-1, 0.0)`.
fn most_recent(
    value_code: &[i64],
    date: &[i64],
    date_null: &[i64],
    off: usize,
    size: usize,
) -> (i64, f64) {
    let eligible = |l: usize| value_code[off + l] != -1 && date_null[off + l] == 0;
    let mut max_date: Option<i64> = None;
    for l in 0..size {
        if !eligible(l) {
            continue;
        }
        let d = date[off + l];
        max_date = Some(match max_date {
            Some(m) if m >= d => m,
            _ => d,
        });
    }
    let md = match max_date {
        Some(m) => m,
        None => return (-1, 0.0),
    };
    let mut winner_local: i64 = -1;
    let mut tie_count = 0usize;
    for l in 0..size {
        if eligible(l) && date[off + l] == md {
            if winner_local < 0 {
                winner_local = l as i64;
            }
            tie_count += 1;
        }
    }
    let conf = if tie_count > 1 { 0.5 } else { 1.0 };
    (winner_local, conf)
}

/// Kernel result: `(winner_idx, field_conf, cluster_ids_out)`, each outer Vec
/// indexed by output column (except `cluster_ids_out`, the per-cluster id list).
/// `winner_idx[col][k]` is the GLOBAL pre-sorted-frame row index whose value
/// survives for column `col` in cluster `k` (`-1` = null); `field_conf[col][k]`
/// its confidence.
type GoldenFusedResult = (Vec<Vec<i64>>, Vec<Vec<f64>>, Vec<i64>);

/// Per-column strategy SIDE CHANNELS, extracted from a Python
/// `_GoldenFusedSideChannels` dataclass (attribute-named to match these fields).
/// Consolidating the side channels into ONE carrier keeps `golden_fused`'s
/// positional arity flat as later stages add channels: each new channel is ONE
/// struct field + ONE Python assignment, not a new positional arg threaded
/// through both the marshal site and this destructure.
///
/// Stage 2 fields:
/// - `source_code`: factorized `__source__` (Int64, len n_rows) — present only
///   when some column uses source_priority (else an empty array).
/// - `priority_codes[col]`: that column's source_priority list mapped into
///   source-code space (empty for non source_priority columns; absent sources
///   encoded `< 0`).
/// - `date_cols[col]` / `date_null_masks[col]`: Int64 arrays (len n_rows) for
///   most_recent columns (empty arrays otherwise); the mask is 1 = null-date,
///   0 = present.
///
/// Future stages append fields here (qweights, pair scores, group specs,
/// predicate IR, cluster-override codes) — same one-field-per-stage rule.
#[derive(FromPyObject)]
pub struct GoldenFusedSideChannels {
    source_code: PyArrowType<ArrayData>,
    priority_codes: Vec<Vec<i64>>,
    date_cols: Vec<PyArrowType<ArrayData>>,
    date_null_masks: Vec<PyArrowType<ArrayData>>,
}

#[pyfunction]
#[pyo3(signature = (
    row_ids, cluster_ids, n_output_cols, strategy_ids, text_cols, code_cols, side,
))]
// 8/7 with `py`: the six core args + the single `side` carrier. Folding the
// side channels into `side` is exactly what keeps this from ballooning as later
// stages add channels; the remaining args are the irreducible per-column core.
#[allow(clippy::too_many_arguments)]
pub fn golden_fused(
    py: Python<'_>,
    row_ids: PyArrowType<ArrayData>,
    cluster_ids: PyArrowType<ArrayData>,
    n_output_cols: usize,
    strategy_ids: Vec<u8>,
    text_cols: Vec<PyArrowType<ArrayData>>,
    code_cols: Vec<PyArrowType<ArrayData>>,
    side: GoldenFusedSideChannels,
) -> PyResult<GoldenFusedResult> {
    let GoldenFusedSideChannels {
        source_code,
        priority_codes,
        date_cols,
        date_null_masks,
    } = side;
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

    // ── Stage 2 keys: source_priority + most_recent ─────────────────────────
    let any_source_priority = strategy_ids.contains(&STRAT_SOURCE_PRIORITY);

    // Read an Int64 arrow array into an owned Vec. A length-0 array yields an
    // empty Vec (the "column doesn't use this key" placeholder).
    fn read_i64(d: ArrayData, what: &str) -> PyResult<Vec<i64>> {
        if d.data_type() != &DataType::Int64 {
            return Err(PyValueError::new_err(format!(
                "golden_fused: {what} must be int64, got {:?}",
                d.data_type()
            )));
        }
        Ok(Int64Array::from(d).values().to_vec())
    }

    // Per-column side-channel Vecs are ALWAYS n_output_cols long (Python fills a
    // placeholder for columns that don't use a channel) -- validate all three
    // unconditionally so the per-column indexing below can't panic.
    for (name, len) in [
        ("priority_codes", priority_codes.len()),
        ("date_cols", date_cols.len()),
        ("date_null_masks", date_null_masks.len()),
    ] {
        if len != n_output_cols {
            return Err(PyValueError::new_err(format!(
                "golden_fused: {name} length {len} != n_output_cols {n_output_cols}"
            )));
        }
    }

    // source_code is a single shared column: only required (len n_rows) when a
    // source_priority column exists (else it's an empty placeholder array).
    let source_vals = read_i64(source_code.0, "source_code")?;
    if any_source_priority && source_vals.len() != n_rows {
        return Err(PyValueError::new_err(format!(
            "golden_fused: source_code length {} != row count {n_rows}",
            source_vals.len()
        )));
    }

    let mut date_vals: Vec<Vec<i64>> = Vec::with_capacity(n_output_cols);
    for (c, p) in date_cols.into_iter().enumerate() {
        let v = read_i64(p.0, "date col")?;
        if strategy_ids[c] == STRAT_MOST_RECENT && v.len() != n_rows {
            return Err(PyValueError::new_err(format!(
                "golden_fused: date col {c} length {} != row count {n_rows}",
                v.len()
            )));
        }
        date_vals.push(v);
    }
    let mut date_null_vals: Vec<Vec<i64>> = Vec::with_capacity(n_output_cols);
    for (c, p) in date_null_masks.into_iter().enumerate() {
        let v = read_i64(p.0, "date null mask")?;
        if strategy_ids[c] == STRAT_MOST_RECENT && v.len() != n_rows {
            return Err(PyValueError::new_err(format!(
                "golden_fused: date null mask {c} length {} != row count {n_rows}",
                v.len()
            )));
        }
        date_null_vals.push(v);
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
                        STRAT_SOURCE_PRIORITY => source_priority(
                            &source_vals,
                            &code_vals[col],
                            &priority_codes[col],
                            off,
                            size,
                        ),
                        STRAT_MOST_RECENT => most_recent(
                            &code_vals[col],
                            &date_vals[col],
                            &date_null_vals[col],
                            off,
                            size,
                        ),
                        STRAT_FIRST_NON_NULL => first_non_null(&non_null),
                        STRAT_LONGEST_VALUE => longest_value(&text[col], &non_null, off),
                        STRAT_UNANIMOUS_OR_NULL => unanimous_or_null(&non_null),
                        // confidence_majority (Stage 4) still declines in Python
                        // before reaching here; null sentinel defensively.
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

    // ── source_priority ──────────────────────────────────────────────────────

    #[test]
    fn source_priority_top_priority_wins() {
        // sources [A=0, B=1], values [x, y]; priority [B, A] -> B (code 1) first
        // occurrence at local 1, idx 0 in priority -> conf 1.0.
        let src = [0i64, 1];
        let val = [10i64, 20];
        assert_eq!(source_priority(&src, &val, &[1, 0], 0, 2), (1, 1.0));
    }

    #[test]
    fn source_priority_null_top_source_falls_through() {
        // First row of the TOP-priority source has a null value -> skip it, next
        // priority wins. sources [A=0, B=1]; values [null, 20]; priority [A, B]:
        // A's first value is null -> B wins at local 1, idx 1 -> conf 0.9.
        let src = [0i64, 1];
        let val = [-1i64, 20];
        assert_eq!(source_priority(&src, &val, &[0, 1], 0, 2), (1, 0.9));
    }

    #[test]
    fn source_priority_absent_source_skipped() {
        // priority [absent(-1), A(0)] -> absent skipped, A wins at idx 1 conf 0.9.
        let src = [0i64, 1];
        let val = [10i64, 20];
        assert_eq!(source_priority(&src, &val, &[-1, 0], 0, 2), (0, 0.9));
    }

    #[test]
    fn source_priority_first_occurrence_per_source() {
        // Two rows of source A (code 0): only the FIRST (local 0) is recorded.
        // sources [A, A, B]; values [10, 99, 20]; priority [A] -> local 0.
        let src = [0i64, 0, 1];
        let val = [10i64, 99, 20];
        assert_eq!(source_priority(&src, &val, &[0], 0, 3), (0, 1.0));
    }

    #[test]
    fn source_priority_no_match_is_sentinel() {
        // priority holds only an absent source -> no winner.
        let src = [0i64, 1];
        let val = [10i64, 20];
        assert_eq!(source_priority(&src, &val, &[-1], 0, 2), (-1, 0.0));
    }

    #[test]
    fn source_priority_conf_floor_01() {
        // 11th priority position -> 1.0 - 10*0.1 = 0.0, floored to 0.1.
        let src = [0i64];
        let val = [10i64];
        let prio: Vec<i64> = (0..10).map(|_| -1).chain(std::iter::once(0)).collect();
        assert_eq!(source_priority(&src, &val, &prio, 0, 1), (0, 0.1));
    }

    // ── most_recent ──────────────────────────────────────────────────────────

    #[test]
    fn most_recent_picks_latest() {
        // dates [1, 3, 2], all values present, no nulls -> local 1 (date 3), conf 1.0.
        let val = [10i64, 20, 30];
        let date = [1i64, 3, 2];
        let mask = [0i64, 0, 0];
        assert_eq!(most_recent(&val, &date, &mask, 0, 3), (1, 1.0));
    }

    #[test]
    fn most_recent_top_date_tie_first_occurrence_conf_05() {
        // dates [3, 3, 1]; two rows share the top date 3 -> first (local 0), conf 0.5.
        let val = [10i64, 20, 30];
        let date = [3i64, 3, 1];
        let mask = [0i64, 0, 0];
        assert_eq!(most_recent(&val, &date, &mask, 0, 3), (0, 0.5));
    }

    #[test]
    fn most_recent_drops_null_date_and_null_value() {
        // local 0: null date (dropped). local 1: null value (dropped). local 2:
        // eligible date 5. -> local 2, conf 1.0.
        let val = [10i64, -1, 30];
        let date = [9i64, 8, 5];
        let mask = [1i64, 0, 0];
        assert_eq!(most_recent(&val, &date, &mask, 0, 3), (2, 1.0));
    }

    #[test]
    fn most_recent_none_eligible_is_sentinel() {
        // all rows have null date -> no eligible row.
        let val = [10i64, 20];
        let date = [1i64, 2];
        let mask = [1i64, 1];
        assert_eq!(most_recent(&val, &date, &mask, 0, 2), (-1, 0.0));
    }

    #[test]
    fn most_recent_negative_epoch_ordering() {
        // Negative epoch values order correctly (the reason for an explicit mask,
        // not a sentinel): dates [-10, -3, -20] -> latest is -3 at local 1.
        let val = [10i64, 20, 30];
        let date = [-10i64, -3, -20];
        let mask = [0i64, 0, 0];
        assert_eq!(most_recent(&val, &date, &mask, 0, 3), (1, 1.0));
    }
}
