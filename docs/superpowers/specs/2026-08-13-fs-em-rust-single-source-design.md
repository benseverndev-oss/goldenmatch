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

### Phase 2 -- term-frequency adjustment on the distributed path *(done)*

**The open question is resolved, and not the way this spec first guessed.**
Verified 2026-08-14 against both sources rather than inferred:

* **Splink** (`em_training_session.py`): `estimate_without_term_frequencies`
  defaults to **`False`**, so by default TF *does* join its E-step, through
  `predict_from_comparison_vectors_sqls(training_mode=True)`. Its
  agreement-pattern-counts path is the `True` branch. So counted training and
  TF-in-training are **mutually exclusive there too** -- that flag is the
  switch between them.
* **GoldenMatch** (`core/probabilistic.py::_em_iterate`): contains **zero**
  references to tf. TF is a purely SCORING-time adjustment here; training only
  *builds* the table (`_build_tf_tables`) and stores it on the `EMResult`,
  where `backends/score_buckets.py` reads it.

So the earlier framing -- "Splink applies TF at scoring time, which would make
it orthogonal" -- was half right for the wrong reason. It is not orthogonal for
Splink. It is orthogonal **for us**, because we never put TF in training at all.

What the counted path actually could not do was narrower than the refusal
claimed: it cannot *derive* a TF table, because collapsing identical comparison
vectors is exactly what discards the values. It never needed to. The table is a
property of the SOURCE column, so `spark.em.tf_value_frequencies` recovers it
with a separate `GROUP BY` -- the distributed twin of `_build_tf_tables`,
mirroring `core.tf_tables.value_frequencies` including the detail that the
denominator counts SURVIVING values, not rows.

`train_em_from_counts` now takes `tf_freqs` / `tf_collision` rather than
refusing, and refuses only when TF is configured and no table is supplied (and
rejects a table for a field that never opted in). `train_em_distributed`
computes them once and carries them onto every session.

**Bound:** a TF table has no `prod(levels + 1)` ceiling -- one entry per
distinct VALUE -- so it gets its own `MAX_TF_VALUES` (1,000,000) with a message
saying plainly that this is a driver-memory limit, not a property of the field.
The one-box builds the same dict from its own frame.

**Still not supported:** negative evidence, which sat in the same guard and is
a different problem -- it needs a per-pair matrix nothing outside the pair loop
can reconstruct.

### Phase 3 -- backends *(partially done; the premise was wrong)*

**Correction to this spec.** Phase 3 was written as "counting is the only
per-engine piece, and `agreement_pattern_counts` is ~40 lines of SQL --
DataFusion and DuckDB versions are the same `GROUP BY` over the same gamma
expressions". Checked 2026-08-14: **the gamma expressions do not exist outside
Spark.** `fs_level_expr` and `_field_similarity_and_observed` live only in
`goldenmatch/spark/probabilistic.py`; there is no engine-neutral SQL form of the
level ladder anywhere in the repo.

So porting the counting to another engine is not a `GROUP BY` -- it is writing a
**second implementation of what a level MEANS**, which is exactly what
`gamma_columns`'s own docstring warns against ("a training run that disagreed
with scoring about levels would produce weights for a partition of the data that
scoring never reproduces"). That is the duplication this whole arc exists to
remove, so doing it per engine would be self-defeating.

**Delivered instead:** the counted TRAINER on DuckDB
(`goldenmatch_train_em_from_counts`), matching the Postgres surface from Phase
1c. Both SQL backends now train from vectors their own engine counted, using
whatever `GROUP BY` the caller writes. The DuckDB UDF is Python (every UDF in
that package is, by construction) -- what it gains is the counted SHAPE, an
input that is not a sample, not freedom from the interpreter.

**What a full Phase 3 still needs**, and it is a design job rather than a port:
extract the level ladder + per-field similarity into an engine-neutral form both
`spark/probabilistic.py` and a DuckDB/DataFusion emitter can render, so
`gamma_columns` has one definition per surface instead of one per engine. Until
that exists, "counting on N backends" means N copies of the calibration rules
this arc keeps finding bugs in.

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
- ~~**TF may not be orthogonal.**~~ RESOLVED 2026-08-14, and the risk was real:
  TF is NOT orthogonal for Splink (its `estimate_without_term_frequencies`
  defaults to `False`, putting TF in the E-step). It is orthogonal for us only
  because `_em_iterate` never touches tf. Had that not held, the counting key
  would have needed frequency bands and Phase 2 would have been a different
  piece of work. Verified against both sources before designing, which is the
  only reason the cheap answer was trustworthy.
