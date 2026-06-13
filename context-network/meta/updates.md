# Network Updates Log

Newest first. One entry per meaningful change to the network.

## 2026-06-13 — Discoverability / public-surface audit (PR #883)
- New workstream node [../planning/discoverability.md](../planning/discoverability.md);
  linked from [../discovery.md](../discovery.md) and promoted in
  [../planning/roadmap.md](../planning/roadmap.md) as an adjacent arc.
- An accuracy-first, value-forward pass over every pre-install surface. **README:**
  fixed DBLP-ACM `97.2→96.4`, dropped the `DQBench 95.30` headline badge (competitor
  ceiling, not a GM score — keep 91.04 in benchmarks only), `478→~940` tests,
  `36+→50+` MCP tools, Identity-Graph-v2-is-a-feature-shipped-in-v1.15 (not package
  v2.0); led the accuracy story with beats-Splink + surfaced GoldenAnalysis/WASM.
- **`llms.txt` family is now complete:** added the missing **goldenanalysis** +
  **infermap** files and a **suite-level root `/llms.txt`**; corrected the stale A2A
  skill count `10`/`12` → **`31`** (the agent card's `_SKILLS` grew via an MCP-parity
  pass) and the ~500K-cap performance framing → the verified 100M run; retargeted all
  archived-repo cross-links to the live monorepo paths.
- **Registry + citation:** PyPI keywords were behind npm (added `splink`,
  `record-linkage`, `fuzzy-matching`, `pprl`, … across the 6; `golden-suite` on all);
  new root **`CITATION.cff`**. **GitHub About** description + topics refreshed
  (`+splink`, `-negative-evidence`).
- **Archived sibling repos redirected** (their About now points to the monorepo).
- **Three durable facts recorded in the node:** (1) the homepage "what's new" block is
  single-sourced from `<!-- README-callout -->` markers in goldenmatch's CHANGELOG via
  `scripts/sync_readme_callouts.py` (`--check` is a CI gate) — edit the CHANGELOG, not
  the README; (2) the docs site is **Mintlify**, which auto-serves `/llms.txt` +
  `/llms-full.txt` at `docs.bensevern.dev` (the repo-root file is the GitHub/raw
  supplement); (3) an **archived repo is fully API-read-only** (`gh repo edit` → HTTP
  403), so editing its About needs unarchive → edit → re-archive.
- **Open / handed off:** the GitHub social-preview image (manual, no API) and external
  awesome-list PRs (`awesome-mcp-servers` / `Awesome-Entity-Resolution` /
  `awesome-data-quality` — entries drafted, pending go-ahead).

## 2026-06-13 — Opt-in WASM acceleration arc (TypeScript) — #878/#879/#880/#881
- New [../architecture/wasm-acceleration.md](../architecture/wasm-acceleration.md)
  and [../decisions/0014-opt-in-wasm-acceleration.md](../decisions/0014-opt-in-wasm-acceleration.md);
  linked both from [../discovery.md](../discovery.md).
- The TS packages now optionally reach the same pyo3-free `*-core` Rust kernels
  via WebAssembly — opt-in, pure-TS stays the default + fallback, edge-safe,
  `.wasm` built in CI (never committed). Two cores shipped:
  - **#878** `score-core` → goldenmatch `scoreMatrix` (`enableWasm()`; jaro_winkler/
    levenshtein/exact at first).
  - **#880** `analysis-core` → goldenanalysis `histogram`/`quantile`
    (`enableAnalysisWasm()`) **+** extracted the shared `goldenmatch-wasm-runtime`
    workspace package (byte loader + generic enable skeleton + registry) that both
    consumers ride.
  - **#879** aligned the hand-rolled pure-TS scorers with rapidfuzz (the parity
    prerequisite): codepoint iteration, Winkler `>0.7` boost, floored transposition
    `t//2` — empirically settled as integer-vs-float halving, not bit-parallel
    matching (0/50000 vs rapidfuzz incl. non-BMP).
  - **#881** added `token_sort` WASM coverage (new `score-core::token_sort_normalized_ratio`,
    distinct from the pinned un-normalized `score_one(2)`) + validated the bundled
    dist artifact path (defensive multi-location copy; flipped the benches to a
    dist-path gate, which caught a real 1M-element `histogram` `Math.min(...vals)`
    stack overflow).
