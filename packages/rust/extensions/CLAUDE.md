# GoldenMatch Extensions

Native SQL extensions for [GoldenMatch](https://github.com/benseverndev-oss/goldenmatch) (`D:\show_case\goldenmatch`).

## Related Projects
- **Main repo:** `D:\show_case\goldenmatch` -- Python entity resolution toolkit (v1.1.0+). Has its own CLAUDE.md with full architecture docs.
- **This repo:** Rust bridge + Postgres extension + DuckDB Python UDFs
- **PyPI packages:** `goldenmatch` (Python), `goldenmatch-duckdb` (DuckDB UDFs)

## Branch & Merge SOP (all Golden Suite repos)
- Feature work goes on `feature/<name>` branches, never directly to main
- Merge via **squash merge PR** (watchers see PR activity, history stays clean)
- PR title format: `feat: <description>` or `fix: <description>`
- PR body: summary bullets + test plan
- Merge when: tests pass, docs updated. Days not weeks.
- After merge: delete remote branch

## Environment
- Windows 11, bash shell (Git Bash) -- use Unix paths
- Two GitHub accounts: `benzsevern` (personal, for this repo) and `benzsevern-mjh` (work)
- MUST `gh auth switch --user benzsevern` before push, switch back to `benzsevern-mjh` after
- Rust 1.94.0 at `C:\Users\bsevern\.cargo\bin` -- must set `RUSTUP_HOME="C:/Users/bsevern/.rustup"` and `CARGO_HOME="C:/Users/bsevern/.cargo"` in every bash command
- No admin privileges -- no LLVM/libclang, no WSL2. pgrx builds only work in CI (Linux)
- PostgreSQL 16 portable at `C:\Users\bsevern\tools\pg16portable\pgsql`
- Bridge crate compiles locally. Postgres crate requires Linux (CI only).

## Architecture
- Rust workspace (`Cargo.toml`) contains only `bridge/` crate
- `postgres/` is excluded from workspace (`exclude = ["postgres"]`) -- pgrx 0.12.9 bug with SQL generation in workspace mode
- `duckdb/` is a standalone Python package (not Rust)

### bridge/ (goldenmatch-bridge)
- Shared crate: embeds CPython via pyo3, calls goldenmatch Python API
- `api.rs` -- wrappers for dedupe, match, score_strings, score_pair, explain_pair, dedupe_pairs (structured), dedupe_clusters (structured)
- `convert.rs` -- JSON <-> Polars DataFrame conversion (future: Arrow C Data Interface)
- `error.rs` -- BridgeError enum with From impls for PyErr and ArrowError

### postgres/ (goldenmatch_pg)
- pgrx 0.12.9 Postgres extension, standalone crate (not in workspace)
- `quick.rs` -- 11 SQL functions: table-based (SPI), table-returning (TableIterator), scalar, JSON-based
- `pipeline.rs` -- 5 job management functions: gm_configure, gm_run, gm_jobs, gm_golden, gm_drop
- `spi.rs` -- reads PG tables via `row_to_json()` SPI queries
- `correction.rs` -- Learning Memory CRUD + tuning: `correction_add`, `correction_list`, plus `memory_learn` (force a MemoryLearner pass) and `memory_stats` (counts + learned adjustments). All wrap the bridge `goldenmatch_bridge::api::*`. `memory_learn` is REVOKEd from PUBLIC like `correction_add`; `memory_stats` is read-only status, left for PUBLIC.
- SQL file at `sql/goldenmatch_pg--0.6.0.sql` -- handwritten (pgrx doesn't auto-generate); `sql/goldenmatch_pg--0.5.0--0.6.0.sql` is the upgrade script
- `kernels.rs` -- native-direct graph + fingerprint functions (#509). `goldenmatch_pair_dedup`/`_str` + `goldenmatch_connected_components`/`_str` call the pyo3-free `goldenmatch-graph-core` crate in PURE RUST (no embedded CPython); `goldenmatch_record_fingerprint` uses `fingerprint-core`. `goldenmatch_embed_local(text, model_path)` and `gm_embed(text)` (#737; dir from `GOLDENEMBED_MODEL_DIR`, `float4[]` for DataFusion parity, NULL->"") call `goldenembed-rs` native-direct (no CPython); the model is loaded once per backend process and cached by dir (`embed_one` + an `OnceLock<Mutex<HashMap>>`)
- .control file: `schema = goldenmatch` -- all functions in goldenmatch schema

### duckdb/ (goldenmatch-duckdb)
- Python package: `pip install goldenmatch-duckdb`
- `functions.py` -- registers the core DuckDB UDFs via `con.create_function()`, then delegates to `goldenflow.py::register_goldenflow_functions` and `core_apis.py::register_core_api_functions`
- `goldenflow.py` -- 8 goldenflow series-transform UDFs (fail-open if goldenflow not installed)
- `core_apis.py` -- 13 parity UDFs wrapping goldenmatch's function-shaped core APIs (`goldenmatch_profile_table`, `goldenmatch_suggest_threshold`, `goldenmatch_detect_domain`, `goldenmatch_extract_features`, `goldenmatch_evaluate`, `goldenmatch_compare_clusters`, `goldenmatch_validate_table`, `goldenmatch_autofix_table`, `goldenmatch_detect_anomalies`, `goldenmatch_preflight`, `goldenmatch_postflight`, `goldenmatch_train_em`, `goldenmatch_score_probabilistic`). Pure-Python wrappers (import `goldenmatch` directly), JSON in/JSON out, fail-soft to `{"error": ...}`. Tests: `tests/test_core_apis.py`
- Table-reading UDFs use `con.cursor()` to avoid deadlock (UDF can't query same connection)
- `goldenmatch_suggest_threshold` registers with `null_handling="special"` because it legitimately returns SQL NULL (unimodal / too-few-scores). Other UDFs that may "fail" return a JSON `{"error": ...}` string instead.
- `functions.py` also registers the `gm_configure`/`gm_run`/`gm_jobs`/`gm_golden`/`gm_drop` job-management UDFs (in-memory dict equivalent of the Postgres `pipeline.rs` set) -- both backends expose these. Only the table-returning `gm_pairs`/`gm_clusters` remain Postgres-only; DuckDB returns JSON via `dedupe`/`dedupe_table` instead.
- `goldenmatch_postflight` derives `pair_scores` by running `dedupe_df` on the table (postflight needs scored pairs that aren't in the table).
- `functions.py` also registers Learning Memory UDFs: `goldenmatch_correction_add`/`_list` (CRUD) + `goldenmatch_memory_learn` (MemoryLearner pass) + `goldenmatch_memory_stats` (status). Both backends expose all four. Tests: `tests/test_memory_learn_stats.py`.
- Requires `pyarrow` for DuckDB `.pl()` Polars conversion

## Testing
- Rust bash preamble (copy-paste before any cargo command): `export PATH="/c/Users/bsevern/.cargo/bin:$PATH" && export RUSTUP_HOME="C:/Users/bsevern/.rustup" && export CARGO_HOME="C:/Users/bsevern/.cargo"`
- `cargo build -p goldenmatch-bridge` -- builds bridge locally (works on Windows)
- `cargo test -p goldenmatch-bridge` -- runs bridge tests (needs goldenmatch Python package installed)
- Postgres extension: build/test only via CI (needs libclang + PG dev headers)
- DuckDB: `cd duckdb && pip install -e . && python -m pytest tests/ -v`

## CI
- 4 jobs: lint, bridge-tests, postgres-build, duckdb-tests
- Lint: `cargo fmt --check` (bridge + postgres separately) + `cargo clippy` (bridge only)
- Postgres CI: tests PG 15/16/17 in parallel (fail-fast: false). Uses PostgreSQL apt repo for PG 15/17 availability
- Multi-PG: must `pg_createcluster` explicitly + use `pg_lsclusters` to find correct port per version
- System Python for Postgres: `sudo rm -f /usr/lib/python3/dist-packages/typing_extensions.py` then `sudo pip install --break-system-packages goldenmatch`
- **Embed smoke (#737):** the `rust_pgrx` lane proves `ort`/onnxruntime loads inside the Postgres BACKEND process (distinct from the proven DataFusion cdylib case) by calling `goldenmatch_embed_local`/`gm_embed` against the goldenembed `tests/fixtures/tiny_model`. `gm_embed` reads `GOLDENEMBED_MODEL_DIR` from the backend env, so the smoke STOPS the cluster and restarts it with `sudo -u postgres env GOLDENEMBED_MODEL_DIR=<dir> pg_ctlcluster ... start` (sudo strips env otherwise). `gm_embed` is NOT `STRICT` (NULL -> "" parity with DataFusion), so the smoke also asserts `gm_embed(NULL)` still returns a vector. The lane triggers on `goldenembed/**` too (path dep of the postgres crate).
- Release workflow: builds .tar.gz + .deb + .rpm for PG 15/16/17, pushes Docker to ghcr.io + Docker Hub

## Identity Graph (v2.0, 2026-05-13)

DuckDB UDFs + Postgres pg_extern functions implementing the contract at
`docs/superpowers/specs/2026-05-12-identity-graph-duckdb-contract.md`
(monorepo root). Five read-only functions per backend:

| Function | Args |
|---|---|
| `goldenmatch_identity_resolve` | `(record_id TEXT, db_path TEXT)` |
| `goldenmatch_identity_view`    | `(entity_id TEXT, db_path TEXT)` |
| `goldenmatch_identity_history` | `(entity_id TEXT, db_path TEXT)` |
| `goldenmatch_identity_conflicts` | `(dataset TEXT, db_path TEXT)` |
| `goldenmatch_identity_list`    | `(dataset TEXT, status TEXT, db_path TEXT)` |

- **Deviation from contract**: takes explicit `db_path` per call rather than
  reading a session-level `SET goldenmatch_identity_path` setting. DuckDB
  Python UDFs cannot easily read session settings; pgrx + pyo3 settings
  round-trips add complexity for no real benefit. Explicit-arg also reads
  better at the SQL call site.
- **Read-only**: writes must go through the Python `goldenmatch identity
  merge/split` CLI, REST `/api/v1/identities/{id}/{merge,split}`, or MCP
  `identity_merge` / `identity_split` tools in the main goldenmatch package.
- **Python dep**: requires `goldenmatch>=1.15.0` (ships `goldenmatch.identity.*`).
- **Tests**: `duckdb/tests/test_identity.py` -- 9 cases against a
  tmp_path-seeded SQLite identity DB. Postgres-side is CI-only.
- **Version bumps**: `goldenmatch-duckdb` 0.2.0 -> 0.3.0; pgrx
  `goldenmatch_pg` 0.3.0 -> 0.4.0.

## SQL surface coverage + deferred-by-design

Both backends expose the same function set (DuckDB UDFs <-> Postgres
`pg_extern`, JSON in/JSON out): core scoring/dedupe/match, the 13 core-API
parity functions, 8 `goldenflow_*` transforms, 5 read-only identity functions,
job-management (`gm_*`), and Learning Memory (`correction_add`/`_list` +
`memory_learn`/`memory_stats`).

A sizeable slice of the Python `goldenmatch.__all__` is **intentionally not**
exposed in SQL. These are deferred by design, not gaps -- the rationale:

| Python capability | Why not SQL |
|---|---|
| PPRL (`pprl_link`, `run_pprl`, ...) | File-path / multi-party protocol args; not a single-table JSON-in/out shape. |
| `run_sensitivity` | Takes `file_specs` (sweep over multiple input files). |
| Streaming (`match_one`, `StreamProcessor`) | Stateful / incremental; SQL UDFs are stateless per call. |
| LLM family (`llm_score_pairs`, `llm_cluster_pairs`, `llm_extract_features`) | Needs network + API keys; not safe/deterministic inside a SQL engine. |
| boost / rerank (`boost_accuracy`, `rerank_top_pairs`) | Bootstraps a HuggingFace cross-encoder (model download); too heavy for a UDF. |
| `run_graph_er` | Multi-table + relationship config; not a single-table call. |
| Identity **writes** (`manual_merge`, `manual_split`, `resolve_clusters`) | SQL identity surface is read-only by design; writes go through the Python CLI / REST `/api/v1/identities/...` / MCP `identity_merge`/`identity_split`. |
| `auto_map_columns` | InferMap schema mapping -- a separate package, not goldenmatch core. |
| lineage / output writers (`build_lineage`, `write_output`, `generate_dedupe_report`) | Run-coupled / file-emitting; SQL callers get the data back directly and persist it themselves. |

If a user genuinely needs one of these in SQL, add it to BOTH backends in
lockstep (bridge fn + pgrx wrapper + handwritten SQL on the Postgres side;
`functions.py` UDF on the DuckDB side) so the two stay interchangeable.

## Gotchas
- pgrx 0.12.9 does NOT auto-generate SQL files -- must maintain `sql/goldenmatch_pg--0.6.0.sql` manually (+ the `--0.5.0--0.6.0.sql` upgrade script). Bumping the PG version means renaming the SQL file AND updating the hardcoded `cp sql/goldenmatch_pg--X.Y.Z.sql` lines in root `.github/workflows/ci.yml` + `publish-goldenmatch-pg.yml` (the orphaned `packages/.../.github` copies are ignored)
- pgrx extension functions are in `goldenmatch` schema -- use `goldenmatch.function_name()` in psql, or explicit `::TEXT` casts
- `cargo` defaults CARGO_HOME to drive root on Windows when CWD is D: -- always set explicitly
- DuckDB UDFs cannot query same connection (deadlock) -- use `con.cursor()` for table reads
- `cargo fmt` must run separately for bridge (workspace) and postgres (standalone)
- PyPI publishing uses credentials from `D:\show_case\goldenmatch\.testing\.env`

## maturin wheels over `ort` (onnxruntime) -- cross-platform PUBLISH gotchas (`goldenmatch-embed`, learned 2026-06-05, took 3 build-fix rounds)
`ort` builds fine in the plain `embed_wheel`/`goldenembed` CI lanes (ubuntu-latest has system OpenSSL), but the maturin **publish** workflow uses minimal manylinux/cross containers that bite:
- **`ort-sys` pulls `openssl-sys` as a BUILD-dependency** (its build script downloads ONNX Runtime via a TLS client). Cargo resolver v2 does NOT propagate a normal-dep `openssl/vendored` feature into build-deps, so vendoring from the crate is a NO-OP. Fix = install SYSTEM OpenSSL into the build container via maturin-action `before-script-linux`.
- **The two Linux legs use DIFFERENT base images.** x86_64 = AlmaLinux manylinux (`dnf`/`yum` -> `openssl-devel`); aarch64 = the Debian-based `rust-cross/manylinux_2_28-cross:aarch64` (`apt-get` -> `libssl-dev`). A `dnf`-only before-script exits **127** on aarch64. Use a portable `if dnf; elif yum; elif apt-get` script (see `publish-goldenmatch-embed.yml`).
- **No `ort` prebuilt for `x86_64-apple-darwin`** (Intel Macs) -> that maturin leg can't link without compiling ONNX from source. Drop it; macOS = `aarch64` only (Intel users fall back to the Python in-house embedder). Windows + macOS-arm + both Linux legs build fine once OpenSSL is present.
- **Re-run a failed publish** with `gh workflow run publish-goldenmatch-embed.yml --ref main` (workflow_dispatch builds main HEAD + publishes the pyproject version) instead of re-tagging. The `publish` job is `release: published`-gated per package, so every release event fires ALL per-package publish workflows; each self-skips unless the tag matches its prefix (a "skipped" run for the wrong tag is correct, not a failure).
