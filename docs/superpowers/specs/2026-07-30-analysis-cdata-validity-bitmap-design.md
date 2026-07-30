# GoldenAnalysis frame-kernel C-Data ABI — Arrow-conformant packed validity bitmap

**Status:** design (2026-07-30). Closes the last open box of #1788 ("packed validity
bitmap instead of byte-per-row"), reframed from a micro-optimization to a
**shared-capabilities-conform** change (the North Star decision test): make the frame
kernels' C-Data column ABI speak Apache Arrow's *actual* validity layout.

## Problem

The Wave-1b frame-kernel interner (`analysis-core::intern_f64/intern_i64/intern_str`)
takes validity as a **byte-per-row** `&[u8]` (`is_null` = `validity[i] == 0`). Arrow's
C Data Interface — the layout this ABI is meant to mirror — encodes validity as a
**packed LSB-first bitmap** (1 bit/element, bit *set* = valid; the buffer is absent
entirely when `null_count == 0`). So the current ABI is a bespoke stand-in, not the
shared standard, with two concrete costs:

- **`analysis-native` expands Arrow's packed null bitmap into a byte-per-row `Vec<u8>`
  per column** (`validity!` macro: `(0..n).map(|i| a.is_null(i) …).collect()`) purely to
  satisfy the byte-per-row contract — an O(n) allocation on the *production* (Python
  wheel) path, discarding the buffer Arrow already has.
- The JS→wasm crossing ships `n` validity bytes where Arrow ships `ceil(n/8)`.

Neither is a perf emergency (the #1788 boundary bench showed the validity copy is a
tiny fraction of the call). The point is **conformance**: one Arrow-standard validity
representation across native + wasm + TS, so native passes Arrow's own buffer through
instead of translating it.

## Change

**Validity encoding only** — values, offsets, and every kernel's output are byte-for-byte
unchanged. The ids the kernels emit do not move; only how "row i is null" is expressed
across the boundary changes.

- **`analysis-core`**: `is_null(validity, i)` reads bit `i` of a packed LSB-first bitmap —
  `validity.get(i >> 3).map_or(false, |&b| (b >> (i & 7)) & 1 == 0)`. Arrow convention:
  bit set = valid, bit clear = null; an **empty** slice = "no null buffer" = all valid
  (Arrow's `null_count == 0` optimization). The three `intern_*` signatures are unchanged
  (`validity: &[u8]`); only its interpretation (byte-per-row → bitmap) changes.
- **`analysis-native`**: replace the byte-per-row `validity!` expansion with the array's
  Arrow null buffer directly. `None` (no nulls) → empty slice (all valid); `Some` with
  bit-offset 0 (the common case for a full column from Polars `.to_arrow()`) → the null
  buffer's packed bytes **zero-copy**; `Some` with a non-zero offset (a sliced array) →
  realign into a fresh `ceil(n/8)`-byte bitmap (still 8× smaller than the old per-row
  `Vec<u8>`, and correct). Values stay per-element `arr.value(i)` (offset-safe, unchanged).
- **`analysis-wasm`**: exports forward to core unchanged; only the doc comments change to
  say `validity` is a packed Arrow bitmap.
- **TS (`aggregate.ts::columnToBuffers`)**: build a packed LSB-first `Uint8Array`
  (`bits[i>>3] |= 1 << (i&7)` for valid rows) instead of a byte-per-row array, and read it
  by bit at the two sites that consume it (string-bytes assembly, numeric value fill).
  Only the **wasm** path uses `columnToBuffers`; the pure-TS `rowKey` path is untouched.

## Invariant & gates

Output is byte-identical — the existing parity locks are the gate, unchanged:

- `analysis-core` unit tests (`intern_*_matches_fixture`) — validity literals wrapped in a
  `pack(&[valid…])` test helper (byte-per-row → bitmap); assertions unchanged.
- `frame_kernels_adversarial.json` cross-surface fixture — Python↔TS↔native↔wasm still
  agree exactly (the numbers don't move).
- CI lanes `analysis_native` (Python native == pure) and `analysis_wasm` (builds the wasm,
  runs `wasm-frame-kernels.parity.test.ts` un-skipped) are the standing cross-surface gate.

## Non-goals

- No change to the values/offsets buffers or any kernel's math.
- No `validity_offset` parameter in the core/wasm ABI — offset is a native-only concern
  (native realigns a sliced array before the call); wasm/TS always emit offset-0 bitmaps.
