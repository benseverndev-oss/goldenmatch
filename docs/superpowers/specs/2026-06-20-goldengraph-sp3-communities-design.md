# goldengraph SP3 — community detection — design

**Status:** Design draft 2026-06-20. SP3 of the program roadmap (`2026-06-20-goldengraph-program-roadmap.md`). Awaiting approval → plan.

**Builds on:** SP1 (resolved `Graph`), graph-core (`connected_components` WCC kernel). **Surface:** Rust core (graph-core kernel + goldengraph-core query), pyo3-free. **Independent of SP2** (runs over the in-memory resolved graph, like SP1's `neighborhood`).

---

## Motivation

GraphRAG's "global" queries (themes spanning the whole corpus) need community structure: groups of densely-connected entities, each summarizable into a report the LLM map-reduces over. SP1 gives local (neighborhood) retrieval; SP3 adds the community partition that global search rides on. The popular frameworks lean on Leiden (Microsoft GraphRAG) for this; we add a deterministic, portable, pyo3-free kernel.

## Scope

1. **A community-detection kernel in `graph-core`** (the shared pyo3-free kernel crate, alongside `connected_components`), reusing its graph-traversal idiom. Suite-wide: native/pgrx/datafusion can expose it later, exactly as they do WCC.
2. **A `communities(&Graph)` query in `goldengraph-core`** that runs the kernel over the resolved entity-space edges and returns entity-id communities.

## Resolved decision (the roadmap's deferred item)

- **Algorithm: deterministic label propagation** (not Leiden, for SP3). Rationale: (a) **determinism** — the core forbids RNG/wall-clock (golden vectors); standard LP randomizes node order, so we fix it to an **asynchronous in-place sweep in ascending id order** with a smallest-label tie-break — fully deterministic, no RNG, no oscillation (async, unlike synchronous LP); (b) it mirrors the existing `connected_components` shape (edge list + id universe → `Vec<Vec<i64>>`), minimal new surface; (c) it's O(iters × edges), fast. **Leiden (modularity-optimal, hierarchical) is a future quality upgrade** behind the same return shape — noted, not built. Communities are **flat** in SP3; hierarchical is the Leiden follow-up.
- **Honest limitation:** on small or densely-connected graphs, label propagation tends to merge across sparse bridges, so it often returns **community ≈ connected-component** granularity (a bridged two-cluster graph usually collapses to ONE community). Its sub-component granularity only emerges on larger graphs with clear density contrast. In the extreme — one large, densely-connected corpus graph (exactly the shape GraphRAG global search targets) — LP can return a *single* community, degenerating "themes across the corpus" to one report. So SP3 delivers a *working but coarse* community layer (enough to get global search off the ground — each community gets a summary); **Leiden is the granularity upgrade**. The golden vectors pin the kernel's *actual, descriptive* behavior, not an optimality claim.

## The kernel (`graph-core/src/lib.rs`)

```
/// Deterministic label-propagation communities over `all_ids` ∪ edge endpoints.
/// Each id starts in its own community. Repeatedly, in ASCENDING id order, each
/// id adopts the most frequent label among its neighbors (ties → smallest
/// label); a node with no neighbors keeps its own. Iterate until a full sweep
/// makes no change or `max_iters` is hit. Returns communities (each a sorted
/// Vec of ids); singletons included (parity with `connected_components`).
pub fn label_propagation_communities(
    edges: &[(i64, i64, f64)], all_ids: &[i64], max_iters: u32,
) -> Vec<Vec<i64>>;
```

- **Determinism:** fixed ascending-id sweep order + smallest-label tie-break → identical output regardless of edge/id input order. No RNG. **Termination** is guaranteed by the `max_iters` cap (LP converges fast in practice; async reduces but doesn't eliminate oscillation on all topologies, so the cap is the hard backstop). At the cap the kernel returns the current — deterministic, well-formed (every id assigned) — partition, not necessarily a fixed point.
- **Undirected, set adjacency:** an edge `(a,b,_)` makes `a` and `b` neighbors both ways (matching `neighborhood`'s undirected expansion). Adjacency is a **set** — duplicate edges collapse (no double-counting) and **self-loops `(a,a)` are ignored** (a node never votes for its own label; "most frequent among neighbors" excludes self). Edge weight is ignored (LP is unweighted in SP3; weighted LP is a future option).
- **Output order:** communities sorted by their minimum id; members sorted ascending (deterministic, like the snapshot canonical form).

## The query (`goldengraph-core/src/community.rs`)

```
/// `id` = positional index after sorting communities by their minimum member.
pub struct Community { pub id: u32, pub members: Vec<EntityId> }   // members sorted ascending

/// Fixed iteration cap for the query path — part of the deterministic contract
/// (it co-determines the frozen golden partition; never an ad-hoc value).
pub const COMMUNITY_MAX_ITERS: u32 = 100;

/// Partition a resolved Graph's entities into communities via the graph-core
/// kernel over its entity-space edges. Entities with no edges are singletons.
pub fn communities(graph: &Graph) -> Vec<Community>;   // passes COMMUNITY_MAX_ITERS
```

- Maps the `Graph`'s `Edge`s to `(subj as i64, obj as i64, 1.0)` pairs + the entity-id universe, calls the kernel with `COMMUNITY_MAX_ITERS`, maps the `Vec<Vec<i64>>` back to `Community` (members as `EntityId`, communities sorted by min member, `id` = that positional index).
- Deterministic; reuses SP1 `Graph` so it composes with `neighborhood` (a global query = communities → per-community subgraph via `neighborhood` on community members).

## Determinism + parity

Golden vectors extend the SP1/SP2 pattern: a fixture `(Graph) -> communities` checked byte-for-byte (canonical JSON: communities by min member, members sorted). SP5's WASM/C reuse it.

## Testing (TDD)

Tests pin determinism + mechanics + the kernel's *actual* behavior (not optimality):
- **Disconnected components** (two separate triangles, no bridge) → 2 communities (LP = WCC here).
- **Connected graph** (one triangle) → 1 community.
- **Singletons** (ids with no edges) → each its own community (parity with `connected_components`).
- **Determinism / order-independence:** shuffling edge and id input order → byte-identical output (the headline guard — the fixed ascending-id sweep + smallest-label tie-break is the sole order source).
- **Convergence:** a chain (0-1-2-3-4) terminates within `max_iters` and returns a deterministic, well-formed partition; pin whatever partition LP actually yields.
- **`max_iters` cutoff:** with `max_iters=1` on a graph needing more sweeps, assert a well-formed partition (every id assigned) is returned — proves the cap backstops termination, not just the convergence path.
- **Set adjacency / self-loop:** a duplicate edge and a self-loop `(a,a)` don't change the partition vs the deduped, self-loop-free graph.
- **Query:** the SP1 differentiator graph (Apple/Jobs/iPhone resolved) → connected entities share a community; an isolated entity is its own.
- **Golden-vector byte-equality** (descriptive: whatever the deterministic kernel produces, frozen).

## CI

graph-core changes run under ci.yml's `rust` job (`cargo test/clippy --manifest-path graph-core/Cargo.toml`) — **ci-required**. goldengraph-core changes also run the informational `goldengraph` lane. No workflow change.

## Non-goals (SP3)

Leiden / modularity optimization (future quality upgrade). Hierarchical communities (future). Weighted LP (future). **LLM community summaries + global-search map-reduce** (host-side — SP4). **Persisting communities in the SP2 store + as-of-community queries** (a follow-up once both land; SP3 is in-memory over a resolved `Graph`). Distributed community detection.

## Risks / open questions (resolve in the plan)

- **LP quality vs Leiden:** label propagation can under-merge or produce unbalanced communities on some topologies. Acceptable for SP3 (gets global-search off the ground); Leiden is the quality follow-up. The golden vectors pin behavior, not optimality.
- **Determinism of the sweep:** the fixed ascending-id, smallest-label-tie rule must be the sole source of order — the order-independence test is the guard.
