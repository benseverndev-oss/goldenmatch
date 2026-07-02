# Node Provenance — Design

**Date:** 2026-07-02
**Branch:** `feat/node-provenance` (off `main`)
**Program:** goldengraph substrate-quality arc — the **engine fix** the presence-aligner probe (PR #1367) greenlit. Stamps `source_refs` on entity nodes so edgeless-but-present entities are doc-attributed and the shipped aligner reaches them per-doc. Recovers the real-prose coverage the edge-only aligner was under-counting.

**Source note:** the probe proved the prize is real and safe (relaxed coverage 0.49→1.0 at P(B)=1.0, both 7B and V3), globally. This fix does the same *per-doc* — a strict subset of global, so P(B) is at least as good. See `docs/superpowers/reports/2026-07-02-presence-aligner-probe-verdict.md`.

## Problem

Provenance lives on edges, not nodes: `BatchEntity`/`StoredEntity` (`goldengraph-core/src/store.rs`) carry no `source_refs`, and the aligner's per-doc candidate set is built only from edge endpoints. So an entity extracted from a doc but with no surviving relation has no doc association and is structurally unreachable by the doc-keyed aligner — the ~51% coverage wall. The entities exist and are correctly resolved (P=1.0); the metric just can't see them.

## Goal

Thread a doc id onto entity nodes end-to-end (build → store → query → aligner), mirroring exactly what edges already do, so the shipped `_assign_real_nodes_aliased` reaches edgeless nodes per-doc. Ships default-on as a correctness fix (the edge-only aligner under-counted). Back-compat: graphs/batches without node `source_refs` behave exactly as today (serde default on the Rust side, edge fallback on the aligner side).

## Non-goals

- Not the metric-reporting split (presence-coverage vs relational-R(B)) — a separate follow-on sub-project.
- No change to the engineered-corpus aligner (`_assign_nodes`, doc-id oracle) — untouched.
- No PyPI wheel republish in this change (the Modal bench builds `goldengraph-native` from source; republish is a separate optional rollout step).
- No new resolution/merge logic — `source_refs` union rides on the existing record-key merge.

## Architecture — four layers, mirroring the edge-provenance pattern

```
build_batch (Python, ingest.py)   stamp source_refs=[doc_id] on each out_entity (edges already do this)
        │  JSON append
        ▼
core store (Rust, goldengraph-core/src/store.rs)
        │   BatchEntity/StoredEntity gain source_refs; UNION on the cross-doc record-key merge
        │   (the same union the edges already do at store.rs ~350-360)
        ▼
native wrapper (Rust, goldengraph-native/src/lib.rs)
        │   the TWO hand-built entity dicts (entities() ~72-78, query serializer ~161-167) add source_refs
        ▼  query
graph dict (Python)   each entity dict carries "source_refs": [docs it was extracted from]
        │
        ▼
aligner (bench, substrate_eval._assign_real_nodes_aliased)
        per-doc candidate set from ENTITY source_refs when present (reaching edgeless nodes),
        else edge fallback. Shipped coverage/F1 rises to the true ~0.82 (7B) / ~0.90 (V3).
```

**Precision (why safe):** the probe reached these nodes *globally* (any doc) at P(B)=1.0; this fix reaches them *per-doc* (only nodes whose `source_refs` contain the gold's doc) — a strict subset — so P(B) is at least as good. The probe was the measure-first gate; it passed.

## Components

### 1. Rust core (`goldengraph-core/src/store.rs`)

- `BatchEntity` (~line 36): add `#[serde(default)] pub source_refs: Vec<String>` — `serde(default)` so older JSON batches without the field still deserialize (back-compat).
- `StoredEntity` (~line 67): add `pub source_refs: Vec<String>`.
- `append` merge path (~307-333): a brand-new entity takes its batch `source_refs` verbatim; an entity that merges into an existing one by record-key overlap **unions** `source_refs` (dedup-preserving, sorted for stable snapshots — mirror the edge union at ~350-360).
- Union semantics: an entity from docs A and B (name_ci cross-doc merge) ends with `source_refs = {A, B}` and aligns as a candidate in both docs (correct — it appears in both).

### 2. Rust native wrapper (`goldengraph-native/src/lib.rs`)

The entity dict is hand-built in **two** spots — `entities()` (~72-78) and the query serializer (~161-167). Add `d.set_item("source_refs", PyList::new(py, refs)?)?` to **both**, or Python never sees the field.

### 3. Python `build_batch` (`ingest.py`)

Stamp `source_refs` on each `out_entity` dict (and each literal-leaf entity), mirroring the edge stamp (`"source_refs": list(refs)` where `refs = [source_ref] if source_ref else []`). One additive change; edges are unchanged.

### 4. Aligner (`substrate_eval._assign_real_nodes_aliased`)

Build the per-doc candidate set from entity `source_refs` when any entity carries them:
```
node_by_doc: dict[str, set[int]] = defaultdict(set)
have_node_prov = False
for e in graph.get("entities", ()):
    for ref in e.get("source_refs", ()):
        have_node_prov = True
        node_by_doc[_base_doc_id(ref)].add(e["entity_id"])
# candidate set for a gold's doc:
#   node_by_doc[doc] if have_node_prov  (superset of edge endpoints, incl. edgeless)
#   else the existing edge-derived by_doc  (back-compat, unchanged)
```
The alias-set + exact-before-substring matching afterward is unchanged — only the candidate *set* widens. `real_alignment_coverage_aliased` and `align_real_mentions_to_nodes_aliased` (which call `_assign_real_nodes_aliased`) inherit the fix automatically.

## Error handling / back-compat

- **serde default** on the new Rust fields → old serialized graphs and older JSON batches load unchanged (empty `source_refs`).
- **Aligner edge fallback** → a graph with no node `source_refs` (e.g. produced by an old wheel, or the engineered path) aligns exactly as today. The fix only activates when node provenance is present.
- The engineered-corpus path uses a different aligner (`_assign_nodes`) and is untouched.

## Measurement

Rerun the **shipped** `run_wiki` (no probe flag — the fix is *in* the shipped strict aligner) on 7B-seeded (seed 42) + V3 best config (`name_ci` + chunking `(6,2)`). Expected, matching the probe's relaxed numbers now shipped per-doc:

| | coverage | R(B) | P(B) | F1(B) |
|---|---|---|---|---|
| 7B before | 0.49 | 0.30 | 1.0 | 0.465 |
| 7B after (target) | ~1.0 | ~0.70 | ~1.0 | ~0.82 |
| V3 after (target) | ~1.0 | ~0.81 | ~1.0 | ~0.90 |

- **WIN (fix landed):** coverage → ~1.0, F1 → ~0.82/~0.90, **P(B) holds ~1.0**. The per-doc numbers should match the probe's global relaxed numbers (or be *slightly higher* precision, being per-doc).
- **Precision regression (unexpected):** P(B) drops below the probe's 1.0 → a per-doc collision the global probe didn't surface; investigate before shipping default-on.

## Testing

- **Rust (`cargo test -p goldengraph-core`, local — the crate is pure serde, builds in ~17s):** extend `tests/store_integration.rs`: (a) append a batch whose entity has `source_refs=["docA"]` → query returns it with that ref; (b) two record-key-merging batches → entity `source_refs` is the union `{docA, docB}`; (c) an entity with NO edges still carries its `source_refs` (the point — edgeless provenance survives).
- **Python box (`build_batch`):** `build_batch(extraction, entities, at=1, source_ref="docA")` puts `["docA"]` on every out-entity and every literal-leaf entity.
- **Python box (aligner):** a hand-built graph with an edgeless node carrying `source_refs=["docA"]` and a matching gold in docA → the node now aligns (was orphan under edge-only); a graph with NO node `source_refs` → aligner output byte-identical to today (regression guard on the fallback); the existing `test_substrate_eval.py` suite stays green.
- **Modal:** one 7B-seeded + one V3 `run_wiki` leg for the end-to-end coverage lift.

Box invocations: goldengraph `.venv` + `PYTHONPATH` shadow for Python; `cargo test -p goldengraph-core` with the D: rustup toolchain on PATH for Rust.

## Rollout

Default-on correctness fix. The native change is backward-compatible reads (serde default); the Modal bench rebuilds the wheel from source, so no republish is needed for the measurement. A PyPI `goldengraph-native` republish (so `pip install goldenmatch[native]` users get node provenance) is a separate, optional rollout step noted in the verdict, not part of this change. If the measurement confirms the coverage lift at P(B)~1.0, the arc's next sub-project is the metric-reporting split (presence-coverage vs relational-R(B)).
