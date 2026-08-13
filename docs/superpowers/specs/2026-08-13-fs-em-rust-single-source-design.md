# Fellegi-Sunter training: one implementation, served everywhere

**Date:** 2026-08-13
**Status:** design; Phase 0 in progress
**Supersedes nothing.** Resumes PR-C of
`docs/superpowers/plans/2026-07-18-fs-rust-arrow-only.md`, which built its
numeric core and was never wired.

---

## The finding

The Fellegi-Sunter EM loop exists **three times** in this repo, and the copy
designated as the source of truth is the only one nothing calls.

| implementation | covers | callers |
| --- | --- | --- |
| `packages/python/goldenmatch/goldenmatch/core/probabilistic.py` (`train_em`, `_em_iterate`) | NE dims, TF tables, monotonicity guard, missing-value modes, weighted/counted rows | every Python surface, and `bridge::train_em` |
| `packages/typescript/goldenmatch/src/core/probabilistic.ts` (`trainEM`) | NE dims; no counted path | the TS surface |
| `packages/rust/extensions/score-core/src/em_core.rs` (`train_em_core`) | the discrete E/M loop only | **none** |

Verified 2026-08-13: `grep -rn "train_em_core\|EmOutput\|EmParams"` across
`*.rs`, `*.py`, `*.ts` returns nothing outside `em_core.rs` itself.
`score-core/src/lib.rs` does `pub mod em_core;` and stops there.

Meanwhile `packages/rust/extensions/bridge/src/api.rs::train_em` is a pyo3 shim
that **embeds CPython** and calls
`goldenmatch.core.probabilistic.train_em`. Every non-Python surface that wants
FS training therefore reaches Python, which is the inverse of
[[project_rust_is_the_reference]]: the Rust kernel is supposed to be the
reference and Python the lossy fallback.

`em_core.rs`'s own header lists what it does not cover -- "negative-evidence
dims, TF (Winkler) tables, the monotonicity guard, missing-value modes, and the
two-table linkage sampling". So the reference is also the **least complete** of
the three.

### This got worse on 2026-08-13

