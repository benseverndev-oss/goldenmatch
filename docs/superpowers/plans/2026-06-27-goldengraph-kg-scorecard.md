# GoldenGraph KG-vs-KG capability scorecard (slice D) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the KG-vs-KG capability scorecard: a free deterministic gate that runs each KG framework's ER strategy (as a record_key dial) through bridge-recall + aggregation set-F1 to prove goldengraph's fuzzy ER beats the exact-match frameworks, plus an opt-in real-framework confirmation lane.

**Architecture:** New `erkgbench/qa_e2e/kg_scorecard.py` + `run_kg_scorecard.py`. The deterministic core sweeps four ER tiers (`oracle / goldengraph-fuzzy / exact_match / none`, mapping labels to EXISTING `dials.py` keyfns -- NO new dial) through two ER-driven capability metrics, both built via `ablation._build_store` with only the dial's `km` changing. A measurement-frozen gate asserts goldengraph beats the `exact_match` tier (LightRAG/MS-GraphRAG, which coincide on this single-entity-type corpus) by a margin on every metric. The opt-in lane drives the real `engines/` adapters on the aggregation corpus.

**Tech Stack:** Python 3.12, pytest, ruff. Reuses `ablation`, `aggregation`, `dials`, `gold`, `engineered`, `engines/`, `scorecard_llm._BudgetedLLM` in er-kg-bench. Graph metrics need the `goldengraph_native` wheel (run in `goldengraph-pipeline.yml`); gate-shape + parser are wheel-free.

**Spec:** `docs/superpowers/specs/2026-06-27-goldengraph-kg-scorecard-design.md`

---

## Conventions for every task

- Worktree: `D:\show_case\gg-kg-scorecard`, branch `feat/goldengraph-kg-scorecard`.
- Bench dir (all relative paths below are under it): `packages/python/goldenmatch/benchmarks/er-kg-bench/`.
- Run tests from the bench dir via the main `.venv` python + PYTHONPATH (the local `.venv` has no
  `goldengraph_native` wheel, so wheel-free tests run here; graph metrics run in CI):
  `PYEXE="D:/show_case/goldenmatch/.venv/Scripts/python.exe"` then
  `cd packages/python/goldenmatch/benchmarks/er-kg-bench && POLARS_SKIP_CPU_CHECK=1 PYTHONPATH="$(pwd)" "$PYEXE" -m pytest tests/test_qa_kg_scorecard.py -v`.
- Do NOT call any function that reaches `ablation._build_store` in a wheel-free test (needs the wheel).
- Keep every commit ruff-clean: `"$PYEXE" -m ruff check erkgbench/qa_e2e/kg_scorecard.py`. Only add a
  top-level import in the task that first uses it (avoids transient F401; add `from dataclasses import
  dataclass` only when `@dataclass` lands in Task 2).
- Commit message footer for every commit:
  ```
  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01Sc5aGSaVBTWBWpytNH99QE
  ```

## File structure

- Create `erkgbench/qa_e2e/kg_scorecard.py` -- the whole slice (parser, per-dial helpers, scorecard
  orchestrator, gate, render, opt-in framework lane).
- Create `erkgbench/qa_e2e/run_kg_scorecard.py` -- CLI (deterministic + `--with-frameworks`), modeled
  on `run_aggregation.py`.
- Create `tests/test_qa_kg_scorecard.py` -- wheel-free (parser, gate shape).
- Modify `.github/workflows/goldengraph-pipeline.yml` -- scorecard gate step + upload (anchor after the
  "Upload AGGREGATION.md" step).
- Modify `.github/workflows/bench-er-kg.yml` -- add the wheel-free test to the pure-Python list.
- Modify `.github/workflows/bench-graphrag-qa.yml` -- `run_kg_capability` input + opt-in step.

---

## Task 1: Answer -> entity-set parser (wheel-free)

**Files:**
- Create: `erkgbench/qa_e2e/kg_scorecard.py`
- Test: `tests/test_qa_kg_scorecard.py`

The opt-in real lane asks frameworks "List all entities that X <rel>" and must parse the free-text
answer into a set of canonical ids. Maps surfaces to canonicals with a FIRST-WINS scalar map
`s2c: dict[str, str]` (the same shape the aggregation floor builds), NOT `dials.surface_to_canon`
(set-valued).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_qa_kg_scorecard.py
"""Slice D KG-vs-KG scorecard -- wheel-free unit tests (no goldengraph_native)."""
from __future__ import annotations

from erkgbench.qa_e2e import kg_scorecard as ks


def test_parse_entity_set_finds_all_known_surfaces():
    s2c = {"Apple": "a", "Cupertino": "c", "Widgets": "w"}
    answer = "Apple, Cupertino and also Widgets."
    assert ks.parse_entity_set(answer, s2c) == {"a", "c", "w"}


def test_parse_entity_set_ignores_unknown_and_dedups():
    s2c = {"Apple": "a"}
    assert ks.parse_entity_set("Apple Apple Bogus", s2c) == {"a"}


