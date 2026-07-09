# GoldenCheck Polars eviction — Profiler ports Batch B (scalar-stats + comparison seam)

Date: 2026-07-09
Status: design — approved in brainstorming, pending spec review
Base: fresh `origin/main` (P0 #1605 + Batch A #1606 + Batch A2 #1607 all merged; the Frame/Column seam has `dtype`/`cast`/`member_count`/`str_match_count`/`str_filter`)
Parent program: goldencheck Polars eviction (see `2026-07-08-goldencheck-polars-eviction-p0-design.md`)

## Context

Continuing the goldencheck Polars eviction. P0 built the `Frame`/`Column` seam and ported
`nullability`/`cardinality`/`uniqueness`. Batch A added `type_inference`/`fuzzy_values`
(`dtype`/`cast`/`member_count`). Batch A2 added `format_detection`/`encoding_detection`
(`str_match_count`/`str_filter`). **7 of ~13 column profilers are polars-free.**

This batch ports the three **scalar-reduction** profilers — `freshness`, `range_distribution`,
`sequence_detection` — which all lean on the same `min`/`max`/`mean`/`std` reductions plus a small
comparison/filter surface. Grouping them keeps the new seam family cohesive in one PR.

**Seam philosophy (unchanged):** the ops go on the `Column` seam; `PolarsColumn` delegates to the
exact Polars call (byte-identical, no perf regression, Polars stays the fast path). Following the
Batch A2 idiom, counts are **bundled count-shaped** ops (like `str_match_count` = `int(...sum())`)
and filters **return a Column** (like `str_filter`). The non-Polars implementation arrives with the
Stage-2 substrate backend — not here.

## Scope

### In scope
Port `freshness`, `range_distribution`, `sequence_detection` onto the seam, adding **9** `Column`
methods. Byte-identical, no version bump.

### Explicitly NOT in scope
`drift_detection` (Batch C — needs positional `slice` + `cast("str")` + the categorical set path;
reuses this batch's `mean`/`std`, so it lands after B). `pattern_consistency` (Batch A2b — needs
`value_counts` + a cross-column mask filter; the gnarliest port, and already vectorized so the port
buys no perf). The relation profilers. The Stage-2 non-Polars backend. The reader. The deps flip.

### Success criteria
- `freshness`, `range_distribution`, `sequence_detection` are polars-free (import no `pl`), routing
  through the seam.
- Their existing tests pass **unedited** (byte-identical Findings — the parity gate).
- Full suite green; `import goldencheck` still loads zero Polars.

## The seam additions (`core/frame.py` — `Column`, PolarsColumn delegates)

Nine methods. Each `PolarsColumn` method delegates to the exact Polars call the profilers use
today, so ports are byte-identical. **None reference the `pl.` symbol** (all `self._s.*`), so the
import gate stays trivially green.

| Method | Signature | PolarsColumn impl | Used by |
|---|---|---|---|
| `min` | `-> Any` | `self._s.min()` | range, sequence |
| `max` | `-> Any` | `self._s.max()` | freshness, range, sequence |
| `mean` | `-> Any` | `self._s.mean()` | range |
| `std` | `-> Any` | `self._s.std()` (default ddof=1) | range |
| `diff` | `-> Column` | `PolarsColumn(self._s.diff())` | sequence |
| `is_sorted` | `-> bool` | `bool(self._s.is_sorted())` | sequence |
| `count_gt` | `count_gt(value: Any) -> int` | `int((self._s > value).sum())` | freshness, sequence |
| `count_eq` | `count_eq(value: Any) -> int` | `int((self._s == value).sum())` | sequence |
| `filter_outside` | `filter_outside(lower: Any, upper: Any) -> Column` | `PolarsColumn(self._s.filter((self._s < lower) \| (self._s > upper)))` | range |

- `min`/`max` return the **native Python scalar** the Polars reduction yields (`.max()` on a `Date`
  series → `datetime.date`, on `Datetime` → `datetime.datetime`), so freshness's `.date()` /
  day-arithmetic and range's `float(...)` wrapping are unchanged.
- `count_gt`/`count_eq` bundle `int((self._s <op> value).sum())` — exactly the `str_match_count`
  count-shape. The scalar `value` may be an int (sequence) or a `datetime` (freshness); the
  comparison is delegated to Polars unchanged (freshness keeps its `try/except` for the tz-aware
  case; see risks).
- `filter_outside` returns a full `PolarsColumn`, so `len(...)` and `.to_list()[:5]` compose off it
  (no `head` op needed — `.to_list()[:5]` ≡ `.head(5).to_list()`, same as Batch A2).

## The 3 ports

Each port: signature `def profile(self, frame, column: str, *, context: dict | None = None)` (drop
the `frame: pl.DataFrame` annotation — matches P0/A/A2); keep `frame = to_frame(frame)`; remove
`from goldencheck._polars_lazy import pl`; delete the module's `_*_dtypes()` `@lru_cache` helper and
the now-unused `from functools import lru_cache`.

- **freshness** — `col = frame.column(column)`; `is_datetime = col.dtype == "datetime"`;
  `is_date = col.dtype == "date"`; gate unchanged. `non_null = col.drop_nulls()`;
  `if len(non_null) == 0: return []`. Inside the existing `try/except` (kept **verbatim** — the
  tz-aware guard): `future_count = non_null.count_gt(now)`; `newest = non_null.max()`. The
  future-dated Finding and the name-gated staleness block (pure-Python date math on `newest`) are
  UNCHANGED. `non_null.len()` in the staleness `affected_rows` → `len(non_null)`.
- **range_distribution** — `col = frame.column(column)`; snapshot `dtype = col.dtype` (a string)
  **before** the cast chain reassigns `col`. Numeric gate `is_numeric = dtype in ("int", "uint",
  "float")` (**includes uint** — range flags all numerics, unlike type_inference). `mostly_numeric`
  chain: `col = col.cast("float", strict=False).drop_nulls()`. The `non_null =` line keeps its
  re-check byte-identically: `non_null = col.drop_nulls() if is_numeric and dtype in ("int","uint",
  "float") else col`. `mean/std/min/max` via the seam; `float(non_null.max())`/`float(non_null.min())`
  for `max_dev`; `outliers = non_null.filter_outside(lower, upper)`; `len(outliers)`;
  `sample = outliers.to_list()[:5]`. All thresholds/messages/Findings UNCHANGED.
- **sequence_detection** — `col = frame.column(column)`; integer gate `if col.dtype not in
  ("int", "uint"): return`. `non_null = col.drop_nulls()`; `diffs = non_null.diff().drop_nulls()`;
  `unit_diffs = diffs.count_eq(1)`; `positive_diffs = diffs.count_gt(0)`;
  `is_sorted_sequential = (positive_ratio >= SEQUENTIAL_THRESHOLD) and non_null.is_sorted()`.
  `col_min = int(non_null.min())`; `col_max = int(non_null.max())`. **Gap-set moves to pure Python**
  (drops `pl.Series`): `present = set(non_null.unique().to_list())`;
  `gaps = [v for v in range(col_min, col_max + 1) if v not in present]`; `gap_count = len(gaps)`;
  `sample_gaps = gaps[:10]`. All thresholds/messages/Finding UNCHANGED.

## Testing

- **Seam unit tests** (`tests/core/test_frame.py` additions): each new `PolarsColumn` method equals
  the raw Polars call it wraps — `min`/`max`/`mean`/`std` on a numeric Series; `diff` returns
  consecutive differences (with a leading null); `is_sorted` True/False; `count_gt`/`count_eq`
  against a scalar (incl. a `datetime` scalar for `count_gt`); `filter_outside(lo, hi)` returns the
  values `< lo` or `> hi` in original order (compare `.to_list()` to the raw
  `s.filter((s<lo)|(s>hi))`).
- **Parity gate:** `test_freshness.py`, `test_range_distribution.py`, `test_sequence_detection.py`
  pass with **zero edits** (byte-identical Findings). If a test diverges, the port broke
  byte-identity — fix the port, never the test.
- **Regression:** full suite green (same counts + the new seam tests); import gate green
  (`tests/test_import_no_polars.py`); the 3 ported files grep-clean of `polars`/`_polars_lazy`/`pl.`.

## Risks

- **tz-aware / non-`us` Datetime dtype gate (freshness)** — **confirmed non-issue.**
  `_neutral_dtype` already classifies via `dt == pl.Datetime` (frame.py line 54) — the *identical*
  comparison freshness's original `col.dtype == pl.Datetime` uses. Polars' `Datetime` instance
  compares equal to the `pl.Datetime` class regardless of `time_unit`/`time_zone`, so a tz-aware
  column still maps to `"datetime"`, the gate passes, and the existing `try/except` catches the
  naive-`now`-vs-aware raise → `[]` exactly as today. (Even in the impossible case the map returned
  `"other"`, the output is still `[]`, so both paths agree.)
- **range `dtype` capture ordering** — `dtype` is snapshotted as a string before the `mostly_numeric`
  cast reassigns `col`; the `non_null =` re-check (`dtype in (...)`) therefore reflects the ORIGINAL
  dtype exactly as the pre-port `dtype in _numeric_dtypes()` did. Byte-identical.
- **sequence gap-set pure-Python port** — the original builds an ascending `pl.Series` range and
  `filter(~is_in(present))`, which preserves ascending order; the list comprehension over
  `range(col_min, col_max+1)` is also ascending, and `gaps[:10]` ≡ `full_range.filter(...).head(10)
  .to_list()`. Byte-identical order + count.
- **`std` ddof** — Polars `.std()` defaults to ddof=1; the delegate preserves it (no arg passed).
- **`count_gt`/`count_eq` int-wrapping** — the delegate returns `int((...).sum())`, matching the
  originals' `int((...).sum())`; the counts feed arithmetic (`/ n_diffs`) and int fields
  (`affected_rows`), so values are byte-identical.
- **Seam growth** — 9 methods, but 6 are trivial one-line reductions (`min`/`max`/`mean`/`std`/`diff`
  /`is_sorted`) and the other 3 follow the A2 count-shaped / returns-a-Column idioms. No task-shaped
  bespoke ops (no `count_future`, no `outlier_filter(mean, std)`).

## Non-goals (YAGNI)
`drift_detection` (Batch C); `pattern_consistency` (Batch A2b); the relation profilers; a `head`/
`slice` seam op (use `.to_list()[:n]`); the Stage-2 non-Polars backend; reader; deps flip.
