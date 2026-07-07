# GoldenPipe auto-config brain — Slice 2 Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the GoldenPipe auto-config brain a data-complexity signal, a confidence band, and a size-gated refuse-on-RED (`PipeNotConfidentError`) — the `ControllerNotConfidentError` analog that makes it decline instead of guess.

**Architecture:** Extend the portable, Polars/Pydantic-free decision core (`autoconfig_planner.py`) with a `ComplexityProfile` + `PlannerInput` bundle and a pure `band_of` function; keep the impure profiling (`profile_complexity`) and the refuse-raise (`enforce_confidence`) in the host glue (`autoconfig_glue.py`). Wire both into `Pipeline._plan_config`. All decision logic stays plain-struct-in/plain-struct-out so the later `goldenpipe-core` Rust port is mechanical.

**Tech Stack:** Python 3.12, Polars (glue only), pytest, ruff. No goldenflow/goldenmatch imports in the new code or its tests.

**Spec:** `docs/superpowers/specs/2026-07-07-goldenpipe-autoconfig-brain-slice2-design.md`

---

## Environment (every test/ruff command in this plan)

Native Windows Python. **PYTHONPATH uses `;` as separator — NOT `:`** (a `:`-joined path collapses to one malformed entry on native Windows Python and silently resolves `goldenpipe` to a *different* repo's installed copy).

```bash
cd "D:/show_case/gg-local-llm"
INTERP="D:/show_case/goldenmatch/.venv/Scripts/python.exe"
export PYTHONPATH="packages/python/goldenpipe;packages/python/infermap;packages/python/goldencheck-types"
export POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8
```

- Run a test file: `"$INTERP" -m pytest <path> -q`
- Ruff: `"$INTERP" -m ruff check <files>` (run `--fix` if it flags import order; harmless).
- Branch is already `feat/goldenpipe-autoconfig-brain-slice2` off fresh `origin/main`, spec committed.

## File Structure (what each touched file owns)

| File | Responsibility | Change |
|------|----------------|--------|
| `goldenpipe/autoconfig_planner.py` | Portable decision core (no Polars/Pydantic) | Add `ComplexityProfile`, `PlannerInput`, `band_of`; migrate `Predicate`/`Action`/`plan_pipeline`/`_default_plan`/`default_evidence` to `PlannerInput` |
| `goldenpipe/errors.py` | goldenpipe-local exceptions | **New** — `PipeNotConfidentError` |
| `goldenpipe/autoconfig_planner_rules.py` | Concrete rule table (portable) | Migrate signatures to `PlannerInput`; add `rule_low_confidence`; extend `DEFAULT_RULES` |
| `goldenpipe/autoconfig_glue.py` | Impure host bracket (Polars in, Pydantic out, raises) | Add `profile_complexity`, `build_planner_input`, `enforce_confidence` |
| `goldenpipe/pipeline.py` | Orchestrator | `_plan_config` builds `PlannerInput` + calls `enforce_confidence`; `run()` docstring notes the raise. `_auto_config` **untouched** |
| `tests/test_autoconfig_planner.py` | Core tests | **Migrate** 6 `plan_pipeline` call sites + inline lambda to `PlannerInput`; add band/new-rule tests |
| `tests/test_autoconfig_glue.py` | Glue + integration tests | Extend with `profile_complexity`, `enforce_confidence`, refuse integration |

**Ordering note:** Changing `plan_pipeline`'s signature is atomic across the core, the rules module, and the test file — all three read the old `PipeProfile`-arg shape. **Task 1 migrates all three in one commit** so the suite is green at every commit. Task 3 then *additively* adds the `low_confidence` rule. Tasks 2–5 are otherwise additive. Task 6 adds the new planner tests; Task 7 the glue/integration tests; Task 8 ships. **No commit in this plan is red.**

---

### Task 1: Core structs + `band_of` + migrate planner signatures (core + rules + test, one green commit)

**Files:**
- Modify: `packages/python/goldenpipe/goldenpipe/autoconfig_planner.py`
- Modify (migrate signatures only, NO new rule yet): `packages/python/goldenpipe/goldenpipe/autoconfig_planner_rules.py`
- Modify (migrate): `packages/python/goldenpipe/tests/test_autoconfig_planner.py`

- [ ] **Step 1: Migrate the existing planner test to the new `PlannerInput` signature (RED)**

The current tests call `plan_pipeline(_profile(...))` and use `lambda p: ...` predicates reading `PipeProfile` directly. Rewrite the top of `tests/test_autoconfig_planner.py` so every call goes through a `_planner_input()` helper and predicates read `inp.runtime.*`. Replace lines 1–47 (imports through `test_structs_are_frozen`) and update lines 53–80's `plan_pipeline(_profile(...))` calls to `plan_pipeline(_planner_input(...))`.

Replace the import block + helpers + first three tests with:

```python
from goldenpipe.autoconfig_planner import (
    ComplexityProfile,
    PipePlan,
    PipePlannerRule,
    PipeProfile,
    PlannedStage,
    PlannerInput,
    band_of,
    plan_pipeline,
)


def _profile(**kw):
    base = dict(n_rows=100, n_cols=3, column_names=("a", "b", "c"),
                dtypes=("String", "Int64", "String"),
                inferred_domain=None, domain_confidence=0.0)
    base.update(kw)
    return PipeProfile(**base)


def _complexity(**kw):
    base = dict(max_null_density=0.0, mean_null_density=0.0)
    base.update(kw)
    return ComplexityProfile(**base)


def _planner_input(*, max_null_density=0.0, mean_null_density=0.0, **profile_kw):
    return PlannerInput(
        runtime=_profile(**profile_kw),
        complexity=_complexity(max_null_density=max_null_density,
                               mean_null_density=mean_null_density),
    )


def test_plan_pipeline_first_match_wins_else_default():
    fired = PipePlannerRule(
        rule_name="fired",
        predicate=lambda inp: inp.runtime.n_rows == 100,
        action=lambda inp: PipePlan(stages=(PlannedStage("x", {}),), rule_name="fired",
                                    confidence=0.9, evidence={"n_rows": inp.runtime.n_rows}),
    )
    plan = plan_pipeline(_planner_input(), rules=[fired])
    assert plan.rule_name == "fired"
    assert plan.stages == (PlannedStage("x", {}),)
    assert plan.evidence == {"n_rows": 100}


def test_plan_pipeline_falls_through_to_default():
    never = PipePlannerRule("never", lambda inp: False,
                            lambda inp: PipePlan((), "never", 0.0, {}))
    plan = plan_pipeline(_planner_input(), rules=[never])
    assert plan.rule_name == "default"
    assert tuple(s.name for s in plan.stages) == (
        "goldencheck.scan", "goldenflow.transform", "goldenmatch.dedupe",
    )


def test_structs_are_frozen():
    import dataclasses

    import pytest
    inp = _planner_input()
    with pytest.raises(dataclasses.FrozenInstanceError):
        inp.runtime.n_rows = 5  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        inp.complexity.max_null_density = 0.5  # type: ignore[misc]
```

Then update the three later `plan_pipeline(_profile(...))` calls (currently lines 54, 63, 73, 79) to `plan_pipeline(_planner_input(...))`. Concretely:
- `plan_pipeline(_profile(n_rows=1))` → `plan_pipeline(_planner_input(n_rows=1))` (two occurrences: `test_rule_pathological_skips_dedupe` and `test_default_rules_is_the_module_table`).
- `plan_pipeline(_profile(inferred_domain="finance", domain_confidence=0.8))` → `plan_pipeline(_planner_input(inferred_domain="finance", domain_confidence=0.8))`.
- `plan_pipeline(_profile(inferred_domain="finance", domain_confidence=0.4))` → `plan_pipeline(_planner_input(inferred_domain="finance", domain_confidence=0.4))`.

- [ ] **Step 2: Run — verify it FAILS (ImportError: ComplexityProfile / PlannerInput / band_of don't exist)**

```bash
"$INTERP" -m pytest packages/python/goldenpipe/tests/test_autoconfig_planner.py -q
```
Expected: collection ImportError on `ComplexityProfile`.

- [ ] **Step 3: Implement the core changes in `autoconfig_planner.py`**

Add the two structs + `band_of` + thresholds, and migrate the signatures. The full migrated file:

```python
"""Plan-first auto-config decision core (portable — NO Polars/Pydantic).

The pyo3-free-portable kernel: PlannerInput (in) -> PipePlan (out) via a pure
rule table. Host glue (Polars profiling, Pydantic config, refuse-raise) lives in
`autoconfig_glue.py`. Mirrors goldenmatch's controller (PlannerRule + first-match
plan, RuntimeProfile + ComplexityProfile, traffic-light confidence) so the later
`goldenpipe-core` Rust port is mechanical.
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
class ComplexityProfile:
    """Data-derived signals from one columnar pass. Zeros = unknown
    (engine-resident frame not profiled this slice)."""
    max_null_density: float    # 0..1, worst column's null fraction
    mean_null_density: float   # 0..1, mean across columns


@dataclass(frozen=True)
class PlannerInput:
    """Everything a rule sees: cheap runtime signals + deeper complexity."""
    runtime: PipeProfile
    complexity: ComplexityProfile


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


Predicate = Callable[["PlannerInput"], bool]
Action = Callable[["PlannerInput"], "PipePlan"]


@dataclass(frozen=True)
class PipePlannerRule:
    rule_name: str
    predicate: Predicate
    action: Action


GREEN_THRESHOLD = 0.7
AMBER_THRESHOLD = 0.4


def band_of(confidence: float) -> str:
    """Map a confidence float to a traffic-light band (Rust-portable strings)."""
    if confidence >= GREEN_THRESHOLD:
        return "green"
    if confidence >= AMBER_THRESHOLD:
        return "amber"
    return "red"


def default_evidence(inp: PlannerInput) -> dict:
    """Signal snapshot attached to every plan (evidence for humans/telemetry)."""
    return {
        "n_rows": inp.runtime.n_rows,
        "n_cols": inp.runtime.n_cols,
        "inferred_domain": inp.runtime.inferred_domain,
        "domain_confidence": inp.runtime.domain_confidence,
        "max_null_density": inp.complexity.max_null_density,
        "mean_null_density": inp.complexity.mean_null_density,
    }


def _default_plan(inp: PlannerInput) -> PipePlan:
    """The current static shape: scan -> flow -> dedupe (no infer_schema)."""
    return PipePlan(
        stages=(
            PlannedStage("goldencheck.scan", {}),
            PlannedStage("goldenflow.transform", {}),
            PlannedStage("goldenmatch.dedupe", {}),
        ),
        rule_name="default",
        confidence=0.7,
        evidence=default_evidence(inp),
    )


def plan_pipeline(
    inp: PlannerInput,
    rules: Sequence[PipePlannerRule] | None = None,
) -> PipePlan:
    """First matching rule's action builds the plan; else the default shape."""
    if rules is None:
        from goldenpipe.autoconfig_planner_rules import DEFAULT_RULES
        rules = DEFAULT_RULES
    for rule in rules:
        if rule.predicate(inp):
            return rule.action(inp)
    return _default_plan(inp)
```

NOTE: `default_evidence` now records `max_null_density` AND `mean_null_density` (so `mean` is not dead weight — resolves the spec-review nit). This changes the evidence dict shape; the migrated `test_plan_pipeline_first_match_wins_else_default` uses a custom rule with its own `evidence={"n_rows": ...}`, so it is unaffected.

- [ ] **Step 4: Migrate `autoconfig_planner_rules.py` signatures ONLY (no new rule yet)**

The rule predicates/actions still read the old `PipeProfile`-arg shape (`_is_pathological(p)` reads `p.n_rows`), which `PlannerInput` breaks. Migrate them to read `inp.runtime.*` so the suite stays green. Do NOT add `low_confidence` here — that is Task 3 (additive). Rewrite the file to:

```python
"""Concrete planner rules for the goldenpipe auto-config brain.

Ordered; first match wins (see plan_pipeline). Predicates read PlannerInput
(runtime + complexity). Portable — no Polars/Pydantic. Stage names are the EXACT
dotted registry names (plan_to_config drops any name not in the registry).
"""
from __future__ import annotations

from goldenpipe.autoconfig_planner import (
    PipePlan,
    PipePlannerRule,
    PlannedStage,
    PlannerInput,
    default_evidence,
)

_CONFIDENT_DOMAIN_THRESHOLD = 0.5


def _is_pathological(inp: PlannerInput) -> bool:
    return inp.runtime.n_rows <= 1


def _pathological_plan(inp: PlannerInput) -> PipePlan:
    return PipePlan(
        stages=(
            PlannedStage("goldencheck.scan", {}),
            PlannedStage("goldenflow.transform", {}),
        ),
        rule_name="pathological",
        confidence=1.0,
        evidence=default_evidence(inp),
    )


rule_pathological = PipePlannerRule("pathological", _is_pathological, _pathological_plan)


def _is_confident_schema(inp: PlannerInput) -> bool:
    r = inp.runtime
    return r.inferred_domain is not None and r.domain_confidence >= _CONFIDENT_DOMAIN_THRESHOLD


def _confident_schema_plan(inp: PlannerInput) -> PipePlan:
    return PipePlan(
        stages=(
            PlannedStage("infer_schema", {"domain": inp.runtime.inferred_domain}),
            PlannedStage("goldencheck.scan", {}),
            PlannedStage("goldenflow.transform", {}),
            PlannedStage("goldenmatch.dedupe", {}),
        ),
        rule_name="confident_schema",
        confidence=inp.runtime.domain_confidence,
        evidence=default_evidence(inp),
    )


rule_confident_schema = PipePlannerRule(
    "confident_schema", _is_confident_schema, _confident_schema_plan,
)


DEFAULT_RULES: tuple[PipePlannerRule, ...] = (
    rule_pathological,
    rule_confident_schema,
)
```

- [ ] **Step 5: Run — verify the WHOLE planner test file PASSES**

```bash
"$INTERP" -m pytest packages/python/goldenpipe/tests/test_autoconfig_planner.py -q
```
Expected: all pass (signatures consistent across core + rules + tests; no `low_confidence` yet, so no new-rule test yet — those come in Task 6).

- [ ] **Step 6: Ruff + commit (green)**

```bash
"$INTERP" -m ruff check packages/python/goldenpipe/goldenpipe/autoconfig_planner.py packages/python/goldenpipe/goldenpipe/autoconfig_planner_rules.py packages/python/goldenpipe/tests/test_autoconfig_planner.py
git add packages/python/goldenpipe/goldenpipe/autoconfig_planner.py packages/python/goldenpipe/goldenpipe/autoconfig_planner_rules.py packages/python/goldenpipe/tests/test_autoconfig_planner.py
git commit -m "feat(goldenpipe): ComplexityProfile + PlannerInput + band_of (core+rules)

Migrate the decision core and rule table to a PlannerInput bundle (runtime +
complexity) and add the traffic-light band_of. Atomic signature migration; the
low_confidence rule is added additively next.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44"
```

---

### Task 2: `PipeNotConfidentError` (new errors module)

**Files:**
- Create: `packages/python/goldenpipe/goldenpipe/errors.py`
- Test: `packages/python/goldenpipe/tests/test_errors.py`

- [ ] **Step 1: Failing test**

Create `tests/test_errors.py`:

```python
import pytest

from goldenpipe.errors import PipeNotConfidentError


def test_pipe_not_confident_is_an_exception():
    with pytest.raises(PipeNotConfidentError):
        raise PipeNotConfidentError("nope")


def test_pipe_not_confident_carries_message():
    err = PipeNotConfidentError("rule=low_confidence on 200000 rows")
    assert "low_confidence" in str(err)
```

- [ ] **Step 2: Run — verify FAIL** (ImportError)

```bash
"$INTERP" -m pytest packages/python/goldenpipe/tests/test_errors.py -q
```

- [ ] **Step 3: Implement `errors.py`**

```python
"""goldenpipe-local exceptions."""
from __future__ import annotations


class PipeNotConfidentError(RuntimeError):
    """Raised by the auto-config brain when it cannot confidently plan a
    pipeline for a large input (red confidence band at/above the row threshold).

    Parallels goldenmatch's ``ControllerNotConfidentError``: refuse loudly
    rather than run an expensive, likely-wrong pipeline. Supply an explicit
    pipeline config (or reduce the input size) to proceed.
    """
```

- [ ] **Step 4: Run — verify PASS**

```bash
"$INTERP" -m pytest packages/python/goldenpipe/tests/test_errors.py -q
```
Expected: 2 passed.

- [ ] **Step 5: Ruff + commit**

```bash
"$INTERP" -m ruff check packages/python/goldenpipe/goldenpipe/errors.py packages/python/goldenpipe/tests/test_errors.py
git add packages/python/goldenpipe/goldenpipe/errors.py packages/python/goldenpipe/tests/test_errors.py
git commit -m "feat(goldenpipe): PipeNotConfidentError (refuse-on-RED exception)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44"
```

---

### Task 3: Add `rule_low_confidence` (additive — the RED source)

**Files:**
- Modify: `packages/python/goldenpipe/goldenpipe/autoconfig_planner_rules.py`

Signatures already migrated in Task 1. This task ONLY adds the new rule + constant + extends `DEFAULT_RULES`. Its tests land in Task 6 (kept together with the band tests), so this task's verification is "no regression."

- [ ] **Step 1: Add the constant, rule, and extend the table**

At the top constants, add `RED_NULL_DENSITY = 0.6` next to `_CONFIDENT_DOMAIN_THRESHOLD`. Before the `DEFAULT_RULES` tuple, add:

```python
def _is_low_confidence(inp: PlannerInput) -> bool:
    # The sole RED source: no usable signal AND the data is mostly empty, so
    # running the full dedupe pipeline is likely to produce garbage clusters.
    return (
        inp.runtime.inferred_domain is None
        and inp.complexity.max_null_density > RED_NULL_DENSITY
    )


def _low_confidence_plan(inp: PlannerInput) -> PipePlan:
    # Return the SAFE DEFAULT shape (so small inputs proceed) but tag it RED so
    # the glue can refuse at scale.
    return PipePlan(
        stages=(
            PlannedStage("goldencheck.scan", {}),
            PlannedStage("goldenflow.transform", {}),
            PlannedStage("goldenmatch.dedupe", {}),
        ),
        rule_name="low_confidence",
        confidence=0.3,
        evidence=default_evidence(inp),
    )


rule_low_confidence = PipePlannerRule(
    "low_confidence", _is_low_confidence, _low_confidence_plan,
)
```

Then extend `DEFAULT_RULES`, with the ordering rationale as a comment:

```python
# Order: positive rules first (a clear signal wins), then low_confidence (RED),
# then plan_pipeline's plain default. Nothing shadows low_confidence — rules 1/2
# require n_rows<=1 / domain-present, which the garbage case (domain None, high
# null) does not satisfy.
DEFAULT_RULES: tuple[PipePlannerRule, ...] = (
    rule_pathological,
    rule_confident_schema,
    rule_low_confidence,
)
```

- [ ] **Step 2: Run — verify no regression** (existing planner tests still green; the new rule is not yet exercised by a test — that's Task 6)

```bash
"$INTERP" -m pytest packages/python/goldenpipe/tests/test_autoconfig_planner.py -q
```
Expected: all pass. As a sanity smoke, confirm the rule is reachable:

```bash
"$INTERP" -c "import os; os.environ['POLARS_SKIP_CPU_CHECK']='1'
from goldenpipe.autoconfig_planner import PlannerInput, PipeProfile, ComplexityProfile, plan_pipeline
inp = PlannerInput(PipeProfile(200, 2, ('a','b'), ('String','Int64'), None, 0.0), ComplexityProfile(0.9, 0.5))
print(plan_pipeline(inp).rule_name)"
```
Expected: prints `low_confidence`.

- [ ] **Step 3: Ruff + commit**

```bash
"$INTERP" -m ruff check packages/python/goldenpipe/goldenpipe/autoconfig_planner_rules.py
git add packages/python/goldenpipe/goldenpipe/autoconfig_planner_rules.py
git commit -m "feat(goldenpipe): add low_confidence rule (the RED source)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44"
```

---

### Task 4: Glue — `profile_complexity`, `build_planner_input`, `enforce_confidence`

**Files:**
- Modify: `packages/python/goldenpipe/goldenpipe/autoconfig_glue.py`
- Test: `packages/python/goldenpipe/tests/test_autoconfig_glue.py` (unit tests for the three functions)

- [ ] **Step 1: Failing tests** — append to `tests/test_autoconfig_glue.py`:

```python
# --- Slice 2: complexity profiling + refuse ----------------------------------

from goldenpipe.autoconfig_glue import (  # noqa: E402
    build_planner_input,
    enforce_confidence,
    profile_complexity,
)
from goldenpipe.autoconfig_planner import PipePlan, PlannerInput  # noqa: E402
from goldenpipe.errors import PipeNotConfidentError  # noqa: E402


def test_profile_complexity_null_heavy_column():
    df = pl.DataFrame({"a": [1, None, None, None], "b": [1, 2, 3, 4]})
    comp = profile_complexity(PipeContext(df=df))
    assert comp.max_null_density == 0.75      # column a: 3/4
    assert comp.mean_null_density == 0.375     # (0.75 + 0.0) / 2


def test_profile_complexity_no_nulls_is_zero():
    df = pl.DataFrame({"a": [1, 2], "b": [3, 4]})
    comp = profile_complexity(PipeContext(df=df))
    assert comp.max_null_density == 0.0
    assert comp.mean_null_density == 0.0


def test_profile_complexity_engine_resident_is_zero():
    comp = profile_complexity(PipeContext(df=None))
    assert comp.max_null_density == 0.0
    assert comp.mean_null_density == 0.0


def test_profile_complexity_empty_df_is_zero():
    df = pl.DataFrame({"a": []})
    comp = profile_complexity(PipeContext(df=df))
    assert comp.max_null_density == 0.0
    assert comp.mean_null_density == 0.0


def test_build_planner_input_bundles_runtime_and_complexity():
    df = pl.DataFrame({"account_number": ["A1", "A2"], "currency": ["USD", "EUR"]})
    inp = build_planner_input(PipeContext(df=df))
    assert isinstance(inp, PlannerInput)
    assert inp.runtime.n_rows == 2
    assert inp.runtime.inferred_domain == "finance"
    assert inp.complexity.max_null_density == 0.0


def _red_plan():
    return PipePlan(stages=(), rule_name="low_confidence", confidence=0.3, evidence={})


def _green_plan():
    return PipePlan(stages=(), rule_name="default", confidence=0.7, evidence={})


def test_enforce_confidence_red_at_scale_raises():
    runtime = profile_context(PipeContext(df=pl.DataFrame({"x": [1, 2]})))
    runtime = _replace_n_rows(runtime, 100_000)
    with pytest.raises(PipeNotConfidentError):
        enforce_confidence(_red_plan(), runtime)


def test_enforce_confidence_red_small_proceeds():
    runtime = profile_context(PipeContext(df=pl.DataFrame({"x": [1, 2]})))
    runtime = _replace_n_rows(runtime, 99_999)
    assert enforce_confidence(_red_plan(), runtime) is None


def test_enforce_confidence_green_proceeds():
    runtime = profile_context(PipeContext(df=pl.DataFrame({"x": [1, 2]})))
    runtime = _replace_n_rows(runtime, 100_000)
    assert enforce_confidence(_green_plan(), runtime) is None
```

Add these imports/helpers near the top of the test file if not already present: `import pytest`, and a `_replace_n_rows` helper (PipeProfile is frozen — use `dataclasses.replace`):

```python
import dataclasses


def _replace_n_rows(profile, n):
    return dataclasses.replace(profile, n_rows=n)
```

- [ ] **Step 2: Run — verify FAIL** (ImportError on `profile_complexity`)

```bash
"$INTERP" -m pytest packages/python/goldenpipe/tests/test_autoconfig_glue.py -q
```

- [ ] **Step 3: Implement in `autoconfig_glue.py`**

Add a module logger + the three functions. Insert imports at top: `import logging`, `from goldenpipe.autoconfig_planner import ComplexityProfile, PlannerInput, band_of`, `from goldenpipe.errors import PipeNotConfidentError`. Add after `profile_context`:

```python
logger = logging.getLogger(__name__)

REFUSE_ROW_THRESHOLD = 100_000


def profile_complexity(ctx: PipeContext) -> ComplexityProfile:
    """One columnar pass for null density. Engine-resident or empty -> zeros
    (unknown; not profiled this slice)."""
    df = ctx.df
    if df is None:
        return ComplexityProfile(max_null_density=0.0, mean_null_density=0.0)
    n_rows = len(df)
    if n_rows == 0:
        return ComplexityProfile(max_null_density=0.0, mean_null_density=0.0)
    # null_count() -> a 1-row frame of per-column null counts.
    counts = df.null_count().row(0)
    fractions = [c / n_rows for c in counts]
    return ComplexityProfile(
        max_null_density=max(fractions),
        mean_null_density=sum(fractions) / len(fractions),
    )


def build_planner_input(ctx: PipeContext) -> PlannerInput:
    """Assemble the full decision input (runtime + complexity) from a context."""
    return PlannerInput(
        runtime=profile_context(ctx),
        complexity=profile_complexity(ctx),
    )


def enforce_confidence(plan: PipePlan, runtime: PipeProfile) -> None:
    """Refuse-on-RED (size-gated). Raises PipeNotConfidentError on a red-band
    plan at/above the row threshold; warns and proceeds below it; no-op otherwise."""
    if band_of(plan.confidence) != "red":
        return
    if runtime.n_rows >= REFUSE_ROW_THRESHOLD:
        raise PipeNotConfidentError(
            f"auto-config not confident (rule={plan.rule_name}, "
            f"confidence={plan.confidence}) on {runtime.n_rows} rows; "
            f"supply an explicit pipeline config or reduce the input size. "
            f"evidence={plan.evidence}"
        )
    logger.warning(
        "auto-config low confidence (rule=%s) on %d rows; proceeding on safe "
        "default plan", plan.rule_name, runtime.n_rows,
    )
```

Note: `df.null_count().row(0)` returns a tuple of per-column null counts (one row). Guard `n_rows == 0` avoids division by zero. `PipeProfile` is already imported at the top of the glue module (line 11); leave it.

- [ ] **Step 4: Run — verify PASS**

```bash
"$INTERP" -m pytest packages/python/goldenpipe/tests/test_autoconfig_glue.py -q
```
Expected: the slice-1 glue/integration tests + the 8 new tests all pass.

- [ ] **Step 5: Ruff + commit**

```bash
"$INTERP" -m ruff check packages/python/goldenpipe/goldenpipe/autoconfig_glue.py packages/python/goldenpipe/tests/test_autoconfig_glue.py
git add packages/python/goldenpipe/goldenpipe/autoconfig_glue.py packages/python/goldenpipe/tests/test_autoconfig_glue.py
git commit -m "feat(goldenpipe): profile_complexity + build_planner_input + enforce_confidence

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44"
```

---

### Task 5: Wire the brain into `Pipeline._plan_config`

**Files:**
- Modify: `packages/python/goldenpipe/goldenpipe/pipeline.py:122-143` (`_plan_config`) + `run()` docstring

- [ ] **Step 1: Rewrite `_plan_config` (lines 122-143)**

Replace the body (keep the method name/signature) with:

```python
    def _plan_config(self, ctx: PipeContext) -> PipelineConfig:
        """Plan-first auto-config: profile the loaded context, run the rule
        table, refuse if not confident at scale, and materialize the chosen
        plan into a PipelineConfig.

        This is the "brain" (parity with GoldenMatch's controller): the shape
        of the pipeline is DECIDED from the data + InferMap-inferred schema, and
        a red-confidence plan on a large input raises ``PipeNotConfidentError``
        rather than running an expensive, likely-wrong pipeline. The portable
        decision core (``autoconfig_planner``) is kept free of Polars/Pydantic
        for the later ``goldenpipe-core`` Rust port; this method is the host
        glue bracket.
        """
        from goldenpipe.autoconfig_glue import (
            build_planner_input,
            enforce_confidence,
            plan_to_config,
        )
        from goldenpipe.autoconfig_planner import plan_pipeline

        inp = build_planner_input(ctx)
        plan = plan_pipeline(inp)
        self._last_plan = plan
        enforce_confidence(plan, inp.runtime)  # may raise PipeNotConfidentError
        return plan_to_config(
            plan,
            self._registry.list_all(),
            self._identity_opts,
        )
```

- [ ] **Step 2: Update `run()`'s docstring** to note the raise. Find `def run(` (line 40) and add to its docstring a line:

```
        Raises:
            PipeNotConfidentError: when auto-config (no explicit config) is not
                confident on a large input (>= 100k rows). Pass an explicit
                config to bypass the brain.
```

If `run()` currently has no docstring, add a one-line one plus the Raises block. Do NOT change `run()`'s logic — the `_plan_config` call at line 106 is already *before* the `Resolver.resolve` try-block, so the raise propagates out of `run()` (not swallowed into a FAILED PipeResult). Verify by reading lines 104-116.

- [ ] **Step 3: `_auto_config` stays UNTOUCHED.** Confirm you did not modify `_auto_config` (line 145+). Its callers (`core/_planner_json.py`, `tests/test_pipeline.py`) rely on the static default.

- [ ] **Step 4: Run the existing pipeline tests + slice-1 integration to verify no regression**

```bash
"$INTERP" -m pytest packages/python/goldenpipe/tests/test_pipeline.py packages/python/goldenpipe/tests/test_autoconfig_glue.py -q
```
Expected: all pass (slice-1 integration tests exercising `_plan_config` still green — confident_schema df includes infer_schema, single-row skips dedupe, order preserved).

- [ ] **Step 5: Ruff + commit**

```bash
"$INTERP" -m ruff check packages/python/goldenpipe/goldenpipe/pipeline.py
git add packages/python/goldenpipe/goldenpipe/pipeline.py
git commit -m "feat(goldenpipe): _plan_config profiles complexity + refuses on RED at scale

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44"
```

---

### Task 6: New planner-core tests (bands + rules)

**Files:**
- Modify: `packages/python/goldenpipe/tests/test_autoconfig_planner.py` (append)

- [ ] **Step 1: Append tests**

```python
def test_band_of_boundaries():
    assert band_of(0.7) == "green"
    assert band_of(0.71) == "green"
    assert band_of(0.69) == "amber"
    assert band_of(0.4) == "amber"
    assert band_of(0.39) == "red"
    assert band_of(0.0) == "red"


def test_rule_low_confidence_is_red_and_safe_default():
    plan = plan_pipeline(_planner_input(inferred_domain=None, max_null_density=0.7))
    assert plan.rule_name == "low_confidence"
    assert plan.confidence == 0.3
    assert band_of(plan.confidence) == "red"
    assert tuple(s.name for s in plan.stages) == (
        "goldencheck.scan", "goldenflow.transform", "goldenmatch.dedupe",
    )


def test_low_confidence_not_shadowed_by_confident_schema():
    # domain present -> confident_schema wins; domain absent + high null -> low_confidence.
    with_domain = plan_pipeline(_planner_input(inferred_domain="finance",
                                               domain_confidence=0.8, max_null_density=0.9))
    assert with_domain.rule_name == "confident_schema"
    no_domain = plan_pipeline(_planner_input(inferred_domain=None, max_null_density=0.7))
    assert no_domain.rule_name == "low_confidence"


def test_low_confidence_only_above_null_threshold():
    # domain absent but low null density -> falls through to default (not RED).
    plan = plan_pipeline(_planner_input(inferred_domain=None, max_null_density=0.5))
    assert plan.rule_name == "default"


def test_default_evidence_records_null_density():
    plan = plan_pipeline(_planner_input(inferred_domain=None, max_null_density=0.5,
                                        mean_null_density=0.25))
    assert plan.evidence["max_null_density"] == 0.5
    assert plan.evidence["mean_null_density"] == 0.25
```

- [ ] **Step 2: Run — verify PASS**

```bash
"$INTERP" -m pytest packages/python/goldenpipe/tests/test_autoconfig_planner.py -q
```
Expected: all pass.

- [ ] **Step 3: Ruff + commit**

```bash
"$INTERP" -m ruff check packages/python/goldenpipe/tests/test_autoconfig_planner.py
git add packages/python/goldenpipe/tests/test_autoconfig_planner.py
git commit -m "test(goldenpipe): band_of + low_confidence rule coverage

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44"
```

---

### Task 7: Refuse integration tests (through `_plan_config`)

**Files:**
- Modify: `packages/python/goldenpipe/tests/test_autoconfig_glue.py` (append)

These use the slice-1 stub-registry pattern (`_registry_with`, `Pipeline(registry=reg)`) already present in the file. To hit `n_rows >= 100_000` cheaply, build a real 100k-row null-heavy DataFrame with NO detectable domain (generic column names).

- [ ] **Step 1: Append integration tests**

```python
def _garbage_df(n_rows: int) -> pl.DataFrame:
    # Generic column names (no domain) + a mostly-null column (max_null_density > 0.6).
    import polars as pl
    return pl.DataFrame({
        "col_a": [None] * n_rows,             # 100% null -> max_null_density 1.0
        "col_b": list(range(n_rows)),
    })


def test_plan_config_refuses_red_at_scale():
    reg = _registry_with("goldencheck.scan", "goldenflow.transform", "goldenmatch.dedupe")
    eng = Pipeline(registry=reg)
    ctx = PipeContext(df=_garbage_df(100_000))
    with pytest.raises(PipeNotConfidentError):
        eng._plan_config(ctx)
    assert eng._last_plan.rule_name == "low_confidence"


def test_plan_config_red_below_threshold_proceeds():
    reg = _registry_with("goldencheck.scan", "goldenflow.transform", "goldenmatch.dedupe")
    eng = Pipeline(registry=reg)
    ctx = PipeContext(df=_garbage_df(1_000))
    cfg = eng._plan_config(ctx)                      # no raise
    assert eng._last_plan.rule_name == "low_confidence"
    assert [s.use for s in cfg.stages] == [
        "goldencheck.scan", "goldenflow.transform", "goldenmatch.dedupe",
    ]
```

Confirm `PipeContext`, `Pipeline`, `_registry_with`, `pytest`, and `PipeNotConfidentError` are imported in the file (slice-1 block + Task-4 block already import most; add any missing).

- [ ] **Step 2: Run — verify PASS**

```bash
"$INTERP" -m pytest packages/python/goldenpipe/tests/test_autoconfig_glue.py -q
```
Expected: all pass. (The 100k-row build is a trivial all-null + range frame; `null_count` over it is sub-second.)

- [ ] **Step 3: Ruff + commit**

```bash
"$INTERP" -m ruff check packages/python/goldenpipe/tests/test_autoconfig_glue.py
git add packages/python/goldenpipe/tests/test_autoconfig_glue.py
git commit -m "test(goldenpipe): refuse-on-RED integration through _plan_config

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44"
```

---

### Task 8: Full suite + ship

**Files:** none (verification + PR)

- [ ] **Step 1: Run the three touched test files together**

```bash
"$INTERP" -m pytest packages/python/goldenpipe/tests/test_autoconfig_planner.py packages/python/goldenpipe/tests/test_autoconfig_glue.py packages/python/goldenpipe/tests/test_errors.py packages/python/goldenpipe/tests/test_pipeline.py -q
```
Expected: all pass.

- [ ] **Step 2: Run the full goldenpipe suite (tolerate pre-existing env failures)**

```bash
"$INTERP" -m pytest packages/python/goldenpipe/tests -q -p no:cacheprovider --continue-on-collection-errors
```
Expected: the ONLY failures/errors are the pre-existing environmental ones from the borrowed goldenmatch venv — files that `import goldenflow`/`goldenmatch` (e.g. `test_engine_stage_v2.py`, `test_adapters.py::TestTransformStage`, `test_identity_cli.py`, `test_a2a.py`, `core/test_planner_parity.py::...resolve_json`). These are identical to slice-1's baseline. If UNSURE whether a failure is pre-existing, stash your changes and re-run the same nodeids:

```bash
git stash push -- packages/python/goldenpipe
"$INTERP" -m pytest <suspect nodeids> -q -p no:cacheprovider --continue-on-collection-errors
git stash pop
```
Every NEW test (planner, glue, errors) must pass. Do NOT touch goldenflow/goldenmatch to "fix" the environmental failures.

- [ ] **Step 3: Ruff on all touched files**

```bash
"$INTERP" -m ruff check \
  packages/python/goldenpipe/goldenpipe/autoconfig_planner.py \
  packages/python/goldenpipe/goldenpipe/autoconfig_planner_rules.py \
  packages/python/goldenpipe/goldenpipe/autoconfig_glue.py \
  packages/python/goldenpipe/goldenpipe/errors.py \
  packages/python/goldenpipe/goldenpipe/pipeline.py \
  packages/python/goldenpipe/tests/test_autoconfig_planner.py \
  packages/python/goldenpipe/tests/test_autoconfig_glue.py \
  packages/python/goldenpipe/tests/test_errors.py
```
Expected: All checks passed.

- [ ] **Step 4: DO NOT touch `emit_ts_parity_fixtures.py`.** Cross-surface is deferred; the parity fixture stays aimed at the shared static engine (slice-1's fix). This slice adds no TS work and must not change the emitter or the committed `pipe_parity.json`.

- [ ] **Step 5: Rebase onto fresh origin/main, push, PR, arm auto-merge, STOP**

```bash
cd "D:/show_case/gg-local-llm"
unset GH_TOKEN; gh auth switch --user benzsevern >/dev/null 2>&1; export GH_TOKEN=$(gh auth token --user benzsevern)
git fetch origin main -q && git rebase origin/main
# resolve any conflicts (unlikely — new files + isolated _plan_config edit), then:
git push -u origin feat/goldenpipe-autoconfig-brain-slice2 --force-with-lease
gh pr create --repo benseverndev-oss/goldenmatch --base main \
  --head feat/goldenpipe-autoconfig-brain-slice2 \
  --title "feat(goldenpipe): auto-config brain slice 2 (ComplexityProfile + refuse-on-RED)" \
  --body "<summary: ComplexityProfile (null density), band_of green/amber/red, size-gated refuse-on-RED via PipeNotConfidentError; low_confidence rule; _plan_config wired, _auto_config untouched, parity fixture untouched. Deferred: scale-hint merge, PII stage, Rust/TS port. New tests all green on box; remaining suite failures are the pre-existing borrowed-venv goldenflow/goldenmatch breakage (confirmed via stash-compare).>

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44"
gh pr merge <PR#> --auto --squash   # WITHOUT --delete-branch (merge queue); if it says strategy set by queue, run: gh pr merge <PR#> --auto
```
Then STOP. Do not poll CI (merge queue lands it on green).

---

## Cross-cutting reminders

- **PYTHONPATH `;` not `:`** on every command (native Windows Python).
- **No goldenflow/goldenmatch imports** in the new code or its tests — that keeps every new test box-runnable despite the borrowed venv's mid-conflict goldenflow.
- **`_auto_config` is untouched** — load-bearing for the `_planner_json` Rust parity bridge.
- **Exact dotted stage names** in every `PlannedStage` (`plan_to_config` silently drops unknown names).
- **DRY/YAGNI:** `mean_null_density` is carried + recorded in evidence but consumed by no rule this slice — deliberate seam for future signals, made non-dead via `default_evidence`.
- Frequent commits (one per task). The suite is green at every commit EXCEPT the documented transient between Task 1 and Task 3 (atomic signature migration across core+rules).
