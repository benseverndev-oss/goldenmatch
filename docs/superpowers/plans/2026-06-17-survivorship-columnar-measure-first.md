# Columnar Survivorship Measure-First Bench Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a benchmark (`scripts/bench_survivorship_columnar.py`) + a CI workflow + a smoke test that measure the survivorship slow path's wall + RSS against the vectorized fast-path floor at scale, with a per-phase breakdown, then produce a measured GO/NO-GO verdict on a Phase-2 vectorized rewrite. NO production code changes.

**Architecture:** The bench synthesizes a `__cluster_id__`-tagged multi-member frame directly (skip blocking/dedupe), then measures two variants in separate subprocesses (clean peak RSS each, mirroring `bench_scorer_columnar.py`): (1) the SLOW path, reconstructed by calling the same functions `build_golden_records_batch`'s survivorship branch calls (`sort` -> `build_resolution_order` -> `partition_by` -> per-cluster `resolve_cluster`) with per-phase timers; (2) the FLOOR, a plain `most_complete` config routed through the vectorized `_build_golden_records_polars_native` path (asserted eligible). The verdict computes the vectorizable tax and applies the distributed-plan-style kill criterion.

**Tech Stack:** Python 3.11+, Polars, `argparse`/`subprocess`/`resource`/`statistics`/`time`, pytest (smoke only), GitHub Actions. Spec: `docs/superpowers/specs/2026-06-17-survivorship-columnar-measure-first-design.md`.

