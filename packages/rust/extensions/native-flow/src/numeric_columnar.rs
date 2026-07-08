//! Numeric columnar execution — Phase 3 waves 3b/3c. Runs a numeric config on a
//! string column entirely natively: `[string ops] -> [one parser] -> [f64 ops]`,
//! formatting the numeric result back to string (f64 via the Polars-matching
//! `float_to_polars_string` from wave 3a, i64 as a plain integer) so the CSV output
//! + manifest are byte-identical to the Polars engine.
//!
//! Parity model (from `engine/transformer.py`): every op's `affected` is
//! `(before.cast(Utf8) != after.cast(Utf8)).sum()` with null comparisons excluded,
//! and samples are `head(3).cast(Utf8)`. For a string cell cast(Utf8) is itself;
//! for an f64 cell it is `float_to_polars_string`. So the whole manifest is
//! computed by formatting each state and comparing — which also makes a
//! `-0.0 -> 0.0` change count (the formats differ) exactly as Polars does, even
//! though raw `==` would call them equal.
//!
//! Parsers: f64 (`currency_strip`/`percentage_normalize`/`comma_decimal`/
//! `scientific_to_decimal`/`fraction_to_decimal`) and i64 (`to_integer`/
//! `roman_to_int`/`ordinal_to_int`). Array ops (`round`/`clamp`/`abs_value`/
//! `fill_zero`) are f64; the first one applied to an Int64 column promotes it to
//! Float64, exactly as Polars does (`[to_integer, round]` -> Float64 output).

use arrow::array::{
    Array, Float64Array, Float64Builder, Int64Array, Int64Builder, LargeStringArray,
};
use pyo3::prelude::*;

use goldenflow_core::chain::{
    apply_chain_f64, apply_chain_nullable, NullableKernel, NumericKernel,
};
use goldenflow_core::float_fmt::float_to_polars_string;
use goldenflow_core::numeric;

use crate::csvio::OpRecord;

/// A string -> number parser (the dtype transition). f64-valued parsers yield a
/// Float64 column; i64-valued parsers yield an Int64 column (which a later f64
/// array op promotes to Float64, exactly as Polars does).
#[derive(Clone, Copy)]
enum NumericParser {
    // f64-valued
    CurrencyStrip,
    PercentageNormalize,
    CommaDecimal,
    ScientificToDecimal,
    FractionToDecimal,
    // i64-valued
    ToInteger,
    RomanToInt,
    OrdinalToInt,
    // SYNTHETIC (zero-gap): the implicit string->f64 coerce prepended to a numeric-only
    // chain (`round`/`clamp`/`abs_value`/`fill_zero` with no explicit parser). Matches
    // Polars `cast(Float64, strict=False)`: standard float parse, NO whitespace trim,
    // accepts inf/nan/E/leading-dot, rejects `_`/`,`/`0x`. Emits NO manifest record (the
    // engine applies the numeric op as one step, not a separate coerce).
    AsFloat,
}

impl NumericParser {
    fn from_name(name: &str) -> Option<NumericParser> {
        Some(match name {
            "currency_strip" => NumericParser::CurrencyStrip,
            "percentage_normalize" => NumericParser::PercentageNormalize,
            "comma_decimal" => NumericParser::CommaDecimal,
            "scientific_to_decimal" => NumericParser::ScientificToDecimal,
            "fraction_to_decimal" => NumericParser::FractionToDecimal,
            "to_integer" => NumericParser::ToInteger,
            "roman_to_int" => NumericParser::RomanToInt,
            "ordinal_to_int" => NumericParser::OrdinalToInt,
            _ => return None,
        })
    }

