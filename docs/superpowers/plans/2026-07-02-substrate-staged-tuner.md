# Substrate Staged Tuner (SP-B2) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `erkgbench/substrate_tuner.py` — a deterministic staged-ejection optimizer that builds a config on a cheap gold slice, scores it with the SP-A three-axis scorecard, escalates the config along the failing axis, and promotes the best-so-far config to the full build.

**Architecture:** One pure module. `evaluate_gate` reads the SP-A scorecard; `escalate` walks `LEVER_AXIS_MAP[axis]` applying next-state lever ladders and returns `(lever, new_config)`; `run_staged` loops slice-build→gate→escalate up to a budget and promotes the argmax config to the full build. The real build is injected via a `build_and_score` callable, so the whole control flow is box-testable with a fake; a thin `build_and_score_real` adapter wires the actual `ingest_corpus`+`substrate_scorecard` (Modal-smoke only, not TDD).

**Tech Stack:** Python stdlib (`dataclasses`, `contextlib`), pytest. Imports `goldengraph.config` (SP-B1) + `erkgbench.substrate_eval` (SP-A).

**Spec:** `docs/superpowers/specs/2026-07-02-substrate-staged-tuner-design.md`

**Branch:** `feat/substrate-tuner` (off `origin/main`). SP-B1 (#1373) is merged to main, so **rebase onto `origin/main` before starting Task 1** (`git fetch origin main && git rebase origin/main`) to bring `goldengraph.config` in; otherwise the imports fail.

---

## Files

- **Create** `packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/substrate_tuner.py` — the whole SP-B2 harness + the thin real adapter.
- **Create** `packages/python/goldenmatch/benchmarks/er-kg-bench/tests/test_substrate_tuner.py` — pure box-safe tests (fake `build_and_score`).

## Test runner (box-safe)

From the er-kg-bench package dir (`PYTHONPATH=$PWD` so `erkgbench` + `goldengraph` resolve; env flags avoid the polars WMI hang / stale native wheel):

```bash
cd /d/show_case/gg-local-llm/packages/python/goldenmatch/benchmarks/er-kg-bench
PYTHONPATH="$PWD" PYTHONIOENCODING=utf-8 PYTHONUTF8=1 POLARS_SKIP_CPU_CHECK=1 GOLDENMATCH_NATIVE=0 \
  /d/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest tests/test_substrate_tuner.py -q
```

## Ground-truth facts (verified — do not re-derive)

- **`SubstrateConfig`** (frozen dataclass, `goldengraph.config`): fields `xdoc_key=""`, `chunk_extract=False`, `chunk_sentences=6`, `chunk_overlap=2`, `entity_type_canon=False`, `entity_type_vocab=()`, `schema_canon=False`, `relation_vocab=()`, `extractor="api"`, `relation_reprompt=False`, `rebel_fuse=False`, `extract_recall=False`. Frozen → build new configs with `dataclasses.replace`.
- **`LEVER_AXIS_MAP`** (`erkgbench.substrate_eval`): `{"presence": ["chunk_extract","extract_recall","extractor"], "relational": ["xdoc_key","entity_type_canon","schema_canon","relation_vocab","relation_reprompt","rebel_fuse"], "connectivity": ["relation_reprompt","rebel_fuse","relation_vocab"]}`.
- **Refuted levers** (never escalated unless `allow_refuted`): `relation_reprompt`, `rebel_fuse`, `extract_recall`.
- **`substrate_scorecard` return shape**: `{"presence": {"coverage": float}|None, "relational": {"f1","recall","precision"}, "connectivity": {"coverage"|None,"f1"|None,"edge_recall"}, "coherence": {"components","largest_fraction"}}`. The tuner reads `presence.coverage` and `relational.f1`.

---

### Task 1: `GateThresholds` + `GateResult` + `evaluate_gate`

**Files:**
- Create: `erkgbench/substrate_tuner.py`
- Test: `tests/test_substrate_tuner.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_substrate_tuner.py`:

```python
"""SP-B2 substrate staged tuner: gate / escalate / run_staged (pure, box-safe with a fake scorer)."""
from __future__ import annotations

import pytest

from goldengraph.config import SubstrateConfig
from erkgbench import substrate_tuner as st


def _sc(presence, rel_f1, *, conn_cov=0.5):
    """A minimal scorecard dict in the substrate_scorecard shape."""
    return {
        "presence": None if presence is None else {"coverage": presence},
        "relational": {"f1": rel_f1, "recall": rel_f1, "precision": 1.0},
        "connectivity": {"coverage": conn_cov, "f1": conn_cov, "edge_recall": 0.9},
        "coherence": {"components": 1, "largest_fraction": 1.0},
    }


def test_gate_passes_when_both_axes_clear():
    g = st.evaluate_gate(_sc(0.95, 0.60), st.GateThresholds())
    assert g.passed is True and g.failing_axis is None


def test_gate_routes_presence_before_relational():
    g = st.evaluate_gate(_sc(0.10, 0.10), st.GateThresholds())
    assert g.passed is False and g.failing_axis == "presence"


def test_gate_relational_when_presence_ok():
    g = st.evaluate_gate(_sc(0.95, 0.10), st.GateThresholds())
    assert g.failing_axis == "relational"


def test_gate_skips_presence_when_none():
    # engineered/no-alias path: presence None -> only relational gated
    g = st.evaluate_gate(_sc(None, 0.60), st.GateThresholds())
    assert g.passed is True
    g2 = st.evaluate_gate(_sc(None, 0.10), st.GateThresholds())
    assert g2.failing_axis == "relational"
```

- [ ] **Step 2: Run to verify it fails** — box-safe `-k gate`. Expected: `ModuleNotFoundError: erkgbench.substrate_tuner`.

- [ ] **Step 3: Implement**

Create `erkgbench/substrate_tuner.py`:

```python
"""SP-B2 substrate staged tuner.

A deterministic staged-ejection optimizer: build a config on a cheap gold slice, score it with the
SP-A three-axis scorecard (erkgbench.substrate_eval.substrate_scorecard), escalate the config along
the failing axis, and promote the best-so-far config to the full build. The real build is injected
(build_and_score) so the whole control flow is pure + box-testable; build_and_score_real wires the
actual ingest_corpus + scorecard (Modal-smoke only). See
docs/superpowers/specs/2026-07-02-substrate-staged-tuner-design.md.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

from goldengraph.config import SubstrateConfig, for_profile, profile_corpus

from erkgbench.substrate_eval import LEVER_AXIS_MAP

_REFUTED = ("relation_reprompt", "rebel_fuse", "extract_recall")


@dataclass(frozen=True)
class GateThresholds:
    """Per-axis pass floors. Hypotheses tuned by running the harness; env-overridable by the caller."""
    presence_min: float = 0.90
    relational_f1_min: float = 0.50


@dataclass(frozen=True)
class GateResult:
    passed: bool
    failing_axis: str | None
    scorecard: dict


def evaluate_gate(scorecard: dict, thresholds: GateThresholds) -> GateResult:
    """Presence before relational (can't fix relational quality on absent entities). Presence is
    skipped when the scorecard's presence is None (engineered/no-alias path)."""
    presence = scorecard.get("presence")
    if presence is not None and presence["coverage"] < thresholds.presence_min:
        return GateResult(False, "presence", scorecard)
    if scorecard["relational"]["f1"] < thresholds.relational_f1_min:
        return GateResult(False, "relational", scorecard)
    return GateResult(True, None, scorecard)
```

- [ ] **Step 4: Run to verify passes** — box-safe `-k gate`. Expected: PASS (4).

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/substrate_tuner.py \
        packages/python/goldenmatch/benchmarks/er-kg-bench/tests/test_substrate_tuner.py
git commit -m "feat(erkgbench): GateThresholds + evaluate_gate (presence-before-relational) (SP-B2 task 1)"
```

---

### Task 2: `escalate` next-state ladder

**Files:**
- Modify: `erkgbench/substrate_tuner.py`
- Test: `tests/test_substrate_tuner.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_escalate_presence_enables_chunking_first():
    step = st.escalate(SubstrateConfig(), "presence", set())
    assert step is not None
    lever, cfg = step
    assert lever == "chunk_extract" and cfg.chunk_extract is True


def test_escalate_relational_ladder_bumps_xdoc_key_twice():
    tried = set()
    l1, c1 = st.escalate(SubstrateConfig(), "relational", tried)
    assert l1 == "xdoc_key" and c1.xdoc_key == "name_ci"
    l2, c2 = st.escalate(c1, "relational", tried)
    assert l2 == "xdoc_key" and c2.xdoc_key == "name_ci_type" and c2.entity_type_canon is True


def test_escalate_schema_canon_sets_vocab():
    # xdoc_key ladder + entity_type_canon exhausted -> schema_canon fires, setting BOTH fields
    c = SubstrateConfig(xdoc_key="name_ci_type", entity_type_canon=True)
    tried = {("xdoc_key", "name_ci"), ("xdoc_key", "name_ci_type"), ("entity_type_canon", "True")}
    step = st.escalate(c, "relational", tried, relation_vocab=("acquired",))
    assert step is not None
    lever, cfg = step
    assert lever == "schema_canon" and cfg.schema_canon is True and cfg.relation_vocab == ("acquired",)


def test_escalate_schema_canon_skipped_without_vocab():
    c = SubstrateConfig(xdoc_key="name_ci_type", entity_type_canon=True)
    tried = {("xdoc_key", "name_ci"), ("xdoc_key", "name_ci_type"), ("entity_type_canon", "True")}
    step = st.escalate(c, "relational", tried, relation_vocab=())  # no vocab -> schema_canon ineligible
    assert step is None


def test_escalate_skips_refuted():
    # presence levers: chunk_extract (ok), extract_recall (REFUTED), extractor. With chunking already on
    # and gliner tried, the only remaining is extract_recall which must be skipped -> None.
    c = SubstrateConfig(chunk_extract=True, extractor="gliner")
    step = st.escalate(c, "presence", {("chunk_extract", "True"), ("extractor", "gliner")})
    assert step is None


def test_escalate_returns_none_when_exhausted():
    c = SubstrateConfig(chunk_extract=True, extractor="gliner")
    step = st.escalate(c, "presence", {("chunk_extract", "True"), ("extractor", "gliner")})
    assert step is None


def test_escalate_returns_lever_and_config():
    step = st.escalate(SubstrateConfig(), "presence", set())
    assert isinstance(step, tuple) and step[0] == "chunk_extract" and isinstance(step[1], SubstrateConfig)
```

- [ ] **Step 2: Run to verify it fails** — box-safe `-k escalate`. Expected: FAIL — no `escalate`.

- [ ] **Step 3: Implement**

Add to `substrate_tuner.py`. Each lever's `_advance(config) -> (next_value_repr, new_config) | None`:

```python
def _advance_chunk_extract(c):
    if c.chunk_extract:
        return None
    return "True", replace(c, chunk_extract=True, chunk_sentences=6, chunk_overlap=2)


def _advance_extractor(c):
    if c.extractor != "api":
        return None
    return "gliner", replace(c, extractor="gliner")


def _advance_xdoc_key(c):
    nxt = {"": "name_ci", "name_ci": "name_ci_type"}.get(c.xdoc_key)
    if nxt is None:
        return None
    if nxt == "name_ci_type":
        return nxt, replace(c, xdoc_key=nxt, entity_type_canon=True)
    return nxt, replace(c, xdoc_key=nxt)


def _advance_entity_type_canon(c):
    if c.entity_type_canon:
        return None
    return "True", replace(c, entity_type_canon=True)


def _advance_schema_canon(c, relation_vocab):
    if c.schema_canon or not relation_vocab:
        return None
    return "True", replace(c, schema_canon=True, relation_vocab=tuple(relation_vocab))


#: lever -> its _advance. Refuted levers + relation_vocab (no self-ladder) are absent -> never advanced.
_ADVANCERS = {
    "chunk_extract": lambda c, v: _advance_chunk_extract(c),
    "extractor": lambda c, v: _advance_extractor(c),
    "xdoc_key": lambda c, v: _advance_xdoc_key(c),
    "entity_type_canon": lambda c, v: _advance_entity_type_canon(c),
    "schema_canon": lambda c, v: _advance_schema_canon(c, v),
}


def escalate(config, failing_axis, tried, *, relation_vocab=(), allow_refuted=False):
    """First eligible next-state advance along LEVER_AXIS_MAP[failing_axis]. Returns (lever, new_config)
    and records (lever, next_value_repr) in `tried` (sole mutator), or None when the axis is exhausted."""
    for lever in LEVER_AXIS_MAP.get(failing_axis, ()):
        if lever in _REFUTED and not allow_refuted:
            continue
        adv = _ADVANCERS.get(lever)
        if adv is None:
            continue
        step = adv(config, relation_vocab)
        if step is None:
            continue
        next_repr, new_config = step
        if (lever, next_repr) in tried:
            continue
        tried.add((lever, next_repr))
        return lever, new_config
    return None
```

- [ ] **Step 4: Run to verify passes** — box-safe `-k escalate`. Expected: PASS (7).

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/substrate_tuner.py \
        packages/python/goldenmatch/benchmarks/er-kg-bench/tests/test_substrate_tuner.py
git commit -m "feat(erkgbench): escalate next-state ladder, returns (lever, config), skips refuted (SP-B2 task 2)"
```

---

### Task 3: `RoundReport` + `TunerResult` + `run_staged`

**Files:**
- Modify: `erkgbench/substrate_tuner.py`
- Test: `tests/test_substrate_tuner.py`

- [ ] **Step 1: Write the failing tests**

```python
def _tiny_corpus():
    # docs/gold/aliases the fake ignores; run_staged only passes them through to build_and_score.
    return ["doc one.", "doc two."], [("Q1", "a", "d1")], {"Q1": ["a"]}


def test_run_staged_rejects_budget_below_one():
    docs, gold, al = _tiny_corpus()
    with pytest.raises(ValueError):
        st.run_staged(docs, gold, al, build_and_score=lambda cfg, ds: _sc(1.0, 1.0), budget=0)


def test_run_staged_passes_first_round():
    docs, gold, al = _tiny_corpus()
    calls = []

    def fake(cfg, ds):
        calls.append(ds)
        return _sc(0.95, 0.60)  # passes immediately

    res = st.run_staged(docs, gold, al, build_and_score=fake, budget=3)
    assert res.stopped_reason == "passed"
    assert len(res.trace) == 1
    assert len(calls) == 2  # 1 slice round + 1 full build


def test_run_staged_escalates_then_passes():
    docs, gold, al = _tiny_corpus()
    scores = iter([_sc(0.95, 0.10), _sc(0.95, 0.60)])  # fail relational, then pass after escalate

    res = st.run_staged(docs, gold, al, build_and_score=lambda cfg, ds: next(scores), budget=5)
    assert res.stopped_reason == "passed"
    assert len(res.trace) == 2
    assert res.trace[0].escalated_to == "xdoc_key"  # relational fail -> xdoc_key bump


def test_run_staged_budget_exhausted():
    docs, gold, al = _tiny_corpus()
    res = st.run_staged(docs, gold, al, build_and_score=lambda cfg, ds: _sc(0.95, 0.10), budget=2)
    assert res.stopped_reason == "budget" and len(res.trace) == 2


def test_run_staged_promotes_argmax_not_last():
    docs, gold, al = _tiny_corpus()
    # round0 high, then regressions; best-so-far must be round0's config, and the full build uses it.
    scores = iter([_sc(0.95, 0.80), _sc(0.95, 0.10), _sc(0.95, 0.10)])
    seen = []

    def fake(cfg, ds):
        seen.append(cfg)
        return next(scores)

    res = st.run_staged(docs, gold, al, build_and_score=fake, budget=3)
    # best config == the round-0 config (default from for_profile), full build invoked with it
    assert res.config == res.trace[0].config
    assert seen[-1] == res.config  # last call (the full build) used the argmax config


def test_run_staged_terminal_eject():
    docs, gold, al = _tiny_corpus()
    # force presence axis to fail with all presence levers pre-exhausted so escalate returns None
    cfg0 = SubstrateConfig(chunk_extract=True, extractor="gliner")
    res = st.run_staged(docs, gold, al, build_and_score=lambda cfg, ds: _sc(0.10, 0.95),
                        budget=5, initial_config=cfg0)
    assert res.stopped_reason == "exhausted"


def test_tuner_result_trace_is_serializable():
    docs, gold, al = _tiny_corpus()
    res = st.run_staged(docs, gold, al, build_and_score=lambda cfg, ds: _sc(0.95, 0.60), budget=1)
    r0 = res.trace[0]
    assert r0.round == 0 and isinstance(r0.config, SubstrateConfig) and "relational" in r0.scorecard
    assert r0.escalated_to is None
```

- [ ] **Step 2: Run to verify it fails** — box-safe `-k run_staged or tuner_result`. Expected: FAIL — no `run_staged`.

- [ ] **Step 3: Implement**

Add to `substrate_tuner.py`:

```python
@dataclass(frozen=True)
class RoundReport:
    round: int
    config: SubstrateConfig
    scorecard: dict
    gate: GateResult
    escalated_to: str | None


@dataclass(frozen=True)
class TunerResult:
    config: SubstrateConfig
    slice_scorecard: dict
    full_scorecard: dict
    trace: list = field(default_factory=list)
    stopped_reason: str = ""


def _score(scorecard: dict) -> float:
    """Round-ranking scalar: relational.f1 (+ presence.coverage when present). Higher wins."""
    s = scorecard["relational"]["f1"]
    presence = scorecard.get("presence")
    if presence is not None:
        s += presence["coverage"]
    return s


def run_staged(docs, gold, qid_aliases, *, build_and_score, thresholds=None, budget=5,
               relation_vocab=(), expect_homographs=False, has_known_schema=False,
               initial_config=None):
    """Slice-gated deterministic optimizer. Escalates on the failing axis up to `budget` rounds, then
    promotes the best-so-far (argmax _score) config to a full build. `build_and_score(config, dataset)`
    is the injection seam (dataset = (docs, gold, qid_aliases)). Raises ValueError on budget < 1."""
    if budget < 1:
        raise ValueError(f"budget must be >= 1, got {budget}")
    thresholds = thresholds or GateThresholds()
    if initial_config is None:
        initial_config = for_profile(
            profile_corpus(docs), has_known_schema=has_known_schema,
            expect_homographs=expect_homographs, relation_vocab=relation_vocab,
        )
    dataset = (docs, gold, qid_aliases)
    config = initial_config
    tried: set = set()
    rounds: list[RoundReport] = []
    stopped = "budget"
    for r in range(budget):
        sc = build_and_score(config, dataset)
        gate = evaluate_gate(sc, thresholds)
        step = None if gate.passed else escalate(config, gate.failing_axis, tried,
                                                 relation_vocab=relation_vocab)
        escalated_to, next_config = (None, None) if step is None else step
        rounds.append(RoundReport(r, config, sc, gate, escalated_to))
        if gate.passed:
            stopped = "passed"
            break
        if step is None:
            stopped = "exhausted"
            break
        config = next_config
    best = max(rounds, key=lambda rr: _score(rr.scorecard))
    full_sc = build_and_score(best.config, dataset)
    return TunerResult(best.config, best.scorecard, full_sc, rounds, stopped)
```

- [ ] **Step 4: Run to verify passes** — box-safe `-k "run_staged or tuner_result"`. Expected: PASS (7).

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/substrate_tuner.py \
        packages/python/goldenmatch/benchmarks/er-kg-bench/tests/test_substrate_tuner.py
git commit -m "feat(erkgbench): run_staged slice-gated loop + best-so-far argmax + budget guard (SP-B2 task 3)"
```

---

### Task 4: `build_and_score_real` adapter (thin, Modal-smoke only — NOT TDD)

**Files:**
- Modify: `erkgbench/substrate_tuner.py`

- [ ] **Step 1: Implement the adapter** (no unit test — needs native store + LLM; validated by a Modal smoke, not the box)

Add to `substrate_tuner.py`:

```python
def build_and_score_real(config, dataset):
    """Real build_and_score: under config.apply(), build the graph over the dataset docs and score it
    with the SP-A three-axis scorecard. Needs the native store + an LLM -> Modal/CI only, NOT box-safe.
    Kept thin; the pure harness above is the tested surface."""
    from erkgbench import substrate_eval
    from erkgbench.run_substrate_eval import _build_graph_from_documents

    docs, gold, qid_aliases = dataset

    class _Doc:  # _build_graph_from_documents wants .text/.id; wrap raw (text, id) pairs
        __slots__ = ("text", "id")

        def __init__(self, text, doc_id):
            self.text, self.id = text, doc_id

    with config.apply():
        documents = [_Doc(t, f"d{i}") for i, t in enumerate(docs)] if not hasattr(docs[0], "text") else docs
        graph = _build_graph_from_documents(documents)
    return substrate_eval.substrate_scorecard(graph, gold, qid_aliases)
```

> NOTE: the exact `dataset`/`_Doc` shape must match whatever the Modal smoke passes (wiki `load_wiki_corpus` yields `Document` objects with `.text`/`.id` + real `gold`/`qid_aliases`). Adjust the wrapper at smoke time; this adapter is deliberately NOT locked by a box test. Do NOT import it at module top in a way that forces `run_substrate_eval` (which pulls native) into the box test path — it is imported INSIDE the function so the pure harness stays box-safe.

- [ ] **Step 2: Static-check** — confirm the module imports cleanly box-safe (the real adapter's heavy imports are function-local):

```bash
cd /d/show_case/gg-local-llm/packages/python/goldenmatch/benchmarks/er-kg-bench
PYTHONPATH="$PWD" POLARS_SKIP_CPU_CHECK=1 GOLDENMATCH_NATIVE=0 \
  /d/show_case/goldenmatch/.venv/Scripts/python.exe -c "import erkgbench.substrate_tuner as st; print('import-ok', hasattr(st, 'build_and_score_real'))"
```
Expected: `import-ok True` with no native/LLM import triggered.

- [ ] **Step 3: Commit**

```bash
git add packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/substrate_tuner.py
git commit -m "feat(erkgbench): thin build_and_score_real adapter (Modal-smoke only, heavy imports fn-local) (SP-B2 task 4)"
```

---

### Task 5: Finish the branch

- [ ] **Step 1: Rebase onto main** (SP-B1 #1373 is merged — should already be done pre-Task-1; re-confirm clean):
  ```bash
  unset GH_TOKEN; export GH_TOKEN=$(gh auth token --user benzsevern)
  git fetch origin main && git rebase origin/main
  ```
- [ ] **Step 2: Full file green** — box-safe command on the whole file; expect all green (Tasks 1-3 = 18 tests).
- [ ] **Step 3: Lint** — `ruff check` the two files; fix any finding.
  ```bash
  /d/show_case/goldenmatch/.venv/Scripts/python.exe -m ruff check packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/substrate_tuner.py packages/python/goldenmatch/benchmarks/er-kg-bench/tests/test_substrate_tuner.py
  ```
- [ ] **Step 4: Spec status** — flip the spec `Status:` to `implemented`; commit.
- [ ] **Step 5: Push + PR** — `git push -u origin feat/substrate-tuner`; open PR base `main`; arm `gh pr merge <N> --repo benseverndev-oss/goldenmatch --auto` and STOP (no CI poll).
- [ ] **Step 6: Update memory** — append SP-B2 shipped to `project_goldengraph_local_oss_llm_lane.md` (deterministic staged tuner; the Modal smoke of `build_and_score_real` + SP-C LLM loop are the follow-ons).

---

## Notes for the implementer

- **Box-safe only.** Run just `tests/test_substrate_tuner.py`. The pure harness has NO native/LLM/Modal dependency — that is the whole point of the injected `build_and_score`. If a test triggers a native/polars import, something leaked; the real adapter's heavy imports MUST stay function-local.
- **`escalate` never re-arms a refuted lever** unless `allow_refuted=True` (which nothing in SP-B2 passes). `relation_reprompt`/`rebel_fuse`/`extract_recall` have no `_ADVANCERS` entry AND are in `_REFUTED` — double-guarded.
- **`tried` is a `(lever, next_value_repr)` set**, keyed by the RESULTING state so a ladder lever (`xdoc_key`) can advance multiple times. Never key it by lever name.
- **best-so-far, not last.** `run_staged` returns the argmax-`_score` config, and the full build runs on THAT, on every exit (`passed`/`budget`/`exhausted`).
- **The real adapter is not unit-tested by design.** Its correctness is a Modal smoke (a follow-up), because it needs the native store + an LLM. Don't try to make it box-testable.
