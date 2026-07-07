# GoldenPipe scale-hint merge — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** At ≥1M rows the auto-config brain attaches a throughput hint to the dedupe stage, and the `match.py` adapter forwards it to `dedupe_df(df, throughput=…)` — which auto-configures AND applies the hint, without disabling GoldenMatch's controller.

**Architecture:** A pure, portable `apply_scale_hints` post-transform in the decision core annotates the plan; `_plan_config` calls it; the dedupe adapter recognizes the reserved `_dedupe_hints` key and routes to GM's kwargs-merge path. Decision-core piece stays Polars/Pydantic-free for the later Rust port.

**Tech Stack:** Python 3.12, Polars (glue/adapter only), pytest, ruff. `goldenmatch` is importable on the box venv.

**Spec:** `docs/superpowers/specs/2026-07-07-goldenpipe-scale-hint-merge-design.md`

---

## Environment (every command)

Native Windows Python. **PYTHONPATH uses `;` NOT `:`.**

```bash
cd "D:/show_case/gg-local-llm"
INTERP="D:/show_case/goldenmatch/.venv/Scripts/python.exe"
export PYTHONPATH="packages/python/goldenpipe;packages/python/infermap;packages/python/goldencheck-types"
export POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8
```
- Test: `"$INTERP" -m pytest <path> -q`; ruff: `"$INTERP" -m ruff check <files>` (`--fix` if import-order flagged).
- Branch `feat/goldenpipe-scale-hint-merge` (off fresh origin/main, spec committed). Every commit green.

## File Structure

| File | Change |
|------|--------|
| `goldenpipe/autoconfig_planner.py` | Add `SCALE_ROUTE_MIN_ROWS`, `_THROUGHPUT_RECALL_TARGET`, `apply_scale_hints` |
| `goldenpipe/adapters/match.py` | `_dedupe_hints` routing branch + `_throughput_from_hint` |
| `goldenpipe/pipeline.py` | `_plan_config` calls `apply_scale_hints` |
| `tests/test_autoconfig_planner.py` | `apply_scale_hints` unit tests |
| `tests/test_match_hints.py` (new) | adapter routing + GM-guarded smoke |
| `tests/test_autoconfig_glue.py` | `_plan_config` ≥1M integration |

---

### Task 1: `apply_scale_hints` (portable core)

**Files:**
- Modify: `packages/python/goldenpipe/goldenpipe/autoconfig_planner.py`
- Test: `packages/python/goldenpipe/tests/test_autoconfig_planner.py`

- [ ] **Step 1: Write failing tests** — append to `tests/test_autoconfig_planner.py` (the file already has `_profile`, `_complexity`, `_planner_input`, and imports `PipePlan`, `PlannedStage`, `PipeProfile`). Add `apply_scale_hints` + `SCALE_ROUTE_MIN_ROWS` to the top import from `goldenpipe.autoconfig_planner`, then append:

```python
def _plan_with_dedupe():
    return PipePlan(
        stages=(
            PlannedStage("goldencheck.scan", {}),
            PlannedStage("goldenflow.transform", {}),
            PlannedStage("goldenmatch.dedupe", {}),
        ),
        rule_name="default", confidence=0.7, evidence={"n_rows": 2_000_000},
    )


def test_apply_scale_hints_annotates_dedupe_at_scale():
    plan = _plan_with_dedupe()
    out = apply_scale_hints(plan, _profile(n_rows=SCALE_ROUTE_MIN_ROWS))
    dedupe = next(s for s in out.stages if s.name == "goldenmatch.dedupe")
    assert dedupe.config == {"_dedupe_hints": {"throughput": {"recall_target": 0.95}}}
    assert out.evidence["scale_hinted"] is True
    # other stages untouched
    assert [s.name for s in out.stages] == [
        "goldencheck.scan", "goldenflow.transform", "goldenmatch.dedupe",
    ]


def test_apply_scale_hints_noop_below_threshold():
    plan = _plan_with_dedupe()
    out = apply_scale_hints(plan, _profile(n_rows=SCALE_ROUTE_MIN_ROWS - 1))
    assert out is plan  # identity: unchanged below threshold


def test_apply_scale_hints_noop_without_dedupe():
    plan = PipePlan(
        stages=(PlannedStage("goldencheck.scan", {}), PlannedStage("goldenflow.transform", {})),
        rule_name="pathological", confidence=1.0, evidence={},
    )
    out = apply_scale_hints(plan, _profile(n_rows=5_000_000))
    assert out is plan  # no dedupe stage -> unchanged


def test_apply_scale_hints_is_pure():
    plan = _plan_with_dedupe()
    apply_scale_hints(plan, _profile(n_rows=SCALE_ROUTE_MIN_ROWS))
    # original plan's dedupe stage config must NOT be mutated
    orig_dedupe = next(s for s in plan.stages if s.name == "goldenmatch.dedupe")
    assert orig_dedupe.config == {}
    assert "scale_hinted" not in plan.evidence


def test_scale_route_min_rows_constant():
    assert SCALE_ROUTE_MIN_ROWS == 1_000_000
```

