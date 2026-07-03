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
- `graph-layout/` is a standalone demo **binary** (own `[workspace]`, in the parent `exclude` list so the default rust build skips it): Barnes-Hut + multilevel force-directed layout of ER graphs → PPM frames. One real dep (`rayon`); built-in dependency-free PPM rasterizer (`--features skia` swaps in tiny-skia). `export_graph_layout.py` (stdlib) turns a goldenmatch identity DB / scored-pair CSV into its edge-list input. `cargo test` covers Barnes-Hut-vs-exact, cluster separation, coarsening. See its README.

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
- `kernels.rs` -- native-direct graph + fingerprint functions (#509). `goldenmatch_pair_dedup`/`_str` + `goldenmatch_connected_components`/`_str` call the pyo3-free `goldenmatch-graph-core` crate in PURE RUST (no embedded CPython); `goldenmatch_record_fingerprint` uses `fingerprint-core`. `goldenmatch_embed_local(text, model_path)` and `gm_embed(text)` (#737; dir from `GOLDENEMBED_MODEL_DIR`, `float4[]` for DataFusion parity, NULL->"") call `goldenembed-rs` native-direct (no CPython); the model is loaded once per backend process and cached by dir (`embed_one` + an `OnceLock<Mutex<HashMap>>`). **`goldenmatch_hnsw_pairs(flat_vecs real[], dim int, k int, threshold float8)` (0.10.0)** calls the pyo3-free `goldenhnsw` kernel native-direct: native HNSW ANN blocking over a row-major flat corpus, returns `TABLE(a,b,s)` canonical candidate pairs (0-based positions) — the SQL analogue of `ANNBlocker.query_with_scores`, one kernel shared with the wheel / TS-wasm / DuckDB surfaces. Flat `real[]` (not `real[][]`) because pgrx flattens multidim arrays; the `rust_pgrx` lane smoke-tests it.
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
- **Embed smoke (#737):** the `rust_pgrx` lane proves `ort`/onnxruntime loads inside the Postgres BACKEND process (distinct from the proven DataFusion cdylib case) by calling `goldenmatch_embed_local`/`gm_embed` against the goldenembed `tests/fixtures/tiny_model`. `gm_embed` reads `GOLDENEMBED_MODEL_DIR` from the backend env, and the postmaster does NOT inherit env passed to `pg_ctlcluster` (it re-execs) — write the var to the per-cluster `/etc/postgresql/<ver>/main/environment` file (the documented Debian mechanism PostgreSQL reads at startup) then `pg_ctlcluster ... restart`. `gm_embed` is NOT `STRICT` (NULL -> "" parity with DataFusion), so the smoke also asserts `gm_embed(NULL)` still returns a vector. The lane triggers on `goldenembed/**` too (path dep of the postgres crate).
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
- pgrx 0.12.9 does NOT auto-generate SQL files -- must maintain `sql/goldenmatch_pg--0.7.0.sql` (the current `default_version` base) manually, plus the chained upgrade scripts (`--0.5.0--0.6.0.sql`, `--0.6.0--0.7.0.sql`). Bumping the extension version means adding a new `--X.Y.Z.sql` base + `--<prev>--X.Y.Z.sql` migration, bumping `default_version` in `goldenmatch_pg.control` AND `version` in `Cargo.toml`, and adding the hardcoded `cp sql/goldenmatch_pg--*.sql` lines in root `.github/workflows/ci.yml` + `publish-goldenmatch-pg.yml` (the orphaned `packages/.../.github` copies are ignored). A new function in an ALREADY-PUBLISHED version is wrong: published versions are immutable, so the function goes in the NEXT version's base + migration, NOT retro-added to the released base (bit gm_embed/#737 -- it shipped into 0.6.0 which was already released, fixed by moving it to 0.7.0). **CI guard (`pgrx_sql_sync` job → `scripts/check_pgrx_sql_sync.py`):** since the SQL is hand-maintained, a CI job asserts every `#[pg_extern]` in the crate appears as a `CREATE FUNCTION "<name>"` in the base SQL for the current `default_version`. Adding a `#[pg_extern]` without wiring it into the SQL now fails on the PR (Python-only, no pgrx toolchain) instead of surfacing at the pgrx build/smoke, if at all. It enforces Rust→SQL presence only (name-based, no `pg_extern(name=…)` overrides exist in this crate); `extension_sql!` helpers with no matching `#[pg_extern]` are reported as info, not errors.
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

## goldenhnsw / goldenmatch-hnsw — native HNSW ANN index (IndexHNSWFlat)
- **`goldenhnsw/`** is a pyo3-free, standalone-workspace core crate (like `goldenembed`): a pure-Rust HNSW (`IndexHNSWFlat`) over f32 inner-product vectors, **zero C deps** (no FAISS, no `ort`, no openssl), **no `rayon`** (insertion is single-threaded — the Python caller already parallelizes across probes/buckets, and the #688 rayon `LockLatch` futex-park is structurally absent). Deterministic graph via a seeded SplitMix64 (no `rand`). Scores are the raw inner product, **byte-for-byte with FAISS `IndexFlatIP`**; on the normal path the embedder emits L2-normalized vectors so IP == cosine. `ef_search` auto-scales to the corpus size, so recall is *exact* at small N — the fallback-parity regime.
- **`hnsw-py/`** is the maturin/abi3 wheel `goldenmatch-hnsw` (module `goldenmatch_hnsw._hnsw`, class `HnswIndex`), a thin pyo3 wrapper over `goldenhnsw` — same layout as `embed-py`. Bulk `add_batch`/`search_batch` take the raw little-endian float32 buffer (`arr.astype('<f4').tobytes()`) to skip per-element Python↔Rust marshaling on large corpora; per-vector `add`/`search` take Python lists.
- **Consumed by `goldenmatch.core.ann_blocker.ANNBlocker` as a THIRD backend** (native HNSW → FAISS → numpy). `_resolve_backend(n, top_k)` picks HNSW in `auto` mode only above a size gate — `n >= GOLDENMATCH_ANN_HNSW_MIN` (4096) AND `top_k <= GOLDENMATCH_ANN_HNSW_MAX_K` (512) — so small N (and `VectorIndex`'s `top_k=N` retrieve-all pattern) stays on the exact path and every existing parity test is unchanged. `GOLDENMATCH_ANN_BACKEND` ∈ {auto,hnsw,faiss,numpy} forces a backend; graph params via `GOLDENMATCH_ANN_HNSW_{M,EF_CONSTRUCTION,EF_SEARCH}`.
- **NOT wired as a goldenmatch optional extra** (deliberately, like the unpublished-package footgun in the root CLAUDE.md): `goldenmatch-hnsw` isn't on PyPI yet, and an extra pointing at an unpublished package breaks `uv sync --all-packages`. It's an optional runtime import (`importlib.util.find_spec("goldenmatch_hnsw")`), exactly how faiss is treated. Add a `[hnsw]` extra only after the wheel publishes.
- **CI:** `goldenhnsw` lane (clippy + `cargo test`) and `hnsw_wheel` lane (maturin build + standalone smoke + `tests/test_ann_hnsw.py` integration) in root `ci.yml`; both in the `ci-required` gate. Publish: `publish-goldenmatch-hnsw.yml` on a `goldenmatch-hnsw-v*` tag — NO openssl `before-script` (no C deps) and **both** macOS arches build (unlike embed's ort-constrained aarch64-only).

### Cross-surface HNSW (every-capability-on-every-surface)
The `goldenhnsw` kernel is exposed on the other surfaces too, all running the SAME code (byte-identical inner-product ranking, proven by the shared golden fixture `goldenhnsw/golden/hnsw_vectors.json` — reproduced by `goldenhnsw/tests/golden.rs`, the TS `tests/parity/hnsw.parity.test.ts`, and the Python wheel):
- **TypeScript / WASM** — `goldenhnsw-wasm/` (wasm-bindgen) is embedded into the TS port as the opt-in subpath `goldenmatch/core/hnsw-wasm` (`WasmHNSWANNBlocker`), edge-safe (no `hnswlib-node` native addon). Regen: `node packages/typescript/goldenmatch/scripts/build_goldenhnsw_wasm.mjs`. Drift-guarded in the `typescript` CI lane (`hnsw_wasm` filter).
- **DuckDB SQL** — `duckdb/goldenmatch_duckdb/hnsw_kernels.py` registers `goldenmatch_hnsw_pairs(vectors DOUBLE[][], k BIGINT, threshold DOUBLE) -> STRUCT(a,b,s)[]`: native HNSW ANN blocking over an aggregated embedding column (the SQL analogue of `ANNBlocker.query_with_scores`). Uses the `goldenmatch-hnsw` wheel when present, else a numpy brute fallback (the `duckdb_extensions` CI lane exercises the fallback; local runs with the wheel exercise the native path). Tests: `duckdb/tests/test_hnsw_kernels.py`.
- **Postgres (pgrx)** — see the `postgres/` note (a native-direct `goldenmatch_hnsw_pairs` set-function, CI-only build).