def test_parse_entity_set_empty_on_no_match():
    assert ks.parse_entity_set("nothing here", {"Apple": "a"}) == set()
```

- [ ] **Step 2: Run to verify it fails**

Run: `POLARS_SKIP_CPU_CHECK=1 PYTHONPATH="$(pwd)" "$PYEXE" -m pytest tests/test_qa_kg_scorecard.py -v`
Expected: FAIL (`ModuleNotFoundError: kg_scorecard` / `AttributeError: parse_entity_set`).

- [ ] **Step 3: Write minimal implementation**

```python
# erkgbench/qa_e2e/kg_scorecard.py
"""Slice D: KG-vs-KG capability scorecard. Model each KG framework's documented ER strategy as
a record_key dial and run it through two ER-driven capability metrics (bridge-recall from slice A,
aggregation set-F1 from slice B1). Gates that goldengraph's fuzzy ER beats the exact-match
frameworks (LightRAG/MS-GraphRAG, which coincide on this single-entity-type corpus). Plus an
opt-in real-framework confirmation lane.

NO new dial: the scorecard maps framework labels to EXISTING dials.py keyfns. The deterministic
metrics + gate + render are wheel-free EXCEPT the per-dial graph helpers (they reach
ablation._build_store). The answer->set parser + gate shape are wheel-free.
"""
from __future__ import annotations


def parse_entity_set(answer: str, s2c: dict) -> set:
    """Scan the framework's free-text answer for known surfaces; return the set of canonical ids.
    `s2c` is a FIRST-WINS scalar surface->canonical map (matches set_f1's scalar gold members)."""
    low = answer.lower()
    out: set = set()
    for surf, canon in s2c.items():
        if surf.lower() in low:
            out.add(canon)
    return out
```

- [ ] **Step 4: Run to verify it passes**

Run: `POLARS_SKIP_CPU_CHECK=1 PYTHONPATH="$(pwd)" "$PYEXE" -m pytest tests/test_qa_kg_scorecard.py -v`
Expected: PASS (3 passed). Then `"$PYEXE" -m ruff check erkgbench/qa_e2e/kg_scorecard.py` -> clean.

- [ ] **Step 5: Commit**

```bash
git add erkgbench/qa_e2e/kg_scorecard.py tests/test_qa_kg_scorecard.py
git commit -m "feat(er-kg-bench): slice D answer->entity-set parser"
```

---

## Task 2: ScorecardResult + tiered gate + render (wheel-free shape)

**Files:**
- Modify: `erkgbench/qa_e2e/kg_scorecard.py`
- Test: `tests/test_qa_kg_scorecard.py`

The gate runs on a hand-built `ScorecardResult` (no wheel). Freeze `MOAT_MARGIN`/`MONO_TOL`/`EPS`
AFTER the measured run (Task 4); conservative placeholders here.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_qa_kg_scorecard.py

def _good_result():
    # oracle >= goldengraph >= exact_match >= none per metric; goldengraph beats exact by a
    # margin on both; exact_match ~= none on bridge-recall (the slice-A name_only==none finding).
    bridge = {"oracle": 1.0, "goldengraph": 0.558, "exact_match": 0.234, "none": 0.234}
    aggf1 = {"oracle": 1.0, "goldengraph": 1.0, "exact_match": 0.45, "none": 0.10}
    return ks.ScorecardResult(bridge_recall=bridge, aggregation_f1=aggf1)


def test_gate_passes_on_well_formed_scorecard():
    res = _good_result()
    hard = [(lbl, ok) for lbl, ok, is_hard in ks.evaluate_assertions(res) if is_hard]
    assert all(ok for _lbl, ok in hard), hard
    assert ks.gate_exit_code(res) == 0


def test_gate_fails_when_no_moat():
    res = _good_result()
    res.aggregation_f1["exact_match"] = 0.99  # goldengraph 1.0 - 0.99 < MOAT_MARGIN
    assert ks.gate_exit_code(res) == 1


def test_gate_fails_when_monotonicity_violated():
    res = _good_result()
    res.bridge_recall["exact_match"] = 0.80  # now > goldengraph 0.558
    assert ks.gate_exit_code(res) == 1


def test_gate_fails_when_exact_beats_none_on_bridge():
    res = _good_result()
    res.bridge_recall["none"] = 0.05  # exact 0.234 >> none + EPS -> exact not inert
    assert ks.gate_exit_code(res) == 1


def test_render_md_is_ascii_and_has_both_capabilities():
    md = ks.render_scorecard_md(_good_result())
    assert md.isascii()
    assert "bridge_recall" in md and "aggregation" in md and "## verdicts" in md
```

- [ ] **Step 2: Run to verify it fails**

Run: `POLARS_SKIP_CPU_CHECK=1 PYTHONPATH="$(pwd)" "$PYEXE" -m pytest tests/test_qa_kg_scorecard.py -k gate -v`
Expected: FAIL (`AttributeError: ScorecardResult`).

