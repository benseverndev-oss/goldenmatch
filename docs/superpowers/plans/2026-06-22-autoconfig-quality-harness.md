# Auto-config quality harness — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a harness that runs auto-config across a corpus (real labeled datasets + synthetic failure-shape anchors) and emits a comparative quality scorecard (fast config-signals + F1) with a committed baseline, diff, gate, and bless — so a kernel change's quality impact is measurable in one run.

**Architecture:** A repo-root package `scripts/autoconfig_quality/` of six small single-purpose units behind one CLI. It **consumes** existing decision primitives (`profile_columns`, `build_blocking`, `build_matchkeys`, `measure_blocking_profile`, `apply_planner_rules`, `dedupe_df`, `evaluate_clusters`, `attribution`) and never reimplements decision logic. Two tiers: fast config-signals (no dedupe; the anchor-pinned regression net) and slow F1/P/R on real data (ground truth).

**Tech Stack:** Python 3, Polars, the `goldenmatch` package; pytest for unit tests; pure-stdlib JSON for the scorecard.

**Spec:** `docs/superpowers/specs/2026-06-22-autoconfig-quality-harness-design.md`

---

## Load-bearing facts (verified against the code — do not re-derive)

- **Row-index id space is the universal convention.** `DedupeResult.clusters` is `dict[int, {"members": list[int], ...}]` where members are **0-based row indices** into the input df; `scored_pairs` is `list[(a, b, score)]` of row indices; `evaluate_clusters(clusters, ground_truth)` expands cluster members to pairs and compares to `ground_truth: set[tuple]`. **Therefore every dataset loader returns `(df, gt_pairs)` with `gt_pairs` already in row-index space** (a `set[(i, j)]`, `i<j`). Loaders that have a stable id column build `gt_pairs` by mapping id→position at load time. `gen_labeled` already does this.
- `measure_blocking_profile(df, config)` reads `config.blocking` — pass the **full config or a `SimpleNamespace(blocking=<BlockingConfig>)`** (the `bench_autoconfig_sample_quality.py` pattern), NOT a bare `BlockingConfig`.
- `build_blocks(lf, blocking_config)` takes a **bare `BlockingConfig`** and returns `list[BlockResult]`; each block's row ids are in `b.df.collect()["__row_id__"]` — which only exists if the input frame had `df.with_row_index("__row_id__")` added. **Candidate pairs are regenerated this way** (`DedupeResult` has NO candidate set).
- `evaluate_clusters` is in `goldenmatch/core/evaluate.py`; `dedupe_df`/`DedupeResult` are in `goldenmatch/_api.py` (re-exported as `goldenmatch.dedupe_df`); `attribution(gt, cand, emitted)` is in `scripts/bench_er_headtohead/attribution.py` and canonicalizes pairs internally.
- `ColumnProfile.col_type` (not `.type`); exact matchkey columns = `{f.field for mk in mks if mk.type == "exact" for f in mk.fields}`.
- `apply_planner_rules(ComplexityProfile, RuntimeProfile, n_rows_full, DEFAULT_RULES)` → `ExecutionPlan(.backend, .rule_name)`. Pass the real `DEFAULT_RULES` from `goldenmatch.core.autoconfig_planner_rules` (native fast-path engages only for that list). Minimal profile: `ComplexityProfile(blocking=<BlockingProfile>)`.
- **Repo-root script preamble:** set `os.environ.setdefault("POLARS_SKIP_CPU_CHECK", "1")` (and `PYTHONIOENCODING=utf-8`) BEFORE importing polars/goldenmatch. Run with `PYTHONPATH=packages/python/goldenmatch`. Do NOT force `GOLDENMATCH_NATIVE=0` — the harness runs native-default-on (it measures production behavior); `--native 0` flips it per-run.
- **Local test runs:** targeted file runs only (full suite OOMs the box). Pattern used throughout this repo:
  `cd packages/python/goldenmatch && PYTHONPATH="$(pwd)" GOLDENMATCH_NATIVE=0 POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8 <venv>/python.exe -m pytest <files> -q -p no:cacheprovider --timeout=180`.
  The venv python is `D:/show_case/goldenmatch/.venv/Scripts/python.exe`.