- **Measure-first parked `graph-core`** without building it (UnionFind is one O(N)
  step among several in `buildClusters`, marshaling N pairs is O(N) →
  boundary-bound); `fingerprint-core` / `goldencheck-core` stay parked by design.

## 2026-06-12 — #855 goldencheck TS port: module parity + hardened golden harness (#873/#874)
- New [../decisions/0013-goldencheck-ts-parity-hardening.md](../decisions/0013-goldencheck-ts-parity-hardening.md);
  added the `#855` subsection to [../planning/surface-hardening.md](../planning/surface-hardening.md).
- **#873** ported the goldencheck TS gaps the 2026-06-11 audit found: 2 profilers
  (`freshness`, `fuzzy_values`), 4 relations (`approx_duplicate`, `approx_fd`,
  `composite_key`, `functional_dependency`), and the `validate` MCP tool —
  registries now 12 column profilers / 9 relations / 18 MCP tools, each mirroring
  the Python **fallback** (native kernels stay Python-only by design).
- **#874** hardened `tests/parity/parity.test.ts`: it now asserts confidence (4 dp)
  + affected_rows and FAILS on a missing manifest/golden, where it previously
  checked only (column, check, severity) and skipped silently. Goldens were
  regenerated on a clean `ubuntu-latest` runner (`regen-855-parity-goldens.yml`,
  artifact-download) because the dev box OOMs on Polars.
- **The hardening immediately caught a pre-existing bug.** TS `TemporalOrderProfiler`
  used `new Date(s)`, which parses bare integers (`"7"`) as dates, so it fired
  `temporal_order` on integer column pairs Python never flags. Gated TS
  `tryParseDate` on `YYYY-MM-DD` to match Python's `str.to_date('%Y-%m-%d')`; all 6
  parity cases now match Python byte-for-byte. **#855 CLOSED.**
- **By design:** `freshness` is unit-test-only (the CSV-roundtrip harness reads
  dates as `Utf8`, so Python's date-gated profiler can't fire through it).

## 2026-06-12 — FS block-scoring perf + the "native is slow" red herring (PR #869)
- New [../decisions/0012-fs-block-scoring-perf.md](../decisions/0012-fs-block-scoring-perf.md);
  added a perf section + corrected the "3-19x faster" framing in
  [../architecture/fellegi-sunter-splink-parity.md](../architecture/fellegi-sunter-splink-parity.md).
- **The bake-off's "Splink 3-19x faster" measured GM's NUMPY path, not native** —
  it never set `GOLDENMATCH_FS_NATIVE`, and probabilistic mode doesn't refuse on a
  missing kernel. Added a `gm_prob_native` bake-off column (native built +
  `score_block_pairs_fs` asserted in CI): **native ≈ numpy, no wall change.** The
  wall is per-block fan-out (historical_50k: 31,735 blocks, 79% ≤8 rows, ~222k tiny
  FFI calls), not scoring math — so the Rust kernel can't move it.
- **Three output-identical optimizations** on the numpy path, each gated by a
  fixed-`em_result` pair-set diff (200,058 pairs byte-identical), NOT the cluster
  hash (pipeline is non-deterministic ±3 clusters run-to-run): value-dedup (−32%),
  block-batching into shared S×S matrices (−48%, native calls 222k→4.3k), batch
  row-cap 512→256 (−20%). **historical_50k 86.5s → 24.6s (−72%) local.** All three
  CI-green on PR #869.
- Also refreshed [../../docs/er-vendor-comparison.md](../../docs/er-vendor-comparison.md)
  to v1.30.0 (refdata, identity graph, Splink-parity flip) earlier in the same PR.
- **Flagged, not fixed:** EM-sampling cluster-count nondeterminism (±3) and the
  pre-optimization bake-off table (re-bench pending) are recorded in 0012.

