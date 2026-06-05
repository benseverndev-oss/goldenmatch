//! Clustering kernels — behavior-exact replacements for the pure-Python loops
//! in `goldenmatch/core/cluster.py`.
use std::collections::{HashMap, HashSet};

use arrow::array::{Array, ArrayData, BooleanArray, Float64Array, Int64Array};
use arrow::datatypes::DataType;
use arrow::pyarrow::PyArrowType;
use pyo3::exceptions::PyValueError;
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
    goldenmatch_graph_core::connected_components(&edges, &all_ids)
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
#[pyfunction]
pub fn mst_split_components(
    members: Vec<i64>,
    edges: Vec<(i64, i64, f64)>,
) -> Vec<Vec<i64>> {
    // Kruskal over a max-weight ordering. Vec::sort_by is stable, so equal
    // scores keep pair_scores insertion order -- matching Python's stable sort.
    let mut sorted = edges;
    sorted.sort_by(|a, b| {
        b.2.partial_cmp(&a.2).unwrap_or(std::cmp::Ordering::Equal)
    });

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

/// Type alias for the Arrow build_clusters_arrow result tuple. Eight
/// PyArrowType<ArrayData> fields keep clippy::type_complexity quiet.
type BuildClustersArrowResult = (
    PyArrowType<ArrayData>, // assignments.cluster_id
    PyArrowType<ArrayData>, // assignments.member_id
    PyArrowType<ArrayData>, // metadata.cluster_id
    PyArrowType<ArrayData>, // metadata.size
    PyArrowType<ArrayData>, // metadata.confidence
    PyArrowType<ArrayData>, // metadata.oversized
    PyArrowType<ArrayData>, // metadata.bottleneck_pair_a
    PyArrowType<ArrayData>, // metadata.bottleneck_pair_b
    PyArrowType<ArrayData>, // metadata.min_edge
    PyArrowType<ArrayData>, // metadata.avg_edge
);

/// Arrow-native roadmap Phase 3 (#625): `build_clusters` over Arrow
/// pair arrays. Emits two ClusterFrames-shaped Arrow buffer sets:
/// assignments (cluster_id, member_id) and metadata (cluster_id,
/// size, confidence, oversized, bottleneck_pair_a, bottleneck_pair_b).
///
/// Reuses the existing find() helper + same Union-Find pattern as
/// `build_clusters_native` (1-based cluster ids, sorted by
/// min(member)). Confidence + bottleneck via `cluster_confidence`.
/// Cluster quality and auto-split logic are NOT in this kernel --
/// callers wrap and post-process for those (matches the Phase 2a
/// ClusterFrames shape which has a fixed `quality="strong"` until the
/// downstream weak-cluster downgrade fires).
///
/// Output shape matches the Phase 2a ClusterFrames spec exactly so
/// the Python wrapper just hands the arrays to pl.DataFrame.
#[pyfunction]
pub fn build_clusters_arrow(
    id_a: PyArrowType<ArrayData>,
    id_b: PyArrowType<ArrayData>,
    score: PyArrowType<ArrayData>,
    all_ids: PyArrowType<ArrayData>,
    max_cluster_size: usize,
) -> PyResult<BuildClustersArrowResult> {
    // ---- Type validation. -------------------------------------------------
    let id_a_data = id_a.0;
    let id_b_data = id_b.0;
    let score_data = score.0;
    let all_ids_data = all_ids.0;
    for (name, dt, expected) in [
        ("id_a", id_a_data.data_type(), DataType::Int64),
        ("id_b", id_b_data.data_type(), DataType::Int64),
        ("score", score_data.data_type(), DataType::Float64),
        ("all_ids", all_ids_data.data_type(), DataType::Int64),
    ] {
        if dt != &expected {
            return Err(PyValueError::new_err(format!(
                "build_clusters_arrow: column {name:?} must be {expected:?}, got {dt:?}"
            )));
        }
    }
    let id_a = Int64Array::from(id_a_data);
    let id_b = Int64Array::from(id_b_data);
    let score = Float64Array::from(score_data);
    let all_ids = Int64Array::from(all_ids_data);

    let n_pairs = id_a.len();
    if id_b.len() != n_pairs || score.len() != n_pairs {
        return Err(PyValueError::new_err(format!(
            "build_clusters_arrow: pair-stream column lengths differ -- \
             id_a={}, id_b={}, score={}",
            n_pairs, id_b.len(), score.len(),
        )));
    }

    // ---- Union-Find on Arrow ids (same algorithm as build_clusters_native). -
    let mut parent: HashMap<i64, i64> = HashMap::with_capacity(
        all_ids.len() + n_pairs * 2,
    );
    for i in 0..all_ids.len() {
        if !all_ids.is_null(i) {
            let id = all_ids.value(i);
            parent.entry(id).or_insert(id);
        }
    }
    for i in 0..n_pairs {
        let a = id_a.value(i);
        let b = id_b.value(i);
        parent.entry(a).or_insert(a);
        parent.entry(b).or_insert(b);
    }
    for i in 0..n_pairs {
        let a = id_a.value(i);
        let b = id_b.value(i);
        let ra = find(&mut parent, a);
        let rb = find(&mut parent, b);
        if ra != rb {
            parent.insert(ra, rb);
        }
    }

    // ---- Group + 1-based cluster_id assignment by sort-by-min-member. -------
    let keys: Vec<i64> = parent.keys().copied().collect();
    let mut root_to_members: HashMap<i64, Vec<i64>> = HashMap::new();
    for k in keys {
        let r = find(&mut parent, k);
        root_to_members.entry(r).or_default().push(k);
    }
    let mut clusters: Vec<Vec<i64>> = root_to_members.into_values().collect();
    clusters.sort_by_key(|c| *c.iter().min().expect("non-empty by construction"));

    let mut member_to_cid: HashMap<i64, i64> =
        HashMap::with_capacity(parent.len());
    for (idx, members) in clusters.iter().enumerate() {
        let cid = (idx + 1) as i64;
        for &m in members {
            member_to_cid.insert(m, cid);
        }
    }

    // ---- Bucket input edges per cluster, with ORDERED LAST-WINS dedup by
    //      (id_a, id_b): keep the FIRST-occurrence position, overwrite with the
    //      LAST score. This is byte-identical to the Python dict path's
    //      `result[cid]["pair_scores"][(a, b)] = s` (a dict keeps insertion order
    //      and last-wins value), so the metadata confidence/bottleneck/min/avg
    //      below are computed over the SAME deduped edge set the dict path uses --
    //      letting SP4 read them off frames.metadata bit-identically instead of
    //      re-materializing per-cluster pair_scores dicts. (Each pair belongs to
    //      exactly one cluster -- a's cluster -- so (a, b) is a global key.)
    let n_clusters = clusters.len();
    let mut per_cluster_edges: Vec<Vec<(i64, i64, f64)>> = vec![Vec::new(); n_clusters];
    let mut edge_pos: HashMap<(i64, i64), (usize, usize)> = HashMap::with_capacity(n_pairs);
    for i in 0..n_pairs {
        let a = id_a.value(i);
        let b = id_b.value(i);
        let s = score.value(i);
        if let Some(&cid) = member_to_cid.get(&a) {
            // cid is 1-based; per_cluster_edges is 0-indexed.
            let cidx = (cid - 1) as usize;
            if let Some(&(ci, ei)) = edge_pos.get(&(a, b)) {
                per_cluster_edges[ci][ei].2 = s; // last-wins, same position
            } else {
                let ei = per_cluster_edges[cidx].len();
                per_cluster_edges[cidx].push((a, b, s));
                edge_pos.insert((a, b), (cidx, ei));
            }
        }
    }

    // ---- Assemble Arrow output arrays. --------------------------------------
    // Assignments: long form, one row per (cluster_id, member_id).
    let total_members: usize = clusters.iter().map(|c| c.len()).sum();
    let mut a_cid: Vec<i64> = Vec::with_capacity(total_members);
    let mut a_mid: Vec<i64> = Vec::with_capacity(total_members);
    for (idx, members) in clusters.iter().enumerate() {
        let cid = (idx + 1) as i64;
        for &m in members {
            a_cid.push(cid);
            a_mid.push(m);
        }
    }

    // Metadata: one row per cluster.
    let mut m_cid: Vec<i64> = Vec::with_capacity(n_clusters);
    let mut m_size: Vec<i64> = Vec::with_capacity(n_clusters);
    let mut m_conf: Vec<f64> = Vec::with_capacity(n_clusters);
    let mut m_over: Vec<bool> = Vec::with_capacity(n_clusters);
    let mut m_bot_a: Vec<i64> = Vec::with_capacity(n_clusters);
    let mut m_bot_b: Vec<i64> = Vec::with_capacity(n_clusters);
    let mut m_min: Vec<f64> = Vec::with_capacity(n_clusters);
    let mut m_avg: Vec<f64> = Vec::with_capacity(n_clusters);
    for (idx, members) in clusters.iter().enumerate() {
        let cid = (idx + 1) as i64;
        let size = members.len();
        let edges = &per_cluster_edges[idx];
        // min_e/avg_e were previously discarded; SP4 emits them on metadata so the
        // Python weak-quality test (avg_edge - min_edge > threshold) stays
        // byte-identical without per-cluster pair_scores dicts.
        let (min_e, avg_e, _conn, bn, conf) = cluster_confidence(edges.clone(), size);
        m_cid.push(cid);
        m_size.push(size as i64);
        m_conf.push(conf);
        m_over.push(size > max_cluster_size);
        m_min.push(min_e.unwrap_or(0.0));
        m_avg.push(avg_e.unwrap_or(0.0));
        match bn {
            Some((a, b)) => {
                m_bot_a.push(a);
                m_bot_b.push(b);
            }
            None => {
                m_bot_a.push(0);
                m_bot_b.push(0);
            }
        }
    }

    let assignments_cid = Int64Array::from(a_cid);
    let assignments_mid = Int64Array::from(a_mid);
    let metadata_cid = Int64Array::from(m_cid);
    let metadata_size = Int64Array::from(m_size);
    let metadata_conf = Float64Array::from(m_conf);
    let metadata_over = BooleanArray::from(m_over);
    let metadata_bot_a = Int64Array::from(m_bot_a);
    let metadata_bot_b = Int64Array::from(m_bot_b);
    let metadata_min = Float64Array::from(m_min);
    let metadata_avg = Float64Array::from(m_avg);

    Ok((
        PyArrowType(assignments_cid.to_data()),
        PyArrowType(assignments_mid.to_data()),
        PyArrowType(metadata_cid.to_data()),
        PyArrowType(metadata_size.to_data()),
        PyArrowType(metadata_conf.to_data()),
        PyArrowType(metadata_over.to_data()),
        PyArrowType(metadata_bot_a.to_data()),
        PyArrowType(metadata_bot_b.to_data()),
        PyArrowType(metadata_min.to_data()),
        PyArrowType(metadata_avg.to_data()),
    ))
}

/// Arrow columnar connected components. Edge columns int64/int64/float64 + an
/// int64 `all_ids` universe column. Returns one Arrow `List<Int64>` array (one
/// list per component, members sorted ascending). Delegates to the pyo3-free
/// `graph-core` kernel so DuckDB (via this shim) and DataFusion share one path.
#[pyfunction]
pub fn connected_components_arrow(
    id_a: PyArrowType<ArrayData>,
    id_b: PyArrowType<ArrayData>,
    score: PyArrowType<ArrayData>,
    all_ids: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    let out = goldenmatch_graph_core::connected_components_arrow_data(
        id_a.0, id_b.0, score.0, all_ids.0,
    )
    .map_err(PyValueError::new_err)?;
    Ok(PyArrowType(out))
}
