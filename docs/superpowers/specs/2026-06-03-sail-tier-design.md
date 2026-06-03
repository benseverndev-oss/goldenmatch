# Sail tier — full spine-on-Sail, Sail-native distributed (replaces Ray)

**Date:** 2026-06-03
**Status:** design (approved by Ben; pre-spec-review)
**Parent:** `2026-06-01-arrow-native-finish-line-design.md` § "Gate reframe: engine
portability" + `2026-06-03-datafusion-spine-design.md` (the one-box spine). Stage E of
the spine recorded HONEST-NULL on one-box spill survival — the UF pair-collection island
dominates on one box — so **the real value is the distributed (Sail) path**, exactly as
Ben framed the arc ("DataFusion embedded one box + Sail distributed, later").

**Goal:** A Sail-native distributed entity-resolution pipeline — the spine's relational
plan (score → dedup → cluster → golden → identity) re-expressed against Sail and executed
across nodes — that **completes where the one-box pipeline cannot** and **scales out**.
It removes the in-memory Union-Find island (the one-box binding constraint) by computing
connected components distributed. The target **replaces** the existing Ray distributed
stack (`goldenmatch/distributed/`, Phases 1-6) once parity-green and the binding bench
passes.

---

## The reframe that shapes everything: Sail is Spark Connect, not datafusion-python

Sail (LakeSail, v0.6.x, May 2026) is a Rust drop-in Spark replacement built ON DataFusion
but **programmed via the Spark Connect protocol** — PySpark `DataFrame` / Spark SQL, with
Python / Pandas / **Arrow UDFs** (same conventions as Spark), plus UDAF/UDWF/UDTF. You
start a driver (`sail spark server --port 50051`, or K8s for cluster mode) and connect
with `SparkSession.builder.remote("sc://<driver>")`.

**Consequence:** the one-box spine's code does NOT port. `run_spine` is written against
the `datafusion` Python API (`SessionContext`, the Stage-B FFI `ScalarUDF` PyCapsule).
The Sail tier is a **re-expression of the same relational ALGORITHM against PySpark**, with
the native scorers rebound as Spark **Arrow UDFs**. The embedded one-box spine stays as-is;
Sail is the distributed sibling that shares the algorithm, not the code. Sail's own
compatibility checker is experimental and "does NOT verify behavioral parity" — so every
stage is **self-parity-gated** against the one-box spine / in-memory pipeline.

## Scope guard

IN: a new `goldenmatch.sail` package — distributed load → block → score → dedup → WCC →
golden (incl. custom field-rules) → identity, all Sail-native (Spark Connect); the native
scorer as an Arrow UDF; a connected-components implementation on Sail (the holdout); a
BYO-cluster binding bench at 100M+; retirement of the Ray distributed stack at the end.

OUT: bootstrapping/owning a Sail cluster (BYO, docs-not-bootstrap, mirrors the Ray phase5
posture); the embedded one-box spine (unchanged); flipping `mode` defaults (separate
decision); LLM/rerank/boost/NE/exotic matchkeys (same reduced surface as scale mode).

## Architecture — `goldenmatch.sail` (new module)

```
spark = SparkSession.builder.remote(os.environ["SAIL_REMOTE"]).getOrCreate()

load     df = spark.read.parquet(input)                         # distributed, lazy
block    df = df.withColumn("__block_key__", block_expr)        # soundex/exact, Spark cols
score    pairs = (df.alias("a").join(df.alias("b"),
                   on=(a.__block_key__==b.__block_key__) & (a.__row_id__<b.__row_id__))
                 .select(a.__row_id__, b.__row_id__,
                         score_udf(a.__value__, b.__value__).alias("score"))
                 .where("score >= threshold"))                  # distributed shuffle-join
dedup    pairs = pairs.groupBy("a","b").agg(max("score"))       # distributed
WCC      assignments = connected_components(pairs, all_ids)     # THE HOLDOUT (below)
golden   golden = assignments.join(df).groupBy("cluster_id").applyInArrow(survivorship)
identity edges  = emit per-cluster evidence edges; resolve against the identity store
```

