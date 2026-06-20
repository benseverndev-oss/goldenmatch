//! Graph data model: the mention-space inputs and the entity-space outputs.
//!
//! `MentionId` is a mention's position in the input `mentions` slice;
//! `EntityId` is assigned by resolution. Names / types / predicates /
//! source refs are owned `String`s (the engine takes ownership of the
//! extracted text; callers keep their own copies).

/// A mention's position in the input `mentions` slice.
pub type MentionId = usize;

/// An entity id assigned by resolution (stable within one `build_graph` call).
pub type EntityId = u32;

/// An extracted mention: a surface name plus a coarse type used for blocking.
#[derive(Clone, Debug)]
pub struct Mention {
    pub name: String,
    pub typ: String,
}

/// A mention-level relationship (subject/predicate/object over mention ids)
/// plus the source it was extracted from.
#[derive(Clone, Debug)]
pub struct MentionEdge {
    pub subj: MentionId,
    pub predicate: String,
    pub obj: MentionId,
    pub source_ref: String,
}

/// A resolved entity: its id, a canonical name, a type, and the mention ids
/// that merged into it.
#[derive(Clone, Debug, PartialEq)]
pub struct EntityNode {
    pub entity_id: EntityId,
    pub canonical_name: String,
    pub typ: String,
    pub members: Vec<MentionId>,
}

/// An entity-space relationship: endpoints rewritten to entity ids, with the
/// (deduped, sorted) set of source refs that asserted it.
#[derive(Clone, Debug, PartialEq)]
pub struct Edge {
    pub subj: EntityId,
    pub predicate: String,
    pub obj: EntityId,
    pub source_refs: Vec<String>,
}

/// The resolution-merged knowledge graph.
#[derive(Clone, Debug)]
pub struct Graph {
    pub entities: Vec<EntityNode>,
    pub edges: Vec<Edge>,
}

/// A neighborhood result (same shape as `Graph`, but a subset).
#[derive(Clone, Debug)]
pub struct Subgraph {
    pub entities: Vec<EntityNode>,
    pub edges: Vec<Edge>,
}
