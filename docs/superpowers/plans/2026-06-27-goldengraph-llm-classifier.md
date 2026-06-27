# GoldenGraph query-router slice 3 (LLM classifier tier) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a two-tier query classifier (heuristic-first, injected LLM last-resort, budget-capped, abstain->local) + `ask(mode="auto")` opt-in injection (default heuristic-only) + a paraphrased-NL eval asset + a deterministic gate proving the heuristic misses paraphrases and a stub classifier recovers them.

**Architecture:** Extend `goldengraph/route.py` with a `QueryClassifier` protocol, an `LLMQueryClassifier` (defensive JSON parse, budget cap, fail-open abstain), and `resolve_profile` (heuristic first; escalate to the LLM only when heuristic confidence < `MIN_CONF`; LLM wins only if strictly more confident). `ask` gains an optional `query_classifier` kwarg (default None == byte-identical to slice 2). The gate (`erkgbench/qa_e2e/router_eval.py`) uses a hand-authored paraphrase fixture + a deterministic StubClassifier.

**Tech Stack:** Python 3.12, pytest, ruff. STACKED on slice 2 (`feat/goldengraph-temporal-mode`, PR #1284). `resolve_profile` + `LLMQueryClassifier` (stub-LLM) + the paraphrase gate are wheel-free; the one `ask(...query_classifier=stub)` routing test needs the `goldengraph_native` wheel (goldengraph-pipeline CI).

**Spec:** `docs/superpowers/specs/2026-06-27-goldengraph-llm-classifier-design.md`

---

## Conventions for every task

- Worktree: `D:\show_case\gg-llm-classifier`, branch `feat/goldengraph-llm-classifier` (stacked on slice 2).
- `PYEXE="D:/show_case/goldenmatch/.venv/Scripts/python.exe"`.
- `route.py` tests wheel-free: `cd D:/show_case/gg-llm-classifier && POLARS_SKIP_CPU_CHECK=1 PYTHONPATH="packages/python/goldengraph" "$PYEXE" -m pytest packages/python/goldengraph/tests/test_route.py -v`.
- er-kg-bench wheel-free tests: from the bench dir, `PYTHONPATH="$(pwd);D:/show_case/gg-llm-classifier/packages/python/goldengraph"` (`;` separator on Windows).
- `ask(...query_classifier=...)` routing test needs the wheel -> CI only; `pytest.importorskip("goldengraph_native")`.
- Ruff-clean per commit. **GOTCHA (from slice 2): any `ask(mode="auto")`-via-classify test MUST use the SPACED relation phrase** (e.g. "works at", not "works_at") -- the slot splitter suffix-matches `rel.replace("_"," ")`.
- Commit footer:
  ```
  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01Sc5aGSaVBTWBWpytNH99QE
  ```
- Verified slice-1/2 facts: `route.py` has `classify_query(query, *, predicates=None) -> QueryProfile`; `QueryProfile(intent, anchor_surface, relation, as_of, confidence=0.0)`; `QueryIntent` StrEnum (AGGREGATE/TEMPORAL_ASOF/MULTI_HOP/LOOKUP); `plan_query`; `MIN_CONF=0.8`; classify_query returns conf 0.9 (well-formed aggregate/temporal), 0.5 (matched-intent/missing-slots, LOOKUP), 0.3 (MULTI_HOP). `answer.py:128` = `profile = classify_query(query, predicates=_slice_predicates(slice_graph))`. `llm.py`: `LLMClient.complete(prompt)->str`, `OpenAIClient`. `extract.py` has `_strip_fence(raw)`.

## File structure

- Modify `packages/python/goldengraph/goldengraph/route.py` -- `QueryClassifier` Protocol, `resolve_profile`, `LLMQueryClassifier`.
- Modify `packages/python/goldengraph/goldengraph/answer.py` -- `query_classifier` kwarg + the `resolve_profile` swap at line 128.
- Modify `packages/python/goldengraph/tests/test_route.py` -- `resolve_profile` + `LLMQueryClassifier` tests.
- Modify `packages/python/goldengraph/tests/test_asof_mode.py` -- one wheel `ask(...query_classifier=stub)` paraphrase-routing test.
- Create `erkgbench/qa_e2e/router_paraphrases.py` -- the paraphrase fixture.
- Modify `erkgbench/qa_e2e/router_eval.py` -- `StubClassifier`, `heuristic_paraphrase_accuracy`, `stub_escalation_accuracy`, `RouterResult` ext + assertions + render; opt-in `llm_classifier_accuracy` + `run_router_llm` ext.
- Modify `tests/test_qa_router.py` -- paraphrase gate tests.
- No new CI input: the goldengraph-pipeline router gate + the `run_router_capability` lane already exist and pick up the extensions.

---

## Task 1: QueryClassifier protocol + resolve_profile (wheel-free)

**Files:**
- Modify: `packages/python/goldengraph/goldengraph/route.py`
- Test: `packages/python/goldengraph/tests/test_route.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_route.py

class _StubClassifier:
    """Deterministic tier-2: returns a pre-set high-confidence profile for a known query."""
    def __init__(self, mapping):
        self.mapping = mapping  # query -> QueryProfile
    def classify(self, query, *, predicates=None):
        return self.mapping.get(query, route.QueryProfile(route.QueryIntent.MULTI_HOP, confidence=0.0))


def test_resolve_profile_no_classifier_is_heuristic():
    # canonical query (heuristic conf 0.9) -> heuristic, no escalation
    p = route.resolve_profile("List all entities that Metaphone works at.", predicates=_PREDS)
    assert p.intent is route.QueryIntent.AGGREGATE and p.relation == "works_at"


def test_resolve_profile_canonical_never_escalates():
    # even WITH a classifier, a high-conf heuristic result is returned unchanged
    oracle = route.QueryProfile(route.QueryIntent.AGGREGATE, anchor_surface="WRONG", relation="works_at", confidence=1.0)
    stub = _StubClassifier({"List all entities that Metaphone works at.": oracle})
    p = route.resolve_profile("List all entities that Metaphone works at.", predicates=_PREDS, llm_classifier=stub)
    assert p.anchor_surface == "Metaphone"  # heuristic kept (0.9 >= MIN_CONF), stub NOT consulted


def test_resolve_profile_low_conf_escalates_to_classifier():
    q = "who all does Metaphone work with?"  # heuristic misses -> low conf
    oracle = route.QueryProfile(route.QueryIntent.AGGREGATE, anchor_surface="Metaphone", relation="works_at", confidence=1.0)
    stub = _StubClassifier({q: oracle})
    p = route.resolve_profile(q, predicates=_PREDS, llm_classifier=stub)
    assert p.intent is route.QueryIntent.AGGREGATE and p.anchor_surface == "Metaphone" and p.relation == "works_at"


def test_resolve_profile_classifier_abstain_keeps_heuristic():
    q = "ramble ramble nonsense"
    stub = _StubClassifier({})  # returns MULTI_HOP conf 0.0
    p = route.resolve_profile(q, predicates=_PREDS, llm_classifier=stub)
    assert p.intent is route.QueryIntent.MULTI_HOP  # heuristic 0.3 kept (0.0 not > 0.3)
```

- [ ] **Step 2: Run to verify it fails**

Run: `... pytest packages/python/goldengraph/tests/test_route.py -k resolve_profile -v`
Expected: FAIL (`AttributeError: resolve_profile`).

- [ ] **Step 3: Write minimal implementation**

```python
# in route.py: add Protocol import + the protocol + resolve_profile (after plan_query)

# at the top imports: `from typing import Protocol`


class QueryClassifier(Protocol):
    def classify(self, query: str, *, predicates=None) -> "QueryProfile": ...


def resolve_profile(query: str, *, predicates=None, llm_classifier: "QueryClassifier | None" = None) -> QueryProfile:
    """Two-tier: heuristic FIRST; escalate to the injected classifier ONLY when the heuristic is
    below MIN_CONF AND a classifier is given; the classifier's result wins only if strictly more
    confident (so a confidently-abstaining tier-2 keeps the heuristic -> safe local route)."""
    h = classify_query(query, predicates=predicates)
    if h.confidence >= MIN_CONF or llm_classifier is None:
        return h
    ll = llm_classifier.classify(query, predicates=predicates)
    return ll if ll.confidence > h.confidence else h
```

- [ ] **Step 4: Run to verify it passes**

Run: `... pytest packages/python/goldengraph/tests/test_route.py -v`
Expected: PASS (all). `ruff check route.py` -> clean.

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldengraph/goldengraph/route.py packages/python/goldengraph/tests/test_route.py
git commit -m "feat(goldengraph): QueryClassifier protocol + two-tier resolve_profile"
```

---

## Task 2: LLMQueryClassifier (wheel-free, stub-LLM)

**Files:**
- Modify: `packages/python/goldengraph/goldengraph/route.py`
- Test: `packages/python/goldengraph/tests/test_route.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_route.py

class _StubLLM:
    def __init__(self, response): self.response = response; self.calls = 0
    def complete(self, prompt): self.calls += 1; return self.response


def test_llm_classifier_parses_json_to_profile():
    llm = _StubLLM('{"intent": "aggregate", "anchor": "Metaphone", "relation": "works_at", "as_of": null}')
    c = route.LLMQueryClassifier(llm)
    p = c.classify("who all does Metaphone work with?", predicates=_PREDS)
    assert p.intent is route.QueryIntent.AGGREGATE
    assert p.anchor_surface == "Metaphone" and p.relation == "works_at"
    assert p.confidence >= 0.8


def test_llm_classifier_strips_fence():
    llm = _StubLLM('```json\n{"intent":"temporal_asof","anchor":"X","relation":"works_at","as_of":"3"}\n```')
    p = route.LLMQueryClassifier(llm).classify("...", predicates=_PREDS)
    assert p.intent is route.QueryIntent.TEMPORAL_ASOF and p.as_of == "3"


def test_llm_classifier_out_of_vocab_relation_abstains():
    llm = _StubLLM('{"intent":"aggregate","anchor":"X","relation":"NOT_A_PREDICATE","as_of":null}')
    p = route.LLMQueryClassifier(llm).classify("...", predicates=_PREDS)
    assert p.confidence == 0.0  # relation not in vocab -> abstain


def test_llm_classifier_bad_json_abstains():
    p = route.LLMQueryClassifier(_StubLLM("not json at all")).classify("...", predicates=_PREDS)
    assert p.intent is route.QueryIntent.MULTI_HOP and p.confidence == 0.0


def test_llm_classifier_budget_cap():
    llm = _StubLLM('{"intent":"lookup","anchor":null,"relation":null,"as_of":null}')
    c = route.LLMQueryClassifier(llm, max_calls=2)
    c.classify("a", predicates=_PREDS); c.classify("b", predicates=_PREDS)
    p = c.classify("c", predicates=_PREDS)  # 3rd call over budget -> abstain, no LLM call
    assert p.confidence == 0.0 and llm.calls == 2
```

- [ ] **Step 2: Run to verify it fails**

Run: `... pytest packages/python/goldengraph/tests/test_route.py -k llm_classifier -v`
Expected: FAIL (`AttributeError: LLMQueryClassifier`).

- [ ] **Step 3: Write minimal implementation**

```python
# in route.py: add `import json` at top; add a local fence-strip + the classifier

def _strip_fence(raw: str) -> str:
    s = raw.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s[3:]
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    return s.strip()


_ABSTAIN = None  # sentinel built lazily below to avoid forward-ref at import


class LLMQueryClassifier:
    """Tier-2 classifier: prompt an LLMClient for {intent, anchor, relation, as_of}; defensive
    parse -> QueryProfile. Budget-capped (max_calls). Fail-open: any failure (budget, exception,
    bad JSON, out-of-vocab relation) -> abstain QueryProfile(MULTI_HOP, confidence=0.0)."""

    _PROMPT = (
        "Classify this knowledge-graph question. Reply with ONLY a JSON object:\n"
        '{{"intent": "aggregate|temporal_asof|lookup|multi_hop", "anchor": "<entity or null>", '
        '"relation": "<one of: {preds}> or null", "as_of": "<integer date or null>"}}\n'
        "Question: {q}"
    )

    def __init__(self, llm, *, max_calls: int = 5):
        self._llm = llm
        self._max_calls = max_calls
        self._calls = 0

    def classify(self, query: str, *, predicates=None) -> QueryProfile:
        abstain = QueryProfile(QueryIntent.MULTI_HOP, confidence=0.0)
        if self._calls >= self._max_calls:
            return abstain
        self._calls += 1
        try:
            preds = ", ".join(sorted(predicates)) if predicates else ""
            raw = self._llm.complete(self._PROMPT.format(preds=preds, q=query))
            data = json.loads(_strip_fence(raw))
        except Exception:
            return abstain
        try:
            intent = QueryIntent(str(data.get("intent", "")).strip().lower())
        except ValueError:
            return abstain
        anchor = data.get("anchor") or None
        relation = data.get("relation") or None
        if relation is not None and (not predicates or relation not in predicates):
            return abstain  # hallucinated/out-of-vocab relation
        as_of = str(data["as_of"]) if data.get("as_of") not in (None, "") else None
        return QueryProfile(intent=intent, anchor_surface=anchor, relation=relation,
                            as_of=as_of, confidence=0.85)
```

(Delete the `_ABSTAIN` sentinel line -- it was a thinking artifact; build `abstain` inline as shown.)

- [ ] **Step 4: Run to verify it passes**

Run: `... pytest packages/python/goldengraph/tests/test_route.py -v`
Expected: PASS (all). `ruff check route.py` -> clean.

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldengraph/goldengraph/route.py packages/python/goldengraph/tests/test_route.py
git commit -m "feat(goldengraph): LLMQueryClassifier (defensive parse, budget cap, fail-open abstain)"
```

---

## Task 3: ask(query_classifier=...) injection (wheel-bound)

**Files:**
- Modify: `packages/python/goldengraph/goldengraph/answer.py`
- Test: `packages/python/goldengraph/tests/test_asof_mode.py`

- [ ] **Step 1: Write the failing test** (reuse `_windowed_store` from test_asof_mode.py)

```python
# add to tests/test_asof_mode.py

def test_ask_auto_injected_classifier_recovers_paraphrase():
    from goldengraph.route import QueryIntent, QueryProfile

    class _Stub:
        def classify(self, query, *, predicates=None):
            # a paraphrase the heuristic misses; oracle says it's a temporal as_of @ D=3
            return QueryProfile(QueryIntent.TEMPORAL_ASOF, anchor_surface="X",
                                relation="works_at", as_of="3", confidence=1.0)

    store = _windowed_store()
    # "tell me X's employer back in 3" -- heuristic LEADIN regexes miss -> would route local;
    # injected stub recovers -> as_of slice at D=3 -> Apple
    out = ask("tell me X's employer back in 3", store, llm=StubLLM("UNUSED"),
              embedder=StubEmbedder({}), valid_t=_BIG, tx_t=_BIG, mode="auto", query_classifier=_Stub())
    assert out == "Apple"


def test_ask_auto_default_no_classifier_unchanged():
    store = _windowed_store()
    # canonical temporal (spaced phrasing) still routes via heuristic with no classifier
    out = ask("As of 3, what does X works at?", store, llm=StubLLM("UNUSED"),
              embedder=StubEmbedder({}), valid_t=_BIG, tx_t=_BIG, mode="auto")
    assert out == "Apple"
```

- [ ] **Step 2: Run to verify it fails** (CI lane / skips locally)

Run: `... pytest packages/python/goldengraph/tests/test_asof_mode.py -k injected -v`
Expected: FAIL in CI (`ask() got unexpected keyword 'query_classifier'`), SKIP locally.

- [ ] **Step 3: Write minimal implementation**

```python
# answer.py: import resolve_profile; add the kwarg; swap the call

# at top: from .route import classify_query, plan_query, resolve_profile
#   (classify_query may now be unused -> drop it from the import if ruff flags F401)

# in ask(...) signature, add (after passages/passage_k, keep keyword-only):
#     query_classifier: object | None = None,

# at line 128, replace:
#     profile = classify_query(query, predicates=_slice_predicates(slice_graph))
# with:
        profile = resolve_profile(
            query, predicates=_slice_predicates(slice_graph), llm_classifier=query_classifier
        )
```

NOTE: the paraphrase "tell me X's employer back in 3" must NOT match `_TEMPORAL_RE`
(`as of|at the time|in \d{4}|before \d|after \d`) in a way that makes the heuristic confident -- it
won't (no slots extract -> conf 0.5 -> escalates). Confirm the heuristic returns < MIN_CONF for it so
the stub is actually consulted. (If a chosen paraphrase accidentally yields a confident heuristic
result, pick a different one -- the test asserts the STUB's answer flows through.)

- [ ] **Step 4: Run to verify it passes** (CI lane). `ruff check answer.py` -> clean.

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldengraph/goldengraph/answer.py packages/python/goldengraph/tests/test_asof_mode.py
git commit -m "feat(goldengraph): ask(query_classifier=) opt-in two-tier routing"
```

---

## Task 4: paraphrase fixture + router gate (er-kg-bench)

**Files:**
- Create: `erkgbench/qa_e2e/router_paraphrases.py`
- Modify: `erkgbench/qa_e2e/router_eval.py`
- Test: `tests/test_qa_router.py`

- [ ] **Step 1: Write the paraphrase fixture**

```python
# erkgbench/qa_e2e/router_paraphrases.py
"""Hand-authored paraphrased-NL questions the heuristic lead-in regexes MISS, each carrying gold
slots. Anchors/relations are real concept surfaces/predicates from the engineered universe. Used by
slice 3 to prove heuristic-misses -> LLM(stub)-recovers. Deterministic; no LLM, no randomness."""
from __future__ import annotations

from dataclasses import dataclass

from goldengraph.route import QueryIntent


@dataclass(frozen=True)
class Paraphrase:
    question: str
    intent: QueryIntent
    anchor_surface: str
    relation: str
    as_of: str | None = None


# anchor surfaces + relations must exist in dataset/concepts.jsonl + RELATION_SCHEMA.
# (Author ~12-16 entries; examples below. Verify the heuristic misses each -- Task 4 Step 3.)
PARAPHRASES = [
    Paraphrase("who all does Soundex works at, list them", QueryIntent.AGGREGATE, "Soundex", "works_at"),
    Paraphrase("which things is Metaphone connected to through works at", QueryIntent.AGGREGATE, "Metaphone", "works_at"),
    Paraphrase("tell me everything Levenshtein distance located in", QueryIntent.AGGREGATE, "Levenshtein distance", "located_in"),
    Paraphrase("back in year 3, what did Soundex works at", QueryIntent.TEMPORAL_ASOF, "Soundex", "works_at", "3"),
    Paraphrase("at time 7 what was Metaphone works at", QueryIntent.TEMPORAL_ASOF, "Metaphone", "works_at", "7"),
    # ... author more; keep anchors/relations valid for the universe.
]
```

NOTE: the relation in `Paraphrase` is the underscored predicate id (e.g. `works_at`); the QUESTION
text uses natural phrasing. The anchor surfaces must be real `by_id[*].canonical` values -- pick a
handful from `dataset/concepts.jsonl` (Soundex/Metaphone/Levenshtein distance are present). The
TEMPORAL paraphrases must NOT contain a `\d{4}` year or literal "as of" at the start in a way the
heuristic parses (use "year 3" / "time 7").

- [ ] **Step 2: Write the failing gate test**

```python
# add to tests/test_qa_router.py

def test_heuristic_misses_paraphrases():
    acc = re_.heuristic_paraphrase_accuracy()
    assert acc <= 0.2  # heuristic routes ~none of the paraphrases correctly


def test_stub_escalation_recovers_paraphrases():
    acc = re_.stub_escalation_accuracy()
    assert acc == 1.0  # an oracle tier-2 recovers EVERY paraphrase's slots + correct plan


def test_gate_shape_includes_paraphrase_rows():
    res = re_.RouterResult(
        aggregate_recall=1.0, slot_accuracy=1.0, routed_setf1=1.0,
        temporal_recall=1.0, temporal_slot_acc=1.0, temporal_past_acc=1.0, temporal_current_acc=1.0,
        heuristic_paraphrase_acc=0.0, stub_escalation_acc=1.0,
    )
    assert re_.gate_exit_code(res) == 0


def test_gate_fails_when_stub_escalation_low():
    res = re_.RouterResult(
        aggregate_recall=1.0, slot_accuracy=1.0, routed_setf1=1.0,
        temporal_recall=1.0, temporal_slot_acc=1.0, temporal_past_acc=1.0, temporal_current_acc=1.0,
        heuristic_paraphrase_acc=0.0, stub_escalation_acc=0.5,
    )
    assert re_.gate_exit_code(res) == 1
```

- [ ] **Step 3: Write minimal implementation**

```python
# in router_eval.py

# extend RouterResult with two defaulted fields:
#     heuristic_paraphrase_acc: float = 0.0
#     stub_escalation_acc: float = 1.0
# new frozen thresholds:
HEURISTIC_PARAPHRASE_CEIL = 0.2   # heuristic must route <= this fraction correctly (proves the gap)
STUB_ESCALATION_MIN = 0.99        # the oracle tier-2 must recover all paraphrases


class StubClassifier:
    """Deterministic tier-2 oracle: paraphrase question -> a high-confidence QueryProfile from its
    gold slots (confidence=1.0 is REQUIRED so resolve_profile/plan_query accept it)."""
    def __init__(self, paraphrases):
        from goldengraph.route import QueryProfile
        self._m = {
            pp.question: QueryProfile(intent=pp.intent, anchor_surface=pp.anchor_surface,
                                      relation=pp.relation, as_of=pp.as_of, confidence=1.0)
            for pp in paraphrases
        }
    def classify(self, query, *, predicates=None):
        from goldengraph.route import QueryIntent, QueryProfile
        return self._m.get(query, QueryProfile(QueryIntent.MULTI_HOP, confidence=0.0))


def _profile_matches(p, pp) -> bool:
    return (p.intent is pp.intent and p.anchor_surface == pp.anchor_surface
            and p.relation == pp.relation and (p.as_of or None) == (pp.as_of or None))


def heuristic_paraphrase_accuracy() -> float:
    from goldengraph.route import classify_query

    from .router_paraphrases import PARAPHRASES

    preds = set(RELATION_SCHEMA)
    hits = sum(_profile_matches(classify_query(pp.question, predicates=preds), pp) for pp in PARAPHRASES)
    return hits / (len(PARAPHRASES) or 1)


def stub_escalation_accuracy() -> float:
    from goldengraph.route import plan_query, resolve_profile

    from .router_paraphrases import PARAPHRASES

    preds = set(RELATION_SCHEMA)
    stub = StubClassifier(PARAPHRASES)
    hits = 0
    for pp in PARAPHRASES:
        p = resolve_profile(pp.question, predicates=preds, llm_classifier=stub)
        want = "aggregate" if pp.intent.name == "AGGREGATE" else "as_of"
        if _profile_matches(p, pp) and plan_query(p).mode == want:
            hits += 1
    return hits / (len(PARAPHRASES) or 1)
```

Then APPEND to `evaluate_assertions` (both HARD):

```python
        (f"heuristic MISSES paraphrases (acc {res.heuristic_paraphrase_acc:.3f} <= {HEURISTIC_PARAPHRASE_CEIL})",
         res.heuristic_paraphrase_acc <= HEURISTIC_PARAPHRASE_CEIL, True),
        (f"stub tier-2 RECOVERS paraphrases (acc {res.stub_escalation_acc:.3f} >= {STUB_ESCALATION_MIN})",
         res.stub_escalation_acc >= STUB_ESCALATION_MIN, True),
```

And in `run_router_deterministic`, set the two fields:
```python
        heuristic_paraphrase_acc=heuristic_paraphrase_accuracy(),
        stub_escalation_acc=stub_escalation_accuracy(),
```

Add two `render_router_md` lines (`- heuristic_paraphrase_acc:` / `- stub_escalation_acc:`) + a note
that the stub is an ORACLE (proves the mechanism, not real-LLM accuracy).

- [ ] **Step 4: Run the wheel-free tests + ruff + VERIFY the heuristic actually misses**

Run: `... pytest tests/test_qa_router.py -k "paraphrase or escalation" -v` (PASS). Manually run
`python -c "from erkgbench.qa_e2e.router_eval import heuristic_paraphrase_accuracy as h; print(h())"`
and confirm it is <= 0.2 (re-author any paraphrase the heuristic accidentally parses). `ruff check`.

- [ ] **Step 5: Commit**

```bash
git add erkgbench/qa_e2e/router_paraphrases.py erkgbench/qa_e2e/router_eval.py tests/test_qa_router.py
git commit -m "feat(er-kg-bench): paraphrase fixture + heuristic-miss/stub-escalation gate"
```

---

## Task 5: opt-in real-LLM classifier accuracy

**Files:**
- Modify: `erkgbench/qa_e2e/router_eval.py`
- Test: `tests/test_qa_router.py`

- [ ] **Step 1: Write the failing test (pure-ish; stub LLM)**

```python
# add to tests/test_qa_router.py

def test_llm_classifier_accuracy_with_stub_llm():
    # a stub LLMClient that returns the correct JSON for one paraphrase -> accuracy 1.0 on a 1-item set
    from erkgbench.qa_e2e.router_paraphrases import Paraphrase
    from goldengraph.route import QueryIntent

    class _LLM:
        def complete(self, prompt):
            return '{"intent":"aggregate","anchor":"Soundex","relation":"works_at","as_of":null}'

    pps = [Paraphrase("who all does Soundex works at, list them", QueryIntent.AGGREGATE, "Soundex", "works_at")]
    acc = re_.llm_classifier_accuracy(pps, _LLM())
    assert acc["slot_acc"] == 1.0
```

- [ ] **Step 2-4:** implement `llm_classifier_accuracy(paraphrases, llm) -> dict` (build an
  `LLMQueryClassifier(llm)`, classify each paraphrase with `predicates=set(RELATION_SCHEMA)`, score
  intent-acc + slot-acc via `_profile_matches`). Then extend `RouterLLMResult` with
  `paraphrase_intent_acc`/`paraphrase_slot_acc` (default None) and `run_router_llm` to call
  `llm_classifier_accuracy(PARAPHRASES, engine._llm)` (guarded), and `render_router_llm_md` to add
  the rows. Run the stub-LLM test (PASS), ruff clean, commit.

```bash
git add erkgbench/qa_e2e/router_eval.py tests/test_qa_router.py
git commit -m "feat(er-kg-bench): opt-in real-LLM classifier accuracy on paraphrases"
```

---

## Final verification (before finishing the branch)

- [ ] `... pytest packages/python/goldengraph/tests/test_route.py -v` -> PASS.
- [ ] er-kg-bench `tests/test_qa_router.py` -> PASS (wheel-free).
- [ ] `ruff check` on all modified .py -> clean.
- [ ] Use superpowers:finishing-a-development-branch: push (benzsevern account), open PR. **Base:
  `feat/goldengraph-temporal-mode` if #1284 is still open; if #1284 merged first, rebase
  `--onto origin/main <slice-2-tip>` and target main** (slice-2 tip = the commit this branch started
  at; `git log --oneline` to find it). Watch the `goldengraph-pipeline` router gate green BEFORE
  arming `gh pr merge --auto`. Record memory.
- [ ] If `stub_escalation_accuracy` is not 1.0, surface to Ben (the two-tier resolution or plan_query
  is wrong) -- do not loosen the gate. If `heuristic_paraphrase_accuracy` is ABOVE the ceiling, the
  paraphrases are too easy -- re-author harder ones (the asset must exercise the gap).

## Known unknowns to resolve during implementation (call out, don't guess)

- Confirm each authored paraphrase's heuristic result is < MIN_CONF (Task 4 Step 4) -- the whole
  point is that the heuristic misses them. The `_TEMPORAL_RE`/`_AGG_RE` keyword regexes may still
  set the INTENT (e.g. "list them" contains "list" but not "list all"; "as of"-free temporal phrasings
  avoid TEMPORAL_RE) -- what matters is that SLOTS don't extract (conf 0.5) so it escalates and the
  heuristic's own routing is wrong (slot mismatch). `heuristic_paraphrase_accuracy` measures the full
  profile match, so a wrong-slots heuristic counts as a miss regardless of intent.
- Confirm `classify_query` may become unused in `answer.py` after the swap -> drop it from the import
  (ruff F401) but KEEP it imported in `route.py`/tests where used.
- Confirm `RELATION_SCHEMA` values match the predicate ids the paraphrase relations use.
