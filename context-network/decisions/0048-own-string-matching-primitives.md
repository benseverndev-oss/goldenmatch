# 0048 — Own the string-matching primitives (goldenfuzz, goldenphonetic)

**Status:** Accepted. **Adopted:** 2026-07-28.
**Shipped:** goldenfuzz 0.1.1 + goldenphonetic 0.1.1 (PyPI + crates.io); rapidfuzz and
jellyfish evicted from every suite runtime dependency.

## Context

The suite depended on two third-party string-matching libraries on hot paths:
`rapidfuzz` (jaro-winkler / levenshtein / indel + the `fuzz.*` composites) across
goldenmatch / goldencheck / goldenflow / infermap, and `jellyfish` (phonetic
encoders) in goldenmatch's `metaphone` transform. Both are C/Rust-backed
dependencies we could neither tune, verify byte-for-byte across our own
surfaces, nor carry into WASM/TS without a second implementation. Owning the
scoring math is also the [0047](0047-one-product-two-engines-architecture.md)
"one authoritative semantic owner per capability" test applied to primitives.

## Decision

Own both as standalone, pyo3-free Rust kernels published as their own products —
`goldenfuzz` (crate `goldenfuzz-core`) and `goldenphonetic` (crate
`goldenphonetic-core`) — each a **byte-identical** drop-in for the library it
replaces, proven by an oracle fuzz against the incumbent, and reused across
Python (wheel) + crates.io + (future) WASM/TS from one source.

Governing principle — **own it, don't clone it forever** (see
[[feedback_own_dont_clone]] in agent memory): the goal is *better and faster*,
not a permanent byte-clone. Concretely:
- Remove the incumbent from **runtime** everywhere; consumers depend on the owned kernel.
- Pin correctness to the **algorithm** (a textbook/naive reference or fixed
  values), never `assert x == rapidfuzz`. The incumbent stays only as a
  port-fidelity oracle inside the owning kernel's own test suite + the workspace
  test group, never scattered across consumers.
- Prove speed in a **benchmark** (where referencing the competitor is fine),
  not a parity test.
- For FIXED-math primitives (jaro-winkler, levenshtein, soundex, metaphone) a
  drop-in is inherently byte-identical output; the win is speed + cross-surface +
  more scorers. For opinionated composites (WRatio) or a smarter normalization,
  "better" can later mean a genuinely different, improved result — a clone-oracle
  would block that.

## Consequence

- **Shipped:** goldenfuzz owns the rapidfuzz surface (distances + the full
  `fuzz.*` composite family + `extract`/`cdist`), faster on short strings;
  goldenphonetic owns jellyfish's soundex/metaphone/nysiis/match-rating
  (nysiis 6.8× faster via an ASCII fast path). Both byte-identical (goldenfuzz:
  oracle fuzz; goldenphonetic: 26,602-comparison fuzz), zero runtime deps.
- rapidfuzz + jellyfish are gone from every suite *runtime* dependency; they
  remain only as workspace **test-only** oracles (`[dependency-groups].dev`), so
  `uv sync` installs them for CI while `pip install <pkg>` ships neither.
- New public products to maintain (PyPI + crates.io publish workflows,
  `goldenfuzz-v*` / `goldenphonetic-v*` release tags). Accepted cost.
- Pattern set for the next ownership target (ANN via `faiss`/`hnswlib` → a
  `hnsw-core`).
