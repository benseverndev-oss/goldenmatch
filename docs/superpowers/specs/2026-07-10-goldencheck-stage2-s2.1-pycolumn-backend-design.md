# GoldenCheck Polars eviction — Stage-2 S2.1 (covered pure-Python Column/Frame backend + scan_columns)

Date: 2026-07-10
Status: design — approved in brainstorming, pending spec review
Base: fresh `origin/main` **after S2.0 #1618 merges** (the code tasks MODIFY S2.0's `tests/nopolars/` scaffold, so the code branch must be rebased onto main-with-S2.0; this spec doc is a new file and carries no conflict).
Parent program: goldencheck Polars eviction — Stage-2 (see `2026-07-10-goldencheck-stage2-s2.0-nopolars-lane-design.md` for the S2.0–S2.2/reader/P4 roadmap)

## Context

S2.0 shipped the nopolars scaffold + advisory lane (import-survival + clean-decline, **no covered
scan** — goldencheck had no non-Polars backend). S2.1 ships the **first covered non-Polars backend**: a
pure-Python `Column`/`Frame` so the three *mechanical* column profilers run polars-free, byte-identically,
plus a public `scan_columns(dict)` entry and the covered-scan assertions the S2.0 lane deferred.

**Why these three are cleanly coverable (verified from source):** `nullability`, `cardinality`,
`uniqueness` use only a tiny, **dtype-free** op set — `__len__`, `null_count`, `n_unique`, `drop_nulls`,
`unique`, `sort`, `to_list` — and none call `dtype`/`cast`/regex/date/stats. Every one of those 7 ops is
byte-identical to Polars on a pure-Python `dict[str,list]` backend for homogeneous, null-as-`None`,
non-NaN columns (see byte-identity anchors). This is the goldenflow "covered-subset" pattern: run the
covered checks on the pure-Python backend, leave the rest to Polars.

## Scope

### In scope
1. **`PyColumn` / `PyFrame`** (pure-Python, `dict[str,list]`) in `core/frame.py` implementing the 7
   mechanical `Column` ops + the `Frame` surface + `PyFrame.from_columns(dict)`.
2. **`to_frame` reorder** so the `PolarsFrame`/`PyFrame` fast-paths are checked BEFORE `pl.DataFrame`
   (so `to_frame(py_frame)` never touches the `pl` symbol) + a `PyFrame` passthrough.
3. **`scan_columns(columns: dict[str, list]) -> list[Finding]`** — a public, polars-free reduced scan
   that runs the covered profilers (`Nullability`, `Uniqueness`, `Cardinality`) per column on a
   `PyFrame`. Exported from `goldencheck/__init__.py`.
4. **A byte-parity test** (normal suite, polars present): each covered profiler yields identical
   `Finding`s on `PolarsFrame` vs `PyFrame` for representative data; `scan_columns(d)` equals the covered
   profilers run over `PyFrame.from_columns(d)`.
