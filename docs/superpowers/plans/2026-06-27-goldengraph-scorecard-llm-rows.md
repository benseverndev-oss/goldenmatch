# GoldenGraph Scorecard Real-LLM Rows Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the three real-LLM scorecard rows (extraction-F1, synthesis-given-gold, 4-dial answer-match ablation matched to bridge-recall) as an opt-in, budget-capped, non-gating bench lane that produces `SCORECARD.md`.

**Architecture:** Pure metric functions (extraction-F1, gold-subgraph build, synthesis scoring, tracking verdict) are wheel-free and unit-tested with stub LLMs. An orchestrator (`run_scorecard`) runs all three rows under one `BudgetTracker`, wrapping the real LLM so every call records usage and the run stops cleanly at the cap. The answer-match ablation reuses #1274's `ablation._build_store` + `scorecard.bridge_recall` so both curves come from the identical store/ball. A thin CLI writes `SCORECARD.md`; a new opt-in `workflow_dispatch` lane runs it with `OPENAI_API_KEY`.

**Tech Stack:** Python 3.11, pytest (wheel-free except the e2e ablation row, which `importorskip`s `goldengraph_native`), GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-06-27-goldengraph-scorecard-llm-rows-design.md`

---

## Key code facts (verified against main, post-#1274)

- **Reused from #1274** (`erkgbench/qa_e2e/`): `gold.GoldGraph` (`from_corpus`, `canonical_name`, `gold_chain(g, qa) -> [(src_id, rel, dst_id), ...]`); `dials.{oracle,goldengraph,name_only,none}_keys`, `dials.surface_to_canon`; `ablation._build_store(corpus, g, km, typ_of) -> (slice_graph, coverage)`, `ablation._typ_of(g) -> {entity_id: entity_type}`; `scorecard.bridge_recall(gold_chain, subgraph, coverage) -> {"whole_chain","edge_recall"}`.
- **`run_ablation`'s seed pattern** (replicate): `seed_of = {}; for nid in sorted(coverage): for c in coverage[nid]: seed_of.setdefault(c, nid)`; then `_retrieve_local(slice_graph, [seed_of[start_entity_id]], max_hops=_RETRIEVAL_HOPS, node_budget=_NODE_BUDGET)`. Constants in `engines/goldengraph.py`: `_RETRIEVAL_HOPS` (6), `_NODE_BUDGET` (256), `_AS_OF` (10**12).
- **Synthesis:** `from goldengraph.synthesize import synthesize_local`; `synthesize_local(query, subgraph, llm, *, seed_names=None) -> str`. Subgraph shape: `{"entities":[{"entity_id","canonical_name","typ"}], "edges":[{"subj","predicate","obj"}]}` (`_format_subgraph` reads exactly these). `seed_names` is a list of NAMES (not ids).
- **Extraction:** `from goldengraph.extract import extract as _extract` (or `from goldengraph.ingest import _extract`); `_extract(text, llm) -> Extraction`. `Extraction.mentions: list[Mention]` (`Mention.name`, `.typ`); `Extraction.relationships: list[Relationship]` (`Relationship.subj`/`obj` are INT indices into `mentions`, `.predicate` free-form).
- **Answer-match:** `from . import metrics`; `metrics.answer_match(pred, gold) -> float` (1.0 if normalized gold is a contiguous token run in pred); `metrics._normalize(s)`.
- **Budget:** `from goldenmatch.config.schemas import BudgetConfig`; `from goldenmatch.core.llm_budget import BudgetTracker`. `t = BudgetTracker(BudgetConfig(max_cost_usd=X))`; `t.record_usage(in_tokens, out_tokens, model)`; `t.budget_exhausted`; `t.can_send(n_tokens)`; `t.total_cost_usd`.
- **Real LLM + token counter:** `from goldengraph.llm import OpenAIClient` → `OpenAIClient(model="gpt-4o-mini")`. `from .engines.goldengraph import _CountingLLM` wraps an LLMClient, exposes `.complete`, `.input_tokens`, `.output_tokens` (estimate = `len(prompt)//4`; the bench's existing approximation — reuse it, don't invent real token counts).

