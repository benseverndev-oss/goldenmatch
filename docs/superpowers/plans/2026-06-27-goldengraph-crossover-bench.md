# GoldenGraph crossover bench (slice C) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the ambiguity x passage_k crossover bench -- a free deterministic recall-crossover CI gate plus an opt-in real-LLM answer-match headline -- that measures where (if anywhere) GoldenGraph overtakes passage-RAG on prose multi-hop QA.

**Architecture:** New `erkgbench/qa_e2e/crossover.py` + `run_crossover.py`. The deterministic core sweeps a 5x4 grid: graph reachability (reuse slice A `ablation` bridge-recall; `passage_k`-invariant) vs a pure-Python lexical passage-recall surface (`passage_k`-decaying). A tiered gate asserts the by-construction mechanism + a retriever-sanity floor + a measurement-frozen crossover cell. The opt-in arm feeds the SAME retrieval to a budgeted LLM for the answer-match crossover table. No dependency on the unmerged #1270 hybrid engine.

**Tech Stack:** Python 3.12, pytest, ruff. Reuses `engineered`, `gold`, `ablation`, `scorecard`, `dials`, `scorecard_llm` in the er-kg-bench package. Graph-reachability path needs the `goldengraph_native` wheel (runs in `goldengraph-pipeline.yml`); everything else is wheel-free.

**Spec:** `docs/superpowers/specs/2026-06-27-goldengraph-crossover-bench-design.md`

---

## Conventions for every task

- Worktree: `D:\show_case\goldenmatch\.worktrees\gg-crossover`, branch `feat/goldengraph-crossover-bench`.
- Bench dir (all relative paths below are under it): `packages/python/goldenmatch/benchmarks/er-kg-bench/`.
- Run tests from the bench dir with `POLARS_SKIP_CPU_CHECK=1` set, e.g.
  `cd packages/python/goldenmatch/benchmarks/er-kg-bench && POLARS_SKIP_CPU_CHECK=1 python -m pytest tests/test_qa_crossover.py -v`.
- The local `.venv` may lack the `goldengraph_native` wheel; the wheel-free tests in this plan run on this box, the graph-reachability path is exercised in CI. Do NOT call `graph_recall_at` in a wheel-free test.
- Keep every commit ruff-clean: `ruff check packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/qa_e2e/crossover.py`. Only add a top-level import in the task that first uses it (avoids transient F401).
- Commit message footer for every commit:
  ```
  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01Sc5aGSaVBTWBWpytNH99QE
  ```

## File structure

- Create `erkgbench/qa_e2e/crossover.py` -- the whole slice (lexical retriever, recall grid, gate, render, opt-in LLM arms). One module, mirrors how `aggregation.py`/`temporal.py` keep a slice self-contained.
- Create `erkgbench/qa_e2e/run_crossover.py` -- CLI (deterministic + `--with-llm`), modeled byte-for-byte on `run_aggregation.py`.
- Create `tests/test_qa_crossover.py` -- wheel-free unit tests (retriever, recall, monotonicity, gate shape).
- Create `tests/test_qa_crossover_llm.py` -- stub-LLM tests for the answer arms.
- Modify `.github/workflows/goldengraph-pipeline.yml` -- add the deterministic crossover gate step + upload.
- Modify `.github/workflows/bench-er-kg.yml` -- add `tests/test_qa_crossover.py` to the wheel-free list.
- Modify `.github/workflows/bench-graphrag-qa.yml` -- add `run_crossover_llm` input + OR it into the `scorecard` job `if:` + a guarded step.

---

## Task 1: Lexical retriever + query terms (wheel-free)

**Files:**
- Create: `erkgbench/qa_e2e/crossover.py`
- Test: `tests/test_qa_crossover.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_qa_crossover.py
"""Slice C crossover bench -- wheel-free unit tests (no goldengraph_native)."""
from __future__ import annotations

from erkgbench.qa_e2e.corpora import Document
from erkgbench.qa_e2e import crossover as cx


def _docs():
    return (
        Document(id="x::works_at::a", text="X works at Apple.", src_surface="X", dst_surface="Apple"),
        Document(id="a::located_in::b", text="Apple located in Cupertino.", src_surface="Apple", dst_surface="Cupertino"),
        Document(id="z::founded::w", text="Zeta founded Widgets.", src_surface="Zeta", dst_surface="Widgets"),
    )


def test_lexical_retrieve_ranks_by_overlap_then_id():
    # query terms hit doc 0 (works, at) and doc 1 (located via none) -> doc0 first
    got = cx.lexical_retrieve(_docs(), ["x", "works", "at"], 2)
    assert got[0] == "x::works_at::a"
    assert len(got) == 2


def test_lexical_retrieve_is_nested_prefix_in_k():
    terms = ["apple", "located", "in"]
    top3 = cx.lexical_retrieve(_docs(), terms, 3)
    top1 = cx.lexical_retrieve(_docs(), terms, 1)
    top2 = cx.lexical_retrieve(_docs(), terms, 2)
    assert top1 == top3[:1]
    assert top2 == top3[:2]


def test_lexical_retrieve_ties_broken_by_doc_id():
    # all-zero-overlap query -> every doc scores 0, order is by id ascending
    got = cx.lexical_retrieve(_docs(), ["nonexistent"], 3)
    assert got == sorted(d.id for d in _docs())[:3]
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd packages/python/goldenmatch/benchmarks/er-kg-bench && POLARS_SKIP_CPU_CHECK=1 python -m pytest tests/test_qa_crossover.py -v`
Expected: FAIL (`ModuleNotFoundError: crossover` / `AttributeError: lexical_retrieve`).