- [ ] **Step 3: Write minimal implementation**

Add `from dataclasses import dataclass` at the top (first use lands now), then:

```python
# add to kg_scorecard.py

#: best -> worst ER (by merge-recall). Labels map to EXISTING dials.py keyfns in Task 3.
DIAL_TIERS = ("oracle", "goldengraph", "exact_match", "none")

#: Frozen from the measured grid (Task 4). Placeholders -- TIGHTEN after measuring.
MOAT_MARGIN = 0.15   # goldengraph - exact_match must be >= this on EVERY capability
MONO_TOL = 1e-9      # tolerance for the oracle>=goldengraph>=exact_match>=none chain
EPS = 0.02           # exact_match <= none + EPS on bridge-recall (exact-match ~= no-merge)


@dataclass
class ScorecardResult:
    bridge_recall: dict   # dial -> mean whole-chain bridge-recall
    aggregation_f1: dict  # dial -> mean set-F1


def evaluate_assertions(res: ScorecardResult):
    """[(label, passed, is_hard), ...]. HARD gates; soft only warns.

    The exact_match column models the LightRAG/MS-GraphRAG ER strategy (exact-surface merge) as a
    record_key policy, NOT the full framework runtime; the real-framework confirmation is the
    opt-in lane. Claim: a store built under that ER strategy loses the capability."""
    metrics = {"bridge_recall": res.bridge_recall, "aggregation_f1": res.aggregation_f1}

    # 1. HEADLINE: fuzzy ER beats the exact-match tier on EVERY capability.
    worst_moat = min(m["goldengraph"] - m["exact_match"] for m in metrics.values())
    moat = worst_moat >= MOAT_MARGIN
    # 2. ER-quality monotonicity (merge-recall direction) per metric.
    mono = all(
        m["oracle"] + MONO_TOL >= m["goldengraph"]
        and m["goldengraph"] + MONO_TOL >= m["exact_match"]
        and m["exact_match"] + MONO_TOL >= m["none"]
        for m in metrics.values()
    )
    # 3. exact-match ER ~= no-merge on reachability (the slice-A name_only==none finding).
    exact_inert = res.bridge_recall["exact_match"] <= res.bridge_recall["none"] + EPS

    return [
        (f"goldengraph beats exact-match on every capability (worst moat {worst_moat:.3f} >= {MOAT_MARGIN})", moat, True),
        ("ER-quality monotonic per metric (oracle>=goldengraph>=exact_match>=none)", mono, True),
        (f"exact-match ~= no-merge on bridge-recall (exact <= none + {EPS})", exact_inert, True),
    ]


def gate_exit_code(res: ScorecardResult) -> int:
    hard_failed = any(is_hard and not ok for _l, ok, is_hard in evaluate_assertions(res))
    return 1 if hard_failed else 0


_LABEL = {
    "oracle": "oracle (perfect ER)",
    "goldengraph": "goldengraph (fuzzy)",
    "exact_match": "exact-match (LightRAG / MS-GraphRAG)",
    "none": "none (no merge)",
}


def render_scorecard_md(res: ScorecardResult) -> str:
    lines = [
        "# GoldenGraph KG-vs-KG capability scorecard (ER dial x capability, no LLM)",
        "",
        "Each KG framework's documented ER strategy as a record_key dial, run through two",
        "ER-driven capabilities. Does weak (exact-match) ER cost the frameworks vs goldengraph's",
        "fuzzy ER? The exact-match column models the LightRAG/MS-GraphRAG ER STRATEGY, not the full",
        "framework runtime (the opt-in real lane is the faithfulness check).",
        "",
        "| ER tier | bridge_recall | aggregation_setF1 |",
        "|---|---|---|",
    ]
    for d in DIAL_TIERS:
        lines.append(f"| {_LABEL[d]} | {res.bridge_recall[d]:.3f} | {res.aggregation_f1[d]:.3f} |")
    lines += ["", "## verdicts", "",
              "(assertions are measurement-frozen empirical gates, not structural guarantees)"]
    for label, passed, is_hard in evaluate_assertions(res):
        tag = "PASS" if passed else ("FAIL" if is_hard else "WARN")
        lines.append(f"- [{tag}] {label}{'' if is_hard else ' (soft)'}")
    return "\n".join(lines) + "\n"
```

- [ ] **Step 4: Run to verify it passes**

Run: `POLARS_SKIP_CPU_CHECK=1 PYTHONPATH="$(pwd)" "$PYEXE" -m pytest tests/test_qa_kg_scorecard.py -v`
Expected: PASS (all). `ruff check` -> clean.

- [ ] **Step 5: Commit**

```bash
git add erkgbench/qa_e2e/kg_scorecard.py tests/test_qa_kg_scorecard.py
git commit -m "feat(er-kg-bench): slice D ScorecardResult + tiered gate + render"
```

