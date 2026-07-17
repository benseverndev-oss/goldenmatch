# FS scoring as a shared `fs-core` — cross-surface source of truth

**Date:** 2026-07-17
**Status:** in progress. Landed + verified (against a locally rebuilt native
`.so`): increments 1–2 (leaf math + per-pair scoring in `fs-core`), 3a (`fs-wasm`
crate), **4 (name scorers: fs-core leaf fns + `score_fs_pair` dispatch + native
pyo3 marshaling via a process-level census/alias registry + `probabilistic.py`
gating)**, **5 (Winkler `tf_adjustment` end-to-end)**, **6 (`ensemble` scorer +
a jellyfish-compatible `soundex`)**. Every FS comparison scorer the probabilistic
path can emit now runs on the native kernel; a new end-to-end test asserts
native == numpy (abs 1e-6) across name/ensemble/tf. Remaining: 3b (TS-wasm
rewiring — wasm-toolchain gated) and 7 (delete the numpy/scalar production paths).
**Owner:** benchmark-failure follow-up (`claude/benchmark-failure-gh-vbtusq`)

## Problem: Fellegi-Sunter is the repo's parity orphan

Every other kernel in the repo follows the established triad — pyo3-free `*-core`
(the one canonical implementation) consumed by `*-native` (Python via pyo3) and
`*-wasm` (TS via wasm-bindgen): `analysis-core`, `infermap-core`,
`goldencheck-core`, `fingerprint-core`, `sketch-core`, `perceptual-core`,
`goldenhnsw`, …

FS block scoring does **not**. Today it has **three independent implementations**:

- **Python** — `core/probabilistic.py` carries a numpy-vectorized path
  (`score_probabilistic_vectorized` + `_batch` + `_blocks_batched`, ~370 LOC)
  **and** a scalar path (`score_probabilistic`, ~110 LOC).
- **Rust (native only)** — `packages/rust/extensions/native/src/score.rs`
  (`score_block_pairs_fs` + banding/normalization/emit). This lives in the
  **Python-only pyo3 crate**, not a shared core.
- **TypeScript** — a hand-written FS in `packages/typescript/goldenmatch/src/core/probabilistic.ts`.

`score-core` (shared) owns only *per-string* similarity (`jaro_winkler`,
`levenshtein`, `token_sort`); `score-wasm` exposes only `score_matrix`. The FS
block-scoring *math* (level banding, weight accumulation, min-max/posterior
normalization, threshold emit) is duplicated three ways and kept in parity by
hand + fixtures — the worst possible parity posture, and the direct cause of the
recent divergences (a memory guard landing on numpy but not native; numpy OOMing
where native completes).

## Goal

Rust/Arrow-native is the **source of truth** for FS. Extract the FS scoring math
into a new pyo3-free crate **`goldenmatch-fs-core`**, consumed by:

- `goldenmatch-native` (Python) via pyo3 — replaces the numpy + scalar paths.
- `fs-wasm` (TS) via wasm-bindgen — replaces `probabilistic.ts`.

Parity becomes **by construction** (one implementation), matching the rest of the
repo. Net: three FS implementations collapse to one shared core + thin bindings.

## Reference data — never in the crate

Two flagship scorers carry bundled reference data (measured, tiny):

| asset | size | scorer |
|---|---|---|
| `census_surnames_2010_top10k.csv` | 176K (already top-10k) | `name_freq_weighted_jw` |
| `given_name_aliases.json` | 8K | `given_name_aliased_jw` |

Design rules so the crate/WASM binary never bloat:

1. **`fs-core` carries zero data — ever.** The name scorers take the tables as
   *inputs* (a frequency lookup + an alias-equivalence structure), via a small
   provider type. No `include_bytes!`. Crate + WASM binary stay pure logic.
   ("Kernel owns the math, host owns the data" — the same shape the kernel
   already uses for `weights`/`freqs`, hoisted to a process-level table.)
2. **One canonical data file, checksum-gated per surface.** The data lives in a
   single canonical location; Python loads it (as today) and hands it across
   pyo3; TS ships the identical file as an npm asset and hands it to `fs-wasm`.
   A checksum/codegen gate asserts the copies are byte-identical → **data parity
   by construction**, alongside the logic parity from the shared core.
3. **Optional + lazy per surface.** The scorers already degrade to plain
   Jaro-Winkler when the table is absent, so a minimal/edge WASM build ships
   without the ~50K asset (not compiled in, not force-loaded) and only pays for
   it on opt-in. Python's wheel keeps carrying it (noise next to pyarrow).
4. **No per-call cost.** Build the frequency/alias index once per process; inject
   a handle; reuse across every block-scoring call.

## Carve-out: embedding

`embedding` / `record_embedding` FS scorers are **not** ported into the kernel.
The expensive part (model inference) is irreducibly host-side (Python torch /
JS); all that remains is cosine on precomputed L2-normalized vectors (a dot
product). Porting a dot product into Rust buys nothing and adds a vector-marshal
FFI path. Embedding stays a thin Python path (or is dropped as an FS-comparison
scorer). Note: the *probabilistic* auto-config builder never emits embedding
scorers, so this is explicit-config only.

## Sequencing (one verifiable increment each, parity-gated)

1. **`fs-core` extraction — leaf logic (this increment).** Create the crate
   (standalone `[workspace]`, mirrors `score-core`), move the two pure leaf
   functions `fs_normalize` + `fs_level_from_sim` out of `native/src/score.rs`
   into `fs-core`, re-export from `score.rs` so all call sites are unchanged.
   Parity by construction (same functions relocated). Build + FS tests green.
