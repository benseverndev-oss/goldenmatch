# GoldenCheck Polars eviction — Profiler ports Batch A (dtype + cast + membership seam)

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

### In scope — port 2 profilers (narrowed after spec review)
`type_inference` and `fuzzy_values`. Spec review found `format_detection` and `encoding_detection`
each use a Polars op NOT covered by a simple seam addition and NOT expressible byte-identically
without a string-predicate **filter/sampler** family: `format_detection` has a *cross-format* block
`non_null.filter(~matchesA).str.contains(B).sum()` ("count rows not matching A that match B"), and
`encoding_detection` samples the *matching* rows (`filter(mask).head(5)`), not the complement. Those
two need a properly-designed positive+negative string sampler + a filtered-count op, worked out from
a complete reading — a **separate follow-up batch** ("Batch A2"), NOT this one. `type_inference` and
`fuzzy_values` port cleanly with a `dtype` accessor + `cast` + `member_count`, so Batch A ships them.

### Explicitly NOT in scope
`format_detection`, `encoding_detection` (Batch A2 — need the string filter/sampler seam family).
`pattern_consistency` (its `_generalize` is the #3 cProfile self-time hotspot at 100K rows — needs
a vectorized `str.replace_all` + `value_counts` seam op; a later batch). Batch B (`freshness`,
`range_distribution`, `sequence_detection` — need scalar stats min/max/mean/std). Batch C
(`drift_detection` — positional slicing + stats + cast + is_in). The Stage-2 non-Polars backend.
No reader, no deps flip.

### Success criteria
- `type_inference` and `fuzzy_values` are polars-free (import no `pl`), routing through the seam.
- Their existing tests pass **unedited** (byte-identical Findings — the parity gate).
- Full suite green; `import goldencheck` still loads zero Polars.

## The seam additions (`core/frame.py` — `Column`, PolarsColumn delegates)

Three new `Column` methods (+ an optional `Frame.dtype(name)` thin wrapper). Each `PolarsColumn`
method delegates to the exact Polars call the profiler uses today, so ports are byte-identical:

| Method | Signature | PolarsColumn impl | Used by |
|---|---|---|---|
| `dtype` | `-> str` | neutral map (below) | both (dtype gates) |
| `cast` | `cast(kind: str, *, strict: bool = False) -> Column` | `PolarsColumn(self._s.cast(_pl(kind), strict=strict))` | type_inference |
| `member_count` | `member_count(values: list) -> int` | `int(self._s.is_in(values).sum())` | fuzzy_values |

**Neutral `dtype` mapping** (`PolarsColumn.dtype`): `pl.Utf8`/`pl.String → "str"`; `pl.Int8..Int64
→ "int"`; `pl.UInt8..UInt64 → "uint"` (**kept DISTINCT from `int`** — see the byte-identity note);
`pl.Float32/Float64 → "float"`; `pl.Date → "date"`; `pl.Datetime → "datetime"`; else `"other"`.
`cast`'s `kind` accepts `"float"`/`"int"` mapped to `pl.Float64`/`pl.Int64`.

**Byte-identity note (UInt):** `type_inference`'s "should-be-string" numeric gate is currently
`dtype in (pl.Int8..Int64, pl.Float32/Float64)` — **signed ints + floats only, NO UInt**. So the map
MUST keep `uint` distinct from `int`, and the port's numeric gate checks `dtype in ("int", "float")`
(excluding `uint`) to stay byte-identical. Folding UInt→"int" would fire the warning on a `UInt`
column the current code ignores.

## The 2 ports

- **type_inference** — `col.dtype` gate: `== "str"` (was `pl.Utf8`; note `pl.Utf8 is pl.String` in
  current polars, so `"str"` covers both); the should-be-string numeric gate uses
  `dtype in ("int", "float")` (signed+float only — NOT `uint`). For numeric-parse counting:
  `casted = col.drop_nulls().cast("float", strict=False)`;
  `parseable = len(casted) - casted.null_count()` (byte-identical to
  `.cast(pl.Float64, strict=False).is_not_null().sum()` — both count non-nulls, cast preserves
  length). Same for int.
- **fuzzy_values** — `col.dtype == "str"` gate (`pl.Utf8`/`pl.String → "str"`; `pl.Categorical →
  "other"`, excluded, matching today); distinct values via existing
  `col.drop_nulls().unique().to_list()` + Python `len()`; clustering unchanged (already `list[str]`
  through `core/kernels.py`); `affected_rows = col.member_count(variants)` (byte-identical to
  `int(col.is_in(variants).sum())`, both on the full column).

Each ported file removes its `from goldencheck._polars_lazy import pl` import → polars-free.

## Testing

- **Seam unit tests** (`tests/core/test_frame.py` additions): each new `PolarsColumn` method equals
  the raw Polars call it wraps — `dtype` mapping for each pl type (incl. a `pl.UInt32` series →
  `"uint"`, distinct from `int`); `cast("float"/"int", strict=False)` uncastable→null; `member_count`.
- **Parity gate:** the `type_inference` + `fuzzy_values` existing test files pass with ZERO edits
  (byte-identical). If a test diverges, the port broke byte-identity — fix the port, never the test.
- **Regression:** full suite green (same counts); import gate green; the 2 ported files grep-clean
  of `polars`/`_polars_lazy`/`pl.`.

## Risks

- **`dtype` neutral-map lossiness** — mitigated: int/uint/float/date/datetime kept distinct (the
  UInt distinction is byte-identity-load-bearing for `type_inference`; see the note above). If a
  profiler ever needs a finer distinction (Int32 vs Int64, tz-aware Datetime, Boolean, Categorical),
  add it to the map then (YAGNI now — Batch A needs only str/int/uint/float).
- **Seam growth** — only 3 methods (dtype/cast/member_count), each a one-line delegate. The string
  `str.contains`/filter/sampler ops that format/encoding need are DEFERRED to Batch A2 where they'll
  be designed from a complete reading (positive+negative samplers + a cross-format filtered-count) —
  NOT half-added here.
- **Byte-identity of the new ops** — by construction (PolarsColumn calls the same Polars method);
  the parity tests are the proof. The regex-vs-Python risk is NOT here (no string ops this batch);
  it moves to Stage 2's non-Polars backend.
- **Stacked base** — this branch stacks on unmerged P0; rebase onto fresh `origin/main` once P0
  merges (`git rebase --onto origin/main <P0-tip>`), or land after P0.

## Non-goals (YAGNI)
`format_detection`, `encoding_detection` (Batch A2 — string filter/sampler seam family);
pattern_consistency; Batches B/C; scalar stats / `.str`/`.dt` vectorized seam ops; the Stage-2
non-Polars backend; reader; deps flip.
