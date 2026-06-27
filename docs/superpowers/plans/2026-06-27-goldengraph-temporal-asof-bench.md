# GoldenGraph Temporal `as_of` Bench (slice B2) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Measure the temporal `as_of` capability — answer "what was true about X as of date D" exactly via the bi-temporal store, where a passage retriever structurally can't — with a free, deterministic CI gate.

**Architecture:** A new corpus emits, per fact, two edges with explicit valid windows (`X-rel-A [T1,Tc)`, `X-rel-B [Tc,∞)`) hand-built into the store JSON. goldengraph answers by `store.as_of(valid_t=D)` traversal (the edge true at D); a deterministic temporal-blind floor returns the latest-mentioned object (wrong on past-date queries). The gate asserts goldengraph is accurate in both regimes and ≫ the floor on past-date queries. An opt-in real-LLM RAG row confirms.

**Tech Stack:** Python 3.11, pytest (wheel-free except the `as_of` traversal + the valid_to round-trip, which `importorskip` `goldengraph_native`), GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-06-27-goldengraph-temporal-asof-bench-design.md`

---

## Key code facts (verified against main)

- **Store JSON shape** (`build_batch` in `goldengraph/ingest.py`): `{"entities":[{local_id, canonical_name, typ, surface_names, record_keys}], "edges":[{subj_local, predicate, obj_local, valid_from, valid_to, source_refs}], "ingested_at": at}`. `build_batch` always emits `valid_to=None` — B2 hand-builds this dict with explicit `valid_to`.
- **valid_to is honored end-to-end** (reviewer-confirmed): `PyStore.append(json)` → serde `StoreBatch` (no rename, no validation) → `append` copies `e.valid_to` → `as_of` window filter `valid_from <= valid_t && valid_to.is_none_or(|vt| valid_t < vt)`. The regime boundary is half-open: `D=Tc → B`.
- **Store API:** `from goldengraph_native import _native as ggn; store = ggn.PyStore(); store.append(json.dumps(batch)); slice = store.as_of(valid_t, tx_t)`. `slice.query([node_id], 1) -> {"entities":[{entity_id, canonical_name, typ, surface_names}], "edges":[{subj, predicate, obj, source_refs}]}` (edges in slice-view id space). `slice.entities()` enumerates.
- **Reused:** `engineered._load_entities() -> [_Entity(id, canonical, variants)]`, `engineered._render_mention(ent, rng, ambiguity)`, `engineered.RELATION_SCHEMA` (5); `dials.surface_to_canon(g) -> {surface: set(canonical_id)}` (concept-universe, edge-independent — call with any `GoldGraph`); `gold.GoldGraph.from_corpus`; `scorecard_llm._BudgetedLLM` (#1276); `from . import metrics`.
- `goldengraph` is a STANDALONE package (PYTHONPATH for local runs; CI installs editable).

### Reviewer notes (carry these)
1. goldengraph is right-BY-CONSTRUCTION in both regimes → expected as_of-accuracy is **1.0**. The gate's `>=0.9` is defensive slack; a real ~0.9 is a signal to investigate, not "passing".
2. A fact's **two edges go in ONE batch** (single `ingested_at`); valid-time is the only discriminator — NOT two batches at different tx-times. "Corrections appended after originals" refers to **doc list order** (consumed by the wheel-free `temporal_blind_floor`, which reads docs), NOT edge/batch ingest order.
3. **Floor cleanliness:** sample the A/B objects from a pool DISJOINT from the anchor pool, so an anchor never appears as another fact's object (which would contaminate the floor's "docs mentioning the anchor"). The floor also filters by the relation phrase, so anchor-reuse-across-relations stays clean.

## Test environment

```
PY="/d/show_case/goldenmatch/.venv/Scripts/python.exe"
GG="D:/show_case/goldenmatch/.worktrees/gg-temporal/packages/python/goldengraph"
cd packages/python/goldenmatch/benchmarks/er-kg-bench
PYTHONPATH="$(pwd -W);$GG" POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8 GOLDENMATCH_NATIVE=0 "$PY" -m pytest <path> -q
```
Wheel-free for Tasks 1, 2, 4(partial), 6; Tasks 3 (valid_to round-trip + as_of traversal) `importorskip` `goldengraph_native`. Run only named files.

---

## Task 1: Temporal corpus generator + gold (wheel-free)

**Files:**
- Create: `erkgbench/qa_e2e/temporal.py`
- Test: `tests/test_qa_temporal_corpus.py`

Use @superpowers:test-driven-development.

- [ ] **Step 1: Write the failing tests**

```python
"""Temporal as_of corpus: per fact a value corrected at valid-time Tc
(X-rel-A [1,Tc), X-rel-B [Tc,inf)), with past (D<Tc -> A) and current (D>=Tc -> B)
questions. Deterministic for a seed."""
from __future__ import annotations