### Plan-review precision notes (carry these)
- `build_gold_subgraph` needs `GoldGraph` (for canonical names) AND a typ map (`ablation._typ_of`) — `gold_chain` alone yields only ids. `typ` is non-load-bearing for synthesis but use `_typ_of` to match the stated shape.
- The answer-match ablation must build `{entity_id: canonical_name}` from `slice_graph.entities()` to pass `seed_names=[name]` (the `seed_of` inversion yields an id).

## Test environment

```
PY="/d/show_case/goldenmatch/.venv/Scripts/python.exe"
cd packages/python/goldenmatch/benchmarks/er-kg-bench
PYTHONPATH="$(pwd -W)" POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8 GOLDENMATCH_NATIVE=0 "$PY" -m pytest <path> -q
```
Wheel-free for Tasks 1, 2, 4, 5; the Task 3 e2e ablation `importorskip`s `goldengraph_native` (skips locally, validates in the opt-in lane). Run only named files — never the full suite.

---

## Task 1: extraction-F1 metric (wheel-free)

**Files:**
- Create: `erkgbench/qa_e2e/scorecard_llm.py`
- Test: `tests/test_qa_extraction_f1.py`

Use @superpowers:test-driven-development.

- [ ] **Step 1: Write the failing tests**

```python
"""extraction-F1: did real extraction recover the gold entities + edge per doc.
Pure -- operates on a gold (src,dst) surface pair + a goldengraph Extraction."""
from __future__ import annotations

from dataclasses import dataclass

from erkgbench.qa_e2e.scorecard_llm import extraction_counts, f1_from_counts


@dataclass
class _M:
    name: str
    typ: str = "concept"


@dataclass
class _R:
    subj: int
    predicate: str
    obj: int


@dataclass
class _Ex:
    mentions: list
    relationships: list


def test_perfect_extraction_is_f1_one():
    ex = _Ex([_M("Acme"), _M("Rocket")], [_R(0, "made", 1)])
    c = extraction_counts("Acme", "Rocket", ex)
    assert c["ent_tp"] == 2 and c["ent_fp"] == 0 and c["ent_fn"] == 0
    assert c["rel_tp"] == 1 and c["rel_fp"] == 0 and c["rel_fn"] == 0


def test_missing_entity_drops_recall_and_loses_the_edge():
    ex = _Ex([_M("Acme")], [])  # dst entity + the edge both missing
    c = extraction_counts("Acme", "Rocket", ex)
    assert c["ent_tp"] == 1 and c["ent_fn"] == 1
    assert c["rel_tp"] == 0 and c["rel_fn"] == 1


def test_spurious_entity_drops_precision():
    ex = _Ex([_M("Acme"), _M("Rocket"), _M("Noise")], [_R(0, "made", 1)])
    c = extraction_counts("Acme", "Rocket", ex)
    assert c["ent_tp"] == 2 and c["ent_fp"] == 1


def test_relation_matches_either_direction_ignoring_predicate():
    # edge authored dst->src with a different predicate word still counts
    ex = _Ex([_M("Rocket"), _M("Acme")], [_R(0, "built by", 1)])
    c = extraction_counts("Acme", "Rocket", ex)
    assert c["rel_tp"] == 1 and c["rel_fp"] == 0


def test_normalization_case_insensitive():
    ex = _Ex([_M("ACME"), _M(" rocket ")], [_R(0, "made", 1)])
    c = extraction_counts("Acme", "Rocket", ex)
    assert c["ent_tp"] == 2 and c["rel_tp"] == 1


def test_f1_from_counts():
    assert f1_from_counts(2, 0, 0) == {"precision": 1.0, "recall": 1.0, "f1": 1.0}
    r = f1_from_counts(1, 1, 1)
    assert r["precision"] == 0.5 and r["recall"] == 0.5 and r["f1"] == 0.5
    assert f1_from_counts(0, 0, 0)["f1"] == 0.0  # empty -> 0, no ZeroDivision
```

