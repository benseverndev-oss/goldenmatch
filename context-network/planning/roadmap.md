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

## Next candidates (not yet specced/scheduled)
- **Sail / distributed tier** — route the Union-Find cluster build to distributed
  label-prop (≥50M), removing the in-memory pair-collection island. This is the lever
  that unlocks beyond-one-box scale and would let the default flip.
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
