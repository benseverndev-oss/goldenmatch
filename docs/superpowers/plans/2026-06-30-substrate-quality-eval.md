# Substrate-Quality Eval — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a substrate-quality instrument that scores goldengraph's BUILT graph as a knowledge base at two levels (resolver-in-isolation vs end-to-end), reusing `metrics.score`, so the A−B gap turns the construction ceiling into an optimizable number.

**Architecture:** All scoring logic is PURE functions over plain dicts (a graph `{entities, edges}` + a gold-mention list) → box-safe TDD. The live `ingest_corpus` build + resolver run is the integration glue, validated by a Modal ambiguity sweep. Both levels reduce to "a clustering over the same gold-mention index space, scored by `metrics.score`."

**Tech Stack:** Python (stdlib), pytest, the existing `er-kg-bench` (`metrics.py`, `engineered.py`) + `goldengraph.ingest`, the Modal harness for the live run.

**Spec:** `docs/superpowers/specs/2026-06-30-substrate-quality-eval-design.md`
**Branch:** `feat/substrate-quality-eval` (off `origin/main`).

---

## Environment notes

- **Box-safe pure tests** (the new logic is pure dicts — no native store, no LLM):
  ```bash
  cd packages/python/goldenmatch/benchmarks/er-kg-bench
  PYTHONPATH="." POLARS_SKIP_CPU_CHECK=1 "D:/show_case/goldenmatch/.venv/Scripts/python.exe" \
    -m pytest tests/test_substrate_eval.py -q -p no:cacheprovider
  ```
- **Do NOT run the whole suite locally.** `ruff check` + `py_compile` before commit. GitHub auth: `GH_TOKEN=$(gh auth token --user benzsevern)`.
- **`ingest_corpus` needs the native `goldengraph_native` store** (absent on the box) → the end-to-end Level-B *live* run is the Modal validation (Task 5), NOT a box test. The box tests cover all PURE logic with stub graph dicts.

## Shared data shapes (used across tasks)

- **gold mention:** a tuple `(entity_id: str, surface: str, doc_id: str)`. The list of these is the index space; `entity_ids[i] = gold_mentions[i][0]`.
- **graph dict** (the shape `slice_graph.query(ids,1)` returns):
  ```python
  {"entities": [{"entity_id": int, "canonical_name": str, "surface_names": [str, ...]}, ...],
   "edges":    [{"subj": int, "predicate": str, "obj": int, "source_refs": [str, ...]}, ...]}
  ```
- **clustering:** `list[list[int]]` — lists of gold-mention INDICES grouped by built node (Level B) or by resolver cluster (Level A). Fed to `metrics.score(entity_ids, clustering)`.

## File structure

- **Modify:** `erkgbench/qa_e2e/engineered.py` — add `emit_gold_mentions(seed, ambiguity, max_hops) -> list[tuple]` (two mentions per edge-doc, from the SAME generation the corpus uses).
- **Create:** `erkgbench/substrate_eval.py` — pure: `align_mentions_to_nodes`, `graph_coherence`, `provenance_coverage`, `score_substrate`.
- **Create:** `erkgbench/run_substrate_eval.py` — the runner (engineered → resolver + ingest_corpus → `score_substrate` → scoreboard markdown).
- **Create:** `tests/test_substrate_eval.py` — pure TDD for all four functions + the emitter.

---

### Task 1: gold-mention emitter

**Files:** Modify `erkgbench/qa_e2e/engineered.py`; Test `tests/test_substrate_eval.py`.

- [ ] **Step 1: Write failing test** (create the file)

```python
# tests/test_substrate_eval.py
"""Substrate-quality eval: pure scoring over a built graph (alignment / coherence / provenance / A-B)."""
from __future__ import annotations


def test_emit_gold_mentions_from_documents():
    from erkgbench.qa_e2e.engineered import emit_gold_mentions

    class _Doc:  # mimic corpora.Document (id + src_surface + dst_surface)
        def __init__(self, id, ss, ds):
            self.id, self.src_surface, self.dst_surface = id, ss, ds

    docs = [_Doc("gm:a::works_at::gm:b", "Ay", "Bee"),
            _Doc("gm:a::located_in::gm:c", "Ay", "Cee"),
            _Doc("gm:a::works_at::gm:b::1", "X", "Y")]   # a co-occurrence extra (::1) -> SKIPPED
    mentions = emit_gold_mentions(docs)
    assert mentions == [
        ("gm:a", "Ay", "gm:a::works_at::gm:b"), ("gm:b", "Bee", "gm:a::works_at::gm:b"),
        ("gm:a", "Ay", "gm:a::located_in::gm:c"), ("gm:c", "Cee", "gm:a::located_in::gm:c"),
    ]
```

