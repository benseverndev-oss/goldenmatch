# GoldenCheck W1 — fused aggregate column checks — design

Date: 2026-07-11
Status: wave design (Arrow fused-scan program). Pending spec review + user approval.
Program: `2026-07-11-goldencheck-arrow-fused-scan-engine-program-design.md` + `...-W-path-scoping.md`. This is **W1** — the first wave that builds the fused-kernel scan pattern, on the lowest-risk checks. It also lands the **neutral dtype vocabulary + `dtype_category` shim** the whole W-path depends on.
Base: fresh `origin/main` (has W0-land arrow-in-core + parity harness; CSV wave enqueuing).

## Goal

Build a **fused Arrow-native Rust kernel** that computes, in ONE pass over a column, the aggregate stats the four cheapest checks need — **nullability, uniqueness, cardinality, type_inference** — plus the `ColumnProfile` fields. Rust = source of truth; the current Polars/`PyColumn` path is the parity oracle + fallback. Define the **owned neutral dtype vocabulary** + a single `dtype_category()` shim that replaces the ~15 duplicated `pl.Utf8`/numeric-tuple dtype checks. **Shadow discipline:** the fused path is computed + CI-compared but the 2.x output stays authoritative until the Flip.

These four checks are ALREADY polars-free via `scan_columns`/`PyColumn` (S2.1). W1 is NOT about eviction — it is about (a) FUSING them into one kernel pass (perf/peak-RSS + the pattern for W2-W5), (b) making them Rust-authoritative, (c) landing the dtype vocabulary, (d) wiring the fused kernel into BOTH scan paths (`scan_columns` + the full-scan column loop, which still uses raw `pl.Series` ops).

## The neutral dtype vocabulary (the cross-cutting decision, fixed here)

Adopt the vocabulary the Frame seam's `_neutral_dtype` already uses: **`str`, `int`, `uint`, `float`, `date`, `datetime`, `bool`, `other`** (8 categories). This becomes the owned `inferred_type` value + the input to every dtype gate. It is COARSER than Polars' `str(dtype)` (`"Int64"` -> `"int"`, losing bit-width — nothing in the data-quality reporting depends on bit-width; documented).

