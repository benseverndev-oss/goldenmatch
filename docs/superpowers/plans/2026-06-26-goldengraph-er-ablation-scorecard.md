# GoldenGraph ER-Ablation Scorecard Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the deterministic, $0, CI-gateable bridge-recall ER-ablation that proves/refutes goldengraph's `(ER_accuracy)^hops` thesis at the retrieval layer, plus the per-stage scorecard scaffold.

**Architecture:** A pure-Python oracle reconstructs the engineered corpus's gold graph + per-question chain from doc-id parsing. Four ER-quality "dials" assign cross-document identity by controlling the `record_key`s each mention emits (the store merges mentions across documents iff they share a record_key). For each dial we build a goldengraph native store directly from gold triples (bypassing the LLM extractor), oracle-seed retrieval at the gold start entity, expand the ball, and measure bridge-recall (is the gold chain walkable) bucketed by hop. The gate asserts monotonic decay in ER quality + a widening oracle−none gap with hops.

**Tech Stack:** Python 3.11, pytest (wheel-free for the metric core; `goldengraph_native` wheel for the end-to-end ablation), GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-06-26-goldengraph-er-ablation-scorecard-design.md`

**Scope of THIS plan:** the deterministic core (Tasks 1–6) — oracle, dial key-policies, bridge-recall metric, store-build + ablation runner, CLI/`ABLATION.md`, CI gate. The real-LLM scorecard rows (extraction-F1, synthesis-given-gold-subgraph, answer-match confirmation) are **Phase 2 of slice A, a follow-up PR** wired into the existing opt-in `bench-graphrag-qa` lane — they need real-LLM budget and don't gate. Noted in §"Phase 2".

---

## Key code facts (verified against the branch)

- **Corpus:** `erkgbench.qa_e2e.engineered.generate_engineered(*, seed, n_questions, ambiguity, max_hops=4) -> QACorpus`. One `Document` per edge, `Document.id = f"{src_id}::{rel}::{dst_id}"` (canonical ids; `::` separator; ids contain at most a single `:`). `QAItem(question, gold_answer, gold_supporting_fact_ids, hop_count, ambiguity_level, start_entity_id, relation_chain)`. Each `(entity, relation)` has a unique edge → `relation_chain` walks deterministically.
- **Concepts:** `dataset.concepts_loader.load_concepts(path) -> list[Concept]`; `Concept(concept, canonical_id, entity_type, variants: tuple[Variant])`, `Variant(surface)`. Gives the `surface → canonical_id` map for the `oracle` dial.
- **Ingest seams** (`goldengraph.ingest` / `.extract` / `.resolve`):
  - `Mention(name: str, typ: str, context: str = "")`
  - `Relationship(subj: int, predicate: str, obj: int)` (subj/obj index `Extraction.mentions`)
  - `Extraction(mentions: list[Mention], relationships: list[Relationship])`
  - `ResolvedEntity(local_id, canonical_name, typ, surface_names: list[str], record_keys: list[str], member_idx: list[int])`
  - `build_batch(extraction, entities, *, at, valid_from=None) -> dict`
  - store: `from goldengraph_native import _native as ggn; store = ggn.PyStore(); store.append(json.dumps(batch))`
  - **The store merges entities across appends when they share a `record_key`** (the cross-doc-link mechanism injects record_keys precisely so "the store's overlap-merge" connects them).
- **Retrieval:** `slice_graph = store.as_of(valid_t, tx_t)`; `slice_graph.query(seed_ids, hops) -> {"entities":[{entity_id,canonical_name,typ,surface_names,record_keys,...}], "edges":[{subj,obj,predicate}]}`. Seeds are explicit ids — no embedder needed for oracle-seeding. `_AS_OF` in the engine adapter is the as-of coordinate (read it; reuse the same value).

## Test environment

Wheel-free tests (Tasks 1–3) run via the main venv with a path shadow + polars guard, from the er-kg-bench dir:
```
PY="/d/show_case/goldenmatch/.venv/Scripts/python.exe"
cd packages/python/goldenmatch/benchmarks/er-kg-bench
PYTHONPATH="$(pwd -W)" POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8 "$PY" -m pytest <path> -q
```
End-to-end ablation tests (Task 4+) need the `goldengraph_native` wheel — guard with `pytest.importorskip("goldengraph_native")` so they skip locally and run in the wheel-building CI gate. Do NOT run the full suite (OOMs the box) — run only the named files.

---

## Task 1: Gold oracle (wheel-free)

**Files:**
- Create: `packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/qa_e2e/gold.py`
- Test: `packages/python/goldenmatch/benchmarks/er-kg-bench/tests/test_qa_gold_oracle.py`

Use @superpowers:test-driven-development.

- [ ] **Step 1: Write the failing tests**

```python
"""Pure oracle: reconstruct the engineered gold graph + walk a question's chain.
No native, no LLM, no network."""
from __future__ import annotations