from erkgbench.qa_e2e.temporal import T1, generate_temporal


def test_facts_have_two_windows_and_both_regime_questions():
    docs, facts, qs = generate_temporal(seed=7, n_facts=20, ambiguity=0.5)
    assert any(q.regime == "past" for q in qs) and any(q.regime == "current" for q in qs)
    fbyk = {(f.anchor_id, f.relation): f for f in facts}
    for q in qs:
        f = fbyk[(q.anchor_id, q.relation)]
        if q.regime == "past":
            assert T1 <= q.D < f.tc and q.gold_obj == f.a_id
        else:
            assert q.D >= f.tc and q.gold_obj == f.b_id
        assert q.relation.replace("_", " ") in q.question


def test_objects_disjoint_from_anchors():
    # floor cleanliness: an anchor never appears as another fact's A/B object
    docs, facts, qs = generate_temporal(seed=7, n_facts=30, ambiguity=0.4)
    anchors = {f.anchor_id for f in facts}
    objects = {f.a_id for f in facts} | {f.b_id for f in facts}
    assert not (anchors & objects)


def test_each_regime_has_enough_questions():
    docs, facts, qs = generate_temporal(seed=7, n_facts=40, ambiguity=0.3)
    past = sum(1 for q in qs if q.regime == "past")
    current = sum(1 for q in qs if q.regime == "current")
    assert past >= 20 and current >= 20
```

- [ ] **Step 2: Run -> fail** (`ModuleNotFoundError ... temporal`).

- [ ] **Step 3: Implement** `temporal.py` (corpus half):

```python
"""Temporal as_of capability bench (slice B2). A bi-temporal corpus + goldengraph
store.as_of(D) traversal vs a temporal-blind passage floor. The KG does what RAG
can't: answer 'as of a PAST date' correctly when a fact was later corrected."""
from __future__ import annotations

import random
from dataclasses import dataclass

from .corpora import Document
from .engineered import RELATION_SCHEMA, _load_entities, _render_mention

T1 = 1            # valid_from of every original edge
_TMAX = 100       # query/date horizon
_N_ANCHORS = 20   # first N entities are anchors; the rest are objects (disjoint)


@dataclass(frozen=True)
class TemporalFact:
    anchor_id: str
    relation: str
    a_id: str     # original object (valid [T1, tc))
    b_id: str     # corrected object (valid [tc, inf))
    tc: int       # correction valid-time


@dataclass(frozen=True)
class TemporalQuestion:
    id: str
    question: str
    anchor_id: str
    relation: str
    D: int
    regime: str    # "past" | "current"
    gold_obj: str  # canonical id of the object true at D