- The package conftest (`packages/python/goldenmatch/tests/conftest.py`) adds the **package** `scripts/` to `sys.path` (where `repro_issue_715.py` lives) — but NOT the repo-root `scripts/`. The harness lives in repo-root `scripts/`, so its OWN tests live in repo-root `tests/` or use an explicit path add (see Task 7).

---

## File structure

| File | Responsibility |
|---|---|
| `scripts/autoconfig_quality/__init__.py` | package marker (empty) |
| `scripts/autoconfig_quality/_preamble.py` | env setdefault + `goldenmatch` import guard (one place) |
| `scripts/autoconfig_quality/anchors.py` | shared synthetic anchor generators (extracted `_crm_df`, `gen_labeled`; re-export `make_healthcare_df`) |
| `scripts/autoconfig_quality/datasets.py` | `Dataset` dataclass + `REGISTRY` (anchors + real loaders, skip-when-absent) |
| `scripts/autoconfig_quality/signals.py` | FAST tier: `extract_signals(df) -> dict` (classification, matchkeys, blocking fields+cost, planner rung) |
| `scripts/autoconfig_quality/f1.py` | SLOW tier: `evaluate_f1(df, gt_pairs, row_cap) -> dict` (F1/P/R + attribution) |
| `scripts/autoconfig_quality/scorecard.py` | `build_scorecard(results, meta) -> dict` + `load`/`dump` JSON |
| `scripts/autoconfig_quality/diff.py` | `diff_scorecards(current, baseline) -> (rows, verdict)` + render table |
| `scripts/autoconfig_quality/__main__.py` | CLI: `report` / `gate` / `bless`, `--fast-only`/`--datasets`/`--native`/`--row-cap` |
| `scripts/autoconfig_quality/baselines/scorecard.json` | committed baseline (the bless target) |
| `scripts/autoconfig_quality/tests/test_*.py` | unit tests per unit + a self-gating smoke test |

Tests live under `scripts/autoconfig_quality/tests/` and run via an explicit `PYTHONPATH` that includes both the repo-root `scripts/` (for `autoconfig_quality` + `bench_er_headtohead`) and `packages/python/goldenmatch` (for `goldenmatch` + `repro_issue_715` on the package scripts path). See each task's run command.

**Shared run-env (used by every test command below), call it `$ENV`:**
```
GOLDENMATCH_NATIVE=0 POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8 \
PYTHONPATH="D:/show_case/gm-autoconfig-core;D:/show_case/gm-autoconfig-core/packages/python/goldenmatch;D:/show_case/gm-autoconfig-core/packages/python/goldenmatch/scripts"
```
(`D:/show_case/gm-autoconfig-core` on the path makes `scripts.autoconfig_quality` and `scripts.bench_er_headtohead` importable; the package dir makes `goldenmatch` importable; the package `scripts` dir makes `repro_issue_715` importable.) Use `;` separators on Windows. The venv python is `D:/show_case/goldenmatch/.venv/Scripts/python.exe` (call it `$PY`).

---

## Task 1: Shared anchor generators (extract fixtures, no copy)

**Files:**
- Create: `scripts/autoconfig_quality/__init__.py` (empty), `scripts/autoconfig_quality/anchors.py`
- Modify: `packages/python/goldenmatch/tests/test_quality_gate.py` (import `gen_labeled` from the shared module), `packages/python/goldenmatch/tests/test_autoconfig_multisource.py` (import `_crm_df` from the shared module)
- Test: `scripts/autoconfig_quality/tests/test_anchors.py`

The spec requires ONE definition of each anchor shape. `gen_labeled` and `_crm_df` currently live inside test modules; extract them so both tests and the harness import them. `make_healthcare_df` already lives in an importable script — re-export it.

- [ ] **Step 1: Write the failing test**

`scripts/autoconfig_quality/tests/test_anchors.py`:
```python
from scripts.autoconfig_quality.anchors import crm_df, gen_labeled, make_healthcare_df

def test_crm_df_shape():
    df = crm_df()
    assert df.height == 30
    assert "email" in df.columns and "phone" in df.columns

def test_gen_labeled_returns_df_and_rowindex_gt():
    df, gt = gen_labeled(n_entities=50, seed=7)
    assert df.height >= 50
    # GT pairs are row-index tuples i<j
    assert all(isinstance(a, int) and isinstance(b, int) and a < b for a, b in gt)
    assert max(b for _, b in gt) < df.height  # indices in range

def test_make_healthcare_df_has_zip5():
    df = make_healthcare_df(2000, seed=715, zip_present=0.5)
    assert "zip5" in df.columns and "matching_id" in df.columns
```

