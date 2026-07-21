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

## Agent surfaces (MCP / A2A / CLI) — deferred by design
GoldenGraph ships **no MCP server, no A2A AgentCard, and no CLI** today, and is
therefore **not in the `api_parity` gate** (no `parity/goldengraph.yaml`; absent
from `scripts/emit_ts_surface.mjs`). This is a *sequenced* deferral, not an
oversight — verified 2026-07-07:
- No `goldengraph/mcp/` module, no `server.json`, no MCP/FastMCP dep, no
  `[project.scripts]` entry point, no `Dockerfile.mcp` / `railway*.json` deploy
  scaffold. Not in `publish-mcp.yml`, the MCP Registry, or Smithery.
- Not one of the 4 A2A packages (goldenmatch/goldencheck/goldenflow/goldenpipe);
  the TS package is edge/WASM-only (no `src/node/` agent surface).
- **Rollout condition:** `docs/superpowers/specs/2026-06-20-goldengraph-sp4b-pipeline-design.md`
  lists "Publishing `goldengraph` to PyPI / MCP roster" as a later rollout "once
  the pipeline is real". GoldenGraph is pre-1.0 (`v0.1.0`), not yet on PyPI, and
  still building out the extract→resolve→store→answer pipeline (SP4b/SP4c).

When that bar is hit, stand up the agent surfaces together: a `goldengraph/mcp/`
FastMCP server (`build`/`ask`/`neighborhood`/`communities`) + `server.json` with
the first-line `mcp-name:` marker (mirror the infermap MCP layout), then add
`parity/goldengraph.yaml` + the `emit_ts_surface.mjs` roster entry so MCP/CLI/A2A
stay Python↔TS in lockstep. (Do NOT wire `api_parity` before a real surface
exists — the gate has nothing to compare.)

## Single gated engine entry point (2026-07-20)
Both runtime call sites now go through `goldengraph.core._native_loader` instead
of hard-importing the wheel ad-hoc, so the `GOLDENGRAPH_NATIVE=0/1` contract
governs the WHOLE engine, not just the JSON parity surface:
- `graph.py::_new_store` → `_native_loader.new_store()` (builds `PyStore` via
  `native_module()`; also picks up the in-tree build, which the old
  `from goldengraph_native import _native` never did). The test `store` fixture
  (`tests/conftest.py`) goes through it too.
- `profile.py::_engine` → `_native_loader.profile_resolve_json()` (the separate
  `goldenprofile-native` wheel, still lazily imported so importing the loader
  never requires it — but now under the same gate).
Both loader entry points raise a clear, actionable error on `=0` (force-disable:
no pure-Python fallback exists) and on a missing/unbuilt engine, rather than an
opaque `ImportError` at the call site. Gate logic is unit-tested wheel-free in
`tests/test_native_loader.py` (loads the loader by file path to dodge
`goldengraph/__init__`'s heavy deps, mirroring `test_native_parity`).

## Template-free NL multi-hop routing (2026-07-21)
`trace_chain` (answer.py) is the deterministic, LLM-free multi-hop walk, but it
only fired when a question matched the engineered `_CHAIN_RE` template ("Starting
from X, follow the relation R1, then R2."). Real questions ("Who is married to the
person who directed Inception?") fell through to LLM synthesis over the retrieved
ball — the diagnosed #1 answer-quality gap (a ball that CONTAINS the chain,
bridge-recall ~1.0, still answered only ~0.275; synthesis-given-gold-chain is 1.0,
so the loss is path-selection, not reasoning; the two cheap fixes — topology prune,
query-name embedding — were already refuted, see `results/RESULTS_PATH_AWARE_RETRIEVAL.md`).
- **`route._extract_nl_chain_slots`** recovers `(anchor, relation_chain)` from free
  NL, grounded in the slice's own vocab: anchor = the longest stored ENTITY NAME in
  the question; relations = PREDICATE ids whose salient token appears (bridged to
  the question's noun form by a `_stem_match` shared-≥5-char-stem rule, so
  "director"→"directed_by", "location"→"located_in"). Wired into `classify_query`
  for MULTI_HOP **and** LOOKUP intents (needs `entity_names`, threaded from
  `answer._slice_entity_names`); when the vocab is absent it's a no-op (back-compat).
- **Multiplicity, not a set:** each content word maps to one predicate occurrence,
  so a repeated relation ("the employer of the employer of X") yields a repeated
  hop. Dedup-to-set was the whole accuracy gap (repeat-relation chains 0%→97.6%).
- **Order is a HINT, the graph validates it:** the extracted order is proximity-to-
  anchor; `answer._trace_chain_any_order` tries permutations and accepts the first
  that completes (only the true order has a matching first edge in the ≤1-edge-per-
  (entity,relation) graph). `QueryProfile.chain_ordered` distinguishes the
  authoritative template order (single walk) from the NL hint (permute).
- **Conservative by construction — never worse than status quo:** fires only with a
  grounded anchor + ≥1 grounded predicate; a COMPLETENESS GUARD abstains when an
  unmapped content word sits before an "of"/"by" relation marker (an ungrounded hop
  like the pure synonym "spouse"→"married_to"), because a truncated chain would
  complete early and return a WRONG intermediate node (the None-fallthrough can't
  catch that). Abstaining routes to today's retrieval+synthesis path. Pure noun
  synonyms with no shared stem are the documented boundary (needs the embedding /
  `LLMQueryClassifier` tier).
- **Measured (engineered corpus, gold chains, StubGraph, LLM-FREE):** NL phrasings
  reach **96.8%** answer accuracy — parity with the engineered-template ceiling
  (96.8%) — up from **0%** LLM-free before (they fell to ~0.15–0.28 paid synthesis).
  By hop: 2-hop 100%, 3-hop 93%, 4-hop 97%. Tests: `tests/test_nl_chain.py`
  (wheel-free; fires, safe abstentions, ordering, multiplicity, end-to-end `ask`).