- [ ] **Step 2: Run -> fail** (`ModuleNotFoundError ... scorecard_llm`).

- [ ] **Step 3: Implement** in `scorecard_llm.py`:

```python
"""Real-LLM scorecard rows (Phase 2 of slice A): extraction-F1, synthesis-given-gold,
and the 4-dial answer-match ablation matched to bridge-recall. Opt-in, budget-capped,
NON-gating -- the deterministic bridge-recall gate (#1274) stays the blocking signal."""
from __future__ import annotations

from . import metrics


def _norm(s: str) -> str:
    return metrics._normalize(s)


def extraction_counts(gold_src: str, gold_dst: str, extraction) -> dict:
    """Per-doc entity + (existence-based) relation TP/FP/FN of `extraction` vs the
    one gold edge {gold_src, gold_dst}. Predicate label ignored; edge counted in
    either direction."""
    gold_ents = {_norm(gold_src), _norm(gold_dst)}
    got_ents = {_norm(m.name) for m in extraction.mentions}
    ent_tp = len(gold_ents & got_ents)
    ent_fp = len(got_ents - gold_ents)
    ent_fn = len(gold_ents - got_ents)

    gold_edge = frozenset(gold_ents)
    got_edges = [
        frozenset({_norm(extraction.mentions[r.subj].name), _norm(extraction.mentions[r.obj].name)})
        for r in extraction.relationships
        if r.subj < len(extraction.mentions) and r.obj < len(extraction.mentions)
    ]
    rel_tp = 1 if gold_edge in got_edges else 0
    rel_fp = sum(1 for e in got_edges if e != gold_edge)
    rel_fn = 1 - rel_tp
    return {
        "ent_tp": ent_tp, "ent_fp": ent_fp, "ent_fn": ent_fn,
        "rel_tp": rel_tp, "rel_fp": rel_fp, "rel_fn": rel_fn,
    }


def f1_from_counts(tp: int, fp: int, fn: int) -> dict:
    p = tp / (tp + fp) if (tp + fp) else 0.0
    r = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * p * r / (p + r) if (p + r) else 0.0
    return {"precision": p, "recall": r, "f1": f1}
```

- [ ] **Step 4: Run -> pass.**
- [ ] **Step 5: Commit** — `feat(er-kg-bench): extraction-F1 metric (Phase 2 scorecard)`.

---

## Task 2: gold subgraph + synthesis-given-gold (wheel-free, stub LLM)

**Files:**
- Modify: `erkgbench/qa_e2e/scorecard_llm.py`
- Test: `tests/test_qa_synthesis_given_gold.py`

- [ ] **Step 1: Write the failing tests**

```python
from __future__ import annotations

from erkgbench.qa_e2e.engineered import generate_engineered
from erkgbench.qa_e2e.gold import GoldGraph, gold_chain
from erkgbench.qa_e2e import ablation
from erkgbench.qa_e2e.scorecard_llm import build_gold_subgraph, synthesis_given_gold


class _StubLLM:
    def __init__(self, response):
        self.response = response
        self.prompts = []

    def complete(self, prompt):
        self.prompts.append(prompt)
        return self.response


def _setup():
    corpus = generate_engineered(seed=7, n_questions=10, ambiguity=0.3, max_hops=4)
    g = GoldGraph.from_corpus(corpus)
    return corpus, g, ablation._typ_of(g)


def test_build_gold_subgraph_carries_the_chain():
    corpus, g, typ_of = _setup()
    qa = corpus.questions[0]
    chain = gold_chain(g, qa)
    sub = build_gold_subgraph(chain, g, typ_of)
    ids = {e["entity_id"] for e in sub["entities"]}
    # every chain entity present; every chain edge present
    for (s, rel, o) in chain:
        assert s in ids and o in ids
        assert any(e["subj"] == s and e["obj"] == o and e["predicate"] == rel for e in sub["edges"])
    # canonical names attached (not bare ids)
    assert all(e["canonical_name"] for e in sub["entities"])


def test_synthesis_given_gold_scores_answer_match():
    corpus, g, typ_of = _setup()
    qa = corpus.questions[0]
    chain = gold_chain(g, qa)
    llm = _StubLLM(f"reasoning...\nAnswer: {qa.gold_answer}")
    score = synthesis_given_gold(qa.question, chain, g, typ_of, qa.gold_answer, llm)
    assert score == 1.0
    # the synthesis prompt was handed the gold chain (a chain entity name appears)
    assert g.canonical_name(chain[-1][2]) in llm.prompts[-1]


def test_synthesis_given_gold_wrong_answer_scores_zero():
    corpus, g, typ_of = _setup()
    qa = corpus.questions[0]
    chain = gold_chain(g, qa)
    llm = _StubLLM("Answer: definitely-not-the-gold-xyzzy")
    assert synthesis_given_gold(qa.question, chain, g, typ_of, qa.gold_answer, llm) == 0.0
```

