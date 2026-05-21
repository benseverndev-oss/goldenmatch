# ADR-0004: Chao1 sample-size correction for autoconfig cardinality

**Status:** Accepted
**Date:** 2026-05-21 (PR #411, #414)

## Context

Auto-config profiles a 5K controller sample. The blocking-candidate gate (ADR-0003) checks `cardinality_ratio` against bounds (0.001–0.5). On real data, `zip` at full N=1.13M has ratio ~0.004 (5K distinct), but at sample N=1000 it shows ratio 0.6-0.8 (600-800 distinct) because the sample is too small to repeat any zip code many times. The gate would reject `zip` on sample data even though it's an obvious blocking candidate at full scale.

Three projection options considered:

| Option | Formula | Math character | Trade-off |
|---|---|---|---|
| **Linear** | `sample_distinct × full_n / sample_n` | overestimates | rejects more |
| **Postgres `pg_stats.n_distinct`** | (exact, from catalog) | accurate | Postgres-only |
| **Chao1 sqrt** | `sample_distinct × √(full_n / sample_n)` | sublinear, underestimates ~30% | passes more |

## Decision

Use the **Chao1 sqrt scaler** for projecting sample cardinality to full population. Universal across connectors; underestimates by ~30% on heavy-tail distributions, which is the **safe direction** for our gate (smaller projected ratio → more permissive on real columns → more likely to keep a useful blocking candidate).

Implementation: `scale_cardinality_ratio_to_full_population(sample_distinct, sample_n_rows, full_n_rows)` in `core/blocking_candidates.py`. Same scaler reused by `estimate_avg_block_size` for the `BLOCKING_DEGENERATE` fail-loud guard.

Env override: `GOLDENMATCH_BLOCKING_CARDINALITY_SCALER=observed` reverts to linear scaling (pre-#411 behavior) for users who want exact reproducibility against a prior version.

Rejected alternatives:
- **Linear scaling.** Overestimates distinct count on heavy-tail data; would incorrectly reject `zip` and similar mid-cardinality columns that ARE good blocking candidates.
- **Postgres-specific `pg_stats.n_distinct`.** Works only for the postgres connector; the same problem exists for CSV / parquet / DuckDB. Universal Chao1 is the right default, with a Postgres optimization as a deferred follow-up.
- **Coupon-collector exact correction.** Overkill — magnitude is what matters for the gate (is it ~0.05 or ~0.8), not 3-decimal precision.

## Consequences

Positive:
- Mid-cardinality columns survive the gate even on small samples.
- Same scaler composes into the `BLOCKING_DEGENERATE` guard (one formula, two consumers).
- Postgres `pg_stats.n_distinct` shortcut is still possible as a future optimization without changing the API.

Negative:
- Chao1 **underestimates** uniqueness for per-record-unique columns sampled small. A column with 1000 distinct in a 1000-row sample projects to ratio ~0.03 at full N=1.13M, which **passes** the gate. The downstream `BLOCKING_DEGENERATE` guard catches this false-pass at the controller level (ADR-0001's machinery). Documented explicitly in the spec + test.
- The `sample_n ≥ full_n` short-circuit (returns observed ratio) requires the controller to actually pass the true full count. Initial #411 shipped without the controller threading the true `n_rows` to v0 — sample size leaked in as `total_rows`. #414 fixed that by adding `n_rows_full` kwarg to `_legacy_auto_configure_v0` and threading from the controller. Tests now pin the contract.

Cross-references:
- Specs: `docs/superpowers/specs/2026-05-21-blocking-pool-followup-design.md`
- PRs: #411 (Chao1 wiring), #414 (controller threading fix)
- ADR-0003: matchkey vs blocking pools
