# PR-C design — Fellegi–Sunter EM training in Rust + Arrow

**Epic:** `docs/superpowers/plans/2026-07-18-fs-rust-arrow-only.md` (goal 2).
**Goal:** replace the numpy EM trainer with a Rust+Arrow implementation so
`probabilistic.py` no longer `import numpy`. `bridge::train_em` today just
`prob.call_method("train_em", …)` — a Python-delegating shim. This makes the EM
*fit* actually native, byte-parity with the Python reference (kept as a test oracle).

**Non-goal:** scoring (PR-B) and blocking (PR-D) are separate. This PR is the model
fit only: `df + MatchkeyConfig + blocks → EMResult`.

## Why this is the hard PR
EM calibration is the repo's most regression-prone code (#1835 per-pass conditioning
→ #1836 revert → this session's threshold fix). A Rust port must reproduce the
*exact* decisions, including the near-unique-`u` fixed-prior guard, or it silently
re-breaks historical_50k. **Byte-parity is the acceptance bar, not "close enough."**

## What `train_em` must reproduce (full feature surface)

Output `EMResult`: `m_probs`, `u_probs`, `match_weights` (`log2(m/u)` per level),
`converged`, `iterations`, `proportion_matched`, `tf_freqs`, `tf_collision`,
`training_config`. Algorithm (from `core/probabilistic.py::train_em`):

1. **u from random pairs (fixed):** sample ≤5000 random pairs, build the
   comparison matrix, `u[level] = (count+1e-6)/total`. Splink posture — u is NOT
   EM-updated.
2. **Comparison vectors:** per field, apply transforms, run the scorer (jaro_winkler
   / levenshtein / token_sort / exact / soundex / date / embedding), bin the
   similarity into N discrete levels via `partial_threshold` / `level_thresholds`.