- [ ] **Step 2: Run, verify FAIL** (`emit_gold_mentions` undefined).

- [ ] **Step 3: Implement** — in `engineered.py`, derive the gold mentions DIRECTLY off the generated
`Document`s (NO rng replay — so surfaces match the built graph by construction at any `ambiguity`). Each
engineered edge-doc's `id` is `_edge_doc_id(src, rel, dst)` = `src::rel::dst` and it carries
`src_surface`/`dst_surface`:

```python
def emit_gold_mentions(documents) -> list[tuple[str, str, str]]:
    """Gold mentions read directly off the generated engineered `Document`s -- two per edge-doc,
    `(entity_id, surface, doc_id)` for src and dst. The doc id encodes `src::rel::dst` (gold canonical
    ids) and the Document carries the rendered `src_surface`/`dst_surface`, so the mentions match EXACTLY
    what the build saw -- no rng replay, no drift at any ambiguity. Co-occurrence extras (`::N` suffix,
    4+ `::`-parts) and any non-edge docs are skipped, so run the corpus WITHOUT GOLDENGRAPH_BENCH_COOCCUR
    for a clean base-doc gold set."""
    out: list[tuple[str, str, str]] = []
    for d in documents:
        parts = d.id.split("::")
        if len(parts) != 3:          # not a base edge-doc (cooccur ::N extra / non-edge) -> skip
            continue
        src_id, dst_id = parts[0], parts[2]
        out.append((src_id, d.src_surface, d.id))
        out.append((dst_id, d.dst_surface, d.id))
    return out
```

> **Why this kills the determinism risk:** the gold comes from the SAME `Document` objects the build
> ingests (Task 5 passes `corpus.documents` to both `emit_gold_mentions` and `ingest_corpus`), so the
> surfaces and doc ids are identical by construction — not reconstructed from a replayed rng. No
> `ambiguity`-dependent drift.

- [ ] **Step 4: Run, verify PASS.**
- [ ] **Step 5: Commit** `feat(er-kg-bench): emit_gold_mentions for the substrate eval`.

---

### Task 2: alignment (mention → built node)

**Files:** Create `erkgbench/substrate_eval.py`; Test `tests/test_substrate_eval.py`.

- [ ] **Step 1: Write failing tests** (append) — the four spec cases:

```python
from erkgbench.substrate_eval import align_mentions_to_nodes

def _edge(subj, obj, doc, pred="r"):
    return {"subj": subj, "predicate": pred, "obj": obj, "source_refs": [doc]}

def test_align_clean_one_node_per_entity():
    # docs: A::r::B and A::r2::C ; build kept both edges, endpoints distinct nodes
    gm = [("A", "A", "A::r::B"), ("B", "B", "A::r::B"), ("A", "A", "A::r2::C"), ("C", "C", "A::r2::C")]
    graph = {"entities": [], "edges": [_edge(0, 1, "A::r::B"), _edge(0, 2, "A::r2::C", "r2")]}
    clustering = align_mentions_to_nodes(graph, gm)
    # mention 0 (A) -> node0, 1 (B)->node1, 2 (A)->node0, 3 (C)->node2
    assert sorted(map(sorted, clustering)) == [[0, 2], [1], [3]]   # A's two mentions share node0

def test_align_entity_split_recall_loss():
    # A appears in two docs but under-merge put it in DIFFERENT nodes (0 and 9)
    gm = [("A", "A", "A::r::B"), ("B", "B", "A::r::B"), ("A", "A", "A::r2::C"), ("C", "C", "A::r2::C")]
    graph = {"entities": [], "edges": [_edge(0, 1, "A::r::B"), _edge(9, 2, "A::r2::C", "r2")]}
    clustering = align_mentions_to_nodes(graph, gm)
    assert [0] in [sorted(c) for c in clustering] and [2] in [sorted(c) for c in clustering]  # A split

def test_align_node_absorbs_two_entities_precision_loss():
    # B and C both landed in node 5 (cross-doc over-merge of distinct entities)
    gm = [("A", "A", "A::r::B"), ("B", "B", "A::r::B"), ("D", "D", "D::r::C"), ("C", "C", "D::r::C")]
    graph = {"entities": [], "edges": [_edge(0, 5, "A::r::B"), _edge(3, 5, "D::r::C")]}
    clustering = align_mentions_to_nodes(graph, gm)
    assert [1, 3] in [sorted(c) for c in clustering]   # B(idx1) + C(idx3) share node5 -> precision loss

def test_align_shared_surface_collision_disambiguated_by_doc():
    # A and X share the surface "Ay" but are different entities in different docs -> doc keys them apart
    gm = [("A", "Ay", "A::r::B"), ("B", "B", "A::r::B"), ("X", "Ay", "X::r::Y"), ("Y", "Y", "X::r::Y")]
    graph = {"entities": [], "edges": [_edge(0, 1, "A::r::B"), _edge(7, 8, "X::r::Y")]}
    clustering = align_mentions_to_nodes(graph, gm)
    # A(idx0)->node0, X(idx2)->node7 ; the shared surface did NOT merge them
    flat = {tuple(sorted(c)) for c in clustering}
    assert (0,) in flat and (2,) in flat

def test_align_extraction_miss_singleton():
    # doc D::r::E produced NO edge (extraction dropped it) -> both mentions are singletons
    gm = [("D", "D", "D::r::E"), ("E", "E", "D::r::E")]
    graph = {"entities": [], "edges": []}
    clustering = align_mentions_to_nodes(graph, gm)
    assert sorted(map(sorted, clustering)) == [[0], [1]]

def test_align_strips_cooccur_suffix():
    # build edge's source_ref carries the ::1 co-occurrence suffix; base doc id still matches
    gm = [("A", "A", "A::r::B"), ("B", "B", "A::r::B")]
    graph = {"entities": [], "edges": [{"subj": 0, "predicate": "r", "obj": 1, "source_refs": ["A::r::B::1"]}]}
    clustering = align_mentions_to_nodes(graph, gm)
    assert sorted(map(sorted, clustering)) == [[0], [1]]   # matched via base id
```

