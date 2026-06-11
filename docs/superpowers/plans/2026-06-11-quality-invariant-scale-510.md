# Quality-Invariant Scale Validation (#510) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the #510 quality-invariance ladder — a corruption knob on the synthetic generator that yields a drift-able F1 (~0.90–0.95 at the 1K oracle), an oracle-delta aggregator, parity/determinism/golden-equivalence tests, and a published `docs/quality-invariant-scale.md` report measuring pairwise/B-cubed/cluster F1 across 1K→200M.

**Architecture:** Extend the existing `scripts/quality_invariant_scale.py` harness (it already does generate → dedupe → score → JSON). Three code additions: (1) a per-field, prefix-stable corruption knob on the realistic generator so row `i`'s corruption depends only on `(seed, level, field)` and never on `n_rows` — every smaller rung is an exact prefix of every larger one; (2) JSON-friendly `golden_hash` + `clusters_signature` fields in the rung output so parity/determinism can assert byte-identity without pickling `DedupeResult`; (3) a pure-Python `scripts/qis_aggregate.py` that reads per-rung JSONs and emits the oracle-delta table + PASS/FAIL verdict. Then run the ladder and publish the report.

**Tech Stack:** Python 3.12, numpy (deterministic RNG via `SeedSequence.spawn`), polars, goldenmatch (`auto_configure_df` / `dedupe_df`), pytest. Mid-ladder rungs run on the existing `bench-quality-invariant-scale.yml` `large-new-64GB` lane; cluster rungs on the #844 GCP recipe.

**Spec:** `docs/superpowers/specs/2026-06-11-quality-invariant-scale-510-design.md`

**Worktree:** `D:\show_case\gm-510` on branch `feat/510-quality-invariant-scale`.

---

## Refinement vs spec (read first)

The spec's §4 said `run_rung` would expose the `DedupeResult` (or tests call `dedupe_df` directly) so parity/determinism can compare `result.golden`. This plan refines that to a **smaller, JSON-friendly surface**: `run_rung` gains two output fields — `golden_hash` (sha256 of the sorted golden frame) and `clusters_signature` (sha256 of the sorted membership) — which prove byte-identity exactly as well as comparing the frames, but serialize into the per-rung JSON and so also let the **subprocess** native-parity test compare across `GOLDENMATCH_NATIVE=1/0` without pickling. No conditional return type, no `DedupeResult` leak. Everything else in the spec stands.

## File structure

| File | Action | Responsibility |
|------|--------|----------------|
| `scripts/quality_invariant_scale.py` | Modify | Add `CorruptionConfig` + levels, `_corrupt_cell`, `_apply_field_corruption`, wire into `_generate_realistic` / `generate_with_gt` / `run_rung`; add `--corruption` CLI flag; add `golden_hash` + `clusters_signature` to output. |
| `scripts/qis_aggregate.py` | Create | Pure-Python oracle-delta table + PASS/FAIL verdict from a dir of per-rung JSONs. No goldenmatch/Ray import. |
| `packages/python/goldenmatch/tests/test_qis_harness.py` | Create | Corruption determinism + prefix-stability + oracle-preservation + F1-in-band (1K oracle) + native parity + run determinism + aggregator deltas. Runs in the `python` lane (no Ray). |
| `scripts/railway_qis_job.py` | Modify | Plumb `QIS_CORRUPTION` env → `--corruption`. |
| `.github/workflows/bench-quality-invariant-scale.yml` | Modify | Add `corruption` dispatch input → `--corruption`. |
| `docs/quality-invariant-scale.md` | Create | The published report (table + methodology + verdict + repro commands). |
| `docs/scale-envelope.md`, `README.md`, `packages/python/goldenmatch/README.md` | Modify | Link the report from the scale section. |

## Conventions for every test-run command

Local single-file pytest only (never the full suite locally — it OOMs the dev box, see `feedback_avoid_full_suite_oom`). Pattern:

```bash
cd D:/show_case/gm-510/packages/python/goldenmatch && \
POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8 \
PYTHONPATH="D:/show_case/gm-510/packages/python/goldenmatch" \
D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest tests/test_qis_harness.py -q
```

Running the harness CLI directly (tuning):

```bash
cd D:/show_case/gm-510 && \
POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8 \
D:/show_case/goldenmatch/.venv/Scripts/python.exe scripts/quality_invariant_scale.py --rows 1000 --corruption moderate
```

