# Changelog

All notable changes to `goldenprofile` are documented here.

## [0.1.0] - 2026-06-28

Initial release: edge-safe TypeScript surface for the GoldenProfile Virtual
Fingerprint engine (cross-document entity resolution).

### Added
- `resolveProfiles(request)` — resolve profile mentions into cross-document
  entities, returning the cluster partition + scored merge edges.
- Opt-in `goldenprofile/wasm` subpath (`enableGoldenprofileWasm()`) that loads
  the same pyo3-free Rust kernel (`goldenprofile-core`) the Python and C bindings
  use, via WebAssembly. The base import pulls zero wasm bytes and stays edge-safe.
- Refusing contract: `resolveProfiles()` throws an actionable error until the
  wasm backend is enabled (never returns a silently-wrong empty resolution).
- Cross-surface parity tests (WASM vs Python-native, over one canonical fixture)
  and a scale bench (`scripts/bench_goldenprofile_wasm.mjs`).
