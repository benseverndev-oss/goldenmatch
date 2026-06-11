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
- [architecture/goldenflow-native-kernel.md](architecture/goldenflow-native-kernel.md) — GoldenFlow date/phone vectorized fast paths + the optional `goldenflow-native` phone kernel (NANP-only gated); SHIPPED (2026-06-07).
- [architecture/goldencheck-native-kernel.md](architecture/goldencheck-native-kernel.md) — GoldenCheck's Arrow-native runtime (`goldencheck-native`) + deep-profiling expansion (Benford / composite-key / FD / fuzzy / approx-FD kernels, `--deep`, `refs`, freshness) + the `cell_quality` / `functional_dependencies` bridge APIs; SHIPPED (#793, 2026-06-07).
- [architecture/goldencheck-goldenmatch-integration.md](architecture/goldencheck-goldenmatch-integration.md) — data quality feeds entity resolution: four fail-open, default-OFF doors (survivorship, blocking, FD negative-evidence, quality-gated review); #794/#795/#798 shipped, #797 open.
- [architecture/fellegi-sunter-splink-parity.md](architecture/fellegi-sunter-splink-parity.md) — the `type: probabilistic` matchkey from scorer to Splink-class engine: model lifecycle, supervised m, match-weight waterfall, calibration, accuracy analysis, bucket/native scale-out. FS auto-config v2 (#823, v1.29.0) now beats hand-rolled Splink on every dataset Splink scores (pairwise F1, shared evaluator), made reproducible by the #829 EM-sampling determinism fix; the deterministic three-engine bake-off is at [`docs/benchmarks/2026-06-09-splink-bakeoff.md`](../docs/benchmarks/2026-06-09-splink-bakeoff.md).

## Decisions (records with no other home)
- [decisions/0001-gate-reframe-engine-portability.md](decisions/0001-gate-reframe-engine-portability.md) — retire one-box RSS as the gate; engine portability is the destination.
- [decisions/0002-scale-mode-contract.md](decisions/0002-scale-mode-contract.md) — `mode={standard,scale}`, opt-in, semantically-correct-not-bit-identical.
- [decisions/0003-stage-e-spill-honest-null.md](decisions/0003-stage-e-spill-honest-null.md) — one-box spill-survival does not bind (the UF island); default stays opt-in.
- [decisions/0004-sail-tier-scope.md](decisions/0004-sail-tier-scope.md) — Sail tier: full, buildable, Sail-native, replaces Ray; WCC-on-Sail is the gate.
- [decisions/0005-sql-native-direct-udfs.md](decisions/0005-sql-native-direct-udfs.md) — SQL graph + embed UDFs go native-direct (drop the CPython bridge); shared `graph-core`, accept-both ids, embed wheel, 3 surfaces.
- [decisions/0006-goldenflow-native-nanp-gating.md](decisions/0006-goldenflow-native-nanp-gating.md) — GoldenFlow: vectorize in Polars first; gate the native phone kernel to NANP-only (parity-safe by construction).
- [decisions/0007-goldencheck-goldenmatch-integration.md](decisions/0007-goldencheck-goldenmatch-integration.md) — GoldenCheck→GoldenMatch: fail-open quality bridges, additive, default-OFF + benchmark-gated; hold the DQ↔ER boundary.
- [decisions/0008-fellegi-sunter-splink-parity.md](decisions/0008-fellegi-sunter-splink-parity.md) — Fellegi-Sunter: close the Splink engine gap in dependency order, reuse the scale substrate, keep defaults reproducible (new power opt-in), measure the scale gate on a real runner.

## Processes (how work is done here)
- [processes/development-workflow.md](processes/development-workflow.md) — spec → plan → execute → review → CI → merge, plus the hard environment constraints.

## Planning (where it's going)
- [planning/roadmap.md](planning/roadmap.md) — the Arrow-native arc and what's next.
- [planning/surface-hardening.md](planning/surface-hardening.md) — the 2026-06-05 four-surface audit arc: fail-closed HTTP auth, CLI/TUI fixes, Python->TS parity ports, and the parity-fixture methodology — now including the #856/#857 "fixtures rot silently" lesson and the merged #857 refdata-name-scorer + autoconfig-blocking TS port (generate-and-drift-guard bundled data; pin parity to Python ground truth).
- [planning/security-hardening.md](planning/security-hardening.md) — the 2026-06-05 security-hardening arc: 42-alert remediation (Dependabot + code scanning), Scorecard 6.1->7.3 (Token-Permissions/Signed-Releases/Fuzzing), the CodeQL Autofix incident, property-test bug ledger, and open actions.

## Meta (keeping the network alive)
- [meta/updates.md](meta/updates.md) — chronological change log for the network.
- [meta/maintenance.md](meta/maintenance.md) — how to keep nodes accurate and small.

---
**Classification:** navigation • **Last updated:** 2026-06-11
