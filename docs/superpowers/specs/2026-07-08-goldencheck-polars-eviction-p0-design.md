# GoldenCheck Polars eviction — Stage 1 (P0: lazy-import linchpin + Frame/Column seam)

Date: 2026-07-08
Status: design — approved in brainstorming, pending spec review
Base: fresh `origin/main` (goldencheck 1.4.1, denial-constraints #1601/#1602 merged)
Parent program: "Evict Polars as a hard dependency from goldencheck" (5 stages; this is P0)

## Context

`goldencheck` is Polars-native: `polars>=1.0` is a **hard** dependency
(`packages/python/goldencheck/pyproject.toml`), **49** modules `import polars`, and every
profiler's `BaseProfiler.profile(self, df: pl.DataFrame, column, *, context)` operates on a
`pl.DataFrame`. The sibling `goldenflow` already evicted Polars behind a lazy-import proxy + a
`Frame` seam (`polars` → optional `[polars]` extra, ~185 MB installed weight removed, native
Arrow substrate). The suite-wide direction (`docs/design/2026-07-01-rust-is-the-reference-roadmap.md`)
lists check as a follower. This program brings the same eviction to goldencheck.

**The driver is WEIGHT, not speed** (goldenflow measured ~185 MB; the eviction lets `import
goldencheck` and the base install run without Polars). Polars stays as an optional accelerator.

### What makes goldencheck a NARROWER eviction than goldenflow
goldencheck's Polars use is overwhelmingly **eager per-column scalar reductions** (`null_count`,
`n_unique`, `value_counts`, `cast`, `drop_nulls`) — **no lazy frames, no real joins
(the 41 `.join(` hits are Python `str.join`), exactly one `.pivot()`** (`baseline/correlation.py`).
And goldencheck **starts further along**: the native `goldencheck-core` crate, the
`core/_native_loader.py` gate, and Polars-free `core/kernels.py` (plain-`Sequence`-typed) already
exist — goldenflow had to build those.

## The program (each stage its own spec → plan → build)

Mirrors goldenflow's proven arc (seam → lazy-import → incremental ports → nopolars lane → flip):

| Stage | Scope | goldenflow ref |
|---|---|---|
| **P0 (this spec)** | Lazy-import linchpin (`_polars_lazy.py` + 49-site sweep) **+** the `Frame`/`Column` seam (PolarsColumn backend only) routed through `scanner.py` + `BaseProfiler.profile`, with 3 profilers ported. Byte-identical, non-breaking. | #1525 + #1552 |
| P1 | Reader Polars-free path (`engine/reader.py`: pyarrow CSV/Parquet + stdlib tail) | Phase 2 |
| P2 | Incremental profiler ports + **decline-to-Polars** contract for the gnarly tail (`correlation.py` pivot, FD-mining `group_by`) | #1554-#1567 |
| P3 | `nopolars` CI lane (uninstall polars, assert absent, run a Polars-free test dir) | #1568 |
| P4 | The flip — `polars` → `[polars]` extra, deps-only, major version | #1586 |

