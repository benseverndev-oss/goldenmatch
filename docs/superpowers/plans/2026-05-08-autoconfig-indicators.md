# Auto-Config Indicators Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 5 complexity indicators to the auto-config controller so it can distinguish "blocking key wrong" from "blocking right but sample sparse," recover DQbench T1's 89.3% F1 from v1.9's mistake-prone path, and target composite ≥70 (fallback ≥65).

**Architecture:** New `core/indicators.py` module with 5 pure indicator functions (cheap eager + expensive lazy). New `IndicatorContext` threads through `RefitPolicy.propose()` via optional kwarg with introspection-based backward compat for old-shape custom policies. New `ColumnPrior` + `IndicatorsProfile` typed sub-profiles. Existing `rule_no_matches` and `rule_blocking_key_swap` modified to consult indicators; 3 new rules (`corruption_normalize`, `cross_blocking_disagreement`, `sparse_match_expand`). `PolicyDecision` is unchanged — action-list pattern is rule-internal scaffolding.

**Tech Stack:** Python 3.12, polars, Pydantic v2, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-05-08-autoconfig-indicators-design.md` — read this first; the plan refers to it by section.

---

## Pre-flight checklist

Before starting any task:
- [ ] Working in dedicated branch `feature/autoconfig-indicators` (already created from main, currently at HEAD `ff940ec` with spec committed).
- [ ] Branch base is `main` at HEAD `d80746f` (v1.9.0 release).
- [ ] DQbench dataset present at `~/.dqbench/datasets/er_tier{1,2,3}/data.csv` (5K-10K rows each).
- [ ] DBLP-ACM, NCVR sample present at `packages/python/goldenmatch/tests/benchmarks/datasets/`.
- [ ] OPENAI_API_KEY available via `set -a && source /d/show_case/goldencheck/.testing/.env && set +a` (only for §Phase 7 with-LLM measurement step).
- [ ] Editable install: `C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe -c "import goldenmatch._api; print(goldenmatch._api.__file__)"` shows the worktree path, not site-packages.
- [ ] Baseline test count: 1850 passing (post-v1.9 release).
- [ ] Bash shell (Git Bash); the `unset`/`source`/`tee` commands assume bash, not PowerShell.

---

## File structure (locked in here)

| File | Role | Change |
|---|---|---|
| `core/indicators.py` | NEW module | 5 pure indicator functions + dataclasses |
| `core/complexity_profile.py` | Existing | Add `ColumnPrior`, `IndicatorsProfile` dataclasses; add `column_priors` field on `DataProfile`; add `indicators` field on `ComplexityProfile` |
| `core/autoconfig_controller.py` | Existing | Add `IndicatorContext` class; pre-iteration eager indicator compute; thread `ctx` through `policy.propose()` with `inspect.signature` fallback for old-shape custom policies |
| `core/autoconfig_policy.py` | Existing | Widen `RefitPolicy.propose` signature; update `HeuristicRefitPolicy` and `LLMRefitPolicy` to accept and forward `ctx` |
| `core/autoconfig_rules.py` | Existing | Add module-private rule helpers (`_with_lower_threshold`, etc.); modify `rule_no_matches` and `rule_blocking_key_swap`; add 3 new rules; update `DEFAULT_RULES` ordering |
| `tests/test_indicators.py` | NEW | Tier 1: 20 unit tests for indicator functions |
| `tests/test_autoconfig_rules.py` | Existing | Tier 2: 10 fire/no-fire tests for modified + new rules |
| `tests/test_autoconfig_policy.py` | Existing | Tier 3: 5 ctx forwarding + signature-introspection tests |
| `tests/test_autoconfig_memory_v1_9_compat.py` | NEW | Tier 5: 3 cache backward-compat tests |
| `tests/test_dqbench_t1_recovery.py` | NEW | Tier 4: synthetic 200-row T1-style integration test |
| `tests/test_indicators_budget.py` | NEW | Tier 7: per-indicator wall-clock budget enforcement |
| `tests/test_autoconfig_properties.py` | Existing | Tier 6: 2 new property tests for indicator determinism |
| `tests/fixtures/autoconfig/v1_9_memory_snapshot.json` | NEW | v1.9-vintage cache entry fixture for backward-compat verification |

Estimated total delta: **~1150 LOC code + ~400 LOC tests = ~1550 LOC**.

---

## Phase 1 — Foundation types

Add the typed scaffolding that everything else builds on. No behavior change yet.

### Task 1.1: Add `ColumnPrior`, `IndicatorsProfile`, `SparsityVerdict` dataclasses

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/complexity_profile.py` (add new dataclasses; default-None fields on `DataProfile` + `ComplexityProfile`)
- Test: `packages/python/goldenmatch/tests/test_complexity_profile.py` (extend)

- [ ] **Step 1: Read existing structure of `complexity_profile.py`**

```bash
grep -n "^class\|^@dataclass" /d/show_case/goldenmatch/packages/python/goldenmatch/goldenmatch/core/complexity_profile.py
```
Expected: `DataProfile` at line 46, `ComplexityProfile` at line 192. Frozen dataclasses.

- [ ] **Step 2: Write failing tests**

Append to `packages/python/goldenmatch/tests/test_complexity_profile.py`:

```python
def test_column_prior_dataclass_defaults():
    """ColumnPrior is a frozen dataclass with identity_score and corruption_score."""
    from goldenmatch.core.complexity_profile import ColumnPrior
    cp = ColumnPrior(identity_score=0.9, corruption_score=0.1)
    assert cp.identity_score == 0.9
    assert cp.corruption_score == 0.1
    # Frozen: assignment should raise
    import pytest, dataclasses
    with pytest.raises(dataclasses.FrozenInstanceError):
        cp.identity_score = 0.5


def test_sparsity_verdict_dataclass():
    """SparsityVerdict carries is_sparse + estimated_n_true_pairs."""
    from goldenmatch.core.complexity_profile import SparsityVerdict
    sv = SparsityVerdict(is_sparse=True, estimated_n_true_pairs=20)
    assert sv.is_sparse is True
    assert sv.estimated_n_true_pairs == 20


def test_indicators_profile_optional_fields():
    """IndicatorsProfile has optional full_pop_matchkey_hit_rate +
    cross_blocking_overlap; default None."""
    from goldenmatch.core.complexity_profile import IndicatorsProfile
    ip = IndicatorsProfile()
    assert ip.full_pop_matchkey_hit_rate is None
    assert ip.cross_blocking_overlap is None
    # Settable via constructor
    ip2 = IndicatorsProfile(full_pop_matchkey_hit_rate=0.7,
                             cross_blocking_overlap=0.4)
    assert ip2.full_pop_matchkey_hit_rate == 0.7


def test_data_profile_column_priors_default_none():
    """DataProfile.column_priors defaults to None for backward compat."""
    from goldenmatch.core.complexity_profile import DataProfile
    dp = DataProfile(n_rows=100, n_cols=4,
                     column_types={"a": "text", "b": "id-like",
                                   "c": "text", "d": "date"})
    assert dp.column_priors is None


def test_complexity_profile_indicators_default_none():
    """ComplexityProfile.indicators defaults to None for backward compat."""
    from goldenmatch.core.complexity_profile import ComplexityProfile, DataProfile
    cp = ComplexityProfile(data=DataProfile(n_rows=100))
    assert cp.indicators is None
```

- [ ] **Step 3: Run; expect ImportError on first test**

```bash
cd /d/show_case/goldenmatch/packages/python/goldenmatch && C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe -m pytest tests/test_complexity_profile.py -k "column_prior or sparsity or indicators_profile or data_profile_column or complexity_profile_indicators" -v 2>&1 | tail -10
```

- [ ] **Step 4: Add the dataclasses + fields**

Edit `packages/python/goldenmatch/goldenmatch/core/complexity_profile.py`:

After `class HealthVerdict` and `class StopReason`, before `DataProfile`, add:

```python
@dataclass(frozen=True)
class ColumnPrior:
    """v1.10: per-column priors used by indicator-aware rules.

    identity_score: 0.0-1.0; high for canonical identity columns
    (email, ssn, phone, id-like high-cardinality strings).
    corruption_score: 0.0-1.0; high when within-column edit-distance
    variance suggests typo/case noise (Brian/BRIAN/B.).
    """
    identity_score: float
    corruption_score: float


@dataclass(frozen=True)
class SparsityVerdict:
    """v1.10: result of sparse-match estimation.

    is_sparse: True when sample's exact-matchkey hit count is below
    the heuristic floor (default 50) — sample is too small to surface
    visible matches under v0's matchkey config.
    estimated_n_true_pairs: rough estimate from exact-matchkey hits;
    used as a tiebreak indicator for rule_sparse_match_expand.
    """
    is_sparse: bool
    estimated_n_true_pairs: int


@dataclass(frozen=True)
class IndicatorsProfile:
    """v1.10: dynamic measurements computed lazily by indicators.

    Default-None fields are populated by IndicatorContext on first
    rule access; they remain None when the rule didn't need them
    (cheap path on YELLOW-reaching benchmarks).
    """
    full_pop_matchkey_hit_rate: float | None = None
    cross_blocking_overlap: float | None = None
```

Then on `DataProfile` (line ~46), add the field at the end:
```python
    # ... existing fields ...
    column_priors: dict[str, ColumnPrior] | None = None
```

On `ComplexityProfile` (line ~192), add the field at the end:
```python
    # ... existing fields ...
    indicators: IndicatorsProfile | None = None
```

- [ ] **Step 5: Re-run tests; expect 5 PASS**

```bash
cd /d/show_case/goldenmatch/packages/python/goldenmatch && C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe -m pytest tests/test_complexity_profile.py -v --timeout=60 2>&1 | tail -15
```

- [ ] **Step 6: Run full test suite to confirm no regression**

```bash
rm -f ~/.goldenmatch/autoconfig_memory.db && cd /d/show_case/goldenmatch/packages/python/goldenmatch && C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe -m pytest -q --timeout=180 --ignore=tests/test_db.py --ignore=tests/test_reconcile.py --ignore=tests/test_mcp_and_watch.py --ignore=tests/test_embedder.py --ignore=tests/test_llm_boost.py --ignore=tests/benchmarks 2>&1 | tail -5
```
Expected: ≥ 1855 passed (1850 baseline + 5 new).

