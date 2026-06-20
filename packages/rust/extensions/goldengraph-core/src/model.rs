//! Graph data model: the mention-space inputs and the entity-space outputs.
//!
//! `MentionId` is a mention's position in the input `mentions` slice;
//! `EntityId` is assigned by resolution. Names / types / predicates /
//! source refs are owned `String`s (the engine takes ownership of the
//! extracted text; callers keep their own copies).
//!
//! These types derive `serde` so the WASM/C bindings (SP5) can marshal the
//! graph over a JSON boundary; the derived field names match the golden-vector
//! fixtures by construction (the cross-binding contract).

use serde::{Deserialize, Serialize};

/// A mention's position in the input `mentions` slice.
pub type MentionId = usize;

/// An entity id assigned by resolution (stable within one `build_graph` call).
pub type EntityId = u32;

/// An extracted mention: a surface name plus a coarse type used for blocking.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Mention {
    pub name: String,
    pub typ: String,
}

/// A mention-level relationship (subject/predicate/object over mention ids)
/// plus the source it was extracted from.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct MentionEdge {
    pub subj: MentionId,
    pub predicate: String,
    pub obj: MentionId,
    pub source_ref: String,
}

/// A resolved entity: its id, a canonical name, a type, the mention ids that
/// merged into it, and the distinct surface forms those mentions used.
///
/// `surface_names` (sorted, deduped) is what makes the entity findable by ANY
/// of its names, not just the canonical one -- a resolved entity may be queried
/// by a surface form the resolver did NOT pick as canonical (e.g. "Apple Inc."
/// when the canonical is the longer "Apple Computer"). `canonical_name` is the
/// longest member name and is always present in `surface_names`.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct EntityNode {
    pub entity_id: EntityId,
    pub canonical_name: String,
    pub typ: String,
    /// Provenance; a JSON caller building a graph may omit it (defaults empty).
    #[serde(default)]
    pub members: Vec<MentionId>,
    pub surface_names: Vec<String>,
}

/// An entity-space relationship: endpoints rewritten to entity ids, with the
/// (deduped, sorted) set of source refs that asserted it.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Edge {
    pub subj: EntityId,
    pub predicate: String,
    pub obj: EntityId,
    /// Provenance; a JSON caller may omit it (defaults empty).
    #[serde(default)]
    pub source_refs: Vec<String>,
}

/// The resolution-merged knowledge graph.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Graph {
    pub entities: Vec<EntityNode>,
    pub edges: Vec<Edge>,
}

/// A neighborhood result (same shape as `Graph`, but a subset).
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Subgraph {
    pub entities: Vec<EntityNode>,
    pub edges: Vec<Edge>,
}
