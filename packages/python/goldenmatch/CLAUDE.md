# GoldenMatch

## Related Projects
- **SQL Extensions repo:** `D:\show_case\goldenmatch-extensions` -- Postgres extension + DuckDB UDFs. Has its own CLAUDE.md.
- **PyPI:** `goldenmatch` (Python toolkit), `goldenmatch-duckdb` (DuckDB UDFs)
- **npm:** `goldenmatch` (TypeScript port at `packages/goldenmatch-js/`)
- **GitHub:** `benzsevern/goldenmatch`, `benzsevern/goldenmatch-extensions`

## TypeScript Port (`packages/goldenmatch-js/`)
- **npm:** `goldenmatch` (same name, different registry from PyPI)
- **Commands** (from `packages/goldenmatch-js/`): `npx tsc --noEmit`, `npx vitest run`, `npm run build` (tsup)
- **Edge-safe rule:** files in `src/core/` MUST NOT import `node:*`. Node-only code lives in `src/node/`.
- **Parity harness:** `tests/parity/scorer-ground-truth.test.ts` locks scorer output at 4-decimal tolerance against Python
- **Strict TS:** `noUncheckedIndexedAccess` + `exactOptionalPropertyTypes` — use `!` on bounded-loop indices, conditional spread for optional props
- **Optional peer deps:** load via `await import("pkg-name" as string)` — the `as string` cast prevents tsup from resolving at build time
- **tsup has 5 entrypoints:** `index`, `core/index`, `node/index`, `cli`, `node/backends/score-worker` (piscina worker)
- **PORTING_GUIDE.md** in repo root is the playbook for porting
- **No `make*` factory functions** exist for config types — test fixtures use full literals. Required fields: `MatchkeyField` needs `field`+`transforms`+`scorer`+`weight`; `BlockingKeyConfig` needs `fields`+`transforms`; `BlockingConfig` needs `strategy`+`keys`+`maxBlockSize`+`skipOversized`
- **Scorer names are snake_case** (same as Python): `token_sort`, `record_embedding`, `soundex_match`, `ensemble`, `exact`, `jaro_winkler`, `levenshtein`
- **`DOMAIN_EXTRACTED_COLS`** (in `src/core/domain.ts`) has only 3 entries (`__brand__`, `__model__`, `__version__`) — Python's equivalent has 12; don't assume parity when porting domain features
- **`import type` cycle rule:** `types.ts` imports types from `autoconfigVerify.ts`, so `autoconfigVerify.ts` must use `import type { ... }` (never runtime `import`) for any types.ts symbols
- **`exactOptionalPropertyTypes`:** don't spread `undefined` into typed optional fields — use `...(x !== undefined ? { field: x } : {})`
- **Vitest default timeout is 5s** — heavier integration tests (PPRL multi-level, postflight end-to-end) need `{ timeout: 15000 }`; CI concurrent load has bitten this (cost a release: goldenmatch-js v0.3.0 → v0.3.1)

## Branch & Merge SOP (all Golden Suite repos)
- Feature work goes on `feature/<name>` branches, never directly to main
- Merge via **squash merge PR** (watchers see PR activity, history stays clean)
- PR title format: `feat: <description>` or `fix: <description>`
- PR body: summary bullets + test plan
- Merge when: tests pass, docs updated. Days not weeks.
- After merge: delete remote branch
- Commands: `gh pr create --title "..." --body "..."` then squash merge via GitHub UI or `gh pr merge --squash`
- **Release tags:** Python = `v1.4.x` (triggers `publish.yml` → PyPI). TypeScript = `goldenmatch-js-v0.1.x` (triggers `publish-npm.yml` → npm). Never push an unprefixed version tag for TS.

