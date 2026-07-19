# Changelog

All notable changes to golden-suite are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/); this project uses semantic
versioning.

## [0.3.1] - 2026-07-18

- Floor bump: `goldenmatch[polars]>=3.5` (3.5.0: the date-aware `date` scorer;
  the FS missing-value correctness wave that restored `historical_50k`
  probabilistic F1 0.33 -> 0.83; 100% native Fellegi-Sunter coverage via the
  shared `fs-core` crate; and the `from_splink` random-pair-prior fix) and
  `goldenmatch-native>=0.1.18` (adds the native `date_similarity` kernel so the
  `date` scorer runs on the native path, not just the pure-Python fallback).
  Cut after goldenmatch 3.5.0 + goldenmatch-native 0.1.18 landed on PyPI
  (member-on-PyPI-first lockstep).

## [0.3.0] - 2026-07-16

- Suite minor train: floors raised to `goldenmatch[polars]>=3.4` (FS scoring in
  the distributed/chunked lanes, memory-bounded strategy-blocking scorer,
  columnar routing fix, missing-values-as-unobserved FS math),
  `goldencheck[polars]>=3.2`, `goldenpipe[golden-suite]>=1.4` (compiler
  SP1-SP3 + in-process moves), `infermap>=0.6` (Rust kernels on MCP servers +
  pattern_type cutover), `goldenanalysis>=0.4`, `goldencheck-types>=0.2`
  (16 domain packs), `goldenmatch-native>=0.1.17` (FS exclude-set Arc handle +
  zero-copy arrow FS entry), `goldensuite-mcp>=0.5`.

## [0.2.5] - 2026-07-14

- Floor bump: `goldenmatch[polars]>=3.3` (FS negative evidence on probabilistic
  matchkeys, Splink migration upgrade pass incl. the fan-out/NE lever) and
  `goldenmatch-native>=0.1.15` (native FS negative evidence + fused N-level
  banding, `FS_SUPPORTS_NE`).

## [0.2.4] - 2026-07-13

### Changed
- goldenmatch floor to `goldenmatch[polars]>=3.2` (3.2.0: Splink config
  converter -- `from_splink()` + `import-splink` CLI + `convert_splink_config`
  MCP tool -- and N-level `level_thresholds` probabilistic fields).
- goldenmatch-native floor to `goldenmatch-native>=0.1.14` (native N-level
  scoring; the kernel advertises `FS_SUPPORTS_LEVEL_THRESHOLDS` and the host
  routes custom banding natively).

## [0.2.3] - 2026-07-13

### Changed
- goldenflow floor to `goldenflow>=2.1.0` (2.1.0: owned auto-detect profile
  kernel -- zero-config type inference is now a cross-surface goldenflow-core
  kernel; base `goldenflow-native` floor `>=0.27.0`).

## [0.2.2] - 2026-07-13

### Changed
- goldenmatch floor to `goldenmatch[polars]>=3.1` (3.1.0: Arrow-native engine,
  polars optional upstream; the suite pins the `[polars]` extra so the
  wall-optimization paths and the classic `GOLDENMATCH_FRAME=polars` lane stay
  available for suite installs).

## [0.2.1] - 2026-07-12

### Changed
- **goldencheck floor raised to `goldencheck[polars]>=3.0.0`** -- goldencheck 3.0.0
  flips the default scan path to Arrow-native and Polars-free (`pip install
  goldencheck` scans CSV/Parquet/Excel end to end with no Polars; pyarrow is a base
  dep). `[polars]` is retained because the suite bundles goldenmatch, whose quality
  bridge calls `scan_dataframe(pl.DataFrame)`, and goldenmatch pulls Polars anyway.
  Cut after goldencheck 3.0.0 landed on PyPI (member-on-PyPI-first lockstep).

## [0.2.0] - 2026-07-12

### Changed
- **goldenmatch floor raised to `>=3.0`** -- goldenmatch 3.0.0 returns
  Arrow-native results (`pyarrow.Table`; migrate with
  `pl.from_arrow(result.golden)`) and defaults to the Arrow frame backend
  (~36% faster on the 100K zero-config benchmark;
  `GOLDENMATCH_FRAME=polars` is the opt-out). See goldenmatch's
  migrating-to-v3 guide.

