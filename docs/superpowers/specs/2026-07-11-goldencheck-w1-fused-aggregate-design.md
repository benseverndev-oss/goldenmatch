# GoldenCheck W1 — fused aggregate column checks — design

Date: 2026-07-11
Status: wave design (Arrow fused-scan program). Pending spec review + user approval.
Program: `2026-07-11-goldencheck-arrow-fused-scan-engine-program-design.md` + `...-W-path-scoping.md`. This is **W1** — the first wave that builds the fused-kernel scan pattern, on the lowest-risk checks. It also lands the **neutral dtype vocabulary + `dtype_category` shim** the whole W-path depends on.
Base: fresh `origin/main` (has W0-land arrow-in-core + parity harness; CSV wave enqueuing).

## Goal

Build a **fused Arrow-native Rust kernel** `column_aggregate(&dyn Array)` that computes, in ONE pass over a column, the aggregate stats the cheap column checks need (`null_count`, `n_unique_nonnull`, `dtype`) — feeding **nullability, uniqueness, cardinality** and the `ColumnProfile`. Rust = source of truth; the current Polars/`PyColumn` path is the parity oracle. Define the **owned neutral dtype vocabulary** + a single `dtype_category()` shim that replaces the duplicated `pl.Utf8`/numeric-tuple dtype checks (used by the four checks' dtype gates, incl. `type_inference`'s gate).

