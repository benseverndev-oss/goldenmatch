# GoldenGraph KG/RAG query-router (slice 1) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the query-routing kernel (`classify_query` -> `QueryProfile` -> `plan_query` -> `RetrievalPlan`), promote aggregation into a first-class LLM-free `ask` mode, and add `ask(mode="auto")` dispatch, gated by a free deterministic router-correctness gate.

**Architecture:** New `goldengraph/route.py` (pure-Python heuristic classifier + planner). New `aggregate_members` engine-native traversal in `goldengraph/answer.py` (seeds_by_name -> 1-hop -> predicate-filtered objects -> canonical NAMES) + a `mode="auto"` branch. A new `erkgbench/qa_e2e/router_eval.py` gate reuses the B1 aggregation corpus at `ambiguity=0.0`, comparing the routed aggregate set (name space) to name-projected gold.

**Tech Stack:** Python 3.12, pytest, ruff. Standalone `goldengraph` pkg (excluded from uv workspace) + er-kg-bench. `aggregate_members` + the routed gate need the `goldengraph_native` wheel (run in `goldengraph-pipeline.yml`); `route.py` + classifier-accuracy are wheel-free.

**Spec:** `docs/superpowers/specs/2026-06-27-goldengraph-query-router-design.md`

---

## Conventions for every task

