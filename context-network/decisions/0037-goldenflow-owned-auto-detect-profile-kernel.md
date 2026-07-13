# 0037 — GoldenFlow: owned auto-detect profile kernel, cross-surface

**Status:** Accepted • **Shipped:** `goldenflow-native 0.27.0` + `goldenflow 2.1.0`; native-first under `GOLDENFLOW_NATIVE=auto` (opt-out `=0`)

## Context

The owned-kernel program (ADRs
[0031](0031-goldenflow-reference-mode-identifiers-wasm.md) /
[0032](0032-goldenflow-duckdb-compiled-extension.md) /
[0034](0034-goldenflow-fused-columnar-apply.md)) made `goldenflow-core` the
single owned reference for every *transform's* logic. But zero-config's
**type-inference / profiling decision** — "what type is this column?", which
drives *which* transforms get selected — was still computed in Python
(`_infer_type` / `_infer_type_list` regex heuristics) on every surface, byte-for-
byte unowned. The decision that gates the whole zero-config path was the last
big unowned surface.

## Decision

**Own the auto-detect decision as a `goldenflow_core::profile` kernel.**
`infer_type(values, hint) -> String` is the owned decision on every surface (the
Polars columnar path `profile_dataframe` → `_profile_column`, the Polars-free
list/dict path `profile_columns` → `_infer_type_list`, the `goldenflow-native`
wheel, and `goldenflow-wasm` / the TS `inferType`). `profile_column(values,
hint) -> ColumnProfileOut` is the fused columnar (Path 1) wrapper: one FFI call
returns `inferred_type` + null/unique/samples, Polars-free. Pure-Python
`_infer_type`/`_infer_type_list`/`_profile_column` and the pure-TS port are
retained as byte-matched fallbacks. Wired via a new `profile` `_native_loader`
component (floor symbol `infer_type_list_arrow`).

Load-bearing design points:
- **A SURFACE, not a registered transform.** The profiler is not a
  `@register_transform` entry, so it is NOT in the
  `test_owned_kernel_boundary.py` buckets (those enumerate `registry()`). It is
  documented as a distinct owned surface in
  `docs/design/2026-07-06-goldenflow-owned-kernel-boundary.md`.
- **Byte-parity oracle = goldenflow-core**, proven by
  `tests/parity/profile_corpus.jsonl` (mirrored into the TS package), the same
  mechanism as the identifier corpus.
- **Dtype-hint vs value-hint contract.** Path 1 (Polars, dtype-hinted) matches
  `_infer_type`'s dtype short-circuit; Path 2a (list, value-hinted) matches
  `_infer_type_list`'s value-check — the two Python references already differ
  (list path has no `Date` case), so the kernel takes an explicit `TypeHint`
  rather than re-deriving it.

## Consequence

The zero-config decision is now cross-surface byte-identical and Polars-free on
the columnar path, opt-out via `GOLDENFLOW_NATIVE=0`. Base `goldenflow-native`
floor bumped to `>=0.27.0` so the kernel reaches users out of the box.

**Accepted known edge (corpus-unexercised follow-up):** the pure-TS profiler
builds its `≤100`-value sample as **strip-then-slice** while Python/Rust do
**slice-then-strip**. On a column with `>100` non-null values containing empty
strings among the first 100, the surfaces can pick a different 100-value window
and infer a different type. Treated as a documented reference-mode lossy TS
fallback; tracked as a follow-up to align the TS sampling order. Native / Polars
/ list-Python paths all slice-then-strip identically.
