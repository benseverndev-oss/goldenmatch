# GoldenCheck Polars eviction — Stage-2 S2.2 (native Rust kernels for the hard column-profiler ops)

Date: 2026-07-10
Status: design — approved in brainstorming, pending spec review
Base: fresh `origin/main` **after S2.1 #1630 merges** (the code tasks EXTEND S2.1's `PyColumn`/`PyFrame`/`scan_columns`, so the code branch must be rebased onto main-with-S2.1; this spec doc is a new file and carries no conflict).
Parent program: goldencheck Polars eviction — Stage-2 (see `2026-07-10-goldencheck-stage2-s2.0-nopolars-lane-design.md` for the S2.0–S2.2/reader/P4 roadmap, and `...s2.1-pycolumn-backend-design.md` for the covered-subset backend this extends)

## Context

S2.1 shipped the first covered non-Polars backend: a pure-Python `PyColumn`/`PyFrame` implementing the 7 *mechanical* dtype-free ops, so `nullability`/`uniqueness`/`cardinality` run polars-free byte-identically via a public `scan_columns(dict)`. S2.2 extends the covered subset to the **4 hard-op profilers** — `encoding_detection`, `format_detection`, `pattern_consistency`, `temporal` — by providing the ops they need without Polars. Pure-Python `re`/`strptime` **cannot** be byte-identical to Polars for the regex/date ops (different engines; the documented `\p{Nd}`-vs-`isdigit` gap), so the byte-identity mechanism is a **native Rust kernel that uses the SAME engine as Polars** (the `regex` crate; `chrono`). This is the goldenflow "covered-subset + byte-identical-or-decline" philosophy, one tier deeper.

**Correction to prior program notes:** goldenflow-native has NO regex/date/value_counts kernels (only a phone kernel; it explicitly excludes dates). S2.2 is therefore the FIRST regex/date kernel in the Golden Suite — designed fresh, not ported from a goldenflow precedent.

## The five hard ops (verified from source on the S2.1 branch)

| Seam op | Profiler(s) | Polars call it replaces | S2.2 mechanism |
|---|---|---|---|
| `str_match_count(pattern)` | encoding_detection, format_detection | `int(s.str.contains(pattern).sum())` | **native regex kernel** |
| `str_filter(pattern, *, matching)` | encoding_detection, format_detection | `s.filter(s.str.contains(pattern) [~])` | **native regex kernel** |
| `str_replace_all(pattern, value)` | pattern_consistency | `s.str.replace_all(pattern, value)` | **native regex kernel** |
| `str_to_date(fmt, *, strict)` | temporal | `s.str.to_date(format=fmt, strict=strict)` (always `fmt="%Y-%m-%d"`, `strict=False`) | **native chrono kernel** |
| `value_counts_desc()` | pattern_consistency | `s.value_counts().sort("count", descending=True)` then zip | **pure Python (NO kernel)** — see below |

### Why `value_counts_desc` needs NO Rust kernel (the tie-order hazard dissolved)

Counting is engine-agnostic and exact — `collections.Counter(values)` produces the SAME counts as Polars `value_counts()`. The only "hard" part was the ORDER of equal-count entries, which Polars leaves implementation-defined (undocumented tie-break). `pattern_consistency` consumes this order load-bearingly: `pattern_counts[0]` is treated as the dominant pattern, and there is a top-5 minority cutoff — so at an exact skeleton-count tie, which pattern is "dominant" (and the emitted `Finding` fields) depends on tie-order.

**Resolution:** define a deterministic **total order `(count DESC, value ASC)`** and apply it in `value_counts_desc` on BOTH backends (`PolarsColumn` and `PyColumn`). This makes the two byte-identical *by construction* and removes the latent nondeterminism `pattern_consistency` has today. Strictly an improvement; the existing `pattern_consistency` tests (run unedited) are the safety check.

## Scope

### In scope
1. **Native crate:** new `goldencheck-core` kernel modules `regex.rs` (str_match_count / str_filter / str_replace_all) + `date.rs` (str_to_date), exposed as new `_native` pyfunctions in the `goldencheck-native` shim `lib.rs`. Add `regex` + `chrono` as DIRECT Cargo deps (both already resolve transitively in the lock).
2. **Loader:** new `_COMPONENT_SYMBOLS` entries in `core/_native_loader.py` (`regex`, `str_to_date`) each probing their kernel symbol(s), gated by the existing `GOLDENCHECK_NATIVE` env (`0`/`1`/`auto`).
3. **Backend:** extend `PyColumn` (S2.1) with the 5 hard ops:
   - regex + date ops delegate to `_native` when the component is enabled; raise `NativeRequiredError` (a clear, typed error) when not — pure-Python `re`/`strptime` is DELIBERATELY not a fallback (would silently diverge).
   - `value_counts_desc()` is pure Python on `PyColumn` (Counter + the `(count DESC, value ASC)` total order).
   - `PolarsColumn.value_counts_desc()` gains the same secondary sort so the two match.
   - `str_filter`/`str_replace_all`/`str_to_date` return a `PyColumn` (chainable, matching the Polars-backend return contract).
