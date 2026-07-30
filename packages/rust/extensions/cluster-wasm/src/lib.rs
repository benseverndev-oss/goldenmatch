//! wasm-bindgen wrapper over `cluster-core` (MST-split + cluster confidence), so
//! the JS/TS clustering step runs the SAME kernels as the Python native path and
//! the Rust core — one source of truth for splitting oversized clusters and
//! scoring cluster confidence. Edge-safe (pure wasm, no `node:*`). Mirrors the
//! sibling `graph-wasm` shim.
//!
//! Row ids are 0-based positions (well within i32 / JS-safe-int), so edges cross
//! the boundary as two `Int32Array`s (the pair endpoints) plus a `Float64Array`
//! of edge weights, and the member set as an `Int32Array`. The ragged
//! `number[][]` MST-split result and the confidence tuple cross back as JSON
//! strings (nested arrays / options don't fit a typed array).

use goldenmatch_cluster_core::{
    cluster_confidence as core_cluster_confidence, mst_split_components as core_mst_split_components,
};
use wasm_bindgen::prelude::*;

/// Reassemble the `(a, b, weight)` edge triples from three parallel arrays, in
/// the SAME order the caller passed them (pair_scores iteration order) — the
/// stable sort + first-min tie-breaks in the kernel depend on it.
fn zip_edges(edges_a: &[i32], edges_b: &[i32], edges_w: &[f64]) -> Vec<(i64, i64, f64)> {
    let n = edges_a.len().min(edges_b.len()).min(edges_w.len());
    (0..n)
        .map(|i| (edges_a[i] as i64, edges_b[i] as i64, edges_w[i]))
        .collect()
}

/// Max-weight spanning tree, drop the single weakest MST edge, return the
/// resulting components. Behavior-exact mirror of cluster-core's
/// `mst_split_components` (the Python `split_oversized_cluster` core). Returns a
/// JSON `number[][]` — one array of member ids per component. Component and
/// member order is unspecified (HashMap order); the caller canonicalizes. The
/// partition is deterministic, so it matches the pure-TS impl exactly. Returns
/// `"[]"` when the MST is empty (unsplittable — caller treats as "no split").
#[wasm_bindgen]
pub fn mst_split_components(
    members: &[i32],
    edges_a: &[i32],
    edges_b: &[i32],
    edges_w: &[f64],
) -> String {
    let mem: Vec<i64> = members.iter().map(|&x| x as i64).collect();
    let edges = zip_edges(edges_a, edges_b, edges_w);
    let comps = core_mst_split_components(mem, edges);
    let out: Vec<Vec<i32>> = comps
        .into_iter()
        .map(|c| c.into_iter().map(|x| x as i32).collect())
        .collect();
    serde_json::to_string(&out).unwrap_or_else(|_| "[]".to_string())
}

/// Confidence metrics for one cluster. Behavior-exact mirror of cluster-core's
/// `cluster_confidence`. `edges` MUST arrive in pair_scores iteration order so
/// the bottleneck-pair tie-break and the average's float-summation order match
/// bit-for-bit. Returns a JSON array
/// `[min_edge, avg_edge, connectivity, bottleneck, confidence]` where
/// `min_edge`/`avg_edge` are `number | null`, `bottleneck` is `[a, b] | null`.
#[wasm_bindgen]
pub fn cluster_confidence(
    edges_a: &[i32],
    edges_b: &[i32],
    edges_w: &[f64],
    size: u32,
) -> String {
    let edges = zip_edges(edges_a, edges_b, edges_w);
    let result = core_cluster_confidence(edges, size as usize);
    serde_json::to_string(&result).unwrap_or_else(|_| "[null,null,0.0,null,0.0]".to_string())
}
