# Probabilistic matching → Splink parity (design)

**Date:** 2026-06-08
**Status:** Approved design, pre-plan
**Scope:** GoldenMatch probabilistic (Fellegi-Sunter) matching path only. Zero change to
the weighted/exact default paths.
**Workstream:** First of a planned pair — this cycle is *probabilistic accuracy*.
Perf optimization and bio/product-domain scoring are deferred to later cycles.

## Problem

GoldenMatch's probabilistic engine already implements the Splink *core*: u-fixed-from-
random-pairs EM, `log2(m/u)` match weights, 2/3/N comparison levels, and a Winkler
continuous extension (`core/probabilistic.py`, `core/probabilistic_fast.py`). It is
nonetheless "not Splink level" on accuracy.

The decisive evidence (from `packages/python/goldenmatch/CLAUDE.md` Accuracy Strategy):

> **Fellegi-Sunter: 98.8% precision, 57.6% recall, 72.8% F1 on DBLP-ACM.**

That is a textbook **recall-bound** profile. Recall that low is almost always *candidate
pairs never generated* (blocking), not *pairs scored and rejected* (threshold). The two
levers GoldenMatch is missing versus Splink:

1. **No multi-rule union blocking.** Splink generates candidates from a *union* of
   blocking rules (~0.99 pair coverage); GoldenMatch's probabilistic path rides
   single-key bucket blocking (~0.94). This is the recall lever.
2. **No term-frequency adjustments.** Splink's signature accuracy feature: a match on a
   rare value carries far more evidence than a match on a common one. GoldenMatch's
   `comparison_vector()` is level-only — every exact-agree weighs identically. This is
   the precision/discrimination lever.

A third, lesser gap: the probabilistic path is non-iterative (no controller, no
threshold-calibration loop). Out of scope for this cycle; revisit only if the harness
says threshold loss dominates.

## Success criterion

