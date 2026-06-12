# Probabilistic → Splink Parity Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close GoldenMatch's recall-bound gap vs Splink on the probabilistic (Fellegi-Sunter) path by adding a diagnostic gate panel, multi-pass union blocking, and term-frequency weight adjustments — proven on a benchmark panel anchored on Splink's `historical_50k`.

**Architecture:** C→A. Stage 0 builds a benchmark/attribution harness (the success gate + the microscope). Stage 1 lifts recall by emitting multi-pass union blocking for the probabilistic auto-config (reusing the existing `_build_multi_pass_blocks`) and fixing a latent EM `blocking_fields` bug. Stage 2 sharpens discrimination with per-value term-frequency adjustments on the exact-agree level (slow + fast scoring paths). All new behavior is behind the existing `type: probabilistic` matchkey; weighted/exact defaults are untouched.

**Tech Stack:** Python 3.11+, Polars, NumPy, rapidfuzz, Pydantic v2; DuckDB + Splink as bench-only optional deps; pytest.

**Spec:** `docs/superpowers/specs/2026-06-08-probabilistic-splink-parity-design.md`

---

## Conventions for the implementing engineer

- **Run tests** (Windows, this repo): `.venv\Scripts\python.exe -m pytest <path> -v`. Do NOT run the full suite locally (xdist OOMs this box — see root CLAUDE.md). Run only the file(s) you touched.
- Set `POLARS_SKIP_CPU_CHECK=1` and `PYTHONIOENCODING=utf-8` in the shell before any python invocation (local Polars-import WMI hang + non-ASCII console).
- **Never `git add docs/superpowers/`** — it is gitignored. Commit only code + tests.
- Branch/merge SOP: feature branch, squash-merge via PR. Auth dance for `benseverndev-oss` (switch to `benzsevern`, switch back) only when pushing.
- Commit after every green step. Use ASCII-only commit messages (no em-dashes).
- `historical_50k`, NCVR, Leipzig datasets live under the gitignored `tests/benchmarks/datasets/`. Every test reading them MUST `pytest.skip` when the file/dep is absent.

## File Structure

**New (Stage 0 — all under the existing `scripts/bench_er_headtohead/`):**
- `datasets.py` — dataset loaders + adapters to the common `{record_id, …fields}` / truth `{record_id, cluster_id}` shape. Includes `historical_50k`.
- `attribution.py` — recall decomposition (`blocking_recall` / `threshold_loss` / `final_recall`) from candidate-pair, emitted-pair, and GT-pair sets.
- `run_panel.py` — orchestrator: run every dataset × {goldenmatch-probabilistic, splink}, call `evaluate.py` + `attribution.py`, emit markdown + JSON.

**New (Stage 0 — CI):**
- `.github/workflows/bench-probabilistic.yml` — `workflow_dispatch` only.

**Modified:**
- `packages/python/goldenmatch/pyproject.toml` — `[bench]` optional extra.
- `scripts/bench_er_headtohead/run_splink.py`, `run_goldenmatch.py` — accept a `--dataset` arg + `historical_50k`.
- `packages/python/goldenmatch/goldenmatch/core/pipeline.py` — Stage 1a: collect EM `blocking_fields` from `keys` AND `passes`.
- `.../goldenmatch/core/autoconfig.py` — Stage 1b: `_build_probabilistic_blocking`; Stage 2c: `tf_adjust` enable in `build_probabilistic_matchkeys`.
- `.../goldenmatch/core/probabilistic.py` — Stage 2: `EMResult.tf_tables`, TF table build in `train_em`, TF-aware `score_probabilistic` + `score_pair_probabilistic`, `TF_MIN_U`/`TF_MAX_U`.
- `.../goldenmatch/core/probabilistic_fast.py` — Stage 2b: TF freq table in the resolved spec + TF-aware top-level scoring.
- `.../goldenmatch/config/schemas.py` — Stage 2c: `MatchkeyField.tf_adjust: bool = False`.
- `.../goldenmatch/core/blocker.py` — Stage 1c: only if `_build_static_blocks` does not already skip oversized blocks under `skip_oversized`.

**New test modules:**
- `tests/bench/test_attribution.py`, `tests/bench/test_datasets_loader.py`
- `tests/test_probabilistic_union_blocking.py`
- `tests/test_probabilistic_tf.py`
- extend `tests/test_fast_path_probabilistic.py` (TF parity)

---

# STAGE 0 — Diagnostic gate panel (C)

> Goal of the stage: a runnable panel that reports, per dataset, GoldenMatch-vs-Splink {P,R,F1,B³} **and** the recall attribution split. The Stage-0 exit deliverable is a *measured* baseline that sets the gate values and confirms recall is blocking-dominated (or redirects the plan — see Kill criteria in the spec).

### Task 0.1: `[bench]` extra + `historical_50k` loader

**Files:**
- Modify: `packages/python/goldenmatch/pyproject.toml` (`[project.optional-dependencies]`, after line 58)
- Create: `scripts/bench_er_headtohead/datasets.py`
- Test: `tests/bench/test_datasets_loader.py`

- [ ] **Step 1: Add the `[bench]` extra**

In `pyproject.toml` under `[project.optional-dependencies]` add:
```toml
bench = [
    "splink>=4.0",
    "duckdb>=0.10",
    "pyarrow>=15",
]
```
(DuckDB is already used by `evaluate.py`; pin it here so the extra is self-contained.)

- [ ] **Step 2: Write the failing loader test**

```python
# tests/bench/test_datasets_loader.py
import importlib.util
from pathlib import Path
import pytest

# test file is at packages/python/goldenmatch/tests/bench/test_*.py
# parents: [bench, tests, goldenmatch, python, packages, <repo-root>] -> [5] = repo root
REPO = Path(__file__).resolve().parents[5]
SPEC = REPO / "scripts" / "bench_er_headtohead" / "datasets.py"

def _load():
    spec = importlib.util.spec_from_file_location("bench_datasets", SPEC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def test_load_historical_50k_shape_or_skip():
    mod = _load()
    try:
        records, truth = mod.load_dataset("historical_50k")
    except mod.DatasetUnavailable as e:
        pytest.skip(f"historical_50k unavailable: {e}")
    # records: polars DF with __row_id__-able id col; truth: {record_id, cluster_id}
    assert "record_id" in records.columns
    assert set(truth.columns) >= {"record_id", "cluster_id"}
    assert records.height > 1000
    # every truth.record_id exists in records
    rec_ids = set(records["record_id"].to_list())
    assert set(truth["record_id"].to_list()).issubset(rec_ids)
```