## 2026-06-11 — #844 FINISH LINE: 100M validated, per-group scoring fixed, default flipped (#864/#867)
- Updated [../architecture/distributed-wcc.md](../architecture/distributed-wcc.md)
  + [../decisions/0011-distributed-wcc-randomized-contraction.md](../decisions/0011-distributed-wcc-randomized-contraction.md)
  from "specs shipped, operator-deferred" to VALIDATED + default-flipped. **#844 CLOSED.**
- **The binding 100M run is done.** Self-provisioned 5-node `e2-standard-16` GCP
  cluster, 100M synthetic phase-5 dataset in GCS: full recall-complete e2e in
  **554.5 s (9.2 min, under the 30-min kill), 20,000,000 clusters recovered
  exactly, driver RSS 0.36 GB**, no head-wedge / no Ray deadlock. The WCC alone
  cleared a 200M-edge graph in 266 s in isolation.
- **The e2e wall was per-group scoring, NOT the WCC.** `_score_colocated_groups`
  looped `group_by` + a full per-partition kernel call per ~5-row group (~20M
  fixed-overhead calls at 100M; 0 of 64 score-tasks finished in 25 min). #864
  vectorizes it — score the whole partition once (the `bucket` backend already
  groups by the blocking key); parity-tested. That single change made the e2e viable.
- **#864 (merged)** also fixed auto-config `DuplicateError: __row_id__` on a
  `__row_id__`-carrying input (`_add_row_ids` guard) and gave the e2e bench an
  explicit-config + `allow_red_config` path (it always auto-configured before,
  which is slow + RED-degenerate at 100M).
- **#867 (open, reviewable)** flips `GOLDENMATCH_DISTRIBUTED_BLOCK_SHUFFLE` default
  `0→1` + adds `_assert_scratch_shared_if_multinode` (multi-node + node-local WCC
  scratch → raises instead of silently diverging).
- Deferred/optional: (b) project-to-scoring-columns-before-shuffle (a wide-record
  shuffle win, not needed for viability).

## 2026-06-11 — TS parity: refdata name scorers + autoconfig blocking (#857, from the #856 audit)
- Extended the parity workstream node
  [../planning/surface-hardening.md](../planning/surface-hardening.md):
  a new "Fixtures rot silently — the #856/#857 lesson" subsection under the
  parity-fixture methodology, plus the #857 entry.
- **The #856 audit found real cross-language drift sitting in `main`:** the
  TS `typescript` CI lane runs only on TS path changes, so a pure-Python
  autoconfig change leaves the committed parity vectors stale and the TS
  test green against a fixture that no longer reflects Python. The
  controller-stoppoint fixture had drifted (`ensemble` where Python now
  emits the refdata name scorers + evolved multi-pass blocking).
- **#857 (merged) closed it by porting, not pinning.** The edge-safe TS core
  gained `given_name_aliased_jw` (alias-aware JW) and `name_freq_weighted_jw`
  (Census surname-IDF-weighted JW), the first/last-name auto-config refine
  (`refineNameScorer`, last-before-first; `multi_name` unrefined), and a
  faithful port of `build_blocking`'s selection (exact gate at
  `cardinality_ratio ≤ 0.5` on the exact pool only; secondary-name passes).
  Scope grew twice (regen forced the 186KB surname table; the residual red
  was a separate blocking-evolution gap). Both refdata tables are generated
  TS modules synced from the Python source (`scripts/sync_ts_refdata.mjs`)
  and drift-guarded; numeric parity is pinned by Python-computed values in
  `tests/parity/scorer-ground-truth.test.ts` (4dp).
- **Two durable methodology guards recorded:** generate-and-drift-guard
  bundled data (never hand-copy), and pin scorer parity to Python ground
  truth rather than a TS self-mirror (a self-mirror passes even if both
  sides diverge from Python).
- Deferred: refdata transform packs + geo/date blocking branches. Follow-up
  **#860**: TS `buildWeightedMatchkey` still drops `nullRate>0.5` name
  columns while the blocking path now keeps them (why `sparse_people` stays
  loose-shape). Docs-site: `goldenmatch/typescript.mdx` (scorer list/table +
  Python-comparison row) and `goldenmatch/reference-data.mdx` (TS-parity note).

