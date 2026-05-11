# GoldenMatch

## Related Projects
- **SQL Extensions repo:** `D:\show_case\goldenmatch-extensions` -- Postgres extension + DuckDB UDFs. Has its own CLAUDE.md.
- **PyPI:** `goldenmatch` (Python toolkit), `goldenmatch-duckdb` (DuckDB UDFs)
- **npm:** `goldenmatch` (TypeScript port at `packages/goldenmatch-js/`)
- **GitHub:** `benzsevern/goldenmatch`, `benzsevern/goldenmatch-extensions`

## TypeScript Port
Lives at `packages/typescript/goldenmatch/`. See that package's CLAUDE.md for npm release flow, edge-safety rules, parity harness, and port-specific gotchas.

## Environment (goldenmatch-specific)
Root CLAUDE.md owns: branch/merge SOP, GitHub auth dance, Rust + pgrx, PostgreSQL 16 portable. Goldenmatch-only quirks:
- GCP project: `gen-lang-client-0692108803` (Vertex AI embeddings)
- Polars `scan_csv` uses `encoding="utf8"` not `"utf-8"`
- Polars `read_excel` needs explicit `engine="openpyxl"`
- **Release tags:** Python = `v1.x.y` (triggers `publish-goldenmatch.yml` → PyPI). TypeScript = `goldenmatch-js-v0.x.y` (triggers `publish-goldenmatch-js.yml` → npm). Never push an unprefixed version tag for TS.

## Testing
- **TypeScript:** `cd packages/goldenmatch-js && npx vitest run` — 478 tests currently. Full check: `npx tsc --noEmit && npx vitest run && npm run build`
- TS parity check: `tests/parity/` — add new parity cases when porting any new scorer or algorithm
- `pytest --tb=short` from project root — all tests must pass after every change
- 1319 tests (+ 6 skipped for optional deps), run in ~60s
- e2e tests calling `dedupe_df(df)` on auto-config: auto-config enables `rerank=True` for 3+ field weighted matchkeys, which loads a cross-encoder model from HuggingFace. Offline CI fails with download error. Pattern: pre-build config via `auto_configure_df(df)`, set `mk.rerank = False` on weighted matchkeys, then pass `dedupe_df(df, config=config)`. See `tests/test_autoconfig_regressions.py::test_dedupe_df_interaction_all_three_fixes_together`.
- Synthetic person fixtures in `tests/test_autoconfig_regressions.py`: `_person_df(n)` for realistic person shape, `_gate_test_df(n)` for cheap row-count-only boundary tests (gates that only read `df.height`). Reuse rather than rewrite.
- Coverage: 72% (with db/mcp/connectors excluded via pyproject.toml [tool.coverage.run] omit)
- Key module coverage: scorer 87%, probabilistic 96%, pprl/autoconfig 95%, _api 85%, pipeline 82%
- Fixtures in `tests/conftest.py`: `sample_csv`, `sample_csv_b`, `sample_parquet`
- TUI tests use `pytest-asyncio` with `app.run_test()` pilot
- Benchmark scripts in `tests/bench_1m.py`, `tests/analyze_results.py` (not part of test suite)
- Synthetic test data generator: `tests/generate_synthetic.py`
- DB tests (`test_db.py`, `test_reconcile.py`) need PostgreSQL — skip with `--ignore` if not available
- `import torch` hangs on this machine — tests mocking GPU must patch `_has_cuda`/`_has_mps` at module level
- `testing.postgresql` teardown errors on Windows (SIGINT) are harmless — tests still pass
- CI workflow: `.github/workflows/ci.yml` -- test matrix (3.11/3.12/3.13), ruff lint (E9/F63/F7 only), smoke test. Ignores test_db, test_reconcile, test_mcp_and_watch
- Ray tests require `ray` optional dep -- use `pytest.mark.skipif(not HAS_RAY)` pattern
- Windows drive letter tests must use `@pytest.mark.skipif(sys.platform != "win32")` -- Path.stem behaves differently on Linux
- sdist includes benchmark datasets (can bloat to 500MB+) -- add large data dirs to `.gitignore` before building
- Memory tests use `tmp_path` fixture for isolated SQLite: `MemoryStore(backend="sqlite", path=str(tmp_path / "test.db"))`. 48 tests in test_memory_store.py, test_corrections.py, test_learner.py, test_memory_integration.py