**Dependency:** Stacks on the merged/landing survivorship work (v1 + #1053 + #1055). Branch off `origin/main` once #1055 lands (the bench imports `resolve_cluster`, `build_resolution_order`, `GoldenGroupRule`, `_polars_native_eligible`). The bench only READS these; no survivorship-feature code is modified.

---

## Conventions for every task

- **Run tests / the smoke** (targeted local runs only; the SCALE bench runs in CI, never locally):
  `POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8 <repo>/.venv/Scripts/python.exe -m pytest <path> -v`
  (`GOLDENMATCH_NATIVE=0` if a stale native wheel interferes).
- A 1k-row bench run is fine locally; 1M/5M runs are CI-only (`large-new-64GB`).
- **Commit** after each green step. Squash-merge via PR at the end.
- **No em dashes / ASCII only** in committed strings.
- This workstream adds ONLY: one script, one workflow, one smoke test. It changes NO production code -> production behavior is trivially byte-identical.

---

## File Structure

**New files**
- `packages/python/goldenmatch/scripts/bench_survivorship_columnar.py` — the bench (workload synth, configs, per-phase slow timer, floor timer, verdict, table, argparse + subprocess child).
- `.github/workflows/bench-survivorship-columnar.yml` — `workflow_dispatch` on `large-new-64GB`.
- `packages/python/goldenmatch/tests/test_bench_survivorship_columnar_smoke.py` — 1k smoke (harness bit-rot guard, NOT a perf assertion).

**Modified files**: none (no production code touched).

---

## Task 0: Branch setup

- [ ] **Step 1:** Confirm #1055 merged; branch off fresh `origin/main`.
```bash
git fetch origin
git switch -c feat/survivorship-columnar-bench origin/main
# sanity: the imports the bench needs exist
grep -n "def resolve_cluster" packages/python/goldenmatch/goldenmatch/core/survivorship/resolve.py
grep -n "def _polars_native_eligible\|def _build_golden_records_polars_native\|def build_golden_records_batch" packages/python/goldenmatch/goldenmatch/core/golden.py
ls packages/python/goldenmatch/scripts/bench_scorer_columnar.py   # the model
```

---

## Task 1: clustered workload synthesizer

**Files:**
- Create: `scripts/bench_survivorship_columnar.py`
- Test: `tests/test_bench_survivorship_columnar_smoke.py`

- [ ] **Step 1: Write the failing test**
```python
# tests/test_bench_survivorship_columnar_smoke.py
import importlib.util
from pathlib import Path

_BENCH = Path(__file__).parent.parent / "scripts" / "bench_survivorship_columnar.py"


def _load():
    spec = importlib.util.spec_from_file_location("bench_surv_col", _BENCH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_make_clustered_workload_shape():
    mod = _load()
    df = mod.make_clustered_workload(rows=1000, avg_cluster_size=3, seed=7)
    # tagged multi-member frame
    assert "__cluster_id__" in df.columns and "__row_id__" in df.columns
    assert "__source__" in df.columns       # source metadata for source_priority
    assert {"first_name", "last_name", "street", "city", "state", "zip", "phone", "updated_at"} <= set(df.columns)
    assert df.height == 1000
    # multiple clusters, all multi-member (size >= 2), some nulls to give the resolver work
    n_clusters = df["__cluster_id__"].n_unique()
    assert 100 < n_clusters < 500          # ~333 clusters at avg size 3
    assert df["zip"].null_count() > 0       # nulls -> group-winner / fill has work
```

- [ ] **Step 2: Run to verify it fails** (module/function missing).

- [ ] **Step 3: Implement** `make_clustered_workload` in the bench (model the deterministic-seed style of `bench_scorer_columnar.py::make_workload`). Synthesize `rows` records partitioned into clusters of ~`avg_cluster_size`, each cluster a near-duplicate person/address with intra-cluster nulls/variation:
```python
from __future__ import annotations
import argparse, json, os, statistics, subprocess, sys, time
import polars as pl

_STATES = ["CA", "NY", "TX", "FL", "IL", "WA", "MA", "OH"]
_SOURCES = ["crm", "billing", "events"]


def make_clustered_workload(rows: int, avg_cluster_size: int = 3, seed: int = 7) -> pl.DataFrame:
    import random
    rnd = random.Random(seed)
    recs = []
    rid = 0
    cid = 0
    while len(recs) < rows:
        size = max(2, avg_cluster_size)        # all multi-member (survivorship only sees multi-member)
        cid += 1
        base_zip = f"{10000 + cid % 89999:05d}"
        for k in range(size):
            if len(recs) >= rows:
                break
            recs.append({
                "__cluster_id__": cid,
                "__row_id__": rid,
                "first_name": rnd.choice(["Jon", "John", "Jonathan"]),
                "last_name": f"Sev{cid % 997}",
                "street": None if k == 0 else f"{cid % 9999} Main St",   # winner-null on some cells
                "city": "Springfield" if k != 1 else None,
                "state": _STATES[cid % len(_STATES)],
                "zip": None if k == 1 else base_zip,
                "phone": None if k == 2 else f"212555{cid % 9999:04d}",
                "updated_at": f"2024-{1 + (k % 12):02d}-01",
                "__source__": _SOURCES[(cid + k) % len(_SOURCES)],   # metadata col resolve_cluster reads for source_priority
            })
            rid += 1
    return pl.DataFrame(recs[:rows])
```

- [ ] **Step 4: Run to verify pass.**
- [ ] **Step 5: Commit** `bench(survivorship): clustered workload synthesizer`.

## Task 2: configs + eligibility assert

**Files:** Modify the bench; Test: append to the smoke.

- [ ] **Step 1: Write the failing test**
```python
def test_configs_and_floor_eligibility():
    mod = _load()
    surv = mod.make_survivorship_config()
    floor = mod.make_floor_config()
    from goldenmatch.core.golden import _survivorship_active, _polars_native_eligible
    assert _survivorship_active(surv.golden_rules) is True
    assert _survivorship_active(floor.golden_rules) is False
    # the floor MUST land on the vectorized native path (else the tax is corrupted)
    assert _polars_native_eligible(floor.golden_rules, None) is True
    mod.assert_floor_eligible(floor)        # raises if not eligible
```

- [ ] **Step 2: Run to verify it fails.**
- [ ] **Step 3: Implement** `make_survivorship_config`, `make_floor_config`, `assert_floor_eligible`. The survivorship config is the realistic mixed one from the spec (a `mailing_address` field group + a conditional/validated `phone` rule). The floor is plain `most_complete`, empty `field_rules`. Return objects exposing `.golden_rules` (a `MatchConfig`-like holder, or just return the `GoldenRulesConfig` and adapt the test — match whatever `build_golden_records_batch` consumes; it takes `rules: GoldenRulesConfig`, so these helpers can return `GoldenRulesConfig` directly and the test reads them directly rather than via `.golden_rules`. Adjust the test accordingly during impl.):
```python
from goldenmatch.config.schemas import (
    GoldenRulesConfig, GoldenGroupRule, GoldenFieldRule,
)


def make_survivorship_config() -> GoldenRulesConfig:
    return GoldenRulesConfig(
        default_strategy="most_complete",
        field_groups=[GoldenGroupRule(name="mailing_address",
                                      columns=["street", "city", "state", "zip"],
                                      strategy="most_complete")],
        field_rules={"phone": [
            GoldenFieldRule(when="state in ['CA','NY']", strategy="most_recent",
                            date_column="updated_at", validate="nanp"),
            GoldenFieldRule(strategy="source_priority", source_priority=["crm", "billing"]),
        ]},
    )


def make_floor_config() -> GoldenRulesConfig:
    return GoldenRulesConfig(default_strategy="most_complete")   # no levers -> native eligible


def assert_floor_eligible(floor_rules) -> None:
    from goldenmatch.core.golden import _polars_native_eligible, _survivorship_active
    assert not _survivorship_active(floor_rules), "floor config must be non-survivorship"
    assert _polars_native_eligible(floor_rules, None), "floor config must hit the vectorized native path"
```
(NB: `make_*_config` return `GoldenRulesConfig`; update the Task-2 test to call `_survivorship_active(surv)` / `assert_floor_eligible(floor)` on the rules object directly. Also confirm at plan-execution time that `validate="nanp"` resolves in `goldenflow_filter`; if `nanp` is not wired in the bench env, swap to a validator that is, or drop `validate:` and note it in the report -- an unknown validator silently no-ops, understating the conditional path's work.)

- [ ] **Step 4: Run to verify pass.**
- [ ] **Step 5: Commit** `bench(survivorship): mixed + floor configs with native-eligibility guard`.

## Task 3: measurement core (slow per-phase + floor + parity)

**Files:** Modify the bench; Test: append to the smoke.

- [ ] **Step 1: Write the failing test**
```python
def test_measure_returns_phase_split_and_parity():
    mod = _load()
    df = mod.make_clustered_workload(rows=1000, avg_cluster_size=3, seed=7)
    slow = mod.run_slow(df, mod.make_survivorship_config(), runs=1)
    floor = mod.run_floor(df, mod.make_floor_config(), runs=1)
    # phase split keys present
    for k in ("total_wall_s", "sort_wall_s", "partition_wall_s", "loop_wall_s", "n_clusters", "rows_out"):
        assert k in slow
    assert "total_wall_s" in floor and "rows_out" in floor
    # one golden record per cluster on BOTH paths -> same row count (guards a broken workload)
    assert slow["rows_out"] == floor["rows_out"] == df["__cluster_id__"].n_unique()
```

- [ ] **Step 2: Run to verify it fails.**
- [ ] **Step 3: Implement** `run_slow` (reconstruct the survivorship branch's phases with timers, calling the SAME functions `build_golden_records_batch` calls) and `run_floor` (the native path), each returning median wall over `runs`:
```python
def run_slow(multi_df, rules, runs: int) -> dict:
    from goldenmatch.core.golden import _is_internal
    from goldenmatch.core.survivorship.conditions import build_resolution_order
    from goldenmatch.core.survivorship.resolve import resolve_cluster
    totals, sorts, parts, loops = [], [], [], []
    rows_out = 0
    for _ in range(runs):
        t0 = time.perf_counter()
        s_sorted = multi_df.sort("__cluster_id__")
        t_sort = time.perf_counter()
        user_cols = [c for c in s_sorted.columns if not _is_internal(c) and c != "__cluster_id__"]
        order = build_resolution_order(rules.field_rules, rules.field_groups, user_cols)
        partitions = s_sorted.partition_by("__cluster_id__", maintain_order=True)
        t_part = time.perf_counter()
        out = []
        for cdf in partitions:
            cid = cdf["__cluster_id__"][0]
            rec, _ = resolve_cluster(cdf, rules, order, cluster_id=int(cid))
            out.append(rec)
        t_loop = time.perf_counter()
        rows_out = len(out)
        totals.append(t_loop - t0); sorts.append(t_sort - t0)
        parts.append(t_part - t_sort); loops.append(t_loop - t_part)
    return {
        "total_wall_s": round(statistics.median(totals), 4),
        "sort_wall_s": round(statistics.median(sorts), 4),
        "partition_wall_s": round(statistics.median(parts), 4),
        "loop_wall_s": round(statistics.median(loops), 4),
        "n_clusters": multi_df["__cluster_id__"].n_unique(),
        "rows_out": rows_out,
    }


def run_floor(multi_df, floor_rules, runs: int) -> dict:
    from goldenmatch.core.golden import build_golden_records_batch
    assert_floor_eligible(floor_rules)
    walls = []
    rows_out = 0
    for _ in range(runs):
        t0 = time.perf_counter()
        out = build_golden_records_batch(multi_df, floor_rules)   # native vectorized path
        walls.append(time.perf_counter() - t0)
        rows_out = len(out)
    return {"total_wall_s": round(statistics.median(walls), 4), "rows_out": rows_out}
```
(Implementation notes for the executor: the slow reconstruction mirrors `core/golden.py`'s survivorship branch (`sort` -> `build_resolution_order` -> `partition_by(..., maintain_order=True)` -> `resolve_cluster(..., cluster_id=int(cid))`), `provenance=False` for the common case. `build_golden_records_batch` returns a list (one dict per cluster); `rows_out = len(out)`. Confirm `build_golden_records_batch(multi_df, floor_rules)` actually routes to `_build_golden_records_polars_native` for the floor config -- if the native gate lives in a wrapper rather than `build_golden_records_batch`, call that wrapper for the floor instead, keeping the `assert_floor_eligible` guard. The intra-loop split (materialize vs group_winner vs eval_predicate) is NOT computed here; it comes from a `py-spy` profile captured in the verdict report, per the spec.)

- [ ] **Step 4: Run to verify pass.**
- [ ] **Step 5: Commit** `bench(survivorship): slow per-phase + floor measurement + row-count parity`.

## Task 4: verdict + table + main (subprocess per variant)

**Files:** Modify the bench; Test: append to the smoke.

- [ ] **Step 1: Write the failing test**
```python
def test_main_runs_1k_and_emits_table(capsys):
    mod = _load()
    rc = mod.main(["--rows", "1000", "--runs", "1"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "survivorship-columnar" in out.lower()
    assert "tax" in out.lower() and "verdict" in out.lower()


def test_verdict_no_go_when_tax_small():
    mod = _load()
    # slow barely above floor -> tax < bar -> NO-GO
    v = mod.verdict(slow={"total_wall_s": 1.05, "sort_wall_s": 0.1, "partition_wall_s": 0.1,
                          "loop_wall_s": 0.85, "peak_rss_mb": 100},
                    floor={"total_wall_s": 1.0, "peak_rss_mb": 95})
    assert "NO-GO" in v


def test_verdict_go_when_vectorizable_tax_large():
    mod = _load()
    # slow 3x floor, dominated by partition+loop (vectorizable) -> GO
    v = mod.verdict(slow={"total_wall_s": 3.0, "sort_wall_s": 0.1, "partition_wall_s": 0.4,
                          "loop_wall_s": 2.5, "peak_rss_mb": 100},
                    floor={"total_wall_s": 1.0, "peak_rss_mb": 95})
    assert "GO" in v and "NO-GO" not in v
```

- [ ] **Step 2: Run to verify it fails.**
- [ ] **Step 3: Implement** `_peak_rss_mb` (copy from `bench_scorer_columnar.py`, Unix-only, returns None off-Unix), `verdict`, `_table`, and `main` with `argparse` + subprocess-per-variant for clean RSS (mirror `bench_scorer_columnar.py`'s `--child` pattern: parent spawns a child per variant per scale, child prints a JSON dict, parent aggregates). The `verdict`:
```python
def verdict(slow: dict, floor: dict) -> str:
    total = slow["total_wall_s"]
    tax = max(0.0, total - floor["total_wall_s"])
    # vectorizable phases: sort is shared/cheap; partition + loop-overhead + materialization +
    # group_winner are the rewrite-recoverable bucket. Coarse proxy = partition + loop walls.
    # (loop_wall includes the inherently-Python conditional eval; the report's py-spy split
    # refines this. For the coarse GO/NO-GO, treat partition+loop as the recoverable ceiling
    # and require it to dominate the tax.)
    recoverable = slow["partition_wall_s"] + slow["loop_wall_s"]
    frac = recoverable / total if total else 0.0
    rss_ok = (floor.get("peak_rss_mb") is None or slow.get("peak_rss_mb") is None
              or slow["peak_rss_mb"] <= floor["peak_rss_mb"] * 1.15)
    go = (tax / total >= 0.25 if total else False) and frac >= 0.25 and rss_ok
    return (f"VERDICT: {'GO' if go else 'NO-GO'} "
            f"(tax={tax:.3f}s {100*tax/total:.0f}% of slow, recoverable~{100*frac:.0f}%, "
            f"rss_ok={rss_ok}). "
            + ("Vectorizable cost dominates and clears the 25-30% bar -> pursue Phase-2 rewrite."
               if go else
               "Tax below bar or not localized to a vectorizable phase, or RSS regressed -> keep the slow path."))
```
(The `_table` prints the per-scale slow/floor/tax/per-phase/RSS rows. `main` parses `--rows "1000000,5000000"`, `--runs`, `--seed` (default 7), `--avg-cluster-size` (default 3), `--child`; for each scale, runs slow + floor variants (each in its own subprocess for clean peak RSS), prints the table + the verdict. Title line contains "survivorship-columnar". **Seed alignment:** the df is NOT pickled across the process boundary -- each child re-synthesizes the workload from `--rows`/`--seed`/`--avg-cluster-size` (mirroring `bench_scorer_columnar.py`), so the parent MUST thread the SAME `--seed`/`--avg-cluster-size` to both the slow-child and the floor-child, else they measure different frames and the tax is invalid.)

- [ ] **Step 4: Run to verify pass** (the 1k `main` smoke + the two synthetic-verdict unit tests).
- [ ] **Step 5: Commit** `bench(survivorship): verdict + table + main (subprocess per variant)`.

## Task 5: CI workflow

**Files:** Create `.github/workflows/bench-survivorship-columnar.yml`

- [ ] **Step 1: Implement** a `workflow_dispatch` workflow modeled on `.github/workflows/bench-scorer-columnar.yml` (the closest analog -- same script shape, runner, and setup): inputs `rows` (default `1000000,5000000`) and `runs` (default 3); `runs-on: large-new-64GB`; checkout, `setup-uv` + `uv sync --all-packages`; run `uv run python packages/python/goldenmatch/scripts/bench_survivorship_columnar.py --rows ${{ inputs.rows }} --runs ${{ inputs.runs }} | tee -a $GITHUB_STEP_SUMMARY`; `upload-artifact` the output. Set `env: { GOLDENMATCH_NATIVE: "0", POLARS_SKIP_CPU_CHECK: "1" }` (per the scorer-columnar workflow) to dodge stale-wheel / CPU-check footguns. (Read `bench-scorer-columnar.yml` for the exact runner label + setup steps and match them.)
- [ ] **Step 2: Commit** `ci(bench): survivorship columnar workflow_dispatch on large-new-64GB`.

## Task 6 (execution-time, NOT code): run at scale + write the verdict report

> This task runs AFTER the PR merges (or via `workflow_dispatch` on the branch). It is a measurement + writeup step, not a code change.

- [ ] **Step 1:** Trigger `bench-survivorship-columnar.yml` at `rows=1000000,5000000`, `runs=3` on `large-new-64GB`. Capture the table + verdict.
- [ ] **Step 2:** Capture an intra-`resolve_cluster` `py-spy` profile (materialize vs group_winner vs eval_predicate) from one scale run for the report appendix.
- [ ] **Step 3:** Write `docs/superpowers/reports/2026-06-XX-survivorship-columnar-verdict.md`: the 1M+5M wall/RSS/per-phase table, the recoverable computation, the py-spy split, and the GO/NO-GO. If NO-GO, record "keep the slow path" with the evidence so it is not re-litigated; if GO, name the follow-on Phase-2 rewrite spec.

---

## Open items carried from the spec (resolve during execution)

- **`validate="nanp"` reachability:** confirm at impl time `nanp` resolves in `goldenflow_filter` in the bench env; else swap/drop and note in the report (an unknown validator silently no-ops, understating the conditional cost).
- **Floor entry point:** confirm `build_golden_records_batch(multi_df, floor_rules)` reaches `_build_golden_records_polars_native`; if the native gate is in a wrapper, call that for the floor (keep `assert_floor_eligible`).
- **RSS off-Unix:** `_peak_rss_mb()` returns None off-Unix; the smoke asserts table keys only, never an RSS value; `verdict`'s `rss_ok` treats None as pass.
- **Coarse vs fine attribution:** the bench's `loop_wall` lumps materialization + group_winner + the per-cluster conditional eval; the report's py-spy appendix splits them. The coarse verdict treats partition+loop as the recoverable ceiling. This OVER-counts the truly-vectorizable portion (loop_wall includes the non-vectorizable conditional eval), which is **OPTIMISTIC for GO**: a coarse GO MUST be de-risked by the py-spy split before any Phase-2 commitment, while a coarse NO-GO is robust (even the inflated recoverable did not clear the bar). The methodology is conservative *overall* because the only automatic conclusion it can stand on alone is NO-GO.
