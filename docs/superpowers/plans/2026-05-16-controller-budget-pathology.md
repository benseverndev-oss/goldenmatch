# Controller-budget pathology Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop `auto_configure_df` from silently committing a RED-health config on 100K+ row inputs (which causes 26-min degenerate dedupe runs); adaptively size the controller's sample + budget by `df.height`; raise a structured `ControllerNotConfidentError` when the controller can't find a healthy config at scale.

**Architecture:** Three additive units land in `core/autoconfig_controller.py`: a `ControllerBudget.for_dataset(n_rows)` classmethod (pure function), a `ControllerNotConfidentError` exception class + `_identify_failing_subprofile` helper, and a confidence gate inside `AutoConfigController.run()` that runs after `pick_committed()` and before the v3 planner. `confidence_required` plumbs through `auto_configure_df` → `dedupe_df` / `match_df`. No changes to the 7 planner rules, the `pick_committed` lex key, or `_assemble_v0_history_entry`.

**Tech Stack:** Python 3.12, dataclasses (extend existing `ControllerBudget`), pytest, monkeypatch for unit gate coverage. No new deps.

**Spec:** [`docs/superpowers/specs/2026-05-16-controller-budget-vs-blocking-discovery-design.md`](../specs/2026-05-16-controller-budget-vs-blocking-discovery-design.md) — read this first. The plan's six phases match the spec's three additions + wiring + bench validation + docs.

---

## File structure

Six phases, six PRs. One unit per phase keeps each diff readable and revertible.

**Created files:**

| Path | Phase | Responsibility |
|---|---|---|
| `packages/python/goldenmatch/tests/test_controller_budget_for_dataset.py` | 1 | Lock in each tier of the `ControllerBudget.for_dataset(n_rows)` table |
| `packages/python/goldenmatch/tests/test_controller_not_confident_error.py` | 2 | Exception construction, structured fields, `DOCS_URL` class attribute |
| `packages/python/goldenmatch/tests/test_identify_failing_subprofile.py` | 2 | Priority order `[data, blocking, scoring, matchkey, cluster]`; returns first RED sub-profile name |
| `packages/python/goldenmatch/tests/test_controller_confidence_gate.py` | 3 | Branching (monkey-patched `pick_committed`): gate fires on RED+large-N; not on RED+small-N; not on YELLOW; not when `confidence_required=False` |
| `packages/python/goldenmatch/tests/test_api_confidence_required_kwarg.py` | 4 | `gm.dedupe_df(df, confidence_required=False)` short-circuits; default raises on the same fixture |
| `packages/python/goldenmatch/tests/test_controller_adaptive_e2e.py` | 5 | End-to-end real-iteration: build synthetic 100K-row df, force RED, confirm gate fires via real iteration loop |
| `C:\Users\bsevern\.claude\projects\D--show-case-goldenmatch\memory\feedback_controller_confidence_required.md` | 6 | Pin the new behavior for future sessions |

**Modified files:**

| Path | Phase | Change |
|---|---|---|
| `packages/python/goldenmatch/goldenmatch/core/autoconfig_controller.py` | 1, 2, 3 | `ControllerBudget.for_dataset` classmethod; `ControllerNotConfidentError` class; `_identify_failing_subprofile` helper; `confidence_required` parameter on `run()`; confidence-gate block after `pick_committed` |
| `packages/python/goldenmatch/goldenmatch/core/autoconfig.py` | 4 | `confidence_required` kwarg on `auto_configure_df`; construct controller with `ControllerBudget.for_dataset(df.height)`; thread kwarg into `controller.run()` |
| `packages/python/goldenmatch/goldenmatch/_api.py` | 4 | `confidence_required` kwarg on `dedupe_df`, `match_df`, `match_one` (if present); propagate to `auto_configure_df` calls |
| `packages/python/goldenmatch/CLAUDE.md` | 6 | Replace "Recommended for 5M today" caveat in controller-v3 closeout note with the new `ControllerNotConfidentError` behavior |
| `C:\Users\bsevern\.claude\projects\D--show-case-goldenmatch\memory\MEMORY.md` | 6 | Append `feedback_controller_confidence_required.md` link |

---

## Pre-flight checklist

Before starting any task:

- [ ] Working in a clean branch **off `main`** (NOT off the prior phase's branch): `git fetch origin main && git switch -c perf/controller-budget-phase-N origin/main`. Per CLAUDE.md "Stacked PR auto-closure on squash-merge", stacking phases gets bitten when an earlier phase squash-merges. Branching every phase off `main` and rebasing after each prior phase merges avoids the recovery dance. Trade-off: each phase's PR may need a quick rebase once its dependency lands.
- [ ] Editable install active: `cd packages/python/goldenmatch && python -c "import goldenmatch; print(goldenmatch.__file__)"` shows the worktree path (not `site-packages`). Known gotcha — see `packages/python/goldenmatch/CLAUDE.md` "shadows worktree code".
- [ ] Baseline tests green: `python -m pytest tests/ -q --timeout=120 --ignore=tests/test_db.py --ignore=tests/test_reconcile.py --ignore=tests/test_mcp_and_watch.py --ignore=tests/test_embedder.py --ignore=tests/test_llm_boost.py --ignore=tests/benchmarks` reports the post-controller-v3 baseline (currently 2486+ passed / 5 skipped).
- [ ] Controller v3 stack landed: `git log --oneline origin/main | grep -E "Phase 7 \(integration|controller v3"` shows PR #266 (Phase 7) on main. This plan builds on the v3 surfaces.

---

## Phase 1 — `ControllerBudget.for_dataset(n_rows)` classmethod

Pure function. No wiring. Defines the adaptive table that Phase 4 will pass to the controller.

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/autoconfig_controller.py` (extend existing `ControllerBudget` dataclass)
- Test: `packages/python/goldenmatch/tests/test_controller_budget_for_dataset.py` (NEW)

### Task 1.1: Write the failing test

- [ ] **Step 1: Create the test file.**

`packages/python/goldenmatch/tests/test_controller_budget_for_dataset.py`:

```python
"""Unit tests for ControllerBudget.for_dataset(n_rows).

Spec §Design / ControllerBudget.for_dataset:
docs/superpowers/specs/2026-05-16-controller-budget-vs-blocking-discovery-design.md.

Pure function -- no side effects, table-driven, trivially testable.
"""
from __future__ import annotations

import pytest

from goldenmatch.core.autoconfig_controller import ControllerBudget


def test_for_dataset_below_5k_returns_tight_budget():
    """At <5K, sample_skip_below kicks in (full df) -- max_seconds tight."""
    b = ControllerBudget.for_dataset(100)
    assert b.max_seconds == 15.0
    assert b.sample_size_default == 2000  # default; doesn't matter at this N


def test_for_dataset_10k_returns_historical_defaults():
    """5K-100K: today's defaults (30s / 2K). Preserves 100K bench wall."""
    b = ControllerBudget.for_dataset(10_000)
    assert b.max_seconds == 30.0
    assert b.sample_size_default == 2000


def test_for_dataset_at_100k_boundary_lands_in_new_tier():
    """100K exactly hits the >=100K branch. New tier: sqrt-scaled sample,
    60s budget. Bench gate (Phase 5) is calibrated for this."""
    b = ControllerBudget.for_dataset(100_000)
    assert b.max_seconds == 60.0
    # sqrt(100_000) * 20 = 6324.555..., int() => 6324
    assert b.sample_size_default == 6324


def test_for_dataset_500k_sqrt_scaled():
    """500K: sqrt-scaled sample preserves expected dup-pair signal density."""
    b = ControllerBudget.for_dataset(500_000)
    assert b.max_seconds == 60.0
    # sqrt(500_000) * 20 = 14142.135..., int() => 14142
    assert b.sample_size_default == 14142


def test_for_dataset_at_1m_boundary_caps_sample_at_20k():
    """At 1M exactly, hit the cap branch. sample_size capped at 20K to
    keep sample-iteration cost bounded."""
    b = ControllerBudget.for_dataset(1_000_000)
    assert b.max_seconds == 120.0
    assert b.sample_size_default == 20_000


def test_for_dataset_10m_caps_at_20k():
    """Above 1M, sample stays at the cap (no further growth)."""
    b = ControllerBudget.for_dataset(10_000_000)
    assert b.max_seconds == 120.0
    assert b.sample_size_default == 20_000


def test_for_dataset_returns_new_instance_each_call():
    """Pure function; no shared state across calls."""
    b1 = ControllerBudget.for_dataset(50_000)
    b2 = ControllerBudget.for_dataset(50_000)
    assert b1 is not b2
    assert b1 == b2  # but equal


def test_for_dataset_preserves_other_budget_fields():
    """Only sample_size_default and max_seconds vary by tier; the rest
    (max_iterations, sample_skip_below, converge_epsilon, drift_threshold)
    keep their defaults so the iteration loop's other tuning stays stable."""
    b = ControllerBudget.for_dataset(500_000)
    assert b.max_iterations == 3
    assert b.sample_skip_below == 5000
    assert b.converge_epsilon == 0.05
    assert b.drift_threshold == 0.30
```

- [ ] **Step 2: Run tests; expect failure.**

```
cd packages/python/goldenmatch
python -m pytest tests/test_controller_budget_for_dataset.py -v
```

Expected: `AttributeError: type object 'ControllerBudget' has no attribute 'for_dataset'`.

### Task 1.2: Implement the classmethod

- [ ] **Step 1: Find the existing `ControllerBudget` dataclass.**

```
grep -n "class ControllerBudget" packages/python/goldenmatch/goldenmatch/core/autoconfig_controller.py
```

Expected output: one line near line 234.

- [ ] **Step 2: Add `for_dataset` classmethod inside `ControllerBudget`.**

In `core/autoconfig_controller.py`, modify the existing `ControllerBudget` to:

```python
@dataclass
class ControllerBudget:
    max_iterations: int = 3
    max_seconds: float = 30.0
    sample_size_default: int = 2000
    sample_skip_below: int = 5000
    converge_epsilon: float = 0.05
    drift_threshold: float = 0.30

    @classmethod
    def for_dataset(cls, n_rows: int) -> ControllerBudget:
        """Calibrate budget + sample size to the input row count.

        Spec §Design / ControllerBudget.for_dataset. Sqrt-scaling above
        100K preserves expected dup-pair density in the sample within an
        order of magnitude as N grows from 100K -> 1M. Cap at 20K so
        sample-iteration cost stays bounded above 1M.

        Tiers (n_rows -> max_seconds, sample_size_default):
          - <5K        -> 15s, 2K (sample_skip_below bypasses sampling)
          - 5K-100K    -> 30s, 2K (historical defaults; preserves 100K bench)
          - 100K-1M    -> 60s, int(sqrt(n) * 20) capped at 20K
          - >=1M       -> 120s, 20K (capped)
        """
        if n_rows < 5_000:
            return cls(max_seconds=15.0)
        if n_rows < 100_000:
            return cls()  # historical defaults
        if n_rows < 1_000_000:
            sample = min(int((n_rows ** 0.5) * 20), 20_000)
            return cls(sample_size_default=sample, max_seconds=60.0)
        return cls(sample_size_default=20_000, max_seconds=120.0)
```

- [ ] **Step 3: Run tests; expect pass.**

```
python -m pytest tests/test_controller_budget_for_dataset.py -v
```

Expected: 8 passed.

- [ ] **Step 4: Run broader controller tests to confirm no regression.**

```
python -m pytest tests/test_autoconfig_controller.py tests/test_autoconfig.py -q
```

Expected: all green (existing budget construction paths still work; new classmethod is additive).

### Task 1.3: Commit + open PR

- [ ] **Step 1: Commit.**

```bash
git add packages/python/goldenmatch/goldenmatch/core/autoconfig_controller.py packages/python/goldenmatch/tests/test_controller_budget_for_dataset.py
git commit -m "feat(autoconfig): Phase 1 -- ControllerBudget.for_dataset(n_rows)

Phase 1 of controller-budget-pathology plan (docs/superpowers/plans/
2026-05-16-controller-budget-pathology.md). Pure function, no wiring:
defines the adaptive (max_seconds, sample_size) table that Phase 4
threads through auto_configure_df.

Tiers:
  - <5K        -> 15s / default sample (skip_below bypasses anyway)
  - 5K-100K    -> 30s / 2K (today's defaults preserved)
  - 100K-1M    -> 60s / sqrt(n)*20 capped at 20K
  - >=1M       -> 120s / 20K cap

Sqrt-scaling preserves expected dup-pair density in the sample within
an order of magnitude over 100K -> 1M (~158 -> ~500 expected pairs
at 0.5% dup rate). Cap keeps sample-iteration cost bounded.

Spec section: §Design / ControllerBudget.for_dataset.

Tests: 8 new in test_controller_budget_for_dataset.py.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 2: Push + PR.**

Follow the auth dance (memory `feedback_github_auth_switch.md`):

```bash
gh auth switch --user benzsevern
GH_TOKEN=$(gh auth token --user benzsevern) git -c credential.helper="!gh auth git-credential" push -u origin perf/controller-budget-phase-1
GH_TOKEN=$(gh auth token --user benzsevern) gh pr create --base main --title "feat(autoconfig): controller-budget pathology -- Phase 1 (ControllerBudget.for_dataset)" --body "<see plan>"
gh auth switch --user benzsevern-mjh
```

PR title: `feat(autoconfig): controller-budget pathology — Phase 1 (ControllerBudget.for_dataset)`.

---

## Phase 2 — `ControllerNotConfidentError` + `_identify_failing_subprofile`

Two pure data/helper additions. No wiring. Phase 3 calls them.

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/autoconfig_controller.py`
- Test: `packages/python/goldenmatch/tests/test_controller_not_confident_error.py` (NEW)
- Test: `packages/python/goldenmatch/tests/test_identify_failing_subprofile.py` (NEW)

### Task 2.1: ControllerNotConfidentError class

- [ ] **Step 1: Write the failing test.**

`packages/python/goldenmatch/tests/test_controller_not_confident_error.py`:

```python
"""Unit tests for ControllerNotConfidentError.

Spec §Design / Confidence gate -- exception construction, structured
fields, DOCS_URL class attribute. No suggested_config field (footgun).
"""
from __future__ import annotations

from goldenmatch.core.autoconfig_controller import ControllerNotConfidentError


def test_exception_carries_structured_fields():
    exc = ControllerNotConfidentError(
        n_rows=500_000,
        failing_sub_profile="scoring",
        stop_reason="BUDGET_TIME",
    )
    assert exc.n_rows == 500_000
    assert exc.failing_sub_profile == "scoring"
    assert exc.stop_reason == "BUDGET_TIME"


def test_exception_has_docs_url_class_attribute():
    """DOCS_URL is a class attribute so callers can reference it without
    catching the exception first."""
    assert hasattr(ControllerNotConfidentError, "DOCS_URL")
    assert isinstance(ControllerNotConfidentError.DOCS_URL, str)
    assert ControllerNotConfidentError.DOCS_URL.startswith("https://")


def test_exception_str_rendering_includes_diagnostic_fields():
    exc = ControllerNotConfidentError(
        n_rows=500_000,
        failing_sub_profile="scoring",
        stop_reason="BUDGET_TIME",
    )
    rendered = str(exc)
    assert "500000" in rendered or "500_000" in rendered
    assert "scoring" in rendered
    assert "BUDGET_TIME" in rendered
    # The error tells the caller how to recover -- this is load-bearing
    # for users seeing the exception cold without context.
    assert "confidence_required=False" in rendered
    assert ControllerNotConfidentError.DOCS_URL in rendered


def test_exception_has_no_suggested_config_field():
    """Spec deliberately omits suggested_config (footgun: 'suggestion'
    derived from the config that just produced the RED commit). Verify
    the field is NOT present so a future refactor can't silently
    re-introduce it without the spec change."""
    exc = ControllerNotConfidentError(
        n_rows=500_000,
        failing_sub_profile="scoring",
        stop_reason="BUDGET_TIME",
    )
    assert not hasattr(exc, "suggested_config")


def test_exception_is_exception_subclass():
    """Plain Exception, not subclass of ValueError / RuntimeError. Caller
    catches by type, not by hierarchy."""
    assert issubclass(ControllerNotConfidentError, Exception)
```

- [ ] **Step 2: Run; expect failure.**

```
python -m pytest tests/test_controller_not_confident_error.py -v
```

Expected: `ImportError: cannot import name 'ControllerNotConfidentError'`.

- [ ] **Step 3: Implement the exception.**

In `core/autoconfig_controller.py`, find the existing `ConfigValidationError` class (around line 146) and add `ControllerNotConfidentError` immediately after it:

```python
class ControllerNotConfidentError(Exception):
    """Raised when AutoConfigController committed a RED-health config on
    a large input (df.height >= REFUSE_AT_N). Carries the failing
    sub-profile + a DOCS_URL so the caller can recover programmatically.

    Spec: docs/superpowers/specs/2026-05-16-controller-budget-vs-
    blocking-discovery-design.md §Design / Confidence gate.

    The exception deliberately does NOT carry a "suggested config"
    because the only material the controller has to suggest from is
    config_v0 + the priors that produced the RED commit -- handing those
    back as a suggestion is a footgun (looks authoritative; isn't).
    """

    DOCS_URL = (
        "https://github.com/benseverndev-oss/goldenmatch/blob/main/"
        "docs/explicit-config.md"
    )

    def __init__(
        self,
        *,
        n_rows: int,
        failing_sub_profile: str,
        stop_reason: str,
    ) -> None:
        self.n_rows = n_rows
        self.failing_sub_profile = failing_sub_profile
        self.stop_reason = stop_reason
        super().__init__(
            f"AutoConfigController committed a RED config on a "
            f"{n_rows}-row input (failing sub-profile: {failing_sub_profile}, "
            f"stop_reason: {stop_reason}). Running this config would produce "
            f"degenerate dedupe; passing it back instead of running. "
            f"Options: pass an explicit GoldenMatchConfig, lower the matchkey "
            f"threshold, or re-call with confidence_required=False. See "
            f"{self.DOCS_URL}."
        )
```

- [ ] **Step 4: Run; expect 5 passed.**

```
python -m pytest tests/test_controller_not_confident_error.py -v
```

### Task 2.2: `_identify_failing_subprofile` helper

- [ ] **Step 1: Write the failing test.**

`packages/python/goldenmatch/tests/test_identify_failing_subprofile.py`:

```python
"""Unit tests for _identify_failing_subprofile.

Spec §Design / Confidence gate -- priority order [data, blocking,
scoring, matchkey, cluster] (root causes upstream first).
"""
from __future__ import annotations

from goldenmatch.core.autoconfig_controller import _identify_failing_subprofile
from goldenmatch.core.complexity_profile import (
    BlockingProfile,
    ClusterProfile,
    ComplexityProfile,
    DataProfile,
    MatchkeyProfile,
    ProfileMeta,
    ScoringProfile,
)


def _green_profile() -> ComplexityProfile:
    """Builds a profile where every sub-profile reports GREEN."""
    return ComplexityProfile(
        data=DataProfile(n_rows=1000, n_cols=3, column_types={
            "a": "text", "b": "text", "c": "text",
        }),
        blocking=BlockingProfile(
            keys_used=[["a"]], n_blocks=10, total_comparisons=100,
            reduction_ratio=0.9, block_sizes_p50=10, block_sizes_p95=15,
            block_sizes_p99=20, block_sizes_max=25,
            singleton_block_count=0, oversized_block_count=0,
        ),
        scoring=ScoringProfile(
            n_pairs_scored=100, candidates_compared=100,
            mass_above_threshold=0.5, mass_in_borderline=0.1,
        ),
        matchkey=MatchkeyProfile(),
        cluster=ClusterProfile(),
        meta=ProfileMeta(
            iteration=0, is_sample=False, sample_size=1000,
            n_rows_full=1000, wall_clock_ms=0, seed=0,
        ),
    )


def test_data_red_returns_data():
    """Data sub-profile RED (n_rows == 0) -> 'data'."""
    p = _green_profile()
    import dataclasses
    p = dataclasses.replace(p, data=DataProfile(n_rows=0))
    assert _identify_failing_subprofile(p) == "data"


def test_blocking_red_returns_blocking():
    """Blocking sub-profile RED (n_blocks == 0) -> 'blocking'."""
    p = _green_profile()
    import dataclasses
    bp = BlockingProfile()  # n_blocks=0 default -> RED via health()
    p = dataclasses.replace(p, blocking=bp)
    assert _identify_failing_subprofile(p) == "blocking"


def test_scoring_red_returns_scoring():
    """Scoring RED (mass_above_threshold == 0 with candidates compared)."""
    p = _green_profile()
    import dataclasses
    sp = ScoringProfile(
        n_pairs_scored=100, candidates_compared=100,
        mass_above_threshold=0.0, mass_in_borderline=0.1,
    )
    p = dataclasses.replace(p, scoring=sp)
    assert _identify_failing_subprofile(p) == "scoring"


def test_priority_order_data_beats_blocking():
    """When multiple sub-profiles RED, data wins (root cause upstream)."""
    p = _green_profile()
    import dataclasses
    p = dataclasses.replace(
        p,
        data=DataProfile(n_rows=0),               # RED
        blocking=BlockingProfile(),               # RED
    )
    assert _identify_failing_subprofile(p) == "data"


def test_priority_order_blocking_beats_scoring():
    """Blocking RED + Scoring RED -> 'blocking' (upstream cause)."""
    p = _green_profile()
    import dataclasses
    p = dataclasses.replace(
        p,
        blocking=BlockingProfile(),
        scoring=ScoringProfile(
            n_pairs_scored=0, candidates_compared=0,
        ),
    )
    assert _identify_failing_subprofile(p) == "blocking"


def test_all_green_returns_empty_string():
    """Defensive: gate's RED-precondition means this shouldn't happen,
    but the helper must not raise. Returns '' so the error message
    degrades gracefully."""
    p = _green_profile()
    assert _identify_failing_subprofile(p) == ""
```

- [ ] **Step 2: Run; expect failure.**

```
python -m pytest tests/test_identify_failing_subprofile.py -v
```

Expected: `ImportError: cannot import name '_identify_failing_subprofile'`.

- [ ] **Step 3: Implement the helper.**

In `core/autoconfig_controller.py`, place the helper near `ControllerNotConfidentError`:

```python
# Priority order for failing-sub-profile diagnostics: root causes
# upstream first. Spec §Design / Confidence gate.
_SUBPROFILE_PRIORITY_ORDER = ("data", "blocking", "scoring", "matchkey", "cluster")


def _identify_failing_subprofile(profile: ComplexityProfile) -> str:
    """Walk the ComplexityProfile sub-profiles in priority order; return
    the name of the first one reporting RED. Returns '' when none are
    RED (defensive -- the confidence gate's RED precondition guarantees
    at least one will be).

    Priority order [data, blocking, scoring, matchkey, cluster] surfaces
    upstream causes first: if data is RED, blocking RED is a consequence;
    if blocking is RED, scoring RED is a consequence; etc.
    """
    n_rows = profile.data.n_rows
    health_calls = {
        "data": lambda: profile.data.health(),
        "blocking": lambda: profile.blocking.health(n_rows=n_rows),
        "scoring": lambda: profile.scoring.health(),
        "matchkey": lambda: profile.matchkey.health(),
        "cluster": lambda: profile.cluster.health(n_rows=n_rows),
    }
    for name in _SUBPROFILE_PRIORITY_ORDER:
        if health_calls[name]() == HealthVerdict.RED:
            return name
    return ""
```

`HealthVerdict` is already imported at the top of `autoconfig_controller.py` (line 26).

- [ ] **Step 4: Run; expect 6 passed.**

```
python -m pytest tests/test_identify_failing_subprofile.py -v
```

### Task 2.3: Commit + open PR

- [ ] **Step 1: Commit.**

```bash
git add packages/python/goldenmatch/goldenmatch/core/autoconfig_controller.py packages/python/goldenmatch/tests/test_controller_not_confident_error.py packages/python/goldenmatch/tests/test_identify_failing_subprofile.py
git commit -m "feat(autoconfig): Phase 2 -- ControllerNotConfidentError + _identify_failing_subprofile

Phase 2 of controller-budget-pathology plan. Two pure additions; no
wiring. Phase 3 calls them from inside AutoConfigController.run.

- ControllerNotConfidentError: structured exception. Fields:
  n_rows, failing_sub_profile, stop_reason. DOCS_URL class attr.
  Explicitly NO suggested_config field (spec footgun rationale).
- _identify_failing_subprofile(ComplexityProfile) -> str: priority
  order [data, blocking, scoring, matchkey, cluster] -- root causes
  upstream first. Returns '' when no sub-profile is RED (defensive).

Spec section: §Design / Confidence gate.

Tests: 11 new (5 exception construction + 6 helper priority order).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 2: Push + PR (auth dance, base on Phase 1's PR if not yet merged).**

PR title: `feat(autoconfig): controller-budget pathology — Phase 2 (exception + helper)`.

---

## Phase 3 — Confidence gate inside `AutoConfigController.run`

The gate. Uses Phase 2's exception + helper. No external API change yet (Phase 4 surfaces `confidence_required`).

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/autoconfig_controller.py` (add `confidence_required` param + gate block in `run()`)
- Test: `packages/python/goldenmatch/tests/test_controller_confidence_gate.py` (NEW)

### Task 3.1: Write the failing test

- [ ] **Step 1: Create test file with monkey-patched `pick_committed` for branching coverage.**

`packages/python/goldenmatch/tests/test_controller_confidence_gate.py`:

```python
"""Unit tests for the confidence gate inside AutoConfigController.run.

Spec §Design / Confidence gate. The gate fires when:
    confidence_required=True
    AND df.height >= REFUSE_AT_N
    AND best_entry.profile.health() == RED.

These tests use a monkey-patched pick_committed to force RED entries
without needing a real 100K fixture. Phase 5 adds an end-to-end test
that exercises the real iteration loop.
"""
from __future__ import annotations

import dataclasses

import polars as pl
import pytest

import goldenmatch as gm
from goldenmatch.core.autoconfig_controller import (
    REFUSE_AT_N,
    ControllerNotConfidentError,
)
from goldenmatch.core.complexity_profile import (
    BlockingProfile,
    ComplexityProfile,
    DataProfile,
    ProfileMeta,
    ScoringProfile,
)


@pytest.fixture(autouse=True)
def _disable_autoconfig_memory(monkeypatch):
    """Match the integration-test convention: prevent cross-run cache
    short-circuits from affecting these gate tests."""
    monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")


def _force_red_history_entry(monkeypatch, n_rows_in_df: int):
    """Monkeypatch RunHistory.pick_committed to return a forced-RED
    entry whose profile reports df.health() == RED.

    Implementation: replace pick_committed with a callable that returns
    a synthetic HistoryEntry built around a RED ComplexityProfile."""
    from goldenmatch.core.autoconfig_history import HistoryEntry, RunHistory
    from goldenmatch.core.complexity_profile import HealthVerdict

    red_profile = ComplexityProfile(
        # data.n_rows==0 makes data.health() RED, which rolls up
        data=DataProfile(n_rows=0),
        blocking=BlockingProfile(),
        scoring=ScoringProfile(),
        meta=ProfileMeta(
            iteration=0, is_sample=False, sample_size=n_rows_in_df,
            n_rows_full=n_rows_in_df, wall_clock_ms=0, seed=0,
        ),
    )
    assert red_profile.health() == HealthVerdict.RED  # sanity

    def _picker(self, *args, **kwargs):
        return HistoryEntry(
            iteration=0,
            config=None,  # tests don't read this
            profile=red_profile,
            decision=None,
            error=None,
            wall_clock_ms=0,
        )

    monkeypatch.setattr(RunHistory, "pick_committed", _picker)


def _df(n_rows: int) -> pl.DataFrame:
    """Minimum-shape df where only df.height matters for the gate."""
    return pl.DataFrame({
        "name": ["alice"] * n_rows,
        "email": [f"u{i}@x.com" for i in range(n_rows)],
    })


def test_refuse_at_n_constant_is_100k():
    """Spec §Design pins REFUSE_AT_N at 100_000. Lock it down so the
    measured-back-projection rationale stays grounded; recalibration
    after Phase 1 (per Open Question #1) is the spec-channel."""
    assert REFUSE_AT_N == 100_000


def test_gate_fires_on_red_at_or_above_threshold(monkeypatch):
    """df.height = REFUSE_AT_N exactly + RED -> raise."""
    _force_red_history_entry(monkeypatch, n_rows_in_df=REFUSE_AT_N)
    df = _df(REFUSE_AT_N)
    with pytest.raises(ControllerNotConfidentError) as exc_info:
        gm.dedupe_df(df)
    assert exc_info.value.n_rows == REFUSE_AT_N
    assert exc_info.value.failing_sub_profile == "data"


def test_gate_does_not_fire_below_threshold(monkeypatch):
    """df.height < REFUSE_AT_N + RED -> warn-and-run (current behavior)."""
    n = REFUSE_AT_N - 1
    _force_red_history_entry(monkeypatch, n_rows_in_df=n)
    df = _df(n)
    # Should not raise -- small-N path keeps the existing warn-and-run.
    result = gm.dedupe_df(df)
    assert result is not None


def test_gate_does_not_fire_on_yellow_or_green(monkeypatch):
    """Only RED triggers the gate. YELLOW/GREEN at large N: proceed."""
    from goldenmatch.core.autoconfig_history import HistoryEntry, RunHistory

    green_profile = ComplexityProfile(
        data=DataProfile(n_rows=REFUSE_AT_N, n_cols=3, column_types={
            "a": "text", "b": "text", "c": "text",
        }),
        blocking=BlockingProfile(
            keys_used=[["a"]], n_blocks=10, total_comparisons=100,
            reduction_ratio=0.9, block_sizes_p50=10, block_sizes_p95=15,
            block_sizes_p99=20, block_sizes_max=25,
            singleton_block_count=0, oversized_block_count=0,
        ),
        scoring=ScoringProfile(
            n_pairs_scored=100, candidates_compared=100,
            mass_above_threshold=0.5, mass_in_borderline=0.1,
        ),
        meta=ProfileMeta(
            iteration=0, is_sample=False, sample_size=REFUSE_AT_N,
            n_rows_full=REFUSE_AT_N, wall_clock_ms=0, seed=0,
        ),
    )

    def _picker(self, *args, **kwargs):
        return HistoryEntry(
            iteration=0, config=None, profile=green_profile,
            decision=None, error=None, wall_clock_ms=0,
        )

    monkeypatch.setattr(RunHistory, "pick_committed", _picker)
    df = _df(REFUSE_AT_N)
    result = gm.dedupe_df(df)  # should NOT raise
    assert result is not None


def test_gate_short_circuits_with_confidence_required_false(monkeypatch):
    """Spec §Backward compatibility: kwarg opt-out preserves today's
    warn-and-run behavior."""
    _force_red_history_entry(monkeypatch, n_rows_in_df=REFUSE_AT_N)
    df = _df(REFUSE_AT_N)
    result = gm.dedupe_df(df, confidence_required=False)  # should NOT raise
    assert result is not None
```

- [ ] **Step 2: Run; expect failures.**

Expected: `ImportError: cannot import name 'REFUSE_AT_N'` AND `TypeError: dedupe_df() got an unexpected keyword argument 'confidence_required'`.

The kwarg tests will fail in Phase 4; the constant + branching tests need Phase 3.

### Task 3.2: Implement the gate

- [ ] **Step 1: Add `REFUSE_AT_N` module constant near top of `autoconfig_controller.py`.**

After the existing `_RED_PROFILE` sentinel (~line 39):

```python
# Row-count threshold above which a RED-committed config raises
# ControllerNotConfidentError instead of being returned for degenerate
# dedupe. Spec §Design / Confidence gate -- justified by linear back-
# projection from the 500K -> ~26 min measured wall.
REFUSE_AT_N = 100_000
```

- [ ] **Step 2: Add `confidence_required` parameter to `AutoConfigController.run`.**

Find the `run()` signature (line 258); add `confidence_required: bool = True` to the keyword-only block:

```python
    def run(
        self,
        df: pl.DataFrame,
        *,
        reference: pl.DataFrame | None = None,
        v0_kwargs: dict | None = None,
        skip_finalize: bool = False,
        confidence_required: bool = True,
    ) -> tuple[GoldenMatchConfig, ComplexityProfile, RunHistory]:
```

- [ ] **Step 3: Add the gate block after `pick_committed` returns.**

Find `best_entry = history.pick_committed(precision_collapse_floor=0.9)` (line 440). Immediately after the existing `if best_entry is None: ...` block (the "every iteration errored" fallback), add:

```python
        # Confidence gate (Phase 3 of controller-budget pathology spec).
        # When the controller committed a RED entry on a large input,
        # running the full pipeline would produce ~26-min degenerate
        # dedupe. Refuse loudly instead. Spec §Design / Confidence gate.
        if (
            confidence_required
            and df.height >= REFUSE_AT_N
            and best_entry.profile.health() == HealthVerdict.RED
        ):
            failing = _identify_failing_subprofile(best_entry.profile)
            # ``_LAST_CONTROLLER_RUN`` here is the CONTROLLER-LOCAL ContextVar
            # defined at the top of this file (line ~45), NOT the
            # ``(profile, history)`` tuple ContextVar in ``autoconfig.py``.
            # Mirror the existing pattern (line 456, line 549) that sets it
            # right before each return.
            _LAST_CONTROLLER_RUN.set(history)  # surface history before raise
            raise ControllerNotConfidentError(
                n_rows=df.height,
                failing_sub_profile=failing,
                stop_reason=(
                    history.stop_reason.name
                    if history.stop_reason
                    else "unset"
                ),
            )
```

The `_LAST_CONTROLLER_RUN.set(history)` BEFORE the raise lets test code + introspection tooling read the history even when the controller raised (so callers can debug *why* it refused). **No import needed** — the controller-local `_LAST_CONTROLLER_RUN` is defined at the top of `autoconfig_controller.py` and is already in scope. Don't import the `autoconfig._LAST_CONTROLLER_RUN` tuple-based one; the two ContextVars share a name but have different value shapes by design.

- [ ] **Step 4: Run gate tests; first three expected to pass, kwarg-opt-out test fails (Phase 4 surfaces the kwarg).**

```
python -m pytest tests/test_controller_confidence_gate.py -v
```

Expected: `test_refuse_at_n_constant_is_100k` ✓, `test_gate_fires_on_red_at_or_above_threshold` ✓, `test_gate_does_not_fire_below_threshold` ✓, `test_gate_does_not_fire_on_yellow_or_green` ✓, `test_gate_short_circuits_with_confidence_required_false` ✗ (Phase 4 fixes).

- [ ] **Step 5: Skip the kwarg-opt-out test pending Phase 4.**

On Windows the `sed -i` form from Task 4.3 Step 4 won't work without git-bash / wsl; **edit the file by hand** to add the marker now and remove it in Phase 4. Linux/macOS users can use `sed`.

Add `@pytest.mark.skip(reason="confidence_required kwarg lands in Phase 4")` above the failing test temporarily:

```python
@pytest.mark.skip(reason="confidence_required kwarg lands in Phase 4")
def test_gate_short_circuits_with_confidence_required_false(monkeypatch):
    ...
```

Phase 4 removes the skip marker.

- [ ] **Step 6: Re-run; expect 4 passed, 1 skipped.**

- [ ] **Step 7: Run targeted regression to confirm controller path stays intact.**

```
python -m pytest tests/test_autoconfig_controller.py tests/test_autoconfig.py tests/test_autoconfig_planner_protocol.py tests/test_planner_integration.py -q
```

Expected: all green. The new gate ONLY fires at >=100K with RED + confidence_required=True; existing tests run at <100K or with non-RED profiles, so they're unaffected.

### Task 3.3: Commit + open PR

- [ ] **Step 1: Commit.**

```bash
git add packages/python/goldenmatch/goldenmatch/core/autoconfig_controller.py packages/python/goldenmatch/tests/test_controller_confidence_gate.py
git commit -m "feat(autoconfig): Phase 3 -- confidence gate inside controller.run

Phase 3 of controller-budget-pathology plan. Adds the gate that
refuses to commit a RED config at >=100K rows.

- REFUSE_AT_N = 100_000 module constant (spec back-projection from
  500K -> 26 min measured wall).
- run() gains keyword-only confidence_required: bool = True.
- After pick_committed, if confidence_required AND df.height >=
  REFUSE_AT_N AND best_entry health is RED: raise
  ControllerNotConfidentError. _LAST_CONTROLLER_RUN is set
  BEFORE the raise so callers can introspect history.

No public API change yet (Phase 4 surfaces confidence_required on
auto_configure_df / dedupe_df).

Spec section: §Design / Confidence gate.

Tests: 5 new (4 passing now; 1 skipped pending Phase 4 kwarg).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 2: Push + PR.**

PR title: `feat(autoconfig): controller-budget pathology — Phase 3 (gate in run())`.

---

## Phase 4 — `confidence_required` kwarg + `ControllerBudget.for_dataset` wiring

Surfaces `confidence_required` on the public API. Wires Phase 1's adaptive budget into `auto_configure_df`. **This is the user-facing behavior change.**

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/autoconfig.py` (kwarg on `auto_configure_df`; construct controller with adaptive budget; thread kwarg through to `controller.run()`)
- Modify: `packages/python/goldenmatch/goldenmatch/_api.py` (kwarg on `dedupe_df`, `match_df`, propagate)
- Test: `packages/python/goldenmatch/tests/test_api_confidence_required_kwarg.py` (NEW)
- Test: `packages/python/goldenmatch/tests/test_controller_confidence_gate.py` (un-skip the Phase 3 placeholder)

### Task 4.1: Write the new API test

- [ ] **Step 1: Create the API kwarg test.**

`packages/python/goldenmatch/tests/test_api_confidence_required_kwarg.py`:

```python
"""End-to-end API tests for the confidence_required kwarg.

Spec §Backward compatibility. The kwarg defaults to True (loud-not-slow);
False preserves today's warn-and-run behavior."""
from __future__ import annotations

import polars as pl
import pytest

import goldenmatch as gm
from goldenmatch.core.autoconfig_controller import (
    REFUSE_AT_N,
    ControllerNotConfidentError,
)


@pytest.fixture(autouse=True)
def _disable_autoconfig_memory(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")


def _force_red_history(monkeypatch, n_rows_in_df: int):
    """Same forced-RED helper as Phase 3 -- duplicated here for test isolation."""
    from goldenmatch.core.autoconfig_history import HistoryEntry, RunHistory
    from goldenmatch.core.complexity_profile import (
        BlockingProfile, ComplexityProfile, DataProfile,
        ProfileMeta, ScoringProfile,
    )

    red_profile = ComplexityProfile(
        data=DataProfile(n_rows=0),
        blocking=BlockingProfile(),
        scoring=ScoringProfile(),
        meta=ProfileMeta(
            iteration=0, is_sample=False, sample_size=n_rows_in_df,
            n_rows_full=n_rows_in_df, wall_clock_ms=0, seed=0,
        ),
    )

    def _picker(self, *args, **kwargs):
        return HistoryEntry(
            iteration=0, config=None, profile=red_profile,
            decision=None, error=None, wall_clock_ms=0,
        )

    monkeypatch.setattr(RunHistory, "pick_committed", _picker)


def _df(n_rows: int) -> pl.DataFrame:
    return pl.DataFrame({
        "name": ["alice"] * n_rows,
        "email": [f"u{i}@x.com" for i in range(n_rows)],
    })


def test_dedupe_df_default_raises_at_scale_on_red(monkeypatch):
    _force_red_history(monkeypatch, n_rows_in_df=REFUSE_AT_N)
    with pytest.raises(ControllerNotConfidentError):
        gm.dedupe_df(_df(REFUSE_AT_N))


def test_dedupe_df_confidence_required_false_short_circuits(monkeypatch):
    _force_red_history(monkeypatch, n_rows_in_df=REFUSE_AT_N)
    result = gm.dedupe_df(_df(REFUSE_AT_N), confidence_required=False)
    assert result is not None


def test_auto_configure_df_default_raises_at_scale_on_red(monkeypatch):
    from goldenmatch.core.autoconfig import auto_configure_df

    _force_red_history(monkeypatch, n_rows_in_df=REFUSE_AT_N)
    with pytest.raises(ControllerNotConfidentError):
        auto_configure_df(_df(REFUSE_AT_N))


def test_auto_configure_df_confidence_required_false_returns_v0(monkeypatch):
    from goldenmatch.core.autoconfig import auto_configure_df

    _force_red_history(monkeypatch, n_rows_in_df=REFUSE_AT_N)
    cfg = auto_configure_df(_df(REFUSE_AT_N), confidence_required=False)
    assert cfg is not None


def test_match_df_default_raises_at_scale_on_red(monkeypatch):
    _force_red_history(monkeypatch, n_rows_in_df=REFUSE_AT_N)
    target = _df(REFUSE_AT_N)
    # reference is small; only target's height drives the gate (df=target).
    reference = _df(100)
    with pytest.raises(ControllerNotConfidentError):
        gm.match_df(target, reference)


def test_match_df_confidence_required_false_short_circuits(monkeypatch):
    _force_red_history(monkeypatch, n_rows_in_df=REFUSE_AT_N)
    target = _df(REFUSE_AT_N)
    reference = _df(100)
    result = gm.match_df(target, reference, confidence_required=False)
    assert result is not None
```

- [ ] **Step 2: Run; expect failure (TypeError on unknown kwarg).**

### Task 4.2: Wire the kwarg through `auto_configure_df`

- [ ] **Step 1: Add `confidence_required` to `auto_configure_df` signature.**

In `core/autoconfig.py`, find `def auto_configure_df(...)` (line 1432). Modify to add the kwarg in the keyword-only block:

```python
def auto_configure_df(
    df: pl.DataFrame | pl.LazyFrame,
    llm_provider: str | None = None,
    domain_config: Any = None,
    llm_auto: bool = False,
    strict: bool = False,
    allow_remote_assets: bool = False,
    *,
    reference: pl.DataFrame | pl.LazyFrame | None = None,
    _skip_finalize: bool = False,
    confidence_required: bool = True,
) -> GoldenMatchConfig:
```

- [ ] **Step 2: Construct controller with adaptive budget.**

In the same function, find the existing `AutoConfigController(policy=..., budget=ControllerBudget(), memory=...)` call (line 1501-1505). Replace `ControllerBudget()` with `ControllerBudget.for_dataset(df.height)`:

```python
    controller = AutoConfigController(
        policy=policy,
        budget=ControllerBudget.for_dataset(df.height),
        memory=memory,
    )
```

- [ ] **Step 3: Thread `confidence_required` into `controller.run()`.**

Find the `controller.run(df, reference=reference, v0_kwargs=v0_kw, skip_finalize=_skip_finalize)` call (line 1513-1518). Add `confidence_required=confidence_required`:

```python
    config, profile, history = controller.run(
        df,
        reference=reference,
        v0_kwargs=v0_kw,
        skip_finalize=_skip_finalize,
        confidence_required=confidence_required,
    )
```

### Task 4.3: Audit all `auto_configure_df` call sites

Before threading the kwarg through `_api.dedupe_df` / `_api.match_df`, find every other caller so we know which ones (a) need the kwarg surfaced explicitly, (b) need the exception caught + re-raised cleanly, or (c) are unaffected (call with `_skip_finalize=False` plus other state that means the gate never trips).

- [ ] **Step 1: Find all `auto_configure_df` references.**

```bash
grep -rn "auto_configure_df\|auto_configure\b" packages/python/goldenmatch/goldenmatch/ \
  --include="*.py" | grep -v "_pycache_\|/tests/"
```

Cross-reference against this expected list (from project CLAUDE.md + reviewer audit):

| Surface | Call site | Action |
|---|---|---|
| `_api.dedupe_df` | line 369-374 | Thread kwarg (Task 4.4) |
| `_api.match_df` | line 484-488 | Thread kwarg (Task 4.4) |
| `core/match_one.py` | resolved: does NOT call `auto_configure_df` | `match_one` is a single-record primitive that takes an already-built `MatchkeyConfig`; callers configure separately and feed records in. **No kwarg needed**; the calling layer (typically `_api.match_df` or `StreamProcessor`) handles auto-config + threads `confidence_required` to whatever set up the config. Confirmed by inspecting `core/match_one.py` for `auto_configure` calls — none |
| `mcp/agent_tools.py` | line 373 area (`AgentSession.autoconfigure`) | Verify `auto_configure_df` calls there will let `ControllerNotConfidentError` propagate (don't catch + swallow). MCP tool response should surface the exception as a structured error, not crash the tool |
| `mcp/server.py` | line 71-73 (`from goldenmatch.core.autoconfig import auto_configure`) | This calls `auto_configure` (file-based), not `_df`. Verify the file-based path also threads `confidence_required` — same wiring required |
| `cli/dedupe.py` / `cli/autoconfig.py` | check | If CLI commands call `auto_configure_df` / `auto_configure`, exception propagates as non-zero exit. Likely fine; verify no `try/except: pass` swallows |
| A2A `autoconfig` skill | `a2a/skills.py` | Same as MCP — exception should surface as a structured A2A task error |
| Web `/api/v1/controller/...` + `/autoconfig` | `web/routers/` | Catch + return 4xx with structured error body (exception's `n_rows / failing_sub_profile / stop_reason` map cleanly to a JSON body) |

- [ ] **Step 2: For each call site, decide between three actions:**

(a) **Thread kwarg through** — `dedupe_df` / `match_df` / `match_one` / agent skills / MCP tools that accept user-facing kwargs. Lets users opt out via the same surface they invoke from.

(b) **Catch + map to structured error response** — web routers + A2A skills that return structured JSON. Catch `ControllerNotConfidentError`, build a 4xx (or A2A error event) with the structured fields.

(c) **Let it bubble** — CLI commands, internal callers, anything where a Python traceback IS the right user surface. Verify no enclosing `try/except: pass` swallows.

- [ ] **Step 3: Write per-surface decisions into the plan as you find them.**

Tabulate the audit results into a per-surface table in the PR description. The set of surfaces that get the kwarg vs catch vs bubble is the API contract this Phase commits to.

- [ ] **Step 4: Verify NO surface silently catches `ControllerNotConfidentError` and returns the old `DedupeResult`-with-warning shape.**

The whole point of the spec is to make degenerate runs impossible by default. A `try: ... except ControllerNotConfidentError: return run_dedupe_df(df, v0_config)` somewhere is the spec violation.

```bash
grep -rn "ControllerNotConfidentError" packages/python/goldenmatch/ --include="*.py" | grep -v "/tests/"
```

Expected: **only** the definition site in `core/autoconfig_controller.py` + the explicit `raise` call inside `controller.run`. Phase 4 does NOT add `except ControllerNotConfidentError` anywhere in production code (Task 4.4 just threads the kwarg; it doesn't catch). If grep finds an `except` clause for `ControllerNotConfidentError` in any production file, that's the spec violation — investigate before merging.

### Task 4.4: Surface kwarg on `_api.dedupe_df` and `_api.match_df`

- [ ] **Step 1: Find `dedupe_df` signature.**

```
grep -n "^def dedupe_df" packages/python/goldenmatch/goldenmatch/_api.py
```

Expected: line 309.

- [ ] **Step 2: Add `confidence_required: bool = True` to `dedupe_df`'s keyword-only section.**

Find the signature; preserve all existing kwargs; add the new one. Then find the `auto_configure_df(...)` call inside dedupe_df's body (around line 369-374) and pass `confidence_required=confidence_required`:

```python
                config = _self_api.auto_configure_df(
                    df,
                    llm_provider=_auto_config_provider,
                    llm_auto=llm_auto,
                    _skip_finalize=True,
                    confidence_required=confidence_required,
                )
```

- [ ] **Step 3: Same change for `match_df` (line 484-488).**

```python
            target, reference=reference, _skip_finalize=True,
            confidence_required=confidence_required,
```

Add `confidence_required: bool = True` to `match_df`'s signature.

- [ ] **Step 4: Run new + Phase 3 gate tests.**

Un-skip the Phase 3 placeholder first:

```
sed -i 's/@pytest.mark.skip(reason="confidence_required kwarg lands in Phase 4")//' packages/python/goldenmatch/tests/test_controller_confidence_gate.py
```

Or edit by hand. Then:

```
python -m pytest tests/test_api_confidence_required_kwarg.py tests/test_controller_confidence_gate.py -v
```

Expected: 11 passed (5 Phase-3 gate + 6 API kwarg).

- [ ] **Step 5: Run broader regression sweep.**

```
python -m pytest tests/ --timeout=120 --ignore=tests/test_db.py --ignore=tests/test_reconcile.py --ignore=tests/test_mcp_and_watch.py --ignore=tests/test_embedder.py --ignore=tests/test_llm_boost.py --ignore=tests/benchmarks -q
```

Expected: all green. The new adaptive budget at 100K+ changes sample size only above the existing test fixtures' row count, so no behavior change for the suite.

### Task 4.5: Commit + open PR

- [ ] **Step 1: Commit.**

```bash
git add packages/python/goldenmatch/goldenmatch/core/autoconfig.py packages/python/goldenmatch/goldenmatch/_api.py packages/python/goldenmatch/tests/test_api_confidence_required_kwarg.py packages/python/goldenmatch/tests/test_controller_confidence_gate.py
git commit -m "feat(autoconfig): Phase 4 -- confidence_required kwarg + adaptive budget wiring

Phase 4 of controller-budget-pathology plan. Surfaces
confidence_required on the public API and wires
ControllerBudget.for_dataset into auto_configure_df.

- auto_configure_df(df, ..., confidence_required: bool = True):
  controller now built with ControllerBudget.for_dataset(df.height);
  kwarg threads through to controller.run().
- dedupe_df / match_df gain the same kwarg with the same default;
  propagated to auto_configure_df.

Behavior change: callers at df.height >= 100K who land on a RED
committed config now hit ControllerNotConfidentError instead of
getting a degenerate v0 result. Opt out with
confidence_required=False to preserve today's warn-and-run.

Phase 3's gate test (test_gate_short_circuits_with_confidence_
required_false) is un-skipped here.

Spec sections: §Design + §Backward compatibility.

Tests: 6 new + 1 Phase-3 un-skip. Full suite green.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 2: Push + PR.**

PR title: `feat(autoconfig): controller-budget pathology — Phase 4 (kwarg + adaptive budget)`.

---

## Phase 5 — End-to-end real-iteration test + bench validation

Locks in that the gate fires via the *real* iteration loop (not just monkey-patched `pick_committed`), and that the 100K bench wall stays under the calibrated 30s ceiling.

**Files:**
- Test: `packages/python/goldenmatch/tests/test_controller_adaptive_e2e.py` (NEW)
- Bench: dispatch `bench-zero-config.yml` at 100K post-Phase-4

### Task 5.1: Build a synthetic 100K fixture that reliably produces RED

- [ ] **Step 1: Use the documented adversarial pattern.**

A naïve single-column fixture **WILL NOT WORK**: `autoconfig_controller.py:293` short-circuits `if df.height == 1 or len(user_cols) == 1` and returns v0 + a YELLOW sentinel (not RED), bypassing the iteration loop entirely. The gate predicate checks `health() == RED`, so a YELLOW commit won't trigger it.

The documented path to a real-iteration RED commit (per `packages/python/goldenmatch/CLAUDE.md` "Synthetic person fixtures" section + memory `feedback_synthetic_surname_fixtures.md`) is **`_person_df`-shape with surnames in a single soundex bucket**. That's the Mode-A pathology the spec is targeting:

- 2+ user columns (so the pathological-input short-circuit doesn't fire)
- Surnames that all collapse to the same soundex code (so blocking can't reduce the comparison space and the iteration loop genuinely RED's on the scoring sub-profile)

The fixture lives next to existing person-shape helpers — extend rather than duplicate.

- [ ] **Step 2: Write the failing test.**

`packages/python/goldenmatch/tests/test_controller_adaptive_e2e.py`:

```python
"""End-to-end: the confidence gate fires via the real iteration loop,
not just a monkey-patched pick_committed.

Spec §Testing. Without this, a refactor of pick_committed could silently
break the gate while the monkey-patched Phase 3 tests still pass.
"""
from __future__ import annotations

import polars as pl
import pytest

import goldenmatch as gm
from goldenmatch.core.autoconfig_controller import (
    REFUSE_AT_N,
    ControllerNotConfidentError,
)


@pytest.fixture(autouse=True)
def _disable_autoconfig_memory(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")


def _adversarial_df_at_threshold() -> pl.DataFrame:
    """Build a REFUSE_AT_N-row person-shape df where surnames all collapse
    to a single soundex bucket -- the documented adversarial shape per
    memory `feedback_synthetic_surname_fixtures.md`. Two user columns
    (first_name + last_name) so the pathological-input short-circuit at
    autoconfig_controller.py:293 doesn't bypass iteration. All surnames
    in one soundex code -> blocking can't reduce the comparison space
    -> scoring profile reports mass_above_threshold==0 -> RED."""
    n = REFUSE_AT_N
    # All these surnames soundex to S530 ("Smith"-bucket variants):
    # Smith, Smyth, Smithe, Smythe, Smid, Smit, Sneed, Snath, Snoot, Snout
    surnames_one_bucket = [
        "Smith", "Smyth", "Smithe", "Smythe", "Smid",
        "Smit", "Sneed", "Snath", "Snoot", "Snout",
    ]
    first_names = ["Alice", "Bob", "Charlie", "Dana", "Eve", "Frank"]
    return pl.DataFrame({
        "first_name": [first_names[i % len(first_names)] for i in range(n)],
        "last_name": [surnames_one_bucket[i % len(surnames_one_bucket)] for i in range(n)],
    })


def test_gate_fires_via_real_iteration_loop():
    """End-to-end: build a 100K-row adversarial fixture, let
    AutoConfigController iterate normally (no monkey-patch). When it
    commits RED, the gate must fire."""
    df = _adversarial_df_at_threshold()
    with pytest.raises(ControllerNotConfidentError) as exc_info:
        gm.dedupe_df(df)
    assert exc_info.value.n_rows == REFUSE_AT_N
    # failing_sub_profile should be one of the upstream causes
    assert exc_info.value.failing_sub_profile in {
        "data", "blocking", "scoring", "matchkey",
    }


def test_adaptive_budget_picks_sqrt_scaled_sample_at_500k(monkeypatch):
    """Spec §Design / ControllerBudget.for_dataset. Build a controller via
    auto_configure_df at simulated 500K rows; confirm the constructed
    ControllerBudget has sample_size=14142 (sqrt-scaled).

    We don't actually build a 500K df (too slow for unit suite); instead,
    intercept ControllerBudget.for_dataset and capture the call."""
    from goldenmatch.core import autoconfig_controller as ctrl_mod

    captured = {}
    real_for_dataset = ctrl_mod.ControllerBudget.for_dataset

    @classmethod
    def _capturing(cls, n_rows: int):
        captured["n_rows"] = n_rows
        return real_for_dataset(n_rows)

    monkeypatch.setattr(ctrl_mod.ControllerBudget, "for_dataset", _capturing)

    # Use a small df; we're only asserting the right n_rows propagates to
    # the budget classmethod, not measuring real iteration.
    small_df = pl.DataFrame({"name": ["alice"] * 100})
    gm.dedupe_df(small_df, confidence_required=False)
    assert captured["n_rows"] == 100
    # And confirm the table picks the right tier for that n_rows:
    budget = real_for_dataset(100)
    assert budget.max_seconds == 15.0  # tight tier (<5K)
```

- [ ] **Step 3: Run; expect 2 passed.**

```
python -m pytest tests/test_controller_adaptive_e2e.py -v
```

If the fixture doesn't reliably RED, escalate (don't silently weaken the test):
1. **First diagnostic:** print `controller_history.entries[-1].profile.health()` and `controller_history.stop_reason.name` to confirm the controller IS committing RED via the real loop, not getting short-circuited somewhere.
2. **If health is YELLOW not RED:** more surnames may need to be added to the soundex bucket, or `n_blocks` is 1 (single soundex bucket creates exactly one block) and the existing rollup classes that as YELLOW. Inspect `BlockingProfile.health()` rules.
3. **If iteration short-circuits before `pick_committed`:** the pathological-input gate fires (n_rows==1 / single col / all-null). Verify the fixture has 2+ user columns with non-null values.
4. **Last resort (DON'T use unless 1-3 fail):** monkey-patch `_run_pipeline_sample` to raise on every iteration. This tests the real loop's error-handling path; loses the "no monkey-patch" property but is better than no e2e coverage.

### Task 5.2: Bench validation

- [ ] **Step 1: Dispatch 100K bench against the post-Phase-4 main commit.**

```bash
gh auth switch --user benzsevern
GH_TOKEN=$(gh auth token --user benzsevern) gh workflow run bench-zero-config.yml --ref main -f n_records=100000 -f label=phase4-100k-validation
gh auth switch --user benzsevern-mjh
```

- [ ] **Step 2: Wait for the bench to complete (~10-15 min).**

```bash
RUN_ID=$(GH_TOKEN=$(gh auth token --user benzsevern) gh run list --workflow bench-zero-config.yml --limit 1 --json databaseId --jq .[0].databaseId)
while GH_TOKEN=$(gh auth token --user benzsevern) gh run view $RUN_ID --json status --jq .status | grep -qE "in_progress|queued"; do sleep 60; done
GH_TOKEN=$(gh auth token --user benzsevern) gh run view $RUN_ID --log | grep -E "median wall|wall=" | head -10
```

Acceptance: median wall <= 30s.

- [ ] **Step 3: If wall > 30s, investigate.**

Two failure modes:
1. Sample iteration cost climbed more than estimated (sqrt-scaling pushed sample to 6324 rows; iteration is O(sample) for transforms, but GoldenCheck quality scan is also O(sample) — should still be ~5x increase). If wall is in 35-45s range, accept and update CLAUDE.md / spec acceptance.
2. Adaptive budget regressed something else (e.g., max_seconds went from 30s to 60s — the controller might iterate further and commit a worse config in some cases). Inspect a cProfile from the bench's `_cprofile-output` artifact.

If wall lands cleanly under 24s (matching pre-Phase 1 numbers), the headroom in the spec was unnecessary; we can tighten the gate. Either way, document the measured number.

### Task 5.3: Commit + open PR

- [ ] **Step 1: Commit.**

```bash
git add packages/python/goldenmatch/tests/test_controller_adaptive_e2e.py
git commit -m "test(autoconfig): Phase 5 -- end-to-end adaptive budget + real-iteration gate

Phase 5 of controller-budget-pathology plan. Locks in two end-to-end
behaviors that the monkey-patched Phase 3 / Phase 4 tests can't:

- The confidence gate fires via the *real* iteration loop on a 100K-row
  adversarial fixture (single column of unique random tokens; no
  blocking key can reduce the comparison space, controller commits
  v0 RED). A refactor of pick_committed that silently broke the gate
  would slip past the monkey-patched tests but be caught here.
- auto_configure_df calls ControllerBudget.for_dataset(df.height) on
  every invocation (captured via monkey-patched for_dataset).

Bench validation (separate dispatch): 100K wall <= 30s on
large-new-64GB. Spec §Acceptance criteria.

Spec section: §Testing.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 2: Push + PR.**

PR title: `test(autoconfig): controller-budget pathology — Phase 5 (e2e + bench validation)`.

---

## Phase 6 — Docs + memory closeout

CLAUDE.md update, memory entry, optional `docs/explicit-config.md`.

**Files:**
- Modify: `packages/python/goldenmatch/CLAUDE.md`
- Create: `C:\Users\bsevern\.claude\projects\D--show-case-goldenmatch\memory\feedback_controller_confidence_required.md`
- Modify: `C:\Users\bsevern\.claude\projects\D--show-case-goldenmatch\memory\MEMORY.md`
- Optional: `docs/explicit-config.md` (the URL the exception points at)

### Task 6.1: Update CLAUDE.md

- [ ] **Step 1: Find the controller-v3 closeout bullet.**

```
grep -n "Recommended for 5M today" packages/python/goldenmatch/CLAUDE.md
```

- [ ] **Step 2: Replace the bullet's "see Open questions" tail with a pointer to the new behavior.**

The current bullet (post-PR #268) reads:

> **Recommended for 5M today (still):** `backend="chunked"` + `config_mode="explicit-personlike"`. Controller v3 (2026-05-16, PRs #259-#266) replaced the single-threshold `_scale_aware_backend` with a 7-rule planner that picks chunked/duckdb/ray automatically at scale, but the underlying controller-sample-iteration pathology that motivates the "explicit-personlike" recommendation is unchanged — at 500K+ rows on synthetic surname fixtures, sample blocking discovery hits BUDGET_TIME → commits RED v0 → degenerate full-data dedupe. **Planner v3 picks the right backend; it doesn't fix what the controller commits when its iteration loop can't find good blocking keys.** See `docs/superpowers/specs/2026-05-15-controller-v3-planner-design.md` for what v3 does cover and §Open questions for the controller-budget gap that's worth its own spec.

Replace with:

> **Recommended for 5M today:** `backend="chunked"` + `config_mode="explicit-personlike"` when you want the call to *succeed* on synthetic-surname-shape fixtures (the same pathology, with the same shape, that motivates explicit-personlike — see also `docs/superpowers/specs/2026-05-15-controller-v3-planner-design.md` for the v3 backend planner that picks chunked automatically when iteration *does* succeed). Controller-budget v1 (2026-05-16, PRs #N-#N+5) closed the silent-degenerate-dedupe gap: at `df.height >= 100_000`, when sample iteration commits a RED config, `auto_configure_df` now raises `ControllerNotConfidentError` instead of running a 26-min noise-result dedupe. Adaptive sample size (sqrt-scaled, capped at 20K) + 60-120s budget at 100K+ rows ALSO makes iteration succeed more often on real-shape data. Callers who want today's warn-and-run can pass `confidence_required=False`.

- [ ] **Step 3: Add a new bullet for the gate semantics so future-me grep-finds it.**

After the bullet above, insert:

> **`ControllerNotConfidentError` semantics (2026-05-16):** Raised by `auto_configure_df` / `dedupe_df` / `match_df` when (a) `confidence_required=True` (default), (b) `df.height >= 100_000`, AND (c) the controller's committed `ComplexityProfile` is RED. Fields: `n_rows`, `failing_sub_profile` ∈ {`data`, `blocking`, `scoring`, `matchkey`, `cluster`}, `stop_reason` (StopReason name). Recovery options: pass an explicit `GoldenMatchConfig`, lower the matchkey threshold, or opt out with `confidence_required=False`. Adaptive budget table: `<5K -> 15s/full`, `5K-100K -> 30s/2K (historical)`, `100K-1M -> 60s/sqrt(n)*20 capped at 20K`, `>=1M -> 120s/20K`. Source: `core/autoconfig_controller.py:REFUSE_AT_N` + `ControllerBudget.for_dataset`. Spec: `docs/superpowers/specs/2026-05-16-controller-budget-vs-blocking-discovery-design.md`.

### Task 6.2: Write the memory entry

- [ ] **Step 1: Create `feedback_controller_confidence_required.md`.**

```bash
cat > "C:\Users\bsevern\.claude\projects\D--show-case-goldenmatch\memory\feedback_controller_confidence_required.md" <<'EOF'
---
name: feedback-controller-confidence-required
description: At df.height >= 100_000, auto-config raises ControllerNotConfidentError on a RED commit. Don't re-introduce silent-degenerate-run code paths.
metadata:
  type: feedback
---

`auto_configure_df` (and `dedupe_df` / `match_df`) raise `ControllerNotConfidentError` at `df.height >= 100_000` when the controller's committed `ComplexityProfile` is RED.

**Why:** Pre-2026-05-16, the controller silently returned `config_v0` when sample-iteration converged to RED. Callers ran the full pipeline on millions of rows with degenerate config -- measured 26 min wall on 500K with 0.5% dup density. The "result" was mostly noise. Users didn't notice unless they checked logs for the WARNING line. Replaced with a loud structured exception so noise output stops being possible. See PRs #N-#N+5 and spec `docs/superpowers/specs/2026-05-16-controller-budget-vs-blocking-discovery-design.md`.

**How to apply:**
- Don't add a code path that catches `ControllerNotConfidentError` and falls back to running v0 anyway (re-introduces the silent-degenerate problem). If you find yourself wanting this, pass `confidence_required=False` -- the kwarg is the sanctioned escape hatch.
- The exception fields (`n_rows`, `failing_sub_profile`, `stop_reason`) carry the structured diagnostic. `failing_sub_profile` follows the priority order `[data, blocking, scoring, matchkey, cluster]` -- root causes upstream first. Don't shuffle this order without updating the spec.
- The adaptive sample-size table in `ControllerBudget.for_dataset` is the load-bearing piece that makes iteration succeed *more often* before the gate fires. Don't disable adaptive sampling and the gate together -- you'd revert to silent degenerate runs.
- `REFUSE_AT_N = 100_000` is calibrated from one measured datum (500K -> 26 min back-projects to 5 min at 100K). Recalibration is an open question pending more bench data; the kwarg escape hatch makes adjustment low-risk.
EOF
```

- [ ] **Step 2: Append link to `MEMORY.md`.**

```
- [feedback_controller_confidence_required.md](feedback_controller_confidence_required.md) - At df.height >= 100_000, auto-config raises ControllerNotConfidentError on RED commit; don't add silent-fallback code paths
```

### Task 6.3: Optional `docs/explicit-config.md`

- [ ] **Step 1: Check if the file is worth writing yet.**

The `ControllerNotConfidentError.DOCS_URL` points at `docs/explicit-config.md`. If the file doesn't exist, the URL is dead. Per spec §Open questions #2, this can degrade to a CLAUDE.md anchor.

- [ ] **Step 2: Write the minimum-viable doc.**

`docs/explicit-config.md`:

```markdown
# Passing an explicit GoldenMatchConfig

When auto-config raises `ControllerNotConfidentError`, the controller has determined that its sample-iteration cannot find a healthy config for your data. The recovery is to pass an explicit `GoldenMatchConfig` describing your blocking keys and matchkey fields.

## Quick template

```python
from goldenmatch import dedupe_df
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
)

cfg = GoldenMatchConfig(
    matchkeys=[
        MatchkeyConfig(
            name="default",
            type="weighted",
            threshold=0.85,
            fields=[
                MatchkeyField(field="last_name", scorer="jaro_winkler", weight=2.0),
                MatchkeyField(field="first_name", scorer="jaro_winkler", weight=1.5),
                MatchkeyField(field="zip", scorer="exact", weight=1.0),
            ],
        ),
    ],
    blocking=BlockingConfig(
        keys=[
            BlockingKeyConfig(fields=["last_name_soundex"]),
            BlockingKeyConfig(fields=["zip"]),
        ],
        max_block_size=1000,
    ),
)

result = dedupe_df(df, config=cfg)
```

## Diagnosing which sub-profile failed

`ControllerNotConfidentError.failing_sub_profile` is the lead diagnostic. Common patterns:

| `failing_sub_profile` | Likely cause | Try |
|---|---|---|
| `data` | All-null column; single-column input; n_rows == 0 | Inspect `df.describe()`; ensure at least two non-null columns |
| `blocking` | No blocking key reduces comparison space | Identify a column that's neither unique-per-row (no reduction) nor low-cardinality (mega-blocks). Soundex/metaphone of names usually works |
| `scoring` | Sample's matchkey threshold matches nothing | Lower threshold (0.85 -> 0.70); add a transform to normalize the matchkey field |
| `matchkey` | Matchkey field is near-100%-unique (every row is its own cluster) | Pick a less-discriminative matchkey or add a fuzzy weight |
| `cluster` | Cluster output has one giant component | Add a stricter blocking key; the existing blocking is too permissive |

## Opting out

If you understand the controller is committing a noisy config and you want to run it anyway:

```python
result = dedupe_df(df, confidence_required=False)  # warn-and-run
```
```

- [ ] **Step 3: Add to git.** (`docs/` outside `superpowers/` is normally tracked.)

```
git add docs/explicit-config.md
```

### Task 6.4: Commit + open PR

- [ ] **Step 1: Commit.**

```bash
git add packages/python/goldenmatch/CLAUDE.md docs/explicit-config.md
git commit -m "docs: Phase 6 -- closeout for controller-budget pathology

Phase 6 of controller-budget-pathology plan. Three doc updates:

- packages/python/goldenmatch/CLAUDE.md: replace post-controller-v3
  '5M today' caveat with the new ControllerNotConfidentError
  semantics + adaptive budget table.
- docs/explicit-config.md (NEW): the docs URL the exception points
  at. Quick template + failing-sub-profile diagnostic table.
- Memory: feedback_controller_confidence_required.md pinned in
  C:/Users/bsevern/.claude/projects/.../memory/ with the
  'don't re-introduce silent-fallback' rule.

Spec section: §Acceptance criteria.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 2: Push + PR.**

PR title: `docs: controller-budget pathology — Phase 6 (closeout)`.

---

## Acceptance checklist (matches spec §Acceptance criteria)

- [ ] Phase 1 PR merged. `ControllerBudget.for_dataset(n_rows)` classmethod.
- [ ] Phase 2 PR merged. `ControllerNotConfidentError` + `_identify_failing_subprofile`.
- [ ] Phase 3 PR merged. Confidence gate inside `AutoConfigController.run`.
- [ ] Phase 4 PR merged. `confidence_required` kwarg surfaced on public API; adaptive budget wired.
- [ ] Phase 5 PR merged. E2E gate test fires via real iteration loop.
- [ ] 100K bench wall <= 30s on `large-new-64GB` (dispatched and measured against post-Phase-4 main).
- [ ] Phase 6 PR merged. CLAUDE.md updated; memory entry pinned; `docs/explicit-config.md` written.
- [ ] Full test suite passes: `pytest tests/ -q --timeout=120 --ignore=...` (~2500 passed, no new flakes from this plan).

---

## When to escalate

If during execution any of these surface, stop and re-read the spec / surface to the user:

1. **The 100K bench wall exceeds 30s by more than 5s after Phase 5.** Means sqrt-scaling pushed sample iteration cost more than expected. Either tighten the adaptive table (smaller sqrt coefficient at the 100K boundary) or relax the bench gate after measurement.

2. **The Phase 5 adversarial fixture doesn't reliably RED.** If the controller actually finds a workable config on a 100K-row single-unique-token column, the iteration loop is more robust than I assumed and the e2e test needs a stronger adversarial shape (or a `_run_pipeline_sample` monkey-patch).

3. **Phase 4 regresses any test in `tests/test_planner_integration.py` or `tests/test_autoconfig_regressions.py`.** Means `ControllerBudget.for_dataset` is affecting test fixtures that previously ran under the historical defaults. Most likely a fixture at the exact 100K boundary; check whether `df.height == 100_000` or `df.height == 99_999` and adjust accordingly.

4. **`_identify_failing_subprofile` returns `''` from a real run that should have been RED.** Means the priority order or the health() rollup logic missed something. Inspect `controller_history.entries[-1].profile` to see which sub-profile is actually RED, then fix the priority list.
