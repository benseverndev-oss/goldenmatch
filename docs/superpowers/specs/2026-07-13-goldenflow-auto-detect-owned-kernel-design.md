# GoldenFlow auto-detect → owned Rust arrow-native fused kernel

**Date:** 2026-07-13
**Status:** Design — approved for spec review
**Owner:** Ben Severn
**Area:** `packages/rust/extensions/goldenflow-core`, `native-flow`, `goldenflow-wasm`, `packages/python/goldenflow`, `packages/goldenflow-js`

## Summary

Port GoldenFlow's zero-config **auto-detect** decision (the column *profiling +
type inference* that drives `transform_df(df, config=None)`) from Python/Polars
into an owned `goldenflow-core` kernel, exposed over two native execution paths
(zero-copy Arrow columnar **and** arrow-free list/WASM) with cross-surface
byte-parity. The transform **apply** path is already owned-Rust/arrow-native/
fused (Pillar-1 complete); this closes the last significant Python-owned compute
in the zero-config path so the whole auto-detect flow is Rust-authored under the
[[project_rust_is_the_reference]] thesis.

## Motivation (driver, ranked)

1. **Reference-mode / owned-kernel (primary).** Profiling + type inference is the
   only stage of auto-detect still computed in Python (`_infer_type` /
   `_infer_type_list` regex heuristics). Owning it in `goldenflow-core` with
   native/WASM/Python byte-parity makes the zero-config decision Rust-authored
   and cross-surface, matching the Wave 0-D program.
2. **Polars-free profiling (consequence).** The columnar path profiles the native
   Arrow `Column` with zero Polars/pyarrow, extending the polars-eviction thesis
   ([[project_goldenflow_polars_eviction]]) to the auto-detect decision.
3. **Speed (measured, NOT gated).** Type inference samples only ≤100 rows/column
   and `n_unique` is already vectorized, so per the repo's measured NO-GO lessons
   (frame-container + arrow-everywhere spikes) this is unlikely to be a wall win.
   We measure honestly and do **not** gate the flip on a speed number. The bar is
   **byte-identical output + no regression**, same as the fused-apply flip.

## Current state (origin/main)

Auto-detect (`transform_df` zero-config) has three stages:

1. **Profile** — `profile_dataframe` (Polars path) → `_profile_column` → `_infer_type`;
   and `profile_columns` (Polars-free list/dict path) → `_infer_type_list`. Both
   compute `null_count`, `unique_count`, first-5 `sample_values`, and
   `inferred_type`. Type inference runs five hand-written regexes
   (`email`/`zip`/`date`/`phone`/`name`) over the first ≤100 non-null stripped
   values with per-type thresholds (0.7/0.7/0.5/0.6/0.5), most-specific first.
2. **Select** — `select_transforms(ColumnProfile)`: pure-Python metadata dispatch
   over the transform registry (`auto_apply` + `input_types` + `priority`), plus
   the `unique_pct > 0.1` suppression of `category_auto_correct`.
3. **Apply** — already owned-Rust, arrow-native, fused, default-on.

`goldenflow-core` owns transform kernels (`chain.rs`, `text.rs`, …) but has **no**
profiling/inference module. The native `Column` pyclass
(`native-flow/src/column.rs`) already ingests Polars data zero-copy via
`__arrow_c_stream__` and downcasts to `StringArray`/`LargeStringArray`/
`Int64Array`/`Float64Array`.

## Goals

- One owned `goldenflow_core::profile` kernel computing the full `ColumnProfile`
  (null_count, unique_count, ≤5 samples, inferred_type) in a single traversal.
- Two native execution paths over one core: **columnar** (zero-copy Arrow
  `Column.profile()`, the scale path threaded through `engine/columnar.py`) and
  **arrow-free** (`profile_column_list_arrow(list)` + goldenflow-wasm export → TS).
- Byte-identical `inferred_type` and decision-equivalent `unique_pct` vs both
  current reference functions, proven by a cross-surface parity corpus.
- Pure-Python `_infer_type`/`_infer_type_list`/`_profile_column` retained as the
  byte-identical fallback (loader falls back when native/WASM absent).

## Non-goals

- **Do not** port `select_transforms` to Rust (metadata dispatch tied to the
  Python registry; near-zero compute; would require mirroring the registry).
- **Do not** touch the GoldenCheck-backed profile path in `profile_dataframe`
  (the `scan_file` branch). The kernel replaces only the **built-in** profiler
  and the **list** profiler.
