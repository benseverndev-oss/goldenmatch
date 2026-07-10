# Controller Fused Auto-Routing — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route the pipeline to the fused kernels automatically — golden default-on-when-covered (broad, transparent), match under an est-peak-RSS pressure gate (narrow, capacity-survival) — so the ~2x capacity win reaches real `dedupe_df` calls, byte-identical.

**Architecture:** Golden is pipeline-local (try `run_golden_fused_arrow`, fall back to classic on `None`). Match is a Python controller POST-STEP (`maybe_route_fused_match`) run AFTER the backend plan is chosen — it sets `ExecutionPlan.use_fused_match`, which the pipeline reads to short-circuit block→score→cluster. Every fused entry declines to `None`/falls back, so byte-identity holds by construction.

**Tech Stack:** Python (controller + pipeline + planner), pytest. Depends on golden_fused (PR #1604) + match_fused (on main).

**Spec:** `docs/superpowers/specs/2026-07-09-controller-fused-auto-routing-design.md`

**Ground-truth seams (read before starting):**
- `core/autoconfig_controller.py:1335-1377` — `apply_planner_rules` (1335) → `apply_distributed_routing`/`enforce_routing`/throughput-overlay (1346-1373) → `plan.apply_to(committed_config)` (1376) → `history.execution_plan = plan` (1377). **The post-step inserts AFTER 1377** (after the final plan mutation + apply_to), and re-`apply_to`s or writes the flag directly so no earlier `dataclasses.replace` clobbers it.
- `core/execution_plan.py` — `ExecutionPlan` (frozen dataclass; `apply_to` writes `backend` + `_throughput_plan`).
- `core/runtime_profile.py` — `RuntimeProfile(available_ram_gb, cpu_count, disk_free_gb)`.
- `core/complexity_profile.py` — `BlockingProfile.estimated_pair_count`, `block_sizes_max`, `extrapolate_to` (scales `total_comparisons`, NOT the percentiles).
- `core/fused_match.py` — `run_match_fused_arrow` / `run_match_fused_multipass_arrow` + `match_fused_ready` / `match_fused_multipass_ready`.
- `core/golden_fused.py` — `run_golden_fused_arrow` + `golden_fused_ready`.
- `core/pipeline.py::_run_dedupe_pipeline` — golden seam (~2386 frames path / ~2536 dict path), block/score/cluster seam (~1657).
- `core/autoconfig_planner_rules.py` — `_PAIR_SCORE_BYTES=64`, the est_pair_gb pattern in `rule_bucket_suggested`.
- `scripts/bench_match_fused_memcap.py` — the measured classic peaks for calibration.

## Conventions

- **Branch** `feat/controller-fused-routing` (off `feat/golden-fused-kernel`); rebase onto main once #1604 lands. **Do NOT rebase mid-plan** unless #1604 merges — note it and continue.
- **Tests (Windows):** `PYTHONPATH=D:\show_case\goldenmatch\.worktrees\controller-fused-routing\packages\python\goldenmatch POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8 D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest <path> -v`. Run only the touched test modules; NOT the full suite (OOMs the box).
- **Byte-identity is the whole game** — every routing test asserts the fused-routed result equals the classic result (or that it declines/falls back), never a weakened assertion.
- Commit per step. `ruff check` touched files before commit.

---

## Stage A — est-peak-RSS model (foundation)

New pure module `core/fused_routing.py` (routing helpers, no pipeline/controller imports — testable in isolation).

### Task A.1: `estimate_classic_match_peak_rss_gb`

**Files:** Create `core/fused_routing.py`; Test: `tests/test_fused_routing.py`.

- [ ] **Step 1: failing unit tests** — monotonic in each input; a small hand-computed case:

```python
from goldenmatch.core.fused_routing import estimate_classic_match_peak_rss_gb

def test_est_rss_monotonic_and_components():
    base = estimate_classic_match_peak_rss_gb(n_rows=1_000_000, est_pairs=5_000_000, block_max=500, n_score_cols=3)
    assert base > 0
    # more pairs -> more RSS; bigger block -> more RSS; more cols -> more RSS
    assert estimate_classic_match_peak_rss_gb(1_000_000, 50_000_000, 500, 3) > base
    assert estimate_classic_match_peak_rss_gb(1_000_000, 5_000_000, 5000, 3) > base
    assert estimate_classic_match_peak_rss_gb(1_000_000, 5_000_000, 500, 10) > base
```

- [ ] **Step 2: run → fail.**
- [ ] **Step 3: implement** per spec §4.1, env-tunable constants:

```python
import os

_BYTES_PER_PAIR = float(os.environ.get("GOLDENMATCH_FUSED_BYTES_PER_PAIR", "64"))
_BYTES_PER_CELL = float(os.environ.get("GOLDENMATCH_FUSED_BYTES_PER_CELL", "40"))
_BLOCK_CONCURRENCY = float(os.environ.get("GOLDENMATCH_FUSED_BLOCK_CONCURRENCY", "4"))
_RSS_SCALE = float(os.environ.get("GOLDENMATCH_FUSED_RSS_SCALE", "1.0"))  # calibration knob

def estimate_classic_match_peak_rss_gb(n_rows, est_pairs, block_max, n_score_cols):
    frame_b = n_rows * max(1, n_score_cols) * _BYTES_PER_CELL
    pairs_b = est_pairs * _BYTES_PER_PAIR
    block_b = (block_max ** 2) * 8 * _BLOCK_CONCURRENCY
    return _RSS_SCALE * (frame_b + pairs_b + block_b) / 1e9
```

- [ ] **Step 4: run → pass. Step 5: commit.**

### Task A.2: calibration test against the memcap bench

**Files:** Test in `tests/test_fused_routing.py`.

- [ ] **Step 1:** Read `scripts/bench_match_fused_memcap.py` (and its result docstring / the fused_match module docstring citing ~5.19 GB classic at 10M) for the MEASURED classic peaks at 1M/5M/10M and the (est_pairs, block_max, n_score_cols) each run used. Write a calibration test asserting `estimate_classic_match_peak_rss_gb(FULL-data inputs)` lands within ±30% of the measured classic peak at each size:

```python
import pytest
# CALIB = [(n_rows, est_pairs, block_max, n_score_cols, measured_classic_gb), ...]  # from the bench
@pytest.mark.parametrize("n_rows,est_pairs,block_max,n_cols,measured", CALIB)
def test_est_rss_calibrated_to_bench(n_rows, est_pairs, block_max, n_cols, measured):
    est = estimate_classic_match_peak_rss_gb(n_rows, est_pairs, block_max, n_cols)
    assert 0.7 * measured <= est <= 1.3 * measured, f"{est} vs {measured}"
```

- [ ] **Step 2: run → likely fail** (default constants won't hit the band).
- [ ] **Step 3:** tune `_RSS_SCALE` (default) so the band passes at all three points. If a single scale can't fit all three (the sample-scale block issue, spec §4.1), the CALIB `block_max` MUST be the full-data measured max (from the bench), not a sample value — fix the CALIB inputs, not the model shape. Document the chosen scale + why in a comment.
- [ ] **Step 4: run → pass. Step 5: commit.**

---

## Stage B — ExecutionPlan.use_fused_match + apply_to

### Task B.1: add the plan field + wire apply_to

**Files:** Modify `core/execution_plan.py`; Test: `tests/test_execution_plan.py` (or the existing planner test module).

- [ ] **Step 1: failing test** — `ExecutionPlan(use_fused_match=True).apply_to(config)` sets the flag the pipeline reads; default plan leaves it False/unset (byte-identical to today).

```python
def test_apply_to_sets_use_fused_match():
    cfg = GoldenMatchConfig(...)
    ExecutionPlan(use_fused_match=True).apply_to(cfg)
    assert getattr(cfg, "_use_fused_match", False) is True
    cfg2 = GoldenMatchConfig(...)
    ExecutionPlan().apply_to(cfg2)  # default
    assert getattr(cfg2, "_use_fused_match", False) is False
```

- [ ] **Step 2: run → fail.**
- [ ] **Step 3:** add `use_fused_match: bool = False` to `ExecutionPlan`; in `apply_to`, `if self.use_fused_match: config._use_fused_match = True` (private attr, mirroring `_throughput_plan`). Confirm the frozen-dataclass default keeps every existing plan byte-identical.
- [ ] **Step 4: run → pass. Step 5: commit.**

---

## Stage C — config-driven divergence detection

### Task C.1: `config_needs_artifacts` (the config-driven, single-source gate)

This helper covers ONLY the CONFIG-driven conditions — the ones both the controller AND the pipeline can read authoritatively (spec §4.3). The caller-intent conditions (lineage/review/explain/anomaly requested) are NOT here; they ride the `_api.py` `fused_match_allowed` hint (Task D.3). This split is the reviewer's finding: caller-intent flags aren't in pipeline scope, so they can't be re-checked there.

**Files:** Modify `core/fused_routing.py`; Test: `tests/test_fused_routing.py`.

- [ ] **Step 1: failing tests** — one per config condition forcing True, an all-clear forcing False. Confirm each config surface FIRST:
  - `golden_rules.auto_split` (default True) — `GoldenRulesConfig.auto_split`.
  - `config.identity.enabled` — `IdentityConfig.enabled` (guard for `config.identity is None`).
  - golden `confidence_majority` — scan `golden_rules` default_strategy + field_rules (incl. list-form clauses) + field_groups + cluster_overrides.
  - full provenance — `config.output.lineage_provenance` (guard for missing `output`).

```python
from goldenmatch.core.fused_routing import config_needs_artifacts

def test_auto_split_on_blocks(): assert config_needs_artifacts(cfg_auto_split_on) is True
def test_identity_enabled_blocks(): ...
def test_confidence_majority_golden_blocks(): ...
def test_lineage_provenance_blocks(): ...
def test_all_clear_returns_false(): assert config_needs_artifacts(cfg_clean) is False  # auto_split off, identity off, no CM, no lineage-prov
```

- [ ] **Step 2: run → fail.**
- [ ] **Step 3: implement** `config_needs_artifacts(config) -> bool` = OR of (auto_split on, identity enabled, golden uses confidence_majority, lineage_provenance on), with a `_golden_uses_confidence_majority(golden_rules)` sub-helper. Pure config reads, no pipeline/caller state.
- [ ] **Step 4: run → pass. Step 5: commit.**

---

## Stage D — the controller post-step

### Task D.1: `maybe_route_fused_match`

**Files:** Modify `core/fused_routing.py`; Test: `tests/test_fused_routing.py`.

- [ ] **Step 1: failing tests** — covered + pressure + safe → True; each of the three falsified → False. Use the real `match_fused_ready` / `match_fused_multipass_ready` gates (build covered + uncovered configs). Assert the FS branch is NOT consulted (no `em_result` param; a probabilistic config → not covered by this post-step).

```python
from goldenmatch.core.fused_routing import maybe_route_fused_match

def test_routes_when_covered_pressure_safe(monkeypatch):
    # est-RSS forced over threshold via env; covered weighted config; auto_split off etc.
    assert maybe_route_fused_match(config=cfg, profile=prof, runtime=rt, needs_artifacts=False) is True

def test_not_covered_declines(): ...      # probabilistic/ANN/NE config -> False
def test_no_pressure_declines(): ...       # est-RSS under threshold -> False
def test_needs_artifacts_declines(): ...   # needs_artifacts=True -> False
def test_kill_switch(monkeypatch): monkeypatch.setenv("GOLDENMATCH_MATCH_FUSED", "0"); assert ... is False
```

- [ ] **Step 2: run → fail.**
- [ ] **Step 3: implement.** Take **`n_rows` as an explicit param** (the controller has the authoritative full-data count as the local `n_rows` at the insertion point — it's passed as `n_rows_full=n_rows` to `apply_planner_rules`). Do NOT read `profile.meta.n_rows_full` (it defaults to 0 on `profile_for_planner`, which is a `dataclasses.replace` of the sample profile). `estimated_pair_count` (property) + `block_sizes_max` (field) on `profile.blocking` are correct.

```python
def maybe_route_fused_match(*, config, profile, runtime, n_rows, needs_artifacts) -> bool:
    import os
    if os.environ.get("GOLDENMATCH_MATCH_FUSED", "").lower() in {"0", "false", "off"}:
        return False
    if needs_artifacts:
        return False
    from goldenmatch.core.fused_match import match_fused_ready, match_fused_multipass_ready
    if not (match_fused_ready(config) or match_fused_multipass_ready(config)):
        return False  # FS branch out of v1 (no EMResult at decision time)
    frac = float(os.environ.get("GOLDENMATCH_FUSED_PRESSURE_FRACTION", "0.65"))
    est = estimate_classic_match_peak_rss_gb(
        n_rows=n_rows,
        est_pairs=profile.blocking.estimated_pair_count,
        block_max=profile.blocking.block_sizes_max,
        n_score_cols=_count_score_cols(config),
    )
    return est > runtime.available_ram_gb * frac
```

`_count_score_cols(config)` derives the number of matchkey comparison fields from the covered weighted/multipass matchkey (read `config.get_matchkeys()` at Step 3 — confirm the field-list attribute).

- [ ] **Step 4: run → pass. Step 5: commit.**

### Task D.2: wire the post-step into the controller

**Files:** Modify `core/autoconfig_controller.py` (after line 1377); Test: `tests/test_autoconfig_controller_fused.py`.

- [ ] **Step 1: failing test** — the KEY property: the post-step fires even under NATIVE autoconfig. Mock `apply_planner_rules` to return a native-chosen plan (e.g. via a plan fixture / mock `native_module().autoconfig_decide_plan`), run the controller path to the post-step, assert `committed_config._use_fused_match` is set when `maybe_route_fused_match` would return True (patch it to True). And unset when False.

- [ ] **Step 2: run → fail.**
- [ ] **Step 3: implement** — after `history.execution_plan = plan` (line 1377), add:

```python
# Fused-match routing post-step (spec 2026-07-09). Runs AFTER the backend plan
# is chosen (native or Python) -- fused match is a whole-stage short-circuit,
# orthogonal to backend selection; the chosen backend is the fallback if fused
# declines. Not a DEFAULT_RULES rule (the native autoconfig kernel short-circuits
# those).
from goldenmatch.core.fused_routing import maybe_route_fused_match, config_needs_artifacts
# fused_match_allowed threaded from _api.py (Task D.3); default-deny if absent.
_needs_art = (not fused_match_allowed) or config_needs_artifacts(committed_config)
if maybe_route_fused_match(config=committed_config, profile=profile_for_planner,
                           runtime=runtime, n_rows=n_rows, needs_artifacts=_needs_art):
    plan = dataclasses.replace(plan, use_fused_match=True,
                               rule_name=(plan.rule_name or "") + "+fused_match_post_step")
    plan.apply_to(committed_config)
    history.execution_plan = plan
```

`config_needs_artifacts(config)` is the config-driven OR (auto_split/identity/confidence_majority/lineage_provenance) — the SAME helper the pipeline re-checks in F.1 (single source of truth). `fused_match_allowed` reaches `AutoConfigController.run` as a new keyword param (Task D.3); default `False`.

**The `needs_artifacts` sourcing is split by where each signal actually lives (pin exactly — this is a correctness gate, not a nicety):**

| signal | authoritative home | how to read |
|---|---|---|
| `auto_split` (default True) | config | `golden_rules.auto_split` — re-checkable at controller AND pipeline |
| `identity.enabled` | config | `config.identity.enabled` |
| golden `confidence_majority` | config | scan `golden_rules` (default/field_rules/groups/overrides) |
| full provenance (`__survivorship_prov__`) | config | `config.output.lineage_provenance` |
| lineage / review / explain / anomaly requested | **caller (`_api.py`)** — NOT in `_run_dedupe_pipeline` scope | thread a hint from `_api.py` |
| `scored_pairs` | **always built into the result dict unconditionally** | see capacity-mode note below |

**Capacity-mode contract (the scored_pairs finding).** `_run_dedupe_pipeline` builds `scored_pairs` into the result dict unconditionally, so it cannot be gated on "was it requested." A fused-match-routed run therefore returns **empty `scored_pairs` + absent cluster confidence/bottleneck/lineage** — this is the documented capacity-survival tradeoff, acceptable ONLY because match routing fires exclusively under est-RSS pressure (where the classic path would likely OOM: clusters+golden beats a crash). This is byte-identical where present (clusters + golden), absent where fused can't produce. Mark it in telemetry (`match_fused_capacity_mode=True`, Stage G) so it is never silent. The config-driven divergence conditions (auto_split/identity/confidence_majority/full-provenance) still hard-block routing — those would produce WRONG output, not merely absent artifacts.

**Threading (add to Stage D):** thread a `fused_match_allowed: bool` hint from `_api.py::dedupe_df`/`match_df` (the entry points that know what the caller asked for) → the controller. Set it True only when the call does NOT request lineage / review / explain / anomaly. The controller computes `needs_artifacts = (not fused_match_allowed) OR <config-driven conditions>`; absent the hint, `fused_match_allowed=False` (**default-deny**). The pipeline re-checks ONLY the config-driven conditions (authoritative there); the caller-intent conditions are already folded into the hint.

- [ ] **Step 4: run → pass. Step 5: commit.**

### Task D.3: thread the `fused_match_allowed` hint from `_api.py`

**Files:** Modify `_api.py` (`dedupe_df`/`match_df`) + the controller signature; Test: `tests/test_api_fused_routing.py`.

- [ ] **Step 1: failing test** — `dedupe_df(df, lineage=True)` (or explain/review/anomaly) sets `fused_match_allowed=False` reaching the controller (assert via a spy/patch on `maybe_route_fused_match`); a plain `dedupe_df(df)` under pressure sets it True.
- [ ] **Step 2: run → fail.**
- [ ] **Step 3:** compute `fused_match_allowed` in `_api.py` from the caller's kwargs (no lineage/review/explain/anomaly requested), thread it through to `AutoConfigController.run` / the pipeline call, into the post-step's `needs_artifacts`. Default-deny when the entry point can't determine it (e.g. file-based `dedupe()`/CLI paths that don't thread it → fused match simply never routes there in v1; document as a follow-up).
- [ ] **Step 4: run → pass. Step 5: commit.**

---

## Stage E — golden pipeline wiring (default-on)

### Task E.1: intercept the golden seam

**Files:** Modify `core/pipeline.py` (golden seam ~2386/2536); Test: `tests/test_pipeline_fused_golden.py`.

- [ ] **Step 1: failing parity test** — a `dedupe_df` (or `_run_dedupe_pipeline`) run on a covered slow-path golden config produces byte-identical golden output vs `GOLDENMATCH_GOLDEN_FUSED=0` (classic). Reuse the golden_fused parity discipline; assert the golden frame equal.

- [ ] **Step 2: run → fail** (fused not wired yet — or trivially passes if fused not invoked; make the test assert fused WAS used via the telemetry flag from E.2 so it can't pass by accident).

- [ ] **Step 3: implement** — at the golden seam, before `build_golden_records_from_frames` / `build_golden_records_batch`, add a helper `_try_fused_golden(multi_df, golden_rules, quality_scores, cluster_pair_scores, provenance, wants_full_provenance) -> pl.DataFrame | None`:

```python
def _try_fused_golden(...):
    import os
    if os.environ.get("GOLDENMATCH_GOLDEN_FUSED", "").lower() in {"0","false","off"}:
        return None
    if wants_full_provenance:  # __survivorship_prov__ consumer -> decline (spec 3)
        return None
    try:
        from goldenmatch.core.golden_fused import run_golden_fused_arrow
        return run_golden_fused_arrow(multi_df, golden_rules, quality_scores=quality_scores,
                                      cluster_pair_scores=cluster_pair_scores, provenance=provenance)
    except Exception:
        logger.debug("fused golden declined", exc_info=True)
        return None
```

Call it at BOTH branches; on non-`None`, use its output (set `golden_fused_used=True`); on `None`, fall through to the classic builder unchanged. **The two branches source the `multi_df` differently:**
- **Dict slow path (~2536):** `multi_df` (with `__row_id__`+`__cluster_id__`) is a live local — pass it directly.
- **Frames path (~2386):** there is NO `multi_df` local; the classic path passes `cluster_frames` + `_golden_source` into `build_golden_records_from_frames`, which builds it internally via `_multi_df_from_frames`. So the fused wiring here must call `_multi_df_from_frames(_golden_source, cluster_frames)` itself to get the frame `run_golden_fused_arrow` needs (it takes a `(__row_id__, __cluster_id__, cols)` frame and drops singletons/oversized itself).

**`wants_full_provenance` signal (pin, spec §3):** `= config.output.lineage_provenance` (the single flag that drives `__survivorship_prov__` on the classic path). When it's on, decline fused golden (field-level `source_row_id` would be a silent richness loss).

- [ ] **Step 4: run → pass. Step 5: commit.**

---

## Stage F — match pipeline short-circuit

### Task F.1: intercept the block/score/cluster seam

**Files:** Modify `core/pipeline.py` (~1657); Test: `tests/test_pipeline_fused_match.py`.

- [ ] **Step 1: failing test** — with `config._use_fused_match` set (simulate the post-step) on a covered + artifact-free config, `_run_dedupe_pipeline` short-circuits to `run_match_fused_arrow` and its clusters match the classic clustering on the same config; with the flag unset, classic runs. And a fallback test: flag set but `run_match_fused_arrow` returns `None` (uncovered) → classic runs (byte-identical).

- [ ] **Step 2: run → fail.**

- [ ] **Step 3: implement.** Insert the whole-stage short-circuit **BEFORE the scoring stage** — before the `with stage("fuzzy_scoring"): for mk in matchkeys:` loop (~line 1513) and the exact-matching block (~1490), NOT at ~1657 (which is mid-loop, after blocks are built). Covered configs have no exact matchkeys so exact is a no-op, but the short-circuit must precede the whole block/score/cluster sequence.

Two concrete sub-problems the plan pins:

1. **`columns` Mapping is NOT in scope** — `run_match_fused_arrow(columns, config, n_rows)` wants `columns: Mapping[str, pyarrow.Array]`, but the pipeline has a Polars `collected_df`. Build it: `columns = {c: collected_df[c].to_arrow() for c in _fused_needed_src_cols(config)}` (the blocking key + matchkey source columns), `n_rows = collected_df.height`.
2. **Route the fused clusters to golden via the multi_df, NOT a synthetic clusters dict** — the fused table is `(__row_id__, __cluster_id__)` only. Join `__cluster_id__` onto `collected_df` to build the same `multi_df` the dict path uses, then let the golden seam (Stage E) run on it (fused golden or classic). `run_golden_fused_arrow` takes exactly that frame and drops singletons itself, so no `cluster_frames`/oversized metadata and no `pair_scores` are needed (the `needs_artifacts` gate already excluded `confidence_majority`).

```python
if getattr(config, "_use_fused_match", False) and not config_needs_artifacts(config):
    from goldenmatch.core.fused_match import run_match_fused_arrow, run_match_fused_multipass_arrow
    columns = {c: collected_df[c].to_arrow() for c in _fused_needed_src_cols(config)}
    fused_tbl = run_match_fused_arrow(columns, config, n_rows=collected_df.height) \
        or run_match_fused_multipass_arrow(columns, config, n_rows=collected_df.height)
    if fused_tbl is not None:
        multi_df = collected_df.join(pl.from_arrow(fused_tbl), on="__row_id__", how="inner")  # attach __cluster_id__
        # capacity mode: DIRECT-call the golden builder on multi_df + assemble the
        # result dict, BYPASSING the 3-branch golden dispatch (its dict `else`
        # branch REBUILDS multi_df from a `clusters` dict, which we don't have).
        # scored_pairs = [], no cluster confidence/lineage.
        ...  # -> _try_fused_golden(multi_df, ...) or build_golden_records_batch(multi_df, ...) -> result dict, return
    # else fall through to the classic block->score->cluster path unchanged
```

Use the SINGLE-ARG `config_needs_artifacts(config)` (it derives `config.golden_rules` internally — the `golden_rules` local isn't bound until ~2281, out of scope at ~1513). It's the SAME helper the controller calls (single source). The caller-intent conditions (lineage/review/explain/anomaly) are already folded into the controller flag via the `_api.py` hint (Task D.3), so they are NOT re-checked here (they aren't in pipeline scope — the reason for the hint). Set `match_fused_capacity_mode=True` for telemetry (Stage G).

**Control-flow (pin this — the reviewer flagged it):** do NOT try to feed the pre-built `multi_df` into the existing dict-path golden branch (that branch rebuilds `multi_df` from a `clusters` dict). Instead call the golden builder DIRECTLY on the joined `multi_df` (`_try_fused_golden(multi_df, ...) or build_golden_records_batch(multi_df, ...)`), assemble the result dict (empty `scored_pairs`, no confidence/lineage), and return — bypassing the three-branch dispatch. Factor a small helper rather than duplicating the golden logic.

- [ ] **Step 4: run → pass. Step 5: commit.**

### Task F.2: kill-switch + no-pressure parity

- [ ] **Step 1: tests** — `GOLDENMATCH_MATCH_FUSED=0` → classic (byte-identical); a covered config with NO pressure (small est-RSS) → post-step doesn't set the flag → classic. Both assert byte-identical clustering.
- [ ] **Step 2: run → iterate → pass. Step 3: commit.**

---

## Stage G — telemetry + integration

### Task G.1: `golden_fused_used` + match telemetry

**Files:** Modify the result-dict assembly + `web/controller_telemetry.py` serializer; Test: `tests/test_fused_telemetry.py`.

- [ ] **Step 1: failing test** — a fused-golden-routed run surfaces `golden_fused_used=True`; a fused-match-routed run surfaces `use_fused_match` / `rule_name` containing `fused_match_post_step` AND `match_fused_capacity_mode=True` (the marker that this run shed scored_pairs/confidence/lineage), so the capacity tradeoff is never silent.
- [ ] **Step 2: run → fail.**
- [ ] **Step 3:** add `golden_fused_used: bool` + `match_fused_capacity_mode: bool` to the `_run_dedupe_pipeline` result dict (set in E.1 / F.1); confirm `serialize_telemetry` already carries `rule_name` (it does). Surface both new flags through the telemetry serializer.
- [ ] **Step 4: run → pass. Step 5: commit.**

### Task G.2: end-to-end integration

- [ ] **Step 1:** an integration test: a covered `dedupe_df` under simulated pressure (env-forced est-RSS over threshold) routes to fused (assert `use_fused_match` + `golden_fused_used`), and the FINAL result (clusters + golden) is byte-identical to the same run with both `GOLDENMATCH_*_FUSED=0`. This is the whole-feature parity lock.
- [ ] **Step 2: run → iterate → pass. Step 3: commit.**

---

## Stage H — docs, rebase, PR

### Task H.1: docs sweep

- [ ] Invoke `rollout-docs-sweep`. Change set: ADDED `core/fused_routing.py` (`estimate_classic_match_peak_rss_gb`, `maybe_route_fused_match`, `config_needs_artifacts`), `ExecutionPlan.use_fused_match`, `golden_fused_used` + `match_fused_capacity_mode` telemetry, env flags (`GOLDENMATCH_GOLDEN_FUSED`, `GOLDENMATCH_MATCH_FUSED`, `GOLDENMATCH_FUSED_*`). Update `docs-site/goldenmatch/tuning.mdx` (the canonical opt-ins doc) with the new env flags; CHANGELOG under `[Unreleased]`; the controller-v3 planner doc/telemetry surface if it enumerates rules. Run `check_native_symbols` (no new native symbols — should be unaffected) + the version-consistency gate.

### Task H.2: rebase + PR

- [ ] If #1604 has merged, `git rebase --onto origin/main <feat/golden-fused-kernel-tip>` (drop the golden_fused commits now on main); else keep the base and note the dependency in the PR body.
- [ ] Verify the touched test modules green + `ruff`. Push (`gh auth switch --user benzsevern`; `unset GH_TOKEN`), open a PR titled `feat(goldenmatch): controller auto-routes to the fused path (golden default-on, match under memory pressure)`, arm `gh pr merge --auto --squash`, switch auth back. STOP (don't poll CI).
- [ ] Update the memory note `project_fused_golden_kernel` / a new `project_controller_fused_routing` with the routing design + the est-RSS calibration.

## Non-goals (do NOT build)

- FS/probabilistic match routing (EM unavailable at decision time — follow-up).
- Teaching the native `autoconfig_decide_plan` kernel about fused (the post-step sidesteps it).
- Distributed/Sail fused routing; changing existing backend-rule thresholds.