---

## Task 3: Per-dial graph metrics + orchestrator (wheel-bearing)

**Files:**
- Modify: `erkgbench/qa_e2e/kg_scorecard.py`

No wheel-free unit test (needs `goldengraph_native`); covered by the in-pipeline gate run. Keep all
wheel/heavy imports function-local.

- [ ] **Step 1: Write minimal implementation**

```python
# add to kg_scorecard.py

def _bridge_recall_for_dial(corpus, g, typ_of, chains, km) -> float:
    """Mean whole-chain bridge-recall over the engineered corpus under one dial's km. Mirrors the
    ablation.run_ablation per-dial loop exactly. Needs the wheel."""
    from goldengraph.answer import _retrieve_local

    from . import ablation
    from .engines.goldengraph import _NODE_BUDGET, _RETRIEVAL_HOPS
    from .scorecard import bridge_recall

    slice_graph, coverage = ablation._build_store(corpus, g, km, typ_of)
    seed_of: dict[str, int] = {}
    for nid in sorted(coverage):  # ascending id => deterministic tie-break
        for c in coverage[nid]:
            seed_of.setdefault(c, nid)
    vals: list[float] = []
    for qa in corpus.questions:
        sn = seed_of.get(qa.start_entity_id)
        if sn is None:
            vals.append(0.0)
            continue
        sub = _retrieve_local(slice_graph, [sn], max_hops=_RETRIEVAL_HOPS, node_budget=_NODE_BUDGET)
        vals.append(bridge_recall(chains[qa.id], sub, coverage)["whole_chain"])
    return (sum(vals) / len(vals)) if vals else 0.0


def _aggregation_f1_for_dial(corpus, qs, g, typ_of, km) -> float:
    """Mean set-F1 over the fan-out list-questions under one dial's km. Reuses
    aggregation.goldengraph_aggregate + set_f1. Needs the wheel."""
    from . import ablation
    from .aggregation import goldengraph_aggregate, set_f1

    slice_graph, coverage = ablation._build_store(corpus, g, km, typ_of)
    vals: list[float] = []
    for q in (q for q in qs if q.kind == "list"):
        got = goldengraph_aggregate(slice_graph, coverage, q.anchor_id, q.relation)
        vals.append(set_f1(got, set(q.gold_members))["f1"])
    return (sum(vals) / len(vals)) if vals else 0.0


def run_scorecard_deterministic(*, seed: int, n_questions: int, n_anchors: int,
                                ambiguity: float, max_hops: int = 4) -> ScorecardResult:
    """Build BOTH capability corpora, run every ER tier through both metrics. NEEDS the wheel."""
    from . import ablation, dials
    from .aggregation import agg_documents_corpus, generate_aggregation
    from .engineered import generate_engineered
    from .gold import GoldGraph, gold_chain

    keyfn = {
        "oracle": dials.oracle_keys,
        "goldengraph": dials.goldengraph_keys,
        "exact_match": dials.name_only_keys,   # LightRAG / MS-GraphRAG (coincide on single-type corpus)
        "none": dials.none_keys,
    }

    eng = generate_engineered(seed=seed, n_questions=n_questions, ambiguity=ambiguity, max_hops=max_hops)
    g_e = GoldGraph.from_corpus(eng)
    typ_e = ablation._typ_of(g_e)
    chains = {qa.id: gold_chain(g_e, qa) for qa in eng.questions}

    docs, qs = generate_aggregation(seed=seed, n_anchors=n_anchors, ambiguity=ambiguity)
    agg = agg_documents_corpus(docs)
    g_a = GoldGraph.from_corpus(agg)
    typ_a = ablation._typ_of(g_a)

    bridge: dict = {}
    aggf1: dict = {}
    for dial in DIAL_TIERS:
        bridge[dial] = _bridge_recall_for_dial(eng, g_e, typ_e, chains, keyfn[dial](eng, g_e))
        aggf1[dial] = _aggregation_f1_for_dial(agg, qs, g_a, typ_a, keyfn[dial](agg, g_a))
    return ScorecardResult(bridge_recall=bridge, aggregation_f1=aggf1)
```

- [ ] **Step 2: Verify it imports + ruff-clean (no wheel call)**

Run: `POLARS_SKIP_CPU_CHECK=1 PYTHONPATH="$(pwd)" "$PYEXE" -c "from erkgbench.qa_e2e import kg_scorecard; print('ok', hasattr(kg_scorecard,'run_scorecard_deterministic'))"`
Expected: `ok True` (module imports; the wheel functions are not called).
Run: `"$PYEXE" -m ruff check erkgbench/qa_e2e/kg_scorecard.py` -> clean.

- [ ] **Step 3: Commit**

```bash
git add erkgbench/qa_e2e/kg_scorecard.py
git commit -m "feat(er-kg-bench): slice D per-dial graph metrics + scorecard orchestrator"
```

---

## Task 4: CLI (deterministic) + measure + freeze + wire CI gate

