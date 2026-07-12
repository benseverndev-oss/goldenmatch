# Changelog

All notable changes to GoldenCheck will be documented in this file.

## [3.1.0] - 2026-07-12

### Performance
- **Fused single-pass string-column digest.** A new native kernel
  (`string_column_digest`) computes a string column's `null_count`, `n_unique`, and
  the match count for all 7 fixed scan patterns (email/phone/url + 4 encoding
  patterns) in ONE pass over the data, instead of ~10 separate passes (each
  `str_match_count` was its own scan; the 4 encoding patterns went through a
  `to_pylist` + regex-kernel round-trip). `ArrowColumn` computes+caches the digest on
  the first known-pattern `str_match_count` for a string column; subsequent
  known-pattern counts and `n_unique`/`null_count` read the cache. Findings are
  unchanged (differential Jaccard 1.000; all 7 patterns compile in the regex crate).
  1M x 7 scan_file: 1.36s -> **0.98s** (with the 3.0.3 parallel scan); string-heavy
  data benefits most. Cumulative 3.0.1 -> 3.1.0: 3.74s -> 0.98s (~3.8x). The pyarrow
  fallback is unchanged when the native kernel is absent.

## [3.0.3] - 2026-07-12

### Performance
- **Parallel column scan.** The column-profiling loop now fans out across a thread
  pool (pyarrow.compute and the native kernels release the GIL). Column profiling is
  independent per column and `profiler_context` is per-column, so results are merged
  in COLUMN order and are byte-identical to the sequential path (verified: `scan_file`
  1-thread vs 8-thread findings identical incl. order; differential Jaccard 1.000).
  1M x 7: scan_file 1.97s -> 1.36s. Gated by `GOLDENCHECK_SCAN_THREADS` (default:
  parallel when >=2 columns and >=50k rows, capped at min(cpu, 8, ncols); `1` forces
  sequential). Cumulative with 3.0.1/3.0.2: 3.74s -> 1.36s (~2.75x).

## [3.0.2] - 2026-07-12

### Performance
- **Iterative hotspot pass on the Arrow scan path -- ~2x faster** (1M x 7 columns:
  scan_file 3.74s -> 1.91s, native; findings unchanged, differential Jaccard 1.000).
  - `str_match_count` / `str_replace_all` / `str_to_date` now prefer **vectorized**
    `pyarrow.compute` (`match_substring_regex` / `replace_substring_regex` / `strptime`)
    over the list-based native kernel, which forced `to_pylist()` materialization of
    the whole column (the single largest scan cost at 1M rows). The native kernel
    remains the fallback for patterns pyarrow's RE2 cannot compile (e.g. `\uXXXX`).
  - `value_counts_desc` uses `pc.value_counts` (C++) instead of a Python `Counter`
    over a materialized list.
  - `ArrowFrame` caches wrapped columns and `ArrowColumn` memoizes
    `n_unique`/`drop_nulls`/`numeric_stats`, so those run once per column instead of
    once per profiler (all seam profilers share one frame).
  - Net effect: the compiled kernel (`[native]`) now measurably accelerates a scan
    (1.91s vs 3.04s pyarrow-only) where before the string/cast overhead masked it.

## [3.0.1] - 2026-07-12

### Performance
- **Vectorized `ArrowColumn.cast` (string -> numeric).** The 3.0.0 implementation
  parsed string columns element-by-element in a Python loop (`float(v)`/`int(v)`
  with try/except), which made `type_inference`'s numeric probe the dominant cost
  of a scan and scaled linearly with row count -- ~2M `float()`+`append` calls on
  a 1M-row string column, and catastrophic on larger data. Replaced with a single
  vectorized `pyarrow.compute` pass: regex-mask non-numeric literals to null, then
  one `pc.cast`. Roughly halves column-profiling wall on string-heavy data and
  removes the O(N) Python loop entirely. Findings are unchanged (the finding-set
  differential stays at Jaccard 1.000; owned contract: a value is numeric if it
  matches a standard decimal/float literal, so non-standard tokens like `inf`/`nan`
  or underscored digits are treated as non-numeric).

