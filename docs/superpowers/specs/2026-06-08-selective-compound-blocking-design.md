# Selective compound blocking (probabilistic path) -- design

**Date:** 2026-06-08
**Status:** Approved design, pre-plan
**Scope:** GoldenMatch probabilistic (Fellegi-Sunter) auto-config blocking only
(`core/autoconfig.py::_build_probabilistic_blocking`).
**Parent effort:** Probabilistic -> Splink parity. This is **lever #3**, the
kill-criterion redirect from the per-rule EM work
(`2026-06-08-fs-per-rule-em-design.md`). Builds on the already-shipped sigmoid
normalization, TF adjustments, union-aware EM exclusion, multi-pass union
blocking, and per-rule EM on branch `feat/probabilistic-splink-parity`.

## Problem

Per-rule EM (lever just landed) is a validated scoring lever -- it strengthens
disagreement penalties (proven on febrl3 +0.6pp F1 and synthetic +1.5pp F1, no
regression) and fixes the corrupted-m root cause. But it does NOT rescue
`historical_50k`, and the measurement gate fired the **kill criterion**: the
residual wall is **blocking-candidate quality**, not scoring.

Measured (dump PR-curve method, single-run vs per-rule on the same candidate
sample):
- `historical_50k` candidate pool = **8,844,485 pairs** for **303,961** ground-truth
  pairs. Emitting ALL candidates caps precision at `303961 / 8844485 = 3.4%`.
- per-rule BEST F1 = 0.058 (P=0.031, R=0.461); single-run BEST F1 = 0.077.
  Precision never exceeds ~4% at any threshold; no P>=0.8 @ R>=0.7 operating point
  exists. The scorer is now correct; it simply cannot produce high precision from
  a candidate set that is 96.6% noise.

Root cause of the flood: `_build_probabilistic_blocking` augments
`build_blocking`'s transform-rich name passes with **broad single-key** orthogonal
passes (e.g. `[birth_place]`, `[full_name]`, `[surname]`). Each generates huge
blocks because many people share a name or birthplace, and the within-block
non-matches share the blocking field by construction. Splink instead uses more
**selective compound rules** (conjunctions of 2+ fields) unioned across several
complementary passes.

## Approach (grounded in Splink's blocking methodology)

Redesign `_build_probabilistic_blocking` to select a union of passes under an
**explicit candidate budget**, using a **coverage-per-candidate set-cover**
objective that favours selective compound conjunctions while protecting recall:

1. **Generate a candidate POOL** of passes: the transform-rich name passes
   (recall-bearing) + **compound variants** (name component with its transforms,
   conjoined with an orthogonal selective field, optionally coarsened) +
   self-selective orthogonal single-keys.
2. **Estimate per-pass cost + coverage:** exact candidate count via a groupby
   (`sum C(block_size, 2)`); a (sampled, cost-capped) set of canonical record-pair
   ids for the coverage signal.
3. **Select within budget** `K x N` deduped-union candidate pairs via greedy
   set-cover on marginal `|new_pairs| / candidate_cost`, always emitting at least
   one name-bearing pass (recall anchor).

This cuts the redundant noise that floors precision while the coverage objective
keeps the GT-bearing, complementary passes -- exactly the precision-ceiling lift
the gate needs, with recall protected by the set-cover objective rather than by
exempting passes.

## Design

### 0. Enabling change: per-field transforms in `BlockingKeyConfig`

The current blocker (`blocker.py::_build_block_key_expr`) applies a key's single
`transforms` list UNIFORMLY to every field in the key, then concatenates. That
cannot express the central selective compound `[soundex(surname), birth_year]`
(soundex on a date is meaningless; year-coarsening a surname is wrong). Without
per-field transforms a compound is forced to be either bare/uniform (selective but
recall-brittle to a name typo) or to lean on a broad standalone `[soundex(surname)]`
pass that the candidate budget likely cannot afford -- collapsing recall. The
transform-rich compound (selective AND typo-robust on the name) is what clears the
gate, so it must be expressible.

Add an OPTIONAL, backward-compatible per-field transform mechanism:
- `BlockingKeyConfig.field_transforms: list[list[str]] | None = None` -- when set,
  it is a list aligned 1:1 with `fields`, giving each field its own transform chain
  (`field_transforms[i]` applies to `fields[i]`). Default `None` preserves today's
  shared-`transforms` behavior exactly. A validator requires
  `len(field_transforms) == len(fields)` when set.
- `_build_block_key_expr` uses `field_transforms[i]` for field `i` when
  `field_transforms` is set, else falls back to the shared `transforms` list (the
  current code path, byte-identical when `field_transforms is None`).

The weighted/exact path never sets `field_transforms`, so it is untouched. TS parity
is out of scope.

### 1. Candidate-pass generation -- `_candidate_blocking_passes(profiles, df)`

Returns a POOL of candidate `BlockingKeyConfig`s (not yet selected):

