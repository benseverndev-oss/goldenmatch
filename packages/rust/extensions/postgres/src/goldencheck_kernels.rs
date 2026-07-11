//! Native-direct (no CPython) GoldenCheck deep-profiling SQL functions.
//!
//! These call the pyo3-free `goldencheck-core` crate directly — the same
//! reference kernel the `goldencheck[native]` wheel and the DuckDB
//! `goldencheck_*` UDFs run — so the values are identical across every surface
//! (Python / DuckDB / Postgres). They complete the GoldenCheck row of the
//! cross-surface parity roadmap (P5): benford histogram, near-duplicate value
//! clusters, strict + approximate functional dependencies, and composite keys.
//!
//! Shape: like `goldenmatch_hnsw_pairs`, the multi-column functions take a
//! **column-major flat** `text[]` plus an `n_cols` count (pgrx flattens
//! multidim arrays, so a flat array + a dimension is the portable idiom). Lay
//! the columns out end-to-end — all of column 0's rows, then column 1's, … —
//! each column the same length. Interning (`NULL -> 0`, values first-seen
//! `1,2,…`) is done here in pure Rust; the kernels depend only on value
//! equality, so this matches the wheel's Arrow-side interning value-for-value.
//!
//! ```sql
//! SELECT goldenmatch.goldencheck_benford(ARRAY[1,1,2,11,19]::double precision[]);
//! SELECT * FROM goldenmatch.goldencheck_discover_fds(
//!     ARRAY['1','1','2','2', 'A','A','B','B'], 2);  -- 2 columns of 4 rows
//! ```
use pgrx::prelude::*;

/// First-seen interning matching GoldenCheck's `_intern` + the native shim:
/// `NULL -> 0`, distinct values -> `1, 2, …` in first-seen order. The kernels
/// only compare ids for equality, so any consistent scheme (nulls all equal)
/// yields identical output; first-seen keeps it byte-identical to the wheel.
fn intern_column(col: &[Option<String>]) -> Vec<u64> {
    let mut map: std::collections::HashMap<&str, u64> = std::collections::HashMap::new();
    let mut ids = Vec::with_capacity(col.len());
    let mut next: u64 = 1;
    for v in col {
        match v {
            None => ids.push(0),
            Some(s) => {
                let id = *map.entry(s.as_str()).or_insert_with(|| {
                    let id = next;
                    next += 1;
                    id
                });
                ids.push(id);
            }
        }
    }
    ids
}

/// Reshape a column-major flat `text[]` into `n_cols` interned columns. Returns
/// `None` on a shape mismatch (empty, `n_cols == 0`, or length not divisible).
fn interned_columns(flat: &[Option<String>], n_cols: usize) -> Option<Vec<Vec<u64>>> {
    if n_cols == 0 || flat.is_empty() || flat.len() % n_cols != 0 {
        return None;
    }
    let n_rows = flat.len() / n_cols;
    Some(
        (0..n_cols)
            .map(|c| intern_column(&flat[c * n_rows..(c + 1) * n_rows]))
            .collect(),
    )
}

/// Leading-digit (1..9) histogram of the numeric `values`. Returns a 9-element
/// `bigint[]`: element `d-1` is the count of values whose first significant
/// digit is `d` (non-positive / non-finite values skipped). The SQL counterpart
/// to GoldenCheck's Benford conformance check; same value the Python + DuckDB
/// surfaces produce.
#[pg_extern]
pub fn goldencheck_benford(values: Vec<f64>) -> Vec<i64> {
    goldencheck_core::benford_leading_digits_slice(&values)
        .iter()
        .map(|&c| c as i64)
        .collect()
}

/// Cluster near-duplicate string `values` (e.g. `California` / `Californa` /
/// `CALIFORNIA`). Returns `(cluster, member)` rows: one row per clustered
/// element, `member` being its 0-based position in `values`; singletons are
/// omitted. NULL elements are treated as the empty string. Same trigram+prefix
/// blocking + Levenshtein-ratio metric as the `fuzzy_duplicate_values` check.
#[pg_extern]
pub fn goldencheck_near_duplicates(
    values: Vec<Option<String>>,
    min_similarity: f64,
) -> TableIterator<'static, (name!(cluster, i64), name!(member, i64))> {
    let strings: Vec<String> = values.into_iter().map(|v| v.unwrap_or_default()).collect();
    let clusters = goldencheck_core::near_duplicate_clusters(&strings, min_similarity);
    let rows: Vec<(i64, i64)> = clusters
        .into_iter()
        .enumerate()
        .flat_map(|(ci, members)| members.into_iter().map(move |m| (ci as i64, m as i64)))
        .collect();
    TableIterator::new(rows)
}

/// Discover strict single-column functional dependencies among the columns in a
/// column-major flat `text[]` (`n_cols` columns of equal length). `det -> dep`
/// holds iff every value of column `det` maps to exactly one value of column
/// `dep`. Returns `(det, dep)` 0-based column-index pairs; trivial pairs (unique
/// determinant, constant dependent) are skipped. Same result as the
/// `functional_dependency` profiler.
#[pg_extern]
pub fn goldencheck_discover_fds(
    flat: Vec<Option<String>>,
    n_cols: i32,
) -> TableIterator<'static, (name!(det, i64), name!(dep, i64))> {
    let cols = match interned_columns(&flat, n_cols.max(0) as usize) {
        Some(c) => c,
        None => return TableIterator::new(Vec::new()),
    };
    let refs: Vec<&[u64]> = cols.iter().map(Vec::as_slice).collect();
    let pairs = goldencheck_core::discover_functional_dependencies(&refs);
    TableIterator::new(
        pairs
            .into_iter()
            .map(|(i, j)| (i as i64, j as i64))
            .collect::<Vec<_>>(),
    )
}

