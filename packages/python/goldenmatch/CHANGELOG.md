# Changelog

All notable changes to GoldenMatch are documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/). Versioning follows [Semantic Versioning](https://semver.org/) (strict after v1.0.0).

## [Unreleased]

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
- New companion repo: [goldenmatch-extensions](https://github.com/benzsevern/goldenmatch-extensions) -- PostgreSQL extension (`goldenmatch_pg`) and DuckDB extension (`goldenmatch-duckdb`) for in-database entity resolution via SQL

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
