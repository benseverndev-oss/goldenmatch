# Suggester Gym Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the measurement gym that manufactures headroom for the config-suggestion engine — a perturbation catalog (damage zero-config one axis at a time, measure `recovery%`) plus a corruption sweep — so "did the suggester get smarter or dumber" becomes a CI-gated number.

**Architecture:** Extends `scripts/suggest_quality/`. A new unsupervised convergence helper (`converge_unsupervised`), a pure perturbation catalog (`perturbations.py`), a recovery-eval loop + corruption sweep (`gym.py`), a `recovery_pct` metric, a `gym` CLI subcommand, and a CI-gated `gym_scorecard.json`. The suggester runs fully unsupervised; the gym uses ground-truth F1 only to grade. The oracle (`oracle.py`) is NOT modified.

**Tech Stack:** Python 3.11+ (polars, scipy), the Plan-1 config-suggestion engine (`goldenmatch.core.suggest`), `MatchEngine.from_dataframe`, `core/evaluate.py`. `.venv` at main repo root.

**Spec:** `docs/superpowers/specs/2026-06-24-suggester-gym-design.md`

---

## Conventions for the implementing engineer

- **Work from** `D:\show_case\goldenmatch\.worktrees\suggest-gym` (branch `feat/suggest-gym`).
- **Run Python** via the main-repo venv with the worktree on PYTHONPATH (Windows separator is `;` NOT `:`):
  ```
  export PYTHONPATH="D:/show_case/goldenmatch/.worktrees/suggest-gym/packages/python/goldenmatch;D:/show_case/goldenmatch/.worktrees/suggest-gym"
  POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8 D:/show_case/goldenmatch/.venv/Scripts/python.exe ...
  ```
  Confirm `import goldenmatch; print(goldenmatch.__file__)` resolves into the worktree and `hasattr(MatchEngine, "from_dataframe")` is True before running anything native.
- **Native is built** in this base branch (Plan 1 built `goldenmatch._native.suggest_config`); gym integration tests should RUN, not skip. Pure-function tests need no native.
- **Never run the full pytest suite** (OOMs the box). Run targeted files.
- **Determinism:** the harness already pins `GOLDENMATCH_AUTOCONFIG_MEMORY=0` etc. via `cli.py::_pin_determinism`; reuse it. Fixed seeds everywhere.
- **Commit after each green step.** Prefix `feat(gym):` / `test(gym):`. End commit messages with the Co-Authored-By + Claude-Session trailers.
- **Reuse, do not reinvent:** `oracle.py` already has `_auto_configure_no_rerank(df)` (zero-config builder), `_run_config(df, config) -> (clusters, scored_pairs)`, and `_compute_f1(clusters, scored_pairs, gt_pairs)`. The gym reuses these three verbatim (import them). Do NOT write new versions.

---

## File structure

- `scripts/suggest_quality/converge.py` (new) — `converge_unsupervised(df, config) -> (final_config, trail)`.
- `scripts/suggest_quality/perturbations.py` (new) — `Perturbation` dataclass + `CATALOG`.
- `scripts/suggest_quality/gym.py` (new) — `run_catalog(...)`, `run_corruption_sweep(...)`, the per-record eval.
- `scripts/suggest_quality/metrics.py` (modify) — add `recovery_pct`.
- `scripts/suggest_quality/datasets.py` (modify) — add `corruption_sweep_levels()` generator (corruption-injection wrapper over a clean synthetic base).
- `scripts/suggest_quality/cli.py` (modify) — add `gym` subcommand; extend `bless`/`gate` for `gym_scorecard.json`.
- `scripts/suggest_quality/baselines/gym_scorecard.json` (new, committed by `bless`).
- `.github/workflows/bench-suggest-quality.yml` (modify) — run `gym gate`.
- Tests: `packages/python/goldenmatch/tests/test_gym_metrics.py`, `test_gym_perturbations.py`, `test_gym_converge.py`, `test_gym_smoke.py`.

---

## Task 1: `recovery_pct` metric

**Files:** Modify `scripts/suggest_quality/metrics.py`; Test `packages/python/goldenmatch/tests/test_gym_metrics.py`.

- [ ] **Step 1: Failing tests** (pure, no native)

