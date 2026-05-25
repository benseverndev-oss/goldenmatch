//! Clustering kernels — behavior-exact replacements for the pure-Python loops
//! in `goldenmatch/core/cluster.py`.
use std::collections::{HashMap, HashSet};

use pyo3::prelude::*;

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

/// Connected components over `all_ids` ∪ edge endpoints. Mirrors
/// `UnionFind.add_many` + `union` loop + `get_clusters` in cluster.py:323-328.
///
/// Component membership is independent of union strategy, so naive union here
/// yields the identical grouping the Python union-by-rank produces. Component
/// and member order is irrelevant: `build_clusters` re-sorts by `min(member)`.
#[pyfunction]
pub fn connected_components(
    edges: Vec<(i64, i64, f64)>,
    all_ids: Vec<i64>,
) -> Vec<Vec<i64>> {
    let mut parent: HashMap<i64, i64> = HashMap::with_capacity(all_ids.len());
    for id in all_ids {
        parent.entry(id).or_insert(id);
    }
    for (a, b, _s) in &edges {
        parent.entry(*a).or_insert(*a);
        parent.entry(*b).or_insert(*b);
    }
    for (a, b, _s) in &edges {
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

/// Count edges whose removal splits the cluster into two >= 2-node components
/// (the "merged by one weak link" pathology). Behavior-exact mirror of
/// `_severe_bridge_count` in cluster.py:168-200. `edges` are the cluster's
/// `pair_scores` keys as `(a, b, score)` (score unused).
#[pyfunction]
pub fn severe_bridge_count(members: Vec<i64>, edges: Vec<(i64, i64, f64)>) -> usize {
    let mut adj: HashMap<i64, Vec<i64>> =
        members.iter().map(|&m| (m, Vec::new())).collect();
    let mut edge_list: Vec<(i64, i64)> = Vec::with_capacity(edges.len());
    for (a, b, _s) in &edges {
        if adj.contains_key(a) && adj.contains_key(b) {
            adj.get_mut(a).unwrap().push(*b);
            adj.get_mut(b).unwrap().push(*a);
            edge_list.push((*a, *b));
        }
    }
    let n = members.len();
    let mut count = 0usize;
    for &(a, b) in &edge_list {
        // BFS/DFS from a with the a-b edge removed; unreachable b => bridge.
        let mut seen: HashSet<i64> = HashSet::new();
        seen.insert(a);
        let mut stack = vec![a];
        while let Some(u) = stack.pop() {
            if let Some(neigh) = adj.get(&u) {
                for &w in neigh {
                    if (u == a && w == b) || (u == b && w == a) {
                        continue;
                    }
                    if seen.insert(w) {
                        stack.push(w);
                    }
                }
            }
        }
        if !seen.contains(&b) {
            let side_a = seen.len();
            if side_a >= 2 && (n - side_a) >= 2 {
                count += 1;
            }
        }
    }
    count
}

/// Confidence metrics for one cluster. Behavior-exact mirror of
/// `compute_cluster_confidence` (cluster.py:413-455). `edges` MUST be passed in
/// `pair_scores` iteration order so the bottleneck-pair tie-break and the
/// average's float-summation order match Python bit-for-bit. Returns
/// `(min_edge, avg_edge, connectivity, bottleneck_pair, confidence)`.
#[pyfunction]
pub fn cluster_confidence(
    edges: Vec<(i64, i64, f64)>,
    size: usize,
) -> (Option<f64>, Option<f64>, f64, Option<(i64, i64)>, f64) {
    if size <= 1 || edges.is_empty() {
        let c = if size <= 1 { 1.0 } else { 0.0 };
        return (None, None, c, None, c);
    }
    let mut min_edge = f64::INFINITY;
    let mut sum = 0.0_f64;
    let mut bottleneck: Option<(i64, i64)> = None;
    for (a, b, s) in &edges {
        sum += *s; // same order as Python sum(scores) -> identical avg
        if *s < min_edge {
            min_edge = *s; // strict < keeps the FIRST min, matching Python min()
            bottleneck = Some((*a, *b));
        }
    }
    let n = edges.len();
    let avg_edge = sum / n as f64;
    let max_possible = (size * (size - 1)) as f64 / 2.0;
    let connectivity = if max_possible > 0.0 {
        n as f64 / max_possible
    } else {
        0.0
    };
    let confidence = 0.4 * min_edge + 0.3 * avg_edge + 0.3 * connectivity;
    (Some(min_edge), Some(avg_edge), connectivity, bottleneck, confidence)
}