- [ ] **Step 2: Run -> fail.**

- [ ] **Step 3: Implement** (append to `scorecard_llm.py`):

```python
def build_gold_subgraph(gold_chain, g, typ_of: dict) -> dict:
    """{entities, edges} over the chain's canonical entities -- the shape
    synthesize_local's _format_subgraph reads. entity_id = canonical id."""
    ids: list = []
    for (s, _rel, o) in gold_chain:
        for x in (s, o):
            if x not in ids:
                ids.append(x)
    entities = [
        {"entity_id": x, "canonical_name": g.canonical_name(x), "typ": typ_of.get(x, "concept")}
        for x in ids
    ]
    edges = [{"subj": s, "predicate": rel, "obj": o} for (s, rel, o) in gold_chain]
    return {"entities": entities, "edges": edges}


def synthesis_given_gold(question, gold_chain, g, typ_of, gold_answer, llm) -> float:
    from goldengraph.synthesize import synthesize_local

    sub = build_gold_subgraph(gold_chain, g, typ_of)
    start_name = g.canonical_name(gold_chain[0][0])
    pred = synthesize_local(question, sub, llm, seed_names=[start_name])
    return metrics.answer_match(pred, gold_answer)
```

- [ ] **Step 4: Run -> pass.**
- [ ] **Step 5: Commit** — `feat(er-kg-bench): synthesis-given-gold-subgraph row`.

---

## Task 3: 4-dial answer-match ablation + tracking verdict (e2e wheel; verdict wheel-free)

**Files:**
- Modify: `erkgbench/qa_e2e/scorecard_llm.py`
- Test: `tests/test_qa_answer_match_ablation.py`

- [ ] **Step 1: Write the failing tests**

```python
from __future__ import annotations

import pytest

from erkgbench.qa_e2e.scorecard_llm import tracking_verdict


def test_tracking_verdict_pass_when_orders_match():
    am = {"oracle": 0.9, "goldengraph": 0.6, "name_only": 0.3, "none": 0.2}
    br = {"oracle": 1.0, "goldengraph": 0.55, "name_only": 0.23, "none": 0.23}
    label, passed = tracking_verdict(am, br)
    assert passed is True


def test_tracking_verdict_warn_on_divergence():
    # answer-match HIGH for none but bridge-recall says none is worst -> divergence
    am = {"oracle": 0.5, "goldengraph": 0.5, "name_only": 0.5, "none": 0.9}
    br = {"oracle": 1.0, "goldengraph": 0.55, "name_only": 0.23, "none": 0.23}
    label, passed = tracking_verdict(am, br)
    assert passed is False


def test_answer_match_ablation_e2e():
    pytest.importorskip("goldengraph_native")
    from erkgbench.qa_e2e.engineered import generate_engineered
    from erkgbench.qa_e2e.gold import GoldGraph
    from erkgbench.qa_e2e import ablation
    from erkgbench.qa_e2e.scorecard_llm import answer_match_ablation

    corpus = generate_engineered(seed=7, n_questions=40, ambiguity=0.6, max_hops=4)
    g = GoldGraph.from_corpus(corpus)
    typ_of = ablation._typ_of(g)

    class _FixedLLM:  # deterministic, no network -- always answers "Answer: X"
        def complete(self, prompt):
            return "Answer: X"

    res = answer_match_ablation(corpus, g, typ_of, _FixedLLM())
    # both curves present per dial; bridge-recall mirrors the deterministic ablation
    for d in ("oracle", "goldengraph", "name_only", "none"):
        assert "answer_match" in res[d] and "bridge_recall" in res[d]
    assert res["oracle"]["bridge_recall"]["mean"] >= res["none"]["bridge_recall"]["mean"]
```

