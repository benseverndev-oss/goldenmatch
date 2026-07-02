# Node Provenance — Design

**Date:** 2026-07-02
**Branch:** `feat/node-provenance` (off `main`)
**Program:** goldengraph substrate-quality arc — the **engine fix** the presence-aligner probe greenlit. Stamps `source_refs` on entity nodes so an entity that is present in the graph but has no *surviving edge in a given doc* becomes reachable by the shipped aligner **in that doc**. Recovers the real-prose coverage the edge-only aligner was under-counting.

**Source note:** the probe proved the prize is real and safe (relaxed coverage 0.49→1.0 at P(B)=1.0, both 7B and V3), reaching those nodes *globally*. This fix does the same *per-doc* — a strict subset of global, so P(B) is at least as good. The probe verdict `docs/superpowers/reports/2026-07-02-presence-aligner-probe-verdict.md` is on the unmerged `feat/presence-aligner-probe` branch (PR #1367, in the merge queue) — not yet on `main`.

## Problem — and the precise population this recovers

Provenance lives on edges, not nodes: `BatchEntity`/`StoredEntity` (`goldengraph-core/src/store.rs`) and `EntityNode` (`goldengraph-core/src/model.rs`) carry no `source_refs`; only edges do. The aligner's per-doc candidate set is built only from edge endpoints, so a gold mention in doc *D* can only align to a node that has a **surviving edge sourced from *D***.

**Critical scoping (from the spec review of the real query path):** the bench graph is `store.as_of(...).query(...)` (`run_substrate_eval.py:59-61`), and `as_of` (`store.rs:401-428`) emits **only entities that are an endpoint of a surviving edge somewhere**. So there are two distinct "unaligned" populations:

1. **Per-doc-relationless (globally edged)** — the entity IS in the queried graph (it has a surviving edge in *some* doc), but not an edge in the gold's doc *D*. Example: a `name_ci`-merged entity spanning docs A and B whose only surviving edge is sourced from B; a gold mention of it in A can't reach it. **This is the population the probe recovered** (relaxed coverage reached 1.0 over `graph["entities"]`, so every recovered entity is already in the graph → globally edged). This fix targets exactly this population.
2. **Truly isolated (no surviving edge anywhere)** — dropped by `as_of`, never in the queried graph. Since the probe hit coverage 1.0 *without* these, there are effectively none among the 65 gold on this corpus. **Recovering them would require `as_of` to emit isolated entities — an out-of-scope behavior change this design does NOT make.**

The fix: give each entity `source_refs` = the docs it was extracted from (accreted across cross-doc merges), carry it through `as_of`→`EntityNode`→the Python dict, and let the aligner add "nodes whose `source_refs` contain doc *D*" to the per-doc candidate set. A per-doc-relationless entity extracted in A and B ends with `source_refs = {A, B}` and becomes a candidate in A even though its only edge is in B.

## Goal

Thread `source_refs` onto entity nodes end-to-end (build → store-merge → `as_of` → `EntityNode` → native dict → aligner), mirroring the doc-id threading edges already have, so the shipped `_assign_real_nodes_aliased` reaches per-doc-relationless nodes. Default-on correctness fix. Back-compat: graphs/batches without node `source_refs` behave exactly as today.

## Non-goals

- **Not** recovering truly-isolated entities (no edge anywhere) — no `as_of` behavior change (see scoping above).
- Not the metric-reporting split (presence-coverage vs relational-R(B)) — a separate follow-on.
- No change to the engineered-corpus aligner (`_assign_nodes`, doc-id oracle).
- No PyPI wheel republish in this change (the Modal bench builds `goldengraph-native` from source).

## Architecture — the full data path (five mutation points)

```
build_batch (Python, ingest.py)     stamp source_refs=[doc_id] on each out_entity + literal-leaf entity
        │  JSON append                (edges already stamp list(refs))
        ▼
core store (Rust, goldengraph-core/src/store.rs)
        │  (1) BatchEntity + (2) StoredEntity gain source_refs (both #[serde(default)])
        │  (3) append merge: ACCRETIVE union survivor ∪ absorbed ∪ batch  (NOT the record_keys rule)
        ▼
as_of (Rust, store.rs ~416-428)      (4) EntityNode gains source_refs (#[serde(default)]) and as_of
        │                                COPIES se.source_refs into the EntityNode it builds
        ▼
native wrapper (Rust, goldengraph-native/src/lib.rs)
        │  (5) BOTH hand-built entity dicts add source_refs:
        │      entities() (~69-82) and graph_view_to_dict / query serializer (~160-168)
        ▼  query
graph dict (Python)   each entity dict carries "source_refs": [docs it was extracted from]
        │
        ▼
aligner (bench, substrate_eval._assign_real_nodes_aliased)
        per-doc candidate set = edge-endpoints(doc)  ∪  {nodes whose source_refs contain doc}
        (a UNION -> always a superset of today, never a regression; degrades to today when absent)
```

**Precision (why safe):** the probe reached these nodes *globally* at P(B)=1.0; this fix reaches them *per-doc* (only nodes whose `source_refs` contain the gold's doc) — a strict subset — so P(B) is at least as good.

## Components

### 1. Rust core structs (`goldengraph-core/src/store.rs`, `model.rs`)

- `BatchEntity` (~35-43): add `#[serde(default)] pub source_refs: Vec<String>`.
- `StoredEntity` (~66-76): add `#[serde(default)] pub source_refs: Vec<String>` — **`serde(default)` is required** so pre-existing snapshots (loaded via `serde_json::from_str` in `GraphStore::open`, `store.rs:142`) still deserialize.
- `EntityNode` (`model.rs:46-54`): add `#[serde(default)] pub source_refs: Vec<String>` — this is the struct the native dicts actually read, and it is part of the golden-vector cross-binding contract (`model.rs:8-10`), so `serde(default)` keeps that snapshot format back-compat.

### 2. Rust accretive union on merge (`store.rs` append, ~301-324)

`source_refs` is **monotonic provenance** — a doc a node was ever extracted from must never leave the node, even on a record-key split. So it needs a rule *distinct* from `record_keys` (which the authoritative-batch comment at 301-305 deliberately lets shrink on a split):

- New entity: `source_refs` = its batch `source_refs`.
- Merge (batch entity absorbs into / merges with an existing survivor): `source_refs` = **union of** the surviving entity's existing `source_refs` ∪ every absorbed entity's `source_refs` ∪ the batch entity's `source_refs`. Sorted + deduped for stable snapshots.

This is accretive (never loses a doc); do **not** copy the `record_keys` fold literally. (The earlier draft's "mirror the edge union at 350-360" was wrong — that is a per-edge dedup of one edge's own refs, not a cross-doc union; edge cross-source union actually happens at query time in `as_of` via a `BTreeSet`. Entities need the accretive rule specified here.)

### 3. `as_of` carries `source_refs` into `EntityNode` (`store.rs` ~416-428)

The `EntityNode` builder inside `as_of` currently copies `canonical_name`/`typ`/`surface_names`/`members` but not `source_refs`. Add `source_refs: se.source_refs.clone()`. Without this, the field exists on `StoredEntity` but never reaches the Python graph — the fix would silently no-op.

### 4. Native dict serialization (`goldengraph-native/src/lib.rs`, two spots)

The entity dict is hand-built (not serde-passthrough) in `entities()` (~69-82) and the query serializer (~160-168). Add to **both**:
```rust
d.set_item("source_refs", PyList::new(py, e.source_refs.iter().map(String::as_str))?)?;
```

### 5. Python `build_batch` (`ingest.py`, ~98-107 and ~128-136)

Stamp `"source_refs": list(refs)` on each `out_entity` dict and each literal-leaf entity dict, mirroring the edge stamp (`refs = [source_ref] if source_ref else []`). Edges unchanged.

### 6. Aligner (`substrate_eval._assign_real_nodes_aliased`, ~118)

Add node-provenance nodes to the per-doc candidate set as a **union** (not a replace):
```
node_by_doc: dict[str, set[int]] = defaultdict(set)
for e in graph.get("entities", ()):
    for ref in e.get("source_refs", ()):
        node_by_doc[_base_doc_id(ref)].add(e["entity_id"])
# existing edge-derived by_doc unchanged; candidate set for a gold's doc:
cands = sorted(by_doc.get(_base_doc_id(doc), set()) | node_by_doc.get(_base_doc_id(doc), set()))
```
The union guarantees candidates ⊇ today's set for every doc → **never a coverage regression**, even on a mixed graph (some entities with `source_refs`, some without — e.g. old snapshot + new appends). When no entity has `source_refs`, `node_by_doc` is empty and the candidate set is byte-identical to today. Alias-set + exact-before-substring matching afterward is unchanged. `real_alignment_coverage_aliased` / `align_real_mentions_to_nodes_aliased` delegate to this function, so they inherit the fix.

## Error handling / back-compat

- **`serde(default)`** on all three new Rust fields (`BatchEntity`, `StoredEntity`, `EntityNode`) → old JSON batches, persisted snapshots, and golden vectors load unchanged (empty `source_refs`).
- **Aligner union** → a graph with no node `source_refs` aligns byte-identically to today; a *mixed* graph never regresses (union is a superset). The fix activates additively.
- Engineered-corpus path (`_assign_nodes`) untouched.

## Measurement

Rerun the **shipped** `run_wiki` (no probe flag — the fix is in the shipped aligner) on 7B-seeded (seed 42) + V3 best config (`name_ci` + chunking `(6,2)`).

| | coverage | R(B) | P(B) | F1(B) |
|---|---|---|---|---|
| 7B before | 0.49 | 0.30 | 1.0 | 0.465 |
| 7B after (target) | ~1.0 | ~0.70 | ~1.0 | ~0.82 |
| V3 after (target) | ~1.0 | ~0.81 | ~1.0 | ~0.90 |

- **WIN (fix landed):** coverage → ~1.0, F1 → ~0.82/~0.90, **P(B) holds ~1.0** — matching the probe's relaxed numbers, now shipped per-doc (P(B) may be *slightly higher* than the probe's global relaxed, being per-doc).
- **No-op (regression to catch):** coverage flat ~0.49 → the field didn't reach the aligner (a missed layer — most likely `as_of` not copying, or the native dict not serializing). The tiered tests below are designed to catch each layer *before* the Modal run.
- **Precision regression (unexpected):** P(B) < 1.0 → a per-doc collision the global probe didn't surface; investigate before default-on.

## Testing — tiered, one tier per layer

- **Rust (`cargo test -p goldengraph-core`, local; pure serde, ~17s build):** in `tests/store_integration.rs`:
  - (a) append a batch whose entity has `source_refs=["docA"]`; after `as_of(...)` the emitted `EntityNode` (and the queried graph view) carries `source_refs=["docA"]` — this asserts the **`as_of`→`EntityNode` copy (layer 4)**, the layer most likely to be silently missed.
  - (b) two record-key-merging batches with `source_refs=["docA"]` and `["docB"]` → the merged entity's `source_refs` is the accretive union `{docA, docB}` (assert a doc is **not** lost — the C3 accretive rule).
  - (c) a survivor that later *splits* on a record-key change retains its earlier doc in `source_refs` (accretive/monotonic guard — distinguishes it from `record_keys`).
- **Python box (`build_batch`):** `build_batch(extraction, entities, at=1, source_ref="docA")` puts `["docA"]` on every out-entity and every literal-leaf entity.
- **Python box (aligner):** (i) a hand-built graph with an entity that is an edge endpoint only in docB but has `source_refs=["docA","docB"]`, and a gold for it in docA → it now aligns in docA (was orphan under edge-only); (ii) **no** node `source_refs` → aligner output byte-identical to today (fallback regression guard); (iii) **mixed** graph (one entity with `source_refs`, one without) → no regression vs today for the without-provenance entity (union-superset guard). Existing `test_substrate_eval.py` suite stays green.
- **Modal:** one 7B-seeded + one V3 `run_wiki` leg for the end-to-end coverage lift.

**Coverage gap (accepted):** mutation point 5 — the pyo3 dict serialization in `lib.rs` (`entities()` and the query serializer) — has **no pre-Modal test tier**. `cargo test -p goldengraph-core` covers the core crate (`EntityNode`/`as_of`) but not the pyo3 wrapper, and the Python aligner tier uses hand-built dicts. So the native-serialize layer is verified only by the Modal `run_wiki` leg (a flat-coverage no-op there localizes the miss to this layer, since every other layer has a unit test). This is a pragmatic omission given the pyo3 local-build constraints, not a correctness risk.

Box invocations: goldengraph `.venv` + `PYTHONPATH` shadow (Python); `cargo test -p goldengraph-core` with the D: rustup toolchain on PATH (Rust).

## Rollout

Default-on correctness fix; back-compat via `serde(default)` + the aligner union. The Modal bench rebuilds the wheel from source, so no republish is needed for the measurement. A PyPI `goldengraph-native` republish (so `pip install goldenmatch[native]` users get node provenance) is a separate, optional rollout step noted in the verdict. If the measurement confirms coverage → ~1.0 at P(B)~1.0, the arc's next sub-project is the metric-reporting split.
