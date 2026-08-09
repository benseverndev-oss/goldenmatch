# GoldenMatch

## Related Projects
- **SQL Extensions (in-monorepo):** `packages/rust/extensions/` -- Postgres (pgrx) extension + DuckDB UDFs, over a shared pyo3 `bridge`. See `packages/rust/extensions/CLAUDE.md`. (The old standalone `benseverndev-oss/goldenmatch-extensions` repo is ARCHIVED (last push 2026-05-01) -- it was folded into this monorepo; do NOT look for a separate `D:\show_case\goldenmatch-extensions` checkout.)
- **PyPI:** `goldenmatch` (Python toolkit), `goldenmatch-duckdb` (DuckDB UDFs)
- **npm:** `goldenmatch` (TypeScript port at `packages/goldenmatch-js/`)
- **GitHub:** `benseverndev-oss/goldenmatch` (extensions live here now, under `packages/rust/extensions/`; the standalone `goldenmatch-extensions` repo is archived)

## TypeScript Port
Lives at `packages/typescript/goldenmatch/`. See that package's CLAUDE.md for npm release flow, edge-safety rules, parity harness, and port-specific gotchas.

## Environment (goldenmatch-specific)
Root CLAUDE.md owns: branch/merge SOP, GitHub auth dance, Rust + pgrx, PostgreSQL 16 portable. Goldenmatch-only quirks:
- GCP project: `gen-lang-client-0692108803` (Vertex AI embeddings)
- Polars `scan_csv` uses `encoding="utf8"` not `"utf-8"`
- Polars `read_excel` needs explicit `engine="openpyxl"`
- **Release tags:** Python = `v1.x.y` (triggers `publish-goldenmatch.yml` → PyPI). TypeScript = `goldenmatch-js-v0.x.y` (triggers `publish-goldenmatch-js.yml` → npm). Never push an unprefixed version tag for TS.
- **Noise-aware scorer (#662, default ON):** zero-config auto-config upgrades `token_sort` → `jaro_winkler` on `address`/`string` col_types (corruption-prone free text; `token_sort` is word-order-robust but character-noise-fragile). Runs AFTER the qgram short-code guard in `build_matchkeys`, so code-like strings keep `qgram`. Benchmark (`scripts/bench_noise_aware_scorer.py`, NOT a CI gate): +0.48pp F1 on high-corruption NCVR, precision-driven, no Febrl3 regression; `jaro_winkler` == `ensemble` on the sweep (`jaro_winkler` chosen as the cheaper tie). Kill-switch: `GOLDENMATCH_NOISE_AWARE_SCORERS=0` (or `false`/`disabled`) restores legacy `token_sort`; `GOLDENMATCH_NOISE_AWARE_TARGET=ensemble` overrides the target. The clean-precision guard is the in-CI #528 `synthetic_benchmarks` gate. Does NOT reach the negative-evidence `_pick_scorer_for_column` path (separate penalty signal, still `token_sort`).

## Testing
- `CliRunner(mix_stderr=False)` errors on click ≥8.3 (currently installed) with `TypeError: unexpected keyword argument 'mix_stderr'`. Drop the kwarg — default already routes stderr separately; `result.stderr` is still accessible.
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
- CI workflow: `.github/workflows/ci.yml` -- test matrix (3.11/3.12/3.13), ruff lint (the FULL root-pyproject select incl. import-sort `I` -- CI runs plain `ruff check packages/python/goldenmatch`; the old "E9/F63/F7 only" note was stale and nearly shipped 79 I001s in the W0 polars sweep), smoke test. Ignores test_db, test_reconcile, test_mcp_and_watch
- Ray tests require `ray` optional dep -- use `pytest.mark.skipif(not HAS_RAY)` pattern
- Windows drive letter tests must use `@pytest.mark.skipif(sys.platform != "win32")` -- Path.stem behaves differently on Linux
- sdist includes benchmark datasets (can bloat to 500MB+) -- add large data dirs to `.gitignore` before building
- Memory tests use `tmp_path` fixture for isolated SQLite: `MemoryStore(backend="sqlite", path=str(tmp_path / "test.db"))`. 48 tests in test_memory_store.py, test_corrections.py, test_learner.py, test_memory_integration.py

## Architecture
- Pipeline: ingest → column_map → auto_fix → validate → standardize → matchkeys → block → score → cluster → golden → output
- SQL extensions: see `packages/rust/extensions/CLAUDE.md` for Postgres/DuckDB architecture
- `goldenmatch/core/agent.py` -- AgentSession, profile_for_agent, select_strategy, build_alternatives. Autonomous ER: profiles data -> detects domain -> selects strategy -> runs pipeline -> returns reasoning
- `goldenmatch/core/review_queue.py` -- ReviewQueue (memory/SQLite/Postgres backends), ReviewItem, gate_pairs(). Confidence gating: >0.95 auto-merge, 0.75-0.95 review, <0.75 reject
- `goldenmatch/core/memory/` -- Learning Memory: persistent corrections + rule learning. `store.py` (MemoryStore, SQLite/Postgres CRUD, trust-based upsert), `corrections.py` (apply_corrections with dual-hash staleness detection), `learner.py` (MemoryLearner, threshold tuning from 10+ corrections). Config: `MemoryConfig` in schemas.py, optional `memory:` YAML section
- `goldenmatch/a2a/` -- A2A protocol server (aiohttp). Agent card at `/.well-known/agent.json`, 10 skills, task lifecycle, SSE streaming. CLI: `goldenmatch agent-serve --port 8200`
- `goldenmatch/mcp/agent_tools.py` -- 16 agent-level MCP tools (additive to existing). Each creates own AgentSession (no shared global state)
- Adding MCP tools: add Tool to `AGENT_TOOLS` in `mcp/agent_tools.py`, add dispatch handler in `_dispatch()`, update server card tool count in `mcp/server.py` (line ~1002)
- Adding A2A skills: add entry to `_SKILLS` in `a2a/server.py`, add dispatch handler in `a2a/skills.py`, update `test_agent_card_has_N_skills` assertion in `tests/test_a2a.py`
- MCP/A2A handlers must validate `file_path` param, catch `FileNotFoundError` on `pl.read_csv`, and wrap `write_csv(output_path)` in try/except to preserve results on write failure
- `run_transform(strict=True)` re-raises exceptions instead of silently returning unmodified data — use from MCP/A2A handlers where callers explicitly requested transforms
- `_scan_only()` in `quality.py` returns serialized findings dicts (not empty list) so MCP tools can inspect them without reaching into goldencheck internals
- `_api.py` has DataFrame entry points: `dedupe_df()`, `match_df()`, `score_strings()`, `score_pair_df()`, `explain_pair_df()` -- used by SQL extensions
- `pipeline.py` refactored: `_run_dedupe_pipeline()` and `_run_match_pipeline()` extracted as shared internal functions, called by both file-based and DataFrame-based entry points
- `goldenmatch/core/` — pipeline modules (no Textual dependency)
- `goldenmatch/tui/` — Textual TUI + MatchEngine (engine.py has no Textual dependency)
- `goldenmatch/cli/` — Typer CLI commands (including `unmerge`, `evaluate`, `incremental`, `pprl`, `label`, `compare-clusters`, `sensitivity`, `review`)
- **Wave 3.1 (2026-06-05) surfaced 3 library features as CLI front doors** (each ran via the pipeline before, but had no standalone command): `goldenmatch explain` (NL pair/cluster explanation via `core/explain.py`; `--pair a,b` or `--cluster id`), `goldenmatch lineage` (per-pair audit trail via `core/lineage.py`; was MCP-tool-only), `goldenmatch anomalies` (standalone `core/anomaly.py` detection; was `dedupe --anomalies` only). `explain`/`lineage` run the pipeline through `tui/engine.py::MatchEngine` (clean `EngineResult.scored_pairs`/`.clusters` + `engine.data`) — NOT `run_dedupe`, whose result dict does NOT reliably carry `_df`/`scored_pairs` (label.py reconstructs the df when `_df` is None). Reuse MatchEngine for any new command that needs scored pairs + the row-id'd frame.
- `goldenmatch/db/` — Postgres integration (connector, sync, reconcile, clusters, ANN index)
- `goldenmatch/api/` — REST API server (`goldenmatch serve`)
- `goldenmatch/mcp/` — MCP server for Claude Desktop (`goldenmatch mcp-serve`)
- `goldenmatch/plugins/` — Plugin system (registry, base protocols for scorer/transform/connector/golden_strategy)
- `goldenmatch/connectors/` — Data source connectors (Snowflake, Databricks, BigQuery, HubSpot, Salesforce)
- `goldenmatch/backends/` — Storage backends (DuckDB for out-of-core processing)
- `goldenmatch/domains/` — Built-in YAML domain packs (electronics, software, healthcare, financial, real_estate, people, retail)
- dbt integration lives in the top-level `packages/dbt/goldensuite/` package (moved out of this dir)
- Core modules: explainer, explain, evaluate, report, dashboard, graph, anomaly, diff, rollback, schema_match, chunked, cloud_ingest, api_connector, scheduler, gpu, vertex_embedder, llm_scorer, llm_budget, lineage, match_one, probabilistic, learned_blocking, streaming, graph_er
- Config: Pydantic models in `config/schemas.py`, YAML loading in `config/loader.py`
- `config/schemas.py` has `MemoryConfig` (enabled, backend, path, trust, learning) and `LearningConfig` (threshold_min_corrections, weights_min_corrections). `GoldenMatchConfig.memory` is optional
- `config/loader.py` normalizes golden_rules and standardization sections from flat YAML
- `GoldenRulesConfig` fields: `auto_split: bool = True` (auto-split oversized clusters via MST), `quality_weighting: bool = True` (use GoldenCheck quality scores in survivorship), `weak_cluster_threshold: float = 0.3` (edge gap threshold for confidence downgrade)
- **`quality_weighting` is WIRED (2026-06-07):** `pipeline._run_dedupe_pipeline` calls `core.quality.compute_quality_scores(collected_df)` (→ `goldencheck.cell_quality`, remapped positional→`__row_id__`) and threads the result into `build_golden_records_from_frames` / `build_golden_records_batch` and the `_polars_native_eligible` gate. Fail-open + SPARSE: `None` when goldencheck is absent OR the data is clean, so a non-None dict (which forces the slow per-row survivorship path off the fast columnar path) only appears when there are real per-cell issues — clean data keeps the fast path with zero change. Effect: a cluster's golden record prefers the canonical spelling over a typo, a real date over a future one. Was a documented no-op before this.

## Semantic Layer (GoldenModel: discovery + certification)
- `goldenmatch/semantic/` -- the semantic-layer wedge. Certify a *declared* model's keys/joins against real data, and (the generative half) DISCOVER a draft model where every key/join comes PRE-GRADED by the certifier. NOT imported from `goldenmatch/__init__.py` (keeps `import goldenmatch` polars-free) -- import explicitly: `from goldenmatch.semantic import ...`.
- **Certification (the falsification test, single source of "is this valid"):** `certify_key_integrity` (one declared key), `certify_semantic_model` + `certification_report_dict` (whole model, any dialect), `certify_cube_joins` / `certify_osi_relationships` / `certify_serving_joins` (join cardinality). Discovery REUSES this; it never forks a second validator.
- **Dialects (parse + emit, bidirectional):** MetricFlow/dbt (`metricflow.py`, `parse_semantic_models` -> `DeclaredKeySpec`), Cube (`cube.py`, `parse_cube_models` -> `Cube`), OSI (`osi.py`). goldenmatch metadata rides in `meta.goldenmatch` (MetricFlow/Cube) / `custom_extensions.goldenmatch` (OSI).
- **Discovery front door:** `discover_semantic_model(tables, *, dialect="metricflow", resolve=False, name=False, apply_names=False, namer_backend=None) -> ProposedModel` (`semantic/discovery/model.py`). Chain: keys -> entity types -> certified joins -> grain-gated measures/dims -> emit + re-certify. Every field flows through `ProposedModel.to_dict()` (the shape MCP `discover_semantic_model` / REST `POST /semantic/discover` emit). Design + phased plan (items 1-19): `docs/superpowers/specs/2026-08-03-semantic-model-discovery-design.md`.
- **Frontier slices 11-19** (deterministic + default-on unless noted), each `discovery/<x>.py`: hierarchies (near-FD), metrics (avg + capped ratios, grain-gated), time_intelligence (grain inference + MTD/YoY/rolling), cardinality (1:1 + m:n bridges), scd (Type-2), completeness (`score_model` -> grain-weighted 0..1 `ModelCompleteness` + gaps), warehouse (`read_information_schema` -> `WarehouseManifest`, `plan_certification`, `discover_from_manifest`; declared PK/FK left `certified=False`), reconcile (`reconcile_model` vs parsed dbt/Cube catalog; `grain_drift` `proven=True` when certified grain contradicts the declared key), namer_eval (`score_naming` / `run_namer_eval`; live LLM opt-in behind `GOLDENMATCH_NAMER_EVAL_LIVE`).
- **Advisory namer (`discovery/namer.py`, opt-in, NEVER authoritative):** `name_semantic_model` / `apply_names`. Structural discovery is byte-deterministic without it; `apply_names` writes only VERIFIED names into the YAML post-certification, cosmetically. Kill-switch `GOLDENMATCH_SEMANTIC_NAMER=0`.
- **Adding a discovery slice:** new `discovery/<x>.py` -> wire into `model.py` (ProposedTable/ProposedModel fields + orchestrator + `to_dict`) -> export from BOTH `discovery/__init__.py` AND `semantic/__init__.py` (import + `__all__`) -> `tests/test_semantic_discovery_<x>.py` -> CHANGELOG + doc-regen chain. Batching >1 such PR causes an additive CHANGELOG/`__init__` rebase in the merge queue -- branch each slice off a base that already has the prior one.

## Accuracy Strategy
- Structured data (names, addresses, bibliographic): fuzzy matching alone → 97.2% F1. No embeddings or LLM needed.
- Library comparison (v1.2.7): Febrl 0.971 F1 (top-2, behind Splink 0.998), DBLP-ACM 0.918 F1 (top-2, behind RecordLinkage 0.923). Most consistent performer across data types — zero training data, explicit config required.
- Product matching (electronics/Abt-Buy): domain extraction + emb+ANN + LLM → **72.2% F1** (P=94.8%, $0.04). Domain extraction gets 393/1081 model matches for free.
- Product matching (software/Amazon-Google): emb+ANN + LLM → **45.3% F1** (P=63.3%, $0.02). Clean emb+ANN pipeline is best — adding domain extraction/token normalization/mfr blocking adds noise and hurts F1. SOTA is ~78% (GPT-4 few-shot, Ditto fine-tuned).
- Product matching lesson: adding candidate sources (domain extraction, token normalization, manufacturer blocking) helps electronics (Abt-Buy) but HURTS software (Amazon-Google). More pairs = more noise. For domains without precise identifiers, keep the candidate set clean and let the LLM filter.
- LLM scorer sends borderline pairs (0.75-0.95) to GPT, auto-accepts >0.95. Budget cap of $0.05 covers typical datasets.
- Fellegi-Sunter probabilistic: **97.8% precision, 95.8% recall, 96.8% F1 on DBLP-ACM** (full-block vectorized scoring, 2026-06-07). Opt-in; Splink-style EM (fix u from random pairs, train only m). **The old "98.8% P / 57.6% R / 72.8% F1" figure was a benchmark artifact**: `run_v030_quick.py` skipped blocks >500 rows for performance (`if block_df.height > 500: continue`), and since every DBLP-ACM match is same-year, that capped recall at ~60%. The vectorized NxN scorer (`score_probabilistic_vectorized`, default via `probabilistic_block_scorer`) scores all 1.2M within-block pairs in ~0.9s, so large blocks are no longer skipped — recall jumps to ~96%. **Block-skip-for-perf is the dominant FS recall lever, not scoring/calibration.**
- Learned blocking: auto-discovers predicates, 96.9% F1 matching hand-tuned static blocking
- Boost tab reranking can hurt on product data — quality check warns user to try `--llm-boost` instead
- Multi-field embedding helps structured data (DBLP-ACM) but not product data — descriptions differ in format across sources
- Benchmark evaluation: always use threshold-based pair generation, NOT top-1-per-record (argmax)
- Leipzig benchmarks: `python tests/benchmarks/run_leipzig.py`
- v0.3.0 benchmarks: `python tests/benchmarks/run_v030_quick.py` (F-S, learned blocking, LLM budget)
- Domain extraction benchmark: `python tests/benchmarks/run_domain_bench.py` (Abt-Buy) and `run_amazon_google_bench.py`
- LLM+embedding benchmark: `python tests/benchmarks/run_llm_budget_bench.py` (requires OPENAI_API_KEY)

## Remote MCP Server

Hosted on Railway, registered on Smithery:
- **Endpoint:** `https://goldenmatch-mcp-production.up.railway.app/mcp/`
- **Smithery:** `https://smithery.ai/servers/benzsevern/goldenmatch`
- **Server card:** `https://goldenmatch-mcp-production.up.railway.app/.well-known/mcp/server-card.json`
- **Transport:** Streamable HTTP (via `StreamableHTTPSessionManager`)
- **Dockerfile:** `Dockerfile.mcp` (Python 3.12-slim, installs `.[mcp]`)
- **Railway project:** `golden-suite-mcp` (service: `goldenmatch-mcp`, port 8200)
- **Local HTTP:** `goldenmatch mcp-serve --transport http --port 8200`
- **AUTH (Wave 0, 2026-06-05): the HTTP server is fail-closed.** `run_server_http` refuses to start on a non-loopback host (default bind is `0.0.0.0`) unless `GOLDENMATCH_MCP_TOKEN` is set; when set, every `/mcp` request needs `Authorization: Bearer <token>` (the `/.well-known/` card stays public). **The Railway service MUST have `GOLDENMATCH_MCP_TOKEN` set or the deploy crash-loops** (`startCommand` has no `--host`, so it binds `0.0.0.0`). Set it on the service: `railway variables --set GOLDENMATCH_MCP_TOKEN=<token>` (or via the dashboard). Local loopback (`--host 127.0.0.1`) still runs token-free. Same posture on the A2A server via `GOLDENMATCH_AGENT_TOKEN` (`a2a/server.py::create_app`).
- **Session-backed stateful tools (2026-07-12).** The stateful goldenmatch tools (`list_clusters`, `get_cluster`, `get_golden_record`, `explain_match`, `evaluate`, `export_results`, `match_record`, `find_duplicates`) read run state that the *standalone* server sets once at startup (`_initialize` from `--file`). Over the goldensuite-mcp **aggregator** (which never calls `_initialize`) they used to raise `AttributeError`. Now they resolve state via `_resolve_run_state()` (`mcp/server.py`): module globals first (standalone path, byte-identical), else the current MCP session's last `AgentSession` (persisted per session id after `agent_deduplicate`/`match_sources`), else a clean "no run loaded" error. Per-session isolation via a `ContextVar` session id set in `call_tool` from `server.request_context.session` (`mcp/_session_ctx.py`); bounded store in `mcp/_session_store.py` (`GOLDENMATCH_MCP_SESSION_MAX`/`_TTL`). Session `AgentSession.data` is raw `read_csv` (no `__row_id__`); the resolver augments it once (cached) because `match_one` needs `__row_id__`.

## API + Common Mistakes

Lives in the Mintlify docs at `docs-site/goldenmatch/api-quick-reference.mdx` (published at `docs.bensevern.dev/docs/goldenmatch/api-quick-reference`) -- reference content, not session context. DQBench ER scores live in the package README + CHANGELOG.


## Reference detail lives in `docs/context/`

Each line is the part that applies to ANY change in this package. The linked file
carries the mechanism, the measurements and the incident history — read it when
you touch that area, not by default.

- **Zero-config is the product, and the controller can refuse.** `auto_configure_df`
  returns a confidence-rated config; a RED verdict raises rather than guessing, and
  `allow_red_config=True` is the only escape hatch (NOT `confidence_required=False`,
  which #715 removed). Never bypass the gate to make a test pass.
  → [docs/context/auto-config.md](docs/context/auto-config.md)
- **The kernel is the reference; Python is the fallback.** New scoring/blocking work
  goes in the shared Rust core with a byte-identical pure-Python reference beside it,
  and both are locked by a committed fixture. `GOLDENMATCH_NATIVE=0` must stay a
  working path, not a broken one.
  → [docs/context/code-patterns.md](docs/context/code-patterns.md)
- **Measure the whole path before optimising.** Every previous "obvious" win here
  came in under its framing; the one that paid was an allocator env var, not a
  rewrite. Compare 5-run median wall on real shapes, not cProfile cumtime.
  → [docs/context/performance.md](docs/context/performance.md)
- **Identity is a control-plane concern, not a columnar one.** Stable `entity_id`,
  evidence edges and the append-only event log are transactional state; do not push
  them into a batch kernel.
  → [docs/context/identity-graph.md](docs/context/identity-graph.md)
- **Package-specific traps** that have each cost real time.
  → [docs/context/gotchas.md](docs/context/gotchas.md)
- **The optional review UI** (`goldenmatch[web]`).
  → [docs/context/web-ui.md](docs/context/web-ui.md)

This file is loaded into every session that touches goldenmatch, so bytes here are
a tax on all of that work. Before adding a section, apply the Tier-1 test: *would
an agent do the wrong thing without this, on any change in this package?* If it is
"only when touching X", it belongs in `docs/context/` with at most a line here.
