//! `goldengraph._native` -- PyO3 binding for the pyo3-free `goldengraph-core`
//! knowledge-graph engine.
//!
//! A thin marshaling layer: convert Python lists/dicts into the core's plain
//! Rust types, call `goldengraph_core::build_graph`, and wrap the resulting
//! `Graph` in a `#[pyclass]` whose `query` / `seeds_by_name` methods hand back
//! plain Python dicts/lists. No business logic lives here -- the resolution and
//! retrieval all happen in the core crate (shared with future TS/WASM/C
//! bindings).

use std::collections::HashMap;

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};

use goldengraph_core::build_graph as core_build_graph;
use goldengraph_core::community::communities as core_communities;
use goldengraph_core::model::{Edge, EntityId, EntityNode, Graph, Mention, MentionEdge, MentionId};
use goldengraph_core::resolve::{NativeConfig, ResolutionMode};
use goldengraph_core::retrieve::{neighborhood, seeds_by_name as core_seeds_by_name};
use goldengraph_core::store::{GraphStore, HistoryEvent, StableId, StoreBatch};

/// A resolution-merged knowledge graph, queryable by neighborhood.
#[pyclass]
struct PyGraph {
    inner: Graph,
}

#[pymethods]
impl PyGraph {
    /// 1-2 hop neighborhood around `seeds`, as
    /// `{"entities": [...], "edges": [...]}` of plain dicts.
    fn query<'py>(
        &self,
        py: Python<'py>,
        seeds: Vec<EntityId>,
        hops: u8,
    ) -> PyResult<Bound<'py, PyDict>> {
        let sub = neighborhood(&self.inner, &seeds, hops);
        graph_view_to_dict(py, &sub.entities, &sub.edges)
    }

    /// Entity ids whose canonical name OR any merged surface form equals `name`
    /// (so a resolved entity is findable by every name it was mentioned under,
    /// not just the canonical the resolver happened to pick).
    fn seeds_by_name(&self, name: &str) -> Vec<EntityId> {
        core_seeds_by_name(&self.inner, name)
    }

    /// Community partition of this graph (SP3 label propagation), as
    /// `[{ "id": int, "members": [int] }]`. A "global" query = `communities()`
    /// then `query(members, hops)` per community.
    fn communities<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyList>> {
        let out = PyList::empty(py);
        for c in core_communities(&self.inner) {
            let d = PyDict::new(py);
            d.set_item("id", c.id)?;
            d.set_item("members", PyList::new(py, &c.members)?)?;
            out.append(d)?;
        }
        Ok(out)
    }

    /// All entities in the graph as `[{entity_id, canonical_name, typ, members,
    /// surface_names}]` (SP4c needs to enumerate entities to embed their names;
    /// `query`/`seeds_by_name` can't enumerate). Same projection as `query`'s
    /// entity dicts, over the full entity set.
    fn entities<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyList>> {
        let out = PyList::empty(py);
        for e in &self.inner.entities {
            let d = PyDict::new(py);
            d.set_item("entity_id", e.entity_id)?;
            d.set_item("canonical_name", e.canonical_name.as_str())?;
            d.set_item("typ", e.typ.as_str())?;
            d.set_item("members", PyList::new(py, &e.members)?)?;
            let names: Vec<&str> = e.surface_names.iter().map(String::as_str).collect();
            d.set_item("surface_names", PyList::new(py, names)?)?;
            out.append(d)?;
        }
        Ok(out)
    }
}

/// A durable, bi-temporal knowledge-graph store (SP2). Wraps the pyo3-free
/// `goldengraph_core::store::GraphStore`. Identity is keyed on host-supplied
/// `record_key`s; `as_of` answers two-axis time-travel queries.
#[pyclass]
struct PyStore {
    inner: GraphStore,
}

#[pymethods]
impl PyStore {
    /// Open from a canonical JSON `snapshot` (or empty when `None`).
    #[new]
    #[pyo3(signature = (snapshot=None))]
    fn new(snapshot: Option<&str>) -> PyResult<Self> {
        GraphStore::open(snapshot)
            .map(|inner| PyStore { inner })
            .map_err(|e| PyValueError::new_err(format!("invalid snapshot: {e:?}")))
    }

    /// Reconcile + append a batch. `batch_json` is a `StoreBatch` serialized to
    /// JSON (entities with `record_keys`, edges with `valid_from`/`valid_to`/
    /// `source_refs`, `ingested_at`). The SP4b Python layer builds this JSON.
    fn append(&mut self, batch_json: &str) -> PyResult<()> {
        let batch: StoreBatch = serde_json::from_str(batch_json)
            .map_err(|e| PyValueError::new_err(format!("invalid StoreBatch JSON: {e}")))?;
        self.inner.append(batch);
        Ok(())
    }

