# GoldenMatch autoconfig Arrow-port — the last polars island on zero-config

**Date:** 2026-07-13
**Status:** Design — approved (decisions locked), for spec review
**Owner:** Ben Severn
**Area:** `packages/python/goldenmatch/goldenmatch/core/{autoconfig.py, autoconfig_controller.py, blocker.py, profiler.py, indicators.py, pipeline.py, frame.py}`

## Summary

Make GoldenMatch's **zero-config** dedupe/autoconfig path run **Polars-free** by
Arrow-porting the autoconfig stack (autoconfig body + controller + blocker
measurement tail + profiler frame-fn) and the `run_dedupe_df` polars front-door
that the controller re-runs per iteration. This closes the last polars island the
ZERO-Polars eviction left: the dedupe SPINE is arrow-native, but
`auto_configure_df` unwraps `ArrowFrame -> pl.from_arrow(...)` and runs its
internals on polars, so `pip install goldenmatch` (no `[polars]`) + zero-config
`dedupe_df` raises `ModuleNotFoundError: No module named 'polars'`.

## Motivation

The eviction (3.0.0 "polars optional", "COMPLETE") ported the dedupe spine and
made `import goldenmatch` polars-free, but **autoconfig was only made
arrow-ACCEPTING, not arrow-native** — `core/autoconfig.py:auto_configure_df`
unwraps arrow to polars at the top (`df = cast("pl.DataFrame",
pl.from_arrow(df.native))`, comment "REMOVE in W5 when arrow flows past ingest";
W5 for autoconfig never landed). Consequences:

- **Zero-config dedupe requires polars** (it always calls autoconfig first).
- Multiple CI lanes that `pip install goldenmatch` without `[polars]` and run
  zero-config (rust bridge, rust_pgrx, duckdb_extensions, dbt) fail; a `[polars]`
  CI stopgap (#1747) masks it today.
- The "polars-optional" headline overstates: it holds for *configured* arrow-lane
  dedupe, not *zero-config*.

This port delivers true zero-config-without-polars so the stopgap can be dropped
and the eviction's claim becomes honest.

## Decisions (locked)

1. **Full scope — port the controller front-door.** The controller's
   `_run_pipeline_sample` re-runs `run_dedupe_df`/`run_match_df` (pipeline.py)
   whose FRONT still does `df.cast({...pl.Utf8...}).lazy()` + `_add_row_ids` +
   `collect` (pipeline.py:439/731/856) — a polars front-door the spine flip left.
   Port it (new `cast_all_str` frame op; `ensure_row_ids` exists) so the sample
   re-run is polars-free. This is the only way zero-config actually runs without
   polars.
2. **Dtype contract — map arrow→polars-spelling at the boundary.** The native
   `autoconfig_classify_columns` kernel expects the polars dtype spelling
   (`"Float64"`); arrow spells it differently (`"double"`). Keep the Rust kernel's
   vocabulary; map arrow→polars-spelling at the Python boundary (one localized
   table). No Rust change, no classifier golden-vector regen.
3. **Execution — autonomous, subagent-driven, per-PR reviewed**, mirroring the
   goldenflow feature. Checkpoint the user on the high-risk block-key parity PR.

## Current state (current main, 3.1.1)

`frame.py` is a mature 2126-line dual-backend seam: `Frame`/`Column` Protocols,
`PolarsFrame`/`ArrowFrame`, `arrow_derive.py` helpers, and the W3-era seam ops
autoconfig already partly uses. **Most autoconfig helpers were already seam-routed
by W3** (`docs/superpowers/plans/2026-07-11-goldenmatch-polars-eviction-w3.md`):
`profile_columns`, `_union_coverage`, `_source_disjoint`, `_check_source_overlap`,
all of `indicators.py`, `profiler.profile_column`, controller sampling. They
delegate byte-identically **today** because they receive the unwrapped polars
frame — the port removes the unwrap and lets them see an `ArrowFrame`.

Remaining polars-bound surfaces (the real work):

| Module | Remaining polars ops (KIND) |
|--------|------------------------------|
| `autoconfig.py` | boundary unwrap `pl.from_arrow` (~3595); `LazyFrame.collect`; `df.height`/`.columns` scalars (Frame exposes these); `str(df[col].dtype)` fed to native classify (dtype contract); `df.filter(pl.col().is_not_null())`; file readers `pl.read_csv/parquet/excel`+`pl.concat` in `auto_configure(files)` |
| `autoconfig_controller.py` | mostly type hints; 2× `pl.concat([df, reference], how="vertical_relaxed")` → `concat_frames(relaxed=True)` exists |
| `blocker.py` | **the deep tail**: `_build_block_key_expr` → `pl.Expr`; `_fast_static_block_sizes`/`measure_blocking_profile` → `pl.LazyFrame` + `group_by().agg(pl.len())` + `pl.concat_str`/`map_elements`; a 2nd `pl.from_arrow` (~1191) |
| `profiler.py` | `profile_dataframe` (frame-level): `df.unique().height`, all-empty-row fold, `df.filter()`, `df[col].dtype in (pl.Utf8, pl.String)` |
| `indicators.py` | none — fully seam-routed (done) |
| `pipeline.py` (front-door) | `run_dedupe_df` front: `df.cast({...pl.Utf8})` (439), `_add_row_ids` on `pl.LazyFrame` (731/856), `.lazy()/.collect()` |

## Architecture

The port follows the eviction's established seam pattern: route every remaining
frame op through `to_frame(df)` (the seam accepts polars DF / `pa.Table` /
dict-of-arrays / a `Frame`), add the few missing seam ops, then flip the boundary
last. No new abstraction — this extends `frame.py`.

### New/extended seam ops (the additive foundation)

- **`cast_all_str()`** — cast every non-`__`-prefixed column to Utf8 (arrow: cast
  to LargeUtf8; polars: `df.cast({c: pl.Utf8})`). For the `run_dedupe_df` front.
- **`count_duplicate_rows()`** — `height - distinct_row_count()` (both exist);
  or a direct op. For `profiler.profile_dataframe`.
- **all-empty-row count** — likely `select_dicts`/`to_list` + Python (cold path),
  per the W3 plan's note ("declared PYTHON-SIDE over to_list"), not a new op.
- **grouped block-size** convenience (if needed) — arrow twin of
  `group_by(block_key).agg(pl.len())` via `derive_block_key` + `group_len`/
  `run_lengths` (both exist).

Each new op: added to the `Frame`/`Column` Protocol + BOTH backends, with a
**fixtures-first** parity test in `tests/test_frame_relational_ops.py` pinning
every semantic delta (null handling, order, dtype spelling, codepoint-vs-byte).

### Boundary flip

`auto_configure_df` (and `run_dedupe_df`) widen to accept `pl.DataFrame | pa.Table
| Frame` and route through `to_frame(df)` at the top — the idempotent-coercion
pattern the spine already uses. Remove the `pl.from_arrow` unwrap (autoconfig.py
~3595) and the 2nd one in blocker.py (~1191). `reference` (match-mode) can also
flow arrow now (the "can't flow until W5" constraint is what this lifts).
Public back-compat preserved: existing polars callers hit the `PolarsFrame`
branch unchanged; new callers pass arrow.

### Dtype contract (decision 2)

`profile_columns` feeds `str(df[col].dtype)` to the native
`autoconfig_classify_columns`. On the arrow path, add an **arrow→polars-spelling
map** (`"double"`→`"Float64"`, `"large_utf8"`/`"string"`→`"Utf8"`, etc.) at that
boundary so the kernel sees the vocabulary it expects. One table, Python-side,
no Rust/golden-vector change. A parity test pins the mapping against the polars
dtype spellings the kernel already handles.

## PR decomposition (6 PRs, front-loaded on foundation)

1. **PR-1 — seam-op fixtures + gaps (S).** Add `cast_all_str`, duplicate-row
   count, the all-empty-row decision, and (if needed) the grouped block-size op.
   Fixtures-first in `test_frame_relational_ops.py`. Pure-additive; no call-site
   change. *Risk: low.*
2. **PR-2 — `profiler.profile_dataframe` port (S).** Route the frame-level
   dup/empty/dtype detection through the seam. Gate: `test_profiler.py` unedited
   (byte-parity proof). *Risk: low.*
3. **PR-3 — dtype contract + `autoconfig.py` body residue (M).** The
   arrow→polars-spelling map for native classify; route the remaining
   `autoconfig.py` scalar accessors (`.height`/`.columns`/`.dtype`), the
   `df.filter(is_not_null())`, and `auto_configure(files)` file-ingest (off
   `pl.read_*`/`pl.concat` to the io_arrow ingest) through the seam. *Risk: medium
   (dtype contract + golden classifier vectors are cross-surface; assert config
   equivalence).*
4. **PR-4 — controller residue (S-M).** The 2× `pl.concat(...vertical_relaxed)` →
   `concat_frames(relaxed=True)`; type hints; confirm sampling is fully seam. Keep
   `autoconfig_controller.py`'s own tests unedited. *Risk: low-medium.*
5. **PR-5 — blocker measurement/block-key tail (L, HIGH risk — CHECKPOINT).**
   Give `_build_block_key_expr`/`_fast_static_block_sizes`/`measure_blocking_profile`
   an arrow twin via `derive_block_key` + `group_len`/`run_lengths`; remove the 2nd
   `pl.from_arrow` (blocker.py ~1191). **Block-key parity is recall-critical** —
   `pl.Expr` has no direct seam analog; `map_elements`/soundex fallbacks. Pin
   block membership byte-identical on a corpus BEFORE rewiring. *Risk: HIGH —
   surface to the user before landing.*
6. **PR-6 — the boundary flip + controller front-door (M-L).** Remove the
   `auto_configure_df` unwrap (~3595); widen `auto_configure_df`/`dedupe_df` sigs
   to accept `pa.Table`/`Frame` via `to_frame`; port `run_dedupe_df`'s front
   (`cast_all_str` + `ensure_row_ids`, off `.lazy()/.collect()`) so the controller
   sample re-run is polars-free. Add a **zero-config-without-polars tripwire test**
   (subprocess, polars import blocked, `dedupe_df(pa.Table)` succeeds) mirroring
   the eviction's covered-spine tripwire. *Risk: HIGH — blast radius = every
   zero-config caller; the controller re-run coupling is the crux.*

## Parity contracts

- **Byte-parity where deterministic; config-equivalence where sampled.** Per-module
  ports keep the module's existing test file UNEDITED (byte-parity proof). BUT
  `sample()` is **statistical-not-byte across backends BY DESIGN** (W3a) — the
  controller's verdicts are per-backend. So the autoconfig differential harness
  asserts **config-level equivalence** (same suggested matchkeys/thresholds/mode),
  NOT row identity, on the sampled paths. Deterministic ops (profiler counts,
  block membership, dtype classification) stay byte-identical.
- **Block-key membership byte-identical** (PR-5) — recall-critical; a corpus pins
  block assignment native-vs-polars before the rewire.
- **Dtype classification identical** — the arrow→polars-spelling map must produce
  the same `autoconfig_classify_columns` output as the polars path (golden vectors
  unchanged).
- **Public API back-compat** — polars callers unchanged (PolarsFrame branch);
  the DedupeResult surface is NOT touched (the port accepts arrow, the spine
  returns what it already returns).

## Testing

- **Fixtures-first seam-op parity** in `test_frame_relational_ops.py` for every
  new op (both backends, semantic deltas pinned).
- **Per-module byte-parity**: leave `test_profiler.py`/`test_indicators.py`/
  `test_blocker.py`/controller tests UNEDITED as the proof each port is
  output-identical on the polars path.
- **Differential harness** (config-equivalence) for the autoconfig sampled path:
  `auto_configure_df(PolarsFrame)` vs `auto_configure_df(ArrowFrame)` on a corpus
  → same config decision.
- **Block-key membership corpus** (PR-5): native-vs-polars block assignment
  byte-identical.
- **Zero-config-without-polars tripwire** (PR-6): subprocess with polars import
  blocked, `native_available()==True`, `dedupe_df(pa.Table, config=None)` runs to
  completion and returns a result — the acceptance test for the whole port.
- Box constraints: goldenmatch tests via the workspace `.venv`; heavy/native lanes
  are CI's job. `GOLDENMATCH_FRAME=arrow` / `=polars` toggles exercise both.

## Recipe (from the W-waves — mirror it)

1. Seam-op batch, **fixtures-first**, pure-additive (op to Protocol + both
   backends; parity fixture pins semantics; SEMANTIC ops only, cite the call site).
2. Per-module call-site batch: reroute through `to_frame(df)`; module test file
   **unedited** = byte-parity proof.
3. **No default flip during dual-backend prep** — `GOLDENMATCH_FRAME` unchanged;
   arrow just *can* flow.
4. Each batch = its own PR off the predecessor's merged main; fold origin/main
   before shipping call-site batches; watch pytest-split shard-shift (new test
   files → rootdir-relative deselects).
5. **Boundary flip last** (PR-6), env-gated step-down.

## Rollout

After PR-6 lands and the tripwire is green on CI: the `[polars]` CI stopgap
(#1747) can be reverted for the goldenmatch-consuming lanes (rust/rust_pgrx/
duckdb/dbt) — a follow-up PR proving zero-config runs polars-free in those lanes.
Docs sweep: correct the eviction "COMPLETE" claim; ADR for the autoconfig port;
update the tracker.

## Risks

- **Block-key parity (PR-5)** — recall-critical; `pl.Expr` port has no direct
  analog. Mitigation: byte-identical block-membership corpus before rewire;
  user checkpoint.
- **Controller re-run coupling (PR-6)** — the sample pipeline re-enters
  `run_dedupe_df` per iteration; porting its front is the crux. Mitigation: the
  front-door is a small, well-bounded surface (cast_all_str + ensure_row_ids);
  the tripwire is the acceptance gate.
- **Sampled non-determinism** — do NOT assert row-identity on sampled paths;
  assert config-equivalence.
- **Dtype spelling drift** — the arrow→polars map must be exhaustive for the
  dtypes autoconfig sees; pin against the kernel's accepted vocabulary.

## Out of scope

- The DedupeResult output surface (already arrow via W5; untouched here).
- Match-mode `reference` beyond letting it flow arrow (no new match-mode work).
- Reverting the `[polars]` CI stopgap (a follow-up after the tripwire is green).

## Links

`docs/superpowers/plans/2026-07-11-goldenmatch-polars-eviction-w3.md` (the
autoconfig/controller dual-backend recipe) · `...-w5.md` (boundary-flip step-down)
· memory `project_goldenmatch_autoconfig_arrow_port`, `project_goldenmatch_polars_eviction`
