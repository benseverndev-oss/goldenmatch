//! Fused exact/near duplicate-row signature scan (backs the `approx_duplicate`
//! relation profiler in `goldencheck.relations.approx_duplicate`).
//!
//! The Python profiler builds, per row, a signature string -- each column
//! `cast(Utf8).fill_null("")`, and string columns additionally normalized
//! (`to_lowercase -> replace_all(r"[^0-9a-z]+"," ") -> strip_chars`) -- joins
//! the columns with `\x1f`, then `group_by(signature).len()` and reports:
//!   - exact duplicate rows: rows whose EXACT signature repeats (count >= 2),
//!   - near duplicate rows: rows whose NORMALIZED signature repeats but whose
//!     exact signature does NOT (near count >= 2 AND exact count < 2).
//!
//! The reported COUNTS depend only on WHICH rows collide (produce equal
//! signatures), not on the literal signature bytes. So this kernel uses its own
//! deterministic cast-to-string and yields identical counts to Polars as long as
//! its value->string map induces the same equality partition. For int/string/
//! bool/date that holds exactly; float Display is injective on finite values and
//! collapses every NaN to one group (matching Polars' single "NaN" group).
//!
//! BLOCKER (see the W3 spec review): this does NOT reuse `intern_column`. Intern
//! gives null a reserved id distinct from any real value, but the profiler does
//! `fill_null("")` -- so a NULL cell and an actual empty string `""` MUST
//! collide (a column `[null, ""]` is ONE exact group). This kernel builds REAL
//! signature strings with `fill_null("")` applied FIRST, so null, `""`, and a
//! string that normalizes to `""` (e.g. `"!!!"`) all collide, matching Polars.
//!
//! `is_string` comes from the CALLER (`[dt == pl.Utf8 for dt in df.dtypes]`),
//! never inferred from `array.data_type()`: a Polars Categorical/Enum is NOT
//! `pl.Utf8` (so it must stay un-normalized) yet `to_arrow()` emits
//! `Dictionary(_, Utf8)`.

use arrow::array::{
    Array, ArrayRef, BooleanArray, Date32Array, Date64Array, DictionaryArray, Float16Array,
    Float32Array, Float64Array, Int16Array, Int32Array, Int64Array, Int8Array, LargeStringArray,
    StringArray, UInt16Array, UInt32Array, UInt64Array, UInt8Array,
};
use arrow::datatypes::{
    DataType, Int16Type, Int32Type, Int64Type, Int8Type, UInt16Type, UInt32Type, UInt64Type,
    UInt8Type,
};
use arrow::error::ArrowError;
use rustc_hash::FxHashMap;

const SEP: char = '\u{1f}'; // unit separator -- matches approx_duplicate.py `_SEP`

/// The four counts the `approx_duplicate` profiler reports.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct DupStats {
    /// Rows whose exact signature repeats (`exact_count >= 2`).
    pub exact_dup_rows: usize,
    /// Distinct exact signatures with `exact_count >= 2`.
    pub exact_dup_groups: usize,
    /// Rows whose normalized signature repeats but whose exact does not.
    pub near_dup_rows: usize,
    /// Distinct normalized signatures among the near-duplicate rows.
    pub near_dup_groups: usize,
}