4. **Coverage:** expand `scan_columns` — the 3 mechanical profilers run ALWAYS; the 4 hard-op profilers are appended to the covered scan **iff** their required kernels are enabled (`native_enabled(...)`), and skipped-with-a-`logger` line otherwise. Honest coverage matrix (below).
5. **Parity tests:** per hard-op profiler, assert byte-identical `Finding`s from the native-backed `PyColumn` path vs the `PolarsFrame` path across data hitting each finding branch (the byte-identity gate). Plus a `value_counts_desc` backend-parity test. Existing profiler tests (incl. `pattern_consistency`'s and the 3 `_generalize_series` parity-locked tests) pass UNEDITED.
6. **nopolars lane / import-blocker:** with polars unimportable + native present, the hard-op profilers run and produce findings (`"polars" not in sys.modules`).

### Explicitly NOT in scope
Wiring `PyFrame` into the main `scan_dataframe` (its Polars path is UNCHANGED); the other hard seam ops unused by these 4 profilers (`min`/`max`/`mean`/`std`/`diff`/`cast`/`dtype`/etc. — those serve already-ported relation profilers via the Polars accelerator and are out of the covered-column scan); a `dtype` op on `PyColumn` (pattern_consistency/temporal read `col.dtype` — see Open Question D); the non-Polars reader; the P4 deps-flip; any perf claim (kernels are for byte-identical polars-free COVERAGE, not speed).

### Success criteria
- The 4 hard-op profilers produce **byte-identical** Findings on the native-backed `PyColumn` path vs `PolarsFrame` (the parity gate), across data exercising every finding branch.
- With polars genuinely absent + native present, the 4 hard-op profilers run via `scan_columns` and load zero Polars.
- `value_counts_desc` is deterministic and identical on both backends; existing `pattern_consistency` tests pass unedited.
- The existing full suite is green; `import goldencheck` still loads zero Polars; `scan_dataframe` (the Polars path) is byte-identical (unchanged).

## Coverage matrix (the honest statement)

| Environment | Mechanical 3 (nullability/uniqueness/cardinality) | Hard 4 (encoding/format/pattern/temporal) |
|---|---|---|
| polars present (normal) | run (Polars or PyColumn — both byte-identical) | run via Polars (unchanged) |
| polars ABSENT + native present | run via PyColumn | **run via native-backed PyColumn (S2.2)** |
| polars ABSENT + native ABSENT | run via PyColumn | **skipped-with-log** (native is required for byte-identity) |

`scan_columns` never silently under-reports: when it skips the hard 4 (native absent), it emits a `logger.info` naming the skipped checks (mirrors the S2.0 "no silent caps" discipline).

## Native crate design (`goldencheck-core` + `goldencheck-native`)

Current `goldencheck-native` symbol surface (from `lib.rs`): `benford_leading_digits`, `composite_key_search`, `functional_dependency_holds`, `discover_functional_dependencies`, `discover_approximate_fds`, `fd_violation_rows`, `near_duplicate_value_clusters`. Deps: `pyo3`, `arrow` only.

New kernels (take a Python `list[str | None]`; pyo3 handles list↔Vec):
- `gc_str_contains_count(values, pattern) -> int` — count of non-null values matching `regex::Regex::new(pattern)` (mirrors `s.str.contains(pattern).sum()`; nulls do not match).
- `gc_str_filter_mask(values, pattern) -> list[bool]` — per-element match mask (the seam builds the filtered `PyColumn` from the mask, keeping `matching`/`~matching` logic in Python and preserving null handling exactly like `s.filter`). *(Returning a mask, not the filtered list, keeps the Rust surface minimal and lets the seam handle the `matching` flag + null semantics.)*
- `gc_str_replace_all(values, pattern, replacement) -> list[str | None]` — element-wise `Regex::replace_all` (nulls pass through as null; mirrors `s.str.replace_all`).
- `gc_str_to_date(values, fmt) -> list[str | None]` — `chrono::NaiveDate::parse_from_str(v, fmt)`; parse failure → null (matches `strict=False`); success → ISO `%Y-%m-%d` string (the seam wraps into a date-typed `PyColumn`; temporal only needs ordering/non-null on the result — see Open Question D). Nulls pass through.

Registration: add the 4 `m.add_function(...)` lines to `lib.rs`. Cargo: add `regex = "1"` (pin the minor to match Polars 1.40.1's bundled major where feasible — see Risks) and `chrono = { version = "0.4", default-features = false, features = ["std"] }`.

**Regex flag parity:** the seam passes the raw pattern string straight through to `regex::Regex::new` with NO extra flags — Polars' `s.str.contains`/`replace_all` default to the same (Unicode-on, case-sensitive, no inline-flag injection). Any pattern the profilers use (`\p{L}`, `\d`, format/encoding character classes) compiles under the same syntax on both sides.

## Backend design (`core/frame.py`)

`PyColumn` gains (all guarded/typed):
```python
def str_match_count(self, pattern: str) -> int:
    return _regex_kernel().gc_str_contains_count(self._v, pattern)  # raises NativeRequiredError if disabled

def str_filter(self, pattern: str, *, matching: bool) -> PyColumn:
    mask = _regex_kernel().gc_str_filter_mask(self._v, pattern)
    return PyColumn([v for v, m in zip(self._v, mask) if m == matching])

def str_replace_all(self, pattern: str, value: str) -> PyColumn:
    return PyColumn(_regex_kernel().gc_str_replace_all(self._v, pattern, value))

def str_to_date(self, fmt: str, *, strict: bool) -> PyColumn:
    return PyColumn(_date_kernel().gc_str_to_date(self._v, fmt))   # strict handled below

def value_counts_desc(self) -> list[tuple[Any, int]]:
    counts = Counter(self._v)                       # engine-agnostic exact counts
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))   # (count DESC, value ASC)
```
- `_regex_kernel()`/`_date_kernel()` consult the loader (`native_enabled("regex")` / `"str_to_date"`); if disabled, raise `NativeRequiredError` with a message pointing at `pip install goldencheck[native]`.
- `str_filter`'s `matching`/`~matching` + null handling stay in Python (mask-driven), exactly reproducing `s.filter(mask if matching else ~mask)` — nulls in `s` produce null in the contains-mask → excluded by both `matching` and `~matching`, matching Polars.
- `str_to_date`'s `strict=False` is the only mode used; the kernel maps parse-failure to null (strict=True is unused — assert-or-`NotImplementedError` if ever passed True, YAGNI).
- `PolarsColumn.value_counts_desc()` becomes: `vc = s.value_counts(); ... ; sorted(pairs, key=lambda kv: (-kv[1], kv[0]))` (same total order) so the two backends match by construction.

`NativeRequiredError` is a new typed exception in `core/frame.py` (or `core/_native_loader.py`).

## `scan_columns` coverage expansion (`engine/scanner.py`)

```python
_MECHANICAL_PROFILERS = [NullabilityProfiler(), UniquenessProfiler(), CardinalityProfiler()]
_HARD_PROFILERS = [EncodingDetectionProfiler(), FormatDetectionProfiler(),
                   PatternConsistencyProfiler(), TemporalOrderProfiler()]  # names TBC at plan time

def scan_columns(columns: dict[str, list]) -> list[Finding]:
    frame = PyFrame.from_columns(columns)
    profilers = list(_MECHANICAL_PROFILERS)
    if native_enabled("regex") and native_enabled("str_to_date"):
        profilers += _HARD_PROFILERS
    else:
        logger.info("scan_columns: native kernels unavailable; skipping hard-op checks "
                    "(encoding/format/pattern_consistency/temporal). pip install goldencheck[native].")
    findings = []
    for name in columns:
        for profiler in profilers:
            findings.extend(profiler.profile(frame, name))
    return findings
```
- Column-vs-relation shape: the mechanical + encoding/format/pattern profilers are `profile(frame, column, *, context=None)`; `temporal` is a `profile(frame)` relation profiler (Open Question C — how/whether it slots into the per-column loop, or runs once over the frame). Resolve at plan time.
- Gating on BOTH `regex` and `str_to_date` is coarse; a finer split (run encoding/format/pattern when `regex` present even if `str_to_date` absent) is a plan-time refinement if cheap; default to the simple all-or-nothing hard-4 gate.

## Testing

### Byte-parity gate (polars PRESENT + native PRESENT)
`tests/engine/test_scan_columns_hardops_parity.py` (new). For representative data per profiler, assert `profiler.profile(PolarsFrame(pl.DataFrame(d)), col) == profiler.profile(PyFrame.from_columns(d), col)` — identical Findings. Cover each finding branch (each encoding/format check; pattern_consistency dominant + minority + WARNING/<5% + top-5 cutoff; temporal ordered/unordered/unparseable). Plus a `value_counts_desc` backend-parity test incl. a tie case (proves deterministic identical order). `Finding` is a plain `@dataclass` → compare with `==`.

### Existing tests unedited (the regression gate)
`pattern_consistency`, `encoding_detection`, `format_detection`, `temporal` existing test files + the 3 `_generalize_series` parity tests pass with ZERO edits. If `value_counts_desc`'s new total order changes a `pattern_consistency` assertion, that test was relying on nondeterministic tie-order → investigate (do NOT loosen).

### nopolars lane + import-blocker (native PRESENT)
Extend `tests/nopolars/test_polars_absent.py` (skipif-when-polars-present) with a hard-op covered scan asserting the 4 checks fire polars-free (guarded by native availability). Extend `tests/test_import_no_polars.py` with a subprocess proving `scan_columns` runs the hard-op checks with polars UNIMPORTABLE + native present + `"polars" not in sys.modules`. (Whether the advisory `goldencheck_nopolars` CI lane also builds/installs native is a plan-time wiring decision — the required-suite import-blocker + the parity gate already prove correctness locally.)

## Byte-identity anchors / risks

- **regex-crate version drift** — Polars 1.40.1's bundled `regex` vs our pinned `regex` may differ on newly-assigned Unicode codepoints for `\p{L}`/`\d`. Pin `regex` to a version compatible with Polars' major; the parity corpus catches drift on tested characters. Low risk for realistic column data; the profilers already document a `\p{Nd}` corner that "doesn't appear in production column data."
- **chrono vs Polars date parsing** — Polars `.str.to_date` is chrono under the hood; using chrono in the kernel guarantees the same engine. The kernel must use the SAME `fmt` (`%Y-%m-%d`) and the same failure→null semantics (`strict=False`). Verify: a corpus of valid, malformed (bad month/day, non-padded, trailing chars, empty) strings parses identically on both.
- **value_counts total order touches `PolarsColumn`** — the safety check is that existing `pattern_consistency` tests pass unedited; if one ties, handle at plan time.
- **`_generalize_series` untouched** — its 3 parity tests call raw Polars (not the seam), so they stay green; S2.2 does not modify it. The profiler hot path uses the seam `str_replace_all`, which the native kernel matches.
- **`scan_dataframe` unchanged** — S2.2 touches only `scan_columns` + the backend + the native crate; the Polars scan path is untouched (the only behavioral change on the Polars side is `value_counts_desc`'s deterministic secondary sort, gated by the unedited-tests check).
- **Native build for tests** — the parity gate needs `goldencheck._native` built in-tree (loader discover order). Plan documents the build step; CI parity runs where native is buildable.

## Open questions (resolve at plan time)
- **A. `str_filter` return shape** — mask-from-kernel + Python filter (chosen) vs kernel-returns-filtered-list. Mask keeps the Rust surface minimal and null/`matching` semantics in Python; confirm it exactly reproduces `s.filter`.
- **B. `regex` pin** — exact version to match Polars 1.40.1's bundled major (inspect Polars' lock / the transitively-resolved `regex 1.12.3`).
- **C. `temporal` in `scan_columns`** — it's a `profile(frame)` relation profiler, not per-column; how it slots into the covered scan (run once vs per-column) — or whether temporal is covered via a separate call. Possibly narrow S2.2's `scan_columns` expansion to the 3 per-column hard profilers (encoding/format/pattern) and cover temporal's `str_to_date` only at the backend+parity level, deferring its scan wiring.
- **D. `col.dtype` on `PyColumn`** — pattern_consistency and temporal early-return on `col.dtype != "str"` / dtype checks. `PyColumn` has no `dtype` (S2.1 excluded it). Either add a minimal inferred `dtype` to `PyColumn` (all-str/all-None → "str"; else best-effort) sufficient for these profilers' gates, or have `scan_columns` infer per-column dtype. Must stay byte-identical to how the profilers branch on Polars dtype. This is the subtlest wiring point — resolve carefully at plan time; it may warrant its own task.

## Non-goals (YAGNI)
`strict=True` date parsing; regex flags beyond Polars' defaults; kernels for hard ops unused by these 4 profilers; `scan_dataframe` wiring; a NaN-aware anything; perf tuning; the reader; the deps-flip.
