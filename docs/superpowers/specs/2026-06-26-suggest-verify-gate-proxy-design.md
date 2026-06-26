# Config-Suggestion Verify-Gate Proxy: Closing the Raw-vs-Live Gap

**Date:** 2026-06-26
**Branch:** `feat/suggest-verify-gate-proxy` (worktree `.worktrees/verify-gate`), stacked on `feat/suggest-gym` (PR #1271)
**Status:** Design approved (brainstorm); spec under review.

## Problem

Config suggestion does not deliver accuracy wins in production by default. The diagnosis (from a full map of the current system, 2026-06-26) has three layers, in priority order:

1. **Zero-config is already near-ceiling (primary, not addressed here).** After #662 (noise-aware scorer auto-upgrade), zero-config reaches ~0.94-0.98 F1 on every labeled dataset, so the suggester correctly stays quiet most of the time. Little headroom exists.
2. **The self-verify gate discards the wins that DO exist (this project).** The suggester gym shows `headline_raw = 0.555` but `headline_live = 0.151`. That gap is real F1 recovery the kernel *found* and the unsupervised health proxy then *rejected*. The clearest case: `ncvr_synthetic/threshold_too_low` -- the `raise_threshold` rule fires with `recovery_pct_raw = 0.932`, but the live (gated) path delivers `0.0` (`verification_gap = 0.932`).
3. **FULL_DIST off + two unbuilt rules (tertiary).** Real but blocked behind the above; FULL_DIST is now enabled for the gym CI steps (PR #1271).

The root cause of layer 2: the **default** health proxy (`_health_legacy` = `matched_rate * avg_conf - hhi_penalty`, `health.py:108`) is structurally **recall-biased** -- it rewards "more matches," so a precision-improving fix (raise threshold -> fewer-but-stronger matches) lowers `matched_rate` and looks *worse*, so the gate rejects it. A precision-sensitive alternative (`cohesion`, `health.py:271`) already exists and is env-selectable (`GOLDENMATCH_SUGGEST_HEALTH=cohesion`) with three sub-variants, but it is not the default and its quality has never been measured against ground-truth F1.

## Goal

Make the self-verify gate stop discarding correct fixes, lifting gym `headline_live` from **0.151** toward the **0.555** raw ceiling, under a **hard zero-net-negative constraint**: the gate must never keep a suggestion that lowers F1.

**Honest ceiling (stated so we do not oversell):** this lever is bounded by `headline_raw` (~0.55 on the current suite). It cannot exceed what the kernel already finds. 0.15 -> ~0.55 is the realistic prize; getting beyond that is rule-coverage and real-world-headroom work, which are explicitly separate projects.

**Success bar (decided in brainstorm):** maximize live recovery subject to precision = exactly 1.0 (no accepted net-negative) across the full suite **plus added adversarial cases**.

## Architecture

The gate architecture is unchanged: `review_config(verify=True)` applies a suggested fix -> re-runs the pipeline -> compares an **unsupervised** health proxy -> keeps the suggestion only if the proxy did not worsen (`cand_health >= baseline_health - eps`, `adapter.py:651`). Production stays label-free. The F1 oracle (which has ground-truth labels) is used **only offline**, to choose and validate the proxy.

The work is **Approach A** (proxy bake-off -> pick the proxy with the highest recall at precision 1.0), with **Approach B** (design a new proxy) as a *contingent* phase, done only if the bake-off shows no existing proxy clears a useful recall bar at precision 1.0. The bake-off result makes that call from data; we do not pre-commit to inventing a new proxy.

The only production-code change is a one-line default flip in `suggestion_health_from_clusters` (`health.py:203`), with the existing env var preserved as the rollback switch.

## Components

All new code lives under `scripts/suggest_quality/` (the gym/oracle harness), except the eventual one-line default flip in `packages/python/goldenmatch/goldenmatch/core/suggest/health.py`.

### 1. Proxy bake-off harness (core deliverable)
For each `(dataset, perturbation)`, run the gym's convergence **once in raw mode** (`verify=False`) to get the sequence of fixes the kernel proposes and, for each applied fix, the candidate clusters + candidate F1 (computed from labels, as the oracle already does). For every applied fix, compute **all** proxy candidates on the same candidate-vs-baseline clusters -- one re-run feeds every proxy, so the bake-off is cheap (no per-proxy pipeline re-runs).

Emit one row per `(dataset, perturbation, applied_fix, proxy)`:
- `proxy_delta = P(candidate_clusters) - P(baseline_clusters)`; gate decision `accept = proxy_delta >= -eps`
- `f1_delta = F1(candidate) - F1(baseline)`; truth `is_real_win = f1_delta > 0`

Candidate proxies (all already built in `health.py`): `legacy`; `cohesion` x its three sub-variants (`min_edge`, `mean_bottomk_edge`, `edge_below_cutoff_fraction`); plus a small number of coverage-cap settings. Each candidate is identified by the `(GOLDENMATCH_SUGGEST_HEALTH, GOLDENMATCH_SUGGEST_COHESION, coverage-cap)` tuple it corresponds to.

### 2. Classifier scoring & selection
Treat each proxy as an accept/reject classifier vs F1 truth, over the full suite + adversarial cases:
- `precision(P) = accepted-and-real / accepted` -- **must be exactly 1.0** (never keeps a fix that lowers/ties-down F1)
- `recall(P) = accepted-and-real / all-real-wins` -- **maximize**

Selection rule: among candidates with precision == 1.0, pick the highest recall. Record the full per-proxy precision/recall table and the chosen tuple with its numbers. (If `accepted == 0`, precision is treated as 1.0 vacuously but recall is 0 -- such a proxy never wins unless all proxies are degenerate.)

### 3. Adversarial perturbations (the stronger precision bar)
Add deliberate precision traps to the catalog that a good proxy must **reject**:
- a **near-valley** threshold nudge -- a fix that would over-lower the threshold into the sub-threshold tail (precision loss), and
- an **over-merge trap** -- a fix that inflates `matched_rate` by fusing distinct entities (the failure mode the recall-biased legacy proxy is blind to).

These are additive catalog entries (same shape as the existing perturbations); they harden the precision-1.0 test so the winner is not merely fit to the easy cases. They are tagged so the bake-off and the final validation both include them.

### 4. Default flip + rollback
Change the default `mode` in `suggestion_health_from_clusters` (`health.py:203`) to the winning proxy; if the winner is a cohesion sub-variant, also set the corresponding default for `GOLDENMATCH_SUGGEST_COHESION`. Keep `GOLDENMATCH_SUGGEST_HEALTH` (and the sub-variant env) as the rollback switch. This is the only production-code change.

### 5. Re-bless + CI
Re-bless the gym baseline under the new default and confirm `headline_live` rose with zero net-negatives; the existing `gym-gate` then protects the new, higher live floor. (Re-bless runs in CI under FULL_DIST via the `bench-suggest-quality.yml mode=gym-bless` path shipped in PR #1271, which commits the baseline back.)

### Contingent Phase B
If step 2 shows the best existing proxy still has poor recall at precision 1.0, add a new proxy candidate -- a **valley-margin** signal: the separation between the kept-match score distribution and the sub-threshold tail (reusing the dip-arc's valley computation) -- and re-run the bake-off. Same harness, one more candidate. No architecture change.

## Data flow

```
for (dataset, perturbation) in full_suite + adversarial:
    baseline = degraded config (perturbation applied)
    run gym convergence RAW (verify=False):
        for each applied fix:
            candidate_clusters, candidate_F1   <- one pipeline re-run
            for proxy P in candidates:
                row = (dataset, perturbation, fix, P,
                       P(candidate)-P(baseline), F1(candidate)-F1(baseline))
aggregate rows -> per-proxy (precision, recall)
select P* = argmax recall  s.t. precision == 1.0
validate: real LIVE gym run under P*  -> headline_live up, zero net-negatives
flip default to P*; re-bless; gym-gate protects the new floor
```

## Validation & overfitting guard

The per-fix classifier (measured along the raw convergence path) is the **selection** signal -- cheap, all proxies from one run. But the raw path is not identical to the live path (accepting fix 1 changes the baseline for fix 2), so it is an approximation. Selection and the headline claim are therefore separated into a two-gate guarantee:

- **Select** on the per-fix classifier across the full suite + adversarial cases (precision must be 1.0 everywhere, including the traps).
- **Validate** the winner with a real, end-to-end **live gym run** under the chosen proxy (the normal gym with the new default). The headline number and the zero-net-negative guarantee come from *this* real run. If it surfaces a net-negative the classifier missed, the winner is rejected and we fall back to the next candidate (or Phase B).

The guarantee is explicitly "no net-negative on the full + adversarial bench," with that bench as our best available production proxy -- not a proof of zero net-negatives on arbitrary unseen data.

## Testing

- **Unit (fast, no native):** the bake-off classifier math (precision/recall computation, the `accept` rule, the `accepted == 0` and `all-real-wins == 0` edge cases), and the new adversarial perturbations (catalog membership + apply behavior + input-immutability), mirroring the existing `scripts/suggest_quality/tests/test_perturbations.py`.
- **End-to-end (run/record, CI dispatch under FULL_DIST):** the bake-off emits a per-proxy precision/recall table; the winner's real live gym run shows `headline_live` up from 0.151 and zero net-negatives on the full + adversarial suite.

## Done criteria

- Bake-off harness exists and emits a per-proxy precision/recall table over the full + adversarial suite.
- A proxy is selected with precision = 1.0 and the highest recall among candidates; the choice + numbers are recorded.
- Adversarial near-valley and over-merge perturbations are in the catalog (additive) and included in selection + validation.
- Default flipped (one line in `health.py`), rollback env preserved.
- Re-blessed gym shows `headline_live` materially up from 0.151 (toward 0.555) with **zero net-negatives** on the full + adversarial suite, confirmed by the real live run.
- If no existing proxy clears a useful recall bar at precision 1.0, Phase B (valley-margin proxy) is added and re-evaluated before any flip.

## Out of scope

- Changing the gate architecture (still apply -> re-run -> compare proxy).
- Building new suggestion *rules* (blocking-pass, field-weight) -- rule coverage, a different lever.
- Auto-applying suggestions in the default pipeline (posture change) -- separate project.
- Flipping `FULL_DIST` default-on globally (still env-gated; the gym CI steps enable it as shipped in PR #1271).
- Real-world-headroom hunting on messier datasets -- separate project.
- Improving layer 1 (zero-config near-ceiling) -- not a gate problem.
