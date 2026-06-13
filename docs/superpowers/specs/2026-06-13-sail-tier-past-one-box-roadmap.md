# Sail tier — "past one box" roadmap (from scaffolded → proven multi-node)

**Date:** 2026-06-13
**Status:** roadmap (planning artifact — sequences remaining work; not a build spec)
**Parent:** `2026-06-03-sail-tier-design.md` (the S1→S4 stage plan) +
`2026-06-03-datafusion-spine-design.md` (the one-box spine Stage E hands off here)
**Lane:** strategic — the distributed substrate that completes where the one-box
spine OOMs. This roadmap takes the Sail tier from *scaffolded + one-box-parity-gated*
to *proven multi-node, Ray-retired, default-eligible*.

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
| **S4** | multi-node bench + Ray retirement | `pipeline.py::run_sail_pipeline` | **NOT run — THE gate** |

The tier's own kill criterion (from the parent design) stands: *no Ray retirement,
no default-flip, until S4 is green.* Everything below is what makes an S4 green both
*reachable* and *trustworthy*.

## The roadmap

The critical path is one item — **S4** — plus the prerequisites that make it a fair,
trustworthy test. Sequenced:

### R0 — Land S5 (in flight). Release 1.31 with the frozen `IdentityGraphFrames` API
(#859 / #889). Off the critical scale path, but it stabilizes the S3 output contract
S4 benches against and unblocks the showcase. No new work here — just land it.

### R1 — Native scorer Arrow UDF (the PERF prerequisite).
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

### R3 — Coverage / feature-gate honesty.
`run_sail_pipeline` is single `block_col` + single `value_col` + `most_complete`/
`first`. Before any default-eligible claim: either widen to multi-field weighted
matchkeys, or **explicitly error** on unsupported scorers (LLM/rerank/boost/NE/exotic)
the way scale-mode does — never silently degrade. Pure routing; does not block R4.

### R4 — S4: the binding multi-node bench (THE gate).
`workflow_dispatch`, BYO multi-node Sail cluster via a `SAIL_REMOTE` secret, the
phase5 50M/100M parquet from the bench generator.
- **Kill criterion:** completes where one-box OOMs/can't, per-node RSS bounded, **wall
  improves with node count.** Commit the numbers back to this roadmap.
- **The structural win over the spine:** distributed WCC (S2) removes the ~50M
  scipy-UF island that capped the spine's Stage E. NOTE the S2 in-code TODO — at 100M,
  `connected_components_scale` must cache/checkpoint `labels` each round (Spark Connect
  lineage growth) and the long-chain convergence wants large-star/small-star; verify
  before the bind, not after a hang.
- **Dependency:** a live multi-node cluster — a BYO/ops dependency, not code.

### R5 — Ray retirement + wiring (ONLY after R4 is green).
Add `backend="sail"` to the planner/public surface, flip Ray to legacy, document the
BYO-cluster deploy recipe. Forbidden before R4 binds (the parent design's rule).

## Dependency order

```
R0 (1.31 release) ───────────────┐
R1 (native Arrow UDF) ──┐        │
R2 (determinism gate) ──┼──► R4 (multi-node bench) ──► R5 (Ray retire + wire)
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
