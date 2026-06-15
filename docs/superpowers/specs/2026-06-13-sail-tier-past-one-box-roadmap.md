# Sail tier — "past one box" roadmap (from scaffolded → proven multi-node)

**Date:** 2026-06-13
**Status:** roadmap (planning artifact — sequences remaining work; not a build spec)
**Parent:** `2026-06-03-sail-tier-design.md` (the S1→S4 stage plan) +
`2026-06-03-datafusion-spine-design.md` (the one-box spine Stage E hands off here)
**Lane:** strategic — an *additive* distributed substrate that completes where the one-box
spine OOMs. This roadmap takes the Sail tier from *scaffolded + one-box-parity-gated*
to *proven multi-node, default-eligible*. **Scope amended 2026-06-15: Sail is additive,
NOT a Ray replacement** ([../../../context-network/decisions/0004-sail-tier-scope.md](../../../context-network/decisions/0004-sail-tier-scope.md)) — Ray clustering stays the default; this roadmap's
"Ray-retired" framing is superseded throughout.

---

## Why this exists

The one-box DataFusion spine proved out-of-core spill for the RELATIONAL stages,
but its Union-Find break collects pairs to the driver (scipy.csgraph) — an in-memory
island that caps it at ~50M pairs. "Past one box" means removing that island:
distribute the WCC across nodes. The chosen substrate is **Sail** (LakeSail, driven
via the Spark Connect protocol), NOT a second engine. The thread that prompted this
roadmap (Apache Ballista) is the *fallback*, not the path — see the last section.

## Current state (real, on `main`)

`goldenmatch/sail/` is built and gated against the one-box spine on tiny fixtures —
but **nothing has executed on more than one box.** That single fact is the gap.

| Stage | Scope | File(s) | Status |
|---|---|---|---|
| **S1** | harness + score/dedup | `session.py`, `scoring.py`, `scorers.py` | built — scorer is the **pure-Python rapidfuzz `pandas_udf` FLOOR**, not the native Arrow UDF target |
| **S2** | WCC on Sail (the holdout) | `clustering.py` (`connected_components`, `connected_components_scale` pointer-jumping) | built, partition-parity-gated vs reference UF |
| **S3** | golden + identity | `golden.py`, `identity.py` | built (survivorship + identity-graph builders) |
| **S5** | identity API freeze → 1.31 | `IdentityGraphFrames` | in flight (#859 / PR #889) |
| **S4** | binding multi-node bench (additive) | `pipeline.py::run_sail_pipeline` | **NOT run — THE gate** |

The tier's own kill criterion stands: *no default-flip until S4 is green* (Ray retirement
dropped per the 2026-06-15 amendment — Ray stays regardless). Everything below is what makes
an S4 green both *reachable* and *trustworthy*.

## The roadmap

The critical path is one item — **S4** — plus the prerequisites that make it a fair,
trustworthy test. Sequenced:

