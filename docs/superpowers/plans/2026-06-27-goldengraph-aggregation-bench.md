# GoldenGraph Aggregation Bench (slice B1) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Measure the first capability RAG structurally can't do — set/count aggregation — with a free, deterministic CI gate proving goldengraph's exact traversal stays size-invariant while a passage-window floor's recall collapses as the set grows.

**Architecture:** A new fan-out corpus (one source entity with many objects per relation) reuses the engineered entity universe + `src::rel::dst` doc-id convention. goldengraph answers by exact traversal over the resolved store (`ablation._build_store` reused with an oracle resolver); a deterministic substring passage-window floor is the comparison. The gate asserts the `(goldengraph − floor)` set-F1 gap widens with set size. An opt-in real-LLM RAG row (reusing #1276's `_BudgetedLLM`) is the realistic confirmation.

**Tech Stack:** Python 3.11, pytest (wheel-free except the goldengraph-traversal row, which `importorskip`s `goldengraph_native`), GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-06-27-goldengraph-aggregation-bench-design.md`

---

## Key code facts (verified against main)

- **Reusable engineered helpers** (`erkgbench/qa_e2e/engineered.py`): `_Entity(id, canonical, variants)`, `_load_entities() -> list[_Entity]`, `_render_mention(ent, rng, ambiguity) -> str`, `_edge_doc_id(src_id, rel, dst_id) -> "src::rel::dst"`, `RELATION_SCHEMA` (5 relations).
- **`Document`** (`corpora.py`): `id, text, src_surface="", dst_surface=""`. **`QACorpus(name, documents, questions)`.**
- **`ablation._build_store(corpus, g, km, typ_of) -> (slice_graph, coverage)`** — iterates `corpus.documents`, parses `d.id.split("::")` (exactly 3 parts), reads `d.src_surface`/`d.dst_surface`, builds an `Extraction` with `Relationship(subj=0, predicate=rel, obj=1)`, resolves via `km` record-keys, `store.append`. Returns `slice_graph` + `coverage: entity_id -> set(canonical_id)` (from readable `surface_names` + `dials.surface_to_canon`). It uses `g` ONLY for `dials.surface_to_canon(g)`, which reads the concept universe (edge-count-independent) — so it reuses on a fan-out corpus unchanged. `ablation._typ_of(g) -> {canonical_id: entity_type}`. `ablation._DIALS`, `dials.oracle_keys(corpus, g)`.
- **Retrieval/traversal:** `slice_graph.query([node_id], 1) -> {"entities":[{entity_id,canonical_name,typ,surface_names}], "edges":[{subj,predicate,obj}]}`. Edges are DIRECTED (`subj`->`obj` from the relationship). **The aggregation traversal filters edges by `predicate == relation` — the FIRST consumer that depends on the predicate surviving the store round-trip verbatim (`bridge_recall` ignores it). Task 3 pins that.**
- **#1276 reuse:** `from .scorecard_llm import _BudgetedLLM` (records token-estimate usage to a `BudgetTracker`, exposes `.exhausted`); `from . import metrics` (`metrics.answer_match` not needed here — set-F1 is new).
- `goldengraph` is a STANDALONE package (not in the uv workspace). Local test runs need it on PYTHONPATH (CI installs it editable). See test env below.

### Reviewer precision notes (carry these)
1. **The fan-out generator MUST populate `src_surface`/`dst_surface`** on every edge-doc (the `""` default → empty-surface store nodes → empty coverage → broken traversal).
2. **`goldengraph_aggregate` must invert `coverage` to a store-node id** from the canonical anchor (the `seed_of` pattern: `node_of_canon = {c: nid for nid in sorted(coverage) for c in coverage[nid]}`, first-wins).
3. **Predicate round-trip** — add a test asserting the stored predicate comes back verbatim through `_build_store -> query` (Task 3), since a multi-relation anchor would otherwise pull wrong-relation objects.
4. **Bucketing** — pin >= 20 questions/size-bucket and confirm the concept universe has enough distinct members for the largest fan-out bucket (the generator must not request more members than exist).

## Test environment

```
PY="/d/show_case/goldenmatch/.venv/Scripts/python.exe"
GG="D:/show_case/goldenmatch/.worktrees/gg-aggregation/packages/python/goldengraph"
cd packages/python/goldenmatch/benchmarks/er-kg-bench
PYTHONPATH="$(pwd -W);$GG" POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8 GOLDENMATCH_NATIVE=0 "$PY" -m pytest <path> -q
```
Wheel-free for Tasks 1, 2, 4 (+ floor) and the LLM-row's non-LLM parts; the goldengraph-traversal e2e (Task 3) `importorskip`s `goldengraph_native`. Run only named files.

---

## Task 1: Fan-out corpus generator + gold accessor (wheel-free)

**Files:**
- Create: `erkgbench/qa_e2e/aggregation.py`
- Test: `tests/test_qa_aggregation_corpus.py`

Use @superpowers:test-driven-development.

- [ ] **Step 1: Write the failing tests**

```python
"""Fan-out aggregation corpus: one source entity, many objects per relation, one
edge-doc per (X, rel, member). The gold member SET is emitted directly (not
re-derived from doc-id parsing). Deterministic for a seed."""
from __future__ import annotations

from erkgbench.qa_e2e.aggregation import generate_aggregation


def test_emits_list_and_count_questions_with_gold_sets():
    docs, qs = generate_aggregation(seed=7, n_anchors=12, ambiguity=0.5,
                                    fanout_buckets=((2, 4), (5, 10), (11, 20)))
    assert any(q.kind == "list" for q in qs) and any(q.kind == "count" for q in qs)
    for q in qs:
        # gold set size matches the count gold; members are canonical ids
        assert q.gold_count == len(q.gold_members)
        assert q.relation  # the relation is stated
        assert q.relation in q.question  # question names the stated relation
        assert q.anchor_id


def test_edge_docs_are_3part_ids_with_populated_surfaces():
    docs, qs = generate_aggregation(seed=7, n_anchors=8, ambiguity=0.6,
                                    fanout_buckets=((2, 4), (11, 20)))
    for d in docs:
        assert len(d.id.split("::")) == 3            # src::rel::dst (reuse _build_store)
        assert d.src_surface and d.dst_surface       # NOT the "" default (note 1)


def test_gold_members_match_the_emitted_edges_for_an_anchor():
    docs, qs = generate_aggregation(seed=3, n_anchors=6, ambiguity=0.0,
                                    fanout_buckets=((3, 6),))
    by_anchor = {}
    for d in docs:
        s, rel, o = d.id.split("::")
        by_anchor.setdefault((s, rel), set()).add(o)
    for q in (q for q in qs if q.kind == "list"):
        assert set(q.gold_members) == by_anchor[(q.anchor_id, q.relation)]


def test_buckets_each_get_enough_questions(monkeypatch):
    docs, qs = generate_aggregation(seed=7, n_anchors=60, ambiguity=0.3,
                                    fanout_buckets=((2, 4), (5, 10), (11, 20)))
    # size bucket of a question = len(gold_members); each bucket >= 20 list+count qs
    from erkgbench.qa_e2e.aggregation import size_bucket
    counts = {}
    for q in qs:
        counts[size_bucket(q.gold_count)] = counts.get(size_bucket(q.gold_count), 0) + 1
    assert all(c >= 20 for c in counts.values())
```

- [ ] **Step 2: Run -> fail** (`ModuleNotFoundError ... aggregation`).

- [ ] **Step 3: Implement** `aggregation.py` (corpus half):

```python
"""Aggregation/set/count capability bench (slice B1). A fan-out corpus + goldengraph
exact traversal vs a deterministic passage-window floor. The KG does what RAG can't:
exact set aggregation, size-invariant; the window's recall collapses with set size."""
from __future__ import annotations

import random
from dataclasses import dataclass

from .corpora import Document, QACorpus
from .engineered import RELATION_SCHEMA, _edge_doc_id, _load_entities, _render_mention

_BUCKETS = ((2, 4), (5, 10), (11, 20))


@dataclass(frozen=True)
class AggQuestion:
    id: str
    kind: str            # "list" | "count"
    question: str
    anchor_id: str       # canonical id of the source entity
    relation: str
    gold_members: tuple[str, ...]  # canonical ids of the member set
    gold_count: int


def size_bucket(n: int) -> str:
    for lo, hi in _BUCKETS:
        if lo <= n <= hi:
            return f"{lo}-{hi}"
    return f">{_BUCKETS[-1][1]}"


def generate_aggregation(*, seed: int, n_anchors: int, ambiguity: float,
                         fanout_buckets=_BUCKETS):
    rng = random.Random(seed)
    ents = _load_entities()
    by_id = {e.id: e for e in ents}
    ids = [e.id for e in ents]
    docs: list[Document] = []
    qs: list[AggQuestion] = []
    # Cycle buckets across anchors so every bucket fills; clamp fan-out to available
    # members (note 4 -- never request more distinct members than exist).
    for i in range(n_anchors):
        lo, hi = fanout_buckets[i % len(fanout_buckets)]
        src_id = ids[i % len(ids)]
        # Relation varies on the OUTER cycle so (src_id, rel) is unique per anchor up
        # to n_anchors = len(ids)*len(RELATION_SCHEMA) (=225), AND a reused src_id
        # accumulates MULTIPLE relations in the store -- both are load-bearing: the
        # former keeps the gate's exactness (no two anchors merge into one node and
        # union their gold sets -> set-F1 precision would halve and the HARD gate
        # would fail); the latter makes the Task 3 predicate test real. Do NOT revert
        # to `i % len(RELATION_SCHEMA)` (collides at n_anchors>len(ids)).
        rel = RELATION_SCHEMA[(i // len(ids)) % len(RELATION_SCHEMA)]
        k = min(rng.randint(lo, hi), len(ids) - 1)
        members = rng.sample([x for x in ids if x != src_id], k)
        for m in members:
            s = _render_mention(by_id[src_id], rng, ambiguity)
            o = _render_mention(by_id[m], rng, ambiguity)
            docs.append(Document(
                id=_edge_doc_id(src_id, rel, m),
                text=f"{s} {rel.replace('_', ' ')} {o}.",
                src_surface=s, dst_surface=o,
            ))
        rel_words = rel.replace("_", " ")
        canon = by_id[src_id].canonical
        qs.append(AggQuestion(
            id=f"agg-list-{i}", kind="list",
            question=f"List all entities that {canon} {rel_words}.",
            anchor_id=src_id, relation=rel,
            gold_members=tuple(members), gold_count=len(members)))
        qs.append(AggQuestion(
            id=f"agg-count-{i}", kind="count",
            question=f"How many entities does {canon} {rel_words}?",
            anchor_id=src_id, relation=rel,
            gold_members=tuple(members), gold_count=len(members)))
    return tuple(docs), qs


def agg_documents_corpus(docs) -> QACorpus:
    """Wrap the fan-out docs as a QACorpus so ablation._build_store can consume it."""
    return QACorpus(name="aggregation", documents=tuple(docs), questions=())
```

- [ ] **Step 4: Run -> pass.** (If `test_buckets_each_get_enough_questions` underfills, raise `n_anchors`; each anchor yields 1 list + 1 count, so ~`n_anchors/len(buckets)*2` per bucket — `n_anchors=60` over 3 buckets = 40 q/bucket. The outer-cycle relation assignment keeps `(src_id, rel)` unique through `n_anchors=225`, so 60 is safe.)
- [ ] **Step 5: Commit** — `feat(er-kg-bench): fan-out aggregation corpus + gold accessor`.

---

## Task 2: set-F1 + count-accuracy metrics (wheel-free)

**Files:**
- Modify: `erkgbench/qa_e2e/aggregation.py`
- Test: `tests/test_qa_aggregation_metric.py`

- [ ] **Step 1: Write failing tests**

```python
from erkgbench.qa_e2e.aggregation import count_accuracy, set_f1


def test_set_f1_perfect():
    r = set_f1({"a", "b", "c"}, {"a", "b", "c"})
    assert r == {"precision": 1.0, "recall": 1.0, "f1": 1.0}


def test_set_f1_missing_member_drops_recall():
    r = set_f1({"a", "b"}, {"a", "b", "c"})  # missing c
    assert r["recall"] < 1.0 and r["precision"] == 1.0


def test_set_f1_extra_drops_precision():
    r = set_f1({"a", "b", "c", "x"}, {"a", "b", "c"})  # spurious x
    assert r["precision"] < 1.0 and r["recall"] == 1.0


def test_set_f1_empty_gold_no_crash():
    assert set_f1(set(), set())["f1"] == 0.0


def test_count_accuracy_exact():
    assert count_accuracy(3, 3) == 1.0
    assert count_accuracy(2, 3) == 0.0
```

- [ ] **Step 2: Run -> fail.**

- [ ] **Step 3: Implement** (append to `aggregation.py`):

```python
def set_f1(predicted: set, gold: set) -> dict:
    tp = len(predicted & gold)
    p = tp / len(predicted) if predicted else 0.0
    r = tp / len(gold) if gold else 0.0
    f1 = 2 * p * r / (p + r) if (p + r) else 0.0
    return {"precision": p, "recall": r, "f1": f1}


def count_accuracy(predicted_count: int, gold_count: int) -> float:
    return 1.0 if predicted_count == gold_count else 0.0
```

- [ ] **Step 4: Run -> pass.**
- [ ] **Step 5: Commit** — `feat(er-kg-bench): set-F1 + count-accuracy metrics`.

---

## Task 3: goldengraph exact traversal + predicate round-trip (needs the wheel)

**Files:**
- Modify: `erkgbench/qa_e2e/aggregation.py`
- Test: `tests/test_qa_aggregation_traversal.py` (`importorskip`)

- [ ] **Step 1: Write the failing tests**

```python
import pytest

pytest.importorskip("goldengraph_native")
from erkgbench.qa_e2e import ablation, dials
from erkgbench.qa_e2e.aggregation import (
    agg_documents_corpus, generate_aggregation, goldengraph_aggregate,
)
from erkgbench.qa_e2e.gold import GoldGraph


def _build(docs):
    corpus = agg_documents_corpus(docs)
    g = GoldGraph.from_corpus(corpus)
    km = dials.oracle_keys(corpus, g)            # oracle: anchor merges across docs
    slice_graph, coverage = ablation._build_store(corpus, g, km, ablation._typ_of(g))
    return slice_graph, coverage


def test_traversal_returns_the_exact_member_set():
    docs, qs = generate_aggregation(seed=7, n_anchors=20, ambiguity=0.6,
                                    fanout_buckets=((2, 4), (11, 20)))
    slice_graph, coverage = _build(docs)
    for q in (q for q in qs if q.kind == "list"):
        got = goldengraph_aggregate(slice_graph, coverage, q.anchor_id, q.relation)
        assert got == set(q.gold_members)  # exact, size-invariant


def test_predicate_survives_the_store_round_trip():
    # The outer-cycle relation assignment means a reused src_id holds MULTIPLE
    # relations once n_anchors > len(ids) (45). Find an anchor_id that has >=2
    # distinct relations among the questions, then assert traversal of rel A returns
    # A's members and EXCLUDES B's -- the real wrong-relation-exclusion check.
    docs, qs = generate_aggregation(seed=7, n_anchors=95, ambiguity=0.0,
                                    fanout_buckets=((3, 6),))
    slice_graph, coverage = _build(docs)
    rels_by_anchor: dict = {}
    for q in (q for q in qs if q.kind == "list"):
        rels_by_anchor.setdefault(q.anchor_id, {})[q.relation] = set(q.gold_members)
    anchor = next(a for a, rm in rels_by_anchor.items() if len(rm) >= 2)
    rel_a, rel_b = list(rels_by_anchor[anchor])[:2]
    got_a = goldengraph_aggregate(slice_graph, coverage, anchor, rel_a)
    assert got_a == rels_by_anchor[anchor][rel_a]            # A's members come back
    assert not (got_a & rels_by_anchor[anchor][rel_b] - rels_by_anchor[anchor][rel_a])
    # ^ none of B's exclusive members leak into A's traversal (predicate filter works)
```

- [ ] **Step 2: Run -> fail/skip** (skips locally without the wheel; in the gate lane it fails on missing `goldengraph_aggregate`).

- [ ] **Step 3: Implement** (append to `aggregation.py`):

```python
def goldengraph_aggregate(slice_graph, coverage, anchor_id: str, relation: str) -> set:
    """Exact traversal: seed the anchor's store node (invert coverage), pull its
    1-hop ball, filter edges to `relation` with subj == anchor node, map obj nodes
    -> covered canonical members. No LLM, no embedder; size-invariant."""
    node_of_canon: dict = {}
    for nid in sorted(coverage):                 # ascending id -> deterministic
        for c in coverage[nid]:
            node_of_canon.setdefault(c, nid)
    seed = node_of_canon.get(anchor_id)
    if seed is None:
        return set()
    ball = slice_graph.query([seed], 1)
    members: set = set()
    for e in ball.get("edges", ()):
        if e["subj"] == seed and e["predicate"] == relation:
            members |= coverage.get(e["obj"], set())
    members.discard(anchor_id)                   # never count the anchor itself
    return members
```

- [ ] **Step 4: Run in the gate lane (Task 6) / locally if the wheel is present -> pass.**
- [ ] **Step 5: Commit** — `feat(er-kg-bench): goldengraph exact-traversal aggregation`.

---

## Task 4: deterministic passage-window floor + gate assertions + render (wheel-free)

**Files:**
- Modify: `erkgbench/qa_e2e/aggregation.py`
- Test: `tests/test_qa_aggregation_floor.py`

- [ ] **Step 1: Write failing tests**

```python
from erkgbench.qa_e2e.aggregation import (
    AggregationResult, gate_verdicts, passage_window_floor, render_aggregation_md,
)


def _docs(anchor_surface, member_surfaces):
    from erkgbench.qa_e2e.corpora import Document
    return tuple(
        Document(id=f"gm:a::rel::gm:m{i}", text=f"{anchor_surface} rel {m}.",
                 src_surface=anchor_surface, dst_surface=m)
        for i, m in enumerate(member_surfaces)
    )


def test_floor_recall_capped_by_window():
    members = [f"M{i}" for i in range(30)]
    docs = _docs("Acme", members)
    universe = {"M%d" % i: "gm:m%d" % i for i in range(30)}  # surface -> canonical
    got = passage_window_floor(docs, {"Acme"}, "rel", passage_k=10, surface_to_canon=universe)
    # only 10 docs in the window -> <= 10 distinct members recovered
    assert len(got) <= 10


def test_floor_full_when_window_covers_set():
    members = [f"M{i}" for i in range(5)]
    docs = _docs("Acme", members)
    universe = {"M%d" % i: "gm:m%d" % i for i in range(5)}
    got = passage_window_floor(docs, {"Acme"}, "rel", passage_k=10, surface_to_canon=universe)
    assert got == {"gm:m%d" % i for i in range(5)}


def test_gate_verdicts_widening_gap_passes():
    # per-bucket (goldengraph, floor) set-F1 means: gg flat-high, floor collapses
    gg = {"2-4": 1.0, "11-20": 1.0}
    floor = {"2-4": 0.9, "11-20": 0.3}
    v = gate_verdicts(gg, floor, gg_threshold=0.9)
    assert all(passed for _l, passed, _hard in v)


def test_gate_verdicts_flat_gap_fails_hard():
    gg = {"2-4": 1.0, "11-20": 1.0}
    floor = {"2-4": 0.5, "11-20": 0.5}  # no collapse -> gap doesn't widen
    v = gate_verdicts(gg, floor, gg_threshold=0.9)
    widen = next(p for l, p, h in v if "widen" in l.lower())
    assert widen is False


def test_render_has_buckets_and_verdicts():
    res = AggregationResult(
        gg_setf1={"2-4": 1.0, "11-20": 1.0},
        floor_setf1={"2-4": 0.9, "11-20": 0.3},
        gg_count_acc={"2-4": 1.0, "11-20": 1.0},
        llm_setf1=None,
    )
    md = render_aggregation_md(res)
    assert "2-4" in md and "11-20" in md and ("PASS" in md or "FAIL" in md)
```

- [ ] **Step 2: Run -> fail.**

- [ ] **Step 3: Implement** (append to `aggregation.py`): `passage_window_floor` (substring match on ANY anchor surface, first `passage_k` docs in order, extract entity-universe surfaces -> canonical set, minus the anchor), `AggregationResult` dataclass, `gate_verdicts` (3 verdicts: gg>=threshold every bucket [hard]; floor smallest>largest [hard]; gap widens largest>smallest [hard]), `render_aggregation_md`, and `gate_exit_code` (1 if any hard verdict fails). Buckets are ordered by `_BUCKETS`; "smallest"/"largest" = first/last populated bucket.

```python
def passage_window_floor(docs, anchor_surfaces: set, relation: str, *,
                         passage_k: int, surface_to_canon: dict) -> set:
    hits = [d for d in docs if any(a in d.text for a in anchor_surfaces)][:passage_k]
    out: set = set()
    for d in hits:
        for surf, canon in surface_to_canon.items():
            if surf in d.text:
                out.add(canon)
    # the anchor's own canonical(s) are not members
    for a in anchor_surfaces:
        out.discard(surface_to_canon.get(a))
    out.discard(None)
    return out
```

(`gate_verdicts` / `render_aggregation_md` / `AggregationResult` / `gate_exit_code` are straightforward over the per-bucket dicts — model on `ablation.evaluate_assertions` / `render_ablation_md`.)

- [ ] **Step 4: Run -> pass.**
- [ ] **Step 5: Commit** — `feat(er-kg-bench): passage-window floor + gate verdicts + render`.

---

## Task 5: deterministic runner + CLI

**Files:**
- Modify: `erkgbench/qa_e2e/aggregation.py` (a `run_aggregation_deterministic` orchestrator)
- Create: `erkgbench/qa_e2e/run_aggregation.py` (CLI)
- Test: `tests/test_qa_aggregation_cli.py` (argparse + `gate_exit_code` wheel-free; the full run is wheel-gated)

- [ ] **Step 1:** `run_aggregation_deterministic(*, seed, n_anchors, ambiguity, passage_k)` builds the corpus, the oracle store (`ablation._build_store`), runs `goldengraph_aggregate` + `passage_window_floor` per list-question, aggregates set-F1 by `size_bucket`, count-accuracy by bucket, returns `AggregationResult`. (Needs the wheel — `importorskip` in its test.) The floor's `surface_to_canon` = invert `dials._entity_surfaces(g)` to `surface -> canonical_id` (a surface may map to multiple canonicals; pick deterministically or keep a set — for the floor, first-wins is fine).
- [ ] **Step 2:** `run_aggregation.py` CLI: `--seed/--n-anchors/--ambiguity/--passage-k/--out-md`; writes `AGGREGATION.md`; exits `gate_exit_code(res)`. `--with-llm` adds the real-LLM RAG row (Task 7) when `OPENAI_API_KEY` present.
- [ ] **Step 3:** wheel-free CLI test: `_parser().parse_args([])` defaults; `gate_exit_code` over a synthetic widening vs flat result (0 vs 1).
- [ ] **Step 4:** Run -> pass.
- [ ] **Step 5: Commit** — `feat(er-kg-bench): aggregation deterministic runner + CLI`.

---

## Task 6: key-free CI gate

**Files:**
- Modify: `.github/workflows/goldengraph-pipeline.yml` (a step after the #1274 ablation gate)
- Modify: `.github/workflows/bench-er-kg.yml` (add the new wheel-free test files to the pure-Python step)

- [ ] **Step 1:** In `goldengraph-pipeline.yml`, after the ER-ablation gate step, add an `aggregation` gate step (same wheel already built): `python -m erkgbench.qa_e2e.run_aggregation --seed 7 --n-anchors 60 --ambiguity 0.6 --passage-k 10 --out-md AGGREGATION.md`, no key; upload `AGGREGATION.md`. Process exit code (hard verdicts) gates the job.
- [ ] **Step 2:** In `bench-er-kg.yml` pure-Python step, append `tests/test_qa_aggregation_corpus.py tests/test_qa_aggregation_metric.py tests/test_qa_aggregation_floor.py tests/test_qa_aggregation_cli.py` (the wheel-free files; the traversal/runner files `importorskip`).
- [ ] **Step 3:** Validate both YAMLs parse.
- [ ] **Step 4: Commit** — `ci(goldengraph): key-free aggregation capability gate`.
- [ ] **Step 5:** Push, open PR, confirm the `pipeline` lane runs the aggregation gate green (the real validator for the traversal + gate).

---

## Task 7: opt-in real-LLM RAG confirmation row (non-gating)

**Files:**
- Modify: `erkgbench/qa_e2e/aggregation.py` (`llm_rag_aggregate`)
- Modify: `.github/workflows/bench-graphrag-qa.yml` (extend the #1276 `scorecard` job OR a sibling, gated on an input)
- Test: `tests/test_qa_aggregation_llm.py` (stub-LLM, wheel-free)

- [ ] **Step 1:** `llm_rag_aggregate(docs, question, anchor_surfaces, passage_k, llm) -> set`: retrieve the first `passage_k` anchor-mentioning docs, build a prompt "From these passages, list every entity that <X> <relation>. One per line.", call `llm.complete`, parse lines -> normalized set. Reuse `scorecard_llm._BudgetedLLM` for budget. Stub-LLM test: a `_StubLLM` returning a known newline list -> parsed set; assert the retrieval cap + parse work (no real key).
- [ ] **Step 2:** Wire into `bench-graphrag-qa.yml`: extend the opt-in lane to also produce the LLM-RAG set-F1 row in `AGGREGATION.md` when `run_scorecard`/a new toggle is set + `OPENAI_API_KEY` present. Non-gating.
- [ ] **Step 3:** Run the stub test -> pass.
- [ ] **Step 4: Commit** — `feat(er-kg-bench): opt-in real-LLM RAG aggregation confirmation`.

---

## Done criteria

- Wheel-free tests green (corpus, metrics, floor, gate verdicts, render, CLI, LLM-stub).
- `pipeline` lane runs the aggregation gate: `AGGREGATION.md` shows goldengraph set-F1 ~size-invariant high, floor set-F1 collapsing, and the gap WIDENING with set size (hard PASS).
- No existing gate touched; the new gate is additive; the real-LLM RAG row is opt-in/non-gating.

## Not in scope (YAGNI)

B2 temporal `as_of`; the ER-dial tie-in (under-merge degrading goldengraph's set-F1); embedding seed-recall; NL relation-parsing; new entity universe.