5. **Covered-scan assertions in `tests/nopolars/`** (S2.0's module): `scan_columns({...})` runs
   polars-free + asserts Findings; the import-blocker path asserts `scan_columns` works with polars
   unimportable.

### Explicitly NOT in scope
`dtype`/`cast` (type_inference); `min`/`max`/`mean`/`std` (range/sequence/drift — float-precision
byte-identity risk); the regex/date/value_counts profilers; wiring `PyFrame` into the main
`scan_dataframe` scanner (its polars scan path is UNCHANGED — no byte-identity surface touched); the
non-Polars reader (S2.2+/reader); the deps-flip.

### Success criteria
- The 3 covered profilers produce **byte-identical** Findings on `PyFrame` vs `PolarsFrame` (the parity
  gate).
- `scan_columns(dict)` runs with polars genuinely absent (the nopolars lane) + with polars unimportable
  (the import-blocker), producing the covered Findings and loading zero polars.
- The existing full suite is green; `import goldencheck` still loads zero Polars; `scan_dataframe` (the
  polars path) is byte-identical (unchanged).

## The backend (`core/frame.py`)

### `PyColumn` (wraps a plain `list`, `__slots__ = ("_v",)`)
| Op | Impl | Polars equivalent it matches |
|---|---|---|
| `__len__` | `len(self._v)` | `len(s)` |
| `null_count` | `sum(1 for v in self._v if v is None)` | `s.null_count()` (null == `None`; NaN is NOT null) |
| `n_unique` | `len(set(self._v))` | `s.n_unique()` (counts null as ONE distinct — matches `len({...,None})`) |
| `drop_nulls` | `PyColumn([v for v in self._v if v is not None])` | `s.drop_nulls()` |
| `unique` | `PyColumn(list(dict.fromkeys(self._v)))` OR `list(set(...))` | `s.unique()` (order unspecified in Polars; callers `.sort()` after) |
| `sort` | `PyColumn(sorted(self._v))` | `s.sort()` (homogeneous column → same order) |
| `to_list` | `list(self._v)` | `s.to_list()` |

`unique`'s order does not matter (the only caller, `cardinality`, does `drop_nulls().unique().sort()`);
use whichever is simplest — `sorted` is applied downstream so `list(set(...))` is fine. (If a future
caller needs `unique()` order to match Polars, revisit — YAGNI now.)

### `PyFrame` (wraps a `dict[str, list]`, `__slots__ = ("_cols",)`)
- `from_columns(cols: dict[str, list]) -> PyFrame` classmethod (or `PyFrame(cols)`).
- `columns -> list[str]` = `list(self._cols.keys())`.
- `height -> int` = `len(next(iter(self._cols.values())))` if non-empty else `0`.
- `native -> dict` = `self._cols`.
- `column(name) -> PyColumn` = `PyColumn(self._cols[name])`.
- Assumes equal-length columns (like `pl.DataFrame(dict)`); ragged input is caller error (do NOT add
  validation unless a test needs it — YAGNI).

### `to_frame` reorder (byte-identical for existing callers)
```python
def to_frame(native):
    if isinstance(native, (PolarsFrame, PyFrame)):   # fast-paths first; NO pl access
        return native
    if isinstance(native, pl.DataFrame):             # pl touched ONLY for raw polars input
        return PolarsFrame(native)
    raise TypeError(...)
```
The `PolarsFrame` passthrough + the `pl.DataFrame → PolarsFrame` path are unchanged for existing callers;
the ONLY change is the new `PyFrame` passthrough and that a `PyFrame` never reaches the `pl.DataFrame`
check. This is what makes `to_frame(py_frame)` polars-free.

## `scan_columns` (`engine/scanner.py`, exported from `__init__.py`)

```python
_COVERED_COLUMN_PROFILERS = [NullabilityProfiler(), UniquenessProfiler(), CardinalityProfiler()]

def scan_columns(columns: dict[str, list]) -> list[Finding]:
    """Polars-free reduced scan of the covered STRUCTURAL checks (nullability,
    uniqueness, cardinality) over in-memory columns. The regex/format/encoding/
    date/value-count checks need Polars -- use scan_dataframe for a full scan."""
    frame = PyFrame.from_columns(columns)
    findings: list[Finding] = []
    for name in columns:                      # dict order
        for profiler in _COVERED_COLUMN_PROFILERS:
            findings.extend(profiler.profile(frame, name))
    return findings
```
- Deterministic order: columns in dict-insertion order × the fixed covered-profiler order.
- Return type `list[Finding]` (NOT the `(findings, DatasetProfile)` tuple `scan_dataframe` returns — this
  is a deliberately reduced, polars-free entry; it does NOT reproduce classification / denial / sampling
  / DatasetProfile, all of which are polars-coupled and out of scope).
- Export `scan_columns` from `goldencheck/__init__.py` + add to `__all__`.
- The three covered profilers accept `*, context=None` and ignore it; `scan_columns` passes no context
  (matches their default).

## Testing

### Byte-parity test (normal suite — polars PRESENT — the byte-identity gate)
`tests/engine/test_scan_columns_parity.py` (new file). For representative data
`d = {"id": [1,2,3,4,...], "cat": ["a","a","b",None,...], "val": [1.0,2.0,3.0,...], "flag": [...]}`
covering int / str+null / clean float:
- For each covered profiler P and each column: `assert P.profile(PolarsFrame(pl.DataFrame(d)), col) ==
  P.profile(PyFrame.from_columns(d), col)` (backend parity — identical `Finding`s). Relies on
  `Finding.__eq__`; if `Finding` is not directly comparable, compare a normalized tuple of its fields.
- `assert scan_columns(d) == [f for name in d for P in covered for f in P.profile(PolarsFrame(pl.DataFrame(d)), name)]`
  (scan_columns == the covered profilers over the SAME data; proves the wrapper + the backend agree with
  Polars).
- Include cases that exercise each finding branch (all-null, 0-null≥10-rows, low-cardinality enum,
  100%-unique PK, near-unique identifier-with-dups) so parity is proven where the profilers actually emit.

### nopolars lane (polars ABSENT — extends S2.0's `tests/nopolars/test_polars_absent.py`)
- `test_covered_scan_columns_without_polars()`: `from goldencheck import scan_columns`; run
  `scan_columns({...})` over data that triggers known findings; assert the returned Findings against
  **hardcoded literals** (the parity test above proves those literals == the Polars path); assert
  `"polars" not in sys.modules` after. (Mirrors goldenflow's covered-path assertions.)
- Extend the import-blocker subprocess test (S2.0's `test_goldencheck_survives_polars_unimportable` in
  `tests/test_import_no_polars.py`) OR add a sibling: with `polars` blocked, `scan_columns({...})`
  returns findings AND `"polars" not in sys.modules` (proves the covered path is genuinely polars-free
  end-to-end, in the REQUIRED suite).

## Byte-identity anchors / risks

- **null = `None`** — Polars null round-trips as Python `None` in `.to_list()`; the covered profilers see
  nulls only via `null_count`/`drop_nulls`/`n_unique`, all of which treat `None` exactly as Polars treats
  null. NaN is a distinct float value (NOT null) in both — the covered set does not special-case NaN.
- **`n_unique` counts null as ONE** — `pl.Series([1,1,None]).n_unique() == 2`; `len({1,1,None}) == 2`.
  Matches for `cardinality` (full-column `n_unique`) and `uniqueness` (post-`drop_nulls`, no nulls).
- **`sort` on homogeneous columns** — `sorted(list)` matches `s.sort()` for int/str/clean-float columns
  (columns are homogeneous, so no mixed-type `TypeError`). **The one edge is float NaN**: Polars sorts
  NaN last; Python `sorted` with NaN is position-undefined. The covered set **assumes no NaN**; the
  parity test uses clean floats and does NOT include NaN. Documented — if a NaN-bearing low-cardinality
  float column ever needs coverage, that is a follow-up (add a NaN-aware `sort`), not S2.1.
- **`to_frame` reorder** — byte-identical for existing callers (the `PolarsFrame`/`pl.DataFrame` paths are
  unchanged); the reorder matters only so a `PyFrame` never touches `pl.DataFrame`. Existing tests + the
  import gate are the proof.
- **`scan_dataframe` unchanged** — S2.1 does NOT wire `PyFrame` into the main scanner; the polars scan
  path is untouched (no byte-identity surface). `scan_columns` is a separate, additive entry.
- **`Finding` equality** — the parity test depends on comparing `Finding`s; confirm `Finding` supports
  `==` (dataclass/pydantic) or compare normalized field tuples. (Verify at plan time.)

## Non-goals (YAGNI)
`dtype`/`cast`/stats/regex/date/value_counts ops on `PyColumn`; wiring `PyFrame` into `scan_dataframe`;
`PyColumn.unique()` order matching Polars; ragged-column validation; the reader; the deps-flip; a
NaN-aware sort.
