# Roadmap — Arrow-native arc

The destination is **engine portability** (DataFusion single-box → Sail distributed) —
see [../decisions/0001-gate-reframe-engine-portability.md](../decisions/0001-gate-reframe-engine-portability.md).

## Done
- **Step 1 — id_prep plannable (#696).** `ClusterPairScores.from_frames` rewritten as a
  group-by; id_prep 566→34s @100M; end-to-end flips to 2.11×.
- **Step 2 — DataFusion spine, Stages A-E.** Merged. Scale-mode contract shipped
  (#702); Stage E spill verdict recorded as HONEST-NULL on one-box survival (#706).
  See [../architecture/datafusion-spine.md](../architecture/datafusion-spine.md).

## Decided NOT to do (now)
- **Flip `mode` default to `"scale"`** — blocked: the one-box-survival gate is not met
  (Stage E). Revisit when the Sail tier removes the UF island.

## Next — the Sail tier (SPECCED 2026-06-03, build not started)
**The real value of the arc** (Stage E showed one-box is non-binding). A Sail-native
distributed pipeline (Spark Connect / PySpark) that re-expresses the spine's relational
plan across nodes, computes connected components distributed (removing the one-box UF
island), and ultimately REPLACES the Ray distributed stack. See
[../architecture/sail-tier.md](../architecture/sail-tier.md) +
[../decisions/0004-sail-tier-scope.md](../decisions/0004-sail-tier-scope.md).
Spec: `docs/superpowers/specs/2026-06-03-sail-tier-design.md`. Staged, each a gate:
- **S1** — Sail harness + scorer Arrow UDF + score/dedup (parity vs one-box spine).
- **S2** — **WCC on Sail** (port two-phase WCC to Spark Connect) — THE GATE.
- **S3** — golden (incl. custom rules) + identity on Sail.
- **S4** — binding 100M+ multi-node bench + Ray retirement. Kill criterion: completes
  where one-box can't, per-node RSS bounded, wall scales with nodes.

## Other candidates (not specced)
- **Relational-stages-only spill bench** — score+dedup under a cgroup `MemoryMax` cap,
  excluding the UF collection, to show relational spill survival crisply in isolation.
- **Fix the pre-existing empty/all-singleton `run_spine` SchemaError** (frames-out tail,
  null vs i64 join key) — flagged during Stage D, out of scope there.

## Related larger arcs (in `packages/python/goldenmatch/CLAUDE.md`)
- The Splink-Spark parity roadmap (Ray Phases 1-6) — distributed loader → controller →
  clustering → golden → multi-node → identity. Mostly plumbing-complete, gated behind
  `GOLDENMATCH_ENABLE_DISTRIBUTED_RAY=1`.

---
**Classification:** planning/active • **Last updated:** 2026-06-03