- [ ] **Step 2: Run, verify FAIL.**

- [ ] **Step 3: Implement** `erkgbench/substrate_eval.py`:

```python
"""Substrate-quality scoring over a BUILT graph (pure; operates on the graph dict + gold mentions)."""
from __future__ import annotations


def _base_doc_id(ref: str) -> str:
    """A source_ref may carry a `::N` co-occurrence suffix; the base doc id is `src::rel::dst` (3 parts).
    Re-join the first three `::`-separated parts (entity ids use a single `:`, so `::` is unambiguous)."""
    parts = ref.split("::")
    return "::".join(parts[:3]) if len(parts) >= 3 else ref


def align_mentions_to_nodes(graph: dict, gold_mentions: list[tuple[str, str, str]]) -> list[list[int]]:
    """Cluster gold-mention INDICES by the built node each landed in. Exact, doc-keyed (not surface):
    each engineered doc is ONE edge `src::rel::dst`; the built edge for that doc (matched by base doc id
    in `source_refs`) gives endpoints subj=src-node, obj=dst-node. Assumption: direction-canonicalization
    OFF (subj==src). Unmatched mention (no edge for its doc) -> its own singleton (extraction miss).

    KNOWN LIMIT (documented, not fixed in v1): if the resolver merges a single doc's src+dst (distinct
    entities) into one node, the build drops the self-loop -> no edge -> both mentions become singletons,
    mislabeling a within-doc over-merge as recall misses. Does not affect the ambiguity-driven (cross-doc,
    recall-side) headline."""
    # doc base id -> the edge (subj, obj). Prefer an exact base-id match.
    by_doc: dict[str, tuple[int, int]] = {}
    for e in graph.get("edges", ()):
        for ref in e.get("source_refs", ()):
            by_doc.setdefault(_base_doc_id(ref), (e["subj"], e["obj"]))
    node_of: dict[int, int] = {}   # mention index -> node id ; unmatched -> a fresh negative id
    fresh = -1
    for i, (entity_id, _surface, doc_id) in enumerate(gold_mentions):
        edge = by_doc.get(_base_doc_id(doc_id))
        if edge is None:
            node_of[i] = fresh
            fresh -= 1
            continue
        parts = doc_id.split("::")
        src_id, dst_id = parts[0], parts[2]
        node_of[i] = edge[0] if entity_id == src_id else edge[1] if entity_id == dst_id else (fresh)
        if node_of[i] == fresh:   # entity_id matched neither endpoint (shouldn't happen) -> unmatched
            fresh -= 1
    groups: dict[int, list[int]] = {}
    for i, node in node_of.items():
        groups.setdefault(node, []).append(i)
    return [sorted(v) for v in groups.values()]
```