- [ ] **Step 3: Run it, verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/bench/test_datasets_loader.py -v`
Expected: FAIL (`datasets.py` missing / `load_dataset` undefined).

- [ ] **Step 4: Implement `datasets.py` with the `historical_50k` loader**

```python
#!/usr/bin/env python
"""Dataset loaders for the probabilistic accuracy panel.

Each loader returns (records, truth):
  records: polars.DataFrame with a 'record_id' column + matchable fields
  truth:   polars.DataFrame with columns {record_id, cluster_id}

historical_50k is Splink's home-turf biographical dataset (Wikidata historical
people, with a ground-truth cluster label). Loaded via splink_datasets when
splink is installed, else from a vendored parquet under the gitignored
tests/benchmarks/datasets/.
"""
from __future__ import annotations

from pathlib import Path

import polars as pl

REPO = Path(__file__).resolve().parents[2]
DATASETS_DIR = REPO / "packages" / "python" / "goldenmatch" / "tests" / "benchmarks" / "datasets"


class DatasetUnavailable(RuntimeError):
    """Raised when a dataset's data or its loader dependency is missing."""


def _historical_50k() -> tuple[pl.DataFrame, pl.DataFrame]:
    # Preferred: splink ships the parquet via splink_datasets.
    df = None
    try:
        from splink import splink_datasets  # type: ignore
        pdf = splink_datasets.historical_50k
        df = pl.from_pandas(pdf)
    except Exception:
        vendored = DATASETS_DIR / "historical_50k.parquet"
        if not vendored.exists():
            raise DatasetUnavailable(
                "install `goldenmatch[bench]` (for splink_datasets) or vendor "
                f"{vendored}"
            )
        df = pl.read_parquet(vendored)

    # historical_50k columns: unique_id, cluster, first_name, surname, dob,
    # birth_place, postcode_fake, occupation, ...
    df = df.rename({"unique_id": "record_id", "cluster": "cluster_id"})
    truth = df.select(["record_id", "cluster_id"])
    records = df.drop("cluster_id")
    return records, truth


_LOADERS = {
    "historical_50k": _historical_50k,
    # DBLP-ACM / Febrl3 / NCVR / synthetic adapters added in Task 0.2.
}


def load_dataset(name: str) -> tuple[pl.DataFrame, pl.DataFrame]:
    if name not in _LOADERS:
        raise KeyError(f"unknown dataset {name!r}; have {sorted(_LOADERS)}")
    return _LOADERS[name]()
```

- [ ] **Step 5: Run it, verify it passes (or skips cleanly)**

Run: `.venv\Scripts\python.exe -m pytest tests/bench/test_datasets_loader.py -v`
Expected: PASS or SKIP ("historical_50k unavailable") — both are green.

- [ ] **Step 6: Commit**

```bash
git add packages/python/goldenmatch/pyproject.toml scripts/bench_er_headtohead/datasets.py packages/python/goldenmatch/tests/bench/test_datasets_loader.py
git commit -m "bench: add [bench] extra + historical_50k loader for probabilistic panel"
```

---

### Task 0.2: Adapters for DBLP-ACM / Febrl3 / NCVR / synthetic

**Files:**
- Modify: `scripts/bench_er_headtohead/datasets.py`
- Test: `tests/bench/test_datasets_loader.py`

- [ ] **Step 1: Write failing tests** — parametrized over the four names; each asserts the `(records, truth)` contract or `pytest.skip(DatasetUnavailable)`.

```python
import pytest

@pytest.mark.parametrize("name", ["dblp_acm", "febrl3", "ncvr", "synthetic_person"])
def test_adapter_contract_or_skip(name):
    mod = _load()
    try:
        records, truth = mod.load_dataset(name)
    except mod.DatasetUnavailable as e:
        pytest.skip(f"{name} unavailable: {e}")
    assert "record_id" in records.columns
    assert set(truth.columns) >= {"record_id", "cluster_id"}
    assert set(truth["record_id"].to_list()).issubset(set(records["record_id"].to_list()))
```

- [ ] **Step 2: Run, verify fail** (`KeyError: unknown dataset 'dblp_acm'`).

- [ ] **Step 3: Implement the four adapters** in `datasets.py`. Reuse existing loaders where present:
  - `dblp_acm`, `febrl3`: wrap `packages/python/goldenmatch/tests/benchmarks/run_leipzig.py` / `recordlinkage.datasets.load_febrl3(return_links=True)`. Convert their pairwise/labels into a `cluster_id` (connected components over the GT links via `goldenmatch.core.cluster.UnionFind`).
  - `ncvr`: read the gitignored `tests/benchmarks/datasets/NCVR/ncvoter_sample_10k.txt` (tab-delimited; `pl.read_csv(separator="\t", encoding="utf8-lossy", ignore_errors=True)`); cluster_id = the voter id grouping per the existing NCVR convention.
  - `synthetic_person`: import `scripts/bench_er_headtohead/generate_fixture.py` and adapt its output (it already emits person rows + a cluster label).
  - Each raises `DatasetUnavailable` (not `FileNotFoundError`) when its file/dep is missing.

- [ ] **Step 4: Run, verify pass/skip.**

- [ ] **Step 5: Commit**

```bash
git commit -am "bench: dataset adapters (dblp_acm, febrl3, ncvr, synthetic_person) to common shape"
```

---

### Task 0.3: Recall attribution instrument

**Files:**
- Create: `scripts/bench_er_headtohead/attribution.py`
- Test: `tests/bench/test_attribution.py`

- [ ] **Step 1: Write the failing test (hand-built, known split)**

```python
# tests/bench/test_attribution.py
import importlib.util
from pathlib import Path

