# GoldenCheck Polars eviction — Profiler ports Batch A2b (pattern_consistency: str_replace_all + value_counts + cross-column mask)

Date: 2026-07-09
Status: design — approved in brainstorming, pending spec review
Base: fresh `origin/main` (P0 #1605 + A #1606 + A2 #1607 + B #1608 + C #1610 all merged; the seam has `dtype`/`cast`/`member_count`/`str_match_count`/`str_filter`/`min`/`max`/`mean`/`std`/`diff`/`is_sorted`/`count_gt`/`count_eq`/`filter_outside`/`slice`)
Parent program: goldencheck Polars eviction (see `2026-07-08-goldencheck-polars-eviction-p0-design.md`)

## Context

Continuing the goldencheck Polars eviction. `pattern_consistency` is the **last remaining column
profiler** and the gnarliest port — deferred through Batches A/A2/B/C because it needs three ops no
prior batch provided: a chainable vectorized `str_replace_all` (for the digit/letter
generalization), a `value_counts` histogram, and a **cross-column mask filter** (sampling
`non_null` rows by a mask computed on the derived `patterns` column). This batch designs those ops
and ports the profiler. **11 of ~13 column profilers are polars-free; this makes 12 — the whole
column-profiler front.**

**Seam philosophy (unchanged):** the ops go on the `Column` seam; `PolarsColumn` delegates to the
exact Polars call (byte-identical, no perf regression, Polars stays the fast path). Note this port
buys **no perf** — `pattern_consistency` is already vectorized (`_generalize_series` + Polars
`value_counts`); it is a pure byte-identical decoupling.

## Scope

### In scope
Port `pattern_consistency`'s `profile()` onto the seam, adding **4** `Column` methods. Byte-identical,
no version bump.

### Explicitly NOT in scope
The 9 relation profilers (some are the FD-mining/pivot decline-to-Polars tail). The Stage-2
non-Polars backend. The reader. The deps flip.

### Success criteria
- `pattern_consistency` is polars-free (grep-clean of `polars`/`pl.`), routing through the seam.
- Its existing test passes **unedited** (byte-identical — the parity gate). This includes the three
  direct `_generalize_series` tests, which lock that helper's Polars-regex behavior.
- Full suite green; `import goldencheck` still loads zero Polars.

## The seam additions (`core/frame.py` — `Column`, PolarsColumn delegates)

Four methods. Each `PolarsColumn` method delegates to the exact Polars call `pattern_consistency`
uses today, so the port is byte-identical. **None reference the `pl.` symbol** (all `self._s.*` /
`mask._s`), so the import gate stays green.

| Method | Signature | PolarsColumn impl | Covers |
|---|---|---|---|
| `str_replace_all` | `str_replace_all(pattern: str, value: str) -> Column` | `PolarsColumn(self._s.str.replace_all(pattern, value))` | the `\p{L}`→`L`, `\d`→`D` generalization (chained) |
| `value_counts_desc` | `value_counts_desc() -> list[tuple[Any, int]]` | `vc = self._s.value_counts().sort("count", descending=True); return list(zip(vc[self._s.name].to_list(), vc["count"].to_list()))` | the pattern histogram, most-frequent-first |
| `eq` | `eq(value: Any) -> Column` | `PolarsColumn(self._s == value)` | the minority-pattern boolean mask |
| `filter_by` | `filter_by(mask: Column) -> Column` | `PolarsColumn(self._s.filter(mask._s))` | sampling `non_null` rows by the `patterns` mask |

- **`str_replace_all` is chainable** (returns a Column), so
  `non_null.str_replace_all(r"\p{L}", "L").str_replace_all(r"\d", "D")` reproduces
  `_generalize_series` exactly (letters-first, then digits — the ordering the helper documents).
- **`value_counts_desc` bundles the sort.** Polars `value_counts()` order is not guaranteed and
  `.sort("count", descending=True)` is not stable, so tie order is implementation-defined — but the
  seam delegates the *identical* call chain, so the returned pair order is byte-identical to the
  original. `self._s.name` is the original `column` (preserved through `drop_nulls` +
  `str.replace_all`), so `vc[self._s.name]` selects the pattern column exactly as the original
  `pattern_counts[column]` did. The "count" values come back as Python ints via `.to_list()`,
  matching the original's `pattern_counts["count"][i]` (Polars integer indexing also yields a Python
  int) — byte-identical in the f-strings and `affected_rows`.
- **`eq` + `filter_by` are the raw boolean primitives** (declined for Batch B in favor of
  count-shaped). They earn their place here: the sample pulls values from `non_null` using a mask
  computed on the *different* `patterns` column, so no count-shaped op applies. `filter_by`'s
  PolarsColumn impl reaches into `mask._s` — a backend-internal coupling (both are PolarsColumn in
  the Polars backend); the Stage-2 substrate will implement its own `filter_by` over its own Column
  type.

## The port (`profilers/pattern_consistency.py`)

`profile()` signature `def profile(self, frame, column: str, *, context: dict | None = None)` (drop
the `frame: pl.DataFrame` annotation); keep `frame = to_frame(frame)`; remove `df = frame.native`;
`col = frame.column(column)`.

