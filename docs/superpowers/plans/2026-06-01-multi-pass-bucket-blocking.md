# Multi-pass blocking for the bucket backend -- Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the `bucket` scoring backend honor ALL blocking passes (not just `keys[0]`) so it reaches cluster/pair parity with polars-direct on multi_pass auto-config, fixing the Febrl3 recall regression (F1 0.8483 -> ~0.93) that native-by-default exposed.

**Architecture:** Refactor `backends/score_buckets.py::score_buckets` to loop over `blocking_config.passes or blocking_config.keys`. The key-INDEPENDENT setup (slim projection, the frozen exclude set, fast-path resolution, native scorer ids, native exclude handle, and the worker closures) is computed ONCE and hoisted above the loop. A new nested `_score_single_pass(key)` runs the existing per-key body (build `__block_key__` -> hash/partition -> score workers) for one pass. Pairs from all passes are accumulated; cross-pass duplicates are emitted and collapse downstream in `build_clusters` (exactly mirroring polars-direct, whose per-pair score is pass-invariant). Single-pass configs reduce to one iteration -> byte-identical to today (protects the 5M/25M scale path).

**Tech Stack:** Python 3.11+, Polars, the goldenmatch `score_buckets` backend, optional Rust/PyO3 native kernel. Tests via pytest. Reference spec: `docs/superpowers/specs/2026-06-01-multi-pass-bucket-blocking-design.md`.

