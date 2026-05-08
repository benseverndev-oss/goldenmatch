# Auto-Config Best-Effort Commit Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Commit a best-effort `HistoryEntry` whenever any iteration produced a usable profile (instead of falling back to v0 when all iterations are RED), and surface `StopReason` on `RunHistory` so users can see why iteration stopped. Closes the DQbench T1/T2 score wall and a known v1.8 spec gap in one cohesive change.

**Architecture:** Three small surgical edits to `core/autoconfig_history.py`, `core/autoconfig_controller.py`, and `core/complexity_profile.py`. Rename `cheapest_healthy()` → `pick_committed()` with a deprecation alias; extend the lex key to include RED with `health_rank=2`; filter on `error is None and profile is not None` so errored entries don't pollute. Move `StopReason` enum from controller to `complexity_profile.py` to break a circular-import shape, and wire `history.stop_reason` writes at every break point in `run()`.

**Tech Stack:** Python 3.12, Pydantic v2, Polars, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-05-08-autoconfig-best-effort-commit-design.md` — read this first; the plan refers to it by section.

---

## Pre-flight checklist

Before starting any task:
- [ ] Working in a clean dedicated branch (`feature/autoconfig-best-effort-commit`); no uncommitted changes.
- [ ] On `main` at HEAD = `709cee5` (release/v1.8.0 merge) or later. The branch was created off main during the brainstorming session.
- [ ] DQbench dataset present at `~/.dqbench/datasets/tier{1,2,3}/data.csv`. If missing, run `dqbench generate --er` to populate.
- [ ] DBLP-ACM, Febrl3 (via recordlinkage), and NCVR sample present at `packages/python/goldenmatch/tests/benchmarks/datasets/`. Per CLAUDE.md, all gitignored — re-run measurement only if files exist.
- [ ] OPENAI_API_KEY available via `set -a && source /d/show_case/goldencheck/.testing/.env && set +a` (only needed for the with-LLM benchmark step).
- [ ] Editable install: `C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe -c "import goldenmatch._api; print(goldenmatch._api.__file__)"` shows the worktree path, not site-packages.
- [ ] Baseline regression count: 1825+ tests passing (from v1.8.0). After the change, expect ~1838+ (13 new tests).

---

## File structure (locked in here)

| File | Role | Change |
|---|---|---|
| `core/complexity_profile.py` | Typed profiles + enums | Add `StopReason` enum (moved from controller) |
| `core/autoconfig_history.py` | Audit-trail dataclasses | Rename `cheapest_healthy` → `pick_committed`, deprecation alias, `stop_reason` field, document `HistoryEntry` invariant |
| `core/autoconfig_controller.py` | Iteration loop + commit logic | Replace `StopReason` definition with import from `complexity_profile`, set `stop_reason` at each break, switch call site, update commit log |
| `tests/test_autoconfig_history.py` | History tests | Update `cheapest_healthy` tests; add `pick_committed` tests; add deprecation alias test; add `stop_reason` field test |
| `tests/test_autoconfig_controller.py` | Controller tests | Update existing v0-fallback tests; add `stop_reason` recording tests; add commit-RED end-to-end test; add log-message tests |
| `tests/test_autoconfig_facade.py` | Facade tests | Spot-check (probably no changes needed) |
| `tests/fixtures/autoconfig/red_provoking.csv` | Test fixture | NEW — tiny synthetic frame that provokes RED across iterations |

Estimated total delta: **~205 LOC + ~150 LOC tests**.

---

## Phase 1 — Move `StopReason` to `complexity_profile.py`

Tier 1 is mechanical. Locks in the import direction so `RunHistory` can reference `StopReason` without depending on the controller.

### Task 1.1: Add `StopReason` enum to `complexity_profile.py`

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/complexity_profile.py`
- Test: existing `tests/test_complexity_profile.py` is enough (no new test file)

- [ ] **Step 1: Read existing `complexity_profile.py`** to confirm where `HealthVerdict` lives and pick a co-location for `StopReason`.

```bash
grep -n "class HealthVerdict\|^class\|from enum" /d/show_case/goldenmatch/packages/python/goldenmatch/goldenmatch/core/complexity_profile.py | head -10
```

- [ ] **Step 2: Read existing `StopReason` in the controller** to copy verbatim.

```bash
grep -n "class StopReason\|^    GREEN\|^    CONVERGED\|^    BUDGET" /d/show_case/goldenmatch/packages/python/goldenmatch/goldenmatch/core/autoconfig_controller.py | head -15
```

- [ ] **Step 3: Add `StopReason` enum to `complexity_profile.py`** immediately below `HealthVerdict`:

```python
class StopReason(Enum):
    """Why the controller stopped iterating.

    Set on ``RunHistory.stop_reason`` at each break point in
    ``AutoConfigController.run()``. Observable via
    ``result.postflight_report.controller_history.stop_reason``.
    """
    GREEN = "green"                           # iteration produced a healthy profile
    CONVERGED = "converged"                   # profile distance to prev < epsilon
    BUDGET_ITERATIONS = "budget_iterations"   # max_iterations hit
    BUDGET_TIME = "budget_time"               # max_seconds hit
    POLICY_SATISFIED = "policy_satisfied"     # policy returned None on non-green
    POLICY_NO_PROGRESS = "policy_no_progress" # policy returned identical config
    OSCILLATING = "oscillating"               # is_oscillating() fired
    CANCELLED = "cancelled"                   # KeyboardInterrupt
```