- [ ] **Step 3: Write minimal implementation**

```python
# erkgbench/qa_e2e/crossover.py
"""Slice C: ambiguity x passage_k crossover. A free, deterministic recall-crossover
gate (graph reachability flat in passage_k vs lexical passage-recall decay) plus an
opt-in real-LLM answer-match crossover headline. Self-contained -- no #1270 hybrid dep.

The deterministic recall surfaces + gate + render are wheel-free; only graph_recall_at
(reused slice-A store build) needs the goldengraph_native wheel.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

#: 5 x 4 sweep grid (spec).
AMBIGUITY_GRID = (0.0, 0.25, 0.5, 0.75, 1.0)
PASSAGE_K_GRID = (10, 5, 3, 1)

_TOKEN = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def lexical_retrieve(docs, query_terms, passage_k: int) -> list[str]:
    """Deterministic term-overlap retriever. Rank docs by count of query-term tokens
    present in doc.text; ties broken by doc.id ascending. Returns the top-passage_k ids.
    The rank is a single fixed total order independent of k, so top-k is a nested prefix
    of top-(k+1) -- this is what makes passage-recall monotone in k (gate assertion 2)."""
    qt = set(query_terms)
    scored = []
    for d in docs:
        toks = set(_tokens(d.text))
        overlap = len(qt & toks)
        scored.append((-overlap, d.id))
    scored.sort()
    return [doc_id for _neg, doc_id in scored[:passage_k]]
```

- [ ] **Step 4: Run to verify it passes**

Run: `POLARS_SKIP_CPU_CHECK=1 python -m pytest tests/test_qa_crossover.py -v` (from bench dir)
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add erkgbench/qa_e2e/crossover.py tests/test_qa_crossover.py
git commit -m "feat(er-kg-bench): slice C lexical retriever (deterministic, nested-prefix)"
```

---

## Task 2: query_terms_for + passage_recall (wheel-free)

**Files:**
- Modify: `erkgbench/qa_e2e/crossover.py`
- Test: `tests/test_qa_crossover.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_qa_crossover.py
from erkgbench.qa_e2e.engineered import generate_engineered
from erkgbench.qa_e2e.gold import GoldGraph


def test_query_terms_include_relation_tokens():
    corpus = generate_engineered(seed=7, n_questions=8, ambiguity=0.0, max_hops=3)
    g = GoldGraph.from_corpus(corpus)
    qa = corpus.questions[0]
    terms = cx.query_terms_for(qa, g)
    # every relation in the chain contributes its underscore-split tokens
    for rel in qa.relation_chain:
        for tok in rel.split("_"):
            assert tok.lower() in terms


def test_passage_recall_fraction_of_gold_support():
    # 2 gold support ids, 1 retrieved -> 0.5
    class _QA:
        gold_supporting_fact_ids = ("a::r::b", "b::r::c")
    assert cx.passage_recall(_QA(), ["a::r::b", "zzz"]) == 0.5
    assert cx.passage_recall(_QA(), ["a::r::b", "b::r::c"]) == 1.0
    assert cx.passage_recall(_QA(), []) == 0.0
```

- [ ] **Step 2: Run to verify it fails**

Run: `POLARS_SKIP_CPU_CHECK=1 python -m pytest tests/test_qa_crossover.py -k "query_terms or passage_recall" -v`
Expected: FAIL (`AttributeError: query_terms_for`).

- [ ] **Step 3: Write minimal implementation**

```python
# add to crossover.py (after lexical_retrieve)

def query_terms_for(qa, g) -> list[str]:
    """Tokens a naive retriever would key on: the start-entity surface + the relation
    chain. Intermediate-hop entity surfaces are intentionally absent (the multi-hop RAG
    problem -- later edges retrieved on relation overlap alone)."""
    start_surface = g.canonical_name(qa.start_entity_id)
    terms = list(_tokens(start_surface))
    for rel in qa.relation_chain:
        terms.extend(rel.lower().split("_"))
    return terms


def passage_recall(qa, topk_ids) -> float:
    gold = set(qa.gold_supporting_fact_ids)
    if not gold:
        return 0.0
    return len(set(topk_ids) & gold) / len(gold)
```

NOTE: `GoldGraph.canonical_name(entity_id)` is the real accessor (gold.py; same call used in
`scorecard_llm.py`). It returns the canonical surface and falls back to the id when unknown.
Do NOT guess `canonical_of`/`hasattr` -- a silent fall-through to the raw id (`gm:...`) gives
zero document overlap and would depress `rag@10` (gate assertion 3) and the crossover margin.

- [ ] **Step 4: Run to verify it passes**

Run: `POLARS_SKIP_CPU_CHECK=1 python -m pytest tests/test_qa_crossover.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add erkgbench/qa_e2e/crossover.py tests/test_qa_crossover.py
git commit -m "feat(er-kg-bench): slice C query-terms + passage-recall"
```

---

## Task 3: graph_recall_at (wheel-bearing reuse of slice A)

**Files:**
- Modify: `erkgbench/qa_e2e/crossover.py`

No wheel-free unit test (needs `goldengraph_native`); covered by the in-pipeline gate run.
Keep all wheel imports function-local (mirrors `ablation._build_store`).

- [ ] **Step 1: Write minimal implementation**

```python
# add to crossover.py

