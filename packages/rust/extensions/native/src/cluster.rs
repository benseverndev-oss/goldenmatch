//! Clustering kernels — behavior-exact replacements for the pure-Python loops
//! in `goldenmatch/core/cluster.py`.
use std::collections::{HashMap, HashSet};

use pyo3::prelude::*;

/// `(min_edge, avg_edge, connectivity, bottleneck_pair, confidence)` — mirrors
/// the dict `compute_cluster_confidence` returns.
type ConfidenceResult = (Option<f64>, Option<f64>, f64, Option<(i64, i64)>, f64);

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
pub fn cluster_confidence(edges: Vec<(i64, i64, f64)>, size: usize) -> ConfidenceResult {
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

// =============================================================================
// build_clusters_native -- post-UF orchestration kernel (prototype).
// =============================================================================
// Subsumes the Python loop in core/cluster.py:cluster.build_clusters from
// "connected_components" through "compute_cluster_confidence" (steps 1-5 of
// the v34 attribution -- 70-75% of cluster wall). The auto_split + quality
// assignment stay in Python on the returned dict.
//
// Spec: docs/superpowers/specs/2026-05-30-cluster-orchestration-kernel-spec.md
// (gitignored; local design notes).

use pyo3::types::{PyDict, PyList, PyTuple};

/// Build cluster dict[int, dict] from raw pair edges + all node IDs.
///
/// Returns a Python dict matching the existing build_clusters output shape:
///   {cluster_id: {
///      "members": list[int],
///      "size": int,
///      "oversized": bool,
///      "pair_scores": dict[tuple[int,int], float],
///      "confidence": float,
///      "bottleneck_pair": tuple[int,int] | None,
///   }}
///
/// Order invariants the Python path depends on:
/// - cluster_id assignment is enumerate(sorted_clusters, start=1) where
///   sorted is by min(member). This kernel preserves that.
/// - pair_scores dict insertion order is the order edges are encountered
///   during the input pair iteration. CPython 3.7+ dicts preserve insertion
///   order; pyo3 PyDict::set_item likewise. The kernel iterates input pairs
///   once and inserts into the destination dict directly, so order matches.
/// - cluster_confidence's bottleneck-pair tie-break is "first minimum wins"
///   which depends on the same insertion order; identical sequence here.
#[pyfunction]
pub fn build_clusters_native<'py>(
    py: Python<'py>,
    pairs: Vec<(i64, i64, f64)>,
    all_ids: Vec<i64>,
    max_cluster_size: usize,
) -> PyResult<Bound<'py, PyDict>> {
    // ---- 1. Union-Find (reuse the find() logic from connected_components). --
    let mut parent: HashMap<i64, i64> = HashMap::with_capacity(
        all_ids.len() + pairs.len() * 2,
    );
    for id in &all_ids {
        parent.entry(*id).or_insert(*id);
    }
    for (a, b, _s) in &pairs {
        parent.entry(*a).or_insert(*a);
        parent.entry(*b).or_insert(*b);
    }
    for (a, b, _s) in &pairs {
        let ra = find(&mut parent, *a);
        let rb = find(&mut parent, *b);
        if ra != rb {
            parent.insert(ra, rb);
        }
    }

    // ---- 2. Group nodes by root; build member_to_cid via the canonical
    //         "sorted by min(member), enumerate from 1" assignment. ----------
    let keys: Vec<i64> = parent.keys().copied().collect();
    let mut root_to_members: HashMap<i64, Vec<i64>> = HashMap::new();
    for k in keys {
        let r = find(&mut parent, k);
        root_to_members.entry(r).or_default().push(k);
    }
    let mut clusters: Vec<Vec<i64>> = root_to_members.into_values().collect();
    // Same key as the Python `sorted(clusters, key=lambda s: min(s))`.
    clusters.sort_by_key(|c| *c.iter().min().expect("non-empty by construction"));

    // member_to_cid: node -> 1-based cluster_id.
    let mut member_to_cid: HashMap<i64, i64> =
        HashMap::with_capacity(parent.len());
    for (idx, members) in clusters.iter().enumerate() {
        let cid = (idx + 1) as i64;
        for &m in members {
            member_to_cid.insert(m, cid);
        }
    }

    // ---- 3. Bucket input edges by cluster_id -- order-preserving Vec. ------
    // We use Vec (not HashMap) so the per-cluster edge ordering matches the
    // order edges appear in `pairs`. This is the invariant cluster_confidence
    // relies on for the bottleneck-pair tie-break.
    let n_clusters = clusters.len();
    let mut per_cluster_edges: Vec<Vec<(i64, i64, f64)>> =
        vec![Vec::new(); n_clusters];
    for (a, b, s) in pairs {
        if let Some(&cid) = member_to_cid.get(&a) {
            // cid is 1-based; per_cluster_edges is 0-indexed.
            per_cluster_edges[(cid - 1) as usize].push((a, b, s));
        }
    }

    // ---- 4. Build the output Python dict. -----------------------------------
    let out = PyDict::new(py);
    for (idx, members) in clusters.iter().enumerate() {
        let cid = (idx + 1) as i64;
        let size = members.len();
        let edges = &per_cluster_edges[idx];

        // Per-cluster sub-dict.
        let sub = PyDict::new(py);

        // members: list[int]. Python no longer sorts (PR #598).
        let members_list = PyList::new(py, members)?;
        sub.set_item("members", members_list)?;
        sub.set_item("size", size)?;
        sub.set_item("oversized", size > max_cluster_size)?;

        // pair_scores: dict[tuple[int,int], float]. Insertion order = edges
        // iteration order = Python's old loop order.
        let pair_scores = PyDict::new(py);
        for &(a, b, s) in edges {
            let key = PyTuple::new(py, [a, b])?;
            pair_scores.set_item(key, s)?;
        }
        sub.set_item("pair_scores", pair_scores)?;

        // confidence + bottleneck_pair via the existing helper.
        let (_min_e, _avg_e, _conn, bn, conf) = cluster_confidence(edges.clone(), size);
        sub.set_item("confidence", conf)?;
        match bn {
            Some((a, b)) => sub.set_item("bottleneck_pair", PyTuple::new(py, [a, b])?)?,
            None => sub.set_item("bottleneck_pair", py.None())?,
        }

        out.set_item(cid, sub)?;
    }

    Ok(out)
}