- [ ] **Step 2: Run — verify FAIL** (ImportError on `apply_scale_hints`/`SCALE_ROUTE_MIN_ROWS`)

```bash
"$INTERP" -m pytest packages/python/goldenpipe/tests/test_autoconfig_planner.py -q
```

- [ ] **Step 3: Implement** — append to `autoconfig_planner.py` (after `plan_pipeline`):

```python
SCALE_ROUTE_MIN_ROWS = 1_000_000
_THROUGHPUT_RECALL_TARGET = 0.95


def apply_scale_hints(plan: PipePlan, runtime: PipeProfile) -> PipePlan:
    """Composable post-transform: at/above SCALE_ROUTE_MIN_ROWS, attach a
    throughput hint to the dedupe stage so GoldenMatch routes to its
    sketch-then-verify tier. No-op below the threshold or when the plan has no
    dedupe stage. Pure — returns a new PipePlan, never mutates the input.

    The hint travels as a reserved ``_dedupe_hints`` key in the dedupe stage's
    config; the match.py adapter recognizes it and forwards it to
    ``dedupe_df(throughput=...)`` (auto-config + hint) rather than treating it as
    a full-config override.
    """
    if runtime.n_rows < SCALE_ROUTE_MIN_ROWS:
        return plan
    if not any(s.name == "goldenmatch.dedupe" for s in plan.stages):
        return plan
    new_stages = tuple(
        PlannedStage(
            s.name,
            {**s.config, "_dedupe_hints": {"throughput": {"recall_target": _THROUGHPUT_RECALL_TARGET}}},
        )
        if s.name == "goldenmatch.dedupe"
        else s
        for s in plan.stages
    )
    return PipePlan(
        stages=new_stages,
        rule_name=plan.rule_name,
        confidence=plan.confidence,
        evidence={**plan.evidence, "scale_hinted": True},
    )
```

- [ ] **Step 4: Run — verify PASS**

```bash
"$INTERP" -m pytest packages/python/goldenpipe/tests/test_autoconfig_planner.py -q
```
Expected: all pass (prior + 5 new).

- [ ] **Step 5: Ruff + commit**

```bash
"$INTERP" -m ruff check packages/python/goldenpipe/goldenpipe/autoconfig_planner.py packages/python/goldenpipe/tests/test_autoconfig_planner.py
git add packages/python/goldenpipe/goldenpipe/autoconfig_planner.py packages/python/goldenpipe/tests/test_autoconfig_planner.py
git commit -m "feat(goldenpipe): apply_scale_hints post-transform (throughput hint at scale)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44"
```

---

### Task 2: adapter `_dedupe_hints` routing (`match.py`)

**Files:**
- Modify: `packages/python/goldenpipe/goldenpipe/adapters/match.py` (`DedupeStage.run` + a helper)
- Test: `packages/python/goldenpipe/tests/test_match_hints.py` (new)

- [ ] **Step 1: Write failing tests** — create `tests/test_match_hints.py`:

```python
import polars as pl
import pytest

from goldenpipe.adapters import match as match_mod
from goldenpipe.adapters.match import DedupeStage
from goldenpipe.models.context import PipeContext


class _Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, df, **kwargs):
        self.calls.append(kwargs)
        # Return an object with the artifact attributes the adapter reads.
        class _R:
            clusters = pl.DataFrame({"cluster_id": [0]})
            golden = pl.DataFrame({"x": [1]})
            unique = pl.DataFrame({"x": [1]})
        return _R()


def _ctx(stage_config):
    ctx = PipeContext(df=pl.DataFrame({"x": ["a", "b"]}))
    ctx.stage_config = stage_config
    return ctx


def test_hint_routes_to_throughput_not_override(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(match_mod, "_dedupe", rec)
    DedupeStage().run(_ctx({"_dedupe_hints": {"throughput": {"recall_target": 0.95}}}))
    assert len(rec.calls) == 1
    kw = rec.calls[0]
    assert kw.get("throughput") is not None           # hint applied
    assert kw.get("config") is None                    # NOT a full-config override


def test_full_config_still_overrides(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(match_mod, "_dedupe", rec)
    DedupeStage().run(_ctx({"exact": ["x"]}))
    assert len(rec.calls) == 1
    kw = rec.calls[0]
    assert kw.get("config") is not None                # GoldenMatchConfig override
    assert kw.get("throughput") is None


def test_no_config_uses_auto(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(match_mod, "_dedupe", rec)
    DedupeStage().run(_ctx({}))
    assert len(rec.calls) == 1
    assert rec.calls[0].get("config") is None
    assert rec.calls[0].get("throughput") is None


@pytest.mark.smoke
def test_throughput_type_accepted_end_to_end():
    # GM-guarded: confirms dedupe_df(throughput=ThroughputConfig(...)) is accepted.
    pytest.importorskip("goldenmatch")
    df = pl.DataFrame({
        "name": ["Ann", "Ann", "Bob", "Bob", "Cara"] * 60,
        "city": ["NY", "NY", "LA", "LA", "SF"] * 60,
    })
    ctx = PipeContext(df=df)
    ctx.stage_config = {"_dedupe_hints": {"throughput": {"recall_target": 0.95}}}
    res = DedupeStage().run(ctx)
    assert res.status.name == "SUCCESS"
    assert "golden" in ctx.artifacts
```

NOTE on the smoke test: if the throughput tier errors on this small frame, do NOT weaken the routing tests. Instead downgrade THIS smoke to asserting only that no `TypeError` is raised on the `throughput=` argument (i.e. GM accepts the type), and report what happened. The three monkeypatched routing tests are the required assertions.

- [ ] **Step 2: Run — verify FAIL** (routing branch not implemented → hint treated as full config → `GoldenMatchConfig(**{"_dedupe_hints": ...})` raises, or `throughput` not passed)

```bash
"$INTERP" -m pytest packages/python/goldenpipe/tests/test_match_hints.py -q
```

- [ ] **Step 3: Implement** — in `adapters/match.py`, add the helper near the top (after the `_dedupe` import block) and restructure the priority chain in `run`.

Helper:
```python
def _throughput_from_hint(spec: dict | None):
    """Build GoldenMatch's throughput arg from a brain hint (auto-config + hint,
    not an override). GoldenMatchConfig.throughput is a ThroughputConfig, which
    dedupe_df(throughput=) accepts directly."""
    from goldenmatch.config.schemas import ThroughputConfig
    return ThroughputConfig(enabled=True, **(spec or {}))
```

Restructure the top of `run` (the current `if stage_cfg:` / `else:` block) to:
```python
        # Priority 0: brain scale-hint -> auto-config + hint (do NOT override
        # GoldenMatch's controller; it merges kwargs with its auto-config).
        stage_cfg = ctx.stage_config
        hints = stage_cfg.get("_dedupe_hints") if stage_cfg else None
        if hints:
            throughput = _throughput_from_hint(hints.get("throughput"))
            logger.info("Applying auto-config scale hint (throughput) from the brain")
            result = _dedupe(ctx.df, throughput=throughput)
        elif stage_cfg:
            # Priority 1: explicit full config from YAML/PipelineConfig (override)
            from goldenmatch.config.schemas import GoldenMatchConfig
            config = GoldenMatchConfig(**stage_cfg)
            logger.info("Using explicit GoldenMatch config from stage spec")
            result = _dedupe(ctx.df, config=config)
        else:
            # Priority 2: build config from upstream column contexts
            column_contexts = ctx.artifacts.get("column_contexts")
            if column_contexts:
                config = _build_config_from_contexts(column_contexts, ctx.df)
                if config is not None:
                    logger.info("Built match config from pipeline column contexts")
                    result = _dedupe(ctx.df, config=config)
                else:
                    logger.info("Column contexts insufficient for config; using GoldenMatch auto-configure")
                    result = _dedupe(ctx.df)
            else:
                # Priority 3: let GoldenMatch auto-configure
                result = _dedupe(ctx.df)
```
Keep everything below (the `if hasattr(result, ...)` artifact-surfacing block + matchkey surfacing) unchanged. NOTE the matchkey line `config.get_matchkeys() if "config" in locals() else None` already guards the hint branch (where `config` is unbound) — do not touch it.