def graph_recall_at(corpus, g, *, max_hops: int) -> float:
    """Whole-chain bridge-recall under the goldengraph resolution dial -- the slice-A
    number, used here as the passage_k-INVARIANT graph surface. Needs the wheel."""
    from goldengraph.answer import _retrieve_local

    from .ablation import _KEYFN, _build_store, _typ_of
    from .engines.goldengraph import _NODE_BUDGET, _RETRIEVAL_HOPS
    from .gold import gold_chain
    from .scorecard import bridge_recall

    typ_of = _typ_of(g)
    km = _KEYFN["goldengraph"](corpus, g)
    slice_graph, coverage = _build_store(corpus, g, km, typ_of)

    seed_of: dict[str, int] = {}
    for nid in sorted(coverage):  # ascending id => deterministic tie-break (matches ablation)
        for c in coverage[nid]:
            seed_of.setdefault(c, nid)

    chains = {qa.id: gold_chain(g, qa) for qa in corpus.questions}
    vals: list[float] = []
    for qa in corpus.questions:
        seed_node = seed_of.get(qa.start_entity_id)
        if seed_node is None:
            vals.append(0.0)
            continue
        subgraph = _retrieve_local(
            slice_graph, [seed_node], max_hops=_RETRIEVAL_HOPS, node_budget=_NODE_BUDGET
        )
        vals.append(bridge_recall(chains[qa.id], subgraph, coverage)["whole_chain"])
    return (sum(vals) / len(vals)) if vals else 0.0
```

- [ ] **Step 2: Verify it imports + ruff-clean (no wheel call)**

Run: `POLARS_SKIP_CPU_CHECK=1 python -c "from erkgbench.qa_e2e import crossover; print('ok')"` (from bench dir)
Expected: prints `ok` (module imports; `graph_recall_at` not called so no wheel needed).
Run: `ruff check erkgbench/qa_e2e/crossover.py` -> no errors.

- [ ] **Step 3: Commit**

```bash
git add erkgbench/qa_e2e/crossover.py
git commit -m "feat(er-kg-bench): slice C graph reachability (reuse slice-A bridge-recall)"
```

---

## Task 4: CrossoverResult + grid + tiered gate + render (wheel-free shape)

**Files:**
- Modify: `erkgbench/qa_e2e/crossover.py`
- Test: `tests/test_qa_crossover.py`

The grid runner (`recall_crossover_grid`) calls `graph_recall_at` (wheel), so it is NOT unit
tested wheel-free. The gate + render ARE wheel-free: test them on a hand-built
`CrossoverResult`. Freeze `RAG_HIGH_FLOOR` and `CROSSOVER_MARGIN` AFTER the local measured
run (Task 5 step), with conservative placeholders here.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_qa_crossover.py

def _good_result():
    # graph flat per ambiguity; rag decays with k and starts high at k=10;
    # a crossover cell exists at moderate ambiguity + small k.
    graph = {0.0: 0.95, 0.25: 0.9, 0.5: 0.8, 0.75: 0.6, 1.0: 0.3}
    rag = {
        0.0:  {10: 1.0, 5: 0.9, 3: 0.7, 1: 0.4},
        0.25: {10: 1.0, 5: 0.85, 3: 0.6, 1: 0.3},
        0.5:  {10: 0.98, 5: 0.7, 3: 0.45, 1: 0.2},  # graph 0.8 >> rag 0.2 at k=1 -> crossover
        0.75: {10: 0.97, 5: 0.6, 3: 0.4, 1: 0.15},
        1.0:  {10: 0.95, 5: 0.5, 3: 0.3, 1: 0.1},
    }
    return cx.CrossoverResult(graph=graph, rag=rag)


def test_gate_passes_on_well_formed_surface():
    res = _good_result()
    labels = cx.evaluate_assertions(res)
    hard = [(lbl, ok) for lbl, ok, is_hard in labels if is_hard]
    assert all(ok for _lbl, ok in hard), hard
    assert cx.gate_exit_code(res) == 0


def test_gate_fails_when_rag_non_monotone():
    res = _good_result()
    res.rag[0.5][3] = 0.99  # k=3 recall above k=5 -> non-monotone
    assert cx.gate_exit_code(res) == 1


def test_gate_fails_when_retriever_broken_at_k10():
    res = _good_result()
    for a in res.rag:
        res.rag[a][10] = 0.2  # retriever never starts high -> sanity fails
    assert cx.gate_exit_code(res) == 1


def test_render_md_is_ascii_and_has_grid():
    md = cx.render_crossover_md(_good_result())
    assert md.isascii()
    assert "passage_k" in md and "## verdicts" in md
```

- [ ] **Step 2: Run to verify it fails**

Run: `POLARS_SKIP_CPU_CHECK=1 python -m pytest tests/test_qa_crossover.py -k gate -v`
Expected: FAIL (`AttributeError: CrossoverResult`).

- [ ] **Step 3: Write minimal implementation**

