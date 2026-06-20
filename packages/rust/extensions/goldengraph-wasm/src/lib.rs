//! wasm-bindgen wrapper over `goldengraph-core` — the TS/WASM analogue of the
//! `goldengraph-native` pyo3 crate. The engine (resolve / store / query /
//! communities) is byte-identical across surfaces because all wrap the SAME
//! core; this crate only marshals a JSON boundary.
//!
//! Boundary design (SP5): **stateless functions over the canonical snapshot
//! JSON**. The store's snapshot IS the portable state, so no FFI handles /
//! lifetimes cross the boundary — every op is `(json, args...) -> json`, crossed
//! ONCE per call (the score-wasm boundary lesson). The pure `*_impl` fns are
//! `#[cfg]`-independent and host-`rlib`-testable; the `#[cfg(wasm32)]` wrappers
//! map `Err(String)` to a thrown JS error.

use std::collections::HashMap;

use goldengraph_core::build_graph as core_build_graph;
use goldengraph_core::community::communities as core_communities;
use goldengraph_core::model::{EntityId, Graph, Mention, MentionEdge, MentionId};
use goldengraph_core::resolve::{NativeConfig, ResolutionMode};
use goldengraph_core::retrieve::{neighborhood, seeds_by_name as core_seeds_by_name};
use goldengraph_core::store::{GraphStore, StoreBatch};

type R = Result<String, String>;

fn parse_resolution(resolution_json: &str) -> Result<ResolutionMode, String> {
    // Provided: a JSON object {mention_id: entity_id} (string keys -> usize).
    if let Ok(map) = serde_json::from_str::<HashMap<MentionId, EntityId>>(resolution_json) {
        return Ok(ResolutionMode::Provided(map));
    }
    // Native: ["native", scorer_id, threshold].
    if let Ok((tag, scorer_id, threshold)) =
        serde_json::from_str::<(String, u8, f64)>(resolution_json)
    {
        if tag == "native" {
            return Ok(ResolutionMode::Native(NativeConfig {
                scorer_id,
                threshold,
            }));
        }
    }
    Err(
        "resolution must be a JSON object {mention:entity} or [\"native\",scorer_id,threshold]"
            .into(),
    )
}

/// `(mentions_json, edges_json, resolution_json) -> graph_json` (SP1 resolve+merge).
pub fn build_graph_impl(mentions_json: &str, edges_json: &str, resolution_json: &str) -> R {
    let mentions: Vec<Mention> =
        serde_json::from_str(mentions_json).map_err(|e| format!("mentions: {e}"))?;
    let edges: Vec<MentionEdge> =
        serde_json::from_str(edges_json).map_err(|e| format!("edges: {e}"))?;
    let mode = parse_resolution(resolution_json)?;
    let g = core_build_graph(&mentions, &edges, mode);
    serde_json::to_string(&g).map_err(|e| e.to_string())
}

/// `(graph_json, seeds_json, hops) -> subgraph_json`.
pub fn neighborhood_impl(graph_json: &str, seeds_json: &str, hops: u8) -> R {
    let g: Graph = serde_json::from_str(graph_json).map_err(|e| format!("graph: {e}"))?;
    let seeds: Vec<EntityId> =
        serde_json::from_str(seeds_json).map_err(|e| format!("seeds: {e}"))?;
    serde_json::to_string(&neighborhood(&g, &seeds, hops)).map_err(|e| e.to_string())
}

/// `(graph_json, name) -> ids_json`.
pub fn seeds_by_name_impl(graph_json: &str, name: &str) -> R {
    let g: Graph = serde_json::from_str(graph_json).map_err(|e| format!("graph: {e}"))?;
    serde_json::to_string(&core_seeds_by_name(&g, name)).map_err(|e| e.to_string())
}

/// `(graph_json) -> communities_json` (SP3 label propagation).
pub fn communities_impl(graph_json: &str) -> R {
    let g: Graph = serde_json::from_str(graph_json).map_err(|e| format!("graph: {e}"))?;
    serde_json::to_string(&core_communities(&g)).map_err(|e| e.to_string())
}

/// `(snapshot_json_or_empty, batch_json) -> snapshot_json` (SP2 append). An empty
/// `snapshot_json` ("") opens a fresh store; chaining calls == repeated `append`.
pub fn store_append_impl(snapshot_json: &str, batch_json: &str) -> R {
    let snap = if snapshot_json.is_empty() {
        None
    } else {
        Some(snapshot_json)
    };
    let mut store = GraphStore::open(snap).map_err(|e| format!("open: {e:?}"))?;
    let batch: StoreBatch = serde_json::from_str(batch_json).map_err(|e| format!("batch: {e}"))?;
    store.append(batch);
    Ok(store.snapshot())
}

