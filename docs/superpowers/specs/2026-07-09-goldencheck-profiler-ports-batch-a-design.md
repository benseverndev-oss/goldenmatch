# GoldenCheck Polars eviction — Profiler ports Batch A (dtype + string/cast/membership seam)

Date: 2026-07-09
Status: design — approved in brainstorming, pending spec review
Base: stacked on `feat/goldencheck-polars-eviction-p0` (P0 seam); rebase onto fresh `origin/main` once P0 (#1605) merges
Parent program: goldencheck Polars eviction (see `2026-07-08-goldencheck-polars-eviction-p0-design.md`)

## Context

P0 (PR #1605) added the `Frame`/`Column` seam (`goldencheck/core/frame.py`, one `PolarsColumn`
backend) and ported 3 profilers (nullability/cardinality/uniqueness) polars-free. `import
goldencheck` loads zero Polars. This stage continues the incremental profiler ports.

**Staging correction (discovered after P0):** the reader eviction is NOT next — `read_file`
returns a `pl.DataFrame` and the scanner does Polars ops on it directly (`classify_columns`,
`select`, `apply_rules`, drift), so a non-Polars reader requires the whole scan path to be
Polars-free first. The reader is a *late* stage (goldenflow did its CSV path in Phase 4e). The real
next increment is porting more profilers onto the seam.

**Seam philosophy (decided):** operations Polars does today (`str.contains`, numeric `cast`,
`is_in`) are added to the `Column` seam and `PolarsColumn` delegates to the exact Polars call —
byte-identical, **no perf regression** (Polars stays the fast path), profiler decoupled from
Polars. The *non-Polars* implementation of these ops arrives with the Stage-2 substrate backend;
it is NOT this stage's concern. This is goldenflow's "Polars as accelerator" model and pushes the
regex-vs-Python byte-identity risk to Stage 2 where it belongs.

## Scope

### In scope — port 4 profilers (the "dtype-only" batch)
`type_inference`, `format_detection`, `encoding_detection`, `fuzzy_values`. Recon confirmed these
need only a `dtype` accessor plus a few op-shaped seam methods (below); the rest of their logic is
plain-Python thresholds + Finding construction.

### Explicitly NOT in scope
`pattern_consistency` (its `_generalize` is the #3 cProfile self-time hotspot at 100K rows — needs
a vectorized `str.replace_all` + `value_counts` seam op; a later batch). Batch B (`freshness`,
`range_distribution`, `sequence_detection` — need scalar stats min/max/mean/std). Batch C
(`drift_detection` — positional slicing + stats + cast + is_in). The Stage-2 non-Polars backend.
No reader, no deps flip.

### Success criteria
- The 4 profilers are polars-free (import no `pl`), routing through the seam.
- Their existing tests pass **unedited** (byte-identical Findings — the parity gate).
- Full suite green; `import goldencheck` still loads zero Polars.

## The seam additions (`core/frame.py` — `Column`, PolarsColumn delegates)

Five new `Column` methods (+ an optional `Frame.dtype(name)` thin wrapper). Each `PolarsColumn`
method delegates to the exact Polars call the profiler uses today, so ports are byte-identical:

| Method | Signature | PolarsColumn impl | Used by |
|---|---|---|---|
| `dtype` | `-> str` | neutral map (below) | all 4 (dtype gates) |
| `cast` | `cast(kind: str, *, strict: bool = False) -> Column` | `PolarsColumn(self._s.cast(_pl(kind), strict=strict))` | type_inference |
| `str_match_count` | `str_match_count(pattern: str) -> int` | `int(self._s.str.contains(pattern).sum())` | format, encoding |
| `str_not_matching` | `str_not_matching(pattern: str, limit: int) -> list` | `self._s.filter(~self._s.str.contains(pattern)).head(limit).to_list()` | format, encoding |
| `member_count` | `member_count(values: list) -> int` | `int(self._s.is_in(values).sum())` | fuzzy_values |

**Neutral `dtype` mapping** (`PolarsColumn.dtype`): `pl.Utf8`/`pl.String → "str"`; `pl.Int8..Int64`,
`pl.UInt8..UInt64 → "int"`; `pl.Float32/Float64 → "float"`; `pl.Date → "date"`; `pl.Datetime →
"datetime"`; else `"other"`. Keeps int/float and date/datetime distinct (Batch A + later batches
rely on it). `cast`'s `kind` accepts `"float"`/`"int"` mapped to `pl.Float64`/`pl.Int64`.

`str_match_count`/`str_not_matching` operate on the column as-is; the profilers call them on a
`drop_nulls()`'d Column (existing seam op), so null handling matches today's `non_null.str.contains`.

## The 4 ports

- **type_inference** — `frame.dtype/col.dtype` gate; for numeric-parse counting:
  `casted = col.drop_nulls().cast("float", strict=False)`; `parseable = len(casted) - casted.null_count()`
  (replaces `.cast(pl.Float64, strict=False).is_not_null().sum()`). Same for int.
- **format_detection** — `col.dtype == "str"` gate; `match = col.drop_nulls().str_match_count(pat)`;
  `samples = col.drop_nulls().str_not_matching(pat, 5)`. Replaces the `str.contains(...).sum()` +
  `filter(~...).head(5).to_list()`.
- **encoding_detection** — same shape as format_detection, its 4 patterns each via
  `str_match_count` / `str_not_matching`.
- **fuzzy_values** — `col.dtype == "str"` gate; distinct values via existing
  `col.drop_nulls().unique().to_list()`; clustering unchanged (already `list[str]` through
  `core/kernels.py`); `affected_rows = col.member_count(variants)` (replaces `is_in(variants).sum()`).

Each ported file removes its `from goldencheck._polars_lazy import pl` import → polars-free.

## Testing

- **Seam unit tests** (`tests/core/test_frame.py` additions): each new `PolarsColumn` method equals
  the raw Polars call it wraps (`dtype` mapping for each pl type; `cast` uncastable→null;
  `str_match_count`/`str_not_matching` on a mixed column; `member_count`).
- **Parity gate:** the 4 profilers' existing test files pass with ZERO edits (byte-identical). If a
  test diverges, the port broke byte-identity — fix the port, never the test.
- **Regression:** full suite green (same counts + these); import gate green; the 4 files grep-clean
  of `polars`/`_polars_lazy`/`pl.`.

## Risks

- **`dtype` neutral-map lossiness** — mitigated: int/float and date/datetime kept distinct; Batch A
  only needs str/int/float, later batches need date/datetime (present). If a profiler ever needs a
  finer distinction (Int32 vs Int64, tz-aware Datetime), add it to the map then (YAGNI now).
- **Seam growth** — 5 methods is proportionate to a 4-profiler batch; each is a one-line delegate.
  Do NOT over-add (no `str_contains`→bool-Column + `filter` primitive algebra; the task-shaped
  `str_match_count`/`str_not_matching` are what the profilers actually need).
- **Byte-identity of the new ops** — by construction (PolarsColumn calls the same Polars method);
  the parity tests are the proof. The regex-vs-Python risk is NOT here (Polars regex under the hood)
  — it moves to Stage 2's non-Polars backend.
- **Stacked base** — this branch stacks on unmerged P0; rebase onto fresh `origin/main` once P0
  merges (`git rebase --onto origin/main <P0-tip>`), or land after P0.

## Non-goals (YAGNI)
pattern_consistency; Batches B/C; scalar stats / `.str`/`.dt` vectorized seam ops; the Stage-2
non-Polars backend; reader; deps flip.
