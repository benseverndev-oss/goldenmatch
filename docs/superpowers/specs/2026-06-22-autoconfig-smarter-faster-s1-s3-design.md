# Auto-config smarter levers S1–S3 — design

- **Date:** 2026-06-22
- **Branch:** `feat/autoconfig-smarter-faster-s1-s3` (off `main`)
- **Parent assessment:** `docs/superpowers/specs/2026-06-22-autoconfig-smarter-faster-assessment.md`
  (read it first — this doc is the detailed design for that assessment's S1, S2, S3 levers;
  do not re-derive its prior-art survey or sequencing rationale)
- **Predecessor arc (shipped):** the auto-config native-core A–F arc landed on `main`
  (#1166 / #1174 / #1175 / #1177 / #1180). `"autoconfig"` is in `_GATED_ON`, so the shared
  pyo3-free `goldenmatch-autoconfig-core` kernel is the default decision core across
  Python / TS (wasm) / (future) SQL, and `goldenmatch-native` 0.1.7 is on PyPI.

## Summary

The gate-flip is the multiplier: a change to *decision logic* in the core is earned once and
inherited by every surface. This spec spends that leverage on the three highest-value "smarter"
levers from the assessment, **and deliberately routes each one through the core** so Python, TS,
and SQL inherit it for free rather than drifting per-surface:

- **S1** — replace the linear sample→full pair-count extrapolation (which systematically
  *under*-counts at scale) with an unbiased quadratic estimator + a Chao1 block-richness
  correction + a safety cap. This is the single biggest "wrong answer at scale" bug in
  auto-config today.
- **S2a** — make the identifier-classification cardinality floor row-count-aware
  (`0.95` → `1 − 1/√n`).
- **S2b** — make the sparse-match floor pair-count-aware (`50` → `min(50, 0.01·estimated_pairs)`).
- **S3** — replace the single `0.5` exact-matchkey cardinality floor with per-type floors
  (closes the standing TODO at `autoconfig.py:877`, issue #715).

`SIMPLE_PLAN_MAX_PAIRS` (assessment item S2c) is **deferred** — it is only worth revisiting once
S1 makes pair counts trustworthy.

## Architecture: decision kernels in the core, measurement per-surface

Every lever splits into two parts:

| Part | Example | Lives in | Cross-surface? |
|---|---|---|---|
| **Decision kernel** — a pure, deterministic formula or threshold | "given block summary stats + sample/full row counts, return the corrected pair count"; "given `col_type`, return the exact-matchkey floor" | `goldenmatch-autoconfig-core` (Rust) | **Yes** — earned once, every surface inherits it |
| **Measurement** — counting that touches the data frame | running blocking and emitting block-size stats (Polars `group_by`); counting exact-matchkey collisions | per-surface runtime (Python/Polars; TS profiler) | No — each surface measures its own way |

The assessment's split table lumped "candidate-pair counting" into per-surface measurement. This
spec splits finer: the **raw stat collection** is per-surface, but the **extrapolation /
threshold logic that consumes those stats is decision logic** and belongs in the core. The
measurement native kernel is a known perf wash-to-loss (Stage D) — that is about *speed*, not
about *where the decision logic lives*. We are not moving Polars into Rust; we are moving the
deterministic formulas that run once per planning decision (cheap; the JSON bridge cost is
irrelevant at one call per plan / per column).

Each lever therefore follows the established native-core pattern, exactly like the planner and
classifier already shipped:

1. **Pure-Python implementation** in the Python surface — this is the fallback path **and** the
   parity oracle.
2. **Rust core implementation** in `autoconfig-core`.
3. **Golden vectors** generated from the Python oracle, asserted byte-identical by Rust, Python,
   and TS.
4. **Native dispatch** when `native_enabled("autoconfig")` (already default-on via `_GATED_ON`).

Consequence the author chose with eyes open: routing S1/S2b/S3 through the core means all three
now carry the golden-vector re-gen + cross-surface parity gate (previously only S2a did). That is
the price of earning it once, and it is paid against an existing, working golden harness.

## S1 — corrected pair-count extrapolation (headline)

### Problem (measured)

`BlockingProfile.extrapolate_to` (`core/complexity_profile.py:277`) scales the candidate-pair
count linearly:

```python
ratio = n_rows_full / n_rows_sample
total_comparisons = int(self.total_comparisons * ratio)   # linear — WRONG
```

Within-block pairs grow quadratically, not linearly. Under uniform random row sampling at
fraction `f = n_sample / n_full`, a full-data block of true size `S` is sampled to size
`s ~ Binomial(S, f)`, and:

```
E[s·(s−1)/2] = f²·S·(S−1)/2
```

Summing over all blocks (including those unsampled, whose expectation the binomial already
accounts for):

```
E[sample pairs] = f² · (full pairs)      ⇒      unbiased full pairs = sample pairs · ratio²
```

So the correct scale factor for the pair count is **`ratio²`, not `ratio`**. The current code is
biased low by exactly a factor of `ratio` (a 1% sample is 100× low, a 50% sample 2× low) — which
is precisely what `bench_autoconfig_sample_quality.py` measured on 2026-06-21. The downstream
effect: the planner reads an under-counted `estimated_pair_count` and picks `simple` / `bucket`
for a dataset that is truly a chunked-rung workload → under-provisioning.

`n_blocks` and `singleton_block_count` are also scaled linearly today; the number of distinct
blocks saturates with N (it does not grow linearly), so linear scaling *over*-counts `n_blocks`,
which skews `health()` (`avg = n_rows / n_blocks`, the singleton ratio).

### The kernel

A core function (Rust `autoconfig-core`, mirrored by a Python oracle) that takes the block
summary stats + sample/full row counts and returns the corrected `BlockingProfile` fields:

- **Pairs:** `total_comparisons × ratio²`.
- **n_blocks:** Chao1 richness estimate `n_blocks_sample + F1² / (2·(F2 + 1))`, where
  `F1 = singleton_block_count` (blocks with exactly 1 sampled row — already measured) and
  `F2 = doubleton_block_count` (blocks with exactly 2 sampled rows — **new measurement field**,
  see below). This reuses the existing Chao1 pattern already proven in
  `FieldStats.estimated_full_cardinality` (`complexity_profile.py`, added 2026-05-29) — same
  estimator, applied to block richness instead of value richness.
- **singleton_block_count:** scaled consistently with the Chao1 model (the residual singleton
  mass after richness extrapolation); falls back to linear when `F2` is absent.
- **Cap (safety rail):** clamp the extrapolated pair count at `n_full·(n_full−1)/2` (the all-pairs
  maximum — blocking can never produce more) and `n_blocks` at `n_full`. Guards against a
  pathological lucky-large-block sample over-shooting. Over-estimation is the *safe* direction
  (heavier plan, correct results) per the existing `extrapolate_to` docstring; the cap just keeps
  it physically possible.

### Measurement change

`BlockingProfile` gains an **optional** `doubleton_block_count: int = 0` field (it already carries
`singleton_block_count`). The Polars `measure_blocking_profile` fast path
(`_fast_static_block_sizes`, shipped in #1180) already computes the per-key block-size
distribution, so counting size-2 keys is a trivial addition to that aggregate. The field is
optional and the kernel falls back to **linear n_blocks scaling when `F2` is absent**, so the TS
profiler keeps working unchanged until its profiler is next touched (graceful cross-surface
degradation; the pair-count `ratio²` fix needs no new measurement and so reaches TS immediately
via the core).

### Scope of effect

After F2-the-stage (#1180), full-frame measurement is the default at `normal`/`fast`/`thinking`/
`einstein` under the 20M-row backstop, so `extrapolate_to` is now reached **only on the residual
sampling fallbacks**: distributed mode, >20M-row lower tiers, and measurement failure. S1
improves exactly those paths.

### Gates

- `bench_autoconfig_sample_quality.py`: the measured extrapolation error must collapse toward ~0
  (the bench already exists and quantifies the bias).
- A **planner-routing test**: a fixture whose true pair count straddles `SIMPLE_PLAN_MAX_PAIRS`
  (50M) must route to `chunked` after the fix where it routed to `simple` before — the
  under-provisioning bug, pinned.
- DQbench / F1 suites: no regression.
- Golden vectors for the new kernel: Rust ≡ Python ≡ TS.

## S2a — adaptive identifier floor (core classify; cross-surface for free)

### Problem

`_classify_by_data` (`core/autoconfig.py:216`, mirrored in core `classify.rs:406`) reclassifies a
near-unique numeric-shaped column as an identifier when `cardinality_ratio >= 0.95`, on the values
examined. The fixed `0.95` ignores `n`: at 10k+ values a genuine high-entropy *name* column can
sit at 0.95 and be wrongly promoted to identifier; at tiny `n` a genuine identifier can dip below
0.95 by chance and be missed.

### The kernel

Replace the constant with `cardinality_ratio >= (1 − 1/√n)`, where `n` is the number of values
examined (`len(values)`):

| n | floor |
|---|---|
| 10 | 0.684 |
| 100 | 0.900 |
| 1,000 | 0.968 |
| 10,000 | 0.990 |
| 1,000,000 | 0.999 |

Stricter as `n` grows (a 10k-row 0.95 column is a name, not an ID); looser on small samples. The
gate (`data_type in {phone, zip, numeric}` and `len(values) >= 10`) is unchanged.

This edits the core classifier directly, so it is a **core change → golden re-gen + Python/TS
byte-parity re-proof + DQbench/F1 gate**. It is the only S1–S3 lever that touches the classifier
vocabulary path.

### Non-goal

Wiring the Chao1 *cardinality* estimate (`estimated_full_cardinality`) into classification — i.e.
correcting the sample-scale `cardinality_ratio` itself before the floor check — is a deeper
plumbing change (threads `n_full_rows` into the classifier, which it does not currently receive).
Explicitly out of scope here; tracked as a follow-up. S2a is about the floor's *adaptivity*, not
the cardinality measurement.

## S2b — adaptive sparse-match floor (core kernel; depends on S1)

### Problem

`estimate_sparse_match_signal` (`core/indicators.py:142`) flags a sample as sparse when its
exact-matchkey collision count is below a fixed `sparse_threshold = 50`. The `50` is independent
of row count and matchkey config — for a dataset that can only ever produce a few hundred pairs,
demanding 50 collisions in a 1,000-row sample is unreasonable and over-triggers
`rule_sparse_match_expand`.

### The kernel

Core `sparse_match_floor(estimated_pairs) -> int` returning `min(50, ⌊0.01·estimated_pairs⌋)`.
For datasets expected to produce ≥5,000 pairs the floor stays 50 (capped); below that it scales
down, so sparse-expansion is not over-triggered on small-yield data. `estimated_pairs` is the
**S1-corrected** pair count — this is why S2b sequences after S1.

`estimate_sparse_match_signal` keeps doing the collision counting per-surface; only the floor
comes from the core. The caller (the controller, which holds the `BlockingProfile` at that point)
threads `estimated_pair_count` into the call. Gate: DQbench/F1 + golden.

## S3 — per-type exact-matchkey floor (core kernel; closes TODO #715)

### Problem

The exact-matchkey selection loop (`core/autoconfig.py:877`) rejects any column whose
`cardinality_ratio < 0.5` from backing an exact matchkey, via a single blanket `0.5` for all
types. The code carries the literal `TODO(autoconfig): replace this blanket threshold with
per-type cardinality thresholds once we have empirical data`. zip/geo are already skipped entirely
by a separate guard above this one (unchanged).

### The kernel

Core `exact_matchkey_floor(col_type) -> f64` returning a per-type floor:

| col_type | floor | rationale |
|---|---|---|
| email | 0.70 | emails are near-unique; demand high cardinality |
| phone | 0.30 | legitimately shared (household/business lines) |
| name / string | 0.50 | default behavior preserved |
| (default / other) | 0.50 | default behavior preserved |

These are **starting values, calibrated on DQbench/F1**, not gospel — a per-type floor that helps
one dataset can regress another, so the values are tuned behind the benchmark delta, not reasoned
from first principles alone. The selection loop calls the core for the floor by `p.col_type`;
nothing else in the loop changes. Gate: DQbench/F1 + golden.

## Core API additions

`goldenmatch-autoconfig-core` gains three public entry points and one edit:

- `extrapolate_pair_count(...)` — S1. Input: block summary stats (`total_comparisons`, `n_blocks`,
  `singleton_block_count`, optional `doubleton_block_count`) + `n_rows_sample` + `n_rows_full`.
  Output: the corrected fields. Models the `ratio²` + Chao1 + cap logic.
- `sparse_match_floor(estimated_pairs: u64) -> u64` — S2b.
- `exact_matchkey_floor(col_type: ColType) -> f64` — S3.
- edit to `classify_by_data` in `classify.rs` — S2a (the adaptive floor).

Serde discipline carries over from the native-core arc: optional ints model Python `None` as JSON
`null` (never a `"none"` enum string); see the predecessor spec.

## Plumbing (the established native-core pipeline)

1. **autoconfig-core** — 3 new pub fns + 1 classifier edit + Rust unit tests.
2. **autoconfig-wasm** — 3 new `#[wasm_bindgen]` shims (JSON in/out), so TS consumes the same core.
3. **goldenmatch-native** — 3 new JSON in/out shims in `native/src/autoconfig.rs`; version bump
   `0.1.7 → 0.1.8`; republish the wheel **in the same change that adds the depended-on symbols**
   (the stale-wheel footgun — a new symbol behind an `AttributeError` fallback silently no-ops on
   every `pip install goldenmatch[native]` env until the wheel ships).
4. **TS** — `src/core/autoconfigWasm.ts` wires the 3 new fns + snake↔camel field adapters.
5. **Golden harness** — extend `scripts/gen_autoconfig_golden.py` and the
   `golden/{planner,classifier}_vectors.json` fixtures (or add a focused
   `kernels_vectors.json`); Rust `tests/golden.rs` + the Python parity test + the TS parity test
   cover the new kernels. Regeneration must be a no-op diff when the oracle and core agree.
6. **Python call sites** — `complexity_profile.py` `extrapolate_to` → core (S1);
   `indicators.py` sparse floor → core (S2b); `autoconfig.py:877` matchkey floor → core (S3);
   `classify_by_data` already routes through the core for S2a.

## Gating & benchmarks (measurement discipline)

Per the assessment's discipline and `feedback_verify_perf_not_just_ship` / the Stage-D lesson:
every accuracy/threshold change is gated on the real suites, never a proxy.

- **Golden parity** — Rust ≡ Python ≡ TS on the new kernel vectors (the anti-drift contract).
- **`bench_autoconfig_sample_quality.py`** — S1's primary gate (error → ~0).
- **Planner-routing fixture** — S1's under-provisioning regression, pinned.
- **DQbench / F1** — the accuracy guardrail for all four levers; no regression permitted. Land
  S2a/S2b/S3 one at a time so a regression is attributable.

## Sequencing (one bench-gated PR per lever)

1. **S1** first — foundational; S2b consumes its corrected pair count.
2. **S2a**, **S2b**, **S3** — independent, each its own golden-re-gen + DQbench-gated PR.

`goldenmatch-native` is bumped + republished with the first PR that adds a core symbol and kept in
lockstep thereafter.

## Where it runs

The Python implementations + the sample-quality bench are local-doable. The golden re-gen +
Python/TS parity + DQbench gates want CI / a cloud session (this box cannot run the full pytest
suite, DQbench, or the TS build/CI lane cleanly) — same posture as the native-core arc, which
shipped its TS + gate work from a cloud session.

## Deferred / non-goals

- **S2c** `SIMPLE_PLAN_MAX_PAIRS = 50M` (planner core constant) — revisit only after S1 makes pair
  counts trustworthy; a fixed pair ceiling is meaningless while the input is biased.
- **Chao1-cardinality-into-classification** — correcting the sample-scale `cardinality_ratio` in
  the classifier (S2a non-goal above).
- **TS measurement timing** — the TS profiler's `doubleton_block_count` measurement field; TS
  inherits the core kernels now and adds the field when its profiler is next touched.
- **Native measurement kernel** — a Rust kernel for the blocking measurement itself is a known
  perf wash-to-loss (Stage D); not reopened.
- **S4/S5** (multi-signal rule firing, LLM-escalation calibration) — out of scope for this spec.

## North Star alignment

- **Raise the zero-config floor** — S1 fixes the largest at-scale wrong-answer bug; S2/S3 sharpen
  classification and matchkey selection.
- **Answer-parity across scale** — S1 is precisely a scale-invariance fix.
- **Every surface** — every lever is a core kernel; Python/TS/SQL inherit it, proven by golden
  vectors.
- **Close the gap to an expert** — adaptive, data-shape-aware thresholds replace fixed magic
  numbers an expert would never hard-code.
- **Auditable** — deterministic kernels with golden vectors and benchmark deltas; no black box.