Every stage is a Spark `DataFrame` op Sail plans + distributes; the driver never
materializes the full frame. This is the same relational shape as the one-box spine — the
difference is the engine owns partitioning/shuffle/spill across nodes, and **WCC is
computed distributed instead of collected to the driver** (removing the one-box island).

### Module layout (mirrors `goldenmatch/distributed/`, which it will replace)
- `sail/session.py` — `SparkSession` connect helper (`SAIL_REMOTE` env / arg), config.
- `sail/scoring.py` — block self-join + the scorer Arrow UDF + dedup.
- `sail/scorers.py` — native scorer as a Spark Arrow UDF (decision 2).
- `sail/clustering.py` — connected components on Sail (decision 1).
- `sail/golden.py` — survivorship as `applyInArrow` group aggregation (incl. custom rules).
- `sail/identity.py` — distributed edge emit + resolve.
- `sail/pipeline.py` — `run_sail_pipeline(input, config)` threading the stages.

## The three load-bearing decisions

### Decision 1 — Connected components on Sail (the holdout, the S2 gate)
Sail/Spark Connect has NO native connected-components (no GraphFrames). **Port the proven
in-repo two-phase WCC** (`distributed/clustering.py::two_phase_wcc`, Iverson et al.):
- **Phase A** — per-partition local Union-Find via `mapInArrow` / an Arrow UDTF over each
  partition's edge batch (embarrassingly parallel).
- **Phase B** — cross-partition boundary-edge merge via a driver-side super-graph Union-Find
  over only the boundary components (small: bounded by #partitions × boundary degree).

Rationale: reuses the ALGORITHM already validated at 50M chain-adversarial scale (Ray,
run 26172859442: 120.4s), and the GraphFrames maintainer explicitly endorsed two-phase /
randomized-contraction over label-propagation (chains are label-prop's worst case;
ER graphs are chain-heavy — see memory `reference_graphframes_maintainer_advice`).
**This is a re-expression, NOT code reuse:** the existing `two_phase_wcc` is written against
the Ray Dataset API (`map_batches`, `ray.put`, `from_arrow`) and `core.cluster.UnionFind`;
on Spark Connect the Phase A partition-UF (`mapInArrow`/UDTF) and the Phase B driver-side
merge are new code that shares the algorithm and is parity-tested against the one-box
reference (same posture as the scorer in decision 2).
**Carry forward the WCC-rehydration-OOM lesson (the load-bearing trap):** seed the
isolated/singleton nodes from a DISTRIBUTED frame (a Spark `DataFrame` of `__row_id__`s
left-joined to assignments), NOT a driver-side `list[int]` of every record id — the parent
spine spec flags this exact trap for `all_ids: list[int]`, and the Ray Phase B seeds
isolated nodes from `all_ids`, so the naive port would silently reintroduce the OOM at
100M. The driver must never hold a per-record Python list.
**Fallback if `mapInArrow` partition-UF can't be expressed on Sail v0.6.x:** large-star /
small-star iterative connected-components as Spark SQL self-joins (more shuffles, but pure
relational). **If neither works, S2 fails the gate → escalate** (the Sail-native-everything
premise is then in question; Ray is NOT retired).

### Decision 2 — Native scorer as a Spark Arrow UDF
Rebind the existing `score-core` rapidfuzz kernel (already compiled into `_native` /
`goldenmatch_native`) as a **vectorized Spark Arrow UDF** (Sail runs Arrow UDFs zero-copy).
Reuses the proven kernel, zero new Rust. **Fallback:** a pure-Python `rapidfuzz` Arrow UDF
(no native dep, slower) — kept as the always-available path and the parity reference (it IS
rapidfuzz; `test_native_parity` proves rust==python rapidfuzz at 1e-9).

### Decision 3 — Staged build (full target, load-bearing-risk-first)
The target is the full Sail-native pipeline replacing Ray, but the BUILD is staged and each
stage is gated on the prior's measured result (mirrors the spine's A→E and Ben's
scale-substrate rule). See "Stages" below.