```python
# add to crossover.py

#: Frozen from the local measured grid (Task 5). Placeholders -- TIGHTEN after measuring.
RAG_HIGH_FLOOR = 0.85     # rag_recall at passage_k=10 must be at least this (retriever sane)
CROSSOVER_MARGIN = 0.2    # max over cells of (graph - rag) must reach this (crossover exists)


@dataclass
class CrossoverResult:
    graph: dict  # ambiguity -> recall (passage_k-invariant)
    rag: dict    # ambiguity -> {passage_k -> recall}


def recall_crossover_grid(*, seed: int, n_questions: int, max_hops: int = 4) -> CrossoverResult:
    """The 5x4 deterministic surfaces. NEEDS the wheel (graph_recall_at)."""
    from .engineered import generate_engineered
    from .gold import GoldGraph

    graph: dict = {}
    rag: dict = {}
    for a in AMBIGUITY_GRID:
        corpus = generate_engineered(seed=seed, n_questions=n_questions, ambiguity=a, max_hops=max_hops)
        g = GoldGraph.from_corpus(corpus)
        graph[a] = graph_recall_at(corpus, g, max_hops=max_hops)
        rag[a] = {}
        for k in PASSAGE_K_GRID:
            vals = []
            for qa in corpus.questions:
                if not qa.gold_supporting_fact_ids:
                    continue
                topk = lexical_retrieve(corpus.documents, query_terms_for(qa, g), k)
                vals.append(passage_recall(qa, topk))
            rag[a][k] = (sum(vals) / len(vals)) if vals else 0.0
    return CrossoverResult(graph=graph, rag=rag)


def evaluate_assertions(res: CrossoverResult):
    """[(label, passed, is_hard), ...]. HARD gates; soft only warns."""
    ks_desc = sorted(PASSAGE_K_GRID, reverse=True)  # 10,5,3,1
    kmax, kmin = max(PASSAGE_K_GRID), min(PASSAGE_K_GRID)

    # 1. by-construction: graph is stored per-ambiguity scalar => flat across passage_k.
    graph_flat = all(isinstance(res.graph[a], (int, float)) for a in res.graph)
    # 2. by-construction: RAG monotone non-increasing as passage_k shrinks.
    rag_monotone = all(
        res.rag[a][ks_desc[i]] + 1e-12 >= res.rag[a][ks_desc[i + 1]]
        for a in res.rag
        for i in range(len(ks_desc) - 1)
    )
    # 3. retriever-sanity: RAG starts high at the largest passage_k.
    rag_starts_high = all(res.rag[a][kmax] >= RAG_HIGH_FLOOR for a in res.rag)
    # 4. measurement-frozen: a crossover cell exists somewhere (argmax graph-RAG margin).
    best_margin = max(res.graph[a] - res.rag[a][k] for a in res.rag for k in PASSAGE_K_GRID)
    crossover_exists = best_margin >= CROSSOVER_MARGIN

    return [
        ("graph reachability flat across passage_k (does not read passages)", graph_flat, True),
        ("RAG passage-recall monotone non-increasing as passage_k shrinks", rag_monotone, True),
        (f"RAG passage-recall >= {RAG_HIGH_FLOOR} at passage_k={kmax} (retriever sane)", rag_starts_high, True),
        (f"a crossover cell exists (max graph-RAG margin {best_margin:.3f} >= {CROSSOVER_MARGIN}, k={kmin} most starved)", crossover_exists, True),
    ]


def gate_exit_code(res: CrossoverResult) -> int:
    hard_failed = any(is_hard and not ok for _l, ok, is_hard in evaluate_assertions(res))
    return 1 if hard_failed else 0


def render_crossover_md(res: CrossoverResult) -> str:
    ks = list(PASSAGE_K_GRID)
    lines = [
        "# GoldenGraph crossover -- ambiguity x passage_k (recall, no LLM)",
        "",
        "graph = whole-chain bridge-recall (passage_k-invariant). rag = lexical top-k",
        "passage-recall vs the gold answer-chain docs. Where does graph overtake RAG?",
        "",
        "| ambiguity | graph | " + " | ".join(f"rag@{k}" for k in ks) + " |",
        "|---|---|" + "---|" * len(ks),
    ]
    for a in AMBIGUITY_GRID:
        cells = " | ".join(f"{res.rag[a][k]:.3f}" for k in ks)
        lines.append(f"| {a:.2f} | {res.graph[a]:.3f} | {cells} |")
    lines += ["", "## verdicts", "",
              "(assertion 4 is a measurement-frozen empirical gate, not a structural guarantee)"]
    for label, passed, is_hard in evaluate_assertions(res):
        tag = "PASS" if passed else ("FAIL" if is_hard else "WARN")
        lines.append(f"- [{tag}] {label}{'' if is_hard else ' (soft)'}")
    return "\n".join(lines) + "\n"
```

- [ ] **Step 4: Run to verify it passes**

Run: `POLARS_SKIP_CPU_CHECK=1 python -m pytest tests/test_qa_crossover.py -v`
Expected: PASS (all). Then `ruff check erkgbench/qa_e2e/crossover.py` -> clean.

- [ ] **Step 5: Commit**

```bash
git add erkgbench/qa_e2e/crossover.py tests/test_qa_crossover.py
git commit -m "feat(er-kg-bench): slice C recall grid + tiered gate + render"
```

---

## Task 5: CLI (deterministic) + measure + freeze constants + wire CI gate

**Files:**
- Create: `erkgbench/qa_e2e/run_crossover.py`
- Modify: `erkgbench/qa_e2e/crossover.py` (freeze constants)
- Modify: `.github/workflows/goldengraph-pipeline.yml`
- Modify: `.github/workflows/bench-er-kg.yml`

- [ ] **Step 1: Write the CLI (copy run_aggregation.py shape)**

