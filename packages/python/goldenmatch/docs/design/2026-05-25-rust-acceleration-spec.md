# Rust acceleration — technical spec

Date: 2026-05-25
Status: **Phase 0 + Phase 1 implemented**; Phase 2 environment-blocked here;
Phase 3 buildable but unverified; Phase 4 research. Companion to
`2026-05-25-rust-acceleration-roadmap.md` (the roadmap is the "what/when"; this is
the "how" — concrete module layout, APIs, parity-test design, packaging, and
per-phase acceptance criteria).

All `file:line` anchors are against the tree at the time of writing.

## Implementation status (2026-05-25)

- **Phase 0 — done.** `goldenmatch._native` crate at
  `packages/rust/extensions/native` (standalone workspace, pyo3 abi3-py311,
  `cdylib`); `core/_native_loader.py` gate (`GOLDENMATCH_NATIVE` 0/1/auto,
  default Python); built via `scripts/build_native.py` (atomic install of
  `goldenmatch/_native.abi3.so`, which is gitignored). NOTE: the build is a
  script, **not** a registered hatch hook — registering a hook runs on every
  build of the live PyPI package, so it's deferred until validated in CI to avoid
  risking `pip install goldenmatch`.
- **Phase 1 — done (3 per-cluster/per-edge hot kernels), parity-green.**
  `connected_components` (Union-Find), `severe_bridge_count`, and
  `cluster_confidence` are implemented in Rust (`native/src/cluster.rs`) and wired
  into `core/cluster.py` (`build_clusters`, `_severe_bridge_count`,
  `compute_cluster_confidence`) behind the gate. `tests/test_native_parity.py`
  asserts identical output Python-vs-native (18 cases incl. float parity within
  1e-12 + bottleneck-pair tie-break). **`transitivity_rate` and `mst_split` are
  deliberately kept in Python**, each for a concrete reason, not just triage:
  `transitivity_rate` samples triples via `random.Random(seed).sample(...)` (any
  cluster >20 members, or >1000 total triples — a single 20-member cluster already
  exceeds that at C(20,3)=1140), so its output depends on CPython's Mersenne
  Twister sequence and **cannot be reproduced bit-for-bit in Rust** without
  reimplementing CPython's RNG — a native port would fail the parity gate. It's
  also once-per-profile, not a per-cluster hot loop. `mst_split` is the rare
  oversized-split exception path with an entangled dict rebuild. Default stays
  Python (`_GATED_ON` empty) pending a DQbench run.
- **Phase 2 — blocked in this environment.** `rapidfuzz`/`rayon` crates aren't in
  the offline cargo cache and there's no crates.io network here, so the
  block-scorer kernel can't be built/validated. Design below stands; needs a
  network-enabled build env.
- **Phase 3 — buildable, unverified.** The Arrow helpers already exist in
  `bridge/src/convert.rs`; the api.rs rewiring builds offline (pyo3 cached) but
  full parity needs the DuckDB/pgrx layers (Postgres is CI-Linux-only). Not done.
- **Phase 4 — research, not started.**

## 0. Module & build architecture

### 0.1 Where the Rust lives
Add a `native` crate to the existing Cargo workspace at
`packages/rust/extensions/` (next to `bridge/`). It is a **PyO3 cdylib** built
with `abi3-py311` (one stable-ABI wheel covers 3.11–3.13). It is independent of
the `bridge`/`postgres` crates (those embed CPython; this one *is* imported by
CPython — opposite direction).

```
packages/rust/extensions/native/
  Cargo.toml          # crate-type = ["cdylib"], pyo3 abi3-py311, rayon, rapidfuzz
  src/lib.rs          # #[pymodule] goldenmatch._native — registers submodules
  src/cluster.rs      # Phase 1
  src/blockscore.rs   # Phase 2
```