```python
import math
from scripts.suggest_quality.metrics import recovery_pct

def test_full_recovery_is_one():
    # degraded 0.70, recovered back to ceiling 0.90
    assert abs(recovery_pct(0.70, 0.90, 0.90) - 1.0) < 1e-9

def test_half_recovery():
    assert abs(recovery_pct(0.70, 0.80, 0.90) - 0.5) < 1e-9

def test_overshoot_above_one():
    # recovered beyond the zero-config ceiling
    assert recovery_pct(0.70, 0.95, 0.90) > 1.0

def test_negative_when_made_worse():
    assert recovery_pct(0.70, 0.60, 0.90) < 0.0

def test_no_damage_returns_nan():
    # ceiling == degraded (denominator below DAMAGE_EPS) -> nan
    assert math.isnan(recovery_pct(0.90, 0.90, 0.90))
    assert math.isnan(recovery_pct(0.899, 0.90, 0.90))  # gap 0.001 < 0.005
```

- [ ] **Step 2: Run → fail.** `... -m pytest packages/python/goldenmatch/tests/test_gym_metrics.py -v`
- [ ] **Step 3: Implement** in `metrics.py`:

```python
DAMAGE_EPS = 0.005  # min ceiling-minus-degraded gap for recovery% to be meaningful

def recovery_pct(f1_degraded: float, f1_recovered: float, f1_ceiling: float) -> float:
    """Fraction of the damage the suggester recovered.

    (f1_recovered - f1_degraded) / (f1_ceiling - f1_degraded).
    1.0 = fully undid the damage; >1.0 = beat the zero-config ceiling;
    <0.0 = made it worse. Returns nan when the damage gap < DAMAGE_EPS
    (no meaningful damage to recover). Not clamped.
    """
    import math
    denom = f1_ceiling - f1_degraded
    if denom < DAMAGE_EPS:
        return float("nan")
    return (f1_recovered - f1_degraded) / denom
```

- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** `feat(gym): recovery_pct metric`.

---

## Task 2: `converge_unsupervised` helper

**Files:** Create `scripts/suggest_quality/converge.py`; Test `packages/python/goldenmatch/tests/test_gym_converge.py`.

This is the realistic no-labels convergence: apply what `review_config` emits (already self-verified) until it has nothing more. The oracle's label-driven loop is NOT reused.

- [ ] **Step 1: Failing tests** — drive the loop with FAKE `review_config`/`apply_suggestion` (monkeypatch) so the mechanics are tested without native or a real pipeline.

```python
import types
from scripts.suggest_quality import converge as C

class _Sugg:
    def __init__(self, sid): self.id = sid

def test_stops_when_no_suggestions(monkeypatch):
    monkeypatch.setattr(C, "review_config", lambda df, cfg: [])
    monkeypatch.setattr(C, "apply_suggestion", lambda cfg, s: cfg)
    final, trail = C.converge_unsupervised(df=object(), config={"v": 0})
    assert trail == []

def test_applies_until_empty(monkeypatch):
    calls = {"n": 0}
    def fake_review(df, cfg):
        # emit 2 distinct suggestions across 2 rounds, then nothing
        if calls["n"] >= 2: return []
        return [_Sugg(f"s{calls['n']}")]
    def fake_apply(cfg, s):
        calls["n"] += 1
        return {"applied": s.id}
    monkeypatch.setattr(C, "review_config", fake_review)
    monkeypatch.setattr(C, "apply_suggestion", fake_apply)
    final, trail = C.converge_unsupervised(df=object(), config={"v": 0})
    assert [s.id for s in trail] == ["s0", "s1"]

def test_cycle_guard_breaks_on_repeated_id(monkeypatch):
    monkeypatch.setattr(C, "review_config", lambda df, cfg: [_Sugg("same")])
    monkeypatch.setattr(C, "apply_suggestion", lambda cfg, s: cfg)
    final, trail = C.converge_unsupervised(df=object(), config={"v": 0})
    assert len(trail) == 1  # applied "same" once, then broke on repeat

def test_respects_step_cap(monkeypatch):
    counter = {"i": 0}
    def fake_review(df, cfg):
        counter["i"] += 1
        return [_Sugg(f"s{counter['i']}")]  # always a NEW id -> would loop forever
    monkeypatch.setattr(C, "review_config", fake_review)
    monkeypatch.setattr(C, "apply_suggestion", lambda cfg, s: cfg)
    final, trail = C.converge_unsupervised(df=object(), config={"v": 0}, step_cap=4)
    assert len(trail) == 4
```

- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement** `converge.py`:

```python
"""Unsupervised greedy convergence: apply review_config's (self-verified)
suggestions until none remain. NO ground truth -- the realistic path a user
gets. Distinct from oracle.py's label-driven convergence (which peeks at F1)."""
from __future__ import annotations

import copy

# Imported at module level so tests can monkeypatch these names on this module.
from goldenmatch.core.suggest import apply_suggestion, review_config

_STEP_CAP = 5

def converge_unsupervised(df, config, *, step_cap: int = _STEP_CAP):
    current = copy.deepcopy(config)
    applied_ids: set = set()
    trail: list = []
    for _ in range(step_cap):
        suggestions = review_config(df, current)
        if not suggestions:
            break
        top = suggestions[0]
        if top.id in applied_ids:
            break
        applied_ids.add(top.id)
        current = apply_suggestion(current, top)
        trail.append(top)
    return current, trail
```

Note: importing `review_config`/`apply_suggestion` at module top means `monkeypatch.setattr(C, "review_config", ...)` works. (`copy.deepcopy` of `{"v":0}` in tests is fine.)

- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** `feat(gym): unsupervised convergence helper`.

---

## Task 3: Perturbation catalog

**Files:** Create `scripts/suggest_quality/perturbations.py`; Test `packages/python/goldenmatch/tests/test_gym_perturbations.py`.

Each perturbation is a pure config→config mutation (deep-copy first; never mutate input) tagged with the rule that should reverse it. Read `packages/python/goldenmatch/goldenmatch/config/schemas.py` for the real fields: `GoldenMatchConfig.get_matchkeys()`, `MatchkeyConfig.{name,type,threshold,fields,negative_evidence}`, `MatchkeyField.{field,scorer,weight}`, `config.blocking.{keys,passes}`. Read Plan-1's `goldenmatch/core/suggest/apply.py` for the exact mutation idioms (it already sets threshold/scorer/NE on a `model_copy(deep=True)`).

- [ ] **Step 1: Failing tests** — build a small hand-made `GoldenMatchConfig` with one weighted matchkey (fields: `first_name` token_sort, `address` jaro_winkler; threshold 0.85), assert each perturbation's `apply` does the right mutation and leaves the original unchanged, and `applies_to` gates correctly.

```python
from scripts.suggest_quality.perturbations import CATALOG, get as get_perturbation

def _cfg():
    from goldenmatch.config.schemas import GoldenMatchConfig, MatchkeyConfig, MatchkeyField
    mk = MatchkeyConfig(name="person", type="weighted", threshold=0.85, fields=[
        MatchkeyField(field="first_name", scorer="jaro_winkler", weight=1.0),
        MatchkeyField(field="address", scorer="jaro_winkler", weight=1.0),
    ])
    return GoldenMatchConfig(matchkeys=[mk])

def test_threshold_too_low_lowers_and_preserves_original():
    p = get_perturbation("threshold_too_low")
    orig = _cfg()
    out = p.apply(orig)
    assert out.get_matchkeys()[0].threshold == 0.70  # 0.85 - 0.15
    assert orig.get_matchkeys()[0].threshold == 0.85  # immutability

def test_bad_freetext_scorer_sets_token_sort():
    p = get_perturbation("bad_freetext_scorer")
    out = p.apply(_cfg())
    addr = [f for f in out.get_matchkeys()[0].fields if f.field == "address"][0]
    assert addr.scorer == "token_sort"

def test_threshold_too_low_applies_to_weighted_config():
    assert get_perturbation("threshold_too_low").applies_to(_cfg()) is True

def test_every_catalog_entry_has_required_shape():
    for p in CATALOG:
        assert p.name and p.expected_rule and callable(p.apply) and callable(p.applies_to)
        assert isinstance(p.builds_on_existing_rule, bool)
```

- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement** `perturbations.py`:

```python
from __future__ import annotations
from dataclasses import dataclass
from typing import Callable
import copy

@dataclass(frozen=True)
class Perturbation:
    name: str
    expected_rule: str            # the suggestion kind that should reverse it
    builds_on_existing_rule: bool # True = fixing rule exists today (counts in headline)
    description: str
    applies_to: Callable          # (config) -> bool
    apply: Callable               # (config) -> new config (deep-copied, immutable)
```

Then define helper mutators (deep-copy via `config.model_copy(deep=True)`; find the primary weighted matchkey; locate a free-text field where `field in {"address","street","name",...}` or `scorer in {"jaro_winkler","token_sort","ensemble"}`). Implement these catalog entries (mutate the deep copy, return it):

- `threshold_too_low` (expected `lower_threshold`... NO: expected `raise_threshold`; `builds_on_existing_rule=True`): primary threshold `max(floor, t - 0.15)`. `applies_to`: a weighted/fuzzy matchkey with a non-None threshold exists.
- `threshold_too_high` (expected `lower_threshold`, existing=True): primary threshold `min(0.99, t + 0.10)`.
- `bad_freetext_scorer` (expected `swap_scorer`, existing=True): set a free-text field's scorer to `token_sort`. `applies_to`: such a field exists and isn't already token_sort.
- `missing_negative_evidence` (expected `add_negative_evidence`, existing=True): remove one entry from the primary matchkey's `negative_evidence` (if any). `applies_to`: `negative_evidence` non-empty.
- `dropped_blocking_pass` (expected `add_blocking_pass`, existing=**False**): drop one `config.blocking.passes` entry, keeping ≥1. `applies_to`: `>1` blocking pass.
- `flattened_weights` (expected `adjust_field_weight`, existing=False): set every field `weight = 1.0`. `applies_to`: a weighted matchkey with ≥2 fields whose weights aren't already all equal.
- `skewed_weight` (expected `adjust_field_weight`, existing=False): multiply one field's weight by 5. `applies_to`: weighted matchkey with ≥2 fields.
- `naive_single_fuzzy` (expected `""`/multi, existing=False, a realism scenario): replace config with a minimal single fuzzy matchkey on the most-identifying string column at a default 0.85 threshold. `applies_to`: always True if any string column is known from the matchkeys.

Provide `CATALOG: list[Perturbation]` and `get(name) -> Perturbation`.

IMPORTANT: `expected_rule` strings MUST match the kernel's `SuggestionKind` serde names exactly (`raise_threshold`, `lower_threshold`, `swap_scorer`, `add_negative_evidence`) for the built-rule ones; the unbuilt-rule ones (`add_blocking_pass`, `adjust_field_weight`) are placeholder names the future rules will adopt — document that.

- [ ] **Step 4: Run → PASS.** Add a test that each built-rule perturbation's `expected_rule` is one of the 4 real `SuggestionKind` snake_case names (import from the kernel/contract or hard-code the set with a comment).
- [ ] **Step 5: Commit** `feat(gym): perturbation catalog`.

---

## Task 4: The gym recovery loop

**Files:** Create `scripts/suggest_quality/gym.py`; Test `packages/python/goldenmatch/tests/test_gym_smoke.py` (native-guarded).

- [ ] **Step 1: Write `gym.py::evaluate_perturbation`** (and `run_catalog`). Reuse oracle helpers:

```python
from scripts.suggest_quality.oracle import (
    _auto_configure_no_rerank, _run_config, _compute_f1,
)
from scripts.suggest_quality.converge import converge_unsupervised
from scripts.suggest_quality.metrics import recovery_pct, DAMAGE_EPS
```

`evaluate_perturbation(df, gt_pairs, perturbation, ceiling_config, f1_ceiling) -> dict`:
1. If not `perturbation.applies_to(ceiling_config)` → return `{"status": "n/a"}`.
2. `degraded = perturbation.apply(ceiling_config)`; `clusters_d, scored_d = _run_config(df, degraded)`; `f1_degraded = _compute_f1(...)`.
3. If `f1_ceiling - f1_degraded < DAMAGE_EPS` → return `{"status": "no_damage", "f1_ceiling":..., "f1_degraded":...}`.
4. `recovered, trail = converge_unsupervised(df, degraded)`; `f1_recovered = _compute_f1(_run_config(df, recovered)..., gt_pairs)`.
5. `rec = recovery_pct(f1_degraded, f1_recovered, f1_ceiling)`.
6. `expected_rule_fired = any(s.kind == perturbation.expected_rule for s in trail)` (each `Suggestion` has `.kind`).
7. Return `{status:"ok", name, expected_rule, builds_on_existing_rule, f1_ceiling, f1_degraded, f1_recovered, recovery_pct: rec, expected_rule_fired, n_applied: len(trail), applied_kinds: [s.kind for s in trail]}`.

