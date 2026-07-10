//! Multi-output (split) columnar execution — Phase 3 wave 3e. A config of shape
//! `[string ops] -> [one splitter]` (`split_name`/`split_name_reverse` ->
//! `first_name`+`last_name`; `split_address` -> `street`+`city`+`state`+`zip`) runs
//! natively: the leading string ops transform the source column in place, then the
//! terminal splitter ADDS the fixed-name output columns (the source column itself is
//! unchanged by the split, exactly as Polars' `df.with_columns(...)` dataframe-mode
//! transform does). Byte-identical (data + manifest) to the Polars engine.
//!
//! Manifest: the string ops record as usual; the splitter records the SOURCE column
//! (unchanged by the split -> affected 0, before == after == the post-string-ops
//! source), matching how the engine audits a dataframe-mode transform.

use arrow::array::{Array, LargeStringArray};
use pyo3::prelude::*;

use goldenflow_core::address;
use goldenflow_core::chain::{apply_chain_nullable, NullableKernel};
use goldenflow_core::names;

use crate::csvio::OpRecord;

/// `(source_column, [(output_name, output_column)], per-op records)`.
type SplitResult = (
    LargeStringArray,
    Vec<(String, LargeStringArray)>,
    Vec<OpRecord>,
);

/// Host gate: is this a config the native split path can run? (A valid
/// `string* splitter` shape.) The single source of truth the Python
/// `config_is_columnar_ready` / `columnar_file_ready` call.
#[pyfunction]
pub fn columnar_split_ready(ops: Vec<(String, Vec<String>)>) -> bool {
    resolve_split(&ops).is_some()
}

/// A terminal 1 -> N splitter and its fixed output column names.
#[derive(Clone, Copy)]
enum Splitter {
    Name,
    NameReverse,
    Address,
}

impl Splitter {
    fn from_name(name: &str) -> Option<Splitter> {
        Some(match name {
            "split_name" => Splitter::Name,
            "split_name_reverse" => Splitter::NameReverse,
            "split_address" => Splitter::Address,
            _ => return None,
        })
    }

    fn output_names(self) -> &'static [&'static str] {
        match self {
            Splitter::Name | Splitter::NameReverse => &["first_name", "last_name"],
            Splitter::Address => &["street", "city", "state", "zip"],
        }
    }

    /// Split `col` into the output columns (one `LargeStringArray` per output name).
    /// A null input cell yields null in every output.
    fn split(self, col: &LargeStringArray) -> Vec<LargeStringArray> {
        let n = self.output_names().len();
        let mut outs: Vec<Vec<Option<String>>> = vec![Vec::with_capacity(col.len()); n];
        for i in 0..col.len() {
            if col.is_null(i) {
                for o in outs.iter_mut() {
                    o.push(None);
                }
                continue;
            }
            let s = col.value(i);
            match self {
                Splitter::Name => {
                    let (f, l) = names::split_name(s);
                    outs[0].push(Some(f));
                    outs[1].push(Some(l));
                }
                Splitter::NameReverse => {
                    let (f, l) = names::split_name_reverse(s);
                    outs[0].push(Some(f));
                    outs[1].push(Some(l));
                }
                Splitter::Address => {
                    let (street, city, state, zip) = address::split_address(s);
                    outs[0].push(Some(street));
                    outs[1].push(city);
                    outs[2].push(state);
                    outs[3].push(zip);
                }
            }
        }
        outs.into_iter().map(LargeStringArray::from).collect()
    }
}

/// A resolved split plan: leading string ops + one terminal splitter.
pub struct SplitPlan {
    string_ops: Vec<(String, NullableKernel)>,
    splitter: (String, Splitter),
}

/// Resolve `ops` to a [`SplitPlan`] iff they form `string* splitter` with the
/// splitter as the LAST op. `None` if there is no splitter (not a split config) or
/// the shape is invalid (an op after the splitter, a non-fusable string op, etc.).
pub fn resolve_split(ops: &[(String, Vec<String>)]) -> Option<SplitPlan> {
    let mut string_ops = Vec::new();
    let mut splitter: Option<(String, Splitter)> = None;
    let last = ops.len().checked_sub(1)?;
    for (i, (name, params)) in ops.iter().enumerate() {
        if let Some(s) = Splitter::from_name(name) {
            if i != last {
                return None; // a splitter must be terminal
            }
            splitter = Some((name.clone(), s));
        } else {
            let refs: Vec<&str> = params.iter().map(String::as_str).collect();
            let k = NullableKernel::from_op(name, &refs)?;
            string_ops.push((name.clone(), k));
        }
    }
    Some(SplitPlan {
        string_ops,
        splitter: splitter?,
    })
}

fn str_opts(arr: &LargeStringArray) -> Vec<Option<String>> {
    (0..arr.len())
        .map(|i| (!arr.is_null(i)).then(|| arr.value(i).to_string()))
        .collect()
}

fn head3(v: &[Option<String>]) -> Vec<Option<String>> {
    v.iter().take(3).cloned().collect()
}

/// Execute a [`SplitPlan`] over one string column, returning `(source_column,
/// [(output_name, output_column)], records)`: the source column after the string
/// ops (the split leaves it unchanged), the fixed-name output columns to add, and
/// the per-op manifest records (string ops as usual; the splitter records the
/// unchanged source -> affected 0). Byte-identical to the Polars engine.
pub fn run_split_column(input: &LargeStringArray, plan: &SplitPlan) -> SplitResult {
    let total = input.len() as u64;
    let mut records: Vec<OpRecord> = Vec::with_capacity(plan.string_ops.len() + 1);

    // String phase (fused; 3-row replay for samples).
    let str_kernels: Vec<NullableKernel> = plan.string_ops.iter().map(|(_, k)| *k).collect();
    let src: LargeStringArray = if str_kernels.is_empty() {
        input.clone()
    } else {
        let fused = apply_chain_nullable(input, &str_kernels);
        let mut head = LargeStringArray::from_iter(str_opts(input).into_iter().take(3));
        for (i, (name, _)) in plan.string_ops.iter().enumerate() {
            let before = head3(&str_opts(&head));
            let replay = apply_chain_nullable(&head, std::slice::from_ref(&str_kernels[i]));
            let after = head3(&str_opts(&replay.array));
            records.push((name.clone(), fused.changed[i], total, before, after));
            head = replay.array;
        }
        fused.array
    };

    // The split adds columns but leaves the source unchanged -> a no-op audit record
    // on the source column (before == after == the post-string-ops source).
    let src_head = head3(&str_opts(&src));
    records.push((
        plan.splitter.0.clone(),
        0,
        total,
        src_head.clone(),
        src_head,
    ));

    let arrays = plan.splitter.1.split(&src);
    let new_cols: Vec<(String, LargeStringArray)> = plan
        .splitter
        .1
        .output_names()
        .iter()
        .map(|s| s.to_string())
        .zip(arrays)
        .collect();

    (src, new_cols, records)
}
