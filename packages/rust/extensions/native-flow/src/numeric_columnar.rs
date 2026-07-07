//! Numeric columnar execution — Phase 3 wave 3b. Runs a numeric config on a string
//! column entirely natively: `[string ops] -> [one f64 parser] -> [f64 ops]`,
//! formatting the f64 result back to string via the Polars-matching
//! `float_to_polars_string` (wave 3a) so the CSV output + manifest are
//! byte-identical to the Polars engine.
//!
//! Parity model (from `engine/transformer.py`): every op's `affected` is
//! `(before.cast(Utf8) != after.cast(Utf8)).sum()` with null comparisons excluded,
//! and samples are `head(3).cast(Utf8)`. For a string cell cast(Utf8) is itself;
//! for an f64 cell it is `float_to_polars_string`. So the whole manifest is
//! computed by formatting each state and comparing — which also makes a
//! `-0.0 -> 0.0` change count (the formats differ) exactly as Polars does, even
//! though raw `==` would call them equal.
//!
//! Scope: the f64 parsers (`currency_strip`/`percentage_normalize`/`comma_decimal`/
//! `scientific_to_decimal`/`fraction_to_decimal`) + the f64 array ops
//! (`round`/`clamp`/`abs_value`/`fill_zero`). The i64 parsers (`to_integer`/
//! `roman_to_int`/`ordinal_to_int`, which yield an Int64 column with different
//! formatting) are a later increment.

use arrow::array::{Array, Float64Array, Float64Builder, LargeStringArray};
use pyo3::prelude::*;

use goldenflow_core::chain::{
    apply_chain_f64, apply_chain_nullable, NullableKernel, NumericKernel,
};
use goldenflow_core::float_fmt::float_to_polars_string;
use goldenflow_core::numeric;

use crate::csvio::OpRecord;

/// A string -> f64 parser (the dtype transition). f64-valued only.
#[derive(Clone, Copy)]
enum NumericParser {
    CurrencyStrip,
    PercentageNormalize,
    CommaDecimal,
    ScientificToDecimal,
    FractionToDecimal,
}

impl NumericParser {
    fn from_name(name: &str) -> Option<NumericParser> {
        Some(match name {
            "currency_strip" => NumericParser::CurrencyStrip,
            "percentage_normalize" => NumericParser::PercentageNormalize,
            "comma_decimal" => NumericParser::CommaDecimal,
            "scientific_to_decimal" => NumericParser::ScientificToDecimal,
            "fraction_to_decimal" => NumericParser::FractionToDecimal,
            _ => return None,
        })
    }

    fn parse(self, s: &str) -> Option<f64> {
        match self {
            NumericParser::CurrencyStrip => numeric::currency_strip(s),
            NumericParser::PercentageNormalize => numeric::percentage_normalize(s),
            NumericParser::CommaDecimal => numeric::comma_decimal(s),
            NumericParser::ScientificToDecimal => numeric::scientific_to_decimal(s),
            NumericParser::FractionToDecimal => numeric::fraction_to_decimal(s),
        }
    }
}

/// A resolved numeric column plan: optional leading string ops, exactly one f64
/// parser (the transition), optional trailing f64 array ops.
pub struct NumericPlan {
    string_ops: Vec<(String, NullableKernel)>,
    parser: (String, NumericParser),
    f64_ops: Vec<(String, NumericKernel)>,
}

/// Resolve `ops` to a [`NumericPlan`] iff they form a valid numeric shape:
/// `string* parser f64*`. Returns `None` if there is no f64 parser (not a numeric
/// config → the string chains handle it) or the shape is invalid (a string op after
/// the transition, a second parser, an f64 op before the parser, etc.).
pub fn resolve_numeric(ops: &[(String, Vec<String>)]) -> Option<NumericPlan> {
    let mut string_ops = Vec::new();
    let mut parser: Option<(String, NumericParser)> = None;
    let mut f64_ops = Vec::new();
    for (name, params) in ops {
        let refs: Vec<&str> = params.iter().map(String::as_str).collect();
        if parser.is_none() {
            if let Some(p) = NumericParser::from_name(name) {
                parser = Some((name.clone(), p));
            } else if let Some(k) = NullableKernel::from_op(name, &refs) {
                string_ops.push((name.clone(), k));
            } else {
                return None;
            }
        } else if let Some(k) = NumericKernel::from_op(name, &refs) {
            f64_ops.push((name.clone(), k));
        } else {
            return None;
        }
    }
    Some(NumericPlan {
        string_ops,
        parser: parser?,
        f64_ops,
    })
}