- Worktree: `D:\show_case\gg-query-router`, branch `feat/goldengraph-query-router`.
- `PYEXE="D:/show_case/goldenmatch/.venv/Scripts/python.exe"`.
- Run `route.py` tests wheel-free locally: `cd D:/show_case/gg-query-router && POLARS_SKIP_CPU_CHECK=1 PYTHONPATH="packages/python/goldengraph" "$PYEXE" -m pytest packages/python/goldengraph/tests/test_route.py -v`.
- `aggregate_members` + auto-dispatch tests need the wheel -> they run only in `goldengraph-pipeline` CI (the local `.venv` has no `goldengraph_native`). Guard them with `pytest.importorskip("goldengraph_native")`.
- Ruff-clean per commit: `"$PYEXE" -m ruff check <files>`. Add a top-level import only in the task that first uses it.
- `RELATION_SCHEMA` (the relation vocabulary) lives in `erkgbench/qa_e2e/engineered.py`. `route.py` must NOT import the bench (goldengraph is standalone); the classifier takes an optional `predicates` hint instead (the gate passes `RELATION_SCHEMA`; `ask(mode="auto")` passes the graph's distinct predicates). This is an additive elaboration of the spec's bare `classify_query(query)` signature.
- Commit footer for every commit:
  ```
  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01Sc5aGSaVBTWBWpytNH99QE
  ```

## File structure

- Create `packages/python/goldengraph/goldengraph/route.py` -- the kernel (intent enum, QueryProfile, classify_query, RetrievalPlan, plan_query). Pure-Python.
- Modify `packages/python/goldengraph/goldengraph/answer.py` -- `aggregate_members`, `_format_aggregate`, `mode == "auto"` branch.
- Create `packages/python/goldengraph/tests/test_route.py` -- classifier + planner unit tests (pure-Python).
- Create `packages/python/goldengraph/tests/test_aggregate_mode.py` -- `aggregate_members` + auto-dispatch (wheel; importorskip).
- Create `packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/qa_e2e/router_eval.py` -- the gate (classifier accuracy + routed correctness at ambiguity=0.0).
- Create `.../qa_e2e/run_router_eval.py` -- CLI (deterministic + `--with-llm`).
- Create `.../tests/test_qa_router.py` -- wheel-free classifier-accuracy + gate shape.
- Modify `.github/workflows/goldengraph-pipeline.yml` -- router gate step + upload.
- Modify `.github/workflows/bench-er-kg.yml` -- wheel-free router test on the pure-Python list.
- Modify `.github/workflows/bench-graphrag-qa.yml` -- `run_router_capability` opt-in step.

---

## Task 1: `route.py` intent classification (wheel-free)

**Files:**
- Create: `packages/python/goldengraph/goldengraph/route.py`
- Test: `packages/python/goldengraph/tests/test_route.py`

- [ ] **Step 1: Write the failing test**

```python
# packages/python/goldengraph/tests/test_route.py
"""Query-router kernel -- pure-Python unit tests (no wheel)."""
from __future__ import annotations

from goldengraph import route


def test_classify_aggregate_intent():
    p = route.classify_query("List all entities that Metaphone works with.")
    assert p.intent is route.QueryIntent.AGGREGATE


def test_classify_count_is_aggregate():
    p = route.classify_query("How many entities does Metaphone cite?")
    assert p.intent is route.QueryIntent.AGGREGATE


def test_classify_temporal_intent():
    p = route.classify_query("Who did X work for as of 2019?")
    assert p.intent is route.QueryIntent.TEMPORAL_ASOF


def test_classify_default_multihop():
    p = route.classify_query("How is Metaphone related to Levenshtein distance?")
    assert p.intent is route.QueryIntent.MULTI_HOP
```

- [ ] **Step 2: Run to verify it fails**

Run: `POLARS_SKIP_CPU_CHECK=1 PYTHONPATH="packages/python/goldengraph" "$PYEXE" -m pytest packages/python/goldengraph/tests/test_route.py -v`
Expected: FAIL (`ModuleNotFoundError: goldengraph.route`).

- [ ] **Step 3: Write minimal implementation**

```python
# packages/python/goldengraph/goldengraph/route.py
"""KG/RAG query-routing kernel (slice 1). Heuristic classify_query -> QueryProfile and a
plan_query rule table -> RetrievalPlan. Pure-Python (no wheel). Mirrors the ER auto-config
controller's HeuristicRefitPolicy; an LLM-assisted classifier tier is a slice-3 seam.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

_AGG_RE = re.compile(r"\b(list all|how many|which entities|all entities)\b", re.IGNORECASE)
_TEMPORAL_RE = re.compile(r"\b(as of|at the time|in \d{4}|before \d|after \d)\b", re.IGNORECASE)
_LOOKUP_RE = re.compile(r"^\s*(what is|who is|where is)\b", re.IGNORECASE)


class QueryIntent(str, Enum):
    AGGREGATE = "aggregate"
    TEMPORAL_ASOF = "temporal_asof"
    MULTI_HOP = "multi_hop"
    LOOKUP = "lookup"


@dataclass
class QueryProfile:
    intent: QueryIntent
    anchor_surface: str | None = None
    relation: str | None = None
    as_of: str | None = None
    confidence: float = 0.0


def _detect_intent(query: str) -> QueryIntent:
    # temporal takes precedence over aggregate (a dated set-query is still as-of-flavored)
    if _TEMPORAL_RE.search(query):
        return QueryIntent.TEMPORAL_ASOF
    if _AGG_RE.search(query):
        return QueryIntent.AGGREGATE
    if _LOOKUP_RE.search(query):
        return QueryIntent.LOOKUP
    return QueryIntent.MULTI_HOP


def classify_query(query: str, *, predicates=None) -> QueryProfile:
    """Heuristic intent + (for AGGREGATE) anchor/relation slots. `predicates` is an optional
    set of stored predicate ids (underscored) used to split '<anchor> <relation words>'; when
    absent the relation slot stays None and confidence drops (routes to the safe fallback)."""
    intent = _detect_intent(query)
    return QueryProfile(intent=intent, confidence=0.5 if intent is not QueryIntent.MULTI_HOP else 0.3)
```

- [ ] **Step 4: Run to verify it passes**

Run: `POLARS_SKIP_CPU_CHECK=1 PYTHONPATH="packages/python/goldengraph" "$PYEXE" -m pytest packages/python/goldengraph/tests/test_route.py -v`
Expected: PASS (4). `ruff check packages/python/goldengraph/goldengraph/route.py` -> clean (note: `field` import is unused until Task 3; if ruff flags it, drop it now and re-add in Task 3).

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldengraph/goldengraph/route.py packages/python/goldengraph/tests/test_route.py
git commit -m "feat(goldengraph): query-router intent classification"
```

---

## Task 2: anchor/relation slot extraction (wheel-free)

**Files:**
- Modify: `packages/python/goldengraph/goldengraph/route.py`
- Test: `packages/python/goldengraph/tests/test_route.py`

The B1 questions are `f"List all entities that {anchor} {rel_words}."` and
`f"How many entities does {anchor} {rel_words}?"` where `rel_words = relation.replace("_", " ")`.
Given the predicate vocabulary, split anchor from relation by matching a predicate's words as the
suffix of the span after the lead-in.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_route.py

_PREDS = {"works_with", "cites", "depends_on", "succeeded_by", "located_in"}


def test_slots_extracted_with_predicates():
    p = route.classify_query("List all entities that Metaphone works with.", predicates=_PREDS)
    assert p.anchor_surface == "Metaphone"
    assert p.relation == "works_with"
    assert p.confidence >= 0.8


def test_slots_multiword_anchor():
    p = route.classify_query(
        "How many entities does Levenshtein distance cites?", predicates=_PREDS
    )
    assert p.anchor_surface == "Levenshtein distance"
    assert p.relation == "cites"


def test_slots_without_predicates_low_confidence():
    p = route.classify_query("List all entities that Metaphone works with.")
    assert p.relation is None
    assert p.confidence < 0.8
```

- [ ] **Step 2: Run to verify it fails**

Run: `POLARS_SKIP_CPU_CHECK=1 PYTHONPATH="packages/python/goldengraph" "$PYEXE" -m pytest packages/python/goldengraph/tests/test_route.py -k slots -v`
Expected: FAIL (anchor_surface None).

- [ ] **Step 3: Write minimal implementation**

```python
# in route.py: add the lead-in stripper + slot extractor, call it from classify_query for AGGREGATE

_LEADIN_RE = re.compile(
    r"^\s*(?:list all entities that|all entities that|how many entities does|"
    r"which entities)\s+(?P<rest>.+?)\s*[.?]?\s*$",
    re.IGNORECASE,
)


def _extract_agg_slots(query: str, predicates) -> tuple[str | None, str | None]:
    m = _LEADIN_RE.match(query)
    if not m:
        return None, None
    rest = m.group("rest").strip()
    if not predicates:
        return rest, None  # can't split anchor from relation without the vocab
    # longest predicate-phrase that is a suffix of `rest` -> that's the relation; prefix is anchor.
    best = None
    for pred in predicates:
        phrase = pred.replace("_", " ")
        if rest.lower().endswith(phrase.lower()):
            if best is None or len(phrase) > len(best[1]):
                best = (pred, phrase)
    if best is None:
        return rest, None
    pred, phrase = best
    anchor = rest[: len(rest) - len(phrase)].strip()
    return (anchor or None), pred


def classify_query(query: str, *, predicates=None) -> QueryProfile:
    intent = _detect_intent(query)
    if intent is QueryIntent.AGGREGATE:
        anchor, relation = _extract_agg_slots(query, predicates)
        conf = 0.9 if (anchor and relation) else 0.5
        return QueryProfile(intent=intent, anchor_surface=anchor, relation=relation, confidence=conf)
    conf = 0.5 if intent is not QueryIntent.MULTI_HOP else 0.3
    return QueryProfile(intent=intent, confidence=conf)
```

- [ ] **Step 4: Run to verify it passes**

Run: `... -m pytest packages/python/goldengraph/tests/test_route.py -v`
Expected: PASS (all). `ruff check` -> clean.

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldengraph/goldengraph/route.py packages/python/goldengraph/tests/test_route.py
git commit -m "feat(goldengraph): query-router anchor/relation slot extraction"
```

---

## Task 3: `RetrievalPlan` + `plan_query` rules + confidence floor (wheel-free)

**Files:**
- Modify: `packages/python/goldengraph/goldengraph/route.py`
- Test: `packages/python/goldengraph/tests/test_route.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_route.py

def test_plan_aggregate_routes_to_aggregate():
    p = route.classify_query("List all entities that Metaphone works with.", predicates=_PREDS)
    plan = route.plan_query(p)
    assert plan.mode == "aggregate"


def test_plan_low_confidence_aggregate_falls_back():
    # no predicates -> relation None -> conf 0.5 < MIN_CONF -> safe general mode, not aggregate
    p = route.classify_query("List all entities that Metaphone works with.")
    plan = route.plan_query(p)
    assert plan.mode in ("local", "hybrid")


def test_plan_temporal_marked_not_yet_promoted():
    p = route.classify_query("Who did X work for as of 2019?")
    plan = route.plan_query(p)
    assert plan.mode == "local" and plan.note == "not_yet_promoted"


def test_plan_multihop_routes_hybrid():
    p = route.classify_query("How is A related to B?")
    assert route.plan_query(p).mode == "hybrid"
```

- [ ] **Step 2: Run to verify it fails**

Run: `... -m pytest packages/python/goldengraph/tests/test_route.py -k plan -v`
Expected: FAIL (`AttributeError: plan_query`).

- [ ] **Step 3: Write minimal implementation**

```python
# add to route.py

MIN_CONF = 0.8  # below this, a specialized intent routes to the safe general mode


@dataclass
class RetrievalPlan:
    mode: str
    note: str | None = None
    params: dict = field(default_factory=dict)


def plan_query(profile: QueryProfile) -> RetrievalPlan:
    if profile.intent is QueryIntent.AGGREGATE and profile.confidence >= MIN_CONF \
            and profile.anchor_surface and profile.relation:
        return RetrievalPlan(mode="aggregate")
    if profile.intent is QueryIntent.TEMPORAL_ASOF:
        # mode lands in slice 2; route to general for now but mark it honestly
        return RetrievalPlan(mode="local", note="not_yet_promoted")
    if profile.intent is QueryIntent.MULTI_HOP:
        return RetrievalPlan(mode="hybrid")
    return RetrievalPlan(mode="local")  # LOOKUP + low-confidence fallbacks
```

- [ ] **Step 4: Run to verify it passes**

Run: `... -m pytest packages/python/goldengraph/tests/test_route.py -v`
Expected: PASS (all). `ruff check` -> clean (`field` now used).

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldengraph/goldengraph/route.py packages/python/goldengraph/tests/test_route.py
git commit -m "feat(goldengraph): query-router RetrievalPlan + plan rules + confidence floor"
```

---

## Task 4: `aggregate_members` + `ask(mode="auto")` (wheel-bound)

**Files:**
- Modify: `packages/python/goldengraph/goldengraph/answer.py`
- Test: `packages/python/goldengraph/tests/test_aggregate_mode.py`

First READ `answer.py` (the `ask` body: `slice_graph = store.as_of(valid_t, tx_t)`, the
`mode == "global"` / `("local","hybrid")` branches) and the smoke test
(`er-kg-bench tests/test_qa_goldengraph_smoke.py` or goldengraph `tests/test_retrieval.py`) for how
a real `PyStore` is built in a test. The test builds a tiny store with one anchor + a few objects on
one predicate, then asserts `aggregate_members` returns the object names and `ask(mode="auto")` on a
"List all entities that <anchor> <rel>" question routes there.

- [ ] **Step 1: Write the failing test**

```python
# packages/python/goldengraph/tests/test_aggregate_mode.py
"""aggregate_members + ask(mode='auto') -- needs goldengraph_native (CI lane)."""
from __future__ import annotations

import pytest

pytest.importorskip("goldengraph_native")

# Build a minimal store via the same primitives the smoke test uses (Extraction/ResolvedEntity/
# build_batch/PyStore.append), seed "Apple" -> works_with -> {"Banana","Cherry"}. See the smoke
# test for the exact construction; keep it 1 anchor + 2 objects on predicate "works_with".
# (Construction omitted here for brevity -- mirror tests/test_qa_goldengraph_smoke.py.)


def _build_store():
    ...  # build per the smoke-test pattern; return a PyStore with the edges above


def test_aggregate_members_returns_object_names():
    from goldengraph.answer import aggregate_members

    store = _build_store()
    slice_graph = store.as_of(_BIG, _BIG)  # latest slice; _BIG per the smoke test
    got = aggregate_members(slice_graph, "Apple", "works_with")
    assert got == {"Banana", "Cherry"}


def test_ask_auto_routes_aggregation(monkeypatch):
    from goldengraph import answer

    store = _build_store()
    out = answer.ask(
        "List all entities that Apple works with.", store,
        llm=_StubLLM(), embedder=_StubEmbedder(), valid_t=_BIG, tx_t=_BIG, mode="auto",
    )
    # auto -> aggregate -> formatted set (no LLM call); both objects present
    assert "Banana" in out and "Cherry" in out
```

NOTE: pass the graph's predicates into `classify_query` from inside the auto branch (Step 3) so the
"works_with" relation resolves; in the test the stub LLM/embedder must never be called on the
aggregate path (assert that if you want to be strict).

- [ ] **Step 2: Run to verify it fails**

Run (in CI, or locally if the wheel is present): `... -m pytest packages/python/goldengraph/tests/test_aggregate_mode.py -v`
Expected: FAIL (`ImportError: cannot import name 'aggregate_members'`), or SKIP locally (no wheel).

- [ ] **Step 3: Write minimal implementation**

```python
# add to answer.py (top-level), importing the kernel
from .route import classify_query, plan_query


def aggregate_members(slice_graph, anchor_surface: str, relation: str) -> set[str]:
    """Engine-native exact aggregation: seed the anchor by name, 1-hop ball, return the canonical
    NAMES of objects on edges (subj in seeds, predicate==relation). LLM-free."""
    seeds = slice_graph.seeds_by_name(anchor_surface)
    if not seeds:
        return set()
    sub = slice_graph.query(seeds, 1)
    id_to_name = {e["entity_id"]: e["canonical_name"] for e in sub.get("entities", ())}
    seedset = set(seeds)
    return {
        id_to_name[e["obj"]]
        for e in sub.get("edges", ())
        if e["subj"] in seedset and e["predicate"] == relation and e["obj"] in id_to_name
    }


def _format_aggregate(members: set[str]) -> str:
    return ", ".join(sorted(members)) if members else "(none found)"


def _slice_predicates(slice_graph) -> set[str]:
    # distinct predicates available for slot disambiguation
    preds: set = set()
    for e in slice_graph.query([e2["entity_id"] for e2 in slice_graph.entities()], 1).get("edges", ()):
        preds.add(e["predicate"])
    return preds
```

Then in `ask`, AFTER the existing `slice_graph = store.as_of(valid_t, tx_t)` line, add:

```python
    if mode == "auto":
        profile = classify_query(query, predicates=_slice_predicates(slice_graph))
        plan = plan_query(profile)
        if plan.mode == "aggregate" and profile.anchor_surface and profile.relation:
            return _format_aggregate(aggregate_members(slice_graph, profile.anchor_surface, profile.relation))
        mode = plan.mode  # fall through to the existing local/hybrid/global handling
```

NOTE: `_slice_predicates` enumerates all entities to collect predicates -- fine at bench scale. If
`slice_graph.entities()` + a full `query` is too heavy later, cache it; not a slice-1 concern.
Confirm the `mode == "global"` branch still precedes this OR that `auto` is handled before the
`global` early-return (place the `auto` block right after the `as_of` line, before the `global`
check, so `auto` can itself resolve to any mode including global via `mode = plan.mode`).

- [ ] **Step 4: Run to verify it passes** (CI lane; locally skips without the wheel)

Run: `... -m pytest packages/python/goldengraph/tests/test_aggregate_mode.py -v`
Expected: PASS in CI. `ruff check packages/python/goldengraph/goldengraph/answer.py` -> clean.

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldengraph/goldengraph/answer.py packages/python/goldengraph/tests/test_aggregate_mode.py
git commit -m "feat(goldengraph): aggregate_members + ask(mode=auto) dispatch"
```

---

## Task 5: router gate (er-kg-bench) + CLI + CI wiring

**Files:**
- Create: `erkgbench/qa_e2e/router_eval.py`, `erkgbench/qa_e2e/run_router_eval.py`
- Create: `erkgbench/qa_e2e/../tests/test_qa_router.py`
- Modify: `.github/workflows/goldengraph-pipeline.yml`, `.github/workflows/bench-er-kg.yml`

`router_eval.py` reuses the B1 corpus. Classifier accuracy is wheel-free; routed correctness needs
the wheel (builds the oracle store at ambiguity=0.0 via `ablation._build_store` and calls the engine
`aggregate_members`).

- [ ] **Step 1: Write the failing wheel-free test**

```python
# tests/test_qa_router.py
"""Router gate -- wheel-free classifier accuracy + gate shape."""
from __future__ import annotations

from erkgbench.qa_e2e import router_eval as re_


def test_classifier_accuracy_on_b1_questions():
    acc = re_.classifier_accuracy(seed=7, n_anchors=20, ambiguity=0.0)
    # every "List all ..."/"How many ..." question must classify AGGREGATE with correct slots
    assert acc["aggregate_recall"] == 1.0
    assert acc["slot_accuracy"] == 1.0


def test_gate_shape_passes_on_good_result():
    res = re_.RouterResult(aggregate_recall=1.0, slot_accuracy=1.0, routed_setf1=1.0)
    assert re_.gate_exit_code(res) == 0


def test_gate_fails_when_routed_setf1_low():
    res = re_.RouterResult(aggregate_recall=1.0, slot_accuracy=1.0, routed_setf1=0.5)
    assert re_.gate_exit_code(res) == 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd packages/python/goldenmatch/benchmarks/er-kg-bench && POLARS_SKIP_CPU_CHECK=1 PYTHONPATH="$(pwd):D:/show_case/gg-query-router/packages/python/goldengraph" "$PYEXE" -m pytest tests/test_qa_router.py -v`
Expected: FAIL (`ModuleNotFoundError: router_eval`).

- [ ] **Step 3: Write minimal implementation**

```python
# erkgbench/qa_e2e/router_eval.py
"""Slice-1 router gate over the B1 aggregation corpus. classifier_accuracy is wheel-free;
run_routed_correctness needs the goldengraph_native wheel (builds the oracle store at
ambiguity=0.0 and calls the engine aggregate_members). Compares in NAME space vs name-projected
gold (see the design's 'Why ambiguity=0.0')."""
from __future__ import annotations

from dataclasses import dataclass

from goldengraph.route import QueryIntent, classify_query

from .aggregation import generate_aggregation
from .engineered import RELATION_SCHEMA, _load_entities


def classifier_accuracy(*, seed: int, n_anchors: int, ambiguity: float) -> dict:
    _docs, qs = generate_aggregation(seed=seed, n_anchors=n_anchors, ambiguity=ambiguity)
    by_id = {e.id: e for e in _load_entities()}
    preds = set(RELATION_SCHEMA)
    list_qs = [q for q in qs if q.kind == "list"]
    agg_hits = slot_hits = 0
    for q in list_qs:
        p = classify_query(q.question, predicates=preds)
        if p.intent is QueryIntent.AGGREGATE:
            agg_hits += 1
        if p.anchor_surface == by_id[q.anchor_id].canonical and p.relation == q.relation:
            slot_hits += 1
    n = len(list_qs) or 1
    return {"aggregate_recall": agg_hits / n, "slot_accuracy": slot_hits / n}


@dataclass
class RouterResult:
    aggregate_recall: float
    slot_accuracy: float
    routed_setf1: float


# frozen from the first measured run (verify-then-freeze)
AGG_RECALL_MIN = 0.99
SLOT_ACC_MIN = 0.99
ROUTED_SETF1_MIN = 0.99


def evaluate_assertions(res: RouterResult):
    return [
        (f"classifier routes list-questions to AGGREGATE (recall {res.aggregate_recall:.3f} >= {AGG_RECALL_MIN})", res.aggregate_recall >= AGG_RECALL_MIN, True),
        (f"anchor/relation slots correct (acc {res.slot_accuracy:.3f} >= {SLOT_ACC_MIN})", res.slot_accuracy >= SLOT_ACC_MIN, True),
        (f"routed aggregate set-F1 == 1.0 at ambiguity=0.0 (got {res.routed_setf1:.3f} >= {ROUTED_SETF1_MIN})", res.routed_setf1 >= ROUTED_SETF1_MIN, True),
    ]


def gate_exit_code(res: RouterResult) -> int:
    return 1 if any(h and not ok for _l, ok, h in evaluate_assertions(res)) else 0


def run_routed_correctness(*, seed: int, n_anchors: int) -> float:
    """Build the B1 oracle store at ambiguity=0.0, route each list-question through
    classify_query -> aggregate_members, score set-F1 vs NAME-PROJECTED gold. Needs the wheel."""
    from goldengraph.answer import aggregate_members

    from . import ablation, dials
    from .aggregation import agg_documents_corpus, set_f1
    from .gold import GoldGraph

    docs, qs = generate_aggregation(seed=seed, n_anchors=n_anchors, ambiguity=0.0)
    corpus = agg_documents_corpus(docs)
    g = GoldGraph.from_corpus(corpus)
    slice_graph, _cov = ablation._build_store(corpus, g, dials.oracle_keys(corpus, g), ablation._typ_of(g))
    by_id = {e.id: e for e in _load_entities()}
    preds = set(RELATION_SCHEMA)
    vals = []
    for q in (q for q in qs if q.kind == "list"):
        p = classify_query(q.question, predicates=preds)
        got = aggregate_members(slice_graph, p.anchor_surface, p.relation) if (p.anchor_surface and p.relation) else set()
        gold_names = {by_id[m].canonical for m in q.gold_members}
        vals.append(set_f1(got, gold_names)["f1"])
    return (sum(vals) / len(vals)) if vals else 0.0


def run_router_deterministic(*, seed: int, n_anchors: int) -> RouterResult:
    acc = classifier_accuracy(seed=seed, n_anchors=n_anchors, ambiguity=0.0)
    routed = run_routed_correctness(seed=seed, n_anchors=n_anchors)
    return RouterResult(aggregate_recall=acc["aggregate_recall"], slot_accuracy=acc["slot_accuracy"], routed_setf1=routed)


def render_router_md(res: RouterResult) -> str:
    lines = [
        "# GoldenGraph query-router gate (slice 1, no LLM)",
        "",
        "Heuristic classify_query routes B1 list-questions to the aggregate lever; the engine-native",
        "aggregate_members traversal returns the exact member set (name space, ambiguity=0.0).",
        "",
        f"- aggregate_recall: {res.aggregate_recall:.3f}",
        f"- slot_accuracy:    {res.slot_accuracy:.3f}",
        f"- routed_setF1:     {res.routed_setf1:.3f}",
        "",
        "## verdicts",
        "",
    ]
    for label, ok, _h in evaluate_assertions(res):
        lines.append(f"- [{'PASS' if ok else 'FAIL'}] {label}")
    return "\n".join(lines) + "\n"
```

NOTE: confirm `RELATION_SCHEMA` and `_load_entities` are importable from `engineered.py` (grep
showed both there). If `RELATION_SCHEMA` lives in a different module, fix the import.

- [ ] **Step 4: CLI**

```python
# erkgbench/qa_e2e/run_router_eval.py  (mirror run_aggregation.py)
from __future__ import annotations

import argparse
import os
import sys

from .router_eval import gate_exit_code, render_router_md, run_router_deterministic


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="GoldenGraph query-router gate")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--n-anchors", type=int, default=60)
    ap.add_argument("--out-md", default="ROUTER.md")
    ap.add_argument("--with-llm", action="store_true")
    ap.add_argument("--budget-usd", type=float, default=3.0)
    ap.add_argument("--llm-out-md", default="ROUTER_LLM.md")
    args = ap.parse_args(argv)
    res = run_router_deterministic(seed=args.seed, n_anchors=args.n_anchors)
    md = render_router_md(res)
    with open(args.out_md, "w", encoding="utf-8") as fh:
        fh.write(md)
    sys.stdout.write(md)
    if args.with_llm and os.environ.get("OPENAI_API_KEY"):
        from .router_eval import render_router_llm_md, run_router_llm  # Task 6
        from goldenmatch.config.schemas import BudgetConfig
        from goldenmatch.core.llm_budget import BudgetTracker
        tr = BudgetTracker(BudgetConfig(max_cost_usd=args.budget_usd))
        lm = render_router_llm_md(run_router_llm(seed=args.seed, n_anchors=args.n_anchors, tracker=tr))
        with open(args.llm_out_md, "w", encoding="utf-8") as fh:
            fh.write(lm)
        sys.stdout.write(lm)
    return gate_exit_code(res)


