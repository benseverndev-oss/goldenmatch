# Changelog

All notable changes to GoldenMatch are documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/). Versioning follows [Semantic Versioning](https://semver.org/) (strict after v1.0.0).

## [Unreleased]

## [1.23.0] - 2026-05-27

### Changed

- **Auto-config now commits by zero-label confidence by default** (issue #489).
  The controller's `pick_committed` tiebreaker prefers the higher
  `-overall_confidence` candidate over the higher `-mass_separation` one among
  same-health-rank entries that carry a `zero_label` profile (the
  precision-collapse rank-3 demotion still precedes it). Gated on a DQbench
  non-regression run (composite 92.03 >= 91.04 floor; byte-identical to the
  prior default on T1/T2/T3). Opt out with
  `GOLDENMATCH_AUTOCONFIG_ZERO_LABEL_COMMIT=0` to restore the legacy
  `-mass_separation` tiebreaker.

### Fixed

- **Composite name+DOB identity matchkey recovers person-data recall** (#538).
  When individual name/year columns fall below the cardinality≥0.5 exact-matchkey
  gate, auto-config previously degraded to a single weighted matchkey that
  averages every field, so one corrupted field (e.g. an abbreviated address
  scored by `token_sort`) could sink an otherwise-clean true pair. Auto-config
  now also emits an exact matchkey on the name+DOB *combination* (highly unique
  even when the individual fields aren't). Matchkeys are OR'd, so this only adds
  candidate pairs — no fuzzy scorer is loosened. Gated on a date/year anchor
  being present (names alone collide too heavily to anchor an identity claim).
  Benchmarks: NCVR F1 0.871 → 0.969, Febrl3 0.897 → 0.912, DBLP-ACM and DQbench
  unchanged.
- **Phonetic name+DOB identity matchkey** (#491). A sibling to the composite
  above: the same name+DOB key but with a `soundex` transform on the name
  fields, so equal-sounding spellings (Smith/Smyth, Catherine/Katherine) that
  share a DOB still match when the exact composite misses them. Same date-anchor
  gate, so DQbench (no DOB column) is untouched; matchkeys are OR'd, so it only
  adds candidate pairs. Lifts Febrl3 F1 0.912 → 0.924 (CI smoke).

### Internal

- CI Febrl3 smoke-gate floor raised 0.85 → 0.90 (#438), reflecting the recall
  improvements above (CI-measured Febrl3 F1 now 0.924, deterministic).

## [1.22.0] - 2026-05-27

### Added — field-level golden-record provenance at scale

- **`build_golden_records_batch(..., provenance=True)`** adds `source_row_id`
  to each field dict — the `__row_id__` of the record whose value won
  survivorship for that field — while preserving the single-group_by-per-column
  vectorization (one extra agg expr per column on the fast path). `None` for
  all-null fields; raises if there's no `__row_id__` column.
- **`config.output.lineage_provenance`** (default `False`): when enabled, the
  lineage sidecar (`{run_name}_lineage.json`) gains a `golden_records` section
  with per-field provenance (value, `source_row_id`, strategy, confidence)
  for every cluster. Default off because at large scale this materializes one
  provenance object per cluster plus a large JSON sidecar; the vectorized
  builder is what makes it feasible. `candidates` is empty in this path (the
  per-row candidate list is the slow-path-only detail that breaks
  vectorization).
- **`golden_records_to_provenance(records, clusters, rules)`** adapts the
  batch builder's output to the `ClusterProvenance` shape lineage consumes.

## [1.21.0] - 2026-05-27

### Added — optional native acceleration runtime

- **`goldenmatch[native]` extra** pulls in `goldenmatch-native`, a separately
  distributed compiled (Rust/PyO3, abi3) runtime — the polars / polars-runtime
  split. `goldenmatch` itself stays a pure-Python wheel; install
  `pip install "goldenmatch[native]"` (or `pip install goldenmatch-native`
  directly) and it's discovered automatically. Absent it, the pure-Python paths
  run unchanged.
- With the runtime present, the auto-config planner routes simple/fast-box plans
  through the **native Arrow block-scorer** (1.7–3.7x faster at 1k–60k rows,
  byte-identical clusters). Opt out with `GOLDENMATCH_PLANNER_BUCKET=0`.
- Wheels: linux x86_64 + aarch64, windows x64, macOS x86_64 + arm64 (CPython
  3.11+).

### Added — stable record fingerprint

- **`record_fingerprint(record)`** in the public API (and the TypeScript port):
  a canonical, cross-language SHA-256 over a type-tagged, key-sorted byte
  canonicalization. Stable across Python/TypeScript/SQL surfaces and used to
  derive identity record ids.

## [1.20.0] - 2026-05-26

### Added — cluster-decision tuner (RFC from MJH Print Modernization)

Third tuner in the Learning Memory family, paralleling the
pair-level `MemoryLearner` and field-level `tune_field_strategy`.
Consumes cluster-level approve/reject decisions and proposes an
updated auto-approve threshold per-dataset.

- **`Decision.CLUSTER_DECISION`** enum value (`"cluster_decision"`).
- **`Correction.cluster_score`** + **`Correction.cluster_outcome`**
  optional fields. Default None for pair-level + field-level rows.
- **`MemoryStore.record_cluster_decision(dataset, cluster_id, score,
  outcome)`** convenience wrapper.
- **`MemoryStore._migrate_cluster_decision_columns()`** idempotent
  ALTER TABLE for pre-existing v1.18.2+ DBs.
- **`tune_decision_threshold(store, dataset, *, target_approve_rate,
  min_band_n, holdout_frac, max_overfit_drop_pp, seed)`** in
  `core/autoconfig_cluster_threshold_tuner.py`. Returns
  `ThresholdSuggestion(threshold, n_total, n_train, n_heldout,
  train_approve_rate, heldout_approve_rate, reason)`. Same shape as
  `StrategyTuning` so a single dashboard can render both.
- Deterministic shuffle seeded by `sha256(dataset)[:8]` (override via
  `seed` kwarg). Avoids PYTHONHASHSEED non-determinism.
- 90/10 train/heldout split + 1pp overfit guard (configurable).
- Reasons: `"ok"`, `"below_minimum"`, `"no_qualifying_band"`,
  `"overfit"`.

Spec: `docs/superpowers/specs/2026-05-22-cluster-decision-tuner-design.md`

## [1.19.0] - 2026-05-22

### Added -- v1.18 surface-sync roadmap (Phases 1 + 2)

Brings 6 programmatic surfaces into parity with v1.18.2's field-level
Corrections + 22 predefined plugins.

**Phase 1** (originally targeted v1.18.3; folded into v1.19.0 since
Phase 2 merged first):

- **Python API re-exports.** `PluginRegistry`, `BUILTIN_PLUGINS`,
  `Decision`, `CorrectionSource` are now importable from the
  top-level `goldenmatch` package.
- **MCP `add_correction` schema extension.** `decision` enum gains
  `"field_correct"`. Three new optional properties:
  `field_name`, `original_value`, `corrected_value`. `cluster_id`
  property added for field-level shape. Pair-level path unchanged.
- **MCP `list_plugins` tool.** Lists all registered goldenmatch
  plugins by category. Each entry includes `name`, `category`,
  `source` (builtin / user), and the merge-docstring summary.
- **CLI `goldenmatch memory add` command.** Supports both
  pair-level (`--id-a` / `--id-b`) and field-level (`--cluster-id`
  + `--field-name` + `--corrected-value`) shapes via `--decision`.
  Source defaults to `steward` (trust 1.0); override via `--source`.

**Phase 2:** REST API CRUD.

- `POST /api/v1/memory/corrections` -- pair AND field-level shapes;
  source defaults to "rest" with trust=0.8.
- `GET /api/v1/plugins` -- discovery endpoint; category filter;
  builtin vs user source tagging.
- `GET /api/v1/memory/corrections` response gains field-level fields.

Specs:
- `docs/superpowers/specs/2026-05-22-phase-1-discovery-mcp-parity-design.md`
- `docs/superpowers/specs/2026-05-22-phase-2-rest-api-crud-design.md`

## [1.18.2] - 2026-05-22

### Added

- **Field-level Correction support** (#437). `Correction` dataclass
  gains three optional fields: `field_name`, `original_value`,
  `corrected_value`. `Decision.FIELD_CORRECT` enum value identifies
  the new shape. SQLite migration adds three TEXT columns to
  pre-existing DBs via idempotent `ALTER TABLE`. Tuner
  `_strategy_would_match` runs in two regimes -- field-level
  (predicts strategy fit from edit shape) or pair-level (old
  heuristic). Two-tier corpus selection: field-specific corrections
  beat dataset-wide signal when above threshold.

- **22 predefined golden-strategy plugins** auto-registered via
  `PluginRegistry.discover()` (#442, #443). User opts in via
  `strategy="custom:<name>"` in YAML/Python config. User
  entry-point plugins with the same name override builtins.

  - **Numeric (6):** `numeric_max`, `numeric_min`, `numeric_mean`,
    `numeric_median` (outlier-resilient), `numeric_sum` (aggregate
    amounts), `numeric_weighted_average` (uses `quality_weights`).
  - **Format-canonical (7):** `shortest_value`, `concat_unique`
    (sorted comma-join with configurable separator),
    `email_normalize` (lowercase + strip plus-addressing),
    `phone_digits_only`, `url_canonical` (lowercase host, http→https,
    trim /), `whitespace_normalize`, `boolean_normalize`.
  - **Business-shaped (6):** `system_of_record` (priority-config),
    `lifecycle_stage` (most-advanced via `lifecycle_order`),
    `freshness_with_max_age` (compliance NULL-emit),
    `enum_canonical` (alias_map → canonical), `regex_validated`
    (pattern filter; fallback configurable), `weighted_by_recency`
    (exponential decay).
  - **Aggregation / telemetry (3):** `count_distinct`,
    `count_non_null`, `agreement_rate` (0.0-1.0 mode-agreement).

### Spec

- `docs/superpowers/specs/2026-05-22-predefined-merge-plugins-design.md`
- `docs/superpowers/specs/2026-05-22-golden-strategy-plugin-slot-design.md`
  (carried from v1.18.0; expanded in v1.18.2 with builtin
  registration order)

## [1.18.1] - 2026-05-22

### Added — golden-rules intelligence layer 2

User feedback: "could be more intelligent." Three of four follow-up
lifts shipped; #4 (LLM-assisted picks) deferred to its own PR.

- **Per-source consensus agreement.** `source_priority` ranking now
  uses per-source agreement-with-cluster-consensus rate, not just
  completeness. Catches "complete but wrong" sources. New
  `RefinementSignals.per_source_agreement` populated from
  cluster-mode voting; falls back to completeness when < 10 attempts
  per source.
- **MemoryStore-learned strategy tuner.**
  `core/autoconfig_golden_strategy_tuner.py`. 90/10 train/heldout,
  5pp overfit guard, gated on >= 50 corrections per dataset,
  env-overridable via `GOLDENMATCH_GOLDEN_TUNER_MIN_CORRECTIONS`.
  Refiner consults tuner FIRST per field; falls back to heuristics
  on `no_memory` / `below_minimum` / `overfit_guard`.
- **Per-cluster strategy overrides.**
  `GoldenRulesConfig.cluster_overrides: dict[int, dict[str,
  GoldenFieldRule]] | None`. Refiner sets per-cluster, per-field
  overrides based on cluster shape:
  - `cluster_quality='weak'` → `unanimous_or_null`
  - `oversized` clusters → `confidence_majority`
  - `size == 2` clusters → `unanimous_or_null`

  `_polars_native_eligible` returns False when overrides are set
  (fast path can't honor per-cluster picks).

### Deferred to v1.19+

- **LLM-assisted picks for ambiguous fields** (issue #430). Needs
  prompt design + benchmark measurement before shipping.

Spec: `docs/superpowers/specs/2026-05-22-golden-rules-intelligence-layer-2-design.md`

## [1.18.0] - 2026-05-22

### Added — intelligent golden-field consolidation

Two-phase auto-config: matchkey + blocking stays pre-cluster
(unchanged); golden-rules picking moves POST-cluster where it
benefits from real cluster shape signals.

- **Three new strategies** in `VALID_STRATEGIES` and `core/golden.py`:
  - **`longest_value`** — pick the longest non-null string.
    Quality-weighted tie-break. For free-text fields where length
    correlates with completeness (address line, description).
  - **`unanimous_or_null`** — emit the value only if every non-null
    member agrees; emit NULL on any disagreement. For compliance-grade
    fields where a heuristic-chosen value is worse than missing.
  - **`confidence_majority`** — majority vote weighted by within-cluster
    pair_scores. Surfaces the consensus the clustering itself trusts;
    a strong-edge minority can beat a weak-edge majority. Falls back to
    count-majority when pair_scores absent.
- **`GoldenRulesRefiner`** (`core/golden_rules_refiner.py`). Runs
  between `build_clusters` and `build_golden_records` when
  `golden_rules.adaptive=True`. Computes per-field signals
  (within-cluster spread, per-source completeness, date-column
  coverage, col_type, null_rate, avg_len) and emits refined
  `GoldenRulesConfig.field_rules`. Rule table:
  - `col_type=date` + > 50% cluster timestamp coverage → `most_recent`
  - One source > 1.5× median completeness → `source_priority`
    (ranked by completeness)
  - Free-text + long + within-cluster disagreement → `longest_value`
  - `null_rate > 0.5` → `first_non_null` fast path
  - High within-cluster spread (> 2.0) → `confidence_majority`
  - Else → defer to base default
- **`GoldenRulesConfig.adaptive: bool = False`** opt-in flag. Default
  False to preserve existing behavior on benchmarks; flip-to-True is
  a v1.19 candidate after benchmark validation.

Spec: `docs/superpowers/specs/2026-05-22-intelligent-golden-rules-design.md`

### Deferred (v1.19+ candidates)

- **Custom Python plugin slot** (`strategy="custom:my_rule"`). Protocol
  shape is load-bearing; deserves its own spec + PR.
- **Default flip** of `adaptive=True` once benchmarks confirm no F1
  regression.

## [1.17.1] - 2026-05-22

### Changed — streaming-sync match log throughput (#424, PR #426)

- **`log_matches_batch` uses `cursor.copy()` on psycopg3**. The previous
  `executemany` path was NOT pipelined without an explicit
  `with conn.pipeline():` context, capping throughput at ~125 rows/sec
  on the user's 1.13M-row managed-Postgres workload (~7ms RTT × N
  round-trips per batch). `cursor.copy("COPY gm_match_log ... FROM STDIN")`
  is 10-100× faster on bulk loads. Falls back to `executemany` for
  non-psycopg3 cursors (SQLite / DuckDB test paths).
- **Buffer pairs across blocks before flushing**. Default
  `GOLDENMATCH_MATCH_LOG_FLUSH_PAIRS=10000`. Final flush at end of
  streaming loop covers the tail. Set to `1` for per-block flush
  (preserves the historical incremental-progress behavior + test
  contract).
- **Opt-out via `GOLDENMATCH_SKIP_MATCH_LOG=1`**. Nightly-cron pipelines
  that only consume `gm_clusters` / `gm_golden_records` can skip
  per-pair audit logging entirely. INFO log line surfaces when set.

### Notes

User-reported impact: 125 rows/sec ceiling → projected 10K+ rows/sec.
For the user's ~3M-pair workload, ~5 hours of writes → < 5 min.

## [1.17.0] - 2026-05-22

### Added — v1.13 autoconfig roadmap (2026-05-21, PRs #415/#416/#418)

- **ADR practice** (`docs/adr/`). 7 foundational records capturing
  load-bearing architectural decisions:
  - ADR-0000: Adopt ADR practice
  - ADR-0001: `confidence_required=True` as the default safety gate
  - ADR-0002: Unified `exclude_columns` API surface across the suite
  - ADR-0003: Matchkey vs blocking pools as orthogonal axes
  - ADR-0004: Chao1 sample-size correction for autoconfig cardinality
  - ADR-0005: Streaming-block sync as the >500K-row path
  - ADR-0006: Telemetry-gated rule deprecation
- **Wave A — stratified autoconfig sampling (#131)**. `_sample_one`
  picks a mid-cardinality column (prefers `zip` / `state` / `postal`
  names) and stratifies by it; rare strata get a `min_per_stratum=10`
  floor. Falls back to uniform-random when no qualifying column.
  Env-rollback: `GOLDENMATCH_AUTOCONFIG_SAMPLE_STRATEGY=random`.
- **Wave A — iteration-oscillation guard (#127)**.
  `HeuristicRefitPolicy` tracks the last `(rule_name, rationale)` that
  fired; identical fire next call → skip, policy advances. Different
  rationale bypasses; first call never blocks.
- **Wave B — real `ExpandSample(2.0)` controller action (#125)**.
  `PolicyDecision` gains `expand_sample: float | None` field.
  `rule_sparse_match_expand` emits `expand_sample=2.0` instead of
  proxying via threshold-lowering. `AutoConfigController.run`
  intercepts and resamples `df` with `sample_size_default *= factor`
  before the next iteration. Capped at 5× initial.
- **Wave B — telemetry log for `rule_demote_clustered_identity` (#124)**.
  Env-gated INFO log (`GOLDENMATCH_TELEMETRY_DEMOTE_RULE=1`) for the
  1-2 week observation window. Deletion gated on zero firings; see
  ADR-0006 for the pattern.
- **Wave D — NE-on-Fellegi-Sunter investigation (#126)**. Concluded
  document-and-close. Three formulations evaluated; none cleanly
  preserve LLR additivity without labeled data that Wave E provides
  downstream. `MatchkeyConfig.negative_evidence` field docstring
  updated to document the non-goal + the
  `GOLDENMATCH_NE_FS_ESCAPE_MODE=floor` escape hatch.
- **Wave E — adaptive NE tuner module (#129)**.
  `core/autoconfig_ne_tuner.py`. `tune_ne_field(store, dataset, field)`
  runs grid search over `PENALTY_GRID × THRESHOLD_GRID`, 90/10
  train/heldout split, overfit guard at 5pp F1 drop. Gated on ≥ 50
  MemoryStore corrections. Env-overridable
  `GOLDENMATCH_NE_TUNER_MIN_CORRECTIONS`. Integration with
  `promote_negative_evidence` is the natural follow-up; this lands
  the tuner module + tests with a stable API.
- **v1.13 autoconfig roadmap** at
  `docs/superpowers/specs/2026-05-21-v1-13-autoconfig-roadmap.md`
  sequencing 6 issues into 5 waves with explicit kill criteria.
- **Composite-search candidate log (#417)**. Every evaluated pair in
  `find_composite_blocking_keys` emits an INFO line:
  `pair (c1, c2) joint_cardinality=K -> in_band|below_band|above_band`.
  Header + winner-or-no-pair summary. Eliminates the "why didn't it
  pick X" black-box.
- **`degenerate_guard_max_avg_block_size()` helper (#417, default 10K)**.
  Env-overridable
  `GOLDENMATCH_BLOCKING_DEGENERATE_MAX_AVG_BLOCK_SIZE`.

### Changed — autoconfig safety + correctness (2026-05-21, PRs #411 → #423)

- **Sample-corrected blocking via Chao1 (ADR-0004, PR #411)**.
  `core/blocking_candidates.py::scale_cardinality_ratio_to_full_population`
  projects sample-observed distinct values to full-population
  cardinality using `sample_distinct × √(N_full / N_sample)`. Same
  scaler reused by `estimate_avg_block_size`. Env-rollback:
  `GOLDENMATCH_BLOCKING_CARDINALITY_SCALER=observed` reverts to linear.
- **Controller threads true `n_rows_full` into v0 (#414)**.
  `AutoConfigController._initial_config` accepts a new
  `n_rows_full: int | None` kwarg and injects into `v0_kwargs`.
  `_legacy_auto_configure_v0` reads it as `total_rows`. Without this,
  v0 saw `df.height` (the sample size) and the Chao1 short-circuit
  fired, defeating the gate.
- **Composite-search wired into `build_blocking` (#411)**.
  `find_composite_blocking_keys` was already implemented + tested but
  never called from the real path. Now inserted as a fall-through
  between single-column candidates (`exact_cols` / `name_cols`) and
  the text-canopy / first-string last resort.
- **`BLOCKING_DEGENERATE` guard catches mega-block case (#419)**.
  Existing guard only caught singleton blocks (`avg_block_size < 2`);
  upper-bound now catches `avg_block_size > 10K` (the
  every-row-in-one-block scenario that wedges downstream
  `bucket_score` in O(n²) on one core).
- **`BLOCKING_DEGENERATE` guard catches empty `blocking.keys` (#420)**.
  Pre-#420 precondition required `keys` non-empty, which short-circuited
  exactly the case the guard exists for. Restructured into three RED-
  gated branches: `blocking is None`/`keys=[]`, `keys` present but
  `fields=[]`, and the existing estimator-based check.
- **`--exclude-columns` enforced PRE-autoconfig in `goldenmatch sync` (#421)**.
  CLI now parses excludes and sets `_RUNTIME_EXCLUDE_COLUMNS` ContextVar
  before calling `auto_configure`, then resets in `finally`. Previously
  the merge happened post-hoc, after matchkeys + blocking were already
  built. Excluded columns no longer slip through as matchkeys.
- **`BLOCKING_DEGENERATE` guard inspects matchkey-derived blocking (#421)**.
  When `config.blocking.keys` is empty, the streaming sync derives
  effective blocking from `matchkeys[0].fields`. Guard now falls back
  to those fields (gated on profile.health() == RED) so the implicit-
  blocking degenerate case is caught at the controller level.

### Changed — streaming-sync throughput (2026-05-22, PR #423)

- **`score_buckets` small-block fast path (#422)**. When
  `prepared_df.height < n_buckets`, the hash + `partition_by(__bucket__)`
  step always collapses to 1 bucket by pigeonhole. Skip the bookkeeping
  on small inputs; treat `keyed` directly as the single bucket. Removes
  3 Polars ops per block on the user's 585K-block 1.13M-row workload.
- **`_full_scan_streaming` parallel outer loop (#422)**. Per-block
  scoring dispatched via `ThreadPoolExecutor(max_workers=min(cpu_count, 8))`.
  Env override: `GOLDENMATCH_STREAMING_BLOCK_WORKERS`. Cross-block
  dedup deferred to a single canonical `(min, max)` pass over the
  merged pair list. Serial fallback preserved for ≤ 2 work items.
- **`MemoryStore` enables SQLite WAL mode by default (#130, PR #413)**.
  `PRAGMA journal_mode=WAL` on every open. Resolves "database is
  locked" intermittent failures when two `MemoryStore` instances point
  at the same file. Allows one writer + multiple readers concurrently.

### Fixed

- `goldenmatch sync` cluster: four user-visible bugs that all surfaced
  from one Postgres sync run against a 1.13M-row table on a slim Python
  build.
- **#410** post-#409 follow-up: composite-search wired but never fired
  because v0 saw sample-sized `total_rows` (full-pop count wasn't
  threaded through controller). Fixed in #414. Spec:
  `docs/superpowers/specs/2026-05-21-blocking-pool-followup-design.md`.
- **#417** v1-v3: autoconfig wedge on 1.13M-row real-world Postgres
  table. Resolved across PRs #419 → #420 → #421 → #423. User
  confirmed correctness fix + 585K small-block run with sub-second
  match-log lag. See ADR-0003.
- **#422** throughput: small-block fast path + parallel outer loop.
  Target acceptance: < 30 min on 8 vCPU / 16 GB for the user's
  585K-block table.

### Added (Phase 5 — Splink-Spark roadmap, pre-existing)

- `bench-phase5-simulated` workflow job: runs a 4-worker Ray cluster
  inside one `large-new-64GB` GitHub runner against 50M synthetic rows,
  exercising the Phase 5 streaming pipeline without provisioning
  external infrastructure. Workflow_dispatch only (gated on
  `run_phase5_simulated=true`); optional identity variant via
  `run_phase5_simulated_identity=true`. Honest scoping (single NIC,
  single disk, shared OS page cache): this is a regression check, NOT
  a Splink-Spark parity proof. The real-cluster bench
  (`bench-phase5-end2end`) is still required for any parity claim.
- `scripts/bench_phase5_simulated.py`: cluster-agnostic Phase 5 bench
  driver (works against simulated AND real clusters, only `RAY_ADDRESS`
  differs). Connects to a pre-started Ray cluster, runs
  `run_dedupe_pipeline_distributed`, writes a JSON summary with wall
  + RSS + cluster resources.
- `scripts/generate_phase5_50m_dataset.py`: one-shot 50M dataset
  generator wrapper. Outputs `bench-dataset/bench_50000000.parquet`
  for upload to the `bench-dataset-v1` release. ~8 hr single-node
  generation cost.

### Fixed

- `goldenmatch sync` cluster: four user-visible bugs that all surfaced
  from one Postgres sync run against a 1.13M-row table on a slim Python
  build.
  - #362: `golden.py` no longer calls `top_k_by(..., reverse=False)`,
    which mis-binds args on newer Polars versions. Replaced with
    `sort_by(descending=True).first()`.
  - #363: chunked Postgres reads now cast `Null`-dtype columns to
    `Utf8` before `pl.concat`, fixing `type String is incompatible
    with expected type Null` on sparse-column tables. Defensive
    `how="diagonal_relaxed"` on the concat call too.
  - #364: `sqlite3` is now lazy-imported inside `ReviewQueue`,
    `MemoryStore`, and `IdentityStore`. `import goldenmatch` and the
    CLI no longer require `_sqlite3` on minimal Python builds (Vercel
    Sandbox, slim Docker, etc.).
  - #365: `_quote_ident("schema.table")` now produces
    `"schema"."table"`, allowing `goldenmatch sync --table gm.foo`
    against non-public schemas without a search_path workaround.

- `two_phase_wcc` no longer rehydrates a 475 MiB Python `dict[int, int]`
  in every worker. Phase B's lookup table now travels as a Polars frame
  via `ray.put`, zero-copy mapped from plasma into worker address space
  (~80 MiB at 5M nodes, ~6x smaller). Phase B internals use vectorized
  Polars joins instead of Python row-loops. Resolves the 5M-scale OOM
  surfaced by run 26166347530.

### Added (Phase 6 — Identity Graph at distributed scale)
- **psycopg3 migration** of the Postgres backend in `goldenmatch.identity.store`.
  `psycopg2-binary` replaced by `psycopg[binary]>=3.1`. Adds first-class
  COPY context manager support and `psycopg_pool` integration.
- **Bulk COPY writes**: `IdentityStore.bulk_upsert_identities`,
  `bulk_upsert_records`, `bulk_add_edges`, `bulk_emit_events`. Each
  COPYs a Polars frame into a staging temp table, then
  `INSERT ... ON CONFLICT` into the real table. Postgres only;
  SQLite raises `NotImplementedError` with a helpful message.
- **Connection pool**: `goldenmatch.identity.pool.get_identity_pool(dsn)`
  process-singleton `psycopg_pool.ConnectionPool`. `IdentityStore`
  constructor accepts optional `pool=` kwarg.
- **Alembic migrations**: `goldenmatch/db/alembic/` with baseline
  revision `0001_identity_v1` mirroring the existing schema. New CLI
  `goldenmatch identity migrate --dsn ... [--stamp-existing]` runs
  upgrades; `--stamp-existing` brings a pre-Alembic schema under
  version control without touching tables.
- **Distributed dispatch**: `goldenmatch.distributed.identity.resolve_identities_distributed`
  polymorphic on `dict[int, dict] | ray.data.Dataset`. Ray Dataset
  path materializes cluster aggregates driver-side via
  `materialize_cluster_dict`, then runs the existing resolver against
  a pooled Postgres connection.
- `goldenmatch.core.pipeline._resolve_identities` polymorphic on
  cluster shape; Ray Dataset path requires `identity.backend='postgres'`
  and `identity.connection` set.
- `IdentityStore.add_edge` on the Postgres branch now uses
  `ON CONFLICT (entity_id, record_a_id, record_b_id, kind, run_name) DO NOTHING`
  matching the SQLite `INSERT OR IGNORE` semantic. Fixes
  `UniqueViolation` on replayed runs.
- Tests: `tests/identity/test_pool.py`,
  `tests/identity/test_alembic_migration.py`,
  `tests/identity/test_uuid7_concurrent.py`,
  `tests/identity/test_distributed_identity.py`.

### Added (Phase 5.5)
- **Two-Phase WCC** (`goldenmatch.distributed.clustering.two_phase_wcc`)
  replaces label propagation as the default distributed connected-
  components algorithm. Phase A: per-partition local `UnionFind` via
  `map_batches` (embarrassingly parallel). Phase B: driver-side super-
  graph Union-Find on boundary edges between partitions (bounded by
  O(P^2) max edges).
- New env var `GOLDENMATCH_DISTRIBUTED_WCC` — default `two_phase`,
  opt back into `label_propagation` for regression testing.
- `UnionFind.nodes()` accessor on `goldenmatch.core.cluster.UnionFind`.
- `scripts/generate_chain_dataset.py` — synthetic chain-heavy graph
  generator for the adversarial bench (label-prop's worst case).
- `scripts/bench_phase5_5_wcc.py` — head-to-head bench timing both
  algorithms on the same input + asserting partition-structure
  equivalence.
- `bench-phase5-5-wcc` workflow job (workflow_dispatch only,
  `run_phase5_5_wcc=true`). Generates 5M-chain dataset on the fly.
- Motivated by GraphFrames maintainer Sem Sinchenko's recommendation:
  chains are label-prop's worst case and identity graphs are
  chain-heavy.

### Added (Phase 5)
- **Distributed scoring:** `goldenmatch.distributed.scoring`:
  - `score_blocks_distributed(df_ds, config)` — per-partition fuzzy +
    exact scoring via `ds.map_batches`. Each worker runs in-memory
    `dedupe_df` with `backend="bucket"` on its slice; pair tuples emitted
    as a Ray Dataset.
  - `dedup_pairs_distributed(pairs_ds)` — cross-partition pair dedup.
    Canonicalizes `(min, max)` ordering, groupby + max(score) keeps the
    highest score per canonical pair.
- **Phase 5 streaming pipeline** in `goldenmatch.distributed.pipeline`:
  `GOLDENMATCH_DISTRIBUTED_PIPELINE=2` activates `_run_phase5_pipeline`
  — auto-configure → distributed score → distributed cluster (Phase 3
  polymorphic) → annotate-with-cluster-id (broadcast dict via `ray.put`)
  → distributed golden (Phase 4 polymorphic) → write_parquet. No entry-
  side `take_all` on input. Phase 2 default cheat-line and Phase 4
  scaffold (`=1`) remain available.
- `output_path` kwarg on `run_dedupe_pipeline_distributed` writes the
  golden output to parquet at end of pipeline.
- **100M dataset generator:** `scripts/generate_phase5_dataset.py` —
  synthetic 100M rows with ~5-member clusters + 10% typo injection.
- **Multi-node Ray cluster docs:** `docs/distributed-ray-cluster-setup.md`
  — sizing recommendations, `ray up` config, KubeRay equivalent, network
  requirements, object store sizing, cost framing. Splink-posture:
  documentation, not bootstrap automation.
- **`bench-phase5-end2end` workflow job:** `workflow_dispatch` only,
  gated on `run_phase5_bench=true`. Requires `RAY_ADDRESS` secret +
  pre-uploaded `bench_100000000.parquet`. Runs on `ubuntu-latest`
  client; Ray work happens on the remote cluster.
- Kill criterion: 100M end-to-end < 30 min on a 4 × 16c/64GB worker
  Ray cluster.
- **Two-Phase WCC swap** (per GraphFrames maintainer advice on label-prop's
  chain pathology) **deliberately deferred to Phase 5.5** — separate
  spec/plan after we have real 100M chain-heavy graph data to verify
  against. Label propagation stays the distributed clustering algorithm
  with the existing routing threshold.

### Added (Phase 4)
- **Distributed golden record build:** `goldenmatch.distributed.golden`:
  - `build_golden_records_distributed(multi_ds, rules, user_columns)` —
    Ray Dataset `repartition(keys=["__cluster_id__"]) + map_batches`. Hash
    partitioning co-locates each cluster's rows; no cross-partition splits.
    `groupby.map_groups` not used — it re-enters Ray's streaming executor
    and deadlocks in 2.54.
  - `build_golden_records_smart` — cluster-count routing
    (default 5M threshold; `GOLDENMATCH_DISTRIBUTED_GOLDEN_THRESHOLD` override).
  - `materialize_golden_dataframe(golden_ds)` — adapter to `pl.DataFrame`.
- `core.golden.build_golden_records_batch` polymorphic on Ray Dataset
  input. In-memory `pl.DataFrame` callers byte-identical.
- Custom field rules + `quality_scores` always route to in-memory
  (closure serialization + N×K dict size considerations).
- `run_dedupe_pipeline_distributed` env-gated Phase 4 path
  (`GOLDENMATCH_DISTRIBUTED_PIPELINE=1`). Today the Phase 4 path retains
  the take_all cheat-line; the polymorphic dispatch in build_clusters
  (Phase 3) and build_golden_records_batch (Phase 4) handles Dataset
  callers directly. Phase 5 retires the take_all once scoring distributes.
- `scripts/bench_phase4_golden.py` + `bench-phase4-golden` workflow job.
  Kill criterion: golden wall < 180s at 25M.

### Added (Phase 3)
- **Distributed clustering:** `goldenmatch.distributed.clustering`:
  - `pairs_list_to_dataset(pairs)` — convert scored pairs to a Ray Dataset.
  - `label_propagation(pairs_ds, all_ids, convergence_max_iterations=30)`
    — iterative label-prop; raises `ConvergenceError` on non-convergence.
  - `build_clusters_distributed(pairs_ds, all_ids, ...)` — returns a Ray
    Dataset of `{member_id, cluster_id, cluster_size, oversized}` rows.
    Falls back to driver-side scipy.csgraph on non-convergence with a
    WARNING log.
  - `materialize_cluster_dict(clusters_ds, pairs_ds)` — adapter to the
    existing `dict[int, dict]` shape (Phase 4 removes this).
- `core.cluster.build_clusters` polymorphic on Ray Dataset input via
  `is_ray_dataset` dispatch; in-memory callers byte-identical.
- `scripts/bench_phase3_cluster.py` + `bench-phase3-cluster` workflow job.
- **Splink-style routing** in `build_clusters_distributed`:
  pair count < 50M routes to driver-side scipy.csgraph;
  >= 50M routes to distributed label propagation. Override via
  `GOLDENMATCH_DISTRIBUTED_CLUSTERING_THRESHOLD` env var or
  `force_label_propagation=True`. Calibrated against run `26119800863`:
  at 8.3M pairs, label-prop's per-iter Ray Dataset HashAggregate
  overhead (10-20s/iter) dominates; scipy.csgraph on the same pair
  count is seconds. Mirrors Splink's own pattern (DuckDB below scale,
  Spark above). Label propagation's binding multi-node proof point is
  Phase 5.
- The Phase 2 cheat-line in `run_dedupe_pipeline_distributed` stays in
  place — Phase 4 distributes golden and removes the materialize.

### Added (Phase 2)
- **Distributed controller:** `AutoConfigController.run` polymorphic on
  `pl.DataFrame | ray.data.Dataset`. New modules under `goldenmatch.distributed`:
  - `indicators` — `compute_column_priors_distributed`,
    `estimate_sparse_match_signal_distributed` (bounded-sample collect).
  - `sample` — `take_sample_distributed(ds, sample_cap)` returns a
    Polars DataFrame for the iteration loop.
  - `pipeline` — `run_dedupe_pipeline_distributed`: materialize-then-call
    cheat-line for `_finalize` (Phase 3 removes the materialize).
  - `_utils.is_ray_dataset` — module-name duck-type helper.
- Dispatch shims in `core.indicators`: `dispatch_compute_column_priors`,
  `dispatch_estimate_sparse_match_signal`.
- `scripts/bench_phase2_controller.py` + `bench-phase2-controller`
  workflow job. **Result, run 26107123459: 25M / 94.2s controller wall /
  1.08 GB driver peak RSS.** Architectural goal MET (driver does not
  materialize the full df). Wall budget realigned from < 30s to < 180s
  after measurement — per-iter `_run_pipeline_sample` on bench-dataset-v1
  is ~30s, independent of distributed wiring. Phase 3 distributes the
  per-iteration pipeline to remove that cost.

### Added
- **Distributed Phase 1: partition-aware data loader on Ray Datasets.**
  Opt-in via `GOLDENMATCH_ENABLE_DISTRIBUTED_RAY=1` env var combined with
  `backend="ray"`. New module `goldenmatch.distributed`:
  - `read_csv_partitioned(path, n_partitions, schema=None)` — returns a
    lazy `ray.data.Dataset` partitioned into N blocks. Driver never holds
    the full input frame. Supports single path or list of paths.
  - `apply_transforms_distributed(ds, transforms)` — runs a list of
    `TransformPlan`s per-partition via `ds.map_batches(batch_format="pyarrow")`.
  - `TransformPlan` (in `goldenmatch.distributed.transforms`) — frozen
    dataclass replacing closure-based transforms from `core/transform.py`.
    Round-trips cleanly across Ray worker boundaries. Ops: `lower`, `upper`,
    `strip`, `strip_punctuation`, `nfkc`.
  - `_load_input_frames(config)` in `core/pipeline.py` — env-gated branch
    point. Routes to the distributed loader only when both `backend="ray"`
    AND the env flag are set; otherwise legacy `core.ingest.load_files`.
- **Phase 1 kill-criterion bench:** `scripts/bench_phase1_loader.py` +
  `bench-phase1-loader` job in `.github/workflows/bench-distributed-stack.yml`.
  Kill criterion: driver peak RSS < 8 GB at 25M rows on a 16c/64GB runner.
  **PASS, run 26100132595: 25M rows in 14.7s prep wall at 0.24 GB driver
  peak RSS (33x margin under the 8 GB threshold).** Phase 2 (controller
  iteration on distributed samples) unblocks. Three bench-harness fixes
  preceded the green result: pandas + pipefail (#338), parquet support
  (#339), bench-dataset-v1 column names (#340).
- **`core/transform.build_transform(column, op)`** back-compat shim: returns a
  closure that delegates to `apply_plan(df, TransformPlan(column, op))`.
  Callers still consuming the callable-style transform get equivalent output.

### Notes
- Phase 1 is plumbing only — `_load_input_frames` is NOT yet wired into the
  default pipeline path. Production runs without the env flag continue
  through `core.ingest.load_files` byte-for-byte. Phases 2-5 (controller
  iteration on distributed samples, distributed clustering, distributed
  golden, cluster orchestration) remain TODO. See
  `docs/superpowers/specs/2026-05-19-ray-splink-spark-parity-roadmap.md`.
- The 25M-on-bucket single-node path landed in the same window (run
  26095134836, 6.5 min / 57.7 GB peak RSS) and is the supported
  recommendation for the 5M-25M lane. Phase 1 is value-add for 50M+
  workloads, not a prerequisite for 25M.

## [1.16.0] - 2026-05-18

<!-- README-callout
**5M records in 9.94 min, 6.4 GB peak RSS, on one 16-core node** — the new `backend="bucket"` path is now the recommended 5M-on-one-node config. 5x wall reduction and 2x peak RSS reduction vs the v1.15 chunked baseline (~50 min, 11.9 GB), with rock-solid reliability on Linux runners where the chunked path was hanging at 63 GB plateau on the same fixture. PRs #310-#326.
-->

### Added -- 5M-on-one-node performance pass (PRs #310-#326)

The 5M-record workload on a 16-core / 64 GB Linux runner went from "chunked path hangs at 63 GB plateau" to a **clean 9.94-minute completion at 6.4 GB peak RSS** through a focused set of in-process pipeline optimisations. Headline:

| Path | Wall | Peak RSS | Reliability |
|---|---|---|---|
| Pre-v1.16 `chunked` on same fixture | hung @ 63 GB plateau | — | never completed |
| **v1.16 `backend="bucket"`** | **596s / 9.94 min** | **6.4 GB** | reliable |

What landed:

- **`backend="bucket"` (PR #308)** — in-process bucket scorer that replaces the per-block `LazyFrame` model. A single `with_columns(hash(block_key) % N)` plus one `partition_by("__bucket__")` produces N≈64 eager bucket DataFrames; per-block scoring runs inside each bucket without re-materialising millions of small frames. The legacy chunked path's per-block `.collect()` on filter-LazyFrames was the root cause of the Linux arena pathology where 1.67M small frames OOM-killed the runner.
- **Bucket fast path for tiny-block / weighted workloads (PR #320)** — when a matchkey has no negative-evidence, no rerank, no LLM, no `record_embedding`, and a single-source dedupe, the scorer pre-resolves each field's `score_pair` callable + xform column ONCE at entry, then iterates row pairs inside each block via direct Python loops with inlined scorer calls. At 5M / 1.67M 3-row blocks this is **~24× faster** than the previous `find_fuzzy_matches` per block path (2513s → 91s on the bench).
- **Pre-fuzzy stage sweep (PRs #310/#311/#312)** — three independent Python-UDF bottlenecks fixed: `analyze_blocking` now samples to 100K rows before scoring candidates; the bucket scorer drops a wasteful `_BlockShim` + `LazyFrame.collect()` round-trip; `_build_block_key_expr` uses native Polars expression chains (`lowercase`, `strip`, `substring`, …) instead of `pl.col().map_elements(apply_transforms)` per row.
- **Golden batch builder + Polars-native fast path (PRs #322/#324)** — `build_golden_records_batch` pre-extracts each user column to a Python list ONCE per multi_df instead of calling `cluster_df[col].to_list()` 6.7M times. For the common "uniform default_strategy, no field_rules, no quality_scores" case, the new `_build_golden_records_polars_native` path computes the entire stage via Polars `group_by(...).agg(top_k_by(len)).first()` per column — golden stage 307s → 122s at 5M.
- **Cluster pure-Python UnionFind retained.** A scipy.csgraph rewrite (PR #323) regressed cluster from 121s → 297s at 3.3M-cluster scale because the Polars list-of-lists materialisation for per-cluster members cost more than the original Python loop saved. Reverted in PR #326; the Python UnionFind path is the keeper.
- **Bench harness gates** (PRs #314 / #316 / #321) — bench is now metrics-only (never materialises `result.clusters` at scale), passes `_skip_finalize=True` to `auto_configure_df` so the controller doesn't double-run the full pipeline, and gates the legacy Distributed-Plan-v1 treatment lane behind `--run-treatment` (default off; see soft-revert note below).

### Changed -- Distributed Plan v1 soft-reverted (PRs #318/#321)

The Distributed Plan v1 stack (`prepared_record_store=True` + `partitioned_block_scoring=True` + `backend="ray"`) FAILED the binding 5M kill criterion set in `docs/superpowers/specs/2026-05-15-distributed-plan-v1-design.md`: on the same `large-new-64GB` runner where the new bucket path completes in 9.94 min at 6.4 GB peak RSS, the distributed stack climbed past baseline's peak within minutes and had to be cancelled.

The fix is a SOFT revert: code paths stay (PRs #280-#287 untouched), but the v3 planner no longer auto-picks ray. Set `GOLDENMATCH_ENABLE_DISTRIBUTED_RAY=1` to opt back in, or pass `backend="ray"` explicitly. Defaults for `prepared_record_store` and `partitioned_block_scoring` are unchanged (already False).

### Added -- `_skip_finalize` is now a documented stable knob

Already a kwarg on `auto_configure_df` but previously underscore-prefixed and undocumented. The bench harness uses it (PR #316) to prevent the controller from running the full pipeline inside `auto_configure_df` (which masquerades as a hang and double-counts wall in any external benchmark). Users running their own benches against an explicit config should pass `_skip_finalize=True`.

### Bench progression (5M, `large-new-64GB`, `backend="bucket"`)

| PR | Wall | Δ | bucket_score | golden | Notes |
|---|---|---|---|---|---|
| v1.15 baseline (`chunked`) | hung @ 63 GB | — | — | — | never completed on same fixture |
| #308 (bucket backend) | ~54 min | — | ~30 min | ~5 min | reliable |
| #317 (drop shim) | 48 min | -10% | 36 min | 5 min | |
| #320 (fast path) | 13.2 min | -73% | 1.5 min | 5 min | the headline |
| #322 (golden batch) | 11.2 min | -15% | 1.5 min | 3.3 min | |
| **#324 (golden polars-native) — v1.16.0** | **9.94 min** | **-11%** | **1.5 min** | **2.1 min** | shipping |

## [1.15.0] - prior

<!-- README-callout
**5M records in ~50 min on commodity hardware** — Chunked mode now actually delivers on its "1M to 100M+" promise. The streaming `scan_csv().slice()` reader + Polars-native cross-chunk join (B) + block-keyed bucketed index (C) + DuckDB pair-store backend (D) replace a broken eager-read + Python-double-loop path that OOM-killed at 3h+ on the pre-fix 5M dispatch. **Measured: 5M records, 50 min wall, 11.9 GB peak RSS, 618,817 multi-member clusters, no OOM** on a 4c/16GB GitHub runner. Pass `backend="chunked"` with an explicit blocking config. PRs #233/#234/#235.
-->

### Added -- Chunked-mode out-of-core delivery (PRs #233/#234/#235)

The pre-fix `core/chunked.py` claimed "1M to 100M+ records" in its docstring but eagerly materialized the full CSV via `pl.read_csv` before slicing, and ran a Python double-loop in `_match_against_index` that was O(chunk_size × index_size) per chunk — making the chunked path strictly worse than `polars-direct` past 1M. A scale audit dispatched at 5M on 16 GB ran for 3h 36m without completing.

Three landed PRs fixed it:

- **B (#233) — `feat(chunked): vectorize cross-chunk match via Polars`** — `_match_against_index` no longer loops; it `pl.concat`s the chunk slim slice with the persistent index, recomputes matchkey-derived columns, and runs `find_exact_matches` + `build_blocks` + `score_blocks_parallel` over the joint frame, filtering to cross-pairs. Same machinery as the within-chunk path. `_index_df: pl.DataFrame` replaces `_index_records: list[dict]`. Expected constant-factor improvement 50–200× from rapidfuzz cdist replacing per-pair `score_pair` calls.
- **C (#234) — `feat(chunked): block-keyed cross-chunk index lookup`** — adds `_index_by_block: list[dict[str, pl.DataFrame]]` for `strategy="static"` blocking. Cross-chunk weighted matching now groups the chunk by block key, looks up the matching bucket per block, synthesizes mixed `BlockResult`s, and scores with `target_ids=chunk_ids`. **Pure-index blocks are never instantiated.** Algorithmic shift from O(joint_size × avg_block) to O(Σ over chunk-blocks K of \|chunk[K]\| × \|index[K]\|).
- **D (#235) — `feat(backend): score_blocks_duckdb out-of-core pair store`** — wires `config.backend="duckdb"` into `_get_block_scorer` so the in-package DuckDB backend is finally non-empty. Pair accumulator moves from Python `list` to a DuckDB table (`:memory:` by default; `db_path="auto"` or `GOLDENMATCH_DUCKDB_SCORE_DB` for spill-to-disk). Per-block rapidfuzz cdist work is unchanged — D's value is at 50M+ where pair counts hit 10⁸, not at 5M.

Measured at 5M (scale-audit 2026-05-14):

| Lane | Wall | Peak RSS | Result |
|---|---|---|---|
| `backend=chunked` (B+C) | 50 min | 11.9 GB | ✅ 618,817 multi-member clusters |
| `backend=duckdb-backend` (D smoke) | OOM ~25 min | — | ❌ in-memory scoring matrices still hit 16 GB ceiling |

The `chunked` lane is the new recommended path for 5M+ on commodity hardware. `duckdb-backend` is infrastructure for 50M+ once paired with future SQL-native scoring.

### Changed -- Refdata autoconfig hook gates on ColumnProfile.col_type (strategy direction #8, ninth slice)

Finishes the deferred slice from #8. Refdata refinements (surname scorer swap, given-name alias scorer swap, legal-form-strip, address-normalize, NAICS-normalize) now consult the profiled data shape, not just the column name. A column literally named `last_name` but holding numeric IDs, dates, or hashed identifiers no longer gets its scorer silently swapped — the swap was previously a quality regression on those shapes, hidden behind the column-name match.

- **`refine_matchkey_field()`** gains an optional `col_type: str | None = None` parameter. Per-rule accept-lists: name swaps require `col_type in {"name", "multi_name"}`; company `legal_form_strip` accepts `{"name", "multi_name", "description", "string"}` (free-text company names sometimes profile as description/string); address `address_normalize` accepts `{"address", "string"}`; NAICS `naics_normalize` accepts `{"identifier", "numeric", "string", "description"}` (NAICS codes commonly land as identifier or numeric).
- **`col_type=None` preserves the legacy name-only behavior** so callers without a profile (ad-hoc use, tests) don't change shape.
- **Call sites in `core/autoconfig.py`** at both `build_matchkeys()` and `build_probabilistic_matchkeys()` now pass `p.col_type` from the `ColumnProfile`.
- **Tests** in `tests/test_refdata_autoconfig.py` cover the gate firing and skipping for each refinement, the `None` backwards-compat path, and end-to-end via `build_matchkeys()` on a synthetic frame where `last_name` holds numeric data — the surname scorer should NOT fire.

### Changed -- Refdata cross-pack cleanup (strategy direction #8, eighth slice)

Refactor-only PR — no behavior change to any user-facing surface. Addresses the systemic findings from the parallel review of slices #1–#7. Five refdata modules (surnames, given_names, business, addresses, industries) plus the plugin registry are touched in one coordinated change.

- **Frozen-dataclass state** replaces the `_state: dict[str, Any]` lazy-load pattern in all five refdata modules. Each module now has a small `_<Pack>State` `@dataclass(frozen=True)` with explicit fields and types. Module-level `_state: _<Pack>State | None`; `None` means "not loaded or data file missing". Reviewer's concern: the dict-pattern carried 4–6 implicit invariants per copy with no static type backing; a typo silently wrote a string to an int field with no detection.
- **Atomic-swap `_reload()`** falls out for free: it just sets `_state = None` under the lock; the next reader drives `_load()` which assigns a freshly built dataclass. Compared to the previous `_state["loaded"] = False` + dict-wipe pattern, readers never see a half-built state mid-reload. This was flagged on industries.py in the PR #222 review-fix; the pattern is now consistent across all five packs.
- **Plugin Protocols bound and enforced.** `goldenmatch/plugins/base.py` Protocols (`ScorerPlugin`, `VectorizedScorerPlugin`, `TransformPlugin`) now match the runtime contracts in `core/scorer.py` / `utils/transforms.py`. `PluginRegistry.register_scorer` / `register_transform` `isinstance`-check the plugin against the Protocol and raise `TypeError` at registration if the duck-typed implementation is missing a method or the `name` attribute — fails at bind time instead of deep inside a scoring loop. Refdata adapters (`NameFreqWeightedJW`, `GivenNameAliasedJW`, `LegalFormStripTransform`, `AddressNormalizeTransform`, `NaicsNormalizeTransform`) explicitly inherit the Protocol for documentation + isinstance support.
- **Comment trim**: dropped the WHAT-not-WHY comments flagged by the comment analyzer in `industries.py` (the section-loop block comments, redundant whitespace-collapse note, etc.). Kept the WHY comments that document non-obvious invariants. Same pass applied to the other four refdata modules where applicable.
- **6 new tests** in `tests/test_plugins.py` under a new `TestProtocolEnforcement` class — registry rejects bad scorers/transforms at bind time, accepts Protocol conformers, and verifies every built-in refdata adapter satisfies the Protocol at runtime.

What's NOT in this slice (deferred to a separate PR):

- Auto-config gating on `ColumnProfile.col_type` — the higher-impact behavior change flagged in the PR #221 review. Touches `core/autoconfig.py`'s call signature into the hook, so it deserves its own focused PR with regression numbers against NCVR/DBLP-ACM.

### Added -- NAICS industry-code normalization (strategy direction #8, seventh slice)

Extends the `reference-business` pack with US Census 2022 NAICS industry classification. Canonicalizes both numeric codes ("511210", "511 210", "511210 (Software Publishing)") AND known industry titles ("Software Publishers" → "513210") to the same string before matching, so two records describing the same business industry land on the same value.

- **Bundled NAICS 2022 hierarchy** at `goldenmatch/refdata/data/naics_2022.json` -- 2,125 entries across all five hierarchy levels (17 sectors, 96 subsectors, 308 industry groups, 692 5-digit industries, 1,012 6-digit US industries). Sourced from the U.S. Census Bureau's "2-6 digit 2022 Codes" file (https://www.census.gov/naics/2022NAICS/2-6%20digit_2022_Codes.xlsx). Public-domain US federal data, no license restrictions. The "31-33" range-encoded sector for Manufacturing is expanded across each constituent 2-digit code.
- **Lookup API**: `title_for_code(code)`, `code_for_title(title)`, `naics_normalize(value)`, `industries_available()`, `known_codes()`, `known_titles()`. Title lookup is case- and punctuation-tolerant; code lookup tolerates separators and trailing text.
- **`naics_normalize` transform** auto-registered via `PluginRegistry` on `import goldenmatch.refdata`. Three input shapes:
  - Numeric input: scans EVERY 2+-digit run in the string; for each, walks back through prefixes looking for the longest known code. Returns the first run that resolves. If no run resolves, returns the 6-digit-truncated form of the first run (so two records sharing an unknown code still match). Multi-run scanning lets inputs like `"NAICS 2022 code 511210"` skip the vintage-year prefix and pick up the real code (review-driven; was a first-run short-circuit before).
  - Known industry titles → the canonical code at the narrowest matching hierarchy level.
  - Anything else → lowercase + whitespace-collapse pass-through.
  Never raises. Falls back to lowercase+strip if the bundled data is missing.
- **Autoconfig hook extended**: column-name patterns `naics`, `sic`, `industry`, `industry_code`, `industry_classification`, `business_type` → `naics_normalize` is prepended to the transforms list (mirrors the existing legal_form_strip / address_normalize handling). `_COMPANY_NAME_RE` was tightened to exclude `business[_ ]?type` so that classification column doesn't accidentally also pick up `legal_form_strip` (review-driven).
- **Thread-safety**: `_reload()` now relies on `_load()`'s lock + new-dict-assignment for atomic state swap, instead of wiping dicts before re-parse — readers see either the old dict or the new dict, never an empty in-between state (review-driven).
- **Tests**: `tests/test_refdata_industries.py` (~40 tests) -- title↔code round-trips, case/punctuation tolerance, separator-tolerant code parsing, overlong-code truncation, longest-known-prefix fallback (covers the review-flagged uncovered branch), title-precedence narrowest-wins (regression for the iteration-order rule), multi-digit-run scanning, `business_type` non-overlap with `_COMPANY_NAME_RE`, transform plugin registration, transform-chain composition, `FieldTransform` validator acceptance, `MatchkeyField` accepts in `transforms:`, autoconfig column-name variants including `business[_ ]?type` regex branches.
- **In-session validation blocked** by the same Polars DLL hang documented in PRs #220 and #221 (`goldenmatch/__init__.py` eagerly imports polars-heavy modules, which poisons every `goldenmatch.*` import including refdata submodules; openpyxl-only extraction of the source xlsx worked fine). The transform is a pure synchronous regex+dict function with no hot loops; rerun `pytest tests/test_refdata_industries.py -v` on a fresh Python boot to materialize the test result.
- **What's still deferred**: OpenCorporates company-name variants (the last documented `reference-business` extension), libpostal binding for `reference-address-postal`, per-scorer threshold tuning in `LearningMemory`, and the controller-level A/B rule that would A/B-test refdata refinements in the iteration loop instead of applying them unconditionally.

### Added -- Auto-config integration for refdata packs (strategy direction #8, sixth slice)

Wires all four refdata packs into the zero-config controller. Auto-config no longer needs an explicit YAML to pick up surname-IDF weighting, given-name aliasing, legal-form stripping, or USPS address normalization — it picks them automatically when column names signal the relevant shape.

- **New module** `goldenmatch/refdata/autoconfig_hooks.py` exposes `refine_matchkey_field(column_name, scorer, transforms) -> (scorer, transforms)`. Pure function; safe to call on every column unconditionally.
- **Refinement rules** (each gated on the relevant pack's `is_available()` — non-refdata installs behave exactly as before):
  - `last_name | surname | lname | family_name` → scorer becomes `name_freq_weighted_jw`.
  - `first_name | given_name | fname | forename` → scorer becomes `given_name_aliased_jw`.
  - `company | business | org | firm | employer | legal_name | entity_name` → `legal_form_strip` is prepended to the transforms list.
  - `address | street | addr_line | mailing_address` → `address_normalize` is prepended.
- **Wired into `core/autoconfig.py`** at both `build_matchkeys()` (vanilla weighted/exact path) and `build_probabilistic_matchkeys()` (Fellegi-Sunter path). The hook fires *after* `_SCORER_MAP[col_type]` resolves but *before* `MatchkeyField` is constructed — so the existing column-type classification, cardinality guards, and exact-matchkey skips still run unchanged.
- **Scorer-swap protection**: only string-similarity scorers (`jaro_winkler`, `levenshtein`, `token_sort`, `ensemble`, `dice`, `jaccard`) get swapped. Exact and embedding scorers pass through — preserves identity-field semantics.
- **Transform prepend, not replace**: `legal_form_strip` runs before any existing `lowercase`/`strip`, so the canonical short form still goes through downstream normalization. Idempotent — the function won't double-prepend if the transform is already in the list.
- **Compound columns** (e.g. `company_last_name`) get both refinements: scorer swap from the last_name match + transform prepend from the company match.
- **Tests**: `tests/test_refdata_autoconfig.py` (~50 tests) — parametrized across every column-name variant for each refinement rule, exact-scorer-not-swapped, idempotency, no-mutation-of-caller's-list, compound-column composition, and two end-to-end tests via `build_matchkeys()`.
- **In-session validation blocked** by the same Polars DLL hang documented in PR #220 / CLAUDE.md — every Python invocation in the session that imports Polars (directly or via pytest's conftest chain) sits idle indefinitely. The refinement function is a pure synchronous function and its tests don't depend on Polars; rerun `pytest tests/test_refdata_autoconfig.py` on a fresh Python boot to materialize the test result. Three of the existing test cases (those calling `build_matchkeys()`) do touch Polars and need the same fresh-boot rerun.
- **What's still deferred**: per-scorer threshold tuning in `LearningMemory`, regression check on NCVR / DBLP-ACM under the new auto-config (need a clean Python session), and the controller-level rule that A/B-tests refdata vs vanilla in its iteration loop for ground-truth-aware swap decisions.

### Added -- Surname-scorer common-name-FP synthetic benchmark (strategy direction #8, fifth slice)

Companion benchmark to the synthetic fixtures shipped with the other refdata packs (PR #217 nicknames, PR #218 legal-form, PR #219 address). NCVR's corruption distribution doesn't exercise the borderline JW zone the `name_freq_weighted_jw` scorer was built for; this fixture does, so we can finally show the scorer's actual lift instead of just "no regression".

- **`tests/benchmarks/run_surname_fp_synth.py`** -- 1000-record fixture:
  - **200 TP pairs**: same person across two records, identical first name, identical surname drawn from the common-US-Census pool (Smith, Johnson, Williams, ...). 20% of these use OOV-typo surnames on one side (Smith / Smiht) to verify the scorer's pass-through-to-plain-JW degradation doesn't regress recall.
  - **200 FP-candidate pairs**: *different* people, same first name, borderline-similar common surnames (Smith vs Smyth, Johnson vs Johnsen, Jones vs Jonas, Miller vs Millar, Martin vs Marten, White vs Whyte). Plain JW scores the surname pair around 0.89-0.94 -- exactly the borderline zone the refdata scorer down-weights.
  - **600 distractor singletons**: unique first AND last names, no FP pressure.
  - Blocking on `first_name` puts each pair into its own 2-record block.
- **Configured matchkey**: `first_name + last_name` weighted, threshold 0.92. The threshold is tuned so plain JW (1.0 + 0.89-0.94)/2 squeaks above (calls FP-candidates duplicates), but refdata-weighted (1.0 + 0.77-0.84)/2 drops below (rejects them).
- **Predicted numbers** from the scorer math validated by direct plugin calls earlier in the session:

  | | TP | FP-candidates passed | P | R | F1 |
  | - | - | - | - | - | - |
  | baseline (`jaro_winkler` on last_name) | 200 | ~200 (most pass at JW 0.89-0.94 averaged with 1.0) | ~0.50 | ~1.00 | **~0.67** |
  | refdata (`name_freq_weighted_jw`) | 200 | ~5-30 (only the residual high-JW cases) | ~0.87-0.98 | ~1.00 | **~0.93** |

  Expected F1 delta around **+0.26**. Numbers are predicted, not measured -- the in-session benchmark run is blocked by a known Polars DLL hang (CLAUDE.md gotcha: `Polars DLL hangs: kill zombie python ...`). After a clean Python boot, run `python tests/benchmarks/run_surname_fp_synth.py --out report.txt` to materialize the actual measurement.

- **Per-pair scorer math validated** for the surnames used in the fixture (direct plugin calls):

  | Surname pair | Plain JW | Refdata-weighted | Drop |
  | - | - | - | - |
  | Smith / Smyth | 0.893 | 0.769 | -0.124 |
  | Johnson / Johnsen | 0.943 | 0.818 | -0.125 |
  | Jones / Jonas | 0.907 | 0.790 | -0.117 |
  | Miller / Millar | 0.933 | 0.821 | -0.112 |
  | Martin / Marten | 0.933 | 0.840 | -0.093 |
  | White / Whyte | 0.893 | 0.793 | -0.100 |

  The down-weighting is consistent in the [0.10, 0.13] range for both-sides-known common-name pairs in the borderline JW zone.

- **What's still deferred** (after this slice): auto-config integration across all four refdata packs, libpostal binding for `reference-address-postal` extra, industry codes (NAICS) and OpenCorporates company-name variants for `reference-business`.

### Added -- Reference-address pack: USPS-style address normalization (strategy direction #8, fourth slice)

Fourth refdata slice. Opens the `reference-address` pack with the `address_normalize` transform: collapses USPS Publication 28 street-suffix, directional, and secondary-unit variants to their canonical short forms so "123 Main Street North Apartment 5" and "123 Main St N Apt 5" both reduce to "123 main st n apt 5" before scoring.

- **Bundled USPS abbreviation table** at `goldenmatch/refdata/data/address_abbreviations.json` — ~500 surface variants covering street suffixes (150+ canonical forms), 8 directionals, 9 secondary-unit designators. Sourced from USPS Publication 28 Appendix C; public-domain US federal data, no license restrictions.
- **`address_normalize` transform** auto-registered via `PluginRegistry` on `import goldenmatch.refdata`. Tokenizes on whitespace + commas, lowercases each token, strips punctuation, then maps any recognised variant to its USPS canonical short form. Unknown tokens pass through unchanged. Idempotent.
- **Position-agnostic by design**: every USPS-known token is normalized, not just trailing ones. Trade-off: words that are both name parts and suffix variants ("Lake", "Court", "Park") collapse along with true suffixes. Match invariance is preserved as long as both sides reduce equally — pinned by `test_aggressive_normalization_preserves_match_invariance`. For display purposes use a different normalization; this transform is matching-only.
- **Tests**: `tests/test_refdata_addresses.py` (48 tests) — per-suffix parametrized across 17 variants, directionals across 8, secondary units across 7, multi-token compound case, punctuation stripping, idempotency, unknown-token pass-through, position-agnostic invariance, plugin transform dispatch, transform-chain composition, validator acceptance.
- **Synthetic address benchmark** at `tests/benchmarks/run_address_synth.py`. 1000-record fixture, 200 same-street pairs differing in suffix abbreviation (some also in directional/unit) + 600 distractors, threshold 0.95:

  | | TP | FP | FN | P | R | F1 |
  | - | - | - | - | - | - | - |
  | baseline (no transform) | 116 | 0 | 84 | 1.0000 | 0.5800 | **0.7342** |
  | baseline (lowercase only) | 114 | 0 | 86 | 1.0000 | 0.5700 | **0.7261** |
  | refdata (`address_normalize`) | 200 | 0 | 0 | 1.0000 | 1.0000 | **1.0000** |

  F1 delta +0.2658. Recall +0.4200. Plain JW catches the small suffix deltas (Ave/Avenue, St/Street) but misses larger ones (Boulevard/Blvd, Northeast/NE, Apartment/Apt). The transform catches everything.

- **What's still deferred**: libpostal binding (heavy C deps + ~2 GB model; opt-in extra rather than bundled), street-name canonicalization (USPS CASS proper), ZIP+4 lookups, international postal-code formats.

### Added -- Reference-business pack: legal-form normalization (strategy direction #8, third slice)

Third refdata slice. Opens the `reference-business` pack with the `legal_form_strip` transform: strips trailing corporate suffixes ("Inc", "LLC", "GmbH", "Pty Ltd", …) so "Acme Inc." and "Acme Incorporated" collapse to "Acme" before scoring.

- **Bundled token list** at `goldenmatch/refdata/data/legal_forms.json` — ~80 surface variants spanning US, UK, EU, Asia-Pacific, LatAm jurisdictions. Public-knowledge corporate-suffix conventions; no license restrictions.
- **`legal_form_strip` transform** auto-registered via `PluginRegistry` on `import goldenmatch.refdata`. Strips multi-word suffixes first (so "Limited Liability Company" beats "Limited" or "Company" alone). Iterative (handles "Acme Holdings Inc" -> "Acme"). Idempotent. Case-insensitive. Returns input unchanged when no match or data file missing.
- **Plugin transform fallback wired into the core pipeline**:
  - `goldenmatch.utils.transforms.apply_transform` now falls through to `PluginRegistry.get_transform` for unknown transform names (mirrors the existing scorer fallback).
  - `goldenmatch.config.schemas.FieldTransform._validate_transform` checks the registry before raising `Invalid transform` (mirrors `MatchkeyField._validate_*` scorer fallback).
  - Net result: any plugin transform Just Works in YAML config, matchkey transforms list, and `apply_transforms` chains.
- **Tests**: `tests/test_refdata_business.py` (45 tests) -- per-form strip parametrized across 28 variants, case-insensitive, whitespace normalize, iterative strip on multi-suffix names, idempotency, mid-name preservation, None/empty handling, plugin transform dispatch, transform-chain composition (`legal_form_strip` then `lowercase`), `FieldTransform` validator accepts plugin name, `MatchkeyField` accepts it in `transforms:`.
- **Synthetic business-name benchmark** at `tests/benchmarks/run_business_synth.py`. 1000-record fixture, 200 same-stem pairs differing only in legal-form suffix (Acme Inc vs Acme Incorporated, etc.) + 600 distractors, threshold 0.95:

  | | TP | FP | FN | P | R | F1 |
  | - | - | - | - | - | - | - |
  | baseline (no transform) | 79 | 0 | 121 | 1.0000 | 0.3950 | **0.5663** |
  | refdata (`legal_form_strip`) | 200 | 0 | 0 | 1.0000 | 1.0000 | **1.0000** |
  | refdata (`legal_form_strip` + `lowercase`) | 200 | 0 | 0 | 1.0000 | 1.0000 | **1.0000** |

  F1 delta +0.4337. Recall +0.6050. The transform catches every pair the variant labels differ on; precision unchanged at 1.0 (no FPs introduced).
- **What's still deferred**: industry code lookups (NAICS), OpenCorporates company-name variants, `reference-address` pack (token normalization + libpostal binding), auto-config integration, per-scorer threshold tuning.

### Added -- Reference data infrastructure (strategy direction #8, first slice)

`goldenmatch.refdata` -- bundled, public-domain reference data the engine can consume to lift accuracy on people-shape matching. Spec: `docs/superpowers/specs/2026-05-08-competitive-strategy-review.md` direction #8.

- **US Census 2010 top-10K surname frequency table** bundled at `goldenmatch/refdata/data/census_surnames_2010_top10k.csv` (~176 KB, public domain). Provenance, license, regenerate command documented in `PROVENANCE.md`.
- **Lookup API**: `surname_count`, `surname_rank`, `surname_frequency`, `surname_idf`, `is_available`. Case-insensitive; strips non-alpha. OOV names return `None` (or `1.0` from `surname_idf`, treated as "rarer than known").
- **`name_freq_weighted_jw` scorer** registered via the plugin system on `import goldenmatch.refdata`. Algorithm: Jaro-Winkler outside the borderline zone (`jw >= 0.95` or `jw < 0.70`) returns plain JW unchanged -- preserves recall on confident matches. Inside the borderline zone, both-sides-known pairs get re-weighted by mean surname IDF with a `_COMMON_NAME_FLOOR = 0.6`. OOV-on-either-side falls back to plain JW (refuses to up-credit typos of common names).
- **NxN plugin path**: `core/scorer.py::_fuzzy_score_matrix` now falls through to `PluginRegistry` for unknown scorer names, building the matrix via `score_pair` calls. Slower than rapidfuzz `cdist` for the registered scorers but keeps the contract uniform.
- **Regenerate**: `python -m goldenmatch.refdata.scripts.fetch_census_surnames` pulls the upstream archive and rewrites the bundled CSV.
- **Tests**: `tests/test_refdata_surnames.py` (21 tests) -- lookup correctness, IDF monotonicity, scorer borderline behavior, OOV pass-through, plugin registration, `MatchkeyField` validator accepts the new scorer.
- **NCVR A/B benchmark** at `tests/benchmarks/run_ncvr_refdata.py`. 7500-record corrupted-duplicates GT, last_name scorer swapped: F1 0.9721 (baseline, zero-config) -> 0.9721 (refdata). No regression. Lift is zero on this dataset because NCVR's heavy-corruption distribution puts few pairs in the borderline JW zone where the weighting acts -- needs an enterprise-shape benchmark per direction #5 to demonstrate positive lift.
- **What's deferred** (future work): auto-config integration (the controller doesn't yet pick `name_freq_weighted_jw` automatically); `reference-business` and `reference-address` packs; threshold tuning per-scorer in `LearningMemory`.

### Added -- Given-name alias pack (strategy direction #8, second slice)

Second slice of the `reference-people` pack. Adds nickname-equivalence to first-name matching: William ↔ Bill, Robert ↔ Bob, Margaret ↔ Peggy, etc.

- **Curated alias table** at `goldenmatch/refdata/data/given_name_aliases.json` (~140 canonical English given names, public-knowledge naming conventions; no license restrictions).
- **Lookup API**: `canonical_form`, `aliases_of`, `are_equivalent`, `given_names_available`. Case-insensitive; strips non-alpha. Symmetric and transitive within an equivalence class. OOV pass-through.
- **`given_name_aliased_jw` scorer** registered via the plugin system on `import goldenmatch.refdata`. Alias-equivalent pairs return 1.0 regardless of edit distance; unrelated pairs return plain Jaro-Winkler. The scorer never *lowers* a JW score -- it only promotes known aliases. Degrades cleanly when the alias table is missing.
- **Tests**: `tests/test_refdata_given_names.py` (23 tests) -- lookup symmetry, transitive equivalence, multi-canonical name handling (e.g. "Jack" canonical AND alias-to-John), case/punct insensitivity, OOV pass-through, scorer correctness, plugin registration, validator acceptance.
- **Synthetic nickname benchmark** at `tests/benchmarks/run_nickname_synth.py`. 1000-record fixture with 200 nickname-shape duplicate pairs + 600 distractors with isolated random first/last names. Plain JW baseline catches **0/200** pairs at threshold 0.95 (JW(William, Bill) ~= 0.55, far below threshold); `given_name_aliased_jw` catches **200/200**, P=1.0, R=1.0, **F1 0.00 -> 1.00**.
- **Asymmetry-on-ambiguous-short-form bugfix**: short forms that belong to multiple canonicals (e.g. "kate" appears in Catherine, Kathleen, Kaitlyn; "chris" in Christopher, Christine, Christina) were silently asymmetric — `are_equivalent("Kate", "Catherine")` returned False while `("Catherine", "Kate")` returned True, because the old lookup stored a single canonical per form (last-writer-wins). The matcher's NxN score matrix only consults the upper triangle, so the False direction was the one being read and every ambiguous-short-form pair was being dropped. Each form now stores the full set of canonicals it belongs to; equivalence holds iff the two forms share a canonical. Regression test in `test_are_equivalent_symmetric_for_ambiguous_short_forms`.
- **What's still deferred**: same list as the first slice (auto-config integration, business / address packs, per-scorer threshold tuning).

## [1.15.0] - 2026-05-12

### Added -- Identity Graph (v2.0 headline feature)

`goldenmatch.identity` -- a first-class durable graph layer above run-local clusters. Spec: `docs/superpowers/specs/2026-05-12-identity-graph-design.md`. Roadmap: `docs/superpowers/plans/2026-05-12-identity-graph-roadmap.md`.

- **`IdentityStore`** (SQLite default, Postgres optional): identity nodes, source records, evidence edges, append-only event log, aliases. WAL + busy_timeout for multi-process safety. Schema versioned via `PRAGMA user_version`.
- **Stable `entity_id` across runs**. `resolve_clusters()` runs after dedupe clustering and decides `create` / `absorb` / `merge` based on which existing identities cover the cluster's records. Idempotent on `(run_name, kind, entity_id)`.
- **`IdentityConfig`** -- new optional section in `goldenmatch.yml`. When `identity.enabled: true`, the pipeline writes graph state at `.goldenmatch/identity.db` (or the configured backend) on every `run_dedupe()`. Disabled by default; failure logs + skips, never blocks dedupe output.
- **Surfaces**: Python (`goldenmatch.identity.*` + root re-exports), CLI (`goldenmatch identity list/show/resolve/history/conflicts/merge/split`), REST (`/api/v1/identities/...`), web "Identities" tab, MCP (6 `identity_*` tools), A2A (6 skills, agent card now declares 18 total skills). TS edge-safe core (`InMemoryIdentityStore` + `findByRecord` / `getEntity` / `manualMerge` / `manualSplit`) ships in the same release; persistent SQLite backend + pipeline-driven population are TS-port v2 follow-ups.
- **Postgres analytical views**: `v_identities`, `v_identity_pairs`, `v_identity_timeline` in `goldenmatch/db/migrations/identity_v1.sql`. `IdentityStore(backend="postgres")` creates the same schema on first connect.
- **DuckDB / extensions contract** documented at `docs/superpowers/specs/2026-05-12-identity-graph-duckdb-contract.md` for the `goldenmatch-extensions` repo to implement.
- **47 new Python tests**, **13 new TS tests**. Full sweep: 1984 passed, 0 regressions.
- Example: `examples/identity_graph.py`.

## [1.14.0] - 2026-05-11

This release ships the full v1.7-v1.12 AutoConfigController surface to every user-facing entry point in the suite. No algorithm changes vs 1.13.0 — same DQbench / DBLP-ACM / Febrl3 / NCVR numbers — but you can now read what the controller decided from every interface (web, TUI, CLI, REST, MCP, A2A, Postgres, DuckDB) and round-trip the committed config (including Path Y negative-evidence) through SQL.

### Fixed

- **AgentSession default path now actually runs the AutoConfigController** (PR #169). `deduplicate(config=None)` / `match_sources(config=None)` were building a config from the legacy `select_strategy()` heuristic and passing it explicitly to `dedupe_df`/`match_df`, which suppressed the zero-config controller path. `last_telemetry` ended up `None` for the case it was meant to capture. Default path now calls `dedupe_df(df)` / `match_df(df_a, df_b)` with no config so the controller fires. Explicit-config calls explicitly clear `last_telemetry` to prevent stale-blob leaks across calls on the same session. `select_strategy()` still runs but only for the `reasoning` payload, not to back the actual matching config. Hard-asserting tests landed alongside the fix.

### Added — AutoConfigController surface-parity arc

Six PRs (#156-#159 + #161; #160 added CI lanes) bring every user-facing entry point up to speed with the v1.7-v1.12 AutoConfigController / IndicatorContext / NegativeEvidence work. Before this arc, controller decisions were observable only by reading `result.postflight_report.controller_history` in Python. Now every surface returns the same JSON shape (`stop_reason`, `health`, refit decisions, indicator column priors, committed `negative_evidence`) via `goldenmatch.web.controller_telemetry.serialize_telemetry`.

**Web UI** (PR #156)
- New `ControllerPanel` in Workbench surfaces stop_reason badge, health verdict, complexity profile cells, indicator column priors, refit decision trace, and `Path Y · N NE` indicator on committed matchkeys.
- New `GET /api/v1/controller/telemetry` endpoint populated by `/autoconfig` and `/run?auto_config=true`.
- Home gains a `ProvenanceCallout` linking `docs/reproducing-benchmarks.md` + `docs/scale-envelope.md` with the four reproducible numbers.

**TUI** (PR #157)
- New `Controller` tab (7th tab) showing the same telemetry the web panel shows.
- New `Ctrl+A` binding triggers async auto-configure; result adopted into ConfigTab + ExportTab; switches to Controller tab on completion.
- `MatchEngine.auto_configure(domain=None)` captures `_LAST_CONTROLLER_RUN` and exposes telemetry on `engine.last_telemetry`.

**CLI** (PR #158)
- New `goldenmatch autoconfig <files>` subcommand. Prints committed config to stdout (pipe to `> goldenmatch.yml`); telemetry panel to stderr. Flags: `--out PATH`, `--domain`, `--verbose`, `--hide-controller`.
- `goldenmatch dedupe` zero-config path captures `_LAST_CONTROLLER_RUN` and renders the same panel before the cluster report (`--show-controller` / `--hide-controller`).
- New shared `goldenmatch.cli._controller_render` module with Rich Panel + one-line `render_short_status` for log scraping.

**SQL extensions** (PR #159)
- Bridge (Rust/pyo3) gains `DedupeResult.telemetry_json`, `autoconfig()` returning `(committed_config_json, telemetry_json)`, and `dedupe_full()` accepting the full Pydantic `GoldenMatchConfig` JSON (unlocks `negative_evidence` from SQL).
- Postgres: new `goldenmatch_autoconfig`, `goldenmatch_autoconfig_telemetry`, `goldenmatch_dedupe_full`, `goldenmatch_dedupe_full_telemetry`, `gm_telemetry`. New JSONB column `goldenmatch._jobs.last_telemetry_json` (added via `ALTER TABLE ... IF NOT EXISTS` for in-place upgrade).
- DuckDB: parallel UDFs registered on every `register(con)`.

**CI** (PR #160)
- New `rust_pgrx` lane (matrix: PG 15/16/17) — cargo pgrx install + psql smoke covering the new v1.7-v1.12 surface.
- New `duckdb_extensions` lane — runs the DuckDB UDF Python tests that the main `python` matrix doesn't pick up.

**Agent / programmatic surfaces** (PR #161)
- `AgentSession.autoconfigure(file_path)` returns `{config, telemetry}`; `deduplicate` / `match_sources` cache `last_telemetry`.
- REST API (`goldenmatch serve`): new `POST /autoconfig` (with optional `records` body override) + `GET /controller/telemetry`.
- MCP: `auto_configure` tool rewired off the legacy `select_strategy` heuristic onto the controller. New `controller_telemetry` tool. `agent_deduplicate` / `agent_match_sources` embed telemetry inline.
- A2A: 10 → 12 skills (added `autoconfig` + `controller_telemetry`). `deduplicate` / `match` skills embed telemetry in their wire result.

**Cross-surface telemetry shape** (single source of truth at `goldenmatch.web.controller_telemetry.serialize_telemetry`)

```json
{
  "available": true,
  "source": "autoconfig",
  "stop_reason": "green",
  "health": "green",
  "elapsed_ms": 1234.5,
  "full_vs_sample_drift": 0.12,
  "scoring": {"n_pairs_scored": 4421, "mass_above_threshold": 0.087},
  "blocking": {"n_blocks": 312, "reduction_ratio": 0.94},
  "cluster": {"n_clusters": 1820, "transitivity_rate": 0.99},
  "column_priors": [{"column": "email", "identity_score": 0.95, "corruption_score": 0.0}],
  "decisions": [{"iteration": 1, "rule_name": "...", "rationale": "...", "wall_clock_ms": 234}],
  "committed_matchkeys": [{"name": "exact_email", "has_negative_evidence": true}],
  "negative_evidence": [{"matchkey_name": "exact_email", "field": "phone", "penalty": 0.5}]
}
```

## [1.13.0] - 2026-05-11

This is a release-plumbing wave: typed-accessor API additions, PyPI metadata refresh, and contributor-facing quality improvements. **No DQbench / Febrl3 / NCVR / DBLP-ACM number changes** — algorithm is unchanged this wave.

### Added
- **Typed accessor API on `MatchkeyConfig` / `MatchkeyField`** (PR #151). New properties: `MatchkeyConfig.fuzzy_threshold`, `MatchkeyField.fuzzy_scorer`, `MatchkeyField.fuzzy_weight`, `MatchkeyField.resolved_field`. Each raises `ValueError` when the underlying matchkey isn't a fuzzy/weighted type, so the invariant is now enforceable in pyright strict mode rather than asserted in callers.

  ```python
  from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField

  mk = MatchkeyConfig(
      name="identity",
      type="weighted",
      threshold=0.85,
      fields=[MatchkeyField(field="name", transforms=["lowercase"], scorer="jaro_winkler", weight=1.0)],
  )
  assert mk.fuzzy_threshold == 0.85  # safe access on weighted matchkey
  # mk.fuzzy_threshold on an exact matchkey raises ValueError
  ```

- **`docs/scale-envelope.md`** (PR #149): documents the Polars / DuckDB / Ray operating ranges plus block-size failure modes so callers can pick a backend before hitting an OOM.
- **Postgres CI lane** (PR #144): flipped from skipped to live so DB integration tests now run on every PR.

### Changed
- **PyPI metadata corrected** (PR #148): `[project.urls]` Homepage / Repository / Documentation entries now point at the monorepo at `benseverndev-oss/goldenmatch`. The pre-fold standalone-repo URLs are gone. Metadata only refreshes on a wheel build, so this release is what makes the corrected URLs visible on PyPI.

### Fixed
- **Reproducibility of all four published benchmark numbers** (PR #152, replaces #150): DQbench composite 91.04, DBLP-ACM 0.9641, Febrl3 0.9443, NCVR 0.9719 now all reproduce from a fresh clone. See `docs/reproducing-benchmarks.md` for the exact commands and dataset prep steps.

### Internal (contributors only)
- Ruff lint expanded to F / I / B-narrowed / UP rule sets across `packages/python/` (PR #146).
- Pyright strict mode now enforced on the 21-file core slice of `goldenmatch` (PR #147). The new typed accessors in PR #151 eliminated 7 type-suppression workarounds in callers.

### Benchmarks (zero-config, no LLM)

Unchanged vs v1.12.0 — algorithm not touched this wave.

| Dataset | v1.12.0 | v1.13.0 | Delta |
|---|---|---|---|
| DBLP-ACM | 0.9641 | 0.9641 | +0.0000 |
| Febrl3 | 0.9443 | 0.9443 | +0.0000 |
| NCVR | 0.9719 | 0.9719 | +0.0000 |
| DQbench composite | 91.04 | 91.04 | +0.00 |

## [1.12.0] - 2026-05-10

<!-- README-callout
**Negative evidence on exact matchkeys (Path Y)** — NE penalties now filter adversarial collision pairs at the `exact_email` level, not just inside the weighted matchkey scoring loop. DQbench composite **91.04** (was 66.99 at v1.11). T2 F1 69.0% → 97.5%, T3 F1 53.8% → 85.5%.
-->

### Added
- **`_apply_negative_evidence_to_exact_pairs`** in `core/scorer.py`: post-filter helper that applies NE penalties to pairs produced by exact matchkeys. Called from `core/pipeline.py` after `find_exact_matches`. Score formula: `final = max(0, 1.0 - sum(penalties))`; pair emits only if `final >= matchkey.threshold`. Exact matchkeys without NE fields are unaffected (binary 1.0/0.0 emit preserved).
- **Exact-matchkey NE threshold default**: when `promote_negative_evidence` adds NE fields to a threshold-None exact matchkey, the threshold is defaulted to 0.5 to activate the score-and-threshold path.
- **`promote_negative_evidence` extended** to walk all matchkey types (was weighted-only in v1.11). The `_is_exact_matchkey_field` gate is selectively skipped when iterating an exact matchkey for itself — its v1.11 rationale (prevent recall regression on fuzzy data) doesn't apply to exact-matchkey self-iteration.

### Changed
- **`core/pipeline.py`**: calls `_apply_negative_evidence_to_exact_pairs` after `find_exact_matches` when any exact matchkey carries NE fields. Zero overhead when no NE fields are present.
- **`promote_negative_evidence`** now populates NE on exact matchkeys in addition to weighted matchkeys. Exact matchkeys for high-identity-prior columns (email) gain NE from disagreeing secondary fields, allowing adversarial collision pairs to be filtered at the exact matchkey level.

### Benchmarks (zero-config, no LLM)

| Dataset | v1.11.0 | v1.12.0 | Delta |
|---|---|---|---|
| DBLP-ACM | 0.9641 | 0.9641 | +0.0000 |
| Febrl3 | 0.9443 | 0.9443 | +0.0000 |
| NCVR | 0.9719 | 0.9719 | +0.0000 |
| DQbench composite | 66.99 | 91.04 | +24.05 pp |

DQbench tier detail (v1.12.0):

| Tier | Precision | Recall | F1 | vs v1.11 |
|---|---|---|---|---|
| T1 | 80.6% | 100.0% | 89.3% | flat |
| T2 | 95.1% | 100.0% | 97.5% | +28.5 pp |
| T3 | 74.7% | 100.0% | 85.5% | +31.7 pp |

Primary target (>= 75) met. T3 F1 headline target (>= 70%) met. All floor constraints met. The T3 gain resolves the v1.11 root cause: Path Y NE filtering now operates at the `exact_email` matchkey level, directly shedding adversarial collision pairs that share an email but disagree on name/address NE fields.

## [1.11.0] - 2026-05-10

### Added
- **`NegativeEvidenceField`** in `config/schemas.py`: new optional field on `MatchkeyConfig`. Each entry specifies a field, transforms, scorer, similarity threshold, and penalty. When a weighted matchkey scores a pair, any NE field whose similarity falls below its threshold subtracts the penalty from the weighted score.
- **`_apply_negative_evidence`** in `core/scorer.py`: pure helper that computes the NE penalty for a scored pair and returns the adjusted score. Called inside the weighted-matchkey scoring loop.
- **`promote_negative_evidence`** in `core/autoconfig_negative_evidence.py`: eager rule that adds NE fields to weighted matchkeys for columns with high identity priors (identity_score >= 0.75, cardinality_ratio >= 0.5) that also have an exact matchkey counterpart. Gated on the exact-matchkey counterpart requirement to prevent recall regression on noisy ER data where legitimate duplicates may have differing phone/address values.
- **`_pick_scorer_for_column`** in `core/autoconfig_negative_evidence.py`: maps column name / type to (transforms, scorer) for NE fields. Phone -> (digits_only, exact). Email -> ([], token_sort). Address -> ([], token_sort). Default -> ([], ensemble).
- **`rule_demote_clustered_identity`** at position 7 in `DEFAULT_RULES`: detects when an exact matchkey identity column is shared across distinct entities (adversarial reuse pattern). Demotes the exact matchkey to a fuzzy participant on the weighted matchkey and adds the column to blocking. Threshold of 0.75 (raised from 0.5 after Phase 7 analysis showed T2's collision rate of 0.62 was causing false demotion and 186 FNs).
- **`compute_identity_collision_signal`** in `core/indicators.py`: for each multi-record group sharing an identity column value, computes max pairwise divergence on witness columns using token_sort_ratio. Returns fraction of groups with max divergence > 0.5.

### Changed
- **`AutoConfigController.run`**: calls `promote_negative_evidence` between v0 config build and the iteration loop, so NE fields are present on weighted matchkeys before the first iteration profiles them.
- **`rule_demote_clustered_identity` collision threshold**: raised from 0.5 to 0.75. This prevents false-firing on legitimate fuzzy ER datasets (T2 collision rate 0.615) while still catching high-rate adversarial reuse (rates near 1.0).

### Benchmarks (zero-config, no LLM)

| Dataset | v1.10.0 | v1.11.0 |
|---|---|---|
| DBLP-ACM | 0.9641 | 0.9641 |
| Febrl3 | 0.9443 | 0.9443 |
| NCVR | 0.9719 | 0.9719 |
| DQbench composite | 66.91 | 66.99 |

T2 recall regression (186 FNs from v1.11 early iteration) fixed by raising `rule_demote_clustered_identity` threshold from 0.5 to 0.75. T3 unchanged at 53.8%. Primary target (>= 75) not met; ships on best-effort basis above v1.10 baseline. T3 F1 target (>= 70%) remains an open v1.12 challenge: the exact-matchkey gate correctly protects T2 recall but also prevents phone NE from reducing T3 adversarial FPs.

### Notes for v1.12

- T3 adversarial FPs come from the `exact_email` matchkey capturing collision pairs directly. NE on the weighted matchkey does not affect these pairs. Real T3 improvement requires either a higher-precision collision signal or a different mechanism for adversarial reuse that does not require collision_rate to exceed T2's rate (0.615).
- Removing the exact-matchkey gate would raise composite to ~68.9 but drops T2 by ~0.8 pp. Not shipped due to net regression on T2 at the pair level.

## [1.10.0] - 2026-05-08

### Added
- **5 complexity indicators** (`core/indicators.py`): `compute_column_priors`, `estimate_sparse_match_signal`, `compute_corruption_score`, `estimate_full_pop_hits`, `compute_cross_blocking_overlap`. Each has a wall-clock budget; cheap two run eagerly, expensive three run lazily via `IndicatorContext` memoization.
- **`IndicatorContext`** in `autoconfig_controller.py` threads indicators through the policy/rule chain. `RefitPolicy.propose` gains optional `ctx` kwarg; `HeuristicRefitPolicy` and `LLMRefitPolicy` both forward; controller introspects custom-policy signatures via `inspect.signature` for backward compat.
- **3 new indicator-aware rules**: `rule_corruption_normalize`, `rule_cross_blocking_disagreement`, `rule_sparse_match_expand`. `DEFAULT_RULES` now has 13 rules (was 10).
- **`GOLDENMATCH_AUTOCONFIG_INDICATOR_BUDGET=fast`** env var gates the two expensive indicators (full-pop scan, cross-blocking probe) for users who prefer v1.9 wall-clock.
- **`ColumnPrior`, `SparsityVerdict`, `IndicatorsProfile`** dataclasses in `core/complexity_profile.py`. New default-None fields: `DataProfile.column_priors`, `ComplexityProfile.indicators`.

### Changed
- **`rule_no_matches`** (modified): when ctx provides high-identity-prior on the blocking column, tries `[lower_threshold, normalize, multi_pass]` alternatives in order before falling back to today's behavior. When `ctx.sparsity_verdict.is_sparse`, lowers threshold by 0.10 (proxy for ExpandSample, queued v1.11).
- **`rule_blocking_key_swap`** (modified): vetoed when blocking column has `identity_score >= 0.8` AND `full_pop_matchkey_hits > 0` (protects v0's correct identity blocking from being abandoned on noisy samples).

### Benchmarks (zero-config, no LLM)

| Dataset | v1.9.0 | v1.10.0 |
|---|---|---|
| DBLP-ACM | 0.9641 | 0.9641 |
| Febrl3 | 0.9443 | 0.9443 |
| NCVR | 0.9719 | 0.9719 |
| DQbench composite | 62.87 | 66.91 |

T2 F1: 58.7% → 69.0% (+10.3 pp). T1 and T3 unchanged. Primary target (>= 70) not met; ships on fallback basis (>= 65).

### Notes for v1.11

- `rule_sparse_match_expand` substitutes `_with_lower_threshold(0.10)` for the spec's `ExpandSample(2.0)` action; real controller-level sample expansion queued for v1.11.
- No rule forces a *positive* swap to an identity-prior column when v0 picked something else; v1.10 only protects identity columns from being abandoned. v1.11 may add `rule_promote_identity_blocking` if benchmark measurement shows the gap matters.
- Attribution sweep (which of the 5 indicators drove the T2 gain) not run — composite fell in fallback range (65-70); sweep was deferred per plan.

## [1.9.0] - 2026-05-08

### Added
- **Best-effort commit semantics.** `RunHistory.pick_committed()` extends the lex key to RED entries (rank=2) and returns the highest-ranked entry by `(health_rank, -mass_separation, iteration)`. Replaces v1.8's `cheapest_healthy()` which returned None on all-RED history. Filters errored entries via `error is None and profile is not None`. Closes a known v1.8 design-doc gap.
- **`RunHistory.stop_reason: StopReason | None`** populated at every break point in `AutoConfigController.run()`. Observable via `result.postflight_report.controller_history.stop_reason`. Eight values: GREEN, CONVERGED, BUDGET_ITERATIONS, BUDGET_TIME, POLICY_SATISFIED, POLICY_NO_PROGRESS, OSCILLATING, CANCELLED.
- **Virtual v0 fallback + precision-collapse floor.** The controller appends `config_v0`'s profile as a synthetic `HistoryEntry(iteration=-1)` before `pick_committed()` runs, so v0 stays in the candidate pool. `pick_committed(precision_collapse_floor=0.9)` demotes RED entries with `mass_above_threshold > 0.9` (the "everything matches" pathology) to rank=3. Together these prevent committing a config demonstrably worse than v0.
- **Health-aware commit logging.** WARNING on RED commit (names failing sub-profile + stop_reason + iteration); INFO on YELLOW; silent on GREEN; ERROR on all-errored fallback. Logs use `iter=v0` to identify virtual-v0 commits.

### Changed
- `RunHistory.cheapest_healthy()` is now a deprecation alias for `pick_committed()`. **Behavior change**: returns RED entries when no GREEN/YELLOW exists (was: returned None). DeprecationWarning text calls out the change explicitly. Removed in v2.0.
- `StopReason` enum moved from `core/autoconfig_controller.py` to `core/complexity_profile.py` (next to `HealthVerdict`).

### Fixed
- DQbench composite regression caught during release verification: unguarded best-effort commit could select a precision-collapsed RED config (T1: 1% precision, 100% recall -- "match everything"). Virtual v0 + precision floor restored v1.8 parity exactly.

### Benchmarks (zero-config, no LLM)

| Dataset | v1.8.0 | v1.9.0 |
|---|---|---|
| DBLP-ACM | 0.9641 | 0.9641 |
| Febrl3 | 0.9443 | 0.9443 |
| NCVR | 0.9719 | 0.9719 |
| DQbench composite | 62.87 | 62.87 |

### Notes for v1.10

The original v1.9 spec assumed best-effort RED commit would deliver a DQbench composite gain (target >= 65). In practice, the controller's complexity indicators can't distinguish "blocking key is wrong" from "blocking key is right but sample has no visible matches" -- both produce `mass_above_threshold=0.0`. v1.10 will add new indicators (identity-column priors, cross-blocking overlap probe, blocking-column corruption signal, sparse-match sensitivity) so the controller can tell these cases apart and deliver real gains on the tiers where it currently can't escape the impasse.

## [1.8.0] - 2026-05-08

<!-- README-callout
**Introspective auto-config controller** — Iterates on stage-emitted complexity signals (block-size dist, score histogram, transitivity, borderline mass) and refines its config via heuristic rules until convergence. Zero-config beats hand-tuned on DBLP-ACM (F1 **0.964** vs 0.918 ceiling), NCVR (**0.972**), Febrl3 (**0.944**). Cross-run memory at `~/.goldenmatch/autoconfig_memory.db`, LLM policy fallback (`GOLDENMATCH_AUTOCONFIG_LLM=1`), standardization auto-detection. Built by [Ben Severn](https://bensevern.dev).
-->

### Added
- **Introspective auto-config controller** that beats hand-tuned configs on multiple benchmarks without manual tuning. Zero-config now produces a defensible config the first time, even on shapes it hasn't been hand-tuned for. The controller iterates on stage-emitted complexity signals (block size distribution, score histogram, transitivity rate, candidates compared, mass above/in-borderline) and refines its config via a heuristic rule policy until convergence. (#103, #104, #109, #114)
- **Cross-run memory** at `~/.goldenmatch/autoconfig_memory.db` — past committed configs are reused when the data shape signature matches. Opt out with `GOLDENMATCH_AUTOCONFIG_MEMORY=0`. (#111)
- **LLM policy fallback** (option B): when heuristic rules exhaust without reaching GREEN, an `LLMRefitPolicy` proposes a config diff. Default off; opt in with `GOLDENMATCH_AUTOCONFIG_LLM=1`. (#112)
- **Per-pair LLM scoring auto-enable** when the committed profile shows borderline-heavy mass and an LLM API key is available. Adaptive bounds track the matchkey's threshold dynamically. (#113, #115)
- **Standardization auto-detection** in v0 — phone/email/zip/state/name/address columns now auto-emit `StandardizationConfig` rules. (#115)
- **Recall-aware probes** — `random_pair_above_threshold_rate` signal in `ScoringProfile`; `rule_recall_gap_suspected` and `rule_blocking_field_null_heavy` rules. (#109)
- **NCVR benchmark regression test** (gated on dataset presence). (#110)
- **11 real-data integration tests** + **5 Hypothesis property tests** for controller invariants. (#106, #107)

### Changed
- `auto_configure_df` is now controller-backed; gains optional `reference` kwarg for cross-source match mode. Public signature otherwise unchanged.
- Zero-config callers in `_api.dedupe_df` / `_api.match_df` now call `auto_configure_df` *before* the pipeline (eliminates double pipeline run). (#103)
- `PostflightReport` gains `controller_profile` + `controller_history` fields surfacing the typed `ComplexityProfile` and audit trail. (#103, #108)

### Fixed
- Zero-config crashes in `match_df` (`ColumnNotFoundError: __title_key__`) and `match()` (`ColumnNotFoundError: __placeholder__`). (#102)
- Cache poisoning across structurally-identical-but-semantically-different datasets. (#112)
- SQLite cross-thread access in default memory store (web routers fixed). (#111)

### Benchmarks (zero-config, no manual tuning)

| Dataset | v1.7.1 | v1.8.0 | Hand-tuned ceiling |
|---|---|---|---|
| DBLP-ACM (cross-source) | 0.5102 | **0.9641** | 0.918 |
| Febrl3 (single-source) | 0.8528 | **0.9443** | 0.971 |
| NCVR (corruption GT) | — | **0.9719** | — |
| DQbench (no LLM) | 46.24 (hand-tuned) | **62.87** (zero-config) | — |

## [1.6.0] - 2026-05-04

### Added
- **Learning Memory completion** — corrections now flow end-to-end from collection points through pipeline application to postflight surfaces.
  - **Re-anchor via record_hash**: corrections survive row reorder and input refresh through a collision-safe vectorized record-hash lookup. Ambiguous re-anchors (duplicate rows) report as `stale_ambiguous` rather than silently misapplying. New `MemoryConfig.reanchor` flag (default `True`) gates the behavior.
  - **Pipeline hook**: `dedupe_df` and `match_df` apply stored corrections after scoring and overlay learned thresholds before scoring. `DedupeResult.memory_stats` and `MatchResult.memory_stats` surface applied/stale/stale-ambiguous counts.
  - **Seven collection points** capture corrections automatically: review queue (`steward`, trust 1.0), boost tab y/n (`boost`, 1.0), `unmerge_record`/`unmerge_cluster` (`unmerge`, 1.0, empty hashes), LLM scorer decisions (`llm`, 0.5), MCP `agent_approve_reject` (`agent`, 0.5), and REST `POST /reviews/decide` (`steward`, 1.0).
  - **Postflight section**: rendered postflight string adds a `Memory: N corrections applied, M stale, K stale-ambiguous` line when memory is active.
  - **Explainer integration**: review queue items carry a `why` field. Deterministic template by default; routes to `core/llm_scorer.llm_explain_pair` when `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` is set.
  - **CLI subgroup**: `goldenmatch memory stats|learn|export|import|show`.
  - **Five MCP tools**: `list_corrections`, `add_correction`, `learn_thresholds`, `memory_stats`, `memory_export`. Server card description updated to "35 MCP tools".
  - **Python API**: `goldenmatch.get_memory()`, `goldenmatch.add_correction()`, `goldenmatch.learn()`, `goldenmatch.memory_stats()`.
  - **Stale persistence**: stale corrections are enqueued to a sibling SQLite review queue (`.goldenmatch/review_queue.db`) so the next `goldenmatch review` invocation surfaces them.
  - **8 end-to-end integration tests** in `test_memory_e2e.py` covering happy path, re-anchor on reorder, stale-on-edit, trust conflict, threshold learning, deterministic explainer fallback, postflight rendering, and stale-ambiguous reporting.

### Changed
- Zero-config posture preserved: nothing changes for users who don't enable memory (`config.memory.enabled = False` by default; absent config section means no memory work).

- **NEW**: TypeScript / Node.js port published as `goldenmatch` on npm
  - Full feature parity with Python: scorers, clustering, golden records, LLM, PPRL, probabilistic, graph ER, streaming, MCP/REST/A2A servers
  - Edge-safe core (browsers, Workers, Edge Runtime) + Node-only file/DB layer
  - 478 tests, strict TypeScript

## [1.4.1] - 2026-04-06

### Added
- **MCP tools for data quality** — `scan_quality` (scan without fixing), `fix_quality` (scan + apply fixes with safe/moderate mode), `run_transforms` (GoldenFlow phone/date/Unicode normalization). All 3 tools validate file paths, handle write failures gracefully, and include logging
- **A2A skills for data quality** — `quality` (scan + fix via GoldenCheck) and `transform` (normalize via GoldenFlow) skills added to the Agent-to-Agent protocol
- `run_transform(strict=True)` parameter — MCP/A2A handlers surface transform failures instead of silently returning unmodified data
- `_scan_only()` now returns serialized findings so MCP tools can inspect quality issues without duplicating the scan
- 10 new tests: happy-path coverage with mocked deps, file validation, write failure handling

### Fixed
- Eliminated redundant double-scan in `scan_quality` MCP handler (was scanning data twice and reaching into goldencheck internals)
- Temp file cleanup handles `PermissionError` on Windows (file locks no longer leak orphaned temp files)
- `_serialise_result` exception clause narrowed from `Exception` to `ImportError`
- `fix_quality` test assertion strengthened to check error message content

## [1.4.0] - 2026-04-06

### Added
- **Scoring & survivorship quality upgrade** — MST-based cluster auto-splitting, cluster quality labels (strong/weak/split), quality-weighted survivorship strategies, field-level provenance tracking
- **Data-driven strategy selection** — auto-config selects learned blocking (>= 5K rows), enables cross-encoder reranking (3+ fields), adjusts thresholds from data quality (null rate, string length)
- **`llm_auto` flag** — `GoldenMatchConfig.llm_auto=True` auto-enables LLM scorer ($0.05 budget) and memory store when API key detected. Applied uniformly across all config paths
- New config: `auto_split`, `quality_weighting`, `weak_cluster_threshold` in `GoldenRulesConfig`

### Fixed
- Pipeline wires `auto_split` config to `build_clusters`
- `add_to_cluster` documents oversized-flag-only behavior (callers must split)
- Threshold adjustments mutually exclusive (high-null and short-string no longer cancel out)

## [1.3.2] - 2026-04-03

### Fixed
- Auto-config: blocking keys with zero value overlap between sources are now skipped with a warning (fixes DBLP-ACM venue blocking failure where DBLP uses "VLDB" and ACM uses "Very Large Data Bases")
- Embedding scorer: falls back to token_sort when embedding model fails to load (HuggingFace auth, Vertex AI quota, missing dep, CUDA OOM) instead of crashing the pipeline

## [1.3.1] - 2026-04-03

### Added
- GoldenFlow integration: optional data transformation step in the dedupe pipeline (`pip install goldenmatch[transform]`)
- `TransformConfig` Pydantic model (enabled, mode: announced/silent/disabled)
- Pipeline step 1.4b: GoldenFlow runs after GoldenCheck, before autofix — normalizes phone numbers, dates, categoricals, unicode
- Graceful degradation: if goldenflow crashes, logs warning and continues with untransformed data
- Warning when config enables transforms but goldenflow is not installed
- 8 new tests

## [1.3.0] - 2026-04-03

### Added
- CCMS cluster comparison: `compare_clusters()` classifies each cluster from run A as unchanged, merged, partitioned, or overlapping relative to run B (based on Talburt et al., arXiv:2601.02824v1)
- `CompareResult` and `ClusterCase` dataclasses with `summary()` method
- Talburt-Wang Index (TWI) for normalized clustering similarity (1.0 = identical, approaches 0 for divergent outcomes)
- Parameter sensitivity analysis: `run_sensitivity()` sweeps config parameters and compares each run against a baseline using CCMS
- `SweepParam`, `SweepPoint`, `SensitivityResult` dataclasses with `stability_report()` for identifying optimal parameter ranges
- Supported sweep fields: `threshold` (all fuzzy matchkeys), `matchkey.<name>.threshold` (individual), `blocking.max_block_size`
- `--sample` option for sensitivity sweeps (random subsample for speed on large datasets)
- Per-point error handling: failed sweep points are logged and skipped, partial results preserved
- CLI command `goldenmatch compare-clusters` with `--details`, `--case-type` filter, `--output` JSON
- CLI command `goldenmatch sensitivity` with `--sweep field:start:stop:step` (repeatable), `--sample`, `--output`
- 16 new tests (10 comparison, 6 sensitivity)

## [1.2.7] - 2026-04-02

### Added
- Three auto-config cardinality guards to prevent failures on edge-case data:
  - Blocking: exclude near-unique columns (cardinality_ratio >= 0.95)
  - Matchkeys: skip exact matchkeys for low-cardinality columns (cardinality_ratio < 0.01)
  - Description columns: route long text to fuzzy matching (token_sort) alongside embedding
- Library comparison benchmarks: head-to-head against Splink, Dedupe, and RecordLinkage on Febrl (0.971 F1) and DBLP-ACM (0.918 F1)

### Fixed
- Auto-config no longer generates blocking keys from near-unique columns that produce single-record blocks
- Auto-config no longer creates exact matchkeys for columns with very few distinct values (e.g., gender, status)
- Description/long-text columns now get fuzzy fallback scoring instead of embedding-only

## [1.2.6] - 2026-04-01

### Added
- Iterative LLM calibration: samples ~100 pairs per round, learns optimal threshold via grid search, converges in 2-3 rounds (~200 pairs, ~$0.01) instead of scoring all candidates
- Concurrent LLM requests via ThreadPoolExecutor with configurable `max_workers` (default 5)
- Thread-safe BudgetTracker with `threading.RLock`
- ANN hybrid blocking: oversized blocks fall back to ANN sub-blocking via embeddings (embeds only unique text values)
- LLM-assisted column classification for ambiguous auto-config types
- Utility-based fuzzy field ranking (cardinality × completeness × string length)
- Price/cost/amount column name patterns to prevent zip misclassification
- `get_embedder()` GPU routing — returns VertexEmbedder when mode=vertex
- 3 new LLMScorerConfig fields: `calibration_sample_size`, `calibration_max_rounds`, `calibration_convergence_delta`
- 3 new ColumnProfile fields: `null_rate`, `cardinality_ratio`, `avg_len`
- 40 new tests (test_llm_calibration.py, test_ann_subblock.py, expanded test_autoconfig.py)

### Fixed
- ID patterns checked before phone/zip in auto-config — SalesID no longer misclassified as "phone"
- SalePrice (5-digit amounts) no longer misclassified as "zip"
- Identifier classifications authoritative over data profiling
- fiModelDesc no longer dropped from fuzzy fields on wide datasets
- Default batch_size bumped from 20 to 75
- "Never demote" behavior: LLM-rejected pairs keep original fuzzy score (was 0.0)
- Robust error handling: URLError/timeout retried, fut.result() guarded, ANN failures caught gracefully
- VertexEmbedder import failures fall back to local embedder

### Changed
- LLM scorer uses iterative calibration when candidates > calibration_sample_size (100)
- Multi-pass blocking passes ann_column/ann_top_k/ann_model to static builder
- `_classify_by_name` check order: date → email → ID → price → zip → geo → address → phone → name

## [1.2.0] - 2026-03-25

### Added
- **Autonomous ER Agent** -- GoldenMatch as a discoverable AI agent via A2A and MCP protocols
- `AgentSession` class -- profiles data, selects strategy, runs pipeline, explains reasoning
- `ReviewQueue` with confidence gating (auto-merge >0.95, review 0.75-0.95, reject <0.75)
- Three storage backends for review queue: memory (default), SQLite, Postgres
- `gate_pairs()` -- split scored pairs by confidence thresholds
- A2A server (`goldenmatch agent-serve`) with agent card, task lifecycle, SSE streaming
- 8 A2A skills: analyze_data, configure, deduplicate, match, explain, review, compare_strategies, pprl
- 10 MCP agent-level tools (additive to existing tools)
- `goldenmatch agent-serve --port 8200` CLI command
- Demo script: `python examples/agent_demo.py`
- Branch & Merge SOP added to CLAUDE.md

## [1.1.0] - 2026-03-23

### Added
- `gm.dedupe_df()` -- deduplicate a Polars DataFrame directly (no file I/O)
- `gm.match_df()` -- match two Polars DataFrames directly (no file I/O)
- `gm.score_strings()` -- score two strings with a named similarity algorithm
- `gm.score_pair_df()` -- score a pair of record dicts
- `gm.explain_pair_df()` -- explain a pair match from record dicts
- Internal: `run_dedupe_df()` and `run_match_df()` pipeline entry points
- These functions are the prerequisite for native SQL extensions (Postgres/DuckDB)
- New companion repo: [goldenmatch-extensions](https://github.com/benseverndev-oss/goldenmatch-extensions) -- PostgreSQL extension (`goldenmatch_pg`) and DuckDB extension (`goldenmatch-duckdb`) for in-database entity resolution via SQL

## [1.0.0] - 2026-03-23

### Changed
- **Production/Stable** -- dropped Beta label. Semver strictly enforced from this release.
- Public API surface frozen: 96 exports from `import goldenmatch as gm`, 21 CLI commands, config YAML schema, REST endpoints, MCP tools. See `docs/api-stability.md`.

### Added
- Clean Python API: `gm.dedupe()`, `gm.match()`, `gm.pprl_link()`, `gm.evaluate()` with typed results
- 96 public exports covering every feature (config, pipeline, streaming, LLM, PPRL, domain, explain, etc.)
- REST API client: `gm.Client("http://localhost:8000")`
- Jupyter/notebook display: `_repr_html_()` on DedupeResult and MatchResult
- CI/CD quality gates: `goldenmatch evaluate --min-f1 0.90` exits code 1 if below threshold
- 7 runnable example scripts in `examples/`
- `goldenmatch label` CLI for interactive ground truth building

## [0.7.0] - 2026-03-23

### Added
- Ray distributed backend for large-scale entity resolution (`pip install goldenmatch[ray]`)
- `--backend ray` CLI flag for dedupe command
- `backend: ray` config option in GoldenMatchConfig
- `backends/ray_backend.py` with `score_blocks_ray()` -- drop-in replacement for ThreadPoolExecutor
- Automatic fallback to parallel scorer for small block counts (<= 4)
- Ray auto-initializes locally using all CPU cores, no user configuration needed
- Supports Ray clusters for 50M+ record workloads
- `goldenmatch label` CLI command -- interactive pair labeling to build ground truth CSV for accuracy measurement (y/n/s keyboard input)

## [0.6.0] - 2026-03-23

### Added
- Privacy-preserving record linkage (PPRL) package (`goldenmatch/pprl/`)
- Trusted third party mode: parties send encrypted bloom filters, coordinator computes similarity
- SMC mode: secret-shared dice similarity, only match bits revealed (simulated circuit)
- `goldenmatch pprl link` CLI command for cross-party linkage
- Bloom filter security levels: standard (512-bit), high (1024-bit + HMAC), paranoid (2048-bit + balanced padding)
- Per-field HMAC salting prevents cross-field correlation attacks
- Balanced bloom filter padding normalizes filter density for short strings
- Custom HMAC key support via transform parameter (`bloom_filter:2:20:512:my_key`)
- `pip install goldenmatch[pprl]` optional dependency group
- PPRL auto-configuration (`auto_configure_pprl`) -- profiles data, selects optimal fields, bloom filter parameters, and threshold automatically. 92.4% F1 on FEBRL4, 76.1% on NCVR
- MCP tools: `pprl_auto_config` (auto-configure PPRL for a dataset), `pprl_link` (run cross-party linkage)
- Vectorized PPRL similarity computation (13x speedup over row-wise scoring)
- NCVR (North Carolina Voter Registration) and FEBRL4 benchmark suites for PPRL evaluation

## [0.5.0] - 2026-03-23

### Added
- In-context LLM clustering (`mode: cluster`) -- send blocks of 50-100 borderline records to LLM for direct cluster assignment instead of pairwise yes/no scoring
- Uncertainty scores -- LLM returns confidence per cluster, surfaced in cluster metadata and review queue
- `core/llm_cluster.py` -- new module with component detection, graph splitting, structured JSON parsing, pairwise fallback
- LLMScorerConfig gains `mode`, `cluster_max_size`, `cluster_min_size` fields
- Budget-aware degradation: cluster mode -> pairwise fallback -> stop

## [0.4.0] - 2026-03-23

### Added
- CI/CD pipeline: automated tests on Python 3.11/3.12/3.13, ruff lint, smoke test
- `py.typed` PEP 561 marker for type checker support
- `docs/api-stability.md` documenting the public API surface
- This CHANGELOG

### Changed
- Version policy: public API surface defined and documented ahead of 1.0 semver commitment

## [0.3.1] - 2026-03-22

### Added
- 5 new domain packs: healthcare, financial, real_estate, people, retail (7 total)
- `goldenmatch evaluate` CLI command -- precision/recall/F1 against ground truth CSV
- `goldenmatch incremental` CLI command -- match new records against existing base
- GitHub Actions "Try It" workflow for zero-install demo
- GitHub Codespaces devcontainer
- `dbt-goldenmatch` package for DuckDB-based entity resolution
- GitHub Discussions, issue templates, community standards (CoC, contributing, security)
- PyPI download badge in README

## [0.3.0] - 2026-03-21

### Added
- Fellegi-Sunter probabilistic matching with EM-trained m/u probabilities
- Learned blocking -- data-driven predicate selection
- LLM scorer with budget controls (BudgetTracker, cost caps, model tiering)
- Domain-aware feature extraction (electronics, software auto-detection)
- Custom domain registry (YAML rulebooks, MCP tools)
- Plugin architecture (scorers, transforms, connectors, golden strategies via entry points)
- Enterprise connectors: Snowflake, Databricks, BigQuery, HubSpot, Salesforce
- DuckDB backend for out-of-core processing
- Streaming/CDC mode with StreamProcessor
- Multi-table graph entity resolution
- Natural language explainability (zero LLM cost)
- Lineage tracking with streaming writer (no 10K cap)
- REST API review queue for data steward approval
- Daemon mode with health endpoint and PID file
- MCP server tools: list_domains, create_domain, test_domain, suggest_config

### Changed
- LLM scorer refactored to accept LLMScorerConfig with BudgetConfig
- Pipeline: domain extraction step between standardize and matchkeys