**Files:**
- Create: `erkgbench/qa_e2e/run_kg_scorecard.py`
- Modify: `erkgbench/qa_e2e/kg_scorecard.py` (freeze constants)
- Modify: `.github/workflows/goldengraph-pipeline.yml`, `.github/workflows/bench-er-kg.yml`

- [ ] **Step 1: Write the CLI (copy run_aggregation.py shape)**

```python
# erkgbench/qa_e2e/run_kg_scorecard.py
"""CLI: deterministic KG-vs-KG capability scorecard (ER dial x capability); write KG_SCORECARD.md,
exit non-zero on a HARD gate failure. Key-free; needs the goldengraph_native wheel.
--with-frameworks adds the opt-in real-framework aggregation confirmation (needs OPENAI_API_KEY +
the engine extras/infra).

Example:
    python -m erkgbench.qa_e2e.run_kg_scorecard --seed 7 --n-questions 80 --n-anchors 60 \
        --ambiguity 0.6 --out-md KG_SCORECARD.md
"""
from __future__ import annotations

import argparse
import os
import sys

from .kg_scorecard import gate_exit_code, render_scorecard_md, run_scorecard_deterministic


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="GoldenGraph KG-vs-KG capability scorecard")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--n-questions", type=int, default=80)
    p.add_argument("--n-anchors", type=int, default=60)
    p.add_argument("--ambiguity", type=float, default=0.6)
    p.add_argument("--max-hops", type=int, default=4)
    p.add_argument("--out-md", default="KG_SCORECARD.md")
    p.add_argument("--with-frameworks", action="store_true",
                   help="also run the real-framework aggregation confirmation (needs OPENAI_API_KEY)")
    p.add_argument("--budget-usd", type=float, default=3.0)
    p.add_argument("--frameworks-out-md", default="KG_SCORECARD_FRAMEWORKS.md")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    res = run_scorecard_deterministic(
        seed=args.seed, n_questions=args.n_questions, n_anchors=args.n_anchors,
        ambiguity=args.ambiguity, max_hops=args.max_hops,
    )
    md = render_scorecard_md(res)
    with open(args.out_md, "w", encoding="utf-8") as fh:
        fh.write(md)
    sys.stdout.write(md)

    if args.with_frameworks and os.environ.get("OPENAI_API_KEY"):
        from goldenmatch.config.schemas import BudgetConfig
        from goldenmatch.core.llm_budget import BudgetTracker

        from .kg_scorecard import framework_aggregation_f1, render_framework_md
        from .scorecard_llm import _BudgetedLLM

        # the inner LLM client is constructed inside framework_aggregation_f1 per engine; the
        # tracker enforces the shared budget.
        tracker = BudgetTracker(BudgetConfig(max_cost_usd=args.budget_usd))
        fr = framework_aggregation_f1(seed=args.seed, n_anchors=args.n_anchors,
                                      ambiguity=args.ambiguity, tracker=tracker, model="gpt-4o-mini")
        fr_md = render_framework_md(fr)
        with open(args.frameworks_out_md, "w", encoding="utf-8") as fh:
            fh.write(fr_md)
        sys.stdout.write(fr_md)

    return gate_exit_code(res)  # gate is the deterministic scorecard; frameworks lane is ungated


if __name__ == "__main__":
    raise SystemExit(main())
```

NOTE: the `--with-frameworks` branch imports `framework_aggregation_f1`/`render_framework_md` (Task 5).
Until then `--with-frameworks` is unused in CI (the gate step does not pass it); the imports are
inside the branch so ruff stays clean. Do NOT invoke `--with-frameworks` before Task 5.

- [ ] **Step 2: MEASURE on the real corpus (verify-before-freeze)**

Needs the wheel. If the local `.venv` has `goldengraph_native`, run:
`POLARS_SKIP_CPU_CHECK=1 PYTHONPATH="$(pwd)" "$PYEXE" -m erkgbench.qa_e2e.run_kg_scorecard --seed 7 --n-questions 80 --n-anchors 60 --ambiguity 0.6 --out-md /tmp/KG_SCORECARD.md`
If the wheel is NOT available locally, push the branch (Task 4 step 4 wires the gate step) and read
`KG_SCORECARD.md` from the `goldengraph-pipeline` artifact, then freeze in a follow-up commit.

Read the table. Freeze in `kg_scorecard.py`:
- `MOAT_MARGIN` = just below the measured `min over both metrics of (goldengraph - exact_match)`
  (e.g. if the worst gap is 0.31, set 0.20). Leave headroom.
- `MONO_TOL` = small (1e-9) unless a measured chain is fractionally out of order from sampling noise,
  in which case set it just above that wobble.
- `EPS` = just above the measured `exact_match - none` on bridge-recall (slice A had 0.000; keep
  ~0.02 headroom for reseed).