The test file imports the repo-root script by inserting `scripts/` on `sys.path` (see Task 2 Step 1). Commits use the SOP: feature branch, focused commits, `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

## Task 0: Pre-flight baseline

**Files:** none (read-only).

- [ ] **Step 1: Confirm worktree + branch**

```bash
cd D:/show_case/gm-510 && git branch --show-current
```
Expected: `feat/510-quality-invariant-scale`

- [ ] **Step 2: Baseline the existing harness once (light shape, 1K)**

```bash
cd D:/show_case/gm-510 && POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8 \
D:/show_case/goldenmatch/.venv/Scripts/python.exe scripts/quality_invariant_scale.py --rows 1000 --out /tmp/qis_baseline.json
```
Expected: JSON prints; `pairwise.f1` ≈ 0.98–0.99 (today's near-perfect realistic shape). Record the number — Task 3 must drop it into the 0.90–0.95 band. No commit.

---

## Task 1: Corruption primitives (pure, TDD)

The crux of #510: a deterministic, prefix-stable, per-field corruption that makes F1 drift-able. Pure string/RNG functions, no goldenmatch.

**Files:**
- Modify: `scripts/quality_invariant_scale.py`
- Test: `packages/python/goldenmatch/tests/test_qis_harness.py`

- [ ] **Step 1: Write the failing test for `_corrupt_cell` + `_apply_field_corruption`**

Create `packages/python/goldenmatch/tests/test_qis_harness.py`:

```python
"""#510 quality-invariant scale harness tests. Imports the repo-root script
(scripts/quality_invariant_scale.py) by path; runs in the `python` lane (no Ray)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

# Repo root is 4 parents up from this file:
# packages/python/goldenmatch/tests/<this> -> repo root.
_REPO_ROOT = Path(__file__).resolve().parents[4]
_SCRIPTS = _REPO_ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import quality_invariant_scale as qis  # noqa: E402


def test_corrupt_cell_types_are_deterministic_and_string_valued():
    # transpose (type_sel<0.25): "abcd" with pos 0 -> "bacd"
    assert qis._corrupt_cell("abcd", 0.10, 0.0) == "bacd"
    # delete (0.25<=type_sel<0.50): "abcd" pos 0 -> "bcd"
    assert qis._corrupt_cell("abcd", 0.30, 0.0) == "bcd"
    # token drop (0.50<=type_sel<0.75) on multi-token: "12 main st" drop tok 0
    out = qis._corrupt_cell("12 main st", 0.60, 0.0)
    assert out == "main st"
    # whole-field null (type_sel>=0.75) -> empty
    assert qis._corrupt_cell("abcd", 0.90, 0.5) == ""
    # empty / single-char inputs never raise
    assert qis._corrupt_cell("", 0.10, 0.0) == ""
    assert qis._corrupt_cell("x", 0.10, 0.0) in ("x", "")


def test_apply_field_corruption_prefix_stable_across_n():
    # Row i's corruption depends only on (seed, field stream), NOT on n.
    base = [f"value{i:04d}" for i in range(50)]
    ss = np.random.SeedSequence([0, 1])
    rng_small = np.random.default_rng(ss.spawn(1)[0])
    rng_big = np.random.default_rng(np.random.SeedSequence([0, 1]).spawn(1)[0])
    small = qis._apply_field_corruption(list(base), 0.5, rng_small)
    big = qis._apply_field_corruption([f"value{i:04d}" for i in range(50)], 0.5, rng_big)
    # Same stream, same length here -> identical. (Cross-n prefix stability is
    # asserted at the generator level in Task 2; the (n,3) block draw is what
    # guarantees it.)
    assert small == big
```

- [ ] **Step 2: Run it — expect failure (functions undefined)**

```bash
cd D:/show_case/gm-510/packages/python/goldenmatch && POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8 PYTHONPATH="D:/show_case/gm-510/packages/python/goldenmatch" D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest tests/test_qis_harness.py -q
```
Expected: FAIL — `AttributeError: module 'quality_invariant_scale' has no attribute '_corrupt_cell'`.

- [ ] **Step 3: Implement the corruption primitives**

In `scripts/quality_invariant_scale.py`, add `from dataclasses import dataclass` to the imports, and after the `_CITIES` block insert:

```python
@dataclass(frozen=True)
class CorruptionConfig:
    """Per-field corruption rates for the realistic generator. Each value is the
    probability that a given row's field is corrupted. Per corrupted cell, one of:
    adjacent-char transpose, single-char delete, whitespace-token drop (multi-token
    fields), or whole-field null. Field streams are independent and each row's
    decision is drawn from a fixed (n, 3) block, so corruption for row i depends
    only on (seed, level, field) — never on n_rows. That makes every smaller rung
    an EXACT prefix of every larger one, which is the precondition for attributing
    cross-rung F1 differences to scale rather than to data shape."""
    first_name: float = 0.0
    last_name: float = 0.0
    address: float = 0.0
    email: float = 0.0


# Stream order is FIXED (spawn index = position here) so each field's child RNG
# is stable regardless of which other fields are corrupted.
_CORRUPT_FIELDS = ("first_name", "last_name", "address", "email")
_CORRUPT_LEVEL_INT = {"light": 0, "moderate": 1, "hard": 2}

# Starting rates; Task 3 tunes `moderate` so the 1K oracle lands F1 ~0.90-0.95.
CORRUPTION_LEVELS: dict[str, CorruptionConfig] = {
    "light": CorruptionConfig(),  # no extra corruption beyond the 10% a->@ typo
    "moderate": CorruptionConfig(first_name=0.30, last_name=0.20, address=0.30, email=0.08),
    "hard": CorruptionConfig(first_name=0.50, last_name=0.40, address=0.50, email=0.20),
}


def _corrupt_cell(s: str, type_sel: float, pos_sel: float) -> str:
    """One deterministic corruption of a single string from two uniforms in [0,1).

    type_sel partitions the corruption kind; pos_sel picks the position. Falls
    through to a no-op when the chosen kind can't apply (e.g. token-drop on a
    single-token string) so the corruption rate is an upper bound on actual edits."""
    if not s:
        return s
    if type_sel < 0.25 and len(s) >= 2:                 # transpose adjacent chars
        i = min(int(pos_sel * (len(s) - 1)), len(s) - 2)
        return s[:i] + s[i + 1] + s[i] + s[i + 2:]
    if type_sel < 0.50 and len(s) >= 2:                 # delete one char
        i = min(int(pos_sel * len(s)), len(s) - 1)
        return s[:i] + s[i + 1:]
    if type_sel < 0.75 and " " in s:                    # drop one whitespace token
        toks = s.split(" ")
        if len(toks) >= 2:
            j = min(int(pos_sel * len(toks)), len(toks) - 1)
            return " ".join(toks[:j] + toks[j + 1:]) or s
        return s
    return ""                                            # whole-field null


def _apply_field_corruption(values: list[str], rate: float, field_rng) -> list[str]:
    """Corrupt a column of strings in place. `field_rng` is this field's own
    numpy Generator. Draws a (n, 3) block — [apply_mask, type_sel, pos_sel] per
    row — so row i's three uniforms sit at fixed flat offsets [3i, 3i+1, 3i+2];
    the first k rows of an n=k draw equal the first k rows of any larger draw
    from the same stream (prefix stability). Loops only over masked rows."""
    n = len(values)
    if rate <= 0.0 or n == 0:
        return values
    draws = field_rng.random((n, 3))
    idx = np.nonzero(draws[:, 0] < rate)[0]
    for k in idx:
        i = int(k)
        values[i] = _corrupt_cell(values[i], float(draws[i, 1]), float(draws[i, 2]))
    return values
```

- [ ] **Step 4: Run the test — expect pass**

```bash
cd D:/show_case/gm-510/packages/python/goldenmatch && POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8 PYTHONPATH="D:/show_case/gm-510/packages/python/goldenmatch" D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest tests/test_qis_harness.py -q
```
Expected: PASS (2 tests).

- [ ] **Step 5: Lint + commit**

```bash
cd D:/show_case/gm-510 && D:/show_case/goldenmatch/.venv/Scripts/python.exe -m ruff check scripts/quality_invariant_scale.py packages/python/goldenmatch/tests/test_qis_harness.py && D:/show_case/goldenmatch/.venv/Scripts/python.exe -m py_compile scripts/quality_invariant_scale.py
git add scripts/quality_invariant_scale.py packages/python/goldenmatch/tests/test_qis_harness.py
git commit -m "feat(qis): deterministic per-field corruption primitives (#510)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Wire corruption into the generator (TDD)

**Files:**
- Modify: `scripts/quality_invariant_scale.py` (`_generate_realistic`, `generate_with_gt`, `_generate_phase5` guard)
- Test: `packages/python/goldenmatch/tests/test_qis_harness.py`

- [ ] **Step 1: Write failing tests — determinism, prefix-stability, oracle preservation**

Append to `test_qis_harness.py`:

```python
def test_generate_corruption_is_deterministic():
    df1, c1 = qis.generate_with_gt(1000, seed=0, shape="realistic", corruption="moderate")
    df2, c2 = qis.generate_with_gt(1000, seed=0, shape="realistic", corruption="moderate")
    assert df1.equals(df2)
    assert (c1 == c2).all()


def test_generate_corruption_prefix_stable_across_n():
    # The scale-invariance precondition: row i is byte-identical whether the
    # dataset is 1000 rows or 5000 rows (same seed, same corruption level).
    small, cs = qis.generate_with_gt(1000, seed=0, shape="realistic", corruption="moderate")
    big, cb = qis.generate_with_gt(5000, seed=0, shape="realistic", corruption="moderate")
    assert small.equals(big.head(1000))
    assert (cs == cb[:1000]).all()


def test_generate_corruption_preserves_oracle():
    # Corruption never moves a row's ground-truth cluster id; only the displayed
    # fields change. cids must equal the light-shape cids exactly.
    _, c_light = qis.generate_with_gt(1000, seed=0, shape="realistic", corruption="light")
    _, c_mod = qis.generate_with_gt(1000, seed=0, shape="realistic", corruption="moderate")
    assert (c_light == c_mod).all()


def test_generate_light_is_unchanged_baseline():
    # `light` must equal the pre-#510 default (no extra corruption), so existing
    # callers and published light-shape numbers are untouched.
    df_default, _ = qis.generate_with_gt(1000, seed=0, shape="realistic")
    df_light, _ = qis.generate_with_gt(1000, seed=0, shape="realistic", corruption="light")
    assert df_default.equals(df_light)


def test_moderate_actually_corrupts_some_rows():
    df_light, _ = qis.generate_with_gt(1000, seed=0, shape="realistic", corruption="light")
    df_mod, _ = qis.generate_with_gt(1000, seed=0, shape="realistic", corruption="moderate")
    # At least the first_name column must differ on a meaningful fraction.
    diff = (df_light["first_name"] != df_mod["first_name"]).sum()
    assert diff > 50  # rate ~0.3 over 1000 rows; comfortably > 50
```

- [ ] **Step 2: Run — expect failure (`generate_with_gt` has no `corruption` kwarg)**

```bash
cd D:/show_case/gm-510/packages/python/goldenmatch && POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8 PYTHONPATH="D:/show_case/gm-510/packages/python/goldenmatch" D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest tests/test_qis_harness.py -q
```
Expected: FAIL — `TypeError: generate_with_gt() got an unexpected keyword argument 'corruption'`.

- [ ] **Step 3: Thread `corruption` through the generator**

In `generate_with_gt`, add the kwarg and dispatch:

```python
def generate_with_gt(n_rows: int, seed: int = 0, shape: str = "realistic",
                     corruption: str = "light"
                     ) -> tuple[pl.DataFrame, np.ndarray]:
    if corruption not in CORRUPTION_LEVELS:
        raise ValueError(f"unknown corruption {corruption!r}; expected one of "
                         f"{sorted(CORRUPTION_LEVELS)}")
    if shape == "phase5":
        if corruption != "light":
            print(f"[qis] WARNING: corruption={corruption!r} ignored for shape "
                  f"'phase5' (corruption knob applies to 'realistic' only)", flush=True)
        return _generate_phase5(n_rows, seed)
    if shape == "realistic":
        return _generate_realistic(n_rows, seed, corruption=corruption)
    raise ValueError(f"unknown shape {shape!r}; expected 'phase5' or 'realistic'")
```

(Keep the existing docstring; add a line documenting the `corruption` arg.)

In `_generate_realistic`, add the kwarg and apply corruption AFTER the canonical
rows are built but rebuild `email_rows` from the corrupted name fields. Replace
the tail of the function (from `first_with_typo = ...` through the `df = ...`
construction) with:

```python
def _generate_realistic(n_rows: int, seed: int = 0, corruption: str = "light"
                        ) -> tuple[pl.DataFrame, np.ndarray]:
    n_rows = (n_rows // ROWS_PER_CLUSTER) * ROWS_PER_CLUSTER
    n_clusters = n_rows // ROWS_PER_CLUSTER
    rng = np.random.default_rng(seed)

    # ... (canonical-field block UNCHANGED: first_canon, last_canon, street_num,
    #      street_idx, address_canon, city_canon, zip_canon, year_canon, cids,
    #      typo, first_rows, last_rows, addr_rows, city_rows, zip_rows, year_rows) ...

    # Same 'a' -> '@' typo on first_name (baseline noise, all corruption levels).
    first_with_typo = [f.replace("a", "@") if t else f for f, t in zip(first_rows, typo)]

    # #510 corruption knob (realistic only). Applied on a SEPARATE RNG derived
    # from (seed, level) so the canonical-field draws above are untouched ->
    # oracle (cids) and the un-corrupted identity are identical across levels.
    corr = CORRUPTION_LEVELS[corruption]
    if any(getattr(corr, f) > 0.0 for f in _CORRUPT_FIELDS):
        ss = np.random.SeedSequence([seed, 0xC0FFEE, _CORRUPT_LEVEL_INT[corruption]])
        streams = dict(zip(_CORRUPT_FIELDS, ss.spawn(len(_CORRUPT_FIELDS))))
        first_with_typo = _apply_field_corruption(
            first_with_typo, corr.first_name, np.random.default_rng(streams["first_name"]))
        last_rows = _apply_field_corruption(
            last_rows, corr.last_name, np.random.default_rng(streams["last_name"]))
        addr_rows = _apply_field_corruption(
            addr_rows, corr.address, np.random.default_rng(streams["address"]))
        # Email inherits the corrupted name (realistic), THEN gets its own low-rate
        # pass — kept low so it stays a strong independent recall path.
        email_rows = [f"{f}.{l}@example.com" for f, l in zip(first_with_typo, last_rows)]
        email_rows = _apply_field_corruption(
            email_rows, corr.email, np.random.default_rng(streams["email"]))
    else:
        email_rows = [f"{f}.{l}@example.com" for f, l in zip(first_with_typo, last_rows)]

    df = pl.DataFrame({
        "id": [f"r{i}" for i in range(n_rows)],
        "first_name": first_with_typo,
        "last_name": last_rows,
        "address": addr_rows,
        "city": city_rows,
        "zip": zip_rows,
        "birth_year": year_rows,
        "email": email_rows,
    })
    return df, cids
```

Note: the existing `email_rows = [...]` line in the original function is REMOVED (it's now inside the if/else). The `df = pl.DataFrame(...)` block is otherwise identical.

- [ ] **Step 4: Run the tests — expect pass**

```bash
cd D:/show_case/gm-510/packages/python/goldenmatch && POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8 PYTHONPATH="D:/show_case/gm-510/packages/python/goldenmatch" D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest tests/test_qis_harness.py -q
```
Expected: PASS (7 tests).

- [ ] **Step 5: Lint + commit**

```bash
cd D:/show_case/gm-510 && D:/show_case/goldenmatch/.venv/Scripts/python.exe -m ruff check scripts/quality_invariant_scale.py packages/python/goldenmatch/tests/test_qis_harness.py && D:/show_case/goldenmatch/.venv/Scripts/python.exe -m py_compile scripts/quality_invariant_scale.py
git add scripts/quality_invariant_scale.py packages/python/goldenmatch/tests/test_qis_harness.py
git commit -m "feat(qis): wire prefix-stable corruption into the realistic generator (#510)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: `--corruption` CLI flag + tune `moderate` to the 0.90–0.95 band

**Files:**
- Modify: `scripts/quality_invariant_scale.py` (`run_rung` signature, `main` argparse)
- Test: `packages/python/goldenmatch/tests/test_qis_harness.py`

- [ ] **Step 1: Add `corruption` to `run_rung` + the `--corruption` CLI flag**

In `run_rung`, add `corruption: str = "light"` to the signature and pass it to
`generate_with_gt`:

```python
def run_rung(n_rows: int, seed: int = 0, shape: str = "realistic",
             backend: str | None = None, corruption: str = "light") -> dict:
    ...
    df, gt = generate_with_gt(n_rows, seed=seed, shape=shape, corruption=corruption)
    ...
```

Add `"corruption": corruption` to the returned dict (next to `"rows"`).

In `main`, add the flag and forward it:

```python
ap.add_argument("--corruption", choices=tuple(("light", "moderate", "hard")),
                default="light",
                help="realistic-shape corruption level. light = today's baseline "
                     "(10%% a->@ typo only); moderate ~ F1 0.90-0.95 (drift-sensitive, "
                     "the published ladder default); hard = stress. Ignored for --shape phase5.")
...
res = run_rung(args.rows, seed=args.seed, shape=args.shape, backend=args.backend,
               corruption=args.corruption)
res["shape"] = args.shape
res["backend"] = args.backend or "auto"
res["corruption"] = args.corruption
```

- [ ] **Step 2: Tune `moderate` — run the 1K oracle and read F1**

```bash
cd D:/show_case/gm-510 && POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8 \
D:/show_case/goldenmatch/.venv/Scripts/python.exe scripts/quality_invariant_scale.py --rows 1000 --corruption moderate
```
Read `pairwise.f1`. Target: **0.90 ≤ f1 ≤ 0.95**.
- If f1 > 0.95 (corruption too weak): raise rates in `CORRUPTION_LEVELS["moderate"]` (e.g. first_name 0.30→0.40, address 0.30→0.40) and re-run.
- If f1 < 0.90 (too strong / recall collapsing): lower rates (especially `email` and `last_name`, the recall paths) and re-run.
Iterate until in band. Record the final rates and the landed f1 — they go in the report (Task 8). Also confirm `cluster.f1` is non-degenerate (> 0.5); if cluster F1 collapses while pairwise holds, blocking is missing whole clusters → lower rates.

- [ ] **Step 3: Write the F1-in-band regression test (locks the tuning)**

Append to `test_qis_harness.py`:

```python
@pytest.mark.slow
def test_moderate_oracle_f1_in_target_band():
    # The tuning gate: `moderate` must land the 1K oracle in the drift-sensitive
    # 0.90-0.95 band (with a small tolerance so CI native/py float jitter and
    # platform RNG don't flake). If this fails after a deliberate rate change,
    # re-tune AND update the report.
    out = qis.run_rung(1000, seed=0, shape="realistic", corruption="moderate")
    f1 = out["pairwise"]["f1"]
    assert 0.88 <= f1 <= 0.96, f"moderate 1K pairwise F1 out of band: {f1:.4f}"
    assert out["cluster"]["f1"] > 0.5, f"cluster F1 degenerate: {out['cluster']['f1']:.4f}"
    assert out["corruption"] == "moderate"
```

- [ ] **Step 4: Run the new test — expect pass**

```bash
cd D:/show_case/gm-510/packages/python/goldenmatch && POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8 PYTHONPATH="D:/show_case/gm-510/packages/python/goldenmatch" D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest tests/test_qis_harness.py::test_moderate_oracle_f1_in_target_band -q
```
Expected: PASS. (If FAIL, return to Step 2 and re-tune.)

- [ ] **Step 5: Lint + commit**

```bash
cd D:/show_case/gm-510 && D:/show_case/goldenmatch/.venv/Scripts/python.exe -m ruff check scripts/quality_invariant_scale.py packages/python/goldenmatch/tests/test_qis_harness.py && D:/show_case/goldenmatch/.venv/Scripts/python.exe -m py_compile scripts/quality_invariant_scale.py
git add scripts/quality_invariant_scale.py packages/python/goldenmatch/tests/test_qis_harness.py
git commit -m "feat(qis): --corruption CLI flag; tune moderate to the 0.90-0.95 band (#510)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: `golden_hash` + `clusters_signature` for parity/determinism (TDD)

JSON-friendly byte-identity witnesses so the parity (subprocess) and determinism (in-process) tests can compare without pickling `DedupeResult`.

**Files:**
- Modify: `scripts/quality_invariant_scale.py` (`run_rung` output)
- Test: `packages/python/goldenmatch/tests/test_qis_harness.py`

- [ ] **Step 1: Write the failing determinism + golden-equivalence test**

Append to `test_qis_harness.py`:

```python
@pytest.mark.slow
def test_qis_run_determinism_and_golden_equivalence():
    # Same (seed, corruption) -> identical metrics AND byte-identical golden +
    # cluster membership across two independent runs. This is #510's
    # "deterministic clustering" + "golden-record equivalence" check.
    a = qis.run_rung(1000, seed=0, shape="realistic", corruption="moderate")
    b = qis.run_rung(1000, seed=0, shape="realistic", corruption="moderate")
    assert a["pairwise"] == b["pairwise"]
    assert a["b_cubed"] == b["b_cubed"]
    assert a["cluster"] == b["cluster"]
    assert a["golden_hash"] is not None
    assert a["golden_hash"] == b["golden_hash"]
    assert a["clusters_signature"] == b["clusters_signature"]
```

- [ ] **Step 2: Run — expect failure (`KeyError: 'golden_hash'`)**

```bash
cd D:/show_case/gm-510/packages/python/goldenmatch && POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8 PYTHONPATH="D:/show_case/gm-510/packages/python/goldenmatch" D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest tests/test_qis_harness.py::test_qis_run_determinism_and_golden_equivalence -q
```
Expected: FAIL — `KeyError: 'golden_hash'`.

- [ ] **Step 3: Implement the two hashes**

Add module-level helpers in `scripts/quality_invariant_scale.py` (near `score_quality`):

```python
def _golden_hash(golden) -> str | None:
    """sha256 of the golden frame sorted by all columns. Byte-identity witness
    for backend-parity / determinism without pickling the DedupeResult."""
    if golden is None:
        return None
    import hashlib
    g = golden.sort(by=golden.columns)
    return hashlib.sha256(g.write_csv().encode("utf-8")).hexdigest()


def _clusters_signature(predicted_members: dict[int, list[int]]) -> str:
    """sha256 over the sorted set of sorted member tuples — label-independent
    cluster-membership identity (cluster_id values can differ; the partition
    can't)."""
    import hashlib
    canon = sorted(tuple(sorted(int(m) for m in members))
                   for members in predicted_members.values())
    return hashlib.sha256(repr(canon).encode("utf-8")).hexdigest()
```

In `run_rung`, after `metrics = score_quality(predicted, gt)`, compute the
signatures and add them to the returned dict:

```python
    golden_hash = _golden_hash(getattr(result, "golden", None))
    clusters_sig = _clusters_signature(predicted)
    ...
    return {
        "rows": len(df),
        "corruption": corruption,
        ...
        "golden_hash": golden_hash,
        "clusters_signature": clusters_sig,
        "bench": bench_dict,
        "native": native_info,
    }
```

- [ ] **Step 4: Run — expect pass**

```bash
cd D:/show_case/gm-510/packages/python/goldenmatch && POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8 PYTHONPATH="D:/show_case/gm-510/packages/python/goldenmatch" D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest tests/test_qis_harness.py::test_qis_run_determinism_and_golden_equivalence -q
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd D:/show_case/gm-510 && D:/show_case/goldenmatch/.venv/Scripts/python.exe -m ruff check scripts/quality_invariant_scale.py packages/python/goldenmatch/tests/test_qis_harness.py && D:/show_case/goldenmatch/.venv/Scripts/python.exe -m py_compile scripts/quality_invariant_scale.py
git add scripts/quality_invariant_scale.py packages/python/goldenmatch/tests/test_qis_harness.py
git commit -m "feat(qis): golden_hash + clusters_signature; determinism+golden-equivalence test (#510)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: Native vs pure-Python parity test (subprocess)

**Files:**
- Test: `packages/python/goldenmatch/tests/test_qis_harness.py`

- [ ] **Step 1: Write the parity test**

Append to `test_qis_harness.py`:

```python
import json
import os
import subprocess


def _run_harness_subprocess(native_env: str, tmp_path) -> dict:
    out = tmp_path / f"native_{native_env}.json"
    env = dict(os.environ)
    env["GOLDENMATCH_NATIVE"] = native_env
    env["POLARS_SKIP_CPU_CHECK"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    script = _SCRIPTS / "quality_invariant_scale.py"
    subprocess.run(
        [sys.executable, str(script), "--rows", "1000", "--corruption", "moderate",
         "--out", str(out)],
        check=True, env=env, cwd=str(_REPO_ROOT),
    )
    return json.loads(out.read_text(encoding="utf-8"))


@pytest.mark.slow
def test_qis_native_parity(tmp_path):
    # native==pure-Python must produce identical clusters, equal F1, and
    # byte-identical golden. Parity is scale-independent, so 1K suffices.
    from goldenmatch.core._native_loader import native_available

    py = _run_harness_subprocess("0", tmp_path)
    # Witness: the pure-Python run must report native OFF.
    assert py["native"]["available"] in (False, True)  # field present
    if not native_available():
        pytest.skip("native kernel unavailable in this env; pure-Python witness asserted")
    nat = _run_harness_subprocess("1", tmp_path)
    assert nat["native"]["available"] is True
    assert py["pairwise"]["f1"] == pytest.approx(nat["pairwise"]["f1"], abs=1e-9)
    assert py["clusters_signature"] == nat["clusters_signature"]
    assert py["golden_hash"] == nat["golden_hash"]
```

- [ ] **Step 2: Run — expect pass or skip**

```bash
cd D:/show_case/gm-510/packages/python/goldenmatch && POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8 PYTHONPATH="D:/show_case/gm-510/packages/python/goldenmatch" D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest tests/test_qis_harness.py::test_qis_native_parity -q
```
Expected: PASS (if native built locally) or SKIP with the witness asserted. On Windows dev box native is usually in-tree-built; if the `=1` subprocess errors because the kernel isn't importable, the `native_available()` guard skips cleanly.

- [ ] **Step 3: Commit**

```bash
cd D:/show_case/gm-510 && D:/show_case/goldenmatch/.venv/Scripts/python.exe -m ruff check packages/python/goldenmatch/tests/test_qis_harness.py
git add packages/python/goldenmatch/tests/test_qis_harness.py
git commit -m "test(qis): native vs pure-Python parity via subprocess (#510)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6: Oracle-delta aggregator (TDD)

**Files:**
- Create: `scripts/qis_aggregate.py`
- Test: `packages/python/goldenmatch/tests/test_qis_harness.py`

- [ ] **Step 1: Write the failing aggregator test (pure, synthetic JSON)**

Append to `test_qis_harness.py`:

```python
def _load_aggregate():
    import qis_aggregate  # on sys.path via _SCRIPTS
    return qis_aggregate


def test_aggregate_oracle_deltas_and_verdict():
    agg = _load_aggregate()
    rungs = [
        {"rows": 1000, "corruption": "moderate",
         "pairwise": {"f1": 0.920}, "b_cubed": {"f1": 0.930}, "cluster": {"f1": 0.700},
         "wall_s": {"total": 1.0}, "rss_mb_peak": 100.0,
         "predicted_clusters": 200, "multi_member_clusters": 180,
         "bench": {"scored_pair_count": 500}},
        {"rows": 1000000, "corruption": "moderate",
         "pairwise": {"f1": 0.918}, "b_cubed": {"f1": 0.929}, "cluster": {"f1": 0.695},
         "wall_s": {"total": 40.0}, "rss_mb_peak": 4000.0,
         "predicted_clusters": 200000, "multi_member_clusters": 180000,
         "bench": {"scored_pair_count": 500000}},
    ]
    report = agg.build_report(rungs)
    assert report["oracle_rows"] == 1000
    # 1M rung deltas vs the 1K oracle, all within targets -> PASS
    row_1m = next(r for r in report["rows"] if r["rows"] == 1000000)
    assert abs(row_1m["pairwise_delta"]) == pytest.approx(0.002, abs=1e-9)
    assert row_1m["passed"] is True
    assert report["verdict_passed"] is True
    assert "| rows |" in report["markdown"].lower()


def test_aggregate_flags_drift_as_fail():
    agg = _load_aggregate()
    rungs = [
        {"rows": 1000, "pairwise": {"f1": 0.920}, "b_cubed": {"f1": 0.930},
         "cluster": {"f1": 0.700}, "wall_s": {"total": 1.0}, "rss_mb_peak": 1.0,
         "predicted_clusters": 1, "multi_member_clusters": 1, "bench": {}},
        {"rows": 100000000, "pairwise": {"f1": 0.800}, "b_cubed": {"f1": 0.900},
         "cluster": {"f1": 0.690}, "wall_s": {"total": 500.0}, "rss_mb_peak": 1.0,
         "predicted_clusters": 1, "multi_member_clusters": 1, "bench": {}},
    ]
    report = agg.build_report(rungs)
    row = next(r for r in report["rows"] if r["rows"] == 100000000)
    assert row["passed"] is False               # pairwise delta 0.12 > 0.005
    assert report["verdict_passed"] is False
```

- [ ] **Step 2: Run — expect failure (no module)**

```bash
cd D:/show_case/gm-510/packages/python/goldenmatch && POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8 PYTHONPATH="D:/show_case/gm-510/packages/python/goldenmatch" D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest "tests/test_qis_harness.py::test_aggregate_oracle_deltas_and_verdict" -q
```
Expected: FAIL — `ModuleNotFoundError: No module named 'qis_aggregate'`.

- [ ] **Step 3: Implement `scripts/qis_aggregate.py`**

```python
#!/usr/bin/env python3
"""#510 oracle-delta aggregator: read a directory of per-rung JSONs (emitted by
quality_invariant_scale.py), compute each rung's F1 deltas vs the smallest-N
oracle rung, flag PASS/FAIL against the #510 targets, and emit a Markdown table
plus a one-line verdict. Pure Python — no goldenmatch, no Ray.

    python scripts/qis_aggregate.py results_dir/ --out docs/quality-invariant-scale-table.md
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

# #510 pass targets (deltas vs the oracle rung).
TARGET_PAIRWISE = 0.005
TARGET_B_CUBED = 0.005
TARGET_CLUSTER = 0.010


def _f1(rung: dict, metric: str) -> float:
    return float(rung.get(metric, {}).get("f1", 0.0))


def build_report(rungs: list[dict]) -> dict:
    """Compute oracle deltas + verdict. `rungs` is a list of per-rung dicts.
    Oracle = the rung with the smallest `rows`."""
    rungs = sorted(rungs, key=lambda r: r.get("rows", 0))
    if not rungs:
        return {"rows": [], "markdown": "(no rungs)", "verdict_passed": True,
                "oracle_rows": None}
    oracle = rungs[0]
    o_pw, o_bc, o_cl = _f1(oracle, "pairwise"), _f1(oracle, "b_cubed"), _f1(oracle, "cluster")

    out_rows = []
    all_pass = True
    for r in rungs:
        d_pw = _f1(r, "pairwise") - o_pw
        d_bc = _f1(r, "b_cubed") - o_bc
        d_cl = _f1(r, "cluster") - o_cl
        passed = (abs(d_pw) <= TARGET_PAIRWISE and abs(d_bc) <= TARGET_B_CUBED
                  and abs(d_cl) <= TARGET_CLUSTER)
        # The oracle compares to itself (deltas 0) -> always PASS; keep it in.
        all_pass = all_pass and passed
        out_rows.append({
            "rows": r.get("rows"),
            "pairwise_f1": _f1(r, "pairwise"), "pairwise_delta": d_pw,
            "b_cubed_f1": _f1(r, "b_cubed"), "b_cubed_delta": d_bc,
            "cluster_f1": _f1(r, "cluster"), "cluster_delta": d_cl,
            "wall_s": r.get("wall_s", {}).get("total"),
            "rss_mb_peak": r.get("rss_mb_peak"),
            "scored_pairs": r.get("bench", {}).get("scored_pair_count"),
            "predicted_clusters": r.get("predicted_clusters"),
            "multi_member": r.get("multi_member_clusters"),
            "backend": r.get("backend", "auto"),
            "native": (r.get("native") or {}).get("available"),
            "passed": passed,
        })

    return {
        "oracle_rows": oracle.get("rows"),
        "rows": out_rows,
        "verdict_passed": all_pass,
        "markdown": _render_markdown(out_rows, oracle.get("rows"), all_pass),
    }


def _fmt(x, nd=4):
    if x is None:
        return ""
    if isinstance(x, float):
        return f"{x:.{nd}f}"
    return str(x)


def _render_markdown(rows: list[dict], oracle_rows, all_pass: bool) -> str:
    header = ("| rows | pairwise F1 | Δpw | B-cubed F1 | Δbc | cluster F1 | Δcl | "
              "wall s | RSS MB | scored pairs | pred clusters | multi | backend | native | PASS |")
    sep = "|" + "|".join(["---"] * 15) + "|"
    lines = [header, sep]
    for r in rows:
        lines.append("| " + " | ".join([
            f"{r['rows']:,}",
            _fmt(r["pairwise_f1"]), _fmt(r["pairwise_delta"]),
            _fmt(r["b_cubed_f1"]), _fmt(r["b_cubed_delta"]),
            _fmt(r["cluster_f1"]), _fmt(r["cluster_delta"]),
            _fmt(r["wall_s"], 1), _fmt(r["rss_mb_peak"], 1),
            _fmt(r["scored_pairs"]), _fmt(r["predicted_clusters"]),
            _fmt(r["multi_member"]), _fmt(r["backend"]), _fmt(r["native"]),
            "✅" if r["passed"] else "❌",
        ]) + " |")
    verdict = ("**VERDICT: PASS** — quality is invariant across the ladder "
               f"(oracle = {oracle_rows:,} rows; targets Δpairwise≤{TARGET_PAIRWISE}, "
               f"Δb-cubed≤{TARGET_B_CUBED}, Δcluster≤{TARGET_CLUSTER})."
               if all_pass else
               "**VERDICT: FAIL** — at least one rung drifted beyond target; see ❌ rows.")
    return "\n".join(lines) + "\n\n" + verdict + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("results_dir", type=Path, help="directory of per-rung *.json")
    ap.add_argument("--out", type=Path, default=None, help="write the Markdown table here")
    args = ap.parse_args(argv)
    rungs = [json.loads(p.read_text(encoding="utf-8"))
             for p in sorted(args.results_dir.glob("*.json"))]
    report = build_report(rungs)
    print(report["markdown"])
    if args.out:
        args.out.write_text(report["markdown"], encoding="utf-8")
    return 0 if report["verdict_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the aggregator tests — expect pass**

```bash
cd D:/show_case/gm-510/packages/python/goldenmatch && POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8 PYTHONPATH="D:/show_case/gm-510/packages/python/goldenmatch" D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest "tests/test_qis_harness.py::test_aggregate_oracle_deltas_and_verdict" "tests/test_qis_harness.py::test_aggregate_flags_drift_as_fail" -q
```
Expected: PASS (2 tests).

- [ ] **Step 5: Lint + commit**

```bash
cd D:/show_case/gm-510 && D:/show_case/goldenmatch/.venv/Scripts/python.exe -m ruff check scripts/qis_aggregate.py packages/python/goldenmatch/tests/test_qis_harness.py && D:/show_case/goldenmatch/.venv/Scripts/python.exe -m py_compile scripts/qis_aggregate.py
git add scripts/qis_aggregate.py packages/python/goldenmatch/tests/test_qis_harness.py
git commit -m "feat(qis): oracle-delta aggregator with PASS/FAIL verdict (#510)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 7: Plumb `--corruption` through Railway runner + workflow

**Files:**
- Modify: `scripts/railway_qis_job.py`
- Modify: `.github/workflows/bench-quality-invariant-scale.yml`

- [ ] **Step 1: Railway runner — read `QIS_CORRUPTION`**

In `scripts/railway_qis_job.py`, after `backend = ...`:

```python
    corruption = os.environ.get("QIS_CORRUPTION", "light").strip() or "light"
    ...
    cmd = [sys.executable, "scripts/quality_invariant_scale.py",
           "--rows", rows, "--shape", shape, "--seed", seed, "--corruption", corruption]
    if backend:
        cmd += ["--backend", backend]
```
Update the module docstring's env list to include `QIS_CORRUPTION  light | moderate | hard (default light)`.

- [ ] **Step 2: Workflow — add a `corruption` dispatch input + env wire**

In `.github/workflows/bench-quality-invariant-scale.yml`, add to `inputs:`:

```yaml
      corruption:
        description: "realistic-shape corruption: light | moderate | hard (default moderate for the #510 ladder)"
        required: false
        default: "moderate"
```
And wire it into the harness invocation. The invocation is a bash heredoc `run:`
block (grep for `quality_invariant_scale.py` — it builds a `$BACKEND_FLAG` var and
calls the script across continuation lines, ~lines 172-178). Add `--corruption`
as its own continuation line in that command; inline interpolation is safe here
because `corruption` has a non-blank default (unlike `backend`, which is why that
one uses a `$BACKEND_FLAG` guard). For example, change the invocation tail to:

```bash
          python scripts/quality_invariant_scale.py \
            --rows "${{ inputs.rows }}" \
            --shape "${{ inputs.shape }}" \
            --seed "${{ inputs.seed }}" \
            --corruption "${{ inputs.corruption || 'moderate' }}" \
            $BACKEND_FLAG \
            --out "qis_${{ inputs.label }}.json"
```
(Match the exact existing flag/line layout — the point is the new `--corruption`
continuation line, not a wholesale rewrite of the block.)

- [ ] **Step 3: Validate workflow YAML**

```bash
cd D:/show_case/gm-510 && D:/show_case/goldenmatch/.venv/Scripts/python.exe -c "import yaml,sys; yaml.safe_load(open('.github/workflows/bench-quality-invariant-scale.yml')); print('yaml ok')"
```
Expected: `yaml ok`. Also `py_compile` the Railway runner:
```bash
cd D:/show_case/gm-510 && D:/show_case/goldenmatch/.venv/Scripts/python.exe -m py_compile scripts/railway_qis_job.py
```

- [ ] **Step 4: Commit**

```bash
cd D:/show_case/gm-510 && git add scripts/railway_qis_job.py .github/workflows/bench-quality-invariant-scale.yml
git commit -m "feat(qis): plumb --corruption through Railway runner + bench workflow (#510)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 8: Run the ladder + publish the report

This task is execution + writing, not TDD. Collect per-rung JSONs into a results
dir, run the aggregator, paste the table into the report.

**Files:**
- Create: `docs/quality-invariant-scale.md`
- (Produces, not committed to the package: per-rung JSON artifacts)

- [ ] **Step 1: Run the local rungs (1K, 10K, 1M) on the dev box**

```bash
cd D:/show_case/gm-510 && mkdir -p /tmp/qis_results
for N in 1000 10000 1000000; do \
  POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8 GOLDENMATCH_NATIVE=1 \
  D:/show_case/goldenmatch/.venv/Scripts/python.exe scripts/quality_invariant_scale.py \
    --rows $N --corruption moderate --backend bucket --out /tmp/qis_results/rung_${N}.json; done
```
1M may take a few minutes. If the dev box struggles at 1M, drop it to the workflow lane (Step 2). Confirm each JSON has `pairwise.f1` near the oracle.

- [ ] **Step 2: Dispatch the 10M + 25M rungs on the `large-new-64GB` lane**

Requires the branch pushed (auth dance: `benzsevern` for `benseverndev-oss`,
switch back to `benzsevern-mjh` after). The workflow reads the harness from the
dispatched ref.

```bash
cd D:/show_case/gm-510 && GH_TOKEN=$(gh auth token --user benzsevern) \
gh workflow run bench-quality-invariant-scale.yml --repo benseverndev-oss/goldenmatch \
  --ref feat/510-quality-invariant-scale \
  -f rows=10000000 -f corruption=moderate -f backend=bucket -f label=10m-moderate
GH_TOKEN=$(gh auth token --user benzsevern) \
gh workflow run bench-quality-invariant-scale.yml --repo benseverndev-oss/goldenmatch \
  --ref feat/510-quality-invariant-scale \
  -f rows=25000000 -f corruption=moderate -f backend=duckdb -f label=25m-moderate
```
Poll the runs; download the JSON artifacts into `/tmp/qis_results/`. (25M on the realistic shape is heavier than the published bench-dataset-v1 25M; give it the 5h cap the workflow already sets. If duckdb 25M is too slow, fall back to `backend=bucket` on the 64GB box.)

- [ ] **Step 3: Run the cluster rungs (50M, 100M, optional 200M) via the #844 GCP recipe**

Use the `project_scale_driver_bottleneck` GCP recipe (e2-standard-16, manual Ray
cluster, `GOLDENMATCH_DISTRIBUTED_PIPELINE=2` + `BLOCK_SHUFFLE=1` +
`WCC_SCRATCH=gs://…`). **Size from 50M first** (Risk: realistic+fuzzy is heavier
than #844's exact-last_name cliques — measure, don't assume). Run 50M, read wall,
extrapolate, then 100M; run 200M only if it fits a sane window — otherwise drop it
loudly in the report (the spec sanctions this). Land each rung's JSON in
`/tmp/qis_results/`. Tear the cluster down after (cost discipline).

- [ ] **Step 4: Aggregate**

```bash
cd D:/show_case/gm-510 && POLARS_SKIP_CPU_CHECK=1 \
D:/show_case/goldenmatch/.venv/Scripts/python.exe scripts/qis_aggregate.py /tmp/qis_results \
  --out /tmp/qis_table.md
cat /tmp/qis_table.md
```
Expected: the rung×metrics table + verdict line.

- [ ] **Step 5: Write `docs/quality-invariant-scale.md`**

Structure (fill the table from Step 4, the methodology from the spec):
- **Thesis** — quality (not just throughput) is invariant across scale; this is the F1 ladder vs a 1K oracle.
- **Methodology** — realistic generator shape; the corruption knob (final `moderate` rates + the landed 1K F1 from Task 3); per-rung auto-config (committed-config captured); metric definitions (pairwise / B-cubed / cluster F1, oracle-delta); the in-house-embedder DEFERRAL (per spec); native-on witness.
- **Results table** — paste Step 4's Markdown.
- **Verdict** — PASS/FAIL line; call out any dropped rung (e.g. 200M) with the reason.
- **Reproduction** — the exact commands: local rungs, `gh workflow run` for 10M/25M, the GCP recipe pointer for 50M+, and `qis_aggregate.py`.

- [ ] **Step 6: Link the report from scale docs + READMEs**

Add a one-line link in `docs/scale-envelope.md` (near the throughput-envelope
table: "Quality across the same ladder: see [quality-invariant-scale.md](quality-invariant-scale.md)"),
and one line each in `README.md` and `packages/python/goldenmatch/README.md` in
their scale sections. Keep it ASCII, no em-dashes (`gh release`/README convention).

- [ ] **Step 7: Commit**

```bash
cd D:/show_case/gm-510 && git add docs/quality-invariant-scale.md docs/scale-envelope.md README.md packages/python/goldenmatch/README.md
git commit -m "docs(qis): publish the quality-invariant scale report + ladder verdict (#510)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 9: Full-file test pass + PR

**Files:** none (CI + PR).

- [ ] **Step 1: Run the whole harness test file once locally**

```bash
cd D:/show_case/gm-510/packages/python/goldenmatch && POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8 PYTHONPATH="D:/show_case/gm-510/packages/python/goldenmatch" D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest tests/test_qis_harness.py -q
```
Expected: all green (slow tests included). If a `@pytest.mark.slow` marker triggers an unknown-marker warning, register it in the package `pytest.ini` / `pyproject.toml` `[tool.pytest.ini_options] markers` (grep for an existing `markers =` block first; add `slow: longer in-process dedupe runs` only if not already present).

- [ ] **Step 2: Push + open the PR (auth dance)**

```bash
cd D:/show_case/gm-510 && gh auth switch --user benzsevern >/dev/null 2>&1
git push -u origin feat/510-quality-invariant-scale 2>&1 | tail -3
GH_TOKEN=$(gh auth token --user benzsevern) gh pr create --repo benseverndev-oss/goldenmatch \
  --base main --head feat/510-quality-invariant-scale \
  --title "feat(qis): #510 quality-invariant scale ladder + published report" \
  --body "Closes #510. Corruption knob (drift-able F1), oracle-delta aggregator, parity/determinism/golden-equivalence tests, and docs/quality-invariant-scale.md with the 1K->[…] ladder verdict. Per spec docs/superpowers/specs/2026-06-11-quality-invariant-scale-510-design.md."
gh auth switch --user benzsevern-mjh >/dev/null 2>&1
```

- [ ] **Step 3: Babysit CI to green, merge per SOP**

Poll `gh pr checks`, arm auto-merge (`--squash --auto --delete-branch`), update-branch as main advances (strict up-to-date). Merge on green per `feedback_branch_merge_sop` standing authorization.

---

## Risks & mitigations (carried from the spec)

- **Corruption too strong/weak** → Task 3's tuning gate + the in-band test catch it before any cluster spend. Tune on the 1K oracle locally first.
- **Cluster-rung cost/feasibility** (50M/100M heavier than #844's exact cliques) → size from 50M, extrapolate; 200M is the droppable stretch.
- **Generation memory at 100M** — the `(n,3)` corruption draw is a transient ~2.4GB at 100M on top of the already-O(n) Python generation; cluster rungs run on the 64GB+ box so this is in budget. Note it in the report's methodology.
- **Auto-config instability at scale** masquerading as quality drift → `committed_config` is captured per rung; report it rather than hide it.
- **`slow` marker / CI lane time** — the in-process slow tests run a 1K dedupe (~seconds). If the `python` lane budget is tight, they can be gated behind `-m "not slow"` in CI and run in a dedicated step; default is to let them run (they're cheap at 1K).
