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
3. Route `scanner.py` + `BaseProfiler.profile` (all ~13 profilers + relation profilers) through
   `Frame` instead of raw `pl.DataFrame`.
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
triggers the proxy's import and defeats the linchpin — e.g. `_SUPPORTED = (pl.Utf8, pl.Int64, …)`
dtype tuples in the profilers, a `pl.col(...)` in a module constant, a dtype in a default
argument. The sweep must **grep for every `pl.` attribute access outside a `def`/method body** and
defer each: move it into a function, a lazily-built cache (`@lru_cache`/module-level function), or
a backend-neutral dtype string. The subprocess import-graph gate is what proves none were missed.

## Component 2 — The `Frame`/`Column` seam (`goldencheck/core/frame.py`)

Two minimal Protocols (mirroring goldenflow's `engine/frame.py`, plus a `Column` accessor
goldencheck needs for its scalar reductions):

```python
class Column(Protocol):
    @property
    def dtype(self) -> str: ...          # backend-neutral: "str"|"int"|"float"|"bool"|"date"
    def len(self) -> int: ...
    def null_count(self) -> int: ...
    def n_unique(self) -> int: ...
    def drop_nulls(self) -> "Column": ...
    def value_counts(self) -> list[tuple[object, int]]: ...
    def is_in(self, values) -> "Column": ...
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

## Component 3 — Route `scanner.py` + `BaseProfiler` through the seam

`BaseProfiler.profile` signature changes from `df: pl.DataFrame` to `frame: Frame` for **all ~13
profilers + the relation profilers**. For the 10 unported profilers this is a **one-line** change:
`df = frame.native` at the top, body untouched. `scanner.py` wraps its sampled `pl.DataFrame` in a
`PolarsFrame` once (`to_frame(sample)`) and passes the `Frame` everywhere. The seam threads through
the whole scan immediately; bodies migrate one stage at a time. (Relation profilers take the
`Frame` too and use `.native` until P2.)

## Component 4 — Port `nullability`, `cardinality`, `uniqueness`

These use only `null_count`/`n_unique`/`len`/`drop_nulls` (+ dtype checks). Port each body from
`df[col].drop_nulls().n_unique()` → `frame.column(col).drop_nulls().n_unique()`, and dtype checks
from `col.dtype == pl.Utf8` → `frame.dtype(col) == "str"`. Because `PolarsColumn` delegates to the
same Polars calls, the ports are **byte-identical by construction** — the existing tests for these
three profilers pass with zero edits (the parity gate).

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
- **The 13-profiler signature change** — broad but mechanical (one line per unported profiler);
  parity tests guard against behavior drift.
- **Seam API incompleteness / over-design** — start minimal (only what the 3 profilers + scanner
  need); grow the `Column` Protocol as later stages port more. Do NOT model the pivot/`group_by`
  tail in P0 (those decline to `.native`/Polars until P2).
- **A relation profiler or engine module with a module-level Polars dtype constant** that the sweep
  misses — the import gate catches it; fix by deferral.

## Non-goals (YAGNI)
Reader eviction; the other ~10 profiler ports; the pivot/FD-`group_by` decline-tail; the substrate
choice; the `nopolars` CI lane; the deps flip. All later stages.