    /// Parse every non-null cell of `col` into the parser's numeric dtype
    /// (unparseable / null -> null).
    fn parse_column(self, col: &LargeStringArray) -> NumCol {
        let cells = (0..col.len()).map(|i| (!col.is_null(i)).then(|| col.value(i)));
        match self {
            NumericParser::ToInteger => {
                NumCol::I64(build_i64(cells.map(|c| c.and_then(numeric::to_integer))))
            }
            NumericParser::RomanToInt => {
                NumCol::I64(build_i64(cells.map(|c| c.and_then(numeric::roman_to_int))))
            }
            NumericParser::OrdinalToInt => NumCol::I64(build_i64(
                cells.map(|c| c.and_then(numeric::ordinal_to_int)),
            )),
            _ => {
                let f = move |s: &str| match self {
                    NumericParser::CurrencyStrip => numeric::currency_strip(s),
                    NumericParser::PercentageNormalize => numeric::percentage_normalize(s),
                    NumericParser::CommaDecimal => numeric::comma_decimal(s),
                    NumericParser::ScientificToDecimal => numeric::scientific_to_decimal(s),
                    NumericParser::FractionToDecimal => numeric::fraction_to_decimal(s),
                    // Polars `cast(Float64, strict=False)` == Rust std f64 parse (no
                    // trim). ` 1.5`/`1_000`/`1,234`/`abc` -> null; inf/nan/E accepted.
                    NumericParser::AsFloat => s.parse::<f64>().ok(),
                    _ => unreachable!("i64 parsers handled above"),
                };
                NumCol::F64(build_f64(cells.map(|c| c.and_then(f))))
            }
        }
    }
}

/// A parsed numeric column: Int64 (from an integer parser, until an f64 op
/// promotes it) or Float64. `cast(Utf8)` — for `affected`/samples — is a plain
/// integer for I64, the Polars-matching float format for F64.
pub enum NumCol {
    I64(Int64Array),
    F64(Float64Array),
}

impl NumCol {
    /// `cast(Utf8)` per cell — the CSV output + the manifest before/after strings.
    pub fn fmt(&self) -> Vec<Option<String>> {
        match self {
            NumCol::I64(a) => (0..a.len())
                .map(|i| (!a.is_null(i)).then(|| a.value(i).to_string()))
                .collect(),
            NumCol::F64(a) => fmt_f64(a),
        }
    }

    /// The RAW numeric array (Int64 / Float64) for the in-memory path, which egresses
    /// it as an Arrow column of the matching dtype (compared by value, not formatted).
    pub fn into_array(self) -> arrow::array::ArrayRef {
        match self {
            NumCol::I64(a) => std::sync::Arc::new(a),
            NumCol::F64(a) => std::sync::Arc::new(a),
        }
    }

    /// The values as f64 (an f64 array op promotes an Int64 column to Float64).
    fn to_f64(&self) -> Float64Array {
        match self {
            NumCol::F64(a) => a.clone(),
            NumCol::I64(a) => {
                build_f64((0..a.len()).map(|i| (!a.is_null(i)).then(|| a.value(i) as f64)))
            }
        }
    }
}

fn build_i64(it: impl Iterator<Item = Option<i64>>) -> Int64Array {
    let mut b = Int64Builder::new();
    for v in it {
        b.append_option(v);
    }
    b.finish()
}

fn build_f64(it: impl Iterator<Item = Option<f64>>) -> Float64Array {
    let mut b = Float64Builder::new();
    for v in it {
        b.append_option(v);
    }
    b.finish()
}

/// A resolved numeric column plan: optional leading string ops, exactly one f64
/// parser (the transition), optional trailing f64 array ops.
pub struct NumericPlan {
    string_ops: Vec<(String, NullableKernel)>,
    parser: (String, NumericParser),
    f64_ops: Vec<(String, NumericKernel)>,
    /// The parser is the SYNTHETIC `AsFloat` coerce (a numeric-only chain, no explicit
    /// parser) — emit no manifest record for it.
    synthetic: bool,
}

