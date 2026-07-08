//! Block formation over Arrow string key-fields — the one new kernel the fused
//! match stage needs (score/dedup/cluster already have Arrow-native siblings).
//!
//! `build_block_index_arrow` derives the block key per row, groups by key, and
//! returns `(order, block_sizes)`: `order` is the row indices grouped
//! contiguously by block (feed it to `.take()` on `row_ids` + every field, then
//! call `score_block_pairs_arrow` with `block_sizes`). `block_sizes` are the
//! per-block lengths, all `>= 2` (singleton blocks have no candidate pairs).
//!
//! Byte-parity target: `core/blocker.py::build_blocks` +
//! `_build_block_key_expr` (Polars `concat_str([...], separator="||")` + the
//! `is_not_null & ~strip.lower() in {nan,null,none}` filter + the `size >= 2`
//! drop). The candidate-pair SET this produces is identical to the Polars path;
//! within-block and block ordering differ (Polars `group_by` is unordered) but
//! the scorer emits canonical `(min,max)` pairs, so the pair set is what matters.

use arrow::array::{Array, ArrayData, Int64Builder};
use arrow::pyarrow::PyArrowType;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use rustc_hash::FxHashMap;

use crate::score::StrCol;

/// Block keys whose stripped+lowercased form is one of these are dropped, matching
/// `build_blocks`' sentinel filter. Sentinels are ASCII, so `eq_ignore_ascii_case`
/// against the whitespace-trimmed key is byte-exact with Polars
/// `strip_chars().to_lowercase().is_in([...])` (a non-ASCII char can't fold to an
/// ASCII sentinel, and the ASCII case-fold matches Polars' Unicode lowercase here).
const SENTINELS: [&str; 3] = ["nan", "null", "none"];

#[pyfunction]
pub fn build_block_index_arrow(
    key_fields: Vec<PyArrowType<ArrayData>>,
) -> PyResult<(PyArrowType<ArrayData>, Vec<usize>)> {
    if key_fields.is_empty() {
        return Err(PyValueError::new_err(
            "build_block_index_arrow: at least one key field required",
        ));
    }
    let cols: Vec<StrCol> = key_fields
        .into_iter()
        .map(|p| StrCol::from_data(p.0))
        .collect::<PyResult<_>>()?;

    let n_rows = cols[0].len();
    for (i, c) in cols.iter().enumerate() {
        if c.len() != n_rows {
            return Err(PyValueError::new_err(format!(
                "build_block_index_arrow: field {i} length {} != row count {n_rows}",
                c.len()
            )));
        }
    }

    // Group rows by derived block key, preserving first-appearance order so the
    // output is deterministic (order within a block doesn't affect the pair set).
    // FxHash (not SipHash) since these keys are trusted, not adversarial, and the
    // single-field path borrows `&str` straight from the Arrow buffer (no per-row
    // String alloc) — both matter at 5M rows where std::HashMap<String> lost ~9x
    // to Polars' SIMD group_by.
    let mut groups: Vec<Vec<i64>> = Vec::new();

    if cols.len() == 1 {
        let col = &cols[0];
        let mut index: FxHashMap<&str, usize> = FxHashMap::default();
        index.reserve(n_rows / 4);
        for r in 0..n_rows {
            let key = match col.get(r) {
                Some(s) => s,
                None => continue, // null single-field key -> dropped
            };
            // Sentinel drop: strip+lowercase(key) in {nan,null,none}.
            let stripped = key.trim();
            if SENTINELS.iter().any(|s| stripped.eq_ignore_ascii_case(s)) {
                continue;
            }
            match index.get(key) {
                Some(&gi) => groups[gi].push(r as i64),
                None => {
                    index.insert(key, groups.len());
                    groups.push(vec![r as i64]);
                }
            }
        }
    } else {
        // Multi-field: build the owned concat key (Polars concat_str, "||" sep).
        let mut index: FxHashMap<String, usize> = FxHashMap::default();
        index.reserve(n_rows / 4);
        let mut key = String::new();
        'row: for r in 0..n_rows {
            // concat_str yields null if ANY field is null (ignore_nulls=False
            // default) -> is_not_null drops it, so a null field drops the row.
            key.clear();
            for (fi, col) in cols.iter().enumerate() {
                match col.get(r) {
                    Some(s) => {
                        if fi > 0 {
                            key.push_str("||");
                        }
                        key.push_str(s);
                    }
                    None => continue 'row,
                }
            }
            let stripped = key.trim();
            if SENTINELS.iter().any(|s| stripped.eq_ignore_ascii_case(s)) {
                continue;
            }
            match index.get(key.as_str()) {
                Some(&gi) => groups[gi].push(r as i64),
                None => {
                    index.insert(key.clone(), groups.len());
                    groups.push(vec![r as i64]);
                }
            }
        }
    }

    let mut order = Int64Builder::with_capacity(n_rows);
    let mut block_sizes: Vec<usize> = Vec::new();
    for rows in &groups {
        if rows.len() >= 2 {
            for &r in rows {
                order.append_value(r);
            }
            block_sizes.push(rows.len());
        }
    }

    Ok((PyArrowType(order.finish().into_data()), block_sizes))
}

