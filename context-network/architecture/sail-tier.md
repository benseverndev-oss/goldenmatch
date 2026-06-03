# Sail Tier (distributed, Sail-native — replaces Ray)

The distributed sibling of the one-box DataFusion spine. Re-expresses the spine's
relational plan against **Sail** (LakeSail — a Rust Spark drop-in built on DataFusion,
programmed via **Spark Connect / PySpark**) to run across nodes, computes connected
components distributed (removing the one-box UF island), and ultimately retires the
existing Ray distributed stack.

**Status:** specced 2026-06-03 (approved, pre-build). **Spec:**
`docs/superpowers/specs/2026-06-03-sail-tier-design.md`.
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

## Verification
- CI smoke: a local `sail spark server` runs the same plan single-process for small-scale
  parity (deps: `pysail` + `pyspark` client).
- Binding bench (S4): BYO multi-node cluster via a `SAIL_REMOTE` secret (docs-not-bootstrap,
  mirrors the Ray phase5 `RAY_ADDRESS` posture); 100M dataset from the phase5 generator.

---
**Classification:** architecture/planned • **Last updated:** 2026-06-03
