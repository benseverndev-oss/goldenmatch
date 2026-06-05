# Network Updates Log

Newest first. One entry per meaningful change to the network.

## 2026-06-05 — SQL-native graph + embedding UDFs shipped (#509, all 3 PRs)
- #509 fully delivered across PRs #740 (graph half — DuckDB + Postgres), #743 (embed
  half — `goldenmatch-embed` wheel + repoint + bridge cleanup), #745 (DataFusion FFI
  graph UDFs). The graph + embed SQL surface is now **native-direct** (pure-Rust
  `graph-core` + `goldenembed-rs`, no embedded-CPython JSON bridge) across all three
  backends; the #503 bridge placeholder + its dead `bridge::api` fns are gone.
- New nodes: [../architecture/sql-native-extensions.md](../architecture/sql-native-extensions.md),
  [../decisions/0005-sql-native-direct-udfs.md](../decisions/0005-sql-native-direct-udfs.md).
  Noted the adjacent SQL surface in [../planning/roadmap.md](../planning/roadmap.md).
- New crates/packages: `graph-core` (pyo3-free shared kernel), `goldenmatch-embed`
  (maturin wheel over goldenembed-rs). `goldenmatch_pg` + `goldenmatch-duckdb` bumped
  0.5.0→0.6.0 (new handwritten SQL surface + upgrade script). Spec/plan:
  `docs/superpowers/specs/2026-06-04-sql-native-graph-embed-udfs-design.md`,
  `docs/superpowers/plans/2026-06-04-sql-native-graph-embed-udfs.md`.

## 2026-06-04 — Sail tier S4 harness shipped (buildable tier COMPLETE)
- S4 harness merged (PR #717): chain-robust O(log n) WCC via pointer-jumping (the blind
  large-star/small-star attempt was wrong, caught by plan-review hand-trace + replaced),
  `run_sail_pipeline` end-to-end, and the 100M bench scaffold. The `sail` lane has 6 green gates.
  The BUILDABLE Sail tier is COMPLETE; only the real 100M cluster run + Ray retirement remain
  (need a BYO Sail cluster). Updated [../architecture/sail-tier.md](../architecture/sail-tier.md) +
  [../planning/roadmap.md](../planning/roadmap.md).

## 2026-06-03 — Sail tier Stage S3 (golden) shipped
- S3 golden merged (PR #714): distributed survivorship on Sail (collect_list + merge_field UDF),
  content-parity green. SCOPE DECISION: S3 scoped to golden only; identity split to its own next
  stage (stateful graph subsystem, not a relational op). Updated
  [../architecture/sail-tier.md](../architecture/sail-tier.md) +
  [../planning/roadmap.md](../planning/roadmap.md). Identity-on-Sail is next, then S4 (real cluster).

## 2026-06-03 — Sail tier Stage S2 shipped (make-or-break gate)
- S2 merged (PR #712): WCC on Sail via min-label propagation, partition-parity green. The
  existential "WCC-on-Sail at all" risk is CLOSED. Marked S2 done in
  [../architecture/sail-tier.md](../architecture/sail-tier.md) +
  [../planning/roadmap.md](../planning/roadmap.md). S3 (golden + identity on Sail) is next.

## 2026-06-03 — Sail tier Stage S1 shipped
- S1 merged (PR #709): the `goldenmatch.sail` harness + scorer pandas UDF + score/dedup,
  parity-green on a new `sail` CI lane. Marked S1 done in
  [../architecture/sail-tier.md](../architecture/sail-tier.md) +
  [../planning/roadmap.md](../planning/roadmap.md). S2 (WCC on Sail) is the next gate.

## 2026-06-03 — Sail tier specced + roadmapped
- Added the Sail-tier design (`docs/superpowers/specs/2026-06-03-sail-tier-design.md`,
  spec-reviewer approved) — the distributed Sail-native pipeline that replaces Ray.
- New nodes: [../architecture/sail-tier.md](../architecture/sail-tier.md),
  [../decisions/0004-sail-tier-scope.md](../decisions/0004-sail-tier-scope.md); promoted
  the Sail tier in [../planning/roadmap.md](../planning/roadmap.md) (S1-S4, WCC as the gate).

## 2026-06-03 — Network created
- Seeded the context network and the root `.context-network.md` discovery file.
- Captured the DataFusion-spine workstream end-to-end: Stages A-E status
  ([../architecture/datafusion-spine.md](../architecture/datafusion-spine.md)), and the
  three decisions that had no prior home:
  - [0001 gate reframe — engine portability](../decisions/0001-gate-reframe-engine-portability.md)
  - [0002 scale-mode contract](../decisions/0002-scale-mode-contract.md) (PR #702)
  - [0003 Stage E spill HONEST-NULL](../decisions/0003-stage-e-spill-honest-null.md) (PRs #705/#706)
- Recorded the development workflow + environment constraints
  ([../processes/development-workflow.md](../processes/development-workflow.md)) and the
  roadmap ([../planning/roadmap.md](../planning/roadmap.md)).
- Committed to git on branch `chore/context-network`.

---
**Classification:** meta/log • **Last updated:** 2026-06-05
