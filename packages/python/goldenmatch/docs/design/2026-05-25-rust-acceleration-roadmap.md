# Rust acceleration roadmap

Date: 2026-05-25
Status: proposed (audit follow-up). No code yet — this is the plan.

## 1. Where we are

All Rust in the repo lives in `packages/rust/extensions/`:
- `bridge/` — a **pyo3 delegation layer**. Every function (`bridge/src/api.rs`)
  is `Python::with_gil { import goldenmatch; call_method(...); convert }`. It runs
  **zero** ER compute in Rust; it marshals JSON in/out and calls the Python package.
- `postgres/` — pgrx extension that wraps the bridge (embeds CPython in Postgres).
- `duckdb/` — Python UDFs (not Rust).

So the only fast numeric work today is fast because **rapidfuzz and polars are
themselves Rust** (`core/scorer.py` cdist, `core/blocker.py` group_by). "Fleshing
out Rust" therefore means *moving specific hot kernels into Rust*, not polishing
existing kernels.

## 2. Guiding principles (hard guardrails)

1. **Measure before rewriting.** Per the repo's own perf-audit lesson
   (`docs/superpowers/specs/2026-05-02-performance-audit-checklist.md`): "always
   measure wall-clock with the workload of interest; cProfile cumtime != wall."
   Each phase opens with a `core/bench.py` capture on representative shapes and
   does not proceed unless the candidate is the measured bottleneck.
2. **Behavior parity is non-negotiable.** Any kernel must produce identical
   clusters/scores to the Python path and hold the **DQbench composite >= 91.04**
   gate (the v1.12 ship bar). Land every kernel behind a flag, diff against the
   Python implementation, and only then consider flipping the default.
3. **Pure-Python fallback always works.** Native is an optional accelerator, not
   a dependency. Mirror the `[web]` extra pattern: if the native module isn't
   importable, the Python path runs unchanged. CI must test both paths.
4. **Reuse the existing Cargo setup** (`packages/rust/extensions/bridge`) rather
   than standing up a parallel toolchain.
5. **Don't reimplement what's already native.** rapidfuzz (string similarity) and
   polars (block-key construction) stay as-is. The opportunity is the *Python
   orchestration loops around them*, not the kernels.

## 3. Target module: `goldenmatch._native`

A maturin-built PyO3 extension shipped inside the `goldenmatch` wheel (optional;
absent => Python fallback). Python keeps orchestration thin and calls in — the
polars/rapidfuzz model. Selection via `GOLDENMATCH_NATIVE=1` during rollout,
default-on only after the parity + DQbench gates pass.

## 4. Phasing

### Phase 0 — Baseline + scaffold (no behavior change)
- **Goal:** establish the measurement baseline and the build/packaging skeleton.
- **Do:** capture `core/bench.py` stage timings for the three candidates at
  100K / 1M / (if a box allows) 5M on the bucket path; stand up the
  `goldenmatch._native` maturin crate + an empty `GOLDENMATCH_NATIVE` switch + a
  CI lane that builds the wheel with the native ext AND runs the suite without it.
- **Exit:** documented per-stage wall baseline; native wheel builds in CI; the
  Python-only path is byte-identical to today.

### Phase 1 — Clustering kernel (recommended first real kernel)
- **Why first:** self-contained, used by *every* dedupe, and the largest single
  stage at scale (cluster = 126.6s at 25M). `core/cluster.py` Union-Find, MST
  (`split_oversized_cluster`), `transitivity_rate`, and bridge detection
  (`_severe_bridge_count`, O(E*(V+E)) BFS/cluster) are all pure-Python graph loops.
- **Scope:** Rust connected-components + Union-Find + MST + bridge/articulation
  detection, PyO3-exposed; `build_clusters` calls in when `_native` present.
  Inputs/outputs are plain `(id, id, score)` edges and the existing cluster dict
  shape — narrow, easily diffable surface.
- **Gate:** identical cluster membership + `cluster_quality` + bridge signals on a
  battery of fixtures; bench shows a real cluster-stage drop; DQbench unchanged.