/// Cast one Arrow column to its per-row `cast(Utf8).fill_null("")` strings.
/// Null slots become `""`. The exact string form only needs to be DETERMINISTIC
/// and injective on distinct non-null values (see the module doc): the reported
/// counts depend on the equality partition, not the literal bytes.
fn cast_column_utf8(array: &dyn Array) -> Result<Vec<String>, ArrowError> {
    let len = array.len();

    macro_rules! display_col {
        ($arrty:ty) => {{
            let arr = array.as_any().downcast_ref::<$arrty>().unwrap();
            let mut out = Vec::with_capacity(len);
            for i in 0..len {
                if arr.is_null(i) {
                    out.push(String::new());
                } else {
                    out.push(arr.value(i).to_string());
                }
            }
            Ok(out)
        }};
    }

    match array.data_type() {
        DataType::Utf8 => {
            let arr = array.as_any().downcast_ref::<StringArray>().unwrap();
            let mut out = Vec::with_capacity(len);
            for i in 0..len {
                if arr.is_null(i) {
                    out.push(String::new());
                } else {
                    out.push(arr.value(i).to_string());
                }
            }
            Ok(out)
        }
        DataType::LargeUtf8 => {
            let arr = array.as_any().downcast_ref::<LargeStringArray>().unwrap();
            let mut out = Vec::with_capacity(len);
            for i in 0..len {
                if arr.is_null(i) {
                    out.push(String::new());
                } else {
                    out.push(arr.value(i).to_string());
                }
            }
            Ok(out)
        }
        DataType::Int8 => display_col!(Int8Array),
        DataType::Int16 => display_col!(Int16Array),
        DataType::Int32 => display_col!(Int32Array),
        DataType::Int64 => display_col!(Int64Array),
        DataType::UInt8 => display_col!(UInt8Array),
        DataType::UInt16 => display_col!(UInt16Array),
        DataType::UInt32 => display_col!(UInt32Array),
        DataType::UInt64 => display_col!(UInt64Array),
        // Rust `{}` Display: shortest round-trip (injective on finite values);
        // any NaN -> "NaN" collapses every NaN to one group, matching Polars.
        DataType::Float16 => {
            let arr = array.as_any().downcast_ref::<Float16Array>().unwrap();
            let mut out = Vec::with_capacity(len);
            for i in 0..len {
                if arr.is_null(i) {
                    out.push(String::new());
                } else {
                    out.push(f64::from(arr.value(i)).to_string());
                }
            }
            Ok(out)
        }
        DataType::Float32 => display_col!(Float32Array),
        DataType::Float64 => display_col!(Float64Array),
        DataType::Boolean => {
            let arr = array.as_any().downcast_ref::<BooleanArray>().unwrap();
            let mut out = Vec::with_capacity(len);
            for i in 0..len {
                if arr.is_null(i) {
                    out.push(String::new());
                } else {
                    out.push(if arr.value(i) { "true" } else { "false" }.to_string());
                }
            }
            Ok(out)
        }
        // Dates: any deterministic injective form works for the count partition.
        // Use the raw epoch value (days for Date32, millis for Date64).
        DataType::Date32 => display_col!(Date32Array),
        DataType::Date64 => display_col!(Date64Array),
        // Dictionary-encoded (Polars Categorical/Enum). Resolve each row to its
        // value's string via the (small) values array, then index. A null key or
        // a null dictionary value -> "".
        DataType::Dictionary(key_type, _) => {
            macro_rules! cast_dict {
                ($kt:ty) => {{
                    let dict = array
                        .as_any()
                        .downcast_ref::<DictionaryArray<$kt>>()
                        .unwrap();
                    let value_strs = cast_column_utf8(dict.values().as_ref())?;
                    let keys = dict.keys();
                    let mut out = Vec::with_capacity(keys.len());
                    for i in 0..keys.len() {
                        if keys.is_null(i) {
                            out.push(String::new());
                        } else {
                            out.push(value_strs[keys.value(i) as usize].clone());
                        }
                    }
                    Ok(out)
                }};
            }
            match key_type.as_ref() {
                DataType::Int8 => cast_dict!(Int8Type),
                DataType::Int16 => cast_dict!(Int16Type),
                DataType::Int32 => cast_dict!(Int32Type),
                DataType::Int64 => cast_dict!(Int64Type),
                DataType::UInt8 => cast_dict!(UInt8Type),
                DataType::UInt16 => cast_dict!(UInt16Type),
                DataType::UInt32 => cast_dict!(UInt32Type),
                DataType::UInt64 => cast_dict!(UInt64Type),
                other => Err(ArrowError::InvalidArgumentError(format!(
                    "duplicate_signatures: unsupported dictionary key type {other:?}"
                ))),
            }
        }
        other => Err(ArrowError::InvalidArgumentError(format!(
            "duplicate_signatures does not support Arrow dtype {other:?}; \
             cast to string/int/float/bool/date first"
        ))),
    }
}

/// Normalize a string cell the way the profiler normalizes `pl.Utf8` columns:
/// `to_lowercase` (Rust std, Unicode-aware -- matches Polars, which is
/// Rust-backed) -> collapse every run of non-`[0-9a-z]` (incl. Unicode) to a
/// single ASCII space -> trim. So `"Acme, Inc."` and `"acme  inc"` -> `"acme
/// inc"`, and `"!!!"` -> `""` (colliding with null / `""`).
fn normalize(s: &str, re: &regex::Regex) -> String {
    let lowered = s.to_lowercase();
    let replaced = re.replace_all(&lowered, " ");
    replaced.trim().to_string()
}

/// Join a row's per-column cells with `\x1f` into one signature string.
fn join_row(cells: &[&str]) -> String {
    let mut sig = String::new();
    for (c, cell) in cells.iter().enumerate() {
        if c > 0 {
            sig.push(SEP);
        }
        sig.push_str(cell);
    }
    sig
}