/// Discover *near*-strict functional dependencies and count their violations.
/// Returns `(det, dep, violations)`: `det -> dep` holds for at least
/// `min_confidence` of rows (but not all), with `violations` rows breaking the
/// pattern. Same first-seen interning + mode tie-break + average-group guard as
/// the `fd_violation` profiler.
#[pg_extern]
pub fn goldencheck_discover_approx_fds(
    flat: Vec<Option<String>>,
    n_cols: i32,
    min_confidence: f64,
) -> TableIterator<'static, (name!(det, i64), name!(dep, i64), name!(violations, i64))> {
    let cols = match interned_columns(&flat, n_cols.max(0) as usize) {
        Some(c) => c,
        None => return TableIterator::new(Vec::new()),
    };
    let refs: Vec<&[u64]> = cols.iter().map(Vec::as_slice).collect();
    let triples = goldencheck_core::discover_approximate_fds(&refs, min_confidence);
    TableIterator::new(
        triples
            .into_iter()
            .map(|(i, j, v)| (i as i64, j as i64, v as i64))
            .collect::<Vec<_>>(),
    )
}

/// Find minimal composite keys (column subsets of size 2..`max_size` whose
/// tuples are all distinct) among the columns in a column-major flat `text[]`.
/// Constant columns and columns that are a key on their own are excluded first
/// (a single-column key needs no composite), then the minimal-subset search
/// runs. Returns `(key_id, col_index)` rows: one row per column in each key,
/// `col_index` being the 0-based position in the input columns. Mirrors the
/// `composite_key` profiler's search.
#[pg_extern]
pub fn goldencheck_composite_keys(
    flat: Vec<Option<String>>,
    n_cols: i32,
    max_size: i32,
) -> TableIterator<'static, (name!(key_id, i64), name!(col_index, i64))> {
    let cols = match interned_columns(&flat, n_cols.max(0) as usize) {
        Some(c) => c,
        None => return TableIterator::new(Vec::new()),
    };
    let n_rows = cols[0].len();
    if n_rows < 2 {
        return TableIterator::new(Vec::new());
    }
    // Drop constant (can't help a key) and single-unique (is the key alone)
    // columns — the shared candidate contract with the DuckDB/Python surface.
    let cand_orig: Vec<usize> = cols
        .iter()
        .enumerate()
        .filter(|(_, c)| {
            let nu = c
                .iter()
                .copied()
                .collect::<std::collections::HashSet<u64>>()
                .len();
            nu > 1 && nu < n_rows
        })
        .map(|(i, _)| i)
        .collect();
    if cand_orig.len() < 2 {
        return TableIterator::new(Vec::new());
    }
    let cand: Vec<&[u64]> = cand_orig.iter().map(|&i| cols[i].as_slice()).collect();
    let single_unique = vec![false; cand.len()];
    let keys = goldencheck_core::composite_key_search(
        &cand,
        n_rows,
        max_size.max(0) as usize,
        &single_unique,
    );
    let rows: Vec<(i64, i64)> = keys
        .into_iter()
        .enumerate()
        .flat_map(|(ki, key)| {
            let mut orig: Vec<usize> = key.into_iter().map(|li| cand_orig[li]).collect();
            orig.sort_unstable();
            orig.into_iter().map(move |ci| (ki as i64, ci as i64))
        })
        .collect();
    TableIterator::new(rows)
}

#[cfg(any(test, feature = "pg_test"))]
#[pgrx::pg_schema]
mod tests {
    use pgrx::prelude::*;

    /// Benford histogram of a pinned vector, shared with the Python + DuckDB
    /// surfaces: 1->5, 2->1, 3->1, 7->1, 9->2.
    #[pg_test]
    fn benford_matches_pinned() {
        let hist = crate::goldencheck_kernels::goldencheck_benford(vec![
            1.0, 1.0, 2.0, 11.0, 19.0, 3.0, 100.0, 7.0, 9.0, 9.0,
        ]);
        assert_eq!(hist, vec![5, 1, 1, 0, 0, 0, 1, 0, 2]);
    }

    /// Strict FD discovery over 2 columns of 4 rows (zip -> city and back).
    #[pg_test]
    fn discover_fds_finds_both_directions() {
        let flat: Vec<Option<String>> = ["1", "1", "2", "2", "A", "A", "B", "B"]
            .iter()
            .map(|s| Some(s.to_string()))
            .collect();
        let rows: Vec<(i64, i64)> =
            crate::goldencheck_kernels::goldencheck_discover_fds(flat, 2).collect();
        assert!(rows.contains(&(0, 1)));
        assert!(rows.contains(&(1, 0)));
    }

    /// Composite key: (order, line) over 3 columns of 5 rows; neither is unique
    /// alone, so {0,1} is a minimal composite key.
    #[pg_test]
    fn composite_keys_finds_order_line() {
        let cols = [
            ["o1", "o1", "o2", "o2", "o3"],
            ["1", "2", "1", "2", "1"],
            ["mon", "mon", "tue", "tue", "wed"],
        ];
        let mut flat: Vec<Option<String>> = Vec::new();
        for c in &cols {
            for v in c {
                flat.push(Some(v.to_string()));
            }
        }
        let rows: Vec<(i64, i64)> =
            crate::goldencheck_kernels::goldencheck_composite_keys(flat, 3, 3).collect();
        // key_id 0 groups columns {0,1}.
        let key0: Vec<i64> = rows
            .iter()
            .filter(|(k, _)| *k == 0)
            .map(|(_, c)| *c)
            .collect();
        assert_eq!(key0, vec![0, 1]);
    }
}
