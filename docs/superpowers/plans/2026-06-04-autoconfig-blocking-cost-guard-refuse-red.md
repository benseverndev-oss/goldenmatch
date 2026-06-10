# Auto-config Blocking Cost Guard + Refuse-on-RED (#715 reopened) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Stop auto-config from committing an unbounded `soundex(name)` blocking pass that explodes candidate pairs at scale, and refuse (instead of running) when it commits a RED give-up config.

**Architecture:** Three blocking-side changes in `build_blocking`/`_build_compound_blocking` (bound every emitted pass by projected full-N block size; let the compound search use sparse/identifier-typed numeric columns; scale the iteration budget), plus a new `allow_red_config` flag that makes a RED commit raise by default. Closed by an at-scale CI repro (the gap that let #715 reopen) + DQbench validation.

**Tech Stack:** Python 3.12, polars, pytest. Files: `goldenmatch/core/autoconfig.py`, `goldenmatch/core/autoconfig_controller.py`, `goldenmatch/core/blocking_candidates.py`, `goldenmatch/_api.py`.

**Spec:** `docs/superpowers/specs/2026-06-04-autoconfig-blocking-cost-guard-refuse-red-design.md`

**Precondition — branch from latest `main`** (has #720). Create `fix/715-blocking-cost-guard` off `origin/main`.

**Run environment:** prefix every local python/pytest with `POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8` and use the worktree `.venv/Scripts/python.exe`. Run ONLY targeted test files locally (full suite OOMs the box; it runs in CI). Kill zombie python with `powershell.exe -Command "Get-Process python | Stop-Process -Force"`.

---

## File Structure

- **Modify** `goldenmatch/core/blocking_candidates.py`: add `project_max_block_size(sample_max_block, sample_n, full_n)` (Chao1-style on group sizes; NEW — distinct from the existing cardinality projection and `estimate_avg_block_size`).
- **Modify** `goldenmatch/core/autoconfig.py`: `build_blocking` (per-pass block-size gate, B1) and `_build_compound_blocking` (candidate pool, B2).
- **Modify** `goldenmatch/core/autoconfig_controller.py`: `ControllerBudget.for_dataset` (iteration scaling) + the commit/raise path (A, reconciled with the #417 guard at `:879-937`).
- **Modify** `goldenmatch/_api.py` + `core/autoconfig.py::auto_configure_df`: thread `allow_red_config`.
- **Create** `tests/test_autoconfig_blocking_cost_715.py`, `tests/test_allow_red_config.py`.
- **Create/Modify** `.github/workflows/repro-issue-715.yml`: add an at-scale sparse-zip blocking-bound assertion (or a new `repro-issue-715-blocking.yml`).

---

## Task 1: Block-size projection helper (B1 foundation)

**Files:** Modify `goldenmatch/core/blocking_candidates.py`; Test `tests/test_autoconfig_blocking_cost_715.py`

- [ ] **Step 1: failing test**
```python
from goldenmatch.core.blocking_candidates import project_max_block_size

def test_project_max_block_size_scales_with_full_n():
    # A key with max block 250 in a 5K sample projects up toward full N.
    proj = project_max_block_size(sample_max_block=250, sample_n=5_000, full_n=1_000_000)
    assert proj > 250  # must grow toward full scale
    # identity when sample IS full data
    assert project_max_block_size(4055, 200_000, 200_000) == 4055
```
- [ ] **Step 2:** run, confirm ImportError/fail.
- [ ] **Step 3: implement** `project_max_block_size`. Block size for a fixed-cardinality key scales ~linearly with N (a key's block holds a fixed *fraction* of rows): `projected = sample_max_block * (full_n / sample_n)`, clamped to `[sample_max_block, full_n]`. Return `sample_max_block` when `full_n <= sample_n`. Document why linear (not sqrt): block size is a fraction of N, unlike distinct-count which is sublinear. Mirror the env-opt-out / clamp style of `scale_cardinality_ratio_to_full_population`.
- [ ] **Step 4:** run, pass.
- [ ] **Step 5: commit** `feat(autoconfig): add project_max_block_size for full-N blocking cost (#715)`.

---

## Task 2: B1 — gate every emitted blocking key/pass by projected block size

**Files:** Modify `goldenmatch/core/autoconfig.py` (`build_blocking`, ~1387-1503); Test same file.

Context: v0 runs `build_blocking` on the full df (non-distributed), so `_max_block_size(cols)` is exact; for safety/distributed reuse `project_max_block_size(_max_block_size(cols), df.height, n_rows_full)`. The name-fallback emits `keys=[soundex(best_name)]` + soundex/substring passes with NO per-pass size gate — that's the bug.

- [ ] **Step 1: failing test** — sparse-zip healthcare profiles (use the generator from `scripts/repro_issue_715.py::make_healthcare_df`, with zip5 ~50% present) at N where `soundex(name)`/single-name passes exceed `max_safe_block`. Assert: every key in `blocking.keys` AND every pass in `blocking.passes` has projected max block size `<= blocking.max_block_size` (i.e., no oversized pass emitted).
```python
def _proj(df, cols, full_n):
    from goldenmatch.core.blocking_candidates import project_max_block_size
    mb = int(df.group_by(cols).len().get_column("len").max() or 0)
    return project_max_block_size(mb, df.height, full_n)

def test_no_emitted_blocking_pass_exceeds_cap_on_sparse_zip():
    # build sparse-zip df at, say, 30K; n_rows_full simulates 1M
    ...
    blk = build_blocking(profiles, df, n_rows_full=1_000_000)
    cap = blk.max_block_size
    for k in (blk.keys or []):
        assert _proj(df, k.fields, 1_000_000) <= cap, (k.fields, "key oversized")
    for p in (blk.passes or []):
        # single-column soundex passes are the offenders
        assert _proj(df, p.fields, 1_000_000) <= cap, (p.fields, "pass oversized")
```
- [ ] **Step 2:** run, confirm FAIL (an oversized `soundex(name)` pass is currently emitted).
- [ ] **Step 3: implement.** After constructing the candidate pass list in the name-fallback branches (both the geo-compound branch ~1448-1475 and the no-geo branch ~1477-1503), FILTER passes: drop any pass whose `project_max_block_size(_max_block_size(p.fields), df.height, n_rows_full)` exceeds `max_safe_block`. The primary `keys=` must also be bounded — if the chosen `best_name`/soundex key is oversized, prefer a bounded compound (from Task 3 / `_build_compound_blocking`) or, if none survives, return a config the controller will read as degenerate (empty `keys` -> existing `#417` guard / new RED path handles it). Add an INFO log naming dropped oversized passes. Keep `skip_oversized=True`/`max_block_size` runtime filter as-is (defense in depth).
- [ ] **Step 4:** run, pass. Also re-run with DENSE zip5 and assert the bounded `zip5+last_name` compound is still chosen (no regression on the good shape).
- [ ] **Step 5: commit** `fix(autoconfig): drop oversized blocking passes by projected full-N block size (#715)`.

---

## Task 3: B2 — compound search uses sparse/identifier-typed numeric columns

**Files:** Modify `goldenmatch/core/autoconfig.py` (`_build_compound_blocking`, ~872-895); Test same file.

- [ ] **Step 1: failing test** — sparse zip5 (reclassified `identifier`, ~50% null) profiles; assert `_build_compound_blocking` returns a config whose primary key includes `zip5` (e.g. `zip5+last_name`) with projected max block `<= max_safe_block`.
- [ ] **Step 2:** run, confirm FAIL (zip5 excluded today: `col_type=="identifier"` and null>20%).
- [ ] **Step 3: implement.** In the candidate pool (`:890-895`): (a) stop excluding `col_type == "identifier"`; instead include a column when its single-column block size is non-singleton (`_max_block_size > 1`) AND `cardinality_ratio < 1.0` (exclude surrogate keys). (b) For the null ceiling: a high-null column is admissible as a compound *component* in a multi_pass set (other passes cover the null rows), so relax the `_null_rate <= max_null_rate` gate for the compound-component role only — keep it for single-key blocking. Keep `numeric`/`date` excluded. Keep `_check_source_overlap > 0`. NOTE: `_build_compound_blocking(profiles, df, max_safe_block, max_null_rate)` has no `n_rows_full` param; block-size checks here use `_max_block_size` on the build `df` directly, exact at the v0-on-full-df path. Threading `n_rows_full` in is OUT OF SCOPE unless a distributed/sample caller needs it.
- [ ] **Step 4:** run, pass. Re-run Task 2's dense-zip no-regression check.
- [ ] **Step 5: commit** `fix(autoconfig): admit sparse/identifier numeric cols to compound blocking (#715)`.

---

## Task 4: Iteration-budget scaling (add-on)

**Files:** Modify `goldenmatch/core/autoconfig_controller.py` (`ControllerBudget.for_dataset`, ~419-440); Test `tests/test_autoconfig_blocking_cost_715.py`.

- [ ] **Step 1: failing test**
```python
from goldenmatch.core.autoconfig_controller import ControllerBudget
def test_max_iterations_scales_with_size():
    assert ControllerBudget.for_dataset(2_000_000).max_iterations > \
           ControllerBudget.for_dataset(10_000).max_iterations
```
- [ ] **Step 2:** run, confirm FAIL (fixed at 3).
- [ ] **Step 3: implement.** In each size tier of `for_dataset`, set `max_iterations`: `<100K -> 3` (default), `100K-1M -> 4`, `>=1M -> 5`. Update the docstring tier table. Add an inline note that this is non-load-bearing for #715 (sample masks at-scale blocking blow-up) per the spec.
- [ ] **Step 4:** run, pass.
- [ ] **Step 5: commit** `feat(autoconfig): scale controller max_iterations with dataset size (#715)`.

---

## Task 5: A — allow_red_config default-raise

**Files:** Modify `goldenmatch/core/autoconfig_controller.py` (commit/raise path ~843-937, 1067-1078), `goldenmatch/core/autoconfig.py::auto_configure_df`, `goldenmatch/_api.py` (`dedupe_df`/`match_df`); Test `tests/test_allow_red_config.py`.

- [ ] **Step 1: failing tests** (mirror `tests/test_api_confidence_required_kwarg.py` patterns — monkeypatch a forced-RED history):
  - default (`allow_red_config=False`): `auto_configure_df` on a RED commit RAISES — **including a small-N (<100K) case** (default-raise is independent of REFUSE_AT_N).
  - `allow_red_config=True`: returns the config (today's warn-and-run).
  - `confidence_required` default unchanged otherwise.
- [ ] **Step 2:** run, confirm FAIL (no such kwarg; small-N RED runs today).
- [ ] **Step 3: implement.**
  - Add `allow_red_config: bool = False` to `AutoConfigController.run`, `auto_configure_df`, `dedupe_df`, `match_df`; thread through.
  - In the commit path: when committed health is RED with `stop_reason` set, and `not allow_red_config`, raise. Reconcile with the existing #417 guard (`:879-937`) and the confidence gate (`:857`): compute the raise decision ONCE, choose the most specific `failing_sub_profile`/message (blocking-degenerate if that's it, else generic RED), and raise a single error. Reuse `ControllerNotConfidentError` (extend its message to mention `allow_red_config=True`) rather than a second exception type.
  - `allow_red_config=True` short-circuits all RED-refuse paths to warn-and-run.
- [ ] **Step 4:** run, pass.
- [ ] **Step 5: commit** `feat(autoconfig): allow_red_config — refuse RED configs by default (#715)`.

---

## Task 6: At-scale CI repro (the mandatory gap-closer) + verdict reframe

**Files:** Modify `.github/workflows/repro-issue-715.yml` (or add `repro-issue-715-blocking.yml`); Modify `scripts/repro_issue_715.py`.

A sample-sized unit test cannot catch the at-scale blocking blow-up — that is why #715 reopened. This CI job is mandatory.

- [ ] **Step 1:** extend `scripts/repro_issue_715.py` (or add a sibling) to: generate the sparse-zip healthcare shape at a `--rows` count (default 500K), run the FULL `auto_configure_df(df, allow_red_config=True)` (so it doesn't raise) AND inspect the committed config's blocking; compute each emitted key/pass's max block size on the full df; print `BLOCKING BOUNDED` if all `<= max_safe_block`, else `BLOCKING UNBOUNDED: <fields>=<size>`.
- [ ] **Step 2:** workflow step asserts the log contains `BLOCKING BOUNDED` and fails on `BLOCKING UNBOUNDED`. Run at >= 500K on `large-new-64GB` (per the bench-runner default).
- [ ] **Step 3: commit** `test(autoconfig): at-scale sparse-zip blocking-bound CI assertion (#715)`.

---

## Task 7: Validation + PR + land

- [ ] **Step 1:** Full targeted test files green locally; ruff clean on all changed files.
- [ ] **Step 2:** `#528` quality gate (CI) + DQbench T1/T2/T3 (per `project_issue_489_zerolabel_gate`): confirm no recall regression from dropping oversized recall passes / changed blocking selection. Record before/after in the PR. **Hard gate** — the blocking-selection change is the recall risk.
- [ ] **Step 3:** Push (`benzsevern` auth dance), open PR to `main` linking #715; paste the at-scale `BLOCKING BOUNDED` output + DQbench numbers.
- [ ] **Step 4:** Wait `python (goldenmatch)` + `ci-required` green; dispatch the at-scale repro workflow on the PR branch and confirm `BLOCKING BOUNDED`.
- [ ] **Step 5:** Squash-merge `--delete-branch`; comment on #715 with the reproduced root cause (unbounded soundex pass + sparse-zip->identifier drop + fixed iterations + runs-on-RED) and the four-part fix; confirm closed.
