//! wasm-bindgen wrapper over `graph-core` (connected components), so the JS/TS
//! clustering step runs the SAME kernel as the Python native path and the
//! DuckDB / Postgres native UDFs — one source of truth for turning scored
//! candidate pairs into entity clusters. Edge-safe (pure wasm, no `node:*`).
//! Mirrors the sibling `goldenhnsw-wasm` / `sketch-wasm` shims.
//!
//! Row ids are 0-based positions (well within i32 / JS-safe-int), so edges cross
//! the boundary as two `Int32Array`s (the pair endpoints) plus an `Int32Array`
//! of all ids; the ragged `number[][]` result crosses back as a JSON string
//! (nested arrays don't fit a typed array). Edge weights are irrelevant to
//! connected components, so they are not passed.

use goldenmatch_graph_core::connected_components as core_connected_components;
use wasm_bindgen::prelude::*;

/// Connected components of the graph whose vertices are `all_ids` (∪ edge
/// endpoints) and whose edges are `(edges_a[i], edges_b[i])`. Returns a JSON
/// `number[][]` — one array of member ids per component (singletons included).
/// Component and member order is unspecified (HashMap order); the caller
/// canonicalizes. The partition itself is unique, so it matches the pure-TS
/// union-find exactly.
#[wasm_bindgen]
pub fn connected_components(edges_a: &[i32], edges_b: &[i32], all_ids: &[i32]) -> String {
    let n = edges_a.len().min(edges_b.len());
    let edges: Vec<(i64, i64, f64)> = (0..n)
        .map(|i| (edges_a[i] as i64, edges_b[i] as i64, 0.0))
        .collect();
    let ids: Vec<i64> = all_ids.iter().map(|&x| x as i64).collect();
    let comps = core_connected_components(&edges, &ids);
    let out: Vec<Vec<i32>> = comps
        .into_iter()
        .map(|c| c.into_iter().map(|x| x as i32).collect())
        .collect();
    serde_json::to_string(&out).unwrap_or_else(|_| "[]".to_string())
}