fn str_opts(arr: &LargeStringArray) -> Vec<Option<String>> {
    (0..arr.len())
        .map(|i| (!arr.is_null(i)).then(|| arr.value(i).to_string()))
        .collect()
}

fn fmt_f64(arr: &Float64Array) -> Vec<Option<String>> {
    (0..arr.len())
        .map(|i| (!arr.is_null(i)).then(|| float_to_polars_string(arr.value(i))))
        .collect()
}

/// `(before.cast(Utf8) != after.cast(Utf8)).sum()` — rows where both are non-null
/// and the formatted strings differ.
fn affected(before: &[Option<String>], after: &[Option<String>]) -> u64 {
    before
        .iter()
        .zip(after)
        .filter(|(b, a)| matches!((b, a), (Some(b), Some(a)) if b != a))
        .count() as u64
}

fn head3(v: &[Option<String>]) -> Vec<Option<String>> {
    v.iter().take(3).cloned().collect()
}

/// Execute a [`NumericPlan`] over one string column, returning the formatted output
/// column (f64 rendered exactly as Polars' write_csv would) and the per-op manifest
/// records — all byte-identical to the Polars engine.
pub fn run_numeric_column(
    input: &LargeStringArray,
    plan: &NumericPlan,
) -> (LargeStringArray, Vec<OpRecord>) {
    let total = input.len() as u64;
    let mut records: Vec<OpRecord> =
        Vec::with_capacity(plan.string_ops.len() + 1 + plan.f64_ops.len());

    // --- string phase (fused one pass; per-op affected from the fused counts, 3-row
    // replay for samples — the same shape as the string columnar path) ---
    let str_kernels: Vec<NullableKernel> = plan.string_ops.iter().map(|(_, k)| *k).collect();
    let str_col: LargeStringArray = if str_kernels.is_empty() {
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

    // --- parser: string -> f64 (null in / unparseable -> null) ---
    let mut builder = Float64Builder::with_capacity(str_col.len());
    for i in 0..str_col.len() {
        match (!str_col.is_null(i))
            .then(|| str_col.value(i))
            .and_then(|s| plan.parser.1.parse(s))
        {
            Some(f) => builder.append_value(f),
            None => builder.append_null(),
        }
    }
    let mut cur_arr = builder.finish();
    let before_fmt = str_opts(&str_col);
    let mut cur_fmt = fmt_f64(&cur_arr);
    records.push((
        plan.parser.0.clone(),
        affected(&before_fmt, &cur_fmt),
        total,
        head3(&before_fmt),
        head3(&cur_fmt),
    ));

    // --- f64 array ops (op-by-op so `affected` uses the formatted comparison) ---
    for (name, kernel) in &plan.f64_ops {
        let next_arr = apply_chain_f64(&cur_arr, std::slice::from_ref(kernel)).array;
        let next_fmt = fmt_f64(&next_arr);
        records.push((
            name.clone(),
            affected(&cur_fmt, &next_fmt),
            total,
            head3(&cur_fmt),
            head3(&next_fmt),
        ));
        cur_arr = next_arr;
        cur_fmt = next_fmt;
    }

    (LargeStringArray::from_iter(cur_fmt), records)
}

/// Host gate: is this a config the native numeric columnar path can run? (A valid
/// `string* parser f64*` shape.) The single source of truth the Python
/// `config_is_columnar_ready` calls, so host and kernel never disagree.
#[pyfunction]
pub fn columnar_numeric_ready(ops: Vec<(String, Vec<String>)>) -> bool {
    resolve_numeric(&ops).is_some()
}
