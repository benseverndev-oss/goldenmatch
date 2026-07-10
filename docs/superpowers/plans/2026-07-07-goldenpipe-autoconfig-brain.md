# goldenpipe auto-config brain (slice 1) — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace goldenpipe's static `_auto_config()` with a plan-first "brain": profile the input up front → run a rule table → produce a `PipePlan` (stages + config + `rule_name` + confidence + evidence), then materialize it to the pipeline config.

**Architecture:** A portable, Polars/Pydantic-free decision core (`PipeProfile` → `PipePlan` via a pure rule table) bracketed by host glue (Polars/InferMap profiling in; Pydantic `PipelineConfig` out). Python prototype = slice 1 of the Python→Rust→cross-surface arc; the core is written to port mechanically to `goldenpipe-core` later.

**Tech Stack:** Python 3 (frozen dataclasses, Polars, Pydantic, InferMap `detect_domain_detailed`), pytest.

**Spec:** `docs/superpowers/specs/2026-07-07-goldenpipe-autoconfig-brain-design.md`

**Reference skill:** @superpowers:test-driven-development

---

## Environment & Conventions

**Repo:** `D:\show_case\gg-local-llm`, branch `feat/goldenpipe-autoconfig-brain` (off fresh `origin/main`, spec committed).

**THIS SLICE IS FULLY BOX-RUNNABLE** — real Python TDD (red→green), unlike the CI-only TS work. Env:
```bash
INTERP="D:/show_case/goldenmatch/.venv/Scripts/python.exe"
export PYTHONPATH="packages/python/goldenpipe:packages/python/infermap:packages/python/goldencheck-types"
export POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8
```
Run pytest via `"$INTERP" -m pytest <path> -q`; ruff via `"$INTERP" -m ruff check <file>`. `cd "D:/show_case/gg-local-llm"` first.

**Installed-metadata caveat (Task 4):** `StageRegistry.discover()` reads entry-points from *installed* package metadata, so a new `pyproject.toml` entry-point is NOT visible on the box without a reinstall. **Tests therefore register `infer_schema` explicitly** on the registry they build (install-independent + deterministic); the entry-point is the *production* wiring, verified by CI's fresh install. Also note: on the box, `discover()` currently yields `{goldencheck.scan, goldenmatch.dedupe, goldenmatch.identity_resolve, goldenanalysis.report, load}` — `goldenflow.transform` isn't installed in this venv — so tests must NOT assume the real registry has every sibling; they pass a controlled `available`/registry.

**Git:** benzsevern (`unset GH_TOKEN; gh auth switch --user benzsevern; export GH_TOKEN=$(gh auth token --user benzsevern)`). Merge-queue — `--auto --squash`, no `--delete-branch`. Trailer:
```
Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44
```

---

## File Structure

| File | Responsibility | Action |
| --- | --- | --- |
| `packages/python/goldenpipe/goldenpipe/autoconfig_planner.py` | portable core: `PipeProfile`, `PlannedStage`, `PipePlan`, `PipePlannerRule`, `plan_pipeline` (NO Polars/Pydantic) | Create |
| `packages/python/goldenpipe/goldenpipe/autoconfig_planner_rules.py` | the 3 rules + `DEFAULT_RULES` (portable) | Create |
| `packages/python/goldenpipe/goldenpipe/autoconfig_glue.py` | host glue: `profile_context(ctx)`, `plan_to_config(plan, available, identity_opts)` | Create |
| `packages/python/goldenpipe/pyproject.toml` | register `infer_schema` entry-point | Modify |
| `packages/python/goldenpipe/goldenpipe/pipeline.py` | new `_plan_config(self, ctx)` brain + `run()` call site + `_last_plan` init (`_auto_config` untouched) | Modify |
| `packages/python/goldenpipe/tests/test_autoconfig_planner.py` | core + rules unit tests | Create |
| `packages/python/goldenpipe/tests/test_autoconfig_glue.py` | glue + wiring/integration tests | Create |

---

## Task 1: Portable decision core (structs + `plan_pipeline`)

**Files:** Create `goldenpipe/autoconfig_planner.py`; Test `tests/test_autoconfig_planner.py`.