2. **Move the block-scoring loop into `fs-core`.** The pyo3 `score_block_pairs_fs`
   wrapper in `native` becomes thin marshaling (Arrow/PyList → slices → fs-core →
   pairs). Native output byte-unchanged.
3. **`fs-wasm` + retire `probabilistic.ts`.** wasm-bindgen binding over `fs-core`;
   TS FS becomes `fs-wasm == native` by construction; migrate the TS parity
   harness from fixture-mirror to shared-core.
   - **3a (landed):** the `fs-wasm` crate — standalone `[workspace]`, path-deps
     `fs-core`, mirrors `score-wasm`'s split: a `pub` host-testable
     `score_block_pairs_fs_impl` (a sequential mirror of native's Vec
     `score_block_pairs_fs`, delegating per-pair to `fs_core::score_fs_pair`, so
     it is byte-identical to native by construction) linked via `rlib` +
     unit-tested under `cargo test`, plus a `#[cfg(target_arch="wasm32")]`
     `#[wasm_bindgen]` shim crossing the JS↔WASM boundary once per block (flat
     column-major arrays in, one JSON `[[a,b,s],…]` string out). The shim's
     initial entry covers the zero-config FS shape (no NE / custom banding /
     cross-batch exclude — the `auto_configure_probabilistic_df` shape); `_impl`
     already supports the full shape for the harness to grow into.
   - **3b (deferred — wasm-toolchain + CI gated, NOT done here):** build the wasm
     artifact (`wasm-pack`/the repo's `build_*_wasm.mjs` idiom — no wasm target
     in this env), swap `probabilistic.ts`'s scoring loop to call `fs-wasm`
     (keeping TS `trainEM` + the transform step host-side, exactly as EM/transforms
     stay Python-side), and repoint the TS parity harness from the hand-mirrored
     fixture to the shared core. These need the wasm build + `node`/vitest CI
     lanes to verify, so they are left for a CI-backed follow-up rather than
     shipping an unbuildable blob or an unverified 1,400-line TS rewrite.
4. **Port the name scorers** (`given_name_aliased_jw`, `name_freq_weighted_jw`)
   with reference tables injected (§ Reference data), + a `FrequencyProvider` /
   `AliasProvider` seam.
   - **Why it matters:** `build_probabilistic_matchkeys` routes name fields
     through `refdata.autoconfig_hooks.refine_matchkey_field`, which swaps in
     `name_freq_weighted_jw` (family) / `given_name_aliased_jw` (given) when the
     refdata packs are present. Neither is in `_NATIVE_FS_SCORER_IDS` (only
     `score_one` ids 0–3), so a person-name probabilistic matchkey declines the
     WHOLE matchkey to numpy — this is the concrete reason zero-config person
     dedupe still needs numpy.
   - **4a (landed):** the two scorer *leaf functions* in `fs-core` +
     the injected-provider seam, host-unit-tested (11 fs-core tests, clippy +
     fmt clean). `SurnameFreq { idf(value) -> Option<f64> }` (`None` = OOV, the
     exact `surname_rank is None` gate) and `NameAliases { are_equivalent(a,b) }`
     traits carry ZERO data — the host injects the census / alias tables.
     `name_freq_weighted_sim` mirrors the STATIC-census branch of
     `NameFreqWeightedJW.score_pair` (the branch the FS path takes; it never
     populates `tf_freqs`, and TF fields decline native anyway); the borderline
     band `[0.70, 0.95)`, the `0.6` common-name floor, and the surname-idf
     formula are single-sourced here. `given_name_aliased_sim` mirrors
     `GivenNameAliasedJW.score_pair` (alias → `1.0`, else JW), including the
     reflexive `normalize(a)==normalize(b)` shortcut. Base similarity is
     score-core's Jaro-Winkler (id 0) — Rust is the reference. Concrete
     `SurnameIdfTable` (`from_counts` / `from_idf_pairs`) + `AliasTable`
     (`from_forms`) impls back the traits for tests and the future marshaling;
     `normalize_name` single-sources the refdata `_normalize` (ASCII-scoped).
   - **4b/4c (deferred — pyo3-build + CI gated, NOT done here):** route
     name-scorer fields through the injected providers inside `score_fs_pair`
     (extend `FsPairParams` to carry the per-field provider handles), add
     `name_freq_weighted_jw` / `given_name_aliased_jw` to `_NATIVE_FS_SCORER_IDS`
     with a `FS_SUPPORTS_NAME_SCORERS` wheel-const gate, and marshal the census /
     alias tables across pyo3 once per call (host builds the provider from the
     already-loaded `refdata` state). These need the maturin/pyo3 build + the
     `native` CI lane to verify byte-for-byte against the numpy scorer, so they
     are left for a CI-backed follow-up.
5. **Port `tf_adjustment`** (EM freq/collision tables across the seam).
6. **Port `ensemble` NE** (explicit-config only; probabilistic auto-config emits
   no NE).
7. **Delete the Python numpy + scalar *production* paths, the `_fs_vec_guard`,
   and the `GOLDENMATCH_FS_NATIVE` / `GOLDENMATCH_FS_VECTORIZED` knobs.** Keep a
   minimal scalar reference in **test scope** as the parity oracle. Bump the
   `goldenmatch-native` floor and delete the `FS_SUPPORTS_*` old-wheel branches.

## Notes

- This **supersedes** the numpy-only `_fs_vec_guard` / `_fs_vec_max_elems` added
  in the scale fix — once native is the sole path there is nothing to guard.
- This is an **architecture/parity** change. It does **not** fix the
  zero-config over-match (F1 ≈ 0.09 on the person shape) — that is the fixed
  `link_threshold = 0.50` in `compute_thresholds`, identical on every path, and
  is tracked as a separate measured change against `bench-probabilistic`.