/// Resolve `ops` to a [`NumericPlan`] iff they form a valid numeric shape:
/// `string* parser f64*`. A numeric ARRAY op (`round`/`clamp`/`abs_value`/`fill_zero`)
/// reached with no explicit parser synthesizes an `AsFloat` coerce (zero-gap: a
/// numeric-only chain runs on the same machinery). Returns `None` if the ops carry no
/// numeric work at all (string-only → the string chains handle it) or the shape is
/// invalid (a string op after the transition, a second parser, etc.).
pub fn resolve_numeric(ops: &[(String, Vec<String>)]) -> Option<NumericPlan> {
    let mut string_ops = Vec::new();
    let mut parser: Option<(String, NumericParser)> = None;
    let mut f64_ops = Vec::new();
    let mut synthetic = false;
    for (name, params) in ops {
        let refs: Vec<&str> = params.iter().map(String::as_str).collect();
        if parser.is_none() {
            if let Some(p) = NumericParser::from_name(name) {
                parser = Some((name.clone(), p));
            } else if let Some(k) = NumericKernel::from_op(name, &refs) {
                // A numeric array op reached with no parser AND no leading string ops ->
                // synthesize the AsFloat coerce (a PURE numeric-only chain). A numeric op
                // AFTER a string op (`["strip","round"]`) is NOT synthesized — string
                // ops on a numeric column are ambiguous, so that config declines to the
                // Polars engine, unchanged.
                if !string_ops.is_empty() {
                    return None;
                }
                parser = Some((String::new(), NumericParser::AsFloat));
                synthetic = true;
                f64_ops.push((name.clone(), k));
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
        synthetic,
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

/// Execute a [`NumericPlan`] over one string column, returning the raw numeric
/// result column ([`NumCol`], Int64/Float64) and the per-op manifest records — all
/// byte-identical to the Polars engine. The CSV path formats the `NumCol` to string
/// (`.fmt()`); the in-memory path egresses its raw array (`.into_array()`).
pub fn run_numeric_column(input: &LargeStringArray, plan: &NumericPlan) -> (NumCol, Vec<OpRecord>) {
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

    // --- parser: string -> Int64/Float64 (null in / unparseable -> null) ---
    let mut cur = plan.parser.1.parse_column(&str_col);
    let before_fmt = str_opts(&str_col);
    // A SYNTHETIC AsFloat coerce emits no record AND is FUSED into the first numeric op:
    // the engine applies `round(cast(f64))` as ONE transform, so that op's before-sample
    // + affected compare against the ORIGINAL string (`'1e2'`), not the coerced float. So
    // seed `cur_fmt` with the pre-parse strings; a real parser seeds it with its output.
    let mut cur_fmt = if plan.synthetic {
        before_fmt.clone()
    } else {
        cur.fmt()
    };
    if !plan.synthetic {
        records.push((
            plan.parser.0.clone(),
            affected(&before_fmt, &cur_fmt),
            total,
            head3(&before_fmt),
            head3(&cur_fmt),
        ));
    }

    // --- f64 array ops (op-by-op so `affected` uses the formatted comparison; the
    // first op promotes an Int64 column to Float64, exactly as Polars does) ---
    for (name, kernel) in &plan.f64_ops {
        let next_arr = apply_chain_f64(&cur.to_f64(), std::slice::from_ref(kernel)).array;
        let next_fmt = fmt_f64(&next_arr);
        records.push((
            name.clone(),
            affected(&cur_fmt, &next_fmt),
            total,
            head3(&cur_fmt),
            head3(&next_fmt),
        ));
        cur = NumCol::F64(next_arr);
        cur_fmt = next_fmt;
    }
    let _ = cur_fmt; // the final formatting is the caller's concern (CSV) — keep raw

    (cur, records)
}

/// Format f64 cells exactly like Polars `cast(Utf8)` (`float_to_polars_string`). The
/// host uses this to stringify a numeric-INPUT dict column (`{"c": [1.55]}` + `["round"]`)
/// so (a) the synthetic `AsFloat` coerce round-trips losslessly and (b) the manifest
/// before-samples + affected counts match the engine — Python `str(float)` is NOT
/// byte-identical to Polars' float format on some values.
#[pyfunction]
pub fn format_f64(vals: Vec<Option<f64>>) -> Vec<Option<String>> {
    vals.into_iter()
        .map(|v| v.map(float_to_polars_string))
        .collect()
}

/// Host gate: is this a config the native numeric columnar path can run? (A valid
/// `string* parser f64*` shape.) The single source of truth the Python
/// `config_is_columnar_ready` calls, so host and kernel never disagree.
#[pyfunction]
pub fn columnar_numeric_ready(ops: Vec<(String, Vec<String>)>) -> bool {
    resolve_numeric(&ops).is_some()
}
