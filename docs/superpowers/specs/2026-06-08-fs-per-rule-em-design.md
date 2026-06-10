# Fellegi-Sunter per-rule EM (within-block-aware m-estimation) — design

**Date:** 2026-06-08
**Status:** Approved design, pre-plan
**Scope:** GoldenMatch probabilistic (Fellegi-Sunter) `train_em` m-estimation only.
**Parent effort:** Probabilistic → Splink parity. Builds on the already-shipped
sigmoid normalization, TF adjustments, and multi-pass union blocking
(branch `feat/probabilistic-splink-parity`). This is the deepest scoring lever,
re-brainstormed after sigmoid+un-exclusion+TF recovered recall but not precision.

## Problem

On `historical_50k` (Splink's home-turf bio dataset), the probabilistic path —
even with sigmoid match-probability + term-frequency adjustments + multi-pass union
blocking — has **precision ~0.1** (PR-curve best F1 ~0.19). Measured root cause:
GoldenMatch runs a **single global EM** over the union of all within-block pairs,
holding the *union* of blocking fields constant. Splink's own docs name this exact
failure: *"when we block on first name and surname, we can't get parameter estimates
for these two columns because we've forced all comparisons to be equal."*

The consequence is not just the (expected, Splink-shared) large weight on name
agreement — it is **corrupted `m`-estimates for the OTHER fields**. Because the
inflated name-agreement weight dominates the EM E-step posteriors, within-block
non-matches look like matches, so EM over-estimates `m[disagree]` for the
discriminating fields. Result: disagreement penalties are far too weak
(`postcode_fake` disagree weight = **−0.06**, where a sound estimate is ≈ **−3**).
With weak penalties, a same-name/different-person pair scores almost as high as a
true match → precision collapse, and **no threshold separates them** (PR curve flat).

## Approach (grounded in Splink's documented methodology)

Splink estimates parameters with:
1. `u` from **random sampling** (two random records are ~never a match) — GoldenMatch
   already does this; keep it.
2. `m` via **EM run once per blocking rule**. Each run blocks on rule `R`; the columns
   in `R` are held constant (no `m` estimate for them in that run), and the OTHER
   columns vary, so their `m` is estimated cleanly. Run across multiple rules so every
   comparison is estimated in at least one run where it is free.
3. Combine: a comparison's `m` is the **average** of its estimates across the runs that
   estimated it (Splink: *"Under the hood, Splink will take an average."*).
4. Term-frequency adjustment (per-value `u`) and the sigmoid match-probability layer on
   top — both already shipped.

This fixes the corrupted-posterior root: every field's `m` is learned from pairs where
that field actually varies, restoring strong disagreement penalties.

## Design

### 1. Per-rule EM in `train_em` (`core/probabilistic.py`)
New signature accepts the **list of blocking passes**, each carrying its field-set and
its within-block pairs (or its blocks), instead of a single flat `blocks` list +
single `blocking_fields` set.

```
estimate u[field][level]  from random pairs            # unchanged (_sample_pairs)
m_runs: dict[field -> list[per-run m vectors]]
for pass P with field-set F_P and within-P-block pairs S_P:
    estimated_fields = [f for f in mk.fields if f.field not in F_P]
    run the existing EM loop over S_P, updating m ONLY for estimated_fields
    for f in estimated_fields: m_runs[f.field].append(this run's m[f])
for each field f:
    if m_runs[f]:  m[f] = elementwise mean over m_runs[f]      # Splink average
    else:          m[f] = fallback prior (field constant in every pass)   # logged
match_weight[field][level] = log2(m/u)     # then TF (per-value u), then sigmoid downstream
```

- The per-pass EM is the existing vectorized EM loop, parameterized by
  `estimated_fields` (fields in `F_P` are skipped in the M-step — they're constant in
  `S_P`, exactly as the single-run code already skips `blocking_fields`).
- **u is shared** across runs (random-sampling, computed once). **Drop the current
  neutral-`u` override for blocking fields** (`train_em` today forces
  `u=[0.50,0.50]`/`[0.34,...]` for `blocking_fields`). That override existed only to
  support the old wholesale fixed-weight treatment; under per-rule EM, random-sampling
  `u` is the correct, unbiased non-match agreement rate for *every* field (including ones
  that are a block key in some pass — random pairs reflect their true collision rate).
  So estimated fields use `log2(m̄/u_random)` with `u` from random sampling; only the
  always-blocked fallback fields keep fixed weights (where `u` is moot). This decision
  affects every weight and must be pinned by a test (a sometimes-blocked field's `u`
  equals its random-pair estimate, not `0.50`).
- **Combine = simple elementwise mean** of the per-run `m` vectors for each field
  (Splink's documented average). A sample-size-weighted mean is a possible refinement;
  start with the simple mean (matches Splink), note the option.
- **Min-sample guard (precise semantics):** a pass whose sampled within-block pairs fall
  below the existing `len(pairs) < 10` floor is **skipped for estimation only** — it
  contributes **no** `m_runs` entries and the loop continues to the next pass (logged).
  This is distinct from today's `< 10` floor, which returns a full `_fallback_result`.
  Only when **every** pass is below the floor (or the random-pair sample for `u` is
  `< 10`) do we return `_fallback_result` (the existing whole-model fallback).
- **Fallback for a field, not the model:** a field is held constant in `F_P` for *every*
  pass → `m_runs[field]` is empty → it keeps the current fixed neutral prior
  (`[-3,0,3]`-style), logged. **The `m_runs[field]` non-empty check is the mechanical
  successor to the old `_em_excluded_fields` intersection** (which this design retires):
  a field free in *any* pass MUST be estimated (from that pass), NOT given the fixed
  prior — reintroducing the fixed prior for a sometimes-free field is exactly the
  over-exclusion that previously halved recall, so the per-field check must gate on "was
  it estimated in ≥1 run", never on "was it a blocking field in some pass." Under
  multi-pass union, almost every field is free in some pass; under a single static key,
  the one key is constant in the (only) pass → fixed prior (same as today).

### 2. Pipeline wiring (`core/pipeline.py`, both branches)
Both `train_em` call sites (dedupe ~1417, match ~2388) must supply per-pass structure.
Build per-pass blocks by running the existing static-block builder once per pass (reuse
`_build_multi_pass_blocks`'s per-pass loop, but retain pass identity → `(F_P, blocks_P)`
tuples) and pass that list to `train_em`. Retire the `_em_excluded_fields` single-set
call — per-rule exclusion supersedes it. For a single static-key config there is one
pass → one EM run holding that key fixed → behavior equivalent to today.

### 3. Default + kill-switch
Per-rule EM is the new default. `GOLDENMATCH_FS_PER_RULE_EM=0` restores the **current
branch default** — single-run `train_em` with the `_em_excluded_fields` *intersection*
exclusion (NOT a hypothetical single-static config) — for A/B and rollback. Repo
convention for behavior changes (mirrors `GOLDENMATCH_FS_SIGMOID`). The equivalence test
must pin the kill-switch path against that exact current intersection behavior.

### 4. `EMResult` unchanged
The return shape (`m_probs`, `u_probs`, `match_weights`, `tf_tables`, `converged`,
`iterations`, `proportion_matched`) is unchanged, so `score_probabilistic`,
`score_probabilistic_fast`, `score_pair_probabilistic`, the TF tables, and the sigmoid
layer are all untouched. This change is isolated to *how `m` is estimated*.

## Measurement gate

Measured locally via the **surviving-dump PR-curve method** (score a candidate sample
with the trained EM; no clustering → no OOM), on `historical_50k` + febrl3 + synthetic:

- **Primary (mechanism):** disagreement penalties strengthen — on a fixture where
  matches agree on a discriminating field (e.g. postcode), that field's disagree weight
  is meaningfully negative (≤ −1.5), vs the current −0.06.
- **Headline:** `historical_50k` reaches a PR operating point with **F1 materially >
  0.655 baseline** — target precision ≥ ~0.8 at recall ≥ ~0.7 (the Splink-class regime
  the diagnostic showed is reachable if disagreements bite).
- **Non-regression:** febrl3 (≈0.99) and synthetic do not drop.

## Kill criterion

If per-rule EM strengthens the disagreement penalties (primary metric improves) but
`historical_50k` precision still collapses on the PR curve, the remaining wall is
**blocking-candidate quality** (the 35:1 candidate ratio from broad orthogonal passes).
Stop and re-sequence to **selective compound blocking** before any further EM work.

## Testing

- **Per-rule estimation:** a field held constant in pass A but free in pass B gets its
  `m` from B (assert `m_runs` provenance / the resulting weight reflects B's data).
- **Averaging:** a field free in two passes gets the elementwise mean of the two `m`
  vectors.
- **Disagreement-penalty behavioral test:** on a synthetic where matches reliably agree
  on a discriminating field and non-matches don't, per-rule EM yields a clearly negative
  disagree weight for that field (and the old single-run EM yields a near-zero one) —
  the direct precision-mechanism guard.
- **Fallback:** a field blocking-constant in every pass → fixed neutral prior, logged.
- **Single-static-key equivalence:** one pass → one EM run → result equivalent to the
  pre-change single-run EM on that key (within tolerance) — backward-compat guard.
- **Kill-switch:** `GOLDENMATCH_FS_PER_RULE_EM=0` restores the single-run path
  byte-for-byte.
- **`EMResult` shape unchanged:** downstream scoring/TF/fast-path tests stay green.
- Both `train_em` call sites updated; pipeline runs end-to-end on a small fixture.

## Scope / out of scope

- **In:** `train_em` per-rule restructure + averaging + fallback + min-sample guard; the
  two pipeline call sites; the kill-switch.
- **Out (separate, sequenced after the gate):** selective compound blocking (lever #3);
  threshold auto-calibration beyond the sigmoid default; sample-weighted m-combination;
  TS parity. CI/Splink head-to-head panel + the branch rebase/merge remain a follow-up.
- **Explicitly NOT covered: `train_em_continuous`** (the Winkler/Gaussian variant). It
  has the same single-global-EM structure and would benefit from the same per-rule
  treatment, but it is opt-in, rarely used, and out of scope here — do not scope-creep
  into it. The kill-switch + tests target the discrete `train_em` only.

## Risks & mitigations

- **Compute:** N passes × EM iterations. Mitigate by sampling per pass (existing
  `n_sample_pairs` cap) and the min-sample guard; passes are independent (could
  parallelize later, not now).
- **Thin per-pass match signal → noisy `m`:** averaging across passes + the min-sample
  guard; a pass below the floor contributes nothing rather than noise.
- **Candidate-quality wall persists:** the kill criterion catches it and redirects to
  selective blocking.

## Affected files (anticipated)

- Modified: `core/probabilistic.py` (`train_em` per-rule restructure + combine +
  fallback + kill-switch helper), `core/pipeline.py` (both branches: per-pass block
  construction + new `train_em` call; retire `_em_excluded_fields`).
- New tests: `tests/test_probabilistic_per_rule_em.py`.
- Reused: the local PR-curve diagnostic method (`.profile_tmp/diag_pr_tf.py` shape) for
  the measurement gate.