- [ ] **Step 1: Write the failing test** (create `tests/test_autoconfig_planner.py`):
```python
from goldenpipe.autoconfig_planner import (
    PipeProfile, PlannedStage, PipePlan, PipePlannerRule, plan_pipeline,
)


def _profile(**kw):
    base = dict(n_rows=100, n_cols=3, column_names=("a", "b", "c"),
                dtypes=("String", "Int64", "String"),
                inferred_domain=None, domain_confidence=0.0)
    base.update(kw)
    return PipeProfile(**base)


def test_plan_pipeline_first_match_wins_else_default():
    fired = PipePlannerRule(
        rule_name="fired",
        predicate=lambda p: p.n_rows == 100,
        action=lambda p: PipePlan(stages=(PlannedStage("x", {}),), rule_name="fired",
                                  confidence=0.9, evidence={"n_rows": p.n_rows}),
    )
    plan = plan_pipeline(_profile(), rules=[fired])
    assert plan.rule_name == "fired"
    assert plan.stages == (PlannedStage("x", {}),)
    assert plan.evidence == {"n_rows": 100}


def test_plan_pipeline_falls_through_to_default():
    never = PipePlannerRule("never", lambda p: False,
                            lambda p: PipePlan((), "never", 0.0, {}))
    plan = plan_pipeline(_profile(), rules=[never])
    assert plan.rule_name == "default"
    # default plan is the standard 3-stage shape
    assert tuple(s.name for s in plan.stages) == (
        "goldencheck.scan", "goldenflow.transform", "goldenmatch.dedupe",
    )


def test_structs_are_frozen():
    import dataclasses, pytest
    p = _profile()
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.n_rows = 5  # type: ignore[misc]
```

