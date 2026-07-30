# Cost-aware blocking primary-key selection (identifier-poor data)

Status: draft → implementing
Owner: auto-config / blocking
Flag: `GOLDENMATCH_BLOCKING_COST_AWARE` (default OFF; byte-identical when off)
Validation: `bench_er_headtohead` panel (Febrl3 / synthetic / historical / DBLP-ACM), QIS
scale-invariance gate, and a direct 1M blocking-cost measurability check. **DQBench is
intentionally NOT a gate for this change.**

## Problem

On identifier-poor person data (the `qis_gate` corrupted-realistic shape: `id` a unique
surrogate, `email` corrupted/low-overlap, `city`/`zip` geo/zip signals, names near-unique
per cluster), zero-config commits a **coarse `birth_year`-only** blocking key. `birth_year`
has a *fixed* ~65-value domain, so its block size grows ∝N:

| rows | est. candidate pairs = n²/(2·65) |
|---|---|
| 500K | ~1.9B (measurable, ~9 min) |
| 1M | **~7.7B** (infeasible; ran to a runner-preemption — issue #2021) |

Smaller rungs measure this exact config at F1≈1.0, so it is not a *quality* regression —
but it is a scale-behaviour defect: a real 1M+ user gets an exploding (OOM-risk / very slow)
run from zero-config, and the `qis_gate` had to *skip* 1M rather than measure it.

## Root cause (traced)

`autoconfig.build_blocking`, the "best case" exact-blocking branch (~L3289):

1. `exact_cols` candidates = `col_type ∈ {email, phone, zip, identifier, year}` — **`name`
   columns are excluded**. `last_name` (distinct == n_clusters, block ~5, the ideal
   per-cluster key) is never a candidate here; names only appear in the *later* name/geo
   multi_pass branches, which the early `return` at L3315 preempts.
2. The high-cardinality survivors (`email`, `zip`) are rejected by the `blocking_max_ratio`
   near-singleton gate (#408/#410 — they project to ~1-row blocks).
3. That leaves `birth_year` (`col_type="year"`, 65 distinct → low ratio passes the gate,
   block ≤ `max_safe_block` at ≤100K so `_is_scale_safe` admits it) as the **only** exact
   candidate → committed as the sole static key `keys=[birth_year:strip]`.
4. At ≥50K the strategy is flipped to `learned` (keys unchanged), and `_build_learned_blocks`
   seeds Pass-1 static blocking from that key — so the learner even trains on birth_year-biased
   pairs. (Given clean pairs the learner strongly prefers `last_name`: reduction 0.9998 vs
   birth_year 0.984 — so the ranking logic is fine; the seed is the problem.)

`birth_year` as a **recall pass** is a legitimate, benchmarked win (#438, Febrl3 recall
0.72→0.95). The defect is `birth_year` as the **primary/sole** key.

## Fix

A cost-aware guard in the exact-blocking best-case branch: **do not commit a small-fixed-domain
exact key (a `year`/`date` col, or any key whose block grows ∝N and projects oversized at the
true target N) as the SOLE primary when a better-bounded alternative exists** (a `name` key, or
the existing bounded-compound / name-fallback branches). The coarse key stays available as a
recall PASS via the existing #438 `_pick_date_blocking_col` path in the fall-through branches.

Concretely (flag-gated):
- When `safe_exact` would pick a "growing small-domain" primary (`col_type in {year, date}` OR
  projected full-N block ∝N over the pair-explosion envelope) AND a name-based primary is
  available (`name_cols` non-empty, or a bounded compound), skip the early `return` and fall
  through to the name/compound branches — which already attach the date column as a recall pass.
- Net: primary becomes a bounded name/compound key (block ~constant), `birth_year` remains a
  pass. 1M becomes *measurable* (pairs under the in-memory envelope) with no recall loss.

Default OFF (byte-identical) until the panel + QIS prove it, per the `GOLDENMATCH_FS_AUTOCONFIG_V2`
precedent. Flip to default-on only on measured F1-neutrality + the 1M-measurability win.

## Validation plan

1. **1M blocking cost** — auto_configure the qis realistic 1M frame under the flag; assert the
   committed blocking's projected candidate pairs fall under `SIMPLE_PLAN_MAX_PAIRS`-class
   envelope (i.e. primary is a bounded name/compound key, not birth_year-sole).
2. **F1-neutrality** — `scripts/bench_er_headtohead` panel (Febrl3 / synthetic / historical /
   DBLP-ACM) under flag OFF vs ON; require no F1 regression (recall especially, since #438 is a
   recall lever).
3. **QIS scale-invariance** — `qis_gate` ci tier now *measures* 1M (no pair_explosion skip) at
   F1 tracking the smaller rungs.
4. Unit tests locking: birth_year-only shape → cost-aware picks a name primary + keeps the date
   pass; flag OFF is byte-identical; a shape with a genuinely good low-card key is unaffected.

## Non-goals

- No change to the learned-blocking ranking (it is already correct given good pairs).
- No change to #438 date-pass recall behaviour (the date column stays a pass).
- DQBench is not exercised for this change.