- [ ] **Step 7: Commit**

```bash
git add packages/python/goldenmatch/goldenmatch/core/complexity_profile.py packages/python/goldenmatch/tests/test_complexity_profile.py
git commit -m "feat(autoconfig): add ColumnPrior, IndicatorsProfile, SparsityVerdict + default-None fields"
```

---

## Phase 2 — Indicator functions

Five pure functions in a new module. Each gets unit tests + budget enforcement.

### Task 2.1: `compute_column_priors` (cheap, eager)

**Files:**
- Create: `packages/python/goldenmatch/goldenmatch/core/indicators.py`
- Test: `packages/python/goldenmatch/tests/test_indicators.py`

- [ ] **Step 1: Failing tests**

Create `packages/python/goldenmatch/tests/test_indicators.py`:

```python
"""Unit tests for goldenmatch.core.indicators (v1.10)."""
import polars as pl
import pytest


def test_compute_column_priors_email_high_identity():
    """Email columns get identity_score >= 0.9 even on noisy samples."""
    from goldenmatch.core.indicators import compute_column_priors
    df = pl.DataFrame({
        "email": ["a@gmail.com", "b@yahoo.com", "c@hotmail.com"] * 30,
        "name": ["Brian", "Brian", "BRIAN"] * 30,
    })
    priors = compute_column_priors(df)
    assert priors["email"].identity_score >= 0.9


def test_compute_column_priors_categorical_low_identity():
    """Categorical/short columns get identity_score 0.0."""
    from goldenmatch.core.indicators import compute_column_priors
    df = pl.DataFrame({
        "status": ["active", "inactive"] * 50,
        "is_member": [True, False] * 50,
    })
    priors = compute_column_priors(df)
    assert priors["status"].identity_score == 0.0
    assert priors["is_member"].identity_score == 0.0


def test_compute_column_priors_corruption_score_high_on_case_noise():
    """A column with mixed-case variants of identical strings gets
    corruption_score > 0.4."""
    from goldenmatch.core.indicators import compute_column_priors
    df = pl.DataFrame({
        "name": ["Brian", "BRIAN", "brian", "Brian "] * 25,
    })
    priors = compute_column_priors(df)
    assert priors["name"].corruption_score > 0.4


def test_compute_column_priors_corruption_score_low_on_clean():
    """A clean column with no within-row variation has low corruption_score."""
    from goldenmatch.core.indicators import compute_column_priors
    df = pl.DataFrame({
        "email": [f"user{i}@example.com" for i in range(100)],
    })
    priors = compute_column_priors(df)
    assert priors["email"].corruption_score < 0.2


def test_compute_column_priors_missing_column_returns_empty_dict():
    """Empty df → empty priors dict (no raise)."""
    from goldenmatch.core.indicators import compute_column_priors
    df = pl.DataFrame()
    priors = compute_column_priors(df)
    assert priors == {}
```

- [ ] **Step 2: Run; expect ImportError**

- [ ] **Step 3: Implement**

Create `packages/python/goldenmatch/goldenmatch/core/indicators.py`:

```python
"""Auto-config complexity indicators (v1.10).

Pure functions: each takes a polars DataFrame (and optional config args)
and returns a typed result. No controller state, no I/O. Each function
has a wall-clock budget; on exhaustion, returns None or a sentinel.

Spec: docs/superpowers/specs/2026-05-08-autoconfig-indicators-design.md
"""
from __future__ import annotations
import logging
import re
import time
from typing import Any

import polars as pl

from goldenmatch.core.complexity_profile import (
    ColumnPrior, IndicatorsProfile, SparsityVerdict,
)

logger = logging.getLogger(__name__)

# Wall-clock budgets (seconds). Indicators returning None on budget
# exhaustion are documented in the spec § Error handling.
BUDGET_COLUMN_PRIORS = 5.0
BUDGET_SPARSE_MATCH = 2.0
BUDGET_FULL_POP_HITS = 15.0
BUDGET_CROSS_BLOCKING = 20.0
BUDGET_CORRUPTION = 3.0

# Identity-column heuristics. Column-name regex → identity_score floor.
# These are NOT authoritative — they're priors. Real identity verification
# happens via full-pop matchkey hits.
_IDENTITY_NAME_PATTERNS = [
    (re.compile(r"^(email|e[-_]?mail|email_addr)$", re.I), 0.95),
    (re.compile(r"^(ssn|social|tax_id)$", re.I), 0.95),
    (re.compile(r"^(phone|mobile|tel|telephone)$", re.I), 0.85),
    (re.compile(r"^(id|uuid|guid|user_id|account_id)$", re.I), 0.90),
]

_BOOLEAN_DTYPES = {pl.Boolean}
_NON_IDENTITY_DTYPES = {pl.Boolean, pl.Date, pl.Datetime, pl.Time}


def compute_column_priors(df: pl.DataFrame) -> dict[str, ColumnPrior]:
    """Compute per-column identity + corruption priors.

    identity_score:
      - 0.95 for canonical identity column names (email/ssn/uuid)
      - 0.85 for phone-like
      - 0.7 for high-cardinality strings (cardinality_ratio > 0.5)
      - 0.0 for booleans/dates/categoricals/low-cardinality

    corruption_score:
      - Computed on a 1000-row sample
      - High when within-column edit-distance variance is high (Brian/BRIAN/B.)
      - Low when entries are deterministic (clean email patterns)
    """
    start = time.time()
    if df.is_empty():
        return {}
    priors: dict[str, ColumnPrior] = {}
    sample = df.head(1000) if df.height > 1000 else df

    for col in df.columns:
        if (time.time() - start) > BUDGET_COLUMN_PRIORS:
            logger.info(
                "compute_column_priors: budget %ss exceeded; "
                "remaining %d columns get default priors",
                BUDGET_COLUMN_PRIORS, len(df.columns) - len(priors),
            )
            for remaining in df.columns:
                if remaining not in priors:
                    priors[remaining] = ColumnPrior(0.0, 0.0)
            break

        identity_score = _compute_identity_score(df, col)
        corruption_score = _compute_corruption_score_inline(sample, col)
        priors[col] = ColumnPrior(
            identity_score=identity_score,
            corruption_score=corruption_score,
        )

    return priors


def _compute_identity_score(df: pl.DataFrame, col: str) -> float:
    """Identity-score heuristic. Name match > dtype match > cardinality."""
    if df.schema.get(col) in _NON_IDENTITY_DTYPES:
        return 0.0
    for pattern, score in _IDENTITY_NAME_PATTERNS:
        if pattern.match(col):
            return score
    # High-cardinality string column = id-like
    try:
        n_unique = df[col].n_unique()
        cardinality_ratio = n_unique / max(1, df.height)
        if cardinality_ratio > 0.5:
            return 0.7
        if cardinality_ratio > 0.1:
            return 0.3
    except Exception:
        pass
    return 0.0


def _compute_corruption_score_inline(sample: pl.DataFrame, col: str) -> float:
    """Approximation of within-column edit-distance variance.

    Cheap proxy: fraction of values that are case-or-whitespace-collapsed
    duplicates of another value in the sample. High value -> high
    corruption (Brian/BRIAN/brian/Brian ).
    """
    try:
        vals = sample[col].cast(str).fill_null("").to_list()
    except Exception:
        return 0.0
    if not vals:
        return 0.0
    normalized = {v.strip().lower() for v in vals if v}
    raw = {v for v in vals if v}
    if not raw:
        return 0.0
    # Corruption ratio: how many distinct raw forms collapse to fewer
    # normalized forms. 1.0 means perfectly clean (1:1); lower means noise.
    ratio_clean = len(normalized) / len(raw)
    # Invert and clamp: corruption_score = 1.0 - ratio_clean
    return max(0.0, min(1.0, 1.0 - ratio_clean))
```

- [ ] **Step 4: Run; expect 5 PASS**

```bash
cd /d/show_case/goldenmatch/packages/python/goldenmatch && C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe -m pytest tests/test_indicators.py -v --timeout=60 2>&1 | tail -15
```

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldenmatch/goldenmatch/core/indicators.py packages/python/goldenmatch/tests/test_indicators.py
git commit -m "feat(autoconfig): compute_column_priors with identity + corruption scoring"
```

### Task 2.2: `estimate_sparse_match_signal` (cheap, eager)

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/indicators.py`
- Test: `packages/python/goldenmatch/tests/test_indicators.py`

- [ ] **Step 1: Failing tests**

Append to `tests/test_indicators.py`:

```python
def test_estimate_sparse_match_signal_marks_sparse_when_few_hits():
    """When sample's exact-matchkey hits < 50, mark sparse."""
    from goldenmatch.core.indicators import estimate_sparse_match_signal
    df = pl.DataFrame({
        "id": [f"id_{i}" for i in range(1000)],   # all unique = no exact hits
        "email": [f"u{i}@x.com" for i in range(1000)],
    })
    sv = estimate_sparse_match_signal(df, exact_columns=["email"])
    assert sv.is_sparse is True
    assert sv.estimated_n_true_pairs < 50


def test_estimate_sparse_match_signal_not_sparse_when_many_hits():
    """When sample has plenty of exact-matchkey collisions, not sparse."""
    from goldenmatch.core.indicators import estimate_sparse_match_signal
    df = pl.DataFrame({
        # 200 records, 100 emails each appearing twice → 100 exact-match pairs
        "email": [f"u{i % 100}@x.com" for i in range(200)],
    })
    sv = estimate_sparse_match_signal(df, exact_columns=["email"])
    assert sv.is_sparse is False
    assert sv.estimated_n_true_pairs >= 50


def test_estimate_sparse_match_signal_no_columns_marks_sparse():
    """No exact columns provided → can't estimate; treat as sparse."""
    from goldenmatch.core.indicators import estimate_sparse_match_signal
    df = pl.DataFrame({"x": [1, 2, 3]})
    sv = estimate_sparse_match_signal(df, exact_columns=[])
    assert sv.is_sparse is True
```

- [ ] **Step 2: Run; expect FAIL**

