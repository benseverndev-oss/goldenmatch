# GoldenCheck Polars eviction — Profiler ports Batch C (drift_detection: positional slice + str cast)

Date: 2026-07-09
Status: design — approved in brainstorming, pending spec review
Base: fresh `origin/main` (P0 #1605 + Batch A #1606 + Batch A2 #1607 + Batch B #1608 all merged; the Frame/Column seam has `dtype`/`cast`/`member_count`/`str_match_count`/`str_filter`/`min`/`max`/`mean`/`std`/`diff`/`is_sorted`/`count_gt`/`count_eq`/`filter_outside`)
Parent program: goldencheck Polars eviction (see `2026-07-08-goldencheck-polars-eviction-p0-design.md`)

## Context

Continuing the goldencheck Polars eviction. Batch B ported the scalar-reduction profilers and added
`min`/`max`/`mean`/`std` (among others). This batch ports the single remaining
**scalar-stats-dependent** profiler, `drift_detection`, which reuses Batch B's `mean`/`std` and
Batch A's `member_count`, and needs only ONE genuinely new seam op — positional `slice` — plus one
new `cast` kind (`"str"`). **10 of ~13 column profilers are polars-free; this makes 11.**

**Seam philosophy (unchanged):** the op goes on the `Column` seam; `PolarsColumn` delegates to the
exact Polars call (byte-identical, no perf regression, Polars stays the fast path). The non-Polars
implementation arrives with the Stage-2 substrate backend — not here.

## Scope

### In scope
Port `drift_detection` onto the seam, adding **1** `Column` method (`slice`) and **1** `cast` kind
(`"str"`). Byte-identical, no version bump.

### Explicitly NOT in scope
`pattern_consistency` (Batch A2b — needs chainable `str_replace_all` + `value_counts` + a
cross-column mask filter; the gnarliest port, and already vectorized so it buys no perf). The 9
relation profilers. The Stage-2 non-Polars backend. The reader. The deps flip.

### Success criteria
- `drift_detection` is polars-free (import no `pl`), routing through the seam.
- Its existing test passes **unedited** (byte-identical Findings — the parity gate).
- Full suite green; `import goldencheck` still loads zero Polars.

## The seam additions (`core/frame.py` — `Column`, PolarsColumn delegates)

One new method + one new `cast` kind. Each `PolarsColumn` method delegates to the exact Polars call
`drift_detection` uses today, so the port is byte-identical. **Neither references the `pl.` symbol
at method scope** (all `self._s.*`), so the import gate stays trivially green.

| Method / change | Signature | PolarsColumn impl | Used by |
|---|---|---|---|
| `slice` | `slice(offset: int, length: int \| None = None) -> Column` | `PolarsColumn(self._s.slice(offset, length))` | drift (the two halves) |
| `cast` kind `"str"` | (existing `cast(kind, *, strict=False)`) | `_CAST_KIND` gains `"str": "String"` → `getattr(pl, "String")` | drift (categorical path) |

- **Positional halves:** `col[:mid] ≡ self._s.slice(0, mid)`; `col[mid:] ≡ self._s.slice(mid)` (=
  `self._s.slice(mid, None)`, offset to end). Polars `Series.__getitem__` with a slice delegates to
  `.slice` internally, so values + order are identical.
- **`cast("str")`:** `_CAST_KIND["str"] = "String"`; the existing `cast` body
  (`getattr(pl, _CAST_KIND[kind])`) resolves it to `pl.String`. `pl.String` is the alias of
  `pl.Utf8`. Because the original calls `cast(pl.String)` with Polars' **default `strict=True`**, the
  port passes `strict=True` explicitly (the seam default is `strict=False`; for a String target the
  distinction is immaterial — casting any dtype to String never fails — but we keep it faithful).

## The port (`profilers/drift_detection.py`)

Signature `def profile(self, frame, column: str, *, context: dict | None = None)` (drop the
`frame: pl.DataFrame` annotation — matches P0/A/A2/B); keep `frame = to_frame(frame)`; remove
`df = frame.native`; `col = frame.column(column)`; remove `from goldencheck._polars_lazy import pl`;
delete the `@lru_cache _numeric_dtypes()` helper AND `from functools import lru_cache`.

- `total = len(col)`; `if total < MIN_ROWS: return findings` UNCHANGED.
- `mid = total // 2`; `first_half = col.slice(0, mid).drop_nulls()`;
  `second_half = col.slice(mid).drop_nulls()` (was `col[:mid].drop_nulls()` / `col[mid:].drop_nulls()`).
- `if len(first_half) == 0 or len(second_half) == 0: return findings` UNCHANGED.
- High-cardinality skip: `non_null = col.drop_nulls()`; `if len(non_null) > 0:`
  `unique_pct = non_null.n_unique() / len(non_null)`;
  `if unique_pct > 0.90 and col.dtype not in ("int", "uint", "float"): return findings`
  (was `col.dtype not in _numeric_dtypes()`).
- `is_numeric = col.dtype in ("int", "uint", "float")` (was `col.dtype in _numeric_dtypes()`).
- **Numeric path:** `mean1 = first_half.mean()`; `mean2 = second_half.mean()`;
  `std1 = first_half.std()` (all via seam). The `None`/`std1 == 0` guard, `deviation`, thresholds,
  severity, and the numeric-drift Finding are UNCHANGED.
- **Categorical path:** `cats_first = set(first_half.cast("str", strict=True).to_list())`;
  `cats_second = set(second_half.cast("str", strict=True).to_list())` (was `cast(pl.String)`).
  `new_cats = cats_second - cats_first`; the `new_cat_pct` threshold, `sample_new = sorted(new_cats)
  [:10]` are UNCHANGED. `affected = second_half.cast("str", strict=True).member_count(list(new_cats))`
  (was `int(second_half.cast(pl.String).is_in(list(new_cats)).sum())` — `member_count` already
  delegates to exactly `int(self._s.is_in(values).sum())`). The categorical-drift Finding UNCHANGED.

## Testing

- **Seam unit tests** (`tests/core/test_frame.py` additions): `slice(0, mid)` / `slice(mid)` equal
  the raw `s.slice(0, mid)` / `s.slice(mid)` (compare `.to_list()`), and match Python slicing
  `s[:mid]` / `s[mid:]`; `cast("str")` on an int column yields the string values (compare `.to_list()`
  to `s.cast(pl.String).to_list()`).
- **Parity gate:** `test_drift_detection.py` passes with **zero edits** (byte-identical Findings —
  both the numeric-drift and categorical-drift branches).
- **Regression:** full suite green (same counts + the new seam tests); import gate green
  (`tests/test_import_no_polars.py`); the ported file grep-clean of `polars`/`_polars_lazy`/`pl.`.

## Risks

- **`slice` byte-identity** — `s[:mid]`/`s[mid:]` delegate to `Series.slice` under the hood; the
  seam op calls `.slice` directly with the same offsets, so values + order are identical. The
  `slice(mid)` (no length) form correctly runs to the end, matching `s[mid:]`.
- **`cast("str")` strictness** — the port passes `strict=True` to mirror the original
  `cast(pl.String)` default; for a String target the result is identical regardless, so this is
  belt-and-suspenders faithfulness, not a behavior change.
- **`member_count` reuse** — `second_half.cast("str").member_count(list(new_cats))` delegates to
  `int(self._s.is_in(values).sum())`, byte-identical to the original
  `int(second_half.cast(pl.String).is_in(list(new_cats)).sum())`. `is_in` on a String column with a
  `list(new_cats)` of Python strings is unchanged.
- **Seam growth** — only 1 method + 1 cast-kind. `slice` is a general positional primitive (offset +
  optional length), not a task-shaped `first_half`/`second_half` op.
- **`n_unique` / `.dt` etc.** — no new op needed; `n_unique`, `drop_nulls`, `to_list`, `__len__`,
  `mean`, `std`, `member_count` all pre-exist.

## Non-goals (YAGNI)
`pattern_consistency` (Batch A2b); the relation profilers; a `value_counts` / `str_replace_all` seam
op; the Stage-2 non-Polars backend; reader; deps flip.