## Architecture
- Pipeline: ingest → column_map → auto_fix → validate → standardize → matchkeys → block → score → cluster → golden → output
- SQL extensions: see `D:\show_case\goldenmatch-extensions\CLAUDE.md` for Postgres/DuckDB architecture
- `goldenmatch/core/agent.py` -- AgentSession, profile_for_agent, select_strategy, build_alternatives. Autonomous ER: profiles data -> detects domain -> selects strategy -> runs pipeline -> returns reasoning
- `goldenmatch/core/review_queue.py` -- ReviewQueue (memory/SQLite/Postgres backends), ReviewItem, gate_pairs(). Confidence gating: >0.95 auto-merge, 0.75-0.95 review, <0.75 reject
- `goldenmatch/core/memory/` -- Learning Memory: persistent corrections + rule learning. `store.py` (MemoryStore, SQLite/Postgres CRUD, trust-based upsert), `corrections.py` (apply_corrections with dual-hash staleness detection), `learner.py` (MemoryLearner, threshold tuning from 10+ corrections). Config: `MemoryConfig` in schemas.py, optional `memory:` YAML section
- `goldenmatch/a2a/` -- A2A protocol server (aiohttp). Agent card at `/.well-known/agent.json`, 10 skills, task lifecycle, SSE streaming. CLI: `goldenmatch agent-serve --port 8200`
- `goldenmatch/mcp/agent_tools.py` -- 13 agent-level MCP tools (additive to existing). Each creates own AgentSession (no shared global state)
- Adding MCP tools: add Tool to `AGENT_TOOLS` in `mcp/agent_tools.py`, add dispatch handler in `_dispatch()`, update server card tool count in `mcp/server.py` (line ~1002)
- Adding A2A skills: add entry to `_SKILLS` in `a2a/server.py`, add dispatch handler in `a2a/skills.py`, update `test_agent_card_has_N_skills` assertion in `tests/test_a2a.py`
- MCP/A2A handlers must validate `file_path` param, catch `FileNotFoundError` on `pl.read_csv`, and wrap `write_csv(output_path)` in try/except to preserve results on write failure
- `run_transform(strict=True)` re-raises exceptions instead of silently returning unmodified data — use from MCP/A2A handlers where callers explicitly requested transforms
- `_scan_only()` in `quality.py` returns serialized findings dicts (not empty list) so MCP tools can inspect them without reaching into goldencheck internals
- `_api.py` has DataFrame entry points: `dedupe_df()`, `match_df()`, `score_strings()`, `score_pair_df()`, `explain_pair_df()` -- used by SQL extensions
- `pipeline.py` refactored: `_run_dedupe_pipeline()` and `_run_match_pipeline()` extracted as shared internal functions, called by both file-based and DataFrame-based entry points
- `goldenmatch/core/` — pipeline modules (no Textual dependency)
- `goldenmatch/tui/` — Textual TUI + MatchEngine (engine.py has no Textual dependency)
- `goldenmatch/cli/` — Typer CLI commands (23 commands, including `unmerge`, `evaluate`, `incremental`, `pprl`, `label`, `compare-clusters`, `sensitivity`)
- `goldenmatch/db/` — Postgres integration (connector, sync, reconcile, clusters, ANN index)
- `goldenmatch/api/` — REST API server (`goldenmatch serve`)
- `goldenmatch/mcp/` — MCP server for Claude Desktop (`goldenmatch mcp-serve`)
- `goldenmatch/plugins/` — Plugin system (registry, base protocols for scorer/transform/connector/golden_strategy)
- `goldenmatch/connectors/` — Data source connectors (Snowflake, Databricks, BigQuery, HubSpot, Salesforce)
- `goldenmatch/backends/` — Storage backends (DuckDB for out-of-core processing)
- `goldenmatch/domains/` — Built-in YAML domain packs (electronics, software, healthcare, financial, real_estate, people, retail)
- `dbt-goldenmatch/` — Separate package for dbt integration via DuckDB
- Core modules: explainer, explain, evaluate, report, dashboard, graph, anomaly, diff, rollback, schema_match, chunked, cloud_ingest, api_connector, scheduler, gpu, vertex_embedder, llm_scorer, llm_budget, lineage, match_one, probabilistic, learned_blocking, streaming, graph_er
- Config: Pydantic models in `config/schemas.py`, YAML loading in `config/loader.py`
- `config/schemas.py` has `MemoryConfig` (enabled, backend, path, trust, learning) and `LearningConfig` (threshold_min_corrections, weights_min_corrections). `GoldenMatchConfig.memory` is optional
- `config/loader.py` normalizes golden_rules and standardization sections from flat YAML
- `GoldenRulesConfig` fields: `auto_split: bool = True` (auto-split oversized clusters via MST), `quality_weighting: bool = True` (use GoldenCheck quality scores in survivorship, no-op without GoldenCheck), `weak_cluster_threshold: float = 0.3` (edge gap threshold for confidence downgrade)

## Performance
- Exact matching uses Polars self-join (not Python group_by + combinations)
- Fuzzy matching uses `rapidfuzz.process.cdist` for vectorized NxN scoring
- Fuzzy blocks scored in parallel via `ThreadPoolExecutor` (`score_blocks_parallel` in scorer.py)
  - rapidfuzz.cdist releases the GIL so threads give real parallelism
  - Blocks are independent — frozen `exclude_pairs` snapshot avoids races
  - For <=2 blocks, skips thread overhead and runs sequentially
  - All call sites (pipeline.py, engine.py, chunked.py) use the shared helper
- Intra-field early termination in `find_fuzzy_matches`: after each expensive field, breaks early if no pair can reach threshold
- Standardizers have native Polars fast path (`_NATIVE_STANDARDIZERS` in standardize.py)
- Matchkey transforms have native Polars fast path (`_try_native_chain` in matchkey.py)
- Clustering uses iterative Union-Find (not recursive) with lazy pair_scores
- Blocking key choice dominates fuzzy performance — coarse keys create huge blocks
- 1M exact dedupe: ~7.8s. 100K fuzzy (name+zip): ~12.8s via pipeline
- Scale curve: 7,823 rec/s at 100K records on laptop (fuzzy + exact + golden)
- 1M records: OOM in-memory — use DuckDB backend or chunked processing for >500K records

## Accuracy Strategy
- Structured data (names, addresses, bibliographic): fuzzy matching alone → 97.2% F1. No embeddings or LLM needed.
- Library comparison (v1.2.7): Febrl 0.971 F1 (top-2, behind Splink 0.998), DBLP-ACM 0.918 F1 (top-2, behind RecordLinkage 0.923). Most consistent performer across data types — zero training data, explicit config required.
- Product matching (electronics/Abt-Buy): domain extraction + emb+ANN + LLM → **72.2% F1** (P=94.8%, $0.04). Domain extraction gets 393/1081 model matches for free.
- Product matching (software/Amazon-Google): emb+ANN + LLM → **45.3% F1** (P=63.3%, $0.02). Clean emb+ANN pipeline is best — adding domain extraction/token normalization/mfr blocking adds noise and hurts F1. SOTA is ~78% (GPT-4 few-shot, Ditto fine-tuned).
- Product matching lesson: adding candidate sources (domain extraction, token normalization, manufacturer blocking) helps electronics (Abt-Buy) but HURTS software (Amazon-Google). More pairs = more noise. For domains without precise identifiers, keep the candidate set clean and let the LLM filter.
- LLM scorer sends borderline pairs (0.75-0.95) to GPT, auto-accepts >0.95. Budget cap of $0.05 covers typical datasets.
- Fellegi-Sunter probabilistic: 98.8% precision, 57.6% recall, 72.8% F1 on DBLP-ACM. Opt-in for automatic parameter estimation and high-precision use cases. Uses Splink-style EM (fix u from random pairs, train only m).
- Learned blocking: auto-discovers predicates, 96.9% F1 matching hand-tuned static blocking
- Boost tab reranking can hurt on product data — quality check warns user to try `--llm-boost` instead
- Multi-field embedding helps structured data (DBLP-ACM) but not product data — descriptions differ in format across sources
- Benchmark evaluation: always use threshold-based pair generation, NOT top-1-per-record (argmax)
- Leipzig benchmarks: `python tests/benchmarks/run_leipzig.py`
- v0.3.0 benchmarks: `python tests/benchmarks/run_v030_quick.py` (F-S, learned blocking, LLM budget)
- Domain extraction benchmark: `python tests/benchmarks/run_domain_bench.py` (Abt-Buy) and `run_amazon_google_bench.py`
- LLM+embedding benchmark: `python tests/benchmarks/run_llm_budget_bench.py` (requires OPENAI_API_KEY)