if __name__ == "__main__":
    raise SystemExit(main())
```

(The `--with-llm` imports are inside the branch -> Task 5 commit stays ruff-clean without Task 6.)

- [ ] **Step 5: Run the wheel-free test + ruff**

Run: `... -m pytest tests/test_qa_router.py -v` (from er-kg-bench dir, PYTHONPATH incl. the goldengraph pkg)
Expected: PASS. `ruff check erkgbench/qa_e2e/router_eval.py erkgbench/qa_e2e/run_router_eval.py` -> clean.

- [ ] **Step 6: Wire the pipeline gate + bench-er-kg list + commit**

In `goldengraph-pipeline.yml`, after the "Upload KG_SCORECARD.md" step (the last `pipeline` step), add a
router gate step (the goldengraph pkg is already `pip install -e`'d at line 45, so `goldengraph.route`
+ `aggregate_members` import):

```yaml
      - name: Query-router gate (deterministic, key-free)
        # Slice 1 of the KG/RAG router: classify_query routes B1 list-questions to the aggregate
        # lever; the engine-native aggregate_members returns the exact set (name space, amb=0.0).
        # Gates HARD on classifier recall + slot accuracy + routed set-F1 == 1.0. No key.
        working-directory: packages/python/goldenmatch/benchmarks/er-kg-bench
        env:
          POLARS_SKIP_CPU_CHECK: "1"
        run: |
          python -m pytest tests/test_qa_router.py -v
          python -m erkgbench.qa_e2e.run_router_eval --seed 7 --n-anchors 60 --out-md ROUTER.md
      - name: Upload ROUTER.md
        if: ${{ always() }}
        uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a  # v4
        with:
          name: goldengraph-router
          path: packages/python/goldenmatch/benchmarks/er-kg-bench/ROUTER.md
          if-no-files-found: ignore
