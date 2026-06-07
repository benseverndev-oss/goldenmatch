//! Combinatorial key / functional-dependency kernels.
//!
//! These back two things the Python paths cap hard because they are O(pairs x
//! rows) or worse in pure Python:
//!   - **composite-key discovery** (new capability): minimal column subsets that
//!     uniquely identify a row -- `goldencheck/baseline/constraints.py` only
//!     mines *single*-column candidate keys today.
//!   - **functional-dependency mining**: `constraints.py` caps at the 30
//!     lowest-cardinality columns; the native primitive lets the caller raise
//!     that.
//!
//! Columns are passed pre-hashed (`&[u64]` per column, one entry per row) by the
//! `goldencheck-native` shim, which interns each Arrow cell (including a stable
//! sentinel for nulls) to a `u64`. Working on hashed columns keeps this kernel
//! dtype-agnostic and pyo3-free. Counts are exact: row-tuples are compared in
//! full (not just by a combined hash), so there is no collision-driven
//! miscount.

use rustc_hash::{FxHashMap, FxHashSet};

/// Exact number of distinct row-tuples over `subset` of the `columns`.
///
/// `columns[c][r]` is the interned value of column `c` at row `r`; every column
/// must have the same length (the row count). `subset` lists the column indices
/// that form the tuple.
pub fn tuple_distinct_count(columns: &[&[u64]], subset: &[usize]) -> u64 {
    let n_rows = columns.first().map(|c| c.len()).unwrap_or(0);
    if subset.is_empty() || n_rows == 0 {
        return 0;
    }
    let mut seen: FxHashSet<Box<[u64]>> = FxHashSet::default();
    seen.reserve(n_rows);
    let mut tuple = vec![0u64; subset.len()];
    // `r` indexes every selected column in lock-step; a range loop is the
    // natural form here (not a single-column iterator).
    #[allow(clippy::needless_range_loop)]
    for r in 0..n_rows {
        for (slot, &c) in tuple.iter_mut().zip(subset.iter()) {
            *slot = columns[c][r];
        }
        seen.insert(tuple.clone().into_boxed_slice());
    }
    seen.len() as u64
}

/// Whether `lhs -> rhs` holds: every distinct `lhs` value maps to exactly one
/// `rhs` value across all rows. Both slices must have the same length.
pub fn functional_dependency_holds(lhs: &[u64], rhs: &[u64]) -> bool {
    debug_assert_eq!(lhs.len(), rhs.len());
    let mut map: FxHashMap<u64, u64> = FxHashMap::default();
    map.reserve(lhs.len());
    for (&l, &r) in lhs.iter().zip(rhs.iter()) {
        match map.get(&l) {
            Some(&existing) if existing != r => return false,
            Some(_) => {}
            None => {
                map.insert(l, r);
            }
        }
    }
    true
}

/// Search for **minimal** composite keys: column subsets of size `2..=max_size`
/// whose row-tuples are all distinct (i.e. uniquely identify a row), excluding
/// any subset that contains an already-unique single column or a smaller key
/// already found (minimality -- we don't report supersets of a key).
///
/// `single_unique[c]` marks columns that are already unique on their own (the
/// caller detects these cheaply and reports them as simple candidate keys); we
/// skip subsets touching them so composite results are genuinely *new*
/// information. Returns subsets as sorted column-index vectors.
pub fn composite_key_search(
    columns: &[&[u64]],
    n_rows: usize,
    max_size: usize,
    single_unique: &[bool],
) -> Vec<Vec<usize>> {
    let n_cols = columns.len();
    if n_rows == 0 || n_cols < 2 || max_size < 2 {
        return Vec::new();
    }
    // Candidate columns: not individually unique, and not constant (a constant
    // column can never help form a key).
    let candidates: Vec<usize> = (0..n_cols)
        .filter(|&c| !single_unique.get(c).copied().unwrap_or(false))
        .collect();

    let mut found: Vec<Vec<usize>> = Vec::new();
    let cap = max_size.min(candidates.len());

    // BFS over subset sizes so we find the smallest keys first; prune any
    // subset that is a superset of a key already found.
    let mut frontier: Vec<Vec<usize>> = candidates.iter().map(|&c| vec![c]).collect();
    for _size in 2..=cap {
        let mut next: Vec<Vec<usize>> = Vec::new();
        for base in &frontier {
            let last = *base.last().unwrap();
            for &c in &candidates {
                if c <= last {
                    continue; // keep subsets sorted + dedup'd
                }
                let mut subset = base.clone();
                subset.push(c);
                // Prune supersets of an already-found minimal key.
                if found.iter().any(|k| k.iter().all(|x| subset.contains(x))) {
                    continue;
                }
                if tuple_distinct_count(columns, &subset) == n_rows as u64 {
                    found.push(subset);
                } else {
                    next.push(subset);
                }
            }
        }
        if next.is_empty() {
            break;
        }
        frontier = next;
    }
    found
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn distinct_count_pairs() {
        let a = [1u64, 1, 2, 2];
        let b = [10u64, 20, 10, 10];
        let cols: Vec<&[u64]> = vec![&a, &b];
        // tuples: (1,10),(1,20),(2,10),(2,10) -> 3 distinct
        assert_eq!(tuple_distinct_count(&cols, &[0, 1]), 3);
        assert_eq!(tuple_distinct_count(&cols, &[0]), 2);
    }

    #[test]
    fn fd_holds_and_breaks() {
        // city -> country holds; country -> city breaks.
        let city = [1u64, 2, 3, 1];
        let country = [9u64, 9, 8, 9];
        assert!(functional_dependency_holds(&city, &country));
        assert!(!functional_dependency_holds(&country, &city));
    }

    #[test]
    fn finds_minimal_composite_key() {
        // Neither col unique alone; (col0,col1) is a key.
        let a = [1u64, 1, 2, 2];
        let b = [10u64, 20, 10, 20];
        let cols: Vec<&[u64]> = vec![&a, &b];
        let keys = composite_key_search(&cols, 4, 3, &[false, false]);
        assert_eq!(keys, vec![vec![0, 1]]);
    }

    #[test]
    fn skips_unique_columns() {
        let a = [1u64, 2, 3, 4]; // unique alone
        let b = [10u64, 10, 20, 20];
        let cols: Vec<&[u64]> = vec![&a, &b];
        let keys = composite_key_search(&cols, 4, 3, &[true, false]);
        assert!(keys.is_empty());
    }
}
