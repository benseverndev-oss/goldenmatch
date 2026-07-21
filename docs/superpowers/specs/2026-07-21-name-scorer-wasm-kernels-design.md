# TS-WASM kernels for the name scorers (`given_name_aliased_jw` / `name_freq_weighted_jw`)

**Date:** 2026-07-21
**Status:** Proposed → implementing. Feasibility verified (see below). Follow-on to the scorer-kernel
coverage work: these are the two `ts_only` scorers the coverage manifest marks `deferred` to "the TS
WASM surface." Kernelizing them there takes the metric **12/19 → 14/19**.

## Why WASM (not the Python bucket)

`given_name_aliased_jw` and `name_freq_weighted_jw` are `scorers.ts_only` — they live in TS's
`VALID_SCORERS` (Python accepts them via the plugin fallback) and their *reference* implementation is
the pure-TS `scoreField` path. The `scorer_kernels` metric counts a scorer as kernel-backed if it's in
the Python bucket `_NATIVE_SCORER_IDS` **or** the TS `WASM_COVERED_SCORERS`. So the right kernel home is
TS/WASM: add them to `WASM_COVERED_SCORERS` with a real `score-wasm` kernel byte-parity (4dp) with the
pure-TS path.

## The design (verified feasible)

**The Rust kernel already exists.** `fs-core` has `name_freq_weighted_sim(a, b, &dyn SurnameFreq)` and
`given_name_aliased_sim(a, b, &dyn NameAliases)` — byte-identical to the TS static-census branch (same
constants `0.95/0.70/0.6`, same OOV→plain-JW gate). They take **injected** reference-data trait objects;
`fs-core` bundles no data. The pyo3 `native` crate already consumes them via `set_name_reference_data`.

**Feasibility gates (both pass):**
1. **`fs-core` compiles to `wasm32-unknown-unknown`** — verified (`cargo build --target
   wasm32-unknown-unknown --release` clean). Its only dep is `score-core` (already wasm-proven by the
   existing `score-wasm` build).
2. **IDF byte-parity** — `fs-core::SurnameIdfTable::from_counts` uses the *exact* `surnames.surname_idf`
   formula `clamp[0,1]( ln(total/count) / ln(total/min) )` with the same `normalize_name`
   (alpha-only + lowercase). The only cross-language wobble is JS `Math.log` vs Rust `f64::ln` (≤1 ULP),
   which is far inside the WASM surface's parity bar: the existing `wasm-scorer.test.ts` asserts
   `toBeCloseTo(expected, 4)` (WASM rapidfuzz vs the pure-TS JW impl already differ slightly), NOT
   byte-exact. So **4dp is the standard**, and a log ULP never shows.