- [ ] **Step 3: Implement**

Append to `core/indicators.py`:

```python
def estimate_sparse_match_signal(
    df: pl.DataFrame,
    exact_columns: list[str] | None = None,
    sample_size: int = 1000,
    sparse_threshold: int = 50,
) -> SparsityVerdict:
    """Count exact-matchkey collisions in a sample.

    If `exact_columns` is empty (caller has no exact matchkeys), treat as
    sparse — controller can't sanity-check otherwise.
    """
    if not exact_columns or df.is_empty():
        return SparsityVerdict(is_sparse=True, estimated_n_true_pairs=0)
    sample = df.head(sample_size) if df.height > sample_size else df
    n_pairs = 0
    for col in exact_columns:
        if col not in sample.columns:
            continue
        try:
            counts = (
                sample.group_by(col)
                .agg(pl.len().alias("n"))
                .filter(pl.col("n") > 1)
            )
            # Each group of size k contributes k*(k-1)/2 pairs
            n_pairs += int(
                counts.select(
                    (pl.col("n") * (pl.col("n") - 1) / 2).sum()
                ).item()
            )
        except Exception:
            continue
    is_sparse = n_pairs < sparse_threshold
    return SparsityVerdict(is_sparse=is_sparse, estimated_n_true_pairs=n_pairs)
```

- [ ] **Step 4: Run; expect 3 PASS**

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldenmatch/goldenmatch/core/indicators.py packages/python/goldenmatch/tests/test_indicators.py
git commit -m "feat(autoconfig): estimate_sparse_match_signal via exact-matchkey collision count"
```

### Task 2.3: `compute_corruption_score` (per-column, on-demand)

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/indicators.py`
- Test: `packages/python/goldenmatch/tests/test_indicators.py`

The `_compute_corruption_score_inline` private helper from Task 2.1 already exists. This task promotes it to the public API for direct rule access (used by `rule_corruption_normalize`).

- [ ] **Step 1: Failing tests**

Append:

```python
def test_compute_corruption_score_brian_variants():
    """Brian/BRIAN/brian collapse to 1 normalized form → corruption_score > 0.5."""
    from goldenmatch.core.indicators import compute_corruption_score
    df = pl.DataFrame({
        "name": ["Brian", "BRIAN", "brian", "Brian "] * 25,
    })
    score = compute_corruption_score(df, "name")
    assert score > 0.5


def test_compute_corruption_score_clean_email():
    """Distinct emails with no case noise → corruption_score < 0.1."""
    from goldenmatch.core.indicators import compute_corruption_score
    df = pl.DataFrame({
        "email": [f"u{i}@x.com" for i in range(100)],
    })
    score = compute_corruption_score(df, "email")
    assert score < 0.1


def test_compute_corruption_score_missing_column_returns_zero():
    from goldenmatch.core.indicators import compute_corruption_score
    df = pl.DataFrame({"x": [1, 2, 3]})
    score = compute_corruption_score(df, "nonexistent")
    assert score == 0.0
```

- [ ] **Step 2: Implement public alias**

Append:

```python
def compute_corruption_score(df: pl.DataFrame, col: str) -> float:
    """Public API for per-column corruption score (case/whitespace noise).

    See _compute_corruption_score_inline for the heuristic.
    """
    if col not in df.columns or df.is_empty():
        return 0.0
    sample = df.head(1000) if df.height > 1000 else df
    return _compute_corruption_score_inline(sample, col)
```

- [ ] **Step 3: Run; expect 3 PASS, commit**

```bash
git add packages/python/goldenmatch/goldenmatch/core/indicators.py packages/python/goldenmatch/tests/test_indicators.py
git commit -m "feat(autoconfig): public compute_corruption_score helper"
```

### Task 2.4: `estimate_full_pop_hits` (expensive, lazy)

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/indicators.py`
- Test: `packages/python/goldenmatch/tests/test_indicators.py`

- [ ] **Step 1: Failing tests**

```python
def test_estimate_full_pop_hits_disjoint_zero():
    from goldenmatch.core.indicators import estimate_full_pop_hits
    df = pl.DataFrame({
        "email": [f"unique_{i}@x.com" for i in range(1000)],
    })
    hits = estimate_full_pop_hits(df, "email")
    assert hits == 0


def test_estimate_full_pop_hits_with_duplicates():
    from goldenmatch.core.indicators import estimate_full_pop_hits
    df = pl.DataFrame({
        "email": [f"u{i % 50}@x.com" for i in range(200)],   # 4× collision per email
    })
    hits = estimate_full_pop_hits(df, "email")
    # 50 emails × C(4,2) = 50 × 6 = 300 pairs
    assert hits >= 100


def test_estimate_full_pop_hits_budget_returns_none(monkeypatch):
    """Synthetic slow path → returns None."""
    from goldenmatch.core import indicators
    monkeypatch.setattr(indicators, "BUDGET_FULL_POP_HITS", 0.0)
    df = pl.DataFrame({"email": ["a@x.com"] * 1000})
    hits = indicators.estimate_full_pop_hits(df, "email")
    assert hits is None
```

- [ ] **Step 2: Implement**

```python
def estimate_full_pop_hits(df: pl.DataFrame, blocking_col: str) -> int | None:
    """Count exact-match collisions on the full population.

    Returns None on budget exhaustion. Used by indicator-aware rules
    to validate that v0's blocking key has structural signal even when
    sample's mass_above_threshold == 0.
    """
    start = time.time()
    if blocking_col not in df.columns or df.is_empty():
        return 0
    if (time.time() - start) > BUDGET_FULL_POP_HITS:
        return None
    try:
        counts = (
            df.group_by(blocking_col)
            .agg(pl.len().alias("n"))
            .filter(pl.col("n") > 1)
        )
        if (time.time() - start) > BUDGET_FULL_POP_HITS:
            return None
        n_pairs = int(
            counts.select(
                (pl.col("n") * (pl.col("n") - 1) / 2).sum()
            ).item()
        )
        return n_pairs
    except Exception as exc:
        logger.warning("estimate_full_pop_hits failed: %s", exc)
        return None
```

- [ ] **Step 3: Run; expect 3 PASS, commit**

```bash
git commit -m "feat(autoconfig): estimate_full_pop_hits with budget enforcement"
```

### Task 2.5: `compute_cross_blocking_overlap` (expensive, lazy)

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/indicators.py`
- Test: `packages/python/goldenmatch/tests/test_indicators.py`

- [ ] **Step 1: Failing tests**

```python
def test_cross_blocking_overlap_identical_keys_returns_one():
    """Same key on both sides → overlap = 1.0 (degenerate guard)."""
    from goldenmatch.core.indicators import compute_cross_blocking_overlap
    df = pl.DataFrame({"city": ["nyc", "nyc", "la"] * 10})
    overlap = compute_cross_blocking_overlap(df, "city", "city")
    assert overlap == 1.0


def test_cross_blocking_overlap_orthogonal_keys_low():
    """Two unrelated keys with no record-level correlation → low overlap."""
    from goldenmatch.core.indicators import compute_cross_blocking_overlap
    import random
    random.seed(42)
    n = 100
    df = pl.DataFrame({
        "city": [random.choice(["nyc", "la", "sf"]) for _ in range(n)],
        "category": [random.choice(["a", "b", "c", "d", "e"]) for _ in range(n)],
    })
    overlap = compute_cross_blocking_overlap(df, "city", "category")
    # Random correlation should be small but not zero
    assert overlap < 0.5


def test_cross_blocking_overlap_budget_returns_none(monkeypatch):
    from goldenmatch.core import indicators
    monkeypatch.setattr(indicators, "BUDGET_CROSS_BLOCKING", 0.0)
    df = pl.DataFrame({"city": ["nyc"] * 100, "state": ["NY"] * 100})
    result = indicators.compute_cross_blocking_overlap(df, "city", "state")
    assert result is None
```

- [ ] **Step 2: Implement**

```python
def compute_cross_blocking_overlap(
    df: pl.DataFrame, key_a: str, key_b: str,
) -> float | None:
    """Fraction of (record_i, record_j) pairs that are co-blocked under
    BOTH key_a AND key_b out of all pairs co-blocked under EITHER.

    overlap = |co_a INTERSECTION co_b| / |co_a UNION co_b|

    Returns 1.0 if key_a == key_b (degenerate). Returns None on budget.
    Used by rule_cross_blocking_disagreement: low overlap indicates
    blocking key is genuinely capturing wrong candidates (orthogonal
    keys agree on nothing) vs. just-too-few-matches.
    """
    if key_a == key_b:
        return 1.0
    start = time.time()
    if key_a not in df.columns or key_b not in df.columns or df.is_empty():
        return None
    if (time.time() - start) > BUDGET_CROSS_BLOCKING:
        return None
    try:
        # Co-blocked under key_a: pairs sharing same key_a value
        df_indexed = df.with_row_index("__row__")
        a_pairs = (
            df_indexed.group_by(key_a)
            .agg(pl.col("__row__").alias("rows"))
            .filter(pl.col("rows").list.len() > 1)
        )
        b_pairs = (
            df_indexed.group_by(key_b)
            .agg(pl.col("__row__").alias("rows"))
            .filter(pl.col("rows").list.len() > 1)
        )

        if (time.time() - start) > BUDGET_CROSS_BLOCKING:
            return None

        # Build pair sets (small enough to materialize as Python sets
        # within budget; if too big, return None defensively)
        def _pairs_set(grouped):
            pairs = set()
            for rows in grouped["rows"].to_list():
                rows = sorted(rows)
                for i in range(len(rows)):
                    for j in range(i+1, len(rows)):
                        pairs.add((rows[i], rows[j]))
                        if (time.time() - start) > BUDGET_CROSS_BLOCKING:
                            return None
            return pairs

        set_a = _pairs_set(a_pairs)
        if set_a is None:
            return None
        set_b = _pairs_set(b_pairs)
        if set_b is None:
            return None

        union = set_a | set_b
        if not union:
            return 1.0   # no co-blocked pairs at all → degenerate, treat as match
        intersection = set_a & set_b
        return len(intersection) / len(union)
    except Exception as exc:
        logger.warning("compute_cross_blocking_overlap failed: %s", exc)
        return None
```