    /// Bi-temporal slice as a queryable `PyGraph` (valid time × transaction time).
    fn as_of(&self, valid_t: i64, tx_t: i64) -> PyGraph {
        PyGraph {
            inner: self.inner.as_of(valid_t, tx_t),
        }
    }

    /// Canonical JSON snapshot (round-trips via the constructor).
    fn snapshot(&self) -> String {
        self.inner.snapshot()
    }

    /// History events literally naming `id`, as plain dicts.
    fn history<'py>(&self, py: Python<'py>, id: StableId) -> PyResult<Bound<'py, PyList>> {
        let out = PyList::empty(py);
        for ev in self.inner.history(id) {
            let d = PyDict::new(py);
            match ev {
                HistoryEvent::Merge { kept, absorbed, at } => {
                    d.set_item("kind", "merge")?;
                    d.set_item("kept", kept)?;
                    d.set_item("absorbed", PyList::new(py, absorbed)?)?;
                    d.set_item("at", at)?;
                }
                HistoryEvent::Split { from, into, at } => {
                    d.set_item("kind", "split")?;
                    d.set_item("from", from)?;
                    d.set_item("into", PyList::new(py, into)?)?;
                    d.set_item("at", at)?;
                }
            }
            out.append(d)?;
        }
        Ok(out)
    }
}

/// Serialize a slice of entities + edges into a `{"entities", "edges"}` dict of
/// plain Python dicts (used by `query`; the full graph and any subgraph share
/// this shape).
fn graph_view_to_dict<'py>(
    py: Python<'py>,
    entities: &[EntityNode],
    edges: &[Edge],
) -> PyResult<Bound<'py, PyDict>> {
    let ent_list = PyList::empty(py);
    for e in entities {
        let d = PyDict::new(py);
        d.set_item("entity_id", e.entity_id)?;
        d.set_item("canonical_name", e.canonical_name.as_str())?;
        d.set_item("typ", e.typ.as_str())?;
        d.set_item("members", PyList::new(py, &e.members)?)?;
        let names: Vec<&str> = e.surface_names.iter().map(String::as_str).collect();
        d.set_item("surface_names", PyList::new(py, names)?)?;
        ent_list.append(d)?;
    }
    let edge_list = PyList::empty(py);
    for e in edges {
        let d = PyDict::new(py);
        d.set_item("subj", e.subj)?;
        d.set_item("predicate", e.predicate.as_str())?;
        d.set_item("obj", e.obj)?;
        let refs: Vec<&str> = e.source_refs.iter().map(String::as_str).collect();
        d.set_item("source_refs", PyList::new(py, refs)?)?;
        edge_list.append(d)?;
    }
    let out = PyDict::new(py);
    out.set_item("entities", ent_list)?;
    out.set_item("edges", edge_list)?;
    Ok(out)
}

/// Parse the `resolution` argument: either a `dict[int, int]` (Provided
/// `mention -> entity-id` map) or a `("native", scorer_id, threshold)` tuple
/// (the native explicit-config resolver).
fn parse_resolution(obj: &Bound<'_, PyAny>) -> PyResult<ResolutionMode> {
    if let Ok(map) = obj.extract::<HashMap<MentionId, EntityId>>() {
        return Ok(ResolutionMode::Provided(map));
    }
    if let Ok((tag, scorer_id, threshold)) = obj.extract::<(String, u8, f64)>() {
        if tag == "native" {
            return Ok(ResolutionMode::Native(NativeConfig {
                scorer_id,
                threshold,
            }));
        }
    }
    Err(PyValueError::new_err(
        "resolution must be a dict[int, int] (provided) or a (\"native\", scorer_id, threshold) tuple",
    ))
}

/// Build the entity-space graph from `mentions` (list of `(name, typ)`) +
/// `edges` (list of `(subj, predicate, obj, source_ref)`) under the given
/// `resolution`.
#[pyfunction]
fn build_graph(
    mentions: Vec<(String, String)>,
    edges: Vec<(MentionId, String, MentionId, String)>,
    resolution: &Bound<'_, PyAny>,
) -> PyResult<PyGraph> {
    let mentions: Vec<Mention> = mentions
        .into_iter()
        .map(|(name, typ)| Mention { name, typ })
        .collect();
    let edges: Vec<MentionEdge> = edges
        .into_iter()
        .map(|(subj, predicate, obj, source_ref)| MentionEdge {
            subj,
            predicate,
            obj,
            source_ref,
        })
        .collect();
    let mode = parse_resolution(resolution)?;
    Ok(PyGraph {
        inner: core_build_graph(&mentions, &edges, mode),
    })
}

#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    m.add_class::<PyGraph>()?;
    m.add_class::<PyStore>()?;
    m.add_function(wrap_pyfunction!(build_graph, m)?)?;
    Ok(())
}