def generate_temporal(*, seed: int, n_facts: int, ambiguity: float):
    rng = random.Random(seed)
    ents = _load_entities()
    by_id = {e.id: e for e in ents}
    ids = [e.id for e in ents]
    anchors = ids[:_N_ANCHORS]
    objects = ids[_N_ANCHORS:]
    docs: list[Document] = []
    facts: list[TemporalFact] = []
    qs: list[TemporalQuestion] = []
    for i in range(n_facts):
        src_id = anchors[i % len(anchors)]
        rel = RELATION_SCHEMA[(i // len(anchors)) % len(RELATION_SCHEMA)]  # B1 outer cycle
        a_id, b_id = rng.sample(objects, 2)
        tc = rng.randint(20, 80)
        facts.append(TemporalFact(src_id, rel, a_id, b_id, tc))
        rel_words = rel.replace("_", " ")
        # two source passages (real RAG reads these; nothing enforces a slice)
        xs = _render_mention(by_id[src_id], rng, ambiguity)
        docs.append(Document(id=f"{src_id}::{rel}::{a_id}::t{T1}",
                             text=f"As of {T1}, {xs} {rel_words} {_render_mention(by_id[a_id], rng, ambiguity)}.",
                             src_surface=xs, dst_surface=by_id[a_id].canonical))
        xs2 = _render_mention(by_id[src_id], rng, ambiguity)
        docs.append(Document(id=f"{src_id}::{rel}::{b_id}::t{tc}",
                             text=f"From {tc}, {xs2} {rel_words} {_render_mention(by_id[b_id], rng, ambiguity)}.",
                             src_surface=xs2, dst_surface=by_id[b_id].canonical))
        # one past + one current question per fact
        d_past = rng.randint(T1, tc - 1)
        d_cur = rng.randint(tc, _TMAX)
        for tag, D, regime, gold in (("p", d_past, "past", a_id), ("c", d_cur, "current", b_id)):
            qs.append(TemporalQuestion(
                id=f"tmp-{i}-{tag}",
                question=f"As of {D}, what does {by_id[src_id].canonical} {rel_words}?",
                anchor_id=src_id, relation=rel, D=D, regime=regime, gold_obj=gold))
    return tuple(docs), facts, qs
```

> Note: doc ids are 4-part (`src::rel::dst::t`) so they never collide with the 3-part engineered convention and `gold.GoldGraph.from_corpus` skips them (the store is built from FACTS, not doc-id parsing). The `tc >= 20` floor + `d_past in [T1, tc-1]` guarantees a non-empty past window.

- [ ] **Step 4: Run -> pass.** (`n_facts=40` over 20 anchors × outer-cycle relations → 40 distinct facts, 40 past + 40 current questions.)
- [ ] **Step 5: Commit** — `feat(er-kg-bench): temporal as_of corpus + gold`.

---

## Task 2: temporal-blind floor + as_of-accuracy + gate + render (wheel-free)

**Files:**
- Modify: `erkgbench/qa_e2e/temporal.py`
- Test: `tests/test_qa_temporal_floor.py`

- [ ] **Step 1: Write failing tests**

```python
from erkgbench.qa_e2e.corpora import Document
from erkgbench.qa_e2e.temporal import (
    TemporalResult, as_of_accuracy, gate_verdicts, render_temporal_md, temporal_blind_floor,
)


def _docs():
    # X works_at A (early), then X works_at B (correction, appended LATER)
    return (
        Document(id="x::works_at::a::t1", text="As of 1, X works at Apple.", src_surface="X", dst_surface="Apple"),
        Document(id="x::works_at::b::t5", text="From 5, X works at Google.", src_surface="X", dst_surface="Google"),
    )


def test_floor_returns_latest_object_ignoring_D():
    docs = _docs()
    s2c = {"X": "x", "Apple": "a", "Google": "b"}
    # asked about a PAST date (D=3, before the correction) -> floor STILL returns latest (b)
    got = temporal_blind_floor(docs, {"X"}, "works_at", D=3, surface_to_canon=s2c)
    assert got == "b"  # wrong for the past regime (gold would be 'a')


def test_as_of_accuracy():
    assert as_of_accuracy("a", "a") == 1.0
    assert as_of_accuracy("b", "a") == 0.0
    assert as_of_accuracy(None, "a") == 0.0


def test_gate_verdicts_pass_when_gg_high_and_beats_floor_on_past():
    gg = {"past": 1.0, "current": 1.0}
    floor = {"past": 0.0, "current": 1.0}
    v = gate_verdicts(gg, floor)
    assert all(p for _l, p, _h in v)


def test_gate_verdicts_fail_when_floor_matches_gg_on_past():
    gg = {"past": 1.0, "current": 1.0}
    floor = {"past": 1.0, "current": 1.0}  # floor somehow right on past -> no capability gap
    v = gate_verdicts(gg, floor)
    gap = next(p for label, p, _h in v if "PAST" in label)
    assert gap is False


def test_render_has_regimes_and_verdicts():
    res = TemporalResult(gg_acc={"past": 1.0, "current": 1.0},
                         floor_acc={"past": 0.0, "current": 1.0}, llm_acc=None)
    md = render_temporal_md(res)
    assert "past" in md and "current" in md and ("PASS" in md or "FAIL" in md)
```

- [ ] **Step 2: Run -> fail.**

- [ ] **Step 3: Implement** (append to `temporal.py`):

```python
import re
from dataclasses import dataclass  # already imported; keep one


def _mentions(text: str, surface: str) -> bool:
    return re.search(r"\b" + re.escape(surface) + r"\b", text) is not None


def temporal_blind_floor(docs, anchor_surfaces: set, relation: str, D: int, *,
                         surface_to_canon: dict) -> str | None:
    """RAG-without-a-temporal-axis: among docs mentioning the anchor AND the relation
    phrase, take the LAST in doc order (corrections are appended after originals) and
    return its non-anchor object. Ignores D -> wrong on past-date queries. Deterministic."""
    rel_words = relation.replace("_", " ")
    hits = [d for d in docs
            if any(_mentions(d.text, a) for a in anchor_surfaces) and rel_words in d.text]
    if not hits:
        return None
    d = hits[-1]  # latest-mentioned (temporal-blind)
    for surf, canon in surface_to_canon.items():
        if canon not in {surface_to_canon.get(a) for a in anchor_surfaces} and _mentions(d.text, surf):
            return canon
    return None


def as_of_accuracy(predicted_obj, gold_obj) -> float:
    return 1.0 if predicted_obj == gold_obj else 0.0


@dataclass
class TemporalResult:
    gg_acc: dict        # regime -> mean goldengraph as_of-accuracy
    floor_acc: dict     # regime -> mean temporal-blind floor accuracy
    llm_acc: dict | None = None


def gate_verdicts(gg_acc: dict, floor_acc: dict, *, gg_threshold: float = 0.9,
                  past_gap_margin: float = 0.5) -> list[tuple[str, bool, bool]]:
    """[(label, passed, is_hard), ...]. Expected gg = 1.0 both regimes (right by
    construction); >=0.9 is slack. The capability is the PAST-regime gap (the floor
    returns the corrected value -> ~0 on past)."""
    both = all(gg_acc.get(r, 0.0) >= gg_threshold for r in ("past", "current"))
    past_gap = (gg_acc.get("past", 0.0) - floor_acc.get("past", 0.0)) >= past_gap_margin
    floor_current_ok = floor_acc.get("current", 0.0) >= 0.5
    return [
        (f"goldengraph as_of-accuracy >= {gg_threshold} in BOTH regimes (respects "
         "valid-time)", both, True),
        (f"goldengraph beats the temporal-blind floor by >= {past_gap_margin} on PAST "
         "queries (RAG can't answer 'as of a past date')", past_gap, True),
        ("floor is OK on the current regime (it's temporal-blind, not broken) (soft)",
         floor_current_ok, False),
    ]


def gate_exit_code(res: TemporalResult) -> int:
    return 1 if any(not p for _l, p, h in gate_verdicts(res.gg_acc, res.floor_acc) if h) else 0


def render_temporal_md(res: TemporalResult) -> str:
    lines = ["# GoldenGraph temporal as_of -- KG vs temporal-blind floor", "",
             "as_of-accuracy by regime (past = ask about a corrected-away value).", "",
             "| regime | goldengraph | floor |", "|---|---|---|"]
    for r in ("past", "current"):
        la = f" | {res.llm_acc[r]:.3f}" if res.llm_acc else ""
        lines.append(f"| {r} | {res.gg_acc.get(r, 0.0):.3f} | {res.floor_acc.get(r, 0.0):.3f}{la} |")
    if res.llm_acc:
        lines[lines.index("| regime | goldengraph | floor |")] = "| regime | goldengraph | floor | llm-rag |"
        lines[lines.index("|---|---|---|")] = "|---|---|---|---|"
    lines += ["", "## verdicts", ""]
    for label, passed, is_hard in gate_verdicts(res.gg_acc, res.floor_acc):
        tag = "PASS" if passed else ("FAIL" if is_hard else "WARN")
        lines.append(f"- [{tag}] {label}")
    return "\n".join(lines) + "\n"
```

> The render's llm-column header swap is fiddly — the implementer may instead build the header row conditionally up front. Keep the asserted strings (`past`, `current`, `PASS`/`FAIL`) intact.

- [ ] **Step 4: Run -> pass.**
- [ ] **Step 5: Commit** — `feat(er-kg-bench): temporal-blind floor + as_of metric + gate + render`.

---

## Task 3: valid_to round-trip + store build + goldengraph as_of (needs the wheel)

**Files:**
- Modify: `erkgbench/qa_e2e/temporal.py`
- Test: `tests/test_qa_temporal_store.py` (`importorskip`)

- [ ] **Step 1: Write the failing tests** (the valid_to round-trip is FIRST — it pins the load-bearing path)

```python
import json

import pytest

pytest.importorskip("goldengraph_native")


def test_valid_to_round_trips_through_pystore_append_and_as_of():
    from goldengraph_native import _native as ggn

    store = ggn.PyStore()
    # X(0) -rel-> A(1) valid [1,5); X(0) -rel-> B(2) valid [5,inf). ONE batch.
    batch = {
        "entities": [
            {"local_id": 0, "canonical_name": "X", "typ": "c", "surface_names": ["X"], "record_keys": ["x"]},
            {"local_id": 1, "canonical_name": "A", "typ": "c", "surface_names": ["A"], "record_keys": ["a"]},
            {"local_id": 2, "canonical_name": "B", "typ": "c", "surface_names": ["B"], "record_keys": ["b"]},
        ],
        "edges": [
            {"subj_local": 0, "predicate": "rel", "obj_local": 1, "valid_from": 1, "valid_to": 5, "source_refs": []},
            {"subj_local": 0, "predicate": "rel", "obj_local": 2, "valid_from": 5, "valid_to": None, "source_refs": []},
        ],
        "ingested_at": 1,
    }
    store.append(json.dumps(batch))
    BIG = 10**12
    past = store.as_of(3, BIG)
    cur = store.as_of(7, BIG)
    past_names = {e["canonical_name"] for e in past.entities()}
    cur_names = {e["canonical_name"] for e in cur.entities()}
    assert "A" in past_names and "B" not in past_names   # D=3 -> only A's window
    assert "B" in cur_names and "A" not in cur_names      # D=7 -> only B's window


def test_goldengraph_asof_returns_the_gold_object_in_both_regimes():
    from erkgbench.qa_e2e.temporal import (
        build_temporal_store, generate_temporal, goldengraph_asof,
    )

    docs, facts, qs = generate_temporal(seed=7, n_facts=20, ambiguity=0.6)
    store = build_temporal_store(facts)
    for q in qs:
        got = goldengraph_asof(store, q.anchor_id, q.relation, q.D)
        assert got == q.gold_obj  # exact, in both regimes
```

- [ ] **Step 2: Run -> fail/skip** (skips locally without the wheel; the round-trip test is the first thing the gate lane runs).

- [ ] **Step 3: Implement** (append to `temporal.py`):

```python
import json
from pathlib import Path  # if needed


_BIG_TX = 10**12


def build_temporal_store(facts):
    """Build a bi-temporal store from the facts: per fact ONE batch with X-rel-A
    [T1,tc) and X-rel-B [tc,inf). Oracle record_keys (= canonical id) so X merges
    across facts/relations. Hand-built JSON (build_batch can't set valid_to)."""
    from goldengraph_native import _native as ggn

    store = ggn.PyStore()
    for i, f in enumerate(facts):
        batch = {
            "entities": [
                {"local_id": 0, "canonical_name": f.anchor_id, "typ": "concept",
                 "surface_names": [f.anchor_id], "record_keys": [f.anchor_id]},
                {"local_id": 1, "canonical_name": f.a_id, "typ": "concept",
                 "surface_names": [f.a_id], "record_keys": [f.a_id]},
                {"local_id": 2, "canonical_name": f.b_id, "typ": "concept",
                 "surface_names": [f.b_id], "record_keys": [f.b_id]},
            ],
            "edges": [
                {"subj_local": 0, "predicate": f.relation, "obj_local": 1,
                 "valid_from": T1, "valid_to": f.tc, "source_refs": []},
                {"subj_local": 0, "predicate": f.relation, "obj_local": 2,
                 "valid_from": f.tc, "valid_to": None, "source_refs": []},
            ],
            "ingested_at": 1,
        }
        store.append(json.dumps(batch))
    return store


def goldengraph_asof(store, anchor_id: str, relation: str, D: int) -> str | None:
    """Exact as_of traversal: slice the store at valid_t=D, seed the anchor, 1-hop,
    filter edges by predicate -> the single object whose valid window contains D."""
    slice_g = store.as_of(D, _BIG_TX)
    # canonical-name IS the record_key/canonical id here (build_temporal_store sets
    # canonical_name = the id), so map view-entity-id -> canonical_name directly.
    id_to_canon = {e["entity_id"]: e["canonical_name"] for e in slice_g.entities()}
    seed = next((eid for eid, c in id_to_canon.items() if c == anchor_id), None)
    if seed is None:
        return None
    ball = slice_g.query([seed], 1)
    objs = {id_to_canon.get(e["obj"]) for e in ball.get("edges", ())
            if e["subj"] == seed and e["predicate"] == relation}
    objs.discard(None)
    objs.discard(anchor_id)
    return next(iter(objs)) if len(objs) == 1 else (next(iter(objs), None) if objs else None)
```

> Because `build_temporal_store` sets `canonical_name == the canonical id` and uses oracle record_keys, the slice's `canonical_name` IS the gold id — no `surface_to_canon` needed on the goldengraph side (the corpus uses canonical ids as names in the store). The DOC text (for the floor) still uses rendered surfaces; the two paths are independent.

- [ ] **Step 4: Run in the gate lane (Task 5) -> both pass.**
- [ ] **Step 5: Commit** — `feat(er-kg-bench): temporal store build + goldengraph as_of traversal`.

---

## Task 4: deterministic runner + CLI

**Files:**
- Modify: `erkgbench/qa_e2e/temporal.py` (`run_temporal_deterministic`)
- Create: `erkgbench/qa_e2e/run_temporal.py`
- Test: `tests/test_qa_temporal_cli.py` (argparse + `gate_exit_code` wheel-free)

- [ ] **Step 1:** `run_temporal_deterministic(*, seed, n_facts, ambiguity, llm=None) -> TemporalResult`: build the corpus + temporal store, for each question score `goldengraph_asof` vs `gold_obj` and `temporal_blind_floor` vs `gold_obj`, mean by regime. The floor's `surface_to_canon` = invert `dials.surface_to_canon(GoldGraph.from_corpus(... a QACorpus of the docs ...))` to `{surface: canonical_id}` (first-wins); `anchor_surfaces[anchor_id]` = the anchor's concept surfaces. If `llm` given, also score `llm_temporal_rag` (Task 6) into `llm_acc`. Needs the wheel (`build_temporal_store`).
- [ ] **Step 2:** `run_temporal.py` CLI: `--seed/--n-facts/--ambiguity/--out-md`, writes `TEMPORAL.md`, exits `gate_exit_code`; `--with-llm --budget-usd` builds `_BudgetedLLM(OpenAIClient...)` when `OPENAI_API_KEY` set.
- [ ] **Step 3:** wheel-free CLI test: `_parser().parse_args([])` defaults; `gate_exit_code` over a synthetic pass (gg 1.0/1.0, floor 0.0/1.0 -> 0) and fail (floor 1.0/1.0 -> 1).
- [ ] **Step 4:** Run -> pass.
- [ ] **Step 5: Commit** — `feat(er-kg-bench): temporal deterministic runner + CLI`.

---

## Task 5: key-free CI gate

**Files:**
- Modify: `.github/workflows/goldengraph-pipeline.yml` (a step after the B1 aggregation gate)
- Modify: `.github/workflows/bench-er-kg.yml` (add the wheel-free temporal test files)

- [ ] **Step 1:** In `goldengraph-pipeline.yml`, after the aggregation gate step, add a `temporal` gate step (same wheel): `python -m pytest tests/test_qa_temporal_store.py -v` (the valid_to round-trip + as_of e2e) then `python -m erkgbench.qa_e2e.run_temporal --seed 7 --n-facts 40 --ambiguity 0.6 --out-md TEMPORAL.md`; no key; upload `TEMPORAL.md`. Exit code gates.
- [ ] **Step 2:** In `bench-er-kg.yml` pure-Python step, append `tests/test_qa_temporal_corpus.py tests/test_qa_temporal_floor.py tests/test_qa_temporal_cli.py` (wheel-free; the store test `importorskip`s).
- [ ] **Step 3:** Validate both YAMLs parse.
- [ ] **Step 4: Commit** — `ci(goldengraph): key-free temporal as_of capability gate`.
- [ ] **Step 5:** Push, open PR, confirm the `pipeline` lane runs the temporal gate green (the real validator: the valid_to round-trip + as_of traversal + the 3 verdicts).

---

## Task 6: opt-in real-LLM RAG confirmation (non-gating)

**Files:**
- Modify: `erkgbench/qa_e2e/temporal.py` (`llm_temporal_rag`)
- Modify: `.github/workflows/bench-graphrag-qa.yml` (extend the opt-in lane, gated on a new input)
- Test: `tests/test_qa_temporal_llm.py` (stub-LLM, wheel-free)

- [ ] **Step 1:** `llm_temporal_rag(docs, anchor_surfaces, relation, D, llm, *, surface_to_canon) -> str|None`: pass BOTH dated passages (anchor+relation docs) + "As of {D}, what does X {relation}? Answer with one entity name." -> map the answer name to a canonical id. The LLM has the dated text but no enforced slice -> past-regime accuracy collapses. Stub-LLM test: returns a known name -> mapped to canonical; assert mapping + that both passages were in the prompt.
- [ ] **Step 2:** Wire a `run_temporal_llm` input into `bench-graphrag-qa.yml` (mirror B1's `run_aggregation_llm`): a step (gated on the input) runs `run_temporal --with-llm` -> `TEMPORAL_LLM.md`, NON-gating (`|| true`).
- [ ] **Step 3:** Run the stub test -> pass.
- [ ] **Step 4: Commit** — `feat(er-kg-bench): opt-in real-LLM RAG temporal confirmation`.

---

## Done criteria

- Wheel-free tests green (corpus, floor, metric, gate verdicts, render, CLI, LLM-stub).
- `pipeline` lane: the valid_to round-trip passes, `goldengraph_asof` returns the gold object in both regimes, and `TEMPORAL.md` shows goldengraph as_of-accuracy ~1.0 in both regimes with the floor ~0 on past (HARD PASS).
- No existing gate touched; the new gate is additive; the real-LLM RAG row is opt-in/non-gating.

## Not in scope (YAGNI)

Transaction-time audit (Model B), attribute history, multi-correction chains, the ER-dial tie-in, NL date parsing, new entity universe.