```

Also add `packages/python/goldengraph/tests/test_route.py` to `goldengraph-pipeline.yml`? NO -- it's
already covered by `pytest packages/python/goldengraph/tests` (line 46). Add `tests/test_qa_router.py`
to the wheel-free pure-Python list in `bench-er-kg.yml`.

```bash
git add erkgbench/qa_e2e/router_eval.py erkgbench/qa_e2e/run_router_eval.py tests/test_qa_router.py \
  ../../../../.github/workflows/goldengraph-pipeline.yml ../../../../.github/workflows/bench-er-kg.yml
git commit -m "feat(er-kg-bench): query-router deterministic gate + CI wiring"
```

(Use repo-root-relative paths for the workflow files; `git add` from the repo root is simplest.)

---

## Task 6: opt-in real-LLM auto-vs-local row + bench-graphrag-qa wiring

**Files:**
- Modify: `erkgbench/qa_e2e/router_eval.py`
- Modify: `.github/workflows/bench-graphrag-qa.yml`
- Test: `tests/test_qa_router.py`

The opt-in row builds the store (ambiguity 0.6, realistic), and for each list-question compares
answer-match of `ask(mode="auto")` (routes to aggregate) vs `ask(mode="local")`. Reuses
`run_qa_e2e._build_engine("goldengraph")` for a configured engine OR calls `goldengraph.answer.ask`
directly with a real OpenAIClient + embedder. Per-question parse answer text -> set via
`set_f1` against name-gold. NOT unit-tested beyond the pure scoring helper.

- [ ] **Step 1: Write the failing test (pure helper)**

```python
# add to tests/test_qa_router.py
def test_router_answer_setf1_pure():
    from erkgbench.qa_e2e import router_eval as re2
    # parse "Banana, Cherry" -> {Banana,Cherry}; gold {Banana,Cherry} -> 1.0
    assert re2.answer_setf1("Banana, Cherry.", {"Banana", "Cherry"}, {"Banana", "Cherry", "Date"}) == 1.0
