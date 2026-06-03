# Sail Tier (distributed, Sail-native — replaces Ray)

The distributed sibling of the one-box DataFusion spine. Re-expresses the spine's
relational plan against **Sail** (LakeSail — a Rust Spark drop-in built on DataFusion,
programmed via **Spark Connect / PySpark**) to run across nodes, computes connected
components distributed (removing the one-box UF island), and ultimately retires the
existing Ray distributed stack.

**Status:** S1+S2+S3(golden) SHIPPED 2026-06-03 (PRs #709, #712, #714);
identity stage + S4 next. The make-or-break WCC-on-Sail risk is CLOSED.
**Spec:** `docs/superpowers/specs/2026-06-03-sail-tier-design.md`. **Plans:**
`docs/superpowers/plans/2026-06-03-sail-tier-stage-{s1,s2,s3}.md`.
**Why it matters:** Stage E showed one-box spill-survival is non-binding
([../decisions/0003-stage-e-spill-honest-null.md](../decisions/0003-stage-e-spill-honest-null.md))
— the distributed path is where the value is.

## The defining constraint
Sail is **Spark Connect (PySpark DataFrame/SQL)**, not the `datafusion` Python API. So the
one-box `run_spine` does NOT port — the Sail tier is a **re-expression of the same
algorithm** in a new `goldenmatch.sail` package, with native scorers rebound as Spark
**Arrow UDFs**. Shared algorithm, new code, self-parity-gated against the one-box spine
(Sail's own compat checker doesn't verify behavioral parity).

## Three load-bearing decisions (see the spec for detail)
1. **WCC on Sail (the holdout — Sail has no native graph):** port the proven two-phase
   WCC to Spark Connect (Phase A partition-UF via `mapInArrow`; Phase B driver-side
   boundary merge). Fallback: large-star/small-star SQL. **Trap:** seed isolated nodes
   from a distributed frame, NOT a driver-side `list[int]` (the WCC-rehydration OOM).
2. **Scorer:** native `score-core` kernel as a Spark Arrow UDF; pure-Python rapidfuzz
   Arrow UDF as the floor + parity reference.
3. **Staged build** S1→S4, WCC as the gate; Ray stays default until S4 binds.

## Relationship to existing code
- **Replaces** `goldenmatch/distributed/` (the Ray Phases 1-6) — but only after S4's
  binding 100M+ multi-node bench passes (one-release deprecation window).
- **Parallels** `backends/datafusion_spine.py` (the one-box spine — unchanged; it is the
  parity reference for S1/S2/S3).
- Reuses the algorithm of `distributed/clustering.py::two_phase_wcc` (re-expressed) and
  the `score-core` kernel (rebound as an Arrow UDF).

## Stage status
- **S1 — SHIPPED (PR #709).** `goldenmatch[sail]` extra; `goldenmatch.sail` (session.connect,
  scorers.make_scorer_udf = rapidfuzz pandas_udf, scoring.score_and_dedup = block self-join +
  UDF + GROUP BY max). New path-filtered `sail` CI lane (in-process Sail server). Green gates:
  Spark Connect connectivity + score/dedup pair-set parity vs python-rapidfuzz. NO Java needed
  (pyspark[connect] is pure gRPC); open-ended versions worked.
- **S2 — SHIPPED (PR #712), the make-or-break gate.** `clustering.connected_components` computes
  the Union-Find holdout DISTRIBUTED on Sail via min-label propagation (pure Spark Connect joins),
  partition-parity-green vs reference UF (chain + junction + singleton). Existential "WCC-on-Sail"
  risk CLOSED. Deliberate spec deviation: led with label-prop (correctness gate); large-star/
  small-star is an S4 prerequisite (label-prop is O(diameter) on 100M chains).
- **S3 (golden) — SHIPPED (PR #714).** `golden.build_golden` = distributed survivorship via
  `groupBy + collect_list` + a scalar pandas UDF calling the one-box `merge_field` (parity by
  construction). Content-parity-green per multi-member cluster. SCOPE: golden only; **identity
  SPLIT to its own next stage** (stateful entity store, not a relational op). Uniform most_complete;
  order-dependent/custom/oversized/provenance deferred (in-memory fallback, like Ray).
- **Identity-on-Sail — NEXT** (split from S3).  **S4:** 100M+ multi-node bench (REAL BYO cluster) +
  large-star/small-star WCC swap + Ray retire.

## Verification
- CI smoke: a local Sail Spark Connect server (`pysail.spark.SparkConnectServer`) runs the same
  plan single-process for small-scale parity (deps: `pysail` + `pyspark[connect]`).
- Binding bench (S4): BYO multi-node cluster via a `SAIL_REMOTE` secret (docs-not-bootstrap,
  mirrors the Ray phase5 `RAY_ADDRESS` posture); 100M dataset from the phase5 generator.

---
**Classification:** architecture/planned • **Last updated:** 2026-06-03