# parents: [bench, tests, goldenmatch, python, packages, <repo-root>] -> [5] = repo root
REPO = Path(__file__).resolve().parents[5]
SPEC = REPO / "scripts" / "bench_er_headtohead" / "attribution.py"

def _load():
    spec = importlib.util.spec_from_file_location("bench_attribution", SPEC)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod

def test_attribution_known_split():
    mod = _load()
    # 10 ground-truth matching pairs (ids 0..9 paired 0-1, 2-3, ... 18-19)
    gt = {(2*i, 2*i+1) for i in range(10)}
    # candidate generation surfaced 7 of them (3 never blocked together)
    candidates = {(2*i, 2*i+1) for i in range(7)} | {(0, 4), (1, 5)}  # +2 non-GT cands
    # scorer emitted 5 of the candidate GT pairs (2 scored but below threshold)
    emitted = {(2*i, 2*i+1) for i in range(5)} | {(0, 4)}             # +1 non-GT emit
    rep = mod.attribution(gt_pairs=gt, candidate_pairs=candidates, emitted_pairs=emitted)
    assert rep["n_gt_pairs"] == 10
    assert rep["blocking_recall"] == 0.7      # 7/10 GT pairs survived blocking
    assert rep["final_recall"] == 0.5         # 5/10 emitted
    assert round(rep["threshold_loss"], 4) == 0.2  # (7-5)/10 GT lost at scoring
```

- [ ] **Step 2: Run, verify fail.**

- [ ] **Step 3: Implement `attribution.py`**

```python
#!/usr/bin/env python
"""Recall attribution: localize where true pairs die (blocking vs threshold).

All inputs are sets of canonical (min,max) record-id pairs:
  gt_pairs        ground-truth matching pairs
  candidate_pairs pairs that survived candidate generation (blocking)
  emitted_pairs   pairs the scorer emitted above threshold

  blocking_recall = |gt & candidates| / |gt|     (the ceiling)
  final_recall    = |gt & emitted|    / |gt|
  threshold_loss  = (|gt & candidates| - |gt & emitted|) / |gt|
"""
from __future__ import annotations


def _canon(pairs):
    return {(min(a, b), max(a, b)) for a, b in pairs}


def attribution(gt_pairs, candidate_pairs, emitted_pairs) -> dict:
    gt = _canon(gt_pairs)
    cand = _canon(candidate_pairs)
    emit = _canon(emitted_pairs)
    n = len(gt)
    if n == 0:
        return {"n_gt_pairs": 0, "blocking_recall": 0.0,
                "final_recall": 0.0, "threshold_loss": 0.0}
    blocked = len(gt & cand)
    emitted_gt = len(gt & emit)
    return {
        "n_gt_pairs": n,
        "blocking_recall": round(blocked / n, 4),
        "final_recall": round(emitted_gt / n, 4),
        "threshold_loss": round((blocked - emitted_gt) / n, 4),
    }


def truth_to_pairs(truth) -> set:
    """Expand a {record_id, cluster_id} frame into within-cluster GT pairs."""
    from itertools import combinations
    import polars as pl
    pairs = set()
    for _cid, grp in truth.group_by("cluster_id"):
        ids = grp["record_id"].to_list()
        if len(ids) > 1:
            pairs.update((min(a, b), max(a, b)) for a, b in combinations(ids, 2))
    return pairs
