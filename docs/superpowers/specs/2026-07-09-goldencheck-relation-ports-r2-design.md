# GoldenCheck Polars eviction — Relation ports R2 (composite_key + functional_dependency + approx_fd)

Date: 2026-07-09
Status: design — approved in brainstorming, pending spec review
Base: fresh `origin/main` (through R1 #1612; the seam has the full column-profiler surface + `dtype_repr` + `pl.Boolean → "bool"`)
Parent program: goldencheck Polars eviction — relation front (see `2026-07-09-goldencheck-relation-ports-r1-design.md` for the R1–R4 roadmap)

## Context

R2 of the relation front: the three **native-kernel + Polars-fallback** relation profilers —
`composite_key`, `functional_dependency`, `approx_fd` — plus the `functional_dependencies.py` bridge.
Each discovers cross-column structure (composite keys, strict FDs, near-FD violations) via a Rust
kernel (fed pyarrow arrays) with a pure-Python correctness fallback.

**Nature (discovered from the source + tests):** these three cannot be *fully* seam-decoupled. Their
fast path is already Arrow/Rust (not the Polars engine), and their Python-fallback helpers plus the
candidate selector are **parity-gate-locked to a raw `pl.DataFrame`**: `test_native_parity.py` calls
`_select_candidates(df, …)`, `_python_search(df, …)`, `_discover_python(df, …)`, and `_intern(list)`
directly (unedited), and `core/kernels.py` calls the same helpers by building a `pl.DataFrame` from
lists. So those helper signatures are fixed.

**Chosen approach (partial decouple):** add two Column ops (`to_arrow`, `get`) and route the native
fast-path Arrow export + the sample scalar-access through the seam (backend-neutral, substrate-ready);
reach the parity-locked helpers through the sanctioned `frame.native` escape hatch. Same "Polars as
accelerator" model; byte-identical, no version bump.

## Scope

### In scope
Port the `profile()` methods (and the internal `_select_candidates`/`_has_single_column_key` bodies)
of `composite_key`, `functional_dependency`, `approx_fd` onto the seam + `frame.native` escape hatch,
adding **2** `Column` methods (`to_arrow`, `get`). Make each file grep-clean of `polars`/`pl.`. The
`functional_dependencies.py` bridge is a thin delegator whose only direct Polars is `df.height`/
`df.width`/`df[c].to_arrow()`/`df[c].to_list()` — port it too (rides along). Byte-identical.

### Explicitly NOT in scope
Changing any parity-locked helper signature (`_select_candidates`, `_python_search`,
`_discover_python`, `_intern`) — they keep taking a raw `pl.DataFrame`/lists. Editing
`test_native_parity.py` or `core/kernels.py`. R3/R4. The Stage-2 non-Polars backend. The reader. The
deps flip.

### Success criteria
- `composite_key`, `functional_dependency`, `approx_fd` (+ the bridge) are grep-clean of
  `polars`/`_polars_lazy`/`pl.`, routing through the seam + `frame.native`.
- Their existing tests (`tests/relations/test_{composite_key,functional_dependency,approx_fd}.py`,
  `tests/test_functional_dependencies.py`) AND `tests/core/test_native_parity.py` pass **unedited**.
- Full suite green; `import goldencheck` still loads zero Polars.

## The seam additions (`core/frame.py` — `Column`, PolarsColumn delegates)

| Method | Signature | PolarsColumn impl | Used by |
|---|---|---|---|
| `to_arrow` | `to_arrow() -> Any` | `self._s.to_arrow()` | native-kernel input (all 3 + bridge) |
| `get` | `get(index: int) -> Any` | `self._s[index]` | approx_fd sample scalars (`df[det][r]`) |

Neither references the `pl.` symbol (`self._s.*`), so the import gate stays green. `to_arrow` returns a
pyarrow Array (the exact object the native kernels take today); `get(i)` returns the native Python
scalar `self._s[i]` yields.

## The shared port pattern

For each of the three profiler modules:
- Remove `from goldencheck._polars_lazy import pl`; add `from goldencheck.core.frame import _neutral_dtype`
  (keep `to_frame`). Drop the `@lru_cache`-decorated `_supported()` pl-tuple helper AND
  `from functools import lru_cache`; replace with a **module-level neutral-string frozenset**:
  - `composite_key`: `_SUPPORTED = frozenset({"str", "int", "uint", "float", "bool"})` (was Utf8 +
    Int8-64 + UInt8-64 + Float32/64 + Boolean).
  - `functional_dependency` / `approx_fd`: `_SUPPORTED = frozenset({"str", "int", "uint", "bool"})`
    (was Utf8 + Int8-64 + UInt8-64 + Boolean — **no float**).
- `_select_candidates` keeps its raw-df signature (parity-locked) — drop the `pl.DataFrame` annotation;
  change the dtype gate `if series.dtype not in _supported():` → `if _neutral_dtype(series.dtype) not in
  _SUPPORTED:` (`series = df[col]` stays a raw Polars Series — grep-clean, `df.`/`series.` not `pl.`).
  `series.n_unique()` unchanged. **Byte-identical:** every listed width maps to its category
  (`Int8..Int64 → "int"`, `UInt* → "uint"`, `Float32/64 → "float"`, `Utf8/String → "str"`,
  `Boolean → "bool"`) and no *unsupported* dtype collides into a supported category, so membership is
  preserved exactly (incl. the float-excluded fd/approx_fd sets: a Float column → `"float"` ∉ their set,
  matching the original exclusion).
- `_has_single_column_key` (composite_key only) keeps its raw-df signature — drop the `pl.DataFrame`
  annotation; body (`df[col].null_count()`, `.n_unique()`) is already grep-clean, unchanged.
- The Python-fallback helpers (`_python_search`, `_discover_python`, `_intern`, `_group_modes`,
  `_violation_rows`, approx_fd `_python`) keep their signatures — drop any `pl.DataFrame`/`pl.Series`
  annotation; bodies (`df[c].to_list()`, list ops) are grep-clean, unchanged.
- `profile(self, frame)`: drop the `pl.DataFrame` annotation; keep `frame = to_frame(frame)`; keep
  `df = frame.native` (the escape hatch — grep-clean). `n_rows = df.height`; `df.width` →
  `len(frame.columns)`. Pass `df` to `_select_candidates(df, …)` / `_has_single_column_key(df, …)` /
  the fallback helpers (raw-df, parity-locked). **Route the native fast path through the seam:**
  `arrays = [frame.column(c).to_arrow() for c in cols]` (was `df[c].to_arrow()`). The `except → Python
  fallback` and the finding-building loops are UNCHANGED except approx_fd's samples (below).

### Per-profiler specifics
- **composite_key** — `if n_rows < 2 or len(frame.columns) < 2: return []` (was `df.width < 2`).
  Native: `arrays = [frame.column(c).to_arrow() for c in candidates]`; `except:` →
  `_python_search(df, candidates, n_rows, MAX_KEY_SIZE)`. Findings UNCHANGED.
- **functional_dependency** — `if n_rows < _MIN_ROWS or len(frame.columns) < 2: return []`. Native:
  `arrays = [frame.column(c).to_arrow() for c in cols]`; `except:` → `_discover_python(df, cols, n_rows)`.
  Findings UNCHANGED.
- **approx_fd** — `if n_rows < _MIN_ROWS or len(frame.columns) < 2: return []`. Native:
  `arrays = [frame.column(c).to_arrow() for c in cols]`; `fd_violation_rows(arrays[i], arrays[j])`
  uses those same seam-produced arrays; `except:` → `self._python(df, cols, n_rows)`. **Samples through
  the seam:** pull `det_col = frame.column(det)`, `dep_col = frame.column(dep)` once, then
  `samples = [f"{det}={det_col.get(r)!r} has {dep}={dep_col.get(r)!r}" for r in rows]` (was
  `df[det][r]`/`df[dep][r]`). `self._python(self, frame_native, cols, n_rows)` keeps its raw-df param —
  drop its annotation; body `_intern(df[c].to_list())` unchanged.

### The bridge (`functional_dependencies.py`)
Thin delegator into `relations.functional_dependency`/`approx_fd` internals. Its test
(`tests/test_functional_dependencies.py`) imports ONLY the public `functional_dependencies` (verified),
so the private `_strict_pairs`/`_approx_triples` signatures are free to change. Remove
`from goldencheck._polars_lazy import pl`; **add `from goldencheck.core.frame import to_frame`** (the
bridge does NOT currently import it — unlike the three profilers — so it must be added or `to_frame`
NameErrors). Drop all `pl.DataFrame` annotations.
- `functional_dependencies(df, *, min_confidence)`: keep the public param `df` (tests pass a raw
  `pl.DataFrame`); add `frame = to_frame(df)`; `n = frame.height`; `if n < 2 or len(frame.columns) < 2:
  return []` (was `df.height`/`df.width`). Call `_strict_pairs(frame, n)` / `_approx_triples(frame, n,
  min_confidence)`. The det-grouping / confidence-merge / sort logic UNCHANGED.
- `_strict_pairs(frame, n)`: `cols = _fd._select_candidates(frame.native, n)` (raw-df, parity-locked);
  native `native_module().discover_functional_dependencies([frame.column(c).to_arrow() for c in cols])`;
  `except:` → `_fd._discover_python(frame.native, cols, n)`. Return list UNCHANGED.
- `_approx_triples(frame, n, min_conf)`: `cols = _afd._select_candidates(frame.native)` (raw-df,
  parity-locked); native `discover_approximate_fds([frame.column(c).to_arrow() for c in cols], min_conf)`;
  `except:`/else → `_afd._discover_python([_afd._intern(frame.column(c).to_list()) for c in cols], n,
  min_conf)`. Return list UNCHANGED.

## Testing

- **Seam unit tests** (`tests/core/test_frame.py` additions): `to_arrow()` equals `s.to_arrow()`
  (compare `.to_pylist()` or type + values); `get(i)` equals `s[i]` for a few indices (int + string
  columns).
- **Parity gates (unedited):** the three `tests/relations/test_*.py`, `tests/test_functional_dependencies.py`,
  AND `tests/core/test_native_parity.py` (which calls the raw-df helpers directly) all pass with ZERO
  edits.
- **Regression:** full suite green (same counts + the new seam tests); import gate green; the three
  relation files + the bridge grep-clean of `polars`/`_polars_lazy`/`pl.`.

## Risks

- **`_neutral_dtype` membership vs the pl-tuple** — byte-identical: the supported sets are whole neutral
  categories (`str`/`int`/`uint`/`float`/`bool`) and the excluded types (`date`/`datetime`/`other`) never
  collide into them. The float-excluded fd/approx_fd sets stay exact (Float → `"float"` ∉ set). Pinned by
  `test_native_parity`'s `_select_candidates(df, …)` calls, which now exercise the neutral path.
- **Parity-locked helpers reached via `.native`** — `_select_candidates`/`_python_search`/`_discover_python`/
  `_intern` keep raw-df/list signatures; `profile()` hands them `frame.native`. This is honest partial
  decoupling: the fast path is seam-routed (Arrow), the correctness fallback stays Polars-coupled
  (unavoidable — the parity gate locks it). Documented as such.
- **`frame.column(c).to_arrow()` vs `df[c].to_arrow()`** — `frame.column(c)` wraps `df[c]`; `.to_arrow()`
  is the identical Series→Arrow conversion, so the native kernel gets byte-identical input (and
  `test_native_parity` still compares native-vs-python results).
- **`get(r)` repr** — `frame.column(c).get(r)` == `df[c][r]` (same Series indexing → same Python scalar);
  `!r` renders identically.
- **importing a private `_neutral_dtype`** — cross-module import of a private symbol; acceptable within the
  package (R3 will reuse it). Consider promoting to public later — out of scope here.
- **Seam growth** — 2 general primitives (`to_arrow` = Arrow-export escape hatch; `get` = positional
  scalar). No task-shaped op.

## Non-goals (YAGNI)
R3/R4; changing parity-locked signatures; editing `test_native_parity.py`/`kernels.py`; a `Frame.width`
op (use `len(columns)`); promoting `_neutral_dtype` to public; the Stage-2 backend; reader; deps flip.
