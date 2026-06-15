# #876: residual precision drift at scale from a bounded-cardinality sole blocking key — design

**Status:** VERIFIED — Outcome A (already fixed by #715/#723). No fix code; landed a regression guard + closing the issue.
**Issue:** #876 (filed 2026-06-12, from the #510 quality-invariant scale audit)
**Approach:** VERIFICATION-FIRST. Reproduce on current `main` before writing a fix — most of the machinery #876's fix-direction called for already landed in #715/#723 *after* #876 was filed, so the first job is to find whether a real residual gap survives, not to build greenfield.

## RESULT (verification complete)
The residual-gap hypothesis below was **REFUTED by source + a passing unit test** — #715/#723 already closed #876:
- The pairs-per-row budget in `_is_scale_safe` (`autoconfig.py:2062-2063`) is gated on `pb > sb` (*projected block grew vs the sample block*) — which is exactly the all-bounded path, NOT only concentrated/unbounded keys as the hypothesis assumed. A dense sole-`zip` at 100M projects `ceil(100M / 100_000) = 1000` rows/block → `_project_pairs_per_row(1000) = 499 > 50` budget → **refused** → falls to `_scale_safe_bounded_compound` (`[zip, birth_year]`).
- The harness `scripts/quality_invariant_scale.py` (repo-root) already ships the post-#876 frozen config: blocking `["zip","birth_year"]`, `skip_oversized: true`, built by passing `n_rows_full=200M` into auto-config — NOT the issue's sole-`zip` / `skip_oversized:false`. So the exact config #876 reported on no longer exists.
- Regression guard landed in `tests/test_autoconfig_blocking_cost_715.py`: `test_sole_zip_exceeds_pairs_budget_at_scale_876` (the mechanism, on the real constants) + `test_dense_zip_no_sole_bounded_key_at_scale_876` (end-to-end build_blocking at 100M emits no sole bounded key). Both green.
- Confirmation of no *residual non-blocking* precision slope: a `bench-quality-invariant-scale.yml` 10M frozen run (the purpose-built lane, `large-new-64GB`).

The phased plan below is the original verification-first design; Phase 0's outcome was "does not reproduce" → Outcome A.

## Problem (from the issue + its root-cause comment)
Under a FIXED auto-derived config on the #510 realistic-shape scale fixture, recall is flat scale-invariant (0.988 at every rung) but **precision drifts down beyond ~1M**: 0.890 (1M) → 0.855 (10M), so F1 misses the Δ≤0.005 invariance target at 10M (Δ=−0.019).

Root cause (pinned in the issue comment): the frozen config blocks on a single key, `zip`, with `max_block_size: 5000, skip_oversized: false`. In the fixture `zip = cid % 100000`, which WRAPS at 100K clusters, so the zip block size grows ~linearly with N (10 rows/block at 1M → 1,000 at 100M). Those bloated zip blocks are mostly CROSS-cluster pairs, a growing fraction of which the fuzzy scorer matches → precision decay. Real-world-relevant: real US zips are also bounded (~40K), so a sole-`zip` block explodes on any large real dataset.

The hard part stated in the issue: **the cardinality cap is invisible from a small sample** — a 1K sample shows `zip` as clean/high-cardinality.

## What already exists (the reason this is verification-first)
The Explore pass over `core/autoconfig.py` + `core/blocking_candidates.py` found the #876 fix-direction is largely implemented (in #715/#723, which postdate the issue):

- **Bounded-cardinality type registry** — `_BLOCKING_DOMAIN_CAP` (`autoconfig.py:1741`): `zip=100_000, year=300, month=12, boolean=2`. Plus name-based detection (`_ZIP_PATTERNS` / `_classify_by_name`, ~`autoconfig.py:112-199`). This solves "invisible from the sample" via the **semantic type**, not sample stats.
- **Type-aware block projection** — `_typed_projected_block` (`autoconfig.py:1749`): all-bounded `[zip]` projects `ceil(N / 100_000)`; AND-compounds bounded keys via `_scale_safe_bounded_compound` (`autoconfig.py:1801`).
- **Scale-safe refuse gate** — `_is_scale_safe` (`autoconfig.py:2045`) drops keys whose projected block exceeds `max_safe_block = max(1000, min(10_000, N//200))` (`autoconfig.py:2008`); plus a pairs-per-row budget `_project_pairs_per_row <= GOLDENMATCH_BLOCKING_PAIRS_PER_ROW` (default 50, `autoconfig.py:1704-1718`).
- **Controller RED-refuse** at `REFUSE_AT_N = 100_000` (`autoconfig_controller.py:49`).

## Residual-gap hypothesis (to be confirmed in Phase 0/1, NOT assumed)
A **dense sole-`zip`** projects block ≈ `ceil(N / 100_000)` = ~1,000 even at 100M — which stays UNDER `max_safe_block` (caps at 10K). So it passes the block-size gate and is admitted as the sole key. The ~12B candidate pairs + the precision drift would only be refused if the **pairs-per-row budget were applied to the all-bounded path** (block 1,000 → ~500 pairs/row ≫ 50). The map indicates that budget check is gated to *concentrated/unbounded* keys, so dense-bounded sole-`zip` may slip through. If true, the fix is small and targeted; if false, #876 is already closed and this is verify-and-document.

## Goal
Either (a) confirm #715/#723 already fixed #876 and close it with the verified curve, or (b) localize and close the exact residual gap so that, under a frozen auto-derived config, **F1 holds Δ≤0.005 across the rungs** (the #510 invariance target) without a sole bounded-cardinality block key exploding.

## Plan (phased; Phase 0 branches the work)

### Phase 0 — Reproduce on current main (the gate)
Run the #510 quality-invariant scale harness (`scripts/quality_invariant_scale.py`, `--frozen`, realistic shape) at **10M** (the smallest rung where the drift appears in the issue's table) on **current main** (with the #715/#723 machinery). This is a ≥10M run → a CI / cluster job (`bench-ray-cluster` or the synthetic-bench lane), NOT local.

Capture: (1) the auto-derived blocking config (does it still pick sole-`zip`?), (2) pairwise precision/recall/F1 vs the 1M baseline, (3) the projected vs actual max block size + candidate-pair count.

- **If the drift does NOT reproduce** (sole-`zip` is now refused/compounded by #715/#723, precision holds) → skip to "Outcome A": document the curve on the issue and CLOSE #876. No code.
- **If it DOES reproduce** → Phase 1.

### Phase 1 — Localize the gap (only if reproduced)
Instrument `build_blocking` on the 10M fixture: which key was picked, what `_typed_projected_block` returned, whether `_is_scale_safe` applied the pairs-per-row budget to it. Confirm or refute the hypothesis above and pin the exact file:line of the miss. Write a SMALL-N unit reproduction that exercises the same code path (the gate is a projection function — it can be unit-tested at small N by feeding a synthetic ColumnProfile with `col_type=zip` + a large `full_n`), so the fix is CI-verifiable without a 10M run.

### Phase 2 — Targeted fix (driven by Phase 1)
Most-likely shape (pending Phase 1): extend `_is_scale_safe` so the **pairs-per-row budget applies to the all-bounded path too** — a sole bounded key whose `ceil(N/domain)` block implies > budget pairs/row is NOT scale-safe, forcing `_scale_safe_bounded_compound` / a refining sub-key (`zip + name-prefix`) / multi-pass. Ensure the refusal FALLS BACK to a refining compound, not a degenerate name-only mega-block (the regression `test_dense_zip_still_picks_bounded_compound` guards this). Keep it a pure projection change (no new at-scale dependency).

### Phase 3 — Scale gate + invariance test
- Unit test (from Phase 1) in `tests/test_autoconfig_blocking_cost_715.py`: a synthetic `col_type=zip` sole-key at large `full_n` is refused / compounded, and no emitted pass projects > the pairs-per-row budget.
- Extend the existing `blocking-scale` CI lane (#715/#723) with the #876 fixture shape so the invariance (Δ≤0.005 F1, or at minimum: no sole bounded-cardinality block key + projected pairs/row ≤ budget) is gated at the repro scale.

## Out of scope / honest residual
Part of the drift is a fundamental ER-at-scale property (more entities → more confusable near-collisions), independent of blocking. This spec fixes the BLOCKING-driven component (the bloated cross-cluster blocks). If, after the blocking fix, a residual precision slope remains attributable to genuine fuzzy near-collisions (not blocking), it gets DOCUMENTED as an inherent property in the #510 report, not chased with a scale-adaptive fuzzy threshold (a separate, riskier lever the issue floats as "possibly").

## Risks
- **Phase 0 may show no gap** (the most likely-good outcome) → the deliverable is a verified close, not code. The spec is written to make that a first-class result, not a failure.
- **Reproduction cost** — a 10M frozen run. Mitigation: Phase 1 distills the gap into a small-N unit repro so the FIX is gated in normal CI; only the end-to-end invariance check needs the scale lane.
- **Over-refusal** — tightening the bounded-path gate could refuse a legitimately-fine bounded sole-key on smaller N. Mitigation: the gate is N-scaled (`max_safe_block` + pairs-per-row both reference N), and the regression tests (`test_dense_zip_still_picks_bounded_compound`, `test_sparse_zip_gets_bounded_compound_not_degenerate`) guard the small-N behavior.

## References
- Issue #876 + root-cause comment. Prior art: #715/#723 blocking-refuse (`project_autoconfig_715_blocking_refuse`), #510 quality-invariant audit, ADR `docs/adr/0004-chao1-sample-correction.md`.
- Lesson applied: reproduce at scale BEFORE designing a scale-dependent fix (#715 round 1 shipped on un-reproduced analysis and was reopened).