**Corrected scope (from spec review):**
- The three MECHANICAL profilers in `scan_columns` are Nullability/Uniqueness/Cardinality (`_MECHANICAL_PROFILERS`, scanner.py). **`type_inference` is NOT in `scan_columns`** — it's full-scan-only and can't run on a `PyColumn` (it needs `.cast("float")`, which `PyColumn` doesn't implement). W1 touches type_inference only via its **dtype gate** (`dtype_category`), not a scan_columns swap.
- **`scan_columns` (list-backed `PyColumn` over `dict[str,list]`) is UNCHANGED in W1.** Routing an Arrow-in kernel there would need a per-column `list -> Arrow` build that may be net-negative on small columns AND re-introduce an Arrow-type-inference step that must match `PyColumn.dtype`'s first-non-null heuristic — not worth it now. Leave `scan_columns` on PyColumn (already polars-free).
- **The fused kernel targets the FULL-SCAN column loop** (`_scan_dataframe_impl`, raw `pl.Series`), in SHADOW: convert each `pl.Series -> Arrow (to_arrow()) -> column_aggregate`, CI-compare to the Polars-computed `ColumnProfile` stats, but the authoritative values stay Polars until the Flip (when the full-scan input becomes Arrow and the `to_arrow()` cost disappears).

So W1 = (a) the fused kernel + the pattern for W2-W5, (b) the neutral dtype vocabulary + `dtype_category` shim (collapsing the dtype-check debt), (c) Rust-authoritative-in-shadow for the full-scan aggregates. **Perf is measure-first**, not asserted — real fusion (one bitmap+hashset pass) is a genuine win over Arrow, but over Python lists it may not beat `set()`/`sum()`; W1's value is the pattern + vocabulary + Rust-authority, and any perf claim is measured before it's made (the repo's measure-wall-clock lesson).

## The neutral dtype vocabulary (the cross-cutting decision, fixed here)

Adopt the vocabulary the Frame seam's `_neutral_dtype` already uses: **`str`, `int`, `uint`, `float`, `date`, `datetime`, `bool`, `other`** (8 categories). This becomes the owned `inferred_type` value + the input to every dtype gate. It is COARSER than Polars' `str(dtype)` (`"Int64"` -> `"int"`, losing bit-width — nothing in the data-quality reporting depends on bit-width; documented).

- **`dtype_category(&dyn Array) -> DtypeCat`** in `goldencheck-core` (Arrow type -> the 8 categories), + a Python `dtype_category()` mirror. The ~15 duplicated `col.dtype in (pl.Utf8, pl.String)` / numeric-tuple checks across `scanner.py`, `_post_classification_checks`, `profilers/*`, `relations/*`, `semantic/classifier.py` collapse into this one shim (mechanical; do it incrementally, not all at once — W1 lands the shim + converts the type_inference + column-loop sites; later waves convert their own).
- **`inferred_type` stays `str(pl.dtype)` (user-visible) until the Flip** — the fused kernel produces the neutral vocabulary, and the neutral-vs-Polars-string difference is a **registered divergence** (the Flip gate's dtype-string bucket). W1 does NOT change what users see; it just makes the neutral form available + authoritative-in-shadow.

## The fused kernel

`column_aggregate(array: &dyn Array) -> ColumnAgg` in `goldencheck-core` (new `fused.rs` or `aggregate.rs`):
```rust
pub struct ColumnAgg {
    pub len: usize,               // total rows
    pub null_count: usize,        // from the Arrow null bitmap (correctness upgrade — bitmap-direct)
    pub n_unique_nonnull: usize,  // distinct EXCLUDING null (one hash set, non-null only)
    pub dtype: DtypeCat,          // the neutral category
    // room to grow (min/max land in W2 for range)
}
```
- **ONE pass**: stream the null bitmap for `null_count`; build ONE hash set over the non-null values for `n_unique_nonnull`; `dtype` from the array type (O(1)). NO second hash set.
- **Caller derivations (verified against the profilers):** uniqueness uses `n_unique_nonnull` directly (matches `non_null.n_unique()`); cardinality uses `n_unique_nonnull + (1 if null_count > 0 else 0)` (matches `PyColumn.n_unique() == len(set(self._v))`, which counts null as 1 distinct); nullability uses `len` + `null_count`; the full-scan `ColumnProfile.unique_count` also uses `n_unique_nonnull` (`non_null.n_unique()`). So one `n_unique_nonnull` + `null_count` covers all four — the "+1 in the caller" is the resolution of the flagged null-counting risk.
- **The kernel replaces the COUNTS + dtype gate ONLY** — not the value passes: cardinality's `sample_values` (`drop_nulls().unique().sort()`) and type_inference's numeric-cast still touch raw values (unchanged). W1 does not eliminate those.
- Native shim + `_COMPONENT_SYMBOLS["column_aggregate"]`. Arrow-in (reuse W0-land's `arrow_support`).

## Wiring
- **`scan_columns` (list-backed PyColumn): UNCHANGED in W1** (see Goal — no Arrow-in swap over Python lists this wave).
- **Full scan (`_scan_dataframe_impl` column loop, raw `pl.Series`): compute `column_aggregate` in SHADOW.** Convert each `pl.Series -> Arrow (to_arrow()) -> column_aggregate`; CI-compare (via the parity harness) to the Polars-computed `null_count`/`n_unique_nonnull`/`dtype`; the **authoritative `ColumnProfile` values stay the Polars ones** until the Flip (when the full-scan input becomes Arrow and `to_arrow()` disappears). No user-visible change.
- **dtype gates -> neutral, NOW, authoritative (output-identical).** `type_inference` (and the column-loop `is_string`/`is_numeric` gates) already branch on the NEUTRAL dtype semantics (`type_inference.py` reads `col.dtype -> _neutral_dtype`, branching on `"str"`/`"int"`/`"float"`; the column loop's `pl.Utf8`/numeric-tuple checks map 1:1 to `str`/`int`/`uint`/`float`). Routing these gates through `dtype_category` is output-identical PROVIDED `dtype_category(arrow) == _neutral_dtype(pl.dtype)` (parity-locked). This is authoritative now (not shadow) because it changes no output.
- **`ColumnProfile.inferred_type` string -> the ONE deferred divergence.** It is `str(pl.dtype)` (`"Int64"`) today; the neutral form (`"int"`) is a DIFFERENT value from anything the profilers' dtype gates emit. W1 does NOT change `inferred_type` (stays `str(pl.dtype)`, user-visible); the neutral-vs-Polars-string difference is registered for the Flip's dtype-string bucket.
- The redundant double-compute (loop computes counts for `ColumnProfile` AND profilers recompute) is noted; collapsing it is Flip-time cleanup, not W1.

## Parity / contract
- `column_aggregate` (native, on `pl.Series.to_arrow()`) == the Polars reductions: `null_count` exact, `n_unique_nonnull` == `non_null.n_unique()` exact, `dtype` == `_neutral_dtype(pl.dtype)`. Registered in the W0-land parity harness, **empty divergence registry** (integer/enum-exact).
- `dtype_category(arrow) == _neutral_dtype(pl.dtype)` — the parity that makes the gate-routing output-identical. Assert across all 8 categories.
- No user-visible change: `scan_columns` unchanged; the full-scan `ColumnProfile` authoritative values stay Polars; `inferred_type` string unchanged (its neutral-form divergence is deferred to the Flip).
- `import goldencheck` loads zero polars; existing suite green.

## Testing
- Rust: `column_aggregate` + `dtype_category` over Arrow arrays — null/empty/single/all-null; each of the 8 dtype categories; `n_unique_nonnull` with + without nulls; **NaN-float and heterogeneous inputs** (float NaN counts distinct differently across engines; include so the divergence, if any, is caught).
- Parity harness: native `column_aggregate` == Polars reductions on random + adversarial columns (register `column_aggregate`, empty divergence).
- Python: existing `scan_columns` tests pass UNEDITED (scan_columns untouched). The full-scan `ColumnProfile` values unchanged (authoritative Polars path); a SHADOW test asserts `column_aggregate` (on `pl.Series.to_arrow()`) MATCHES the Polars `null_count`/`n_unique`/`dtype` on a corpus. The `dtype_category`-converted gate sites (`type_inference` + the column-loop `is_string`/`is_numeric`) behave identically -> existing profiler/scanner tests pass UNEDITED.

## Risks
- **n_unique null-counting mismatch** — the seam's cardinality-counts-null-as-1 vs uniqueness-post-dropnull is subtle; the kernel must expose whatever the callers need + parity-lock it. Top risk (a silent off-by-one in a count).
- **Shadow conversion cost** — `pl.Series.to_arrow()` per column in the full scan adds work; it's shadow (CI/divergence-corpus), acceptable, and disappears at the Flip when input is Arrow.
- **dtype_category incremental conversion** — converting only SOME of the ~15 dtype sites in W1 risks inconsistency; convert a coherent set (type_inference + the ColumnProfile loop) + leave the rest with a tracking note; do NOT half-convert a single profiler.
- **arrow-rs lockstep** (W0-land's standing risk) — the new kernel uses the same `arrow=59`.

## Non-goals
- min/max/histogram (W2 range). No other checks. No changing user-visible `inferred_type` (Flip). No collapsing the full double-compute (Flip). No removing PyColumn (it's the fallback). No new dtype categories beyond the 8.