### The deferred decision (Stage 2, not P0)
The **default substrate** that replaces Polars for ported reductions — pyarrow-backed `Column`
(pyarrow is already a dep; easiest), a native `goldencheck-core` reduction, or a pure-Python
`dict[str,list]` fallback (goldenflow's "correctness floor", ~3.3x slower). P0's seam is
substrate-agnostic (ships only `PolarsColumn`), so the choice is made when P2 ports the runtime.

## P0 scope

### In scope
1. `goldencheck/_polars_lazy.py` — a `_LazyPolars` proxy (port goldenflow's verbatim) that imports
   Polars only on first attribute access; sweep all 49 `import polars as pl` → `from
   goldencheck._polars_lazy import pl`, and **defer every module-level `pl.` reference**.
2. `goldencheck/core/frame.py` — `Frame` + `Column` Protocols + a `PolarsFrame`/`PolarsColumn`
   backend + `to_frame()` factory.
3. Route `scanner.py` + the profilers through `Frame` instead of raw `pl.DataFrame` — two passes:
   the 13 `BaseProfiler` column profilers (`profile(df, column, *, context)`) and the 9
   relation profilers (`profile(df)`, don't inherit `BaseProfiler`).
4. Port 3 profilers (`nullability`, `cardinality`, `uniqueness`) onto the seam (byte-identical).
5. An import-graph subprocess gate test.

### Explicitly NOT in P0
Reader eviction (P1); porting the other ~10 profilers + the pivot/FD-`group_by` tail (P2); the
full `nopolars` CI lane (P3); the deps flip (P4); choosing the non-Polars substrate (Stage 2).
No behavior change, no version bump.

### Success criteria
- `python -c "import goldencheck, sys; assert 'polars' not in sys.modules"` passes.
- The 3 ported profilers emit byte-identical `Finding`s (their existing tests pass with **zero**
  edits).
- Full existing test suite green (unported profilers/relations run via `frame.native`).

## Component 1 — The lazy-import linchpin (`_polars_lazy.py`)

Copy goldenflow's `packages/python/goldenflow/goldenflow/_polars_lazy.py` (`_LazyPolars` proxy,
imports polars on first attribute access) into `goldencheck/_polars_lazy.py`. Sweep all 49
`import polars as pl` sites to `from goldencheck._polars_lazy import pl`.

**The real work + top risk — module-level Polars references.** Function-body `pl.DataFrame(...)`
is lazy for free (the function isn't run at import). But anything evaluated at *import* time
triggers the proxy's import and defeats the linchpin. An audit of the ~90 `pl.<dtype>` references
found the vast majority are inside function/method bodies (lazy-safe); the actual import-time
killers are exactly **7 module-level dtype-tuple constants** (verified — no module-level
`pl.col(...)` constant, no default-arg dtypes):
- `profilers/sequence_detection.py:9` `INTEGER_DTYPES = (pl.Int8, …)`
- `profilers/range_distribution.py:9-12`
- `profilers/drift_detection.py:9-12`
- `relations/numeric_cross.py:8-11` `NUMERIC_DTYPES`
- `relations/composite_key.py:36-41`
- `relations/approx_fd.py:33-37`
- `relations/functional_dependency.py:34-39` `_SUPPORTED`

The sweep is therefore genuinely mechanical: the 49 import-line rewrites + deferring these **7**
constants (into a function or a lazily-built cache; these live in *unported* modules, so they're
part of the lazy-import sweep, independent of the 3-profiler port). The subprocess import-graph
gate proves none were missed.

**Precondition to assert (goldenflow's proxy relies on it):** every swept module must have `from
__future__ import annotations` (so `-> pl.Expr` annotations stay strings) and no `def f(x=pl.X)`
default-arg dtypes. Both already hold across goldencheck (audited: zero default-arg dtypes) — the
plan asserts it rather than assuming it.

## Component 2 — The `Frame`/`Column` seam (`goldencheck/core/frame.py`)

Two minimal Protocols (mirroring goldenflow's `engine/frame.py`, plus a `Column` accessor
goldencheck needs for its scalar reductions):

The `Column` surface is exactly what the 3 P0 profilers use — verified against
`nullability.py` (`len`, `null_count`), `uniqueness.py` (`len`, `drop_nulls`, `n_unique`), and
`cardinality.py` (`len`, `n_unique`, and the chain `drop_nulls().unique().sort().to_list()`). So
P0's `Column` is `{len, null_count, n_unique, drop_nulls, unique, sort, to_list}` plus `dtype` —
**not** `value_counts`/`is_in` (no P0 profiler uses those; they arrive when later stages need them):

```python
class Column(Protocol):
    @property
    def dtype(self) -> str: ...          # backend-neutral: "str"|"int"|"float"|"bool"|"date"
    def len(self) -> int: ...
    def null_count(self) -> int: ...
    def n_unique(self) -> int: ...
    def drop_nulls(self) -> "Column": ...
    def unique(self) -> "Column": ...    # cardinality: drop_nulls().unique().sort().to_list()
    def sort(self) -> "Column": ...
    def to_list(self) -> list: ...

class Frame(Protocol):
    @property
    def columns(self) -> list[str]: ...
    @property
    def height(self) -> int: ...
    @property
    def native(self): ...                # escape hatch for not-yet-ported code
    def dtype(self, name: str) -> str: ...
    def column(self, name: str) -> Column: ...
```

Ship exactly one backend: `PolarsFrame(pl.DataFrame)` / `PolarsColumn(pl.Series)`, each method
delegating to the identical Polars call (so parity is by construction). `to_frame(native) ->
Frame` accepts a `pl.DataFrame` (later Arrow). **Keep the `Column` surface minimal** — only the ops
the 3 ported profilers use; grow it as later stages port more. Backend-neutral `dtype` strings map
`pl.Utf8 -> "str"`, `pl.Int* -> "int"`, `pl.Float* -> "float"`, `pl.Boolean -> "bool"`,
`pl.Date/Datetime -> "date"` (the exact mapping is defined here so ported profilers compare against
strings, not `pl.` types — which also removes their module-level `pl.` dtype references).

## Component 3 — Route `scanner.py` + the profilers through the seam (TWO signatures)

goldencheck has **two distinct `profile()` shapes** — the routing is two mechanical passes, not
one:
- **13 column profilers** inherit `BaseProfiler` (`profilers/base.py`): `profile(self, df:
  pl.DataFrame, column: str, *, context: dict | None = None)`. Change `df: pl.DataFrame` → `frame:
  Frame`. 10 unported ones get a one-line `df = frame.native` at the top, body untouched.
- **9 relation profilers** (`relations/*.py`) do **NOT** inherit `BaseProfiler` and use a different
  signature `profile(self, df: pl.DataFrame)` — whole frame, **no `column`, no `context`** (e.g.
  `functional_dependency.py`). Change `df` → `frame`, add `df = frame.native` — all 9 stay on
  `.native` in P0 (they're the FD-mining/pivot tail, ported or declined in P2).

`scanner.py` (`sample: pl.DataFrame` at ~line 84) wraps the sample once via `to_frame(sample)` and
passes the `Frame` to both fan-outs (`COLUMN_PROFILERS` ~44-60 and `RELATION_PROFILERS` ~62-81).
The seam threads through the whole scan immediately; bodies migrate one stage at a time.

## Component 4 — Port `nullability`, `cardinality`, `uniqueness`

These three do **no dtype comparisons** and have **no module-level `pl.` references** — they use
only the `Column` reductions above (`nullability`: `len`+`null_count`; `uniqueness`:
`len`+`drop_nulls`+`n_unique`; `cardinality`: `len`+`n_unique`+`drop_nulls().unique().sort()
.to_list()`). Port each body from `df[col]…` → `frame.column(col)…`. Because `PolarsColumn`
delegates to the same Polars calls (and its chained methods return `PolarsColumn` wrappers that
also delegate), the ports are **byte-identical by construction** — the existing tests for these
three profilers pass with zero edits (the parity gate).

(The backend-neutral `dtype`-string mapping defined in Component 2 is **infrastructure for later
ports** — `type_inference`, `format_detection`, `sequence_detection`, etc. compare against `pl.`
dtypes — not something the 3 P0 profilers use. The neutral collapse `int`/`date` is safe for P0
precisely because the profilers needing Int32-vs-Float64 or Date-vs-Datetime distinctions aren't
ported yet and reach through `.native`.)

## Testing

- **Import-graph gate:** a subprocess test asserting `'polars' not in sys.modules` after `import
  goldencheck` (the linchpin proof).
- **Parity:** the 3 ported profilers' existing tests pass unedited (byte-identical Findings).
- **Seam unit tests:** each `PolarsColumn`/`PolarsFrame` op returns the same value as the raw Polars
  call it wraps (null_count/n_unique/value_counts/drop_nulls/is_in/dtype-mapping).
- **Regression:** the full existing goldencheck suite green (unported profilers/relations via
  `frame.native`).

## Risks

- **Module-level `pl.` references defeating the linchpin** (top risk) — mitigated by the
  grep-for-module-level-`pl.` sweep + the subprocess import-graph gate.
- **The signature change spans 22 `profile()` methods across TWO shapes** (13 `BaseProfiler`
  column profilers `profile(df, column, *, context)` + 9 relation profilers `profile(df)`) — broad
  but mechanical (one line per unported method); parity tests guard against behavior drift. Treat
  them as two passes.
- **Seam API incompleteness / over-design** — start minimal (only what the 3 profilers + scanner
  need); grow the `Column` Protocol as later stages port more. Do NOT model the pivot/`group_by`
  tail in P0 (those decline to `.native`/Polars until P2).
- **A relation profiler or engine module with a module-level Polars dtype constant** that the sweep
  misses — the import gate catches it; fix by deferral.

## Non-goals (YAGNI)
Reader eviction; the other ~10 profiler ports; the pivot/FD-`group_by` decline-tail; the substrate
choice; the `nopolars` CI lane; the deps flip. All later stages.
