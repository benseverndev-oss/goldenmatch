//! Pyo3-free graph kernels. Behavior-exact extraction of the loops that lived in
//! `native/src/{cluster,pairs}.rs`; the `native` crate keeps thin `#[pyfunction]`
//! shims delegating here (one source of truth, like `score-core`).
use std::collections::BTreeMap;
use std::collections::HashMap;

/// Canonicalize each pair as `(min,max)` and keep the max score per pair.
/// Behavior-exact port of `native::pairs::dedup_pairs_max_score`.
pub fn dedup_pairs_max_score(pairs: &[(i64, i64, f64)]) -> Vec<(i64, i64, f64)> {
    let mut best: BTreeMap<(i64, i64), f64> = BTreeMap::new();
    for &(a, b, s) in pairs {
        let key = if a <= b { (a, b) } else { (b, a) };
        match best.get(&key) {
            Some(&cur) if s <= cur => {}
            _ => {
                best.insert(key, s);
            }
        }
    }
    best.into_iter().map(|((a, b), s)| (a, b, s)).collect()
}

/// Iterative find with path compression over a `HashMap` parent table.
fn find(parent: &mut HashMap<i64, i64>, x: i64) -> i64 {
    let mut root = x;
    while let Some(&p) = parent.get(&root) {
        if p == root {
            break;
        }
        root = p;
    }
    // Path-compress x..root.
    let mut cur = x;
    while let Some(&p) = parent.get(&cur) {
        if p == root {
            break;
        }
        parent.insert(cur, root);
        cur = p;
    }
    root
}

/// Connected components over `all_ids` ∪ edge endpoints. Behavior-exact port of
/// `native::cluster::connected_components`. Component membership is independent
/// of union strategy, so naive union yields the identical grouping the Python
/// union-by-rank produces. Component and member order is irrelevant.
pub fn connected_components(edges: &[(i64, i64, f64)], all_ids: &[i64]) -> Vec<Vec<i64>> {
    let mut parent: HashMap<i64, i64> = HashMap::with_capacity(all_ids.len());
    for &id in all_ids {
        parent.entry(id).or_insert(id);
    }
    for (a, b, _s) in edges {
        parent.entry(*a).or_insert(*a);
        parent.entry(*b).or_insert(*b);
    }
    for (a, b, _s) in edges {
        let ra = find(&mut parent, *a);
        let rb = find(&mut parent, *b);
        if ra != rb {
            parent.insert(ra, rb);
        }
    }
    let keys: Vec<i64> = parent.keys().copied().collect();
    let mut groups: HashMap<i64, Vec<i64>> = HashMap::new();
    for k in keys {
        let r = find(&mut parent, k);
        groups.entry(r).or_default().push(k);
    }
    groups.into_values().collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn dedup_keeps_max_and_canonicalizes() {
        let got = dedup_pairs_max_score(&[(2, 1, 0.5), (1, 2, 0.9), (3, 3, 0.1)]);
        assert_eq!(got, vec![(1, 2, 0.9), (3, 3, 0.1)]);
    }

    #[test]
    fn cc_groups_transitive_and_includes_singletons() {
        let comps = connected_components(&[(1, 2, 0.9), (2, 3, 0.8)], &[1, 2, 3, 4]);
        let mut sorted: Vec<Vec<i64>> = comps.iter().map(|c| { let mut v = c.clone(); v.sort(); v }).collect();
        sorted.sort();
        assert_eq!(sorted, vec![vec![1, 2, 3], vec![4]]);
    }
}