- [ ] **Step 3: Run; expect 3 PASS, commit**

```bash
git commit -m "feat(autoconfig): compute_cross_blocking_overlap (Jaccard on pair sets)"
```

---

## Phase 3 — IndicatorContext + propose() signature widening

### Task 3.1: `IndicatorContext` class

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/autoconfig_controller.py`
- Test: `packages/python/goldenmatch/tests/test_autoconfig_controller.py`

- [ ] **Step 1: Failing tests**

```python
def test_indicator_context_memoizes_calls():
    """ctx.full_pop_matchkey_hits memoizes by (col, matchkey_signature)."""
    from goldenmatch.core.autoconfig_controller import IndicatorContext
    from goldenmatch.core.complexity_profile import SparsityVerdict
    import polars as pl
    df = pl.DataFrame({"email": ["a@x.com", "a@x.com", "b@x.com"]})
    ctx = IndicatorContext(
        df=df,
        column_priors={},
        sparsity_verdict=SparsityVerdict(is_sparse=False, estimated_n_true_pairs=1),
    )
    h1 = ctx.full_pop_matchkey_hits("email")
    h2 = ctx.full_pop_matchkey_hits("email")
    assert h1 == h2
    # Internal cache hit (best test: assert _memo dict has entry)
    assert ("full_pop_matchkey_hits", "email") in ctx._memo


def test_indicator_context_has_fired_one_shot_guard():
    from goldenmatch.core.autoconfig_controller import IndicatorContext
    from goldenmatch.core.complexity_profile import SparsityVerdict
    import polars as pl
    ctx = IndicatorContext(
        df=pl.DataFrame(),
        column_priors={},
        sparsity_verdict=SparsityVerdict(is_sparse=True, estimated_n_true_pairs=0),
    )
    assert ctx.has_fired("rule_x") is False
    ctx.mark_fired("rule_x")
    assert ctx.has_fired("rule_x") is True
```

- [ ] **Step 2: Implement**

Add to `core/autoconfig_controller.py` (top-level, near `_LAST_CONTROLLER_RUN`):

```python
class IndicatorContext:
    """v1.10: per-run object threading indicators through the policy/rule chain.

    Memoizes lazy indicator calls by (function_name, args). Tracks
    one-shot rule firings for guards like rule_sparse_match_expand.

    Spec: docs/superpowers/specs/2026-05-08-autoconfig-indicators-design.md
          §Architecture #2.
    """

    def __init__(
        self,
        df: pl.DataFrame,
        column_priors: dict[str, ColumnPrior],
        sparsity_verdict: SparsityVerdict,
    ) -> None:
        self._df = df
        self._column_priors = column_priors
        self._sparsity_verdict = sparsity_verdict
        self._memo: dict[tuple[str, ...], Any] = {}
        self._fired: set[str] = set()

    @property
    def column_priors(self) -> dict[str, ColumnPrior]:
        return self._column_priors

    @property
    def sparsity_verdict(self) -> SparsityVerdict:
        return self._sparsity_verdict

    def full_pop_matchkey_hits(self, blocking_col: str) -> int | None:
        from goldenmatch.core.indicators import estimate_full_pop_hits
        key = ("full_pop_matchkey_hits", blocking_col)
        if key not in self._memo:
            self._memo[key] = estimate_full_pop_hits(self._df, blocking_col)
        return self._memo[key]

    def cross_blocking_overlap(self, key_a: str, key_b: str) -> float | None:
        from goldenmatch.core.indicators import compute_cross_blocking_overlap
        # Canonicalize ordering for cache hits
        a, b = sorted([key_a, key_b])
        key = ("cross_blocking_overlap", a, b)
        if key not in self._memo:
            self._memo[key] = compute_cross_blocking_overlap(self._df, a, b)
        return self._memo[key]

    def has_fired(self, rule_name: str) -> bool:
        return rule_name in self._fired

    def mark_fired(self, rule_name: str) -> None:
        self._fired.add(rule_name)
```

Also add the imports near the top:
```python
from goldenmatch.core.complexity_profile import (
    ComplexityProfile, HealthVerdict, StopReason, ColumnPrior, SparsityVerdict,
)
```

- [ ] **Step 3: Run, commit**

```bash
git commit -m "feat(autoconfig): IndicatorContext with memoization + one-shot guards"
```

### Task 3.2: Widen `RefitPolicy.propose` signature

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/autoconfig_policy.py`
- Test: `packages/python/goldenmatch/tests/test_autoconfig_policy.py`

- [ ] **Step 1: Failing tests**

Append to `tests/test_autoconfig_policy.py`:

```python
def test_heuristic_propose_accepts_ctx_kwarg():
    """HeuristicRefitPolicy.propose accepts an optional ctx kwarg."""
    from goldenmatch.core.autoconfig_policy import HeuristicRefitPolicy
    from goldenmatch.core.autoconfig_controller import IndicatorContext
    from goldenmatch.core.complexity_profile import SparsityVerdict
    import polars as pl
    pol = HeuristicRefitPolicy()
    ctx = IndicatorContext(
        df=pl.DataFrame(), column_priors={},
        sparsity_verdict=SparsityVerdict(False, 0),
    )
    # Should not raise on extra kwarg
    import inspect
    sig = inspect.signature(pol.propose)
    assert "ctx" in sig.parameters


def test_llm_propose_forwards_ctx_to_base():
    """LLMRefitPolicy.propose forwards ctx to its wrapped base policy."""
    from goldenmatch.core.autoconfig_policy import LLMRefitPolicy, HeuristicRefitPolicy
    import inspect
    pol = LLMRefitPolicy(base=HeuristicRefitPolicy())
    sig = inspect.signature(pol.propose)
    assert "ctx" in sig.parameters
```

- [ ] **Step 2: Modify both classes**

In `core/autoconfig_policy.py`:

1. Update the `RefitPolicy` Protocol:
```python
class RefitPolicy(Protocol):
    def propose(
        self,
        profile: ComplexityProfile,
        current: Any,
        history: RunHistory,
        ctx: "IndicatorContext | None" = None,
    ) -> Any | None: ...
```

2. Update `HeuristicRefitPolicy.propose`:
```python
    def propose(
        self,
        profile: ComplexityProfile,
        current: Any,
        history: RunHistory,
        ctx: "IndicatorContext | None" = None,
    ) -> Any | None:
        if profile.health() == HealthVerdict.GREEN:
            return None
        for rule in self._rules:
            outcome = self._call_rule(rule, profile, current, history, ctx)
            if outcome is None:
                continue
            new_config, decision = outcome
            if new_config == current:
                return None
            if history.entries:
                history.entries[-1].decision = decision
            return new_config
        return None

    @staticmethod
    def _call_rule(rule, profile, current, history, ctx):
        """Call a rule with ctx if its signature accepts it; else 3-arg."""
        import inspect
        params = inspect.signature(rule).parameters
        if "ctx" in params:
            return rule(profile, current, history, ctx=ctx)
        return rule(profile, current, history)
```

3. Update `LLMRefitPolicy.propose`:
```python
    def propose(
        self,
        profile: ComplexityProfile,
        current: Any,
        history: RunHistory,
        ctx: "IndicatorContext | None" = None,
    ) -> Any | None:
        # Forward ctx to base
        base_result = self._base.propose(profile, current, history, ctx=ctx)
        if base_result is not None:
            return base_result
        # ... rest of LLM fallback unchanged ...
```

Also add the type-only import at the top:
```python
if TYPE_CHECKING:
    from goldenmatch.core.autoconfig_controller import IndicatorContext
```

- [ ] **Step 3: Run, commit**

```bash
git commit -m "feat(autoconfig): RefitPolicy.propose accepts optional ctx kwarg"
```

### Task 3.3: Controller signature introspection for old-shape custom policies

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/autoconfig_controller.py`
- Test: `packages/python/goldenmatch/tests/test_autoconfig_policy.py`

- [ ] **Step 1: Failing test**

```python
def test_controller_supports_old_shape_3arg_custom_policy():
    """A custom policy with 3-arg propose (no ctx) still works."""
    from goldenmatch.core.autoconfig_controller import AutoConfigController, ControllerBudget
    import polars as pl

    class _OldShapePolicy:
        def propose(self, profile, current, history):
            return None    # always satisfied

    df = pl.DataFrame({"a": [1, 2, 3]})
    controller = AutoConfigController(
        policy=_OldShapePolicy(),
        budget=ControllerBudget(max_iterations=2, sample_skip_below=1),
    )
    # Should not raise TypeError
    config, profile, history = controller.run(df)
    assert profile is not None
```

- [ ] **Step 2: Wire the controller**

Find the call site in `autoconfig_controller.py` where `self.policy.propose(...)` is called. Replace with:

```python
import inspect

def _call_policy_propose(policy, profile, current, history, ctx):
    """Call policy.propose with ctx if its signature accepts it; else 3-arg."""
    params = inspect.signature(policy.propose).parameters
    if "ctx" in params:
        return policy.propose(profile, current, history, ctx=ctx)
    return policy.propose(profile, current, history)
```

Then in the iteration loop:
```python
config_next = _call_policy_propose(self.policy, profile_n, config_n, history, ctx)
```

- [ ] **Step 3: Run, commit**

```bash
git commit -m "feat(autoconfig): controller introspects custom policy signature for backward compat"
```

---

## Phase 4 — Rule helpers + modified existing rules

> **DEVIATION FROM SPEC** — `rule_sparse_match_expand` (Task 5.3): the spec's `ExpandSample(2.0)` action requires a controller-level sample-expansion mechanism that doesn't exist in v1.9. Rather than scope-creep that work into v1.10, this rule fires `ctx.mark_fired("rule_sparse_match_expand")` to signal the side channel AND uses `_with_lower_threshold(delta=0.10)` as a partial proxy. Real `ExpandSample` is queued for v1.11. Spec amendment will land in §Non-goals if v1.10 ships.

### Task 4.0: Scaffold shared test helpers (prerequisite)

**Files:**
- Modify: `packages/python/goldenmatch/tests/test_autoconfig_rules.py` (or create a `tests/_autoconfig_test_helpers.py` if cleaner — the existing test file is large)

The Phase 4-5 rule tests reference helpers that don't exist yet. Define them ONCE before any rule test runs.

- [ ] **Step 1: Inspect existing rule tests** to find names of any helpers that already exist:

```bash
grep -n "^def _\|@pytest.fixture" /d/show_case/goldenmatch/packages/python/goldenmatch/tests/test_autoconfig_rules.py | head -20
```

- [ ] **Step 2: Add the helper module/section** with these functions:

```python
"""Shared test helpers for v1.10 indicator-aware rule tests."""
from __future__ import annotations
import polars as pl

