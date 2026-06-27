# GoldenGraph slice 4c (unified entry point) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a stateful `GoldenGraph` facade that runs the whole program arc in one object -- profile a query workload -> tier-resolver build (4a/4b) -> auto-routed answers (slices 1/2/3) -- while surfacing the ER controller's scale `ExecutionPlan` and threading ONE budget across build + answer LLM work.

**Architecture:** New `goldengraph/budget.py` (`Budget` + `_BudgetedLLM` shared seam) + `goldengraph/graph.py` (`plan_er_execution` ExecutionPlan surface + `GoldenGraph` facade composing `plan_resolver` + `ingest_corpus` + `ask`). A free deterministic 3-verdict gate (`erkgbench/qa_e2e/unified_entry_eval.py`) proves end-to-end routing, cross-controller budget sharing, and that the surfaced plan scales with corpus size.

**Tech Stack:** Python 3.12, pytest, ruff. Off main (4a #1286 + 4b #1287 merged). Composition-only -- adds the facade/plan-surface/budget; changes NO existing route/resolve/ask logic.

**Spec:** `docs/superpowers/specs/2026-06-27-goldengraph-unified-entrypoint-design.md`

---

## Conventions for every task

- Worktree: `D:\show_case\gg-unified-entry`, branch `feat/goldengraph-unified-entrypoint` (off main).
- `PYEXE="D:/show_case/goldenmatch/.venv/Scripts/python.exe"`. ALWAYS set `POLARS_SKIP_CPU_CHECK=1`.
- Bench dir (where `erkgbench/...` paths root) = `packages/python/goldenmatch/benchmarks/er-kg-bench`.
- goldengraph tests: `cd D:/show_case/gg-unified-entry && POLARS_SKIP_CPU_CHECK=1 PYTHONPATH="packages/python/goldengraph" "$PYEXE" -m pytest packages/python/goldengraph/tests/<f> -v`.
- er-kg-bench wheel-free: from the bench dir, `PYTHONPATH="$(pwd);D:/show_case/gg-unified-entry/packages/python/goldengraph"`.
- Ruff-clean per commit. StrEnum NOT `(str, Enum)` (ruff UP042). Add an import only where first used (F401).
- Verified reuse facts (do NOT re-derive):
  - `goldengraph.unified.plan_resolver(queries, *, predicates=None, llm_classifier=None) -> (UnifiedPlan, Resolver)`; `UnifiedPlan.resolution_tier` is a `ResolutionTier` StrEnum (EXACT/FUZZY/FUZZY_CONTEXT).
  - `goldengraph.ingest.ingest_corpus(docs, store, *, llm, resolver=None, embedder=None, fp_index=None, max_workers=None)`; `Resolver = Callable[[list[Mention]], list[ResolvedEntity]]`; `LLMClient` protocol = `complete(str) -> str`.
  - `goldengraph.answer.ask(query, store, *, llm, embedder, valid_t, tx_t, mode="local", k=5, hops=4, max_communities=10, node_budget=64, passages=None, passage_k=10, query_classifier=None) -> str`. `mode="auto"` routes via slices 1/2/3.
  - PyStore = `from goldengraph_native import _native as ggn; ggn.PyStore()` (native wheel; e2e lane only).
  - `goldenmatch.core.autoconfig_planner.apply_planner_rules(profile: ComplexityProfile, runtime: RuntimeProfile, n_rows_full: int, rules) -> ExecutionPlan`; default rules via the module's default-rules accessor (CONFIRM the exact symbol: `_default_rules()` vs a public name -- grep `autoconfig_planner.py`). `ExecutionPlan` carries `.rule_name`.
  - `goldenmatch.core.runtime_profile.capture_runtime_profile() -> RuntimeProfile`.
  - `ComplexityProfile` lives in `goldenmatch/core/complexity_profile.py` (KNOWN-UNKNOWN: construction cost; minimal/representative profile is the fallback -- scale rules key off `n_rows_full` + `runtime.available_ram_gb`).
  - Engineered universe loader pattern (mirror `engineered._load_entities`): `bench_root = Path(__file__).resolve().parents[2]`; `sys.path.insert(0, str(bench_root))`; `from dataset.concepts_loader import load_concepts`; `load_concepts(bench_root/"dataset"/"concepts.jsonl")`. Slice-1 `router_eval.py` already builds the engineered aggregate setup at `ambiguity=0.0` -- REUSE it, don't rebuild.
  - `goldengraph.extract.Mention(name, typ, context="")`.
  - `goldengraph.resolve.ResolvedEntity(local_id, canonical_name, typ, surface_names, record_keys, member_idx)` (positional 6-arg dataclass; re-grep `resolve.py` in Task 3 Step 2 to confirm before relying on the stub).
- Commit footer:
  ```
  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01Sc5aGSaVBTWBWpytNH99QE
  ```

## File structure

- Create `packages/python/goldengraph/goldengraph/budget.py` -- `Budget`, `BudgetExhausted`, `_BudgetedLLM`.
- Create `packages/python/goldengraph/goldengraph/graph.py` -- `plan_er_execution`, `_estimate_records`, `_representative_complexity`, `_new_store`, `GoldenGraph`.
- Create `packages/python/goldengraph/tests/test_budget.py` -- wheel-free Budget + _BudgetedLLM + sharing.
- Create `packages/python/goldengraph/tests/test_graph_facade.py` -- facade composition (injected store + stubs) + `plan_er_execution` scales (needs goldenmatch).
- Create `erkgbench/qa_e2e/unified_entry_eval.py` -- 3-verdict gate + `UnifiedEntryResult` + render.
- Create `erkgbench/qa_e2e/run_unified_entry_eval.py` -- CLI -> UNIFIED_ENTRY.md.
- Create `tests/test_qa_unified_entry.py` -- wheel-free gate-shape.
- Modify `.github/workflows/goldengraph-pipeline.yml` -- gate step + upload (after "Upload TIER.md").
- Modify `.github/workflows/bench-er-kg.yml` -- add `tests/test_qa_unified_entry.py` to the pure-Python list.

---

## Task 1: Budget seam (`budget.py`)

**Files:**
- Create: `packages/python/goldengraph/goldengraph/budget.py`
- Test: `packages/python/goldengraph/tests/test_budget.py`

- [ ] **Step 1: Write the failing test**

```python
# packages/python/goldengraph/tests/test_budget.py
"""Slice 4c budget seam -- wheel-free Budget + _BudgetedLLM (the cross-controller ceiling)."""
from __future__ import annotations

import pytest

from goldengraph.budget import Budget, BudgetExhausted, _BudgetedLLM


class _StubLLM:
    def __init__(self):
        self.calls = 0

    def complete(self, prompt: str) -> str:
        self.calls += 1
        return "ok response text"


def test_unbounded_budget_is_passthrough():
    b = Budget()
    assert b.would_exceed(10**9) is False
    bllm = _BudgetedLLM(_StubLLM(), b)
    assert bllm.complete("hello there") == "ok response text"
    assert b.spent_tokens > 0  # recorded


def test_one_budget_shared_across_two_draws():
    # the cross-controller proof: a "build" draw then an "ask" draw on ONE budget
    b = Budget(total_tokens=10_000)
    bllm = _BudgetedLLM(_StubLLM(), b)
    bllm.complete("x" * 80)          # build draw
    after_build = b.spent_tokens
    assert after_build > 0
    bllm.complete("y" * 80)          # ask draw, SAME pool
    assert b.spent_tokens > after_build


def test_over_budget_input_raises_before_calling():
    b = Budget(total_tokens=5)
    stub = _StubLLM()
    bllm = _BudgetedLLM(stub, b)
    with pytest.raises(BudgetExhausted):
        bllm.complete("z" * 400)     # est_in = 100 > 5
    assert stub.calls == 0           # raised BEFORE delegating


def test_exhaustion_on_next_call_not_exact_ceiling():
    # spent may overshoot total (output not pre-charged); contract = NEXT over-input call raises
    b = Budget(total_tokens=30)
    bllm = _BudgetedLLM(_StubLLM(), b)
    bllm.complete("a" * 40)          # est_in 10 <= 30, passes; records 10 + out//4
    with pytest.raises(BudgetExhausted):
        bllm.complete("b" * 200)     # est_in 50 > remaining
```

- [ ] **Step 2: Run to verify it fails**

Run: `POLARS_SKIP_CPU_CHECK=1 PYTHONPATH="packages/python/goldengraph" "$PYEXE" -m pytest packages/python/goldengraph/tests/test_budget.py -v`
Expected: FAIL (`ModuleNotFoundError: goldengraph.budget`).

- [ ] **Step 3: Write minimal implementation**

```python
# packages/python/goldengraph/goldengraph/budget.py
"""Slice 4c cross-controller budget: ONE ceiling threaded through build-LLM (extraction) AND
answer-LLM (classification + synthesis) via a single _BudgetedLLM wrapper.

Token counts are a deterministic provider-agnostic ESTIMATE (len//4), not a real tokenizer. The
pre-check is on INPUT only, so spent_tokens may overshoot total_tokens after a call that passed the
check -- the contract is "raise before a call whose INPUT would exceed", not exact-ceiling accounting.
"""
from __future__ import annotations

from dataclasses import dataclass


class BudgetExhausted(RuntimeError):
    """Raised by _BudgetedLLM when a call's input estimate would exceed the budget."""


@dataclass
class Budget:
    total_tokens: int | None = None      # None -> unbounded
    spent_tokens: int = 0

    def remaining(self) -> float:
        return float("inf") if self.total_tokens is None else self.total_tokens - self.spent_tokens

    def would_exceed(self, n: int) -> bool:
        return self.total_tokens is not None and self.spent_tokens + n > self.total_tokens

    def record(self, n: int) -> None:
        self.spent_tokens += n


def _est_tokens(s: str) -> int:
    return max(1, len(s) // 4)


class _BudgetedLLM:
    """Wraps an LLMClient (complete(str)->str); charges input BEFORE the call, records output after."""

    def __init__(self, llm, budget: Budget):
        self._llm = llm
        self._budget = budget

    def complete(self, prompt: str) -> str:
        est_in = _est_tokens(prompt)
        if self._budget.would_exceed(est_in):
            raise BudgetExhausted(
                f"budget exhausted: {self._budget.spent_tokens}/{self._budget.total_tokens} (+{est_in})"
            )
        out = self._llm.complete(prompt)
        self._budget.record(est_in + _est_tokens(out))
        return out
```

- [ ] **Step 4: Run to verify it passes**

Run: `... pytest packages/python/goldengraph/tests/test_budget.py -v` -> PASS. `ruff check budget.py test_budget.py` -> clean.

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldengraph/goldengraph/budget.py packages/python/goldengraph/tests/test_budget.py
git commit -m "feat(goldengraph): cross-controller Budget + _BudgetedLLM seam"
```

---

## Task 2: ExecutionPlan surface (`plan_er_execution` in `graph.py`)

**Files:**
- Create: `packages/python/goldengraph/goldengraph/graph.py` (this task adds the plan helper only)
- Test: `packages/python/goldengraph/tests/test_graph_facade.py` (plan-scales tests)

- [ ] **Step 1: CONFIRM the goldenmatch symbols first** (do not guess)

Run: `grep -n "def _default_rules\|DEFAULT_RULES\|def apply_planner_rules" D:/show_case/gg-unified-entry/packages/python/goldenmatch/goldenmatch/core/autoconfig_planner.py` and `sed -n '470,540p' .../complexity_profile.py` to learn the `ComplexityProfile` constructor (required fields / a cheap factory / defaults). Decide minimal-vs-full profile NOW based on what its `__init__` requires. Record the chosen construction in a code comment.

- [ ] **Step 2: Write the failing test** (needs goldenmatch)

```python
# packages/python/goldengraph/tests/test_graph_facade.py
"""Slice 4c facade + ExecutionPlan surface (needs goldenmatch for the planner / real PyStore for e2e)."""
from __future__ import annotations

from goldengraph.graph import plan_er_execution


def test_plan_er_execution_returns_a_plan():
    plan = plan_er_execution(["a doc", "another doc"], corpus_records=1_000)
    assert hasattr(plan, "rule_name") and isinstance(plan.rule_name, str)


def test_plan_scales_with_corpus_size():
    small = plan_er_execution([], corpus_records=1_000)
    huge = plan_er_execution([], corpus_records=500_000_000)
    # the ER controller's planner must react to scale -> a constant would fail this
    assert (small.rule_name != huge.rule_name) or (
        getattr(small, "backend", None) != getattr(huge, "backend", None)
    )
```

- [ ] **Step 3: Run to verify it fails**

Run: `... pytest packages/python/goldengraph/tests/test_graph_facade.py -k plan -v` -> FAIL (`ImportError`).

- [ ] **Step 4: Write minimal implementation** (in `graph.py`)

```python
# packages/python/goldengraph/goldengraph/graph.py  (plan-surface portion)
"""Slice 4c GoldenGraph facade + the ER ExecutionPlan surface.

plan_er_execution surfaces the goldenmatch controller-v3 ExecutionPlan the ER controller WOULD pick for
the aggregate corpus ER workload at this scale. ADVISORY: the per-doc build resolver still runs small
per document; this plan is the scale SIGNAL (flip to a batched/distributed resolution path at scale),
not yet consumed by the build. goldenmatch is imported lazily so the package imports without it.
"""
from __future__ import annotations

_AVG_ENTITIES_PER_DOC = 8  # rough heuristic when the caller gives no corpus_records


def _estimate_records(docs) -> int:
    return _AVG_ENTITIES_PER_DOC * sum(1 for _ in docs)


def _representative_complexity(n_rows_full: int):
    # CONFIRMED in Step 1: construct the minimal ComplexityProfile the planner needs (scale rules key
    # off n_rows_full + runtime.available_ram_gb). Fill required fields from Step 1's findings.
    from goldenmatch.core.complexity_profile import ComplexityProfile
    return ComplexityProfile(...)  # <- per Step 1

def plan_er_execution(docs, *, corpus_records: int | None = None):
    """Return the ER controller's scale ExecutionPlan for this corpus (ADVISORY)."""
    n_rows_full = corpus_records if corpus_records is not None else _estimate_records(docs)
    from goldenmatch.core.autoconfig_planner import apply_planner_rules  # + the default-rules symbol
    from goldenmatch.core.runtime_profile import capture_runtime_profile
    runtime = capture_runtime_profile()
    profile = _representative_complexity(n_rows_full)
    return apply_planner_rules(profile, runtime, n_rows_full, <default_rules_symbol>())
```

- [ ] **Step 5: Run to verify it passes + MEASURE the scale separation**

Run: `... pytest packages/python/goldengraph/tests/test_graph_facade.py -k plan -v` -> PASS.
MEASURE the two rule_names: `... "$PYEXE" -c "from goldengraph.graph import plan_er_execution as p; print(p([],corpus_records=1000).rule_name, '|', p([],corpus_records=500_000_000).rule_name)"`.
**If the two rule_names are EQUAL at every reachable scale, STOP and surface to Ben** (the planner isn't scale-sensitive on this box, or the profile is wrong -- per the spec STOP clause). Record the observed small/huge rule_names in the test as a comment so the gate's frozen endpoints are grounded. `ruff check graph.py`.

- [ ] **Step 6: Commit**

```bash
git add packages/python/goldengraph/goldengraph/graph.py packages/python/goldengraph/tests/test_graph_facade.py
git commit -m "feat(goldengraph): plan_er_execution surfaces the ER controller scale plan (advisory)"
```

---

## Task 3: GoldenGraph facade (`graph.py`)

**Files:**
- Modify: `packages/python/goldengraph/goldengraph/graph.py` (add the class)
- Test: `packages/python/goldengraph/tests/test_graph_facade.py`

- [ ] **Step 1: Write the failing test** (wheel-free via injected store stub + stub LLM/resolver)

```python
# add to tests/test_graph_facade.py
from goldengraph.budget import Budget
from goldengraph.extract import Mention
from goldengraph.graph import GoldenGraph
from goldengraph.resolve import ResolvedEntity


class _StubLLM:
    def complete(self, prompt: str) -> str:
        # minimal valid extraction JSON so ingest's _prepare_doc parses (CONFIRM contract in Step 3)
        return '{"entities": [], "relationships": []}'


class _RecordingStore:
    """Captures append calls so the facade build path runs wheel-free."""
    def __init__(self):
        self.appends = 0
    def append(self, *a, **k):
        self.appends += 1
    # add any other methods _commit_doc calls (CONFIRM in Step 2)


def _stub_resolver(mentions):
    return [ResolvedEntity(i, m.name, m.typ, [m.name], [f"k{i}"], [i]) for i, m in enumerate(mentions)]


def test_build_shares_one_budget_across_build_and_planning():
    b = Budget(total_tokens=100_000)
    gg = GoldenGraph.build(
        ["doc one", "doc two"],
        workload=["List all entities that X works at."],
        llm=_StubLLM(), embedder=None, predicates={"works_at"},
        budget=b, resolver=_stub_resolver, store=_RecordingStore(), corpus_records=1_000,
    )
    assert gg.budget is b
    assert gg.plan.resolution_tier.value in {"fuzzy", "exact"}
    assert hasattr(gg.execution_plan, "rule_name")
    assert b.spent_tokens > 0           # build drew from the budget
```

NOTE: Step 2 must CONFIRM exactly which store methods `_commit_doc` calls (grep `ingest.py` for `store.`); the `_RecordingStore` stub must implement them, OR -- if `_commit_doc` is too coupled to the real engine -- move this build assertion to the goldengraph-pipeline lane (real PyStore) and keep only `plan`/`execution_plan`/`budget`-property assertions wheel-free here. Decide in Step 2; do not force a brittle stub.

- [ ] **Step 2: Confirm the store-call surface, run to verify the test fails**

Grep: `grep -n "store\.\|extraction =" packages/python/goldengraph/goldengraph/ingest.py | head -30` to enumerate the store methods `_commit_doc` uses + the extraction JSON contract `_prepare_doc` expects, AND `grep -n "class ResolvedEntity" -A8 packages/python/goldengraph/goldengraph/resolve.py` to confirm the stub's field order. Adjust `_RecordingStore`/`_StubLLM`/`_stub_resolver` to match (or relocate per the Step-1 note).
Run: `... pytest packages/python/goldengraph/tests/test_graph_facade.py -k build -v` -> FAIL (`AttributeError: GoldenGraph`).

- [ ] **Step 3: Write minimal implementation** (append the class to `graph.py`)

```python
def _new_store():
    from goldengraph_native import _native as ggn  # native wheel; e2e lane only
    return ggn.PyStore()


class GoldenGraph:
    """One stateful entry point: profile a workload -> tier-resolver build (4a/4b) -> auto answers
    (slices 1/2/3), with the ER scale plan surfaced and ONE budget across build + answer LLM work."""

    def __init__(self, *, store, plan, execution_plan, budget, llm, embedder, llm_classifier):
        self._store = store
        self._plan = plan
        self._execution_plan = execution_plan
        self._budget = budget
        self._llm = llm                 # the _BudgetedLLM, reused by ask -> shared pool
        self._embedder = embedder
        self._llm_classifier = llm_classifier

    @classmethod
    def build(cls, docs, workload, *, llm, embedder, predicates=None, llm_classifier=None,
              budget=None, resolver=None, corpus_records=None, store=None):
        from .budget import Budget, _BudgetedLLM
        from .ingest import ingest_corpus
        from .unified import plan_resolver

        budget = budget if budget is not None else Budget()
        plan, planned_resolver = plan_resolver(workload, predicates=predicates,
                                               llm_classifier=llm_classifier)
        execution_plan = plan_er_execution(docs, corpus_records=corpus_records)
        store = store if store is not None else _new_store()
        bllm = _BudgetedLLM(llm, budget)
        ingest_corpus(docs, store, llm=bllm, resolver=resolver or planned_resolver, embedder=embedder)
        return cls(store=store, plan=plan, execution_plan=execution_plan, budget=budget,
                   llm=bllm, embedder=embedder, llm_classifier=llm_classifier)

    def ask(self, query, *, valid_t, tx_t, mode="auto", **ask_kwargs):
        from .answer import ask as _ask
        return _ask(query, self._store, llm=self._llm, embedder=self._embedder,
                    valid_t=valid_t, tx_t=tx_t, mode=mode,
                    query_classifier=self._llm_classifier, **ask_kwargs)

    @property
    def plan(self): return self._plan
    @property
    def execution_plan(self): return self._execution_plan
    @property
    def budget(self): return self._budget
    @property
    def store(self): return self._store
```

- [ ] **Step 4: Run to verify it passes**

Run: `... pytest packages/python/goldengraph/tests/test_graph_facade.py -v` -> PASS. `ruff check graph.py test_graph_facade.py` -> clean.

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldengraph/goldengraph/graph.py packages/python/goldengraph/tests/test_graph_facade.py
git commit -m "feat(goldengraph): GoldenGraph facade (build->auto-answer, shared budget)"
```

---

## Task 4: Gate (`unified_entry_eval.py`) + CLI + CI wiring

**Files:**
- Create: `erkgbench/qa_e2e/unified_entry_eval.py`, `erkgbench/qa_e2e/run_unified_entry_eval.py`
- Create: `tests/test_qa_unified_entry.py`
- Modify: `.github/workflows/goldengraph-pipeline.yml`, `.github/workflows/bench-er-kg.yml`

- [ ] **Step 1: Write the failing wheel-free gate-shape test**

```python
# tests/test_qa_unified_entry.py
"""Slice 4c unified-entry gate -- wheel-free gate shape."""
from __future__ import annotations

from erkgbench.qa_e2e import unified_entry_eval as ue


def test_gate_passes_on_good_result():
    res = ue.UnifiedEntryResult(capability_tier="fuzzy", routed_set_f1=1.0,
                                budget_shared=True, plan_scales=True,
                                small_rule="single_box", huge_rule="distributed")
    assert ue.gate_exit_code(res) == 0


def test_gate_fails_when_plan_constant():
    res = ue.UnifiedEntryResult(capability_tier="fuzzy", routed_set_f1=1.0,
                                budget_shared=True, plan_scales=False,
                                small_rule="x", huge_rule="x")
    assert ue.gate_exit_code(res) == 1


def test_gate_fails_when_budget_not_shared():
    res = ue.UnifiedEntryResult(capability_tier="fuzzy", routed_set_f1=1.0,
                                budget_shared=False, plan_scales=True,
                                small_rule="a", huge_rule="b")
    assert ue.gate_exit_code(res) == 1
```

- [ ] **Step 2: Run to verify it fails**

Run from bench dir: `PYTHONPATH="$(pwd);<goldengraph>" POLARS_SKIP_CPU_CHECK=1 "$PYEXE" -m pytest tests/test_qa_unified_entry.py -v` -> FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Write minimal implementation**

`unified_entry_eval.py`:
- `@dataclass UnifiedEntryResult(capability_tier, routed_set_f1, budget_shared, plan_scales, small_rule, huge_rule)`.
- `evaluate_assertions(res) -> [(label, ok, hard)]` with THREE hard rows:
  1. capability workload routes FUZZY AND `routed_set_f1 >= 0.99` (end-to-end routing).
  2. `budget_shared is True` (one pool across build+ask).
  3. `plan_scales is True` (small_rule != huge_rule).
- `gate_exit_code(res)`; `render_unified_entry_md(res)`.
- `run_unified_entry_deterministic() -> UnifiedEntryResult`: build the real run --
  - Reuse slice-1 `router_eval` engineered aggregate setup at `ambiguity=0.0` (anchors, gold member sets, predicates). Build the store the FACADE way: `GoldenGraph.build(docs, workload=<the capability queries>, llm=StubLLM, embedder=StubEmbedder, resolver=<oracle/stub>, predicates=..., budget=Budget(...))` over the REAL PyStore (this module runs in the pipeline lane). Capture `gg.plan.resolution_tier` (-> capability_tier) and `gg.budget.spent_tokens` before/after an `ask`.
  - For the aggregate query, `gg.ask(q, valid_t, tx_t, mode="auto")` -> `_format_aggregate` text; compare to the gold member NAME set (reuse the slice-1 name-space `set_f1`). routed_set_f1 = that F1.
  - budget_shared = `spent_after_ask > spent_after_build > 0`.
  - plan_scales: `plan_er_execution([], corpus_records=SMALL).rule_name != plan_er_execution([], corpus_records=HUGE).rule_name` (SMALL/HUGE frozen from Task 2's measured values).
  REUSE slice-1 `router_eval.py` helpers wherever they exist (engineered build, set_f1, predicate extraction) -- import them, don't re-author.

`run_unified_entry_eval.py`: argparse `--out-md UNIFIED_ENTRY.md` (+ seed/n-anchors like the sibling CLIs); `args = ap.parse_args(argv)`; `res = run_unified_entry_deterministic(...)`; write `render_unified_entry_md(res)` to `args.out_md`; `return gate_exit_code(res)`; `if __name__ == "__main__": raise SystemExit(main())`.

- [ ] **Step 4: Run the wheel-free gate-shape test + ruff**

Run: `... pytest tests/test_qa_unified_entry.py -v` -> PASS. `ruff check erkgbench/qa_e2e/unified_entry_eval.py erkgbench/qa_e2e/run_unified_entry_eval.py tests/test_qa_unified_entry.py` -> clean. (The full `run_unified_entry_deterministic` needs the wheel + goldenmatch -> validated by the pipeline lane, Step 6.)

- [ ] **Step 5: Wire CI**

In `goldengraph-pipeline.yml`, AFTER the "Upload TIER.md" step add:

```yaml
      - name: Unified entry-point gate (deterministic, key-free)
        # Slice 4c (program close): GoldenGraph facade end-to-end -- profile workload -> tier-resolver
        # build -> auto answer, ONE budget across build+ask, ER scale plan surfaced. Gates HARD on
        # routing + budget-sharing + the plan scaling with corpus size. Needs goldenmatch + the wheel.
        working-directory: packages/python/goldenmatch/benchmarks/er-kg-bench
        env:
          POLARS_SKIP_CPU_CHECK: "1"
        run: |
          python -m pytest tests/test_qa_unified_entry.py -v
          python -m erkgbench.qa_e2e.run_unified_entry_eval --seed 7 --n-anchors 60 --out-md UNIFIED_ENTRY.md

      - name: Upload UNIFIED_ENTRY.md
        if: ${{ always() }}
        uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a  # v4
        with:
          name: goldengraph-unified-entry
          path: packages/python/goldenmatch/benchmarks/er-kg-bench/UNIFIED_ENTRY.md
          if-no-files-found: ignore
```

In `bench-er-kg.yml`, append `tests/test_qa_unified_entry.py` to the pure-Python pytest list (currently ends `tests/test_qa_tier.py -v"` -- CONFIRM the exact current string at edit time).

- [ ] **Step 6: Validate yaml + commit**

```bash
python -c "import yaml; [yaml.safe_load(open(f)) for f in ['.github/workflows/goldengraph-pipeline.yml','.github/workflows/bench-er-kg.yml']]; print('yaml ok')"
git add erkgbench/qa_e2e/unified_entry_eval.py erkgbench/qa_e2e/run_unified_entry_eval.py tests/test_qa_unified_entry.py .github/workflows/goldengraph-pipeline.yml .github/workflows/bench-er-kg.yml
git commit -m "feat(er-kg-bench): slice 4c unified entry-point gate + CI wiring"
```

---

## Final verification (before finishing the branch)

- [ ] `... pytest packages/python/goldengraph/tests/test_budget.py packages/python/goldengraph/tests/test_graph_facade.py -v` -> PASS (goldenmatch present; the `GoldenGraph.build` e2e assertion may be lane-only per Task 3 Step 2).
- [ ] er-kg-bench `tests/test_qa_unified_entry.py` -> PASS (wheel-free gate-shape).
- [ ] `ruff check` on all created/modified .py -> clean.
- [ ] Use superpowers:finishing-a-development-branch: push (benzsevern account), open PR targeting main. Watch the `goldengraph-pipeline` unified-entry gate GREEN (the real validator: wheel + goldenmatch). Then arm `gh pr merge <N> --auto`. Record memory + note the program is COMPLETE (1/2/3/4a/4b/4c).
- [ ] If `plan_er_execution` does NOT scale (small_rule == huge_rule) on the lane, surface to Ben -- do NOT loosen verdict 3.

## Known unknowns to resolve during implementation (call out, don't guess)

- `ComplexityProfile` constructor (Task 2 Step 1): required fields / minimal construction. Decide minimal-vs-full from its `__init__`; the plan-scales gate is the guard.
- The exact default-rules symbol in `autoconfig_planner` (`_default_rules()` vs public) -- grep, don't guess.
- Which store methods `_commit_doc` calls + the extraction JSON contract `_prepare_doc` parses (Task 3 Step 2) -- determines whether the facade build assertion is wheel-free (store stub) or lane-only.
- The measured small/huge `rule_name` separation (Task 2 Step 5) -- freeze the gate's corpus_records endpoints from it; STOP if no separation exists.
- The exact reusable helpers in slice-1 `router_eval.py` (engineered build, set_f1, predicate extraction) -- import them; do not re-author the engineered setup.
