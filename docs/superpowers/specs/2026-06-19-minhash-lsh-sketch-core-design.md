# MinHash / LSH sketch kernel (`sketch-core`) — design

- **Issue:** [#1081](https://github.com/benseverndev-oss/goldenmatch/issues/1081) — MinHash / LSH sketch kernel
- **Epic:** [#1080](https://github.com/benseverndev-oss/goldenmatch/issues/1080) — Training-Data Dedup at Scale (throughput tier)
- **Date:** 2026-06-19
- **Status:** Approved (brainstorming) — pending spec review + implementation plan

## Motivation

The engine is accuracy-oriented and structured-record-shaped. There is no
probabilistic sketching path for candidate generation, so document/corpus-scale
near-duplicate detection (LLM training-data dedup) has no efficient blocking
primitive. This slice adds the foundational kernel — MinHash signatures over
shingle sets, plus banded LSH bucketing — as a new pyo3-free Rust core shared by
Python and TypeScript, exposed as a blocking primitive with measured recall.

This is **phase 1** of the epic. Later phases build on it: the document near-dup
path (#1082), the sketch-then-verify execution plan (#1083), distributed
billion-scale dedup (#1084), and the product surface (#1085).

## Goals

- A pyo3-free `goldenmatch-sketch-core` Rust crate: shingling, MinHash, banded LSH.
- Python binding (pyo3 wrapper in `native`) + pure-Python reference/fallback,
  reusing the `_native_loader` gate.
- Pure-TypeScript port (parity-first; WASM speed slice deferred).
- A `MinHashLSHBlocker` on Python and TS conforming to the existing blocker
  contract, available via blocking config.
- Cross-language **byte-identical** output, proven by golden vectors +
  `GOLDENMATCH_NATIVE=0/1` parity tests.
- Measured recall: a synthetic CI recall gate **and** a real-corpus
  (Quora Question Pairs) bench job.

## Non-goals (deferred to later epic phases)

- Document/text near-dup *path* tuning and corpus (parquet/jsonl) adapters (#1082).
- Sketch-then-verify execution plan (#1083).
- Distributed billion-scale dedup (#1084).
- Corpus-dedup product surface / CLI (#1085).
- Throughput CI perf gate (#1086) — this slice gates on **recall**, not throughput.
- WASM acceleration for the TS port (a later opt-in slice, per the `score-core`
  rollout pattern).

## Architecture

Chosen approach (**A**): the shared kernel owns per-record sketching
(`text → shingle set → MinHash signature → per-band bucket hashes`); the host
language (Python/TS) groups records by `(band, bucket)` into blocks using the
existing blocking infrastructure. Rationale: fits the established
`score-core`/`fingerprint-core` kernel pattern and the `BlockResult` blocker
contract, keeps the parallel CPU work in Rust, and leaves bucket grouping to the
already-efficient polars/`Map` paths. Full-Rust grouping + pair emission is a
later optimization owned by the distributed plan (#1083/#1084).

```
text ──► shingle(mode,k) ──► {u64 shingle hashes}
                                   │
                                   ▼
                    signature(num_perms, seed) ──► [u64; num_perms]   (MinHash)
                                   │
                                   ▼
                    band_hashes(num_bands) ──► [u64; num_bands]        (LSH buckets)
                                   │
              ┌────────────────────┴────────────────────┐
        (Rust kernel boundary)                    (host language)
                                          group records by (band_idx, bucket)
                                          → BlockResult per non-singleton bucket
                                          → candidate pairs (dedup across bands)
```

## Canonical algorithm (parity contract)

This section is the normative definition. Rust, Python, and TypeScript MUST
produce identical `u64` outputs for identical inputs. All integer arithmetic is
unsigned and **wrapping** at the stated width unless a modulus is given.

### Constants

```
FNV_OFFSET   = 0xcbf29ce484222325   (u64)
FNV_PRIME    = 0x00000100000001B3   (u64)
SM_C1        = 0xbf58476d1ce4e5b9   (u64)   # splitmix64 finalizer
SM_C2        = 0x94d049bb133111eb   (u64)
SM_GAMMA     = 0x9e3779b97f4a7c15   (u64)   # splitmix64 increment
MERSENNE_P   = 2^61 - 1 = 0x1FFFFFFFFFFFFFFF (u64, used as u128 modulus)
```

### `base_hash(bytes) -> u64`

FNV-1a over the bytes, then a splitmix64 finalizer for avalanche:

```
h = FNV_OFFSET
for b in bytes:                 # b is one byte (0..=255)
    h = (h XOR b) * FNV_PRIME   # wrapping u64
# splitmix64 finalize:
h = (h XOR (h >> 30)) * SM_C1   # wrapping u64
h = (h XOR (h >> 27)) * SM_C2   # wrapping u64
h = h XOR (h >> 31)
return h
```

Strings are encoded as **UTF-8 bytes** before hashing, in every language.

### `splitmix64(state) -> (next_value, new_state)`

Deterministic stream used to derive permutation coefficients:

```
state = state + SM_GAMMA        # wrapping u64
z = state
z = (z XOR (z >> 30)) * SM_C1   # wrapping u64
z = (z XOR (z >> 27)) * SM_C2   # wrapping u64
z = z XOR (z >> 31)
return (z, state)
```

**Stream timing (normative):** the increment is applied **before** finalization,
so a stream started at `state = S` produces its first value as
`finalize(S + SM_GAMMA)` — there is **no** draw that finalizes the raw seed `S`.
Implement this exact pseudocode; do **not** substitute a stdlib/reference
splitmix64 that increments *after* producing a value — that variant yields a
different, still-internally-consistent coefficient stream and silently breaks
parity. (The golden vectors will catch a mistake, but the contract does not
rely on them alone.)

### `shingle(text, mode, k) -> sorted unique Vec<u64>`

Both modes first build an ordered **unit sequence**, then window it. Let `n` be
the sequence length.

- **char mode:** the unit sequence is the text's Unicode scalar values (Rust
  `chars()`, Python iteration over `str`, TS `Array.from(string)` — code points,
  not UTF-16 units).
- **word mode:** the unit sequence is the text's tokens. Tokenization is
  **maximal runs split on the exact ASCII whitespace set**
  `{ U+0009 (tab), U+000A (LF), U+000B (VT), U+000C (FF), U+000D (CR),
  U+0020 (space) }` — and *only* those six code points. Empty tokens are
  dropped. **Do not** use a language's default whitespace splitter
  (Rust `split_whitespace`, Python no-arg `str.split`, JS `\s`): they disagree on
  Unicode whitespace (U+00A0, the `Zs` category, ZWSP, …) and one disagreement on
  a separator changes the token set and breaks parity. (char mode does not
  tokenize and is unaffected.)

`k` must be `>= 1`; every port MUST reject `k < 1` with an error (do not rely on
language behavior — Rust `windows(0)` panics while Python would silently emit
empty-string shingles, a parity divergence).

Windowing, given the unit sequence and `n`:

- **`n >= k`:** for each contiguous window of `k` units, materialize the window's
  bytes and `base_hash` them. char-mode bytes = UTF-8 of the `k` code points
  concatenated; word-mode bytes = UTF-8 of the `k` tokens joined by a single
  `0x20` space.
- **`1 <= n < k` (short input):** emit exactly one shingle of the whole sequence
  (char: UTF-8 of all code points; word: UTF-8 of all tokens joined by `0x20`).
- **`n == 0` (empty / whitespace-only):** empty shingle set. Its signature is all
  `u64::MAX`, so it shares no LSH bucket with any non-empty record. This `n == 0`
  case takes precedence over the short-input branch — a whitespace-only word-mode
  input has zero tokens and yields the empty set, never a single empty shingle.

Output is the **sorted, deduplicated** set of shingle hashes (sort makes the set
canonical; MinHash is order-independent, but sorting keeps golden vectors
stable).

### `signature(shingles, num_perms, seed) -> Vec<u64>` (length `num_perms`)

Derive coefficients once from `seed`:

```
state = seed
for i in 0..num_perms:
    (v, state) = splitmix64(state); a[i] = (v mod (MERSENNE_P - 1)) + 1   # a[i] in [1, P-1]
    (v, state) = splitmix64(state); b[i] = v mod MERSENNE_P               # b[i] in [0, P-1]
```

Coefficients are drawn independently and **may repeat**; do not deduplicate or
reject collisions — a repeated `(a, b)` is valid and expected at large
`num_perms`.

Then, for each permutation, the min over shingles (compute in `u128`):

```
for i in 0..num_perms:
    m = u64::MAX
    for x in shingles:
        xr = (x mod MERSENNE_P)                       # reduce u64 into the field
        p  = ((a[i] as u128 * xr as u128 + b[i] as u128) mod MERSENNE_P) as u64
        m  = min(m, p)
    sig[i] = m
return sig
```

Empty shingle set ⇒ every `sig[i] = u64::MAX`.

### `band_hashes(signature, num_bands) -> Vec<u64>` (length `num_bands`)

`num_perms` must be divisible by `num_bands`; `r = num_perms / num_bands`.

```
for band_idx in 0..num_bands:                          # band_idx is a u64
    buf = le_bytes_u64(band_idx)                       # 8 bytes, little-endian
    for j in 0..r:
        buf ++= le_bytes_u64(signature[band_idx*r + j])  # 8 bytes each, little-endian
    bucket[band_idx] = base_hash(buf)
return bucket
```

Both `band_idx` and each signature value are serialized as exactly 8
little-endian bytes (`u64`), so the hashed buffer is always `8*(r+1)` bytes
regardless of target word size. Mixing `band_idx` into the buffer prevents
identical row-tuples in different bands from colliding into one global bucket
space.

### `optimal_bands(num_perms, threshold) -> (b, r)` — host-side helper

This is a **host-side configuration helper, not part of the byte-exact hash
path** — its result is an integer `(b, r)` that is then fed to `band_hashes` as
an explicit `num_bands`. The kernel (`band_hashes`) only ever consumes an
explicit `num_bands`; it never calls `optimal_bands`. So `optimal_bands` does not
need to be byte-identical with the hashes — but to keep the *blocker* consistent
across Python and TS, it is pinned to one deterministic procedure:

```
best = None
for b in 1..=num_perms:                  # ascending
    if num_perms mod b != 0: continue
    r = num_perms / b
    fp = integral over s in [0, threshold] of (1 - (1 - s^r)^b)         # false positives
    fn = integral over s in [threshold, 1] of (1 - (1 - (1 - s^r)^b))   # false negatives
    err = 0.5*fp + 0.5*fn
    if best is None or err < best.err - 1e-12: best = (b, r, err)       # strict improvement only
return (best.b, best.r)
```

Each integral is a **fixed trapezoidal rule with 1000 equal subintervals over
its stated range** (so `[0, threshold]` and `[threshold, 1]` each get 1000
trapezoids). The `err < best.err - 1e-12` guard means ties keep the **smaller
`b`** found first (the ascending scan); document this so Python and TS agree.
Floats appear only here, choosing an integer `(b, r)` — they never enter the hash
path. When the caller supplies an explicit `num_bands`, this helper is not
invoked at all.

## Crate structure

```
packages/rust/extensions/sketch-core/
├── Cargo.toml          # standalone [workspace]; lib name goldenmatch_sketch_core
└── src/
    ├── lib.rs          # public API + batch entry points (rayon)
    ├── hash.rs         # base_hash, splitmix64
    ├── shingle.rs      # shingle(text, mode, k)
    ├── minhash.rs      # signature, estimate_jaccard
    └── lsh.rs          # band_hashes, optimal_bands
```

- Standalone workspace (empty `[workspace]`) so it is a path dependency of
  `native` (and later `postgres`/`datafusion-udf`) without workspace conflict —
  same pattern as `score-core`.
- Dependencies: `rayon` only (for batch parallelism). No hashing crates — the
  hash is hand-rolled for guaranteed parity.
- Batch entry points (`band_hashes_batch`, `signature_batch`) parallelize
  per-record with rayon, scoring small inputs on the calling thread and fanning
  out only above a pair/row threshold (the #688 `LockLatch` lesson).

## Python surface

- **`native` pyo3 wrappers** — thin `#[pyfunction]` shims (`sketch_band_hashes_batch`,
  `sketch_signature_batch`) delegating to the core, returning `list[list[int]]`.
  Add `goldenmatch-sketch-core = { path = "../sketch-core" }` to `native/Cargo.toml`.
- **`goldenmatch/core/sketch.py`** — the pure-Python **reference implementation**
  (the parity reference) and the fallback. Same constants, same algorithm. This
  file is authoritative for golden-vector generation.
- **`goldenmatch/core/lsh_blocker.py`** — `MinHashLSHBlocker`:
  - Input: `pl.LazyFrame`, text column, `LSHKeyConfig`.
  - Computes per-record `band_hashes` (native iff `native_enabled("sketch")`,
    else `sketch.py`).
  - Explodes to `(band_idx, bucket)` rows, groups, emits one `BlockResult` per
    non-singleton bucket with `strategy="minhash_lsh"`; dedups candidate pairs
    across bands so a pair colliding in multiple bands is offered once.
  - Conforms to the existing `BlockResult` contract (block_key, df, strategy).
- **Config** — new `strategy="lsh"` in `BlockingConfig`, plus an `LSHKeyConfig`
  dataclass: `column`, `mode` (char|word), `k`, `num_perms`, `seed`, and either
  `threshold` (→ `optimal_bands`) or explicit `num_bands`. Wired into the
  blocking dispatch.
- **`_native_loader.py`** — add a `"sketch"` component to the gate. Ship
  native-available; default-on once parity CI is green (output is deterministic
  and byte-identical — no accuracy sign-off needed). `GOLDENMATCH_NATIVE=0`
  forces Python; `=1` requires native.

## TypeScript surface

- **`src/core/sketch.ts`** — pure-TS port matching the reference byte-for-byte.
  64-bit and the `mod (2^61-1)` multiply use `BigInt`; code-point shingling via
  `Array.from`. Correctness-first; a WASM speed slice is explicitly deferred
  (consistent with the `score-core` rollout). Document the `BigInt` perf caveat.
- **TS `MinHashLSHBlocker`** mirroring the Python blocker in the TS blocking
  layer, same config shape.
- Parity via the committed golden vectors (no native dependency in TS).

## Parity & correctness strategy

- **Golden vectors:** `packages/python/goldenmatch/tests/fixtures/sketch_golden.json`
  — a set of `{text, mode, k, num_perms, num_bands, seed} → {shingles, signature,
  band_hashes}` cases generated from `sketch.py`, including edge cases (empty,
  whitespace-only, len < k, unicode/multibyte, repeated tokens, long text). All
  three impls assert against this file. The regen script (`scripts/gen_sketch_golden.py`)
  **imports and calls `sketch.py`** — there is exactly one reference source, never
  a second hand-rolled generator. The file is committed and treated as the contract.
- **Rust↔Python:** `tests/test_native_sketch_parity.py` toggles
  `GOLDENMATCH_NATIVE=0/1` (the repo's standard kernel-parity pattern) over a
  property-style sweep of random texts/params and asserts identical output.
- **Rust unit tests** in-crate (`#[cfg(test)]`): hash reference values, shingling
  edge cases, signature determinism, `estimate_jaccard` ≈ true Jaccard within a
  tolerance, banding shape, `optimal_bands` divisor invariants.
- **TS:** golden-vector test in the TS unit suite.

## Recall benchmark (done bar: "measured recall")

- **Synthetic CI gate** — `scripts/bench_lsh_recall.py`: generate seed documents,
  produce near-dup variants via controlled edits (insert/delete/substitute tokens
  at configurable rates) with a known dup-pair set; measure **recall** (fraction
  of true near-dup pairs sharing ≥1 LSH bucket) and **candidate-reduction ratio**
  (1 − candidate pairs / all pairs) across `(k, num_perms, b, r)`. A pytest in
  `tests/` runs a fixed small config and asserts recall ≥ a real threshold
  (target ≈ 0.95 for high-similarity variants) and a meaningful reduction ratio —
  an always-on regression gate, not a tautology.
- **Real corpus bench job** — **Quora Question Pairs** (labeled
  `is_duplicate` text pairs):
  - A tiny committed sample (`tests/fixtures/qqp_sample.csv`, a few hundred pairs)
    drives a CI smoke test of the end-to-end blocker on real text.
  - `.github/workflows/bench-lsh-recall.yml` (`workflow_dispatch`, default runner
    `large-new-64GB`) downloads the full QQP set, runs the LSH blocker, and reports
    recall / precision / reduction vs the labels, writing a markdown report.
    Mirrors the existing bench-workflow pattern (`bench-issue-688.yml` etc.).

## Docs & rollout

Swept at the end via the **rollout-docs-sweep** skill against the repo's
`doc-surfaces.md` inventory:

- `docs-site/goldenmatch/tuning.mdx` — new `GOLDENMATCH_NATIVE` `sketch` component
  and the `lsh` blocking strategy / `LSHKeyConfig` fields.
- CHANGELOGs (Python + TS).
- A context-network ADR for the sketch tier (records approach A and the parity
  contract).
- Discovery/`llms.txt`/MCP surfaces as applicable (the blocker is config-driven,
  not a new MCP tool in this slice).

## File manifest (new / modified)

**New**
- `packages/rust/extensions/sketch-core/{Cargo.toml, src/lib.rs, src/hash.rs, src/shingle.rs, src/minhash.rs, src/lsh.rs}`
- `packages/python/goldenmatch/goldenmatch/core/sketch.py`
- `packages/python/goldenmatch/goldenmatch/core/lsh_blocker.py`
- `packages/python/goldenmatch/tests/test_native_sketch_parity.py`
- `packages/python/goldenmatch/tests/test_sketch_golden.py`
- `packages/python/goldenmatch/tests/test_lsh_blocker.py`
- `packages/python/goldenmatch/tests/test_lsh_recall.py`
- `packages/python/goldenmatch/tests/fixtures/sketch_golden.json`
- `packages/python/goldenmatch/tests/fixtures/qqp_sample.csv`
- `scripts/bench_lsh_recall.py`
- `scripts/gen_sketch_golden.py`
- `.github/workflows/bench-lsh-recall.yml`
- `packages/typescript/goldenmatch/src/core/sketch.ts`
- `packages/typescript/goldenmatch/src/core/lshBlocker.ts` (+ exports)
- `packages/typescript/goldenmatch/tests/unit/sketch.test.ts`

**Modified**
- `packages/rust/extensions/native/Cargo.toml` (+ path dep)
- `packages/rust/extensions/native/src/lib.rs` (+ pyfunctions)
- `packages/python/goldenmatch/goldenmatch/core/_native_loader.py` (+ `"sketch"` component)
- `packages/python/goldenmatch/goldenmatch/config/schemas.py` — add `"lsh"` to the
  closed `BlockingConfig.strategy` `Literal[...]`, add the `LSHKeyConfig`
  dataclass, and extend the `_validate_keys_or_passes` validator so `"lsh"`
  requires neither `keys` nor `passes` (it carries its own `LSHKeyConfig`) — the
  same exemption `"ann"` already has.
- blocking dispatch (`blocker.py` / wherever `strategy` is routed) — add the
  `"lsh"` branch that constructs and runs `MinHashLSHBlocker`.
- docs surfaces (rollout sweep)

## Risks & mitigations

- **Cross-language hash drift** → hand-rolled dependency-free hash + committed
  golden vectors checked by all three impls; no third-party hash crates.
- **`u128`/`BigInt` overflow in the `mod p` multiply** → spec pins `u128`
  intermediate (Rust) and `BigInt` (TS); Python is arbitrary-precision. Covered
  by golden vectors with large-coefficient cases.
- **TS `BigInt` perf** → acceptable for the correctness-first fallback; WASM slice
  deferred. Documented.
- **`native` symbol skew / stale wheel** → in-tree build picks up the new symbols
  immediately; the wheel-republish lesson (#688) applies when this ships to the
  `goldenmatch-native` wheel. Parity tests `skipif` native is unbuilt.
- **Recall gate flakiness** → fixed seed for synthetic generation; the gate
  asserts a margin, not an exact number.
```