- [ ] **Step 4: Run, verify PASS** (6 alignment tests).
- [ ] **Step 5: ruff + commit** `feat(er-kg-bench): substrate-eval mention->node alignment`.

---

### Task 3: coherence + provenance

**Files:** Modify `erkgbench/substrate_eval.py`; Test `tests/test_substrate_eval.py`.

- [ ] **Step 1: Write failing tests** (append)

```python
from erkgbench.substrate_eval import graph_coherence, provenance_coverage

def test_coherence_components_and_largest_fraction():
    # nodes 0-1 connected, 2-3 connected, 4 isolated -> 3 components, largest = 2/5
    graph = {"entities": [{"entity_id": i, "canonical_name": str(i), "surface_names": [str(i)]} for i in range(5)],
             "edges": [{"subj": 0, "predicate": "r", "obj": 1, "source_refs": ["d"]},
                       {"subj": 2, "predicate": "r", "obj": 3, "source_refs": ["d"]}]}
    coh = graph_coherence(graph)
    assert coh["components"] == 3 and abs(coh["largest_fraction"] - 0.4) < 1e-9

def test_provenance_coverage():
    graph = {"entities": [], "edges": [
        {"subj": 0, "predicate": "r", "obj": 1, "source_refs": ["d"]},
        {"subj": 1, "predicate": "r", "obj": 2, "source_refs": []}]}
    assert provenance_coverage(graph) == 0.5   # 1 of 2 edges has a source_ref
```

- [ ] **Step 2: Run, verify FAIL.**

- [ ] **Step 3: Implement** (append to `substrate_eval.py`):

```python
def graph_coherence(graph: dict) -> dict:
    """Connected components of the built graph (edges undirected) + largest-component fraction. A
    coherent knowledge base is few components / one dominant; the construction ceiling shows as many
    small components."""
    nodes = {e["entity_id"] for e in graph.get("entities", ())}
    parent: dict[int, int] = {n: n for n in nodes}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for e in graph.get("edges", ()):
        parent[find(e["subj"])] = find(e["obj"])
    roots = [find(n) for n in parent]
    if not roots:
        return {"components": 0, "largest_fraction": 0.0}
    from collections import Counter
    sizes = Counter(roots)
    return {"components": len(sizes), "largest_fraction": max(sizes.values()) / len(roots)}


def provenance_coverage(graph: dict) -> float:
    """Fraction of edges carrying a non-empty `source_refs` (every fact traceable to a source). ~1.0 for
    goldengraph alone (it always stamps doc ids); discriminating in the multi-engine bake-off."""
    edges = list(graph.get("edges", ()))
    if not edges:
        return 1.0
    return sum(1 for e in edges if e.get("source_refs")) / len(edges)
```

- [ ] **Step 4: Run, verify PASS.**
- [ ] **Step 5: ruff + commit** `feat(er-kg-bench): substrate-eval coherence + provenance`.

---

### Task 4: A/B scoring assembly

**Files:** Modify `erkgbench/substrate_eval.py`; Test `tests/test_substrate_eval.py`.

- [ ] **Step 1: Write failing test** (append)

```python
from erkgbench.substrate_eval import score_substrate

def test_score_substrate_assembles_a_b_gap():
    gm = [("A", "A", "A::r::B"), ("B", "B", "A::r::B"), ("A", "A", "A::r2::C"), ("C", "C", "A::r2::C")]
    # Level A: a PERFECT resolver clustering (A's two mentions together)
    resolver_clusters = [[0, 2], [1], [3]]
    # Level B graph: under-merge split A across node0 and node9 -> worse than A
    graph = {"entities": [{"entity_id": n, "canonical_name": "x", "surface_names": ["x"]} for n in (0,1,2,9)],
             "edges": [{"subj": 0, "predicate": "r", "obj": 1, "source_refs": ["A::r::B"]},
                       {"subj": 9, "predicate": "r2", "obj": 2, "source_refs": ["A::r2::C"]}]}
    sb = score_substrate(gold_mentions=gm, resolver_clusters=resolver_clusters, graph=graph)
    assert sb["er_f1_a"] == 1.0                       # perfect resolver
    assert sb["er_f1_b"] < sb["er_f1_a"]              # build fragmented A -> B worse
    assert abs(sb["ab_gap"] - (sb["er_f1_a"] - sb["er_f1_b"])) < 1e-9
    assert sb["components"] == 2 and 0.0 <= sb["provenance"] <= 1.0
```

- [ ] **Step 2: Run, verify FAIL.**

- [ ] **Step 3: Implement** (append):

