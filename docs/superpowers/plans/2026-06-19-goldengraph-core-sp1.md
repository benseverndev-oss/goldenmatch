# goldengraph-core (SP1) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `goldengraph-core` — a pyo3-free Rust engine (graph model + typed edges + dual-path resolution + 1-2 hop retrieval) plus a `goldengraph-native` pyo3 binding, so a host can turn extracted mentions+relationships into a resolution-merged knowledge graph and query neighborhoods.

**Architecture:** A pure-Rust core crate `goldengraph-core` does all compute: `apply_resolution` rewrites mention-edges into entity space given either a host-supplied `mention->entity-id` map (Provided) or a native explicit-config resolver that reuses `score-core` (scoring) + `graph-core` (WCC). A separate abi3 crate `goldengraph-native` exposes it to Python (the established `-core`/`-native` split). No LLM, no embeddings, no persistence — those are SP2+.

**Tech Stack:** Rust 2021 (workspace edition), `score-core` + `graph-core` (path deps), pyo3/abi3 + maturin (the binding), pytest (Python-side), Cargo golden-vector fixtures.

**Spec:** `docs/superpowers/specs/2026-06-19-goldengraph-native-kg-engine-design.md`

---

## Context the implementer needs

- **Worktree:** create off freshly-fetched `origin/main`:
  `git -C D:/show_case/goldenmatch fetch origin main` then
  `git -C D:/show_case/goldenmatch worktree add D:/show_case/goldenmatch/.worktrees/goldengraph -b claude/goldengraph-core origin/main`.
  Work ONLY there. `$EXT` = `<worktree>/packages/rust/extensions`.
- **Rust bash preamble — prefix EVERY cargo/maturin command with this** (per the extensions CLAUDE.md; cargo otherwise defaults `CARGO_HOME` to the drive root on Windows):
  `export PATH="/c/Users/bsevern/.cargo/bin:$PATH" && export RUSTUP_HOME="C:/Users/bsevern/.rustup" && export CARGO_HOME="C:/Users/bsevern/.cargo"`
