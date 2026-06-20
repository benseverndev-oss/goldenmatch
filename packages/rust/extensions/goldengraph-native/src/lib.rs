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
use goldengraph_core::model::{Edge, EntityId, EntityNode, Graph, Mention, MentionEdge, MentionId};
use goldengraph_core::resolve::{NativeConfig, ResolutionMode};
use goldengraph_core::retrieve::{neighborhood, seeds_by_name as core_seeds_by_name};

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
    m.add_function(wrap_pyfunction!(build_graph, m)?)?;
    Ok(())
}