```

- [ ] **Step 4: Run, verify pass.**

- [ ] **Step 5: Commit**

```bash
git commit -am "bench: recall attribution instrument (blocking vs threshold split)"
```

---

### Task 0.4: Extend Splink + GoldenMatch runners for `--dataset`

**Files:**
- Modify: `scripts/bench_er_headtohead/run_splink.py`, `scripts/bench_er_headtohead/run_goldenmatch.py`

- [ ] **Step 1:** Add a `--dataset NAME` arg to both runners. When set, load via `datasets.load_dataset(name)` instead of reading the synthetic fixture parquet. Keep the existing fixture path working (backward compatible) when `--dataset` is absent.

- [ ] **Step 2:** `run_goldenmatch.py` — for the panel, build the config via `auto_configure_probabilistic_df(records)` (the probabilistic path) and run `dedupe_df(records, config=config)`. Emit predictions parquet `{record_id, pred_cluster_id}` in the shape `evaluate.py` consumes. Also emit `candidate_pairs.parquet` and `emitted_pairs.parquet` for attribution (see Task 0.5 for how these are captured — `run_dedupe` exposes clusters; emitted pairs come from cluster expansion, candidate pairs from the blocker). If candidate-pair capture needs a hook, add an opt-in `GOLDENMATCH_BENCH_DUMP_PAIRS=<dir>` env that the probabilistic pipeline branch honors (guard with `if os.environ.get(...)`, zero cost otherwise).

- [ ] **Step 3:** `run_splink.py` — add a `historical_50k` code path using Splink's documented settings for that dataset (the runner already trains EM; parametrize the comparison columns by dataset). Emit predictions in the same `{record_id, pred_cluster_id}` shape.

- [ ] **Step 4:** Manual smoke (not a unit test — these need datasets/splink):
```bash
.venv\Scripts\python.exe scripts/bench_er_headtohead/run_goldenmatch.py --dataset historical_50k --out .profile_tmp/gm_hist.parquet
.venv\Scripts\python.exe scripts/bench_er_headtohead/run_splink.py --dataset historical_50k --out .profile_tmp/splink_hist.parquet
```
Expected: both write a predictions parquet (or skip with a clear "dataset unavailable").

- [ ] **Step 5: Commit**

```bash
git commit -am "bench: --dataset arg + historical_50k path for both runners"
```

---

### Task 0.5: Panel orchestrator + CI workflow

**Files:**
- Create: `scripts/bench_er_headtohead/run_panel.py`
- Create: `.github/workflows/bench-probabilistic.yml`

- [ ] **Step 1:** Implement `run_panel.py`: for each dataset in `[historical_50k, dblp_acm, febrl3, ncvr, synthetic_person]` × engine in `[goldenmatch, splink]`, invoke the runner, then `evaluate.py::evaluate(pred, truth)` and (for GoldenMatch) `attribution.attribution(...)`. Collect into one JSON + a markdown table with columns: dataset, engine, P, R, F1, B³-F1, blocking_recall, threshold_loss. Datasets/engines that raise `DatasetUnavailable` are recorded as `skipped`, never fatal.

- [ ] **Step 2:** Implement `.github/workflows/bench-probabilistic.yml` as `workflow_dispatch` only, `runs-on: large-new-64GB` (per `feedback_bench_default_runner`), installs `goldenmatch[bench]`, runs `run_panel.py`, uploads the markdown + JSON as artifacts. Do NOT gate any other workflow on it yet (no `needs:`).

- [ ] **Step 3:** Commit

```bash
git add scripts/bench_er_headtohead/run_panel.py .github/workflows/bench-probabilistic.yml
git commit -m "bench: probabilistic panel orchestrator + workflow_dispatch CI"
```

---

### Task 0.6: Run the baseline + set the gate (STAGE-0 EXIT GATE)

> This is the C deliverable. No code; it produces the numbers that drive Stages 1–2.

- [ ] **Step 1:** Trigger `bench-probabilistic.yml` (`gh workflow run bench-probabilistic.yml --ref <branch>`), or run `run_panel.py` locally on whatever datasets are available.
- [ ] **Step 2:** Read the attribution row for `historical_50k` and `dblp_acm`. Record `blocking_recall` vs `final_recall`.
- [ ] **Step 3: DECISION GATE.**
  - If `blocking_recall` is well below Splink's recall AND `threshold_loss` is small → **recall is blocking-dominated → proceed to Stage 1** as planned.
  - If `threshold_loss` ≫ the blocking-ceiling gap → **re-sequence: do Stage 2 (TF) first.** Note this in the plan and swap the stage order. (Spec Kill criteria.)
- [ ] **Step 4:** Write the concrete gate values into the plan/PR description: e.g. `historical_50k F1 >= <splink_f1> - 0.02`; `dblp_acm recall >= <X>` with `precision >= 0.95`; Febrl3/NCVR/synthetic non-regression floors = their measured baselines.
- [ ] **Step 5:** Commit the baseline JSON/markdown into the PR (artifact or `.profile_tmp/`, which is gitignored — paste the table into the PR body instead of committing data).

---

# STAGE 1 — Multi-pass union blocking (A1, recall lever)

> Reuses the existing `_build_multi_pass_blocks`. No new module, no new config field.

### Task 1.1: Fix the latent EM `blocking_fields` bug (CORRECTNESS — do first)

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/pipeline.py:1337-1340`
- Test: `tests/test_probabilistic_union_blocking.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_probabilistic_union_blocking.py
import polars as pl
from goldenmatch.config.schemas import (
    BlockingConfig, BlockingKeyConfig, GoldenMatchConfig,
    MatchkeyConfig, MatchkeyField,
)

def _collect_blocking_fields(config):
    # Mirror the production helper extracted in Step 3.
    from goldenmatch.core.pipeline import _collect_blocking_fields
    return _collect_blocking_fields(config.blocking)

def test_blocking_fields_include_passes():
    blocking = BlockingConfig(
        strategy="multi_pass",
        passes=[
            BlockingKeyConfig(fields=["first_name", "birth_year"]),
            BlockingKeyConfig(fields=["surname"]),
        ],
    )
    cfg = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(name="p", type="probabilistic",
                                  fields=[MatchkeyField(field="first_name", scorer="jaro_winkler")])],
        blocking=blocking,
    )
    fields = _collect_blocking_fields(cfg)
    assert set(fields) == {"first_name", "birth_year", "surname"}

def test_blocking_fields_include_keys_only_still_works():
    blocking = BlockingConfig(keys=[BlockingKeyConfig(fields=["zip"])])
    cfg = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(name="p", type="probabilistic",
                                  fields=[MatchkeyField(field="zip", scorer="exact")])],
        blocking=blocking,
    )
    assert set(_collect_blocking_fields(cfg)) == {"zip"}
```

- [ ] **Step 2: Run, verify fail** (`ImportError: cannot import name '_collect_blocking_fields'`).

- [ ] **Step 3: Implement.** Extract a helper near the probabilistic branch in `pipeline.py` and use it at the call site:

```python
def _collect_blocking_fields(blocking) -> list[str]:
    """Fields that are agree-by-construction within their block, so EM must
    treat them as blocking fields (neutral priors). Under strategy='multi_pass'
    the fields live in `passes`, not `keys` -- collect the union of both so EM
    does not try to learn m/u for always-agree fields (which collapses the
    weights). This is a conservative over-approximation under union blocking
    (a field anchoring one pass is not agree in pairs from another pass); see
    the spec's Stage 1d. Conservative = never wrong merges.
    """
    if blocking is None:
        return []
    fields: list[str] = []
    for kc in (blocking.keys or []):
        fields.extend(kc.fields)
    for pc in (blocking.passes or []):
        fields.extend(pc.fields)
    # de-dup, preserve order
    seen: set[str] = set()
    out: list[str] = []
    for f in fields:
        if f not in seen:
            seen.add(f); out.append(f)
    return out
```

Replace the inline collection at `pipeline.py:1337-1340`:
```python
            blocking_fields = _collect_blocking_fields(config.blocking)
```

- [ ] **Step 4: Run, verify pass.**

Run: `.venv\Scripts\python.exe -m pytest tests/test_probabilistic_union_blocking.py -v`

- [ ] **Step 5: Commit**

```bash
git commit -am "fix(probabilistic): EM blocking_fields must include multi_pass `passes`, not just `keys`"
```

---