### 0.2 Build integration (hatchling, not maturin)
The package builds with `hatchling` (`pyproject.toml:2-3`). Options, in
preference order:
1. **Custom hatch build hook** (`hatch_build.py`) that shells `cargo build
   --release -p goldenmatch-native` and copies the artifact to
   `goldenmatch/_native.<abi3-suffix>.so`. Keeps one build backend.
2. Maturin as a *separate* build producing a platform wheel that's merged — more
   moving parts; only if the hook proves painful.

Wheels: publish per-platform binary wheels (manylinux/macos/windows) with the
compiled ext; the **sdist stays pure-Python-installable** (no Rust toolchain at
install time) and the ext is simply absent there. This mirrors the optional
`[web]` extra discipline (`pyproject.toml:53`, and the web static carve-out in
`.gitignore`): the accelerator is optional, never a hard dep.

### 0.3 Runtime selection
New `goldenmatch/core/_native_loader.py`:
```python
import os
try:
    import goldenmatch._native as _native
except ImportError:
    _native = None

def native_enabled() -> bool:
    mode = os.environ.get("GOLDENMATCH_NATIVE", "auto").lower()
    if mode == "0":
        return False
    if mode == "1":              # require it; raise if missing (CI parity lane)
        if _native is None:
            raise RuntimeError("GOLDENMATCH_NATIVE=1 but goldenmatch._native not built")
        return True
    return _native is not None   # "auto": use iff importable
```
**Default during rollout is `auto`, but every call site keeps the Python branch
as the source of truth until that phase's parity + DQbench gates pass** — i.e.
we ship the ext able to run, flip the per-phase default only after gating.

### 0.4 Determinism contract
The kernels must produce output the Python post-processing turns into
*byte-identical* results. The one ordering subtlety: `build_clusters` re-sorts
clusters by `min(member)` after components are found (`core/cluster.py:280+`), so
the kernel's component *order* is irrelevant to final cluster IDs — but member
lists and tie-breaks (MST weakest-edge, bridge counts) must match exactly. Use
stable sorts and the same tie-break rules as Python.

## 1. Phase 0 — baseline + scaffold (no behavior change)
- Capture `core/bench.py` stage timings (`with bench_capture()`) for the three
  candidates at 100K / 1M / (where a box allows) 5M on `backend="bucket"`.
  Record `cluster`, `fuzzy_scoring`/`bucket_score`, and (FS) timings.
- Land the `native` crate (empty `#[pymodule]`), `_native_loader.py`, the hatch
  build hook, and a CI lane that (a) builds the wheel with the ext and (b) runs
  the suite with `GOLDENMATCH_NATIVE=0`.
- **Exit:** documented baseline; native wheel builds in CI; Python-only path
  byte-identical to today.

## 2. Phase 1 — clustering kernel (first real kernel)

### 2.1 Rust surface (`src/cluster.rs`) — as implemented
```rust
#[pyfunction] fn connected_components(edges: Vec<(i64,i64,f64)>, all_ids: Vec<i64>) -> Vec<Vec<i64>>
#[pyfunction] fn severe_bridge_count(members: Vec<i64>, edges: Vec<(i64,i64,f64)>) -> usize
#[pyfunction] fn cluster_confidence(edges: Vec<(i64,i64,f64)>, size: usize)
                  -> (Option<f64>, Option<f64>, f64, Option<(i64,i64)>, f64)  // min_edge, avg_edge, connectivity, bottleneck_pair, confidence
```
Marshalling is trivial primitives (`(i64,i64,f64)` + `i64`); no Arrow needed.

