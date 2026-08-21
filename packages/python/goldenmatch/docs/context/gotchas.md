# Gotchas

Specific traps in this package that have each cost real time, with the incident that produced them.

> Reference detail extracted from `packages/python/goldenmatch/CLAUDE.md` so it
> is read when relevant rather than loaded into every session. The Tier-1 rule
> that always applies stays in that file, which links here.

## Gotchas
- Design specs + implementation plans live at the **repo-root** `docs/superpowers/{specs,plans}/`, which IS git-tracked — commit them with a plain `git add` (no `-f`; 80+ are committed, `git check-ignore` is negative). Only the per-package `packages/python/<pkg>/docs/superpowers/` dirs are gitignored local scratch (each package's `.gitignore`).
- `tests/benchmarks/datasets/` is gitignored — tests reading those files must `pytest.skip` when absent OR be marked `@pytest.mark.benchmark` and excluded from default CI via `--ignore`
- `publish-npm.yml` runs the full vitest suite pre-publish — any flake blocks the release; can't retry, must fix + bump patch version + new tag
- `gh run watch <run-id> --exit-status` blocks until a workflow completes — useful for confirming publish success before moving on
- GitHub Discussions API: REST returns 404. Use GraphQL `createDiscussion` mutation with `repositoryId` (R_kgDORoztPA for this repo) and a `categoryId` fetched via `discussionCategories`.
- `gh repo edit --add-topic` fails at 20 topics (API-side cap). Drop low-value topics with `--remove-topic` before adding.
- Wiki repo: `git clone https://github.com/benseverndev-oss/goldenmatch.wiki.git`, branch is `master` (not `main`).
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
- PyPI version must be bumped in THREE spots: `pyproject.toml`, `goldenmatch/__init__.py`, and `CHANGELOG.md` (new dated entry). Release flow: bump -> PR -> merge -> tag `v1.x.y` -> `publish-goldenmatch.yml` -> PyPI (~25s; JSON API lags ~20s).
- `test_memory_e2e.py::test_e2e_edit_on_matchkey_field_marks_stale_and_enqueues` was flaky in the merge queue (full matrix under `-n auto`). ROOT CAUSE (fixed, not a rerun): `ReviewQueue`'s `_SQLiteBackend._connect` opened a bare `sqlite3.connect` (journal_mode=delete, no busy_timeout) — the same #130 class `MemoryStore` already fixed with WAL. Under saturated CI disk the stale-pair enqueue hit "database is locked", which `pipeline._enqueue_stale_pairs` SWALLOWS (best-effort) → empty review queue → the `(0,1) in pair_ids` assertion failed intermittently. Fix: `_connect` now sets `timeout=30.0` + `PRAGMA journal_mode=WAL` + `PRAGMA busy_timeout=30000` (product hardening for every review-queue user, not a test patch). Do NOT "rerun rather than chase" a flaky test — see the root CLAUDE.md STANDING RULE.
- `test_api.py::TestDedupeDf::test_dedupe_df_empty` observed flaky under `pytest -n auto` (`assert 3 == 0`, passed on rerun, with a `'Finding' has no attribute 'rule_id'` quality-scan warning) -- rerun rather than chase; a version-only PR can't cause it.
- v1.0.0 is live on PyPI -- Production/Stable, semver enforced — `pip install goldenmatch` works
- Adding a TUI tab: update `test_tabs_exist` in `tests/test_tui.py` — asserts exact tab count (currently 9)
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
- pgrx 0.12.9 does NOT auto-generate SQL files -- must provide handwritten `sql/goldenmatch_pg--0.5.0.sql` manually
- pgrx in workspace mode is broken -- postgres crate must be excluded from workspace (`exclude = ["postgres"]` in root Cargo.toml)
- pgrx extension functions live in `goldenmatch` schema (per .control file) -- must use `goldenmatch.function_name()` or explicit `::TEXT` casts in psql
- DuckDB UDFs cannot query the same connection they're called on (deadlock) -- use `con.cursor()` for table reads inside UDFs
- DuckDB `.pl()` (Polars conversion) requires `pyarrow` as a dependency
- Rust `cargo` defaults `CARGO_HOME` to the drive root on Windows when CWD is D: -- always set `CARGO_HOME="C:/Users/bsevern/.cargo"` explicitly
- `winget install Rustlang.Rustup` fails silently on Windows without Developer Mode -- use `rustup-init.exe -y` with `RUSTUP_WINDOWS_PATH_TYPE=hardlink`
- SQL-extension CI is IN this monorepo: `ci.yml`'s `rust_pgrx` lane (ci-required, PG 15/16/17, `cargo pgrx install` + psql smoke) builds/tests the Postgres extension; `publish-goldenmatch-pg.yml` + `publish-goldenmatch-duckdb.yml` cut the releases (tags `goldenmatch-pg-v*` / `goldenmatch-duckdb-v*`). No separate extensions-repo CI.
- Trunk (pgt.dev) shut down July 2025 -- do not reference it for Postgres extension distribution
- dbdev (database.dev) only supports SQL/PL/pgSQL extensions (TLE) -- compiled C extensions not eligible
- `typing_extensions` on Ubuntu CI: system package at `/usr/lib/python3/dist-packages/` overrides pip install -- must `sudo rm -f` the system file first
- pyo3 embeds Python linked at compile time -- CI must install goldenmatch into the same Python that Postgres uses
- DuckDB UDF `con.sql()` without `.fetchone()` may not execute the UDF -- always fetch results
- `json.dumps(clusters)` fails when cluster dict has tuple keys (pair_scores) -- use str() fallback
- Coverage config in pyproject.toml: omit db/*, mcp/*, vertex_embedder, connectors/* (require external services)
- Docs site is **Mintlify** at `docs-site/` (custom domain `docs.bensevern.dev`); the legacy Jekyll site under `packages/python/goldenmatch/docs/` was torn down (only `images/`/`screenshots/`/`design/`/`superpowers/`/`wiki/`/`llms*.txt` remain there as assets/engineering docs). Edit docs via the Mintlify MDX under `docs-site/`; run `mint validate` + `mint broken-links` from `docs-site/`.
- GitHub Release triggers publish.yml workflow which auto-publishes to PyPI via trusted publishing
- Scored pairs are canonicalized as `(min(id_a, id_b), max(id_a, id_b))` throughout cluster.py, graph.py, chunked.py, ann_blocker.py -- any new code storing/looking up pairs must canonicalize too
- v1.6.0 Learning Memory: end-to-end loop wired. Pipeline applies corrections + learned thresholds; 7 collection points (ReviewQueue, BoostTab, unmerge_record/cluster, LLM scorer, agent_approve_reject, REST `/reviews/decide`, Python API); 5 MCP tools (`list_corrections`, `add_correction`, `learn_thresholds`, `memory_stats`, `memory_export`); CLI subgroup `goldenmatch memory ...`. Spec: `docs/superpowers/specs/2026-05-04-learning-memory-completion.md` (foundation: pre-fold 2026-03-26 spec).
- `record_hash` excludes `__row_id__` so corrections survive row reordering across runs (the durability invariant; including it would defeat re-anchoring).
- `Correction.source` and `Correction.decision` are `StrEnum`s in `core/memory/store.py`. Trust mapping lives in `HIGH_TRUST_SOURCES` + `trust_for_source(source)` — use these instead of inline `if source in {...}: trust = 1.0`.
- `MemoryConfig.dataset` field validator strips whitespace and rejects empty strings; pass `None` to omit.
- `apply_corrections` reanchor builds `record_hash → list[row_id]` via `pl.concat_str` + `map_elements` (vectorized O(N)). Ambiguous re-anchors counted as `stale_ambiguous`, never silently misapplied.
- PyPI publish: `publish-goldenmatch.yml` lives at the **monorepo root**, not under this package. Trusted publishing NOT configured — uses `PYPI_TOKEN` secret. To enable trusted publishing later, claim PyPI publisher: owner `benzsevern`, repo `goldenmatch`, workflow `publish-goldenmatch.yml`, environment `pypi`.