- Keep the literals compatible with the hand-built `_good_result()` fixture (bridge exact 0.234 /
  none 0.234; aggregation goldengraph 1.0 / exact 0.45) OR update the fixture in the SAME commit.
- **If goldengraph does NOT beat exact_match by a positive margin on BOTH metrics (no moat), STOP
  and surface to Ben** -- the slice's thesis fails and the gate must not be loosened to pass.

- [ ] **Step 3: Edit the frozen constants + commit**

Re-run the wheel-free tests (hand-built fixture, stay green): `... -m pytest tests/test_qa_kg_scorecard.py -v` -> PASS.

```bash
git add erkgbench/qa_e2e/run_kg_scorecard.py erkgbench/qa_e2e/kg_scorecard.py
git commit -m "feat(er-kg-bench): slice D deterministic CLI + freeze gate constants from measured grid"
```

- [ ] **Step 4: Wire the pipeline gate step**

In `.github/workflows/goldengraph-pipeline.yml`, after the **"Upload AGGREGATION.md"** step (the last
step in the `pipeline` job), add:

```yaml
      - name: KG-vs-KG capability scorecard gate (deterministic, key-free)
        # Slice D: does each KG framework's documented ER strategy (as a record_key dial) cost it
        # on the capabilities goldengraph wins? Runs oracle/goldengraph-fuzzy/exact-match/none
        # through bridge-recall + aggregation set-F1. Gates HARD on goldengraph beating the
        # exact-match tier (LightRAG/MS-GraphRAG) by a frozen margin on every metric. No key.
        working-directory: packages/python/goldenmatch/benchmarks/er-kg-bench
        env:
          POLARS_SKIP_CPU_CHECK: "1"
        run: |
          python -m pytest tests/test_qa_kg_scorecard.py -v
          python -m erkgbench.qa_e2e.run_kg_scorecard \
            --seed 7 --n-questions 80 --n-anchors 60 --ambiguity 0.6 --out-md KG_SCORECARD.md

      - name: Upload KG_SCORECARD.md
        if: ${{ always() }}
        uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a  # v4
        with:
          name: goldengraph-kg-scorecard
          path: packages/python/goldenmatch/benchmarks/er-kg-bench/KG_SCORECARD.md
          if-no-files-found: ignore
```

- [ ] **Step 5: Add the wheel-free test to bench-er-kg.yml**

In `.github/workflows/bench-er-kg.yml`, find the pure-Python test list (names `tests/test_qa_ablation.py`
etc.) and append `tests/test_qa_kg_scorecard.py` to it.

- [ ] **Step 6: Commit the CI wiring**

```bash
git add .github/workflows/goldengraph-pipeline.yml .github/workflows/bench-er-kg.yml
git commit -m "ci(er-kg-bench): wire slice D deterministic KG scorecard gate"
```

---

## Task 5: Opt-in real-framework aggregation lane + bench-graphrag-qa wiring

**Files:**
- Modify: `erkgbench/qa_e2e/kg_scorecard.py`
- Modify: `.github/workflows/bench-graphrag-qa.yml`
- Test: `tests/test_qa_kg_scorecard.py`

Drives the real `engines/` adapters over the aggregation list-questions. The adapters are
heavy/real-LLM/infra and NOT runnable locally; this lane is opt-in + `|| true` and expected-red until
the OpenAI key is funded. The local test exercises only the pure `framework_set_f1` scoring helper.

First READ `engines/lightrag.py`, `engines/ms_graphrag.py`, `engines/graphiti.py` + `harness.py`
(`QAEngine` protocol: `build(...)` then `answer(question)->AnswerResult` with `.answer` text) to
confirm the exact build/answer signatures before wiring `framework_aggregation_f1`. Capture per-engine
build/answer failures (missing infra, 429) and record that engine as `None`, never raise.

- [ ] **Step 1: Write the failing test (pure scoring helper)**

```python
# add to tests/test_qa_kg_scorecard.py

def test_framework_set_f1_scores_parsed_answers():
    s2c = {"Apple": "a", "Widgets": "w", "Cupertino": "c"}
    # gold for one question = {a, w}; framework answered "Apple and Widgets"
    got = ks.framework_set_f1(
        answers=["Apple and Widgets."],
        golds=[{"a", "w"}],
        s2c=s2c,
    )
    assert got == 1.0  # perfect set match -> F1 1.0


def test_framework_set_f1_partial():
    s2c = {"Apple": "a", "Widgets": "w"}
    got = ks.framework_set_f1(answers=["Apple only."], golds=[{"a", "w"}], s2c=s2c)
    assert 0.0 < got < 1.0  # recall 0.5 -> F1 0.667
```

- [ ] **Step 2: Run to verify it fails**

Run: `POLARS_SKIP_CPU_CHECK=1 PYTHONPATH="$(pwd)" "$PYEXE" -m pytest tests/test_qa_kg_scorecard.py -k framework_set_f1 -v`
Expected: FAIL (`AttributeError: framework_set_f1`).

- [ ] **Step 3: Write minimal implementation**

