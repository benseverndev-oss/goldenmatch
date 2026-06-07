# Network Updates Log

Newest first. One entry per meaningful change to the network.

## 2026-06-07 — GoldenFlow Arrow-native kernel shipped
- New architecture node: [../architecture/goldenflow-native-kernel.md](../architecture/goldenflow-native-kernel.md)
  and decision: [../decisions/0006-goldenflow-native-nanp-gating.md](../decisions/0006-goldenflow-native-nanp-gating.md).
- Measured that GoldenFlow's `date_iso8601` + `phone_e164` were ~92 % of a 1M-row
  run (per-row `dateutil`/`phonenumbers`). Shipped: (1) **vectorized Polars fast
  paths** with a per-row fallback (`transforms/_fastpath.py::apply_with_residual`),
  76× date / 19× phone, ~14× end-to-end, parity-safe; (2) the optional
  `goldenflow-native` Rust/PyO3 abi3 kernel (`packages/rust/extensions/native-flow`)
  for the phone residual, Arrow zero-copy, **NANP-only gated** so it's byte-identical
  to `phonenumbers` by construction (the Rust port diverges on int'l/ambiguous
  numbers; two gates confine it to country-code-1 + canonical NANP).
- Infra mirrors `goldenmatch-native`: loader (`goldenflow/core/_native_loader.py`,
  `GOLDENFLOW_NATIVE` 0/auto/1), `publish-goldenflow-native.yml` (per-platform abi3
  wheels on `goldenflow-native-v*`), and two `native_flow` CI lanes (build + parity).
- Promoted in [../planning/roadmap.md](../planning/roadmap.md) as an adjacent
  Arrow-native arc. Docs-site updated in the same change: new
  `goldenflow/performance.mdx` (+ overview card + nav). Code-level notes in
  `packages/python/goldenflow/CLAUDE.md` + `packages/rust/extensions/native-flow/README.md`.

## 2026-06-06 — Auto-config search strategy after the engine speedup (v1.28.0)
- New planning node: [../planning/autoconfig-search-strategy.md](../planning/autoconfig-search-strategy.md)
  — the thesis (the controller's search strategy was calibrated to a cost model the
  perf arc falsified), the four-phase arc, and what shipped in 1.28.0 vs. what's staged.
- Shipped (1.28.0): a **planning-effort tier** (`fast`/`normal`/`thinking`/`einstein`)
  on `GoldenMatchConfig` + `dedupe_df`/`match_df`/`auto_configure_df` +
  `GOLDENMATCH_PLANNING_EFFORT`; `ControllerBudget.for_dataset(n_rows, effort)`;
  **measure-don't-extrapolate** at thinking+ (`blocker.measure_blocking_profile`);
  **provider-aware in-house embedding** (`_check_remote_assets` no longer demotes the
  local model). Default `normal` is byte-for-byte unchanged.
- Staged for a follow-up (behind the thinking/einstein seam): full successive-halving
  over a candidate grid (Phase 2) and an LLM-judge labeling objective (Phase 4).
- Spec: `docs/superpowers/specs/2026-06-06-autoconfig-search-strategy-after-engine-speedup-design.md`.
  Docs-site: `goldenmatch/auto-config.mdx` (Planning effort + in-house embeddings).

## 2026-06-05 — Security hardening arc (42 alerts cleared, Scorecard 6.1->7.3)
- New workstream node: [../planning/security-hardening.md](../planning/security-hardening.md)
  — Dependabot 7/7 (#761/#762), code-scanning 35/35 (log-injection #764,
  path-injection #768, TokenPermissions #760/#772, dismissals), Scorecard
  climbs (Token-Permissions->10, Signed-Releases->8, Fuzzing->10 via
  #770/#778/#783), the CodeQL Autofix incident, the 4-bug property-test
  ledger, and 3 open actions.
- Promoted in [../planning/roadmap.md](../planning/roadmap.md) as an adjacent arc.
- Docs-site updated in the same change: `GOLDENMATCH_ALLOWED_ROOT` in
  configuration + MCP path-sandbox section; cosign release-verification
  snippet in installation.

## 2026-06-05 — Surface hardening + parity arc (Waves 0-4)
- New workstream node: [../planning/surface-hardening.md](../planning/surface-hardening.md)
  — the four-surface audit, the merged auth/bug/TUI waves (#766/#767/#769), the
  ten-PR open queue (#771-#782), the Railway `GOLDENMATCH_MCP_TOKEN` open action,
  and the parity-fixture methodology (structure-not-ids; emitter-asserted margins).
- Promoted in [../planning/roadmap.md](../planning/roadmap.md) as an adjacent arc.
- Docs-site updated in the same change (MCP/REST/A2A auth, review command, TUI
  8-tabs + auto-config screen, REST readiness health, A2A streaming:false).

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
**Classification:** meta/log • **Last updated:** 2026-06-07
