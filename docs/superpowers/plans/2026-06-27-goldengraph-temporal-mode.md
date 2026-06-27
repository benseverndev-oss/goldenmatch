# GoldenGraph query-router slice 2 (temporal as-of mode) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Promote temporal as-of into a first-class LLM-free `ask` mode, flip the router's `temporal_asof` plan to a real `as_of` lever (date = slice time), and extend the slice-1 router gate to prove routed as-of-accuracy == 1.0 in both regimes.

**Architecture:** Extend `goldengraph/route.py` (temporal slot extraction + `as_of` plan rule), add `asof_object` + an `as_of` branch to `ask(mode="auto")` in `goldengraph/answer.py` (parses the query's integer date and slices `store.as_of(D)`, overriding the caller's valid_t; a CLAMP routes any non-returning specialized plan to a valid mode). The gate (`erkgbench/qa_e2e/router_eval.py`) builds a concept-surface-named windowed store (B2's is QID-named) and adds temporal classifier + routed-accuracy assertions.

**Tech Stack:** Python 3.12, pytest, ruff. STACKED on slice 1 (`feat/goldengraph-query-router`, PR #1283). `asof_object` + routed gate need the `goldengraph_native` wheel (goldengraph-pipeline CI); `route.py` + temporal classifier-accuracy are wheel-free.

**Spec:** `docs/superpowers/specs/2026-06-27-goldengraph-temporal-mode-design.md`

---

## Conventions for every task

- Worktree: `D:\show_case\gg-temporal-mode`, branch `feat/goldengraph-temporal-mode` (stacked on slice 1).
- `PYEXE="D:/show_case/goldenmatch/.venv/Scripts/python.exe"`.
- `route.py` tests wheel-free: `cd D:/show_case/gg-temporal-mode && POLARS_SKIP_CPU_CHECK=1 PYTHONPATH="packages/python/goldengraph" "$PYEXE" -m pytest packages/python/goldengraph/tests/test_route.py -v`.
- er-kg-bench wheel-free tests: from the bench dir, `PYTHONPATH="$(pwd);D:/show_case/gg-temporal-mode/packages/python/goldengraph"` (use `;` separator on Windows).
- `asof_object` + routed-correctness need the wheel -> CI only; guard wheel tests with `pytest.importorskip("goldengraph_native")`.
- Ruff-clean per commit. Commit footer:
  ```
  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01Sc5aGSaVBTWBWpytNH99QE
  ```
- Reuse from B2 `erkgbench/qa_e2e/temporal.py` (verified): `generate_temporal(*, seed, n_facts, ambiguity) -> (docs, facts, qs)`; `TemporalFact(anchor_id, relation, a_id, b_id, tc)` (frozen); `TemporalQuestion(id, question, anchor_id, relation, D, regime, gold_obj)`; question text `f"As of {D}, what does {by_id[src_id].canonical} {rel_words}?"`; `T1 = 1`; `goldengraph_asof(store, anchor_id, relation, D)`; `as_of_accuracy(pred, gold)`. `engineered.RELATION_SCHEMA`, `engineered._load_entities()` (`.id` QID, `.canonical` surface).

## File structure

- Modify `packages/python/goldengraph/goldengraph/route.py` -- temporal slot extraction + `as_of` plan.
- Modify `packages/python/goldengraph/goldengraph/answer.py` -- `asof_object`, `as_of` branch + clamp.
- Modify `packages/python/goldengraph/tests/test_route.py` -- temporal intent/slot/plan tests.
- Create `packages/python/goldengraph/tests/test_asof_mode.py` -- `asof_object` + auto dispatch (wheel).
- Modify `erkgbench/qa_e2e/router_eval.py` -- concept-named store + temporal gate + RouterResult ext.
- Modify `erkgbench/qa_e2e/.../tests/test_qa_router.py` -- temporal classifier-accuracy + gate shape.
- No CI-wiring changes: the slice-1 router gate step (goldengraph-pipeline), the bench-er-kg
  `test_qa_router.py` entry, and the `run_router_capability` lane already exist and now cover
  temporal automatically.

---

## Task 1: route.py temporal slots + `as_of` plan (wheel-free)

**Files:**
- Modify: `packages/python/goldengraph/goldengraph/route.py`
- Test: `packages/python/goldengraph/tests/test_route.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_route.py

def test_temporal_slots_extracted():
    p = route.classify_query("As of 42, what does Metaphone works at?", predicates=_PREDS)
    assert p.intent is route.QueryIntent.TEMPORAL_ASOF
    assert p.anchor_surface == "Metaphone"
    assert p.relation == "works_at"
    assert p.as_of == "42"
    assert p.confidence >= 0.8


def test_temporal_multiword_anchor():
    p = route.classify_query(
        "As of 7, what does Levenshtein distance located in?", predicates=_PREDS
    )
    assert p.anchor_surface == "Levenshtein distance"
    assert p.relation == "located_in"
    assert p.as_of == "7"


def test_plan_temporal_routes_to_as_of():
    p = route.classify_query("As of 42, what does Metaphone works at?", predicates=_PREDS)
    plan = route.plan_query(p)
    assert plan.mode == "as_of" and plan.note is None


def test_plan_temporal_low_confidence_falls_back():
    # no predicates -> relation None -> not routed to as_of
    p = route.classify_query("As of 42, what does Metaphone works at?")
    assert route.plan_query(p).mode == "local"
```

- [ ] **Step 2: Run to verify it fails**

Run: `... pytest packages/python/goldengraph/tests/test_route.py -k temporal -v`
Expected: FAIL (anchor_surface/as_of None; plan still `not_yet_promoted`).

- [ ] **Step 3: Write minimal implementation**

```python
# in route.py: add the temporal lead-in regex + extend classify_query's TEMPORAL_ASOF branch

_TEMPORAL_LEADIN_RE = re.compile(
    r"^\s*as of\s+(?P<d>\d+)\s*,\s*what does\s+(?P<rest>.+?)\s*[.?]?\s*$",
    re.IGNORECASE,
)


def _extract_temporal_slots(query: str, predicates):
    """(anchor, relation, as_of) from 'As of <D>, what does <anchor> <relation words>?'.
    Reuses the predicate-suffix split. Returns (None, None, None) when the lead-in misses."""
    m = _TEMPORAL_LEADIN_RE.match(query)
    if not m:
        return None, None, None
    rest = m.group("rest").strip()
    anchor, relation = _split_anchor_relation(rest, predicates)  # see NOTE
    return anchor, relation, m.group("d")
```

NOTE: factor the predicate-suffix split out of the existing `_extract_agg_slots` into a shared
`_split_anchor_relation(rest, predicates) -> (anchor|None, relation|None)` (the body that does the
longest-suffix predicate match), and call it from BOTH `_extract_agg_slots` and
`_extract_temporal_slots`. DRY; keep `_extract_agg_slots`'s behavior identical.

Then in `classify_query`, add the TEMPORAL_ASOF slot branch:

```python
    if intent is QueryIntent.TEMPORAL_ASOF:
        anchor, relation, as_of = _extract_temporal_slots(query, predicates)
        conf = 0.9 if (anchor and relation and as_of) else 0.5
        return QueryProfile(intent=intent, anchor_surface=anchor, relation=relation,
                            as_of=as_of, confidence=conf)
```

And in `plan_query`, replace the temporal rule:

```python
    if profile.intent is QueryIntent.TEMPORAL_ASOF:
        if profile.confidence >= MIN_CONF and profile.anchor_surface and profile.relation and profile.as_of:
            return RetrievalPlan(mode="as_of")
        return RetrievalPlan(mode="local")  # low-confidence temporal -> safe general mode
```

(Delete the old `RetrievalPlan(mode="local", note="not_yet_promoted")` line. Update the slice-1
test `test_plan_temporal_marked_not_yet_promoted` -> assert `mode == "as_of"` for a well-formed
temporal query, or delete it -- it's superseded by `test_plan_temporal_routes_to_as_of`.)

- [ ] **Step 4: Run to verify it passes**

Run: `... pytest packages/python/goldengraph/tests/test_route.py -v`
Expected: PASS (all, including the updated slice-1 temporal test). `ruff check route.py` -> clean.

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldengraph/goldengraph/route.py packages/python/goldengraph/tests/test_route.py
git commit -m "feat(goldengraph): query-router temporal slot extraction + as_of plan"
```

---

## Task 2: asof_object + as_of branch + clamp (wheel-bound)

**Files:**
- Modify: `packages/python/goldengraph/goldengraph/answer.py`
- Test: `packages/python/goldengraph/tests/test_asof_mode.py`

- [ ] **Step 1: Write the failing test** (mirror `test_aggregate_mode.py`; build a 2-window store)

```python
# packages/python/goldengraph/tests/test_asof_mode.py
"""asof_object + ask(mode='auto') temporal dispatch -- needs goldengraph_native (CI lane)."""
from __future__ import annotations

import json

import pytest

pytest.importorskip("goldengraph_native")

from conftest import StubEmbedder, StubLLM  # noqa: E402
from goldengraph.answer import asof_object, ask  # noqa: E402


def _windowed_store():
    # X works_at A for [1,5); X works_at B for [5, inf). Hand-built (build_batch can't set valid_to).
    from goldengraph_native import _native as ggn

    store = ggn.PyStore()
    batch = {
        "entities": [
            {"local_id": 0, "canonical_name": "X", "typ": "concept", "surface_names": ["X"], "record_keys": ["kx"]},
            {"local_id": 1, "canonical_name": "Apple", "typ": "concept", "surface_names": ["Apple"], "record_keys": ["ka"]},
            {"local_id": 2, "canonical_name": "Banana", "typ": "concept", "surface_names": ["Banana"], "record_keys": ["kb"]},
        ],
        "edges": [
            {"subj_local": 0, "predicate": "works_at", "obj_local": 1, "valid_from": 1, "valid_to": 5, "source_refs": []},
            {"subj_local": 0, "predicate": "works_at", "obj_local": 2, "valid_from": 5, "valid_to": None, "source_refs": []},
        ],
        "ingested_at": 1,
    }
    store.append(json.dumps(batch))
    return store


def test_asof_object_flips_across_the_correction():
    store = _windowed_store()
    assert asof_object(store.as_of(3, 10**12), "X", "works_at") == "Apple"   # past window
    assert asof_object(store.as_of(7, 10**12), "X", "works_at") == "Banana"  # current window


def test_ask_auto_routes_temporal_past():
    store = _windowed_store()
    out = ask("As of 3, what does X works_at?", store, llm=StubLLM("UNUSED"),
              embedder=StubEmbedder({}), valid_t=10**12, tx_t=10**12, mode="auto")
    assert out == "Apple"


def test_ask_auto_unparseable_date_falls_back_to_local(monkeypatch):
    # a temporal-looking query whose date can't int() must NOT raise (clamp -> local synthesis)
    store = _windowed_store()
    # StubLLM returns a canned synthesis string; the point is it does not raise ValueError
    out = ask("As of 2020, what does X works_at?", store, llm=StubLLM("some answer"),
              embedder=StubEmbedder({"X": 0}), valid_t=10**12, tx_t=10**12, mode="auto")
    assert isinstance(out, str)  # "2020" parses fine; this asserts the path is exercised w/o raise
```

NOTE: "2020" parses as an int, so the third test mostly asserts no-raise on the temporal path. To
test the TRUE unparseable branch, the date regex requires `\d+`, so a non-digit date won't even
classify TEMPORAL_ASOF. The clamp's real value is defensive (a future date format); the test that
matters is `test_asof_object_flips_across_the_correction` + `test_ask_auto_routes_temporal_past`.
Keep the third test as a smoke that the temporal `ask` path returns a string without raising.

- [ ] **Step 2: Run to verify it fails** (CI lane / skips locally without the wheel)

Run: `... pytest packages/python/goldengraph/tests/test_asof_mode.py -v`
Expected: FAIL (`ImportError: cannot import name 'asof_object'`) in CI, SKIP locally.

- [ ] **Step 3: Write minimal implementation**

```python
# add to answer.py (near aggregate_members)

def asof_object(slice_graph, anchor_surface: str, relation: str) -> str | None:
    """The object on a (subj==seed, predicate==relation) edge present IN THIS SLICE (the slice
    already encodes the as-of window). LLM-free."""
    seeds = slice_graph.seeds_by_name(anchor_surface)
    if not seeds:
        return None
    sub = slice_graph.query(seeds, 1)
    id_to_name = {e["entity_id"]: e["canonical_name"] for e in sub.get("entities", ())}
    seedset = set(seeds)
    objs = {
        id_to_name[e["obj"]]
        for e in sub.get("edges", ())
        if e["subj"] in seedset and e["predicate"] == relation and e["obj"] in id_to_name
    }
    objs.discard(anchor_surface)
    return next(iter(sorted(objs)), None)
```

Then update the `mode == "auto"` block (add the as_of branch + the CLAMP, per the spec):

```python
        if plan.mode == "as_of" and profile.anchor_surface and profile.relation and profile.as_of:
            try:
                d = int(profile.as_of)
            except ValueError:
                d = None
            if d is not None:
                obj = asof_object(store.as_of(d, tx_t), profile.anchor_surface, profile.relation)
                return obj if obj is not None else "(unknown)"
        # clamp: a specialized plan that did not return must not carry an invalid mode downstream
        mode = plan.mode if plan.mode in ("local", "hybrid", "global") else "local"
```

(Replace the slice-1 `mode = plan.mode  # fall through ...` line with this clamp.)

- [ ] **Step 4: Run to verify it passes** (CI lane). `ruff check answer.py` -> clean.

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldengraph/goldengraph/answer.py packages/python/goldengraph/tests/test_asof_mode.py
git commit -m "feat(goldengraph): asof_object + ask(mode=auto) temporal dispatch + mode clamp"
```

---

## Task 3: router gate temporal extension (er-kg-bench)

**Files:**
- Modify: `erkgbench/qa_e2e/router_eval.py`
- Test: `tests/test_qa_router.py`

- [ ] **Step 1: Write the failing wheel-free test**

```python
# add to tests/test_qa_router.py

def test_temporal_classifier_accuracy_on_b2_questions():
    acc = re_.temporal_classifier_accuracy(seed=7, n_facts=20, ambiguity=0.6)
    assert acc["temporal_recall"] == 1.0
    assert acc["temporal_slot_acc"] == 1.0


def test_gate_shape_includes_temporal():
    res = re_.RouterResult(
        aggregate_recall=1.0, slot_accuracy=1.0, routed_setf1=1.0,
        temporal_recall=1.0, temporal_slot_acc=1.0, temporal_past_acc=1.0, temporal_current_acc=1.0,
    )
    assert re_.gate_exit_code(res) == 0


def test_gate_fails_when_temporal_past_low():
    res = re_.RouterResult(
        aggregate_recall=1.0, slot_accuracy=1.0, routed_setf1=1.0,
        temporal_recall=1.0, temporal_slot_acc=1.0, temporal_past_acc=0.0, temporal_current_acc=1.0,
    )
    assert re_.gate_exit_code(res) == 1
```

NOTE: extending `RouterResult` with required fields breaks slice-1's `RouterResult(...)` test
constructions (they pass only the 3 aggregate fields). Give the 4 new temporal fields DEFAULTS
(`= 1.0`) so slice-1 tests still construct a passing result, OR update those test constructors. Use
defaults -- less churn, and a default-1.0 means "not measured -> not failing".

- [ ] **Step 2: Run to verify it fails**

Run: `cd <bench dir> && PYTHONPATH="$(pwd);<goldengraph>" "$PYEXE" -m pytest tests/test_qa_router.py -k temporal -v`
Expected: FAIL (`AttributeError: temporal_classifier_accuracy`).

- [ ] **Step 3: Write minimal implementation**

```python
# in router_eval.py

# extend the dataclass (defaults keep slice-1 constructions valid)
@dataclass
class RouterResult:
    aggregate_recall: float
    slot_accuracy: float
    routed_setf1: float
    temporal_recall: float = 1.0
    temporal_slot_acc: float = 1.0
    temporal_past_acc: float = 1.0
    temporal_current_acc: float = 1.0


def temporal_classifier_accuracy(*, seed: int, n_facts: int, ambiguity: float) -> dict:
    from goldengraph.route import QueryIntent, classify_query

    from .engineered import RELATION_SCHEMA, _load_entities
    from .temporal import generate_temporal

    _docs, _facts, qs = generate_temporal(seed=seed, n_facts=n_facts, ambiguity=ambiguity)
    by_id = {e.id: e for e in _load_entities()}
    preds = set(RELATION_SCHEMA)
    rec = slot = 0
    for q in qs:
        p = classify_query(q.question, predicates=preds)
        if p.intent is QueryIntent.TEMPORAL_ASOF:
            rec += 1
        ok_date = p.as_of is not None and p.as_of.isdigit() and int(p.as_of) == q.D
        if p.anchor_surface == by_id[q.anchor_id].canonical and p.relation == q.relation and ok_date:
            slot += 1
    n = len(qs) or 1
    return {"temporal_recall": rec / n, "temporal_slot_acc": slot / n}


def _build_concept_named_temporal_store(facts, by_id):
    """Mirror temporal.build_temporal_store but name nodes by CONCEPT SURFACE (so question-text
    seeds_by_name resolves) while keeping oracle merge on the QID record_keys. Needs the wheel."""
    import json

    from goldengraph_native import _native as ggn

    from .temporal import T1

    store = ggn.PyStore()
    for f in facts:
        def ent(local, _id):
            return {"local_id": local, "canonical_name": by_id[_id].canonical, "typ": "concept",
                    "surface_names": [by_id[_id].canonical], "record_keys": [_id]}
        batch = {
            "entities": [ent(0, f.anchor_id), ent(1, f.a_id), ent(2, f.b_id)],
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


def run_temporal_routed_accuracy(*, seed: int, n_facts: int, ambiguity: float) -> dict:
    """Route each B2 question through classify_query -> store.as_of(D) -> asof_object; as-of-accuracy
    by regime vs name-projected gold. Needs the wheel."""
    from goldengraph.answer import asof_object
    from goldengraph.route import classify_query

    from .engineered import RELATION_SCHEMA, _load_entities
    from .temporal import _BIG_TX, as_of_accuracy, generate_temporal

    _docs, facts, qs = generate_temporal(seed=seed, n_facts=n_facts, ambiguity=ambiguity)
    by_id = {e.id: e for e in _load_entities()}
    store = _build_concept_named_temporal_store(facts, by_id)
    preds = set(RELATION_SCHEMA)
    acc: dict = {"past": [], "current": []}
    for q in qs:
        p = classify_query(q.question, predicates=preds)
        got = None
        if p.anchor_surface and p.relation and p.as_of and p.as_of.isdigit():
            got = asof_object(store.as_of(int(p.as_of), _BIG_TX), p.anchor_surface, p.relation)
        gold_name = by_id[q.gold_obj].canonical
        acc[q.regime].append(as_of_accuracy(got, gold_name))
    return {r: (sum(v) / len(v) if v else 0.0) for r, v in acc.items()}
```

Then extend the slice-1 freeze constants + `evaluate_assertions` + `run_router_deterministic` +
`render_router_md`:

```python
# new frozen thresholds
TEMPORAL_RECALL_MIN = 0.99
TEMPORAL_SLOT_MIN = 0.99
TEMPORAL_ACC_MIN = 0.99

# in evaluate_assertions: APPEND these HARD rows
        (f"classifier routes temporal questions to TEMPORAL_ASOF (recall {res.temporal_recall:.3f} >= {TEMPORAL_RECALL_MIN})",
         res.temporal_recall >= TEMPORAL_RECALL_MIN, True),
        (f"temporal slots (anchor/relation/date) correct (acc {res.temporal_slot_acc:.3f} >= {TEMPORAL_SLOT_MIN})",
         res.temporal_slot_acc >= TEMPORAL_SLOT_MIN, True),
        (f"routed as-of-accuracy past=={res.temporal_past_acc:.3f} current=={res.temporal_current_acc:.3f} (both >= {TEMPORAL_ACC_MIN})",
         res.temporal_past_acc >= TEMPORAL_ACC_MIN and res.temporal_current_acc >= TEMPORAL_ACC_MIN, True),
```

```python
def run_router_deterministic(*, seed: int, n_anchors: int) -> RouterResult:
    acc = classifier_accuracy(seed=seed, n_anchors=n_anchors, ambiguity=0.0)
    routed = run_routed_correctness(seed=seed, n_anchors=n_anchors)
    tacc = temporal_classifier_accuracy(seed=seed, n_facts=n_anchors, ambiguity=0.6)
    tr = run_temporal_routed_accuracy(seed=seed, n_facts=n_anchors, ambiguity=0.6)
    return RouterResult(
        aggregate_recall=acc["aggregate_recall"], slot_accuracy=acc["slot_accuracy"],
        routed_setf1=routed,
        temporal_recall=tacc["temporal_recall"], temporal_slot_acc=tacc["temporal_slot_acc"],
        temporal_past_acc=tr.get("past", 0.0), temporal_current_acc=tr.get("current", 0.0),
    )
```

Add temporal lines to `render_router_md` (the `- temporal_*:` rows). NOTE the B2 temporal store is
ER-perfect by construction (oracle record_keys), so `ambiguity=0.6` is fine for temporal (the
ambiguity only affects rendered doc surfaces, which the gate does not read -- it routes from the
question text and builds the store from facts). Aggregation stays pinned at `ambiguity=0.0` (slice 1).

- [ ] **Step 4: Run the wheel-free test + ruff**

Run: `... pytest tests/test_qa_router.py -v` (the temporal-classifier + gate-shape tests; routed
accuracy runs in CI). `ruff check router_eval.py` -> clean.

- [ ] **Step 5: Commit**

```bash
git add erkgbench/qa_e2e/router_eval.py tests/test_qa_router.py
git commit -m "feat(er-kg-bench): router gate temporal extension (concept-named windowed store)"
```

---

## Task 4: opt-in temporal auto-vs-local row

**Files:**
- Modify: `erkgbench/qa_e2e/router_eval.py`
- Test: `tests/test_qa_router.py`

Extend `run_router_llm` (slice 1) to ALSO compare `ask(mode="auto")` vs `ask(mode="local")` on the
PAST-regime temporal questions, building the concept-named WINDOWED store (NOT engine.build_kg --
spec). The pure scoring is `as_of_accuracy` (already in temporal.py) over the answer text mapped to a
name; reuse the slice-1 `answer_setf1`-style parse for a single object. Heavy/billing-blocked/guarded.

- [ ] **Step 1: Write the failing test (pure helper)**

```python
# add to tests/test_qa_router.py
def test_first_known_name_in_text():
    assert re_.first_known_name("It is Banana.", {"Apple", "Banana"}) == "Banana"
    assert re_.first_known_name("nothing", {"Apple"}) is None
```

- [ ] **Step 2-4:** implement `first_known_name(text, universe) -> str|None` (the single-object
  analog of slice-1 `parse_entity_set`: first universe name appearing in the text). Extend
  `RouterLLMResult` with `temporal_auto_acc`/`temporal_local_acc` (defaults None) and extend
  `run_router_llm` to: build the concept-named windowed store, iterate PAST-regime questions, call
  `ask(..., mode="auto")` and `ask(..., mode="local")` with the engine's real llm/embedder (from
  `_build_engine("goldengraph")` for the llm+embedder only; the STORE is the windowed one), map each
  answer via `first_known_name` to a name, score `as_of_accuracy` vs `by_id[q.gold_obj].canonical`,
  mean per arm. Guard the whole thing in try/except -> None on failure. Add the rows to
  `render_router_llm_md`. Run the pure test (PASS), ruff clean, commit.

```bash
git add erkgbench/qa_e2e/router_eval.py tests/test_qa_router.py
git commit -m "feat(er-kg-bench): opt-in temporal auto-vs-local row (windowed store)"
```

---

## Final verification (before finishing the branch)

- [ ] `... pytest packages/python/goldengraph/tests/test_route.py -v` -> PASS.
- [ ] er-kg-bench `tests/test_qa_router.py` -> PASS (wheel-free part: aggregate + temporal classifier + gate shape + pure helpers).
- [ ] `ruff check` on all modified .py -> clean.
- [ ] Use superpowers:finishing-a-development-branch: push (benzsevern account), open PR vs `feat/goldengraph-query-router` (STACKED -- base is slice 1, not main, until #1283 merges; if #1283 merges first, rebase `--onto origin/main` + retarget to main). Watch the `goldengraph-pipeline` router gate go green BEFORE arming `gh pr merge --auto`. Freeze `TEMPORAL_*` thresholds from the measured `ROUTER.md` if needed; record memory.
- [ ] If routed as-of-accuracy is NOT 1.0 in BOTH regimes, surface to Ben (the as-of traversal or the date->slice wiring is wrong) -- do not loosen the gate.

## Known unknowns to resolve during implementation (call out, don't guess)

- Confirm `_split_anchor_relation` factoring keeps `_extract_agg_slots` byte-identical (run the
  slice-1 aggregate route tests after refactoring).
- Confirm the slice-1 `RouterResult(...)` test constructors still pass after adding defaulted
  temporal fields (or update them).
- Confirm the slice-1 `test_plan_temporal_marked_not_yet_promoted` is updated/removed (it now routes
  to `as_of`).
- The opt-in `run_router_llm` temporal arm uses the engine's `_llm`/`_embedder` over the WINDOWED
  store -- confirm `_build_engine("goldengraph")` exposes those (it does: `engine._llm`,
  `engine._embedder`) and that `ask(mode="local")` over the windowed store doesn't need a passage
  retriever (local mode doesn't; hybrid does).