```python
# add to kg_scorecard.py

from dataclasses import field  # only if needed; else inline

@dataclass
class FrameworkResult:
    set_f1: dict          # engine name -> mean set-F1 (or None if the engine failed/skipped)
    budget_exhausted: bool


def framework_set_f1(answers, golds, s2c) -> float:
    """Mean set-F1 of parsed framework answers vs gold member sets (reuses set_f1)."""
    from .aggregation import set_f1

    if not golds:
        return 0.0
    vals = [set_f1(parse_entity_set(a, s2c), g)["f1"] for a, g in zip(answers, golds)]
    return sum(vals) / len(vals)


def framework_aggregation_f1(*, seed: int, n_anchors: int, ambiguity: float, tracker, model: str) -> FrameworkResult:
    """Drive each real engine over the aggregation list-questions; mean set-F1 per engine. Heavy /
    real-LLM / infra-dependent: a per-engine failure (missing extra, infra, 429) -> None, never
    raises. NOT unit-tested (the pure scoring is framework_set_f1)."""
    from .aggregation import agg_documents_corpus, generate_aggregation
    from .gold import GoldGraph
    from . import dials
    from .scorecard_llm import _BudgetedLLM

    docs, qs = generate_aggregation(seed=seed, n_anchors=n_anchors, ambiguity=ambiguity)
    g = GoldGraph.from_corpus(agg_documents_corpus(docs))
    s2c: dict = {}
    for eid, surf, _typ in dials._entity_surfaces(g):
        s2c.setdefault(surf, eid)  # first-wins scalar
    list_qs = [q for q in qs if q.kind == "list"]
    golds = [set(q.gold_members) for q in list_qs]

    # Engine registry: build a fresh adapter per engine; each owns its LLM + infra. See engines/.
    # Build the index over `docs`, ask each q.question, collect .answer text.
    from .engines import lightrag, ms_graphrag, graphiti  # adjust to actual module exports

    engines = {
        "lightrag": lightrag,
        "ms_graphrag": ms_graphrag,
        "graphiti": graphiti,
    }
    out: dict = {}
    for name, mod in engines.items():
        if tracker.budget_exhausted:
            out[name] = None
            continue
        try:
            llm = _BudgetedLLM(_engine_llm(mod, model), tracker, model=model)  # see NOTE
            answers = _run_engine_over_questions(mod, docs, [q.question for q in list_qs], llm)
            out[name] = framework_set_f1(answers, golds, s2c)
        except Exception:  # missing infra / 429 / version drift -> skip this engine
            out[name] = None
    return FrameworkResult(set_f1=out, budget_exhausted=tracker.budget_exhausted)


def render_framework_md(res: FrameworkResult) -> str:
    lines = [
        "# KG-vs-KG real-framework aggregation confirmation (real LLM, opt-in, UNGATED)",
        "",
        "Real LightRAG/MS-GraphRAG/Graphiti over the aggregation list-questions. Confirms the",
        "exact-match dial model (real frameworks under-aggregate) and gives Graphiti a real",
        "semantic-ER number. A None row = the engine failed to build (missing infra / 429).",
        "",
        f"budget_exhausted: {res.budget_exhausted}",
        "",
        "| framework | aggregation_setF1 |",
        "|---|---|",
    ]
    for name, v in res.set_f1.items():
        lines.append(f"| {name} | {'n/a' if v is None else f'{v:.3f}'} |")
    return "\n".join(lines) + "\n"
```

NOTE (resolve during impl, do NOT guess): `_engine_llm(mod, model)` and `_run_engine_over_questions`
are placeholders for the ACTUAL adapter build/answer calls -- READ each `engines/*.py` + `harness.py`
first and wire the real `build`/`answer` signatures (LightRAG holds an in-process index on one event
loop; Graphiti needs FalkorDB; MS-GraphRAG writes settings.yaml + parquet). The cost seam is the
injected counting LLM (`make_counting_llm_func` in lightrag.py; `_CountingLLM` pattern elsewhere). If
an engine's real signature can't be confirmed without running it, wire what you can and let the
opt-in CI lane (the real validator) surface the gap -- the lane is `|| true` so a wiring miss never
blocks. Keep `framework_set_f1` (the tested pure helper) the single scoring path.

- [ ] **Step 4: Run to verify it passes + ruff**

Run: `POLARS_SKIP_CPU_CHECK=1 PYTHONPATH="$(pwd)" "$PYEXE" -m pytest tests/test_qa_kg_scorecard.py -v`
Expected: PASS (parser + gate + framework_set_f1). `ruff check erkgbench/qa_e2e/kg_scorecard.py erkgbench/qa_e2e/run_kg_scorecard.py` -> clean. (Drop the `from dataclasses import field` line if unused.)

- [ ] **Step 5: Wire bench-graphrag-qa.yml**