- **Do not** change the LLM-scan branch or `detected_format` semantics.
- **No** new dependency (no `regex` crate — hand-roll matchers, per email.rs/
  address.rs precedent, for cross-runtime parity).
- Dates remain excluded from owned per-row parsing (unchanged; inference only
  *classifies* a column as `date`, it does not parse).

## Architecture

```
                    goldenflow_core::profile   (arrow-free, always compiled = ORACLE)
                    profile_column(values: &[Option<&str>], hint: TypeHint)
                        -> ColumnProfileOut { null_count, unique_count,
                                              samples: Vec<String>, inferred_type }
                             ▲                         ▲                    ▲
             ┌───────────────┘                         │                    └───────────────┐
   Path 1: COLUMNAR (zero-copy Arrow)        Path 2a: arrow-free list        Path 2b: WASM/TS
   native-flow  Column.profile()            native-flow                      goldenflow-wasm
   - StringArray -> &str view (no copy)      profile_column_list_arrow(list)   profile_column(values,hint)
   - typed arrays: null/unique off buffers   (mirrors apply_chain_str_list)    -> TS profiler
     + hint, no regex
```

### `goldenflow_core::profile` (new module)

- `enum TypeHint { Utf8, Numeric, Boolean, Date }` — lets one kernel serve both
  reference behaviors (see Parity Contracts).