- **`dtype_category(&dyn Array) -> DtypeCat`** in `goldencheck-core` (Arrow type -> the 8 categories), + a Python `dtype_category()` mirror. The ~15 duplicated `col.dtype in (pl.Utf8, pl.String)` / numeric-tuple checks across `scanner.py`, `_post_classification_checks`, `profilers/*`, `relations/*`, `semantic/classifier.py` collapse into this one shim (mechanical; do it incrementally, not all at once — W1 lands the shim + converts the type_inference + column-loop sites; later waves convert their own).
- **`inferred_type` stays `str(pl.dtype)` (user-visible) until the Flip** — the fused kernel produces the neutral vocabulary, and the neutral-vs-Polars-string difference is a **registered divergence** (the Flip gate's dtype-string bucket). W1 does NOT change what users see; it just makes the neutral form available + authoritative-in-shadow.

## The fused kernel

`column_aggregate(array: &dyn Array) -> ColumnAgg` in `goldencheck-core` (new `fused.rs` or `aggregate.rs`):
```rust
pub struct ColumnAgg {
    pub len: usize,          // total rows
    pub null_count: usize,   // from the Arrow null bitmap (correctness upgrade — bitmap-direct)
    pub n_unique: usize,     // distinct non-null values (hash set; null counted per the seam: cardinality counts null-as-1, uniqueness post-drop-nulls)
    pub dtype: DtypeCat,     // the neutral category
    // room to grow (min/max land in W2 for range)
}
```
- **ONE pass**: stream the null bitmap for `null_count`; build a hash set for `n_unique` in the same pass; `dtype` from the array type (O(1)). (n_unique's null-counting must match the seam exactly: `cardinality` uses full-column `n_unique` counting null as 1 distinct; `uniqueness` uses post-`drop_nulls` n_unique. Decide whether the kernel returns both `n_unique_all` and `n_unique_nonnull`, or the caller adjusts by +1 when a null exists — match `PyColumn.n_unique` / `_neutral_dtype` behaviour exactly, verified by parity.)
- Native shim + `_COMPONENT_SYMBOLS["column_aggregate"]`. Arrow-in (reuse W0-land's `arrow_support`).

## Wiring (shadow)
- **`scan_columns` (polars-free, PyColumn-backed):** the nullability/uniqueness/cardinality/type_inference profilers currently each call `col.null_count()` / `col.n_unique()` / `col.dtype` on `PyColumn`. Route these through `column_aggregate` (one kernel call per column feeding all four) when `native_enabled("column_aggregate")`; PyColumn stays the fallback. This is where the fused kernel becomes REAL + authoritative (scan_columns is already polars-free, so no shadow needed here — it's a direct swap, parity-gated).
- **Full scan (`_scan_dataframe_impl` column loop, raw `pl.Series`):** compute `column_aggregate` in SHADOW — convert each `pl.Series -> Arrow (to_arrow()) -> column_aggregate`, compare to the Polars-computed `null_count`/`n_unique`/`dtype` via the parity harness, but the **authoritative `ColumnProfile` values stay the Polars ones** until the Flip. (At the Flip, the full scan takes Arrow directly and the fused values become authoritative.)
- The redundant double-compute (the column loop computes null_count/n_unique for `ColumnProfile` AND the profilers recompute) is noted; fully collapsing it is a Flip-time cleanup, not W1.

## Parity / contract
- `column_aggregate` (native) == the `PyColumn`/Polars reductions: `null_count` exact, `n_unique` exact (matching the seam's null-counting), `dtype` == `_neutral_dtype`. Registered in the W0-land parity harness, **empty divergence registry** (these are integer/enum-exact).
- No user-visible change (shadow); the neutral-vs-Polars `inferred_type` string is the only divergence, deferred to the Flip.
- `import goldencheck` loads zero polars; existing suite green (the swap in `scan_columns` is parity-locked; the full-scan authoritative output unchanged).

## Testing
- Rust: `column_aggregate` + `dtype_category` over Arrow arrays (null/empty/single/all-null; each of the 8 dtype categories; n_unique with + without nulls).
- Parity harness: native `column_aggregate` == `PyColumn` reductions on random + adversarial columns (register `column_aggregate`, empty divergence).
- Python: `scan_columns` findings for nullability/uniqueness/cardinality/type_inference are IDENTICAL with the fused kernel routed in (parity-locked — existing `scan_columns` tests pass UNEDITED). The full-scan `ColumnProfile` values unchanged (authoritative Polars path untouched); a shadow test asserts the fused values MATCH the Polars ones on a fixture.
- `dtype_category` shim: the converted call sites (type_inference + the column-loop dtype gates) behave identically (existing tests unedited).

## Risks
- **n_unique null-counting mismatch** — the seam's cardinality-counts-null-as-1 vs uniqueness-post-dropnull is subtle; the kernel must expose whatever the callers need + parity-lock it. Top risk (a silent off-by-one in a count).
- **Shadow conversion cost** — `pl.Series.to_arrow()` per column in the full scan adds work; it's shadow (CI/divergence-corpus), acceptable, and disappears at the Flip when input is Arrow.
- **dtype_category incremental conversion** — converting only SOME of the ~15 dtype sites in W1 risks inconsistency; convert a coherent set (type_inference + the ColumnProfile loop) + leave the rest with a tracking note; do NOT half-convert a single profiler.
- **arrow-rs lockstep** (W0-land's standing risk) — the new kernel uses the same `arrow=59`.

## Non-goals
- min/max/histogram (W2 range). No other checks. No changing user-visible `inferred_type` (Flip). No collapsing the full double-compute (Flip). No removing PyColumn (it's the fallback). No new dtype categories beyond the 8.
