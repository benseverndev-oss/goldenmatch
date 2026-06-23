# Auto-config Quality Harness: Corpus Broadening Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add four real benchmark datasets (FEBRL3, synthetic-NCVR, real-NCVR, historical_50k) to the auto-config quality harness corpus so its F1 + attribution tier can localize where the kernel underperforms on real data and nominate the next lever on evidence.

**Architecture:** Pure extension of the existing `Dataset` registry in `scripts/autoconfig_quality/datasets.py`. Each new entry calls an *existing* standalone loader, converts its native truth (rec_id / ncid string pairs, or a `cluster` label column) into the harness's row-index `set[(i,j)]`, and skips-when-absent. One vendored parquet is committed for historical_50k; one scale guard is added to the F1 attribution path so the 50k candidate set can't OOM. No kernel behavior changes — this adds only measurement.

**Tech Stack:** Python, polars, the goldenmatch dedupe pipeline, the repo's `scripts/dqbench_adapters/` benchmark loaders, `recordlinkage` (FEBRL3, pip), `splink` (one-time historical_50k vendoring).

**Spec:** `docs/superpowers/specs/2026-06-23-autoconfig-quality-corpus-broadening-design.md`

---

## Execution model (READ FIRST — box constraint)

This Windows box is memory-starved and accumulates zombie Python processes that
starve it (OOM / fork-fail). Follow the same model the harness itself was built
under:

- **The controller (you) runs all tests in-session**, not via subagents. Use the
  pinned env for every harness run:
  ```
  PYTHONPATH="D:/show_case/gm-autoconfig-core;D:/show_case/gm-autoconfig-core/packages/python/goldenmatch;D:/show_case/gm-autoconfig-core/packages/python/goldenmatch/scripts" \
  GOLDENMATCH_AUTOCONFIG_MEMORY=0 POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8 \
  /d/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest <target> -q -p no:cacheprovider
  ```
  Targeted file runs ONLY — never the full xdist suite (it OOMs the box).
- **Subagent reviewers are READ-ONLY**: ruff / py_compile / reading the diff only.
  Never let a subagent run pytest / import goldenmatch / uv / pyright.
- Branch: `feat/quality-corpus-broadening` (already created off `origin/main`).

### One-time local environment (needed for Tasks 4 + 7)

```bash
/d/show_case/goldenmatch/.venv/Scripts/python.exe -m pip install recordlinkage splink
```
`recordlinkage` is needed for FEBRL3 (loader returns `None` without it → would
silently ship no FEBRL3 floor). `splink` is needed ONLY for the one-time
`vendor_historical_50k.py` run, not at gate time.

---

## File Structure

| File | Responsibility |
| --- | --- |
| `scripts/autoconfig_quality/datasets.py` (modify) | `Dataset.full_scan` field; shared `_pairs_to_row_index` helper; `_febrl3`, `_ncvr_synthetic`, `_ncvr_real`, `_historical_50k` loaders + registry entries |
| `scripts/autoconfig_quality/__main__.py` (modify) | route per-dataset `full_scan` to `evaluate_f1`'s row cap |
| `scripts/autoconfig_quality/f1.py` (modify) | attribution candidate-pair scale guard |
| `scripts/autoconfig_quality/vendor_historical_50k.py` (create) | one-time splink→parquet generator |
| `scripts/autoconfig_quality/vendored/historical_50k.parquet` (create) | committed dataset (records + `cluster` truth) |
| `scripts/autoconfig_quality/tests/test_datasets.py` (modify) | loader + conversion + no-truth-column tests |
| `scripts/autoconfig_quality/tests/test_f1.py` (modify) | attribution scale-guard test |
| `.github/workflows/ci.yml` (modify) | `recordlinkage` install in `quality_gate` |
| `scripts/autoconfig_quality/README.md` (modify) | broadened-corpus + attribution-localizes-levers docs |
| `scripts/autoconfig_quality/baselines/scorecard.json` (re-bless) | pin the new F1 floors |

---

## Task 1: `full_scan` field + per-dataset cap plumbing

**Files:**
- Modify: `scripts/autoconfig_quality/datasets.py` (the `Dataset` dataclass, ~line 25)
- Modify: `scripts/autoconfig_quality/__main__.py` (`run()`, ~lines 25-57)
- Test: `scripts/autoconfig_quality/tests/test_main_cap.py` (create)

- [ ] **Step 1: Write the failing test** — `scripts/autoconfig_quality/tests/test_main_cap.py`:

```python
from scripts.autoconfig_quality.datasets import Dataset
from scripts.autoconfig_quality.__main__ import effective_row_cap


def test_full_scan_defaults_false_and_uses_cli_cap():
    d = Dataset("x", "real", lambda: None)
    assert d.full_scan is False
    assert effective_row_cap(d, 20_000) == 20_000


def test_full_scan_true_disables_cap():
    d = Dataset("big", "real", lambda: None, full_scan=True)
    assert effective_row_cap(d, 20_000) is None
```

- [ ] **Step 2: Run it to verify it fails**

Run (pinned env): `python -m pytest scripts/autoconfig_quality/tests/test_main_cap.py -q`
Expected: FAIL — `Dataset` has no `full_scan`; `effective_row_cap` not defined.

- [ ] **Step 3: Add the field** — in `datasets.py`, extend the dataclass (keep `frozen=True`; the default keeps every existing `Dataset(...)` call valid):

```python
@dataclass(frozen=True)
class Dataset:
    name: str
    kind: Literal["anchor", "real"]
    loader: Callable[[], tuple[pl.DataFrame, set] | None]
    full_scan: bool = False  # True -> F1 tier ignores --row-cap, runs the whole df
```

- [ ] **Step 4: Add the helper + wire it** — in `__main__.py`, add at module scope:

```python
def effective_row_cap(dataset, cli_row_cap: int | None) -> int | None:
    """A full_scan dataset ignores the CLI cap (None = no truncation in evaluate_f1)."""
    return None if dataset.full_scan else cli_row_cap
```

Then in `run()`, change the F1 call from `evaluate_f1(df, gt, row_cap=row_cap)` to:

```python
rec["f1"] = evaluate_f1(df, gt, row_cap=effective_row_cap(d, row_cap))
```

(`d` is the loop variable over `REGISTRY`; it is in scope at that call site.)

- [ ] **Step 5: Run the test to verify it passes** + ruff.

Run: `python -m pytest scripts/autoconfig_quality/tests/test_main_cap.py -q` → PASS
Run: `python -m ruff check scripts/autoconfig_quality/datasets.py scripts/autoconfig_quality/__main__.py scripts/autoconfig_quality/tests/test_main_cap.py` → clean

- [ ] **Step 6: Commit**

```bash
git add scripts/autoconfig_quality/datasets.py scripts/autoconfig_quality/__main__.py scripts/autoconfig_quality/tests/test_main_cap.py
git commit -m "feat(quality): per-dataset full_scan cap override"
```

---

## Task 2: Shared row-index conversion helper + FEBRL3 loader

**Files:**
- Modify: `scripts/autoconfig_quality/datasets.py`
- Test: `scripts/autoconfig_quality/tests/test_datasets.py`

The id-pair → row-index recipe is identical for DBLP-ACM, FEBRL3, and both NCVRs.
Factor it once, then add FEBRL3.

- [ ] **Step 1: Write the failing test** — append to `tests/test_datasets.py`:

```python
import polars as pl
import pytest
from scripts.autoconfig_quality.datasets import _pairs_to_row_index


def test_pairs_to_row_index_maps_and_canonicalizes():
    df = pl.DataFrame({"id": ["a", "b", "c"]})
    # (c,a) -> rows (2,0) -> canonical (0,2); (b,b) self -> dropped; (x,a) missing -> dropped
    gt = _pairs_to_row_index(df, "id", {("c", "a"), ("b", "b"), ("x", "a")})
    assert gt == {(0, 2)}


def test_febrl3_loader_shape_or_skip():
    pytest.importorskip("recordlinkage")
    from scripts.autoconfig_quality.datasets import _febrl3
    loaded = _febrl3()
    assert loaded is not None
    df, gt = loaded
    assert "id" in df.columns and gt and all(0 <= a < b < df.height for a, b in gt)
```

- [ ] **Step 2: Run to verify it fails** → FAIL (`_pairs_to_row_index` / `_febrl3` not defined).

