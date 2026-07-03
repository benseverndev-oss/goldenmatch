//! wasm-bindgen wrapper over `goldencheck-core` — the deep-profiling kernels
//! (Benford, composite-key & functional-dependency mining, near-duplicate value
//! clustering) — so the JS/TS port runs the SAME code as the Python
//! `goldencheck-native` wheel. One source of truth; the hand-written TS
//! re-implementations become a pure-TS fallback, and parity is proven by a
//! shared golden fixture. Mirrors the `autoconfig-wasm` shim.
//!
//! JSON in / JSON out (columnar inputs don't fit typed arrays cleanly). Columns
//! arrive as arrays of nullable strings; each is interned to `u64` ids exactly
//! as the native shim's `intern_column` does (null -> 0, first-seen non-null ->
//! 1, 2, 3, …). The FD / composite-key kernels are value-equality based, so the
//! output (column-pair indices, row indices) is independent of the specific
//! hash — identical to the pure-Python reference and the pure-TS port.

use goldencheck_core as gc;
use wasm_bindgen::prelude::*;

/// Intern a column of nullable strings to `u64` ids: null -> 0, else first-seen
/// order 1, 2, 3, … (mirrors `goldencheck-native`'s `intern_column`).
fn intern(col: &[Option<String>]) -> Vec<u64> {
    let mut map: std::collections::HashMap<&str, u64> = std::collections::HashMap::new();
    let mut next: u64 = 1;
    let mut ids = Vec::with_capacity(col.len());
    for v in col {
        match v {
            None => ids.push(0),
            Some(s) => {
                let id = *map.entry(s.as_str()).or_insert_with(|| {
                    let v = next;
                    next += 1;
                    v
                });
                ids.push(id);
            }
        }
    }
    ids
}

fn parse_columns(json: &str) -> Result<Vec<Vec<Option<String>>>, JsError> {
    serde_json::from_str(json).map_err(|e| JsError::new(&format!("bad columns json: {e}")))
}

fn intern_all(columns: &[Vec<Option<String>>]) -> Vec<Vec<u64>> {
    columns.iter().map(|c| intern(c)).collect()
}

fn refs(interned: &[Vec<u64>]) -> Vec<&[u64]> {
    interned.iter().map(|c| c.as_slice()).collect()
}

/// Discover strict single-column FDs `(det_idx, dep_idx)` among the columns.
/// Input: JSON array of columns (each an array of string|null). Output: JSON
/// `[[det, dep], …]`.
#[wasm_bindgen]
pub fn gc_discover_functional_dependencies(columns_json: &str) -> Result<String, JsError> {
    let columns = parse_columns(columns_json)?;
    let interned = intern_all(&columns);
    let out = gc::discover_functional_dependencies(&refs(&interned));
    serde_json::to_string(&out).map_err(|e| JsError::new(&e.to_string()))
}

/// Discover approximate FDs `(det_idx, dep_idx, n_violations)` holding for a
/// fraction of rows in `[min_confidence, 1.0)`. Output: JSON `[[det, dep, nv], …]`.
#[wasm_bindgen]
pub fn gc_discover_approximate_fds(
    columns_json: &str,
    min_confidence: f64,
) -> Result<String, JsError> {
    let columns = parse_columns(columns_json)?;
    let interned = intern_all(&columns);
    let out = gc::discover_approximate_fds(&refs(&interned), min_confidence);
    serde_json::to_string(&out).map_err(|e| JsError::new(&e.to_string()))
}

