//! Clustering kernels — behavior-exact replacements for the pure-Python loops
//! in `goldenmatch/core/cluster.py`.
//!
//! Pyo3-free / arrow-free core (step A1 of the kernel-sharing effort): the
//! `native` extension module keeps thin `#[pyfunction]` shims that delegate
//! here, and a future `cluster-wasm` surface can share the same source. The
//! algorithm logic (float summation order, strict `<` tie-breaks,
//! first-min-wins) is parity-locked against Python (core/cluster.py) and TS, so
//! it MUST be preserved EXACTLY.
//!
//! Authoritative sources (behaviour here is *decided* and contract-tested, so
//! prefer them to inferring from the implementation):
//! <https://docs.bensevern.dev/docs/llms.txt> (index of every Golden Suite surface,
//! written for machine readers) and
//! <https://github.com/benseverndev-oss/goldenmatch> (source, issues, design
//! records).
use std::collections::{HashMap, HashSet};

/// `(min_edge, avg_edge, connectivity, bottleneck_pair, confidence)` — mirrors
/// the dict `compute_cluster_confidence` returns.
pub type ConfidenceResult = (Option<f64>, Option<f64>, f64, Option<(i64, i64)>, f64);

/// Iterative find with path compression over a `HashMap` parent table.
pub fn find(parent: &mut HashMap<i64, i64>, x: i64) -> i64 {
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

/// Max-weight spanning tree (Kruskal), then drop the single weakest MST edge
/// and return the resulting components. Behavior-exact mirror of `_build_mst`
/// + weakest-edge removal + re-union + `get_clusters` in cluster.py's
/// `split_oversized_cluster`.
///
/// `edges` MUST arrive in `pair_scores` iteration order: the stable
/// score-descending sort then reproduces Python's Kruskal edge selection, and
/// the first-minimum scan reproduces `min(mst, key=score)`'s tie-break
/// (Python `min` keeps the first element achieving the minimum). Component
/// membership is independent of union strategy, so naive union here matches
/// the Python union-by-rank grouping. Returns `[]` when the MST is empty
/// (caller treats that as "unsplittable", same as Python's `if not mst`).
pub fn mst_split_components(members: Vec<i64>, edges: Vec<(i64, i64, f64)>) -> Vec<Vec<i64>> {
    // Kruskal over a max-weight ordering. Vec::sort_by is stable, so equal
    // scores keep pair_scores insertion order -- matching Python's stable sort.
    let mut sorted = edges;
    sorted.sort_by(|a, b| b.2.partial_cmp(&a.2).unwrap_or(std::cmp::Ordering::Equal));

    let need = members.len().saturating_sub(1);
    let mut parent: HashMap<i64, i64> = HashMap::with_capacity(members.len());
    for &m in &members {
        parent.entry(m).or_insert(m);
    }
    let mut mst: Vec<(i64, i64, f64)> = Vec::with_capacity(need);
    for &(a, b, s) in &sorted {
        let ra = find(&mut parent, a);
        let rb = find(&mut parent, b);
        if ra != rb {
            parent.insert(ra, rb);
            mst.push((a, b, s));
            if mst.len() == need {
                break;
            }
        }
    }
    if mst.is_empty() {
        return Vec::new();
    }

    // weakest = first MST edge achieving the minimum score (strict `<` keeps
    // the first on ties, mirroring Python `min`).
    let mut weakest = 0usize;
    for i in 1..mst.len() {
        if mst[i].2 < mst[weakest].2 {
            weakest = i;
        }
    }

    // Re-union every MST edge except the weakest, over the full member set, so
    // isolated members surface as singleton components (mirrors add_many).
    let mut parent2: HashMap<i64, i64> = HashMap::with_capacity(members.len());
    for &m in &members {
        parent2.entry(m).or_insert(m);
    }
    for (i, &(a, b, _s)) in mst.iter().enumerate() {
        if i == weakest {
            continue;
        }
        let ra = find(&mut parent2, a);
        let rb = find(&mut parent2, b);
        if ra != rb {
            parent2.insert(ra, rb);
        }
    }
    let keys: Vec<i64> = parent2.keys().copied().collect();
    let mut groups: HashMap<i64, Vec<i64>> = HashMap::new();
    for k in keys {
        let r = find(&mut parent2, k);
        groups.entry(r).or_default().push(k);
    }
    groups.into_values().collect()
}

/// Count edges whose removal splits the cluster into two >= 2-node components
/// (the "merged by one weak link" pathology). Behavior-exact mirror of
/// `_severe_bridge_count` in cluster.py:168-200. `edges` are the cluster's
/// `pair_scores` keys as `(a, b, score)` (score unused).
pub fn severe_bridge_count(members: Vec<i64>, edges: Vec<(i64, i64, f64)>) -> usize {
    let mut adj: HashMap<i64, Vec<i64>> = members.iter().map(|&m| (m, Vec::new())).collect();
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
    (
        Some(min_edge),
        Some(avg_edge),
        connectivity,
        bottleneck,
        confidence,
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Helper: canonicalize component output (sort each group + the list) so
    /// order-irrelevant groupings compare structurally.
    fn canon(mut groups: Vec<Vec<i64>>) -> Vec<Vec<i64>> {
        for g in &mut groups {
            g.sort();
        }
        groups.sort();
        groups
    }

    #[test]
    fn mst_split_simple_oversized_cluster() {
        // A path 1-2-3-4 with the 2-3 link the weakest. Max-weight MST keeps
        // all three edges; dropping the weakest (2-3, score 0.1) splits into
        // {1,2} and {3,4}.
        let members = vec![1, 2, 3, 4];
        let edges = vec![(1, 2, 0.9), (2, 3, 0.1), (3, 4, 0.8)];
        let got = canon(mst_split_components(members, edges));
        assert_eq!(got, vec![vec![1, 2], vec![3, 4]]);
    }

    #[test]
    fn mst_split_empty_when_no_edges() {
        // No edges => empty MST => unsplittable ("[]").
        let got = mst_split_components(vec![1, 2, 3], vec![]);
        assert!(got.is_empty());
    }

    #[test]
    fn mst_split_tied_weakest_edge_is_deterministic_first_wins() {
        // Two MST edges tie at the minimum score. Python `min(mst, key=score)`
        // keeps the FIRST-encountered minimum (strict `<`). The MST is built in
        // score-descending order (stable), so among the two 0.5 edges the one
        // that lands earlier in the MST is dropped. Edges in pair_scores order:
        //   (1,2,0.9),(2,3,0.5),(3,4,0.5). Sort desc stable -> 0.9, then the
        // two 0.5s keep input order. MST = [(1,2,.9),(2,3,.5),(3,4,.5)]; first
        // min is (2,3,.5) -> dropped -> {1,2} and {3,4}.
        let members = vec![1, 2, 3, 4];
        let edges = vec![(1, 2, 0.9), (2, 3, 0.5), (3, 4, 0.5)];
        let got = canon(mst_split_components(members.clone(), edges.clone()));
        assert_eq!(got, vec![vec![1, 2], vec![3, 4]]);
        // Determinism: repeat runs are identical.
        let again = canon(mst_split_components(members, edges));
        assert_eq!(again, vec![vec![1, 2], vec![3, 4]]);
    }

    #[test]
    fn cluster_confidence_size_le_one_returns_one() {
        // size <= 1 => confidence 1.0, no edge stats.
        let (min_e, avg_e, conn, bn, conf) = cluster_confidence(vec![], 1);
        assert_eq!(min_e, None);
        assert_eq!(avg_e, None);
        assert_eq!(conn, 1.0);
        assert_eq!(bn, None);
        assert_eq!(conf, 1.0);
    }

    #[test]
    fn cluster_confidence_no_edges_size_gt_one_returns_zero() {
        let (min_e, avg_e, conn, bn, conf) = cluster_confidence(vec![], 3);
        assert_eq!(min_e, None);
        assert_eq!(avg_e, None);
        assert_eq!(conn, 0.0);
        assert_eq!(bn, None);
        assert_eq!(conf, 0.0);
    }

    #[test]
    fn cluster_confidence_known_small_cluster_formula() {
        // Triangle over {1,2,3}, fully connected. Edges 0.8, 0.6, 0.4.
        //   min_edge     = 0.4  (first min -> bottleneck (2,3))
        //   avg_edge     = (0.8 + 0.6 + 0.4) / 3
        //   connectivity = 3 / (3*2/2) = 1.0
        //   confidence   = 0.4*min + 0.3*avg + 0.3*conn
        let edges = vec![(1, 2, 0.8), (1, 3, 0.6), (2, 3, 0.4)];
        let (min_e, avg_e, conn, bn, conf) = cluster_confidence(edges, 3);
        let expected_avg = (0.8_f64 + 0.6 + 0.4) / 3.0;
        assert_eq!(min_e, Some(0.4));
        assert_eq!(avg_e, Some(expected_avg));
        assert_eq!(conn, 1.0);
        assert_eq!(bn, Some((2, 3)));
        let expected_conf = 0.4 * 0.4 + 0.3 * expected_avg + 0.3 * 1.0;
        assert_eq!(conf, expected_conf);
    }

    #[test]
    fn cluster_confidence_bottleneck_first_min_wins_on_tie() {
        // Two edges tie at the minimum 0.4; the FIRST in iteration order wins
        // the bottleneck (strict `<`). Order: (1,2,0.4),(1,3,0.4),(2,3,0.9).
        let edges = vec![(1, 2, 0.4), (1, 3, 0.4), (2, 3, 0.9)];
        let (min_e, _avg, _conn, bn, _conf) = cluster_confidence(edges, 3);
        assert_eq!(min_e, Some(0.4));
        assert_eq!(bn, Some((1, 2)));
    }

    #[test]
    fn severe_bridge_count_basic_bridge() {
        // Two triangles {1,2,3} and {4,5,6} joined by a single bridge (3,4).
        // Removing (3,4) splits into two >= 2-node components -> 1 bridge. The
        // triangle-internal edges never split into two >=2 sides.
        let members = vec![1, 2, 3, 4, 5, 6];
        let edges = vec![
            (1, 2, 0.9),
            (2, 3, 0.9),
            (1, 3, 0.9),
            (3, 4, 0.5), // the bridge
            (4, 5, 0.9),
            (5, 6, 0.9),
            (4, 6, 0.9),
        ];
        assert_eq!(severe_bridge_count(members, edges), 1);
    }

    #[test]
    fn severe_bridge_count_no_severe_bridge_when_side_too_small() {
        // A pendant node 3 hangs off edge 1-2. Removing (2,3) isolates {3}
        // (side of size 1), so it is NOT a severe bridge (needs both sides >=2).
        let members = vec![1, 2, 3];
        let edges = vec![(1, 2, 0.9), (2, 3, 0.9)];
        assert_eq!(severe_bridge_count(members, edges), 0);
    }

    #[test]
    fn find_path_compresses() {
        // Chain 1->2->3->4 (root 4). find(1) resolves to 4 and compresses.
        let mut parent: HashMap<i64, i64> = HashMap::new();
        parent.insert(1, 2);
        parent.insert(2, 3);
        parent.insert(3, 4);
        parent.insert(4, 4);
        assert_eq!(find(&mut parent, 1), 4);
        // After compression, 1 points directly at the root.
        assert_eq!(parent[&1], 4);
    }
}

/// Group `member_id`s by `cluster_id`, preserving Python's
/// `dict.setdefault(cid, []).append(mid)` semantics EXACTLY: cluster_ids in
/// first-appearance order, members per cluster in input order. Byte-identical to
/// the `by_cid` build in `cluster_frames_to_dict`. Returns
/// `(cluster_ids, member_lists)` aligned by index (so Python zips them into the
/// dict). The assignments frame is sorted by cluster_id in practice, but this
/// does not rely on it -- the first-appearance HashMap index handles any order.
pub fn group_members_by_cluster(
    cluster_id: &[i64],
    member_id: &[i64],
) -> (Vec<i64>, Vec<Vec<i64>>) {
    use std::collections::HashMap;
    let n = cluster_id.len().min(member_id.len());
    let mut index: HashMap<i64, usize> = HashMap::new();
    let mut cids: Vec<i64> = Vec::new();
    let mut lists: Vec<Vec<i64>> = Vec::new();
    for i in 0..n {
        let cid = cluster_id[i];
        let mid = member_id[i];
        match index.get(&cid) {
            Some(&slot) => lists[slot].push(mid),
            None => {
                index.insert(cid, lists.len());
                cids.push(cid);
                lists.push(vec![mid]);
            }
        }
    }
    (cids, lists)
}

#[cfg(test)]
mod group_tests {
    use super::group_members_by_cluster;

    #[test]
    fn matches_setdefault_semantics() {
        // first-appearance cid order, input-order members (incl. unsorted input).
        let cids = [1i64, 1, 2, 1, 3, 2];
        let mids = [10i64, 11, 20, 12, 30, 21];
        let (out_cids, lists) = group_members_by_cluster(&cids, &mids);
        assert_eq!(out_cids, vec![1, 2, 3]);
        assert_eq!(lists, vec![vec![10, 11, 12], vec![20, 21], vec![30]]);
    }

    #[test]
    fn empty() {
        let (c, l) = group_members_by_cluster(&[], &[]);
        assert!(c.is_empty() && l.is_empty());
    }
}