- **Name / recall-floor passes:** reuse `build_blocking`'s transform-rich passes
  verbatim as candidates (e.g. `[surname]` + `['lowercase','substring:0:5']`,
  `[first_name]` + `['lowercase','soundex']`). These carry the typo-robustness
  that is the recall lever.
- **Compound variants:** for each name pass `P_name` and each eligible orthogonal
  field `o` (the same orthogonal-eligibility test the current code uses: not a
  name field, not description/numeric, null-rate <= 0.20, `0.02 <= cardinality < 1.0`,
  plus dob-like dates allowed), emit a compound pass that conjoins `P_name`'s
  field+transforms with `o`. **Coarsening rule (concrete, so the implementer does
  not invent a heuristic):** coarsen date / `dob`-typed orthogonals to year via
  `substring` (the `historical_50k` recall case); leave non-date orthogonals
  (postcode, birth_place, etc.) uncoarsened. The conjunction is a multi-field
  `BlockingKeyConfig(fields=[name_field, o], field_transforms=[<name pass's
  transforms>, <year-coarsen-or-empty for o>])` -- using the per-field
  `field_transforms` from section 0 so the name component keeps its soundex/substring
  recall transforms while the date component gets year-coarsening (and non-date
  orthogonals get an empty transform list).
- **Orthogonal single-keys:** an orthogonal field on its own ONLY when its
  cardinality is high enough to be self-selective (reuse the existing `>= 0.30`
  threshold as the single-key gate).

Standalone name passes keep using the shared `transforms` field (no
`field_transforms`); only compounds populate `field_transforms`.

### 2. Per-pass cost + coverage -- `_estimate_pass_stats(pass, df)`

- **Exact candidate count:** materialize the pass's blocks (via the existing
  static block builder / a groupby on the transformed key columns) and compute
  `candidate_count = sum over blocks of C(size, 2)`. Cheap; this is the quantity
  the budget is enforced on.
- **Coverage signal:** the set of canonical record-pair ids (`min*N + max`) the
  pass generates. To bound autoconfig-time cost, per-pass pair enumeration is
  **capped at a sample** (default ~500K pairs/pass; a pass above the cap
  contributes a uniformly sampled subset). The set-cover ratio is therefore an
  ESTIMATE; the budget itself stays exact (from the groupby).

### 3. Budget set-cover selection -- `_select_passes_within_budget(stats, budget)`

- `budget = K x N` deduped-union candidate pairs. **`K` default 25**, overridable
  via `GOLDENMATCH_BLOCKING_CANDIDATE_BUDGET_K`; calibrated in the plan (sweep).
- Greedy: `covered = empty set`, `selected = []`, `spent = 0`. Each round, among
  passes whose `candidate_count` still fits (`spent + count <= budget`), pick the
  one maximizing `|pass_pairs - covered| / candidate_count`; add it, union its
  pairs into `covered`, add its count to `spent`. Stop when no pass fits or
  marginal coverage saturates (best ratio ~ 0).
- **Recall anchor (safety):** always include at least one name-bearing pass -- the
  highest-coverage name/compound pass that fits the budget -- so the emitted config
  is never degenerate or name-less. If the budget is so tight that no name-bearing
  pass fits, include the single most-covering name-bearing pass anyway (documented
  budget-override; a name-less probabilistic config is never acceptable).
- **Degenerate fallback:** if the pool has no orthogonal fields to form compounds
  from (or `compute_column_priors` fails), return `build_blocking`'s output
  unchanged -- never regress. Note this is a deliberate change from today's
  degenerate path: the current `_build_probabilistic_blocking` returns an AUGMENTED
  superset (base passes + orthogonal extras); the new fallback returns plain
  `build_blocking` (more conservative). The plan must assert this in a
  non-regression test so a reviewer does not read it as an accidental regression.

### 4. Emit

`BlockingConfig(strategy="multi_pass", passes=selected, max_block_size=...,
skip_oversized=...)` -- the exact shape the pipeline's probabilistic branches and
`_build_blocks_per_pass` (per-rule EM) already consume. Nothing downstream changes.

## Budget calibration (plan, not code)

The design ships `K = 25` (candidates per record; current `historical_50k` is
~175/record). The plan sweeps `K` in `{10, 25, 50, 100}` on the dump PR-curve gate
and picks the `K` that maximizes `historical_50k` F1 subject to febrl3/synthetic
non-regression. `K` is a module constant + env override so the sweep needs no code
edits per point.

## Measurement gate (reuses the lever-#1/#2 harness)

- **Blocking-recall ceiling** per config via `.profile_tmp/diag_blocking_recall.py`
  (coverage-only, no OOM): the selected config's `blocking_recall` on
  `historical_50k` (current ceiling 0.83) is the recall side.
- **PR-curve F1** via the per-rule dump method (`.profile_tmp/diag_pr_per_rule.py`
  shape): per-rule scoring on the NEW (selective) candidate set.