#[cfg(test)]
mod tests {
    use super::*;
    use arrow::array::StringArray;

    fn col(vals: &[Option<&str>]) -> PyArrowType<ArrayData> {
        PyArrowType(StringArray::from(vals.to_vec()).into_data())
    }

    fn run(fields: Vec<PyArrowType<ArrayData>>) -> (Vec<i64>, Vec<usize>) {
        let (order, sizes) = build_block_index_arrow(fields).unwrap();
        let arr = arrow::array::Int64Array::from(order.0);
        (arr.values().to_vec(), sizes)
    }

    #[test]
    fn groups_and_drops_singletons() {
        // a,a,b,c,a -> block "a" = rows {0,1,4}; b,c singletons dropped.
        let (order, sizes) = run(vec![col(&[
            Some("a"),
            Some("a"),
            Some("b"),
            Some("c"),
            Some("a"),
        ])]);
        assert_eq!(order, vec![0, 1, 4]);
        assert_eq!(sizes, vec![3]);
    }

    #[test]
    fn nulls_and_sentinels_drop() {
        // null field -> drop; literal "NULL"/"nan" (case-insensitive) -> drop.
        let (order, sizes) = run(vec![col(&[
            Some("x"),
            None,
            Some("x"),
            Some("NULL"),
            Some("nan"),
            Some("x"),
        ])]);
        assert_eq!(order, vec![0, 2, 5]);
        assert_eq!(sizes, vec![3]);
    }

    #[test]
    fn multi_field_concat_with_separator() {
        // key = f1||f2. (a,1) twice, (a,2) once -> only (a,1) survives as a block.
        let f1 = col(&[Some("a"), Some("a"), Some("a")]);
        let f2 = col(&[Some("1"), Some("1"), Some("2")]);
        let (order, sizes) = run(vec![f1, f2]);
        assert_eq!(order, vec![0, 1]);
        assert_eq!(sizes, vec![2]);
    }

    #[test]
    fn separator_prevents_ambiguous_join() {
        // ("ab","c") vs ("a","bc"): with "||" separator these are DISTINCT keys
        // ("ab||c" != "a||bc"), so no false block. Both are singletons -> dropped.
        let f1 = col(&[Some("ab"), Some("a")]);
        let f2 = col(&[Some("c"), Some("bc")]);
        let (order, sizes) = run(vec![f1, f2]);
        assert!(order.is_empty());
        assert!(sizes.is_empty());
    }

    #[test]
    fn empty_string_is_a_valid_key() {
        // "" is not null and not a sentinel -> rows with empty key block together.
        let (order, sizes) = run(vec![col(&[Some(""), Some(""), Some("z")])]);
        assert_eq!(order, vec![0, 1]);
        assert_eq!(sizes, vec![2]);
    }
}
