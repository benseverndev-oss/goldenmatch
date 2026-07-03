# GoldenGraph slice 4a (workload-aware resolution planner) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a `UnifiedPlanner` that profiles a query workload (via the slice-3 router) and picks the ER resolution tier the workload demands (capability-heavy -> FUZZY, lookup -> EXACT), with a free deterministic gate that proves the routing AND that the chosen tier wins the workload's capability (reusing slice-D's measured kg_scorecard).

**Architecture:** New `goldengraph/unified.py` (pure-Python: `ResolutionTier`, `WorkloadProfile`, `UnifiedPlan`, `profile_workload` over `route.resolve_profile`, `plan_resolution`). A new `erkgbench/qa_e2e/unified_eval.py` gate builds capability + lookup workloads from the engineered corpus, asserts routing correctness (wheel-free), and reuses `kg_scorecard.run_scorecard_deterministic` + `MOAT_MARGIN` as the measured justification (wheel).

**Tech Stack:** Python 3.12, pytest, ruff. STACKED on slice 3 (`feat/goldengraph-llm-classifier`, PR #1285). `unified.py` + routing-correctness are wheel-free; the kg_scorecard justification needs the `goldengraph_native` wheel (goldengraph-pipeline CI).

**Spec:** `docs/superpowers/specs/2026-06-27-goldengraph-unified-planner-design.md`

---

## Conventions for every task

- Worktree: `D:\show_case\gg-unified`, branch `feat/goldengraph-unified-planner` (stacked on slice 3).
- `PYEXE="D:/show_case/goldenmatch/.venv/Scripts/python.exe"`.
- `unified.py` tests wheel-free: `cd D:/show_case/gg-unified && POLARS_SKIP_CPU_CHECK=1 PYTHONPATH="packages/python/goldengraph" "$PYEXE" -m pytest packages/python/goldengraph/tests/test_unified.py -v`.
- er-kg-bench wheel-free tests: from the bench dir, `PYTHONPATH="$(pwd);D:/show_case/gg-unified/packages/python/goldengraph"` (`;` on Windows).
- The kg_scorecard justification needs the wheel -> CI only.
- Ruff-clean per commit.
- Verified reuse facts: `route.py` has `resolve_profile(query, *, predicates=None, llm_classifier=None) -> QueryProfile` (.intent), `plan_query(profile).mode` in {aggregate, as_of, hybrid, local}, `QueryIntent` StrEnum (AGGREGATE/TEMPORAL_ASOF/MULTI_HOP/LOOKUP), `_LOOKUP_RE = ^(what is|who is|where is)`. `kg_scorecard.py`: `run_scorecard_deterministic(*, seed, n_questions, n_anchors, ambiguity, max_hops=4) -> ScorecardResult` (`.bridge_recall`/`.aggregation_f1` dicts keyed by dial), `DIAL_TIERS=("oracle","goldengraph","exact_match","none")`, `MOAT_MARGIN=0.15`. `engineered._load_entities()` (`.id`,`.canonical`), `RELATION_SCHEMA`. B1 `generate_aggregation`, B2 `generate_temporal` produce the capability question texts.
- Commit footer:
  ```
  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01Sc5aGSaVBTWBWpytNH99QE
  ```

## File structure

- Create `packages/python/goldengraph/goldengraph/unified.py` -- the planner (pure-Python).
- Create `packages/python/goldengraph/tests/test_unified.py` -- planner unit tests.
- Create `erkgbench/qa_e2e/unified_eval.py` -- workloads + routing gate + kg_scorecard justification + render.
- Create `erkgbench/qa_e2e/run_unified_eval.py` -- CLI -> UNIFIED.md.
- Create `erkgbench/qa_e2e/.../tests/test_qa_unified.py` -- wheel-free routing + gate-shape.
- Modify `.github/workflows/goldengraph-pipeline.yml` -- unified gate step + upload (after "Upload ROUTER.md").
- Modify `.github/workflows/bench-er-kg.yml` -- add `tests/test_qa_unified.py` to the pure-Python list.

---

## Task 1: the planner (`unified.py`, wheel-free)

**Files:**
- Create: `packages/python/goldengraph/goldengraph/unified.py`
- Test: `packages/python/goldengraph/tests/test_unified.py`

- [ ] **Step 1: Write the failing test**

```python
# packages/python/goldengraph/tests/test_unified.py
"""Workload-aware resolution planner (slice 4a) -- pure-Python unit tests."""
from __future__ import annotations

from goldengraph import unified

_PREDS = {"works_at", "located_in", "acquired", "authored", "part_of"}


def test_capability_workload_routes_to_fuzzy():
    queries = [
        "List all entities that Metaphone works at.",
        "As of 3, what does Soundex works at?",
    ]
    plan = unified.plan_resolution(unified.profile_workload(queries, predicates=_PREDS))
    assert plan.resolution_tier is unified.ResolutionTier.FUZZY
    assert plan.capability_fraction >= 0.5


def test_lookup_workload_routes_to_exact():
    queries = ["what is Soundex?", "what is Metaphone?", "who is Levenshtein distance?"]
    plan = unified.plan_resolution(unified.profile_workload(queries, predicates=_PREDS))
    assert plan.resolution_tier is unified.ResolutionTier.EXACT
    assert plan.capability_fraction < 0.5


def test_modes_needed_collected():
    queries = ["List all entities that Metaphone works at.", "what is Soundex?"]
    wp = unified.profile_workload(queries, predicates=_PREDS)
    assert "aggregate" in wp.retrieval_modes_needed


def test_empty_workload_defaults_to_exact():
    plan = unified.plan_resolution(unified.profile_workload([], predicates=_PREDS))
    assert plan.resolution_tier is unified.ResolutionTier.EXACT


def test_profile_workload_accepts_injected_classifier():
    # NL paraphrase the heuristic misses; a stub tier-2 says AGGREGATE -> capability
    from goldengraph.route import QueryIntent, QueryProfile

    class _Stub:
        def classify(self, query, *, predicates=None):
            return QueryProfile(QueryIntent.AGGREGATE, anchor_surface="Soundex",
                                relation="works_at", confidence=1.0)

    wp = unified.profile_workload(["who all does Soundex work with?"], predicates=_PREDS, llm_classifier=_Stub())
    assert unified.plan_resolution(wp).resolution_tier is unified.ResolutionTier.FUZZY
```

- [ ] **Step 2: Run to verify it fails**

Run: `... pytest packages/python/goldengraph/tests/test_unified.py -v`
Expected: FAIL (`ModuleNotFoundError: goldengraph.unified`).

- [ ] **Step 3: Write minimal implementation**

```python
# packages/python/goldengraph/goldengraph/unified.py
"""Slice 4a: workload-aware resolution planner -- the meta-kernel join. Profile a query workload via
the slice-3 router and pick the ER resolution tier the workload demands (capability-heavy -> high-
recall FUZZY; lookup -> cheap EXACT). Pure-Python (reuses route). Nothing CONSUMES the plan yet (4b
wires it into ingest); 4a is the grounded decision + (gate side) its measured justification.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from .route import QueryIntent, plan_query, resolve_profile

#: ER-demanding intents -- weak ER costs these (A multi-hop reachability, B1 aggregation, B2 temporal).
_CAPABILITY_INTENTS = frozenset({QueryIntent.AGGREGATE, QueryIntent.TEMPORAL_ASOF, QueryIntent.MULTI_HOP})

CAP_THRESHOLD = 0.5  # >= this fraction of capability-demanding queries -> high-recall ER


class ResolutionTier(StrEnum):
    EXACT = "exact"               # name-only merge (cheap)
    FUZZY = "fuzzy"               # name+type (current default)
    FUZZY_CONTEXT = "fuzzy_context"  # name+type+context (+13pp lever; a 4b refinement)


@dataclass
class WorkloadProfile:
    intents: dict
    capability_fraction: float
    retrieval_modes_needed: set


@dataclass
class UnifiedPlan:
    resolution_tier: ResolutionTier
    retrieval_modes_needed: set
    capability_fraction: float
    rationale: str


def profile_workload(queries, *, predicates=None, llm_classifier=None) -> WorkloadProfile:
    intents: dict = {}
    modes: set = set()
    cap = 0
    for q in queries:
        p = resolve_profile(q, predicates=predicates, llm_classifier=llm_classifier)
        intents[p.intent] = intents.get(p.intent, 0) + 1
        modes.add(plan_query(p).mode)
        if p.intent in _CAPABILITY_INTENTS:
            cap += 1
    n = len(queries) or 1
    return WorkloadProfile(intents=intents, capability_fraction=cap / n, retrieval_modes_needed=modes)


def plan_resolution(wp: WorkloadProfile) -> UnifiedPlan:
    if wp.capability_fraction >= CAP_THRESHOLD:
        tier = ResolutionTier.FUZZY
        why = (f"{wp.capability_fraction:.0%} capability-demanding queries (aggregation/temporal/"
               f"multi-hop) -> high-recall FUZZY ER (A-D: exact-match ER == no-merge on these; "
               f"FUZZY_CONTEXT is the 4b upgrade for reachability-heavy workloads)")
    else:
        tier = ResolutionTier.EXACT
        why = (f"only {wp.capability_fraction:.0%} capability-demanding -> cheap EXACT ER suffices")
    return UnifiedPlan(resolution_tier=tier, retrieval_modes_needed=wp.retrieval_modes_needed,
                       capability_fraction=wp.capability_fraction, rationale=why)
```

(Drop the unused `field` import if ruff flags it -- the dataclasses use no `field(...)` here.)

- [ ] **Step 4: Run to verify it passes**

Run: `... pytest packages/python/goldengraph/tests/test_unified.py -v`
Expected: PASS (all). `ruff check unified.py` -> clean.

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldengraph/goldengraph/unified.py packages/python/goldengraph/tests/test_unified.py
git commit -m "feat(goldengraph): workload-aware resolution planner (slice 4a)"
```

---

## Task 2: gate (`unified_eval.py`) + CLI (er-kg-bench)

**Files:**
- Create: `erkgbench/qa_e2e/unified_eval.py`, `erkgbench/qa_e2e/run_unified_eval.py`
- Test: `tests/test_qa_unified.py`

- [ ] **Step 1: Write the failing wheel-free test**

```python
# tests/test_qa_unified.py
"""Slice 4a unified planner gate -- wheel-free routing + gate shape."""
from __future__ import annotations

from erkgbench.qa_e2e import unified_eval as ue


def test_capability_workload_routes_fuzzy():
    r = ue.routing_correctness(seed=7, n_anchors=12, n_facts=12)
    assert r["capability_tier"] == "fuzzy"
    assert r["lookup_tier"] == "exact"
    assert r["capability_fraction"] >= 0.5
    assert r["lookup_fraction"] < 0.5


def test_gate_shape_passes_on_good_result():
    res = ue.UnifiedResult(capability_tier="fuzzy", lookup_tier="exact",
                           capability_fraction=1.0, lookup_fraction=0.0,
                           agg_delta=0.287, bridge_delta=0.324)
    assert ue.gate_exit_code(res) == 0


def test_gate_fails_when_tier_wrong():
    res = ue.UnifiedResult(capability_tier="exact", lookup_tier="exact",
                           capability_fraction=1.0, lookup_fraction=0.0,
                           agg_delta=0.287, bridge_delta=0.324)
    assert ue.gate_exit_code(res) == 1


def test_gate_fails_when_justification_delta_low():
    res = ue.UnifiedResult(capability_tier="fuzzy", lookup_tier="exact",
                           capability_fraction=1.0, lookup_fraction=0.0,
                           agg_delta=0.05, bridge_delta=0.05)  # fuzzy doesn't beat exact -> thesis refuted
    assert ue.gate_exit_code(res) == 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd <bench dir> && PYTHONPATH="$(pwd);<goldengraph>" "$PYEXE" -m pytest tests/test_qa_unified.py -v`
Expected: FAIL (`ModuleNotFoundError: unified_eval`).

- [ ] **Step 3: Write minimal implementation**

```python
# erkgbench/qa_e2e/unified_eval.py
"""Slice 4a gate. Build a CAPABILITY workload (B1 aggregation + B2 temporal question texts) and a
LOOKUP workload ("what is <concept>?"); assert the planner routes capability->FUZZY, lookup->EXACT
(wheel-free), and reuse slice-D kg_scorecard (+ its MOAT_MARGIN) to prove FUZZY (goldengraph dial)
beats EXACT (exact_match dial) on the workload's capability -- the unification thesis, MEASURED
(needs the wheel)."""
from __future__ import annotations

from dataclasses import dataclass

from goldengraph.unified import ResolutionTier, plan_resolution, profile_workload

from .engineered import RELATION_SCHEMA, _load_entities


def _capability_queries(seed: int, n_anchors: int, n_facts: int):
    from .aggregation import generate_aggregation
    from .temporal import generate_temporal

    _d, agg_qs = generate_aggregation(seed=seed, n_anchors=n_anchors, ambiguity=0.0)
    _d2, _f, tmp_qs = generate_temporal(seed=seed, n_facts=n_facts, ambiguity=0.6)
    return [q.question for q in agg_qs if q.kind == "list"] + [q.question for q in tmp_qs]


def _lookup_queries():
    return [f"what is {e.canonical}?" for e in _load_entities()]


def routing_correctness(*, seed: int, n_anchors: int, n_facts: int) -> dict:
    preds = set(RELATION_SCHEMA)
    cap = plan_resolution(profile_workload(_capability_queries(seed, n_anchors, n_facts), predicates=preds))
    look = plan_resolution(profile_workload(_lookup_queries(), predicates=preds))
    return {"capability_tier": cap.resolution_tier.value, "lookup_tier": look.resolution_tier.value,
            "capability_fraction": cap.capability_fraction, "lookup_fraction": look.capability_fraction}


@dataclass
class UnifiedResult:
    capability_tier: str
    lookup_tier: str
    capability_fraction: float
    lookup_fraction: float
    agg_delta: float    # goldengraph(FUZZY) - exact_match(EXACT) on aggregation set-F1 (kg_scorecard)
    bridge_delta: float # ... on bridge-recall


def evaluate_assertions(res: UnifiedResult):
    from .kg_scorecard import MOAT_MARGIN  # reuse the slice-D margin (no drift)

    return [
        (f"capability workload -> FUZZY, lookup -> EXACT (cap_tier={res.capability_tier}, look_tier={res.lookup_tier})",
         res.capability_tier == ResolutionTier.FUZZY.value and res.lookup_tier == ResolutionTier.EXACT.value, True),
        (f"capability_fraction {res.capability_fraction:.3f} >= 0.5 > lookup_fraction {res.lookup_fraction:.3f}",
         res.capability_fraction >= 0.5 and res.lookup_fraction < 0.5, True),
        (f"chosen FUZZY tier WINS capability: agg_delta {res.agg_delta:.3f} & bridge_delta {res.bridge_delta:.3f} >= {MOAT_MARGIN} (measured, slice D)",
         res.agg_delta >= MOAT_MARGIN and res.bridge_delta >= MOAT_MARGIN, True),
    ]


def gate_exit_code(res: UnifiedResult) -> int:
    return 1 if any(h and not ok for _l, ok, h in evaluate_assertions(res)) else 0


def run_justification(*, seed: int, n_questions: int, n_anchors: int) -> tuple[float, float]:
    """kg_scorecard deltas: goldengraph(FUZZY) - exact_match(EXACT) on agg + bridge. Needs the wheel."""
    from .kg_scorecard import run_scorecard_deterministic

    sc = run_scorecard_deterministic(seed=seed, n_questions=n_questions, n_anchors=n_anchors, ambiguity=0.6)
    return (sc.aggregation_f1["goldengraph"] - sc.aggregation_f1["exact_match"],
            sc.bridge_recall["goldengraph"] - sc.bridge_recall["exact_match"])


def run_unified_deterministic(*, seed: int, n_anchors: int, n_facts: int, n_questions: int) -> UnifiedResult:
    rc = routing_correctness(seed=seed, n_anchors=n_anchors, n_facts=n_facts)
    agg_d, bridge_d = run_justification(seed=seed, n_questions=n_questions, n_anchors=n_anchors)
    return UnifiedResult(capability_tier=rc["capability_tier"], lookup_tier=rc["lookup_tier"],
                         capability_fraction=rc["capability_fraction"], lookup_fraction=rc["lookup_fraction"],
                         agg_delta=agg_d, bridge_delta=bridge_d)


def render_unified_md(res: UnifiedResult) -> str:
    lines = [
        "# GoldenGraph unified planner gate (slice 4a, no LLM)",
        "",
        "The meta-kernel JOIN: a query workload's capability demand picks the ER resolution tier, and",
        "slice-D's measured dial scorecard proves the chosen tier WINS the workload's capability.",
        "(4a is the decision + its justification; nothing consumes the plan yet -- 4b wires it.)",
        "",
        f"- capability workload: tier={res.capability_tier}  (capability_fraction {res.capability_fraction:.3f})",
        f"- lookup workload:     tier={res.lookup_tier}  (capability_fraction {res.lookup_fraction:.3f})",
        f"- FUZZY-vs-EXACT measured deltas (slice D): aggregation {res.agg_delta:.3f}, bridge-recall {res.bridge_delta:.3f}",
        "",
        "## verdicts",
        "",
    ]
    for label, ok, _h in evaluate_assertions(res):
        lines.append(f"- [{'PASS' if ok else 'FAIL'}] {label}")
    return "\n".join(lines) + "\n"
```

- [ ] **Step 4: CLI**

```python
# erkgbench/qa_e2e/run_unified_eval.py
"""CLI: slice 4a unified planner gate; write UNIFIED.md, exit non-zero on a HARD gate failure.
Key-free; the justification needs the goldengraph_native wheel."""
from __future__ import annotations

import argparse
import sys

from .unified_eval import gate_exit_code, render_unified_md, run_unified_deterministic


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="GoldenGraph unified planner gate")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--n-anchors", type=int, default=60)
    ap.add_argument("--n-facts", type=int, default=40)
    ap.add_argument("--n-questions", type=int, default=80)
    ap.add_argument("--out-md", default="UNIFIED.md")
    args = ap.parse_args(argv)
    res = run_unified_deterministic(seed=args.seed, n_anchors=args.n_anchors,
                                    n_facts=args.n_facts, n_questions=args.n_questions)
    md = render_unified_md(res)
    with open(args.out_md, "w", encoding="utf-8") as fh:
        fh.write(md)
    sys.stdout.write(md)
    return gate_exit_code(res)


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Run the wheel-free test + ruff**

