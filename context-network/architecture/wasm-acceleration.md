# Opt-in WASM acceleration (pyo3-free cores → TypeScript)

The TypeScript packages reach the same Rust `*-core` kernels the Python native
wheels and the SQL UDFs use — via **WebAssembly**, **opt-in**, without giving up
the zero-dependency pure-TS default or the edge-safety guarantee. WASM is the TS
sibling of the `*-native` abi3 wheels: same crate, byte-identical by construction.
Pure-TS stays the default and the fallback forever; default users download and
parse **zero** wasm bytes.

**Status:** SHIPPED across four PRs — `score-core` → goldenmatch (#878), the
pure-TS rapidfuzz alignment that unblocks parity (#879), `analysis-core` →
goldenanalysis + the shared runtime (#880), token_sort coverage + dist-path
validation (#881). **Spec:**
`docs/superpowers/specs/2026-06-12-opt-in-wasm-rust-acceleration-design.md` (+
`2026-06-12-analysis-wasm-acceleration-design.md`,
`2026-06-12-scorer-rapidfuzz-parity-design.md`). **Decision:**
[../decisions/0014-opt-in-wasm-acceleration.md](../decisions/0014-opt-in-wasm-acceleration.md).
**Code-level notes:** `packages/typescript/CLAUDE.md` (shared runtime),
`packages/typescript/goldenmatch/CLAUDE.md` (scorer slice).

**Folds since (2026-06-28).** The same `-core → -wasm → TS` pattern extended to
five more kernels:

| Core | WASM surface | Track | ADR |
|---|---|---|---|
| `autoconfig-core` | goldenmatch `core/autoconfig-wasm` | acceleration | — |
| `suggest-core` (healer) | goldenmatch `core/suggest-wasm` | enablement | [0027](../decisions/0027-healer-wasm-ts.md) |
| `goldenprofile-core` (Virtual Fingerprint) | standalone `goldenprofile` pkg | enablement | [0028](../decisions/0028-goldenprofile-wasm-ts.md) |
| `goldengraph-core` (KG: build/query + bitemporal store) | standalone `goldengraph` pkg | enablement | [0029](../decisions/0029-goldengraph-wasm-ts.md) |
| `perceptual-core` (image pHash) | goldenmatch `core/perceptual-wasm` (PR #1309) | enablement (+ determinism fix) | [0030](../decisions/0030-perceptual-cross-platform-determinism.md) |

Contract refinements learned across these: cross-surface parity for the
resolver/graph kernels is **partition + value (4dp), not byte-ordering**
(hash-map iteration order is non-deterministic); the perceptual fold was really a
**cross-platform determinism** fix (runtime-`cos` libm divergence flipped pHash
bits → a committed DCT table makes native/wasm/Python bit-identical); `i64`/`u64`
kernel params surface as wasm-bindgen **BigInt**; `goldenprofile`/`goldengraph`
publish is wired-but-unfired (not yet on npm). The `goldenprofile_native` CI lane
([#1304](https://github.com/benseverndev-oss/goldenmatch/issues/1304)) runs the
Python↔WASM cross-parity.

## The shared runtime: `goldenmatch-wasm-runtime`
A tiny zero-dependency workspace package holding the genuinely-shared, fiddly
plumbing — extracted once and reused, not duplicated per core:
- `resolveWasmBytes(opts, fallbackUrl)` — the edge-safe byte loader + env
  detection (fs on Node, `fetch` on browser/Workers; the documented
  `await import("node:fs/promises" as string)` idiom keeps bundlers from
  statically resolving node built-ins).
- `enableWasmBackend<B>(opts, instantiate, register, fallbackUrl)` — the generic
  async opt-in skeleton (browsers ban sync instantiation >4 KB): load → glue →
  register, or fall back to pure-TS (`{ require: true }` to hard-fail instead).
- `createBackendRegistry<B>()` — the module-singleton swap point (mirrors
  `setSyncEmbedder(null)`).

**Each consumer owns its artifact URL, its wasm-bindgen glue import, and its
backend interface.** The `new URL('./artifacts/X_bg.wasm', import.meta.url)` and
the dynamic `import('./artifacts/X.js')` MUST live in the consumer's own module
so `import.meta.url` resolves to *that* package's `dist` — passing the URL into
the shared package would resolve to the wrong location. That constraint is the
whole reason the runtime takes `fallbackUrl` as a parameter.

## Per-core slices (batch-first, never per-call)

| Core → consumer | Covered ops | Win profile |
|---|---|---|
| `score-core` → goldenmatch `scoreMatrix` | `jaro_winkler` / `levenshtein` / `token_sort` / `exact` | jaro_winkler is the dominant scorer; the swap is at the NxN block boundary (one JS↔WASM crossing per block) |
| `analysis-core` → goldenanalysis `histogram` / `quantile` | `histogram` / `quantile` | numeric arrays cross as **zero-copy `Float64Array`** + real Rust compute (quantile sorts); the Python native path measured 5.8–9.9x |

The boundary is **batch-first**: a single crossing per NxN block / per array,
never per-pair — per the perf-audit lesson that boundary cost dwarfs a single
scorer. The swap is per-scorer: only covered ops route to WASM; everything else
stays pure-TS even when enabled.

## The two gates
- **Parity:** a skip-guarded test (`tests/parity/wasm-*.test.ts`) asserts
  WASM ≈ pure-TS ≈ Python goldens to 4 decimals, including non-BMP / accented
  inputs. Skips without the built artifact; the CI lane builds it and runs
  un-skipped.
- **Bench (measure-first graduation):** a core ships acceleration only if the
  wall-clock measurably beats pure-TS on a realistic block / large-array
  workload. The bench is also the **dist-path validator** (see below).

## The rapidfuzz-alignment prerequisite (#879)
WASM parity (WASM ≈ pure-TS) is unachievable while the hand-rolled pure-TS
scorers diverge from rapidfuzz (which `score-core` IS). #879 aligned them as one
change — three latent divergences: **codepoint iteration** (`Array.from`, not
UTF-16 code units), the **Winkler boost gated on `jaro > 0.7`**, and **floored
transposition `t // 2`** (the divergence was integer-vs-float halving, NOT
bit-parallel match-assignment — empirically settled, 0/50000 vs rapidfuzz incl.
non-BMP). Existing canonical anchors (MARTHA, DIXON, …) and #857's refdata
scorers do not shift.

## token_sort coverage + the pinned asymmetry (#881)
`score-core::token_sort_normalized_ratio` is a **new** fn doing the TS-parity
lowercase + strip-non-alnum + token-sort normalize → `fuzz::ratio`. It is
deliberately distinct from `score_one(2)`, which stays **un-normalized** (the
pinned asymmetry the FFI / native path depends on — do not merge). score-wasm
branches `id == 2` to the normalized fn; the rest of the FFI/native surface is
untouched.

## The dist artifact path (#881)
The loader's `new URL('./artifacts/X_bg.wasm', import.meta.url)` resolves
relative to wherever tsup bundles the loader in `dist` — unpredictable. Rather
than guess, `copy_wasm_artifact.mjs` copies the artifact to **every plausible**
`./artifacts/` parent (`dist/core/wasm/artifacts/`, `dist/core/artifacts/`,
`dist/artifacts/`). The wasm benches were then flipped OFF `continue-on-error`:
they build `dist` + run `enableWasm()` / `enableAnalysisWasm()`, so a broken
bundled path reddens the (non-required) lane. That gate paid for itself on the
first run — it caught `aggregate.ts` histogram's `Math.min(...vals)`
stack-overflowing at 1M elements (exactly the large-array case WASM exists for),
now a loop.

## Parked cores (measure-first verdict, not built)
- **`graph-core` / `fingerprint-core` / `sketch-core` — PARK as a *direct* TS
  slice** (boundary-bound: marshaling N pairs is itself O(N), so a per-op TS
  surface can't replicate Python's whole-clustering-in-Rust win). NOTE these are
  now compiled **into** the `goldenprofile`/`goldengraph` wasm bundles (those
  cores depend on them), so the kernels DO reach JS — only a standalone per-op
  surface stays unjustified.
- **`goldenflow` — PARK** (scoped 2026-06-28, measure-first NO-GO). Its only
  kernel is phone (via the `phonenumber` crate; dates were vectorized in Polars,
  [0006](../decisions/0006-goldenflow-native-nanp-gating.md)). No win: the TS
  phone transform is a trivial digit-strip (nothing hot to beat). No parity: the
  native kernel already diverges ~6% from Python `phonenumbers` on international
  numbers, so a WASM kernel would be a third disagreeing impl, not a unifier. And
  the mature `libphonenumber-js` already serves JS.
- **`goldencheck-core` — PARK** (re-confirmed 2026-06-28, [0014](../decisions/0014-opt-in-wasm-acceleration.md)).
  wasm-viable (only `rustc-hash`), but: the relations kernels (FD / composite-key
  / approx-dup) use **different algorithms** than the existing pure-TS profilers
  (Rust distinct-count-all-pairs vs TS "simplified TANE single-column") → no
  parity drop-in (would change results). The one clean reduction
  (`benford_leading_digits`) is a trivial single-pass tally pure-TS does fine,
  and TS already computes the fuller benford (chi²). Differs from `analysis-core`
  (which won) = same-algorithm + genuinely hot reduction. Unifying would need
  TS↔Rust algorithm alignment first (bigger than a fold), with no measured need.

## Verification (CI)
- The required `typescript` lane builds the shared runtime first (turbo `^build`)
  and runs the artifact-free unit tests (`wasm-backend`, `wasm-fallback`).
- The non-blocking `wasm_score` / `analysis_wasm` lanes build the `wasm32`
  artifact, run the parity test un-skipped, and run the bench (now a dist-path
  gate). Each lane builds `goldenmatch-wasm-runtime` before its parity vitest
  (the parity test imports it; turbo isn't in that path).
- Rust host `cargo test` on the `*-wasm` crates (logic lives in `score-core` /
  `analysis-core`; the shims are trivial).

## Adding a new accelerated core
New `*-wasm` crate (mirror `score-wasm`), a consumer `src/core/wasm/` (backend +
loader + index over the shared runtime), wire the batch boundary, a skip-guarded
parity test + bench, a CI lane (build runtime before the parity test; bench as a
dist gate). Only build it if a cheap profile says it will win.

---
**Classification:** architecture/shipped • **Last updated:** 2026-06-28