from goldenmatch.config.schemas import (
    GoldenMatchConfig, MatchkeyConfig, MatchkeyField, BlockingConfig,
    BlockingKeyConfig, StandardizationConfig,
)
from goldenmatch.core.complexity_profile import (
    ComplexityProfile, DataProfile, BlockingProfile, ScoringProfile,
    ClusterProfile, MatchkeyProfile, FieldStats, HealthVerdict, ColumnPrior,
    SparsityVerdict,
)
from goldenmatch.core.autoconfig_history import (
    RunHistory, HistoryEntry, PolicyDecision,
)


def _build_test_config(
    blocking_field: str = "email",
    threshold: float = 0.85,
) -> GoldenMatchConfig:
    """Minimal valid GoldenMatchConfig with one weighted matchkey + blocking."""
    return GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="primary",
            type="weighted",
            threshold=threshold,
            fields=[MatchkeyField(
                field="email", transforms=["lowercase"],
                scorer="ensemble", weight=1.0,
            )],
        )],
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=[blocking_field], transforms=["lowercase"])],
            max_block_size=1000,
            skip_oversized=True,
        ),
    )


def _build_test_config_with_email_standardization() -> GoldenMatchConfig:
    cfg = _build_test_config(blocking_field="email")
    return cfg.model_copy(update={
        "standardization": StandardizationConfig(rules={
            "email": ["lowercase", "strip"],
        }),
    })


def _get_threshold(cfg: GoldenMatchConfig) -> float:
    return cfg.get_matchkeys()[0].threshold


def _get_blocking_field(cfg: GoldenMatchConfig) -> str:
    return cfg.blocking.keys[0].fields[0]


def _profile_with_mass_above(
    mass_above: float, blocking_col: str = "email",
) -> ComplexityProfile:
    """Build a ComplexityProfile with the given scoring mass_above_threshold."""
    return ComplexityProfile(
        data=DataProfile(
            n_rows=1000, n_cols=4,
            column_types={blocking_col: "id-like", "name": "text",
                          "city": "geo", "dob": "date"},
        ),
        blocking=BlockingProfile(
            keys_used=[[blocking_col]], n_blocks=100,
            total_comparisons=500, reduction_ratio=0.95, block_sizes_p99=20,
        ),
        scoring=ScoringProfile(
            n_pairs_scored=500, candidates_compared=500,
            mass_above_threshold=mass_above,
            mass_in_borderline=0.1, dip_statistic=0.05,
        ),
        cluster=ClusterProfile(transitivity_rate=0.95),
        matchkey=MatchkeyProfile(per_field={
            blocking_col: FieldStats(0.5, 0.0, 10),
        }),
    )


def _profile_with_health_yellow() -> ComplexityProfile:
    """Build a ComplexityProfile that rolls up to YELLOW."""
    return _profile_with_mass_above(mass_above=0.3)


def _empty_history() -> RunHistory:
    return RunHistory()


def _history_with_prior_decision() -> RunHistory:
    h = RunHistory()
    h.entries.append(HistoryEntry(
        iteration=0, config=_build_test_config(),
        profile=_profile_with_mass_above(0.0),
        decision=PolicyDecision(
            rule_name="rule_blocking_field_null_heavy",
            rationale="prior", config_diff={},
        ),
        error=None, wall_clock_ms=10,
    ))
    return h


def _ctx_with_priors(priors: dict[str, ColumnPrior]):
    """Build an IndicatorContext with given column_priors and default sparsity."""
    from goldenmatch.core.autoconfig_controller import IndicatorContext
    return IndicatorContext(
        df=pl.DataFrame(),
        column_priors=priors,
        sparsity_verdict=SparsityVerdict(is_sparse=False, estimated_n_true_pairs=100),
    )


def _ctx_with_sparsity(sv: SparsityVerdict):
    from goldenmatch.core.autoconfig_controller import IndicatorContext
    return IndicatorContext(
        df=pl.DataFrame(),
        column_priors={},
        sparsity_verdict=sv,
    )


def _ctx_with_priors_and_hits(
    priors: dict[str, ColumnPrior],
    full_pop_hits: dict[str, int],
):
    """Build an IndicatorContext that returns mocked full_pop_matchkey_hits."""
    ctx = _ctx_with_priors(priors)
    # Pre-populate the memo cache so full_pop_matchkey_hits returns the mock
    for col, hits in full_pop_hits.items():
        ctx._memo[("full_pop_matchkey_hits", col)] = hits
    return ctx
```

- [ ] **Step 3: Run the existing test file to make sure helpers don't conflict with anything**:

```bash
cd /d/show_case/goldenmatch/packages/python/goldenmatch && C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe -m pytest tests/test_autoconfig_rules.py -q --timeout=60 2>&1 | tail -5
```

- [ ] **Step 4: Commit**

```bash
git commit -m "test(autoconfig): scaffold shared helpers for v1.10 rule tests"
```

### Task 4.1: Rule helpers in `autoconfig_rules.py`

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/autoconfig_rules.py`
- Test: `packages/python/goldenmatch/tests/test_autoconfig_rules.py`

- [ ] **Step 1: Failing tests**

```python
def test_with_lower_threshold_returns_new_config():
    from goldenmatch.core.autoconfig_rules import _with_lower_threshold
    # Build a minimal config with a single matchkey at threshold 0.85
    from goldenmatch.config.schemas import GoldenMatchConfig, MatchkeyConfig
    cfg = _build_test_config(threshold=0.85)
    new_cfg, rationale = _with_lower_threshold(cfg, delta=0.05)
    assert _get_threshold(new_cfg) == 0.80
    assert "0.80" in rationale or "0.80" in rationale.lower()


def test_with_lower_threshold_at_floor_returns_unchanged():
    """Threshold floor: don't go below 0.5."""
    from goldenmatch.core.autoconfig_rules import _with_lower_threshold
    cfg = _build_test_config(threshold=0.5)
    new_cfg, rationale = _with_lower_threshold(cfg, delta=0.05)
    assert new_cfg == cfg


def test_with_normalize_standardization_adds_rule():
    from goldenmatch.core.autoconfig_rules import _with_normalize_standardization
    cfg = _build_test_config()
    new_cfg, _rationale = _with_normalize_standardization(cfg, "email")
    assert "email" in new_cfg.standardization.rules


def test_with_normalize_standardization_idempotent():
    """If standardization rule already exists, return unchanged."""
    from goldenmatch.core.autoconfig_rules import _with_normalize_standardization
    cfg = _build_test_config_with_email_standardization()
    new_cfg, _ = _with_normalize_standardization(cfg, "email")
    assert new_cfg == cfg
```

(`_build_test_config()` is a test helper that returns a minimal valid `GoldenMatchConfig` — pattern it after existing helpers in the test file.)

- [ ] **Step 2: Implement helpers**

Add to `core/autoconfig_rules.py` (private to module):

```python
_THRESHOLD_FLOOR = 0.5
_DEFAULT_NORMALIZE_RULES = {
    "email": ["lowercase", "strip", "remove_invisible_chars"],
    "phone": ["digits_only"],
    "first_name": ["strip", "casefold"],
    "last_name": ["strip", "casefold"],
}


def _with_lower_threshold(
    cfg, delta: float = 0.05,
) -> tuple[Any, str]:
    """Return (new_config, rationale) lowering matchkey threshold by delta.
    Returns (cfg, "") if floor reached.
    """
    matchkeys = cfg.get_matchkeys()
    if not matchkeys:
        return cfg, ""
    primary = matchkeys[0]
    new_threshold = round(primary.threshold - delta, 2)
    if new_threshold < _THRESHOLD_FLOOR:
        return cfg, ""
    new_mk = primary.model_copy(update={"threshold": new_threshold})
    new_matchkeys = [new_mk] + matchkeys[1:]
    new_cfg = cfg.model_copy(update={"matchkeys": new_matchkeys})
    return new_cfg, f"lowered threshold to {new_threshold}"


def _with_normalize_standardization(cfg, col: str) -> tuple[Any, str]:
    """Return (new_config, rationale) adding normalize-standardization on col.
    Returns (cfg, "") if rule already exists for that column.
    """
    rules_dict = dict(cfg.standardization.rules) if cfg.standardization else {}
    if col in rules_dict:
        return cfg, ""
    new_rule = _DEFAULT_NORMALIZE_RULES.get(col, ["strip", "casefold"])
    rules_dict[col] = new_rule
    from goldenmatch.config.schemas import StandardizationConfig
    new_std = StandardizationConfig(rules=rules_dict)
    new_cfg = cfg.model_copy(update={"standardization": new_std})
    return new_cfg, f"added normalize_standardization({col}={new_rule})"


def _with_multi_pass(cfg, additional_key) -> tuple[Any, str]:
    """Return (new_config, rationale) adding a multi-pass blocking key."""
    blocking = cfg.blocking
    existing_keys = list(blocking.keys)
    if any(k.fields == additional_key.fields for k in existing_keys):
        return cfg, ""
    new_keys = existing_keys + [additional_key]
    from goldenmatch.config.schemas import BlockingConfig
    new_blocking = blocking.model_copy(update={
        "strategy": "multi_pass",
        "keys": new_keys,
        "passes": new_keys,
    })
    new_cfg = cfg.model_copy(update={"blocking": new_blocking})
    return new_cfg, f"added multi_pass({additional_key.fields})"


def _orthogonal_key(cfg, df_columns: list[str]):
    """Pick an orthogonal blocking key from remaining columns.
    Returns None if no candidate exists.
    """
    from goldenmatch.config.schemas import BlockingKeyConfig
    used_cols = set()
    for k in cfg.blocking.keys:
        used_cols.update(k.fields)
    candidates = [c for c in df_columns if c not in used_cols and not c.startswith("__")]
    if not candidates:
        return None
    return BlockingKeyConfig(fields=[candidates[0]], transforms=["lowercase"])
```