## Environment
- Windows 11, bash shell (Git Bash) -- use Unix paths in scripts
- GCP project: `gen-lang-client-0692108803` (Vertex AI embeddings)
- Python 3.12 at `C:\Users\bsevern\AppData\Local\Programs\Python\Python312\python.exe`
- Project lives on D: drive: `D:\show_case\goldenmatch`
- Two GitHub accounts: `benzsevern` (personal, for this repo) and `benzsevern-mjh` (work)
- MUST `gh auth switch --user benzsevern` before push, switch back to `benzsevern-mjh` after
- `gh auth switch` sometimes doesn't apply to `gh api graphql` / `gh pr create` / git push — force the right token with `GH_TOKEN=$(gh auth token --user benzsevern) gh ...`. For wiki repo push, rewrite the remote URL: `git remote set-url origin "https://benzsevern:$(gh auth token --user benzsevern)@github.com/benzsevern/goldenmatch.wiki.git"`
- Polars `scan_csv` uses `encoding="utf8"` not `"utf-8"`
- Polars `read_excel` needs explicit `engine="openpyxl"`
- Rust 1.94.0 installed at `C:\Users\bsevern\.cargo\bin` -- must set `RUSTUP_HOME="C:/Users/bsevern/.rustup"` and `CARGO_HOME="C:/Users/bsevern/.cargo"` in every bash command, plus add to PATH
- No admin privileges -- cannot install system packages (LLVM, WSL2, Developer Mode). Workarounds: `RUSTUP_WINDOWS_PATH_TYPE=hardlink` for Rust, user-dir installs for everything else
- pgrx (Postgres extension framework) cannot build locally -- needs libclang/LLVM. Use CI (Linux) for pgrx builds/tests
- PostgreSQL 16 portable at `C:\Users\bsevern\tools\pg16portable\pgsql`

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

## TS port v0.4.0 — Learning Memory parity
- npm `goldenmatch` v0.4.0 ships full cross-language parity with Python `goldenmatch` v1.6.0. A correction written by either runtime applies identically in the other.
- **Hash bytes are the contract.** SHA-256 truncated to 16 hex chars via Web Crypto (`crypto.subtle.digest("SHA-256", new TextEncoder().encode(s))`). UTF-8 encoding mandatory on both sides. Hash input = values only joined by `|` (NOT `<col>=<val>`). `__row_id__` excluded from `record_hash` so it survives row reordering.
- **Edge-safety boundary.** `src/core/memory/` is edge-safe (no `node:*`); `src/node/memory/` has `SqliteMemoryStore` + the Python-API mirror (`getMemory`, `addCorrection`, `learn`, `memoryStats`) — they construct a Node-only SQLite store.
- **`ScoredPair` shape divergence.** Memory module's `applyCorrections` takes a tuple `[a, b, score]` (matches Python). Pipeline passes the object shape. Translation isolated to `_applyMemoryPost` in `pipeline.ts` — touching it means handling both shapes.
- **Pipeline is async post-v0.4.0.** `runDedupePipeline`, `runMatchPipeline`, `dedupe`, `match`, `dedupeFile`, `matchFile`, `runSensitivity`, `unmergeRecord`, `unmergeCluster` all return Promises. Every external caller awaits.
- **MCP tool count is 24** (19 existing + 5 memory tools). Description literal at `src/node/mcp/server.ts:6` reads `Exposes 24 tools`. Update + assert via the existing regex test when adding more.
- **`better-sqlite3` is an OPTIONAL peer dep.** `await import("better-sqlite3" as string)` with the `as string` cast (prevents tsup resolving at build time). Throws clear "install better-sqlite3" if missing.
- **Cross-language parity fixtures** committed at `tests/parity/fixtures/{memory_corrections.json, memory.db, memory_apply_inputs.json}` on both Python and TS sides. Regen via `packages/python/goldenmatch/tests/parity/memory/gen_memory_fixtures.py --rebuild-db`. Determinism clamp: pinned UUIDs, pinned `created_at` (no `datetime.now()`).
- **npm publish workflow:** `.github/workflows/publish-goldenmatch-js.yml` at MONOREPO ROOT. Trusted publishing NOT configured — uses `NPM_TOKEN` secret. Trigger pattern: `goldenmatch-js-v*` tag push OR `workflow_dispatch` with optional `ref` input. Tag MUST point at a commit that has the workflow file, otherwise the trigger doesn't fire.

## API Quick Reference

### dedupe_df() — DataFrame deduplication
```python
import goldenmatch

result = goldenmatch.dedupe_df(
    df,
    config=None,              # GoldenMatchConfig or None
    exact=["email"],          # exact match columns
    fuzzy={"name": 0.85},     # fuzzy match with thresholds
    blocking=["zip"],         # blocking keys
    threshold=0.85,           # overall fuzzy threshold
    llm_scorer=False,         # enable LLM for borderline pairs
)
```

### DedupeResult fields
```python
result.golden          # pl.DataFrame | None — canonical records with __cluster_id__
result.dupes           # pl.DataFrame | None — duplicate records with __row_id__
result.unique          # pl.DataFrame | None — non-duplicate records
result.clusters        # dict[int, dict] — {cluster_id: {"members": [row_ids], "pair_scores": {(a,b): score}}}
result.scored_pairs    # list[tuple[int, int, float]] — all matched pairs
result.stats           # dict — total_records, total_clusters, matched_records, match_rate
result.total_records   # int
result.total_clusters  # int
result.match_rate      # float
```