Run: `... pytest tests/test_qa_unified.py -v` (routing + gate-shape; the justification runs in CI).
`ruff check unified_eval.py run_unified_eval.py` -> clean.

- [ ] **Step 6: Commit**

```bash
git add erkgbench/qa_e2e/unified_eval.py erkgbench/qa_e2e/run_unified_eval.py tests/test_qa_unified.py
git commit -m "feat(er-kg-bench): unified planner gate (routing + kg_scorecard justification)"
```

---

## Task 3: CI wiring

**Files:**
- Modify: `.github/workflows/goldengraph-pipeline.yml`, `.github/workflows/bench-er-kg.yml`

- [ ] **Step 1: Add the pipeline gate step** after the **"Upload ROUTER.md"** step (the last `pipeline` step):

```yaml
      - name: Unified planner gate (deterministic, key-free)
        # Slice 4a meta-kernel JOIN: a query workload's capability demand picks the ER resolution
        # tier (capability -> FUZZY, lookup -> EXACT), and slice-D's measured kg_scorecard proves
        # FUZZY beats EXACT on the workload's capability. Gates HARD on routing + the measured deltas.
        working-directory: packages/python/goldenmatch/benchmarks/er-kg-bench
        env:
          POLARS_SKIP_CPU_CHECK: "1"
        run: |
          python -m pytest tests/test_qa_unified.py -v
          python -m erkgbench.qa_e2e.run_unified_eval \
            --seed 7 --n-anchors 60 --n-facts 40 --n-questions 80 --out-md UNIFIED.md

      - name: Upload UNIFIED.md
        if: ${{ always() }}
        uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a  # v4
        with:
          name: goldengraph-unified
          path: packages/python/goldenmatch/benchmarks/er-kg-bench/UNIFIED.md
          if-no-files-found: ignore
```

