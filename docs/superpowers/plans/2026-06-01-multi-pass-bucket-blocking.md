# Multi-pass blocking for the bucket backend — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the `bucket` scoring backend honor ALL blocking passes (not just `keys[0]`) so it reaches cluster/pair parity with polars-direct on multi_pass auto-config, fixing the Febrl3 recall regression (F1 0.8483 -> ~0.93) that native-by-default exposed.

**Architecture:** Refactor `backends/score_buckets.py::score_buckets` to loop over `blocking_config.passes or blocking_config.keys`. The key-INDEPENDENT setup (slim projection, the frozen exclude set, fast-path resolution, native scorer ids, native exclude handle, and the worker closures) is computed ONCE and hoisted above the loop. A new nested `_score_single_pass(key)` runs the existing per-key body (build `__block_key__` -> hash/partition -> score workers) for one pass. Pairs from all passes are accumulated; cross-pass duplicates are emitted and collapse downstream in `build_clusters` (exactly mirroring polars-direct, whose score is pass-invariant). Single-pass configs reduce to one iteration -> byte-identical to today (protects the 5M/25M scale path).

**Tech Stack:** Python 3.11+, Polars, the goldenmatch `score_buckets` backend, optional Rust/PyO3 native kernel. Tests via pytest. Reference spec: `docs/superpowers/specs/2026-06-01-multi-pass-bucket-blocking-design.md`.