- `struct ColumnProfileOut { null_count: u64, unique_count: u64, samples: Vec<String>, inferred_type: String }`.
- `fn profile_column(values: &[Option<&str>], hint: TypeHint) -> ColumnProfileOut`
  — always-compiled, arrow-free (mirrors `apply_chain_str`). Single pass:
  - `null_count` = count of `None`;
  - `unique_count` = `HashSet<&str>` over non-null (string case) — see contract 2
    for typed columns;
  - `samples` = first 5 non-null values as `String`;
  - `inferred_type`:
    - `hint == Numeric` → `"numeric"`; `Boolean` → `"boolean"`; `Date` → `"date"`
      (skip regex — matches `_infer_type`'s dtype short-circuit);
    - `hint == Utf8` → the five hand-rolled matchers over the first ≤100 non-null
      **stripped, non-empty** values with thresholds 0.7/0.7/0.5/0.6/0.5, most-
      specific first, else `"string"`.
- Five matcher fns (`is_email`/`is_zip`/`is_date`/`is_phone`/`is_name`) hand-rolled
  to the exact semantics of `_EMAIL_RE`/`_ZIP_RE`/`_DATE_RE`/`_PHONE_RE`/`_NAME_RE`.
  These are the parity-critical owned logic; unit-tested against a shared corpus.
- **Column-name override** (`_override_type_by_column_name`) stays in Python — it
  is name-string metadata, not column compute, and is applied by the caller after
  the kernel returns (keeps the kernel a pure column-values function). Documented
  in the module header so the boundary is explicit.

### `native-flow` (thin shim)

- `Column.profile(&self) -> PyResult<ProfileDict>` (Path 1): downcast `self.array`:
  - `Utf8`/`LargeUtf8`/`Utf8View` → cast to `LargeStringArray` if needed (same as
    the chain path), build a `Vec<Option<&str>>` **view** (borrowed slices, no
    string copy), call `profile_column(view, TypeHint::Utf8)`.
  - `Int64`/`Float64` → `TypeHint::Numeric`; `Boolean` → `TypeHint::Boolean`;
    Date/Timestamp → `TypeHint::Date`. For typed arrays, null/unique read off the
    typed buffer; samples formatted to match the Polars `cast(Utf8)` string form
    (see contract 2/3); `inferred_type` from the hint (no regex).
- `profile_column_list_arrow(values: Vec<Option<String>>, hint: &str) -> ProfileDict`
  (Path 2a): mirrors `apply_chain_str_list`; borrows `&str` from the owned
  `String`s and calls the core fn. `hint` string maps to `TypeHint`.
- Return shape: a Python dict `{null_count, unique_count, samples, inferred_type}`
  (or a small pyclass) that `profiler_bridge` maps into `ColumnProfile`.
- New loader component `profile` (floor symbol `profile_column_list_arrow`) in the
  reference-mode loader; probe-gated (skew-safe: old wheel → Python fallback).

### `goldenflow-wasm` + TS

- `profile_column(values: Vec<Option<String>>, hint: String) -> JsValue` export
  (mirrors the identifier exports), backed by the same core fn.
- TS `profileColumn` in the profiler surface + `enableWasm()` dispatch; pure-TS
  port of `_infer_type`/profiling as the default/fallback (byte-matched to Rust).

### Python wiring (`engine/profiler_bridge.py`)

- `profile_columns(dict[str,list])` → **Path 2a**: for each column, call native
  `profile_column_list_arrow(values, hint)` where `hint` is derived exactly as
  `_infer_type_list` does today (all-bool → `boolean`; all-int/float-not-bool →
  `numeric`; else `Utf8`). Apply `_override_type_by_column_name` to the result.
  Pure-Python `_infer_type_list`/current body stays as the fallback when native
  is absent.
- `profile_dataframe` built-in fallback (the `_profile_column` loop) → **Path 1**:
  build a native `Column.from_arrow(df.select([col]))` (zero-copy, pyarrow-free)
  and call `Column.profile()`; hint derived from the Polars dtype (numeric/bool/
  temporal/else-Utf8, exactly `_infer_type`'s dtype checks). Pure-Python
  `_profile_column` stays as fallback. GoldenCheck branch untouched.
- The columnar engine's `_autoconfig_columns` already calls `profile_columns`, so
  it inherits Path 2a with no change.

## Data flow

```
transform_df(df, config=None)
  └─ profile_dataframe(df)                  [GoldenCheck path unchanged]
        └─ built-in fallback ─► Column.from_arrow(df.select([col])) ─► Column.profile()  [Path 1]
                                    └─ goldenflow_core::profile::profile_column(view, hint)
  └─ select_transforms(ColumnProfile)       [Python, unchanged]
  └─ apply (fused owned chain)              [unchanged]

transform_columns_public(dict|csv, config=None)   [Polars-free]
  └─ _autoconfig_columns(data)
        └─ profile_columns(dict)  ─► profile_column_list_arrow(values, hint)   [Path 2a]
                                        └─ goldenflow_core::profile::profile_column(view, hint)
```

## Parity contracts (load-bearing)

1. **Two reference behaviors, one kernel, via `TypeHint`.**
   `_infer_type` (Polars) short-circuits numeric/boolean/date **by dtype** and has
   a `Date`/`Datetime` case; `_infer_type_list` has **no** Date case and decides
   numeric/boolean **by Python `isinstance` over values** (date-looking *strings*
   still match `_DATE_RE`). The caller computes the hint the same way the
   respective reference does and passes it; the kernel never re-derives dtype. So
   Path 1 (dtype-hinted) matches `_infer_type` and Path 2a (value-hinted) matches
   `_infer_type_list` — byte-identical `inferred_type`.

2. **`unique_count` is exact for strings; typed columns read buffers; float edge
   is documented reference-mode and MUST NOT flip the gate.**
   `select_transforms` reads only `inferred_type` + `unique_pct`, and `unique_pct`
   feeds exactly one decision: `> 0.1` suppresses `category_auto_correct`. String
   columns: `HashSet<&str>` = exact vs `n_unique`. Typed columns: unique read off
   the typed Arrow buffer. `Float64` `NaN`/`-0.0` may differ between Polars
   `n_unique` and a hash — documented as reference-mode-resolved; a parity test
   asserts the `unique_pct > 0.1` **decision** matches on a float corpus even where
   the raw count may differ by the NaN/-0.0 edge. (Numeric columns are `numeric`
   and get no `category_auto_correct` anyway, so this never changes selection.)

3. **`sample_values` byte-identical for strings; typed samples match `cast(Utf8)`.**
   Reference: `non_null.head(5).cast(pl.Utf8).to_list()` (Polars) / `str(v)` for the
   first 5 (list). String columns: identical (first 5 non-null). Typed columns:
   format to match Polars `cast(Utf8)` — reuse `float_fmt::float_to_polars_string`
   for `Float64` (already the proven Polars float form), decimal for ints, Polars
   bool form (`true`/`false`) for booleans. `sample_values` are display-only (not
   read by `select_transforms`), so this is a display-parity nicety, not a decision
   contract — but we still assert it in the corpus.

4. **Column-name override applied by the caller, after the kernel.** The kernel is
   a pure function of column *values*; `_override_type_by_column_name` (name-string
   heuristic) stays in Python and wraps the kernel result, byte-identical to today.

5. **Opt-in / fallback-safe.** Native/WASM used only when importable (reference-
   mode `_has_symbol` + probe); absent → pure-Python path, output byte-unchanged.
   Default path stays byte-identical; no behavior flip is required to ship.

## Testing

- **Rust unit tests** in `profile.rs`: each matcher (`is_email` … `is_name`) and
  `profile_column` over pinned vectors covering every threshold boundary, empty/
  all-null columns, stripped-empties, mixed match ratios straddling each threshold,
  and each `TypeHint`.
- **Cross-surface parity corpus** `tests/parity/profile_corpus.jsonl` (new; mirrors
  `identifiers_corpus.jsonl`): each row = `{ values: [...], hint, expected: {
  inferred_type, null_count, unique_count, samples } }`. Generator
  `scripts/gen_profile_corpus.py --check` drift guard (oracle = `goldenflow-core`).
- **`tests/transforms/test_profile_kernels.py`** (pinned-vector, since output is a
  struct not string→string): fallback-path always + native-path in the native lane;
  named `*native*` so the fallback lane `-k "not native"` deselects it.
- **Decision-equivalence test**: over a float corpus straddling `unique_pct = 0.1`,
  assert the `select_transforms` output (specifically `category_auto_correct`
  presence) is identical native-vs-Polars even where raw `unique_count` differs by
  the NaN/-0.0 edge.
- **TS parity** `profile.parity.test.ts`: pure-TS always + wasm `skipIf`-no-artifact;
  corpus is a byte-copy of the Python oracle corpus (cmp-enforced), new `wasm_flow`
  leg entry.
- **Engine smoke**: `transform_df(df, config=None)` and
  `transform_columns_public(dict, None)` produce identical `Manifest` +
  `inferred_type` selections with native on vs off (`GOLDENFLOW_NATIVE=0/1`),
  across a mixed-type fixture (email/zip/date/phone/name/numeric/bool/null-heavy).
- **Speed measurement (report-only)**: extend `benchmarks/` with a profile bench;
  record native-vs-Polars wall at 1M/5M in the PR body. Not a gate.

## Versioning / release

- Bump `goldenflow-core` (new module + `lib.rs` `pub mod profile` → forces rebuild,
  so no stale-core cache-bust needed), `native-flow`, `goldenflow-wasm`, and the
  three lockstep version spots per the native-wheel rule; `cargo update -p
  goldenflow-core` on both dependent lockfiles.
- Republish `goldenflow-native` (verify `profile_column_list_arrow` symbol in the
  built wheel via `grep -a`, per the wheel-skew lesson) before claiming the Polars-
  free path reaches PyPI users; bump `goldenflow` floor; `golden-suite` lockstep.
- npm: goldenflow bump; verify `.wasm` in the tarball (the wasm-never-shipped
  lesson).
- Docs sweep at the end (rollout-docs-sweep): CLAUDE.md profiler section, ADR,
  performance.mdx, CHANGELOG; the owned-kernel boundary doc/test
  (`test_owned_kernel_boundary.py`) classification updated for the new kernel.

## CI landmines (pre-flighted from prior waves)

- Run the full pre-push routine locally: core `clippy --all-targets -D warnings`
  (lints benches + `unreachable_patterns`), `cargo fmt --check` on **native-flow**
  and every touched crate (fmt ≠ clippy; CI fmt-checks native-flow), whole-package
  `ruff check packages/python/goldenflow` (isort I001 on new first-party imports),
  and `grep -nE '[A-Za-z0-9]\*/' *.ts` (JSDoc `*/` bug) before any TS push.
- TS is CI-only (box OOMs vitest): statically cross-check corpus keys ==
  pure-TS-fn keys == wasm-map keys before push.

## Risks

- **Float `unique_count` edge** — mitigated by contract 2 (decision-equivalence
  test; numeric never selects `category_auto_correct` anyway).
- **`Utf8View` ingest** — handled exactly as the chain path (cast to `LargeUtf8`
  on ingest; the hard-won Series-vs-DataFrame stream + StringView gotchas from
  P1b apply here — always pass a 1-col DataFrame to `Column.from_arrow`).
- **Speed non-win** — accepted and pre-authorized; ship on byte-identity + no
  regression, exactly like the fused-apply flip.

## Out of scope / future

- `select_transforms` in Rust (metadata; no compute win).
- GoldenCheck semantic-classifier port (that lives in GoldenCheck).
- Native per-row date parsing (unchanged exclusion).
- DuckDB profiling surface (extension already compiled; ~0 gain — skip).

## Links

[[project_rust_is_the_reference]] · [[project_goldenflow_owned_kernel_cross_surface]] ·
[[project_goldenflow_polars_eviction]] · [[project_goldenflow_fused_columnar_apply]] ·
[[project_688_stale_native_wheel]] · [[feedback_verify_perf_not_just_ship]] ·
[[reference_dates_chrono_dateutil_parity]]