- [ ] **Step 4: Run — verify PASS** (routing tests; smoke skips if GM absent or passes if present)

```bash
"$INTERP" -m pytest packages/python/goldenpipe/tests/test_match_hints.py -q
```
Expected: 3 routing tests pass; smoke passes (GM present on box) or is downgraded per the note.

- [ ] **Step 5: Ruff + commit**

```bash
"$INTERP" -m ruff check packages/python/goldenpipe/goldenpipe/adapters/match.py packages/python/goldenpipe/tests/test_match_hints.py
git add packages/python/goldenpipe/goldenpipe/adapters/match.py packages/python/goldenpipe/tests/test_match_hints.py
git commit -m "feat(goldenpipe): dedupe adapter routes _dedupe_hints to GM throughput merge

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44"
```

---

### Task 3: wire `apply_scale_hints` into `_plan_config` + integration test

**Files:**
- Modify: `packages/python/goldenpipe/goldenpipe/pipeline.py` (`_plan_config`)
- Test: `packages/python/goldenpipe/tests/test_autoconfig_glue.py`

- [ ] **Step 1: Write failing integration tests** — append to `tests/test_autoconfig_glue.py` (it already has `_registry_with`, `Pipeline`, `PipeContext`, `pl`):

```python
def test_plan_config_hints_dedupe_at_scale():
    reg = _registry_with("goldencheck.scan", "goldenflow.transform", "goldenmatch.dedupe")
    eng = Pipeline(registry=reg)
    # Fully-populated (low-null), generic column names -> GREEN default plan at 1M.
    n = 1_000_000
    df = pl.DataFrame({"col_a": range(n), "col_b": range(n)})
    cfg = eng._plan_config(PipeContext(df=df))
    dedupe = next(s for s in cfg.stages if s.use == "goldenmatch.dedupe")
    assert dedupe.config == {"_dedupe_hints": {"throughput": {"recall_target": 0.95}}}
    assert eng._last_plan.evidence["scale_hinted"] is True


def test_plan_config_no_hint_below_scale():
    reg = _registry_with("goldencheck.scan", "goldenflow.transform", "goldenmatch.dedupe")
    eng = Pipeline(registry=reg)
    df = pl.DataFrame({"col_a": range(10), "col_b": range(10)})
    cfg = eng._plan_config(PipeContext(df=df))
    dedupe = next(s for s in cfg.stages if s.use == "goldenmatch.dedupe")
    assert "_dedupe_hints" not in dedupe.config
```

