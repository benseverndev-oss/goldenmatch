# `threshold_far_too_high` Gym Perturbation Design

**Date:** 2026-06-25
**Branch:** `feat/suggest-gym` (worktree `.worktrees/suggest-gym`)
**Status:** Design approved; spec under review.

## Problem

The config-suggestion kernel's `lower_threshold` (dip) rule was fixed on 2026-06-25 to be right-anchored (`suggest-core/src/diagnostics.rs::dip()` now targets the valley below the high-score match mode instead of the global left-tail sliver -- see `2026-06-25-pre-threshold-scores-design.md` findings). That fix is proven *correct* by Rust unit tests and a Python behavioral test, but the gym never demonstrated it has *accuracy value* on a realistic dataset: the existing `threshold_too_high` perturbation raises the primary matchkey threshold by only +0.10. On `ncvr_synthetic` (auto-config ceiling 0.80) that lands at 0.90 -- which is within `DIP_MIN_GAP` (the `suggest-core/src/rules.rs:6` constant `= 0.05`; gate at `rules.rs:27`, `(dip - current).abs() > DIP_MIN_GAP`) of the ~0.875 score valley -- so the dip rule correctly emits *nothing* and the gym records 0% recovery. The rule's recovery behavior is therefore untested end-to-end through the gym's apply-and-remeasure path.

## Goal

Add a gym perturbation that drives the primary matchkey threshold *well* above the valley so the `lower_threshold` rule fires, applies, and recovers F1 -- and demonstrably survives the live (health-proxy `verify=True`) gate, the failure mode that has defeated this arc repeatedly. This makes the dip fix's accuracy value a standing, measured regression asset.

## Measured viability (probe, 2026-06-25, existing `ncvr_synthetic`, FULL_DIST=1, in-tree native kernel)

Setting the primary weighted matchkey (`fuzzy_match`) threshold to an absolute high value, scored exactly as `gym.evaluate_perturbation` would:

| set threshold | fires raw/live | f1_ceiling | f1_degraded | f1_recovered (raw=live) | recovery_pct (raw=live) | verification_gap |
|---|---|---|---|---|---|---|
| 0.98 | yes / yes | 0.9828 | 0.7284 | 0.9203 | **0.754** | 0.0 |
| 0.95 | yes / yes | 0.9828 | 0.7879 | 0.9203 | **0.679** | 0.0 |

Both fire, recover, and survive the live gate (live recovery == raw, `verification_gap = 0.0` -- the health proxy suppresses nothing). The applied suggestion lowers the threshold one step to **0.88** (just above the ~0.875 valley, high/right side -- it does NOT overshoot into the left tail). Precision holds (`f1_recovered_live = 0.9203`, no collapse). 0.98 is chosen: deeper damage -> higher recovery_pct headline, no under-shoot risk.

**Honest caveat:** recovery is *partial* (~0.75, lands at 0.88 not the original 0.80 ceiling) because the kernel targets the valley (0.88), which is arguably a *better* (more precise) threshold than the 0.80 ceiling auto-config originally picked. `recovery_pct` is measured against the 0.80-ceiling F1, so it cannot reach 1.0. It clears the "live recovery > 0" bar comfortably; it is not a full recovery, and that is recorded as-is.

## Approach

Add ONE entry to the perturbation catalog in `scripts/suggest_quality/perturbations.py`. Additive; no existing perturbation, dataset, kernel, rule, or the `FULL_DIST` flag changes.

### The perturbation
```
name="threshold_far_too_high"
expected_rule="lower_threshold"
builds_on_existing_rule=True
description= simulates an EGREGIOUSLY over-strict threshold that lands beyond
             DIP_MIN_GAP of the score valley, so the dip lower_threshold rule
             actually fires and recovers matches (unlike the gentle
             threshold_too_high, whose +0.10 stays within the valley gap).
applies_to = _applies_threshold_too_high   # reuse: primary weighted matchkey exists
apply: deep-copy config; primary weighted mk.threshold = min(0.99, current + 0.18)
```

- `+0.18` (relative, matching the existing `+0.10`/`-0.15` style) lands at 0.98 for ncvr's 0.80 ceiling -- the measured-best value. Capped at 0.99 (same ceiling the existing `threshold_too_high` uses).
- Reuses `_apply`/`_applies` helpers' structure (deep-copy, never mutate input, guard returns config unchanged if no primary weighted mk).

### Why a new entry, not editing `threshold_too_high`
The gentle `threshold_too_high` (+0.10) is still a valid signal: it pins that the kernel correctly stays *quiet* when the threshold is only slightly off (within the valley gap) -- emitting a destructive lower there would be wrong. Editing it would destroy that signal and churn unrelated findings. The far variant is purely additive.

### Catalog-wide behavior
The perturbation runs against every dataset in the gym's `datasets x perturbations` loop (`gym.run_catalog`). `ncvr_synthetic` is the dataset that demonstrates the win; on datasets whose ceiling is already near 0.99, or with no recoverable matches in the lowered band, the gym's `DAMAGE_EPS`/`no_damage` and `n/a` paths handle it gracefully and report honestly. No per-dataset special-casing.