- dtype gate: `if col.dtype != "str": return findings` (was `col.dtype not in (pl.Utf8, pl.String)`).
- `non_null = col.drop_nulls()`; `total = len(non_null)`; `if total == 0: return findings` UNCHANGED.
- Pattern generation via the seam: `patterns = non_null.str_replace_all(r"\p{L}", "L")
  .str_replace_all(r"\d", "D")` (was `patterns = _generalize_series(non_null)`).
- Histogram: `pattern_counts = patterns.value_counts_desc()` (a `list[(pattern, count)]`) — replaces
  `patterns.value_counts().sort("count", descending=True)` and its `["count"]`/`[column]` indexing.
  `n_patterns = len(pattern_counts)`; `if n_patterns <= 1: return findings`.
  `dominant_pattern, dominant_count = pattern_counts[0]`. The minority loop becomes
  `for i in range(1, n_patterns): minority_pattern, minority_count = pattern_counts[i];
  minority_count = int(minority_count); minority_pct = minority_count / total; ...` — same
  thresholds, same `minority_candidates` accumulation, same rarest-first sort + `MAX_PATTERNS` cap.
- Cross-column sample: `sample_vals = non_null.filter_by(patterns.eq(minority_pattern)).to_list()[:5]`
  (was `mask = patterns == minority_pattern; non_null.filter(mask).head(5).to_list()`). Byte-identical
  (`.head(5).to_list()` ≡ `.to_list()[:5]`; same filter, same order).
- All Findings (the per-pattern detection + the structural-shift `msg_extra` + the summary Finding),
  thresholds, severities, confidences, and metadata are UNCHANGED.

### `_generalize_series` handling (chosen: drop annotations → grep-clean)

`test_pattern_consistency.py` imports and directly tests `_generalize_series` (three tests, incl. one
asserting its Polars-regex divergence on compat digits `²`/`³` — which a pure-Python port cannot
reproduce). So the helper **cannot be removed or pure-Python-ported** — the parity gate locks it. It
becomes a Polars helper used only by its own tests once `profile()` routes through the seam.

To keep the file grep-clean: make a **minimal annotation-only edit** — change
`def _generalize_series(s: pl.Series) -> pl.Series:` to `def _generalize_series(s):`; the body
(`return s.str.replace_all(r"\p{L}", "L").str.replace_all(r"\d", "D")`) is **untouched**, so all three
direct tests pass unedited. Then remove `from goldencheck._polars_lazy import pl` (no longer
referenced — the dtype gate moved to `"str"` and the annotation is gone). `_generalize` (the pure-
Python per-row helper) stays fully untouched. Optionally reword `_generalize`'s docstring reference to
`_generalize_series` so it doesn't imply the profiler still calls it (cosmetic; not required for the
gate — the docstrings contain no lowercase `polars` or `pl.` token).

Result: no `pl` symbol anywhere in the module; the file is grep-clean; the import gate is trivially
green; `_generalize_series`'s body still uses `s.str.replace_all` (a method on the passed-in Series,
not the `pl` symbol) exactly as `PolarsColumn.str_replace_all` does.

## Testing

- **Seam unit tests** (`tests/core/test_frame.py` additions): `str_replace_all` chains
  (`\p{L}`→L then `\d`→D) equal the raw `s.str.replace_all(...).str.replace_all(...)`;
  `value_counts_desc()` equals the raw `s.value_counts().sort("count", descending=True)` zipped to
  pairs (assert order + counts on a column with a clear majority + a tie-free minority);
  `eq(value)` returns the boolean mask; `filter_by(mask)` selects the masked rows (compose
  `a.filter_by(b.eq(x))` and compare to the raw `a.filter(b._s == x)`).
- **Parity gate:** `test_pattern_consistency.py` passes with **zero edits** — both the
  `PatternConsistencyProfiler` cases AND the three direct `_generalize`/`_generalize_series` tests.
- **Regression:** full suite green (same counts + the new seam tests); import gate green; the ported
  file grep-clean of `polars`/`_polars_lazy`/`pl.`.

## Risks

- **`value_counts_desc` tie order** — delegates the exact `.value_counts().sort("count",
  descending=True)`, so any nondeterministic tie order is reproduced identically. `self._s.name` ==
  the original `column`, so pattern-column selection matches. Count values are Python ints via
  `.to_list()`, matching Polars integer indexing — byte-identical in f-strings + `affected_rows`.
- **cross-column `filter_by`** — `non_null.filter_by(patterns.eq(p))` reproduces
  `non_null.filter(patterns == p)` exactly (same mask, same order); `.to_list()[:5]` ≡
  `.head(5).to_list()`.
- **`_generalize_series` retained** — the parity gate requires it; the annotation-only edit keeps its
  body + behavior identical (all three direct tests pass). The file is grep-clean because the body
  uses `s.str.*` (no `pl` symbol) and the annotation is dropped.
- **`str_replace_all` chaining order** — letters (`\p{L}`) first, then digits (`\d`), exactly as
  `_generalize_series` documents (digits-first would misclassify the literal `D`s as letters). The
  port preserves this order.
- **Seam growth** — 4 methods, all general primitives (`str_replace_all`, `value_counts_desc`, `eq`,
  `filter_by`). No task-shaped `generalize`/`pattern_histogram` op.

## Non-goals (YAGNI)
The 9 relation profilers; the Stage-2 non-Polars backend; reader; deps flip; any change to
`_generalize` / `_generalize_series` behavior.
