# GoldenGraph — Claude notes

Own-your-KG knowledge-graph engine: LLM extraction → goldenmatch entity
resolution → a durable bi-temporal store. The compute primitives (build_graph /
neighborhood / seeds_by_name / communities + the bi-temporal store) live in the
pyo3-free Rust `goldengraph-core` crate; the Python package is the orchestration
layer (extract / embed / route / answer / synthesize) on top.

## Workspace posture
- **EXCLUDED from the uv workspace** (root `pyproject.toml` `[tool.uv.workspace].exclude`) — it depends on the maturin-built `goldengraph-native` engine wheel + optional LLM extras. Standalone; its full suite runs in `.github/workflows/goldengraph-pipeline.yml`.
- Its Rust surfaces are still gated in the root `ci.yml`: the `goldengraph_wasm` lane (edge/WASM drift guard) and the `goldengraph_native` lane (the engine parity gate, in `ci-required`).

## Rust is the reference — cross-surface parity (2026-07-07)
GoldenGraph is **native-authoritative**: `goldengraph-core` is the reference impl,
and the SAME kernel runs on every surface over one shared **JSON boundary**
(`(json, args...) -> json`) — so all surfaces are byte-identical by construction:
- **Python native** — `goldengraph-native` (pyo3). Exposes the ergonomic
  `PyGraph`/`PyStore` pyclasses **and** the 7 JSON-boundary `wrap_pyfunction!`
  symbols (`build_graph_json`, `neighborhood_json`, `seeds_by_name_json`,
  `communities_json`, `store_append_json`, `store_as_of_json`,
  `store_history_json`) that mirror the wasm `*_impl` exactly.
- **Edge / TS / WASM** — `goldengraph-wasm` + `packages/typescript/goldengraph`.
- **C-ABI** — `goldengraph-cabi` (`gg_abi_version` + `no_mangle` externs).

### The gate + the single-oracle fixture
- `goldengraph/core/_native_loader.py` is the `GOLDENGRAPH_NATIVE` gate (`auto`/`0`/`1`, `_has_symbol`, discovery `goldengraph._native` → `goldengraph_native._native` → None) — mirrors the sibling loaders. `native_enabled(component)` reads `_COMPONENT_SYMBOLS` (the 7 JSON symbols).
- **No pure-Python fallback for these primitives** — the store/resolution engine is Rust-only. `GOLDENGRAPH_NATIVE=0` force-disables (callers with no fallback raise a clear error rather than silently degrade); `=1` requires native (CI parity lane).
- **One oracle, no second drift surface:** `packages/typescript/goldengraph/tests/parity/fixtures/goldengraph/queries.json` (9 cases, all 7 ops) is generated from the host boundary by `goldengraph-wasm/examples/gen_parity_fixtures.rs` and drift-guarded by the `fixture_drift` CI job. Both the TS parity test (`goldengraph-wasm.parity.test.ts`) and the Python parity test (`tests/test_native_parity.py`) read **that same file** — the Python test anchors to it via `Path(__file__).parents[4]` so it resolves from either CWD. Do NOT copy the fixture into the Python package (that would be a second thing to drift).

### `goldengraph_native` CI lane (in `ci-required`)
Builds the ext via `scripts/build_goldengraph_native.py` (cargo `--release` →
`goldengraph/_native.abi3.so`, gitignored) + `cargo clippy`/`test` on the core,
then runs the parity suite with `GOLDENGRAPH_NATIVE=1`. Because the engine is
native-only, this lane is the ONLY correctness signal for the store/resolution
path — hence it is a **blocking** gate, unlike the advisory `infermap_native` /
`analysis_native` lanes.

### Gotchas
- The abi3 init symbol is `PyInit__native` (pymodule name `_native`), so a
  file-path import MUST load the `.so` under the module name `_native` (the parity
  test does this to bypass `goldengraph/__init__` and its heavy deps like numpy).
- `goldengraph-native` is a **standalone cargo workspace** (empty `[workspace]`) —
  the `rust` job's `cargo test --workspace` never builds it; the `goldengraph_native`
  lane is what compiles + parity-checks it.
- Graph entity/edge ordering can fall out of hash-map order, so parity compares
  canonicalize both sides (entities by id, edges by subj/pred/obj, members/
  surface_names/source_refs sorted). Store snapshots ARE deterministic (compared raw).

### Follow-up (not done here)
Runtime call sites still hard-import the engine ad-hoc rather than through the new
loader: `graph.py::_new_store` (`from goldengraph_native import _native`) and
`profile.py` resolution (`goldenprofile_native.resolve_json`). Migrate them to
`goldengraph.core._native_loader.native_module()` so there is one gated entry
point (and the `GOLDENGRAPH_NATIVE=0/1` contract governs the whole engine, not
just the JSON parity surface).
