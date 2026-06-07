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

/// Per-column domain size = max interned id + 1 (ids are dense 0..k from the
/// caller's interner). Used to mixed-radix pack a row-tuple into one integer.
fn domains(columns: &[&[u64]]) -> Vec<u128> {
    columns
        .iter()
        .map(|c| c.iter().copied().max().unwrap_or(0) as u128 + 1)
        .collect()
}

/// Exact number of distinct row-tuples over `subset` of the `columns`.
///
/// Fast path: because interned ids are dense and key columns are low-
/// cardinality, the subset's tuples usually fit a mixed-radix pack into a single
/// `u128` (product of domains <= u128::MAX). That lets us count distinct tuples
/// in an allocation-free `FxHashSet<u128>` -- far faster than hashing a boxed
/// slice per row (which made the kernel lose to Polars). Falls back to the boxed
/// tuple only when the packed domain would overflow.
pub fn tuple_distinct_count(columns: &[&[u64]], subset: &[usize]) -> u64 {
    let doms = domains(columns);
    tuple_distinct_count_with(columns, subset, &doms)
}

fn tuple_distinct_count_with(columns: &[&[u64]], subset: &[usize], doms: &[u128]) -> u64 {
    let n_rows = columns.first().map(|c| c.len()).unwrap_or(0);
    if subset.is_empty() || n_rows == 0 {
        return 0;
    }

    // Can the whole tuple be packed into one u128? product of selected domains.
    let mut product: u128 = 1;
    let mut packable = true;
    for &c in subset {
        match product.checked_mul(doms[c]) {
            Some(p) => product = p,
            None => {
                packable = false;
                break;
            }
        }
    }

    if packable {
        let mut seen: FxHashSet<u128> = FxHashSet::default();
        seen.reserve(n_rows);
        #[allow(clippy::needless_range_loop)]
        for r in 0..n_rows {
            let mut packed: u128 = 0;
            for &c in subset {
                packed = packed * doms[c] + columns[c][r] as u128;
            }
            seen.insert(packed);
        }
        return seen.len() as u64;
    }

    // Rare fallback: domains too large to pack. Exact boxed-tuple counting.
    let mut seen: FxHashSet<Box<[u64]>> = FxHashSet::default();
    seen.reserve(n_rows);
    let mut tuple = vec![0u64; subset.len()];
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
///
/// Early-exits on the first violation -- the edge over Polars' two-column
/// `n_unique`, which materializes full distinct counts even for a pair that
/// breaks on row 2. Most candidate pairs are NOT dependencies, so the bail-out
/// dominates the batch discovery below.
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

/// Discover all strict single-column functional dependencies among `columns`:
/// every ordered pair `(det, dep)`, `det != dep`, for which `det -> dep` holds.
///
/// Interning each column once (the caller's job) and reusing it across every
/// pair amortizes the hashing that Polars repeats per pair; combined with the
/// early-exit above this is where the native path beats the vectorized
/// baseline. Trivial pairs are skipped: a constant `dep` (domain 1) is implied
/// by everything, and a unique `det` (all-distinct) implies everything.
pub fn discover_functional_dependencies(columns: &[&[u64]]) -> Vec<(usize, usize)> {
    let n_cols = columns.len();
    let n_rows = columns.first().map(|c| c.len()).unwrap_or(0);
    if n_cols < 2 || n_rows == 0 {
        return Vec::new();
    }
    // Distinct-value count per column (cheap; reused for the trivial-pair skips).
    let distinct: Vec<usize> = columns
        .iter()
        .map(|c| {
            let mut s: FxHashSet<u64> = FxHashSet::default();
            s.extend(c.iter().copied());
            s.len()
        })
        .collect();

    let mut out = Vec::new();
    for det in 0..n_cols {
        if distinct[det] == n_rows {
            continue; // a key determines everything -- trivial
        }
        for dep in 0..n_cols {
            if det == dep || distinct[dep] <= 1 {
                continue; // constant dep is implied by everything -- trivial
            }
            if functional_dependency_holds(columns[det], columns[dep]) {
                out.push((det, dep));
            }
        }
    }
    out
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
    // Compute per-column domains once and reuse across every subset evaluation
    // (the dominant cost otherwise re-scans for the max each time).
    let doms = domains(columns);

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
                if tuple_distinct_count_with(columns, &subset, &doms) == n_rows as u64 {
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
    fn discovers_fds_and_skips_trivial() {
        // col0=zip (1,1,2,3), col1=city (10,10,10,30): zip->city holds; city->zip
        // breaks (city 10 -> zip 1 then 2). col2 constant (skipped as dep).
        // col3 unique (skipped as det).
        let zip = [1u64, 1, 2, 3];
        let city = [10u64, 10, 10, 30];
        let constant = [7u64, 7, 7, 7];
        let uniq = [1u64, 2, 3, 4];
        let cols: Vec<&[u64]> = vec![&zip, &city, &constant, &uniq];
        let fds = discover_functional_dependencies(&cols);
        // zip->city present; city->zip absent; nothing -> constant (col2) since
        // constant is skipped as a dep; uniq (col3) skipped as det.
        assert!(fds.contains(&(0, 1)));
        assert!(!fds.contains(&(1, 0)));
        assert!(fds.iter().all(|&(_, dep)| dep != 2));
        assert!(fds.iter().all(|&(det, _)| det != 3));
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