/// Whether `lhs -> rhs` holds. Inputs: two JSON columns (arrays of string|null).
/// Output: JSON `true` / `false`. Errors if the columns differ in length.
#[wasm_bindgen]
pub fn gc_functional_dependency_holds(lhs_json: &str, rhs_json: &str) -> Result<String, JsError> {
    let lhs: Vec<Option<String>> =
        serde_json::from_str(lhs_json).map_err(|e| JsError::new(&format!("bad lhs: {e}")))?;
    let rhs: Vec<Option<String>> =
        serde_json::from_str(rhs_json).map_err(|e| JsError::new(&format!("bad rhs: {e}")))?;
    if lhs.len() != rhs.len() {
        return Err(JsError::new(
            "functional_dependency_holds: lhs/rhs length mismatch",
        ));
    }
    let l = intern(&lhs);
    let r = intern(&rhs);
    let out = gc::functional_dependency_holds(&l, &r);
    serde_json::to_string(&out).map_err(|e| JsError::new(&e.to_string()))
}

/// Row indices where `dep` deviates from its per-`det`-group mode. Inputs: two
/// JSON columns. Output: JSON `[row, …]`.
#[wasm_bindgen]
pub fn gc_fd_violation_rows(det_json: &str, dep_json: &str) -> Result<String, JsError> {
    let det: Vec<Option<String>> =
        serde_json::from_str(det_json).map_err(|e| JsError::new(&format!("bad det: {e}")))?;
    let dep: Vec<Option<String>> =
        serde_json::from_str(dep_json).map_err(|e| JsError::new(&format!("bad dep: {e}")))?;
    if det.len() != dep.len() {
        return Err(JsError::new("fd_violation_rows: det/dep length mismatch"));
    }
    let d = intern(&det);
    let p = intern(&dep);
    let out = gc::fd_violation_rows(&d, &p);
    serde_json::to_string(&out).map_err(|e| JsError::new(&e.to_string()))
}

/// Search for minimal composite keys (subsets of columns that are jointly
/// unique). Inputs: JSON columns, `max_size`, and a JSON `[bool, …]` marking the
/// individually-unique columns (excluded from candidates). Output: JSON
/// `[[col_idx, …], …]`.
#[wasm_bindgen]
pub fn gc_composite_key_search(
    columns_json: &str,
    max_size: usize,
    single_unique_json: &str,
) -> Result<String, JsError> {
    let columns = parse_columns(columns_json)?;
    let single_unique: Vec<bool> = serde_json::from_str(single_unique_json)
        .map_err(|e| JsError::new(&format!("bad single_unique: {e}")))?;
    if columns.is_empty() {
        return Ok("[]".to_string());
    }
    let interned = intern_all(&columns);
    let n_rows = interned[0].len();
    let out = gc::composite_key_search(&refs(&interned), n_rows, max_size, &single_unique);
    serde_json::to_string(&out).map_err(|e| JsError::new(&e.to_string()))
}

/// Benford leading-digit histogram: `out[i]` = count of finite, strictly-positive
/// values whose leading significant digit is `i+1`. Input: JSON array of numbers
/// (null / non-finite / non-positive are skipped, as the reference does).
/// Output: JSON `[c1, …, c9]`.
#[wasm_bindgen]
pub fn gc_benford_leading_digits(values_json: &str) -> Result<String, JsError> {
    let values: Vec<Option<f64>> =
        serde_json::from_str(values_json).map_err(|e| JsError::new(&format!("bad values: {e}")))?;
    let finite: Vec<f64> = values.into_iter().flatten().collect();
    let out = gc::benford_leading_digits(&finite);
    serde_json::to_string(&out.to_vec()).map_err(|e| JsError::new(&e.to_string()))
}

/// Cluster the distinct `values` into groups of edit-distance-close strings
/// (inconsistent encodings of the same thing). Input: JSON array of strings +
/// `min_similarity`. Output: JSON `[[idx, …], …]` (clusters of size >= 2, each a
/// sorted list of indices into `values`).
#[wasm_bindgen]
pub fn gc_near_duplicate_clusters(
    values_json: &str,
    min_similarity: f64,
) -> Result<String, JsError> {
    let values: Vec<String> =
        serde_json::from_str(values_json).map_err(|e| JsError::new(&format!("bad values: {e}")))?;
    let out = gc::near_duplicate_clusters(&values, min_similarity);
    serde_json::to_string(&out).map_err(|e| JsError::new(&e.to_string()))
}