- [ ] **Step 2: Run -> fail** (verdict import; e2e skips locally).

- [ ] **Step 3: Implement** (append to `scorecard_llm.py`):

```python
_DIAL_ORDER = ("oracle", "goldengraph", "name_only", "none")


def tracking_verdict(answer_match_by_dial: dict, bridge_recall_by_dial: dict) -> tuple[str, bool]:
    """PASS if the answer-match dial ranking matches the bridge-recall ranking
    (both should descend oracle..none). A faithful proxy tracks."""
    def _rank(d):
        return [k for k in sorted(d, key=lambda k: -d[k])]
    same = _rank(answer_match_by_dial) == _rank(bridge_recall_by_dial)
    return ("answer-match tracks bridge-recall", same)


def answer_match_ablation(corpus, g, typ_of, llm, *, max_hops_unused=None) -> dict:
    """Per dial: reuse ablation._build_store (oracle extraction, dial record_keys),
    oracle-seed + _retrieve_local ball (IDENTICAL to bridge-recall), then real
    synthesize_local over that ball. Returns per-dial answer-match + bridge-recall
    (mean + by_hop)."""
    from goldengraph.answer import _retrieve_local
    from goldengraph.synthesize import synthesize_local

    from . import dials
    from .ablation import _DIALS, _KEYFN, _build_store
    from .engines.goldengraph import _NODE_BUDGET, _RETRIEVAL_HOPS
    from .gold import gold_chain
    from .scorecard import bridge_recall

    chains = {qa.id: gold_chain(g, qa) for qa in corpus.questions}
    out: dict = {}
    for dial in _DIALS:
        km = _KEYFN[dial](corpus, g)
        slice_graph, coverage = _build_store(corpus, g, km, typ_of)
        seed_of: dict = {}
        for nid in sorted(coverage):
            for c in coverage[nid]:
                seed_of.setdefault(c, nid)
        id_to_name = {e["entity_id"]: e["canonical_name"] for e in slice_graph.entities()}

        am, br = [], []
        am_hop, br_hop = {}, {}
        for qa in corpus.questions:
            seed_node = seed_of.get(qa.start_entity_id)
            if seed_node is None:
                a, b = 0.0, 0.0
            else:
                ball = _retrieve_local(slice_graph, [seed_node], max_hops=_RETRIEVAL_HOPS, node_budget=_NODE_BUDGET)
                pred = synthesize_local(qa.question, ball, llm, seed_names=[id_to_name.get(seed_node, "")])
                a = metrics.answer_match(pred, qa.gold_answer)
                b = bridge_recall(chains[qa.id], ball, coverage)["whole_chain"]
            am.append(a); br.append(b)
            am_hop.setdefault(qa.hop_count, []).append(a)
            br_hop.setdefault(qa.hop_count, []).append(b)
        out[dial] = {
            "answer_match": {"mean": sum(am) / len(am) if am else 0.0,
                             "by_hop": {h: sum(v) / len(v) for h, v in sorted(am_hop.items())}},
            "bridge_recall": {"mean": sum(br) / len(br) if br else 0.0,
                              "by_hop": {h: sum(v) / len(v) for h, v in sorted(br_hop.items())}},
        }
    return out
```

> Note: `ablation._DIALS` and `_KEYFN` are module-level in #1274's `ablation.py` — import them (don't re-list the dial names).