## [0.1.10] - 2026-07-11

### Changed
- Bumped the goldencheck floor to **`goldencheck[polars]>=2.0.0`** (was `>=1.4.1`), per the
  lockstep policy. goldencheck 2.0.0 completes its Polars eviction: Polars is no longer a base
  dependency (it moved to the `goldencheck[polars]` extra). The `[polars]` marker keeps CSV
  reading and the full `scan_file`/`scan_dataframe` scan working in a suite install (Parquet/
  Excel + `scan_columns` run Polars-free). goldenmatch (a suite member) already pulls Polars,
  so the suite is unaffected at runtime; this is the explicit declaration.

## [0.1.9] - 2026-07-08

### Changed
- Bumped the goldenflow floor to **`goldenflow>=2.0.0`** (was `>=1.17.0`) and the
  goldenflow-native floor to **`goldenflow-native>=0.26.0`** (was `>=0.24.0`), per the
  lockstep policy. goldenflow 2.0.0 completes the Polars eviction: **Polars is no longer a
  base dependency** — `pip install goldenflow` runs Polars-free by default on
  goldenflow-native (now a base dep of goldenflow), and Polars moved to the optional
  `goldenflow[polars]` backend. All 113 transforms + CSV/Parquet/Excel/DB read + zero-config
  run without Polars. goldenflow-native 0.26.0 carries the full columnar surface
  (`format_f64` + the numeric-input `AsFloat` parser).

## [0.1.8] - 2026-07-07

### Changed
- Bumped the goldenflow floor to **`goldenflow>=1.17.0`** (was `>=1.16.0`) and the
  goldenflow-native floor to **`goldenflow-native>=0.24.0`** (was `>=0.15.0`), per the
  lockstep policy. goldenflow 1.17.0 + goldenflow-native 0.24.0 ship the Polars-eviction
  columnar engine (Phases 2-3): owned transforms run on the native/Arrow substrate (no
  Polars, no pyarrow) on both the whole-file CSV path and the in-memory path, covering
  string, phonetic, nullable URL/company/email, numeric (f64 + i64 parsers + array ops
  with a Polars-matching float formatter), and multi-output splits — byte-identical (data
  + manifest) to the Polars engine, opt-in via `GOLDENFLOW_ENGINE=columnar`.

## [0.1.7] - 2026-07-07

### Changed
- Bumped the goldenflow-native floor to **`goldenflow-native>=0.15.0`** (was
  `>=0.14.0`), per the lockstep policy. goldenflow-native 0.15.0 ships 3-8×
  faster hot transform kernels (name/URL/number families) — measured, byte-identical
  (native == pure-Python == the pinned parity corpus), so it's a pure speed
  improvement with no output change.

## [0.1.6] - 2026-07-06

### Changed
- Bumped the goldenflow-family floors after the nullable fused-apply release
  (lockstep policy): **`goldenflow>=1.16.0`** (was `>=1.15.0`) and
  **`goldenflow-native>=0.14.0`** (was `>=0.13.0`). goldenflow 1.16.0 extends the
  fused columnar apply to the `Option`-returning URL / company / email families
  (`url_normalize`, `company_normalize`, `email_mask`, …) — a run of those fuses
  into one native Arrow pass, byte-identical output (nulls included); goldenflow-native
  0.14.0 ships the `apply_chain_nullable_arrow` kernel symbol they need.

## [0.1.5] - 2026-07-06

### Changed
- Bumped the goldenflow-family floors after the numeric + parameterized fused-apply
  release (lockstep policy): **`goldenflow>=1.15.0`** (was `>=1.14.0`) and
  **`goldenflow-native>=0.13.0`** (was `>=0.12.0`). goldenflow 1.15.0 extends the
  fused columnar apply to f64 numeric chains (`round`/`clamp`/`abs_value`/`fill_zero`)
  and the parameterized string ops (`truncate`/`pad`), byte-identical output with
  lower peak RSS at scale; goldenflow-native 0.13.0 republishes with the
  `apply_chain_ops_arrow` + `apply_chain_f64_arrow` kernel symbols they need.

## [0.1.4] - 2026-07-06