A multi-benchmark **gate panel anchored on Splink's home-turf `historical_50k`** dataset
(Wikidata-derived biographical historical-people set, with `cluster` ground-truth
labels — the dataset Splink's own docs tune and demo on). The panel also covers DBLP-ACM,
Febrl3, NCVR, and the existing synthetic head-to-head set, every row GoldenMatch-vs-Splink
on identical data.

Concrete gate values are **fixed after the first baseline run, not invented now**:
- **Headline:** `historical_50k` F1 ≥ Splink − ε on the same data.
- **Recall hole:** DBLP-ACM recall up materially from 57.6% with precision ≥ ~0.95.
- **Non-regression:** Febrl3 + NCVR + head-to-head do not drop.

## Approach: C → A

Build the diagnostic harness first (C), use it to confirm the recall is
blocking-dominated, then ship the two levers in attribution-justified order (A):

```
Stage 0 (C)  Diagnostic gate panel        — the success criterion + the microscope
Stage 1 (A1) Multi-rule union blocking    — the recall lever
Stage 2 (A2) Term-frequency adjustments   — the precision/discrimination lever
```

Each stage is independently shippable. Stages 1–2 are gated by the Stage 0 harness, and
the harness can **overrule the plan ordering** (see Kill criteria).

---

## Stage 0 — Diagnostic gate panel

The proof target *and* the instrument that localizes where recall dies.

**DRY: extend the existing `scripts/bench_er_headtohead/` directory** rather than create a
parallel `scripts/bench_probabilistic/`. That dir already has `evaluate.py` (pairwise
P/R/F1 + B³ via a DuckDB contingency table), `run_splink.py`, `run_goldenmatch.py`, and
`orchestrate.py`. New Stage-0 files are added there and reuse `evaluate.py` verbatim.

### 0a. Dataset loaders — `scripts/bench_er_headtohead/datasets.py`
- `historical_50k` — loaded via `splink_datasets.historical_50k` when `splink` is
  installed, else from a vendored parquet in the gitignored
  `tests/benchmarks/datasets/`. **Headline dataset.**
- Adapters presenting DBLP-ACM, Febrl3, NCVR (existing loaders under `tests/benchmarks/`)
  and the head-to-head synthetic person set in the common `{record_id, …fields}` +
  truth `{record_id, cluster_id}` shape `evaluate.py` already consumes.

### 0b. Recall attribution instrument — `scripts/bench_er_headtohead/attribution.py`
The new diagnostic. For any labeled dataset, decompose recall into the three places a
true pair can die:

| Metric | Definition |
|---|---|
| `blocking_recall` | (GT pairs surviving candidate generation) / (all GT pairs) — the ceiling |
| `threshold_loss` | (candidate∩GT − emitted∩GT) / (all GT pairs) — pairs scored but rejected |
| `final_recall` | emitted∩GT / (all GT pairs) |

A run reports e.g. "recall 57.6% = blocking ceiling 61% − 3.4% threshold loss" → blocking
is the wall; or the opposite. This makes Stage 1-vs-2 prioritization evidence-based, and
is what the kill criteria read.

### 0c. Splink baseline runner
Extend `scripts/bench_er_headtohead/run_splink.py` to cover `historical_50k` and emit the
same `{P, R, F1, B³}` shape, so every panel row is apples-to-apples.

### 0d. Gate runner + CI
`scripts/bench_er_headtohead/run_panel.py` runs the full panel for both engines, writes a
markdown comparison table + JSON. `.github/workflows/bench-probabilistic.yml` is
`workflow_dispatch` only. `splink` stays an **optional, bench-only dep** (heavy, pulls
DuckDB) — never in the shipped wheel; declared in a `[bench]` extra.

---

## Stage 1 — Multi-rule union blocking (recall lever)

Give the probabilistic path Splink-style union-of-rules candidate generation.

**DRY discovery (verified in source).** Multi-pass union blocking *already exists* and the
probabilistic path *already routes through it*:
- `BlockingConfig.passes: list[BlockingKeyConfig]` + `strategy="multi_pass"` →
  `core/blocker.py::_build_multi_pass_blocks` runs each pass and unions blocks, deduped by
  `block_key`.
- The probabilistic pipeline branch calls `build_blocks(combined_lf, config.blocking)`
  (`pipeline.py:1336`), so a `multi_pass` config flows straight in.
- Pair-level dedup across passes is handled by the pipeline's `matched_pairs` exclude set
  (`pipeline.py:1389-1390`).

So Stage 1 builds **no new module and no new config field** (the spec's earlier
`MatchkeyConfig.blocking_rules` / `core/probabilistic_blocking.py` idea is superseded by
reuse). The actual work is three smaller pieces:

### 1a. Fix the latent EM `blocking_fields` bug (correctness — do FIRST)
At the train_em call site (`pipeline.py:1337-1340`) `blocking_fields` is collected from
`config.blocking.keys` **only**. Under `strategy="multi_pass"` the fields live in
`config.blocking.passes`, so `blocking_fields` comes back **empty** → EM tries to learn
m/u for fields that are agree-by-construction within every pass's blocks → the exact
m/u collapse the module docstring warns about. Fix: collect the **union of fields from
both `keys` and `passes`**. This must land before 1b or the recall win is poisoned by a
scoring regression.

### 1b. Probabilistic auto-config emits a capped multi-pass config
Add `_build_probabilistic_blocking(profiles, df)` and call it from
`auto_configure_probabilistic_df` (replacing the single-strategy `build_blocking` call for
this path). It derives a small pass set from column profiles: 2-field conjunctions of
identity-ish fields (high `identity_score`, moderate cardinality) plus single
high-cardinality keys, **capped at ~4 passes** (Splink's typical count) to bound the pair
budget, and sets `strategy="multi_pass"`, `skip_oversized=True`, and a sane
`max_block_size`. An explicit user `blocking` config always wins.

### 1c. Per-pass block-size guard
`_build_multi_pass_blocks` already forwards `max_block_size` + `skip_oversized` to
`_build_static_blocks` per pass. Stage 1b sets `skip_oversized=True` so a pass that
produces a mega-block (shared null, "info@") is dropped, not scored. A test asserts a
mega-block pass is excluded while the other passes still contribute. (If
`_build_static_blocks` truncates rather than skips on `skip_oversized`, add the skip there.)

### 1d. EM neutral-prior approximation (record, don't fix)
Treating the union of all pass fields as `blocking_fields` is a **conservative
approximation, not an identity**: a field that anchors pass A is not agree-by-construction
in pairs generated only by pass B, yet it gets neutral priors everywhere. Conservative
(never produces wrong merges) but leaves some discrimination on the table. Splink's full
per-comparison `u` handling is a deliberate non-goal this cycle — recorded so nobody chases
a "why isn't this field exactly agree" discrepancy.

### 1e. Stopping rule
Stage 0's attribution reports the `blocking_recall` ceiling each added pass buys. Stop
adding passes when the ceiling stops moving — not blindly.

---

## Stage 2 — F-S scoring core (REVISED 2026-06-08; supersedes the TF plan below)

**Pivot driven by the Stage-1 attribution + a score-distribution diagnostic on
`historical_50k`.** After Stage 1 raised the blocking ceiling to 0.83, F1 *fell*
(0.655→0.585): the scorer rejected half the new candidates (threshold_loss 0.49). The
diagnostic (`.profile_tmp/diag_score_dist.py`) found the cause is NOT the missing TF
adjustments — it is two F-S scoring-core flaws:

1. **Min-max normalization is pathological.** `score_probabilistic` /
   `score_probabilistic_fast` / `score_pair_probabilistic` map the summed log2-Bayes
   weight to [0,1] via `(W − min)/(max − min)`. One high-variance EM weight
   (`postcode_fake` ≈ +31.63 on rare values) blows up the range so a pair agreeing on all
   five name fields scores 0.45 < the 0.5 threshold → rejected. **Fix: Splink-style sigmoid
   match-probability** `P = 1/(1 + 2^(−W))` (W is already the sum of log2 Bayes factors).
   Measured: same weights, t=0.5 recall 0.329 (min-max) → **0.655 (sigmoid)**.
2. **Wholesale blocking-field exclusion neuters the strongest fields.** `train_em`
   excludes every blocking-pass field (fixed `[-3,0,3]` priors). Under *multi-pass union*
   blocking a field varies across passes, so EM *can* train it. Stage 1's orthogonal passes
   pulled name components into the blocking set → names neutered. **Fix: exclude only fields
   agree-by-construction in EVERY pass (the intersection of pass field-sets), not the union**
   — usually empty under union blocking. Measured: no-exclusion + sigmoid → recall **0.828
   @t=0.5 and 0.788 @t=0.95** (≈ the full ceiling at a high-precision threshold ⇒ F1 0.585 →
   ~0.85, the Splink trajectory).
3. **Threshold calibration.** Sigmoid makes the threshold a real match-probability;
   `compute_thresholds`/default link_threshold must be sigmoid-aware (a 0.5 min-max default
   ≠ a sensible match-probability default).

### Revised design
- **2a. Sigmoid match-probability.** Replace the min-max final step in `score_probabilistic`,
  `score_probabilistic_fast`, and `score_pair_probabilistic` with `1/(1+2^(-W))`. Default-on,
  env kill-switch `GOLDENMATCH_FS_SIGMOID=0` restores min-max (repo convention for behavior
  changes). The three paths must stay in parity (fast == slow under sigmoid).
- **2b. Union-aware EM exclusion.** The EM-excluded set = intersection of pass field-sets
  (fields in EVERY candidate-generation pass), treating `keys` as one pass when `passes` is
  absent. Single static key → that key excluded (unchanged); multi-pass union → ~nothing
  excluded (names train). Revises Task 1.1's union semantics (which over-excluded).
- **2c. Sigmoid-aware thresholds.** `compute_thresholds` + the probabilistic default
  link_threshold become match-probability values (e.g. ~0.9 link), refined by measurement on
  the panel.

### Success bar (unchanged target, now concrete)
`historical_50k` F1 materially > the 0.655 Stage-0 baseline (target ≈ Splink-class), with
precision held (confirm at a high match-probability threshold); febrl3/synthetic/dblp_acm
non-regression. Verify precision-at-threshold during implementation (the diagnostic scored
only GT pairs).

### TF demoted
Term-frequency adjustments are now an **optional follow-up**, not this cycle (the data
already uses `name_freq_weighted_jw` similarity scorers; sigmoid + un-exclusion capture the
dominant gap). The original TF design is retained below for the record, superseded by 2a–2c.

---

## (SUPERSEDED) Stage 2 — Term-frequency adjustments (discrimination lever)

Make the top (exact-agree) level value-aware, the way Splink does.

### 2a. The math
For a TF-enabled field, replace the level's population-average `u` with the value-specific
`u_v` at the exact-agree level. Use Splink's **scale-the-base** form, centered so a value
of *average* frequency lands on the base weight:
```
freq(v)   = count(v) / N                 # relative frequency of the shared value
freq_avg  = 1 / n_distinct               # mean relative frequency over distinct values
u_v       = u_exact * ( freq(v) / freq_avg ) = u_exact * freq(v) * n_distinct
u_v       = clamp(u_v, TF_MIN_U, TF_MAX_U)
weight_exact(v) = log2( m_exact / u_v )
```
Sign check (this is the trap — get it right): rarer value ⇒ `freq(v) < freq_avg` ⇒
`u_v < u_exact` ⇒ **larger** positive weight. Average-frequency value ⇒ `u_v == u_exact`
⇒ base weight (TF is a no-op there). Common value ⇒ `u_v > u_exact` ⇒ weight collapses
toward / below base. Partial/disagree levels are unchanged (TF only sharpens exact
agreement).

**This is the mandated formula** — "rarer ⇒ larger weight" is satisfiable by several
non-equivalent expressions (raw-frequency replacement `u_v = freq(v)` vs this scale-the-base
form); the scale-the-base form keeps the non-TF default as the centered case. The
monotonicity test guards the sign; this formula fixes the exact relationship.
`TF_MIN_U`/`TF_MAX_U` are module constants (env-overridable) that bound the hapax /
huge-value extremes.

### 2b. Implementation
- Precompute per-field relative-frequency tables from the prepared data (Polars
  `value_counts` → normalized dict), stored on a new
  `EMResult.tf_tables: dict[str, dict[str, float]] | None` field.
- **Clamp** like Splink: `u_v` floored/capped (`tf_min_u`, `tf_max_u`) so a hapax value or
  a tiny sample cannot produce an unbounded weight.
- Scoring carries the shared value for TF fields: extend the score path to look up `u_v`
  for the exact-agree level and recompute that field's contribution; non-TF fields and
  non-exact levels untouched.
- **Fast path** (`probabilistic_fast.py`): extend the pre-resolved spec with the frequency
  lookup table for TF fields (a dict lookup, fully vectorizable). Fall back to slow path
  only if a TF field is model-backed.

### 2c. Config + auto-config
- `tf_adjust: bool` per `MatchkeyField` (default `False`). Auto-config enables it for
  high-skew identity fields (name-like, where `value_counts` shows Zipfian skew) — exactly
  the fields where Splink gets its lift.

### 2d. Parity scope
Python first. TS port (`probabilistic.ts`) and the `EMResult` schema sync are an explicit
**follow-up**, not in this cycle (keeps the loop tight; matches how prior probabilistic
work landed Python-first).

---

## Testing

### Stage 0
- Attribution math on a hand-built labeled fixture where the blocking-vs-threshold split
  is known by construction (e.g. 10 GT pairs, 3 deliberately un-blockable →
  `blocking_recall` must read 0.7).
- Loader smoke tests `pytest.skip` cleanly when `splink`/datasets absent (the repo's
  gitignored-dataset pattern).

### Stage 1
- Union dedup correctness: overlapping rules produce each canonical pair exactly once.
- Block-size guard: a rule with a mega-block is dropped + warned, not truncated; other
  rules still contribute.
- Backward compat: `blocking_rules=None` reproduces today's candidate set byte-for-byte.
- EM `blocking_fields` now covers the union of rule fields.

### Stage 2
- Weight monotonicity: same field+level, rarer shared value ⇒ strictly larger weight.
- Clamp: a hapax value's weight is bounded by `tf_max_u`.
- Fast-vs-slow parity *with TF on* (within rapidfuzz tolerance) — extends the existing
  parity test.
- `tf_adjust=False` is byte-identical to the pre-change scorer.

## Kill criteria

- **After Stage 0:** if attribution shows recall is *not* blocking-dominated
  (`threshold_loss` ≫ the blocking-ceiling gap), re-sequence — TF/calibration before union
  blocking. The harness overrules the plan.
- **After Stage 1:** if union blocking lifts the `blocking_recall` ceiling but final F1
  does not follow, the wall is scoring/threshold → Stage 2 carries the work; stop piling
  on blocking rules.

## Risks & mitigations

- **Splink as bench dep** — heavy (DuckDB), version-sensitive. → optional `[bench]` extra,
  `workflow_dispatch` only, never in the wheel.
- **`historical_50k` provenance** — Wikidata-derived, ships with Splink (MIT). → vendor to
  the gitignored datasets dir; loader prefers `splink_datasets`, falls back to URL fetch.
- **Pair-budget blow-up** from union blocking — this is where accuracy work touches the
  deferred *perf* workstream. → bounded by the per-rule block-size guard + the ≤4-rule cap.
- **Fast-path divergence** — TF must stay on the vectorized path or it silently falls back
  to slow scoring at scale. → the fast-vs-slow parity test (TF on) is a hard gate.

## Out of scope (explicit)

- Perf optimization (native clustering/golden, driver-side materialization) — next cycle.
- Bio/product domain-aware scorers (unit/dosage comparators, identifier checksums) — next
  cycle.
- TS parity for TF adjustments — follow-up after Python lands.
- Probabilistic threshold-calibration loop / controller integration — only if Stage 0
  attribution says threshold loss dominates.

## Affected files (anticipated)

- New: `scripts/bench_er_headtohead/{datasets,attribution,run_panel}.py`,
  `.github/workflows/bench-probabilistic.yml`, test modules under `tests/`.
- Modified:
  - `core/pipeline.py` — `blocking_fields` collected from `keys` **and** `passes` (Stage 1a).
  - `core/autoconfig.py` — `_build_probabilistic_blocking` (multi-pass derivation, Stage 1b)
    + `tf_adjust` enable in `build_probabilistic_matchkeys` (Stage 2c).
  - `core/probabilistic.py` — `EMResult.tf_tables`, TF table computation in `train_em`,
    TF-aware scoring in `score_probabilistic` / `score_pair_probabilistic` (Stage 2).
  - `core/probabilistic_fast.py` — TF freq table in the resolved spec + TF-aware top-level
    scoring (Stage 2b).
  - `config/schemas.py` — `MatchkeyField.tf_adjust: bool = False` (Stage 2c).
    **No `MatchkeyConfig.blocking_rules`** — Stage 1 reuses `BlockingConfig.passes`.
  - `core/blocker.py` — only if `_build_static_blocks` needs a `skip_oversized` skip
    (Stage 1c; likely already honored).
  - `scripts/bench_er_headtohead/run_splink.py` + `run_goldenmatch.py` — `historical_50k`
    + dataset arg (Stage 0).
  - `pyproject.toml` — `[bench]` optional extra (`splink`, dataset deps).