- [ ] **Step 4: Run -> verdict tests pass; e2e validates in CI.**
- [ ] **Step 5: Commit** — `feat(er-kg-bench): 4-dial answer-match ablation + tracking verdict`.

---

## Task 4: orchestrator + budget + render (wheel-free except the ablation call)

**Files:**
- Modify: `erkgbench/qa_e2e/scorecard_llm.py`
- Test: `tests/test_qa_scorecard_orchestrator.py`

- [ ] **Step 1: Write the failing tests**

```python
from __future__ import annotations

from erkgbench.qa_e2e.scorecard_llm import (
    ScorecardResult, _BudgetedLLM, render_scorecard_md,
)
from goldenmatch.config.schemas import BudgetConfig
from goldenmatch.core.llm_budget import BudgetTracker


class _CostLLM:
    def complete(self, prompt):
        return "x" * 4000  # big output -> burns budget fast


def test_budgeted_llm_stops_at_cap():
    tracker = BudgetTracker(BudgetConfig(max_cost_usd=0.0))  # zero budget
    llm = _BudgetedLLM(_CostLLM(), tracker, model="gpt-4o-mini")
    # one call records usage; with a zero cap the tracker reports exhausted
    llm.complete("a prompt that costs tokens")
    assert llm.exhausted is True


def test_render_scorecard_md_has_all_three_stages():
    res = ScorecardResult(
        extraction={"entity": {"f1": 0.8}, "relation": {"f1": 0.6}},
        synthesis_ceiling={"mean": 0.9, "by_hop": {2: 0.95, 4: 0.85}},
        answer_match_ablation={
            "oracle": {"answer_match": {"mean": 0.9, "by_hop": {}}, "bridge_recall": {"mean": 1.0, "by_hop": {}}},
            "goldengraph": {"answer_match": {"mean": 0.6, "by_hop": {}}, "bridge_recall": {"mean": 0.55, "by_hop": {}}},
            "name_only": {"answer_match": {"mean": 0.3, "by_hop": {}}, "bridge_recall": {"mean": 0.23, "by_hop": {}}},
            "none": {"answer_match": {"mean": 0.2, "by_hop": {}}, "bridge_recall": {"mean": 0.23, "by_hop": {}}},
        },
        tracking=("answer-match tracks bridge-recall", True),
        budget_exhausted=False,
    )
    md = render_scorecard_md(res)
    assert "extraction" in md.lower() and "entity-F1" in md
    assert "synthesis" in md.lower()
    assert "answer-match" in md.lower() and "bridge-recall" in md.lower()
    assert "PASS" in md or "WARN" in md
```

- [ ] **Step 2: Run -> fail.**

- [ ] **Step 3: Implement** (append to `scorecard_llm.py`):