```python
def score_substrate(*, gold_mentions, resolver_clusters, graph) -> dict:
    """Assemble the substrate scoreboard. ER-F1(A) = the resolver clustering scored vs gold; ER-F1(B) =
    the built-graph mention->node clustering scored vs gold; A-B gap = extraction-induced fragmentation;
    plus coherence + provenance on the built graph. All over the SAME gold-mention index space."""
    from erkgbench import metrics

    entity_ids = [m[0] for m in gold_mentions]
    a = metrics.score(entity_ids, resolver_clusters)
    b = metrics.score(entity_ids, align_mentions_to_nodes(graph, gold_mentions))
    coh = graph_coherence(graph)
    return {
        "er_f1_a": a.f1, "er_p_a": a.precision, "er_r_a": a.recall,
        "er_f1_b": b.f1, "er_p_b": b.precision, "er_r_b": b.recall,
        "ab_gap": a.f1 - b.f1,
        "components": coh["components"], "largest_fraction": coh["largest_fraction"],
        "provenance": provenance_coverage(graph),
    }
```

- [ ] **Step 4: Run, verify PASS** + the whole file box-safe.
- [ ] **Step 5: ruff + commit** `feat(er-kg-bench): substrate-eval A/B scoreboard assembly`.

---

### Task 5: runner + Modal ambiguity-sweep validation + report

**Files:** Create `erkgbench/run_substrate_eval.py`; Create `docs/superpowers/reports/2026-06-30-substrate-quality-eval.md`.

- [ ] **Step 1: Write the runner** `erkgbench/run_substrate_eval.py`:
  - Generate the engineered `QACorpus` ONCE (with `GOLDENGRAPH_BENCH_COOCCUR` unset), then derive gold via
    `emit_gold_mentions(corpus.documents)` — the SAME Document objects the build ingests, so surfaces +
    doc ids match exactly with **no rng-drift at any ambiguity** (the determinism risk is gone by
    construction).
  - **Level A:** feed the gold mentions' surfaces to the resolver (the same goldenmatch/goldengraph resolver `ingest_corpus` uses) → a clustering over mention indices → done.
  - **Level B:** `ingest_corpus([doc.text...], store, llm=…, resolver=…, doc_ids=[doc.id...])`; then `slice_graph = store.as_of(_AS_OF,_AS_OF)`; query ALL entities' 1-hop to get the full `{entities, edges}` dict.
  - Call `score_substrate(...)`; print + write the scoreboard markdown for the given `ambiguity`.
  - CLI flags: `--seed`, `--ambiguity` (repeatable for the sweep), `--out-md`.
  - **`ingest_corpus` has NO `extractor` param** — extraction is the injected `llm`; for the real run that's the OpenAIClient/Ollama 7B (the same as the QA bench).

- [ ] **Step 2: Push the branch**

```bash
GH_TOKEN=$(gh auth token --user benzsevern) git push -u origin feat/substrate-quality-eval
```

- [ ] **Step 3: Wire + fire the Modal ambiguity sweep** — add a `substrate` eval entry to `scripts/distill/modal_bench.py`'s `_EVAL`/dispatch (mirroring `end_to_end`: run `erkgbench.run_substrate_eval` with `--ambiguity 0.0 0.3 0.6`), then a detached run on the 7B (A10G). Poll with a Monitor. (This is the integration validation; the native store + LLM only exist on Modal.)

- [ ] **Step 4: Write the verdict report** — `docs/superpowers/reports/2026-06-30-substrate-quality-eval.md`:
  - The scoreboard across `ambiguity ∈ {0.0, 0.3, 0.6}`: `ER-F1(A)`, `ER-F1(B)`, `A−B gap`, components, largest-fraction, provenance.
  - **The instrument's validation:** confirm at `ambiguity=0` A ≈ B ≈ high, and that **B drops below A (the gap widens) as ambiguity rises — reproducing the construction ceiling as a number.** If so, the instrument is trustworthy and ready to drive the architecture work + the v2 bake-off.
  - Note the v1 limits (provenance ~1.0 single-engine; within-doc over-merge attribution).

- [ ] **Step 5: Commit the runner + report**, open a PR, arm auto-merge.

---

## Done criterion

- Tasks 1-4 merged behind green box-safe pure tests (emitter + alignment 4 cases + coherence + provenance + A/B assembly).
- A committed scoreboard report from the Modal ambiguity sweep, with the A−B gap reproducing the construction ceiling (the instrument's own validation).
- PR opened; auto-merge armed. The instrument then grounds the architecture work (cross-doc entity resolution) + the reframed v2 substrate bake-off.