/// `(snapshot_json, valid_t, tx_t) -> graph_json` (SP2 bi-temporal slice).
pub fn store_as_of_impl(snapshot_json: &str, valid_t: i64, tx_t: i64) -> R {
    let store = GraphStore::open(Some(snapshot_json)).map_err(|e| format!("open: {e:?}"))?;
    serde_json::to_string(&store.as_of(valid_t, tx_t)).map_err(|e| e.to_string())
}

/// `(snapshot_json, id) -> history_events_json`.
pub fn store_history_impl(snapshot_json: &str, id: u64) -> R {
    let store = GraphStore::open(Some(snapshot_json)).map_err(|e| format!("open: {e:?}"))?;
    serde_json::to_string(&store.history(id)).map_err(|e| e.to_string())
}

/// wasm-bindgen wrappers: same fns, `Err(String)` -> thrown JS error.
#[cfg(target_arch = "wasm32")]
mod wasm {
    use super::*;
    use wasm_bindgen::prelude::*;

    fn js(r: R) -> Result<String, JsError> {
        r.map_err(|e| JsError::new(&e))
    }

    #[wasm_bindgen]
    pub fn build_graph(
        mentions_json: &str,
        edges_json: &str,
        resolution_json: &str,
    ) -> Result<String, JsError> {
        js(build_graph_impl(mentions_json, edges_json, resolution_json))
    }
    #[wasm_bindgen]
    pub fn neighborhood(graph_json: &str, seeds_json: &str, hops: u8) -> Result<String, JsError> {
        js(neighborhood_impl(graph_json, seeds_json, hops))
    }
    #[wasm_bindgen]
    pub fn seeds_by_name(graph_json: &str, name: &str) -> Result<String, JsError> {
        js(seeds_by_name_impl(graph_json, name))
    }
    #[wasm_bindgen]
    pub fn communities(graph_json: &str) -> Result<String, JsError> {
        js(communities_impl(graph_json))
    }
    #[wasm_bindgen]
    pub fn store_append(snapshot_json: &str, batch_json: &str) -> Result<String, JsError> {
        js(store_append_impl(snapshot_json, batch_json))
    }
    #[wasm_bindgen]
    pub fn store_as_of(snapshot_json: &str, valid_t: i64, tx_t: i64) -> Result<String, JsError> {
        js(store_as_of_impl(snapshot_json, valid_t, tx_t))
    }
    #[wasm_bindgen]
    pub fn store_history(snapshot_json: &str, id: u64) -> Result<String, JsError> {
        js(store_history_impl(snapshot_json, id))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::Value;

    fn fixture(name: &str) -> Value {
        let path = format!(
            "{}/../goldengraph-core/tests/fixtures/{name}",
            env!("CARGO_MANIFEST_DIR")
        );
        serde_json::from_str(&std::fs::read_to_string(path).expect("read fixture")).unwrap()
    }

    /// The WASM JSON boundary is lossless and equivalent to in-process core use:
    /// (a) chaining `store_append_impl` snapshots == repeated `GraphStore::append`
    /// (the load-bearing stateless-snapshot claim), and (b) `store_as_of_impl`
    /// == `serde(core.as_of(..))`. Driven by the SP2 golden fixture's batches.
    #[test]
    fn store_boundary_matches_core_direct() {
        let v = fixture("store_golden.json");
        // (a) WASM path: chain the snapshot across appends.
        let mut snap = String::new();
        for b in v["batches"].as_array().unwrap() {
            snap = store_append_impl(&snap, &b.to_string()).unwrap();
        }
        // core-direct path: open empty, repeated append, snapshot.
        let mut store = GraphStore::open(None).unwrap();
        for b in v["batches"].as_array().unwrap() {
            store.append(serde_json::from_str(&b.to_string()).unwrap());
        }
        assert_eq!(
            snap,
            store.snapshot(),
            "chained snapshot != repeated append"
        );
        // (b) as_of view via the boundary == core-direct as_of.
        for q in v["queries"].as_array().unwrap() {
            let (vt, tx) = (q["valid_t"].as_i64().unwrap(), q["tx_t"].as_i64().unwrap());
            assert_eq!(
                store_as_of_impl(&snap, vt, tx).unwrap(),
                serde_json::to_string(&store.as_of(vt, tx)).unwrap(),
                "query `{}`",
                q["name"].as_str().unwrap()
            );
        }
    }

    /// WASM impl produces the same communities as the SP3 core golden vectors.
    #[test]
    fn community_parity_against_golden() {
        let v = fixture("community_golden.json");
        let graph_json = v["graph"].to_string();
        let got: Value = serde_json::from_str(&communities_impl(&graph_json).unwrap()).unwrap();
        assert_eq!(
            serde_json::to_string(&got).unwrap(),
            serde_json::to_string(&v["expected_communities"]).unwrap(),
        );
    }

    #[test]
    fn bad_json_returns_err() {
        assert!(communities_impl("{ not json").is_err());
        assert!(store_append_impl("", "{ not json").is_err());
        assert!(parse_resolution("\"nope\"").is_err());
    }
}