```python
from dataclasses import dataclass

from .engines.goldengraph import _CountingLLM


@dataclass
class ScorecardResult:
    extraction: dict          # {"entity": f1dict, "relation": f1dict}
    synthesis_ceiling: dict   # {"mean", "by_hop"}
    answer_match_ablation: dict
    tracking: tuple
    budget_exhausted: bool


class _BudgetedLLM:
    """Wrap the real LLM: count tokens (the bench's len//4 estimate) and record each
    call to the BudgetTracker so `exhausted` gates further calls."""

    def __init__(self, inner, tracker, *, model: str = "gpt-4o-mini"):
        self._c = _CountingLLM(inner)
        self._t = tracker
        self._model = model

    @property
    def exhausted(self) -> bool:
        return self._t.budget_exhausted

    def complete(self, prompt: str) -> str:
        bi, bo = self._c.input_tokens, self._c.output_tokens
        out = self._c.complete(prompt)
        self._t.record_usage(self._c.input_tokens - bi, self._c.output_tokens - bo, self._model)
        return out


def render_scorecard_md(res: ScorecardResult) -> str:
    lines = ["# GoldenGraph scorecard -- real-LLM rows (Phase 2)", ""]
    lines += ["## extraction (vs gold triples)",
              f"- entity-F1: {res.extraction['entity']['f1']:.3f}",
              f"- relation-F1: {res.extraction['relation']['f1']:.3f}", ""]
    sc = res.synthesis_ceiling
    lines += ["## synthesis ceiling (answer-match given the GOLD subgraph)",
              f"- mean: {sc['mean']:.3f}"
              + (" | by-hop " + ", ".join(f"{h}:{v:.3f}" for h, v in sc["by_hop"].items())
                 if sc["by_hop"] else ""), ""]
    lines += ["## answer-match ablation (matched to bridge-recall)", "",
              "| dial | answer-match | bridge-recall |", "|---|---|---|"]
    for d in _DIAL_ORDER:
        a = res.answer_match_ablation[d]["answer_match"]["mean"]
        b = res.answer_match_ablation[d]["bridge_recall"]["mean"]
        lines.append(f"| {d} | {a:.3f} | {b:.3f} |")
    label, passed = res.tracking
    lines += ["", f"- [{'PASS' if passed else 'WARN'}] {label}"]
    if res.budget_exhausted:
        lines += ["", "> BUDGET-EXHAUSTED: results are partial."]
    return "\n".join(lines) + "\n"


def run_scorecard(*, seed, n_questions, ambiguity, max_hops, inner_llm, budget_usd) -> ScorecardResult:
    """Orchestrate the three rows under one budget. Each row checks `llm.exhausted`
    before a call and stops cleanly. Needs the wheel for the ablation row."""
    from .ablation import _typ_of
    from .engineered import generate_engineered
    from .gold import GoldGraph, gold_chain

    tracker = BudgetTracker(BudgetConfig(max_cost_usd=budget_usd))
    llm = _BudgetedLLM(inner_llm, tracker)
    corpus = generate_engineered(seed=seed, n_questions=n_questions, ambiguity=ambiguity, max_hops=max_hops)
    g = GoldGraph.from_corpus(corpus)
    typ_of = _typ_of(g)

    # row 1: extraction-F1 (real _extract per edge doc)
    from goldengraph.extract import extract as _extract
    et = {"ent_tp": 0, "ent_fp": 0, "ent_fn": 0, "rel_tp": 0, "rel_fp": 0, "rel_fn": 0}
    for d in corpus.documents:
        if llm.exhausted or len(d.id.split("::")) != 3:
            continue
        ex = _extract(d.text, llm)
        c = extraction_counts(d.src_surface, d.dst_surface, ex)
        for k in et:
            et[k] += c[k]
    extraction = {"entity": f1_from_counts(et["ent_tp"], et["ent_fp"], et["ent_fn"]),
                  "relation": f1_from_counts(et["rel_tp"], et["rel_fp"], et["rel_fn"])}

    # row 2: synthesis-given-gold
    s_all, s_hop = [], {}
    for qa in corpus.questions:
        if llm.exhausted:
            continue
        chain = gold_chain(g, qa)
        sc = synthesis_given_gold(qa.question, chain, g, typ_of, qa.gold_answer, llm)
        s_all.append(sc); s_hop.setdefault(qa.hop_count, []).append(sc)
    synthesis_ceiling = {"mean": sum(s_all) / len(s_all) if s_all else 0.0,
                         "by_hop": {h: sum(v) / len(v) for h, v in sorted(s_hop.items())}}

    # row 3: answer-match ablation (matched). Honors the budget INSIDE via llm.exhausted
    # short-circuit -- synthesize_local calls go through the budgeted llm.
    ama = answer_match_ablation(corpus, g, typ_of, llm)
    am_means = {d: ama[d]["answer_match"]["mean"] for d in ama}
    br_means = {d: ama[d]["bridge_recall"]["mean"] for d in ama}
    return ScorecardResult(
        extraction=extraction,
        synthesis_ceiling=synthesis_ceiling,
        answer_match_ablation=ama,
        tracking=tracking_verdict(am_means, br_means),
        budget_exhausted=tracker.budget_exhausted,
    )
```