- [ ] **Step 2: Run to verify FAIL**
```bash
"$INTERP" -m pytest packages/python/goldenpipe/tests/test_autoconfig_planner.py -q
```
Expect: ImportError (module doesn't exist).

- [ ] **Step 3: Create `goldenpipe/autoconfig_planner.py`:**
```python
"""Plan-first auto-config decision core (portable — NO Polars/Pydantic).

The pyo3-free-portable kernel: PipeProfile (in) -> PipePlan (out) via a pure
rule table. Host glue (Polars profiling, Pydantic config) lives in
`autoconfig_glue.py`. Mirrors goldenmatch's autoconfig_planner (PlannerRule +
first-match plan) so the later `goldenpipe-core` Rust port is mechanical.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class PipeProfile:
    """Cheap, up-front signals — no stage execution required."""
    n_rows: int
    n_cols: int
    column_names: tuple[str, ...]
    dtypes: tuple[str, ...]
    inferred_domain: str | None
    domain_confidence: float


@dataclass(frozen=True)
class PlannedStage:
    name: str            # EXACT registry name (e.g. "goldencheck.scan", "infer_schema")
    config: dict         # per-stage config


@dataclass(frozen=True)
class PipePlan:
    stages: tuple[PlannedStage, ...]
    rule_name: str
    confidence: float
    evidence: dict


Predicate = Callable[[PipeProfile], bool]
Action = Callable[[PipeProfile], "PipePlan"]


@dataclass(frozen=True)
class PipePlannerRule:
    rule_name: str
    predicate: Predicate
    action: Action


def default_evidence(p: PipeProfile) -> dict:
    """Signal snapshot attached to every plan (evidence for humans/telemetry)."""
    return {
        "n_rows": p.n_rows,
        "n_cols": p.n_cols,
        "inferred_domain": p.inferred_domain,
        "domain_confidence": p.domain_confidence,
    }


def _default_plan(p: PipeProfile) -> PipePlan:
    """The current static shape: scan -> flow -> dedupe (no infer_schema)."""
    return PipePlan(
        stages=(
            PlannedStage("goldencheck.scan", {}),
            PlannedStage("goldenflow.transform", {}),
            PlannedStage("goldenmatch.dedupe", {}),
        ),
        rule_name="default",
        confidence=0.7,
        evidence=default_evidence(p),
    )


def plan_pipeline(
    profile: PipeProfile,
    rules: Sequence[PipePlannerRule] | None = None,
) -> PipePlan:
    """First matching rule's action builds the plan; else the default shape."""
    if rules is None:
        from goldenpipe.autoconfig_planner_rules import DEFAULT_RULES
        rules = DEFAULT_RULES
    for rule in rules:
        if rule.predicate(profile):
            return rule.action(profile)
    return _default_plan(profile)
```
(Import only `dataclass` from dataclasses — no `field`; ruff F401 would flag an unused import.)

- [ ] **Step 4: Run to verify PASS** + ruff
```bash
"$INTERP" -m pytest packages/python/goldenpipe/tests/test_autoconfig_planner.py -q
"$INTERP" -m ruff check packages/python/goldenpipe/goldenpipe/autoconfig_planner.py packages/python/goldenpipe/tests/test_autoconfig_planner.py
```
Expect: 3 passed; ruff clean.

- [ ] **Step 5: Commit**
```bash
cd "D:/show_case/gg-local-llm"
git add packages/python/goldenpipe/goldenpipe/autoconfig_planner.py packages/python/goldenpipe/tests/test_autoconfig_planner.py
git commit -m "feat(goldenpipe): portable auto-config decision core (PipeProfile/PipePlan/plan_pipeline)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44"
```

**Report:** `DONE`/`BLOCKED`, pytest summary, SHA.

---

## Task 2: The rule table

**Files:** Create `goldenpipe/autoconfig_planner_rules.py`; extend `tests/test_autoconfig_planner.py`.

- [ ] **Step 1: Append failing tests** to `tests/test_autoconfig_planner.py`:
```python
from goldenpipe.autoconfig_planner_rules import DEFAULT_RULES  # noqa: E402


def test_rule_pathological_skips_dedupe():
    plan = plan_pipeline(_profile(n_rows=1))
    assert plan.rule_name == "pathological"
    assert tuple(s.name for s in plan.stages) == (
        "goldencheck.scan", "goldenflow.transform",
    )
    assert plan.confidence == 1.0


def test_rule_confident_schema_prepends_infer_schema():
    plan = plan_pipeline(_profile(inferred_domain="finance", domain_confidence=0.8))
    assert plan.rule_name == "confident_schema"
    assert tuple(s.name for s in plan.stages) == (
        "infer_schema", "goldencheck.scan", "goldenflow.transform", "goldenmatch.dedupe",
    )
    assert plan.stages[0].config == {"domain": "finance"}
    assert plan.confidence == 0.8


def test_rule_weak_domain_is_default():
    # domain present but below threshold -> default (no infer_schema)
    plan = plan_pipeline(_profile(inferred_domain="finance", domain_confidence=0.4))
    assert plan.rule_name == "default"
    assert all(s.name != "infer_schema" for s in plan.stages)


def test_default_rules_is_the_module_table():
    # sanity: plan_pipeline with no rules uses DEFAULT_RULES
    assert plan_pipeline(_profile(n_rows=1)).rule_name == "pathological"
    assert len(DEFAULT_RULES) >= 2  # pathological + confident_schema (+ default is fallthrough)
```

- [ ] **Step 2: Run to verify FAIL**
```bash
"$INTERP" -m pytest packages/python/goldenpipe/tests/test_autoconfig_planner.py -q -k "rule or default_rules"
```
Expect: ImportError (`autoconfig_planner_rules`).

- [ ] **Step 3: Create `goldenpipe/autoconfig_planner_rules.py`:**
```python
"""Concrete planner rules for the goldenpipe auto-config brain (slice 1).

Ordered; first match wins (see plan_pipeline). All predicates read only cheap
PipeProfile signals. Portable — no Polars/Pydantic.
"""
from __future__ import annotations

from goldenpipe.autoconfig_planner import (
    PipePlan, PipePlannerRule, PipeProfile, PlannedStage, default_evidence,
)

_CONFIDENT_DOMAIN_THRESHOLD = 0.5


# ── Rule 1: pathological (n_rows <= 1) ──────────────────────────────────────
def _is_pathological(p: PipeProfile) -> bool:
    return p.n_rows <= 1


def _pathological_plan(p: PipeProfile) -> PipePlan:
    # Nothing to dedupe with <=1 row: skip dedupe (plan-visible row_count_gate).
    return PipePlan(
        stages=(
            PlannedStage("goldencheck.scan", {}),
            PlannedStage("goldenflow.transform", {}),
        ),
        rule_name="pathological",
        confidence=1.0,
        evidence=default_evidence(p),
    )


rule_pathological = PipePlannerRule("pathological", _is_pathological, _pathological_plan)


# ── Rule 2: confident_schema (domain confidently inferred) ──────────────────
def _is_confident_schema(p: PipeProfile) -> bool:
    return p.inferred_domain is not None and p.domain_confidence >= _CONFIDENT_DOMAIN_THRESHOLD


def _confident_schema_plan(p: PipeProfile) -> PipePlan:
    # Run schema inference first (pinned to the detected domain) so downstream
    # stages get typed columns.
    return PipePlan(
        stages=(
            PlannedStage("infer_schema", {"domain": p.inferred_domain}),
            PlannedStage("goldencheck.scan", {}),
            PlannedStage("goldenflow.transform", {}),
            PlannedStage("goldenmatch.dedupe", {}),
        ),
        rule_name="confident_schema",
        confidence=p.domain_confidence,
        evidence=default_evidence(p),
    )


rule_confident_schema = PipePlannerRule(
    "confident_schema", _is_confident_schema, _confident_schema_plan,
)


# The default (fallthrough) shape lives in plan_pipeline._default_plan.
DEFAULT_RULES: tuple[PipePlannerRule, ...] = (
    rule_pathological,
    rule_confident_schema,
)
```

- [ ] **Step 4: Run to verify PASS** + ruff
```bash
"$INTERP" -m pytest packages/python/goldenpipe/tests/test_autoconfig_planner.py -q
"$INTERP" -m ruff check packages/python/goldenpipe/goldenpipe/autoconfig_planner_rules.py
```
Expect: all pass (7 tests); ruff clean.

- [ ] **Step 5: Commit**
```bash
git add packages/python/goldenpipe/goldenpipe/autoconfig_planner_rules.py packages/python/goldenpipe/tests/test_autoconfig_planner.py
git commit -m "feat(goldenpipe): auto-config rule table (pathological / confident_schema / default)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44"
```

**Report:** `DONE`/`BLOCKED`, pytest summary, SHA.

---

## Task 3: Host glue — `profile_context` + `plan_to_config`

**Files:** Create `goldenpipe/autoconfig_glue.py`; Test `tests/test_autoconfig_glue.py`.

- [ ] **Step 1: Write failing tests** (create `tests/test_autoconfig_glue.py`):
```python
import polars as pl

from goldenpipe.models.context import PipeContext
from goldenpipe.models.config import PipelineConfig
from goldenpipe.autoconfig_planner import PipePlan, PlannedStage
from goldenpipe.autoconfig_glue import profile_context, plan_to_config


def test_profile_context_materialized_df_detects_finance():
    df = pl.DataFrame({"account_number": ["A1", "A2"], "currency": ["USD", "EUR"]})
    ctx = PipeContext(df=df)
    prof = profile_context(ctx)
    assert prof.n_rows == 2
    assert prof.n_cols == 2
    assert prof.column_names == ("account_number", "currency")
    assert prof.inferred_domain == "finance"
    assert prof.domain_confidence > 0.0


def test_profile_context_no_domain_gives_zero_confidence():
    df = pl.DataFrame({"x": [1, 2], "y": [3, 4]})
    prof = profile_context(PipeContext(df=df))
    # unremarkable columns -> no confident domain
    assert prof.domain_confidence == 0.0 or prof.inferred_domain is None
    if prof.inferred_domain is None:
        assert prof.domain_confidence == 0.0


def test_profile_context_engine_resident_is_degraded():
    ctx = PipeContext(df=None)
    ctx.metadata["input_rows"] = 5000
    prof = profile_context(ctx)
    assert prof.n_rows == 5000
    assert prof.column_names == ()
    assert prof.inferred_domain is None
    assert prof.domain_confidence == 0.0


def test_plan_to_config_filters_by_availability_and_builds_stagespecs():
    plan = PipePlan(
        stages=(
            PlannedStage("infer_schema", {"domain": "finance"}),
            PlannedStage("goldencheck.scan", {}),
            PlannedStage("missing.stage", {}),
        ),
        rule_name="confident_schema", confidence=0.8, evidence={},
    )
    available = {"infer_schema": object(), "goldencheck.scan": object()}
    cfg = plan_to_config(plan, available, identity_opts=None)
    assert isinstance(cfg, PipelineConfig)
    assert cfg.pipeline == "auto"
    uses = [s.use for s in cfg.stages]
    assert uses == ["infer_schema", "goldencheck.scan"]  # missing.stage dropped, order kept
    assert cfg.stages[0].config == {"domain": "finance"}


def test_plan_to_config_appends_identity_when_opts_and_available():
    plan = PipePlan((PlannedStage("goldencheck.scan", {}),), "default", 0.7, {})
    available = {"goldencheck.scan": object(), "goldenmatch.identity_resolve": object()}
    cfg = plan_to_config(plan, available, identity_opts={"kinds": ["email"]})
    assert [s.use for s in cfg.stages] == ["goldencheck.scan", "goldenmatch.identity_resolve"]
    assert cfg.stages[-1].config == {"kinds": ["email"]}
```

- [ ] **Step 2: Run to verify FAIL**
```bash
"$INTERP" -m pytest packages/python/goldenpipe/tests/test_autoconfig_glue.py -q
```
Expect: ImportError (`autoconfig_glue`).

- [ ] **Step 3: Create `goldenpipe/autoconfig_glue.py`:**
```python
"""Host glue for the auto-config brain (Polars/InferMap in; Pydantic out).

NOT ported to Rust — the future `goldenpipe-core` kernel is the portable core
(autoconfig_planner). This bracket does the impure extraction + materialization.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from goldenpipe.autoconfig_planner import PipePlan, PipeProfile
from goldenpipe.models.config import PipelineConfig, StageSpec
from goldenpipe.models.context import PipeContext


def profile_context(ctx: PipeContext) -> PipeProfile:
    """Build the portable PipeProfile from a loaded context (cheap, no row scan)."""
    df = ctx.df
    if df is None:
        # Engine-resident (DuckDB) — degrade rather than force materialization.
        return PipeProfile(
            n_rows=int(ctx.metadata.get("input_rows", 0)),
            n_cols=0,
            column_names=(),
            dtypes=(),
            inferred_domain=None,
            domain_confidence=0.0,
        )

    column_names = tuple(df.columns)
    dtypes = tuple(str(dt) for dt in df.dtypes)

    # InferMap detect reads only `.columns` (attribute) — no row scan. Pass a
    # `.columns`-bearing object, NOT a dict (a dict raises AttributeError).
    from infermap import detect_domain_detailed

    det = detect_domain_detailed(SimpleNamespace(columns=list(column_names)))
    inferred_domain = det.domain
    domain_confidence = det.score if det.domain is not None else 0.0

    return PipeProfile(
        n_rows=len(df),
        n_cols=len(column_names),
        column_names=column_names,
        dtypes=dtypes,
        inferred_domain=inferred_domain,
        domain_confidence=domain_confidence,
    )


def plan_to_config(
    plan: PipePlan,
    available: Any,           # anything supporting `name in available` (registry dict / set)
    identity_opts: dict | None,
) -> PipelineConfig:
    """Materialize a PipePlan into a Pydantic PipelineConfig, filtering by availability."""
    specs: list[StageSpec] = [
        StageSpec(use=s.name, config=dict(s.config))
        for s in plan.stages
        if s.name in available
    ]
    if identity_opts and "goldenmatch.identity_resolve" in available:
        specs.append(StageSpec(use="goldenmatch.identity_resolve", config={**identity_opts}))
    return PipelineConfig(pipeline="auto", stages=specs)
```
> Verify `PipeContext` accepts `df=None` + a `metadata` dict (it does — dataclass
> with `df: pl.DataFrame | None = None`, `metadata: dict = field(default_factory=dict)`).
> Verify `detect_domain_detailed(SimpleNamespace(columns=[...]))` returns a
> `DetectionResult` with `.domain`/`.score` — if the Python API rejects a
> non-DataFrame, fall back to passing `df` directly (it also has `.columns`) and
> note it; either works since detect reads only `.columns`.

- [ ] **Step 4: Run to verify PASS** + ruff
```bash
"$INTERP" -m pytest packages/python/goldenpipe/tests/test_autoconfig_glue.py -q
"$INTERP" -m ruff check packages/python/goldenpipe/goldenpipe/autoconfig_glue.py packages/python/goldenpipe/tests/test_autoconfig_glue.py
```
Expect: 5 passed; ruff clean. (If `SimpleNamespace` detect raises, switch to `detect_domain_detailed(df)` and re-run — the finance test still asserts domain=="finance".)

- [ ] **Step 5: Commit**
```bash
git add packages/python/goldenpipe/goldenpipe/autoconfig_glue.py packages/python/goldenpipe/tests/test_autoconfig_glue.py
git commit -m "feat(goldenpipe): auto-config host glue (profile_context + plan_to_config)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44"
```

**Report:** `DONE`/`BLOCKED`, pytest summary, whether `SimpleNamespace` or `df` was used for detect, SHA.

---

## Task 4: Register `infer_schema` (entry-point)

**Files:** Modify `packages/python/goldenpipe/pyproject.toml`. Box: TOML-eye-check (production wiring; CI's fresh install activates it).

- [ ] **Step 1: Add the entry-point.** In `pyproject.toml`, under
`[project.entry-points."goldenpipe.stages"]` (after the existing `goldenanalysis.report` line), add:
```toml
infer_schema = "goldenpipe.stages.infer_schema:infer_schema_stage"
```
(Bare key `infer_schema` — no dot, no quotes needed, but quoting is harmless. Target verified: `goldenpipe/stages/infer_schema.py` exports `infer_schema_stage`.)

- [ ] **Step 2: Verify the target resolves** (box-runnable import, independent of entry-point metadata):
```bash
PYTHONPATH="packages/python/goldenpipe:packages/python/infermap:packages/python/goldencheck-types" POLARS_SKIP_CPU_CHECK=1 "$INTERP" -c "from goldenpipe.stages.infer_schema import infer_schema_stage; print('target OK:', infer_schema_stage.info.name)"
```
Expect: `target OK: infer_schema`. (This proves the entry-point target is importable; the entry-point itself goes live on CI's fresh install/`uv sync`.)

- [ ] **Step 3: Commit**
```bash
git add packages/python/goldenpipe/pyproject.toml
git commit -m "build(goldenpipe): register infer_schema stage entry-point (Python parity with TS)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44"
```

**Report:** `DONE`/`BLOCKED`, the target-OK output, SHA.

---

## Task 5: Wire the planner into `run()` (as a NEW method — do NOT touch `_auto_config`)

**Files:** Modify `packages/python/goldenpipe/goldenpipe/pipeline.py`. Box: eye + the integration test (Task 6) verifies.

> **Why a new method, not a signature change to `_auto_config`:** `_auto_config()`
> has other no-arg callers that must keep the OLD static behavior — production
> `core/_planner_json.py` (the cross-surface parity bridge `auto_config_json`,
> which has no data context and mirrors `goldenpipe-core/src/json.rs`) and
> `tests/test_pipeline.py::test_auto_config`. Changing its arity breaks both, and
> feeding the JSON bridge an empty ctx would silently regress its output
> (n_rows=0 → `pathological` → dedupe dropped). So `_auto_config` stays untouched
> (the data-less static default), and the brain is a **new** `_plan_config(ctx)`
> that only `run()` calls (it has the loaded ctx). The JSON bridge keeps mirroring
> the static planner until the brain itself is ported to `goldenpipe-core` (a later
> slice), at which point the bridge moves too.

- [ ] **Step 1: Add `_last_plan` init.** In `Pipeline.__init__`, after `self._identity_opts = identity_opts`, add:
```python
        self._last_plan = None  # the PipePlan from the most recent brain run
```

- [ ] **Step 2: Add the brain method** `_plan_config` (leave `_auto_config` exactly as-is). Add it right after the existing `_auto_config` method:
```python
    def _plan_config(self, ctx: PipeContext) -> PipelineConfig:
        """Plan-first auto-config brain: profile the loaded ctx -> rule table ->
        materialized PipelineConfig. Only called from run() (needs the loaded ctx).
        `_auto_config` remains the data-less static default for the JSON parity bridge.
        """
        from goldenpipe.autoconfig_planner import plan_pipeline
        from goldenpipe.autoconfig_glue import profile_context, plan_to_config

        profile = profile_context(ctx)
        plan = plan_pipeline(profile)
        self._last_plan = plan
        return plan_to_config(plan, self._registry.list_all(), self._identity_opts)
```
(Lazy imports keep the planner modules off the import path for callers who supply
an explicit config or use the static `_auto_config`.)

- [ ] **Step 3: Update the `run()` call site.** Change the line in `run()`:
```python
        config = self._config or self._auto_config()
```
to:
```python
        config = self._config or self._plan_config(ctx)
```
(This is the ONLY line that switches to the brain. `_auto_config()`'s other
callers — `_planner_json.py`, `test_pipeline.py` — keep calling the untouched
static method.)

- [ ] **Step 4: Confirm the blast radius is exactly as expected.**
```bash
grep -rn "_auto_config\|_plan_config" packages/python/goldenpipe/goldenpipe packages/python/goldenpipe/tests | grep -v "def _auto_config\|def _plan_config"
```
Expect: `_plan_config` appears ONLY at the new `run()` call site; `_auto_config()`
still appears at `core/_planner_json.py:~148` and `tests/test_pipeline.py:~45`
(both INTENTIONALLY unchanged — they use the static default). Do NOT edit those.
If `_auto_config` appears at any NEW site you introduced, revert it.

- [ ] **Step 5: Run the existing goldenpipe pipeline tests** (regression — the wiring must not break the default path):
```bash
"$INTERP" -m pytest packages/python/goldenpipe/tests/ -q -k "pipeline or auto_config or engine" 2>&1 | tail -15
"$INTERP" -m ruff check packages/python/goldenpipe/goldenpipe/pipeline.py
```
Expect: pass (or only pre-existing unrelated failures). If a test asserted the exact old static `_auto_config()` output and now gets a planned config, that's expected — reconcile it (the default-path plan for a normal df should still yield scan→flow→dedupe filtered by availability). Report any test that changed behavior.

- [ ] **Step 6: Commit**
```bash
git add packages/python/goldenpipe/goldenpipe/pipeline.py
git commit -m "feat(goldenpipe): wire plan-first brain into _auto_config (stash _last_plan)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44"
```

**Report:** `DONE`/`BLOCKED`, regression result (esp. any behavior-changed test), SHA.

---

## Task 6: Integration test (the wiring, box-runnable + deterministic)

**Files:** Extend `tests/test_autoconfig_glue.py` (or a new `tests/test_autoconfig_integration.py`). Box-runnable via a controlled registry (register stub stages — install-independent).

- [ ] **Step 1: Add the integration test.** Append to `tests/test_autoconfig_glue.py`:
```python
from goldenpipe.pipeline import Pipeline
from goldenpipe.engine.registry import StageRegistry
from goldenpipe.models.context import StageResult, StageStatus


def _stub_stage(name: str, produces=(), consumes=()):
    """Minimal registrable stage — the planner cares about names, not behavior."""
    class _S:
        info = type("Info", (), {"name": name, "produces": list(produces),
                                 "consumes": list(consumes)})()
        def validate(self, ctx): ...
        def run(self, ctx) -> StageResult:  # noqa: ANN001
            return StageResult(status=StageStatus.SUCCESS)
    return _S()


def _registry_with(*names) -> StageRegistry:
    r = StageRegistry()
    for n in names:
        r.register(_stub_stage(n))
    return r


def _finance_df():
    import polars as pl
    return pl.DataFrame({"account_number": ["A1", "A2"], "currency": ["USD", "EUR"]})


def test_autoconfig_confident_df_includes_infer_schema():
    reg = _registry_with("infer_schema", "goldencheck.scan",
                         "goldenflow.transform", "goldenmatch.dedupe")
    eng = Pipeline(registry=reg)
    ctx = PipeContext(df=_finance_df())
    cfg = eng._plan_config(ctx)
    assert eng._last_plan.rule_name == "confident_schema"
    assert [s.use for s in cfg.stages][0] == "infer_schema"
    assert cfg.stages[0].config == {"domain": "finance"}


def test_autoconfig_one_row_df_skips_dedupe():
    import polars as pl
    reg = _registry_with("goldencheck.scan", "goldenflow.transform", "goldenmatch.dedupe")
    eng = Pipeline(registry=reg)
    ctx = PipeContext(df=pl.DataFrame({"a": [1]}))
    cfg = eng._plan_config(ctx)
    assert eng._last_plan.rule_name == "pathological"
    assert "goldenmatch.dedupe" not in [s.use for s in cfg.stages]


def test_autoconfig_missing_infer_schema_degrades():
    # confident df but infer_schema NOT in registry -> it's dropped, no crash.
    reg = _registry_with("goldencheck.scan", "goldenflow.transform", "goldenmatch.dedupe")
    eng = Pipeline(registry=reg)
    cfg = eng._plan_config(PipeContext(df=_finance_df()))
    assert eng._last_plan.rule_name == "confident_schema"  # plan still says confident
    assert "infer_schema" not in [s.use for s in cfg.stages]  # but it's filtered out
```
> `Pipeline(registry=reg)` skips `discover()` (a registry was supplied), so the
> stub registry is authoritative — install-independent + deterministic on the box.
> Verify `StageResult`/`StageStatus`/`StageRegistry.register` import paths against
> the real modules; adjust the stub's `info` shape to match `StageInfo` if the
> registry validates it on `register` (read `engine/registry.py::register`).

- [ ] **Step 2: Run** + ruff
```bash
"$INTERP" -m pytest packages/python/goldenpipe/tests/test_autoconfig_glue.py -q
"$INTERP" -m ruff check packages/python/goldenpipe/tests/test_autoconfig_glue.py
```
Expect: all pass (8 tests). If `register()` rejects the stub (validates StageInfo), adjust the stub to satisfy it (read the register signature) — do NOT weaken the assertions.

- [ ] **Step 3: Full goldenpipe planner-surface run** (all new tests together):
```bash
"$INTERP" -m pytest packages/python/goldenpipe/tests/test_autoconfig_planner.py packages/python/goldenpipe/tests/test_autoconfig_glue.py -q
```
Expect: all pass (~15 tests).

- [ ] **Step 4: Commit**
```bash
git add packages/python/goldenpipe/tests/test_autoconfig_glue.py
git commit -m "test(goldenpipe): auto-config brain integration (confident/pathological/degrade)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44"
```

**Report:** `DONE`/`BLOCKED`, pytest summary, whether the stub needed adjusting for `register()`, SHA.

---

## Task 7: Full regression + push + PR + arm (controller runs this)

**Files:** none.

- [ ] **Step 1: Full goldenpipe test suite** (catch any regression from the `_auto_config` wiring):
```bash
"$INTERP" -m pytest packages/python/goldenpipe/tests/ -q 2>&1 | tail -20
"$INTERP" -m ruff check packages/python/goldenpipe/goldenpipe/autoconfig_planner.py packages/python/goldenpipe/goldenpipe/autoconfig_planner_rules.py packages/python/goldenpipe/goldenpipe/autoconfig_glue.py packages/python/goldenpipe/goldenpipe/pipeline.py
```
Expect: green (or only pre-existing unrelated failures — note any). Ruff clean.

- [ ] **Step 2: Rebase onto fresh origin/main**
```bash
unset GH_TOKEN; gh auth switch --user benzsevern; export GH_TOKEN=$(gh auth token --user benzsevern)
git fetch origin main -q
git rebase origin/main
```
If `pyproject.toml` conflicts, keep both the existing entry-points and the new `infer_schema` one.

- [ ] **Step 3: Confirm three-dot diff is clean**
```bash
git diff --stat origin/main...HEAD
```
Expect only: the spec, the plan, the 3 new `autoconfig_*.py`, `pyproject.toml`, `pipeline.py`, and the 2 test files.

- [ ] **Step 4: Push + PR**
```bash
git push -u origin feat/goldenpipe-autoconfig-brain
gh pr create --repo benseverndev-oss/goldenmatch --base main \
  --title "feat(goldenpipe): plan-first auto-config brain (slice 1, Python prototype)" \
  --body "$(cat <<'EOF'
## What

Gives goldenpipe a plan-first "brain" analogous to GoldenMatch's auto-config controller: instead of statically assembling check→flow→dedupe, it profiles the input up front, runs a rule table, and produces a `PipePlan` (stages + config + `rule_name` + confidence + evidence) that materializes to the pipeline config.

**Slice 1 = the Python prototype** of the Python→Rust→cross-surface arc: the decision core (`PipeProfile` → `PipePlan` via a pure rule table, `autoconfig_planner.py` + `_rules.py`) is written **portable** (no Polars/Pydantic) so it ports mechanically to a `goldenpipe-core` Rust kernel in a later slice — exactly how GoldenMatch's `autoconfig_planner` became `autoconfig-core`.

## Behavior (new)

- **`confident_schema`** (domain confidently inferred via a cheap column-only InferMap detect) → prepends `infer_schema` pinned to the domain, so downstream stages get typed columns.
- **`pathological`** (`n_rows <= 1`) → skips dedupe (plan-visible form of the reactive `row_count_gate`).
- **`default`** → the current static shape.

Backward-compatible: an explicit user config bypasses the planner; missing stages degrade to the default shape. Also registers `infer_schema` in the Python entry-points (parity with the TS registration in #1520).

## Scope (deferred to later slices)

Refuse-on-low-confidence (`PipeNotConfidentError`), the `goldenpipe-core` Rust port + TS-WASM parity (the "harden and go cross-surface" phase), and any signal needing a stage to run first (PII stays reactive in `decisions.py`).

Spec: `docs/superpowers/specs/2026-07-07-goldenpipe-autoconfig-brain-design.md`
Plan: `docs/superpowers/plans/2026-07-07-goldenpipe-autoconfig-brain.md`

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44
EOF
)"
```

- [ ] **Step 5: Arm auto-merge + STOP**
```bash
gh pr merge <PR#> --repo benseverndev-oss/goldenmatch --squash --auto
```
No `--delete-branch`. Report the PR number + the full-suite result and STOP.

---

## Verification Summary

| What | How | Where |
| --- | --- | --- |
| Portable core (plan_pipeline, structs frozen) | unit tests | Box (Task 1) |
| Rule table fires correctly | rule unit tests | Box (Task 2) |
| profile_context (df + degraded) | glue tests | Box (Task 3) |
| plan_to_config (filter + identity) | glue tests | Box (Task 3) |
| infer_schema entry-point target resolves | import check | Box (Task 4) |
| Wiring (_auto_config(ctx) + _last_plan) | regression + integration | Box (Tasks 5,6) |
| Confident df → infer_schema; 1-row → skip dedupe; degrade | integration | Box (Task 6) |
| No regression | full goldenpipe suite | Box (Task 7) |
| Portable core stays Polars/Pydantic-free | eye (import boundary) | Box (all) |