## 2026-06-10 — Distributed WCC for #844: randomized contraction + recall-complete Phase 5 (#851/#852)
- New nodes: [../architecture/distributed-wcc.md](../architecture/distributed-wcc.md)
  + [../decisions/0011-distributed-wcc-randomized-contraction.md](../decisions/0011-distributed-wcc-randomized-contraction.md).
- **Problem (#844):** the Phase-5 distributed pipeline under-merged at scale. PR
  #845's opt-in block-shuffle co-locates duplicates but makes components cross
  partitions, which the per-partition `local_cc_assignments` Union-Find
  under-merges. The two existing distributed WCCs both die at 100M:
  `two_phase_wcc` driver-collects + runs a cpython-loop UnionFind (head-wedge);
  `distributed_wcc` deadlocks Ray's streaming executor on iterative joins.
- **Fix (both specs SHIPPED):** Spec 1 (PR #851) = `randomized_contraction_wcc`
  (Bögeholz–Brand–Todor 2018, arXiv:1802.09478) — relational, chain-robust,
  O(log|V|) rounds, no driver UF, per-round parquet checkpoint to dodge the
  deadlock; pure-Polars reference gated vs `scipy.csgraph`. Spec 2 (PR #852) =
  wires it into `_run_phase5_pipeline` (block-shuffle on -> distributed WCC, off ->
  `local_cc`; same predicate the scorer uses) via a new `algorithm` kwarg on
  `build_clusters_distributed`; join + golden tail unchanged (shared contract).
  Opt-in (`GOLDENMATCH_DISTRIBUTED_BLOCK_SHUFFLE=1`), **default unchanged**.
- **Two un-locally-testable Ray Data join rules now recorded** (distinct-named keys
  + `ReadParquet` inputs — both surfaced as `ArrowInvalid` on the CI ray lane).
  The `distributed` job `timeout-minutes` went 20 -> 30 to fit the new blocking gate.
- **Deferred (operator):** the binding multi-node 100M run + the default-flip (need
  a BYO Ray cluster; `GOLDENMATCH_DISTRIBUTED_WCC_SCRATCH` must be a `gs://` prefix).
  Parallel to the [Sail tier](../architecture/sail-tier.md) (the Spark-Connect track
  that retires Ray); whichever binds 100M first is go-forward. Mintlify scale page
  (`docs-site/goldenmatch/backends-and-scale.mdx`) updated with the recall-complete
  path.

## 2026-06-10 — publish-containers flake hardening (ghcr buildkit mirror, #846)
- New decision: [../decisions/0010-publish-containers-ghcr-mirror.md](../decisions/0010-publish-containers-ghcr-mirror.md).
- **Audit:** `publish-containers` went red ~1 run in 18 over 30 days (11 fails,
  6 different packages) — every one a transient registry timeout, never a code
  bug. Dominant: `setup-buildx` pulls `moby/buildkit:buildx-stable-1` from Docker
  Hub *anonymously*; 7 legs pulling in parallel each main push race into Docker
  Hub's shared-runner-IP throttle → `context deadline exceeded`. Secondary: ghcr
  502s + GHA-cache blob copy errors at `Build and push`.
- **Fix (PR #846, merged):** a prereq `mirror` job republishes buildkit + binfmt
  into `ghcr.io/<owner>/{buildkit,binfmt}` once per run (retried); the legs pull
  the helper images from ghcr via `setup-buildx driver-opts:` / `setup-qemu
  image:` (ghcr login moved ahead of buildx). Docker Hub off the hot path: 7
  unguarded parallel pulls → 1 retried read. Native retry-once twins
  (`continue-on-error` + `outcome=='failure'`, no third-party action) backstop
  the residual ghcr/cache blips; `publish` still runs on a stale ghcr copy if
  `mirror` flakes.
- **Verified on `main`:** run `27284102426` — 8/8 jobs green, zero retry twin
  fired (the mirror eliminated the Docker Hub pulls outright, not just retried
  them). Operational detail recorded in root `CLAUDE.md` (`## publish-containers
  flakes`); a red leg is cosmetic (content-addressed, self-heals next push).

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
**Classification:** meta/log • **Last updated:** 2026-06-13