```python
# erkgbench/qa_e2e/run_crossover.py
"""CLI: deterministic crossover recall bench (graph reachability vs lexical passage
floor) over ambiguity x passage_k; write CROSSOVER.md, exit non-zero on a HARD gate
failure. Key-free; needs the goldengraph_native wheel. --with-llm adds the opt-in
answer-match crossover table (needs OPENAI_API_KEY).

Example:
    python -m erkgbench.qa_e2e.run_crossover --seed 7 --n-questions 80 --out-md CROSSOVER.md
"""
from __future__ import annotations

import argparse
import os
import sys

from .crossover import gate_exit_code, recall_crossover_grid, render_crossover_md


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="GoldenGraph crossover bench")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--n-questions", type=int, default=80)
    p.add_argument("--max-hops", type=int, default=4)
    p.add_argument("--out-md", default="CROSSOVER.md")
    p.add_argument("--with-llm", action="store_true",
                   help="also score the opt-in real-LLM answer-match crossover (needs OPENAI_API_KEY)")
    p.add_argument("--budget-usd", type=float, default=3.0)
    p.add_argument("--llm-out-md", default="CROSSOVER_ANSWER_MATCH.md")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    res = recall_crossover_grid(seed=args.seed, n_questions=args.n_questions, max_hops=args.max_hops)
    md = render_crossover_md(res)
    with open(args.out_md, "w", encoding="utf-8") as fh:
        fh.write(md)
    sys.stdout.write(md)

    if args.with_llm and os.environ.get("OPENAI_API_KEY"):
        from goldengraph.llm import OpenAIClient
        from goldenmatch.config.schemas import BudgetConfig
        from goldenmatch.core.llm_budget import BudgetTracker

        from .crossover import answer_match_grid, render_answer_match_md
        from .scorecard_llm import _BudgetedLLM

        llm = _BudgetedLLM(
            OpenAIClient(model="gpt-4o-mini"),
            BudgetTracker(BudgetConfig(max_cost_usd=args.budget_usd)),
        )
        am = answer_match_grid(seed=args.seed, n_questions=args.n_questions,
                               max_hops=args.max_hops, llm=llm)
        am_md = render_answer_match_md(am)
        with open(args.llm_out_md, "w", encoding="utf-8") as fh:
            fh.write(am_md)
        sys.stdout.write(am_md)

    return gate_exit_code(res)  # gate is recall-only; answer-match is ungated


if __name__ == "__main__":
    raise SystemExit(main())
```

NOTE: the `--with-llm` branch imports `answer_match_grid`/`render_answer_match_md`, which land
in Task 7. Until then `--with-llm` is unused in CI (the gate step does not pass it), so the CLI
imports stay lazy/inside the branch and ruff stays clean. If you run Task 5 before Task 7,
do NOT invoke `--with-llm`.

- [ ] **Step 2: MEASURE on the real corpus (the verification-before-gating step)**

This requires the wheel. If the local `.venv` has `goldengraph_native`, run:
`POLARS_SKIP_CPU_CHECK=1 python -m erkgbench.qa_e2e.run_crossover --seed 7 --n-questions 80 --out-md /tmp/CROSSOVER.md`
If the wheel is NOT available locally, push the branch and read `CROSSOVER.md` from the
`goldengraph-pipeline` run artifact (the step is added below; the gate constants can be
loosened first, the run observed, then tightened in a follow-up commit).

