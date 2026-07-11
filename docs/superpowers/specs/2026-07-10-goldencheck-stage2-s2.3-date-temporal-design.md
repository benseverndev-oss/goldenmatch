# GoldenCheck Polars eviction — Stage-2 S2.3 (str_to_date chrono kernel + date-typed PyColumn surface + temporal)

Date: 2026-07-10
Status: design (autonomous — /goal "S2 is implemented"); self-reviewed via spec-document-reviewer
Base: the S2.2 branch tip (`feat/goldencheck-stage2-s2.2-native-hardops`, PR #1635) — S2.3 EXTENDS S2.2's `PyColumn` (dtype, regex ops, eq/filter_by, value_counts) + `scan_columns`. Built locally atop S2.2; rebased onto `origin/main` once #1635 merges.
Parent: goldencheck Polars eviction — Stage-2 (S2.0 nopolars lane #1618, S2.1 PyColumn backend #1630, S2.2 regex kernel #1635). S2.3 is the LAST covered-subset piece — it lands the 4th hard profiler, `temporal`, which S2.2 carved out because it needs a date-typed backend surface.

## Context

S2.2 made `encoding_detection`/`format_detection`/`pattern_consistency` run polars-free via a native `regex` kernel. It carved `temporal` because wiring it needs more than the `str_to_date` kernel: `relations/temporal.py` calls `gt_mask`/`fill_null`/`sum`/`filter_by`/`cast("str")` on **date-typed** columns and requires the parsed result to report `dtype == "date"`. S2.3 builds that date-typed surface + the chrono kernel + wires temporal into `scan_columns`. After S2.3, every column/relation profiler that CAN run polars-free byte-identically DOES (the Stage-2 substrate is complete; the remaining program work is the reader + the P4 deps-flip).

## What `temporal` actually needs (verified from `relations/temporal.py`)

`TemporalOrderProfiler.profile(frame)` is a WHOLE-FRAME relation profiler (not per-column). It:
1. Finds start/end column pairs by name heuristics (`_find_date_pairs`) + an any-date-pair fallback.
2. For a str column, casts via `col.str_to_date("%Y-%m-%d", strict=False)` (temporal.py:67,100) and checks `len(casted.drop_nulls()) > 0` to decide it's a date column.
3. For each pair: `start.gt_mask(end).fill_null(False)` → `violation_mask`; `violation_mask.sum()` → `violation_count`; if `> 0`, samples via `start.filter_by(violation_mask).cast("str").to_list()[:3]` (and end), builds a `Finding`.
4. Requires `start_series.dtype`/`end_series.dtype in ("date","datetime")` after the cast (temporal.py:135) — so `str_to_date`'s result MUST report `dtype == "date"`.

**No date ARITHMETIC** (no diffs/day-deltas) — only ordering (`gt_mask`), null-fill, count (`sum`), filter, and `cast("str")` for samples. This is why a date representation whose `>` and `str()` match Polars is sufficient.

## Design

### 1. `str_to_date` chrono kernel (native, byte-identical to Polars)

Polars' `s.str.to_date(format=fmt, strict=strict)` is `chrono` under the hood. A chrono kernel with the SAME `fmt` gives byte-identical parse-validity (which strings become a date vs null) and canonical output.

- **`goldencheck-core/src/date.rs`** (pyo3-free): `str_to_date(values: &[Option<String>], fmt: &str) -> Vec<Option<String>>` — for each element: `None`→`None`; else `chrono::NaiveDate::parse_from_str(s, fmt)` → `Ok(d)`→`Some(d.format("%Y-%m-%d").to_string())` (canonical ISO), `Err`→`None` (matches `strict=False`: unparseable → null). Add `chrono = { version = "0.4", default-features = false, features = ["std"] }` to `goldencheck-core/Cargo.toml`.
- **`goldencheck-native/src/date.rs`** (pyo3 shim): `#[pyfunction] str_to_date(values: Vec<Option<String>>, fmt: &str) -> PyResult<Vec<Option<String>>>` delegating to core. Register in `lib.rs`.
- **Loader:** `_COMPONENT_SYMBOLS["str_to_date"] = ("str_to_date",)`.

Returning ISO strings (not a date type) across FFI keeps the boundary simple; the Python seam converts to `datetime.date` (below). Byte-identity holds because (a) chrono decides valid-vs-null on both sides, and (b) a canonical `%Y-%m-%d` string round-trips to the same `datetime.date`.

### 2. Date-typed `PyColumn` surface

`str_to_date` returns a `PyColumn` whose values are Python `datetime.date` objects (or `None`), so real date `>` comparison + `str()` formatting are used (no lexicographic assumption):
```python
def str_to_date(self, fmt: str, *, strict: bool) -> PyColumn:
    if strict:
        raise NotImplementedError("goldencheck str_to_date supports strict=False only")  # temporal only uses False
    iso = _date_kernel().str_to_date(self._v, fmt)          # list[str|None], native-guarded
    return PyColumn([date.fromisoformat(s) if s is not None else None for s in iso])
```
(`date.fromisoformat` on a canonical `%Y-%m-%d` string always succeeds; `from datetime import date` at module top. `_date_kernel()` mirrors `_regex_kernel()` — raises `NativeRequiredError` when `native_enabled("str_to_date")` is False.)

New pure-Python ops on `PyColumn` (all null-preserving to match Polars):
- **`dtype`** — EXTEND the S2.2 inference: `isinstance(first, datetime) → "datetime"` BEFORE `isinstance(first, date) → "date"` (datetime subclasses date). So a `str_to_date` result reports `"date"`, satisfying temporal.py:135. (Existing str/int/float/bool/other unchanged.)
- **`gt_mask(other)`** — `[None if a is None or b is None else a > b for a, b in zip(self._v, other._v)]` → `PyColumn` of `bool|None`. Matches Polars `s > other` (null propagates to null).
- **`fill_null(value)`** — `[value if v is None else v for v in self._v]`. Matches `s.fill_null(value)`.
- **`sum()`** — `sum(v for v in self._v if v is not None)`. On a null-free bool mask (post-`fill_null(False)`) this is the count of `True`, matching Polars `mask.sum()` (skips nulls, returns int). Returns `0` for empty.
- **`cast(kind, *, strict=False)`** — `kind == "str"`: `[None if v is None else str(v) for v in self._v]`. `str(datetime.date(2021,1,5)) == "2021-01-05"`, matching Polars `date.cast(Utf8)`. Other kinds: raise `NotImplementedError` (YAGNI — temporal only casts date→str; do NOT reintroduce a general cast).

### 3. Wire `temporal` into `scan_columns`

`temporal` is `profile(frame)` (whole-frame), so it does NOT fit the per-column loop. Run it ONCE over the `PyFrame`, gated on the date kernel, appended after the per-column profilers:
```python
# after the per-column loop in scan_columns:
if native_enabled("str_to_date"):
    findings.extend(TemporalOrderProfiler().profile(frame))
```
(Import `TemporalOrderProfiler` in scanner.py — it is already imported there for `scan_dataframe`.) When the date kernel is absent, temporal is skipped-with-a-log (extend the S2.2 skip message, or add a second `logger.info`). `scan_columns`' regex-gated hard-3 (S2.2) is unchanged.

## Coverage matrix (Stage-2 complete)

| Environment | Mechanical 3 | Regex 3 (encoding/format/pattern) | temporal |
|---|---|---|---|
| polars present | run | run via Polars | run via Polars |
| polars ABSENT + native present | run (PyColumn) | run (native regex) — S2.2 | **run (native date) — S2.3** |
| polars ABSENT + native absent | run (PyColumn) | skip-with-log | **skip-with-log** |

## Testing

### Byte-parity gate (polars PRESENT + native PRESENT)
- **kernel/backend:** `str_to_date` on a corpus of valid + malformed strings (`"2021-01-05"`, `"2021-1-5"` non-padded, `"2021-13-01"` bad month, `"2021-02-30"` bad day, `""`, `"nope"`, `None`, `"2021-01-05x"` trailing) — assert `PyColumn.str_to_date(...).to_list() == PolarsColumn.str_to_date(...).to_list()` (both produce `datetime.date | None`; Polars `.to_list()` on a Date series yields `datetime.date`). Also assert the `dtype` of the result is `"date"` on both.
- **date ops:** `gt_mask`/`fill_null`/`sum`/`cast("str")`/`filter_by` on two date columns — identical results PyColumn vs PolarsColumn.
- **temporal end-to-end:** `TemporalOrderProfiler().profile(PolarsFrame(pl.DataFrame(d))) == TemporalOrderProfiler().profile(PyFrame.from_columns(d))` for data with a start/end pair containing violations, a clean pair, and a non-date column — identical `Finding`s (message, sample_values, affected_rows, confidence).
- **scan_columns:** `scan_columns(d)` includes temporal's findings when a date pair is present (mirror the S2.2 hardops-parity test's `expected` construction, now also running `TemporalOrderProfiler().profile(pol)` once).

### Existing tests UNEDITED (regression gate)
`relations/temporal.py`'s existing tests + `tests/core/test_native_parity.py` pass with ZERO edits. The S2.2 `scan_columns` parity tests are updated ONLY to also expect temporal's findings when native is present (same gate-mirror discipline as S2.2's S2.1-test update — a contract-tracking infra edit, not a behavior regression).

### nopolars lane + import-blocker (native present, polars absent)
Extend `tests/nopolars/test_polars_absent.py` + `tests/test_import_no_polars.py`: with polars unimportable + native present, `scan_columns` on a start/end date pair produces a `temporal_order` finding and `"polars" not in sys.modules`. Both skip cleanly when native isn't built.

## Byte-identity anchors / risks
- **chrono parse parity** — Polars `.str.to_date` IS chrono; using chrono with the same `fmt` gives identical valid-vs-null decisions AND the same canonical date (incl. non-zero-padded `%m`/`%d`, which chrono accepts on both sides). The malformed corpus is the guard.
- **`datetime.date` `>` / `str()`** — `date > date` matches Polars date ordering; `str(date)` is zero-padded ISO, matching Polars `date.cast(Utf8)`. Both verified in the parity test.
- **`dtype` extension** — adding `datetime`/`date` to `PyColumn.dtype` inference is additive; the S2.2 dtype parity test (str/int/float/bool/all-None) is unaffected. `datetime` MUST be checked before `date` (subclass).
- **`sum()` return type** — Python `sum` over bools returns `int` (matches Polars `Boolean.sum()`); over an empty/all-null mask returns `0` (matches Polars `sum()` of an empty selection → 0 after the `fill_null(False)`).
- **temporal is whole-frame** — running it once (not per-column) is correct; the per-column loop is untouched. Its name-heuristic pair-finding is Python-only (no seam ops), so it ports unchanged.
- **native build for tests** — same as S2.2 (in-tree `goldencheck._native`; Windows `.dll`→`.pyd` copy; toolchain env). Date-dependent tests skip cleanly when native absent; CI parity lane is the backstop.
- **imports** — `frame.py` must import BOTH `date` and `datetime` (`from datetime import date, datetime`) — the dtype extension checks `isinstance(first, datetime)` before `isinstance(first, date)`.
- **parse-fullness / trailing chars** — chrono `NaiveDate::parse_from_str("2021-01-05x", "%Y-%m-%d")` requires the WHOLE string to match → `Err` → null; Polars `str.to_date(strict=False)` uses the same chrono strptime → also null. The malformed corpus includes the trailing-char case as the guard. **Contingency:** if the parity corpus EVER reveals a Polars leniency a bare `NaiveDate::parse_from_str` doesn't share, do NOT loosen the test — either reconcile the kernel to Polars' exact chrono entry point, or DECLINE temporal (leave it Polars-only, like R4's tail) and document. Byte-identical or decline.
- **out-of-range years** — chrono can parse years outside `datetime.date`'s 1..=9999 range; both `date.fromisoformat` (Python) and Polars `.to_list()` would then fail (possibly differently). Out of scope — temporal runs on real date columns; not in the corpus. If it ever matters, clamp/decline (not this stage).

## Non-goals (YAGNI)
`strict=True` date parsing (temporal only uses False → raise NotImplementedError); date ARITHMETIC / `diff` on date columns; a general `cast` (only date→str); `datetime`-typed parsing (only `str_to_date`→Date); wiring temporal's Polars path differently (`scan_dataframe` unchanged); the reader; the P4 deps-flip.
