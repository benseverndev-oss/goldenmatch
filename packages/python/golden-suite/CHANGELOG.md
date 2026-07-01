# Changelog

All notable changes to golden-suite are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/); this project uses semantic
versioning.

## [0.1.0] - unreleased

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
