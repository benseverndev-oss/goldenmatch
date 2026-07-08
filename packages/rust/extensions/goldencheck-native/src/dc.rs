//! Shim for the denial-constraint evidence-set kernel.
use goldencheck_core::{dc_pair_evidence, dc_row_evidence, Pred};
use pyo3::prelude::*;

/// Denial-constraint evidence set. Plain-list interface (columns already interned
/// to u64 ids Python-side, so the Arrow C Data Interface buys nothing for a
/// derived sample -- mirrors the fuzzy shim).
///
/// - `cols`: per-column id vectors (`cols[c][r]` = id of column c row r; null -> 0)
/// - `nulls`: per-column null masks (same shape as `cols`)
/// - `pred_spec`: predicate list as `(kind, col_a, op, col_b, literal)` tuples;
///     kind 0=const 1=single 2=cross; op 0=EQ 1=NE 2=LT 3=LE 4=GT 5=GE.
///     Split here into singles (kind 0/1, order-preserving) + crosses (kind 2),
///     matching the Python `evidence._split`.
/// - `which_pass`: 1 = row evidence (Pass 1, over rows `0..n`), 2 = pair evidence
///     (Pass 2, over ordered pairs of `sample_idx`).
/// - `n`: row count for Pass 1 (ignored for Pass 2).
/// - `sample_idx`: row indices for Pass 2 (ignored for Pass 1).
///
/// Returns two PARALLEL lists `(masks, counts)` = the `{mask: count}` evidence map.
#[pyfunction]
#[pyo3(signature = (cols, nulls, pred_spec, which_pass, n, sample_idx))]
pub fn denial_constraint_evidence(
    cols: Vec<Vec<u64>>,
    nulls: Vec<Vec<bool>>,
    pred_spec: Vec<(u8, usize, u8, usize, u64)>,
    which_pass: u8,
    n: usize,
    sample_idx: Vec<usize>,
) -> PyResult<(Vec<u64>, Vec<u64>)> {
    // Split preserving order: kind 0/1 -> singles, kind 2 -> crosses.
    let mut singles: Vec<Pred> = Vec::new();
    let mut crosses: Vec<Pred> = Vec::new();
    for (kind, col_a, op, col_b, literal) in pred_spec {
        let p = Pred {
            kind,
            col_a,
            op,
            col_b,
            literal,
        };
        if kind == 2 {
            crosses.push(p);
        } else {
            singles.push(p);
        }
    }
    // Borrow columns as slices.
    let col_refs: Vec<&[u64]> = cols.iter().map(|c| c.as_slice()).collect();
    let null_refs: Vec<&[bool]> = nulls.iter().map(|c| c.as_slice()).collect();

    let ev = match which_pass {
        1 => dc_row_evidence(&singles, &col_refs, &null_refs, n),
        2 => dc_pair_evidence(&singles, &crosses, &col_refs, &null_refs, &sample_idx),
        _ => {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "which_pass must be 1 or 2",
            ))
        }
    };
    let masks: Vec<u64> = ev.iter().map(|(m, _)| *m).collect();
    let counts: Vec<u64> = ev.iter().map(|(_, c)| *c).collect();
    Ok((masks, counts))
}