### StandardizationConfig — use rules dict, NOT keyword args
```python
# WRONG:
StandardizationConfig(email=["email"], phone=["phone"])

# RIGHT:
StandardizationConfig(rules={
    "email": ["email"],
    "phone": ["phone"],
    "first_name": ["strip", "name_proper"],
})
```
Verified: `StandardizationConfig` has a single `rules: dict[str, list[str]]` field with a model validator. Keyword args will raise a Pydantic validation error.

### BlockingConfig requires `keys` field
```python
# keys is required even with multi_pass
BlockingConfig(
    strategy="multi_pass",
    keys=[BlockingKeyConfig(fields=["email"], transforms=["lowercase"])],  # required!
    passes=[
        BlockingKeyConfig(fields=["email"], transforms=["lowercase"]),
        BlockingKeyConfig(fields=["last_name"], transforms=["soundex"]),
    ],
)
```

### MatchkeyConfig requires `name` field
```python
MatchkeyConfig(
    name="identity",       # required!
    type="weighted",
    threshold=0.75,
    fields=[...],
)
```

### Extracting pairs from clusters (correct way)
```python
pairs = []
for cluster in result.clusters.values():
    members = sorted(cluster["members"])
    for i in range(len(members)):
        for j in range(i + 1, len(members)):
            pairs.append((members[i], members[j]))
```

### Multi-pass blocking for catching different dupe types
```python
# Pass 1: exact email (identical-email dupes)
# Pass 2: soundex last_name (phonetic variants: Smith/Smyth)
# Pass 3: first 3 chars of last_name (typo dupes: Johm/John)
BlockingConfig(
    strategy="multi_pass",
    keys=[BlockingKeyConfig(fields=["email"], transforms=["lowercase", "strip"])],
    passes=[
        BlockingKeyConfig(fields=["email"], transforms=["lowercase", "strip"]),
        BlockingKeyConfig(fields=["last_name"], transforms=["soundex"]),
        BlockingKeyConfig(fields=["last_name"], transforms=["substring:0:3"]),
    ],
)
```

### Available scorers
- `exact`: 1.0 if equal, 0.0 otherwise
- `jaro_winkler`: best for short strings (names)
- `levenshtein`: normalized edit distance
- `token_sort`: handles word reordering
- `ensemble`: weighted combination of jaro_winkler + levenshtein + token_sort + dice (best for names)
- `dice`, `jaccard`: set-based similarity
- `soundex_match`: phonetic matching
- `embedding`: sentence-transformer cosine similarity

### Available transforms (applied at matchkey time)
`lowercase`, `uppercase`, `strip`, `strip_all`, `soundex`, `metaphone`, `digits_only`, `alpha_only`, `normalize_whitespace`, `token_sort`, `first_token`, `last_token`, `substring:start:end`, `qgram:n`

### LLM Scorer for borderline pairs
```python
from goldenmatch.config.schemas import LLMScorerConfig, BudgetConfig

config.llm_scorer = LLMScorerConfig(
    enabled=True,
    candidate_lo=0.60,    # send pairs scoring 0.60-0.90 to LLM
    candidate_hi=0.90,
    auto_threshold=0.90,  # auto-accept above 0.90
    budget=BudgetConfig(max_calls=500, max_cost_usd=1.0),
)
# Requires OPENAI_API_KEY or ANTHROPIC_API_KEY in environment
```

## DQBench Integration

GoldenMatch is benchmarked by DQBench ER category:
- **DQBench ER Score: 95.30** (with LLM) / **77.21** (without LLM)
- Key to high score: multi-pass blocking + ensemble scoring + standardization + LLM scorer
- Adapter: `dqbench/adapters/goldenmatch_adapter.py`
- Run: `pip install dqbench && dqbench run goldenmatch`

## Common Mistakes
- Using `exact=["email"]` as sole matchkey — creates oversized clusters with common emails
- Using `auto_configure()` on synthetic data — it may produce poor configs
- Not setting `name=` on MatchkeyConfig — it's required
- Not providing `keys=` on BlockingConfig — it's required even with multi_pass
- Extracting pairs from dupes DataFrame directly instead of using result.clusters

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
