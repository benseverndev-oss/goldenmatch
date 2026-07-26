//! Clustering kernels — behavior-exact replacements for the pure-Python loops
//! in `goldenmatch/core/cluster.py`.
//!
//! The MST-split + confidence kernels (`find`, `mst_split_components`,
//! `severe_bridge_count`, `cluster_confidence`, and the `ConfidenceResult`
//! alias) now live in the pyo3-free `goldenmatch-cluster-core` crate (step A1
//! of the kernel-sharing effort; they can later get a wasm surface). The three
//! that were `#[pyfunction]`s keep thin shims here so the pymodule exports the
//! same symbols; the Arrow/native orchestrators call the imported core fns
//! directly.
use std::collections::HashMap;

use arrow::array::{Array, ArrayData, BooleanArray, Float64Array, Int64Array};
use arrow::datatypes::DataType;
use arrow::pyarrow::PyArrowType;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

// Moved to the pyo3-free core; the `#[pyfunction]` shims below delegate, and the
// build_clusters_* orchestrators call these directly (find, cluster_confidence).
// The three shimmed fns are imported under `core_*` aliases so the shims can
// keep the original names (which the pymodule exports to Python).
use goldenmatch_cluster_core::{
    cluster_confidence as core_cluster_confidence, find,
    mst_split_components as core_mst_split_components,
    severe_bridge_count as core_severe_bridge_count, ConfidenceResult,
};

/// Connected components over `all_ids` ∪ edge endpoints. Mirrors
/// `UnionFind.add_many` + `union` loop + `get_clusters` in cluster.py:323-328.
///
/// Component membership is independent of union strategy, so naive union here
/// yields the identical grouping the Python union-by-rank produces. Component
/// and member order is irrelevant: `build_clusters` re-sorts by `min(member)`.
#[pyfunction]
pub fn connected_components(edges: Vec<(i64, i64, f64)>, all_ids: Vec<i64>) -> Vec<Vec<i64>> {
    goldenmatch_graph_core::connected_components(&edges, &all_ids)
}

/// Max-weight spanning tree (Kruskal), then drop the single weakest MST edge
/// and return the resulting components. Thin shim over
/// `goldenmatch_cluster_core::mst_split_components` (behavior-exact mirror of
/// `split_oversized_cluster` in cluster.py — see the core crate for the
/// byte-parity notes on Kruskal edge selection and the first-minimum tie-break).
#[pyfunction]
pub fn mst_split_components(members: Vec<i64>, edges: Vec<(i64, i64, f64)>) -> Vec<Vec<i64>> {
    core_mst_split_components(members, edges)
}

/// Count edges whose removal splits the cluster into two >= 2-node components
/// (the "merged by one weak link" pathology). Thin shim over
/// `goldenmatch_cluster_core::severe_bridge_count` (behavior-exact mirror of
/// `_severe_bridge_count` in cluster.py:168-200).
#[pyfunction]
pub fn severe_bridge_count(members: Vec<i64>, edges: Vec<(i64, i64, f64)>) -> usize {
    core_severe_bridge_count(members, edges)
}

/// Confidence metrics for one cluster. Thin shim over
/// `goldenmatch_cluster_core::cluster_confidence` (behavior-exact mirror of
/// `compute_cluster_confidence` — see the core crate for the byte-parity notes
/// on the bottleneck-pair tie-break and the average's float-summation order).
/// Returns `(min_edge, avg_edge, connectivity, bottleneck_pair, confidence)`.
#[pyfunction]
pub fn cluster_confidence(edges: Vec<(i64, i64, f64)>, size: usize) -> ConfidenceResult {
    core_cluster_confidence(edges, size)
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
    let mut parent: HashMap<i64, i64> = HashMap::with_capacity(all_ids.len() + pairs.len() * 2);
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
    let mut member_to_cid: HashMap<i64, i64> = HashMap::with_capacity(parent.len());
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
    let mut per_cluster_edges: Vec<Vec<(i64, i64, f64)>> = vec![Vec::new(); n_clusters];
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
        let (_min_e, _avg_e, _conn, bn, conf) = core_cluster_confidence(edges.clone(), size);
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
            n_pairs,
            id_b.len(),
            score.len(),
        )));
    }

    // ---- Union-Find on Arrow ids (same algorithm as build_clusters_native). -
    let mut parent: HashMap<i64, i64> = HashMap::with_capacity(all_ids.len() + n_pairs * 2);
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

    let mut member_to_cid: HashMap<i64, i64> = HashMap::with_capacity(parent.len());
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
        let (min_e, avg_e, _conn, bn, conf) = core_cluster_confidence(edges.clone(), size);
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
    let out =
        goldenmatch_graph_core::connected_components_arrow_data(id_a.0, id_b.0, score.0, all_ids.0)
            .map_err(PyValueError::new_err)?;
    Ok(PyArrowType(out))
}
