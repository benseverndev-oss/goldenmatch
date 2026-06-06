# Context Network — Discovery Hub

The navigation entry point. Each node below is focused and cross-linked; follow the
links rather than reading everything.

## Foundation (what this is)
- [foundation/project-definition.md](foundation/project-definition.md) — what the Golden Suite / goldenmatch is, and the gate that governs the current arc.
- [foundation/structure.md](foundation/structure.md) — the polyglot monorepo layout and where the authoritative context lives (CLAUDE.md files).

## Architecture (active technical knowledge)
- [architecture/datafusion-spine.md](architecture/datafusion-spine.md) — the embedded-DataFusion "scale mode" spine (Stages A-E); current status + entry points.
- [architecture/sail-tier.md](architecture/sail-tier.md) — the distributed Sail-native tier (Spark Connect) that replaces Ray; specced, build not started.
- [architecture/sql-native-extensions.md](architecture/sql-native-extensions.md) — graph + embedding UDFs on DuckDB/Postgres/DataFusion, native-direct (shared `graph-core` + `goldenembed-rs`); SHIPPED (#509).

## Decisions (records with no other home)
- [decisions/0001-gate-reframe-engine-portability.md](decisions/0001-gate-reframe-engine-portability.md) — retire one-box RSS as the gate; engine portability is the destination.
- [decisions/0002-scale-mode-contract.md](decisions/0002-scale-mode-contract.md) — `mode={standard,scale}`, opt-in, semantically-correct-not-bit-identical.
- [decisions/0003-stage-e-spill-honest-null.md](decisions/0003-stage-e-spill-honest-null.md) — one-box spill-survival does not bind (the UF island); default stays opt-in.
- [decisions/0004-sail-tier-scope.md](decisions/0004-sail-tier-scope.md) — Sail tier: full, buildable, Sail-native, replaces Ray; WCC-on-Sail is the gate.
- [decisions/0005-sql-native-direct-udfs.md](decisions/0005-sql-native-direct-udfs.md) — SQL graph + embed UDFs go native-direct (drop the CPython bridge); shared `graph-core`, accept-both ids, embed wheel, 3 surfaces.

## Processes (how work is done here)
- [processes/development-workflow.md](processes/development-workflow.md) — spec → plan → execute → review → CI → merge, plus the hard environment constraints.

## Planning (where it's going)
- [planning/roadmap.md](planning/roadmap.md) — the Arrow-native arc and what's next.
- [planning/surface-hardening.md](planning/surface-hardening.md) — the 2026-06-05 four-surface audit arc: fail-closed HTTP auth, CLI/TUI fixes, Python→TS parity ports (+ the parity-fixture methodology), and the open PR queue.

## Meta (keeping the network alive)
- [meta/updates.md](meta/updates.md) — chronological change log for the network.
- [meta/maintenance.md](meta/maintenance.md) — how to keep nodes accurate and small.

---
**Classification:** navigation • **Last updated:** 2026-06-05