- **Risk:** low — pure algorithm, no scoring semantics, deterministic.

### Phase 2 — Block-scoring orchestration kernel (biggest perf prize)
- **Why:** the headline bottleneck — `bucket_score` was **42 of 53.7 wall-min at
  5M**, "per-block Python orchestration over 1.67M blocks" (`core/pipeline.py`
  bucket path + `core/scorer.py::score_blocks_parallel`). The cdist is native; the
  per-block frame-build -> dispatch -> `.collect()` loop is what costs.
- **Scope:** a Rust kernel that owns the block loop, **batches the many tiny
  blocks into few rapidfuzz-rs calls** (the deferred "Track 1 Fix B"), and uses
  `rayon` for GIL-free parallelism. Emits the same scored-pair stream the Python
  path produces.
- **Gate:** identical scored pairs (within float tolerance) on fixtures; bench
  shows the bucket stage shrinking; DQbench unchanged. Aligns with the project's
  own direction ("the 5M-on-one-node bucket pipeline IS the recommended path";
  Ray was soft-reverted on RSS).
- **Risk:** medium-high — touches the hottest path and scoring orchestration;
  must preserve early-termination + negative-evidence + threshold semantics.

### Phase 3 — Arrow bridge (quality + perf at the SQL boundary)
- **Why:** the extensions bridge marshals via JSON strings. `convert.rs` already
  flags "future: Arrow C Data Interface," and `api.rs` carries correctness hacks
  forced by JSON: `json.dumps` *fails* on tuple-keyed `pair_scores` and falls back
  to a `str()` repr (api.rs ~225-232); everything else is coerced via
  `default=str`. Arrow zero-copy is faster **and** removes that lossiness.
- **Scope:** replace `json_to_polars_df` / `polars_df_to_json` with Arrow C Data
  Interface transfer in `bridge/src/convert.rs`; structured (non-JSON) return of
  clusters/pairs.
- **Gate:** identical SQL-surface outputs on the DuckDB + pgrx parity tests; lower
  per-call overhead measured on a representative table.
- **Risk:** medium — FFI boundary work; isolated from algorithms.

### Phase 4 — Research / longer-horizon (only if measured)
- **Fellegi-Sunter kernel:** `core/probabilistic.py` `comparison_vector` + EM loop
  are per-pair pure Python (`O(n_pairs*n_fields*iters)`, no numpy). A Rust kernel
  would make the high-precision path viable at scale. Opt-in feature today, so
  lower priority than 1-2.
- **Native ER core to decouple SQL extensions from embedded CPython:** the long
  game. Once Phases 1-2 exist as native kernels, the Postgres/DuckDB extensions
  could call them directly instead of embedding a GIL-bound, version-pinned
  interpreter (the extensions CLAUDE.md documents the CPython-in-PG fragility:
  `typing_extensions` clashes, pyo3 version coupling, fail-soft JSON wrappers).
  Large; gated on Phases 1-3 proving the kernels.

## 5. Cross-cutting

- **Packaging:** maturin; native ext optional in the wheel; `importlib`-guarded
  fallback so plain `pip install goldenmatch` keeps working without a Rust
  toolchain at install time.
- **CI:** add a `cargo build`/`clippy`/`test` lane for `_native`; add a
  native-vs-Python **parity test** that runs the same fixtures both ways and
  asserts equal output; keep the existing Python-only suite green.
- **Determinism:** Rust kernels must be deterministic given inputs (seeded where
  randomness exists) so cluster/score diffs are exact, not flaky.

## 6. Non-goals
- Reimplementing rapidfuzz string scorers or polars block-key construction.
- Flipping any default to the native path before the parity + DQbench gates pass.
- A from-scratch distributed engine (Ray path already explored + soft-reverted;
  single-node native kernels are the cheaper, proven-direction lever).

## 7. Suggested order of execution
Phase 0 (baseline + scaffold) -> Phase 1 (clustering: best risk/reward) ->
Phase 2 (block-scoring: biggest prize) -> Phase 3 (Arrow boundary) ->
Phase 4 (research). Each is independently shippable and default-safe.
