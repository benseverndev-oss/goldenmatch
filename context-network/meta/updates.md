# Network Updates Log

Newest first. One entry per meaningful change to the network.

## 2026-06-09 — Rust test-coverage arc: make the tests real, then measure (#827/#830/#832)
- New nodes: [../architecture/rust-test-coverage.md](../architecture/rust-test-coverage.md)
  (per-crate testing map + the measured baseline) and
  [../decisions/0009-rust-test-coverage.md](../decisions/0009-rust-test-coverage.md).
- An audit found the Rust tree's CI claims were partly fictional. Closed the real
  gaps: standalone `graph-core`/`score-core` tests now run (#827); `native` got 18
  Rust unit tests behind an `extension-module` feature-gate (#827); the pgrx graph/
  fingerprint surface is psql-asserted vs a real `CREATE EXTENSION` (#827); the
  `bridge` went from **6 silent self-skipping no-ops to 42 real marshalling tests**
  (#830, install goldenmatch into the embedded interpreter + a `REQUIRE_PY` gate);
  and a `cargo-llvm-cov` `rust_coverage` job posts a per-crate baseline (#832).
- Two load-bearing facts recorded so they aren't re-litigated: **`cargo pgrx test`
  is a structural dead-end** for `goldenmatch_pg` (schema-gen broken → psql smoke
  instead), and **`native`'s 26% measured coverage is an artifact** (it's
  Python-parity-tested; llvm-cov only sees `cargo test`). Fixed the now-stale
  `cargo pgrx test`/`pg_test` claim in
  [../architecture/sql-native-extensions.md](../architecture/sql-native-extensions.md).
- Verdict: no compelling remaining Rust gap; the structural work is done and now
  measurable + regression-guardable.

## 2026-06-09 — FS auto-config v2: GoldenMatch now BEATS Splink on accuracy (#823)
- Updated the architecture node + decision:
  [../architecture/fellegi-sunter-splink-parity.md](../architecture/fellegi-sunter-splink-parity.md)
  (new "Accuracy arc — beating Splink" section + softened "Where Splink still
  leads": Splink no longer leads on PII accuracy),
  [../decisions/0008-fellegi-sunter-splink-parity.md](../decisions/0008-fellegi-sunter-splink-parity.md)
  (appended an accuracy-arc update; Status line bumped).
- **The probabilistic (Fellegi-Sunter) auto-config now beats Splink head-to-head**
  (#823, "FS auto-config v2", default-ON; kill-switch
  `GOLDENMATCH_FS_AUTOCONFIG_V2=0` restores legacy byte-identically). Scope is the
  probabilistic auto-config path only (`auto_configure_probabilistic_df` /
  `build_probabilistic_matchkeys`); weighted/DQbench + zero-config `dedupe_df` are
  untouched. Four levers: (1) admit `dob`/date columns as a `levenshtein`
  discriminator (v1 discarded them); (2a) drop redundant person-name composites
  when atomic given+family exist; (2b) low-cardinality fuzzy floor;
  (3) `_diversify_probabilistic_blocking` — *additive*, recall-positive blocking
  diversification onto orthogonal stable keys (date-year + postcode/zip);
  (4) admit description (title) + multi_name (authors) as `token_sort` (lifts the
  DBLP-ACM venue-only mega-match — 0.003 → 0.377, still recall-bound). #821 built
  the shared head-to-head evaluator (`scripts/bench_er_headtohead`) the claim rests
  on.
- **Measured (pairwise F1, shared evaluator) — deterministic as of #829:**
  historical_50k (Splink's flagship) 0.647 → 0.778 vs Splink 0.757; febrl3
  0.983 → 0.991 vs 0.965; synthetic_person 0.972 → 0.998 vs 0.996; dblp_acm
  0.003 → 0.377 (Splink skips it). GM also wins historical_50k at the cluster
  level (B-cubed F1 0.844 vs 0.789). Full bake-off:
  `docs/benchmarks/2026-06-09-splink-bakeoff.md`.
- **#829 (determinism fix):** the original #823 numbers rested on a
  non-deterministic EM training-pair sample — three invocations of the identical
  GM-prob path gave historical_50k 0.805 / 0.779 / 0.643 on one CI run. #829 sorts
  blocks by their stable `block_key` before the seeded shuffle; post-fix three
  harnesses agree within 0.002. The earlier `dblp_acm = 0.879` was a lucky draw
  that does not reproduce (deterministic 0.377). For bibliographic data use the
  *weighted* path (0.964 on DBLP-ACM), not probabilistic.
- **Honest framing preserved:** these are *pairwise* F1 under one shared harness.
  The often-cited ~0.97 Splink historical_50k number is a *cluster*-level metric,
  not exhaustive pairwise; a local diagnostic ran Splink 4.0.16 and it scores
  ~0.75 pairwise here (recall-bound — 5156 clusters, mean size ~10, no field
  exceeds 0.60 recall → ~0.93 pairwise ceiling for any engine). Claim is
  "matches/beats Splink on every dataset Splink scores on the same evaluator,"
  not "0.97 pairwise." Splink is also 3-19x faster on these datasets.

## 2026-06-08 — Fellegi-Sunter → Splink parity (+ EM perf, scale-out, vendor reposition)
- New architecture node + decision:
  [../architecture/fellegi-sunter-splink-parity.md](../architecture/fellegi-sunter-splink-parity.md),
  [../decisions/0008-fellegi-sunter-splink-parity.md](../decisions/0008-fellegi-sunter-splink-parity.md).
- **The `type: probabilistic` (Fellegi-Sunter) matchkey went from accuracy-competitive
  scorer to a Splink-class probabilistic-linkage engine** (PR #800, Phases 0–4 +
  the 3c bench): model lifecycle (`save_json`/`load_json` + `model_path`),
  supervised m from labels (`estimate_m_from_labels` + review/memory adapters),
  match-weight waterfall (`explain_pair_fs`, surfaced in `explain`/lineage),
  posterior calibration, label-driven threshold/accuracy analysis (`evaluate
  --threshold-sweep`), and scale-out on the shared `score_buckets` path (Phase 3a)
  + an opt-in native Rust kernel (Phase 3b, `GOLDENMATCH_FS_NATIVE=1`). The
  scale-out bet held: only one greenfield kernel function; everything else reused
  the existing bucket/Ray/DataFusion substrate.
- **The scale gate exposed the real bottleneck.** The 6M FS bench was
  `train_em`-bound, not scoring-bound (the native kernel had already cut
  `bucket_score` to ~14 s). `_sample_blocked_pairs` enumerated every within-block
  pair (`O(Σ size_i²)`, ~140M tuples) before sampling 10K; fixed to a
  block-stratified early-exit (PR #803, 13.7× `train_em`, ~100 s off the 6M wall,
  peak RSS halved). A separate fix corrected the *bench's* ground truth from a
  star to the entity clique (PR #802) — F1 was 0.825 only because the harness was
  scoring true matches as false; corrected to 1.000.
- **Measured 6M / `bucket` / 16c-64GB:** numpy 288.5 s / native 162.6 s (was 269 s),
  11.3 GB peak RSS, F1 1.000. Recorded in `docs/scale-envelope.md`.
- Docs-site: `goldenmatch/scoring.mdx` FS section rewritten (parity surface +
  scale + corrected DBLP-ACM 0.968, dropping the stale 57.6%-recall artifact);
  `reference/vendor-comparison.mdx` Splink row repositioned (FS feature parity
  closed; Splink retains distributed-1B+ and interactive-charting edge).
- Also corrects the goldencheck-integration node: #798 (quality-gated review)
  shipped.

## 2026-06-07 — GoldenCheck Arrow-native expansion + GoldenCheck→GoldenMatch integration
- Two new architecture nodes + one decision:
  [../architecture/goldencheck-native-kernel.md](../architecture/goldencheck-native-kernel.md),
  [../architecture/goldencheck-goldenmatch-integration.md](../architecture/goldencheck-goldenmatch-integration.md),
  [../decisions/0007-goldencheck-goldenmatch-integration.md](../decisions/0007-goldencheck-goldenmatch-integration.md).
- **GoldenCheck went from zero Rust to an optional `goldencheck-native` runtime**
  (#793, merged) + a deep-profiling wave: Benford (~16×), composite-key (1.7×, after
  the "naive kernel lost to Polars at 0.4× → u128 packing" fix), strict FD (12.8×),
  fuzzy value clustering (76×), approximate-FD violations (15.5×) — each parity-exact
  AND measured-to-beat-Polars. Plus `--deep` full-population mode, `refs` cross-file
  referential integrity, freshness/staleness, and two bridge APIs (`cell_quality`,
  `functional_dependencies`). Features Polars already wins (duplicate rows, refs,
  freshness) stay pure-Polars on purpose.
- **That quality signal now feeds GoldenMatch** through fail-open, default-OFF,
  benchmark-gated bridges in `core/quality.py` — four doors: quality-weighted
  survivorship (#794 ✅, wired the no-op `quality_weighting`), quality-aware blocking
  (#795 ✅, recall), FD-driven negative evidence (#797 🟡, precision), quality-gated
  review routing (#798 🟡, trust). Boundary held: value-level DQ in GoldenCheck,
  entity resolution in GoldenMatch.
- Process note logged in the decision: stacked PRs across squash-merges go `dirty`;
  recovery is merge-base-to-main then rebase-child-onto-main (done #794→#797).
- Docs-site: new `goldencheck/native.mdx` + `goldenmatch/data-quality.mdx`.

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
**Classification:** meta/log • **Last updated:** 2026-06-09