> The `answer_match_ablation` synthesize calls must also short-circuit on `llm.exhausted` — add a guard in its per-question loop (`if llm has exhausted attr and llm.exhausted: pred=""`) so a mid-ablation cap stops cleanly. Keep it duck-typed (the e2e test's `_FixedLLM` has no `exhausted` — guard with `getattr(llm, "exhausted", False)`).

- [ ] **Step 4: Run -> pass** (budget + render wheel-free; full `run_scorecard` validates in CI).
- [ ] **Step 5: Commit** — `feat(er-kg-bench): scorecard orchestrator + budget + render`.

---

## Task 5: CLI

**Files:**
- Create: `erkgbench/qa_e2e/run_scorecard.py`

- [ ] **Step 1:** Implement a thin argparse CLI: `--seed/--n-questions/--ambiguity/--max-hops/--budget-usd/--out-md`. Build the real LLM (`from goldengraph.llm import OpenAIClient; OpenAIClient(model="gpt-4o-mini")`), call `run_scorecard(..., inner_llm=client, budget_usd=...)`, write `render_scorecard_md(res)` to `--out-md`, print it. Always exit 0 (non-gating). If no `OPENAI_API_KEY` in env, print a notice + exit 0 (the lane is opt-in).
- [ ] **Step 2:** Smoke: `python -c "import erkgbench.qa_e2e.run_scorecard"` imports clean (no exec). Add `tests/test_qa_scorecard_cli.py` asserting `run_scorecard.main(["--help"])`-style argparse builds (use `parser`-only or `pytest.raises(SystemExit)` on `--help`).
- [ ] **Step 3: Commit** — `feat(er-kg-bench): run_scorecard CLI`.

---

## Task 6: opt-in CI lane

**Files:**
- Modify: `.github/workflows/bench-graphrag-qa.yml`

- [ ] **Step 1:** Add a `scorecard` job (workflow_dispatch only) mirroring the existing goldengraph job's setup (build the `goldengraph_native` wheel via maturin + install goldenmatch + goldengraph; the existing bench-graphrag-qa already does this for the goldengraph engine — copy that setup). Add a `scorecard_budget_usd` input (default e.g. `"2"`). Run `python -m erkgbench.qa_e2e.run_scorecard --seed 7 --n-questions 60 --ambiguity 0.6 --budget-usd "${{ inputs.scorecard_budget_usd }}" --out-md SCORECARD.md` with `OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}`; upload `SCORECARD.md`. Non-gating (the job's success is the CLI exit 0; the metrics are reported, not asserted).
- [ ] **Step 2:** Add the new wheel-free test files to the bench-er-kg.yml pure-Python step (alongside #1274's): `tests/test_qa_extraction_f1.py tests/test_qa_synthesis_given_gold.py tests/test_qa_answer_match_ablation.py tests/test_qa_scorecard_orchestrator.py tests/test_qa_scorecard_cli.py`.
- [ ] **Step 3:** Validate YAML: `python -c "import yaml; yaml.safe_load(open('.github/workflows/bench-graphrag-qa.yml'))"`.
- [ ] **Step 4: Commit** — `ci(bench): opt-in scorecard real-LLM lane`.
- [ ] **Step 5:** Push, open PR. The wheel-free tests gate via bench-er-kg; the real-LLM `scorecard` lane is `workflow_dispatch` (dispatch it manually once merged to produce the first `SCORECARD.md`). Confirm the wheel-free lanes green before arming.

---

## Done criteria

- Wheel-free tests green (extraction-F1, synthesis-given-gold, tracking verdict, budget cap, render, CLI argparse).
- `run_scorecard` produces a `SCORECARD.md` with all three stages + the tracking line (validated by a manual dispatch of the opt-in lane post-merge).
- No hard gate added; #1274's deterministic gate untouched; the lane never blocks merge.

## Not in scope (YAGNI)

musique extraction-F1 (no gold triples), LLM-judge variant, hybrid synthesis, any new corpus, slices B/C/D.