The distributed-training work (#2550, #2552) added to Python only:

- `pair_weights` on `train_em` (weighted M-step)
- `train_em_from_counts`
- `estimate_u_from_counts`
- `_neutral_u_for`
- `_combine_em_sessions`

`em_core.rs` already contains `neutral_u` with byte-identical constants
(`[0.5, 0.5]`, `[0.34, 0.33, 0.33]`, uniform) and already estimates u as
`(count + SMOOTH) / (observed + n_levels * SMOOTH)`. Both rules now exist twice,
in two languages, and the second copy was added to the fallback rather than to
the reference. That is the drift this spec exists to stop.

---

## What belongs where

Not everything should move to Rust, and the line matters.

| layer | belongs in | why |
| --- | --- | --- |
| gamma / comparison vectors | **Rust kernel** (already: `score_one` via pyo3 / cabi / jni / wasm) | per-pair math over strings |
| `GROUP BY` over gammas | **the engine** (Spark, DataFusion, DuckDB) | distributed aggregation is what an engine *is*; a Rust kernel here reimplements Spark badly |
| EM iteration | **Rust kernel** | pure numerics over a bounded matrix |
| u from counts | **Rust kernel** | pure numerics |
| session combination | **Rust kernel** | pure numerics |
| per-pass orchestration | host language | glue over engine calls |

The bound is what makes this safe: a counted comparison-vector table has at most
`prod(levels + 1)` rows -- thousands, whatever the pair count -- so the whole
numeric half fits in one kernel call with no streaming and no I/O.

Everything downstream of the `GROUP BY` is therefore **one kernel's worth of
code**, and it is exactly the code that currently exists three times.

---

## Parity is decision-level, not bitwise

The 2026-07-18 plan says "byte-parity". That is not achievable and
`em_core.rs`'s own test helper already says why:

> libm ln/log2/exp differ from CPython in the low mantissa bits

The established tolerances stay: **1e-9 on probabilities, 1e-7 on match
weights** (weights amplify small `m` near the `1e-10` floor). Any new gate uses
the same numbers, and a gate claiming exact equality would be a gate that has to
be loosened the first time it runs on a different libm.

---

## Phases

### Phase 0 -- stop the divergence *(in progress)*

Port the 2026-08-13 Python additions into `em_core.rs`:

- weighted rows (a count per comparison vector)
- `train_em_from_counts_core`
- `estimate_u_from_counts_core`
- `combine_em_sessions_core`

**Gate:** a **committed** Python emitter writes a fixture matrix; a Rust test
reads it and reproduces it within tolerance. This is deliberately unlike the
existing `em_core.rs` anchors, which were hand-pasted from "the C1 commit
message / scratch script" -- an uncommitted scratch script is not a reproducible
gate ([[feedback_commit_the_measurement_harness]]). The fixture must include the
#1836 near-unique-blocking case, which is the one that fails silently.

**Non-goal:** wiring. Phase 0 adds no callers. On its own it is a *fourth* copy;
it is only worth landing because Phase 1 follows immediately.

### Phase 1 -- wire it *(the load-bearing phase)*

- export `train_em_counts` over C-ABI, wasm and JNI
- `bridge::train_em` stops embedding CPython
- Python `train_em_from_counts` and TS `trainEM` become thin callers over the
  kernel, each keeping its pure fallback (the existing `_native_loader` pattern)

**Gate:** the existing FS parity suites pass with the kernel path forced on, and
with it forced off. **Rollback:** `GOLDENMATCH_FS_EM_NATIVE=0` for one release,
matching PR-C's stated posture.

Three implementations become one here. Phase 0 without Phase 1 is strictly worse
than doing neither.

### Phase 2 -- term-frequency adjustment on the distributed path

The largest real gap against Splink. Verified against Splink's
`internals/term_frequencies.py` on 2026-08-13: a TF table per column
(`count(*) / total`), left-joined to the input, then
`log2(u_probability/tf) * tf_adjustment_weight AS log2_bf_tf` -- **all backend
SQL**. `train_em_distributed` currently refuses TF outright.

**Open question, to settle before designing:** TF is per-*value*, so it does not
fit the counted-vector key -- collapsing identical vectors discards the values
the adjustment needs. Splink appears to apply TF as a **scoring-time** Bayes-factor
term rather than folding it into training, which would make it orthogonal to
counted training and far cheaper than it looks. **Verify this against their
source before designing**; if it is wrong, the counting key has to carry
frequency bands and the cost changes completely.

### Phase 3 -- backends

Once the numerics are one kernel, counting is the only per-engine piece, and
`spark/em.py::agreement_pattern_counts` is ~40 lines of SQL. DataFusion and
DuckDB versions are the same `GROUP BY` over the same gamma expressions.
Splink's five backends stop being five ports of a trainer.

### Phase 4 -- the benchmark

Nothing above earns the words "better" or "faster". Head-to-head against Splink
on a shared dataset -- accuracy **and** wall-clock -- on DuckDB and Spark.
Until this exists, no comparative claim about Splink should be made in docs, a
README, or a release note.

---

## Non-goals

- Moving the `GROUP BY` into Rust. It belongs in the engine.
- Removing polars from FS candidate generation. That is PR-D of the 2026-07-18
  plan and is independent of this.
- Changing any trained model's numbers. Every phase here is a relocation; the
  gates exist to prove that.

---

## Risks

- **A fourth copy.** Phase 0 in isolation makes the problem worse. Do not land
  it without Phase 1 following.
- **Silent calibration drift.** #1835/#1836 are the minefield: a blocking
  field's `u` collapsing toward the smoothing floor explodes `log2(m/u)` and
  produces a model that is wrong only in the cells hardest to eyeball
  (measured F1 0.83 -> 0.57). Every fixture matrix carries that case.
- **TF may not be orthogonal.** See the open question in Phase 2. If it is not,
  Phase 2's cost estimate is wrong by a lot.