from erkgbench.qa_e2e.engineered import generate_engineered
from erkgbench.qa_e2e.gold import GoldGraph, gold_chain


def test_gold_graph_rebuilds_edges_from_doc_ids():
    corpus = generate_engineered(seed=7, n_questions=20, ambiguity=0.5, max_hops=4)
    g = GoldGraph.from_corpus(corpus)
    # every traversed-edge document id `src::rel::dst` is an edge in the gold graph
    for d in corpus.documents:
        src, rel, dst = d.id.split("::")
        assert g.has_edge(src, rel, dst)


def test_gold_chain_walks_to_gold_answer():
    corpus = generate_engineered(seed=7, n_questions=20, ambiguity=0.5, max_hops=4)
    g = GoldGraph.from_corpus(corpus)
    for qa in corpus.questions:
        chain = gold_chain(g, qa)  # ordered [(src_id, rel, dst_id), ...]
        assert len(chain) == qa.hop_count
        assert chain[0][0] == qa.start_entity_id
        # the chain's terminal entity's canonical name == gold_answer
        assert g.canonical_name(chain[-1][2]) == qa.gold_answer


def test_gold_graph_ignores_non_edge_documents():
    # MuSiQue-style docs (no `::`) must not crash / pollute the graph.
    corpus = generate_engineered(seed=1, n_questions=5, ambiguity=0.0, max_hops=2)
    g = GoldGraph.from_corpus(corpus)
    assert g.edge_count() > 0