In `.github/workflows/bench-graphrag-qa.yml` (mirror the EXISTING `run_aggregation_llm` patterns --
they are untyped STRING inputs `default: "false"`, NOT `type: boolean`):
1. Under `workflow_dispatch.inputs`, add:
   ```yaml
   run_kg_capability:
     description: "Slice D: real-framework aggregation confirmation (LightRAG/MS-GraphRAG/Graphiti)"
     default: "false"
   ```
2. Append the clause to the existing `scorecard` job `if:` (currently
   `if: ${{ inputs.run_scorecard == 'true' || inputs.run_aggregation_llm == 'true' }}`):
   `... || inputs.run_kg_capability == 'true' }}`.
3. Add a guarded step + upload (mirror the "real-LLM RAG aggregation floor" step + its upload),
   non-gating, using the SAME secret name the existing steps use (`secrets.GOLDENGRAPH_OPENAI_API_KEY`):
   ```yaml
       - name: Real-framework KG capability confirmation (slice D, opt-in)
         if: ${{ inputs.run_kg_capability == 'true' }}
         working-directory: packages/python/goldenmatch/benchmarks/er-kg-bench
         env:
           OPENAI_API_KEY: ${{ secrets.GOLDENGRAPH_OPENAI_API_KEY }}
           POLARS_SKIP_CPU_CHECK: "1"
         run: |
           python -m erkgbench.qa_e2e.run_kg_scorecard \
             --seed 7 --n-questions 80 --n-anchors 60 --ambiguity 0.6 \
             --with-frameworks --budget-usd "${{ inputs.scorecard_budget_usd }}" \
             --out-md KG_SCORECARD.md --frameworks-out-md KG_SCORECARD_FRAMEWORKS.md || true
       - name: Upload KG_SCORECARD_FRAMEWORKS.md
         if: ${{ always() && inputs.run_kg_capability == 'true' }}
         uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a  # v4
         with:
           name: graphrag-qa-kg-frameworks
           path: packages/python/goldenmatch/benchmarks/er-kg-bench/KG_SCORECARD_FRAMEWORKS.md
           if-no-files-found: ignore
   ```
   NOTE: the real lane likely needs the engine extras + FalkorDB service the existing framework jobs
   install -- reuse that job's setup (extras install + service container) if the `scorecard` job lacks
   it; this may need a CI iteration since it's billing/infra-blocked. The lane is `|| true`.

- [ ] **Step 6: Commit**

```bash
git add erkgbench/qa_e2e/kg_scorecard.py tests/test_qa_kg_scorecard.py .github/workflows/bench-graphrag-qa.yml
git commit -m "feat(er-kg-bench): slice D opt-in real-framework aggregation lane"
```

---

## Final verification (before finishing the branch)

- [ ] `cd packages/python/goldenmatch/benchmarks/er-kg-bench && POLARS_SKIP_CPU_CHECK=1 PYTHONPATH="$(pwd)" "$PYEXE" -m pytest tests/test_qa_kg_scorecard.py -v` -> all PASS.
- [ ] `"$PYEXE" -m ruff check erkgbench/qa_e2e/kg_scorecard.py erkgbench/qa_e2e/run_kg_scorecard.py` -> clean.
- [ ] `"$PYEXE" -c "import yaml; [yaml.safe_load(open(f)) for f in ['.github/workflows/goldengraph-pipeline.yml','.github/workflows/bench-er-kg.yml','.github/workflows/bench-graphrag-qa.yml']]; print('yaml ok')"` -> `yaml ok`.
- [ ] Use superpowers:finishing-a-development-branch: push (benzsevern account), open PR vs `main`, watch the `goldengraph-pipeline` lane (the only real-execution gate) go green BEFORE arming `gh pr merge <N> --auto`, then record to memory `project_goldengraph_kg_engine.md`.
- [ ] If the measured `KG_SCORECARD.md` shows NO moat (goldengraph does not beat exact_match by a positive margin on both metrics), surface to Ben (per Task 4 step 2) rather than loosening the gate.

## Known unknowns to resolve during implementation (call out, don't guess)

- Exact `engines/*.py` build/answer signatures + the cost seam per framework (Task 5) -- READ the
  modules + `harness.py` first; the `_engine_llm`/`_run_engine_over_questions` placeholders MUST be
  replaced with the real calls. LightRAG = one persistent event loop; Graphiti = FalkorDB; MS-GraphRAG
  = settings.yaml + parquet.
- Whether the `scorecard` job in `bench-graphrag-qa.yml` already installs the engine extras + FalkorDB
  the real lane needs, or whether the step must reuse a different job's setup.
- `dials.surface_to_canon` is set-valued; the scorecard's first-wins scalar `s2c` is built inline
  (`setdefault`) -- do NOT swap in `surface_to_canon`.
- Confirm `corpus.questions` / `corpus.documents` attribute names and `AggQuestion.kind == "list"` /
  `.gold_members` / `.anchor_id` / `.relation` (confirmed in `aggregation.run_aggregation_deterministic`).
