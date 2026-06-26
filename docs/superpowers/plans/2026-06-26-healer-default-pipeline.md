# Healer in the Default Pipeline — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the healer (`review_config`) discoverable from the default `dedupe_df` pipeline (cheap, trigger-gated, surface-only) plus opt-in `suggest=`/`heal=`, and reach all seven non-TS surfaces — without making the common case pay or silently changing results.

**Architecture:** One shared core module `goldenmatch/core/suggest/surface.py` (free trigger → cheap artifacts-in suggestion → graceful no-native) that the default pipeline and every surface delegate to. A new artifacts-in entry point `suggest_from_result` reuses the `scored_pairs`/`clusters` the run already produced (the existing `review_config` re-runs the whole pipeline, which the default path must NOT do). Default-on but additive/advisory, kill-switch `GOLDENMATCH_SUGGEST_ON_DEDUPE=0`; verified/heal cost is opt-in only.

**Tech Stack:** Python, pytest, Polars, the `goldenmatch.core.suggest` kernel adapter, the native `suggest_config` kernel (`goldenmatch[native]`); surface wiring in `cli/`, `mcp/`, `a2a/`, `tui/`, `web/`, `api/`.

**Worktree:** `D:/show_case/goldenmatch/.worktrees/healer-default` (branch `feat/healer-default-pipeline`, stacked on `feat/suggest-verify-gate-proxy` → #1271 → #1267). Run from the worktree root. Python: `D:/show_case/goldenmatch/.venv/Scripts/python.exe`; tests need `PYTHONPATH=packages/python/goldenmatch` (shadow the stale shared-venv goldenmatch) + `POLARS_SKIP_CPU_CHECK=1`. The native kernel must be built for the end-to-end task (`uv run python scripts/build_native.py`); pure-function tests don't need it.

**Spec:** `docs/superpowers/specs/2026-06-26-healer-default-pipeline-design.md`

---

## Key facts (verified against source)

- `review_config(df, config, *, priors=None, verify=True)` (`core/suggest/adapter.py:465`) **always** calls `engine._run_pipeline(df, _config)` (line 539) to (re)produce `scored_pairs`/`clusters`; `verify` only gates the per-candidate re-run loop (611-671). Building the kernel batches + the kernel call + parse is lines 559-609; the verify loop is 611-671.
- Native kernel signature: `nm.suggest_config(scored_pairs_batch, clusters_batch, column_signals_batch, config_json, priors_json) -> json_str` (adapter.py:570). Batch builders already exist: `_build_scored_pairs_batch`, `_build_clusters_batch`, `_build_column_signals_batch(df, config, clusters)` (267-404 region) — the last builds column_signals from `df` + `clusters` with **no pipeline run**.
- `DedupeResult` (`goldenmatch/_api.py:123`) carries `scored_pairs` (143), `clusters` (139), `config` (144), `postflight_report` (145); advisory-field pattern: `lint_findings`/`native`/`throughput_posture`.
- Controller signals: `result.postflight_report.controller_history` is a `RunHistory`; committed entry via `history.pick_committed(...)` → `.profile.health()` (RED/YELLOW/GREEN), dip at `.profile.scoring.dip_statistic` / `bimodality_or_dip_score`. `controller_history` is `None` on the explicit-config path.
- `_require_kernel()` / `SuggestionsNativeRequired` (adapter.py ~85), `apply_suggestion` (`core/suggest/apply.py`), `suggestion_health_from_clusters` (`core/suggest/health.py`).
- Count-assertion sites: MCP `mcp/server.py` (~1002) + `tests/test_mcp_new_tools.py` (`len(TOOLS) == 68`); A2A `_SKILLS` in `a2a/server.py` + `test_agent_card_has_37_skills` in `tests/test_a2a.py`.

---

## File structure

| File | Responsibility | Change |
|---|---|---|
| `core/suggest/adapter.py` | Extract `_kernel_suggest` + `_verify_suggestions` from `review_config`; add `suggest_from_result` | **Modify** |
| `core/suggest/surface.py` | `headroom_signal`, `maybe_suggest`, `heal`, `serialize_suggestions`, kill-switch | **Create** |
| `core/suggest/__init__.py` | export `suggest_from_result`, `surface` helpers | **Modify** |
| `_api.py` | `DedupeResult.suggestions`/`heal_trail`; `dedupe_df`/`match_df` `suggest=`/`heal=`; default `maybe_suggest` call | **Modify** |
| `cli/` (dedupe command) | default hint + `--suggest`/`--heal` | **Modify** |
| `mcp/` , `a2a/` | tool + skill (+count assertions) | **Modify** |
| `tui/`, `web/`, `api/` | suggestions panel / section / endpoint | **Modify** |
| `tests/test_suggest_surface.py` + per-surface tests | coverage | **Create/Modify** |

---

## Task 1: Refactor `review_config` into reusable helpers (pure refactor, behavior-preserving)

**Files:** Modify `core/suggest/adapter.py`. Guard: the existing `review_config` tests (`tests/test_suggest_verify.py`, `tests/test_health_cohesion.py`, etc.) must stay green — this is a no-behavior-change extraction.

- [ ] **Step 1: Run the existing suggest tests to establish the green baseline**

Run: `POLARS_SKIP_CPU_CHECK=1 PYTHONPATH=packages/python/goldenmatch D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest packages/python/goldenmatch/tests/test_suggest_verify.py -q`
Expected: PASS (record the count). This is the regression guard for the refactor.

- [ ] **Step 2: Extract `_kernel_suggest`**

In `adapter.py`, extract lines ~559-609 (build batches → `nm.suggest_config` → parse into `list[Suggestion]`) into:

```python
def _kernel_suggest(nm, df, config, scored_pairs, clusters, priors):
    """Build the 3 Arrow batches from given artifacts, call the native kernel,
    parse to list[Suggestion]. No pipeline run — pairs/clusters are passed in."""
    scored_pairs_batch = _build_scored_pairs_batch(scored_pairs)
    clusters_batch = _build_clusters_batch(clusters)
    column_signals_batch = _build_column_signals_batch(df, config, clusters)
    config_json = json.dumps(_config_summary(config), default=str)
    priors_json = json.dumps(priors if priors is not None else {"counts": {}}, default=str)
    try:
        raw_json = nm.suggest_config(scored_pairs_batch, clusters_batch,
                                     column_signals_batch, config_json, priors_json)
    except Exception as exc:
        raise RuntimeError(f"suggest: native suggest_config kernel failed: {exc}") from exc
    return _parse_suggestions(raw_json)   # the 583-606 parse loop, also extracted
```

Extract the parse loop (583-606) into `_parse_suggestions(raw_json) -> list[Suggestion]`.

- [ ] **Step 3: Extract `_verify_suggestions`**

Extract the verify loop (611-671) into:

```python
def _verify_suggestions(suggestions, df, config, clusters, engine):
    """Keep only suggestions whose candidate health >= baseline - EPS. Re-runs
    the pipeline per candidate (cost guard _MAX_VERIFY_CANDIDATES). Needs `engine`."""
    # ... exact body of lines 619-671, with `engine` passed in ...
```

- [ ] **Step 4: Rewrite `review_config` to use the helpers (behavior identical)**

`review_config` becomes: `_require_kernel` → `__row_id__` guard → `_run_pipeline` → (FULL_DIST diagnostic as today) → `_kernel_suggest(nm, df, _config, pairs_for_kernel, clusters, priors)` → if `_do_verify and suggestions`: `_verify_suggestions(suggestions, df, _config, clusters, engine)` else return. Keep the deep-copy + rerank-disable + FULL_DIST logic exactly as-is.

- [ ] **Step 5: Run the suggest tests — confirm still green (no behavior change)**

Run: `POLARS_SKIP_CPU_CHECK=1 PYTHONPATH=packages/python/goldenmatch D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest packages/python/goldenmatch/tests/test_suggest_verify.py packages/python/goldenmatch/tests/test_health_cohesion.py -q`
Expected: same PASS count as Step 1. Lint: `ruff check core/suggest/adapter.py` (path under the package).

- [ ] **Step 6: Commit**

```bash
git add packages/python/goldenmatch/goldenmatch/core/suggest/adapter.py
git commit -m "refactor(suggest): extract _kernel_suggest/_verify_suggestions from review_config"
```

---

## Task 2: `suggest_from_result` (artifacts-in entry point)

**Files:** Modify `core/suggest/adapter.py` (add `suggest_from_result`), `core/suggest/__init__.py` (export). Test: `tests/test_suggest_from_result.py`.

### Background
This is the cheap path's workhorse — it skips `_run_pipeline` by feeding the result's already-computed artifacts to `_kernel_suggest`. For `verify=True` it builds an engine and runs `_verify_suggestions` (the opt-in expensive path).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_suggest_from_result.py
import polars as pl, pytest
from goldenmatch import dedupe_df
from goldenmatch.core.suggest import review_config
from goldenmatch.core.suggest.adapter import suggest_from_result
from goldenmatch.core.suggest.types import SuggestionsNativeRequired

def _df():
    # small frame with a primary weighted matchkey shape; reuse an existing fixture
    return pl.DataFrame({"name": ["Jon A","Jon A","Bob"], "email": ["j@x","j@x","b@y"]})

@pytest.mark.native  # skip when no native kernel (see conftest marker pattern)
def test_suggest_from_result_matches_review_config_raw():
    df = _df()
    res = dedupe_df(df)
    # artifacts-in (verify=False) returns the SAME raw suggestions as review_config(verify=False)
    from_result = suggest_from_result(res, df, verify=False)
    from_review = review_config(df, res.config, verify=False)
    assert [s.id for s in from_result] == [s.id for s in from_review]

def test_suggest_from_result_graceful_without_native(monkeypatch):
    # force the kernel absent -> [] not raise
    monkeypatch.setattr("goldenmatch.core.suggest.adapter._require_kernel",
                        lambda: (_ for _ in ()).throw(SuggestionsNativeRequired("no native")))
    df = _df(); res = dedupe_df(df)
    assert suggest_from_result(res, df) == []
```

(If the repo has a `native` marker/skip pattern, follow it; otherwise gate the parity test on `pytest.importorskip` of the kernel. Use an existing person fixture if `_df` is too degenerate for a config — see `tests/test_autoconfig_regressions.py::_person_df`.)

- [ ] **Step 2: Run → fails** (`suggest_from_result` undefined). Command as in Task 1 Step 1 but the new file.

- [ ] **Step 3: Implement `suggest_from_result`**

```python
def suggest_from_result(result, df, *, priors=None, verify=False) -> list[Suggestion]:
    """Artifacts-in suggestion: reuse result.scored_pairs/result.clusters (NO
    pipeline re-run for verify=False) and call the kernel directly. verify=True
    runs the per-candidate simulation loop (which DOES re-run). Returns [] when
    the native kernel is absent (graceful)."""
    try:
        nm = _require_kernel()
    except SuggestionsNativeRequired:
        return []
    if "__row_id__" not in df.columns:
        df = df.with_row_index("__row_id__").with_columns(pl.col("__row_id__").cast(pl.Int64))
    config = result.config
    clusters = result.clusters or {}
    # Default cheap path: reuse the result's pairs (NO re-run). FULL_DIST (off by
    # default) needs the threshold-0 distribution for the dip rule, which IS a
    # re-run — only then build an engine and run the diagnostic.
    pairs_for_kernel = result.scored_pairs or []
    if _full_dist_enabled():
        from goldenmatch.tui.engine import MatchEngine
        diag = _diagnostic_scored_pairs(MatchEngine.from_dataframe(df), df, config)
        if diag is not None:
            pairs_for_kernel = diag
    suggestions = _kernel_suggest(nm, df, config, pairs_for_kernel, clusters, priors)
    if not (verify and _verify_enabled_by_env()) or not suggestions:
        return suggestions
    from goldenmatch.tui.engine import MatchEngine
    engine = MatchEngine.from_dataframe(df)
    return _verify_suggestions(suggestions, df, config, clusters, engine)
```

- [ ] **Step 4: Export in `__init__.py`** — add `suggest_from_result` to the imports + `__all__`.

- [ ] **Step 5: Run → passes; lint; commit**

```bash
git add packages/python/goldenmatch/goldenmatch/core/suggest/adapter.py packages/python/goldenmatch/goldenmatch/core/suggest/__init__.py packages/python/goldenmatch/tests/test_suggest_from_result.py
git commit -m "feat(suggest): artifacts-in suggest_from_result (no pipeline re-run on the cheap path)"
```

---

## Task 3: `headroom_signal` (free trigger, TDD)

**Files:** Create `core/suggest/surface.py`; Test: `tests/test_suggest_surface.py`.

### Background
Pure/free. Reads `result.postflight_report.controller_history`; re-derives the committed entry via `pick_committed` (there's no stored pointer) and fires on RED/YELLOW health OR a score dip. Returns `None` when `controller_history` is `None` (explicit-config path).

- [ ] **Step 1: Write the failing test** (synthetic postflight; mirror the controller_history shape — inspect `complexity_profile.py` for `health()`/`scoring.dip_statistic` and build a minimal fake, or use a tiny real run). Assert: RED→reason, YELLOW→reason, GREEN+no-dip→None, dip-on-GREEN→reason, `controller_history is None`→None.

- [ ] **Step 2: Run → fails.**

- [ ] **Step 3: Implement `headroom_signal(result) -> HeadroomReason | None`** reading `pick_committed(...).profile.health()` + `.profile.scoring.dip_statistic`/`bimodality_or_dip_score`. Define a small `HeadroomReason` dataclass (`kind: str`, e.g. `"health:RED"` / `"dip"`). Wrap reads in try/except → `None` (never raise on an unexpected history shape).

- [ ] **Step 4: Run → passes. Commit** (`feat(suggest): headroom_signal free trigger`).

---

## Task 4: `maybe_suggest` + `serialize_suggestions` + kill-switch (TDD)

**Files:** Modify `core/suggest/surface.py`; `tests/test_suggest_surface.py`.

- [ ] **Step 1: Failing tests** — (a) `maybe_suggest` returns `[]` and does NOT call the kernel when `headroom_signal` is `None` (assert via a monkeypatched `suggest_from_result` spy); (b) returns `[]` when `GOLDENMATCH_SUGGEST_ON_DEDUPE=0`; (c) delegates to `suggest_from_result(result, df, verify=verify)` when fired; (d) `serialize_suggestions` shape `{id,kind,target,rationale,verified,patch}`.

- [ ] **Step 2: Run → fails.**

- [ ] **Step 3: Implement**

```python
def maybe_suggest(result, df, *, verify=False):
    if os.environ.get("GOLDENMATCH_SUGGEST_ON_DEDUPE", "1").strip() == "0":
        return []
    if headroom_signal(result) is None:
        return []
    from goldenmatch.core.suggest.adapter import suggest_from_result
    return suggest_from_result(result, df, verify=verify)

def serialize_suggestions(suggestions, *, verified: bool) -> list[dict]:
    """The single wire shape every surface emits. `verified` is supplied by the
    caller (NOT read off the Suggestion — the dataclass has no such field): the
    default/maybe_suggest path passes verified=False; suggest=/heal= pass True."""
    return [{"id": s.id, "kind": s.kind, "target": s.target,
             "rationale": s.rationale, "verified": verified,
             "patch": dict(s.patch)} for s in suggestions]
```

**`verified` plumbing (resolved — do NOT use a per-object attr).** `Suggestion` (`core/suggest/types.py`) has no `verified`/`_verified` field and `_verify_suggestions` does not tag kept ones. So `verified` is a **caller-supplied** flag: `serialize_suggestions(..., verified=True)` only from the `suggest=`/`heal=` (verify=True) paths, `verified=False` from the default `maybe_suggest` path. `DedupeResult.suggestions` stores the **serialized dicts** (which carry `verified`), so every surface consumes one uniform shape and the spec's "each carries a `verified` bool" holds without touching the `Suggestion` dataclass.

- [ ] **Step 4: Run → passes. Commit.**

---

## Task 5: `heal()` loop (TDD, monkeypatched)

**Files:** Modify `core/suggest/surface.py`; `tests/test_suggest_surface.py`.

- [ ] **Step 1: Failing test** — monkeypatch `suggest_from_result` to return [s1] then [s2] then [] across calls, and `dedupe_df`/`apply_suggestion` to no-op-ish stand-ins; assert `heal()` applies s1,s2 in order, stops on empty, returns `(config, trail=[s1,s2], result)`, and respects `step_cap` + the repeated-id cycle guard.

- [ ] **Step 2: Run → fails.**

- [ ] **Step 3: Implement `heal(df, config, *, step_cap=5)`** — loop: `res = dedupe_df(df, config=config)` → `sugs = suggest_from_result(res, df, verify=True)` → break if empty → `top = sugs[0]`; break if `top.id` already applied → `config = apply_suggestion(config, top)`; append to trail. Return `HealOutcome(config, trail, last_result)`. (Import `dedupe_df` lazily to avoid a cycle.)

- [ ] **Step 4: Run → passes. Commit.**

---

## Task 6: Wire into `dedupe_df` + `DedupeResult` (TDD + no-op parity)

**Files:** Modify `_api.py`. Test: `tests/test_api.py` (extend) or `tests/test_suggest_surface.py`.

> **Scope:** `dedupe_df` ONLY. `match_df` (record-linkage; returns `MatchResult`, which has no suggestion fields) is a named follow-on — do NOT add `suggest=`/`heal=` to it or touch `MatchResult` in this spec. The healer reviews dedupe configs.

- [ ] **Step 1: Failing tests** — (a) `dedupe_df(df)` on a near-ceiling input → `result.suggestions == []` and the call is byte-identical to today (no other field changed) — the no-op parity guard; (b) with `headroom_signal` monkeypatched to fire + `suggest_from_result` monkeypatched to return one Suggestion, `result.suggestions` is a one-element list of dicts with `verified=False`; (c) `dedupe_df(df, heal=True)` (monkeypatch `surface.heal`) returns a result whose `config` is the healed config and `heal_trail` is a non-None list of dicts (`verified=True`); (d) `suggest=True` attaches dicts with `verified=True`.

- [ ] **Step 2: Run → fails.**

- [ ] **Step 3: Implement**
- Add `suggestions: list = field(default_factory=list)` and `heal_trail: list | None = None` to `DedupeResult` (after `throughput_posture`). Both hold **serialized dicts** (the `serialize_suggestions` shape), not raw `Suggestion` objects.
- Add `suggest: bool = False`, `heal: bool = False` to the `dedupe_df` signature only.
- After the result is built (where `lint_findings`/`native` are attached), in a try/except (advisory — never break a dedupe):
  - if `heal`: `outcome = surface.heal(df, result.config)`; rebuild/replace the returned result with `outcome.result`; set `result.heal_trail = serialize_suggestions(outcome.trail, verified=True)` and `result.suggestions = serialize_suggestions(outcome.trail, verified=True)`.
  - elif `suggest`: `result.suggestions = serialize_suggestions(surface.suggest_from_result(result, df, verify=True), verified=True)`.
  - else (default): `result.suggestions = serialize_suggestions(surface.maybe_suggest(result, df, verify=False), verified=False)`.

- [ ] **Step 4: Run → passes; run the broader `tests/test_api.py` to confirm no regression. Commit.**

---

## Task 7: CLI surface

**Files:** Modify the `dedupe` command under `cli/`. Test: extend the CLI test module.

- [ ] Add `--suggest` / `--heal` flags to `goldenmatch dedupe`; pass through to `dedupe_df(suggest=/heal=)`. On a default run, when `result.suggestions` is non-empty, print a one-line hint (e.g. `N suggestion(s) to improve this — re-run with --suggest to see them, --heal to apply`). `--suggest` prints the serialized table; `--heal` prints the applied trail. TDD: a CLI test asserting the flags parse and the hint prints when suggestions are present (monkeypatch `dedupe_df` to return a result with suggestions). Commit.

---

## Task 8: Agent surfaces (MCP + A2A)

**Files:** `mcp/server.py` (+ `tests/test_mcp_new_tools.py`), `a2a/server.py` + `a2a/skills.py` (+ `tests/test_a2a.py`).

- [ ] MCP: repoint/extend the suggestion tool to call the real healer (`review_config` / `suggest_from_result`) returning `serialize_suggestions`; add a `heal` tool. Bump the server-card count (`mcp/server.py` ~1002) AND `len(TOOLS) == 68` → new N in `tests/test_mcp_new_tools.py`. TDD: tool present + count assertion. Commit.
- [ ] A2A: add a `suggest`/`heal` skill in `_SKILLS` + dispatch in `skills.py`; bump `test_agent_card_has_37_skills` → new N. Commit.

---

## Task 9: UI surfaces (TUI + web + REST)

**Files:** `tui/` (a Suggestions panel), `web/` (run-view section + apply endpoint), `api/server.py` (suggest/heal endpoints). Tests per the existing per-surface patterns (TUI pilot test, web router test, REST endpoint test).

- [ ] TUI: a Suggestions panel reading `result.suggestions` with an apply action (reuse the correction/apply write path). Update `test_tabs_exist`/panel test if a tab/panel count is asserted. Commit.
- [ ] Web: a suggestions section on the run view + a `POST` apply endpoint (mirror the review-queue UI pattern); router test. Commit.
- [ ] REST: `GET` suggestions on a run + `POST /heal` (auth-gated like the rest); endpoint test. Commit.

---

## Task 10: Docs + CHANGELOG

- [ ] Update `docs-site/goldenmatch/config-suggestions.mdx` (the healer page from the prior sweep) to document the default-on surface + `suggest=`/`heal=` + the kill-switch; add the `GOLDENMATCH_SUGGEST_ON_DEDUPE` row to `tuning.mdx`. CHANGELOG `[Unreleased]` entry. (Full doc sweep via the rollout-docs-sweep skill at the end.) Commit.

---

## Task 11: End-to-end verification (CI / native)

- [ ] On a native build, confirm: a triggered `dedupe_df` on `ncvr_synthetic`-shaped input attaches candidates; `heal=True` improves F1 with no net-negative; a GREEN/no-dip input attaches `[]` and the kernel is NOT called (cost short-circuit, asserted). Record results in the spec's findings. (Run in CI on `large-new-64GB` if local native is unavailable.)

---

## Done criteria (from the spec)
- Default `dedupe_df` attaches raw candidates only when the free trigger fires + native present; byte-identical no-op otherwise; kill-switch works (Tasks 3,4,6).
- `suggest=True` (verified, not applied) and `heal=True` (verified loop, applied, `heal_trail`) work (Tasks 5,6).
- All seven surfaces expose the healer via the one core helper + `serialize_suggestions`; surface count-assertions updated (Tasks 7,8,9).
- Graceful no-native everywhere; cost short-circuit proven by test (Tasks 2,4,11).

## Out of scope (named follow-ons)
Default auto-apply; base-wheel bundling; the TS/WASM port; new suggestion rules.