**Branch:** `docs/bucket-native-default` (the PR #667 branch -- this fix is what makes that PR's default-flip safe; it must land in the same PR).

**Run tests with:** `cd packages/python/goldenmatch && ../../../.venv/Scripts/python.exe -m pytest <path> -v` (Windows; native `_native.pyd` is already built in-tree). Do NOT run the full suite locally (xdist OOMs Ben's box) -- run targeted files only; CI runs the full suite.

---

## File Structure

- `packages/python/goldenmatch/goldenmatch/backends/score_buckets.py` -- MODIFY `score_buckets` (lines ~318-765): hoist key-independent setup, extract `_score_single_pass`, loop passes. Update the docstring (remove the "keys[0] only / multi-key not supported" note at ~335).
- `packages/python/goldenmatch/tests/test_score_buckets_multipass.py` -- CREATE. Parity (bucket vs polars), single-pass regression lock, missing-field guard.
- `packages/python/goldenmatch/tests/test_planner_integration.py` -- MODIFY 3 tests (native-aware backend assertion).
- `packages/python/goldenmatch/tests/test_autoconfig_planner_protocol.py` -- MODIFY 1 test (native-aware backend assertion).
- `packages/python/goldenmatch/tests/test_partitioned_block_scoring_pipeline.py` -- MODIFY 2 tests (pin native off).
- `packages/python/goldenmatch/tests/test_prepared_record_store_pipeline.py` -- MODIFY 1 test (pin native off).
- `packages/python/goldenmatch/tests/test_bucket_febrl3_parity.py` -- CREATE the Febrl3 bucket-parity integration test (skip when `recordlinkage` absent).

---

## Task 1: Multi-pass loop in `score_buckets`

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/backends/score_buckets.py` (`score_buckets`, ~318-765)
- Test: `packages/python/goldenmatch/tests/test_score_buckets_multipass.py` (create)

**Context for the implementer -- current shape of `score_buckets` (read these lines first):**
- `:364` `key_expr = _build_block_key_expr(blocking_config.keys[0])` -- the single-key bug.
- `:381-402` slim projection; `:390` `for key in blocking_config.keys:` builds the keep-set of source fields.
- `:406` `keyed = slim_df.with_columns(key_expr)`; `:416-453` small-block fast path OR hash+`partition_by` -> `buckets_dict`.
- `:464` `frozen_exclude = frozenset(matched_pairs)`; `:465-466` `non_empty_buckets`.
- `:479-484` `fast_path_specs = _resolve_fast_path(...)`; `:495-501` `native_scorer_ids`; `:513-527` `native_exclude_handle`.
- `:529-729` three closures: `_apply_match_mode_filter`, `_score_one_bucket_fast`, `_score_one_bucket` -- ALL key-independent (verified by review: they reference `fast_path_specs`, `frozen_exclude`, `native_*`, `mk`, `find_fuzzy_matches`, and sort on the `__block_key__` COLUMN, never the key object).
- `:731-753` worker loop -> `all_pairs`, then `matched_pairs.add` for every pair; `:755-759` `record_metrics`.

**Key insight:** the only key-DEPENDENT lines are `:364` (key_expr), `:406` (keyed), `:416-453` (bucketing), `:465-466` (non_empty_buckets for THAT key), and `:731-751` (the worker loop). Everything else is computed once.

- [ ] **Step 1: Write the failing parity test (EXPLICIT blocking config -- do NOT rely on auto-config)**

**Why explicit:** a prior review found `auto_configure_df` on hand-rolled synthetic data produces an unpredictable, often precision-collapsed (RED, everything-chains-into-one-cluster) config that does NOT isolate the multi-pass effect -- the red/green steps become meaningless. Control the blocking + matchkey directly so the divergence is exactly "a dup pair only co-blocked by pass 2."

Create `tests/test_score_buckets_multipass.py`. Fixture: 10 distinct people (distinct `name`/`city`/`zip`, no false matches, singletons in both passes) plus ONE duplicate pair -- rows 10 and 11 are the same person (`name="john smith"`, jaro score 1.0) with DIFFERENT `city` (so the `city` pass cannot co-block them) but the SAME `zip` (so the `zip` pass can). Explicit 2-pass blocking `keys=[city]`, `passes=[city, zip]`; explicit weighted matchkey on `name`.

```python
import polars as pl
import pytest
import goldenmatch as gm
from goldenmatch.config.schemas import BlockingConfig, BlockingKeyConfig


def _fixture_df() -> pl.DataFrame:
    rows = [
        {"name": f"distinct person {i}", "city": f"city{i}", "zip": f"{10000+i}"}
        for i in range(10)
    ]
    # The only true duplicate: same name, DIFFERENT city, SAME zip.
    rows.append({"name": "john smith", "city": "alpha", "zip": "99999"})   # row 10
    rows.append({"name": "john smith", "city": "beta",  "zip": "99999"})   # row 11
    return pl.DataFrame(rows)


def _two_pass_config():
    """Valid full config from auto-config, then OVERRIDE blocking + matchkey so
    only the multi-pass behavior is under test."""
    from goldenmatch.core.autoconfig import auto_configure_df
    cfg = auto_configure_df(_fixture_df())
    cfg.blocking = BlockingConfig(
        strategy="multi_pass",
        keys=[BlockingKeyConfig(fields=["city"])],
        passes=[BlockingKeyConfig(fields=["city"]), BlockingKeyConfig(fields=["zip"])],
    )
    # Replace matchkeys with ONE weighted matchkey on `name`. Build the
    # MatchkeyConfig the same way the codebase does -- inspect config/schemas.py
    # for the exact field names (type="weighted", threshold=0.8,
    # fields=[MatchkeyField(field="name", scorer="jaro_winkler", weight=1.0)]).
    # Assign it where cfg.get_matchkeys() reads (cfg.matchkeys or
    # cfg.match_settings.matchkeys) and VERIFY cfg.get_matchkeys() == [your mk].
    # Ensure mk.rerank is None/False (offline; no cross-encoder).
    ...  # construct + assign `mk`; assert len(cfg.get_matchkeys()) == 1
    return cfg


def _multi_member_clusters(df, cfg, backend) -> set[frozenset[int]]:
    c = cfg.model_copy(deep=True)
    if backend is not None:
        c.backend = backend
    result = gm.dedupe_df(df, config=c)
    out: set[frozenset[int]] = set()
    for _cid, info in result.clusters.items():
        ids = info.get("members") if isinstance(info, dict) else None
        if ids and len(ids) >= 2:
            out.add(frozenset(int(x) for x in ids))
    return out


def test_bucket_matches_polars_on_multipass():
    df = _fixture_df()
    cfg = _two_pass_config()
    assert cfg.blocking.passes and len(cfg.blocking.passes) == 2  # fixture sanity
    polars_clusters = _multi_member_clusters(df, cfg, "polars-direct")
    bucket_clusters = _multi_member_clusters(df, cfg, "bucket")
    assert frozenset({10, 11}) in polars_clusters   # reference path is correct
    assert bucket_clusters == polars_clusters
```

- [ ] **Step 2: Run the test -- verify it FAILS today, for the RIGHT reason**

Run: `cd packages/python/goldenmatch && ../../../.venv/Scripts/python.exe -m pytest tests/test_score_buckets_multipass.py::test_bucket_matches_polars_on_multipass -v`
Expected: FAIL. **Print both cluster sets and confirm the precise divergence:** `polars_clusters == {frozenset({10,11})}` and `bucket_clusters == set()` (bucket uses `keys[0]`=city, so rows 10/11 land in different city blocks and are never compared). If `bucket_clusters` already contains `{10,11}`, the explicit override isn't taking effect (check `cfg.backend` + blocking actually applied) -- the test must demonstrate bucket MISSING the zip-pass pair, or it doesn't guard the fix.

- [ ] **Step 3: Refactor `score_buckets` -- hoist key-independent setup, extract `_score_single_pass`, loop passes**

In `score_buckets`:
1. After the empty-guard, compute `pass_keys = blocking_config.passes or blocking_config.keys`. (`passes` is `None` for static/single-key -> falls back to `keys`.)
2. Slim projection (`:381-402`): change the keep-set loop from `for key in blocking_config.keys:` to `for key in pass_keys:` so fields used only by a non-primary pass survive. Compute slim ONCE, above the loop.
3. Hoist `frozen_exclude` (`:464`), `_resolve_fast_path` (`:479`), `native_scorer_ids` (`:495`), `native_exclude_handle` (`:513`), and the three closures (`:529-729`) ABOVE the pass loop -- they are already key-independent. **Do NOT rebuild `frozen_exclude` / `native_exclude_handle` per pass** and **do NOT add an intra-loop `matched_pairs` skip** -- either would diverge from polars (see spec "Implementation guard").
4. Extract the per-key body into a nested `def _score_single_pass(key) -> tuple[list[tuple[int,int,float]], int, int]` returning `(pass_pairs, blocks_scored, n_non_empty)`. Its body is the existing `:364` key_expr (now `_build_block_key_expr(key)`), `:406` keyed, `:416-453` bucketing/partition (incl. the small-block fast path AND the `del keyed/bucketed`), `:465-466` non_empty_buckets, and the `:731-751` worker loop accumulating into a LOCAL `pass_pairs` (it must NOT mutate `matched_pairs`).
5. Replace the single-key body with the loop:

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
    pass_pairs, blocks_scored, n_non_empty = _score_single_pass(key)
    all_pairs.extend(pass_pairs)
    total_blocks_scored += blocks_scored
    total_non_empty += n_non_empty
# Update matched_pairs ONCE at the end (union of exact + all fuzzy pairs),
# mirroring polars: cross-pass duplicates in all_pairs collapse downstream in
# build_clusters' pair_scores dict (cluster.py:471).
for a, b, _s in all_pairs:
    matched_pairs.add((min(a, b), max(a, b)))
record_metrics({
    "bucket_count": total_non_empty,
    "bucket_n_target": n_buckets,
    "block_count_scored": total_blocks_scored,
})
return all_pairs
```

6. Update the `score_buckets` docstring: remove "`keys[0]` is used; multi-key blocking is not supported in bucket mode v1"; replace with a note that it iterates `blocking_config.passes or keys`, emitting cross-pass duplicates that collapse downstream (parity with polars-direct).

**Watch-outs:**
- Keep `slim_df` immutable; each pass produces its own `keyed`/`bucketed` and `del`s them (preserves peak RSS -- only one pass resident at a time).
- The closures sort on the `__block_key__` COLUMN, which `_score_single_pass` recreates per pass via `slim_df.with_columns(key_expr)`.
- `pass_keys` may contain duplicate keys (Febrl3 repeats `['given_name']` x3, `['surname']` x2). That re-scores identical-key blocks; downstream pair-level collapse keeps output correct. This is INTENTIONAL for v1 -- do NOT add block-level dedup (spec Cost item 2).

- [ ] **Step 4: Run the parity test -- verify it PASSES**

Run: `../../../.venv/Scripts/python.exe -m pytest tests/test_score_buckets_multipass.py::test_bucket_matches_polars_on_multipass -v`
Expected: PASS -- `bucket_clusters == polars_clusters` (both contain `frozenset({10,11})`).

- [ ] **Step 5: Single-pass regression lock + missing-field guard (EXPLICIT configs)**

```python
def test_single_pass_bucket_unchanged():
    """A static SINGLE-key config -> pass_keys == [keys[0]] -> one iteration.
    Locks the proven 5M/25M single-key path by asserting bucket == polars."""
    # Frame where a single `zip` key blocks the dup pair together.
    df = pl.DataFrame({
        "name": [f"person {i}" for i in range(8)] + ["mary jones", "mary jones"],
        "zip":  [f"{200+i}" for i in range(8)] + ["55555", "55555"],
    })
    from goldenmatch.core.autoconfig import auto_configure_df
    cfg = auto_configure_df(df)
    cfg.blocking = BlockingConfig(
        strategy="static", keys=[BlockingKeyConfig(fields=["zip"])],
    )  # passes defaults to None -> pass_keys == [keys[0]]
    # one weighted matchkey on `name` (same construction as _two_pass_config)
    ...  # build + assign mk; rerank off
    assert cfg.blocking.passes is None
    polars = _multi_member_clusters(df, cfg, "polars-direct")
    bucket = _multi_member_clusters(df, cfg, "bucket")
    assert bucket == polars
    assert frozenset({8, 9}) in bucket


def test_missing_pass_field_is_skipped():
    """A pass naming a field ABSENT FROM THE FRAME ENTIRELY is skipped, not
    crashed; the present passes still produce their pairs."""
    df = _fixture_df()                      # has name/city/zip, NOT 'ssn'
    cfg = _two_pass_config()
    cfg.blocking = BlockingConfig(
        strategy="multi_pass",
        keys=[BlockingKeyConfig(fields=["zip"])],
        passes=[BlockingKeyConfig(fields=["zip"]),
                BlockingKeyConfig(fields=["ssn"])],   # 'ssn' not in df
    )
    bucket = _multi_member_clusters(df, cfg, "bucket")   # must not raise
    assert frozenset({10, 11}) in bucket                 # found via the zip pass
```

(The `...` placeholders reuse the same weighted-matchkey construction as `_two_pass_config`; factor it into a shared `_name_matchkey(cfg)` helper to stay DRY. The missing-field key must name a column ABSENT FROM THE DATAFRAME -- because the slim keep-set now unions over `pass_keys`, a field present in the df is always retained, so only a truly-absent field exercises the guard.)

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

**Why:** With bucket now parity-correct, `_scoring_backend()` returning `"bucket"` when native is importable is the INTENDED default. The 4 tests below hardcode `"polars-direct"`; update them to assert the REAL selector's output.

**Files:**
- Modify: `tests/test_planner_integration.py` -- `test_integration_simple_plan_fires_on_small_df` (~:67), `test_integration_postflight_report_renders_plan_line` (~:77), `test_integration_fast_box_plan_fires_at_500k_with_64gb` (~:174)
- Modify: `tests/test_autoconfig_planner_protocol.py` -- `test_controller_run_attaches_execution_plan_to_history` (~:157)

- [ ] **Step 1: Import the real selector (do NOT reimplement it)**

Use the planner's own `_scoring_backend`, so the assertion also respects the `GOLDENMATCH_PLANNER_BUCKET` opt-out (a hand-rolled `native_enabled` check would mispredict when that env var is set). At the top of each test module:

```python
from goldenmatch.core.autoconfig_planner_rules import _scoring_backend
```

(Verify the symbol: `grep -n "def _scoring_backend" packages/python/goldenmatch/goldenmatch/core/autoconfig_planner_rules.py`. Confirm it takes no required args; if it needs a runtime/profile arg, pass the same one the test already has.)

- [ ] **Step 2: Replace the hardcoded assertions**

- `test_integration_simple_plan_fires_on_small_df`: `assert plan.backend == _scoring_backend()` (was `== "polars-direct"`). Keep `rule_name == "plan_selected_simple"` and `clustering_strategy == "in_memory"`.
- `test_integration_postflight_report_renders_plan_line`: `assert f"backend={_scoring_backend()}" in rendered` (was `"backend=polars-direct"`). Keep `"Plan: plan_selected_simple" in rendered`.
- `test_integration_fast_box_plan_fires_at_500k_with_64gb`: `assert plan.backend == _scoring_backend()`. Keep `rule_name == "plan_selected_fast_box"` and `max_workers == 16`.
- `test_controller_run_attaches_execution_plan_to_history`: `assert plan.backend == _scoring_backend()` (was `== "polars-direct"`).

- [ ] **Step 3: Run both files**

Run: `../../../.venv/Scripts/python.exe -m pytest tests/test_planner_integration.py tests/test_autoconfig_planner_protocol.py -v`
Expected: all pass (native is built locally -> `_scoring_backend()` == "bucket"; assertion matches).

- [ ] **Step 4: Commit**

```bash
git add packages/python/goldenmatch/tests/test_planner_integration.py packages/python/goldenmatch/tests/test_autoconfig_planner_protocol.py
git commit -m "test(planner): assert real _scoring_backend() (bucket is the default when native present)"
```

---

## Task 3: Pin native off for the orthogonal pipeline-feature tests

**Why:** `test_partitioned_block_scoring_pipeline.py` and `test_prepared_record_store_pipeline.py` exercise the partitioned-block-scoring / prepared-record-store features on the polars-direct path. The backend flip to bucket diverts them off that path. They are not about backend selection, so pin native off to isolate the feature under test.

**Files:**
- Modify: `tests/test_partitioned_block_scoring_pipeline.py` -- `test_flag_on_materializes_blocks_to_store` (~:84), `test_pipeline_uses_bucketed_materialize_on_flag_on` (~:173)
- Modify: `tests/test_prepared_record_store_pipeline.py` -- `test_dedupe_df_with_prepared_store_skips_second_run_transform`

- [ ] **Step 1: Add a module-level autouse fixture pinning native off**

`native_enabled` reads `os.environ` on every call (verified: NOT `@lru_cache`-wrapped), so `monkeypatch.setenv` takes effect immediately -- no cache_clear needed.

```python
import pytest

@pytest.fixture(autouse=True)
def _force_polars_direct(monkeypatch):
    # These tests exercise the partitioned-block-scoring / prepared-record-store
    # path, which only runs under polars-direct. Native-by-default would route
    # to bucket and bypass the machinery under test.
    monkeypatch.setenv("GOLDENMATCH_NATIVE", "0")
```

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

**Why:** Lock the regression that started this: bucket F1 on Febrl3 must match polars-direct (~0.93), not 0.8483. Skips cleanly when `recordlinkage` is absent (optional dep; present in CI).

**Files:**
- Create: `tests/test_bucket_febrl3_parity.py`

- [ ] **Step 1: Write the test**

```python
import sys
from pathlib import Path
import pytest

pytest.importorskip("recordlinkage")
# dqbench_adapters lives under repo-root scripts/ (see scripts/run_benchmarks.py).
# Confirm parents[N] reaches repo root from packages/python/goldenmatch/tests/.
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
    assert bucket_f1 >= 0.90, f"bucket Febrl3 F1 regressed: {bucket_f1}"
    assert abs(bucket_f1 - polars_f1) <= 0.02, f"bucket {bucket_f1} vs polars {polars_f1}"
```

(Confirm `parents[4]` resolves to repo root from `tests/` -- adjust the index if not. Match the `@pytest.mark.benchmark` gating to how `test_autoconfig_benchmarks.py` is excluded/included so this runs where Febrl3 data + recordlinkage exist but is skippable otherwise. `evaluate_febrl3(df, gt, dedupe_df)` returns a `Febrl3Result` with `.f1`.)

- [ ] **Step 2: Run it (native built locally; recordlinkage installed this session)**

Run: `cd D:/show_case/goldenmatch && .venv/Scripts/python.exe -m pytest packages/python/goldenmatch/tests/test_bucket_febrl3_parity.py -v -m benchmark`
Expected: PASS -- bucket F1 ~0.93, within 0.02 of polars.

- [ ] **Step 3: Commit**

```bash
git add packages/python/goldenmatch/tests/test_bucket_febrl3_parity.py
git commit -m "test(bucket): Febrl3 F1 parity lock (bucket multi-pass == polars-direct)"
```

---

## Final validation (orchestrator step, after all tasks -- NOT a subagent task)

1. Run all previously-red files together to confirm the 7 originally-failing tests are green:
   `cd packages/python/goldenmatch && ../../../.venv/Scripts/python.exe -m pytest tests/test_planner_integration.py tests/test_autoconfig_planner_protocol.py tests/test_partitioned_block_scoring_pipeline.py tests/test_prepared_record_store_pipeline.py tests/test_score_buckets_multipass.py -v`
2. Push the branch; let CI run the full `python (goldenmatch)` lane + `benchmark_runner_smoke` (the Febrl3 smoke that first caught this). Confirm both go green.
3. **Re-bench (sanity, not a gate)** per the spec's success bar: dispatch `bench-fs-stages` at `ns=200000,500000,750000` on `large-new-64GB` with the multi-pass fix; record F1 parity + wall + RSS. Fold the numbers into the bucket-native-default spec's validation section (the prior 4.5-5.3x number was single-pass and is now superseded). Per "parity is enough," bucket stays the default as long as F1 parity holds and wall isn't dramatically worse.

---

## Notes for the implementer

- **DRY/YAGNI:** only `score_buckets` changes; do NOT touch the native kernel, polars-direct, `build_blocks`, or the planner rule. No new blocking strategies, no block-level cross-pass dedup.
- **The single most likely silent break:** rebuilding `frozen_exclude` / `native_exclude_handle` per pass, or adding an intra-loop `matched_pairs` skip. Don't. Freeze once, hoist, emit cross-pass duplicates -- that IS the polars algorithm and the source of guaranteed parity (spec "Implementation guard").
- **Peak-RSS invariant:** slim ONCE; `del keyed/bucketed` inside each pass so only one pass's buckets are resident at a time.
- **Fixtures are explicit on purpose** -- do not "simplify" them back to relying on `auto_configure_df`'s emergent config (that hides the multi-pass effect behind an unstable RED-collapse).
- **Skill:** follow @superpowers:test-driven-development for each task (failing test first).
