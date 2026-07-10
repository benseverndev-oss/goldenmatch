# GoldenCheck Polars eviction — Relation ports R3 (null_correlation + numeric_cross + temporal)

Date: 2026-07-09
Status: design — approved in brainstorming, pending spec review
Base: fresh `origin/main` (through R2 #1614; the seam has the full column surface + `to_arrow`/`get` + `filter_by`/`eq`/`cast`)
Parent program: goldencheck Polars eviction — relation front (see `2026-07-09-goldencheck-relation-ports-r1-design.md` for the R1–R4 roadmap)

## Context

R3 of the relation front — and the **cleanest** relation batch: `null_correlation`, `numeric_cross`,
`temporal`. Unlike R2, these have **no native kernel and no parity-locked raw-df helpers**. Their tests
import only the Profiler classes (`numeric_cross` also imports the pure-Python name-heuristic
`_find_max_pairs`, which is untouched), so every helper is free to seam-route. They share a
**two-column element-wise** shape: build a mask by comparing two columns (`a > b`, or null-mask
`==`), count/sample the flagged rows.

**Approach:** full seam-routing — add the six element-wise/aggregation ops these three need; no
`frame.native` escape hatch required. Same "Polars as accelerator" model; byte-identical, no version
bump.

## Scope

### In scope
Port `null_correlation`, `numeric_cross`, `temporal` `profile()` methods + their internal helpers onto
the seam, adding **6** `Column` methods (`is_null`, `gt_mask`, `eq_mask`, `fill_null`, `sum`,
`str_to_date`) and a `_NUMERIC` neutral-string frozenset in `numeric_cross`. Byte-identical.

### Explicitly NOT in scope
`_find_max_pairs` / `_find_date_pairs` (pure-Python name heuristics over `list[str]` — untouched). R4
(the decline pass). The Stage-2 non-Polars backend. The reader. The deps flip.

### Success criteria
- The three files are grep-clean of `polars`/`_polars_lazy`/`pl.`, routing through the seam.
- Their existing tests (`tests/relations/test_{null_correlation,numeric_cross,temporal}.py`) pass
  **unedited** (the parity gate).
- Full suite green; `import goldencheck` still loads zero Polars.

## The seam additions (`core/frame.py` — `Column`, PolarsColumn delegates)

| Method | Signature | PolarsColumn impl | Used by |
|---|---|---|---|
| `is_null` | `is_null() -> Column` | `PolarsColumn(self._s.is_null())` | null_correlation (null masks) |
| `gt_mask` | `gt_mask(other: Column) -> Column` | `PolarsColumn(self._s > other._s)` | numeric_cross, temporal (`a > b`) |
| `eq_mask` | `eq_mask(other: Column) -> Column` | `PolarsColumn(self._s == other._s)` | null_correlation (mask agreement) |
| `fill_null` | `fill_null(value: Any) -> Column` | `PolarsColumn(self._s.fill_null(value))` | numeric_cross, temporal |
| `sum` | `sum() -> Any` | `self._s.sum()` | all three (callers wrap `int()` where the original did) |
| `str_to_date` | `str_to_date(fmt: str, *, strict: bool) -> Column` | `PolarsColumn(self._s.str.to_date(format=fmt, strict=strict))` | temporal |

- `gt_mask`/`eq_mask` are the first **column-vs-column** comparison ops — they take a `Column` and reach
  `other._s`, mirroring `filter_by(mask: Column)`'s established pattern. Kept distinct from A2b's scalar
  `eq(value)` (which compares against a Python scalar) rather than overloading it.
- `sum()` returns the raw `self._s.sum()` (a Polars scalar). numeric_cross/null_correlation wrap
  `int(...)` exactly as the originals did; temporal passes the raw scalar to `affected_rows` exactly as
  the original did. Byte-identical either way.
- None reference the `pl.` symbol (`self._s.*` / `other._s`), so the import gate stays green.

## The 3 ports

Each: drop `from goldencheck._polars_lazy import pl`; drop `pl.DataFrame`/`pl.Series` annotations
(on `profile`, `_check_exceeds`, `_check_pair`, `_try_cast_to_date`). Keep `to_frame`, `Finding`,
`Severity`, and all pure-Python module bits. The pure-Python heuristics `_find_max_pairs` /
`_find_date_pairs` (over `list[str]`) are UNCHANGED (called with `frame.columns`).

### null_correlation.py
- `profile(self, frame)`: drop annotation; keep `frame = to_frame(frame)`; remove `df = frame.native`.
  `columns = frame.columns`; `n_rows = frame.height` (was `len(df)`). `if n_rows == 0 or len(columns) < 2:
  return findings` UNCHANGED.
- `null_masks = {col: frame.column(col).is_null() for col in columns}` (was `df[col].is_null()`; values
  are now Columns).
- `null_counts = {col: int(null_masks[col].sum()) for col in columns}` (was `int(null_masks[col].sum())`
  on a Series — now `Column.sum()`; byte-identical).
- In the pair loop: `agreement = int(mask_a.eq_mask(mask_b).sum())` (was `int((mask_a == mask_b).sum())`)
  where `mask_a = null_masks[col_a]`, `mask_b = null_masks[col_b]` (Columns). All thresholds, the
  `_UnionFind` grouping, and both Finding bodies UNCHANGED.

### numeric_cross.py
- Delete the `@lru_cache _numeric_dtypes()` helper + `from functools import lru_cache`. Add module
  constant `_NUMERIC = frozenset({"int", "uint", "float"})`.
- `profile(self, frame)`: drop annotation; `frame = to_frame(frame)`; remove `df = frame.native`.
  `max_pairs = _find_max_pairs(frame.columns)` (was `df.columns`). Loop calls
  `self._check_exceeds(frame, value_col, max_col)` (was `(df, …)`).
- `_check_exceeds(self, frame, value_col, max_col)`: drop annotation. `try: val_series =
  frame.column(value_col); max_series = frame.column(max_col) except Exception: return None` (Columns
  now; `frame.column` raises on a missing name exactly as `df[col]` did). Numeric gate: `if val_series.dtype
  not in _NUMERIC or max_series.dtype not in _NUMERIC:` (was `not in _numeric_dtypes()`). String-cast
  branch: `if val_series.dtype == "str": val_series = val_series.cast("float", strict=False)` (was
  `in (pl.Utf8, pl.String)` + `cast(pl.Float64, strict=False)`); same for `max_series`; the re-check
  `if val_series.dtype not in _NUMERIC: return None` (the original `_numeric_dtypes() + (pl.Float64,)` is
  redundant — Float64 already ∈ numeric — so `_NUMERIC` is exact). `violation_mask =
  val_series.gt_mask(max_series).fill_null(False)`; `violation_count = int(violation_mask.sum())`. Samples:
  `val_filtered = val_series.filter_by(violation_mask).to_list()[:3]`; `max_filtered =
  max_series.filter_by(violation_mask).to_list()[:3]` (was `.filter(mask).head(3).to_list()`). The
  `zip`/`f"{v} exceeds {m}"` + the ERROR Finding UNCHANGED.

### temporal.py
- `_try_cast_to_date(col)`: drop `pl.Series` annotation. `if col.dtype == "str": return
  col.str_to_date("%Y-%m-%d", strict=False)` (was `series.dtype == pl.Utf8 or pl.String` +
  `series.str.to_date(format="%Y-%m-%d", strict=False)`); `return col` UNCHANGED.
- `profile(self, frame)`: drop annotation; `frame = to_frame(frame)`; remove `df = frame.native`.
  `kw_pairs = _find_date_pairs(frame.columns)`. Date-col detection loop: `for col_name in frame.columns:
  col = frame.column(col_name); if col.dtype in ("date", "datetime"): date_cols.append(col_name) elif
  col.dtype == "str": casted = col.str_to_date("%Y-%m-%d", strict=False); if len(casted.drop_nulls()) > 0:
  date_cols.append(col_name)` (was `s.str.to_date(...)`; `casted.drop_nulls().len()` → `len(...)`). The
  `<= 6` guard + `combinations` + `_check_pair(frame, …)` calls UNCHANGED.
- `_check_pair(self, frame, start_col, end_col, confidence)`: drop annotation. `start_series =
  frame.column(start_col)`; `end_series = frame.column(end_col)`. `_try_cast_to_date(start_series)`
  (Column). `if start_series.dtype not in ("date", "datetime") or end_series.dtype not in ("date",
  "datetime"): return None`. `violation_mask = start_series.gt_mask(end_series).fill_null(False)`;
  `violation_count = violation_mask.sum()` (**raw, no `int()`** — matches the original). Samples:
  `sample_starts = start_series.filter_by(violation_mask).cast("str").to_list()[:3]` (was
  `.filter(mask).head(3).cast(pl.String).to_list()`); same for ends. The `zip`/`f"{s} > {e}"` + the ERROR
  Finding (incl. `affected_rows=violation_count`) UNCHANGED.

## Testing

- **Seam unit tests** (`tests/core/test_frame.py` additions): `is_null()` on a null-containing column;
  `gt_mask`/`eq_mask` between two columns == the raw `a._s > b._s` / `a._s == b._s` (compare `.to_list()`);
  `fill_null(False)` on a mask with nulls; `sum()` on a bool column == `int(s.sum())`; `str_to_date` on a
  `"%Y-%m-%d"` string column == `s.str.to_date(...)`.
- **Parity gates (unedited):** the three `tests/relations/test_*.py` pass with ZERO edits.
- **Regression:** full suite green (same counts + the new seam tests); import gate green; the three files
  grep-clean of `polars`/`_polars_lazy`/`pl.`.

## Risks

- **`gt_mask`/`eq_mask` null semantics** — `self._s > other._s` yields `null` where either operand is
  null (Polars three-valued). numeric_cross/temporal apply `.fill_null(False)` immediately (matching the
  original), so nulls don't count as violations and `filter_by` excludes them. null_correlation's
  `eq_mask` operates on `is_null()` masks (boolean, never null), so no fill needed — exactly as today.
- **`_NUMERIC` frozenset** — byte-identical: `_numeric_dtypes()` = Int8-64 + UInt8-64 + Float32/64 →
  `{"int","uint","float"}`; the `+ (pl.Float64,)` in the re-check is redundant (Float64 ∈ "float"). No
  unsupported dtype collides into the set.
- **temporal `.head(3).cast(String)` vs `.cast("str").to_list()[:3]`** — cast is element-wise and
  order-preserving, so casting all filtered rows then slicing 3 yields the identical first-3 strings as
  slicing 3 then casting. Byte-identical output (marginally more work when there are many violations —
  same pattern already accepted for range_distribution's outlier samples).
- **`sum()` raw scalar** — temporal's `affected_rows=violation_count` receives the raw Polars scalar
  exactly as the original `violation_mask.sum()` produced; numeric_cross/null_correlation wrap `int()`.
- **`frame.column(missing)` raise** — `PolarsFrame.column` does `self._df[name]`, which raises on a
  missing name exactly as `df[col]` did, so `_check_exceeds`'s `try/except` behaves identically.
- **Seam growth** — 6 methods, all general primitives (element-wise compare/agg/parse). The two-column
  `gt_mask`/`eq_mask` follow the `filter_by(Column)` precedent. No task-shaped op (no `exceeds`,
  no `null_agreement`).
- **Preserve every existing `try/except` block VERBATIM in structure** — only swap the op inside:
  temporal's `_try_cast_to_date` call in `_check_pair` (the `try: … except Exception: return None`
  around the two `_try_cast_to_date(...)` calls) and profile()'s date-detection string branch
  (`try: casted = col.str_to_date(...) … except Exception: pass`); numeric_cross's `_check_exceeds`
  `try: val_series = frame.column(value_col); … except Exception: return None` and the cast-branch
  `try: … except Exception: return None` (with `violation_mask = …` staying OUTSIDE it). These guard
  against exotic-dtype raises; byte-identical control flow requires keeping them.

## Non-goals (YAGNI)
R4; `_find_max_pairs`/`_find_date_pairs` changes; a `head(n)` seam op (use `.to_list()[:n]`); overloading
scalar `eq` for columns; the Stage-2 backend; reader; deps flip.
