# Node Provenance Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Thread `source_refs` onto entity nodes end-to-end (build_batch → core store accretive-union → `as_of`→`EntityNode` → native dicts → aligner union) so the shipped real-prose aligner reaches per-doc-relationless entities, recovering coverage 0.49→~1.0 at P(B)~1.0.

**Architecture:** Five mutation points across two Rust crates + Python, all additive and back-compat via `#[serde(default)]` and an aligner candidate-set UNION. Mirrors the doc-id threading edges already have; the presence probe (PR #1367) already proved the payoff is safe.

**Tech Stack:** Rust (goldengraph-core: pure serde; goldengraph-native: pyo3/maturin), Python (ingest.build_batch, erkgbench aligner). cargo test + box pytest + Modal.

**Spec:** `docs/superpowers/specs/2026-07-02-node-provenance-design.md`
**Branch:** `feat/node-provenance` (off `main`).

**Rust local build/test** (goldengraph-core is pure serde — builds ~17s):
```bash
export PATH="/d/.rustup/toolchains/1.94.0-x86_64-pc-windows-msvc/bin:/d/.cargo/bin:$PATH" CARGO_HOME=/d/.cargo
cd packages/rust/extensions/goldengraph-core && cargo test
```

**Python box-safe test invocation:**
```bash
PY="/d/show_case/goldenmatch/.venv/Scripts/python.exe"
# goldengraph (build_batch):
cd packages/python/goldengraph && PYTHONPATH="D:/show_case/gg-local-llm/packages/python/goldengraph" \
  POLARS_SKIP_CPU_CHECK=1 GOLDENGRAPH_NATIVE=0 "$PY" -m pytest tests/test_ingest_provenance.py -q -p no:cacheprovider
# erkgbench (aligner):
BENCH="D:/show_case/gg-local-llm/packages/python/goldenmatch/benchmarks/er-kg-bench"
cd "$BENCH" && PYTHONPATH="$BENCH" POLARS_SKIP_CPU_CHECK=1 "$PY" -m pytest tests/test_substrate_eval.py -q -p no:cacheprovider
```

## File structure

| File | Responsibility |
|---|---|
| `packages/rust/extensions/goldengraph-core/src/store.rs` | **Modify.** `BatchEntity`/`StoredEntity` gain `source_refs` (serde default); accretive union at the append merge; `as_of` copies `source_refs` into `EntityNode`. |
| `packages/rust/extensions/goldengraph-core/src/model.rs` | **Modify.** `EntityNode` gains `#[serde(default)] source_refs`. |
| `packages/rust/extensions/goldengraph-core/tests/store_integration.rs` | **Modify.** 3 tests (as_of carries refs; accretive merge union; split retains). |
| `packages/rust/extensions/goldengraph-native/src/lib.rs` | **Modify.** Both hand-built entity dicts (`entities()` + query serializer) add `source_refs`. |
| `packages/python/goldengraph/goldengraph/ingest.py` | **Modify.** `build_batch` stamps `source_refs` on out-entities + literal leaves. |
| `packages/python/goldengraph/tests/test_ingest_provenance.py` | **Create.** build_batch entity-provenance test. |
| `packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/substrate_eval.py` | **Modify.** `_assign_real_nodes_aliased` candidate set = edge-endpoints ∪ node-source_refs. |
| `packages/python/goldenmatch/benchmarks/er-kg-bench/tests/test_substrate_eval.py` | **Modify.** 3 aligner tests (recover; fallback byte-identical; mixed no-regress). |
| `docs/superpowers/reports/2026-07-02-node-provenance-verdict.md` | **Create** in Task 5. |

---

## Task 1: Rust core — struct fields + accretive union + as_of copy

**Files:**
- Modify: `goldengraph-core/src/store.rs`, `goldengraph-core/src/model.rs`
- Test: `goldengraph-core/tests/store_integration.rs`

- [ ] **Step 1: Write failing Rust tests.** Append to `tests/store_integration.rs` (match the existing test style there — `PyStore`/`GraphStore` + `StoreBatch`/`BatchEntity`):

```rust
// Node provenance: entity source_refs carried through as_of + accretively unioned on merge.
#[test]
fn as_of_carries_entity_source_refs() {
    let mut s = GraphStore::default();
    s.append(StoreBatch {
        entities: vec![BatchEntity {
            local_id: 0, canonical_name: "Apple".into(), typ: "org".into(),
            surface_names: vec!["Apple".into()], record_keys: vec!["k:apple".into()],
            source_refs: vec!["docA".into()],
        }],
        edges: vec![BatchEdge {
            subj_local: 0, predicate: "is".into(), obj_local: 0,
            valid_from: 1, valid_to: None, source_refs: vec!["docA".into()],
        }],
        ingested_at: 1,
    });
    let g = s.as_of(i64::MAX, i64::MAX);
    let e = g.entities.iter().find(|e| e.canonical_name == "Apple").unwrap();
    assert_eq!(e.source_refs, vec!["docA".to_string()]);
}

#[test]
fn merge_unions_source_refs_accretively() {
    let mut s = GraphStore::default();
    // batch 1: entity from docA (record key k1)
    s.append(StoreBatch {
        entities: vec![BatchEntity {
            local_id: 0, canonical_name: "IBM".into(), typ: "org".into(),
            surface_names: vec!["IBM".into()], record_keys: vec!["k:ibm".into()],
            source_refs: vec!["docA".into()],
        }],
        edges: vec![BatchEdge { subj_local: 0, predicate: "is".into(), obj_local: 0,
            valid_from: 1, valid_to: None, source_refs: vec!["docA".into()] }],
        ingested_at: 1,
    });
    // batch 2: SAME record key (merges) from docB
    s.append(StoreBatch {
        entities: vec![BatchEntity {
            local_id: 0, canonical_name: "IBM".into(), typ: "org".into(),
            surface_names: vec!["IBM".into()], record_keys: vec!["k:ibm".into()],
            source_refs: vec!["docB".into()],
        }],
        edges: vec![BatchEdge { subj_local: 0, predicate: "is".into(), obj_local: 0,
            valid_from: 2, valid_to: None, source_refs: vec!["docB".into()] }],
        ingested_at: 2,
    });
    let g = s.as_of(i64::MAX, i64::MAX);
    let e = g.entities.iter().find(|e| e.canonical_name == "IBM").unwrap();
    assert_eq!(e.source_refs, vec!["docA".to_string(), "docB".to_string()]);  // union, docA NOT lost
}
```
> Adjust the constructor/API (`GraphStore::default()` vs a `new`, field names) to match the existing tests in `store_integration.rs` — read the top of that file first. The assertions (source_refs carried + accretive union) are the invariants that matter.

- [ ] **Step 2: Run, verify fail.** `cargo test` → the new fields (`BatchEntity.source_refs`, `EntityNode.source_refs`) don't exist → compile error / test fail.

- [ ] **Step 3: Add the struct fields.**
  - `model.rs` `EntityNode` (~46-54): add after `surface_names`:
    ```rust
    /// Docs this entity was extracted from (accreted across cross-doc merges). Provenance for
    /// per-doc alignment of relationless entities. Serde-default for snapshot back-compat.
    #[serde(default)]
    pub source_refs: Vec<String>,
    ```
  - `store.rs` `BatchEntity` (~36-43): add `#[serde(default)] pub source_refs: Vec<String>,` after `record_keys`.
  - `store.rs` `StoredEntity` (~66-76): add `#[serde(default)] pub source_refs: Vec<String>,` after `record_keys`.

- [ ] **Step 4: Accretive union at the append merge** (`store.rs` ~299-339, in the `for (i, be) in batch.entities...` loop). After the `keys`/`surfaces` folding, before the `StoredEntity { ... }` insert, compute `refs`:
```rust
        // source_refs are MONOTONIC provenance -- unlike record_keys (authoritative-batch,
        // may shrink on split), a doc a node was ever extracted from must never leave. So
        // ACCRETIVE union: survivor ∪ absorbed ∪ batch.
        let mut refs: Vec<String> = be.source_refs.clone();
        if let Some(prev) = self.entities.get(&id) {
            refs.extend(prev.source_refs.iter().cloned());   // keep survivor's docs (NOT like keys)
        }
        for &sid in &inherited[i] {
            if sid != id {
                if let Some(ab) = self.entities.get(&sid) {
                    refs.extend(ab.source_refs.iter().cloned());
                }
            }
        }
        refs.sort();
        refs.dedup();
```
Then add `source_refs: refs,` to the `StoredEntity { ... }` literal (~326-338).

- [ ] **Step 5: `as_of` copies into `EntityNode`** (`store.rs` ~420-427). Add to the `EntityNode { ... }` literal:
```rust
                    source_refs: se.source_refs.clone(),
```

- [ ] **Step 6: Run tests, verify pass.** `cargo test` (goldengraph-core). Expected: the 2 new tests pass + existing store tests stay green. (Note: the accretive union also survives a later split — the existing split tests plus the union test cover the monotonic invariant; if `store_integration.rs` has a split helper, add a third test asserting the earlier doc is retained across a split.)

- [ ] **Step 7: Commit.**
```bash
git add packages/rust/extensions/goldengraph-core/src/store.rs packages/rust/extensions/goldengraph-core/src/model.rs packages/rust/extensions/goldengraph-core/tests/store_integration.rs
git commit -m "feat(goldengraph-core): entity source_refs -- accretive union on merge + carried through as_of"
```

---

## Task 2: Rust native — serialize source_refs in both entity dicts

**Files:**
- Modify: `goldengraph-native/src/lib.rs`

No unit-test tier (pyo3 dict is Modal-verified per the spec's accepted gap). Verify by compile.

- [ ] **Step 1: Add `source_refs` to `entities()`** (`lib.rs` ~69-82), after the `surface_names` set_item:
```rust
            let refs: Vec<&str> = e.source_refs.iter().map(String::as_str).collect();
            d.set_item("source_refs", PyList::new(py, refs)?)?;
```

- [ ] **Step 2: Add the identical block to the query serializer** (`lib.rs` ~160-168), after its `surface_names` set_item (same two lines).

- [ ] **Step 3: Verify compile.** `cargo check -p goldengraph-native` (from `packages/rust`). If the local pyo3 build needs a Python, set `PYO3_PYTHON=/d/show_case/goldenmatch/.venv/Scripts/python.exe`. If pyo3 can't build locally in this env, the Modal wheel build (Task 5) is the compile gate — note it and proceed.

- [ ] **Step 4: Commit.**
```bash
git add packages/rust/extensions/goldengraph-native/src/lib.rs
git commit -m "feat(goldengraph-native): serialize entity source_refs in both graph dicts"
```

---

## Task 3: Python build_batch stamps entity source_refs

**Files:**
- Modify: `packages/python/goldengraph/goldengraph/ingest.py` (`build_batch`, out_entities ~97-106 + literal leaves ~127-135)
- Test: `packages/python/goldengraph/tests/test_ingest_provenance.py`

- [ ] **Step 1: Write the failing test.** Create `tests/test_ingest_provenance.py`:
```python
"""build_batch stamps source_refs on entity nodes (mirroring edges) for per-doc alignment."""
from goldengraph.extract import Extraction, Mention, Relationship
from goldengraph.ingest import build_batch
from goldengraph.resolve import ResolvedEntity


def test_build_batch_stamps_entity_source_refs():
    extraction = Extraction(
        mentions=[Mention(name="Amazon", typ="org"), Mention(name="Jeff Bezos", typ="person")],
        relationships=[Relationship(subj=0, predicate="founded_by", obj=1)],
    )
    entities = [
        ResolvedEntity(local_id=0, canonical_name="Amazon", typ="org",
                       surface_names=["Amazon"], record_keys=["k:amazon"], member_idx=[0]),
        ResolvedEntity(local_id=1, canonical_name="Jeff Bezos", typ="person",
                       surface_names=["Jeff Bezos"], record_keys=["k:bezos"], member_idx=[1]),
    ]
    batch = build_batch(extraction, entities, at=1, source_ref="docA")
    assert all(e["source_refs"] == ["docA"] for e in batch["entities"])


def test_build_batch_no_source_ref_empty_refs():
    extraction = Extraction(mentions=[Mention(name="X", typ="org")], relationships=[])
    entities = [ResolvedEntity(local_id=0, canonical_name="X", typ="org",
                               surface_names=["X"], record_keys=[], member_idx=[0])]
    batch = build_batch(extraction, entities, at=1)  # no source_ref
    assert all(e["source_refs"] == [] for e in batch["entities"])
```

- [ ] **Step 2: Run, verify fail.** `KeyError: 'source_refs'`.

- [ ] **Step 3: Implement.** In `build_batch`, the out_entities list comprehension (~97-106) adds `"source_refs": list(refs)` to each entity dict (where `refs = [source_ref] if source_ref else []` already exists at the top of the function for edges). Also add `"source_refs": list(refs)` to the literal-leaf entity dict (~127-135).

- [ ] **Step 4: Run tests, verify pass** + `ruff check goldengraph/ingest.py`.

- [ ] **Step 5: Commit.**
```bash
git add packages/python/goldengraph/goldengraph/ingest.py packages/python/goldengraph/tests/test_ingest_provenance.py
git commit -m "feat(goldengraph): build_batch stamps source_refs on entity nodes"
```

---

## Task 4: Aligner candidate set = edge-endpoints ∪ node-source_refs

**Files:**
- Modify: `packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/substrate_eval.py` (`_assign_real_nodes_aliased`)
- Test: `packages/python/goldenmatch/benchmarks/er-kg-bench/tests/test_substrate_eval.py`

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_substrate_eval.py`:
```python
from erkgbench.substrate_eval import _assign_real_nodes_aliased


def _graph_prov(entities, edges):
    return {"entities": entities, "edges": edges}


def test_aligner_reaches_node_via_source_refs_not_edge():
    # node 1 edged only in docB but source_refs = {docA, docB}; gold for it in docA.
    entities = [{"entity_id": 1, "canonical_name": "IBM", "surface_names": ["IBM"],
                 "typ": "org", "source_refs": ["docA", "docB"]}]
    edges = [{"subj": 1, "obj": 1, "predicate": "is", "source_refs": ["docB"]}]
    gold = [("Qibm", "ibm", "docA")]
    aliases = {"Qibm": ["ibm"]}
    node_of = _assign_real_nodes_aliased(_graph_prov(entities, edges), gold, aliases)
    assert node_of[0] == 1          # reached via source_refs (docA), though its edge is in docB


def test_aligner_byte_identical_without_node_source_refs():
    # no entity carries source_refs -> candidate set is edge-only, exactly as before.
    entities = [{"entity_id": 1, "canonical_name": "IBM", "surface_names": ["IBM"], "typ": "org"}]
    edges = [{"subj": 1, "obj": 1, "predicate": "is", "source_refs": ["docA"]}]
    gold = [("Qibm", "ibm", "docA"), ("Qz", "zeta", "docB")]
    aliases = {"Qibm": ["ibm"], "Qz": ["zeta"]}
    node_of = _assign_real_nodes_aliased(_graph_prov(entities, edges), gold, aliases)
    assert node_of[0] == 1 and node_of[1] < 0   # docA aligns via edge; docB orphan (no node there)


def test_aligner_mixed_provenance_no_regression():
    # one entity has source_refs, one doesn't; the without-prov entity still aligns via its edge.
    entities = [
        {"entity_id": 1, "canonical_name": "IBM", "surface_names": ["IBM"], "typ": "org",
         "source_refs": ["docA"]},
        {"entity_id": 2, "canonical_name": "Apple", "surface_names": ["Apple"], "typ": "org"},  # no refs
    ]
    edges = [{"subj": 2, "obj": 2, "predicate": "is", "source_refs": ["docB"]}]
    gold = [("Qibm", "ibm", "docA"), ("Qap", "apple", "docB")]
    aliases = {"Qibm": ["ibm"], "Qap": ["apple"]}
    node_of = _assign_real_nodes_aliased(_graph_prov(entities, edges), gold, aliases)
    assert node_of[0] == 1 and node_of[1] == 2   # IBM via source_refs, Apple via edge -- both align
```

- [ ] **Step 2: Run, verify fail.** `test_aligner_reaches_node_via_source_refs_not_edge` fails (`node_of[0] < 0` — edge-only can't reach docA).

- [ ] **Step 3: Implement the union.** In `_assign_real_nodes_aliased`, after building the edge-derived `by_doc`, add a node-provenance map and union it into the per-doc candidate set:
```python
    node_by_doc: dict[str, set[int]] = defaultdict(set)
    for e in graph.get("entities", ()):
        for ref in e.get("source_refs", ()):
            node_by_doc[_base_doc_id(ref)].add(e.get("entity_id"))
```
and change the candidate line from `cands = sorted(by_doc.get(_base_doc_id(doc), set()))` to:
```python
        d = _base_doc_id(doc)
        cands = sorted(by_doc.get(d, set()) | node_by_doc.get(d, set()))
```
(`defaultdict` is already imported at the top of the module; if not, add `from collections import defaultdict`.)

- [ ] **Step 4: Run tests, verify pass** — the 3 new tests AND the full `test_substrate_eval.py` regression (the existing presence-probe/gliner tests must stay green; the new union must not change any graph that lacks node source_refs). Then `ruff check erkgbench/substrate_eval.py`.

- [ ] **Step 5: Commit.**
```bash
git add packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/substrate_eval.py packages/python/goldenmatch/benchmarks/er-kg-bench/tests/test_substrate_eval.py
git commit -m "feat(erkgbench): aligner candidate set unions node source_refs with edge endpoints"
```

---

## Task 5: Modal measurement + verdict

**Files:** Create `docs/superpowers/reports/2026-07-02-node-provenance-verdict.md`.

The Modal bench rebuilds the `goldengraph-native` wheel from source (picking up Tasks 1-2). Run the SHIPPED `run_wiki` (no probe flag). Rig: best config, SCHEMA_CANON off.

- [ ] **Step 1: Fire two legs** (7B seeded + V3):
```bash
M="/d/show_case/goldenmatch/.venv/Scripts/modal.exe"
BEST=$'GOLDENGRAPH_SUBSTRATE_CORPUS=wiki\nGOLDENGRAPH_XDOC_KEY=name_ci\nGOLDENGRAPH_CHUNK_EXTRACT=1\nGOLDENGRAPH_CHUNK_SENTENCES=6\nGOLDENGRAPH_CHUNK_OVERLAP=2'
$M run --detach scripts/distill/modal_bench.py --engine goldengraph --eval substrate --n 160 \
  --opts "$BEST"$'\nGOLDENGRAPH_LLM_SEED=42' --spawn        # 7B seeded
$M run --detach scripts/distill/modal_bench.py --engine goldengraph --eval substrate --n 161 \
  --chat deepseek-chat --opts "$BEST" --spawn               # V3
```
Poll `gg-bench-cache` for `results/substrate_16{0,1}_*.md`; read the `[substrate-wiki]` line.

> **First-run wheel rebuild:** Tasks 1-2 changed the Rust crate, so the cached wheel is stale. If `modal_bench.py` caches the wheel on the volume and skips rebuild, the run would use the OLD wheel (flat coverage). Confirm the wheel cache key rebuilds on source change, or clear `gg-bench-cache/wheels` before the run (`$M volume rm gg-bench-cache wheels/ -r` then the first leg rebuilds). This is the single most likely cause of a false no-op.

- [ ] **Step 2: Read both legs.** coverage / R(B) / P(B) / F1 / components. Compare to the pre-fix baseline (7B ~0.49/0.465, V3 ~0.49/0.570) and the probe's relaxed targets (~1.0 cov, ~0.82/~0.90 F1).

- [ ] **Step 3: Write the verdict** `docs/superpowers/reports/2026-07-02-node-provenance-verdict.md`:
  - **WIN:** coverage → ~1.0, F1 → ~0.82/~0.90, P(B) ~1.0 — the fix landed, matching the probe per-doc.
  - **No-op:** coverage flat ~0.49 → localize (stale wheel? `as_of` copy? native dict? build_batch stamp?) using the per-layer tests as the map.
  - **Precision dip:** P(B) < 1.0 → a per-doc collision; report before recommending default-on.
  - Note the PyPI republish as a separate optional rollout, and hand off to the metric-split sub-project.

- [ ] **Step 4: Commit** the report.
```bash
git add docs/superpowers/reports/2026-07-02-node-provenance-verdict.md
git commit -m "docs(goldengraph): node-provenance verdict (wiki, 7B + V3)"
```

---

## Completion

Use superpowers:finishing-a-development-branch: run `cargo test -p goldengraph-core` + both Python box suites, open a PR (base `main`), arm auto-merge. Default-on correctness fix; back-compat via serde-default + aligner union. If the measurement confirms coverage → ~1.0 at P(B)~1.0, the arc's next sub-project is the metric-reporting split (presence-coverage vs relational-R(B)); a PyPI `goldengraph-native` republish is a separate optional rollout.