Read the printed grid. Freeze in `crossover.py`:
- `RAG_HIGH_FLOOR` = a value just below the measured `rag@10` minimum across ambiguity rows
  (e.g. if the min `rag@10` is 0.93, set 0.90). This proves the retriever isn't broken.
  CAUTION: `query_terms_for` deliberately omits intermediate-hop surfaces (the multi-hop RAG
  problem) and there are only ~5 relation types, so `rag@10` may measure WELL below 1.0. If so
  that is the honest multi-hop-RAG limitation -- set a LOW floor that the measured min clears;
  do NOT "fix" the retriever to force a high floor (that would defeat the slice's point).
- `CROSSOVER_MARGIN` = a value just below the measured max `(graph - rag)` over all cells
  (e.g. if the argmax margin is 0.34, set 0.25). Leave headroom so the seeded run is not
  borderline. If NO cell shows graph >= rag (margin <= 0), the deterministic crossover does
  not exist on this corpus -- STOP and surface to Ben: the gate's assertion 4 cannot hold and
  the slice's framing (recall crossover) must be revisited before shipping.
- Keep the two literals compatible with the hand-built `_good_result()` fixture in
  `test_qa_crossover.py` (min `rag@10` = 0.95, max margin ~0.6): set `RAG_HIGH_FLOOR <= 0.95`
  and `CROSSOVER_MARGIN <= 0.6`, or update the fixture numbers in the SAME commit so
  `test_gate_passes_on_well_formed_surface` stays green.

- [ ] **Step 3: Edit the frozen constants + commit**

Edit the two literals in `crossover.py` to the measured-and-headroomed values. Re-run the
wheel-free gate-shape tests (they use a hand-built result, so they stay green regardless):
`POLARS_SKIP_CPU_CHECK=1 python -m pytest tests/test_qa_crossover.py -v` -> PASS.

```bash
git add erkgbench/qa_e2e/run_crossover.py erkgbench/qa_e2e/crossover.py
git commit -m "feat(er-kg-bench): slice C deterministic CLI + freeze gate constants from measured grid"
```

- [ ] **Step 4: Wire the pipeline gate step**

In `.github/workflows/goldengraph-pipeline.yml`, after the **"Upload AGGREGATION.md"** step
(the last step in the `pipeline` job; the B2 Temporal slice is NOT on main in this worktree,
so anchor to the aggregation gate, not temporal), add:

```yaml
      - name: Crossover capability gate (deterministic, key-free)
        # Slice C: where (if anywhere) the KG overtakes passage-RAG under retrieval
        # starvation. graph reachability is passage_k-invariant; a lexical passage floor
        # decays as passage_k shrinks. Gates HARD on graph-flat + RAG-monotone +
        # RAG-starts-high + a measurement-frozen crossover cell. No key.
        working-directory: packages/python/goldenmatch/benchmarks/er-kg-bench
        env:
          POLARS_SKIP_CPU_CHECK: "1"
        run: |
          python -m pytest tests/test_qa_crossover.py -v
          python -m erkgbench.qa_e2e.run_crossover \
            --seed 7 --n-questions 80 --out-md CROSSOVER.md

      - name: Upload CROSSOVER.md
        if: ${{ always() }}
        uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a  # v4
        with:
          name: goldengraph-crossover
          path: packages/python/goldenmatch/benchmarks/er-kg-bench/CROSSOVER.md
          if-no-files-found: ignore
```

- [ ] **Step 5: Add the wheel-free test to bench-er-kg.yml**

In `.github/workflows/bench-er-kg.yml`, find the pure-Python test list (it already names
`tests/test_qa_ablation.py` etc.) and add `tests/test_qa_crossover.py` to it.

- [ ] **Step 6: Commit the CI wiring**

```bash
git add .github/workflows/goldengraph-pipeline.yml .github/workflows/bench-er-kg.yml
git commit -m "ci(er-kg-bench): wire slice C deterministic crossover gate"
```

---

## Task 6: Opt-in LLM answer arms (stub-LLM tested)

**Files:**
- Modify: `erkgbench/qa_e2e/crossover.py`
- Test: `tests/test_qa_crossover_llm.py`

Reuse the synthesis/answer-mapping pattern from `scorecard_llm.py` (read it first:
`synthesis_given_gold`, `answer_match_ablation`). The gold answer is the final dst canonical:
`gold_chain(g, qa)[-1][2]`. Map the model's free-text answer to a canonical via
`dials.surface_to_canon(g)` (surface -> set(canonical)); a non-mapping answer scores None.

- [ ] **Step 1: Write the failing stub-LLM test**

```python
# tests/test_qa_crossover_llm.py
"""Opt-in real-LLM answer arms -- stub-LLM, wheel-free."""
from __future__ import annotations

from erkgbench.qa_e2e import crossover as cx


class _StubLLM:
    def __init__(self, response):
        self.response = response
        self.prompts = []

    def complete(self, prompt):
        self.prompts.append(prompt)
        return self.response


def test_llm_answer_rag_maps_text_to_canonical():
    passages = ["X works at Apple.", "Apple is in Cupertino."]
    s2c = {"Apple": {"a"}, "Cupertino": {"c"}, "X": {"x"}}
    llm = _StubLLM("The answer is Cupertino.")
    got = cx.llm_answer_rag(passages, "where is X located?", llm, surface_to_canon=s2c)
    assert got == "c"
    assert "Apple is in Cupertino." in llm.prompts[-1]


def test_llm_answer_unknown_is_none():
    llm = _StubLLM("Some Bogus Entity")
    got = cx.llm_answer_rag(["irrelevant"], "q?", llm, surface_to_canon={"Apple": {"a"}})
    assert got is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `POLARS_SKIP_CPU_CHECK=1 python -m pytest tests/test_qa_crossover_llm.py -v`
Expected: FAIL (`AttributeError: llm_answer_rag`).

- [ ] **Step 3: Write minimal implementation**

```python
# add to crossover.py

def _map_answer_to_canon(text: str, surface_to_canon: dict) -> str | None:
    """Longest-surface-first match of any known surface appearing in the model's text;
    return one canonical id (sorted for determinism) or None."""
    low = text.lower()
    for surf in sorted(surface_to_canon, key=len, reverse=True):
        if surf.lower() in low:
            cands = surface_to_canon[surf]
            return sorted(cands)[0] if cands else None
    return None


def llm_answer_rag(passages, question: str, llm, *, surface_to_canon: dict) -> str | None:
    """RAG arm: answer the question from the retrieved passages; map to a canonical id."""
    ctx = "\n".join(f"- {p}" for p in passages)
    prompt = (
        "Answer the question using ONLY these passages. Reply with the entity name only.\n\n"
        f"Passages:\n{ctx}\n\nQuestion: {question}\nAnswer:"
    )
    out = llm.complete(prompt) or ""
    return _map_answer_to_canon(out, surface_to_canon)


def llm_answer_graph(triples, question: str, llm, *, surface_to_canon: dict) -> str | None:
    """Graph arm: answer the question from resolved-subgraph triples; map to a canonical."""
    ctx = "\n".join(f"- {s} {p} {o}" for (s, p, o) in triples)
    prompt = (
        "Answer the question using ONLY these facts. Reply with the entity name only.\n\n"
        f"Facts:\n{ctx}\n\nQuestion: {question}\nAnswer:"
    )
    out = llm.complete(prompt) or ""
    return _map_answer_to_canon(out, surface_to_canon)
```

- [ ] **Step 4: Run to verify it passes**

Run: `POLARS_SKIP_CPU_CHECK=1 python -m pytest tests/test_qa_crossover_llm.py -v`
Expected: PASS. `ruff check erkgbench/qa_e2e/crossover.py` -> clean.

- [ ] **Step 5: Commit**

```bash
git add erkgbench/qa_e2e/crossover.py tests/test_qa_crossover_llm.py
git commit -m "feat(er-kg-bench): slice C opt-in LLM answer arms (RAG + graph)"
```

---

## Task 7: answer_match_grid + render + bench-graphrag-qa wiring

**Files:**
- Modify: `erkgbench/qa_e2e/crossover.py`
- Modify: `.github/workflows/bench-graphrag-qa.yml`
- Test: `tests/test_qa_crossover_llm.py`

`answer_match_grid` sweeps the same 5x4 grid: per cell, build the lexical top-k passages
(RAG arm) and the resolved-subgraph triples (graph arm; needs the wheel), call the budgeted
LLM, and compare each arm's mapped canonical to the gold answer. Honor `llm.exhausted` like
`scorecard_llm.run_scorecard` (short-circuit remaining cells). It NEEDS the wheel (graph arm),
so it is not unit-tested wheel-free; the stub test below exercises the pure answer-match
counting helper.

- [ ] **Step 1: Write the failing test (pure counting helper)**

```python
# add to tests/test_qa_crossover_llm.py

def test_answer_match_counts_hits():
    # predicted vs gold per question -> accuracy
    preds = ["a", "b", None, "d"]
    gold = ["a", "x", "c", "d"]
    assert cx.answer_match_accuracy(preds, gold) == 0.5  # a,d hit; b,None miss
```

- [ ] **Step 2: Run to verify it fails**

Run: `POLARS_SKIP_CPU_CHECK=1 python -m pytest tests/test_qa_crossover_llm.py -k answer_match_counts -v`
Expected: FAIL (`AttributeError: answer_match_accuracy`).

- [ ] **Step 3: Write minimal implementation**

```python
# add to crossover.py

@dataclass
class AnswerMatchResult:
    graph: dict  # ambiguity -> {passage_k -> accuracy}
    rag: dict    # ambiguity -> {passage_k -> accuracy}
    budget_exhausted: bool


def answer_match_accuracy(preds, gold) -> float:
    if not gold:
        return 0.0
    hits = sum(1 for p, gd in zip(preds, gold) if p is not None and p == gd)
    return hits / len(gold)


def answer_match_grid(*, seed: int, n_questions: int, max_hops: int, llm) -> AnswerMatchResult:
    """Opt-in real-LLM answer-match crossover. NEEDS the wheel (graph arm). Honors
    llm.exhausted (duck-typed; short-circuits remaining cells)."""
    from goldengraph.answer import _retrieve_local

    from . import dials
    from .ablation import _KEYFN, _build_store, _typ_of
    from .engineered import generate_engineered
    from .engines.goldengraph import _NODE_BUDGET, _RETRIEVAL_HOPS
    from .gold import GoldGraph, gold_chain

    graph: dict = {}
    rag: dict = {}
    exhausted = False
    for a in AMBIGUITY_GRID:
        corpus = generate_engineered(seed=seed, n_questions=n_questions, ambiguity=a, max_hops=max_hops)
        g = GoldGraph.from_corpus(corpus)
        s2c = dials.surface_to_canon(g)
        typ_of = _typ_of(g)
        km = _KEYFN["goldengraph"](corpus, g)
        slice_graph, coverage = _build_store(corpus, g, km, typ_of)
        seed_of: dict[str, int] = {}
        for nid in sorted(coverage):
            for c in coverage[nid]:
                seed_of.setdefault(c, nid)
        gold = {qa.id: (gold_chain(g, qa)[-1][2] if gold_chain(g, qa) else None) for qa in corpus.questions}
        questions = {qa.id: _question_text(qa, g) for qa in corpus.questions}

        graph[a], rag[a] = {}, {}
        for k in PASSAGE_K_GRID:
            rag_preds, graph_preds, golds = [], [], []
            for qa in corpus.questions:
                if getattr(llm, "exhausted", False):
                    exhausted = True
                    break
                golds.append(gold[qa.id])
                # RAG arm
                topk = lexical_retrieve(corpus.documents, query_terms_for(qa, g), k)
                texts = {d.id: d.text for d in corpus.documents}
                rag_preds.append(llm_answer_rag([texts[i] for i in topk], questions[qa.id], llm, surface_to_canon=s2c))
                # graph arm
                sn = seed_of.get(qa.start_entity_id)
                if sn is None:
                    graph_preds.append(None)
                else:
                    sub = _retrieve_local(slice_graph, [sn], max_hops=_RETRIEVAL_HOPS, node_budget=_NODE_BUDGET)
                    triples = [(e["subj"], e.get("predicate", ""), e["obj"]) for e in sub.get("edges", ())]
                    graph_preds.append(llm_answer_graph(triples, questions[qa.id], llm, surface_to_canon=s2c))
            rag[a][k] = answer_match_accuracy(rag_preds, golds[:len(rag_preds)])
            graph[a][k] = answer_match_accuracy(graph_preds, golds[:len(graph_preds)])
            if exhausted:
                break
        if exhausted:
            break
    return AnswerMatchResult(graph=graph, rag=rag, budget_exhausted=exhausted)


def _question_text(qa, g) -> str:
    start = g.canonical_name(qa.start_entity_id)
    chain = " then ".join(qa.relation_chain)
    return f"Starting from {start}, follow {chain}. What is the final entity?"


def render_answer_match_md(res: AnswerMatchResult) -> str:
    ks = list(PASSAGE_K_GRID)
    lines = [
        "# GoldenGraph crossover -- answer-match (real LLM, opt-in, UNGATED)",
        "",
        "Does the recall crossover flow to answers? graph arm = LLM over resolved subgraph;",
        "rag arm = LLM over top-k lexical passages. A negative (graph never overtakes RAG)",
        "is a valid finding, not a failure.",
        "",
        f"budget_exhausted: {res.budget_exhausted}",
        "",
    ]
    for arm, tbl in (("graph", res.graph), ("rag", res.rag)):
        lines += [f"## {arm} answer-match", "",
                  "| ambiguity | " + " | ".join(f"k={k}" for k in ks) + " |",
                  "|---|" + "---|" * len(ks)]
        for a in AMBIGUITY_GRID:
            if a not in tbl:
                continue
            cells = " | ".join(f"{tbl[a].get(k, float('nan')):.3f}" for k in ks)
            lines.append(f"| {a:.2f} | {cells} |")
        lines.append("")
    return "\n".join(lines) + "\n"
```

- [ ] **Step 4: Run to verify it passes**

Run: `POLARS_SKIP_CPU_CHECK=1 python -m pytest tests/test_qa_crossover_llm.py -v`
Expected: PASS. `ruff check erkgbench/qa_e2e/crossover.py erkgbench/qa_e2e/run_crossover.py` -> clean.

- [ ] **Step 5: Wire bench-graphrag-qa.yml**

In `.github/workflows/bench-graphrag-qa.yml` (B2/temporal is NOT on main here -- mirror the B1
`run_aggregation_llm` patterns, not temporal):
1. Under `workflow_dispatch.inputs`, add (mirror the `run_aggregation_llm` input block):
   ```yaml
   run_crossover_llm:
     description: "Slice C: real-LLM answer-match crossover (ambiguity x passage_k)"
     type: boolean
     default: false
   ```
2. In the `scorecard` job's `if:`, append the new clause to the EXACT existing two-clause
   expression. The current line is
   `if: ${{ inputs.run_scorecard == 'true' || inputs.run_aggregation_llm == 'true' }}`;
   change it to
   `if: ${{ inputs.run_scorecard == 'true' || inputs.run_aggregation_llm == 'true' || inputs.run_crossover_llm == 'true' }}`.
3. Add a guarded step + upload (mirror the existing "real-LLM RAG aggregation floor" step +
   its upload), non-gating:
   ```yaml
       - name: Slice C answer-match crossover (real LLM)
         if: ${{ inputs.run_crossover_llm == 'true' }}
         working-directory: packages/python/goldenmatch/benchmarks/er-kg-bench
         env:
           OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
           POLARS_SKIP_CPU_CHECK: "1"
         run: |
           python -m erkgbench.qa_e2e.run_crossover --seed 7 --n-questions 80 \
             --with-llm --budget-usd 3 \
             --out-md CROSSOVER.md --llm-out-md CROSSOVER_ANSWER_MATCH.md || true
       - name: Upload CROSSOVER_ANSWER_MATCH.md
         if: ${{ always() && inputs.run_crossover_llm == 'true' }}
         uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a  # v4
         with:
           name: goldengraph-crossover-answer-match
           path: packages/python/goldenmatch/benchmarks/er-kg-bench/CROSSOVER_ANSWER_MATCH.md
           if-no-files-found: ignore
   ```

- [ ] **Step 6: Commit**

```bash
git add erkgbench/qa_e2e/crossover.py tests/test_qa_crossover_llm.py .github/workflows/bench-graphrag-qa.yml
git commit -m "feat(er-kg-bench): slice C answer-match grid + opt-in bench-graphrag-qa lane"
```

---

## Final verification (before finishing the branch)

- [ ] `cd packages/python/goldenmatch/benchmarks/er-kg-bench && POLARS_SKIP_CPU_CHECK=1 python -m pytest tests/test_qa_crossover.py tests/test_qa_crossover_llm.py -v` -> all PASS.
- [ ] `ruff check erkgbench/qa_e2e/crossover.py erkgbench/qa_e2e/run_crossover.py` -> clean.
- [ ] `python -c "import yaml,sys; [yaml.safe_load(open(f)) for f in ['.github/workflows/goldengraph-pipeline.yml','.github/workflows/bench-er-kg.yml','.github/workflows/bench-graphrag-qa.yml']]; print('yaml ok')"` -> `yaml ok`.
- [ ] Use superpowers:finishing-a-development-branch: push, open PR vs `main`, watch the `goldengraph-pipeline` lane (the only real-execution gate) go green BEFORE arming `gh pr merge <N> --auto`, then record to memory `project_goldengraph_kg_engine.md`.
- [ ] If the measured `goldengraph-pipeline` `CROSSOVER.md` shows the gate constants are borderline or no crossover cell exists, surface to Ben (per Task 5 step 2) rather than loosening the gate to pass.

## Known unknowns to resolve during implementation (call out, don't guess)

- `GoldGraph` canonical-surface accessor: RESOLVED -- it is `g.canonical_name(entity_id)`
  (gold.py; also used in `scorecard_llm.py`). The plan uses it directly; no `hasattr` guess.
- `_retrieve_local` subgraph edge dict keys (`subj`/`obj`/`predicate`): confirmed used by
  `ablation.run_ablation` + `scorecard.bridge_recall` (`e["subj"]`, `e["obj"]`); `predicate`
  may be absent on some edges -> `.get("predicate", "")` already guards it.
- Whether `corpus.questions` / `corpus.documents` are the exact attribute names (confirmed in
  `ablation._build_store` which iterates `corpus.documents`, and `run_ablation` which iterates
  `corpus.questions`).