- [ ] **Step 4: Add a unit test for the enum** (if not already covered by `complexity_profile`'s existing tests). Append to `tests/test_complexity_profile.py`:

```python
def test_stop_reason_has_expected_values():
    """StopReason captures all controller exit paths."""
    from goldenmatch.core.complexity_profile import StopReason
    expected = {"GREEN", "CONVERGED", "BUDGET_ITERATIONS", "BUDGET_TIME",
                "POLICY_SATISFIED", "POLICY_NO_PROGRESS", "OSCILLATING",
                "CANCELLED"}
    assert {sr.name for sr in StopReason} == expected
```

- [ ] **Step 5: Run the test to confirm it passes.**

```bash
cd packages/python/goldenmatch && C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe -m pytest tests/test_complexity_profile.py::test_stop_reason_has_expected_values -v
```
Expected: PASS.

- [ ] **Step 6: Commit.**

```bash
git add packages/python/goldenmatch/goldenmatch/core/complexity_profile.py packages/python/goldenmatch/tests/test_complexity_profile.py
git commit -m "feat(autoconfig): add StopReason enum to complexity_profile.py"
```

### Task 1.2: Update controller to import `StopReason` from new location

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/autoconfig_controller.py:42` (delete the local `class StopReason` definition)
- Modify: `packages/python/goldenmatch/goldenmatch/core/autoconfig_controller.py` (top of file: add import)

- [ ] **Step 1: Find every `StopReason` reference in the controller** to confirm scope.

```bash
grep -n "StopReason" /d/show_case/goldenmatch/packages/python/goldenmatch/goldenmatch/core/autoconfig_controller.py
```
Expected: a `class StopReason(Enum):` block + various `StopReason.X` usages and possibly an import nearby.

- [ ] **Step 2: Edit the controller** to remove the local definition and add an import. Use the Edit tool to:
  1. Remove the `class StopReason(Enum): ...` block (about 10 lines).
  2. Add `from goldenmatch.core.complexity_profile import HealthVerdict, StopReason` at the top (or extend the existing import line).

- [ ] **Step 3: Run controller tests + history tests to confirm no breakage.**

```bash
cd packages/python/goldenmatch && C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe -m pytest tests/test_autoconfig_controller.py tests/test_autoconfig_history.py tests/test_autoconfig_facade.py -q --timeout=120 2>&1 | tail -10
```
Expected: all pass (the move is purely a refactor).

- [ ] **Step 4: Verify no other module imports `StopReason` from `autoconfig_controller`.** If any do, update them too.

```bash
grep -rn "from goldenmatch.core.autoconfig_controller import.*StopReason" /d/show_case/goldenmatch/packages/python/goldenmatch
```
Expected: zero hits, OR known callers (likely tests). If hits exist, update each to import from `complexity_profile` instead.

- [ ] **Step 5: Commit.**

```bash
git add packages/python/goldenmatch/goldenmatch/core/autoconfig_controller.py
# plus any other files updated in step 4
git commit -m "refactor(autoconfig): move StopReason import from controller to complexity_profile"
```

---

## Phase 2 — Add `pick_committed()` and `stop_reason` field to `RunHistory`

### Task 2.1: Add `stop_reason` field to `RunHistory`

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/autoconfig_history.py:40` (RunHistory dataclass)
- Test: `packages/python/goldenmatch/tests/test_autoconfig_history.py`

- [ ] **Step 1: Write a failing test for the new field.** Append to `tests/test_autoconfig_history.py`:

```python
def test_runhistory_stop_reason_default_is_none():
    """Default stop_reason is None; controller sets it at each break point."""
    from goldenmatch.core.autoconfig_history import RunHistory
    h = RunHistory()
    assert h.stop_reason is None


def test_runhistory_stop_reason_can_be_set():
    """stop_reason is mutable (the controller writes to it)."""
    from goldenmatch.core.autoconfig_history import RunHistory
    from goldenmatch.core.complexity_profile import StopReason
    h = RunHistory()
    h.stop_reason = StopReason.BUDGET_ITERATIONS
    assert h.stop_reason == StopReason.BUDGET_ITERATIONS
```

- [ ] **Step 2: Run; expect AttributeError (no `stop_reason` field yet).**

```bash
cd packages/python/goldenmatch && C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe -m pytest tests/test_autoconfig_history.py::test_runhistory_stop_reason_default_is_none -v 2>&1 | tail -8
```
Expected: FAIL.

- [ ] **Step 3: Add the field to `RunHistory` dataclass.** Edit `packages/python/goldenmatch/goldenmatch/core/autoconfig_history.py`:

Add the import at top:
```python
from goldenmatch.core.complexity_profile import ComplexityProfile, HealthVerdict, StopReason
```

Add the field to the dataclass (RunHistory has existing fields like `entries`, `full_vs_sample_drift`, `elapsed`, `prior_runs`):
```python
@dataclass
class RunHistory:
    entries: list[HistoryEntry] = field(default_factory=list)
    full_vs_sample_drift: float | None = None
    elapsed: timedelta = field(default_factory=lambda: timedelta(0))
    prior_runs: list[Any] = field(default_factory=list)
    stop_reason: StopReason | None = None    # NEW (v1.9)
```

- [ ] **Step 4: Re-run test; expect PASS.**

```bash
cd packages/python/goldenmatch && C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe -m pytest tests/test_autoconfig_history.py -q --timeout=60
```
Expected: all existing tests still pass + 2 new ones pass.

- [ ] **Step 5: Commit.**

```bash
git add packages/python/goldenmatch/goldenmatch/core/autoconfig_history.py packages/python/goldenmatch/tests/test_autoconfig_history.py
git commit -m "feat(autoconfig): add RunHistory.stop_reason field"
```

### Task 2.2: Document the `HistoryEntry` invariant

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/autoconfig_history.py:30-38` (HistoryEntry dataclass docstring)

- [ ] **Step 1: Read the existing `HistoryEntry` definition** and identify the dataclass docstring location.

- [ ] **Step 2: Add or expand the docstring** to document the invariant:

```python
@dataclass
class HistoryEntry:
    """One iteration's record in the controller's audit trail.

    Invariant: exactly one of ``error`` and ``profile`` is non-None,
    never both, never neither. The controller's iteration-loop append
    site enforces this — every code path either records a real profile
    (success) or records an ``ErrorRecord`` paired with the
    ``_RED_PROFILE_SENTINEL`` (failure path treats sentinel as the
    profile slot for type compatibility, but the entry's ``error`` is
    set, indicating no real profile was produced).

    ``RunHistory.pick_committed()``'s filter relies on this invariant
    (``error is None and profile is not None``); the invariant is
    documented but not defensively re-checked at the filter site.
    """
    iteration: int
    config: Any
    profile: ComplexityProfile
    decision: PolicyDecision | None
    error: ErrorRecord | None
    wall_clock_ms: int
```

(If the existing docstring differs, integrate the invariant prose without removing whatever's already documented.)

- [ ] **Step 3: No test needed for the docstring.** Just confirm the existing tests still pass:

```bash
cd packages/python/goldenmatch && C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe -m pytest tests/test_autoconfig_history.py -q --timeout=60
```

- [ ] **Step 4: Commit.**

```bash
git add packages/python/goldenmatch/goldenmatch/core/autoconfig_history.py
git commit -m "docs(autoconfig): document HistoryEntry invariant"
```

### Task 2.3: Add `pick_committed()` method to `RunHistory`

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/autoconfig_history.py:79` (after existing `cheapest_healthy()`)
- Test: `packages/python/goldenmatch/tests/test_autoconfig_history.py`

- [ ] **Step 1: Write failing tests.** Append to `tests/test_autoconfig_history.py`:

```python
# ============================================================
# pick_committed (added 2026-05-08)
# ============================================================

def test_pick_committed_returns_red_when_no_green_or_yellow():
    """The headline new behavior: pick_committed returns the best RED entry
    when all entries are RED. cheapest_healthy() would return None here."""
    from goldenmatch.core.autoconfig_history import (
        RunHistory, HistoryEntry, ErrorRecord,
    )
    from goldenmatch.core.complexity_profile import (
        ComplexityProfile, DataProfile, BlockingProfile, ScoringProfile,
        ClusterProfile, MatchkeyProfile, FieldStats, HealthVerdict,
    )

    # Two RED entries with different mass_separation
    def _red_entry(iteration, mass_above, mass_borderline):
        # mass_above_threshold == 0 forces ScoringProfile RED → rollup RED
        return HistoryEntry(
            iteration=iteration,
            config=f"cfg_{iteration}",
            profile=ComplexityProfile(
                data=DataProfile(n_rows=100, n_cols=4,
                                  column_types={"a": "text", "b": "id-like",
                                                "c": "text", "d": "date"}),
                blocking=BlockingProfile(
                    keys_used=[["a"]], n_blocks=10, total_comparisons=500,
                    reduction_ratio=0.95, block_sizes_p99=20,
                ),
                scoring=ScoringProfile(
                    n_pairs_scored=0, candidates_compared=500,
                    mass_above_threshold=mass_above,
                    mass_in_borderline=mass_borderline,
                    dip_statistic=0.05,
                ),
                cluster=ClusterProfile(transitivity_rate=0.95),
                matchkey=MatchkeyProfile(per_field={
                    "a": FieldStats(0.5, 0.0, 10),
                }),
            ),
            decision=None, error=None, wall_clock_ms=10,
        )

    h = RunHistory()
    # Both RED: ScoringProfile.health() == RED when mass_above==0
    h.entries.append(_red_entry(0, mass_above=0.0, mass_borderline=0.4))
    h.entries.append(_red_entry(1, mass_above=0.0, mass_borderline=0.1))

    # Verify both are RED
    assert h.entries[0].profile.health() == HealthVerdict.RED
    assert h.entries[1].profile.health() == HealthVerdict.RED

    # pick_committed returns the entry with higher mass_separation
    # (mass_above - mass_borderline). Both have mass_above=0, so the one
    # with lower mass_borderline wins (less negative separation).
    best = h.pick_committed()
    assert best is not None
    assert best.iteration == 1
    assert best.config == "cfg_1"


def test_pick_committed_excludes_errored_entries():
    """Entries with error != None are filtered out before lex-key ranking."""
    from goldenmatch.core.autoconfig_history import (
        RunHistory, HistoryEntry, ErrorRecord,
    )
    from goldenmatch.core.complexity_profile import ComplexityProfile, DataProfile

    h = RunHistory()
    # Entry 0: errored (sentinel profile)
    h.entries.append(HistoryEntry(
        iteration=0, config="errored",
        profile=ComplexityProfile(data=DataProfile(n_rows=0)),  # _RED_PROFILE_SENTINEL shape
        decision=None,
        error=ErrorRecord(exception_type="RuntimeError", traceback_summary="…"),
        wall_clock_ms=10,
    ))
    # Entry 1: real RED entry
    h.entries.append(HistoryEntry(
        iteration=1, config="real_red",
        profile=ComplexityProfile(
            data=DataProfile(n_rows=100, n_cols=4,
                              column_types={"a": "text", "b": "id-like",
                                            "c": "text", "d": "date"}),
        ),
        decision=None, error=None, wall_clock_ms=10,
    ))
    best = h.pick_committed()
    assert best is not None
    assert best.config == "real_red"


def test_pick_committed_returns_none_when_all_errored():
    """All entries errored → pick_committed returns None.
    Controller falls back to v0 in this case (preserves today's safety
    on 'controller couldn't run at all')."""
    from goldenmatch.core.autoconfig_history import (
        RunHistory, HistoryEntry, ErrorRecord,
    )
    from goldenmatch.core.complexity_profile import ComplexityProfile, DataProfile

    h = RunHistory()
    h.entries.append(HistoryEntry(
        iteration=0, config="x",
        profile=ComplexityProfile(data=DataProfile(n_rows=0)),
        decision=None,
        error=ErrorRecord(exception_type="RuntimeError", traceback_summary=""),
        wall_clock_ms=10,
    ))
    h.entries.append(HistoryEntry(
        iteration=1, config="y",
        profile=ComplexityProfile(data=DataProfile(n_rows=0)),
        decision=None,
        error=ErrorRecord(exception_type="ValueError", traceback_summary=""),
        wall_clock_ms=10,
    ))
    assert h.pick_committed() is None


def test_pick_committed_lex_key_orders_red_by_mass_separation():
    """Within RED tier, the one with highest (mass_above - mass_borderline)
    wins (most confident matches relative to borderline noise)."""
    from goldenmatch.core.autoconfig_history import RunHistory, HistoryEntry
    from goldenmatch.core.complexity_profile import (
        ComplexityProfile, DataProfile, BlockingProfile, ScoringProfile,
        ClusterProfile, MatchkeyProfile, FieldStats,
    )

    def _red(iteration, mass_above, mass_borderline):
        return HistoryEntry(
            iteration=iteration, config=f"cfg_{iteration}",
            profile=ComplexityProfile(
                data=DataProfile(n_rows=100, n_cols=4,
                                  column_types={"a": "text", "b": "id-like",
                                                "c": "text", "d": "date"}),
                blocking=BlockingProfile(
                    keys_used=[["a"]], n_blocks=10, total_comparisons=500,
                    reduction_ratio=0.95, block_sizes_p99=20,
                ),
                scoring=ScoringProfile(
                    n_pairs_scored=0, candidates_compared=500,
                    mass_above_threshold=mass_above,
                    mass_in_borderline=mass_borderline,
                    dip_statistic=0.001,  # forces scoring RED via dip rule
                ),
                cluster=ClusterProfile(transitivity_rate=0.95),
                matchkey=MatchkeyProfile(per_field={"a": FieldStats(0.5, 0.0, 10)}),
            ),
            decision=None, error=None, wall_clock_ms=10,
        )

    h = RunHistory()
    h.entries.append(_red(0, mass_above=0.4, mass_borderline=0.3))  # sep = 0.1
    h.entries.append(_red(1, mass_above=0.6, mass_borderline=0.1))  # sep = 0.5 ← winner
    h.entries.append(_red(2, mass_above=0.5, mass_borderline=0.4))  # sep = 0.1

    best = h.pick_committed()
    assert best is not None
    assert best.iteration == 1


def test_pick_committed_empty_history_returns_none():
    """No entries → None. Edge case at the start of run() before any iter."""
    from goldenmatch.core.autoconfig_history import RunHistory
    h = RunHistory()
    assert h.pick_committed() is None
```

- [ ] **Step 2: Run; expect AttributeError (`pick_committed` doesn't exist yet).**

```bash
cd packages/python/goldenmatch && C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe -m pytest tests/test_autoconfig_history.py::test_pick_committed_returns_red_when_no_green_or_yellow -v 2>&1 | tail -10
```

- [ ] **Step 3: Add `pick_committed()` method.** Edit `packages/python/goldenmatch/goldenmatch/core/autoconfig_history.py` — add the method on `RunHistory` (place it adjacent to `cheapest_healthy()`):

```python
    def pick_committed(self) -> HistoryEntry | None:
        """Pick the entry to commit. Returns None ONLY if every entry
        errored or has no profile — otherwise returns the best entry by
        lexicographic key, where RED entries are last resort but still
        beat 'no commit at all.'

        Replaces ``cheapest_healthy()`` as of v1.9 — the new behavior
        commits a best-effort entry whenever any iteration produced a
        usable profile, even if that profile is RED. The user-visible
        health verdict on the returned entry tells them what they got.

        Lex key: ``(health_rank, -mass_separation, iteration)`` where
        ``health_rank`` is 0/1/2 for GREEN/YELLOW/RED and
        ``mass_separation = mass_above_threshold - mass_in_borderline``.

        Filter: ``e.error is None and e.profile is not None`` (per the
        ``HistoryEntry`` invariant — guards the sentinel-mismatch case
        defensively).
        """
        survivors = [
            e for e in self.entries
            if e.error is None and e.profile is not None
        ]
        if not survivors:
            return None

        def key(e: HistoryEntry) -> tuple[int, float, int]:
            h = e.profile.health()
            rank = {
                HealthVerdict.GREEN: 0,
                HealthVerdict.YELLOW: 1,
                HealthVerdict.RED: 2,
            }[h]
            sp = e.profile.scoring
            sep = sp.mass_above_threshold - sp.mass_in_borderline
            return (rank, -sep, e.iteration)

        return min(survivors, key=key)
```

- [ ] **Step 4: Re-run new tests; expect 5 PASS.**

```bash
cd packages/python/goldenmatch && C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe -m pytest tests/test_autoconfig_history.py -k pick_committed -v --timeout=60
```

- [ ] **Step 5: Run full history tests** to confirm `cheapest_healthy()` still works (it's unchanged at this point):

```bash
cd packages/python/goldenmatch && C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe -m pytest tests/test_autoconfig_history.py -q --timeout=60
```
Expected: all pass.

- [ ] **Step 6: Commit.**

```bash
git add packages/python/goldenmatch/goldenmatch/core/autoconfig_history.py packages/python/goldenmatch/tests/test_autoconfig_history.py
git commit -m "feat(autoconfig): add RunHistory.pick_committed (extends lex key to RED)"
```

### Task 2.4: Convert `cheapest_healthy()` into a deprecation alias

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/autoconfig_history.py:79` (existing `cheapest_healthy` body)
- Test: `packages/python/goldenmatch/tests/test_autoconfig_history.py`

- [ ] **Step 1: Write a failing test for the deprecation alias's behavior change.**

```python
def test_cheapest_healthy_emits_deprecation_warning_and_delegates():
    """cheapest_healthy() now emits DeprecationWarning and delegates to
    pick_committed(). Behavior change: returns RED entries that v1.8
    callers expected to be None. The DeprecationWarning message MUST
    explicitly call this out (not just the rename) so callers update
    their code."""
    import pytest
    from goldenmatch.core.autoconfig_history import RunHistory, HistoryEntry
    from goldenmatch.core.complexity_profile import (
        ComplexityProfile, DataProfile, ScoringProfile, BlockingProfile,
        ClusterProfile, MatchkeyProfile, FieldStats,
    )

    # Create a RED entry (mass_above=0 forces scoring RED → rollup RED)
    h = RunHistory()
    h.entries.append(HistoryEntry(
        iteration=0, config="red_cfg",
        profile=ComplexityProfile(
            data=DataProfile(n_rows=100, n_cols=4,
                              column_types={"a": "text", "b": "id-like",
                                            "c": "text", "d": "date"}),
            blocking=BlockingProfile(keys_used=[["a"]], n_blocks=10,
                                      total_comparisons=500,
                                      reduction_ratio=0.95,
                                      block_sizes_p99=20),
            scoring=ScoringProfile(
                n_pairs_scored=0, candidates_compared=500,
                mass_above_threshold=0.0, mass_in_borderline=0.0,
                dip_statistic=0.05,
            ),
            cluster=ClusterProfile(transitivity_rate=0.95),
            matchkey=MatchkeyProfile(per_field={"a": FieldStats(0.5, 0.0, 10)}),
        ),
        decision=None, error=None, wall_clock_ms=10,
    ))

    with pytest.warns(DeprecationWarning, match=r"pick_committed"):
        result = h.cheapest_healthy()

    # Behavior change: now returns the RED entry (was None in v1.8).
    # The warning text must call out this change, not just the rename.
    assert result is not None
    assert result.config == "red_cfg"
```

Also keep one test for the case where it should *not* warn during normal use — actually the alias warns every time it's called, so any existing test that calls `cheapest_healthy()` will warn. This is fine for tests but means we should suppress the warning in test fixtures that don't care:

```python
def test_cheapest_healthy_warning_message_calls_out_behavior_change():
    """The DeprecationWarning text mentions the behavior change explicitly,
    so callers see they need to update their code (not just the function name)."""
    import pytest, warnings
    from goldenmatch.core.autoconfig_history import RunHistory

    h = RunHistory()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        h.cheapest_healthy()
    assert len(caught) >= 1
    msg = str(caught[0].message)
    assert "pick_committed" in msg
    # Must call out the behavior change (not just the rename)
    assert "RED" in msg or "behavior" in msg.lower()
```

- [ ] **Step 2: Run; expect failures.**

```bash
cd packages/python/goldenmatch && C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe -m pytest tests/test_autoconfig_history.py -k "cheapest_healthy" -v --timeout=60 2>&1 | tail -10
```

- [ ] **Step 3: Convert `cheapest_healthy()` to a deprecation alias.** Replace the existing method body:

```python
    def cheapest_healthy(self) -> HistoryEntry | None:
        """**DEPRECATED**: use ``pick_committed()`` instead.

        Behavior change in v1.9: this alias delegates to ``pick_committed()``,
        which returns RED entries when no GREEN/YELLOW exists (instead of
        returning None as in v1.8). Update callers that depended on the
        v1.8 None-on-all-RED behavior to either:
        * call ``pick_committed()`` and check the returned entry's
          ``.profile.health()`` to handle RED explicitly, or
        * inspect ``.health() != HealthVerdict.RED`` on the result.

        Removed in v2.0.
        """
        import warnings
        warnings.warn(
            "RunHistory.cheapest_healthy() is deprecated; use pick_committed(). "
            "Behavior change: pick_committed() returns RED entries when no "
            "GREEN/YELLOW exists (cheapest_healthy() returned None in v1.8).",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.pick_committed()
```

- [ ] **Step 4: Re-run the new tests; expect PASS.**

```bash
cd packages/python/goldenmatch && C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe -m pytest tests/test_autoconfig_history.py -k "cheapest_healthy" -v --timeout=60
```

- [ ] **Step 5: Run full history tests with warnings as errors** to find any internal callers that need updating:

```bash
cd packages/python/goldenmatch && C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe -m pytest tests/test_autoconfig_history.py -W error::DeprecationWarning -q --timeout=60 2>&1 | tail -15
```

If any test fails because it triggered the deprecation warning, that test is a v1.8-style consumer of `cheapest_healthy()`. Update it to call `pick_committed()` instead (it's a test, the rename is straightforward). Don't suppress the warning — silencing the warning in a test fixture defeats the purpose of the deprecation.

- [ ] **Step 6: Commit.**

```bash
git add packages/python/goldenmatch/goldenmatch/core/autoconfig_history.py packages/python/goldenmatch/tests/test_autoconfig_history.py
git commit -m "feat(autoconfig): cheapest_healthy() becomes deprecation alias for pick_committed()"
```

---

## Phase 3 — Wire the controller

### Task 3.1: Set `history.stop_reason` at every break in `run()`

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/autoconfig_controller.py` (the iteration loop in `run()`)
- Test: `packages/python/goldenmatch/tests/test_autoconfig_controller.py`

- [ ] **Step 1: Read the current `run()` body** to identify all break paths.

```bash
grep -n "break\|return\|^def run\|^    def run\|StopReason" /d/show_case/goldenmatch/packages/python/goldenmatch/goldenmatch/core/autoconfig_controller.py | head -30
```

You're looking for ≥6 break points: GREEN reached, CONVERGED, BUDGET_ITERATIONS, BUDGET_TIME, OSCILLATING, POLICY_SATISFIED, POLICY_NO_PROGRESS, plus the KeyboardInterrupt re-raise.

- [ ] **Step 2: Write failing tests, one per StopReason value.** Append to `tests/test_autoconfig_controller.py`:

```python
# ============================================================
# stop_reason recording (added 2026-05-08)
# ============================================================

def test_stop_reason_green_when_iteration_reaches_green_health(small_df):
    """When an iteration produces a GREEN profile, controller breaks with
    stop_reason=GREEN."""
    green = _green_subprofiles()
    controller = _make_controller_with_mocked_runner([green])
    config, profile, history = controller.run(small_df)
    assert history.stop_reason == StopReason.GREEN


def test_stop_reason_budget_iterations_when_max_iter_reached(small_df):
    """All iterations RED, budget exhausted → BUDGET_ITERATIONS."""
    red = _red_blocking_subprofile_dict()
    controller = _make_controller_with_mocked_runner(
        [red, red, red, red, red], max_iterations=2,
    )
    config, profile, history = controller.run(small_df)
    assert history.stop_reason == StopReason.BUDGET_ITERATIONS


def test_stop_reason_oscillating_when_policy_loops(small_df):
    """When policy alternates between two configs, history.is_oscillating()
    fires and the controller exits with stop_reason=OSCILLATING."""
    class _AlternatingPolicy:
        """Returns alternating configs — guaranteed to make is_oscillating()
        fire after a few iterations."""
        def __init__(self):
            self._calls = 0
        def propose(self, profile, config, history):
            self._calls += 1
            return config.model_copy(
                update={"threshold": 0.7 if self._calls % 2 else 0.8},
            )

    red = _red_blocking_subprofile_dict()
    controller = _make_controller_with_mocked_runner(
        [red, red, red, red, red],
        max_iterations=5,
        policy=_AlternatingPolicy(),
    )
    config, profile, history = controller.run(small_df)
    assert history.stop_reason == StopReason.OSCILLATING


def test_stop_reason_policy_satisfied_on_yellow_with_no_proposal(small_df):
    """When profile is YELLOW and no rule proposes a refit, exit with
    stop_reason=POLICY_SATISFIED."""
    yellow = _yellow_subprofiles()  # see existing test_autoconfig_controller fixtures
    controller = _make_controller_with_mocked_runner([yellow])
    # Policy returns None for YELLOW with no fire-able rule
    config, profile, history = controller.run(small_df)
    assert history.stop_reason == StopReason.POLICY_SATISFIED


def test_stop_reason_cancelled_on_keyboard_interrupt(small_df):
    """KeyboardInterrupt mid-iteration → stop_reason=CANCELLED set BEFORE
    the re-raise. Captured via the _LAST_CONTROLLER_RUN ContextVar (controller
    stashes history there at every exit path, including exceptions)."""
    from goldenmatch.core.autoconfig_controller import _LAST_CONTROLLER_RUN

    controller = AutoConfigController(
        policy=HeuristicRefitPolicy(),
        budget=ControllerBudget(sample_skip_below=1),
    )
    def interrupting(*_args, **_kw):
        raise KeyboardInterrupt
    controller._run_pipeline_sample = interrupting

    with pytest.raises(KeyboardInterrupt):
        controller.run(small_df)

    history = _LAST_CONTROLLER_RUN.get()
    assert history is not None
    assert history.stop_reason == StopReason.CANCELLED
```

You'll likely need helper fixtures (`_green_subprofiles`, `_red_blocking_subprofile_dict`, `_make_controller_with_mocked_runner`) — they exist in the current test file from earlier phases. Reuse.

- [ ] **Step 3: Run; expect AssertionError on each (stop_reason is None).**

```bash
cd packages/python/goldenmatch && C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe -m pytest tests/test_autoconfig_controller.py -k stop_reason -v --timeout=60 2>&1 | tail -15
```

- [ ] **Step 4: Wire `history.stop_reason = StopReason.X` at each break.** Edit the controller's `run()` body. The current pattern is something like:

```python
if profile_n.health() == HealthVerdict.GREEN:
    break
if history.profile_distance_to_prev() < self.budget.converge_epsilon:
    break
if history.is_oscillating():
    break

config_next = self.policy.propose(profile_n, config_n, history)
if config_next is None:
    break
if config_next == config_n:
    break

# bottom of loop: budget check
```

Add explicit `stop_reason` writes:

```python
if profile_n.health() == HealthVerdict.GREEN:
    history.stop_reason = StopReason.GREEN
    break
if history.profile_distance_to_prev() < self.budget.converge_epsilon:
    history.stop_reason = StopReason.CONVERGED
    break
if history.is_oscillating():
    history.stop_reason = StopReason.OSCILLATING
    break

config_next = self.policy.propose(profile_n, config_n, history)
if config_next is None:
    history.stop_reason = StopReason.POLICY_SATISFIED
    break
if config_next == config_n:
    history.stop_reason = StopReason.POLICY_NO_PROGRESS
    break
```

For the budget check (after the loop body completes max_iterations):
```python
# after loop:
if history.stop_reason is None:
    history.stop_reason = StopReason.BUDGET_ITERATIONS
```

For wall-clock time check (inside the loop):
```python
if (time.time() - start) > self.budget.max_seconds:
    history.stop_reason = StopReason.BUDGET_TIME
    break
```

For the KeyboardInterrupt path (inside the iteration's `try/except`):
```python
except KeyboardInterrupt:
    history.elapsed = timedelta(seconds=time.time() - start)
    history.stop_reason = StopReason.CANCELLED
    _LAST_CONTROLLER_RUN.set(history)   # observable from the test
    raise
```

- [ ] **Step 5: Re-run new tests; expect PASS.**

```bash
cd packages/python/goldenmatch && C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe -m pytest tests/test_autoconfig_controller.py -k stop_reason -v --timeout=60
```

- [ ] **Step 6: Run controller regression** to ensure no existing test broke.

```bash
cd packages/python/goldenmatch && C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe -m pytest tests/test_autoconfig_controller.py -q --timeout=120
```
Expected: all pass.

- [ ] **Step 7: Commit.**

```bash
git add packages/python/goldenmatch/goldenmatch/core/autoconfig_controller.py packages/python/goldenmatch/tests/test_autoconfig_controller.py
git commit -m "feat(autoconfig): record StopReason at each break point in controller.run()"
```

### Task 3.2: Switch controller to call `pick_committed()`, add health-aware logging

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/autoconfig_controller.py:211` (the existing `cheapest_healthy()` call site + surrounding logic)
- Test: `packages/python/goldenmatch/tests/test_autoconfig_controller.py`

- [ ] **Step 1: Read the current commit-decision branch** in `run()`. Look for:

```bash
grep -n "cheapest_healthy\|could not produce\|committing v0" /d/show_case/goldenmatch/packages/python/goldenmatch/goldenmatch/core/autoconfig_controller.py
```

- [ ] **Step 2: Write a failing test for the commit-RED end-to-end path.**

First, create the test fixture. Add a new file `tests/fixtures/autoconfig/red_provoking.csv`:

```csv
order_id,city,product_category
1,nyc,electronics
2,nyc,electronics
3,nyc,electronics
4,la,electronics
5,la,electronics
6,la,electronics
7,sf,electronics
8,sf,electronics
9,nyc,books
10,nyc,books
11,la,books
12,la,books
13,sf,books
14,sf,books
15,nyc,clothing
16,nyc,clothing
17,la,clothing
18,la,clothing
19,sf,clothing
20,sf,clothing
21,nyc,electronics
22,nyc,books
23,la,electronics
24,la,books
25,sf,electronics
26,sf,books
27,nyc,clothing
28,la,clothing
29,sf,clothing
30,nyc,electronics
31,la,books
32,sf,clothing
33,nyc,books
34,la,clothing
35,sf,electronics
36,nyc,electronics
37,la,electronics
38,sf,electronics
39,nyc,books
40,la,books
41,sf,books
42,nyc,clothing
43,la,clothing
44,sf,clothing
45,nyc,electronics
46,la,books
47,sf,clothing
48,nyc,books
49,la,electronics
50,sf,clothing
```

(Tiny dataset where blocking on `(city, product_category)` produces uniform-large blocks of ~5 records each, mass_above=1.0 false-positives, low transitivity → triggers RED rollup across iterations. Adapt rows if behavior differs in practice; the goal is "every iteration is RED.")

Then append a test in `tests/test_autoconfig_controller.py`:

```python
def test_controller_commits_red_when_data_provokes_red():
    """End-to-end: real pipeline on a fixture where every iteration produces
    a RED profile. Controller commits the best RED entry, surfaces stop_reason,
    runs _finalize on the committed RED config (output exists, just imperfect)."""
    import polars as pl
    from pathlib import Path

    fixture = Path(__file__).parent / "fixtures" / "autoconfig" / "red_provoking.csv"
    if not fixture.exists():
        pytest.skip(f"missing fixture: {fixture}")
    df = pl.read_csv(fixture)

    controller = AutoConfigController(
        policy=HeuristicRefitPolicy(),
        budget=ControllerBudget(
            max_iterations=3,
            sample_skip_below=10,    # force sampling on the small fixture
        ),
    )
    config, profile, history = controller.run(df)
    # The full-data finalize profile may be RED, YELLOW, or GREEN depending
    # on how the pipeline scales the issues — assert at minimum that we got
    # a real config (not v0 sentinel) and stop_reason was recorded.
    assert isinstance(config, GoldenMatchConfig)
    assert history.stop_reason is not None
    # At least one iteration produced a profile (not all errored)
    assert any(e.error is None for e in history.entries)


def test_controller_warns_on_red_commit(small_df, caplog):
    """Committing a RED entry triggers a WARNING log naming the failing
    sub-profile + stop_reason."""
    import logging
    red = _red_blocking_subprofile_dict()
    controller = _make_controller_with_mocked_runner(
        [red, red, red], max_iterations=2,
    )
    with caplog.at_level(logging.WARNING,
                          logger="goldenmatch.core.autoconfig_controller"):
        controller.run(small_df)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any(
        "best-effort RED" in r.message
        and "stop_reason=" in r.message
        and "failing_subprofile=" in r.message
        for r in warnings
    ), f"expected RED-commit warning; got: {[r.message for r in warnings]}"


def test_controller_info_log_on_yellow_commit(small_df, caplog):
    """YELLOW commit logs at INFO."""
    import logging
    yellow = _yellow_subprofiles()
    controller = _make_controller_with_mocked_runner([yellow])
    with caplog.at_level(logging.INFO,
                          logger="goldenmatch.core.autoconfig_controller"):
        controller.run(small_df)
    infos = [r for r in caplog.records if r.levelno == logging.INFO]
    assert any("YELLOW" in r.message for r in infos), (
        f"expected YELLOW-commit info; got: {[r.message for r in infos]}"
    )
```

- [ ] **Step 3: Run; expect failures** (commit logic not yet wired).

```bash
cd packages/python/goldenmatch && C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe -m pytest tests/test_autoconfig_controller.py -k "commits_red\|warns_on_red\|info_log_on_yellow" -v --timeout=60 2>&1 | tail -15
```

- [ ] **Step 4: Update controller's commit logic.** Replace the existing block around line 211:

```python
# Before (current behavior):
best_entry = history.cheapest_healthy()
if best_entry is None:
    logger.warning(
        "auto-config controller could not produce a healthy config; "
        "committing v0. Iterations: %d total, %d crashed, %d RED.",
        history.iteration, n_crashed, n_red,
    )
    return config_v0, _RED_PROFILE, history
# ... existing finalize + return ...
```

```python
# After:
best_entry = history.pick_committed()
if best_entry is None:
    # Every iteration errored — no usable profile produced. Fall back to v0.
    n_errored = len(history.errors)
    logger.error(
        "auto-config controller: every iteration errored (n=%d, "
        "stop_reason=%s); falling back to v0 + RED sentinel. "
        "Inspect _LAST_CONTROLLER_RUN.get() for tracebacks.",
        n_errored,
        history.stop_reason.name if history.stop_reason else "unset",
    )
    return config_v0, _RED_PROFILE, history

committed_health = best_entry.profile.health()
if committed_health == HealthVerdict.RED:
    failing = _first_red_subprofile(best_entry.profile)
    try:
        logger.warning(
            "auto-config committed best-effort RED config "
            "(iter=%d, stop_reason=%s, failing_subprofile=%s); "
            "downstream pipeline will run but output may be low-precision",
            best_entry.iteration,
            history.stop_reason.name if history.stop_reason else "unset",
            failing,
        )
    except Exception:
        pass    # don't let logging failure escape
elif committed_health == HealthVerdict.YELLOW:
    try:
        logger.info(
            "auto-config committed YELLOW config "
            "(iter=%d, stop_reason=%s)",
            best_entry.iteration,
            history.stop_reason.name if history.stop_reason else "unset",
        )
    except Exception:
        pass
# health == GREEN: silent success (today's behavior preserved)

# ... existing _maybe_decorate_with_llm_scorer + _finalize + memory + return ...
```

Add the `_first_red_subprofile` helper as a module-level private function (or static method on the controller):

```python
def _first_red_subprofile(profile: ComplexityProfile) -> str:
    """Return the name of the first sub-profile in canonical declaration
    order whose health() is RED. Used by the WARNING log on commit-RED.

    Canonical order: data, domain, matchkey, blocking, scoring, cluster.
    Returns 'rollup' if no individual sub-profile is RED but the rollup is
    (defensive — shouldn't happen with current health rules).
    """
    n_rows = profile.data.n_rows
    checks = [
        ("data", profile.data.health()),
        ("domain", profile.domain.health()),
        ("matchkey", profile.matchkey.health()),
        ("blocking", profile.blocking.health(n_rows=n_rows)),
        ("scoring", profile.scoring.health()),
        ("cluster", profile.cluster.health(n_rows=n_rows)),
    ]
    for name, health in checks:
        if health == HealthVerdict.RED:
            return name
    return "rollup"
```

- [ ] **Step 5: Re-run the new tests; expect PASS.**

```bash
cd packages/python/goldenmatch && C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe -m pytest tests/test_autoconfig_controller.py -k "commits_red\|warns_on_red\|info_log_on_yellow" -v --timeout=60
```

- [ ] **Step 6: Run controller regression.**

```bash
cd packages/python/goldenmatch && C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe -m pytest tests/test_autoconfig_controller.py -q --timeout=120 2>&1 | tail -10
```

If existing tests like `test_run_returns_v0_red_when_all_iterations_crash` now fail because they assumed v0 would be returned for "all-RED" — distinguish:
- **All errored** path is unchanged (returns v0). Tests asserting that case: keep.
- **All RED but no errors** path is changed (returns best RED, not v0). Tests asserting THIS case need updating: assert `config != config_v0`, `profile.health() == RED`, `history.stop_reason in {BUDGET_ITERATIONS, OSCILLATING}`.

Update each failing test individually based on its intent.

- [ ] **Step 7: Commit.**

```bash
git add packages/python/goldenmatch/goldenmatch/core/autoconfig_controller.py packages/python/goldenmatch/tests/test_autoconfig_controller.py packages/python/goldenmatch/tests/fixtures/autoconfig/red_provoking.csv
git commit -m "feat(autoconfig): commit best-effort entry via pick_committed() with health-aware logging"
```

---

## Phase 4 — Backward-compat sweep + spot checks

### Task 4.1: Sweep existing tests for hardcoded RED-fallback assertions

**Files:**
- Modify: `packages/python/goldenmatch/tests/test_autoconfig_facade.py` (small)
- Modify: `packages/python/goldenmatch/tests/test_autoconfig_no_double_run.py` (small)
- Modify: `packages/python/goldenmatch/tests/test_autoconfig_integration.py` (small)
- Modify: `packages/python/goldenmatch/tests/test_autoconfig_properties.py` (verify only)

- [ ] **Step 1: Find tests asserting v0 returned on all-RED.**

```bash
grep -rn "_RED_PROFILE\|config_v0\|config == v0\|all_iterations_red\|all_red" /d/show_case/goldenmatch/packages/python/goldenmatch/tests/ | head -20
```

- [ ] **Step 2: For each match, classify**:
  - **Test about all-iterations-errored** → keep, behavior preserved.
  - **Test about all-iterations-RED-but-not-errored** → update to assert new behavior (RED entry committed, stop_reason set).
  - **Test about explicit pathological inputs** (empty df, single col, etc.) → these short-circuit before the iteration loop; preserve.

- [ ] **Step 3: Run the full goldenmatch test suite** to find anything else that's broken.

```bash
rm -f ~/.goldenmatch/autoconfig_memory.db && cd packages/python/goldenmatch && C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe -m pytest -q --timeout=180 --ignore=tests/test_db.py --ignore=tests/test_reconcile.py --ignore=tests/test_mcp_and_watch.py --ignore=tests/test_embedder.py --ignore=tests/test_llm_boost.py --ignore=tests/benchmarks 2>&1 | tail -10
```

Expected: all pass after Phase 1-3 task fixes. If anything else fails, triage as above.

- [ ] **Step 4: Commit any cleanup.**

```bash
git add packages/python/goldenmatch/tests/
git commit -m "test(autoconfig): update assertions affected by best-effort commit semantics"
```
(Skip the commit if no test edits were needed.)

### Task 4.2: Verify property tests still hold

**Files:**
- Inspect only: `packages/python/goldenmatch/tests/test_autoconfig_properties.py`

- [ ] **Step 1: Run property tests** (Hypothesis, max_examples=20-30, ~22s wall):

```bash
rm -f ~/.goldenmatch/autoconfig_memory.db && cd packages/python/goldenmatch && C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe -m pytest tests/test_autoconfig_properties.py -v --timeout=180
```

Expected: all 5 pass. The change should not affect:
- Determinism (same input → same `pick_committed` result)
- Sample stability (n_rows < sample_skip_below → no sampling)
- Profile non-collapse (`_finalize` doesn't go RED if drift < threshold AND any history entry was non-RED)
- No silent crashes (the controller never raises uncaught from `run()` except `KeyboardInterrupt`)
- History audit invariant (every decision corresponds to a known rule)

- [ ] **Step 2: If any property fails, investigate.** Don't loosen the property to make it pass — fix the bug or document why the property is now invalid.

---

## Phase 5 — Benchmark verification

### Task 5.1: Confirm DBLP-ACM, Febrl3, NCVR all hold

Per spec §Acceptance #3 and #4: each measurement must produce a non-RED commit AND F1 must hold at v1.8.0 levels.

- [ ] **Step 1: DBLP-ACM** (require dataset present at `tests/benchmarks/datasets/DBLP-ACM/`):

```bash
rm -f ~/.goldenmatch/autoconfig_memory.db
GOLDENMATCH_AUTOCONFIG_MEMORY=0 C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe .profile_tmp/measure_dblp_acm_controller.py 2>&1 | tee .profile_tmp/dblp_acm_after_best_effort.txt | tail -20
```
Expected: F1 ≥ 0.94, `controller_profile.health() != RED`. If F1 regresses or commits RED, **don't proceed** — investigate.

- [ ] **Step 2: Febrl3 + NCVR** (Febrl3 via recordlinkage; NCVR sample optional):

```bash
rm -f ~/.goldenmatch/autoconfig_memory.db
GOLDENMATCH_AUTOCONFIG_MEMORY=0 C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe .profile_tmp/baseline_febrl3_ncvr.py 2>&1 | tee .profile_tmp/febrl3_ncvr_after_best_effort.txt | tail -10
GOLDENMATCH_AUTOCONFIG_MEMORY=0 C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe .profile_tmp/measure_ncvr_controller.py 2>&1 | tee .profile_tmp/ncvr_after_best_effort.txt | tail -10
```
Expected for both: F1 ≥ 0.94, no RED commit.

- [ ] **Step 3: Document the four numbers** (DBLP-ACM, Febrl3, NCVR + their healths) in a working note in `.profile_tmp/` (gitignored, local-only). The durable record of these numbers is the PR description (per spec acceptance #6) — `.profile_tmp/` is just a scratch buffer for copy-pasting into the PR body and CHANGELOG.

### Task 5.2: Re-measure DQbench

Per spec §Acceptance #5.

- [ ] **Step 1: DQbench no-LLM run.**

```bash
unset OPENAI_API_KEY ANTHROPIC_API_KEY GOLDENMATCH_AUTOCONFIG_LLM
rm -f ~/.goldenmatch/autoconfig_memory.db
GOLDENMATCH_AUTOCONFIG_MEMORY=0 dqbench run goldenmatch-zeroconfig --adapter .profile_tmp/goldenmatch_zeroconfig_adapter.py 2>&1 | tee .profile_tmp/dqbench_no_llm_after_best_effort.txt | tail -20
```
Expected: composite ≥ 65 (vs 62.87 baseline). Per-tier F1 should be reported in the table dqbench prints. If composite is ≤ 62.87, the change broke something — diagnose.

- [ ] **Step 2: DQbench with-LLM run.**

```bash
set -a && source /d/show_case/goldencheck/.testing/.env && set +a
rm -f ~/.goldenmatch/autoconfig_memory.db
GOLDENMATCH_AUTOCONFIG_MEMORY=0 dqbench run goldenmatch-zeroconfig --adapter .profile_tmp/goldenmatch_zeroconfig_adapter.py 2>&1 | tee .profile_tmp/dqbench_with_llm_after_best_effort.txt | tail -20
```
Expected: composite ≥ 65 (or higher if LLM scorer engages on RED commits). May cost ~$1-3 in OpenAI calls, bounded by `BudgetConfig(max_calls=500, max_cost_usd=1.0)`.

- [ ] **Step 3: Capture stop_reason traces from the DQbench runs** (per spec §Acceptance #6). Inspect `.profile_tmp/dqbench_no_llm_after_best_effort.txt` for any controller log lines showing `stop_reason=`. If the dqbench harness suppresses logs, run the diagnostic:

```bash
GOLDENMATCH_AUTOCONFIG_MEMORY=0 C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe .profile_tmp/diagnose_dqbench_blocking.py 2>&1 | tee .profile_tmp/dqbench_stop_reasons_after.txt | tail -50
```
Note which `StopReason` each tier landed on. This goes in the PR description.

---

## Phase 6 — Documentation

> **Order constraint:** Phase 5 must complete first. Phase 6 fills `<result>` placeholders in CHANGELOG with numbers produced by Phase 5. Don't land Phase 6 commits before Phase 5 measurement output exists in `.profile_tmp/`.

### Task 6.1: Update CLAUDE.md

**Files:**
- Modify: `packages/python/goldenmatch/CLAUDE.md` (Auto-Config section)

- [ ] **Step 1: Add notes** about the v1.9 behavior changes:

```markdown
- v1.9 (2026-05-XX): commit best-effort `HistoryEntry` when no GREEN/YELLOW
  exists (was: fall back to v0 + RED sentinel). `RunHistory.cheapest_healthy()`
  is now a deprecation alias for `pick_committed()`; both return RED entries
  when no GREEN/YELLOW survives the lex key. Behavior change is documented in
  the DeprecationWarning text. `RunHistory.stop_reason: StopReason | None` is
  the new observable — populated at every break in the controller's
  iteration loop, surfaces via `result.postflight_report.controller_history.stop_reason`.
- `StopReason` enum lives in `core/complexity_profile.py` (moved from
  `core/autoconfig_controller.py` to break a circular import shape).
```

- [ ] **Step 2: No tests for the doc change.** Just confirm the file still parses.

```bash
head -100 packages/python/goldenmatch/CLAUDE.md > /dev/null && echo "ok"
```

- [ ] **Step 3: Commit.**

```bash
git add packages/python/goldenmatch/CLAUDE.md
git commit -m "docs(autoconfig): note v1.9 best-effort commit + stop_reason"
```

### Task 6.2: Update CHANGELOG

**Files:**
- Modify: `packages/python/goldenmatch/CHANGELOG.md`

- [ ] **Step 1: Add a `[1.9.0]` section** above the `[1.8.0]` heading.

```markdown
## [1.9.0] - 2026-05-XX

### Added
- **Best-effort commit when iterations don't reach green.** `RunHistory.pick_committed()`
  returns the highest-ranked entry by lex key `(health_rank, -mass_separation,
  iteration)`, including RED entries when no GREEN/YELLOW exists. Closes the
  DQbench T1/T2 score wall — all the post-iteration plumbing (LLM scorer
  decoration, standardization, finalize, memory) now runs even when the
  controller couldn't drive the profile to green.
- **`RunHistory.stop_reason: StopReason | None`** populated at every break
  point in the controller's iteration loop. Observable via
  `result.postflight_report.controller_history.stop_reason`.
- **Health-aware commit logging.** WARNING when committed health is RED
  (with failing sub-profile name + stop_reason); INFO when YELLOW; silent
  on GREEN.

### Changed
- `RunHistory.cheapest_healthy()` is now a deprecation alias for
  `pick_committed()`. **Behavior change**: returns RED entries when no
  GREEN/YELLOW exists (was: returned None). DeprecationWarning text calls
  out the change so callers update their code, not just the function name.
  Removed in v2.0.
- `StopReason` enum moved from `core/autoconfig_controller.py` to
  `core/complexity_profile.py` (next to `HealthVerdict`).

### Benchmarks (zero-config, no LLM)

| Dataset | v1.8.0 | v1.9.0 |
|---|---|---|
| DBLP-ACM | 0.9641 | <result> |
| Febrl3 | 0.9443 | <result> |
| NCVR | 0.9719 | <result> |
| DQbench composite | 62.87 | <result> |
```

Substitute the actual numbers from Phase 5.

- [ ] **Step 2: Bump version** in `packages/python/goldenmatch/pyproject.toml` and `packages/python/goldenmatch/goldenmatch/__init__.py`:

```bash
grep -n 'version = "1.8.0"\|__version__ = "1.8.0"' packages/python/goldenmatch/pyproject.toml packages/python/goldenmatch/goldenmatch/__init__.py
```

Edit each match to `1.9.0`.

- [ ] **Step 3: Verify version.**

```bash
C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe -c "import goldenmatch; print(goldenmatch.__version__)"
```
Expected: `1.9.0`.

- [ ] **Step 4: Commit.**

```bash
git add packages/python/goldenmatch/CHANGELOG.md packages/python/goldenmatch/pyproject.toml packages/python/goldenmatch/goldenmatch/__init__.py
git commit -m "release(goldenmatch): v1.9.0 — best-effort commit"
```

---

## Final acceptance gate

Before opening the release PR (or merging into a non-release feature branch):

- [ ] All five test tiers from spec §Testing pass: `cd packages/python/goldenmatch && C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe -m pytest -q --timeout=180 --ignore=tests/test_db.py --ignore=tests/test_reconcile.py --ignore=tests/test_mcp_and_watch.py --ignore=tests/test_embedder.py --ignore=tests/test_llm_boost.py --ignore=tests/benchmarks` shows ≥ 1838 passing (1825 baseline + ~13 new).
- [ ] DBLP-ACM, Febrl3, NCVR each commit non-RED (`controller_profile.health() != RED`) AND F1 ≥ 0.94 (per Phase 5.1).
- [ ] DQbench composite ≥ 65 (no LLM); both no-LLM and with-LLM runs documented in `.profile_tmp/`.
- [ ] No new ruff errors: `ruff check packages/python/goldenmatch/goldenmatch/`.
- [ ] CLAUDE.md and CHANGELOG updated with v1.9 entries.
- [ ] PR description includes the four-benchmark before/after table + a sample stop_reason trace from a DQbench run (per spec §Acceptance #6).

Open the PR via `gh pr create` per the standard release SOP (CLAUDE.md "Branch & Merge SOP"). **Before push:** `gh auth switch --user benzsevern` (per package CLAUDE.md — `benzsevern/*` repos use the personal account, not the work account); switch back to `benzsevern-mjh` immediately after push. Squash-merge after CI green and at least one review pass. Tag `v1.9.0` after merge to trigger the PyPI publish workflow. Update About/Topics, wiki, and announcement Discussion as in the v1.8.0 release.