/// Fused exact/near duplicate-row scan over `columns`. `is_string[c]` (from the
/// caller's `dt == pl.Utf8` mask) selects the columns that get normalized for
/// the near-duplicate signature. Returns the four profiler counts.
pub fn duplicate_signatures(
    columns: &[ArrayRef],
    is_string: &[bool],
) -> Result<DupStats, ArrowError> {
    if columns.is_empty() {
        return Ok(DupStats::default());
    }
    let n_rows = columns[0].len();
    if n_rows == 0 {
        return Ok(DupStats::default());
    }

    // Per-column exact cast strings (fill_null("") applied) and, for string
    // columns, the per-cell normalized form. Non-string columns share one Vec
    // between exact + norm (identical in both signatures).
    let re = regex::Regex::new(r"[^0-9a-z]+")
        .map_err(|e| ArrowError::ComputeError(format!("duplicate_signatures regex: {e}")))?;

    let mut exact_cols: Vec<Vec<String>> = Vec::with_capacity(columns.len());
    let mut norm_cols: Vec<Vec<String>> = Vec::with_capacity(columns.len());
    for (c, col) in columns.iter().enumerate() {
        if col.len() != n_rows {
            return Err(ArrowError::InvalidArgumentError(
                "duplicate_signatures: columns differ in length".into(),
            ));
        }
        let exact = cast_column_utf8(col.as_ref())?;
        let norm = if is_string.get(c).copied().unwrap_or(false) {
            exact.iter().map(|s| normalize(s, &re)).collect()
        } else {
            exact.clone()
        };
        exact_cols.push(exact);
        norm_cols.push(norm);
    }

    // Build per-row signatures + count occurrences.
    let mut exact_sigs: Vec<String> = Vec::with_capacity(n_rows);
    let mut norm_sigs: Vec<String> = Vec::with_capacity(n_rows);
    let mut exact_counts: FxHashMap<String, usize> = FxHashMap::default();
    let mut norm_counts: FxHashMap<String, usize> = FxHashMap::default();
    let mut cells: Vec<&str> = Vec::with_capacity(columns.len());
    for r in 0..n_rows {
        cells.clear();
        for col in &exact_cols {
            cells.push(col[r].as_str());
        }
        let exact_sig = join_row(&cells);

        cells.clear();
        for col in &norm_cols {
            cells.push(col[r].as_str());
        }
        let norm_sig = join_row(&cells);

        *exact_counts.entry(exact_sig.clone()).or_insert(0) += 1;
        *norm_counts.entry(norm_sig.clone()).or_insert(0) += 1;
        exact_sigs.push(exact_sig);
        norm_sigs.push(norm_sig);
    }

    // Reduce to the four counts.
    let mut exact_dup_rows = 0usize;
    let mut near_dup_rows = 0usize;
    let mut exact_dup_group_set: FxHashMap<&str, ()> = FxHashMap::default();
    let mut near_dup_group_set: FxHashMap<&str, ()> = FxHashMap::default();
    for r in 0..n_rows {
        let ec = exact_counts[&exact_sigs[r]];
        let nc = norm_counts[&norm_sigs[r]];
        if ec >= 2 {
            exact_dup_rows += 1;
            exact_dup_group_set.insert(exact_sigs[r].as_str(), ());
        }
        if nc >= 2 && ec < 2 {
            near_dup_rows += 1;
            near_dup_group_set.insert(norm_sigs[r].as_str(), ());
        }
    }

    Ok(DupStats {
        exact_dup_rows,
        exact_dup_groups: exact_dup_group_set.len(),
        near_dup_rows,
        near_dup_groups: near_dup_group_set.len(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Arc;

    fn s(v: Vec<Option<&str>>) -> ArrayRef {
        Arc::new(StringArray::from(v))
    }
    fn i(v: Vec<Option<i64>>) -> ArrayRef {
        Arc::new(Int64Array::from(v))
    }

    #[test]
    fn exact_dups_only() {
        // rows 0 and 2 identical.
        let col = s(vec![Some("a"), Some("b"), Some("a")]);
        let r = duplicate_signatures(&[col], &[true]).unwrap();
        assert_eq!(r.exact_dup_rows, 2);
        assert_eq!(r.exact_dup_groups, 1);
        assert_eq!(r.near_dup_rows, 0);
        assert_eq!(r.near_dup_groups, 0);
    }

    #[test]
    fn near_dups_case_space_punct() {
        // "Acme, Inc." / "acme  inc" / "ACME Inc" all normalize equal but no two
        // are exactly equal -> 3 near-dup rows, 1 group.
        let col = s(vec![
            Some("Acme, Inc."),
            Some("acme  inc"),
            Some("ACME Inc"),
        ]);
        let r = duplicate_signatures(&[col], &[true]).unwrap();
        assert_eq!(r.exact_dup_rows, 0);
        assert_eq!(r.near_dup_rows, 3);
        assert_eq!(r.near_dup_groups, 1);
    }

    #[test]
    fn both_exact_and_near() {
        // rows 0,1 exact "Acme"; rows 2,3 near ("acme"/"ACME") but not exact of
        // each other and not exact of 0,1 ("Acme" != "acme"/"ACME" byte-wise).
        // Exact groups: "Acme" x2. Near: normalized "acme" appears on rows
        // 0,1,2,3 (all normalize to "acme") -> nc=4 for each; near rows = those
        // with nc>=2 AND ec<2 = rows 2,3 (rows 0,1 have ec=2). groups: 1.
        let col = s(vec![Some("Acme"), Some("Acme"), Some("acme"), Some("ACME")]);
        let r = duplicate_signatures(&[col], &[true]).unwrap();
        assert_eq!(r.exact_dup_rows, 2);
        assert_eq!(r.exact_dup_groups, 1);
        assert_eq!(r.near_dup_rows, 2);
        assert_eq!(r.near_dup_groups, 1);
    }

    #[test]
    fn no_dups() {
        let col = s(vec![Some("a"), Some("b"), Some("c")]);
        let r = duplicate_signatures(&[col], &[true]).unwrap();
        assert_eq!(r, DupStats::default());
    }

    #[test]
    fn single_col_int_dups() {
        let col = i(vec![Some(1), Some(2), Some(1), Some(1)]);
        let r = duplicate_signatures(&[col], &[false]).unwrap();
        assert_eq!(r.exact_dup_rows, 3);
        assert_eq!(r.exact_dup_groups, 1);
        // ints are not normalized -> near == exact -> no extra near dups.
        assert_eq!(r.near_dup_rows, 0);
    }

    #[test]
    fn mixed_dtype() {
        // (int, string). rows 0,2 exact ("1","x"); row1 differs.
        let a = i(vec![Some(1), Some(2), Some(1)]);
        let b = s(vec![Some("x"), Some("y"), Some("x")]);
        let r = duplicate_signatures(&[a, b], &[false, true]).unwrap();
        assert_eq!(r.exact_dup_rows, 2);
        assert_eq!(r.exact_dup_groups, 1);
    }

    #[test]
    fn all_null_collide() {
        // Every row is null -> "" -> one exact group of 4.
        let col = s(vec![None, None, None, None]);
        let r = duplicate_signatures(&[col], &[true]).unwrap();
        assert_eq!(r.exact_dup_rows, 4);
        assert_eq!(r.exact_dup_groups, 1);
    }

    #[test]
    fn null_and_empty_string_collide() {
        // null, "", and "!!!" (normalizes to "") must all collide.
        // Exact: null->"" and ""->"" collide (rows 0,1); "!!!" is exact-distinct.
        // Near: "!!!" normalizes to "" too -> nc for "" = 3; rows 0,1 have ec=2
        // (exact dup) so excluded from near; row2 ec=1,nc=3 -> near row. groups 1.
        let col = s(vec![None, Some(""), Some("!!!")]);
        let r = duplicate_signatures(&[col], &[true]).unwrap();
        assert_eq!(r.exact_dup_rows, 2); // rows 0,1 ("" == "")
        assert_eq!(r.exact_dup_groups, 1);
        assert_eq!(r.near_dup_rows, 1); // row2 "!!!" -> "" near-collides
        assert_eq!(r.near_dup_groups, 1);
    }

    #[test]
    fn punct_normalizes_to_empty() {
        // Two all-punct rows -> both normalize to "" -> near collide; exact
        // differ ("!!!" vs "???").
        let col = s(vec![Some("!!!"), Some("???")]);
        let r = duplicate_signatures(&[col], &[true]).unwrap();
        assert_eq!(r.exact_dup_rows, 0);
        assert_eq!(r.near_dup_rows, 2);
        assert_eq!(r.near_dup_groups, 1);
    }

    #[test]
    fn empty_input() {
        let cols: Vec<ArrayRef> = vec![];
        assert_eq!(
            duplicate_signatures(&cols, &[]).unwrap(),
            DupStats::default()
        );
        let col = s(vec![]);
        assert_eq!(
            duplicate_signatures(&[col], &[true]).unwrap(),
            DupStats::default()
        );
    }

    #[test]
    fn bool_col_not_normalized() {
        let col: ArrayRef = Arc::new(BooleanArray::from(vec![
            Some(true),
            Some(false),
            Some(true),
        ]));
        let r = duplicate_signatures(&[col], &[false]).unwrap();
        assert_eq!(r.exact_dup_rows, 2);
        assert_eq!(r.exact_dup_groups, 1);
    }
}
