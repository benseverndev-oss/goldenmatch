# Changelog

All notable changes to golden-suite are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/); this project uses semantic
versioning.

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