- [ ] **Step 3: Implement** — in `datasets.py`, add the helper (and refactor `_dblp_acm`'s inline mapping to call it — behavior-identical, DRY):

```python
def _pairs_to_row_index(
    df: pl.DataFrame, id_col: str, str_pairs: set[tuple[str, str]]
) -> set[tuple[int, int]]:
    """Map id-string pairs to canonical (min,max) row-index pairs; drop pairs whose
    endpoints are missing from the frame or identical."""
    pos = {str(v): i for i, v in enumerate(df[id_col].to_list())}
    out: set[tuple[int, int]] = set()
    for a, b in str_pairs:
        ia, ib = pos.get(str(a)), pos.get(str(b))
        if ia is not None and ib is not None and ia != ib:
            out.add((min(ia, ib), max(ia, ib)))
    return out
```

Add the FEBRL3 loader:

```python
def _febrl3() -> tuple[pl.DataFrame, set] | None:
    """FEBRL3 (recordlinkage-bundled). rec_id-pair truth -> row-index via df['id'].
    Returns None when recordlinkage isn't installed (skip-when-absent)."""
    from scripts.dqbench_adapters.febrl3 import load_febrl3_df_and_gt
    loaded = load_febrl3_df_and_gt()
    if loaded is None:
        return None
    df, rec_pairs = loaded
    return df, _pairs_to_row_index(df, "id", rec_pairs)
```

Register it (add to `REGISTRY`, real kind):

```python
    Dataset("febrl3", "real", _febrl3),
```

- [ ] **Step 4: Run the test to verify it passes** (febrl3 case skips if recordlinkage
absent — install it per the env section to actually exercise it) + ruff.

- [ ] **Step 5: Commit**

```bash
git add scripts/autoconfig_quality/datasets.py scripts/autoconfig_quality/tests/test_datasets.py
git commit -m "feat(quality): shared row-index helper + FEBRL3 corpus entry"
```

---

## Task 3: NCVR synthetic + real loaders

**Files:**
- Modify: `scripts/autoconfig_quality/datasets.py`
- Test: `scripts/autoconfig_quality/tests/test_datasets.py`

Two separate entries (different data, different F1, independent baselines):
`ncvr_synthetic` always runs; `ncvr_real` skips-when-absent.

- [ ] **Step 1: Write the failing test** — append to `tests/test_datasets.py`:

```python
def test_ncvr_synthetic_always_loads_with_row_index_gt():
    from scripts.autoconfig_quality.datasets import _ncvr_synthetic
    df, gt = _ncvr_synthetic()
    assert "ncid" in df.columns
    assert gt and all(0 <= a < b < df.height for a, b in gt)


def test_ncvr_real_skips_when_absent(monkeypatch):
    import scripts.autoconfig_quality.datasets as ds
    monkeypatch.setattr(ds, "_NCVR_REAL_PATH", ds.Path("does/not/exist.txt"))
    assert ds._ncvr_real() is None
```

- [ ] **Step 2: Run to verify it fails** → FAIL (loaders not defined).

- [ ] **Step 3: Implement** — in `datasets.py`:

```python
_NCVR_REAL_PATH = _DATASETS_ROOT / "NCVR" / "ncvoter_sample_10k.txt"


def _ncvr_synthetic() -> tuple[pl.DataFrame, set]:
    """PII-free NCVR-shaped corpus (seed 42, committable, runs in CI). Its F1 is its
    OWN baseline, never the real-data number."""
    from scripts.dqbench_adapters.ncvr import build_ncvr_synthetic_df_and_gt
    df, ncid_pairs = build_ncvr_synthetic_df_and_gt(seed=42)
    return df, _pairs_to_row_index(df, "ncid", ncid_pairs)


def _ncvr_real() -> tuple[pl.DataFrame, set] | None:
    """Real NCVR sample (gitignored PII, local-only). None when the file is absent."""
    from scripts.dqbench_adapters.ncvr import build_ncvr_df_and_gt
    loaded = build_ncvr_df_and_gt(_NCVR_REAL_PATH, seed=42)
    if loaded is None:
        return None
    df, ncid_pairs = loaded
    return df, _pairs_to_row_index(df, "ncid", ncid_pairs)
```

Register both:

```python
    Dataset("ncvr_synthetic", "real", _ncvr_synthetic),
    Dataset("ncvr_real", "real", _ncvr_real),
```

- [ ] **Step 4: Run the test to verify it passes** + ruff.

- [ ] **Step 5: Commit**

```bash
git add scripts/autoconfig_quality/datasets.py scripts/autoconfig_quality/tests/test_datasets.py
git commit -m "feat(quality): NCVR synthetic + real corpus entries"
```

---

## Task 4: historical_50k vendor script + loader

**Files:**
- Create: `scripts/autoconfig_quality/vendor_historical_50k.py`
- Create: `scripts/autoconfig_quality/vendored/historical_50k.parquet` (generated, committed)
- Modify: `scripts/autoconfig_quality/datasets.py`
- Test: `scripts/autoconfig_quality/tests/test_datasets.py`

- [ ] **Step 1: Write the vendor script** — `scripts/autoconfig_quality/vendor_historical_50k.py`:

```python
"""One-time generator: pull splink's historical_50k and vendor it as a committed
parquet so the harness reads a fixed, version-independent source (CI == local).
Run locally with splink installed:  python -m scripts.autoconfig_quality.vendor_historical_50k
The parquet keeps the `cluster` truth column; the harness loader drops it before dedupe.
"""
from __future__ import annotations

from pathlib import Path

import polars as pl

_OUT = Path(__file__).resolve().parent / "vendored" / "historical_50k.parquet"


def main() -> None:
    from splink import splink_datasets
    df = pl.from_pandas(splink_datasets.historical_50k)
    _OUT.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(_OUT)
    print(f"wrote {_OUT} ({df.height} rows, cols={df.columns})")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Confirm the vendored path is not gitignored**

Run: `git check-ignore scripts/autoconfig_quality/vendored/historical_50k.parquet`
Expected: NO output (not ignored). If it prints the path, STOP and pick another dir.

- [ ] **Step 3: Generate + commit the parquet** (needs splink locally)

```bash
PYTHONPATH="D:/show_case/gm-autoconfig-core" POLARS_SKIP_CPU_CHECK=1 \
  /d/show_case/goldenmatch/.venv/Scripts/python.exe -m scripts.autoconfig_quality.vendor_historical_50k
git add scripts/autoconfig_quality/vendored/historical_50k.parquet
```
Confirm the print lists `cluster` and `unique_id` in cols and ~50000 rows.

- [ ] **Step 4: Write the failing loader test** — append to `tests/test_datasets.py`:

```python
def test_historical_50k_loads_drops_truth_and_has_row_index_gt():
    from scripts.autoconfig_quality.datasets import _historical_50k
    loaded = _historical_50k()
    assert loaded is not None  # parquet is committed
    df, gt = loaded
    assert "cluster" not in df.columns and "unique_id" not in df.columns
    assert gt and all(0 <= a < b < df.height for a, b in gt)


def test_historical_50k_registered_full_scan():
    from scripts.autoconfig_quality.datasets import REGISTRY
    h = next(d for d in REGISTRY if d.name == "historical_50k")
    assert h.full_scan is True
```

- [ ] **Step 5: Run to verify it fails** → FAIL (`_historical_50k` not defined).

- [ ] **Step 6: Implement the loader + register** — in `datasets.py`:

```python
_VENDORED = Path(__file__).resolve().parent / "vendored"


def _historical_50k() -> tuple[pl.DataFrame, set] | None:
    """Splink historical_50k from the committed parquet. The `cluster` column is the
    truth (grouped into within-cluster row-index pairs) and is dropped — along with
    the `unique_id` surrogate — from the df fed to dedupe so the kernel can't see the
    answer. None when the parquet is absent."""
    p = _VENDORED / "historical_50k.parquet"
    if not p.exists():
        return None
    df = pl.read_parquet(p)
    clusters = df["cluster"].to_list()
    by_cluster: dict[object, list[int]] = {}
    for row, cid in enumerate(clusters):
        by_cluster.setdefault(cid, []).append(row)
    gt: set[tuple[int, int]] = set()
    for members in by_cluster.values():
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                gt.add((members[i], members[j]))
    match_df = df.drop([c for c in ("cluster", "unique_id") if c in df.columns])
    return match_df, gt
```

Register (full_scan so the 50k runs un-capped):

```python
    Dataset("historical_50k", "real", _historical_50k, full_scan=True),
```

- [ ] **Step 7: Run the tests to verify they pass** + ruff.

- [ ] **Step 8: Commit**

```bash
git add scripts/autoconfig_quality/vendor_historical_50k.py scripts/autoconfig_quality/vendored/historical_50k.parquet scripts/autoconfig_quality/datasets.py scripts/autoconfig_quality/tests/test_datasets.py
git commit -m "feat(quality): vendored historical_50k corpus entry (full_scan)"
```

---

## Task 5: Attribution candidate-pair scale guard

**Files:**
- Modify: `scripts/autoconfig_quality/f1.py`
- Test: `scripts/autoconfig_quality/tests/test_f1.py`

The F1/P/R are cheap at 50k (they derive from `result.clusters`, not the candidate
set). Only `_candidate_pairs` blows up — it materializes every block's
`combinations` into a Python set. Guard it so the projected pair count is checked
*before* materializing, and the F1 floor is never at risk.

- [ ] **Step 1: Write the failing test** — append to `tests/test_f1.py`:

```python
import polars as pl
from scripts.autoconfig_quality.anchors import gen_labeled
from scripts.autoconfig_quality.f1 import evaluate_f1


def test_attribution_skips_at_scale(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_QH_ATTR_MAX_PAIRS", "1")  # force the guard
    df, gt = gen_labeled(n_entities=40, seed=7)
    out = evaluate_f1(df, gt)
    assert "f1" in out and "precision" in out and "recall" in out  # floor intact
    assert out["attribution"] == {"skipped": "scale"}  # explicit, not blocking_recall=0
```

- [ ] **Step 2: Run to verify it fails** → FAIL (guard not implemented; attribution
still returns the numeric dict).

- [ ] **Step 3: Implement the guard** — in `f1.py`, replace `_candidate_pairs` so it
returns `None` when the projected pair count exceeds the cap, and have `evaluate_f1`
record the skip. Add `import os` at the top.

```python
def _candidate_pairs(df: pl.DataFrame) -> set[tuple[int, int]] | None:
    """Post-blocking candidate set in row-index space, or None if materializing it
    would exceed GOLDENMATCH_QH_ATTR_MAX_PAIRS (default 10M) -- attribution is then
    skipped (the F1 floor never depends on this set)."""
    cap = int(os.environ.get("GOLDENMATCH_QH_ATTR_MAX_PAIRS", "10000000"))
    profiles = profile_columns(df)
    blocking = build_blocking(profiles, df, n_rows_full=df.height)
    lf = df.with_row_index("__row_id__").lazy()
    blocks: list[list[int]] = []
    projected = 0
    try:
        for b in build_blocks(lf, blocking):
            ids = b.df.collect()["__row_id__"].to_list()
            projected += len(ids) * (len(ids) - 1) // 2
            if projected > cap:
                return None
            blocks.append(ids)
    except Exception:
        return set()
    cand: set[tuple[int, int]] = set()
    for ids in blocks:
        cand.update((min(a, c), max(a, c)) for a, c in combinations(ids, 2))
    return cand
```

In `evaluate_f1`, branch on the `None` sentinel:

```python
    cand = _candidate_pairs(df)
    if cand is None:
        attr_out: dict = {"skipped": "scale"}
    else:
        attr = attribution(gt_pairs, cand, emitted)
        attr_out = {k: attr[k] for k in ("blocking_recall", "final_recall", "threshold_loss")}
    return {
        "f1": ev["f1"],
        "precision": ev["precision"],
        "recall": ev["recall"],
        "attribution": attr_out,
    }
```

- [ ] **Step 4: Run the test to verify it passes** + ruff. Also re-run the existing
`tests/test_f1.py` to confirm the happy path (numeric attribution) still works.

Run: `python -m pytest scripts/autoconfig_quality/tests/test_f1.py -q` → all PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/autoconfig_quality/f1.py scripts/autoconfig_quality/tests/test_f1.py
git commit -m "fix(quality): bound attribution candidate-pair set (skip at scale)"
```

---

## Task 6: CI wiring + README

**Files:**
- Modify: `.github/workflows/ci.yml` (the `quality_gate` job)
- Modify: `scripts/autoconfig_quality/README.md`

- [ ] **Step 1: Add the recordlinkage install** — in `ci.yml`, in the `quality_gate`
job, after `- run: uv sync --all-packages`, add a step (mirrors the
`benchmark_runner_smoke` precedent):

```yaml
      # FEBRL3's loader needs recordlinkage (ships the dataset bundled); it is not
      # a declared dep. synthetic-NCVR + historical_50k (vendored parquet) need no
      # extra install. Real-NCVR + DBLP-ACM skip-when-absent in CI (gitignored).
      - run: uv pip install recordlinkage
```

- [ ] **Step 2: Document the corpus** — in `README.md`, extend the "## Corpus"
section: list the real datasets (febrl3, ncvr_synthetic, ncvr_real, historical_50k,
dblp_acm), which gate in CI vs local-only, and add a short "reading the attribution
to nominate a lever" paragraph (blocking-recall vs final-recall vs threshold-loss
localizes a dataset's loss to a lever class). Note `--datasets`/`--fast-only` keep
the local iterate loop fast (historical_50k's full-50k F1 is the only slow entry).

- [ ] **Step 3: Validate the workflow YAML**

Run: `python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml')); print('ok')"`

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/ci.yml scripts/autoconfig_quality/README.md
git commit -m "ci(quality): install recordlinkage for FEBRL3 + corpus docs"
```

---

## Task 7: Re-bless the baseline + verify the gate is green

**Files:**
- Modify: `scripts/autoconfig_quality/baselines/scorecard.json` (re-bless)

- [ ] **Step 1: Ensure local deps** — `recordlinkage` (FEBRL3) installed (Task env).
The real-NCVR file may or may not be present; if absent, `ncvr_real` skips (expected).

- [ ] **Step 2: Re-bless under the canonical deterministic env**

```bash
PYTHONPATH="<the three paths>" GOLDENMATCH_AUTOCONFIG_MEMORY=0 POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8 \
  /d/show_case/goldenmatch/.venv/Scripts/python.exe -m scripts.autoconfig_quality bless --native 0
```

- [ ] **Step 3: Inspect the new baseline is sane** — load `baselines/scorecard.json`
and confirm: febrl3 / ncvr_synthetic / historical_50k each carry an `f1` floor;
`ncvr_real` is present (local) or in `meta.datasets_skipped` (if PII file absent);
the anchors are unchanged; `historical_50k`'s attribution is either numeric or
`{"skipped":"scale"}` (record which, with its candidate-pair/RSS/wall from the run —
the I2 measurement).

> **historical_50k local-vs-CI bless fallback:** if the full-50k dedupe OOMs or is
> impractically slow on this box, bless the lighter datasets locally, then take
> `historical_50k`'s F1 from a CI `quality_gate` run (identical deterministic
> number — same vendored parquet, memory-off, native auto-falls-back-to-python in
> CI) and write that value into the baseline. Do NOT pin a number from a
> memory-on or capped run.

- [ ] **Step 4: Verify the gate passes on the fresh baseline**

```bash
... -m scripts.autoconfig_quality gate --native 0
```
Expected: `verdict: PASS` (every real F1 == its just-blessed floor → OK; anchors
unchanged; planner_rung WARN at most).

- [ ] **Step 5: Run the full harness test suite (targeted, not xdist)**

```bash
python -m pytest scripts/autoconfig_quality/tests/ -q -p no:cacheprovider
```
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add scripts/autoconfig_quality/baselines/scorecard.json
git commit -m "feat(quality): re-bless baseline with the broadened real-dataset corpus"
```

---

## Task 8: Final review + PR

- [ ] **Step 1:** Dispatch a final READ-ONLY code reviewer over the whole branch
(`main..HEAD`), with the box constraint (no execution). Address any Critical/Important
findings (implement in-session, re-review).

- [ ] **Step 2:** Run `report` locally for the PR description scorecard; capture the
per-dataset F1 + attribution table.

- [ ] **Step 3:** Push; open the PR against `main` with the scorecard; arm
`gh pr merge 1216-style --auto` (merge queue sets strategy — no `--squash`/
`--delete-branch`). Use the `benzsevern` account (`unset GH_TOKEN`,
`gh auth switch --user benzsevern`). Then STOP — do not poll CI.

---

## Verification & sequencing notes

- **Each task is self-contained and committable.** Tasks 2-4 add one corpus entry
  family each; Task 5 is independent (the guard); Task 1 unblocks the historical_50k
  full-scan. Order 1→5→others is also valid, but 1→2→3→4→5→6→7→8 keeps the registry
  and the F1 path growing together.
- **Determinism is the whole game.** Every loader is seeded (FEBRL3 fixed by
  recordlinkage, NCVR seed 42, historical_50k the committed parquet). F1 is
  parity-identical native 0/1. Always bless memory-off + native-0.
- **No new kernel behavior.** This plan changes no auto-config decision; it only
  adds datasets + a defensive attribution guard. If any baseline F1 surprises you
  (e.g. a real dataset scores far below its published number), that's a *finding*
  to record for the lever discussion — not a bug to "fix" by changing the kernel
  inside this change.
- **YAGNI:** no DQbench (deferred), no new lever, no trend DB, no per-dataset cap
  int (the boolean `full_scan` covers the one dataset that needs it).
```