## [3.0.0] - 2026-07-11

### Changed (BREAKING)
- **The default scan path is now Arrow-native and Polars-free.** `scan_file`,
  `scan_dataframe`, and the CLI `check`/`scan` run WITHOUT Polars -- including
  CSV. `scan_file` reads through an Arrow-native reader (`read_file_arrow`) and
  the scan frame is a `pyarrow.Table`, so a plain `pip install goldencheck` can
  scan CSV/Parquet/Excel end-to-end with no Polars installed. The 2.0.0 rule
  that "CSV reading and the full scan still require `goldencheck[polars]`" is no
  longer true.
- **`pyarrow` is now a base dependency** (the scan frame is a `pyarrow.Table`).
- **Polars is no longer needed for the default scan.** It moved to two opt-in
  extras: `goldencheck[baseline]` now pulls `polars>=1.0` for the scipy-backed
  statistical / drift / correlation subsystems (which still run on Polars), and
  `goldencheck[polars]` remains only for the `scan_dataframe(pl.DataFrame)`
  convenience overload. `scan_dataframe` accepts a `pyarrow.Table` natively; a
  `polars.DataFrame` is converted via `.to_arrow()` only when Polars is present.
- **`inferred_type` now emits a neutral dtype vocabulary** (`str`, `int`,
  `uint`, `float`, `date`, `datetime`, `bool`, `other`) instead of raw Polars
  dtype strings (`Int64`, `Utf8`, ...). Scan output, profiles, and the MCP/agent
  `type` fields report the neutral names.
- **Sampling is an owned deterministic sample.** The large-file sampler replaced
  the Polars PRNG (`df.sample(seed=42)`) with an owned deterministic stride
  sample over the Arrow table -- stable across runs and `--workers`, registered
  as an accepted divergence from the old Polars-native sample.

### Notes
- The compiled native kernel (`goldencheck[native]`) still accelerates the
  numeric / sequence / date / regex checks; the profilers self-skip those when
  it is absent (graceful degradation, unchanged pattern).
- The `nopolars` CI lane now asserts the **full** scan (CSV + `scan_file` +
  the CLI) succeeds with Polars uninstalled -- previously it asserted only the
  covered-columns subset ran Polars-free.

### Migration
- Most users need no change: `pip install goldencheck` now scans everything
  without Polars. Only add `goldencheck[polars]` if you call
  `scan_dataframe(pl.DataFrame)` with a Polars frame, and `goldencheck[baseline]`
  if you use the statistical baseline / drift / correlation features.

## [2.0.0] - 2026-07-11