`run_catalog(datasets, perturbations) -> list[dict]`: for each dataset with GT pairs, build `ceiling_config = _auto_configure_no_rerank(df)` once, `f1_ceiling = _compute_f1(_run_config(df, ceiling_config)..., gt)`, then loop perturbations. Wrap each `(dataset,perturbation)` in try/except → record an `error` status (degrade gracefully, never crash the whole run). Return flat records tagged with dataset name.

- [ ] **Step 2: Failing smoke test** (native-guarded skip — mirror `test_suggest_oracle_smoke.py`'s `_suggest_available` guard incl. `MatchEngine.from_dataframe`):

```python
def test_gym_recovers_a_builtin_rule_perturbation():
    # synthetic dataset; at least one BUILT-rule perturbation that causes damage
    # must recover > 0 with the expected rule firing.
    from scripts.suggest_quality.datasets import REGISTRY
    from scripts.suggest_quality.perturbations import CATALOG
    from scripts.suggest_quality.gym import run_catalog
    synthetic = [d for d in REGISTRY if d.name == "synthetic"]
    records = run_catalog(synthetic, [p for p in CATALOG if p.builds_on_existing_rule])
    damaging = [r for r in records if r.get("status") == "ok"]
    assert damaging, "no built-rule perturbation caused measurable damage on synthetic"
    assert any(r["recovery_pct"] > 0 and r["expected_rule_fired"] for r in damaging), \
        "gym measured no real recovery by the expected rule"
```

- [ ] **Step 3: Run → likely FAIL first** (module/loop not implemented). Implement until it passes. NOTE: tuning may be needed — if no built-rule perturbation causes damage on `synthetic` (e.g. zero-config is too robust there), use a dataset where it does (try `ncvr_synthetic`, or make the perturbation harsher, e.g. threshold −0.20). The test's PURPOSE is to prove the gym measures real recovery; pick the perturbation/dataset combo that demonstrates it. Document the chosen combo.
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** `feat(gym): recovery-eval loop`.

---

## Task 5: Corruption-injection wrapper + sweep

**Files:** Modify `scripts/suggest_quality/datasets.py` (or a new `corruption.py`); extend `gym.py` with `run_corruption_sweep`; Test `test_gym_perturbations.py` (add) or a new `test_gym_corruption.py`.

GROUNDING NOTE: `scripts/autoconfig_quality/anchors.py::gen_labeled` produces synthetic person data but has NO corruption-intensity knob (typo rate baked into `_typo`). So add a thin **corruption-injection wrapper** over a CLEAN base.

- [ ] **Step 1: Failing tests** (pure, no native) for `corrupt_dataframe(df, level, seed)`:
  - level 0.0 → returns df unchanged (or value-identical).
  - level 0.3 → ~30% of string cells in target columns are altered (typo/transpose/drop-token); ground-truth pairs are PRESERVED (corruption changes values, not which rows are the same entity).
  - deterministic given seed.

- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement** `corrupt_dataframe(df, level, seed, columns=None)` — for each row, with probability `level`, apply one of {char-typo, adjacent-transpose, drop-a-token} to a chosen string column. Build a `corruption_sweep_levels(levels=(0.0,0.1,0.2,0.3,0.4), seed=...) -> list[(level, df, gt_pairs)]` that starts from a clean `gen_labeled` base (with its known GT) and corrupts at each level (GT unchanged). Add `run_corruption_sweep(...)` to `gym.py`: per level, `ceiling=_auto_configure_no_rerank(df)`... actually for the sweep the baseline IS zero-config on corrupted data: `cfg0=_auto_configure_no_rerank(df); f1_zero=_compute_f1(...)`; `recovered,_=converge_unsupervised(df,cfg0); f1_rec=_compute_f1(...)`; record `{level, f1_zeroconfig, f1_recovered, lift: f1_rec - f1_zero}`.
- [ ] **Step 4: Run → PASS** (pure tests). Add a native-guarded smoke that `run_corruption_sweep` returns one record per level with numeric `lift` and `lift >= -1e-9` at every level (self-verify must hold).
- [ ] **Step 5: Commit** `feat(gym): corruption-injection wrapper + sweep`.

---

## Task 6: `gym` CLI subcommand + report boards

**Files:** Modify `scripts/suggest_quality/cli.py`.

- [ ] **Step 1:** Add a `gym` subcommand (mirror how `report` is wired). It runs `run_catalog` over the registry's GT datasets + `run_corruption_sweep`, then prints:
  - **Catalog board:** one row per `ok`/`no_damage`/`n/a` `(dataset,perturbation)` with `recovery%`, `expected_rule_fired` ✓/✗, `n_applied`, the 3 F1s. Then a per-rule rollup, then the **headline gym score** = mean `recovery_pct` over `status=="ok"` records where `builds_on_existing_rule` is True. Print unbuilt-rule perturbations under a separate "standing targets (rule not built yet)" heading (their recovery%, expected ~0).
  - **Sweep board:** `level | f1_zeroconfig | f1_recovered | lift` rows + a one-line "lift curve" summary.
- [ ] **Step 2: Smoke** (native-guarded): `... -m scripts.suggest_quality.cli gym --datasets synthetic` prints both boards with a numeric headline (not a stub). Verify by eye/capture.
- [ ] **Step 3: Commit** `feat(gym): gym CLI subcommand + report boards`.

---

## Task 7: gym bless + gate + baseline + CI

**Files:** Modify `cli.py`; create `scripts/suggest_quality/baselines/gym_scorecard.json` (via `bless`); modify `.github/workflows/bench-suggest-quality.yml`; Test `test_gym_smoke.py` (add a gate-logic unit test if feasible without native).

- [ ] **Step 1:** Extend `bless` (or add `gym-bless`) to write the gym records + per-rule rollup + sweep + headline to `baselines/gym_scorecard.json`. Extend `gate` (or add `gym-gate`) to load it and FAIL (exit 1) when: a built-rule perturbation's `recovery_pct` drops by more than `RECOVERY_GATE_TOL = 0.05` vs blessed, OR the headline drops by more than `RECOVERY_GATE_TOL`, OR any sweep level's `lift < 0`. Reuse the Plan-1 gate semantics already in `cli.py` (zero-eval guard → fail; missing-blessed dataset → fail). Decide cleanly whether `gym` is a new subcommand pair (`gym-bless`/`gym-gate`) or a `--mode gym` flag on the existing ones — prefer separate subcommands for clarity; mirror the existing structure.
- [ ] **Step 2: Seed the baseline:** run `gym-bless` over the deterministic CI-available datasets (synthetic, ncvr_synthetic, anchors) + the sweep, and COMMIT `gym_scorecard.json`. Confirm `gym-gate` then PASSES against it (a fresh run reproduces the blessed numbers within tolerance).
- [ ] **Step 3: CI:** extend `.github/workflows/bench-suggest-quality.yml` to also run the gym gate after the oracle gate (same native build + symbol assert + `large-new-64GB`). A separate step `uv run python -m scripts.suggest_quality.cli gym-gate` whose exit code gates the job. Add `scripts/suggest_quality/**` is already in the path filter from Plan 1 — confirm it covers the new files.
- [ ] **Step 4: Commit** `feat(gym): bless/gate + blessed baseline + CI gate`.

---

## Done criteria

- `python -m scripts.suggest_quality.cli gym` prints the catalog + sweep boards with real numbers; the headline reflects only built-rule perturbations.
- The smoke test proves at least one built-rule perturbation recovers (>0%, `expected_rule_fired`).
- Unbuilt-rule perturbations record their ~0% recovery as standing targets without erroring.
- `gym-bless` + `gym-gate` wired with a committed `gym_scorecard.json`; gate fails on recovery regression / negative sweep lift; CI runs it.
- `oracle.py` is UNCHANGED (the oracle scorecard is byte-stable — no refactor was done).
- No change to suggester runtime behavior — the gym only reads and grades.
