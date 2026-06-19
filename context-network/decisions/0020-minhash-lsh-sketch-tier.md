# 0020 — MinHash/LSH sketch tier (sketch-core)

**Status:** accepted • **Shipped:** PR #TBD (2026-06-19), issue #1081

## Context
GoldenMatch's blocking is accuracy-oriented and structured-record-shaped
(static/multi-pass/learned/quality-aware predicates, ANN on embeddings). There
was no probabilistic *set-similarity* sketching path, so document/corpus-scale
near-duplicate detection (LLM training-data dedup, #1080) had no efficient
candidate-generation primitive. MinHash + banded LSH is the standard tool: hash
a record's shingle set into a short signature, bucket signature bands, and treat
records sharing ≥1 bucket as candidates — recall is tunable via the band/row
split and the work reduction is large.

This is phase 1 of the Training-Data Dedup at Scale epic (#1080); later phases
build the document near-dup path (#1082), the sketch-then-verify plan (#1083),
distributed billion-scale (#1084), and the product surface (#1085).

## Decision
Add a new pyo3-free `goldenmatch-sketch-core` Rust crate (shingling → MinHash →
banded LSH) and expose it on Python (pyo3 native + pure-Python fallback) and
TypeScript (pure-TS), plus a `MinHashLSHBlocker` conforming to the existing
`BlockResult` blocker contract (`BlockingConfig(strategy="lsh", lsh=...)`).

**Approach A — shared kernel does per-record sketching; host language groups
buckets.** The kernel owns the CPU-heavy, parallelizable part (`text → shingle
set → MinHash signature → per-band bucket hashes`); Python/TS group records by
`(band, bucket)` into blocks using the existing blocking infrastructure. This
fits the established `score-core`/`fingerprint-core` kernel pattern and leaves
the (already efficient) polars/`Map` grouping in the host language. Full-Rust
grouping + pair emission is left to the distributed plan (#1083/#1084).

**Parity-by-construction.** A single hand-rolled, dependency-free hash family is
the cross-language contract: `base_hash = splitmix64_finalize(FNV-1a-64(bytes))`,
permutations `(a·x + b) mod (2⁶¹−1)` with coefficients from a splitmix64 stream,
and banded bucket hashes over little-endian signature bytes. No third-party hash
crate (avoids the version-skew footgun the repo has hit before). The Python
reference (`core/sketch.py`) is authoritative; golden vectors generated from it
(`tests/fixtures/sketch_golden.json`) are checked by all three implementations,
plus the `GOLDENMATCH_NATIVE=0/1` native↔python parity sweep.

**Native gating.** The `sketch` component ships native-available but is NOT in
the `_native_loader._GATED_ON` allowlist yet (same conservative posture as
`pprl_bloom`): reachable via `GOLDENMATCH_NATIVE=1`, default pure-Python under
`auto`. Output is byte-identical (deterministic), so the default-on flip is a
perf/published-wheel decision, not an accuracy one.

**Measured recall.** An always-on synthetic gate (`test_lsh_recall.py`: recall
0.978 / candidate-reduction 0.989 at the pinned config) plus a Quora Question
Pairs bench job (`bench-lsh-recall.yml`, workflow_dispatch) for real-text recall.
Quora data is never committed (licensing); a synthetic QQP-shaped sample drives a
CI smoke test.

## Consequences
- New blocking strategy `"lsh"` + `LSHKeyConfig` (re-exported); a new pyo3-free
  crate wired into the rust CI lane and the `native` pyo3 module.
- TS gains an edge-safe `MinHashLSHBlocker` (BigInt-based; a WASM speed slice is
  deferred, consistent with the `score-core` rollout).
- Rejected alternatives: off-the-shelf hashes (xxHash) per language — version
  skew breaks byte-parity; full-Rust grouping + pair emission — bypasses the
  existing blocker contract and duplicates work the distributed plan will own.