### Task 1.2: Probabilistic auto-config emits a capped multi-pass config

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/autoconfig.py` (`_build_probabilistic_blocking` + use in `auto_configure_probabilistic_df`)
- Test: `tests/test_probabilistic_union_blocking.py`

- [ ] **Step 1: Write the failing test**

```python
def test_build_probabilistic_blocking_emits_capped_multipass():
    from goldenmatch.core.autoconfig import _build_probabilistic_blocking, profile_columns
    import polars as pl
    df = pl.DataFrame({
        "first_name": ["ann", "ann", "bob", "bob", "cara", "cara"] * 50,
        "surname":    ["lee", "lee", "kim", "kim", "ng", "ng"] * 50,
        "birth_year": ["1990", "1990", "1985", "1985", "1972", "1972"] * 50,
        "postcode":   ["AA1", "AA1", "BB2", "BB2", "CC3", "CC3"] * 50,
    })
    profiles = profile_columns(df)
    blocking = _build_probabilistic_blocking(profiles, df)
    assert blocking.strategy == "multi_pass"
    assert blocking.passes is not None
    assert 1 <= len(blocking.passes) <= 4      # capped
    assert blocking.skip_oversized is True
```

- [ ] **Step 2: Run, verify fail.**

- [ ] **Step 3: Implement `_build_probabilistic_blocking`** in `autoconfig.py`:

> **Reviewer-fix:** `ColumnProfile` has NO `identity_score` attribute (it has
> `name, dtype, col_type, confidence, sample_values, null_rate, cardinality_ratio,
> avg_len`). `identity_score` lives on `ColumnPrior`, produced by
> `compute_column_priors(df) -> dict[str, ColumnPrior]` in `core/indicators.py`.
> Rank using that map — do NOT `getattr(p, "identity_score", 0.0)` (silent no-op).

```python
def _build_probabilistic_blocking(
    profiles: list[ColumnProfile],
    df: pl.DataFrame,
    max_passes: int = 4,
) -> BlockingConfig:
    """Derive a Splink-style multi-pass union blocking config for the
    probabilistic path. Each pass is a conjunction of identity-ish fields;
    union over passes lifts blocking recall (the F-S recall lever). Capped at
    `max_passes` to bound the pair budget; oversized blocks are skipped.

    Reuses core/blocker.py::_build_multi_pass_blocks via strategy='multi_pass'.
    """
    from goldenmatch.core.indicators import compute_column_priors

    # identity_score per column (0..~0.95) -- the real ranking signal, from
    # ColumnPrior. Columns absent from the map default to 0.0.
    priors = compute_column_priors(df)
    def _identity(name: str) -> float:
        p = priors.get(name)
        return p.identity_score if p is not None else 0.0

    # Rank identity-ish columns by identity_score (desc), require moderate
    # cardinality (not perfectly unique, not near-constant), drop high-null.
    def _eligible(p: ColumnProfile) -> bool:
        null_rate = df[p.name].null_count() / df.height if df.height else 1.0
        return (
            p.col_type not in ("numeric", "date", "description")
            and 0.01 <= p.cardinality_ratio < 1.0
            and null_rate <= 0.20
        )

    ranked = sorted(
        (p for p in profiles if _eligible(p)),
        key=lambda p: (_identity(p.name), p.cardinality_ratio),
        reverse=True,
    )
    passes: list[BlockingKeyConfig] = []
    # Single-key passes on the strongest identity columns.
    for p in ranked[:2]:
        passes.append(BlockingKeyConfig(fields=[p.name]))
    # 2-field conjunctions of the next strongest pairs (orthogonal coverage).
    for i in range(0, min(len(ranked) - 1, 4), 2):
        if len(passes) >= max_passes:
            break
        passes.append(BlockingKeyConfig(fields=[ranked[i].name, ranked[i + 1].name]))
    passes = passes[:max_passes]
    if not passes:
        # Fall back to the standard single-strategy blocker.
        return build_blocking(profiles, df)

    # Row-count-aware oversized cap (the #715 lesson: block size scales with N).
    n = df.height
    max_safe_block = max(1000, min(10_000, n // 200)) if n else 5000
    return BlockingConfig(
        strategy="multi_pass",
        passes=passes,
        max_block_size=max_safe_block,
        skip_oversized=True,
    )
```

- [ ] **Step 4:** Wire it into `auto_configure_probabilistic_df` (replace the `build_blocking(...)` call at autoconfig.py:2837):
```python
    blocking = _build_probabilistic_blocking(profiles, df)
```
Note: this deliberately drops the `llm_provider=llm_provider` arg the old `build_blocking` call passed. The probabilistic path needs no LLM blocking suggestions; the multi-pass derivation is purely profile-driven. Intentional, not a regression. (The `_build_probabilistic_blocking` internal fallback still calls `build_blocking(profiles, df)` without the provider, which is fine.)

- [ ] **Step 5: Run, verify pass.**

- [ ] **Step 6: End-to-end smoke test** (no Splink needed):

```python
def test_probabilistic_dedupe_with_multipass_runs():
    import polars as pl
    from goldenmatch import dedupe_df
    from goldenmatch.core.autoconfig import auto_configure_probabilistic_df
    df = pl.DataFrame({
        "first_name": ["ann","an","bob","bobby","cara","cara"],
        "surname":    ["lee","lee","kim","kim","ng","ng"],
        "birth_year": ["1990","1990","1985","1985","1972","1972"],
    })
    cfg = auto_configure_probabilistic_df(df)
    res = dedupe_df(df, config=cfg)
    assert res is not None  # runs end-to-end, multi-pass + F-S, no collapse
```

- [ ] **Step 7: Commit**

```bash
git commit -am "feat(probabilistic): auto-config emits capped multi-pass union blocking (recall lever)"
```

---

### Task 1.3: Per-pass oversized guard test

**Files:**
- Test: `tests/test_probabilistic_union_blocking.py`
- Modify (only if needed): `packages/python/goldenmatch/goldenmatch/core/blocker.py`

- [ ] **Step 1: Write the guard test** — a dataset where one pass key produces a giant block and another produces clean small blocks; assert the **invariant** (no surviving block exceeds `max_block_size`) and that the clean pass still contributes. Do NOT assert the mechanism: `_build_static_blocks` under `skip_oversized=True` may *auto-split* the giant block (on its highest-cardinality column) rather than skip it — both outcomes satisfy the invariant we care about (bounded block sizes, recall preserved).

```python
def test_multipass_bounds_oversized_block_keeps_clean_passes():
    import polars as pl
    from goldenmatch.config.schemas import BlockingConfig, BlockingKeyConfig
    from goldenmatch.core.blocker import build_blocks
    n = 400
    df = pl.DataFrame({
        "__row_id__": list(range(n)),
        "country": ["X"] * n,                       # one giant block (skipped OR auto-split)
        "pid": [f"p{i//2}" for i in range(n)],      # clean 2-record blocks
    }).lazy()
    cfg = BlockingConfig(strategy="multi_pass", skip_oversized=True,
                         max_block_size=50,
                         passes=[BlockingKeyConfig(fields=["country"]),
                                 BlockingKeyConfig(fields=["pid"])])
    blocks = build_blocks(df, cfg)
    sizes = [b.df.collect().height if hasattr(b.df, "collect") else b.df.height for b in blocks]
    assert not sizes or max(sizes) <= 50    # INVARIANT: no oversized block survives
    assert len(blocks) >= 100               # the clean pid pass still contributes
```

- [ ] **Step 2: Run, verify behavior.** If a surviving block exceeds `max_block_size` (i.e. `_build_static_blocks` neither skips nor splits under `skip_oversized`), fix `_build_static_blocks` (`blocker.py:~135`, oversized handling ~261-269) to skip blocks above `max_block_size` when `skip_oversized` is True. If the invariant already holds (skip or auto-split), no `blocker.py` change is needed — this task is test-only.

- [ ] **Step 3: Commit**

```bash
git commit -am "test(probabilistic): multi-pass skips oversized blocks, keeps clean passes"
```

---

### Task 1.4: Measure Stage 1 on the panel (no code)

- [ ] Re-run `run_panel.py` on `historical_50k` + `dblp_acm`. Confirm `blocking_recall` rose vs the Stage-0 baseline and `final_recall`/F1 followed. Paste the before/after table into the PR. If the ceiling rose but F1 did not, that's the Stage 2 signal (do not add more passes).

---

# STAGE 2 — Term-frequency adjustments (A2, discrimination lever)

### Task 2.1: `MatchkeyField.tf_adjust` schema field

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/config/schemas.py:73-96`
- Test: `tests/test_probabilistic_tf.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_probabilistic_tf.py
from goldenmatch.config.schemas import MatchkeyField, MatchkeyConfig

def test_tf_adjust_defaults_false_and_roundtrips():
    f = MatchkeyField(field="surname", scorer="exact")
    assert f.tf_adjust is False
    f2 = MatchkeyField(field="surname", scorer="exact", tf_adjust=True)
    assert f2.tf_adjust is True
    # exclude_none must NOT drop a True flag; default False stays out of YAML when excluded
    mk = MatchkeyConfig(name="p", type="probabilistic", fields=[f2])
    assert mk.fields[0].tf_adjust is True
```

- [ ] **Step 2: Run, verify fail.**

- [ ] **Step 3: Implement** — add to `MatchkeyField` (after `partial_threshold`, line 83):
```python
    # Probabilistic-only (Splink term-frequency): when True, the exact-agree
    # (top) level weight is adjusted per shared value -- rare shared values
    # score higher. Default False = today's level-only behavior. See
    # core/probabilistic.py TF section.
    tf_adjust: bool = False
```

- [ ] **Step 4: Run, verify pass. Step 5: Commit**

```bash
git commit -am "feat(probabilistic): add MatchkeyField.tf_adjust schema flag"
```

---

### Task 2.2: `EMResult.tf_tables` + TF table build in `train_em`

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/probabilistic.py` (`EMResult` dataclass lines 31-40; `train_em` lines 217-399; new module constants)
- Test: `tests/test_probabilistic_tf.py`

- [ ] **Step 1: Write failing test**

```python
import polars as pl
from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
from goldenmatch.core.probabilistic import train_em

def test_train_em_builds_tf_table_for_tf_fields_only():
    df = pl.DataFrame({
        "__row_id__": list(range(8)),
        "surname": ["smith","smith","smith","smith","zato","zato","ng","lee"],
        "city": ["x"]*8,
    })
    mk = MatchkeyConfig(name="p", type="probabilistic", fields=[
        MatchkeyField(field="surname", scorer="exact", levels=2, tf_adjust=True),
        MatchkeyField(field="city", scorer="exact", levels=2, tf_adjust=False),
    ])
    em = train_em(df, mk, n_sample_pairs=50)
    assert em.tf_tables is not None
    assert "surname" in em.tf_tables and "city" not in em.tf_tables
    # relative frequencies sum ~1, smith most common
    t = em.tf_tables["surname"]
    assert abs(sum(t.values()) - 1.0) < 1e-6
    assert t["smith"] > t["zato"]
```

- [ ] **Step 2: Run, verify fail** (`EMResult has no field 'tf_tables'`).

- [ ] **Step 3: Implement.**
  - Add module constants near the top of `probabilistic.py`:
    ```python
    import os
    TF_MIN_U = float(os.environ.get("GOLDENMATCH_TF_MIN_U", "1e-6"))
    TF_MAX_U = float(os.environ.get("GOLDENMATCH_TF_MAX_U", "0.5"))
    ```
  - Add `tf_tables: dict[str, dict[str, float]] | None = None` to `EMResult` (default None — backward compatible with all existing constructors, incl. `_fallback_result`).
  - In `train_em`, after computing `u_probs` (after line ~284), build the TF tables from the **transformed** field values over the full `df` (use the same transforms the scorer sees):
    ```python
    from goldenmatch.utils.transforms import apply_transforms
    tf_tables: dict[str, dict[str, float]] = {}
    for f in mk.fields:
        if not getattr(f, "tf_adjust", False):
            continue
        vals = df[f.field].to_list()
        if f.transforms:
            vals = [apply_transforms(str(v), f.transforms) if v is not None else None for v in vals]
        counts: dict[str, int] = {}
        total = 0
        for v in vals:
            if v is None:
                continue
            key = str(v)
            counts[key] = counts.get(key, 0) + 1
            total += 1
        if total == 0:
            continue
        tf_tables[f.field] = {k: c / total for k, c in counts.items()}
    ```
  - Pass `tf_tables=(tf_tables or None)` into the `EMResult(...)` return (line ~392).

> **Fast/slow parity requirement (read before Task 2.4):** the TF-table keys built
> here MUST match the encoding the fast path reads from the precomputed
> `__xform_<sig>__` column. The slow path keys on `apply_transforms(str(v), f.transforms)`;
> the fast path will key on the xform-column value. If the native precompute and the
> manual `apply_transforms` loop ever diverge on edge cases (None handling, numeric→str
> coercion), the fast-vs-slow parity test in Task 2.4 will fail. Keep both keyed on the
> exact same transformed-string form; the parity test is the guard.

- [ ] **Step 4: Run, verify pass. Step 5: Commit**

```bash
git commit -am "feat(probabilistic): EMResult.tf_tables built in train_em for tf_adjust fields"
```

---

### Task 2.3: TF-aware scoring (slow path)

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/probabilistic.py` (`score_probabilistic` 669-728; `score_pair_probabilistic` 731-750; new helper `_tf_adjusted_weight`)
- Test: `tests/test_probabilistic_tf.py`

- [ ] **Step 1: Write failing tests (monotonicity, clamp, no-op)**

```python
import math
from goldenmatch.core.probabilistic import _tf_adjusted_weight, TF_MIN_U, TF_MAX_U

def test_tf_weight_monotonic_rarer_is_larger():
    # base: m_exact=0.8, u_exact=0.1; field has 4 distinct values
    common = _tf_adjusted_weight(m_exact=0.8, u_exact=0.1, freq_v=0.7, n_distinct=4)
    rare   = _tf_adjusted_weight(m_exact=0.8, u_exact=0.1, freq_v=0.02, n_distinct=4)
    base   = _tf_adjusted_weight(m_exact=0.8, u_exact=0.1, freq_v=0.25, n_distinct=4)  # avg freq
    assert rare > base > common
    assert abs(base - math.log2(0.8 / 0.1)) < 1e-9   # avg-freq value == base weight

def test_tf_weight_clamped():
    w = _tf_adjusted_weight(m_exact=0.9, u_exact=0.1, freq_v=1e-12, n_distinct=10**6)
    # u_v floored at TF_MIN_U -> weight bounded
    assert w <= math.log2(0.9 / TF_MIN_U) + 1e-9
```

Then an end-to-end no-op test: `tf_adjust=False` everywhere ⇒ `score_probabilistic` output is byte-identical to before (compare against a fixed expected list on a tiny block).

- [ ] **Step 2: Run, verify fail.**

- [ ] **Step 3: Implement the helper + thread it through scoring.**

```python
def _tf_adjusted_weight(m_exact: float, u_exact: float, freq_v: float, n_distinct: int) -> float:
    """Splink scale-the-base TF adjustment for the exact-agree level.

      freq_avg = 1 / n_distinct
      u_v      = u_exact * freq_v / freq_avg = u_exact * freq_v * n_distinct
      u_v      = clamp(u_v, TF_MIN_U, TF_MAX_U)
      weight   = log2(m_exact / u_v)

    Rarer value -> smaller u_v -> larger weight. An average-frequency value
    (freq_v == 1/n_distinct) returns exactly log2(m_exact/u_exact) (the base).
    """
    freq_avg = 1.0 / max(n_distinct, 1)
    u_v = u_exact * (freq_v / freq_avg) if freq_avg > 0 else u_exact
    u_v = min(max(u_v, TF_MIN_U), TF_MAX_U)
    return math.log2(max(m_exact, 1e-10) / u_v)
```

In `score_probabilistic`, the per-field accumulation (lines 714-717) becomes TF-aware. The top (exact-agree) level index is `f.levels - 1`. When the field is `tf_adjust` and the pair hit the top level, recompute that field's contribution using the shared transformed value's frequency:

```python
            total_weight = 0.0
            for k, f in enumerate(mk.fields):
                level = vec[k]
                top = f.levels - 1
                if (em_result.tf_tables and getattr(f, "tf_adjust", False)
                        and f.field in em_result.tf_tables and level == top):
                    # shared value at exact-agree: use a's transformed value
                    raw = row_a.get(f.field)
                    val = str(raw) if raw is not None else None
                    if val is not None and f.transforms:
                        from goldenmatch.utils.transforms import apply_transforms
                        val = apply_transforms(val, f.transforms)
                    tft = em_result.tf_tables[f.field]
                    freq_v = tft.get(val, 1.0 / max(len(tft), 1)) if val is not None else None
                    if freq_v is not None:
                        m_exact = max(em_result.m_probs[f.field][top], 1e-10)
                        u_exact = max(em_result.u_probs[f.field][top], 1e-10)
                        total_weight += _tf_adjusted_weight(m_exact, u_exact, freq_v, len(tft))
                        continue
                total_weight += em_result.match_weights[f.field][level]
```

**Normalization caveat:** `max_weight`/`min_weight` (lines 692-694) are computed from `match_weights`. A TF-boosted weight can exceed the field's stored `max`. To keep `normalized` in [0,1], clamp `normalized = min(1.0, max(0.0, normalized))` after the division (lines 720-723), and add the same clamp in `score_pair_probabilistic`. Document this: TF can push a field above its level-average max; clamping preserves the [0,1] contract without re-deriving the range per pair.

Apply the identical TF block + clamp to `score_pair_probabilistic`.

- [ ] **Step 4: Run, verify pass** (monotonic, clamp, byte-identical no-op).

- [ ] **Step 5: Commit**

```bash
git commit -am "feat(probabilistic): TF-adjusted exact-level scoring (slow path) + [0,1] clamp"
```

---

### Task 2.4: TF on the fast path + parity

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/probabilistic_fast.py`
- Test: extend `tests/test_fast_path_probabilistic.py`

- [ ] **Step 1: Write failing parity test** — build a block with a `tf_adjust=True` field, train EM, score via both `score_probabilistic` (slow) and the fast path; assert identical emitted pairs + scores (within 1e-6).

- [ ] **Step 2: Run, verify fail** (fast path ignores TF → scores diverge).

- [ ] **Step 3: Implement.**
  - Extend `ProbFieldSpec` to carry `(xform_col, fn, levels, partial_threshold, weights, tf_table_or_None, m_top, u_top)`.
  - In `_resolve_probabilistic_fast_path`: for each field, if `f.tf_adjust` and `em_result.tf_tables` has it, attach the frequency dict + `m_probs[top]`/`u_probs[top]`; else `None`. (Keep the gate otherwise unchanged — TF does not disqualify the fast path.)
  - In `score_probabilistic_fast`: when a field is TF-enabled and the pair maps to the top level, compute `freq_v` from the field's xform array value (already materialized) and use `_tf_adjusted_weight(...)` instead of `weights_list[k][top]`. Apply the same `normalized = min(1.0, max(0.0, normalized))` clamp.
  - Import `_tf_adjusted_weight` from `probabilistic.py`.

- [ ] **Step 4: Run, verify pass** (fast == slow with TF on; and the existing TF-off parity tests still pass).

- [ ] **Step 5: Commit**

```bash
git commit -am "feat(probabilistic): TF adjustment on the fast scoring path (slow/fast parity preserved)"
```

---

### Task 2.5: Auto-config enables `tf_adjust` for skewed identity fields

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/autoconfig.py` (`build_probabilistic_matchkeys` lines 2786-2792)
- Test: `tests/test_probabilistic_tf.py`

- [ ] **Step 1: Write failing test** — a profile set with a high-skew name column and a low-skew code column; assert the built probabilistic matchkey has `tf_adjust=True` on the name field, `False` on the code field.

```python
def test_autoconfig_enables_tf_on_skewed_identity_field():
    import polars as pl
    from goldenmatch.core.autoconfig import build_probabilistic_matchkeys, profile_columns
    df = pl.DataFrame({
        "surname": (["smith"]*60 + ["zato","ng","lee","kim","ohara"]*8),  # Zipfian
        "member_code": [f"C{i}" for i in range(100)],                      # near-unique, flat
    })
    profiles = profile_columns(df)
    mks = build_probabilistic_matchkeys(profiles)
    by_field = {f.field: f for f in mks[0].fields}
    assert by_field["surname"].tf_adjust is True
    # near-unique surrogate is excluded entirely (card>=1.0) OR tf_adjust False
    assert "member_code" not in by_field or by_field["member_code"].tf_adjust is False
```

- [ ] **Step 2: Run, verify fail.**

- [ ] **Step 3: Implement** a skew check + enable. Add a helper and set the flag when building the field (autoconfig.py ~2786):

```python
def _is_skewed_identity(p: ColumnProfile, df: pl.DataFrame) -> bool:
    """TF helps when exact-agreements vary a lot in informativeness, i.e. the
    value frequency distribution is skewed (a few common values + a long tail).
    Gate on name-like identity columns with Zipfian skew; skip flat/uniform
    columns where TF is a no-op."""
    if p.col_type not in ("name", "string"):
        return False
    s = df[p.name].drop_nulls()
    if s.len() < 20:
        return False
    vc = s.value_counts()
    counts = vc[vc.columns[-1]].to_list()
    if not counts:
        return False
    top = max(counts) / sum(counts)
    # skew: most-common value covers notably more than uniform share
    return top >= 2.0 * (1.0 / len(counts))
```

Then in the field append:
```python
        fields.append(MatchkeyField(
            field=p.name,
            scorer=scorer,
            transforms=transforms,
            levels=levels,
            partial_threshold=partial_threshold,
            tf_adjust=_is_skewed_identity(p, _df),   # pass the df into the builder
        ))
```
Note: `build_probabilistic_matchkeys(profiles)` currently has no `df` param. Add an optional `df: pl.DataFrame | None = None` parameter (default None ⇒ `tf_adjust=False`, fully backward compatible) and pass `df` from `auto_configure_probabilistic_df`.

- [ ] **Step 4: Run, verify pass.**

- [ ] **Step 5: Commit**

```bash
git commit -am "feat(probabilistic): auto-enable tf_adjust on skewed identity fields"
```

---

### Task 2.6: Measure Stage 2 on the panel + final gate (no code)

- [ ] Re-run `run_panel.py` across all available datasets. Verify:
  - `historical_50k` F1 ≥ the gate value set in Task 0.6.
  - `dblp_acm` recall up materially from 57.6% with precision ≥ ~0.95.
  - Febrl3 / NCVR / synthetic ≥ their baseline floors (non-regression).
- [ ] Paste the final panel table (vs Splink) into the PR. If a floor regressed, diagnose with `attribution.py` before merging.

---

## Final integration checks (before PR)

- [ ] Run all touched test files individually (not the full suite):
  `.venv\Scripts\python.exe -m pytest tests/bench/test_attribution.py tests/bench/test_datasets_loader.py tests/test_probabilistic_union_blocking.py tests/test_probabilistic_tf.py tests/test_fast_path_probabilistic.py -v`
- [ ] `ruff check` on changed files (E9/F63/F7 are the CI-blocking set).
- [ ] Confirm `tf_adjust=False` + single-key blocking path is byte-identical to pre-change behavior (the no-op parity tests in 2.3/2.4 are the guard).
- [ ] PR body: the before/after panel table + the attribution decomposition + the gate values. Do NOT `git add` anything under `docs/superpowers/`.

## Follow-ups (explicitly NOT in this plan)

- TS parity for TF adjustments (`probabilistic.ts` + `EMResult` schema) — separate PR.
- Probabilistic threshold-calibration loop / controller integration — only if Stage 0 attribution showed threshold-loss dominates.
- Perf optimization + bio/product domain scorers — separate cycles (see spec Out of Scope).