- **`goldengraph-core` is pyo3-free → `cargo test -p goldengraph-core` runs LOCALLY.** Its deps (`score-core`, `graph-core`) are pure Rust (no `ort`, no libclang), so it links on Windows. Do Tasks 1-4 + 6 locally with `cargo test`.
- **`goldengraph-native` (pyo3 abi3) + the Python tests are CI-built.** Don't fight maturin/pyo3 locally (Ben's call); write them, run `cargo build -p goldengraph-native` to confirm it compiles, and let the CI lane (Task 7) run the Python tests.
- **The two crates are NOT workspace members.** `$EXT/Cargo.toml` has `members = ["bridge"]` only; the `-core`/`-native` crates are standalone, pulled in as path-deps. Add `goldengraph-native` to the `exclude` list there (mirror `goldencheck-native`) so the bridge workspace ignores it. `goldengraph-core` needs no Cargo registration (it is only ever a path-dep of `goldengraph-native` + the postgres/other consumers that don't apply here).
- **Mirror the existing `-core`/`-native` pair:** read `$EXT/goldencheck-core/` (pyo3-free crate shape) and `$EXT/goldencheck-native/` (the abi3 pyo3 wrapper: `Cargo.toml`, `pyproject.toml`, `src/lib.rs`) before scaffolding — copy their structure, don't invent.
- **Kernel APIs (verified):** the crates are named `goldenmatch-score-core` and `goldenmatch-graph-core` (`[package].name`), so the `use`/call paths are `goldenmatch_score_core::score_one(scorer_id: u8, a: &str, b: &str) -> f64` and `goldenmatch_graph_core::connected_components(edges: &[(i64,i64,f64)], all_ids: &[i64]) -> Vec<Vec<i64>>` (Rust converts the `-` to `_`). The Task-1/4 code below writes `score_core::`/`graph_core::` for brevity — use the real underscore paths. `connected_components` DOES return singletons as 1-element clusters (covered by graph-core's own `cc_groups_transitive_and_includes_singletons` test). READ `score-core/src/lib.rs` `score_one` match arms to pick the `scorer_id` for jaro_winkler; all `score_one` ids return on the **[0,1]** scale (note: id=2/token_sort returns `ratio` on [0,1], not the *100 public API scale).
- **gh auth:** `unset GH_TOKEN; export GH_TOKEN=$(gh auth token --user benzsevern)`. Commits as Claude: `git -c user.name=Claude -c user.email=noreply@anthropic.com commit ...`. Merge queue: `gh pr merge <N> --auto --squash` then STOP.
- **docs/superpowers/** is local-only — do NOT `git add` the spec/plan.

## Locked decisions (from the spec's deferred list)

- **pyo3 glue:** a SEPARATE `goldengraph-native` abi3 crate (the established `-core`/`-native` split). The core stays pyo3-free.
- **Ids:** `MentionId = usize` (the mention's position in the input `mentions` slice). `EntityId = u32` (assigned by resolution). `name`/`type`/`predicate`/`source_ref` are `String`.
- **Serialization:** the pyo3 layer returns plain Python `dict`/`list` (a `Graph` is a `#[pyclass]` holding the Rust `Graph`; `query` returns `{"entities": [...], "edges": [...]}` of plain dicts). No custom Python classes beyond `Graph`.
- **`NativeConfig` MVP:** `{ scorer_id: u8, threshold: f64 }`, matching within type-blocks (group mentions by `type`, all-pairs within each block). `sketch-core` LSH blocking is a scale optimization DEFERRED (not SP1).

## File structure

```
$EXT/goldengraph-core/Cargo.toml          CREATE  pyo3-free crate; deps score-core, graph-core (path)
$EXT/goldengraph-core/src/lib.rs          CREATE  re-exports + build_graph/query entry points
$EXT/goldengraph-core/src/model.rs        CREATE  Mention, MentionEdge, EntityNode, Edge, Graph, Subgraph
$EXT/goldengraph-core/src/resolve.rs      CREATE  ResolutionMode, NativeConfig, resolve_native, apply_resolution
$EXT/goldengraph-core/src/retrieve.rs     CREATE  neighborhood (BFS)
$EXT/goldengraph-core/tests/fixtures/resolution_split_merge.json   CREATE  the differentiator fixture
$EXT/goldengraph-core/tests/fixtures/goldengraph_golden.json       CREATE  golden vectors
$EXT/goldengraph-native/Cargo.toml        CREATE  abi3 pyo3 wrapper; path dep goldengraph-core
$EXT/goldengraph-native/pyproject.toml    CREATE  maturin (mirror goldencheck-native)
$EXT/goldengraph-native/src/lib.rs        CREATE  pyo3 module: build_graph, Graph(query/seeds_by_name)
$EXT/goldengraph-native/tests/test_goldengraph.py   CREATE  Python binding tests
$EXT/Cargo.toml                           MODIFY  add "goldengraph-native" to [workspace].exclude
.github/workflows/goldengraph.yml         CREATE  cargo test+clippy core; maturin build native + pytest
```

---

## Task 0: Scaffold `goldengraph-core` (compiles + empty test runs)

**Files:** Create `$EXT/goldengraph-core/Cargo.toml`, `src/lib.rs`.

- [ ] **Step 1: Read the reference** `$EXT/goldencheck-core/Cargo.toml` + `src/lib.rs` to copy the standalone pyo3-free crate shape (edition, workspace-package inheritance or explicit, no pyo3).

- [ ] **Step 2: Create `$EXT/goldengraph-core/Cargo.toml`:**

```toml
[package]
name = "goldengraph-core"
version = "0.1.0"
edition = "2021"
license = "MIT"

[dependencies]
score-core = { path = "../score-core" }
graph-core = { path = "../graph-core" }

[dev-dependencies]
serde_json = "1"
```

> Confirm the exact crate names of score-core/graph-core (`[package].name` in their Cargo.toml — they may be `goldenmatch-score-core` etc.; the extensions CLAUDE.md references `goldenmatch-graph-core`). Use the real names in `[dependencies]` and `use` statements.

- [ ] **Step 3: Create `src/lib.rs`** with module decls + a trivial test:

```rust
pub mod model;
pub mod resolve;
pub mod retrieve;

#[cfg(test)]
mod smoke {
    #[test]
    fn crate_builds() { assert_eq!(2 + 2, 4); }
}
```
(Create empty `model.rs`/`resolve.rs`/`retrieve.rs` with a `// placeholder` so it compiles.)

- [ ] **Step 4: Build + test.**
  Run (with the rust preamble): `cargo test -p goldengraph-core`
  Expected: compiles, 1 passed.

- [ ] **Step 5: Commit** `feat(goldengraph): scaffold pyo3-free goldengraph-core crate`.

---

## Task 1: `model.rs` + `apply_resolution` (Provided path)

The core deliverable: given mentions + mention-edges + a `mention->entity-id` map, build the entity-space graph (merged nodes, rewritten + deduped edges). No kernels yet.

**Files:** `$EXT/goldengraph-core/src/model.rs`, `src/resolve.rs`; Test: inline `#[cfg(test)]`.

- [ ] **Step 1: Write the failing test** (in `resolve.rs`):

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::*;

    fn fixture() -> (Vec<Mention>, Vec<MentionEdge>) {
        // mentions 0,1 are the same entity ("Apple Inc"/"Apple"); 2 is "Jobs"; 3 is "iPhone"
        let mentions = vec![
            Mention { name: "Apple Inc".into(), typ: "org".into() },   // 0
            Mention { name: "Apple".into(),     typ: "org".into() },   // 1
            Mention { name: "Jobs".into(),      typ: "person".into() },// 2
            Mention { name: "iPhone".into(),    typ: "product".into() },//3
        ];
        let edges = vec![
            MentionEdge { subj: 0, predicate: "founded_by".into(), obj: 2, source_ref: "c1".into() },
            MentionEdge { subj: 1, predicate: "released".into(),   obj: 3, source_ref: "c2".into() },
        ];
        (mentions, edges)
    }

    #[test]
    fn provided_resolution_merges_nodes_and_keeps_both_edges() {
        let (mentions, edges) = fixture();
        // host says mentions 0 and 1 are entity 0; 2->1; 3->2
        let map = vec![(0usize, 0u32), (1, 0), (2, 1), (3, 2)].into_iter().collect();
        let g = apply_resolution(&mentions, &edges, &map);
        assert_eq!(g.entities.len(), 3);                 // 0+1 merged
        let apple = g.entities.iter().find(|e| e.entity_id == 0).unwrap();
        assert_eq!(apple.canonical_name, "Apple Inc");   // longest name
        assert_eq!(g.edges.len(), 2);                    // both facts attach to entity 0
        assert!(g.edges.iter().any(|e| e.subj == 0 && e.predicate == "founded_by" && e.obj == 1));
        assert!(g.edges.iter().any(|e| e.subj == 0 && e.predicate == "released" && e.obj == 2));
    }

    #[test]
    fn duplicate_edges_dedup_and_accumulate_sources() {
        let mentions = vec![
            Mention { name: "A Inc".into(), typ: "org".into() },
            Mention { name: "A".into(),     typ: "org".into() },
            Mention { name: "B".into(),     typ: "org".into() },
        ];
        let edges = vec![
            MentionEdge { subj: 0, predicate: "rel".into(), obj: 2, source_ref: "c1".into() },
            MentionEdge { subj: 1, predicate: "rel".into(), obj: 2, source_ref: "c2".into() }, // same after merge
        ];
        let map = vec![(0usize,0u32),(1,0),(2,1)].into_iter().collect();
        let g = apply_resolution(&mentions, &edges, &map);
        assert_eq!(g.edges.len(), 1);
        assert_eq!(g.edges[0].source_refs, vec!["c1".to_string(), "c2".to_string()]);
    }
}
```

- [ ] **Step 2: Run, verify fail** (`apply_resolution`/types undefined). `cargo test -p goldengraph-core`.

- [ ] **Step 3: Implement `model.rs`:**

```rust
pub type MentionId = usize;
pub type EntityId = u32;

#[derive(Clone, Debug)]
pub struct Mention { pub name: String, pub typ: String }

#[derive(Clone, Debug)]
pub struct MentionEdge { pub subj: MentionId, pub predicate: String, pub obj: MentionId, pub source_ref: String }

#[derive(Clone, Debug, PartialEq)]
pub struct EntityNode { pub entity_id: EntityId, pub canonical_name: String, pub typ: String, pub members: Vec<MentionId> }

#[derive(Clone, Debug, PartialEq)]
pub struct Edge { pub subj: EntityId, pub predicate: String, pub obj: EntityId, pub source_refs: Vec<String> }

#[derive(Clone, Debug)]
pub struct Graph { pub entities: Vec<EntityNode>, pub edges: Vec<Edge> }

#[derive(Clone, Debug)]
pub struct Subgraph { pub entities: Vec<EntityNode>, pub edges: Vec<Edge> }
```

- [ ] **Step 4: Implement `apply_resolution` in `resolve.rs`:**

```rust
use std::collections::{BTreeMap, HashMap};
use crate::model::*;

/// Build the entity-space Graph from mentions + mention-edges + a mention->entity-id map.
/// Deterministic: entities sorted by entity_id; edges sorted by (subj, predicate, obj).
pub fn apply_resolution(
    mentions: &[Mention],
    edges: &[MentionEdge],
    map: &HashMap<MentionId, EntityId>,
) -> Graph {
    // group mention ids by entity id
    let mut groups: BTreeMap<EntityId, Vec<MentionId>> = BTreeMap::new();
    for (mid, _m) in mentions.iter().enumerate() {
        if let Some(&eid) = map.get(&mid) {
            groups.entry(eid).or_default().push(mid);
        }
    }
    // entity nodes: canonical = longest member name (tie -> lowest mention id)
    let entities: Vec<EntityNode> = groups.iter().map(|(&eid, members)| {
        let rep = *members.iter().max_by_key(|&&m| (mentions[m].name.len(), usize::MAX - m)).unwrap();
        EntityNode {
            entity_id: eid,
            canonical_name: mentions[rep].name.clone(),
            typ: mentions[rep].typ.clone(),
            members: members.clone(),
        }
    }).collect();
    // edges: rewrite endpoints, dedup by (subj,predicate,obj), accumulate source_refs (sorted, unique)
    let mut acc: BTreeMap<(EntityId, String, EntityId), Vec<String>> = BTreeMap::new();
    for e in edges {
        let (Some(&s), Some(&o)) = (map.get(&e.subj), map.get(&e.obj)) else { continue }; // skip unmapped
        acc.entry((s, e.predicate.clone(), o)).or_default().push(e.source_ref.clone());
    }
    let edges: Vec<Edge> = acc.into_iter().map(|((subj, predicate, obj), mut refs)| {
        refs.sort(); refs.dedup();
        Edge { subj, predicate, obj, source_refs: refs }
    }).collect();
    Graph { entities, edges }
}
```

- [ ] **Step 5: Run, verify pass; clippy clean** (`cargo clippy -p goldengraph-core -- -D warnings`).
- [ ] **Step 6: Commit** `feat(goldengraph): model + apply_resolution (Provided path, merge + edge dedup)`.

---

## Task 2: `retrieve.rs` — neighborhood BFS

**Files:** `$EXT/goldengraph-core/src/retrieve.rs`.

- [ ] **Step 1: Write the failing test** (in `retrieve.rs`): build a small `Graph` by hand (entities 0,1,2,3; edges 0->1, 0->2, 2->3); assert `neighborhood(&g, &[0], 1)` returns entities {0,1,2} + the two edges from 0, and `neighborhood(&g, &[0], 2)` additionally pulls entity 3 + edge 2->3. Include a self-loop edge (0->0) and a cycle (3->0) in a second test to confirm no infinite loop and stable output.

- [ ] **Step 2: Run, verify fail.**

- [ ] **Step 3: Implement** `neighborhood(graph: &Graph, seeds: &[EntityId], hops: u8) -> Subgraph`: BFS over edges (treat edges as undirected for neighborhood expansion), collect reached entity ids within `hops`, include every edge whose BOTH endpoints are in the reached set. Sort entities by `entity_id`, edges by `(subj, predicate, obj)` for determinism. `hops` clamped to {1,2}.

- [ ] **Step 4: Run, verify pass; clippy clean.**
- [ ] **Step 5: Commit** `feat(goldengraph): 1-2 hop neighborhood retrieval`.

---

## Task 3: The differentiator test (the thesis, pinned)

**Files:** `$EXT/goldengraph-core/tests/integration.rs`, `tests/fixtures/resolution_split_merge.json`.

- [ ] **Step 1: Create the named fixture** `resolution_split_merge.json`: the Task-1 fixture data (Apple Inc/Apple/Jobs/iPhone + the two edges) plus two resolution maps: `exact` (`{0:0,1:1,2:2,3:3}` — Apple stays split) and `resolved` (`{0:0,1:0,2:1,3:2}` — Apple merged). JSON shape an implementer can `serde_json`-load.

- [ ] **Step 2: Write `tests/integration.rs`** loading the fixture and asserting:
  - Under `resolved`: a 1-hop `neighborhood` from the Apple entity returns BOTH facts (`founded_by` Jobs AND `released` iPhone).
  - Under `exact`: a 1-hop neighborhood from the "Apple Inc" entity (id 0) returns ONLY `founded_by` Jobs (the `released` fact hangs off the separate "Apple" node id 1) — i.e. half the facts.
  - This is SP1's headline test: resolution is the difference between a complete and a half answer.

- [ ] **Step 3: Run, verify pass.** `cargo test -p goldengraph-core`.
- [ ] **Step 4: Commit** `test(goldengraph): differentiator — resolution merges facts a 1-hop query then finds`.

---

## Task 4: `NativeResolver` (kernel reuse: score-core + graph-core)

**Files:** `$EXT/goldengraph-core/src/resolve.rs` (extend).

- [ ] **Step 1: Read** `score-core/src/lib.rs` `score_one` match arms to get the `scorer_id` for jaro_winkler; record it in a comment.

- [ ] **Step 2: Write the failing test:** on the Task-1 mentions, `resolve_native(&mentions, &NativeConfig{ scorer_id: <jw>, threshold: 0.85 })` returns a `mention->entity-id` map that groups 0 and 1 (Apple Inc/Apple score >= 0.85 within the `org` type-block) and leaves 2, 3 singletons. Then `apply_resolution(&mentions,&edges,&map)` has 3 entities + both edges (same end state as the Provided path).

- [ ] **Step 3: Implement:**

```rust
#[derive(Clone, Debug)]
/// `threshold` is on the [0,1] scale (every `score_one` scorer_id returns [0,1]).
pub struct NativeConfig { pub scorer_id: u8, pub threshold: f64 }

pub enum ResolutionMode { Provided(std::collections::HashMap<MentionId, EntityId>), Native(NativeConfig) }

/// Native resolver: type-block, all-pairs score (score-core), threshold -> pairs,
/// WCC (graph-core) -> clusters -> mention->entity-id map. Reuses the kernels; no new ER logic.
pub fn resolve_native(mentions: &[Mention], cfg: &NativeConfig) -> std::collections::HashMap<MentionId, EntityId> {
    use std::collections::HashMap;
    // 1. block by type
    let mut blocks: HashMap<&str, Vec<MentionId>> = HashMap::new();
    for (i, m) in mentions.iter().enumerate() { blocks.entry(m.typ.as_str()).or_default().push(i); }
    // 2. all-pairs score within block -> edges (i64,i64,f64) above threshold
    let mut pair_edges: Vec<(i64, i64, f64)> = Vec::new();
    for ids in blocks.values() {
        for a in 0..ids.len() {
            for b in (a + 1)..ids.len() {
                let (i, j) = (ids[a], ids[b]);
                let s = score_core::score_one(cfg.scorer_id, &mentions[i].name, &mentions[j].name);
                if s >= cfg.threshold { pair_edges.push((i as i64, j as i64, s)); }
            }
        }
    }
    // 3. WCC over all mention ids -> clusters
    let all_ids: Vec<i64> = (0..mentions.len() as i64).collect();
    let clusters = graph_core::connected_components(&pair_edges, &all_ids);
    // 4. assign entity ids (cluster's min mention id -> stable EntityId via sorted order)
    let mut map = HashMap::new();
    let mut sorted: Vec<Vec<i64>> = clusters;
    sorted.sort_by_key(|c| *c.iter().min().unwrap());
    for (eid, cluster) in sorted.iter().enumerate() {
        for &mid in cluster { map.insert(mid as MentionId, eid as EntityId); }
    }
    map
}
```

> Confirm `connected_components` returns EVERY id (singletons as 1-element clusters) — if it only returns multi-member clusters, add the unclustered mention ids as singletons before assigning entity ids. The test catches this.

- [ ] **Step 4: Add a `build_graph` entry in `lib.rs`** that takes `mentions, edges, ResolutionMode` and dispatches: `Provided(map) -> apply_resolution(...)`, `Native(cfg) -> apply_resolution(.., &resolve_native(..))`.

- [ ] **Step 5: Run, verify pass; clippy clean.**
- [ ] **Step 6: Commit** `feat(goldengraph): native explicit-config resolver (score-core + graph-core kernel reuse)`.

---

## Task 5: `goldengraph-native` pyo3 binding

**Files:** Create `$EXT/goldengraph-native/{Cargo.toml,pyproject.toml,src/lib.rs,tests/test_goldengraph.py}`; Modify `$EXT/Cargo.toml`.

- [ ] **Step 1: Read** `$EXT/goldencheck-native/{Cargo.toml,pyproject.toml,src/lib.rs}` — copy its abi3/maturin shape (the `[lib] crate-type=["cdylib"]`, `pyo3` with `abi3-py311` + `extension-module`, the maturin `[build-system]`, the module-init pattern). Mirror it for goldengraph-native (path dep `goldengraph-core`).

- [ ] **Step 2: Add `"goldengraph-native"` to `[workspace].exclude`** in `$EXT/Cargo.toml` (mirror the `goldencheck-native` entry + comment).

- [ ] **Step 3: Implement `src/lib.rs`** — a pyo3 module exposing:
  - `build_graph(mentions: list[(name, typ)], edges: list[(subj, predicate, obj, source_ref)], resolution) -> Graph` where `resolution` is either a `dict[int,int]` (Provided) or `("native", scorer_id, threshold)` (Native). Convert to the core types, call core `build_graph`, wrap the result `Graph` in a `#[pyclass] PyGraph`.
  - `PyGraph.query(seeds: list[int], hops: int) -> dict` returning `{"entities": [{entity_id, canonical_name, typ, members}], "edges": [{subj, predicate, obj, source_refs}]}` (plain dicts).
  - `PyGraph.seeds_by_name(name: str) -> list[int]`.
  Keep the conversion explicit + small.

- [ ] **Step 4: `pyproject.toml`** (mirror goldencheck-native: maturin backend, `name = "goldengraph-native"`, abi3).

- [ ] **Step 5: Write `tests/test_goldengraph.py`:** the Task-3 differentiator, but through the Python API — build via Provided exact vs resolved maps, `query(seeds, 1)`, assert all-facts vs half. Plus a Native-path test (`("native", <jw>, 0.85)`) merging Apple. (These RUN IN CI — Task 7.)

- [ ] **Step 6: Confirm it compiles locally** (rust preamble): `cargo build -p goldengraph-native`. Do NOT chase maturin/pytest locally — CI runs them.
- [ ] **Step 7: Commit** `feat(goldengraph): goldengraph-native pyo3 binding (build_graph/query/seeds_by_name)`.

---

## Task 6: Golden vectors

**Files:** `$EXT/goldengraph-core/tests/fixtures/goldengraph_golden.json`, extend `tests/integration.rs`.

- [ ] **Step 1:** Author `goldengraph_golden.json`: a canonical `(mentions, edges, resolution-map, queries[seeds,hops])` -> expected `(graph entities+edges, subgraphs)`, serialized deterministically (sorted). Cover: a merge, an edge dedup, a 1-hop and a 2-hop query.
- [ ] **Step 2:** A Rust test loads the fixture, runs `build_graph`/`query`, and asserts byte-equality against the expected (serialize both to canonical JSON and compare). This is the cross-binding contract SP3's WASM/C will reuse.
- [ ] **Step 3:** Run, verify pass. Commit `test(goldengraph): golden vectors for cross-binding parity`.

---

## Task 7: CI lane

**Files:** Create `.github/workflows/goldengraph.yml`.

- [ ] **Step 1:** Read an existing rust lane (e.g. the `rust` job in `.github/workflows/ci.yml` or a `*-native` publish/test workflow) for the toolchain setup + maturin pattern. Trigger on push to `packages/rust/extensions/goldengraph-core/**`, `.../goldengraph-native/**`, and the workflow file; plus `workflow_dispatch`.
- [ ] **Step 2:** Jobs:
  - `core`: `cargo test -p goldengraph-core` + `cargo clippy -p goldengraph-core -- -D warnings` (run from `$EXT`).
  - `native`: set up Python 3.12, `pip install maturin pytest`, `cd $EXT/goldengraph-native && maturin develop`, then `pytest tests/ -q`. (No goldenmatch dep needed — the engine is self-contained; the Native path uses only the Rust kernels.)
- [ ] **Step 3:** YAML parse check. Commit `ci(goldengraph): cargo test + clippy (core) and maturin + pytest (native)`.

---

## Task 8: PR + auto-merge

- [ ] Confirm `origin/main` (queue rebases; no manual rebase unless conflict). Run `cargo fmt` (rust preamble) on both crates + confirm `cargo clippy` clean before pushing (the merge-queue full matrix runs rust lints).
- [ ] Push `claude/goldengraph-core`; open PR (base `main`, gh `benzsevern`). Body: SP1 of the goldengraph program (link the spec's program section), what's in/out, the `-core`/`-native` split, the dual-path resolution.
- [ ] The `goldengraph` lane is informational (not in `ci-required`) — confirm it green before arming auto-merge (it's the only build signal for the pyo3 binding). Arm `gh pr merge <N> --auto --squash` and STOP. Use @superpowers:finishing-a-development-branch.

## Out of scope (SP1 — do NOT build)
LLM extraction/synthesis (SP2), embedding-seeded retrieval (SP2), persistence (later), TS/WASM/C bindings (SP3), the native zero-config controller (future port), community/global retrieval, incremental mutation, sketch-core LSH blocking (scale follow-up).

## Risks / watch-items
- **Real crate names** of score-core/graph-core (`goldenmatch-score-core`? `goldenmatch-graph-core`?) — Task 0 Step 2 confirms from their `[package].name`; use the real names in deps + `use`.
- **`connected_components` singleton behavior** — Task 4 Step 3 note; the test catches it.
- **abi3/maturin only in CI** — Tasks 5/7; don't burn time on local maturin.
- **Determinism** — sort entities/edges/subgraph everywhere; the golden-vector test is the guard.