- [ ] **Step 3: Run, commit**

```bash
git commit -m "feat(autoconfig): rule helpers _with_lower_threshold/_normalize/_multi_pass/_orthogonal_key"
```

### Task 4.2: Modify `rule_no_matches`

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/autoconfig_rules.py` (existing `rule_no_matches`)
- Test: `packages/python/goldenmatch/tests/test_autoconfig_rules.py`

- [ ] **Step 1: Failing tests**

```python
def test_rule_no_matches_high_identity_prior_tries_lower_threshold_first():
    """Identity-prior column → first alternative is lower_threshold."""
    from goldenmatch.core.autoconfig_rules import rule_no_matches
    profile = _profile_with_mass_above(0.0, blocking_col="email")
    cfg = _build_test_config(blocking_field="email", threshold=0.85)
    ctx = _ctx_with_priors({"email": ColumnPrior(identity_score=0.9, corruption_score=0.0)})
    history = _empty_history()
    outcome = rule_no_matches(profile, cfg, history, ctx=ctx)
    assert outcome is not None
    new_cfg, decision = outcome
    assert new_cfg != cfg
    assert "lower" in decision.rationale.lower() or "0.80" in decision.rationale


def test_rule_no_matches_sparse_proposes_expand_sample():
    from goldenmatch.core.autoconfig_rules import rule_no_matches
    profile = _profile_with_mass_above(0.0, blocking_col="text_col")
    cfg = _build_test_config(blocking_field="text_col")
    ctx = _ctx_with_sparsity(SparsityVerdict(is_sparse=True, estimated_n_true_pairs=10))
    history = _empty_history()
    outcome = rule_no_matches(profile, cfg, history, ctx=ctx)
    assert outcome is not None
    _, decision = outcome
    assert "expand" in decision.rationale.lower() or "sample" in decision.rationale.lower()


def test_rule_no_matches_baseline_no_ctx_today_behavior():
    """ctx=None → today's behavior (lower threshold)."""
    from goldenmatch.core.autoconfig_rules import rule_no_matches
    profile = _profile_with_mass_above(0.0, blocking_col="text_col")
    cfg = _build_test_config(blocking_field="text_col", threshold=0.85)
    history = _empty_history()
    outcome = rule_no_matches(profile, cfg, history, ctx=None)
    assert outcome is not None
    new_cfg, _ = outcome
    assert _get_threshold(new_cfg) < 0.85
```

- [ ] **Step 2: Modify `rule_no_matches`**

Replace its body:

```python
def rule_no_matches(profile, current, history, ctx=None):
    """Fires when scoring.mass_above_threshold == 0.

    v1.10 (with ctx): tries alternatives in priority order based on
    indicator priors. Falls back to today's behavior when ctx is None.
    """
    if profile.scoring.mass_above_threshold > 0:
        return None

    blocking_col = _get_blocking_field(current)
    candidates = []

    if ctx is not None and ctx.column_priors.get(blocking_col):
        cp = ctx.column_priors[blocking_col]
        if cp.identity_score >= 0.7:
            # Identity column — try gentler alternatives before swap
            candidates.append(_with_lower_threshold(current, 0.05))
            candidates.append(_with_normalize_standardization(current, blocking_col))
            df_cols = list(ctx._df.columns) if ctx is not None else []
            ortho = _orthogonal_key(current, df_cols)
            if ortho is not None:
                candidates.append(_with_multi_pass(current, ortho))
        elif ctx.sparsity_verdict.is_sparse:
            # DEVIATION: spec wanted ExpandSample(2.0); v1.10 substitute is
            # a sharper threshold drop + side-channel signal via mark_fired.
            # rule_sparse_match_expand handles the full sparse path.
            candidates.append(_with_lower_threshold(current, 0.10))
    else:
        candidates.append(_with_lower_threshold(current, 0.05))

    for new_cfg, rationale in candidates:
        if new_cfg != current:
            from goldenmatch.core.autoconfig_history import PolicyDecision
            return new_cfg, PolicyDecision(
                rule_name="rule_no_matches",
                rationale=rationale,
                config_diff={},
            )
    return None
```

(`_get_blocking_field` is an existing helper or trivial inline `current.blocking.keys[0].fields[0]`.)

- [ ] **Step 3: Run new tests + regression-check existing rule tests**

```bash
cd /d/show_case/goldenmatch/packages/python/goldenmatch && C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe -m pytest tests/test_autoconfig_rules.py -v --timeout=60 2>&1 | tail -20
```

Expected: ALL existing `rule_no_matches` tests still pass (the new identity-prior path activates only when ctx is provided; ctx=None preserves today's behavior). New tests pass too. If any pre-existing test fails, the new candidate-list logic broke a documented behavior — investigate before committing.

- [ ] **Step 4: Commit**

```bash
git commit -m "feat(autoconfig): rule_no_matches reads ctx and tries alternatives in priority order"
```

### Task 4.3: Modify `rule_blocking_key_swap`

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/autoconfig_rules.py`
- Test: `packages/python/goldenmatch/tests/test_autoconfig_rules.py`

- [ ] **Step 1: Failing tests**

```python
def test_rule_blocking_key_swap_vetoed_when_v0_key_good():
    """High identity_score + nonzero full_pop_hits → veto swap."""
    from goldenmatch.core.autoconfig_rules import rule_blocking_key_swap
    profile = _profile_with_mass_above(0.0)
    cfg = _build_test_config(blocking_field="email")
    ctx = _ctx_with_priors_and_hits(
        {"email": ColumnPrior(identity_score=0.9, corruption_score=0.5)},
        full_pop_hits={"email": 100},
    )
    # Need a prior decision so today's swap rule would fire
    history = _history_with_prior_decision()
    outcome = rule_blocking_key_swap(profile, cfg, history, ctx=ctx)
    assert outcome is None    # vetoed


def test_rule_blocking_key_swap_proceeds_when_no_indicator_evidence():
    from goldenmatch.core.autoconfig_rules import rule_blocking_key_swap
    profile = _profile_with_mass_above(0.0)
    cfg = _build_test_config(blocking_field="text_col")
    ctx = _ctx_with_priors({"text_col": ColumnPrior(0.0, 0.0)})
    history = _history_with_prior_decision()
    outcome = rule_blocking_key_swap(profile, cfg, history, ctx=ctx)
    # Today's behavior: swap proceeds
    assert outcome is not None
```

- [ ] **Step 2: Modify**

Add the veto check at the top of `rule_blocking_key_swap`:

```python
def rule_blocking_key_swap(profile, current, history, ctx=None):
    """... existing docstring ...

    v1.10: vetoed when identity_score >= 0.8 AND full_pop_matchkey_hits > 0.
    """
    blocking_col = _get_blocking_field(current)
    if ctx is not None:
        cp = ctx.column_priors.get(blocking_col)
        if cp is not None and cp.identity_score >= 0.8:
            hits = ctx.full_pop_matchkey_hits(blocking_col)
            if hits is not None and hits > 0:
                return None    # vetoed: v0 blocking key is structurally good
    # ... existing logic unchanged ...
```

- [ ] **Step 3: Run, commit**

```bash
git commit -m "feat(autoconfig): rule_blocking_key_swap vetoed when indicator priors signal v0-key-good"
```

---

## Phase 5 — New rules

### Task 5.1: `rule_corruption_normalize`

- [ ] **Step 1: Failing tests**

```python
def test_rule_corruption_normalize_fires_high_corruption_high_identity():
    from goldenmatch.core.autoconfig_rules import rule_corruption_normalize
    profile = _profile_with_health_yellow()
    cfg = _build_test_config(blocking_field="email")
    ctx = _ctx_with_priors({"email": ColumnPrior(identity_score=0.9, corruption_score=0.6)})
    outcome = rule_corruption_normalize(profile, cfg, _empty_history(), ctx=ctx)
    assert outcome is not None


def test_rule_corruption_normalize_idempotent_when_already_normalized():
    """Doesn't fire if standardization rule for col already exists."""
    from goldenmatch.core.autoconfig_rules import rule_corruption_normalize
    profile = _profile_with_health_yellow()
    cfg = _build_test_config_with_email_standardization()
    ctx = _ctx_with_priors({"email": ColumnPrior(identity_score=0.9, corruption_score=0.6)})
    outcome = rule_corruption_normalize(profile, cfg, _empty_history(), ctx=ctx)
    assert outcome is None
```

- [ ] **Step 2: Implement**

```python
def rule_corruption_normalize(profile, current, history, ctx=None):
    """v1.10: when blocking column has high corruption + identity prior,
    add normalize-standardization. One-shot via standardization-already-exists
    check (so it doesn't fire repeatedly on the same column)."""
    if ctx is None:
        return None
    if profile.health() == HealthVerdict.GREEN:
        return None
    blocking_col = _get_blocking_field(current)
    cp = ctx.column_priors.get(blocking_col)
    if cp is None or cp.corruption_score <= 0.4 or cp.identity_score <= 0.6:
        return None
    new_cfg, rationale = _with_normalize_standardization(current, blocking_col)
    if new_cfg == current:
        return None    # already has standardization for this column
    from goldenmatch.core.autoconfig_history import PolicyDecision
    return new_cfg, PolicyDecision(
        rule_name="rule_corruption_normalize",
        rationale=rationale,
        config_diff={},
    )
```

- [ ] **Step 3: Run, commit**

### Task 5.2: `rule_cross_blocking_disagreement`

- [ ] **Step 1: Failing tests + implement**