- [ ] **Step 2: Add the wheel-free test to bench-er-kg.yml** -- append `tests/test_qa_unified.py` to the pure-Python pytest list (it currently ends with `tests/test_qa_router.py -v"`).

- [ ] **Step 3: Validate yaml + commit**

```bash
python -c "import yaml; [yaml.safe_load(open(f)) for f in ['.github/workflows/goldengraph-pipeline.yml','.github/workflows/bench-er-kg.yml']]; print('yaml ok')"
git add .github/workflows/goldengraph-pipeline.yml .github/workflows/bench-er-kg.yml
git commit -m "ci(er-kg-bench): wire slice 4a unified planner gate"
```

---

## Final verification (before finishing the branch)

- [ ] `... pytest packages/python/goldengraph/tests/test_unified.py -v` -> PASS.
- [ ] er-kg-bench `tests/test_qa_unified.py` -> PASS (wheel-free).
- [ ] `ruff check` on all created .py -> clean.
- [ ] Use superpowers:finishing-a-development-branch: push (benzsevern account), open PR. **Base:
  `feat/goldengraph-llm-classifier` if #1285 is still open; if #1285 merged first, rebase
  `--onto origin/main <slice-3-tip>` and target main** (slice-3 tip = the commit this branch started
  at; `git log --oneline` to find it). Watch the `goldengraph-pipeline` unified gate green BEFORE
  arming `gh pr merge --auto`. Record memory.
- [ ] If the planner picks the WRONG tier, or the kg_scorecard deltas are < MOAT_MARGIN (fuzzy does
  NOT beat exact on capability), surface to Ben -- the latter would refute the thesis the whole
  program rests on; do not loosen the gate.

## Known unknowns to resolve during implementation (call out, don't guess)

- Confirm "what is <concept>?" classifies LOOKUP (not MULTI_HOP/AGGREGATE) for the real concept
  surfaces -- `_LOOKUP_RE` matches at string start, so it should; verify `lookup_fraction < 0.5` on
  the real entity list (Task 2 Step 5 surfaces it). A concept surface containing "as of"/"list all"
  is the only risk (none in dataset/concepts.jsonl -- verify).
- Confirm `run_scorecard_deterministic` returns dial keys exactly `"goldengraph"`/`"exact_match"`
  (DIAL_TIERS) so `sc.aggregation_f1["goldengraph"]` / `["exact_match"]` resolve.
- The justification step rebuilds the kg_scorecard stores (~the slice-D run cost) inside the unified
  gate -- acceptable (the pipeline already runs kg_scorecard separately; this is a second run). If CI
  time matters later, factor a shared scorecard run; not a 4a concern.