- **Primary (mechanism):** the scored candidate pool shrinks materially (8.84M ->
  within `K x N`) and the precision ceiling rises well above 3.4%.
- **Headline:** `historical_50k` reaches an operating point with **F1 materially >
  0.655** -- target P >= ~0.8 at R >= ~0.7 (the regime per-rule scoring can reach
  once the candidate set is clean).
- **Non-regression:** febrl3 (>= ~0.982) and synthetic_person (>= ~0.987) F1 do not
  drop (their candidate ratios are already low, so the budget should not bind; the
  gate confirms it).

## Kill criterion

If a selective candidate budget that holds the febrl3/synthetic non-regression
floors STILL cannot lift `historical_50k` precision into the gate (i.e. cutting
candidates to `K x N` either craters recall before precision rises, or the
remaining within-budget candidates are still dominated by same-name non-matches),
then the wall is deeper than blocking-rule selectivity -- it is the F-S model's
inability to separate same-name different-person pairs on this biographical data,
and the redirect is to scoring-side entity disambiguation / a learned blocker,
sequenced after this lever. Stop and re-brainstorm rather than chase K.

## Testing

- **Compound generation:** `_candidate_blocking_passes` emits compounds that
  preserve the name component's transforms and conjoin an orthogonal field;
  high-card orthogonals also appear as single-keys; name passes are present as
  candidates.
- **Budget selection:** `_select_passes_within_budget` never exceeds `K x N`, is
  coverage-greedy (a strictly-dominated redundant pass is not chosen over a
  complementary one), and always emits >= 1 name-bearing pass.
- **Degenerate fallback:** no orthogonal fields -> returns `build_blocking` output
  unchanged.
- **Behavioral (synthetic fixture):** the selected config has materially fewer
  candidate pairs than the old broad union AND retains the GT-bearing pairs
  (blocking recall on the fixture is held).
- **Non-regression:** existing autoconfig-probabilistic tests + blocker tests stay
  green; the emitted `BlockingConfig` is consumed by the pipeline + per-rule EM
  unchanged.

## Scope / out of scope

- **In:** the per-field `field_transforms` mechanism (`BlockingKeyConfig` schema
  field + validator + `_build_block_key_expr` support, all backward-compatible);
  `_build_probabilistic_blocking` redesign + `_candidate_blocking_passes`,
  `_estimate_pass_stats`, `_select_passes_within_budget`; the `K` budget constant +
  env override; tests. `K`-calibration is executed in the plan via the dump
  PR-curve sweep.
- **Out:** the weighted/exact-path blocking (`build_blocking` /
  `_build_compound_blocking`) stays unchanged; TS parity; the CI Splink head-to-head
  panel + branch rebase; any further EM/scoring work.
- **Explicitly NOT covered: large-N (>> 50K) budget estimation.** Candidate counts
  scale with N (block size proportional to N), so the exact-groupby budget estimate
  is computed at the gate dataset's 50K scale; at larger N where auto-config samples,
  the estimate needs extrapolation. The probabilistic path's gate is `historical_50k`
  (50K); a sample-extrapolated budget for large-N selective blocking is a noted
  follow-up, not in scope here.

## Risks & mitigations

- **Compounds drop recall:** a typo in either component kills the pair. Mitigated
  by (a) keeping the name component's recall transforms (soundex/substring) inside
  the compound, (b) the coverage-greedy set-cover preferring complementary passes
  that catch different corruption modes, and (c) the recall anchor + the
  blocking-recall ceiling measurement gating each K.
- **Coverage estimation cost at autoconfig time:** the per-pass pair-enumeration
  cap (~500K/pass) bounds it; the budget itself uses the cheap exact groupby count.
- **K mis-calibration:** the plan sweeps K against the real gate (F1 +
  non-regression) rather than guessing; K is env-overridable for A/B.
- **The wall is deeper than blocking:** the kill criterion catches it and redirects
  to scoring-side disambiguation / learned blocking.

## Affected files (anticipated)

- Modified: `config/schemas.py` (`BlockingKeyConfig.field_transforms` optional field
  + length validator); `core/blocker.py` (`_build_block_key_expr` per-field transform
  support, default-None byte-identical); `core/autoconfig.py`
  (`_build_probabilistic_blocking` rewrite + `_candidate_blocking_passes` +
  `_estimate_pass_stats` + `_select_passes_within_budget` + the `K` budget
  constant/env read). May reuse helpers/patterns from the existing
  `_build_compound_blocking`.
- New tests: `tests/test_autoconfig_selective_blocking.py` (and a per-field-transform
  unit test, in `tests/test_blocker.py` or the new file).
- Reused (no change): the dump PR-curve + blocking-recall diagnostics
  (`.profile_tmp/diag_pr_per_rule.py`, `.profile_tmp/diag_blocking_recall.py`) for
  the measurement gate.