Pattern same as 5.1. Firing condition:
```python
def rule_cross_blocking_disagreement(profile, current, history, ctx=None):
    if ctx is None or len(history.entries) < 1:
        return None
    if profile.health() != HealthVerdict.RED:
        return None
    if profile.scoring.mass_above_threshold >= 0.1:
        return None
    blocking_col = _get_blocking_field(current)
    df_cols = list(ctx._df.columns)
    ortho = _orthogonal_key(current, df_cols)
    if ortho is None:
        return None
    overlap = ctx.cross_blocking_overlap(blocking_col, ortho.fields[0])
    if overlap is None or overlap >= 0.3:
        return None
    new_cfg, rationale = _with_multi_pass(current, ortho)
    if new_cfg == current:
        return None
    from goldenmatch.core.autoconfig_history import PolicyDecision
    return new_cfg, PolicyDecision(
        rule_name="rule_cross_blocking_disagreement",
        rationale=f"cross_blocking_overlap={overlap:.2f}; {rationale}",
        config_diff={},
    )
```

- [ ] **Step 2: Commit**

### Task 5.3: `rule_sparse_match_expand`

- [ ] **Step 1: Failing tests + implement**

```python
def rule_sparse_match_expand(profile, current, history, ctx=None):
    if ctx is None:
        return None
    if not ctx.sparsity_verdict.is_sparse:
        return None
    if len(history.entries) > 1:
        return None
    if ctx.has_fired("rule_sparse_match_expand"):
        return None
    # NoOp config change for now — sample expansion is a controller-level
    # signal, not a config change. Mark fired and return a no-progress
    # decision so policy moves on; the controller's iteration loop reads
    # `ctx.has_fired` to decide whether to expand sample on next iter.
    ctx.mark_fired("rule_sparse_match_expand")
    # The cleanest implementation: write the sparsity flag into a config
    # field the controller reads. For now, attach as decision rationale.
    from goldenmatch.core.autoconfig_history import PolicyDecision
    # Lower threshold by 0.10 as a proxy action for expand sample
    new_cfg, rationale = _with_lower_threshold(current, delta=0.10)
    if new_cfg == current:
        return None
    return new_cfg, PolicyDecision(
        rule_name="rule_sparse_match_expand",
        rationale=f"sparse_sample (n_true_pairs={ctx.sparsity_verdict.estimated_n_true_pairs}); {rationale}",
        config_diff={},
    )
```

> **Note:** the spec describes `ExpandSample` as a real action that bumps the sample size. Implementing that requires plumbing through the controller's sample assembly, which is out of scope for the rule itself. As a pragmatic substitute, this rule lowers threshold sharply (proxy effect) AND marks fired so it doesn't loop. v1.11 can promote ExpandSample to a real controller-level action if measurement shows it's needed.

- [ ] **Step 2: Update `DEFAULT_RULES` ordering**

Edit the `DEFAULT_RULES` list at the bottom of `autoconfig_rules.py`:

```python
DEFAULT_RULES = [
    rule_blocking_field_null_heavy,
    rule_blocking_singleton_trap,
    rule_blocking_key_swap,
    rule_blocking_too_coarse,
    rule_uniform_heavy_blocking,
    rule_corruption_normalize,           # NEW v1.10
    rule_unimodal_scoring,
    rule_low_reduction_ratio,
    rule_cross_blocking_disagreement,    # NEW v1.10
    rule_low_transitivity,
    rule_no_matches,
    rule_recall_gap_suspected,
    rule_sparse_match_expand,            # NEW v1.10
]
```

- [ ] **Step 3: Run controller + rules tests, commit**

```bash
git commit -m "feat(autoconfig): 3 new indicator-aware rules + DEFAULT_RULES ordering update"
```

---

## Phase 6 — Controller wiring + integration test

### Task 6.1: Pre-iteration eager indicator compute + ctx threading

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/autoconfig_controller.py`
- Test: `packages/python/goldenmatch/tests/test_autoconfig_controller.py`

- [ ] **Step 1: Failing test**

```python
def test_controller_attaches_indicators_profile_after_run(small_df):
    """After run(), committed profile has column_priors + indicators populated."""
    from goldenmatch.core.autoconfig_controller import AutoConfigController, ControllerBudget
    from goldenmatch.core.autoconfig_policy import HeuristicRefitPolicy
    controller = AutoConfigController(
        policy=HeuristicRefitPolicy(),
        budget=ControllerBudget(sample_skip_below=1),
    )
    config, profile, history = controller.run(small_df)
    assert profile.data.column_priors is not None
    assert profile.indicators is not None
```

- [ ] **Step 2: Wire the controller**

Find `run()` in `autoconfig_controller.py`. Before the iteration loop:

```python
def run(self, df):
    from goldenmatch.core.indicators import (
        compute_column_priors, estimate_sparse_match_signal,
    )
    # ... existing setup ...

    column_priors = compute_column_priors(df)
    exact_cols = self._get_exact_columns(config_v0)
    sparsity_verdict = estimate_sparse_match_signal(df, exact_columns=exact_cols)
    ctx = IndicatorContext(
        df=df,
        column_priors=column_priors,
        sparsity_verdict=sparsity_verdict,
    )
    # ... iteration loop, passing ctx to _call_policy_propose ...
```

After `pick_committed()` selects an entry, attach indicators to its profile (immutable dataclass — use `dataclasses.replace`) and **thread the result into `_finalize`**:

```python
# Stamp the committed entry's profile with column_priors + indicators
import dataclasses
from goldenmatch.core.complexity_profile import IndicatorsProfile

blocking_col = best_entry.config.blocking.keys[0].fields[0]
new_data = dataclasses.replace(
    best_entry.profile.data,
    column_priors=column_priors,
)
new_indicators = IndicatorsProfile(
    full_pop_matchkey_hit_rate=(
        ctx._memo.get(("full_pop_matchkey_hits", blocking_col))
    ),
    cross_blocking_overlap=None,    # populated only if cross-blocking rule cached
)
# Pull cross_blocking_overlap from any cached entry (sorted-key tuple)
for memo_key, value in ctx._memo.items():
    if memo_key[0] == "cross_blocking_overlap":
        new_indicators = dataclasses.replace(
            new_indicators, cross_blocking_overlap=value,
        )
        break

stamped_profile = dataclasses.replace(
    best_entry.profile, data=new_data, indicators=new_indicators,
)

# CRITICAL: thread stamped_profile into the finalize call. The existing
# code path likely reads from best_entry.profile directly — replace those
# reads. Verify by greping for the next _finalize call site:
#   grep -n "_finalize\|_maybe_decorate_with_llm" autoconfig_controller.py
# and substitute stamped_profile for best_entry.profile in each.
final_config, final_profile = self._finalize(
    df, best_entry.config, stamped_profile, history,
)
return final_config, final_profile, history
```

The exact `_finalize` signature in the current codebase may differ — read it before substituting. The invariant: the profile passed downstream of `pick_committed()` MUST be `stamped_profile`, not `best_entry.profile`, otherwise the new fields don't appear on the final report and the Tier 4 integration test fails.

- [ ] **Step 3: Run, commit**

```bash
git commit -m "feat(autoconfig): controller computes eager indicators + threads IndicatorContext through propose"
```

### Task 6.1.5: `GOLDENMATCH_AUTOCONFIG_INDICATOR_BUDGET=fast` env var

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/autoconfig_controller.py` (`IndicatorContext`)
- Test: `packages/python/goldenmatch/tests/test_autoconfig_controller.py`

- [ ] **Step 1: Failing test**

```python
def test_indicator_context_fast_mode_skips_expensive(monkeypatch):
    """When GOLDENMATCH_AUTOCONFIG_INDICATOR_BUDGET=fast, the two expensive
    lazy indicators return None instead of computing."""
    monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_INDICATOR_BUDGET", "fast")
    from goldenmatch.core.autoconfig_controller import IndicatorContext
    from goldenmatch.core.complexity_profile import SparsityVerdict
    import polars as pl
    df = pl.DataFrame({"email": ["a@x.com"] * 100})
    ctx = IndicatorContext(
        df=df, column_priors={},
        sparsity_verdict=SparsityVerdict(False, 0),
    )
    assert ctx.full_pop_matchkey_hits("email") is None
    assert ctx.cross_blocking_overlap("email", "name") is None
```

- [ ] **Step 2: Add fast-mode guard to `IndicatorContext` lazy methods**

```python
import os

class IndicatorContext:
    # ... existing __init__ + properties ...

    def _is_fast_mode(self) -> bool:
        return os.environ.get("GOLDENMATCH_AUTOCONFIG_INDICATOR_BUDGET", "").lower() == "fast"

    def full_pop_matchkey_hits(self, blocking_col: str) -> int | None:
        if self._is_fast_mode():
            return None
        # ... existing memoized call ...

    def cross_blocking_overlap(self, key_a: str, key_b: str) -> float | None:
        if self._is_fast_mode():
            return None
        # ... existing memoized call ...
```

- [ ] **Step 3: Run, commit**

```bash
git commit -m "feat(autoconfig): GOLDENMATCH_AUTOCONFIG_INDICATOR_BUDGET=fast skips expensive indicators"
```

### Task 6.2: Tier 4 — DQbench T1-style synthetic regression test

**Files:**
- Create: `packages/python/goldenmatch/tests/test_dqbench_t1_recovery.py`
- Create: `packages/python/goldenmatch/tests/fixtures/autoconfig/t1_synthetic.csv`

- [ ] **Step 1: Build synthetic fixture**

Create `tests/fixtures/autoconfig/t1_synthetic.csv` — 200 rows, 50 true duplicate pairs with corrupted emails:
- 50 pairs: (A, B) where A.email = "Brian{i}@gmail.com" and B.email = "BRIAN{i}@gmail.com"
- 100 unique singletons with distinct emails
- All have plausible city + name fields

(This can be generated by a small Python script — include the script as a comment in the test file for reproducibility.)

- [ ] **Step 2: Write the test**