### Changed (BREAKING)
- **`polars` is no longer a base dependency** -- it moved to the `[polars]` optional
  extra. `pip install goldencheck` no longer pulls Polars (~185 MB). Parquet/Excel
  reading (`read_columns`) and the structural scan (`scan_columns` / `scan_file_columns`)
  run without Polars. **CSV reading and the full scan (`scan_dataframe` / `scan_file`)
  still require Polars** -- install `goldencheck[polars]` for them (Polars' CSV dtype
  inference isn't reproducible, and the full scan is Polars-native). Upgrading users who
  scan CSVs or use `scan_file` / `scan_dataframe` must add `[polars]`.

### Added
- `read_columns(path)` / `scan_file_columns(path)` -- Polars-free Parquet (pyarrow, new
  `[parquet]` extra) + Excel (openpyxl) read into columns + covered structural scan.
- **Denial-constraint discovery** -- a new opt-in discovered-rule family that
  mines denial constraints `¬(p1 ∧ … ∧ pm)` (if-then / cross-tuple invariants
  like `¬(status=shipped ∧ ship_date<order_date)`) from a single table and
  surfaces the violating rows. Public API `discover_denial_constraints(df, ...)`
  + the exported `DenialConstraint` type; new `goldencheck denial-constraints`
  CLI command and a `--denial` opt-in flag on `goldencheck scan` (`--deep`
  widens the row-level pass to the full population). Sample-then-validate engine
  (`goldencheck/denial/`) with two evidence passes -- row-level exact (single-
  tuple) and pairwise sampled (cross/mixed) -- order-preserving RANK encoding,
  and a native `goldencheck-core::dc.rs` evidence kernel (gated on
  `GOLDENCHECK_NATIVE`, set/byte-parity with the pure-Python fallback,
  measure-first: ~1.5-1.8x over a Polars cross-join, ~60-96x over pure Python).
  Findings surface as `check="denial_constraint"` (WARNING violated / INFO
  strict). Not in the default scan. Stage 1 of a 5-stage program (cross-table
  DCs, numeric-threshold literals, baseline pinning + DC drift, and
  DuckDB/Postgres/WASM/MCP surfaces are deferred to later stages).
- **`goldencheck.core.kernels`** -- list-shaped programmatic entry points to the
  five deep-profiling kernels (benford histogram, near-duplicate value clusters,
  strict + approximate functional dependencies, composite keys). Plain lists in,
  index/count structures out; runs the native-gated kernel when built, else the
  profilers' own pure-Python fallbacks (native == fallback byte-for-byte). This
  is the shared source of truth behind the new native SQL surfaces: the DuckDB
  `goldencheck_*` UDFs (goldenmatch-duckdb) and the Postgres `goldencheck_*`
  functions (goldenmatch_pg 0.13.0), completing GoldenCheck's cross-surface
  parity (roadmap P5).
- **`scan_columns(columns)`** -- a reduced, **Polars-free** structural scan of
  in-memory column data (`dict[str, list]` in, `list[Finding]` out). Always runs
  the mechanical structural checks (nullability, uniqueness, cardinality); also
  runs the format, encoding, pattern-consistency, and temporal-order checks when
  `goldencheck[native]` is installed. Byte-identical to the corresponding
  `scan_dataframe` checks; complements `scan_dataframe` for callers that want the
  covered structural checks without constructing a Polars DataFrame. Internally
  this is the covered-subset backend of the Polars-eviction program (a backend-
  neutral Frame/Column seam with a pure-Python backend); new `goldencheck-native`
  kernel components `regex` (string-pattern checks) and `str_to_date` (chrono
  date parsing, the same engine Polars uses) back the format/encoding/pattern and
  temporal profilers on the non-Polars path, byte-identically. `NativeRequiredError`
  is raised if a native-only covered check is requested without the kernel built.
  (Polars remains a base dependency today; making it optional is a later stage of
  the program.)

## [1.4.1] - 2026-07-02

### Changed
- **Fuzzy-values / `cell_quality` Python path is ~38x faster.** The near-duplicate value profiler's non-native Levenshtein path now uses `rapidfuzz` (a new dependency, the same pin the rest of the suite uses) instead of a pure-Python dynamic-programming loop. Byte-identical clusters (same `1 - dist/maxlen` metric, pinned against a reference DP), so the native kernel and the Python path still agree; the pure-Python fallback is retired. Measured 1757ms → 46ms on 110k candidate pairs — this is the path GoldenMatch's quality-weighted survivorship hits via `cell_quality`. (#1386, #1387)

## [1.4.0] - 2026-06-24

### Added
- **Native acceleration runtime** — optional Rust/Arrow kernels for the CPU-bound deep-profiling work (Benford, composite-key, functional-dependency mining). `pip install goldencheck[native]`; goldencheck stays pure-Python and falls back automatically when the kernel isn't installed. (#793)
- **Deep-profiling expansion** — new profilers and relation checks: data freshness, fuzzy/near-duplicate values, referential integrity, approximate duplicates, approximate functional dependencies, and composite-key discovery. (#793)
- **`functional_dependencies(df, ...)`** top-level export — FD-driven negative evidence (surfaces records that violate a discovered functional dependency). (#797)
- **`cell_quality(...)`** top-level export — per-cell quality scoring for quality-weighted survivorship (wires GoldenCheck signals into GoldenMatch golden-record selection). (#794)

### Changed
- **Fixer perf** — vectorized the per-cell safe fixes behind a guard; large frames apply the safe fixer significantly faster with identical output. (#843)

### Security
- Bumped `aiohttp`/`starlette` floors to close known advisories. (#738)

## [1.3.0] - 2026-06-01

### Added
- `scan_dataframe(df, ...)` top-level export: scan an in-memory Polars DataFrame directly, without a CSV round-trip.
- Identity-safe primary-key preflight (`IdentitySafePkProfiler`): scans now emit a WARNING when a table has no stable primary-key candidate.

### Security
- SSRF guard on the `goldencheck serve` `/scan-url` endpoint (`_validate_remote_url`): rejects non-HTTP(S) schemes and private/internal IP addresses.

### Changed
- Scanner perf: cached `n_unique`/`null_count` and vectorized generalization in the column-profile loop (no behavioral change).
- Repository and project URLs rebranded from `benzsevern` to `benseverndev-oss`.

## [1.1.0] - 2026-04-03

### Added
- Deep Profiling & Baseline System — `goldencheck baseline` discovers statistical properties from data, saves to `goldencheck_baseline.yaml`
- Drift Detection — `goldencheck scan --baseline` detects distribution shifts, constraint violations, type changes, correlation breaks, pattern drift (13 check types)
- 6 baseline techniques: statistical profiler, constraint miner, semantic type inferrer, correlation analyzer, pattern grammar inducer, confidence prior builder
- CLI: `goldencheck baseline data.csv` with `--skip`, `--update`, `-o` flags
- Python API: `create_baseline()`, `load_baseline()`, `baseline=` param on `scan_file()`
- Optional `[baseline]` extras (scipy, numpy); optional `[semantic]` extras (sentence-transformers)
- 139 new tests across baseline/ and drift/ modules

### Changed
- `scan_file()` accepts `baseline: BaselineProfile | Path | None` parameter
- Auto-discovers `goldencheck_baseline.yaml` next to data file

## [1.0.2] - 2026-03-29

### Added
- **MCP Registry metadata** — `server.json` and `mcp-name` verification for registry discovery

## [1.0.1] - 2026-03-25

### Added
- **REST API server** — `goldencheck serve` exposes scan/validate/profile/health endpoints
- **Database scanning** — `goldencheck scan-db` scans tables directly from PostgreSQL, MySQL, SQLite
- **Scheduled runs** — `goldencheck schedule` for cron-style recurring scans with webhook alerts
- **HTML reports** — `--html report.html` generates shareable, self-contained dark-themed reports

### Stats
- **296 tests** | **DQBench 88.40** | **14 commands** | **9 MCP tools** | **3 domain packs**

## [1.0.0] - 2026-03-24

### Added
- **Multi-file scan** — `goldencheck scan file1.csv file2.csv` scans multiple files in one command
- **HTML report** — `--html report.html` generates a shareable, self-contained dark-themed report
- **Progress indicator** — prints row count, column count, and sampling note before scanning
- **TUI dismiss** — `d` key dismisses findings, persists to `ignore` list on F2 save
- **Real-world dataset tests** — 22 tests across 5 public datasets (airports, countries, GDP, population, S&P 500)
- **API stability doc** — `docs/api-stability.md` with stable/beta/experimental classification
- **Fixer completions** — `strip_control_chars` (moderate), `fill_nulls_with_mode` (aggressive)
- **Exit codes in --help** — documented in the app help text

### Fixed
- 10 code review bugs: diff crash, MCP path traversal, domain+LLM forwarding, arg parser guards, history path, TUI guided mode, age/DOB dtype, webhook semantics, init LLM prompt, MCP check list
- `person_name` classifier narrowed to avoid false positives on airport names, municipalities, HQ locations
- Ruff unused import errors in CI

### Stats
- **296 tests** | **DQBench 88.40** | **11 commands** | **9 MCP tools** | **3 domain packs**

## [0.6.0] - 2026-03-24

### Added
- **`goldencheck init`** — interactive setup wizard: scan, auto-pin rules, scaffold GitHub/GitLab CI in one command. Supports `--yes` for non-interactive mode
- **`goldencheck history`** — scan history tracking in `.goldencheck/history.jsonl`. Shows scores, grades, and trends over time. Supports `--last N` and `--json`
- **`--smart` auto-triage** — automatically pin high-confidence findings, dismiss low-confidence. Zero interaction: `goldencheck scan data.csv --smart`
- **`--guided` walkthrough** — walk through findings one-by-one with pin/skip: `goldencheck scan data.csv --guided`
- **TUI guided mode** — press `g` in the TUI to walk through findings sequentially with pin/dismiss/skip
- **Webhook notifications** — `--webhook <url> --notify-on grade-drop|any-error|any-warning` on scan and watch commands
- **LLM prompt improvements** — added cross-column ID prefix checks, age/DOB validation, weekend detection, state/zip consistency, mixed coding standards
- **Merger keyword preservation** — ensures LLM findings include required keywords for benchmark scoring
- **dbt-goldencheck** — separate dbt package for zero-config data validation as a dbt test (`benseverndev-oss/dbt-goldencheck`)
- **goldencheck-types** — community GitHub repo for domain-specific type definitions (`benseverndev-oss/goldencheck-types`)

### New Modules
- `goldencheck/engine/triage.py` — auto-triage engine (pin/dismiss/review buckets)
- `goldencheck/engine/history.py` — JSONL scan history recording and querying
- `goldencheck/engine/notifier.py` — webhook POST with configurable triggers
- `goldencheck/cli/init_wizard.py` — interactive setup wizard logic

## [0.5.0] - 2026-03-24

### Added
- **`goldencheck diff`** — compare two data files or against git HEAD. Shows schema changes, finding changes, and stat deltas. Supports `--ref` and `--json`
- **`goldencheck watch`** — poll a directory for file changes, re-scan on modification. Supports `--interval`, `--pattern`, `--exit-on` for CI, graceful SIGINT/SIGTERM
- **`goldencheck fix`** — auto-fix data quality issues with three modes: safe (whitespace, Unicode, encoding), moderate (+ case standardization), aggressive (+ type coercion). Supports `--dry-run` and `--force`
- **Domain packs** — `--domain healthcare|finance|ecommerce` flag for domain-specific semantic types
- **3 new MCP tools** — `list_domains`, `get_domain_info`, `install_domain` for domain pack discovery
- **Age vs DOB cross-validation** — new relation profiler detecting age/DOB mismatches
- **Numeric cross-column profiler** — detects value > max constraint violations
- **String length format check** — flags identifier columns with inconsistent lengths
- **Public API surface** — `__all__` exports on all public modules, `py.typed` PEP 561 marker, top-level convenience imports (`from goldencheck import scan_file, Finding`)
- **Friendly CLI error messages** — no more raw tracebacks for common errors
- **CI coverage** — Codecov integration + smoke test job
- **GitHub Action** — `benseverndev-oss/goldencheck-action@v1` for CI with PR comments

### Improved
- **DQBench Score: 87.71 → 88.40** — geo suppression narrowing, classifier prefix-match bug fix
- Semantic classifier: prefix-marked hints (`is_`, `has_`) no longer false-match via substring
- Pattern consistency profiler: populates `metadata` dict for structured pattern data
- Mixed coding standard detection improved (letter-first vs digit-first)
- Drift detection skips high-cardinality strings and datetime columns

## [0.4.0] - 2026-03-24

### Added
- **`goldencheck fix`** command with safe/moderate/aggressive modes
- **Friendly error handler** — context manager catching FileNotFoundError, PermissionError, ValueError, ComputeError
- **Public API surface** — `__all__`, `py.typed`, top-level re-exports
- **CI coverage** — Codecov + smoke test jobs
- **Version consolidation** — single `__version__` source in `__init__.py`

## [0.3.0] - 2025-03-24

### Added
- **MCP server** — `goldencheck mcp-serve` exposes 6 tools (scan, validate, profile, health_score, get_column_detail, list_checks) for Claude Desktop integration. Install with `pip install goldencheck[mcp]`
- **LLM rule generation** — `goldencheck learn` sends data samples to an LLM and generates domain-specific validation rules (regex, length, value lists, cross-column). Rules saved to `goldencheck_rules.json` and auto-applied on future scans
- **Jupyter / Colab support** — `_repr_html_()` on Finding and DatasetProfile, plus `ScanResult` wrapper in `goldencheck.notebook` for rich HTML display
- **Colab demo notebook** — `scripts/goldencheck_demo.ipynb` with "Open in Colab" badge
- **DevContainer** — `.devcontainer/devcontainer.json` for Codespaces (Python 3.12, ruff, Jupyter)
- **Try-It GitHub Action** — zero-install demo via `workflow_dispatch`, paste a CSV URL and get results
- **Numeric cross-column profiler** — detects value > max constraint violations (e.g., claim_amount > policy_max)
- **Digits-in-name detection** — flags numeric characters in person_name columns as WARNING
- **Mixed coding standard detection** — pattern_consistency now detects structural pattern shifts (letter-first vs digit-first)

### Improved
- **DQBench Score: 72.00 → 87.71** (+15.71 points)
- Temporal order heuristics expanded: admission/discharge, service/submit, and 15+ new pairs
- Drift detection skips high-cardinality string columns (>90% unique) — eliminates false positives on IPs, UUIDs, session IDs
- Drift detection suppressed on datetime columns via semantic types
- Date-pair fallback tightened (6-column guard) — prevents noisy combinatorial pairs
- CI badge added to README

## [0.2.0] - 2025-03-23

### Added
- **Semantic type classification** — auto-detects 11 column types (email, phone, address, free_text, etc.) via name heuristics and value-based inference
- **Suppression engine** — suppresses irrelevant findings based on semantic type (e.g., uniqueness warnings on email columns)
- **Confidence scoring** — every finding gets a 0.0–1.0 confidence score displayed as H/M/L in the TUI
- **Corroboration boost** — multiple profilers flagging the same column increases confidence (+0.1 for 2 checks, +0.2 for 3+)
- **Confidence downgrade** — low-confidence findings demoted to INFO when LLM boost is not active
- **LLM boost** — `--llm-boost` flag sends representative sample blocks to an LLM for enhanced validation
  - Supports Anthropic (Claude) and OpenAI providers
  - Budget tracking with `GOLDENCHECK_LLM_BUDGET` env var
  - Standardized check names for consistent LLM ↔ profiler merging
- **Cross-column profilers** — temporal ordering and null correlation detection
- **Encoding detection profiler** — detects mojibake, mixed encodings, control characters
- **Sequence detection profiler** — identifies broken auto-increment sequences and gaps
- **Drift detection profiler** — finds temporal distribution shifts within a column
- **DQBench Score: 72.00** — beating Great Expectations (21.68), Pandera (32.51), and Soda Core (22.36)

### Improved
- Range profiler now chains with type inference for better numeric detection
- Minority wrong-type detection catches columns that are "mostly numeric with a few strings"
- Temporal ordering heuristics expanded (signup→login, open→close, etc.)
- Profiler-only column recall improved from 87% to 100%

## [0.1.0] - 2025-03-22

### Added
- **Core profiler pipeline** — 7 column profilers: type inference, nullability, uniqueness, format detection, range/distribution, cardinality, pattern consistency
- **Interactive TUI** — 4-tab Textual interface (Overview, Findings, Column Detail, Rules)
- **Rule pinning** — Space to pin findings, F2 to export to `goldencheck.yml`
- **Validation mode** — `goldencheck validate` enforces saved rules with CI-friendly exit codes
- **CLI** — `goldencheck <file>` shorthand, `--no-tui`, `--json`, `--fail-on`, `--verbose`, `--debug`
- **File formats** — CSV, Parquet, Excel (.xlsx/.xls)
- **Polars-native** — all data operations use Polars for speed
- **Deterministic sampling** — seed=42 for reproducible results on large files
- **Rich CLI output** — severity-colored findings with sample values
- **JSON reporter** — machine-readable output for CI pipelines