**Not ported (by design):** `transitivity_rate` samples triples via
`random.Random(seed).sample(...)`, so its result is tied to CPython's Mersenne
Twister stream — unreproducible in Rust without reimplementing the RNG, so a port
would fail the parity gate (and it's once-per-profile anyway). `mst_split` is the
rare oversized-split exception path whose Python form returns rebuilt sub-cluster
dicts (members + sliced pair_scores + recomputed confidence); the dict-rebuild
entanglement isn't worth the parity surface. Both stay Python.

### 2.2 Python integration (`core/cluster.py`)
`build_clusters(pairs, all_ids=None, max_cluster_size=100, weak_cluster_threshold=0.3, auto_split=True) -> dict[int, dict]`
keeps its **exact signature and return shape** (`members, size, oversized,
pair_scores, confidence, bottleneck_pair, cluster_quality`). Internally, when
`native_enabled("clustering")` (each guarded by the gate so the Python branch is
the source of truth):
- the `UnionFind` + `get_clusters()` block (`cluster.py:323-332`) calls
  `_native.connected_components(...)`;
- `compute_cluster_confidence` (`cluster.py:413`) delegates to
  `_native.cluster_confidence`, then wraps the tuple back into the dict;
- `_severe_bridge_count` (`cluster.py:168`) delegates to
  `_native.severe_bridge_count`.

The dict assembly + `cluster_quality` assignment stays in Python (cheap, preserves
the contract). `_emit_cluster_profile` reads the same values.

### 2.3 Parity test (`tests/test_native_parity.py`)
`@pytest.mark.skipif(_native is None)`. For a battery of fixtures — singletons,
2-node, 3-chain, 4-node bridge, clique, oversized (> max_size), weak-edge — run
`build_clusters` with `GOLDENMATCH_NATIVE=0` then `=1` (monkeypatch env +
reload-safe gate) and assert: identical member sets per cluster id, identical
`cluster_quality`, `confidence`/`bottleneck_pair` within 1e-9, identical
`bridge_edge_count`/`measured_bridge_risk`.

### 2.4 Acceptance
Parity green; `core/bench.py` shows a real `cluster`-stage wall drop (baseline was
126.6s at 25M); DQbench composite unchanged (>= 91.04). **Default flips to native
for clustering only after all three hold.**

## 3. Phase 2 — block-scoring orchestration (biggest prize)

### 3.1 Target
`goldenmatch/backends/score_buckets.py::score_buckets(prepared_df, blocking_config,
mk, matched_pairs, n_buckets=None, across_files_only=False, source_lookup=None,
target_ids=None) -> list[tuple[int,int,float]]` (`score_buckets.py:157`), invoked
from `pipeline.py:953-960` on `backend="bucket"`. This is the 42-of-53.7-min-at-5M
bottleneck ("per-block Python orchestration over 1.67M blocks").

### 3.2 Rust surface (`src/blockscore.rs`)
```rust
#[pyfunction] fn score_buckets_native(
    prepared_ipc: &[u8],          // Arrow IPC of prepared_df (zero-copy via convert helpers)
    spec: ScorerSpec,             // matchkey fields: (column, scorer, weight, threshold, transforms), NE fields
    matched_pairs: Vec<(i64,i64)>,
    n_buckets: usize,
    mode: ScoreMode,              // dedupe | across_files_only(source_lookup) | match(target_ids)
) -> Vec<(i64,i64,f64)>
```
Internals: derive `__block_key__` + `__bucket__` in one pass, `partition_by`
bucket→block, **batch many small blocks into few `rapidfuzz`-rs cdist calls**
(the deferred "Track 1 Fix B"), apply per-field threshold + negative-evidence +
intra-field early-termination, exclude `matched_pairs`, emit canonical
`(min,max)` pairs. Parallelize blocks with `rayon` (no GIL).

### 3.3 Scorer coverage + incremental fallback
Port only the text scorers that have rapidfuzz-rs equivalents:
`jaro_winkler, token_sort, ensemble, levenshtein, soundex_match`. **Explicitly
NOT** `dice`/`jaccard` (PPRL bloom-filter scorers — hex CLK input, see #491) or
`embedding`/`record_embedding` (model bootstrap). The kernel handles a block only
when `mk`'s scorers are all ported; otherwise that matchkey routes to the existing
Python `score_buckets`. This makes Phase 2 incremental and always-safe.

### 3.4 Semantics that MUST be preserved (parity-critical)
Canonical `(min,max)` pair keys; `matched_pairs` exclusion; intra-field early
termination; negative-evidence penalties (`final = max(0, 1 - sum(penalties))`);
the across-files (`source_lookup`) and match (`target_ids`) modes; identical
float results within 1e-9 of the Python `_score_one_block`/`_fuzzy_score_matrix`
(`scorer.py:300`).

### 3.5 Acceptance
Identical scored-pair stream on fixtures (both paths); `core/bench.py` shows the
bucket/`fuzzy_scoring` stage shrinking at 1M and 5M; DQbench unchanged. Sequenced
**after** Phase 1 so the kernel + parity + packaging machinery is already proven.

## 4. Phase 3 — Arrow bridge (quality + perf at the SQL boundary)

### 4.1 The win is mostly wiring — the Arrow primitives already exist
`bridge/src/convert.rs` already implements `polars_df_to_arrow_ipc` /
`arrow_ipc_to_polars_df` (convert.rs:36-56) with passing round-trip tests — but
`api.rs` still calls the JSON path (`json_to_polars_df` / `polars_df_to_json`).

### 4.2 Scope
Switch the data-carrying `api.rs` functions (`dedupe`, `dedupe_full`,
`match_tables`, `dedupe_pairs`, `dedupe_clusters`) from JSON to the Arrow IPC
helpers, and return **structured** pairs/clusters rather than JSON. This deletes
two correctness hazards forced by JSON today:
- the tuple-keyed `pair_scores` `json.dumps` failure → `str()`-repr fallback
  (`api.rs:225-232`, `:293-299`);
- the `default=str` numeric/date coercion across the surface.

The pgrx (`postgres/src/*`) and DuckDB (`duckdb/`) layers keep their SQL contract
identical — Arrow internally, same SQL tuples out.

### 4.3 Acceptance
DuckDB + pgrx parity test-suites produce identical SQL outputs; measured per-call
overhead drop on a representative table; the JSON tuple-key fallback path is gone.

## 5. Phase 4 — research (only if measured)
- **Fellegi-Sunter kernel:** `core/probabilistic.py` `comparison_vector` + EM loop
  (per-pair pure Python, no numpy). Port to Rust if FS adoption/scale warrants.
- **Native ER core:** once Phases 1–2 are native, let the pgrx/DuckDB extensions
  call the kernels directly instead of embedding CPython (removes the GIL-bound,
  version-pinned interpreter and the fail-soft JSON wrappers). Large; gated on 1–3.

## 6. Cross-cutting

| Concern | Decision |
|---|---|
| ABI | `abi3-py311` — one wheel per platform spans 3.11–3.13 |
| Parallelism | `rayon` in kernels; release the GIL around kernel calls |
| Fallback | `GOLDENMATCH_NATIVE=0` instantly reverts to Python; default stays Python per phase until gated |
| CI | add `cargo build`/`clippy`/`test` for `native`; a parity lane that builds the ext and runs `test_native_parity` with `GOLDENMATCH_NATIVE=1`; keep the Python-only suite green |
| Versioning | kernel output is an internal contract; bump only on behavior change, never silently |

## 7. Risk / effort summary

| Phase | Effort | Risk | Why |
|---|---|---|---|
| 0 scaffold | S | low | no behavior change |
| 1 clustering | M | low | pure algorithm, narrow primitive I/O, deterministic |
| 2 block-scoring | L | med-high | hottest path; must replicate scoring semantics exactly |
| 3 Arrow bridge | M | med | FFI wiring; Arrow helpers already exist; isolated from algorithms |
| 4 research | XL | — | gated on 1–3 proving out |

## 8. Open questions
- hatch build hook vs maturin sub-wheel for the compiled ext (0.2) — prototype in Phase 0.
- vendor `rapidfuzz-rs` vs bind the C lib for Phase 2 scorer parity (must match Python rapidfuzz outputs within tolerance — validate in the Phase 2 parity test).
- whether `connected_components` should also subsume the distributed path's driver-side union (out of scope here; in-memory only).