### Committed gym baseline + CI gate (MUST regenerate)
A new catalog entry is NOT a pure no-op for the machine-checked baseline. The gym has a committed scorecard `scripts/suggest_quality/baselines/gym_scorecard.json` and a `gym-gate` CI job (`.github/workflows/bench-suggest-quality.yml`, triggered on `scripts/suggest_quality/**` changes) that fails the build on a headline regression. Adding `threshold_far_too_high`:
- adds new per-pair rows (`*/threshold_far_too_high`) -- these are `NEW`/informational in the gate (no per-pair failure), and
- shifts the `headline_live`/`headline_raw` means and the `lower_threshold` rule rollup. Direction: the new built-rule record contributes `recovery_pct_live ~= 0.754` against a baseline `headline_live = 0.0`, so the headline moves UP -- the gate's drop check (`delta < -RECOVERY_GATE_TOL`) passes, NOT a regression.

Even though the gate passes on direction, the committed baseline MUST be regenerated so the scorecard records the new pair and its aggregates stay in sync with the live catalog (a stale baseline silently disagrees with the catalog). The `mode` is a positional arg (`cli.py:214-216`, choices include `gym-bless`), and the baseline path is `scripts/suggest_quality/baselines/gym_scorecard.json` (`_GYM_BASELINE`, `cli.py:32`; written by `_cmd_gym_bless`, `cli.py:743`). Regenerate under FULL_DIST=1: `GOLDENMATCH_SUGGEST_FULL_DIST=1 ... -m scripts.suggest_quality.cli gym-bless` (or the `bench-suggest-quality.yml` `mode=gym-bless` dispatch), and commit the updated `gym_scorecard.json`. Confirm the regenerated `headline_live`/`headline_raw` moved UP (no regression) before committing. Blessing also locks the new pair's `recovery_pct_live (~0.75)` in as a future regression floor (`gym-gate` fails if a blessed built-rule pair later drops > `RECOVERY_GATE_TOL`) -- exactly the standing regression asset intended.

**Correction (planning, 2026-06-25):** `bench-suggest-quality.yml` did NOT
previously set `GOLDENMATCH_SUGGEST_FULL_DIST=1`, and `gym-gate` re-runs the
catalog live -- so blessing the far pair's ~0.75 live recovery without enabling
FULL_DIST on the gym steps would make the CI gate re-compute ~0 and go red.
The plan therefore enables FULL_DIST for the gym steps of that workflow (gym
bless + gym-gate only; the scorecard gate is untouched) and has CI commit the
re-blessed baseline back to the branch. This is scoped to the gym CI job, NOT
a global FULL_DIST default-on flip (still out of scope).

## Components / files
- **Modify:** `scripts/suggest_quality/perturbations.py` -- add `_apply_threshold_far_too_high` (and reuse `_applies_threshold_too_high`), append the `Perturbation` to `CATALOG`.
- **Test (new file):** `scripts/suggest_quality/tests/test_perturbations.py` (no perturbations test file exists today -- the dir holds only `test_datasets.py`). Assert (a) `threshold_far_too_high` is in `CATALOG` with `expected_rule="lower_threshold"`, and (b) `apply` on a config with a 0.80-threshold primary weighted mk sets it to ~0.98 (>= 0.95, beyond the valley) without mutating the input.
- **Regenerate baseline:** `scripts/suggest_quality/baselines/gym_scorecard.json` via the gym bless path (see "Committed gym baseline + CI gate" above) -- commit the updated scorecard so the `gym-gate` CI job stays in sync.
- **Record:** append a `## Findings (threshold_far_too_high, <date>)` section to `2026-06-25-pre-threshold-scores-design.md` (the dip arc's findings home) with the gym run's row for `ncvr_synthetic` (fires, recovery_pct_live, verification_gap, precision held), comparing to the gentle `threshold_too_high` (0% / no fire).

## Testing
- Unit: the catalog-membership + apply-behavior test above (fast, no native, no pipeline).
- End-to-end (run/record, not pytest): one gym run `GOLDENMATCH_SUGGEST_FULL_DIST=1 ... gym --datasets ncvr_synthetic` (and the full `synthetic,ncvr_synthetic` set) confirming `threshold_far_too_high` fires with `recovery_pct_live > 0` and `verification_gap ~ 0` on `ncvr_synthetic`; record in findings.

## Done criteria
- `threshold_far_too_high` is in the gym catalog, additive (no existing perturbation/dataset/kernel/flag changed).
- Unit test green (`scripts/suggest_quality/tests/test_perturbations.py`): catalog membership + apply sets the primary weighted threshold beyond the valley, input unmutated.
- Gym run on `ncvr_synthetic` records `lower_threshold` firing with `recovery_pct_live ~ 0.75` (> 0) and `verification_gap ~ 0` (survives the live gate), precision held -- recorded honestly in the spec findings, including the partial-recovery caveat.
- `gym_scorecard.json` baseline regenerated (blessed) and committed; the `gym-gate` headline moved UP (no regression), confirmed before commit.

## Out of scope
- Editing the existing `threshold_too_high` perturbation.
- Any kernel / `dip()` / rule / `DIP_MIN_GAP` change.
- Flipping `FULL_DIST` default-on.
- A bespoke synthetic bimodal dataset (the measurement showed existing `ncvr_synthetic` meets the bar, so YAGNI).
