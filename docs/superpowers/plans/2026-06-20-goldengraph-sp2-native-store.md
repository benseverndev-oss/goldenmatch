# goldengraph SP2 — native portable store + bi-temporal model — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a durable, portable, bi-temporal store to `goldengraph-core` — persist a resolution-merged graph, reconcile incremental appends by host-supplied record keys, and answer two-axis `as_of(valid_t, tx_t)` queries.

**Architecture:** A new pyo3-free `store.rs` in `goldengraph-core`. Identity is keyed on an opaque host-supplied `record_key` (the suite's `:h1:` fingerprint; the core never computes it). Reconciliation uses the plurality-heir rule (one rule → total + collision-free). Edges are append-only bi-temporal versions; `as_of` picks the version with the greatest `ingested_at ≤ tx_t` per triple, then filters the valid window. Snapshot is canonical `serde_json`.

**Tech Stack:** Rust 2021, `serde` + `serde_json` (promoted to real deps), reuses SP1's `model.rs` (`Graph`/`EntityNode`/`Edge`). pyo3-free → `cargo test` runs locally.

**Spec:** `docs/superpowers/specs/2026-06-20-goldengraph-sp2-native-store-design.md`

---

## Context the implementer needs

- **Worktree:** `.worktrees/goldengraph-sp2`, branch `claude/goldengraph-sp2-store` (off merged main; SP1 + `goldengraph-core` are on main). Crate dir: `packages/rust/extensions/goldengraph-core`.
- **Rust preamble — prefix EVERY cargo command:** `export PATH="/c/Users/bsevern/.cargo/bin:$PATH" && export RUSTUP_HOME="C:/Users/bsevern/.rustup" && export CARGO_HOME="C:/Users/bsevern/.cargo"`. Run cargo from inside the crate dir (the crate is its own `[workspace]`; `-p goldengraph-core` fails from `$EXT`).
- **Test/clippy:** `cargo test` (from crate dir) and `cargo clippy --all-targets -- -D warnings`. CI (`.github/workflows/goldengraph.yml` core job) already runs these via `--manifest-path` — no workflow change needed.
- **Determinism rule:** no wall-clock in the core; all times are caller-supplied `i64`. No `HashMap` in serialized state (use `BTreeMap`/sorted `Vec`).
- **Commits as Claude:** `git -c user.name=Claude -c user.email=noreply@anthropic.com commit`.
- **SP1 reuse:** `as_of` returns a `crate::model::Graph` (so `neighborhood`/`seeds_by_name` work on it unchanged). `EntityNode { entity_id: u32, canonical_name, typ, members: Vec<usize>, surface_names: Vec<String> }`, `Edge { subj: u32, predicate, obj: u32, source_refs }`.

## File structure

```
packages/rust/extensions/goldengraph-core/Cargo.toml      MODIFY  promote serde+serde_json to [dependencies]
packages/rust/extensions/goldengraph-core/src/lib.rs      MODIFY  add `pub mod store;`
packages/rust/extensions/goldengraph-core/src/store.rs     CREATE  all store types + impl + unit tests
packages/rust/extensions/goldengraph-core/tests/fixtures/store_golden.json  CREATE  store golden vectors
packages/rust/extensions/goldengraph-core/tests/store_integration.rs        CREATE  golden-vector test
```

---

## Task 0: Deps + module scaffold

**Files:** Modify `Cargo.toml`, `src/lib.rs`; Create `src/store.rs`.

- [ ] **Step 1:** In `Cargo.toml`, add to `[dependencies]` (keep `serde_json` in dev too is fine, but it must be a real dep now):
```toml
serde = { version = "1", features = ["derive"] }
serde_json = "1"
```
Remove the now-redundant `serde_json` line from `[dev-dependencies]` (it's a real dep now; dev still sees it).

- [ ] **Step 2:** `src/lib.rs` — add `pub mod store;` after `pub mod retrieve;`.

- [ ] **Step 3:** Create `src/store.rs` with a `// placeholder` + a trivial `#[cfg(test)] mod t { #[test] fn builds() { assert_eq!(2+2,4); } }`.

- [ ] **Step 4:** Run `cargo test` (preamble; from crate dir). Expected: compiles, all pass (SP1 tests + the trivial one).

- [ ] **Step 5:** Commit `feat(goldengraph): SP2 scaffold — serde deps + store module`.

---

## Task 1: Store types + empty `GraphStore`

**Files:** `src/store.rs`.

- [ ] **Step 1: Failing test** — construct an empty store and assert it's empty:
```rust
#[test]
fn empty_store_is_empty() {
    let s = GraphStore::open(None).unwrap();
    assert!(s.entities.is_empty() && s.edges.is_empty() && s.history.is_empty());
    assert_eq!(s.next_id, 0);
}
```

- [ ] **Step 2:** Run, verify fail (types undefined).

- [ ] **Step 3: Implement the types** (per spec "Data model"): `StableId = u64`; `BatchEntity`, `BatchEdge`, `StoreBatch`, `StoredEntity`, `StoredEdge`, `HistoryEvent` (enum `Merge`/`Split`), `GraphStore`, and `StoreError` (an enum with a `Parse(String)` variant). Derive `Serialize, Deserialize, Clone, Debug, PartialEq` on the stored types + `HistoryEvent`; `GraphStore` derives `Serialize, Deserialize, Clone, Debug, Default`. Implement `GraphStore::open(snapshot: Option<&str>) -> Result<Self, StoreError>`: `None` → `Self::default()`; `Some(s)` → `serde_json::from_str(s).map_err(|e| StoreError::Parse(e.to_string()))`.

- [ ] **Step 4:** Run, verify pass.
- [ ] **Step 5:** Commit `feat(goldengraph): SP2 store data model + open()`.

---

## Task 2: `append` — stable-id reconciliation (the core)

**Files:** `src/store.rs`. Implement the plurality-heir rule (spec "Stable-id reconciliation"). Build the test set FIRST, then implement until all pass.

- [ ] **Step 1: Failing tests** — add these (helpers: `be(local, keys)` builds a `BatchEntity` with given record_keys, no edges; `batch(entities, at)` builds a `StoreBatch`):
```rust
// new entity gets a fresh id
#[test] fn append_new_mints_id() { /* one entity {k1} -> StableId 0, next_id 1 */ }
// same record_key across two appends -> same id, no dup
#[test] fn append_unchanged_keeps_id() { /* append {k1} twice -> 1 entity, same id */ }
// n-way merge: a batch entity that is plurality-heir of two stored ids
#[test] fn append_merge_two_into_one() {
    // append e0={a}, e1={b} (ids 0,1). Then append one entity {a,b}:
    // it is heir of both -> keeps min(0,1)=0, absorbs 1, one Merge{kept:0,absorbed:[1]}.
}
// split: a stored entity's keys land across two batch entities
#[test] fn append_split_keeps_id_with_plurality_heir() {
    // append s={a,b,c} (id 0). Then append n1={a}, n2={b,c}:
    // heir of 0 = n2 (overlap 2>1) -> n2 keeps 0, n1 mints 1, one Split{from:0,into:[0,1]}.
}
// merge+split double-claim: two batch entities both overlap the same stored id, no collision
#[test] fn append_double_claim_no_collision() {
    // s={a,b,c,d} id0. batch n1={a,b}, n2={c,d}: heir=whichever has plurality;
    // tie (2 vs 2) -> heir = lex-smallest sorted record_keys (n1 "a,b"); n1 keeps 0, n2 mints 1.
    // assert no two entities share id 0.
}
```

- [ ] **Step 2:** Run, verify fail.

- [ ] **Step 3: Implement `append`** (entities only this task; edges in Task 3). Algorithm:
  1. Candidate stored entities = those `current_as_of(batch.ingested_at)` (helper: `superseded_at` is `None` or `> at`) sharing ≥1 key with any batch entity.
  2. For each candidate stored id `s`, compute its plurality-heir among batch entities (max `|keys(s) ∩ keys(N)|`; tie → batch entity with lex-smallest sorted `record_keys`).
  3. For each batch entity `N`: `inherited(N)` = stored ids whose heir is `N`. Empty → mint `next_id`; `{k}` → keep `k`; `{k1..}` → keep `min`, absorb rest (set `superseded_by`/`superseded_at`, push `Merge`), union their keys.
  4. For each candidate stored id whose keys landed across >1 batch entity → push `Split{from, into:[heir-id + other absorbing ids], at}`.
  5. Insert/update `StoredEntity`s (new ones `created_at = at`; merged keep the survivor; union + sort + dedup `record_keys` and `surface_names`).

- [ ] **Step 4:** Run, verify all Task-2 tests pass.
- [ ] **Step 5:** Commit `feat(goldengraph): SP2 append — plurality-heir stable-id reconciliation`.

---

## Task 3: `append` edge storage (remap + version)

**Files:** `src/store.rs`.

- [ ] **Step 1: Failing test:**
```rust
#[test] fn append_stores_edges_remapped_and_versioned() {
    // batch with entities e0={a}, e1={b} and edge (0 -"r"- 1, valid_from 10, valid_to None),
    // source_refs ["s2","s1"]; ingested_at 100.
    // -> one StoredEdge subj=0 obj=1 ingested_at=100, source_refs sorted ["s1","s2"].
}
```

- [ ] **Step 2:** Run, verify fail.
- [ ] **Step 3: Implement:** after entity assignment, remap each `BatchEdge.subj_local/obj_local` to the assigned `StableId` (absorbed → `kept`), set `ingested_at = batch.ingested_at`, sort+dedup `source_refs`, push a `StoredEdge` (append-only; do NOT mutate existing).
- [ ] **Step 4:** Run, verify pass.
- [ ] **Step 5:** Commit `feat(goldengraph): SP2 append — edge remap + bi-temporal versioning`.

---

## Task 4: `as_of(valid_t, tx_t)` — two-axis bi-temporal slice

**Files:** `src/store.rs`. Returns `crate::model::Graph`.

- [ ] **Step 1: Failing tests:**
```rust
// valid axis: edge with valid_to excluded after it, present within
#[test] fn as_of_valid_axis() { /* edge valid [10,20); as_of(15,_) present, as_of(25,_) absent */ }
// tx axis: a correction (later ingested_at) only visible at/after its tx-time
#[test] fn as_of_tx_axis_correction() {
    // append edge (X,r,Y) valid [10,None) ingested 100.
    // append correction (X,r,Y) valid [10,20) ingested 200 (retracts after 20).
    // as_of(valid=25, tx=150) -> edge PRESENT (only the open version is known); 
    // as_of(valid=25, tx=250) -> edge ABSENT (correction known, window ends at 20).
}
// supersession chain: as_of(_, before_merge) separate; as_of(_, after) merged
#[test] fn as_of_supersession_chain() { /* 2-hop merge chain across tx-times */ }
```

- [ ] **Step 2:** Run, verify fail.
- [ ] **Step 3: Implement `as_of`:**
  1. Group `StoredEdge`s by `(subj, predicate, obj)`; per group pick the version with greatest `ingested_at ≤ tx_t`; include iff `valid_from ≤ valid_t < valid_to` (open = `+∞`; empty window `valid_to ≤ valid_from` always excluded).
  2. For each included edge endpoint, resolve current id as-of `tx_t` (follow `superseded_by` while that hop's `superseded_at ≤ tx_t`).
  3. Build `Graph`: one `EntityNode` per endpoint id (from the `StoredEntity`, `members` empty — store doesn't track within-build mention ids; `surface_names` carried), `Edge` per included edge (mapped endpoints, `source_refs`). Sort entities by `entity_id`, edges by `(subj,predicate,obj)` (SP1 canonical order). `entity_id` here is the `StableId` truncated to `u32`? NO — keep `EntityId` as `u32`; map `StableId -> u32` deterministically by emitting entities in sorted `StableId` order and assigning sequential `EntityId`s, OR widen. DECISION (plan): assign sequential `u32` `EntityId`s in ascending `StableId` order for the returned `Graph` (a view-local id), and document it. The store's durable id is `StableId`; the returned `Graph` is an SP1 view.
- [ ] **Step 4:** Run, verify pass.
- [ ] **Step 5:** Commit `feat(goldengraph): SP2 as_of — two-axis bi-temporal slice -> SP1 Graph`.

---

## Task 5: Canonical snapshot + round-trip + order independence

**Files:** `src/store.rs`.

- [ ] **Step 1: Failing tests:**
```rust
#[test] fn snapshot_round_trip_byte_identical() {
    // build a store via appends; s1 = store.snapshot(); reopen; s2 = reopened.snapshot();
    // assert_eq!(s1, s2);
}
#[test] fn snapshot_within_batch_order_independent() {
    // two stores: same entities/edges but permuted order WITHIN one batch (same ingested_at);
    // assert_eq!(a.snapshot(), b.snapshot());
}
```

- [ ] **Step 2:** Run, verify fail.
- [ ] **Step 3: Implement `snapshot(&self) -> String`:** build a canonical serializable view — `entities` already a `BTreeMap` (key order); clone+sort `edges` by the full tuple `(subj, predicate, obj, valid_from, valid_to[None last], ingested_at, source_refs)`; `history` in order. `serde_json::to_string(&canonical).unwrap()`. (Ensure `append` already sorts each edge's `source_refs` + each entity's `record_keys`/`surface_names`, so only edge-vector order needs sorting here.)
- [ ] **Step 4:** Run, verify pass.
- [ ] **Step 5:** Commit `feat(goldengraph): SP2 canonical JSON snapshot + round-trip`.

---

## Task 6: `history(id)`

**Files:** `src/store.rs`.

- [ ] **Step 1: Failing test:** after a merge, `history(absorbed_id)` and `history(kept_id)` both return the `Merge` event (literal-mention; no chain-follow).
- [ ] **Step 2:** Run, verify fail.
- [ ] **Step 3: Implement:** filter `self.history` to events literally naming `id` in `kept`/`absorbed`/`from`/`into`.
- [ ] **Step 4:** Run, verify pass.
- [ ] **Step 5:** Commit `feat(goldengraph): SP2 history(id) lookup`.

---

## Task 7: Golden vectors (cross-binding contract)

**Files:** `tests/fixtures/store_golden.json`, `tests/store_integration.rs`.

- [ ] **Step 1:** Author `store_golden.json`: a canonical sequence of `(StoreBatch, ingested_at)` appends covering a merge + a bi-temporal edge + a correction, plus an expected canonical `snapshot` string and a list of `as_of(valid_t, tx_t)` queries → expected `Graph` views. Deterministic.
- [ ] **Step 2:** `tests/store_integration.rs`: load the fixture, replay the appends, assert `snapshot()` byte-equals the expected, and each `as_of` view byte-equals (serialize both to canonical JSON). Reuses the SP1 cross-binding contract pattern.
- [ ] **Step 3:** Run `cargo test`, verify pass. Commit `test(goldengraph): SP2 golden vectors (store snapshot + as_of)`.

---

## Task 8: Finalize — fmt, clippy, full suite, PR

- [ ] **Step 1:** `cargo fmt` (crate); `cargo clippy --all-targets -- -D warnings` clean; `cargo test` all green.
- [ ] **Step 2:** Push `claude/goldengraph-sp2-store`; open PR (base `main`, gh `benzsevern`). Body: SP2 of the goldengraph program (link spec + roadmap), what's in (durable store, bi-temporal `as_of`, record-key identity), what's out (binding=SP4, community=SP3, binary format later).
- [ ] **Step 3:** Confirm the `goldengraph` lane green (informational; the only build signal). Arm `gh pr merge <N> --auto --squash` and STOP. @superpowers:finishing-a-development-branch.

## Out of scope (SP2)
Pyo3/WASM/C bindings of the store (SP4/SP5). Community structure (SP3). LLM/extraction + record_key computation (SP4). Compact binary format. Concurrent writers. In-place mutation (corrections are append-only versions).

## Risks / watch-items
- **StableId → EntityId(u32) view mapping** in `as_of` (Task 4 Step 3): sequential-by-StableId assignment, view-local. Keep deterministic.
- **`current_as_of` chain-follow** must stop at hops with `superseded_at > tx_t` (Task 4); the 2-hop chain test pins it.
- **Edge sort tuple** must be total (Task 5) — include all fields; the within-batch-order test is the guard.