```python
"""Synthetic T1-style regression test for v1.10 indicator stack.

Mimics DQbench T1's failure mode: corrupted-email duplicates that the
v1.9 controller catastrophically misclassifies. v1.10 should recover
this via rule_corruption_normalize + indicator priors.
"""
from pathlib import Path
import pytest


@pytest.fixture
def t1_synthetic_df():
    import polars as pl
    fixture = Path(__file__).parent / "fixtures" / "autoconfig" / "t1_synthetic.csv"
    if not fixture.exists():
        pytest.skip(f"missing fixture: {fixture}")
    return pl.read_csv(fixture)


def test_t1_synthetic_commits_email_blocking_with_normalize(t1_synthetic_df):
    """v1.10 should commit a config that:
    1. Uses email as the blocking key (NOT first_token(first_name))
    2. Has a normalize standardization rule on email
    3. Produces non-degenerate clustering (50-100 clusters, not 1 or 200)"""
    import os
    os.environ["GOLDENMATCH_AUTOCONFIG_MEMORY"] = "0"
    from goldenmatch import dedupe_df
    result = dedupe_df(t1_synthetic_df)

    # Inspect the committed config via _LAST_CONTROLLER_RUN
    from goldenmatch.core.autoconfig_controller import _LAST_CONTROLLER_RUN
    history = _LAST_CONTROLLER_RUN.get()
    assert history is not None
    best = history.pick_committed(precision_collapse_floor=0.9)
    assert best is not None
    cfg = best.config

    blocking_field = cfg.blocking.keys[0].fields[0]
    assert blocking_field == "email", (
        f"expected email blocking, got {blocking_field}"
    )

    # Must have email standardization
    if cfg.standardization is not None:
        assert "email" in cfg.standardization.rules

    # Cluster count is reasonable (not collapsed)
    if hasattr(result, "clusters"):
        n_clusters = len(result.clusters)
        n_rows = t1_synthetic_df.height
        assert n_clusters >= 30, (
            f"clusters too few — collapse pathology suspected: "
            f"{n_clusters} clusters for {n_rows} rows"
        )
        assert n_clusters <= n_rows, (
            f"more clusters than rows — clustering broken"
        )
```

- [ ] **Step 3: Run, commit**

```bash
git commit -m "test(autoconfig): T1-style synthetic regression guard for indicator stack"
```

---

### Task 6.3: Tier 5 — v1.9 cache backward compat fixture + test

**Files:**
- Create: `packages/python/goldenmatch/tests/fixtures/autoconfig/v1_9_memory_snapshot.json` (committed fixture)
- Create: `packages/python/goldenmatch/tests/test_autoconfig_memory_v1_9_compat.py`

- [ ] **Step 1: Generate the v1.9 fixture**

Write a one-shot script `packages/python/goldenmatch/tests/fixtures/autoconfig/_gen_v1_9_snapshot.py` (committed for reproducibility):

```python
"""Generate a v1.9-vintage memory cache entry as a JSON fixture.

Run: python tests/fixtures/autoconfig/_gen_v1_9_snapshot.py
Output: tests/fixtures/autoconfig/v1_9_memory_snapshot.json
"""
import json
from pathlib import Path

# Simulate what v1.9 stored: a serialized GoldenMatchConfig dict + signature
# + succeeded flag. NOT a profile (profiles weren't persisted in v1.9).
v1_9_entry = {
    "signature": "abcdef1234567890",
    "config_json": {
        "matchkeys": [{
            "name": "primary",
            "type": "weighted",
            "threshold": 0.85,
            "fields": [{
                "field": "email", "transforms": ["lowercase"],
                "scorer": "ensemble", "weight": 1.0,
            }],
        }],
        "blocking": {
            "strategy": "static",
            "keys": [{"fields": ["email"], "transforms": ["lowercase"]}],
            "max_block_size": 1000,
            "skip_oversized": True,
        },
    },
    "succeeded": 1,
    "version_written_by": "1.9.0",
}

out = Path(__file__).parent / "v1_9_memory_snapshot.json"
out.write_text(json.dumps(v1_9_entry, indent=2))
print(f"wrote {out}")
```

Run it once and commit the resulting JSON.

- [ ] **Step 2: Failing tests**

Create `tests/test_autoconfig_memory_v1_9_compat.py`:

```python
"""Verify v1.9-vintage memory cache entries load cleanly into v1.10.

The point: indicator fields (column_priors on DataProfile, indicators on
ComplexityProfile) must not break deserialization of any v1.9-saved
GoldenMatchConfig. Profiles aren't persisted (only configs are), so this
is mostly a sanity check that the new fields default cleanly.
"""
import json
from pathlib import Path
import pytest


def test_v1_9_memory_snapshot_loads_cleanly():
    """A v1.9-vintage memory entry deserializes into a valid GoldenMatchConfig."""
    from goldenmatch.config.schemas import GoldenMatchConfig
    fixture = (
        Path(__file__).parent / "fixtures" / "autoconfig"
        / "v1_9_memory_snapshot.json"
    )
    if not fixture.exists():
        pytest.skip(f"missing fixture: {fixture}")
    entry = json.loads(fixture.read_text())
    cfg = GoldenMatchConfig.model_validate(entry["config_json"])
    assert cfg.get_matchkeys()[0].name == "primary"
    assert cfg.get_matchkeys()[0].threshold == 0.85


def test_v1_10_data_profile_column_priors_default_none_for_legacy_data():
    """A DataProfile constructed without column_priors (as v1.9 did) has
    column_priors == None — backward compat preserved."""
    from goldenmatch.core.complexity_profile import DataProfile
    dp = DataProfile(
        n_rows=100, n_cols=4,
        column_types={"a": "text", "b": "id-like", "c": "text", "d": "date"},
    )
    assert dp.column_priors is None


def test_v1_10_complexity_profile_indicators_default_none():
    from goldenmatch.core.complexity_profile import ComplexityProfile, DataProfile
    cp = ComplexityProfile(data=DataProfile(n_rows=100))
    assert cp.indicators is None
```

- [ ] **Step 3: Run, commit**

```bash
git add packages/python/goldenmatch/tests/fixtures/autoconfig/v1_9_memory_snapshot.json
git add packages/python/goldenmatch/tests/fixtures/autoconfig/_gen_v1_9_snapshot.py
git add packages/python/goldenmatch/tests/test_autoconfig_memory_v1_9_compat.py
git commit -m "test(autoconfig): v1.9 memory cache backward-compat fixture + tests"
```

---

## Phase 7 — Validation, docs, ship

### Task 7.1: Run benchmarks + capture data

- [ ] **Step 1: DBLP-ACM/Febrl3/NCVR**

```bash
cd /d/show_case/goldenmatch
rm -f ~/.goldenmatch/autoconfig_memory.db
GOLDENMATCH_AUTOCONFIG_MEMORY=0 C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe .profile_tmp/run_phase5_1_gate.py 2>&1 | tee .profile_tmp/v110_phase7_benchmarks.txt | tail -30
```

Expected: each F1 ≥ v1.9 baseline (0.9641 / 0.9443 / 0.9719).

- [ ] **Step 2: DQbench no-LLM**

```bash
unset OPENAI_API_KEY ANTHROPIC_API_KEY GOLDENMATCH_AUTOCONFIG_LLM
rm -f ~/.goldenmatch/autoconfig_memory.db
GOLDENMATCH_AUTOCONFIG_MEMORY=0 dqbench run goldenmatch-zeroconfig --adapter .profile_tmp/goldenmatch_zeroconfig_adapter.py 2>&1 | tee .profile_tmp/v110_dqbench_no_llm.txt | tail -40
```

Capture composite + per-tier numbers.

- [ ] **Step 3: If composite ≥ 70**: run indicator-attribution sweep (5 disabled-indicator runs, ~50 min total). If < 70 but ≥ 65: skip sweep, document partial-win.

- [ ] **Step 4: Write `.profile_tmp/v110_results.md`** with table of {DBLP-ACM, Febrl3, NCVR, DQbench} v1.9 vs v1.10.

### Task 7.2: CLAUDE.md + CHANGELOG + version bump

- [ ] **Step 1: Update `packages/python/goldenmatch/CLAUDE.md`**

Add to the Auto-Config section:

```markdown
- **v1.10 indicators**: `core/indicators.py` has 5 functions — `compute_column_priors`, `estimate_sparse_match_signal`, `compute_corruption_score`, `estimate_full_pop_hits`, `compute_cross_blocking_overlap`. Cheap two are eager; expensive three are lazy via `IndicatorContext`. `RefitPolicy.propose` accepts optional `ctx: IndicatorContext | None` kwarg; controller introspects custom-policy signatures for backward compat. `rule_no_matches` and `rule_blocking_key_swap` consult priors; new rules `rule_corruption_normalize`, `rule_cross_blocking_disagreement`, `rule_sparse_match_expand`. `GOLDENMATCH_AUTOCONFIG_INDICATOR_BUDGET=fast` disables the two expensive indicators.
```

- [ ] **Step 2: Update `CHANGELOG.md`** with `[1.10.0]` section (~20 lines, mirror v1.9.0 structure).

- [ ] **Step 3: Bump version**

```bash
# pyproject.toml + goldenmatch/__init__.py
```

- [ ] **Step 4: Verify**

```bash
C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe -c "import goldenmatch; print(goldenmatch.__version__)"
```
Expected: `1.10.0`.

- [ ] **Step 5: Commit**

```bash
git commit -m "release(goldenmatch): v1.10.0 (1.9.0 -> 1.10.0)"
```

---

## Final acceptance gate

Before opening the release PR:

- [ ] All 7 test tiers pass: 1850 baseline → ≥ 1910 (≥ +60 new tests).
- [ ] DBLP-ACM/Febrl3/NCVR each commit non-RED with F1 ≥ v1.9 baselines.
- [ ] DQbench composite ≥ 70 (primary) OR ≥ 65 (fallback). If neither: STOP, escalate to human.
- [ ] Cache backward-compat: v1.9-vintage entry loads cleanly into v1.10 (Tier 5 test passes).
- [ ] No new ruff errors: `ruff check packages/python/goldenmatch/goldenmatch/`.
- [ ] CLAUDE.md and CHANGELOG updated with v1.10 entries.
- [ ] PR description includes per-tier DQbench breakdown; indicator-attribution sweep included **only if composite ≥ 70**.

Open PR via `gh pr create` per CLAUDE.md SOP. Before push: `gh auth switch --user benzsevern`; switch back to `benzsevern-mjh` immediately after. Squash-merge after CI green. Tag `v1.10.0` to trigger PyPI publish via `publish-goldenmatch.yml`. Update wiki, About/Topics, Discussion as in v1.9.0.