```

- [ ] **Step 2: Run to verify they fail** — `... test_qa_gold_oracle.py -q` → `ModuleNotFoundError: ... qa_e2e.gold`.

- [ ] **Step 3: Implement `gold.py`**

```python
"""Pure-Python oracle over the engineered corpus: rebuild the gold graph from
edge-document ids and walk each question's relation_chain. No native/LLM/network.

Engineered `Document.id` is `src_id::rel::dst_id` with CANONICAL ids; each
(entity, relation) has a unique edge, so a relation_chain walk is deterministic."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .corpora import QACorpus, QAItem


@dataclass
class GoldGraph:
    # src_id -> {relation -> dst_id}
    _edges: dict[str, dict[str, str]] = field(default_factory=dict)
    _names: dict[str, str] = field(default_factory=dict)  # canonical_id -> canonical name

    @classmethod
    def from_corpus(cls, corpus: QACorpus) -> "GoldGraph":
        g = cls()
        # canonical names from the concept universe (ids -> concept string)
        from dataset.concepts_loader import load_concepts  # type: ignore

        bench_root = Path(__file__).resolve().parents[2]
        for c in load_concepts(bench_root / "dataset" / "concepts.jsonl"):
            g._names[c.canonical_id] = c.concept
        for d in corpus.documents:
            parts = d.id.split("::")
            if len(parts) != 3:  # non-edge doc (MuSiQue) -> skip
                continue
            src, rel, dst = parts
            g._edges.setdefault(src, {})[rel] = dst
        return g

    def has_edge(self, src: str, rel: str, dst: str) -> bool:
        return self._edges.get(src, {}).get(rel) == dst

    def edge_count(self) -> int:
        return sum(len(v) for v in self._edges.values())

    def canonical_name(self, entity_id: str) -> str:
        return self._names.get(entity_id, entity_id)


def gold_chain(g: GoldGraph, qa: QAItem) -> list[tuple[str, str, str]]:
    """Walk `qa.relation_chain` from `qa.start_entity_id` over the gold graph.
    Returns the ordered edge list [(src_id, rel, dst_id), ...]. Raises KeyError
    if the chain is broken (should never happen for a generated question)."""
    chain: list[tuple[str, str, str]] = []
    cur = qa.start_entity_id
    for rel in qa.relation_chain:
        dst = g._edges[cur][rel]
        chain.append((cur, rel, dst))
        cur = dst
    return chain
```

- [ ] **Step 4: Run to verify pass** — expect 3 passed.
- [ ] **Step 5: Commit** — `feat(er-kg-bench): gold oracle for the engineered corpus`.

---

## Task 2: ER-quality dial key-policies (wheel-free)

**Files:**
- Create: `.../erkgbench/qa_e2e/dials.py`
- Test: `.../tests/test_qa_dials.py`

The dial controls cross-document identity via the `record_key` assigned to each mention. A dial is a function `record_key_map(corpus, gold) -> dict[(entity_id, surface) -> str]`: the key two mentions must SHARE to merge in the store.

- [ ] **Step 1: Write failing tests**

```python
from erkgbench.qa_e2e.engineered import generate_engineered
from erkgbench.qa_e2e.gold import GoldGraph
from erkgbench.qa_e2e import dials


def _setup():
    corpus = generate_engineered(seed=7, n_questions=20, ambiguity=0.6, max_hops=4)
    return corpus, GoldGraph.from_corpus(corpus)


def test_oracle_merges_all_surfaces_of_an_entity():
    corpus, g = _setup()
    km = dials.oracle_keys(corpus, g)
    # every (entity_id, surface) pair for one entity maps to ONE key
    keys_by_entity: dict[str, set] = {}
    for (eid, _surface), key in km.items():
        keys_by_entity.setdefault(eid, set()).add(key)
    assert all(len(s) == 1 for s in keys_by_entity.values())


def test_none_gives_every_mention_a_unique_key():
    corpus, g = _setup()
    km = dials.none_keys(corpus, g)
    assert len(set(km.values())) == len(km)  # all distinct


def test_name_only_keys_by_exact_surface():
    corpus, g = _setup()
    km = dials.name_only_keys(corpus, g)
    # two different surfaces of the same entity get DIFFERENT keys
    by_entity: dict[str, set] = {}
    for (eid, surface), key in km.items():
        by_entity.setdefault(eid, set()).add(key)
    multi = [s for s in by_entity.values() if len(s) > 1]
    assert multi, "ambiguity>0 should give some entity >1 surface-key"


def test_goldengraph_merges_at_least_exact_and_no_more_than_oracle():
    corpus, g = _setup()
    o, gg, nm = dials.oracle_keys(corpus, g), dials.goldengraph_keys(corpus, g), dials.name_only_keys(corpus, g)
    # distinct-key count: oracle <= goldengraph <= name_only  (more merging = fewer keys)
    assert len(set(o.values())) <= len(set(gg.values())) <= len(set(nm.values()))
```

- [ ] **Step 2: Run → fail** (`ModuleNotFoundError ... dials`).

- [ ] **Step 3: Implement `dials.py`**

Build the `(entity_id, surface)` universe from the corpus: re-derive each document's two mentions (src/dst canonical ids + the rendered surface). Because the renderer is seed-deterministic, re-run the same surface choice is unavailable — instead enumerate from the gold graph + concepts: every entity's surfaces = its canonical name + its variant surfaces. Keys:
- `oracle_keys`: `key = entity_id` (canonical id) for every `(entity_id, surface)`.
- `none_keys`: `key = f"{entity_id}::{surface}::{i}"` unique per pair (use enumerate order; deterministic).
- `name_only_keys`: `key = surface` (exact string — same surface across entities collides, which is the honest behaviour of exact-name resolution).
- `goldengraph_keys`: run `goldenmatch.dedupe_df` over the distinct `(surface, typ)` rows **with rerank forced off** (name+type only — two fields stay under the 3-field cross-encoder trigger; see spec §footgun), assign each cluster a shared synthetic key. Deterministic, offline.

```python
from __future__ import annotations

from pathlib import Path

from .corpora import QACorpus
from .gold import GoldGraph


def _entity_surfaces(g: GoldGraph) -> list[tuple[str, str, str]]:
    """[(entity_id, surface, typ), ...] over the concept universe (canonical + variants)."""
    from dataset.concepts_loader import load_concepts  # type: ignore

    bench_root = Path(__file__).resolve().parents[2]
    out: list[tuple[str, str, str]] = []
    for c in load_concepts(bench_root / "dataset" / "concepts.jsonl"):
        surfaces = [c.concept] + [v.surface for v in c.variants]
        for s in dict.fromkeys(surfaces):
            out.append((c.canonical_id, s, c.entity_type))
    return out


def oracle_keys(corpus: QACorpus, g: GoldGraph) -> dict[tuple[str, str], str]:
    return {(eid, s): eid for (eid, s, _t) in _entity_surfaces(g)}


def none_keys(corpus: QACorpus, g: GoldGraph) -> dict[tuple[str, str], str]:
    return {(eid, s): f"{eid}::{s}::{i}" for i, (eid, s, _t) in enumerate(_entity_surfaces(g))}


def name_only_keys(corpus: QACorpus, g: GoldGraph) -> dict[tuple[str, str], str]:
    return {(eid, s): s for (eid, s, _t) in _entity_surfaces(g)}


def goldengraph_keys(corpus: QACorpus, g: GoldGraph) -> dict[tuple[str, str], str]:
    rows = _entity_surfaces(g)
    import polars as pl  # noqa: F401  (guarded by POLARS_SKIP_CPU_CHECK in tests/CI)
    import goldenmatch as gm

    df = pl.DataFrame({"name": [s for (_e, s, _t) in rows], "type": [t for (_e, _s, t) in rows]})
    # rerank OFF: name+type is two fields, under the 3-field cross-encoder trigger;
    # this keeps the gate network-free (no HuggingFace download).
    result = gm.dedupe_df(df)  # zero-config; 2 fields => no rerank
    cluster_of = result.cluster_ids()  # row index -> cluster id  (CONFIRM exact accessor)
    return {(rows[i][0], rows[i][1]): f"c{cluster_of[i]}" for i in range(len(rows))}
```

> **CONFIRM during impl:** the exact `dedupe_df` result accessor that yields a per-row cluster/group id (`.cluster_ids()` is a placeholder — check `goldenmatch.dedupe_df`'s return type; it may be a frame with a `cluster_id` column). Use whatever maps row index → group. This is the only API-shape unknown; everything else is verified.

- [ ] **Step 4: Run → pass.**
- [ ] **Step 5: Commit** — `feat(er-kg-bench): ER-quality dial key-policies`.

---

## Task 3: Bridge-recall metric (wheel-free)

**Files:**
- Create: `.../erkgbench/qa_e2e/scorecard.py`
- Test: `.../tests/test_qa_bridge_recall.py`

Bridge-recall operates on a **resolved subgraph dict** + a **canonical-coverage map** (`entity_id -> set(canonical_ids)` derived from the build's `record_key -> canonical` side map). It does NOT touch the store — fully wheel-free and unit-testable.

- [ ] **Step 1: Write failing tests**

```python
from erkgbench.qa_e2e.scorecard import bridge_recall

# resolved subgraph: store-entity ids -> edges; coverage maps store id -> canonical ids
_CHAIN = [("c0", "works_at", "c1"), ("c1", "acquired", "c2")]  # 2-hop gold chain


def _sub(edges):
    ents = sorted({e for pair in edges for e in (pair[0], pair[2])})
    return {"entities": [{"entity_id": e} for e in ents],
            "edges": [{"subj": s, "predicate": p, "obj": o} for (s, p, o) in edges]}


def test_full_chain_present_recall_one():
    # store ids 0,1,2 each carry exactly canonical c0,c1,c2; both edges present
    sub = _sub([(0, "works_at", 1), (1, "acquired", 2)])
    cov = {0: {"c0"}, 1: {"c1"}, 2: {"c2"}}
    r = bridge_recall(_CHAIN, sub, cov)
    assert r["whole_chain"] == 1.0 and r["edge_recall"] == 1.0


def test_undermerged_bridge_breaks_walk():
    # c1's mentions are SPLIT: node 1 carries c1 (reached from c0) but the c1->c2
    # edge was authored from node 3 (the other c1 surface). Walk cannot continue.
    sub = _sub([(0, "works_at", 1), (3, "acquired", 2)])
    cov = {0: {"c0"}, 1: {"c1"}, 3: {"c1"}, 2: {"c2"}}
    r = bridge_recall(_CHAIN, sub, cov)
    assert r["whole_chain"] == 0.0
    assert r["edge_recall"] == 0.5  # first edge reachable, second not from the carried node


def test_missing_from_ball_zero():
    sub = _sub([(0, "works_at", 1)])  # second edge absent entirely
    cov = {0: {"c0"}, 1: {"c1"}}
    r = bridge_recall(_CHAIN, sub, cov)
    assert r["whole_chain"] == 0.0
```

- [ ] **Step 2: Run → fail.**

- [ ] **Step 3: Implement `bridge_recall`**

```python
"""Per-stage scorecard metrics. bridge_recall = does the resolved+retrieved
subgraph let you WALK the gold chain end to end (the (ER)^hops thesis at the
retrieval layer; no LLM)."""
from __future__ import annotations


def _nodes_covering(coverage: dict, canon: str) -> set:
    return {nid for nid, cset in coverage.items() if canon in cset}


def bridge_recall(gold_chain, subgraph: dict, coverage: dict) -> dict:
    """gold_chain: [(src_canon, rel, dst_canon), ...]. coverage: store entity_id ->
    set(canonical_ids it carries). Returns {"whole_chain": 0/1, "edge_recall": frac}.

    Walk: start from the set of store nodes covering the first src canonical; for
    each gold edge, can we step (via a subgraph edge, predicate ignored -- gold
    predicates come from the same triples) from a carried node to a node covering
    the next canonical? Carry that node set forward. An edge that strands (no such
    step) ends the walk; remaining edges are unreachable."""
    adj: dict = {}
    for e in subgraph.get("edges", ()):
        adj.setdefault(e["subj"], set()).add(e["obj"])
        adj.setdefault(e["obj"], set()).add(e["subj"])  # undirected: synthesis walks either way
    carried = _nodes_covering(coverage, gold_chain[0][0])
    edges_hit = 0
    for (_src, _rel, dst) in gold_chain:
        targets = _nodes_covering(coverage, dst)
        reachable = {t for c in carried for t in adj.get(c, ()) if t in targets}
        if not reachable:
            break
        edges_hit += 1
        carried = reachable
    return {"whole_chain": 1.0 if edges_hit == len(gold_chain) else 0.0,
            "edge_recall": edges_hit / len(gold_chain) if gold_chain else 0.0}
```

- [ ] **Step 4: Run → pass.**
- [ ] **Step 5: Commit** — `feat(er-kg-bench): bridge-recall metric`.

---

## Task 4: Store-build + ablation runner (needs the wheel)

**Files:**
- Create: `.../erkgbench/qa_e2e/ablation.py`
- Test: `.../tests/test_qa_ablation.py` (guarded `importorskip("goldengraph_native")`)

`ablation.py` ties it together: for each dial, build a store directly from gold triples (record_keys from the dial), retrieve per question (oracle-seeded), compute bridge-recall by hop.

- [ ] **Step 1: Write the failing test**

```python
import pytest

pytest.importorskip("goldengraph_native")
from erkgbench.qa_e2e.ablation import run_ablation


def test_ablation_monotonic_and_hop_widening():
    res = run_ablation(seed=7, n_questions=60, ambiguity=0.6, max_hops=4)
    oracle, gg, name_only, none = (
        res.recall[d] for d in ("oracle", "goldengraph", "name_only", "none")
    )
    # 1. monotone in ER quality (mean whole-chain recall)
    assert oracle["mean"] >= gg["mean"] >= name_only["mean"] - 1e-9 >= none["mean"] - 1e-9
    # 2. oracle-none gap widens with hops
    gap_lo = oracle["by_hop"][2] - none["by_hop"][2]
    gap_hi = oracle["by_hop"][4] - none["by_hop"][4]
    assert gap_hi > gap_lo
    # 3. resolver earns its keep (SOFT: >= within a small margin)
    assert gg["mean"] >= name_only["mean"]
```

- [ ] **Step 2: Run → fail** (skips if no wheel locally; in CI gate it fails on missing `run_ablation`).

- [ ] **Step 3: Implement `ablation.py`**

Build path per dial (bypass `ingest_corpus`/`_extract`):
1. `g = GoldGraph.from_corpus(corpus)`; `km = dials.<dial>_keys(corpus, g)`.
2. For each edge document, derive the two mention surfaces actually rendered in `Document.text` (parse the doc text, OR — simpler & exact — re-derive from the doc id's canonical ids + the gold, choosing the surface deterministically the SAME way the generator did). **CONFIRM the cleanest source of the rendered surface** (the doc text contains it; `engineered.py` renders `src`/`dst` mentions). Pragmatic: extract the two surfaces from `Document.text` via the known render template, or extend `generate_engineered` to also emit per-document `(src_surface, dst_surface)` gold metadata (preferred — a tiny, backward-compatible addition to the generator). Use the metadata route if parsing is fragile.
3. Build one `Extraction(mentions=[Mention(src_surface, src_typ), Mention(dst_surface, dst_typ)], relationships=[Relationship(0, rel, 1)])`.
4. Build `ResolvedEntity`s with `record_keys=[km[(entity_id, surface)]]`, `member_idx=[i]`, `surface_names=[surface]`. (Each mention its own ResolvedEntity; the STORE merges across docs by record_key.)
5. Maintain `record_key -> canonical_id` side map as you go.
6. `store.append(json.dumps(build_batch(extraction, entities, at=i+1)))`.
7. After all docs: `slice_graph = store.as_of(_AS_OF, _AS_OF)`. Build `coverage`: enumerate `slice_graph.entities()`, map each `entity_id` → `{canonical for rk in entity["record_keys"] for canonical in side_map[rk]}`.
8. Per question: oracle-seed = the store entity id covering `qa.start_entity_id`; `subgraph = _retrieve_local(slice_graph, [seed], max_hops, node_budget)`; `bridge_recall(gold_chain(g, qa), subgraph, coverage)`; bucket by `qa.hop_count`.
9. Aggregate per dial: `mean` whole-chain + `by_hop[k]` mean.

Return a small dataclass `AblationResult(recall: dict[str, dict])`.

- [ ] **Step 4: Run in CI gate (Task 6) / locally if wheel present → pass.**
- [ ] **Step 5: Commit** — `feat(er-kg-bench): ER-ablation runner (store build + bridge-recall by hop)`.

---

## Task 5: CLI + ABLATION.md

**Files:**
- Create: `.../erkgbench/qa_e2e/run_ablation.py`
- Test: extend `test_qa_ablation.py` with a markdown-render unit test (wheel-free: feed a fake `AblationResult`).

- [ ] **Step 1: Failing test** — `render_ablation_md(result)` returns a string containing the 4 dial rows, the by-hop columns, and a PASS/FAIL line per assertion.
- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement** `run_ablation.py`: argparse (`--seed --n-questions --ambiguity --max-hops --out-md`), call `run_ablation`, evaluate the 3 assertions, write `ABLATION.md`, exit non-zero if a HARD assertion fails (soft assertion 3 prints a WARN line, never fails the process — the gate's hard failures are assertions 1+2).
- [ ] **Step 4: Run → pass.**
- [ ] **Step 5: Commit** — `feat(er-kg-bench): run_ablation CLI + ABLATION.md`.

---

## Task 6: CI gate (key-free, builds the wheel)

**Files:**
- Modify: `.github/workflows/bench-er-kg.yml` (add a `qa-ablation` job mirroring the existing SP6 `gate` job's wheel-build + key-free pattern).

- [ ] **Step 1:** Add a `qa-ablation` job: checkout → install goldenmatch + maturin → build the goldengraph native wheel (same steps as the `goldengraph-pipeline`/SP6 gate; reuse the `CARGO_TARGET_DIR` off-exFAT only applies locally, CI is clean Linux) → run `python -m erkgbench.qa_e2e.run_ablation --seed 7 --n-questions 80 --ambiguity 0.6 --out-md ABLATION.md` with **no API key in env** → upload `ABLATION.md` as an artifact. The process exit code (hard assertions 1+2) gates the job.
- [ ] **Step 2:** Validate YAML parses: `python -c "import yaml; yaml.safe_load(open('.github/workflows/bench-er-kg.yml'))"`.
- [ ] **Step 3: Commit** — `ci(er-kg-bench): key-free qa-ablation gate`.
- [ ] **Step 4:** Push branch, open PR, confirm the `qa-ablation` lane goes green (it's the real validator — the wheel build + offline run can't be checked locally on the exFAT box). Iterate on the `dedupe_df` accessor / surface-source CONFIRM items if the lane reveals them.

---

## Phase 2 (follow-up PR, slice A part 2 — NOT in this plan)

Wired into the opt-in `bench-graphrag-qa` lane (real LLM, budget-capped):
- **extraction-F1** — capture extracted triples during a real build, score entity-F1/relation-F1 vs the gold triples (`gold.py` already has them).
- **synthesis-given-gold-subgraph** — feed `synthesize_local`/`_hybrid` the gold chain subgraph, measure answer-match (synthesis ceiling).
- **answer-match confirmation** — the same 4-dial ablation measuring real-LLM answer-match; assert it tracks bridge-recall.

## Done criteria (this plan)

- Tasks 1–3 wheel-free tests green via the shadow+polars-guard command.
- `qa-ablation` CI lane green: `ABLATION.md` artifact shows oracle ≥ goldengraph ≥ name_only ≥ none, the oracle−none gap widens 2-hop→4-hop (hard), and the goldengraph≥name_only soft line.
- No API key / network needed for the gate; the full suite is never run locally.

## Not in scope (YAGNI)

Real-LLM scorecard rows (Phase 2), aggregation/temporal corpora (slice B), crossover sweep (slice C), KG-vs-KG (slice D), embedding seed-recall, ANN/persisted embeddings, any `mode="local"`/engine behavior change.
