# GoldenAnalysis Wave 1b (WASM surface for the frame kernels) — consciously deferred

**Status:** DEFERRED (decision 2026-07-06). Not a backlog item to pick up by default —
revisit only if the trigger below fires.

## What Wave 1b would have been

Waves 2 and 3 gave their kernels a **browser/edge (WASM)** surface, so the same Rust
runs in a browser instead of TypeScript re-implementing it. Wave 1's frame kernels
(`null_ratio_per_column`, `duplicate_row_ratio`, `distinct_count`) never got that
surface. Wave 1b = add it, so the Rust dedup/distinct code runs in the browser too.

## Why it's deferred (no clean boundary)

The numeric kernels (Waves 2/3) crossed to WASM cleanly because they take a **flat
array of numbers** — the browser hands that to Rust for free as a `Float64Array`.

The frame kernels do not. To count duplicates/distinct you first decide *when two
values are equal*, including `-0.0` vs `0.0`, `NaN` vs `NaN`, null vs empty-string, int
vs float. Wave 1 handles this by **interning**: `analysis-native` walks each **Apache
Arrow** column and assigns every distinct value a `u64` id (applying `canon_f64_bits`),
then the Rust core just counts over ids. That interning is Arrow-specific and is the
semantically load-bearing part.

The browser doesn't have Arrow — TS operates on plain row objects. So reaching WASM
requires one of:

- **Arrow-in-wasm** — convert `FrameRows`→Arrow in JS, ship IPC bytes to WASM, run the
  same `intern_column`. Thesis-pure (one canon source) but **heavy**: bundles an
  arrow-rs IPC reader (large WASM bloat) + a JS-side conversion that for typical frames
  costs more than the dedup itself.
- **JS-value intern port** — a second interning implementation over heterogeneous JS
  values. New drift surface (two interns to keep byte-identical) — defeats the point.
- **Thin (TS stringifies, WASM counts)** — trivial kernel; string marshaling across the
  boundary is net-negative; canon stays duplicated in JS. No real win.

And the payoff is speculative: GoldenAnalysis is a reporting engine consuming other
packages' artifacts; in-browser dedup of large frames is not an established workload.

## What we did instead (the high-value slice, without WASM)

The only downside of deferring with teeth was that the **TS↔Python equality semantics
were unproven at the edges** — the one existing cross-surface lock
(`report_frame_summary.json`) uses a benign frame with no `-0.0`/`NaN`/null-vs-empty.
Sharing the Rust kernel would remove that risk by construction, but a cheap fixture buys
most of the protection.

So we added **`frame_kernels_adversarial.json`** (byte-identical copy in both packages'
`tests/fixtures/`) + `test_frame_kernels_parity.py` / `frameKernels.parity.test.ts`,
locking `distinct_count` / `null_ratio` / `duplicate_row_ratio` Python↔TS across
scenarios: `float_nan_null` (`-0.0`/`0.0` fold, `NaN` fold, `NaN`-vs-null), `typed_numeric`
(int vs float), `string_empty_null`, and a `mixed` multi-column frame. Frames are built
in CODE on both sides (JSON can't hold `NaN`/`-0.0`); the fixture holds only finite
outputs.

**This found and fixed a real bug.** TS `duplicateRowRatio` keyed rows via a single
`JSON.stringify(cols.map(...))`, which serialized both `NaN` and null to JSON `null` —
so it **conflated NaN and null**, over-counting duplicates on any column mixing them
(`float_nan_null`: reported `1.0` vs Python's `6/7`). TS's own `nUnique` already keyed
them distinctly, so `duplicateRowRatio` was inconsistent even with itself. Fixed by
keying each cell the way `nUnique` does (`aggregate.ts::rowKey`); verified no change on
NaN-free frames (the existing `report_frame_summary` lock is unaffected).

## When to revisit

Pick Wave 1b back up ONLY if a **real in-browser/edge frame-dedup workload** appears
(someone running `frame.summary` on large frames client-side and hitting the JS-hashing
ceiling). At that point the **Arrow-in-wasm** path is the thesis-pure choice — weigh its
WASM-bloat + JS-conversion cost against the measured workload, don't build it on spec.
Until then the equality-semantics parity is enforced by the fixture above and the ratios
are locked by `report_frame_summary.json`.