**Branch:** `docs/bucket-native-default` (the PR #667 branch — this fix is what makes that PR's default-flip safe; it must land in the same PR).

**Run tests with:** `cd packages/python/goldenmatch && ../../../.venv/Scripts/python.exe -m pytest <path> -v` (Windows; native `_native.pyd` is already built in-tree). Do NOT run the full suite locally (xdist OOMs Ben's box) — run targeted files only; CI runs the full suite.

---

## File Structure

- `packages/python/goldenmatch/goldenmatch/backends/score_buckets.py` — MODIFY `score_buckets` (lines ~318-765): hoist key-independent setup, extract `_score_single_pass`, loop passes. Update the docstring (remove the "keys[0] only / multi-key not supported" note at ~335).
- `packages/python/goldenmatch/tests/test_score_buckets_multipass.py` — CREATE. Parity (bucket vs polars), single-pass regression lock, missing-field guard.
- `packages/python/goldenmatch/tests/test_planner_integration.py` — MODIFY 3 tests (native-aware backend assertion).
- `packages/python/goldenmatch/tests/test_autoconfig_planner_protocol.py` — MODIFY 1 test (native-aware backend assertion).
- `packages/python/goldenmatch/tests/test_partitioned_block_scoring_pipeline.py` — MODIFY 2 tests (pin native off).
- `packages/python/goldenmatch/tests/test_prepared_record_store_pipeline.py` — MODIFY 1 test (pin native off).
- `packages/python/goldenmatch/tests/test_autoconfig_benchmarks.py` OR a new `tests/test_bucket_febrl3_parity.py` — CREATE the Febrl3 bucket-parity integration test (skip when `recordlinkage` absent).

---

## Task 1: Multi-pass loop in `score_buckets`

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/backends/score_buckets.py` (`score_buckets`, ~318-765)
- Test: `packages/python/goldenmatch/tests/test_score_buckets_multipass.py` (create)

**Context for the implementer — current shape of `score_buckets` (read these lines first):**
- `:364` `key_expr = _build_block_key_expr(blocking_config.keys[0])` — the single-key bug.
- `:381-402` slim projection; `:390` `for key in blocking_config.keys:` builds the keep-set of source fields.
- `:406` `keyed = slim_df.with_columns(key_expr)`; `:416-453` small-block fast path OR hash+`partition_by` -> `buckets_dict`.
- `:464` `frozen_exclude = frozenset(matched_pairs)`; `:465-466` `non_empty_buckets`.
- `:479-484` `fast_path_specs = _resolve_fast_path(...)`; `:495-501` `native_scorer_ids`; `:513-527` `native_exclude_handle`.
- `:529-729` three closures: `_apply_match_mode_filter`, `_score_one_bucket_fast`, `_score_one_bucket` — ALL key-independent (they reference `fast_path_specs`, `frozen_exclude`, `native_*`, `mk`, `find_fuzzy_matches`, sort on the `__block_key__` COLUMN, never the key object).
- `:731-753` worker loop -> `all_pairs`, then `matched_pairs.add` for every pair; `:755-759` `record_metrics`.

**Key insight:** the only key-DEPENDENT lines are `:364` (key_expr), `:406` (keyed), `:416-453` (bucketing), `:465-466` (non_empty_buckets for THAT key), and `:731-753` (the worker loop). Everything else is computed once.

- [ ] **Step 1: Write the failing parity test**

Create `tests/test_score_buckets_multipass.py`. The fixture is a small person frame where a true-duplicate pair shares `surname` + `date_of_birth` but has a corrupted `given_name`, so the PRIMARY blocking key (which auto-config builds from given_name/state) cannot block them together — only a later surname/dob pass can. This guarantees bucket (single-pass, today) and polars-direct DIVERGE before the fix and CONVERGE after.

```python
import os
import polars as pl
import pytest
import goldenmatch as gm
from goldenmatch.core.autoconfig import auto_configure_df


def _multipass_person_df() -> pl.DataFrame:
    # 30 distinct people + a handful of duplicates whose given_name is
    # corrupted (so the given_name-led primary key misses them) but whose
    # surname + dob agree (a later pass catches them).
    rows = []
    for i in range(30):
        rows.append({
            "given_name": f"person{i}", "surname": f"sur{i}",
            "state": "ca" if i % 2 else "ny",
            "date_of_birth": f"19{50 + (i % 40):02d}-01-{1 + (i % 27):02d}",
        })
    # duplicates: same surname+dob+state, scrambled given_name
    for i in (3, 7, 11, 19, 23):
        base = rows[i]
        rows.append({
            "given_name": base["given_name"][::-1] + "x",  # corrupt given_name
            "surname": base["surname"],
            "state": base["state"],
            "date_of_birth": base["date_of_birth"],
        })
    return pl.DataFrame(rows)


def _clusters_for_backend(df: pl.DataFrame, backend: str | None) -> set[frozenset[int]]:
    """Run dedupe_df with a forced backend; return canonical cluster membership
    (frozensets of row ids) for multi-member clusters only."""
    cfg = auto_configure_df(df)
    for mk in cfg.get_matchkeys():
        if getattr(mk, "rerank", None):
            mk.rerank = False  # offline: no cross-encoder download
    if backend is not None:
        cfg.backend = backend
    result = gm.dedupe_df(df, config=cfg)
    members = set()
    for cid, info in result.clusters.items():
        ids = info.get("members") if isinstance(info, dict) else None
        if ids and len(ids) >= 2:
            members.add(frozenset(int(x) for x in ids))
    return members


def test_bucket_matches_polars_on_multipass():
    """Bucket (native scoring) must produce the same multi-member clusters as
    polars-direct when auto-config emits multi_pass blocking."""
    df = _multipass_person_df()
    # sanity: auto-config must actually emit multi_pass for this shape
    cfg = auto_configure_df(df)
    assert cfg.blocking is not None and cfg.blocking.passes, \
        "fixture must trigger multi_pass blocking or the test proves nothing"
    polars_clusters = _clusters_for_backend(df, "polars-direct")
    bucket_clusters = _clusters_for_backend(df, "bucket")
    assert bucket_clusters == polars_clusters
```

- [ ] **Step 2: Run the test — verify it FAILS today**

Run: `cd packages/python/goldenmatch && ../../../.venv/Scripts/python.exe -m pytest tests/test_score_buckets_multipass.py::test_bucket_matches_polars_on_multipass -v`
Expected: FAIL — `bucket_clusters` is a strict subset of `polars_clusters` (bucket misses the corrupted-given_name duplicates). If it PASSES, the fixture isn't forcing multi_pass divergence — make the given_name corruption stronger / add more dup pairs until it fails, because the test must guard the fix.

- [ ] **Step 3: Refactor `score_buckets` — hoist key-independent setup, extract `_score_single_pass`, loop passes**

In `score_buckets`:
1. Compute `pass_keys` right after the empty-guard: `pass_keys = blocking_config.passes or blocking_config.keys`. (Both call sites pass the whole config; `passes` is `None` for static/single-key, so this falls back to `keys`.)
2. Slim projection (`:381-402`): change the keep-set loop from `for key in blocking_config.keys:` to `for key in pass_keys:` so fields used only by a non-primary pass survive. Compute slim ONCE, above the loop.
3. Hoist `frozen_exclude`, `_resolve_fast_path`, `native_scorer_ids`, `native_exclude_handle`, and the three closures (`_apply_match_mode_filter`, `_score_one_bucket_fast`, `_score_one_bucket`) ABOVE the pass loop — they are already key-independent. **Do NOT rebuild `frozen_exclude` / `native_exclude_handle` per pass** (that would diverge from polars; see spec "Implementation guard").
4. Extract the per-key body into a nested `def _score_single_pass(key) -> tuple[list[tuple[int,int,float]], int, int]` returning `(pass_pairs, blocks_scored, n_non_empty)`. Its body is the existing `:364` key_expr (now `_build_block_key_expr(key)`), `:406` keyed, `:416-453` bucketing/partition, `:465-466` non_empty_buckets, and the `:731-751` worker loop (accumulating into a LOCAL `pass_pairs`, NOT mutating `matched_pairs`).
5. Replace the single-key body with:

```python
all_pairs: list[tuple[int, int, float]] = []
total_blocks_scored = 0
total_non_empty = 0
slim_cols = set(slim_df.columns)
for key in pass_keys:
    if not set(key.fields) <= slim_cols:
        logger.warning(
            "score_buckets: skipping pass %s -- field(s) %s absent from prepared_df",
            key.fields, sorted(set(key.fields) - slim_cols),
        )
        continue
    pass_pairs, blocks, n_non_empty = _score_single_pass(key)
    all_pairs.extend(pass_pairs)
    total_blocks_scored += blocks
    total_non_empty += n_non_empty
# Update matched_pairs ONCE at the end (union of exact + all fuzzy pairs),
# mirroring polars: cross-pass duplicates in all_pairs collapse downstream
# in build_clusters' pair_scores dict.
for a, b, _s in all_pairs:
    matched_pairs.add((min(a, b), max(a, b)))
record_metrics({
    "bucket_count": total_non_empty,
    "bucket_n_target": n_buckets,
    "block_count_scored": total_blocks_scored,
})
return all_pairs
```

6. Update the `score_buckets` docstring: remove "`keys[0]` is used; multi-key blocking is not supported in bucket mode v1" and replace with a note that it iterates `blocking_config.passes or keys`, emitting cross-pass duplicates that collapse downstream (parity with polars-direct).

**Watch-outs:**
- The closures sort on the `__block_key__` COLUMN, which `_score_single_pass` must (re)create per pass via `slim_df.with_columns(key_expr)`. Keep `slim_df` immutable; each pass produces its own `keyed`/`bucketed` frame and `del`s them as today (preserves peak RSS).
- The small-block fast path (`:416-425`) and the `del keyed/bucketed` (`:451-452`) live INSIDE `_score_single_pass`.
- `_resolve_fast_path` prints an `ENGAGED` diagnostic; it now runs once (good — was once before too).

- [ ] **Step 4: Run the parity test — verify it PASSES**

Run: `../../../.venv/Scripts/python.exe -m pytest tests/test_score_buckets_multipass.py::test_bucket_matches_polars_on_multipass -v`
Expected: PASS — `bucket_clusters == polars_clusters`.

- [ ] **Step 5: Write the single-pass regression lock + missing-field guard tests**

```python
def test_single_pass_bucket_unchanged():
    """A static (single-key) config must yield the same bucket clusters as
    before the multi-pass change -- locks the proven 5M/25M single-key path.
    We assert bucket == polars on a shape whose auto-config is single-pass,
    OR force a single-key config explicitly."""
    df = pl.DataFrame({
        "name": [f"alice{i}" for i in range(20)] + [f"alice{i}" for i in range(20)],
        "email": [f"u{i}@x.com" for i in range(20)] * 2,
    })
    cfg = auto_configure_df(df)
    # Force single-pass to exercise the pass_keys == [keys[0]] branch.
    if cfg.blocking is not None:
        cfg.blocking.passes = None
        cfg.blocking.strategy = "static"
    for mk in cfg.get_matchkeys():
        if getattr(mk, "rerank", None):
            mk.rerank = False
    bucket = _clusters_for_backend_with_cfg(df, cfg, "bucket")
    polars = _clusters_for_backend_with_cfg(df, cfg, "polars-direct")
    assert bucket == polars


def test_missing_pass_field_is_skipped(monkeypatch):
    """A pass whose field is absent from the frame is skipped, not crashed."""
    # Build a 2-pass config where pass 2 names a column the df lacks.
    ... (construct df + cfg.blocking.passes with a bogus field; assert
         dedupe_df(df, config=cfg, backend='bucket') does not raise and
         returns the pass-1 clusters)
```

(Add a small `_clusters_for_backend_with_cfg(df, cfg, backend)` helper that clones `cfg`, sets `.backend`, and runs — or parametrize the existing helper to accept a prebuilt cfg.)

- [ ] **Step 6: Run all three tests**

Run: `../../../.venv/Scripts/python.exe -m pytest tests/test_score_buckets_multipass.py -v`
Expected: 3 passed.

- [ ] **Step 7: ruff + commit**

```bash
cd D:/show_case/goldenmatch
.venv/Scripts/python.exe -m ruff check packages/python/goldenmatch/goldenmatch/backends/score_buckets.py packages/python/goldenmatch/tests/test_score_buckets_multipass.py
git add packages/python/goldenmatch/goldenmatch/backends/score_buckets.py packages/python/goldenmatch/tests/test_score_buckets_multipass.py
git commit -m "fix(bucket): honor all blocking passes (multi-pass parity with polars-direct)"
```

---

## Task 2: Make the planner integration tests native-aware

**Why:** With bucket now parity-correct, `_scoring_backend()` returning `"bucket"` when native is importable is the INTENDED default. The 4 tests below hardcode `"polars-direct"`; update them to assert the computed scoring backend.

**Files:**
- Modify: `tests/test_planner_integration.py` — `test_integration_simple_plan_fires_on_small_df` (~:67), `test_integration_postflight_report_renders_plan_line` (~:77), `test_integration_fast_box_plan_fires_at_500k_with_64gb` (~:174)
- Modify: `tests/test_autoconfig_planner_protocol.py` — `test_controller_run_attaches_execution_plan_to_history` (~:157)

- [ ] **Step 1: Add a native-aware expected-backend helper at the top of each test module**

```python
from goldenmatch.core._native_loader import native_enabled

def _expected_scoring_backend() -> str:
    return "bucket" if native_enabled("block_scoring") else "polars-direct"
```

(Confirm the import path: `grep -rn "def native_enabled" packages/python/goldenmatch/goldenmatch/core/_native_loader.py`. If the function lives elsewhere, import from there.)

- [ ] **Step 2: Replace the hardcoded assertions**

- `test_integration_simple_plan_fires_on_small_df`: `assert plan.backend == _expected_scoring_backend()` (was `== "polars-direct"`). Keep the `rule_name == "plan_selected_simple"` and `clustering_strategy == "in_memory"` assertions unchanged.
- `test_integration_postflight_report_renders_plan_line`: `assert f"backend={_expected_scoring_backend()}" in rendered` (was `"backend=polars-direct"`). Keep `"Plan: plan_selected_simple" in rendered`.
- `test_integration_fast_box_plan_fires_at_500k_with_64gb`: `assert plan.backend == _expected_scoring_backend()`. Keep `rule_name == "plan_selected_fast_box"` and `max_workers == 16`.
- `test_controller_run_attaches_execution_plan_to_history`: `assert plan.backend == _expected_scoring_backend()` (was `== "polars-direct"`).

- [ ] **Step 3: Run both files**

Run: `../../../.venv/Scripts/python.exe -m pytest tests/test_planner_integration.py tests/test_autoconfig_planner_protocol.py -v`
Expected: all pass (native is built locally -> backend == "bucket"; assertion now matches).

- [ ] **Step 4: Commit**

```bash
git add packages/python/goldenmatch/tests/test_planner_integration.py packages/python/goldenmatch/tests/test_autoconfig_planner_protocol.py
git commit -m "test(planner): native-aware backend assertions (bucket is the default when native present)"
```

---

## Task 3: Pin native off for the orthogonal pipeline-feature tests

**Why:** `test_partitioned_block_scoring_pipeline.py` and `test_prepared_record_store_pipeline.py` exercise the partitioned-block-scoring / prepared-record-store features on the polars-direct path. The backend flip to bucket diverts them off that path. They are not about backend selection, so pin native off to isolate the feature under test.

**Files:**
- Modify: `tests/test_partitioned_block_scoring_pipeline.py` — `test_flag_on_materializes_blocks_to_store` (~:84), `test_pipeline_uses_bucketed_materialize_on_flag_on` (~:173)
- Modify: `tests/test_prepared_record_store_pipeline.py` — `test_dedupe_df_with_prepared_store_skips_second_run_transform`

- [ ] **Step 1: Add `GOLDENMATCH_NATIVE=0` to the 3 tests**

Prefer a module-level autouse fixture so it covers every test in each file and survives xdist worker isolation:

```python
import pytest

@pytest.fixture(autouse=True)
def _force_polars_direct(monkeypatch):
    # These tests exercise the partitioned-block-scoring / prepared-record-store
    # path, which only runs under polars-direct. Native-by-default would route
    # to bucket and bypass the machinery under test.
    monkeypatch.setenv("GOLDENMATCH_NATIVE", "0")
```

(If `native_enabled` caches its result at import time, setenv alone may not flip it. Verify: `grep -n "lru_cache\|_cache\|@cache" packages/python/goldenmatch/goldenmatch/core/_native_loader.py`. If cached, also clear the cache in the fixture, e.g. `native_enabled.cache_clear()`, or use `GOLDENMATCH_PLANNER_BUCKET=0` which the planner reads per-run.)

- [ ] **Step 2: Run both files**

Run: `../../../.venv/Scripts/python.exe -m pytest tests/test_partitioned_block_scoring_pipeline.py tests/test_prepared_record_store_pipeline.py -v`
Expected: all pass.

- [ ] **Step 3: Commit**

```bash
git add packages/python/goldenmatch/tests/test_partitioned_block_scoring_pipeline.py packages/python/goldenmatch/tests/test_prepared_record_store_pipeline.py
git commit -m "test(pipeline): pin native off for partitioned/prepared-store tests (orthogonal to backend)"
```

---

## Task 4: Febrl3 bucket-parity integration test (the headline accuracy lock)

**Why:** Lock the actual regression that started this: bucket F1 on Febrl3 must match polars-direct (~0.93), not 0.8483. Skips cleanly when `recordlinkage` is absent (optional dep; present in CI).

**Files:**
- Create: `tests/test_bucket_febrl3_parity.py`

- [ ] **Step 1: Write the test**

```python
import os
import pytest

pytest.importorskip("recordlinkage")
import sys
from pathlib import Path
# dqbench_adapters lives under repo-root scripts/ (see scripts/run_benchmarks.py)
_REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(_REPO / "scripts"))


def _febrl3_f1(backend: str | None) -> float:
    from dqbench_adapters.febrl3 import load_febrl3_df_and_gt, evaluate_febrl3
    import goldenmatch as gm
    from goldenmatch.core.autoconfig import auto_configure_df
    df, gt = load_febrl3_df_and_gt()

    def _dd(frame):
        cfg = auto_configure_df(frame)
        for mk in cfg.get_matchkeys():
            if getattr(mk, "rerank", None):
                mk.rerank = False
        if backend is not None:
            cfg.backend = backend
        return gm.dedupe_df(frame, config=cfg)

    return evaluate_febrl3(df, gt, _dd).f1


@pytest.mark.benchmark
def test_bucket_febrl3_f1_matches_polars(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")
    polars_f1 = _febrl3_f1("polars-direct")
    bucket_f1 = _febrl3_f1("bucket")
    # Parity within noise; both must clear the CI smoke floor.
    assert bucket_f1 >= 0.90, f"bucket Febrl3 F1 regressed: {bucket_f1}"
    assert abs(bucket_f1 - polars_f1) <= 0.02, f"bucket {bucket_f1} vs polars {polars_f1}"
```

(Confirm `parents[4]` resolves to repo root from `tests/`; adjust the index if needed. The `@pytest.mark.benchmark` mark keeps it out of the default fast lane if the suite excludes benchmarks — check how `test_autoconfig_benchmarks.py` is gated and match it so this runs where Febrl3 data/recordlinkage exist but is skippable otherwise.)

- [ ] **Step 2: Run it (native is built locally; recordlinkage was installed this session)**

Run: `cd D:/show_case/goldenmatch && .venv/Scripts/python.exe -m pytest packages/python/goldenmatch/tests/test_bucket_febrl3_parity.py -v -m benchmark`
Expected: PASS — bucket F1 ~0.93, within 0.02 of polars.

- [ ] **Step 3: Commit**

```bash
git add packages/python/goldenmatch/tests/test_bucket_febrl3_parity.py
git commit -m "test(bucket): Febrl3 F1 parity lock (bucket multi-pass == polars-direct)"
```

---

## Final validation (orchestrator step, after all tasks — NOT a subagent task)

1. Run the previously-red files together to confirm all 7 originally-failing tests are green:
   `cd packages/python/goldenmatch && ../../../.venv/Scripts/python.exe -m pytest tests/test_planner_integration.py tests/test_autoconfig_planner_protocol.py tests/test_partitioned_block_scoring_pipeline.py tests/test_prepared_record_store_pipeline.py tests/test_score_buckets_multipass.py -v`
2. Push the branch; let CI run the full `python (goldenmatch)` lane + `benchmark_runner_smoke` (the Febrl3 smoke that first caught this). Confirm both go green.
3. **Re-bench (sanity, not a gate)** per the spec's success bar: dispatch `bench-fs-stages` at `ns=200000,500000,750000` on `large-new-64GB` with the multi-pass fix; record F1 parity + wall + RSS. Fold the numbers into the bucket-native-default spec's validation section (the prior 4.5-5.3x number was single-pass and is now superseded). Per the "parity is enough" decision, bucket stays the default as long as F1 parity holds and wall isn't dramatically worse.

---

## Notes for the implementer

- **DRY/YAGNI:** only `score_buckets` changes; do NOT touch the native kernel, polars-direct, `build_blocks`, or the planner rule. No new blocking strategies.
- **The single most likely silent break:** rebuilding `frozen_exclude` / `native_exclude_handle` per pass, or adding an intra-loop `matched_pairs` skip. Don't. Freeze once, hoist, emit cross-pass duplicates — that IS the polars algorithm and the source of guaranteed parity. (Spec: "Implementation guard".)
- **Peak-RSS invariant:** keep slim ONCE and `del keyed/bucketed` inside each pass so only one pass's buckets are resident at a time.
- **Skill:** follow @superpowers:test-driven-development for each task (failing test first).
