# GoldenCheck Polars eviction — Relation ports R1 (identity_safe_pk) + relation-front roadmap

Date: 2026-07-09
Status: design — approved in brainstorming, pending spec review
Base: fresh `origin/main` (P0 #1605 + A #1606 + A2 #1607 + B #1608 + C #1610 + A2b #1611 all merged; the whole **column-profiler** front is seam-routed)
Parent program: goldencheck Polars eviction (see `2026-07-08-goldencheck-polars-eviction-p0-design.md`)

## Context

The column-profiler front is complete (12/13 column profilers polars-free through the `Frame`/`Column`
seam). This opens the **relation-profiler** front: the 9 dataset-level profilers under
`goldencheck/relations/` (plus `baseline/correlation.py` and the `functional_dependencies.py` bridge),
which take a whole `pl.DataFrame` (`profile(self, df)`, not `BaseProfiler`, no `column` arg).

**Key finding (reframes the value):** these profilers are **already import-gate-safe** — P0's
package-wide sweep converted them to `from goldencheck._polars_lazy import pl` (lazy) with deferred
dtype tuples, and they already `import to_frame`. `import goldencheck` loads zero Polars today. So this
front is **not** about the import gate. It is about **seam-decoupling** the portable profilers (route
their `pl.` bodies through the seam so the eventual Stage-2 substrate can back them, and they go
grep-clean) and **formally declining** the gnarly tail. Routing a body through the seam buys nothing at
runtime until the substrate exists (PolarsColumn just delegates to Polars) — the value is completing
the seam surface (it defines exactly which Frame-level ops the substrate must implement) and drawing the
Polars-only boundary in code. Same "Polars as accelerator" model as [[project_goldenflow_polars_eviction]].

This spec designs **R1** (the first, easiest sub-batch: `identity_safe_pk`) and locks the R1–R4
decomposition as the roadmap.

## The relation-front roadmap (R1–R4)

Grouped by shared seam need (each is its own spec→plan→execute cycle):

| Sub-batch | Profilers | Seam work | Bucket |
|---|---|---|---|
| **R1** (this spec) | `identity_safe_pk` | per-column reductions (mostly existing ops) + `pl.Boolean → "bool"` in the neutral map + `Column.dtype_repr()` | EASY |
| **R2** | `composite_key`, `functional_dependency`, `approx_fd` (+ the `functional_dependencies` bridge) | `Frame.select(cols).n_unique()` (multi-column distinct-row count) + an Arrow-export escape hatch for their native-kernel paths | EASY/MED |
| **R3** | `null_correlation`, `numeric_cross`, `temporal` | two-column element-wise ops (`col_a` vs `col_b` comparison → mask, null-mask agreement count, `fill_null`, filter-other-by-mask) + str→date parse | MEDIUM |
| **R4 (DECLINE)** | `age_validation`, `approx_duplicate`, `baseline/correlation` | keep raw-Polars bodies (already lazy-safe); formally mark **Polars-accelerator-only** + document the decline (expression trees / `group_by`+`join` / `pivot`+numpy/scipy — not substrate-portable) | HARD |

R4 is mostly a documentation/decline pass, not a port. Only R1–R3 add seam ops.

## Scope (R1 only)

### In scope
Port `identity_safe_pk`'s `profile()` (and its module helper `_column_qualifies_as_pk`) onto the seam,
adding **1** `Column` method (`dtype_repr`) and **1** neutral-dtype-map entry (`"bool"`).
Byte-identical, no version bump.

### Explicitly NOT in scope
R2/R3/R4 and their profilers. The Stage-2 non-Polars backend. The reader. The deps flip.

### Success criteria
- `identity_safe_pk` is polars-free (grep-clean of `polars`/`pl.`), routing through the seam.
- Its existing test (`tests/relations/test_identity_safe_pk.py`) passes **unedited** (the parity gate).
- Full suite green; `import goldencheck` still loads zero Polars.

## The seam additions (`core/frame.py`)

| Change | Where | Impl | Why |
|---|---|---|---|
| `"bool"` category | `_neutral_dtype` | add `if dt == pl.Boolean: return "bool"` (before `else "other"`) | the disqualifier check `dtype == pl.Boolean` becomes `col.dtype == "bool"` |
| `dtype_repr()` | `Column` Protocol + `PolarsColumn` | `PolarsColumn`: `str(self._s.dtype)` | the reason string `f"unsuitable dtype ({dtype})"` must still render `"Float64"`/`"Boolean"` byte-identically |

- **`"bool"` re-map is behavior-neutral for existing profilers** — verified: no ported profiler branches
  on `col.dtype == "other"` or `"bool"`; every profiler gates on *positive* categories
  (str/int/uint/float/date/datetime), so a Boolean column stays skipped exactly as when it mapped to
  `"other"` (e.g. drift's `dtype not in ("int","uint","float")` skip is unchanged). Only
  `identity_safe_pk` reads `"bool"`.
- **`dtype_repr()` is a display op** (returns the backend's dtype string). For the Polars backend it is
  `str(self._s.dtype)` = `"Float64"`/`"Boolean"`/… — byte-identical to the original `f"({dtype})"`. A
  future substrate implements its own repr; this is a backend-local display string, deliberately not a
  neutral category (the neutral category is `dtype`).
- Neither change references the `pl.` symbol at method scope (`_neutral_dtype` already imports the lazy
  `pl` proxy at module scope, unchanged; `dtype_repr` uses only `self._s.dtype`), so the import gate
  stays green.

## The port (`relations/identity_safe_pk.py`)

- **`_column_qualifies_as_pk(df, column)` → `_column_qualifies_as_pk(frame, column)`** (the test does NOT
  import this helper, so the signature change is free): drop the `df: pl.DataFrame` annotation;
  `col = frame.column(column)`; dtype check `if col.dtype in ("float", "bool"):` (was
  `if dtype.is_float() or dtype == pl.Boolean:`); reason `f"unsuitable dtype ({col.dtype_repr()})"` (was
  `f"({dtype})"`). `n_rows = len(col)`; `n_nulls = col.null_count()`; `col.n_unique() != n_rows` — all
  existing Column ops. The empty/null/non-unique reason strings + the qualifies-True string are pure
  Python, UNCHANGED.
- **`profile(self, df)` → `profile(self, frame)`**: drop `df: pl.DataFrame`; keep `frame = to_frame(frame)`;
  remove `df = frame.native`. `if len(frame.columns) == 0: return []` (was `df.width == 0`). Loop
  `for column in frame.columns:` (was `df.columns`) calling `_column_qualifies_as_pk(frame, column)`.
  `affected_rows=frame.height` (was `len(df)`, both = row count). `sample_cols = ", ".join(frame.columns[:5])`;
  `if len(frame.columns) > 5:` (was `df.width > 5`). Both Findings (the named-PK-disqualifier WARNING and
  the generic `__dataset__` WARNING) — severity/check/column/message/affected_rows/sample_values/suggestion/
  confidence — UNCHANGED.
- Remove `from goldencheck._polars_lazy import pl`. Keep `to_frame`, `Finding`, `Severity`, and the
  `_VALUE_COLUMN_PATTERNS`/`_PK_NAME_PATTERNS`/`_looks_like_value_column`/`_looks_like_pk_column` module bits
  (all pure Python, no `pl`).

## Testing

- **Seam unit tests** (`tests/core/test_frame.py` additions): `_neutral_dtype`/`dtype` maps a `pl.Boolean`
  Series → `"bool"` (and a Float column still → `"float"`); `dtype_repr()` returns `str(s.dtype)`
  (`"Float64"`, `"Boolean"`, `"Int64"`, `"String"`).
- **Parity gate:** `tests/relations/test_identity_safe_pk.py` passes with **zero edits** (all 10 tests —
  clean-PK, uuid, no-PK, named-PK-with-nulls, named-PK-with-dups, value-column, float, boolean, empty-DF,
  multiple-candidates).
- **Regression:** full suite green (same counts + the new seam tests); import gate green; the ported file
  grep-clean of `polars`/`_polars_lazy`/`pl.`.

## Risks

- **`"bool"` neutral-map change touches a SHARED file** (`_neutral_dtype`) used by every ported profiler.
  Mitigated: verified no profiler branches on `"other"`/`"bool"`; Boolean stays skipped by all positive-
  category gates. The seam unit test pins the new mapping; the full suite is the regression proof.
- **`dtype_repr` byte-identity** — `str(self._s.dtype)` equals the original `f"({dtype})"` interpolation
  (both call `str()` on the same Polars dtype). Reachable only via the untested named-PK-disqualified-by-
  dtype path; held strict per the session's byte-identity discipline.
- **`_column_qualifies_as_pk` signature change** — safe: the test imports only `IdentitySafePkProfiler`,
  not the helper.
- **`df.width` → `len(frame.columns)`** — `width` == column count == `len(columns)`; byte-identical for the
  empty-DF (`pl.DataFrame()` → `columns == []`) and the `> 5` cases.
- **Seam growth** — 1 method + 1 map entry. `dtype_repr` is a general display primitive; no task-shaped op.

## Non-goals (YAGNI)
R2/R3/R4 profilers; `Frame.width` (use `len(columns)`); `Frame.select().n_unique()` (R2); two-column
element-wise ops (R3); the Stage-2 non-Polars backend; reader; deps flip.
