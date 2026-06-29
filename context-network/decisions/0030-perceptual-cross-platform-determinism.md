# 0030 — Perceptual image pHash: cross-platform determinism via a committed DCT table, + WASM/TS surface

**Status:** accepted (2026-06-28, Ben) • **Kernel:** [0022-multimodal-er-perceptual-crawl-tier.md](0022-multimodal-er-perceptual-crawl-tier.md) • **WASM policy:** [0014-opt-in-wasm-acceleration.md](0014-opt-in-wasm-acceleration.md) • **Sibling folds:** [0028](0028-goldenprofile-wasm-ts.md) / [0029](0029-goldengraph-wasm-ts.md)

## Context
Scoping the perceptual-core WASM fold uncovered a latent correctness bug, not just a missing surface. The image pHash computed its DCT-II basis `cos()` at **runtime**, so the basis differed in the last places across libms (glibc / MSVC / wasm / CPython). On borderline images those last-place differences flip bits at the median threshold. Measured:
- The committed `golden.rs` test **passed on Linux CI but FAILED on Windows native** (`gradient_16x16`).
- A `wasm32` build sat **up to 6 bits** off the Linux-generated fixture — so a JS-computed pHash could not be compared against a Python-built index.

This violates the suite's "one kernel, identical across surfaces" thesis — and it was already broken across *platforms*, before WASM entered the picture.

## Decision
1. **Freeze the fixed transcendentals as a committed constant table.** The 8×32 DCT-II basis (`cos(π·(i+0.5)·k/32)`) is input-independent, so precompute it once and commit it as a constant that the **Rust kernel (native + wasm) AND the Python reference both read**. No runtime libm on the basis → bit-identical everywhere.
2. **One generator, both languages, bit-exact.** `scripts/gen_perceptual_tables.py` emits `perceptual-core/src/tables.rs` (`f64::from_bits`) and `goldenmatch/core/_perceptual_tables.py` (`float.fromhex`) from the same values. Frozen constants — regenerating re-bases the image fixture.
3. **Image pHash only.** Radial-variance and audio fingerprint have **non-cos** fragility that a basis table can't fix — radial through Python 3.12's compensated `sum()` + rounding, audio through a **per-sample-rate** DFT whose argument is a runtime input. They stay Python/native-only, unchanged.
4. **WASM/TS surface = opt-in `goldenmatch/core/perceptual-wasm`** (`phashImage` + `hamming`), mirroring the `suggest-wasm`/`autoconfig-wasm` subpaths. Edge-safe; the base `goldenmatch` entry carries zero wasm bytes. Parity is **byte-exact** (not within-K-bits) — the whole point of the table fix.

## Consequences / honest flags
- **Fixture rebased by exactly 2 values** (`gradient`, `checker` — the borderline images); `gratings`, radial, and audio entries are untouched.
- **The table is platform-of-generation-frozen.** It was generated from one libm and committed; it is the authority. Regeneration on a different libm would shift the borderline bits and must be paired with a fixture rebase — hence "generate once, commit, treat as frozen."
- **Verified Windows-local**: host golden tests pass (image was failing), wasm phash byte-exact, Python perceptual suite 33 passed. CI's native + python lanes verify the Linux side; the `perceptual_wasm` lane rebuilds the wasm and runs the byte-exact parity test.

## Alternatives not taken
- **Use the `libm` crate** for portable Rust transcendentals (declined — diverges from CPython's `math.cos`, breaking the Python golden fixture; and doesn't help CPython itself).
- **Fuzzy within-K-bits parity for WASM** (declined — a 6-bit drift eats most of the ~10-bit match threshold, so a JS hash couldn't be trusted against a Python index; the table fix makes it byte-exact, which is correct).
- **Table-ize radial/audio too** (declined — non-cos fragility; out of scope, left Python/native-only).

---
**Classification:** decision/accepted • **Last updated:** 2026-06-28