## Stages (each independently testable; each a gate)

- **S1 — Sail harness + score/dedup.** `sail/session.py` + `sail/scorers.py` (Arrow UDF) +
  `sail/scoring.py`. GATE: against a local `sail spark server`, the score+dedup output
  matches the one-box spine's `raw_pairs` set on a fixture (parity); the scorer UDF equals
  python rapidfuzz (1e-9).
- **S2 — WCC on Sail (THE GATE).** `sail/clustering.py` (two-phase WCC, decision 1). GATE:
  the cluster PARTITION is identical (Rand 1.0) to the one-box spine's `build_cluster_frames`
  on a fixture INCLUDING a chain archetype (label-prop's worst case) and a cascading-split
  archetype (the runtime-bug class that bit the one-box frames-out work). The one-box
  reference partition for these fixtures is CAPTURED/pinned (Stage C of the spine already
  produces it) so S2 isn't diffing against a moving target. If WCC can't be made correct +
  distributable on Sail, STOP and escalate.
- **S3 — golden + identity on Sail.** `sail/golden.py` (survivorship incl. custom field-rules
  via `applyInArrow`) + `sail/identity.py`. GATE: golden content parity per multi-member
  cluster; identity edge-set parity vs the one-box path.
- **S4 — binding multi-node bench + Ray retirement.** `run_sail_pipeline` end-to-end on a
  BYO multi-node Sail cluster at 100M+ rows. GATE (kill criterion): completes where the
  one-box pipeline cannot, per-node peak RSS bounded (< node memory), wall scales across
  nodes. ONLY on PASS: deprecate + remove `goldenmatch/distributed/` (the Ray stack) in a
  one-release window.

## Verification

- **CI smoke (every stage):** a local `sail spark server` (single process) powers
  small-scale parity tests in a dedicated lane (install `pysail` + `pyspark` client). No
  cluster needed for correctness — Sail runs the same plan locally.
- **Binding bench (S4):** `workflow_dispatch`, BYO cluster via a `SAIL_REMOTE` secret
  (mirrors the Ray phase5 `RAY_ADDRESS` pattern — docs not bootstrap). 100M dataset reused
  from the phase5 generator. Commit numbers to the roadmap.
- **Parity is the gate, not bit-identity:** scale-mode semantics (deterministic +
  semantically correct, not bit-identical; MAX dedup) carry over. Compare partition SETS,
  golden content, edge sets — never raw float equality.

## Kill criterion (the whole tier)

100M-row end-to-end on a multi-node Sail cluster: **completes where one-box OOMs/can't**,
per-node RSS bounded, wall improves with node count. If S2 (WCC) can't be made native, or
S4 doesn't bind, the honest output is "Sail is not yet the substrate" — Ray stays, and the
arc reassesses. No Ray retirement, no default-flip, until S4 is green.

## Risks

- **WCC-on-Sail (S2)** — the load-bearing unknown; no native graph in Sail. Two-phase WCC
  port is the plan; large-star/small-star is the fallback; escalate if both fail.
- **Sail v0.6.x behavioral parity** — their compat checker doesn't verify it; we self-parity
  -gate every stage against the one-box spine.
- **Spark-Connect re-expression cost** — this is a parallel implementation, not a port;
  the algorithm is shared (and tested against the one-box reference), the code is new.
- **Replacing Ray is multi-subsystem** — staged S1→S4, Ray stays default until S4 binds.
- **Arrow UDF perf on Sail** — measure the scorer UDF throughput; pure-Python fallback is
  the floor, the native-kernel Arrow UDF is the target.

## What this explicitly does NOT do

- Bootstrap/own a Sail cluster (BYO). Touch the embedded one-box spine. Flip `mode`
  defaults. Retire Ray before S4 binds. Support LLM/rerank/boost/NE/exotic matchkeys.