### Changed
- Bumped the goldenflow-family floors after the fused-columnar-apply release
  (lockstep policy): **`goldenflow>=1.14.0`** (was `>=1.13.0`) and
  **`goldenflow-native>=0.12.0`** (was `>=0.11.0`). goldenflow 1.14.0 flips
  fused columnar apply on by default (a run of owned string transforms fuses
  into one native Arrow pass — byte-identical output, ~22% lower peak RSS at
  scale); goldenflow-native 0.12.0 ships the `apply_chain_arrow` kernel it needs.

## [0.1.3] - 2026-07-05

### Changed
- Bumped the goldenflow-family floors after the Wave D owned-kernel release:
  **`goldenflow>=1.13.0`** (was `>=1.4.0`) and **`goldenflow-native>=0.11.0`**
  (was `>=0.2.0`), per the lockstep policy. goldenflow 1.13.0 completes the
  owned-kernel + cross-surface migration of every byte-parity-achievable
  transform family (identifiers, names, email, url, numeric, categorical,
  address, the full text family, and fuzzy category_auto_correct); the
  goldenflow-native 0.11.0 wheel ships the matching compiled kernels.

## [0.1.2] - 2026-07-04

### Changed
- Bumped the two stale goldenflow-family floors to the latest PyPI releases:
  **`goldenflow>=1.4.0`** (was `>=1.3`) and **`goldenflow-native>=0.2.0`** (was
  `>=0.1.1`), per the lockstep policy (whenever a bundled member releases,
  golden-suite bumps its floor and re-cuts). Floors track the latest *published*
  member versions so `pip install golden-suite` stays satisfiable; the workspace
  carries newer unreleased goldenflow work whose floor can be mandated once it
  ships to PyPI.

## [0.1.1] - 2026-07-02

### Changed
- Bumped floors to mandate the latest member fixes: **`goldenmatch>=2.8`** (the B1
  silent Latin-1 data-corruption fix + the config-healer production-slowdown fix)
  and **`goldencheck>=1.4.1`** (rapidfuzz `cell_quality` perf). Lockstep policy:
  whenever a bundled member releases, golden-suite bumps its floor and re-cuts.

## [0.1.0] - 2026-07-02

Initial release. A one-line, perf-optimized install and a single canonical front
door for the whole Golden Suite.

### Added
- `pip install golden-suite` pulls the whole suite — `goldenpipe[golden-suite]`
  (orchestrator + check/flow/match/analysis), plus `goldenmatch`, `goldencheck`,
  `goldenflow`, `infermap` (GoldenSchema), `goldenanalysis`, `goldencheck-types`.
- **Native acceleration on by default.** The four native (Rust/abi3) kernels
  (`goldenmatch-native`, `goldencheck-native`, `goldenflow-native`,
  `goldenanalysis-native`) are **hard dependencies**, not an opt-in extra, so the
  suite defaults to the perf-optimized configuration and never silently runs the
  slow pure-Python path. Wheels cover Linux x86_64/aarch64, macOS x86_64/arm64,
  and Windows amd64; on an unsupported platform the install fails loudly by design.
- `golden-suite` CLI:
  - `doctor` — reports every component + version and whether each native kernel is
    actually active; exits non-zero when a package is silently on the pure-Python
    path (CI/verification-safe).
  - `optimize` — installs any missing native kernels for the current platform, then
    re-verifies. `--strict` additionally emits the require-native env vars
    (`<PKG>_NATIVE=1`), with a warning that strict mode force-runs components not
    yet parity-signed-off (notably goldenflow) and can change outputs.
- Introspection helpers: `golden_suite.installed()` (dist -> version|None) and
  `golden_suite.native_status()` (per-package `native_active` / `silently_slow` /
  `env_mode`).
- Optional extras: `[mcp]` (`goldensuite-mcp` — one server for every tool),
  `[agent]` (GoldenPipe tui/api/agent serving surfaces), and `[all]`.
- Integration guide for agents and humans: `AGENTS.md`, `llms.txt`, `README.md`.

### Notes
- Ships no data-processing logic of its own beyond the CLI + introspection helpers.
- Published on the `golden-suite-v*` release tag via `publish-golden-suite.yml`
  (distinct from the `goldensuite-mcp-v*` tag).