- [ ] **Step 2: Run to verify it fails** — `cd D:/show_case/gm-autoconfig-core && $ENV $PY -m pytest scripts/autoconfig_quality/tests/test_anchors.py -q` → FAIL (module not found).

- [ ] **Step 3: Create `anchors.py`** — move the verbatim bodies of `gen_labeled` (from `test_quality_gate.py`, incl. its `_FIRST`/`_SURN`/`_typo` helpers and imports) and `_crm_df` (from `test_autoconfig_multisource.py`, rename the public fn `crm_df`), and re-export `make_healthcare_df`:
```python
"""Shared synthetic anchor generators — ONE definition, imported by both the
tests and the quality harness. Bodies lifted verbatim from the test fixtures."""
from __future__ import annotations
import random
from collections import defaultdict
from itertools import combinations
import polars as pl
import sys
from pathlib import Path
# make_healthcare_df lives in the package scripts/ dir
_PKG_SCRIPTS = Path(__file__).resolve().parents[2] / "packages/python/goldenmatch/scripts"
if str(_PKG_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_PKG_SCRIPTS))
from repro_issue_715 import make_healthcare_df  # noqa: E402,F401

_FIRST = [...]   # copy from test_quality_gate.py
_SURN = [...]    # copy from test_quality_gate.py
def _typo(s, rng): ...   # copy verbatim
def gen_labeled(n_entities: int = 400, seed: int = 7) -> tuple[pl.DataFrame, set]:
    ...   # body verbatim from test_quality_gate.py
def crm_df() -> pl.DataFrame:
    ...   # body verbatim from test_autoconfig_multisource._crm_df
```
Then update `test_quality_gate.py` to `from autoconfig_quality.anchors import gen_labeled` (the package-conftest sys.path doesn't include repo-root scripts, so add at the top of those test files: `sys.path.insert(0, <repo-root>)` then `from scripts.autoconfig_quality.anchors import ...`) and delete the local copies. Same for `test_autoconfig_multisource.py` (`crm_df` → use as `_crm_df = crm_df` alias to minimize churn).

- [ ] **Step 4: Run to verify it passes** — the anchors test passes; AND re-run the two donor tests to confirm the extraction didn't break them:
`cd packages/python/goldenmatch && PYTHONPATH="$(pwd);D:/show_case/gm-autoconfig-core" GOLDENMATCH_NATIVE=0 POLARS_SKIP_CPU_CHECK=1 $PY -m pytest tests/test_quality_gate.py tests/test_autoconfig_multisource.py -q` → PASS.

- [ ] **Step 5: Commit** — `git add scripts/autoconfig_quality/ packages/python/goldenmatch/tests/test_quality_gate.py packages/python/goldenmatch/tests/test_autoconfig_multisource.py && git commit -m "feat(quality): shared anchor generators (extract gen_labeled + crm_df)"`

---

## Task 2: Fast-tier config-signal extractor (`signals.py`)

**Files:**
- Create: `scripts/autoconfig_quality/signals.py`
- Test: `scripts/autoconfig_quality/tests/test_signals.py`

Extract the five fast signals from a df via the decision path — no dedupe.

- [ ] **Step 1: Write the failing test** — build a tiny df with a known outcome (the sparse-zip anchor at small N is ideal: zip5 should be `zip` and appear in blocking fields):
```python
import polars as pl
from scripts.autoconfig_quality.anchors import make_healthcare_df
from scripts.autoconfig_quality.signals import extract_signals

def test_extract_signals_sparse_zip():
    df = make_healthcare_df(2000, seed=715, zip_present=0.5).drop("matching_id")
    sig = extract_signals(df)
    assert sig["classification"]["zip5"] == "zip"          # not fooled into identifier
    assert "zip5" in sig["blocking_fields"]                 # the decouple fix
    assert sig["blocking_cost"]["candidate_pairs"] < 50_000 # bounded, not 8.9M
    assert "max_block" in sig["blocking_cost"]
    assert isinstance(sig["exact_matchkeys"], list)
    assert "backend" in sig["planner_rung"] and "rule_name" in sig["planner_rung"]
```

- [ ] **Step 2: Run to verify it fails** — `... pytest scripts/autoconfig_quality/tests/test_signals.py -q` → FAIL (no `extract_signals`).

- [ ] **Step 3: Implement `signals.py`**:
```python
"""FAST tier: config-quality signals (no full dedupe)."""
from __future__ import annotations
from itertools import combinations
from types import SimpleNamespace
from typing import Any
import polars as pl
from goldenmatch.core.autoconfig import profile_columns, build_blocking, build_matchkeys
from goldenmatch.core.blocker import measure_blocking_profile
from goldenmatch.core.complexity_profile import ComplexityProfile
from goldenmatch.core.runtime_profile import capture_runtime_profile
from goldenmatch.core.autoconfig_planner import apply_planner_rules
from goldenmatch.core.autoconfig_planner_rules import DEFAULT_RULES

def extract_signals(df: pl.DataFrame) -> dict[str, Any]:
    profiles = profile_columns(df)
    classification = {p.name: p.col_type for p in profiles}
    matchkeys = build_matchkeys(profiles, df)
    exact_matchkeys = sorted({f.field for mk in matchkeys
                              if mk.type == "exact" for f in mk.fields if f.field})
    blocking = build_blocking(profiles, df, n_rows_full=df.height)
    fields: set[str] = set()
    for k in (blocking.keys or []):
        fields.update(k.fields)
    for p in (blocking.passes or []):
        fields.update(p.fields)
    bp = measure_blocking_profile(df, SimpleNamespace(blocking=blocking))
    blocking_cost = (
        {"candidate_pairs": bp.estimated_pair_count, "n_blocks": bp.n_blocks,
         "max_block": bp.block_sizes_max, "p99": bp.block_sizes_p99,
         "reduction_ratio": round(bp.reduction_ratio, 4)}
        if bp is not None else {"candidate_pairs": None, "error": "measure_returned_none"}
    )
    cp = ComplexityProfile(blocking=bp) if bp is not None else ComplexityProfile()
    plan = apply_planner_rules(cp, capture_runtime_profile(), df.height, DEFAULT_RULES)
    return {
        "classification": classification,
        "exact_matchkeys": exact_matchkeys,
        "blocking_fields": sorted(fields),
        "blocking_cost": blocking_cost,
        "planner_rung": {"backend": plan.backend, "rule_name": plan.rule_name},
    }
```

- [ ] **Step 4: Run to verify it passes** — PASS. (If `candidate_pairs` exceeds the bound, the blocking-decouple fix isn't in this tree — confirm you're on main/post-#1205.)

- [ ] **Step 5: Commit** — `git add scripts/autoconfig_quality/signals.py scripts/autoconfig_quality/tests/test_signals.py && git commit -m "feat(quality): fast-tier config-signal extractor"`

---

## Task 3: F1 tier with attribution (`f1.py`)

**Files:**
- Create: `scripts/autoconfig_quality/f1.py`
- Test: `scripts/autoconfig_quality/tests/test_f1.py`

Run the full dedupe, compute F1/P/R via `evaluate_clusters`, and the blocking-recall/threshold-loss attribution (candidate pairs regenerated from `build_blocks`).

- [ ] **Step 1: Write the failing test** — `gen_labeled` gives a df + row-index GT, the cleanest F1 fixture:
```python
from scripts.autoconfig_quality.anchors import gen_labeled
from scripts.autoconfig_quality.f1 import evaluate_f1

def test_evaluate_f1_on_gen_labeled():
    df, gt = gen_labeled(n_entities=200, seed=7)
    out = evaluate_f1(df, gt, row_cap=None)
    assert 0.0 <= out["f1"] <= 1.0
    assert out["f1"] >= 0.80           # synthetic clones are easy
    assert set(out) >= {"f1", "precision", "recall", "attribution"}
    attr = out["attribution"]
    assert {"blocking_recall", "final_recall", "threshold_loss"} <= set(attr)
    assert attr["blocking_recall"] >= attr["final_recall"]  # blocking is the ceiling
```

- [ ] **Step 2: Run to verify it fails** → FAIL (no `evaluate_f1`).

- [ ] **Step 3: Implement `f1.py`**:
```python
"""SLOW tier: full dedupe -> F1/P/R + blocking-recall/threshold-loss attribution."""
from __future__ import annotations
from itertools import combinations
from types import SimpleNamespace
from typing import Any
import polars as pl
import goldenmatch
from goldenmatch.core.evaluate import evaluate_clusters
from goldenmatch.core.autoconfig import profile_columns, build_blocking
from goldenmatch.core.blocker import build_blocks
from scripts.bench_er_headtohead.attribution import attribution

def _candidate_pairs(df: pl.DataFrame) -> set[tuple[int, int]]:
    """Regenerate the post-blocking candidate set in row-index space.
    DedupeResult has no candidate set, so rebuild via build_blocks + __row_id__."""
    profiles = profile_columns(df)
    blocking = build_blocking(profiles, df, n_rows_full=df.height)
    lf = df.with_row_index("__row_id__").lazy()
    cand: set[tuple[int, int]] = set()
    try:
        for b in build_blocks(lf, blocking):
            ids = b.df.collect()["__row_id__"].to_list()
            cand.update((min(a, c), max(a, c)) for a, c in combinations(ids, 2))
    except Exception:
        return set()  # attribution degrades to 0 blocking_recall, never crashes F1
    return cand

def evaluate_f1(df: pl.DataFrame, gt_pairs: set, row_cap: int | None = 20_000) -> dict[str, Any]:
    if row_cap is not None and df.height > row_cap:
        df = df.head(row_cap)
        gt_pairs = {(a, b) for a, b in gt_pairs if a < row_cap and b < row_cap}
    result = goldenmatch.dedupe_df(df)
    ev = evaluate_clusters(result.clusters, gt_pairs).summary()
    emitted = {(min(a, b), max(a, b)) for a, b, _ in result.scored_pairs}
    attr = attribution(gt_pairs, _candidate_pairs(df), emitted)
    return {"f1": ev["f1"], "precision": ev["precision"], "recall": ev["recall"],
            "attribution": {k: attr[k] for k in ("blocking_recall", "final_recall", "threshold_loss")}}
```

- [ ] **Step 4: Run to verify it passes** → PASS. (dedupe at 200 entities is seconds.)

- [ ] **Step 5: Commit** — `git add scripts/autoconfig_quality/f1.py scripts/autoconfig_quality/tests/test_f1.py && git commit -m "feat(quality): F1 tier + blocking/threshold attribution"`

---

## Task 4: Scorecard build + JSON I/O (`scorecard.py`)

**Files:**
- Create: `scripts/autoconfig_quality/scorecard.py`
- Test: `scripts/autoconfig_quality/tests/test_scorecard.py`

Assemble per-dataset records into the JSON shape; stable provenance (git sha + native version, no timestamp/RNG); rounded floats.

- [ ] **Step 1: Write the failing test**:
```python
import json
from scripts.autoconfig_quality.scorecard import build_scorecard, dumps, loads

def test_build_scorecard_shape_and_stability():
    results = {
        "anchor_x": {"kind": "anchor", "signals": {"blocking_cost": {"candidate_pairs": 1529}}},
        "febrl3": {"kind": "real", "signals": {}, "f1": {"f1": 0.991}},
    }
    sc = build_scorecard(results, native_version="0.1.11", git_sha="abc123",
                         skipped={"ncvr": "absent"})
    assert sc["meta"]["native_version"] == "0.1.11"
    assert sc["meta"]["git_sha"] == "abc123"
    assert sc["meta"]["datasets_skipped"] == {"ncvr": "absent"}
    assert sorted(sc["meta"]["datasets_run"]) == ["anchor_x", "febrl3"]
    assert "recorded_at" not in json.dumps(sc)        # NO timestamp -> byte-stable
    assert loads(dumps(sc)) == sc                      # round-trips
```

- [ ] **Step 2: Run to verify it fails** → FAIL.

- [ ] **Step 3: Implement `scorecard.py`** — `build_scorecard(results, native_version, git_sha, skipped) -> dict` assembling `{"meta": {...}, "datasets": results}`; `dumps`/`loads` as `json.dumps(sc, indent=2, sort_keys=True)` / `json.loads`. Provide a `gather_meta()` helper that reads `git rev-parse HEAD` (subprocess) and `goldenmatch_native.__version__` (try/except → "absent"), but `build_scorecard` takes them as args (so tests are deterministic).

- [ ] **Step 4: Run to verify it passes** → PASS.

- [ ] **Step 5: Commit** — `git add ... && git commit -m "feat(quality): scorecard build + stable JSON I/O"`

---

## Task 5: Diff + gate verdict (`diff.py`)

**Files:**
- Create: `scripts/autoconfig_quality/diff.py`
- Test: `scripts/autoconfig_quality/tests/test_diff.py`

The gate logic from the spec: anchor signal change = FAIL, real F1 below floor−tol = FAIL, skipped = neutral, anchor error = FAIL, real error = neutral.

- [ ] **Step 1: Write the failing test** — cover each verdict branch:
```python
from scripts.autoconfig_quality.diff import diff_scorecards

BASE = {"datasets": {
    "anchor_x": {"kind": "anchor", "signals": {"blocking_cost": {"candidate_pairs": 1529}, "classification": {"zip5": "zip"}}},
    "febrl3":   {"kind": "real", "f1": {"f1": 0.99}},
}}

def test_anchor_signal_change_fails():
    cur = {"datasets": {**BASE["datasets"],
        "anchor_x": {"kind": "anchor", "signals": {"blocking_cost": {"candidate_pairs": 8_931_083}, "classification": {"zip5": "zip"}}}}}
    rows, verdict = diff_scorecards(cur, BASE, tolerance=0.01)
    assert verdict == "FAIL"
    assert any(r["status"] == "FAIL" and r["dataset"] == "anchor_x" for r in rows)

def test_real_f1_drop_beyond_tol_fails():
    cur = {"datasets": {**BASE["datasets"], "febrl3": {"kind": "real", "f1": {"f1": 0.95}}}}
    _, verdict = diff_scorecards(cur, BASE, tolerance=0.01)
    assert verdict == "FAIL"

def test_real_f1_within_tol_passes():
    cur = {"datasets": {**BASE["datasets"], "febrl3": {"kind": "real", "f1": {"f1": 0.985}}}}
    _, verdict = diff_scorecards(cur, BASE, tolerance=0.01)
    assert verdict == "PASS"

def test_skipped_is_neutral_and_anchor_error_fails():
    cur = {"datasets": {
        "anchor_x": {"kind": "anchor", "signals": {"error": "boom"}},
        "febrl3":   {"kind": "real", "error": "flake"}}}
    rows, verdict = diff_scorecards(cur, BASE, tolerance=0.01)
    assert verdict == "FAIL"           # anchor error
    # real error is neutral on its own:
    cur2 = {"datasets": {**BASE["datasets"], "febrl3": {"kind": "real", "error": "flake"}}}
    _, verdict2 = diff_scorecards(cur2, BASE, tolerance=0.01)
    assert verdict2 == "PASS"
```

- [ ] **Step 2: Run to verify it fails** → FAIL.

- [ ] **Step 3: Implement `diff.py`** — `diff_scorecards(current, baseline, tolerance) -> (rows, verdict)`:
  - For each dataset in current: if `kind == anchor`: any `signals` value differing from baseline (deep compare) → row `status=FAIL`; an `error` key in anchor signals → `status=FAIL`. If `kind == real`: compare `f1["f1"]` vs baseline; `< baseline_f1 - tolerance` → `FAIL`, else informational row (signals drift rendered as `⚠ changed`, status `OK`); an `error` on a real dataset → `status=NEUTRAL`.
  - Datasets present in baseline but absent/skipped in current → `status=NEUTRAL`.
  - `verdict = "FAIL"` if any row `status==FAIL` else `"PASS"`.
  - Add `render_table(rows) -> str` producing the aligned delta table from the spec (used by the CLI).

- [ ] **Step 4: Run to verify it passes** → PASS.

- [ ] **Step 5: Commit** — `git add ... && git commit -m "feat(quality): scorecard diff + gate verdict"`

---

## Task 6: Dataset registry (`datasets.py`)

**Files:**
- Create: `scripts/autoconfig_quality/datasets.py`
- Test: `scripts/autoconfig_quality/tests/test_datasets.py`

`Dataset` dataclass + `REGISTRY`: three anchors (always available) + the real loaders (skip-when-absent).

- [ ] **Step 1: Write the failing test**:
```python
from scripts.autoconfig_quality.datasets import REGISTRY, Dataset

def test_anchors_always_load():
    by_name = {d.name: d for d in REGISTRY}
    for n in ("anchor_sparse_zip", "anchor_shared_email", "anchor_person_match"):
        d = by_name[n]
        assert d.kind == "anchor"
        loaded = d.loader()
        assert loaded is not None
        df, gt = loaded
        assert df.height > 0

def test_person_anchor_has_gt_others_none():
    by_name = {d.name: d for d in REGISTRY}
    _, gt = by_name["anchor_person_match"].loader()
    assert len(gt) > 0                                   # gen_labeled has GT
    _, gt2 = by_name["anchor_sparse_zip"].loader()
    assert gt2 == set()                                  # blocking-shape anchor, no F1

def test_real_loader_skips_when_absent(monkeypatch):
    by_name = {d.name: d for d in REGISTRY}
    dblp = by_name["dblp_acm"]
    assert dblp.kind == "real"
    # when the data dir doesn't exist the loader returns None (skip), never raises
    res = dblp.loader()
    assert res is None or (isinstance(res, tuple) and len(res) == 2)
```

- [ ] **Step 2: Run to verify it fails** → FAIL.

- [ ] **Step 3: Implement `datasets.py`**:
```python
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal
import polars as pl
from scripts.autoconfig_quality.anchors import crm_df, gen_labeled, make_healthcare_df

@dataclass(frozen=True)
class Dataset:
    name: str
    kind: Literal["anchor", "real"]
    loader: Callable[[], tuple[pl.DataFrame, set] | None]
    expected: dict | None = None   # pinned fast-signal values (anchors only)

def _sparse_zip():
    df = make_healthcare_df(20_000, seed=715, zip_present=0.5).drop("matching_id")
    return df, set()               # no true dups -> blocking-shape anchor, F1 N/A

def _shared_email():
    return crm_df(), set()         # config-shape anchor, F1 N/A

def _person():
    return gen_labeled(n_entities=400, seed=7)

_DATASETS_ROOT = Path(__file__).resolve().parents[2] / "packages/python/goldenmatch/tests/benchmarks/datasets"

def _dblp_acm():
    d = _DATASETS_ROOT / "DBLP-ACM"
    if not d.exists():
        return None
    # load + build row-index GT from the perfectMapping (see test_autoconfig_benchmarks pattern)
    ...

REGISTRY: list[Dataset] = [
    Dataset("anchor_sparse_zip", "anchor", _sparse_zip,
            expected={"classification": {"zip5": "zip"}, "blocking_fields_contains": "zip5",
                      "candidate_pairs_max": 50_000}),
    Dataset("anchor_shared_email", "anchor", _shared_email,
            expected={"exact_matchkeys_contains": "email"}),
    Dataset("anchor_person_match", "anchor", _person, expected=None),  # F1-floored, not signal-pinned
    Dataset("dblp_acm", "real", _dblp_acm),
    # NCVR, FEBRL3, historical_50k, dqbench tiers: same skip-when-absent pattern
]
```
The `expected` block is consumed by `diff.py`/the gate as the pinned baseline for anchors. (Implement `_dblp_acm` and one more real loader concretely following `test_autoconfig_benchmarks.py:122-192` — remap cluster members via the id-column positional lookup to build row-index GT. Other real loaders may be stubs that return `None` until their data is wired, as long as they skip cleanly.)

- [ ] **Step 4: Run to verify it passes** → PASS (anchors load; real skips when absent).

- [ ] **Step 5: Commit** — `git add ... && git commit -m "feat(quality): dataset registry (anchors + real, skip-when-absent)"`

---

## Task 7: CLI + self-gating smoke test (`__main__.py`) + baseline

**Files:**
- Create: `scripts/autoconfig_quality/__main__.py`, `scripts/autoconfig_quality/baselines/scorecard.json`
- Test: `scripts/autoconfig_quality/tests/test_cli_smoke.py`

Wire the units into `report` / `gate` / `bless` with flags; generate + commit the baseline; the smoke test runs the harness over the anchors and asserts the gate passes on the committed baseline (the harness gates itself).

- [ ] **Step 1: Write the failing smoke test**:
```python
import subprocess, sys, os
def test_gate_fast_only_passes_on_committed_baseline():
    env = {**os.environ, "POLARS_SKIP_CPU_CHECK": "1",
           "PYTHONPATH": os.pathsep.join([REPO, PKG, PKG_SCRIPTS])}
    r = subprocess.run([sys.executable, "-m", "scripts.autoconfig_quality",
                        "gate", "--fast-only", "--datasets",
                        "anchor_sparse_zip,anchor_shared_email,anchor_person_match"],
                       cwd=REPO, env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
```

- [ ] **Step 2: Run to verify it fails** → FAIL (no `__main__`).

- [ ] **Step 3: Implement `__main__.py`** — argparse with subcommands `report` (default) / `gate` / `bless`; flags `--fast-only`, `--datasets a,b`, `--native {0,1,auto}` (sets `GOLDENMATCH_NATIVE` before importing goldenmatch), `--row-cap N`. The run loop: for each selected `Dataset`, `loader()` → `None` ⇒ record skipped; else `extract_signals(df)` (always) + (`evaluate_f1` unless `--fast-only` or `gt == set()`); collect into `results`; `build_scorecard`. `report` prints `render_table(diff_scorecards(current, baseline))`; `gate` exits nonzero on `FAIL` verdict; `bless` writes `current` to `baselines/scorecard.json`. Wrap each dataset's extraction in try/except → `{"error": str(e)}` (anchor error path).

- [ ] **Step 4: Generate + commit the baseline** — run `python -m scripts.autoconfig_quality bless --fast-only --datasets anchor_sparse_zip,anchor_shared_email,anchor_person_match` (+ any locally-present real datasets), inspect the JSON is sane (anchor signals match the known-good config, F1 floors set), then run the smoke test → PASS.

- [ ] **Step 5: Commit** — `git add scripts/autoconfig_quality/__main__.py scripts/autoconfig_quality/baselines/scorecard.json scripts/autoconfig_quality/tests/test_cli_smoke.py && git commit -m "feat(quality): CLI (report/gate/bless) + committed baseline + self-gating smoke test"`

---

## Task 8: CI wiring + docs

**Files:**
- Modify: `.github/workflows/ci.yml` (add a `quality-gate` job + a `changes` filter entry), the doc-surface inventory if one lists scripts.
- Create: `scripts/autoconfig_quality/README.md` (how to iterate: change a kernel → `report` → read the diff → `bless` to accept).

- [ ] **Step 1:** Add a `quality_gate` path-filter to the `changes` job (triggers on `packages/rust/extensions/autoconfig-core/**`, `packages/python/goldenmatch/goldenmatch/core/autoconfig*.py`, `.../blocker.py`, `scripts/autoconfig_quality/**`).
- [ ] **Step 2:** Add a `quality-gate` job gated on that filter: checkout, install the package + deps, run `python -m scripts.autoconfig_quality gate --fast-only` (anchors are committed → always runs) + the F1 tier for any dataset that resolves in CI (DBLP-ACM if committed). Keep it a normal-runner job (fast tier is seconds).
- [ ] **Step 3:** Write the README (the iterate loop + bless workflow). Reference it from the spec.
- [ ] **Step 4:** Run `python -m scripts.autoconfig_quality report` locally end-to-end (anchors + any local real datasets) and paste the table into the PR description as the first scorecard.
- [ ] **Step 5: Commit + PR** — `git add .github/workflows/ci.yml scripts/autoconfig_quality/README.md && git commit -m "ci(quality): autoconfig quality-gate job + iterate docs"`; open the PR. (CI runs on this branch; arm `gh pr merge --auto --squash` once green per the repo SOP.)

---

## Verification & sequencing notes

- **Each task is self-contained and committable.** Tasks 2–6 (the units) have no inter-dependencies beyond Task 1's anchors and are unit-tested in isolation on hand-built / anchor dfs — no real datasets needed. Task 7 ties them together; Task 8 wires CI.
- **Local runs:** targeted file runs only (never the full xdist suite — OOMs the box). The dedupe in Task 3's F1 test at 200 entities is seconds; the harness `--fast-only` path is seconds across the anchors.
- **The harness measures production behavior** (native default-on); `--native 0` is the parity knob. Do NOT bake `GOLDENMATCH_NATIVE=0` into the harness itself (only into the *test* commands, for determinism).
- **YAGNI guardrails (from the spec):** no perf/wall-clock metrics, no web UI, no trend DB (the committed baseline's git log IS the trend), no Splink comparison.