**Table injection, NOT embedding (the key edge-safety decision).** `score-wasm` deliberately stays lean
(`score-core` with `default-features = false`, no `regex`/`alias`). The census surname table (~176 KB)
and given-name aliases are ALREADY committed in the TS modules `censusSurnames.ts` /
`givenNameAliases.ts` for the pure path. So the WASM kernel does **not** embed them (no ~240 KB base64
bloat in the edge bundle, the tension `parity/goldenmatch.yaml`'s deferral note flagged). Instead, the
TS loader **injects** the data into the WASM module once at `enableWasm()`, mirroring the native
`set_name_reference_data` pattern — `fs-core` builds the trait tables from the passed data.

### Rust — `score-wasm`

- Add `goldenmatch-fs-core` dep with `default-features = false` on its `score-core` edge (so `score-wasm`
  stays regex/alias-free; `fs-core`'s `score-core` dep is made `default-features = false` — `fs-core`
  uses only jaro_winkler/levenshtein/token_sort/score_one, none behind the `alias` feature; the `native`
  build unions `alias` back in via its own direct `score-core` dep, so it's unaffected).
- Two process-global `OnceLock`s: `SURNAME_IDF: SurnameIdfTable`, `NAME_ALIASES: AliasTable`.
- Two `#[wasm_bindgen]` setters:
  - `set_surname_idf(names: Vec<String>, counts: Vec<f64>)` → `SurnameIdfTable::from_counts(zip(...))`.
  - `set_name_aliases(forms: Vec<String>, canonicals: Vec<String>)` → parallel *edge* arrays
    (`forms[i]` belongs to canonical `canonicals[i]`); group by form → `AliasTable::from_forms`.
    (Two flat `Vec<String>` — wasm-bindgen-friendly, no nested Vec.)
- `score_matrix_impl` gains two id branches (ids **20 = given_name_aliased_jw**, **21 =
  name_freq_weighted_jw** — ≥20 to leave headroom above `score_one`'s 0..=8): call the fs-core sim with
  the installed table; if a table isn't installed, fall back to plain JW (`score_one(0)`) — the
  table-absent behavior the pure path also degrades to.
- Native-side Rust tests set the tables directly (fs-core's own goldens: `William/Bill → 1.0`,
  `smith/smyth` borderline-weighted).

### TypeScript

- `src/core/wasm/backend.ts`: `SCORER_ID += { given_name_aliased_jw: 20, name_freq_weighted_jw: 21 }`
  → `WASM_COVERED_SCORERS` auto-includes them.
- The loader (`src/core/wasm/loader.ts`): after the artifact loads, call `set_surname_idf(CENSUS names,
  counts)` and `set_name_aliases(alias edges)` — the data taken from the same `censusSurnames.ts` /
  `givenNames.ts` state the pure path builds (single-sourced injection helpers `censusInjectionData` /
  `aliasInjectionEdges`), so there is ONE source of table truth per surface.
- `tests/unit/wasm-backend.test.ts`: update the expected `WASM_COVERED_SCORERS` set (+2).
- `tests/parity/wasm-scorer.test.ts`: add goldens for the two scorers (asserted `toBeCloseTo(_, 4)` vs
  the pure-TS `scoreField`), exercised after `enableWasm()` installs the tables.

### Manifest / metric

- Move `given_name_aliased_jw` / `name_freq_weighted_jw` from `scorer_kernels_deferred` →
  `scorer_kernels.ts_only` in `parity/goldenmatch.yaml` (the coverage gate *enforces* this once they're
  covered — `stale_deferral` otherwise). Regenerate suite-matrix (**14/19**) + agent-codemap.
- The `api_parity` `scorer_kernels` surface now shows them as `ts_only` (Python has no bucket kernel for
  them — a real Python↔TS delta, correctly declared).

## Parity + validation

- **Reference:** the pure-TS `scoreField` path (`given_name_aliased_jw` / `name_freq_weighted_jw`) is the
  byte-parity target; the WASM kernel matches it to 4dp (the surface standard). fs-core's own Rust
  goldens are the cross-surface oracle.
- **Local-validation reality:** the Rust side (fs-core→wasm build, score-wasm build, fs-core-backed unit
  tests) is fully local. The full wasm-bindgen artifact + TS vitest parity chain is what the CI
  `wasm_score` lane runs from source (`build_wasm.sh` + `wasm-scorer.test.ts`, in `ci-required`); the TS
  wiring is authored to the mapped patterns and validated there.

## Non-goals

- **The dynamic per-dataset `tf_freqs` branch** of `name_freq_weighted_jw` (Python builds a per-column TF
  table; `MatchkeyField.tf_freqs`). The TS port is static-census-only by design, so the WASM kernel is
  too — that's the scorer's TS reference. (The Python dynamic branch is a separate, Python-bucket
  concern and not part of this scorer's ts_only surface.)
- Embedding the census/alias tables in the wasm bundle (rejected — injected at init instead).

## Rollout

One PR: `score-wasm` fs-core dep + setters + ids 20/21 (+ Rust tests) → TS `SCORER_ID` +
loader injection + parity/backend tests → manifest move + suite-matrix (14/19). Gated by the
`wasm_score` CI lane (builds + runs the parity test) and the `api_parity` coverage gate.