3. **Per-pair conditioning (#1835/#1836):** each blocked training pair carries the
   frozenset of fields the pass that emitted it blocked on. A field is
   `always_conditioned` (fixed ±3 prior, neutral u) iff it conditions EVERY sampled
   pair **OR** it is a configured blocking field whose near-unique `u` would collapse
   (the #1836 guard — the exact tension this session mapped). Non-conditioned fields
   are EM-learned; the E/M step masks out conditioned pairs per field.
4. **EM loop for m only:** init exponential prior (`2**k` per level), E-step
   posterior `P(match|vec)` (numpy `log_m`/`log_u`/softmax today), M-step re-estimate
   m per level from posteriors, iterate to `convergence` or `max_iterations`.
5. **Match weights:** `log2(m/u)` per level; fixed fields get linear `-3..+3`.
6. **Negative-evidence dims:** `__ne__<field>` — `[log2(m0/u0), 0.0]` (the
   agreement/inconclusive clamp), same per-pair conditioning.
7. **TF (Winkler) tables:** `tf_freqs`/`tf_collision` over the full column for
   `tf_adjustment=True` fields.
8. **Monotonicity guard:** warn/enforce non-decreasing weights (`GOLDENMATCH_FS_MONOTONIC`).
9. **Missing-value semantics** (`GOLDENMATCH_FS_MISSING`, #1834/#1846): level −1
   carries no evidence; toggleable.
10. **Linkage mode** (`target_ids`): two-table samples constrained to target×reference.

## Target architecture

- **`score-core` (pyo3-free reference):** add `em_core` — pure `f64`/`Vec` E/M loop,
  comparison-vector binning (reuses `score_one`), u/m estimation, weight/NE/TF/monotonic
  logic. NO Arrow, NO pyo3 — the numeric heart, unit-tested in Rust.
- **`native` crate:** `train_em_arrow(#[pyfunction])` — takes the sampled pairs +
  the field columns as **Arrow arrays** (zero-copy, reusing the `score_block_pairs_fs_arrow`
  column-intern path from PR-B), the `MatchkeyConfig` as JSON, and returns `EMResult`
  as JSON (mirrors the existing `bridge` JSON boundary). Sampling stays where it is
  (see determinism) — the kernel receives already-sampled pair ids + conditioning.
- **`bridge::train_em`:** stops delegating to Python; calls `train_em_arrow`.
- **Python:** `probabilistic.py::train_em` kept as the byte-parity **reference/oracle**
  behind `GOLDENMATCH_FS_EM_NATIVE=0` for one release; loader default routes to native.

## The three hard-parity problems

1. **Determinism of sampling.** `_sample_blocked_pairs_with_fields` sorts blocks by
   `block_key` then seeded-shuffles, caps per-block pairs, dedups conditioning by
   intersection. This MUST stay bit-identical. **Decision:** keep pair *sampling* in
   Python (it's cheap, polars-side, and the determinism contract is subtle); the Rust
   kernel receives the final `(pairs, per_pair_conditioning)` — so the port is the
   numeric EM, not the sampler. (Revisit moving the sampler to Rust in a later PR once
   Arrow blocking lands in PR-D.)
2. **Floating-point byte-parity.** The Python E/M step fixes accumulation order
   (`log_m`/`log_u` summed left-to-right j=0..n_fields, `1e-6`/`1e-10` smoothing,
   `math.log2`). Rust must match order + constants exactly. Risk: `f64` transcendental
   (`log`/`log2`/`exp`) libm differences. **Mitigation:** parity tolerance of 0 on
   *level/weight decisions* and ≤1e-9 on raw probs; assert the derived
   `match_weights` and the downstream pair *decisions* are identical (the thing that
   actually moves F1), not necessarily the 15th mantissa bit.
3. **The #1836 near-unique guard.** Reproduce exactly: a blocking field freed by
   per-pass conditioning but whose top-level random-pair `u` ≤ `1/n_random` keeps the
   fixed prior. This is the calibration cliff — it gets a dedicated fixture.

## Parity harness (acceptance bar)

`tests/test_em_native_parity.py` + a Rust unit suite. Fixture matrix, each asserting
native `EMResult` == Python reference (weights + decisions):
- discrete 2/3/N-level fields; multi-pass per-pair conditioning; **near-unique
  blocking field (#1836 postcode_fake shape)**; NE dims; TF fields; missing values
  (both `GOLDENMATCH_FS_MISSING` modes); linkage `target_ids`; monotonicity warn/enforce;
  the person-shape 0/5-blocking case from this session.
Plus the standing **`bench-probabilistic`** panel (historical_50k/febrl3/dblp_acm/
synthetic F1) and the auto-config quality gate — no per-dataset regression.

## Phasing (sub-PRs)
- **C1** `score-core::em_core` + Rust unit tests (no wiring; pure numeric parity).
- **C2** `native::train_em_arrow` + Python parity harness; loader `GOLDENMATCH_FS_EM_NATIVE`
  default 0 (native available, opt-in) → validate panel → flip default to native.
- **C3** `bridge::train_em` stops delegating; remove `import numpy` from
  `probabilistic.py` (trainer path); numpy reference moves under `tests/` or a
  `_reference` module imported only by the parity test.

## Rollout / gates / rollback
- Land C1→C3 as separate PRs; each green on FS unit + parity suites.
- Default flip (C2) only after `bench-probabilistic` green on the branch.
- `GOLDENMATCH_FS_EM_NATIVE=0` restores the Python trainer for one release (rollback).
- `native_symbols` gate: register `wrap_pyfunction!(self::train_em_arrow, m)` (`::`-qualified).

## Risks
- **Calibration drift** (highest) — mitigated by byte-parity fixtures incl. #1836.
- **libm transcendental divergence** — decision-level parity, not bit-15 parity.
- **Scope creep into the sampler** — explicitly out; sampler stays Python this PR.
- **Wheel republish** — a new depended-on native symbol needs the wheel rebuilt
  (the #688 skew class); `check_native_wheel` advisory covers it.
