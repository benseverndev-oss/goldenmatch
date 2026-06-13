# 0014 — Opt-in WASM acceleration: pure-TS stays the default, measure before shipping each core

**Status:** accepted (2026-06-12, Ben) • **Shipped:** PRs #878 (score-core) / #879 (rapidfuzz alignment) / #880 (analysis-core + shared runtime) / #881 (token_sort + dist-path) • **Architecture:** [../architecture/wasm-acceleration.md](../architecture/wasm-acceleration.md)

## Context
The Golden Suite TypeScript packages are **pure TypeScript** — `src/core/**` is
edge-safe (no `node:*`), zero-dependency, and runs in browsers, Workers, and edge
runtimes. That edge-safety is the value proposition. The Rust acceleration was
Python-only: the pyo3-free `*-core` crates back the `*-native` wheels and the SQL
UDFs, while the TS port reimplements the same algorithms by hand against a
4-decimal parity contract. The question: can the TS side **optionally** reach the
same Rust kernels via WebAssembly without losing the pure-TS default or the
edge-safety guarantee?

## Decision
1. **Opt-in, never the default.** Pure-TS is the default and the fallback
   forever; the `.wasm` is built in CI, never committed, and default users load
   zero wasm bytes. `enableWasm()` is async and returns `false` (pure-TS stays)
   on any load failure; `{ require: true }` hard-fails for callers who must have it.
2. **One shared runtime, extracted — not duplicated.** The fiddly plumbing
   (byte loader, env detection, the enable skeleton, the registry) lives in a
   tiny zero-dep `goldenmatch-wasm-runtime` workspace package both consumers ride.
   Each consumer keeps its artifact URL + glue import + backend interface in its
   OWN module (so `import.meta.url` resolves to its dist).
3. **Batch-first boundary, per-scorer swap.** Cross the JS↔WASM boundary once per
   NxN block / per array, never per pair (boundary cost dwarfs a single op). Only
   covered ops route to WASM; everything else stays pure-TS even when enabled.
4. **Two gates per core: parity AND bench. Ship only on a measured win.** A core
   graduates to shipped acceleration only when WASM ≈ pure-TS ≈ Python (4dp,
   incl. non-BMP) *and* the wall-clock measurably beats pure-TS on a realistic
   workload. "It's Rust/WASM" is never enough — the boundary + marshaling can
   erase the win.
5. **Align the reference, don't fork it.** WASM parity requires the pure-TS
   scorers to match rapidfuzz first; #879 aligned them (codepoint iteration,
   `>0.7` boost, floored transposition) as a single change rather than letting
   WASM and pure-TS diverge.

## Consequence
- Two cores shipped: `score-core` → goldenmatch (jaro_winkler/levenshtein/
  token_sort/exact) and `analysis-core` → goldenanalysis (histogram/quantile,
  the 5.8–9.9x-in-Python numeric-reduction shape). The shared runtime is the
  reusable substrate for any future core.
- The measure-first gate **parked `graph-core`** without building it: its only
  accelerable op (UnionFind construction) is one O(N) step among several in
  `buildClusters`, and marshaling N pairs is itself O(N) → boundary-bound. The
  TS slice can't replicate Python's whole-clustering-in-Rust win. `fingerprint-core`
  / `goldencheck-core` stay parked by design. The verdict cost a paragraph, not a crate.
- The bench-as-dist-validation gate paid for itself on the first real run: it
  caught `aggregate.ts` histogram's `Math.min(...vals)` stack-overflowing at 1M
  elements (the exact large-array case WASM is for) — a latent bug that the
  small-corpus parity test could never have surfaced.
- The `score_one(2)` token_sort asymmetry is preserved: token_sort WASM coverage
  added a NEW normalized `score-core` fn rather than touching the un-normalized
  dispatch the FFI/native path depends on. Don't reconcile them.