- [ ] **Step 2: Run — verify FAIL** (`_plan_config` doesn't call `apply_scale_hints` yet → no `_dedupe_hints`)

```bash
"$INTERP" -m pytest packages/python/goldenpipe/tests/test_autoconfig_glue.py -q -k scale
```

- [ ] **Step 3: Implement** — in `pipeline.py` `_plan_config`, add `apply_scale_hints` to the local import and call it between `plan_pipeline` and stashing `_last_plan`:

```python
        from goldenpipe.autoconfig_planner import apply_scale_hints, plan_pipeline

        inp = build_planner_input(ctx)
        plan = plan_pipeline(inp)
        plan = apply_scale_hints(plan, inp.runtime)
        self._last_plan = plan
        enforce_confidence(plan, inp.runtime)  # may raise PipeNotConfidentError
        return plan_to_config(
            plan,
            self._registry.list_all(),
            self._identity_opts,
        )
```
(Leave the docstring and everything else in the method unchanged. Do NOT touch `_auto_config`.)

- [ ] **Step 4: Run — verify PASS**

```bash
"$INTERP" -m pytest packages/python/goldenpipe/tests/test_autoconfig_glue.py -q
```
Expected: all pass (prior slice-1/2 tests + 2 new). The 1M-row build is a couple of int columns — sub-second, and `_plan_config` never runs dedupe.

- [ ] **Step 5: Ruff + commit**

```bash
"$INTERP" -m ruff check packages/python/goldenpipe/goldenpipe/pipeline.py packages/python/goldenpipe/tests/test_autoconfig_glue.py
git add packages/python/goldenpipe/goldenpipe/pipeline.py packages/python/goldenpipe/tests/test_autoconfig_glue.py
git commit -m "feat(goldenpipe): _plan_config applies scale hints after planning

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44"
```

---

### Task 4: Full suite + ship

**Files:** none (verification + PR)

- [ ] **Step 1: Run the touched test files together**

```bash
"$INTERP" -m pytest packages/python/goldenpipe/tests/test_autoconfig_planner.py packages/python/goldenpipe/tests/test_autoconfig_glue.py packages/python/goldenpipe/tests/test_match_hints.py packages/python/goldenpipe/tests/test_pipeline.py -q
```
Expected: all pass.

- [ ] **Step 2: Full goldenpipe suite (tolerate pre-existing env failures)**

```bash
"$INTERP" -m pytest packages/python/goldenpipe/tests -q -p no:cacheprovider --continue-on-collection-errors
```
Expected: the ONLY failures are the pre-existing baseline (`core/test_planner_parity.py::...resolve_json` native-wheel skew + `test_a2a.py` setup errors). Every NEW test passes. If unsure a failure is pre-existing, checkout the file from `origin/main` for comparison — do NOT touch goldenflow/goldenmatch to "fix" env failures.

- [ ] **Step 3: Ruff on all touched files**

```bash
"$INTERP" -m ruff check \
  packages/python/goldenpipe/goldenpipe/autoconfig_planner.py \
  packages/python/goldenpipe/goldenpipe/adapters/match.py \
  packages/python/goldenpipe/goldenpipe/pipeline.py \
  packages/python/goldenpipe/tests/test_autoconfig_planner.py \
  packages/python/goldenpipe/tests/test_match_hints.py \
  packages/python/goldenpipe/tests/test_autoconfig_glue.py
```
Expected: All checks passed.

- [ ] **Step 4: Rebase, push, PR, arm auto-merge, STOP**

```bash
cd "D:/show_case/gg-local-llm"
unset GH_TOKEN; gh auth switch --user benzsevern >/dev/null 2>&1; export GH_TOKEN=$(gh auth token --user benzsevern)
git fetch origin main -q && git rebase origin/main
# resolve any conflicts (unlikely — isolated additions), then:
git push -u origin feat/goldenpipe-scale-hint-merge --force-with-lease
gh pr create --repo benseverndev-oss/goldenmatch --base main \
  --head feat/goldenpipe-scale-hint-merge \
  --title "feat(goldenpipe): scale-hint merge (throughput hint -> GM auto-config)" \
  --body "<summary: apply_scale_hints post-transform attaches a throughput hint at >=1M rows; match.py routes _dedupe_hints to dedupe_df(throughput=) which auto-configures + hints (no controller clobber); wired into _plan_config after planning, before the refuse gate. Throughput-only this slice; the Rust port (Slice B) captures apply_scale_hints. New tests green on box; remaining suite failures are the pre-existing native-wheel/a2a baseline.>

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44"
gh pr merge <PR#> --auto --squash   # WITHOUT --delete-branch (merge queue); if 'strategy set by queue', run: gh pr merge <PR#> --auto
```
Then STOP. Do not poll CI.

---

## Cross-cutting reminders
- **PYTHONPATH `;` not `:`** on every command.
- `apply_scale_hints` stays free of Polars/Pydantic (Rust-port boundary for Slice B).
- `_auto_config` untouched.
- The 1M integration frame must be **low-null / domain-less-but-clean** so it lands on the GREEN default plan (a high-null frame would trip `low_confidence` and refuse at 1M).
- Every commit green; one commit per task.