## Code Patterns
- **Pydantic typed-accessor pattern for Optional invariants.** `MatchkeyConfig.threshold` / `MatchkeyField.scorer`/`weight`/`field` are typed Optional at the Pydantic level (YAML round-trip) but guaranteed non-None for `weighted`/`fuzzy` matchkey types post-validation. Pattern (PR #151): keep the field Optional, add a `@property` accessor (`fuzzy_threshold`, `fuzzy_scorer`, `fuzzy_weight`, `resolved_field`) that raises `ValueError` if the invariant was broken via post-construction mutation, and use the accessor at call sites that need the narrowed type. Pyright stops seeing Optional — dropped 7 `# pyright: ignore` suppressions in `scorer.py`.
- **Pyright suppressions on multi-line imports.** `# pyright: ignore[reportMissingImports]` must sit on the `from X import (` line itself, NOT on the imported-symbol continuation line. Pyright reports the error at the `from X` column; a comment on a later line doesn't satisfy it. Bit us on `autoconfig_policy.py` (openai) and `scorer.py` (sentence_transformers) in PR #147.
- Auto-config exact matchkeys: `col_type in ("zip","geo")` NEVER backs an exact matchkey (blocking signal, not identity claim). Exact matchkeys require `cardinality_ratio >= 0.5`. Skipped columns still flow into `build_blocking()`.
- Auto-config learned blocking: gated at `total_rows >= 50_000` in `autoconfig.py`. Sample size capped at `min(total_rows // 4, 5000)` so learner has held-out rows. Below 50K, static/multi_pass is default.
- `DedupeResult.total_records` = `dupes.height + unique.height` (golden is a rollup, NOT a separate row population). Adding golden double-counts every multi-member cluster.
- Internal columns prefixed with `__` (e.g. `__row_id__`, `__source__`, `__mk_*__`)
- File specs are tuples: `(path, source_name)` or `(path, source_name, column_map)`
- `GoldenMatchConfig.get_matchkeys()` returns matchkeys from either top-level or match_settings
- Matchkey type field: use `mk.type` (not `mk.comparison`) after validation
- Scorer returns `list[tuple[int, int, float]]` — (row_id_a, row_id_b, score)
- Pair confidence IS the match score (0.0-1.0) — no separate confidence field
- `build_clusters` returns `dict[int, dict]` with keys: members, size, oversized, pair_scores, confidence, bottleneck_pair, cluster_quality
- `cluster_quality` field: `"strong"` (normal), `"weak"` (confidence downgraded), `"split"` (auto-split from oversized)
- `confidence` = 0.4*min_edge + 0.3*avg_edge + 0.3*connectivity; `bottleneck_pair` = weakest link (id_a, id_b)
- Oversized clusters are auto-split via MST (minimum spanning tree) — weakest MST edge removed to guarantee disconnection
- `unmerge_record(record_id, clusters)` removes a record from its cluster, re-clusters remaining via stored pair_scores
- `unmerge_cluster(cluster_id, clusters)` shatters a cluster into singletons
- TUI has 6 tabs: Data, Config, Matches, Golden, Boost, Export (key 1-6)
- Boost tab: active learning with y/n/s keyboard labeling, trains LogisticRegression on labeled pairs
- `match_one(record, df, mk)` in `core/match_one.py` — single-record matching primitive for streaming
- `add_to_cluster(record_id, matches, clusters)` — incremental cluster update (join or merge)
- `ANNBlocker.add_to_index(embedding)` / `ANNBlocker.query_one(embedding)` — incremental FAISS ops
- PPRL: `bloom_filter` transform (CLK via SHA-256, configurable ngram/k/size), `dice`/`jaccard` scorers for fuzzy matching on encrypted data
- LLM scorer: `llm_score_pairs()` in `core/llm_scorer.py` — accepts `LLMScorerConfig` with optional `BudgetConfig` for cost tracking, model tiering, and graceful degradation
- LLM budget: `core/llm_budget.py` — `BudgetTracker` class tracks token usage, cost, and enforces `max_cost_usd`/`max_calls` limits. Budget summary in `EngineStats.llm_cost`
- Fellegi-Sunter: `core/probabilistic.py` — EM-trained m/u probabilities, comparison vectors (2/3/N-level), match weights as log-likelihood ratios. New matchkey `type: probabilistic`
  - Splink-style EM: u estimated from random pairs (fixed), only m trained via EM. Blocking fields get fixed neutral priors
  - Continuous EM (`train_em_continuous`) available but not default — discrete levels are more stable
  - `ContinuousEMResult` + `score_probabilistic_continuous` for advanced users
- Learned blocking: `core/learned_blocking.py` — data-driven predicate selection via two-pass approach (sample → train → apply). Config: `strategy: learned`
- Plugin system: `plugins/registry.py` — `PluginRegistry` singleton discovers plugins via entry points. Schema validators fall through to plugins for unknown scorer/transform names
- Connectors: `connectors/base.py` — `BaseConnector` ABC with `load_connector()` dispatch. Built-in: snowflake, databricks, bigquery, hubspot, salesforce. All optional deps.
- Explainability: `core/explain.py` — `explain_pair_nl()` template-based NL explanations, `explain_cluster_nl()` cluster summaries. Zero LLM cost.
- Lineage: `core/lineage.py` — `build_lineage` + `save_lineage` + `save_lineage_streaming` (no 10K cap). Supports `natural_language=True` for NL explanations. Auto-generated when pipeline writes output.
- DuckDB backend: `backends/duckdb_backend.py` — user-maintained DuckDB read/write. `read_table()`, `write_table()`, `list_tables()`. Optional dep.
- Streaming: `core/streaming.py` — `StreamProcessor` for incremental record matching (immediate or micro-batch). Uses `match_one` → `add_to_cluster`.
- Graph ER: `core/graph_er.py` — multi-table entity resolution with evidence propagation across relationships. Iterative convergence.
- CCMS comparison: `core/compare_clusters.py` — Case Count Metric System for comparing two ER clustering outcomes without ground truth. Classifies each cluster as unchanged/merged/partitioned/overlapping, computes TWI (Talburt-Wang Index). Based on Talburt et al. (arXiv:2601.02824v1).
- Sensitivity analysis: `core/sensitivity.py` — parameter sweep engine. `run_sensitivity()` varies threshold/blocking/matchkey params, compares each run against baseline via CCMS. `SweepParam`, `SweepPoint`, `SensitivityResult` with `stability_report()`. Per-point error handling preserves partial results.
- Domain extraction: `core/domain.py` — auto-detects product subdomain (electronics vs software), extracts brand/model/SKU/color/specs (electronics) or name/version/edition/platform (software). Model normalization strips hyphens, region/color suffixes. Pipeline step between standardize and matchkeys.
- LLM extraction: `core/llm_extract.py` — LLM-based feature extraction for low-confidence records. Reuses BudgetTracker. O(N) preprocessing, not O(N^2) pair scoring.
- Domain registry: `core/domain_registry.py` — YAML-based custom domain rulebooks. Search paths: `.goldenmatch/domains/` (local), `~/.goldenmatch/domains/` (global), `goldenmatch/domains/` (built-in). MCP tools: `list_domains`, `create_domain`, `test_domain`
- MCP `suggest_config` tool: analyze bad merges, identify guilty fields, suggest threshold/weight changes
- REST review queue: `GET /reviews` returns borderline pairs for steward review, `POST /reviews/decide` records approve/reject decisions
- Daemon mode: `watch_daemon()` in `db/watch.py` — adds health endpoint (HTTP /health), PID file, SIGTERM handling to watch mode
- PPRL package: `pprl/protocol.py` — multi-party privacy-preserving record linkage. `PPRLConfig` dataclass, `run_pprl()` convenience function, `link_trusted_third_party()` and `link_smc()` protocol implementations. CLI: `goldenmatch pprl link`. Bloom filter security levels (standard/high/paranoid) with HMAC salting and balanced padding in `utils/transforms.py`.
- Ray backend: `backends/ray_backend.py` -- distributed block scoring via Ray tasks. Drop-in replacement for ThreadPoolExecutor. `pip install goldenmatch[ray]`, config `backend: ray` or CLI `--backend ray`. Auto-initializes locally, falls back to parallel scorer for <= 4 blocks. Pipeline uses `_get_block_scorer(config)` to select scorer function.
- PPRL auto-config: `auto_configure_pprl()` profiles data and picks optimal fields, bloom filter parameters, and threshold. Beats manual tuning -- 92.4% F1 on FEBRL4 (vs 89.8% manual), 76.1% F1 on NCVR. MCP tools: `pprl_auto_config`, `pprl_link`.
- LLM clustering: `core/llm_cluster.py` — in-context block clustering as alternative to pairwise LLM scoring. Config `llm_scorer.mode: cluster`. Builds connected components from borderline pairs, sends blocks to LLM, synthesizes pair_scores from cluster confidence for compatibility with Union-Find/unmerge/lineage. Degrades: cluster → pairwise → stop.
- Evaluation: `core/evaluate.py` — `EvalResult` dataclass, `evaluate_pairs()`, `evaluate_clusters()`, `load_ground_truth_csv()`. CLI: `goldenmatch evaluate --config X --ground-truth Y`
- Incremental CLI: `cli/incremental.py` — match new CSV records against existing base dataset. Handles exact (Polars join) and fuzzy (match_one brute-force) matchkeys separately
- Domain packs: 7 built-in YAML rulebooks in `goldenmatch/domains/` — electronics, software, healthcare, financial, real_estate, people, retail. Auto-discovered by `discover_rulebooks()`
- GitHub infrastructure: `.github/workflows/try-it.yml` (workflow_dispatch demo), `.devcontainer/` (Codespaces)
- dbt integration: `dbt-goldenmatch/` separate package with `run_goldenmatch_dedupe()` for DuckDB tables
- Python API: `_api.py` provides `dedupe()`, `match()`, `dedupe_df()`, `match_df()`, `pprl_link()`, `evaluate()`, `score_strings()`, `score_pair_df()`, `explain_pair_df()` convenience functions. `__init__.py` re-exports ~101 symbols. `DedupeResult`/`MatchResult` have `_repr_html_()` for Jupyter.
- REST client: `client.py` — `Client(base_url)` with `.match()`, `.list_clusters()`, `.explain()`, `.reviews()`. Uses stdlib `urllib` only.
- CI/CD quality gates: `goldenmatch evaluate --min-f1 0.90 --min-precision 0.80` exits code 1 if thresholds not met
- Pipeline backend selection: `_get_block_scorer(config)` in pipeline.py returns `score_blocks_parallel` or `score_blocks_ray` based on `config.backend`
- PPRL vectorized similarity: use numpy matrix multiply (`mat_a @ mat_b.T`) for bloom filter dice, NOT per-pair Python loops. 13x speedup.
- PPRL auto-config: penalize near-unique fields (IDs), long fields (>15 chars), high-null fields. Min threshold 0.85. max_fields=4 beats 6.
- NCVR voter data: tab-delimited, 488MB zip at `tests/benchmarks/datasets/NCVR/`. Gitignored. `birth_year` only (no full DOB). `ssn` is Y/N flag not actual SSN. The 10K-row sample at `ncvoter_sample_10k.txt` is the file the controller benchmark consumes; created by streaming the first 10K rows out of `ncvoter_Statewide.zip` (avoids the full 4.3GB extract). Both files are gitignored.
- AgentSession creates own state (data, config, result, review_queue) -- not shared with MCP global state
- `select_strategy(profile)` returns StrategyDecision with auto_execute=False for PPRL (requires caller confirmation)
- A2A agent card must include `inputModes`/`outputModes` on every skill and `provider` field at top level
- A2A server runs on separate port (8200) from REST API (8000) -- aiohttp for async/SSE, existing REST stays synchronous
- `aiohttp` is optional dep: `pip install goldenmatch[agent]`

## Remote MCP Server

Hosted on Railway, registered on Smithery:
- **Endpoint:** `https://goldenmatch-mcp-production.up.railway.app/mcp/`
- **Smithery:** `https://smithery.ai/servers/benzsevern/goldenmatch`
- **Server card:** `https://goldenmatch-mcp-production.up.railway.app/.well-known/mcp/server-card.json`
- **Transport:** Streamable HTTP (via `StreamableHTTPSessionManager`)
- **Dockerfile:** `Dockerfile.mcp` (Python 3.12-slim, installs `.[mcp]`)
- **Railway project:** `golden-suite-mcp` (service: `goldenmatch-mcp`, port 8200)
- **Local HTTP:** `goldenmatch mcp-serve --transport http --port 8200`

## Auto-Config
- `dedupe_df()` supports zero-config: calls `auto_configure_df(df)` when no exact/fuzzy kwargs
- `auto_configure_df(df)` in `core/autoconfig.py` — profiles DataFrame directly (no file I/O)
- `auto_configure(files)` delegates to `auto_configure_df` after loading files
- Classification: date/geo name heuristics are authoritative over data profiling
- Blocking safety: skips columns with >20% null rate, checks max block size (1000)
- Cardinality guards (v1.2.7):
  - Blocking: skips columns with cardinality_ratio >= 0.95 (near-unique, produces single-record blocks)
  - Matchkeys: skips exact matchkeys for columns with cardinality_ratio < 0.01 (too few distinct values)
  - Description columns: routes long text to fuzzy matching (token_sort) alongside embedding scorer
- `_DATE_PATTERNS` in autoconfig.py — checked before phone/name to prevent shadowing
- `_GEO_PATTERNS` expanded: matches city_desc, state_cd, county (not just ^city$)
- `utf8-lossy` encoding on all CSV read paths (ingest.py, agent.py, chunked.py, smart_ingest.py, skills.py)
- `golden` records != total output. `unique + golden = total distinct people`
- `llm_scorer` and `backend` kwargs applied uniformly after config resolution (not inside zero-config branch)
- Controller iterates on ComplexityProfile signals (block-size dist, score histogram dip, transitivity, candidates_compared, mass_above_threshold). HeuristicRefitPolicy is the v1 policy; LearnedRefitPolicy / LLMRefitPolicy are v2 hooks.
- Stage instrumentation in core/blocker.py, core/scorer.py, core/cluster.py, core/matchkey.py, core/autoconfig.py, core/domain.py emits sub-profiles via a thread-local ProfileEmitter stack (zero cost when no capture is active).
- Controller history is on PostflightReport.controller_history (RunHistory with .decisions, .errors, .full_vs_sample_drift). RunHistory.decisions is the audit trail of which rules fired and why — useful for explaining auto-config output to users.
- **Tier 3 (LLMRefitPolicy):** `GOLDENMATCH_AUTOCONFIG_LLM=1` enables LLM fallback when heuristic rules are exhausted but profile is RED/YELLOW. Requires `OPENAI_API_KEY`. Default OFF. Wraps `HeuristicRefitPolicy` — heuristic always fires first; LLM is last resort only. Max 5 LLM calls per run (configurable). See `core/autoconfig_policy.py::LLMRefitPolicy`.
- **Tier 4 (AutoConfigMemory):** `GOLDENMATCH_AUTOCONFIG_MEMORY=0` disables cross-run memory (useful in CI). Default ON. Uses `~/.goldenmatch/autoconfig_memory.db`.
- **`RunHistory.pick_committed()`** commits the best-effort entry by lex key `(health_rank, -mass_separation, iteration)` — returns a RED entry when no GREEN/YELLOW exists. `cheapest_healthy()` is a deprecated alias (removed in v2.0). `precision_collapse_floor=0.9` demotes RED entries with `mass_above_threshold > 0.9` to rank=3 to guard the "everything matches" pathology.
- **`RunHistory.stop_reason: StopReason | None`** set at every break point in the iteration loop. Observable via `result.postflight_report.controller_history.stop_reason`. `StopReason` lives in `core/complexity_profile.py`.
- **Virtual v0 entry:** after the loop, `config_v0`'s profile is appended as `HistoryEntry(iteration=-1)` so `pick_committed()` can fall back to v0 when all real iterations are worse. WARNING/INFO commit log uses `iter=v0` to identify virtual-entry commits.
- **Health-aware commit logging:** WARNING when committed health is RED (with failing sub-profile name + stop_reason); INFO when YELLOW; silent on GREEN. ERROR when every iteration errored (falls back to v0 + RED sentinel).
- **v1.10 indicators** (2026-05-08): added 5 complexity indicators in `core/indicators.py`. **Cheap eager** (always run): `compute_column_priors` (per-column identity_score + corruption_score), `estimate_sparse_match_signal` (n_exact_hits in sample). **Expensive lazy** (via `IndicatorContext` memoized methods, gated by `GOLDENMATCH_AUTOCONFIG_INDICATOR_BUDGET=fast`): `compute_corruption_score`, `estimate_full_pop_hits`, `compute_cross_blocking_overlap`. Indicators feed into `rule_no_matches` and `rule_blocking_key_swap` (modified) plus 3 new rules: `rule_corruption_normalize` (adds normalize when blocking col is corrupted+identity), `rule_cross_blocking_disagreement` (multi-pass on orthogonal key when overlap is low), `rule_sparse_match_expand` (lower threshold + side-channel when sample is sparse). `RefitPolicy.propose` accepts optional `ctx: IndicatorContext | None` kwarg; controller introspects custom-policy signatures via `inspect.signature` for backward compat.
- **v1.10 ship target**: fallback met (DQbench composite 66.91 >= 65; primary target >= 70 not reached). Real DQbench gains came from T2 recovery (+10.3 pp, 58.7% → 69.0%) — indicators allow the controller to correctly avoid abandoning identity blocking columns on T2 shapes. T1 and T3 unchanged vs v1.9.
- **v1.11 negative evidence** (2026-05-10): adds `NegativeEvidenceField` to `MatchkeyConfig` + `_apply_negative_evidence` in the weighted-matchkey scoring loop (subtracts penalty when an NE field disagrees, i.e. scorer < threshold). `promote_negative_evidence` is the eager rule that populates NE at auto-config time: it selects columns with `identity_score >= _IDENTITY_SCORE_THRESHOLD (0.75)`, cardinality >= 0.5, not in blocking, not in weighted matchkey fields, AND with an exact matchkey counterpart (the **exact-matchkey gate**). The gate prevents NE from being added for columns that have no strong anchor — removing it gives composite 68.92 vs 66.99 on T3-heavy data but hurts T2 recall (68.18 vs 69.03). `rule_demote_clustered_identity` (new in v1.11) detects adversarial identity-column reuse: if a sample of exact-matchkey columns shows collision_rate >= 0.75, the exact matchkey is demoted to avoid false merges. Threshold 0.75 was raised from 0.5 after diagnosis showed T2's collision rate (0.615) was triggering false demotions and producing 186 FNs. `compute_identity_collision_signal` in `core/autoconfig_rules.py` does the sample-based collision measurement. `_pick_scorer_for_column` selects the per-column scorer for NE fields (email→token_sort, phone→exact+digits_only, address→token_sort, other→ensemble). See `core/autoconfig_negative_evidence.py` and `core/autoconfig_rules.py`.
- **v1.11 ship target**: incremental improvement met (DQbench composite 66.99 > v1.10's 66.91; primary >= 75 and fallback >= 70 not reached). T3 FP root cause: the zeroconfig adapter's `exact_email` matchkey directly captures adversarial same-email pairs — NE on the `fuzzy_match` weighted matchkey cannot penalise pairs already matched by an exact rule. T3's collision rate (0.592) is also BELOW T2's (0.615), so no valid collision threshold can distinguish T3-adversarial from T2-normal. Fixing T3 requires either: (a) NE propagated into exact matchkey scoring (v1.12 candidate), or (b) cluster-level re-scoring after all matchkeys fire. Benchmark deltas vs v1.10: T1 88.9%→88.9% (flat), T2 69.0%→69.0% (flat), T3 53.8%→53.8% (flat at DQbench-adapter level; raw exact_email FPs unchanged). Composite improvement is noise-level; v1.11 value is the NE infrastructure and `rule_demote_clustered_identity` guard, not a measurable DQbench gain.
- **v1.12 Path Y** (2026-05-09): extends `_apply_negative_evidence` to exact matchkeys via the new `_apply_negative_evidence_to_exact_pairs` post-filter helper in `core/scorer.py` (called from `core/pipeline.py` after `find_exact_matches`). Score formula: `final = max(0, 1.0 - sum(penalties))`; emit if `final >= matchkey.threshold` (default 0.5 when NE set + threshold None). Backward compat: exact matchkey without NE preserves today's binary 1.0/0.0 emit. `promote_negative_evidence` extended to walk all matchkey types; the `_is_exact_matchkey_field` gate is selectively applied (skipped on the exact-matchkey iteration branch — its v1.11 rationale doesn't apply when iterating an exact matchkey for itself). When NE is added to a threshold-None exact matchkey, threshold defaults to 0.5 to activate the score-and-threshold path. Targets DQbench T3 53.8% → 70%+ via NE penalty filtering collision pairs directly on the exact_email matchkey.
- **v1.12 ship target**: PRIMARY target met (DQbench composite 91.04 >= 75). T3 F1 85.5% (target >= 70%). T1 89.3% (floor 88.9%), T2 97.5% (floor 69.0%). Benchmark deltas vs v1.11: T1 89.3%→89.3% (flat), T2 97.5% (was 69.0%; +28.5 pp), T3 85.5% (was 53.8%; +31.7 pp). Composite 66.99 → 91.04 (+24.05 pp). Path Y NE on exact matchkeys directly filters adversarial collision pairs at the `exact_email` matchkey level, resolving the v1.11 root cause.

## Gotchas
- `docs/superpowers/` is gitignored — specs and plans are local-only; do NOT `git add` them
- `tests/benchmarks/datasets/` is gitignored — tests reading those files must `pytest.skip` when absent OR be marked `@pytest.mark.benchmark` and excluded from default CI via `--ignore`
- `publish-npm.yml` runs the full vitest suite pre-publish — any flake blocks the release; can't retry, must fix + bump patch version + new tag
- `gh run watch <run-id> --exit-status` blocks until a workflow completes — useful for confirming publish success before moving on
- GitHub Discussions API: REST returns 404. Use GraphQL `createDiscussion` mutation with `repositoryId` (R_kgDORoztPA for this repo) and a `categoryId` fetched via `discussionCategories`.
- `gh repo edit --add-topic` fails at 20 topics (API-side cap). Drop low-value topics with `--remove-topic` before adding.
- Wiki repo: `git clone https://github.com/benzsevern/goldenmatch.wiki.git`, branch is `master` (not `main`).
- GoldenFlow (`date_iso8601`) runs BEFORE the inside-pipeline `auto_configure_df` call. This reshapes year-only columns into ISO date form, which then looks phone-shaped to the phone classifier. If auto-config misclassifies a date-ish column, check transform order, not just the classifier.
- GitHub release → PyPI publish workflow: ~25s via trusted publishing. PyPI JSON API takes ~20s to reflect new version after workflow completes — don't check immediately. Trigger is `release: published`, not tag push.
- `.github/workflows/*.yml` currently pin `actions/checkout@v4` and `actions/setup-python@v5` on Node.js 20, which deprecates Sep 2026. Bump or set `FORCE_JAVASCRIPT_ACTIONS_TO_NODE24=true` before then.
- `.profile_tmp/` is gitignored and used for local profiling artifacts (cProfile dumps, sampled parquet fixtures). `.profile_tmp/profile_ncvr.py` is the reference scorer profiling script — loads 200K NCVR voter sample and attributes scorer time across rapidfuzz vs. Python orchestration vs. Polars overhead.
- Leipzig DBLP-ACM dataset: `DBLP2.csv` uses `latin-1` encoding (not UTF-8). `ACM.csv` also latin-1.
- `recordlinkage.datasets.load_febrl3()` needs `return_links=True` to get ground truth pairs (default returns only DataFrame)
- v1.8 (2026-05-08): introspective controller (PR #103–#115) beats hand-tuned on multiple benchmarks: DBLP-ACM F1=0.9641 (hand-tuned ceiling 0.918), Febrl3 F1=0.9443, NCVR F1=0.9719, DQBench no-LLM score 62.87 (was 46.24 hand-tuned). Cross-run memory (`~/.goldenmatch/autoconfig_memory.db`), LLM policy fallback (`GOLDENMATCH_AUTOCONFIG_LLM=1`), per-pair LLM scoring auto-enable, standardization auto-detection. The "always use explicit config for non-trivial dedup" caveat is retired for bibliographic-shape and voter-record data; explicit config + domain extraction + LLM scorer remain the recommended path for product matching (Amazon-Google, Abt-Buy) where auto-config produces defensible but not optimal results.
- Comparison benchmark scripts in `D:\show_case\golden-showcase\comparison_bench\` — GoldenMatch, Splink, Dedupe, RecordLinkage on Febrl/DBLP-ACM/NC Voter
- `dedupe` library class is `dedupe.Dedupe` (not `dedupe.Deduper`). Empty strings cause `ZeroDivisionError` in affinegap — use single space as placeholder. Training pairs must go through `training_file` param, not `mark_pairs` directly.
- .docx files can't be read by Read tool — use `python-docx` or zipfile+XML
- Windows drive letter paths (C:\) break `file:source_name` CLI parsing — handle in `_parse_file_source`
- `ignore_errors=True` needed for `pl.read_csv` on files with junk rows
- Textual version 8.x installed (despite `>=1.0` pin) — API is stable
- Polars DLL hangs: kill zombie python with `powershell.exe -Command "Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force"` (bash `$_` gets mangled by extglob — must use powershell.exe)
- `test_embedder.py` and `test_llm_boost.py` segfault on this machine (torch access violation) — embedding is always via Vertex AI, skip these locally
- Run core tests without torch: `pytest tests/test_cluster.py tests/test_golden.py tests/test_lineage.py tests/test_config.py tests/test_pipeline.py`
- Ruff auto-fix can cascade-delete test functions when removing mid-file imports — always put imports at top of test files
- gcloud CLI sometimes hangs on Windows — try with `timeout 30 gcloud ...` first, fall back to REST API if it hangs. User ADC at `~/AppData/Roaming/gcloud/application_default_credentials.json`
- Vertex AI service account needs `roles/aiplatform.user` for embeddings — grant via `gcloud projects add-iam-policy-binding`. IAM changes take 1-2 minutes to propagate.
- Vertex AI `text-embedding-004` does NOT support fine-tuning — only inference. Use Colab GPU or local CPU for model training.
- `import torch` crashes/hangs on machines without GPU — use `goldenmatch.core.gpu.detect_gpu_mode()` to check before loading
- Polars infers zip/phone as Int64 — explainer/scorer must `str()` values before comparing
- Unicode em dashes (`—`) break on Windows terminals — use ASCII (`-`) in CLI help text
- GitHub Wiki: image paths must use `https://raw.githubusercontent.com/...` URLs, page links omit `.md`
- Textual headless screenshots: `async with app.run_test(size=(W,H)) as pilot: app.save_screenshot('path.svg')`
- PyPI publishing: `source .testing/.env && python -m build && python -m twine upload dist/*`
- `.testing/` folder is gitignored — store credentials, API keys, service account JSON there
- GitHub Wiki repo uses `master` branch, main repo uses `main`
- GitHub Wiki needs `_Sidebar.md` and `_Footer.md` for custom nav/footer
- Rich terminal recording: `Console(record=True)` then `console.export_svg(title='...')`
- PyPI version must be bumped in both `pyproject.toml` and `goldenmatch/__init__.py`
- v1.0.0 is live on PyPI -- Production/Stable, semver enforced — `pip install goldenmatch` works
- Adding a TUI tab: update `test_tabs_exist` in `tests/test_tui.py` — asserts exact tab count (currently 6)
- OpenAI API key: set `OPENAI_API_KEY` env var. Used by LLM scorer and LLM boost. Key stored in `.testing/.env`
- Leipzig benchmark CSVs have invalid UTF-8 — use `pl.read_csv(encoding="utf8-lossy", ignore_errors=True)`, not `load_file()`
- Fellegi-Sunter EM: blocking fields must be excluded from training (always agree within blocks, no discrimination). Pass `blocking_fields=` to `train_em()`.
- Fellegi-Sunter EM: u-probabilities must be estimated from random pairs and FIXED during EM (Splink approach). Training both m and u on blocked pairs causes collapse.
- Fellegi-Sunter: comparison_vector must apply field transforms before scoring — without `apply_transforms()`, case differences cause false disagrees
- `_call_openai` / `_call_anthropic` return `(text, input_tokens, output_tokens)` tuples (changed in v0.3.0 for budget tracking)
- GitHub Actions `pypi` environment needs PYPI_TOKEN secret for token-based publishing fallback (trusted publishing not configured)
- `match_one()` returns empty list for exact matchkeys (threshold=None). Incremental CLI handles exact separately via `find_exact_matches()` Polars join
- `evaluate_clusters()` uses cluster members→pairs expansion. `run_dedupe()` does NOT return `scored_pairs` — use clusters dict instead
- `load_ground_truth_csv()` tries int conversion on IDs — GoldenMatch row IDs are int64, ground truth CSVs may have strings
- Financial/healthcare domain regex patterns require contextual prefixes (CUSIP:, LEI:, NPI:, CPT:) to avoid false positives on generic number patterns
- `ruff check` with F82 (undefined name) flags string type annotations as false positives -- exclude from CI
- `score_blocks_parallel` requires `matched_pairs` arg (set) -- benchmark scripts must pass it
- `run_dedupe()` return dict has NO `stats` key -- compute stats from clusters/golden/dupes/unique DataFrames
- Unicode box drawing chars (pipe/dash) crash on Windows cp1252 terminal -- use ASCII in benchmark scripts
- GitHub release triggers publish workflow -- `twine upload --skip-existing` avoids double-publish errors
- `discover_rulebooks()` returns all 7 packs -- domain match tests must accept retail alongside electronics (overlapping signals like "brand", "sku")
- pgrx 0.12.9 does NOT auto-generate SQL files -- must provide handwritten `sql/goldenmatch_pg--0.1.0.sql` manually
- pgrx in workspace mode is broken -- postgres crate must be excluded from workspace (`exclude = ["postgres"]` in root Cargo.toml)
- pgrx extension functions live in `goldenmatch` schema (per .control file) -- must use `goldenmatch.function_name()` or explicit `::TEXT` casts in psql
- DuckDB UDFs cannot query the same connection they're called on (deadlock) -- use `con.cursor()` for table reads inside UDFs
- DuckDB `.pl()` (Polars conversion) requires `pyarrow` as a dependency
- Rust `cargo` defaults `CARGO_HOME` to the drive root on Windows when CWD is D: -- always set `CARGO_HOME="C:/Users/bsevern/.cargo"` explicitly
- `winget install Rustlang.Rustup` fails silently on Windows without Developer Mode -- use `rustup-init.exe -y` with `RUSTUP_WINDOWS_PATH_TYPE=hardlink`
- goldenmatch-extensions CI: 4 jobs (lint, bridge tests, postgres extension, duckdb tests). Release workflow builds binaries + Docker image on GitHub Release tag
- goldenmatch-extensions uses `benzsevern` GitHub account (same auth switch requirement as main repo)
- Trunk (pgt.dev) shut down July 2025 -- do not reference it for Postgres extension distribution
- dbdev (database.dev) only supports SQL/PL/pgSQL extensions (TLE) -- compiled C extensions not eligible
- Jekyll docs: `{{` in code blocks triggers Liquid template errors -- wrap with `{% raw %}` / `{% endraw %}`
- `typing_extensions` on Ubuntu CI: system package at `/usr/lib/python3/dist-packages/` overrides pip install -- must `sudo rm -f` the system file first
- pyo3 embeds Python linked at compile time -- CI must install goldenmatch into the same Python that Postgres uses
- DuckDB UDF `con.sql()` without `.fetchone()` may not execute the UDF -- always fetch results
- `json.dumps(clusters)` fails when cluster dict has tuple keys (pair_scores) -- use str() fallback
- Coverage config in pyproject.toml: omit db/*, mcp/*, vertex_embedder, connectors/* (require external services)
- GitHub Pages: docs workflow uses `actions/jekyll-build-pages` with source `./docs`, Just the Docs theme
- GitHub Release triggers publish.yml workflow which auto-publishes to PyPI via trusted publishing
- Scored pairs are canonicalized as `(min(id_a, id_b), max(id_a, id_b))` throughout cluster.py, graph.py, chunked.py, ann_blocker.py -- any new code storing/looking up pairs must canonicalize too
- v1.6.0 Learning Memory: end-to-end loop wired. Pipeline applies corrections + learned thresholds; 7 collection points (ReviewQueue, BoostTab, unmerge_record/cluster, LLM scorer, agent_approve_reject, REST `/reviews/decide`, Python API); 5 MCP tools (`list_corrections`, `add_correction`, `learn_thresholds`, `memory_stats`, `memory_export`); CLI subgroup `goldenmatch memory ...`. Spec: `docs/superpowers/specs/2026-05-04-learning-memory-completion.md` (foundation: pre-fold 2026-03-26 spec).
- `record_hash` excludes `__row_id__` so corrections survive row reordering across runs (the durability invariant; including it would defeat re-anchoring).
- `Correction.source` and `Correction.decision` are `StrEnum`s in `core/memory/store.py`. Trust mapping lives in `HIGH_TRUST_SOURCES` + `trust_for_source(source)` — use these instead of inline `if source in {...}: trust = 1.0`.
- `MemoryConfig.dataset` field validator strips whitespace and rejects empty strings; pass `None` to omit.
- `apply_corrections` reanchor builds `record_hash → list[row_id]` via `pl.concat_str` + `map_elements` (vectorized O(N)). Ambiguous re-anchors counted as `stale_ambiguous`, never silently misapplied.
- PyPI publish: `publish-goldenmatch.yml` lives at the **monorepo root**, not under this package. Trusted publishing NOT configured — uses `PYPI_TOKEN` secret. To enable trusted publishing later, claim PyPI publisher: owner `benzsevern`, repo `goldenmatch`, workflow `publish-goldenmatch.yml`, environment `pypi`.

## API + Common Mistakes

Moved to `packages/python/goldenmatch/docs/api-quick-reference.md` (reference content, not session context). DQBench ER scores live in the package README + CHANGELOG.

## Web UI (`goldenmatch[web]`)

- **Source/output split:** frontend source lives **outside** the python package at `packages/python/goldenmatch/web/frontend/`; build output lands **inside** the package at `goldenmatch/web/static/`. Don't collapse the two — wheel inclusion via `[tool.hatch.build.targets.wheel.force-include]` and dev-tooling separation both depend on this split.
- **`goldenmatch/web/static/.gitkeep` is intentional.** Real assets are gitignored (root `.gitignore` carve-out); the placeholder stays so source checkouts have the dir present and the wheel `force-include` glob has something to match.
- **Wheel build sequence:** `python scripts/build_web.py && hatch build`. The script invokes `pnpm install --frozen-lockfile && pnpm build` in `web/frontend/`, then mirrors `dist/` into `goldenmatch/web/static/`. Promoting to a hatch custom build hook is a deliberate v2 follow-up.
- **`[web]` extra is truly optional.** `cli/serve_ui.py` does its `from goldenmatch.web.app import create_app` import lazily (inside the command body) so users on plain `pip install goldenmatch` still get a working CLI. Test guards: `tests/web/test_optional_extra.py`.
- **`AppState.rules` is a typed `RulesPayload | None`** (not `dict | None`) — preview consumes the typed object without re-validating per request. `goldenmatch/web/rules.py::load_rules_from_yaml` is the single source of the 0.85 default threshold; don't re-default in the router.
- **Save path is atomic.** `routers/rules.py::save_rules` writes to `goldenmatch.yml.tmp`, then `os.replace`s. The `.yml.bak` is captured BEFORE the rewrite. Both spellings (`matchkey` singular, `matchkeys` plural) are popped before writing the canonical singular key — without that, files that previously held the plural key end up with both side-by-side.
- **Preview = in-memory bounded LRU.** `goldenmatch/web/registry.py::PreviewRegistry` (default `max_entries=8`) holds tempdirs containing the synthesized lineage/clusters/source. Every `POST /preview` mints a fresh `preview-<uuid8>` run_name (no idempotency on identical configs — UI iteration thrashes the cache, fine for v1). The same `/api/v1/runs/{name}` endpoints serve registry entries by virtue of the fallback in `routers/runs.py::_find_run`.
- **Preview rejects `embedding`/`record_embedding` scorers** at the router with a 400 — they need model bootstrap (HF download / Vertex creds) the local server doesn't wire up. UI dropdown selection of those would otherwise produce a 30s timeout.
- **Preview pre-validates matchkey columns** against `data.csv.columns` and raises `ValueError` (→ 400) on a typo. The engine itself accepts unknown columns silently and returns empty results, which reads as "no matches" rather than an error in the UI.
- **`SCORERS` / `TRANSFORMS` const arrays in `web/frontend/src/lib/types.ts` mirror `goldenmatch/config/schemas.py::VALID_SCORERS` / `VALID_SIMPLE_TRANSFORMS`.** Update both when adding scorers — the workbench dropdowns won't surface new ones otherwise.
- **Pair canonicalization for labels.** `web/labels.py::_canonical_pair` matches the project-wide `(min, max)` invariant. Without it, labeling pair (0,1) then relabeling pair (1,0) — which the inspector may surface either way — would split into two phantom dedup-table entries.
- **Web labels store is intentionally separate** from `goldenmatch label` CLI (writes CSV ground-truth) and from `MemoryStore` corrections (Learning Memory). `labels.jsonl` is steward-facing only; an explicit export step is the future hand-off path.
- **Frontend stack is bleeding-edge** as create-vite scaffolded: Vite ^8, React ^19.2, TypeScript ^6, ESLint ^10. Build's clean today but a major upgrade in any of these may need test/config touches. TanStack Router v1 / Query v5 / Table v8 are all stable.
- **Single-tenant by design** — no concurrency guard on `state.rules`, `state.registry`, the YAML file, or `labels.jsonl`. Localhost dev tool; revisit if/when this surface ever grows past one user.
- **`python -m goldenmatch.cli.main serve-ui` shadows worktree code.** Resolves the installed `goldenmatch` from site-packages, not the worktree source — testing web changes against the installed package will pass, then CI / the next user fails. Either `pip install -e packages/python/goldenmatch[web]` first, or run via a small wrapper that constructs `AppState` + `uvicorn.run(app, ...)` directly with `sys.path` prepended to the worktree.
- **No SPA fallback on the static mount.** `app.mount("/", StaticFiles(html=True))` only serves `index.html` for `/`. Direct nav to `/workbench`, `/match`, `/runs/<name>`, etc. returns FastAPI's 404 — only in-app `<Link>` clicks (TanStack Router client-side) work. Real fix: a catch-all route returning `index.html` before the StaticFiles mount. Workaround for screenshots / Playwright: navigate to `/` and click.
- **Demo project at `web/demo/`** is checked in: 28-contact `data.csv`, 7-row `reference.csv` (for `/match`), 2 saved runs, `labels.jsonl`, `goldenmatch.yml`. Use it for screenshots, walkthrough videos, and the `examples/python/07_web_ui_walkthrough.py` script. Runtime artifacts the demo accumulates (preview run files, `.goldenmatch/memory.db`) are gitignored — `git checkout -- web/demo/labels.jsonl` to revert label additions if you've been clicking around.
- **Deferred for v2** (per design doc `docs/superpowers/specs/2026-05-05-goldenmatch-web-ui-design.md`):
  1. Cluster force-graph view.
  2. Multi-run diff (A vs B).
  3. Full-dataset re-run from UI (currently sampled-only).
  4. Auth / multi-user.
  5. Persisting workbench previews to disk.
  6. Async preview job + streaming progress (current ceiling: 30s synchronous).
  7. Web-labels → MemoryStore handoff.