```

- [ ] **Step 2-4:** implement `answer_setf1(answer_text, gold_names, universe) -> float` (scan the
  answer for any universe name, set-F1 vs gold_names; reuse `aggregation.set_f1`), plus
  `run_router_llm(*, seed, n_anchors, tracker) -> RouterLLMResult` (auto vs local mean answer-set-F1,
  per-question budget short-circuit) and `render_router_llm_md`. Mirror slice-D's `framework_*` +
  `kg_scorecard.parse_entity_set` shape. Run the pure test (PASS), ruff clean, commit.

- [ ] **Step 5: Wire bench-graphrag-qa.yml** (mirror the `run_kg_capability` step from slice D):
  add input `run_router_capability` (untyped string `default: "false"`), append
  `|| inputs.run_router_capability == 'true'` to the `scorecard` job `if:`, add a guarded step that
  runs `run_router_eval --with-llm` + uploads `ROUTER_LLM.md`, `|| true`, secret
  `GOLDENGRAPH_OPENAI_API_KEY`. Commit.

---

## Final verification (before finishing the branch)

- [ ] `POLARS_SKIP_CPU_CHECK=1 PYTHONPATH="packages/python/goldengraph" "$PYEXE" -m pytest packages/python/goldengraph/tests/test_route.py -v` -> PASS.
- [ ] er-kg-bench `tests/test_qa_router.py` -> PASS (wheel-free part).
- [ ] `ruff check` on all created/modified .py -> clean.
- [ ] `python -c "import yaml; [yaml.safe_load(open(f)) for f in (...3 workflows...)]"` -> ok.
- [ ] Use superpowers:finishing-a-development-branch: push (benzsevern account), PR vs `main`, watch `goldengraph-pipeline` (runs both the goldengraph pkg tests AND the router gate) green BEFORE arming `gh pr merge <N> --auto`; freeze `AGG_RECALL_MIN`/`SLOT_ACC_MIN`/`ROUTED_SETF1_MIN` from the measured `ROUTER.md` if the placeholders need adjustment; record memory.
- [ ] If routed set-F1 is NOT 1.0 at ambiguity=0.0, surface to Ben (the engine-native aggregate traversal is wrong) -- do not loosen the gate.

## Known unknowns to resolve during implementation (call out, don't guess)

- Exact `RELATION_SCHEMA` import path + value (grep showed it referenced in aggregation/engineered;
  confirm the module + that the values are underscored predicate ids matching the stored edge predicates).
- The smoke-test store-construction pattern for `test_aggregate_mode.py` (Extraction/ResolvedEntity/
  build_batch/PyStore.append + the `_BIG` as_of constant) -- READ `tests/test_qa_goldengraph_smoke.py`
  / `ablation._build_store` and mirror it.
- Whether the `auto` block must precede the `mode == "global"` early-return in `ask` (place it right
  after the `as_of` line so `auto` can resolve to any downstream mode).
- `_slice_predicates` cost at gate scale (enumerate-entities + 1-hop) -- fine for n_anchors=60; note
  if it needs narrowing.