### R0 — Land S5 (in flight). Release 1.31 with the frozen `IdentityGraphFrames` API
(#859 / #889). Off the critical scale path, but it stabilizes the S3 output contract
S4 benches against and unblocks the showcase. No new work here — just land it.

### R1 — Native scorer Arrow UDF (the PERF prerequisite). ✅ landed in this change
**Done.** Added the dedicated vectorized kernel `score::score_field_pairwise`
(`packages/rust/extensions/native/src/score.rs`): two equal-length Arrow string
arrays in, a contiguous float32 array out, scored row-parallel via the shared
`score-core` `score_one` in ONE FFI crossing (no per-element Python loop, no N*N
matrix). `sail/scorers.py` now routes through it: `score_batch` /
`_native_scores` prefer the kernel when `native_enabled("sail_scoring")`, falling
back to the pure rapidfuzz floor per-batch on any FFI/pyarrow hiccup or absent
wheel. **Default-off** (`sail_scoring` is NOT in the loader's `_GATED_ON`, so
`auto`/unset = pure floor, `GOLDENMATCH_NATIVE=1` = native) until the parity
battery is green on the PUBLISHED wheel -- the loader's documented promotion rule.
- **Measured (this box, `scripts/bench_sail_scorer_native.py`, 200K pairs, len 6-18):**
  jaro_winkler 1.12x / levenshtein 1.13x / token_sort 1.53x over the pure floor,
  parity max|native-pure| ~1e-8 (f32 epsilon). The win is a LOWER bound -- the
  bench builds Arrow arrays from Python lists; a real `pandas_udf` gets a Series,
  so `pa.array(series)` is cheaper, and the gap grows with string length.
- **Reuse-of-`score_block_pairs_arrow` was tried first and REJECTED** (measure-first):
  shoehorning size-2 blocks scored at 0.35-0.51x -- the Python interleave + tuple
  materialization lost to rapidfuzz's C loop. The dedicated contiguous-array kernel
  is what clears the gate.
- **Parity gate:** `tests/test_sail_scorer_native_parity.py` (native == pure floor
  @ f32 ε across the 3 scorers; flag routing; identical-string 1.0; length-mismatch
  raises). Gates on the native kernel, skips in pure-only envs.

`sail/scorers.py` ships the *floor*: pure-Python rapidfuzz in a `pandas_udf`. The
parent design's Decision 2 *target* is the existing `score-core` rapidfuzz kernel
(already compiled into `_native` / `goldenmatch_native`) rebound as a **vectorized
Spark Arrow UDF** (Sail runs Arrow UDFs zero-copy), with the pure-Python UDF kept as
the always-available fallback + parity reference (`rust==python rapidfuzz @ 1e-9`).
- **Why it gates S4:** benching the floor measures Python-UDF overhead, not the
  engine — you would retire Ray on a number that does not represent the ceiling.
- **Crosses the executor boundary as an Arrow UDF (the Spark protocol)** — more
  proven than Ballista's FFI-ScalarUDF-across-scheduler path, which is exactly why
  Sail (not Ballista) is the substrate.
- **Gate:** UDF scores equal the in-process native scorer for a string-pair fixture
  (ε for f32); throughput beats the pure-Python floor on a single-process bench.

### R2 — Multi-node determinism gate (the TRUSTWORTHY prerequisite). ✅ landed in this change
The spine has Stage D (determinism across `target_partitions` {1,3,N}); the Sail tier
needs the same across *executors*: assert the emitted pair **SET** and the cluster
**PARTITION** are invariant to the number of shuffle partitions Sail fans the plan
across (`spark.sql.shuffle.partitions`). A green S4 on a partition-count-sensitive
pipeline measures luck, not the engine.
- **Fixture discipline (carried from Stage D):** every within-block pair scores 1.0
  (0.15 margin over the 0.85 threshold) so NO pair sits within f32-ULP of the cutoff
  — the gate measures determinism, not threshold flapping.
- **Delivered as** `tests/test_sail_determinism.py` (skips without the `sail` extra,
  same convention as the S1/S2 parity tests). Covers WCC alone (both algorithms) and
  the full score→dedup→WCC path.

### R3 — Coverage / feature-gate honesty. ✅ landed in this change
**Done.** `pipeline._validate_sail_pipeline_supported` errors up-front (the scale-mode
posture) on the two real silent-degrade cases: an unsupported `scorer_name`
(LLM/rerank/boost/NE/embedding/cross-encoder — `NotImplementedError`) and an unrecognized
`wcc` (was a *silent* fall-through to label-prop — now `ValueError`). Survivorship
`strategy` was found to already fail-loud (`core.golden.merge_field` raises on unknown), so
it is not re-checked. Widening to multi-field weighted matchkeys stays deferred (the
explicit-error route is the R3 deliverable). Test: `tests/test_sail_r3_feature_gate.py`
(pure-Python, runs every lane).

### R4 — S4: the binding multi-node bench (THE gate).
`workflow_dispatch`, BYO multi-node Sail cluster via a `SAIL_REMOTE` secret, the
phase5 50M/100M parquet from the bench generator.
- **Kill criterion:** completes where one-box OOMs/can't, per-node RSS bounded, **wall
  improves with node count.** Commit the numbers back to this roadmap. (Verdict is "Sail
  proven as an *additive* multi-node option" — NOT a Ray retirement; see the scope
  amendment 2026-06-15.)
- **The structural win:** distributed WCC (S2) removes the ~50M scipy-UF island that capped
  the spine's Stage E. **The S2 100M lineage TODO is now landed:**
  `connected_components_scale(checkpoint_interval=, checkpoint_dir=)` truncates the
  pointer-jump lineage via a per-round parquet barrier (default-off, byte-identical; the
  bench enables it). Large-star/small-star stays deferred — pointer-jumping's O(log n)
  should suffice; revisit only if a real 100M chain run shows too many rounds.
- **Dependency:** a live multi-node cluster — a BYO/ops dependency, not code.

### R5 — `backend="sail"` opt-in surface (ONLY after R4 is green).
Add `backend="sail"` to the planner/public surface as an **additional** distributed path +
document the BYO-cluster deploy recipe. **Ray is untouched** (no retirement, no "legacy"
flip — amended 2026-06-15). Forbidden before R4 binds (the parent design's rule).

## Dependency order

```
R0 (1.31 release) ───────────────┐
R1 (native Arrow UDF) ──┐        │
R2 (determinism gate) ──┼──► R4 (multi-node bench) ──► R5 (backend=sail opt-in)
R3 (coverage gate) ─────┘        │
                                 └─ R0 stabilizes the S3 contract R4 benches
```

R1, R2, R3 are independent and parallelizable; all three precede R4. R2 and R3 are
buildable + CI-testable WITHOUT a cluster (local single-process `sail spark server`);
R1 is buildable but its throughput claim wants a real bench; R4 needs the cluster.

## Ballista (the thread origin): fallback, not path

Ballista is the *other* distributed-DataFusion option. The roadmap's stance: it
re-opens the exact UDF-across-cluster risk that Sail's Arrow-UDF route (R1) already
de-risks, and the team is 3-of-4 stages into Sail. Ballista re-enters ONLY if S2 (WCC)
or S4 (the bind) fails the gate — the parent design's "Sail is not yet the substrate"
branch. Neither has been run to fail. The minimal Ballista re-probe, if it ever comes,
is one existing native scorer registered as an `FFI_ScalarUDF` on a 2-executor cluster
running a block self-join — purely to answer whether our FFI UDFs survive the
scheduler/executor boundary at the pinned DataFusion major. That is the only question
Ballista can answer that Sail cannot.
