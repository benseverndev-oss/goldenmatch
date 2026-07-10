# GoldenMatch Polars Eviction W0 Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land Wave 0 of the Polars eviction: `import goldenmatch` no longer loads Polars, a Frame/Column seam scaffold exists, and the op/dtype audits that size W1-W4 are checked in. Byte-identical behavior, no version bump, existing tests pass unedited.

**Architecture:** A `_LazyPolars` proxy (goldenflow template + a `TYPE_CHECKING` dual-path so pyright keeps real Polars types) replaces all 112 module-level `import polars as pl` sites. Two module-level dtype-set constants in `core/indicators.py` become `lru_cache` functions so nothing evaluates `pl.` at import time. `core/frame.py` ships the Frame/Column Protocol scaffold with a delegating PolarsFrame backend (no call sites port in W0). An AST-based audit script generates the op inventory doc.

**Tech Stack:** Python 3.11+, pytest, ruff, pyright. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-07-09-goldenmatch-polars-eviction-design.md`

**Working directory:** worktree `D:\show_case\goldenmatch\.worktrees\gm-polars-evict`, branch `feat/goldenmatch-polars-eviction-w0` (off fresh origin/main). All paths below are relative to `packages/python/goldenmatch/` unless they start with `docs/` or `scripts/` (repo root).

**Test invocation (worktree + main .venv, Windows):** run from `packages/python/goldenmatch/` inside the worktree:

```bash
cd /d/show_case/goldenmatch/.worktrees/gm-polars-evict/packages/python/goldenmatch
PYTHONPATH="D:\\show_case\\goldenmatch\\.worktrees\\gm-polars-evict\\packages\\python\\goldenmatch" \
POLARS_SKIP_CPU_CHECK=1 GOLDENMATCH_NATIVE=0 \
D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest <target> -v --timeout=120
```

The `PYTHONPATH` prepend makes the worktree package shadow the editable install from the main checkout (PYTHONPATH entries precede site-packages .pth entries). Abbreviated below as `RUNPY -m pytest ...`.

**Verified recon facts (do not re-derive):**
- 112 files carry a column-0 `import polars as pl` line; there are ZERO `from polars import ...` or bare `import polars` module-level variants. Indented (function-local) `import polars as pl` / `as _pl` variants exist and are already lazy -- LEAVE THEM.
- All 112 files already have `from __future__ import annotations` (the proxy's safety precondition; verified 2026-07-09).
- Exactly 2 module-level `pl.` evaluations exist: `core/indicators.py:43` (`_BOOLEAN_DTYPES = {pl.Boolean}`) and `:44` (`_NON_IDENTITY_DTYPES = {pl.Boolean, pl.Date, pl.Datetime, pl.Time}`).
- CI ruff for this package lints E9/F63/F7 only (import-order rules NOT enforced); pyright IS a required gate.

---

### Task 1: `_polars_lazy.py` proxy module

**Files:**
- Create: `goldenmatch/_polars_lazy.py`
- Test: `tests/test_polars_lazy.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_polars_lazy.py
"""The lazy-Polars proxy: real attributes, cached module, no import at module load."""
from __future__ import annotations


def test_proxy_returns_real_polars_attributes():
    from goldenmatch._polars_lazy import pl
    import polars as real_pl

    assert pl.DataFrame is real_pl.DataFrame
    assert pl.Utf8 is real_pl.Utf8


def test_proxy_caches_module():
    from goldenmatch._polars_lazy import _LazyPolars

    proxy = _LazyPolars()
    assert proxy._mod is None
    _ = proxy.DataFrame
    assert proxy._mod is not None
    mod_after_first = proxy._mod
    _ = proxy.Series
    assert proxy._mod is mod_after_first


def test_isinstance_works_through_proxy():
    from goldenmatch._polars_lazy import pl

    df = pl.DataFrame({"a": [1, 2]})
    assert isinstance(df, pl.DataFrame)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `RUNPY -m pytest tests/test_polars_lazy.py -v`
Expected: FAIL / collection error with `ModuleNotFoundError: No module named 'goldenmatch._polars_lazy'`

- [ ] **Step 3: Write the module**

Adapted from `packages/python/goldenflow/goldenflow/_polars_lazy.py` (the proven template) with one deliberate addition: a `TYPE_CHECKING` dual-path so pyright resolves `pl` to the REAL polars module. goldenmatch's core (e.g. `pipeline.py`) depends on real Polars types for narrowing (`assert isinstance(x, pl.DataFrame)`); the plain proxy would silently degrade all of that to `Any`.

```python
# goldenmatch/_polars_lazy.py
"""Lazy Polars proxy -- W0 of the Polars eviction (spec:
docs/superpowers/specs/2026-07-09-goldenmatch-polars-eviction-design.md).

``from goldenmatch._polars_lazy import pl`` gives a stand-in that imports Polars
on the FIRST attribute access, not at import time. Every runtime ``pl.`` use in
the swept modules keeps working unchanged, but ``import goldenmatch`` no longer
eagerly imports Polars.

Safe because (audited 2026-07-09):
- All swept modules have ``from __future__ import annotations`` (string
  annotations never trigger the import).
- No module-level ``pl.`` execution remains (the two dtype-set constants in
  core/indicators.py were converted to lru_cache functions in this wave) and no
  ``def f(x=pl.X)`` default arg exists.
- Attribute access returns the REAL Polars object, so ``isinstance(x,
  pl.DataFrame)`` and dtype identity behave identically to ``import polars``.

The ``TYPE_CHECKING`` branch makes pyright treat ``pl`` as the real module, so
static narrowing across the package is unchanged (a deliberate divergence from
the goldenflow/goldencheck template, which exposes ``Any``).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any


class _LazyPolars:
    """Forwards attribute access to ``polars``, importing it on first use."""

    __slots__ = ("_mod",)

    def __init__(self) -> None:
        self._mod: Any = None

    def __getattr__(self, name: str) -> Any:
        # `_mod` is a slot (set in __init__), so reading it never re-enters
        # __getattr__; only genuine polars attributes reach this path.
        mod = self._mod
        if mod is None:
            import polars as _polars

            self._mod = mod = _polars
        return getattr(mod, name)


if TYPE_CHECKING:
    import polars as pl
else:
    pl = _LazyPolars()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `RUNPY -m pytest tests/test_polars_lazy.py -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldenmatch/goldenmatch/_polars_lazy.py packages/python/goldenmatch/tests/test_polars_lazy.py
git commit -m "feat(goldenmatch): lazy-Polars proxy module (eviction W0)"
```

---

### Task 2: Defer the two module-level dtype constants

**Files:**
- Modify: `goldenmatch/core/indicators.py:43-44` (+ every usage of the two names)
- Test: `tests/test_indicators_dtype_defer.py`

- [ ] **Step 1: Find every usage of the two constants**

Run: `grep -rn "_BOOLEAN_DTYPES\|_NON_IDENTITY_DTYPES" packages/python/goldenmatch/goldenmatch/ packages/python/goldenmatch/tests/`
Record every hit; all call sites change from `X` to `X()` in Step 3.

- [ ] **Step 2: Write the failing test**

```python
# tests/test_indicators_dtype_defer.py
"""The dtype-set constants must be lazy functions, not module-level pl. evaluations."""
from __future__ import annotations


def test_dtype_sets_are_functions_returning_expected_members():
    import polars as pl

    from goldenmatch.core.indicators import _boolean_dtypes, _non_identity_dtypes

    assert _boolean_dtypes() == {pl.Boolean}
    assert _non_identity_dtypes() == {pl.Boolean, pl.Date, pl.Datetime, pl.Time}
    # lru_cache: same object back on the second call
    assert _boolean_dtypes() is _boolean_dtypes()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `RUNPY -m pytest tests/test_indicators_dtype_defer.py -v`
Expected: FAIL with `ImportError: cannot import name '_boolean_dtypes'`

- [ ] **Step 4: Convert the constants (the goldencheck lru_cache pattern)**

In `goldenmatch/core/indicators.py`, replace lines 43-44:

```python
# BEFORE
_BOOLEAN_DTYPES = {pl.Boolean}
_NON_IDENTITY_DTYPES = {pl.Boolean, pl.Date, pl.Datetime, pl.Time}

# AFTER (add `from functools import lru_cache` to the imports)
@lru_cache(maxsize=1)
def _boolean_dtypes() -> set:
    """Deferred: evaluating pl.Boolean at module level would defeat _polars_lazy."""
    return {pl.Boolean}


@lru_cache(maxsize=1)
def _non_identity_dtypes() -> set:
    """Deferred: see _boolean_dtypes."""
    return {pl.Boolean, pl.Date, pl.Datetime, pl.Time}
```

Update every usage found in Step 1 from `_BOOLEAN_DTYPES` -> `_boolean_dtypes()` and `_NON_IDENTITY_DTYPES` -> `_non_identity_dtypes()`.

- [ ] **Step 5: Run the new test + the indicators tests**

Run: `RUNPY -m pytest tests/test_indicators_dtype_defer.py tests/test_indicators.py -v`
Expected: all PASS (if `tests/test_indicators.py` does not exist, run `RUNPY -m pytest tests/ -k indicator -v` instead)

- [ ] **Step 6: Commit**

```bash
git add packages/python/goldenmatch/goldenmatch/core/indicators.py packages/python/goldenmatch/tests/test_indicators_dtype_defer.py
git commit -m "refactor(goldenmatch): defer module-level dtype-set constants (eviction W0)"
```

---

### Task 3: The 112-file import sweep + the import gate

**Files:**
- Modify: all 112 files with a column-0 `import polars as pl` line
- Test: `tests/test_lazy_import_gate.py`

- [ ] **Step 1: Write the failing gate test**

```python
# tests/test_lazy_import_gate.py
"""THE W0 gate: `import goldenmatch` must not load Polars.

Subprocess-based so this test is immune to other tests having already imported
polars into this process.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys


def test_import_goldenmatch_does_not_load_polars():
    code = (
        "import json, sys\n"
        "import goldenmatch\n"
        "print(json.dumps({'polars_loaded': 'polars' in sys.modules,"
        " 'goldenmatch_file': goldenmatch.__file__}))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env={**os.environ},
        check=True,
    )
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["polars_loaded"] is False, (
        f"polars was imported eagerly by `import goldenmatch` "
        f"(package at {payload['goldenmatch_file']}). Run "
        f"`python -X importtime -c 'import goldenmatch' 2>&1 | grep polars` "
        f"to find the offender."
    )


def test_polars_still_works_after_lazy_import():
    """The proxy must not break real use: first pl. access imports polars fine."""
    code = (
        "import sys\n"
        "import goldenmatch\n"
        "from goldenmatch._polars_lazy import pl\n"
        "df = pl.DataFrame({'a': [1]})\n"
        "assert 'polars' in sys.modules\n"
        "assert df.height == 1\n"
        "print('OK')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, env={**os.environ}, check=True,
    )
    assert proc.stdout.strip().endswith("OK")
```

- [ ] **Step 2: Run gate test to verify it fails (pre-sweep baseline)**

Run: `RUNPY -m pytest tests/test_lazy_import_gate.py::test_import_goldenmatch_does_not_load_polars -v`
Expected: FAIL with `polars was imported eagerly` (the 112 top-level imports are still in place)

- [ ] **Step 3: Execute the mechanical sweep**

From the worktree root:

```bash
cd /d/show_case/goldenmatch/.worktrees/gm-polars-evict/packages/python/goldenmatch/goldenmatch
grep -rlE "^import polars as pl$" --include=*.py . | while read -r f; do
  sed -i 's/^import polars as pl$/from goldenmatch._polars_lazy import pl/' "$f"
done
```

Then verify the sweep's completeness invariant -- ZERO column-0 polars imports remain anywhere in the package (the proxy module itself only has an indented, function-local import plus the indented TYPE_CHECKING one):

```bash
grep -rnE "^import polars|^from polars" --include=*.py . ; echo "exit=$?"
```

Expected: no output, `exit=1`.

- [ ] **Step 4: Run the gate test**

Run: `RUNPY -m pytest tests/test_lazy_import_gate.py -v`
Expected: 2 PASS. If `test_import_goldenmatch_does_not_load_polars` still fails, an import-time evaluation remains somewhere. Diagnose with:

```bash
PYTHONPATH="D:\\show_case\\goldenmatch\\.worktrees\\gm-polars-evict\\packages\\python\\goldenmatch" \
D:/show_case/goldenmatch/.venv/Scripts/python.exe -X importtime -c "import goldenmatch" 2>&1 | grep -B5 polars | head -30
```

Known candidate classes to check if this fires (recon found none, but the gate is the authority): (a) a Typer-decorated CLI function with a `pl.`-annotated parameter or return type (Typer resolves annotations at decoration time -- fix: change the annotation to a string the module never resolves, or restructure); (b) a Pydantic model field annotated with a `pl.` type (Pydantic evaluates annotations at class creation -- fix: `Any` + isinstance check); (c) a decorator argument or class-body default evaluating `pl.` at import.

- [ ] **Step 5: Run the broad regression batches (targeted local; full suite is CI's job)**

Run: `RUNPY -m pytest tests/test_cluster.py tests/test_golden.py tests/test_lineage.py tests/test_config.py tests/test_pipeline.py tests/test_api.py -v --timeout=120`
Expected: all PASS (this is the documented torch-free core batch + the API surface)

Run: `RUNPY -m pytest tests/test_polars_lazy.py tests/test_indicators_dtype_defer.py -v`
Expected: all PASS (tasks 1-2 still green after the sweep)

- [ ] **Step 6: Commit**

```bash
cd /d/show_case/goldenmatch/.worktrees/gm-polars-evict
git add -A packages/python/goldenmatch/goldenmatch packages/python/goldenmatch/tests/test_lazy_import_gate.py
git commit -m "refactor(goldenmatch): sweep 112 module-level polars imports to the lazy proxy (eviction W0)"
```

---

### Task 4: `core/frame.py` seam scaffold

**Files:**
- Create: `goldenmatch/core/frame.py`
- Test: `tests/test_frame_seam.py`

W0 ships the Protocols + the delegating Polars backend + `to_frame()` ONLY. No pipeline call site changes (that is W1+). The op set is deliberately minimal (YAGNI -- the Task 5 audit informs W1's expansion): Frame = {columns, height, native, column, to_arrow_columns}; Column = {__len__, null_count, n_unique, to_list, to_arrow}. `to_arrow_columns` exists because it IS the fused-kernel FFI boundary shape (`dict[str, pa.Array]`, what `run_match_fused_arrow` consumes today via `collected_df[c].to_arrow()`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_frame_seam.py
"""W0 Frame/Column seam scaffold: delegation parity vs raw Polars + to_frame idempotency."""
from __future__ import annotations

import polars as pl

from goldenmatch.core.frame import Column, Frame, PolarsFrame, to_frame


def _df() -> pl.DataFrame:
    return pl.DataFrame({"name": ["ann", "bob", None, "ann"], "zip": [1, 2, 3, 1]})


def test_to_frame_wraps_polars_dataframe():
    frame = to_frame(_df())
    assert isinstance(frame, PolarsFrame)
    assert isinstance(frame, Frame)  # runtime_checkable Protocol


def test_to_frame_is_idempotent():
    frame = to_frame(_df())
    assert to_frame(frame) is frame


def test_frame_delegation_matches_raw_polars():
    df = _df()
    frame = to_frame(df)
    assert frame.columns == df.columns
    assert frame.height == df.height
    assert frame.native is df


def test_column_delegation_matches_raw_polars():
    df = _df()
    col = to_frame(df).column("name")
    assert isinstance(col, Column)
    assert len(col) == 4
    assert col.null_count() == df["name"].null_count()
    assert col.n_unique() == df["name"].n_unique()
    assert col.to_list() == df["name"].to_list()


def test_to_arrow_columns_matches_kernel_ffi_shape():
    """to_arrow_columns must produce exactly what the fused kernels consume today."""
    df = _df()
    arrow_cols = to_frame(df).to_arrow_columns(["name", "zip"])
    assert set(arrow_cols) == {"name", "zip"}
    for name in ("name", "zip"):
        assert arrow_cols[name].to_pylist() == df[name].to_arrow().to_pylist()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `RUNPY -m pytest tests/test_frame_seam.py -v`
Expected: collection error, `ModuleNotFoundError: No module named 'goldenmatch.core.frame'`

- [ ] **Step 3: Write the scaffold**

```python
# goldenmatch/core/frame.py
"""Backend-neutral Frame/Column seam for the Polars eviction (W0 scaffold).

Pipeline code will route through this instead of raw ``pl.DataFrame`` so call
sites can migrate off Polars wave by wave (spec:
docs/superpowers/specs/2026-07-09-goldenmatch-polars-eviction-design.md).
W0 ships only the delegating Polars backend; the ArrowFrame backend arrives in
W1. ``to_frame`` is idempotent so a caller may pass a raw ``pl.DataFrame`` or
an already-wrapped ``Frame``.

Op-set discipline: SEMANTIC operations only, added as call sites port -- never
a Polars-expression clone. New ops require both backends plus a delegation-
parity test.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from goldenmatch._polars_lazy import pl


@runtime_checkable
class Column(Protocol):
    def __len__(self) -> int: ...
    def null_count(self) -> int: ...
    def n_unique(self) -> int: ...
    def to_list(self) -> list: ...
    def to_arrow(self) -> Any: ...


@runtime_checkable
class Frame(Protocol):
    @property
    def columns(self) -> list[str]: ...
    @property
    def height(self) -> int: ...
    @property
    def native(self) -> Any: ...
    def column(self, name: str) -> Column: ...
    def to_arrow_columns(self, names: list[str]) -> dict[str, Any]: ...


class PolarsColumn:
    """Delegates each op to the exact Polars call it replaces (byte-identical)."""

    __slots__ = ("_s",)

    def __init__(self, s: Any) -> None:
        self._s = s

    def __len__(self) -> int:
        return len(self._s)

    def null_count(self) -> int:
        return self._s.null_count()

    def n_unique(self) -> int:
        return self._s.n_unique()

    def to_list(self) -> list:
        return self._s.to_list()

    def to_arrow(self) -> Any:
        return self._s.to_arrow()


class PolarsFrame:
    __slots__ = ("_df",)

    def __init__(self, df: Any) -> None:
        self._df = df

    @property
    def columns(self) -> list[str]:
        return self._df.columns

    @property
    def height(self) -> int:
        return self._df.height

    @property
    def native(self) -> Any:
        return self._df

    def column(self, name: str) -> PolarsColumn:
        return PolarsColumn(self._df[name])

    def to_arrow_columns(self, names: list[str]) -> dict[str, Any]:
        # The fused-kernel FFI boundary: dict[str, pa.Array/ChunkedArray],
        # exactly the `collected_df[c].to_arrow()` shape pipeline.py builds today.
        return {n: self._df[n].to_arrow() for n in names}


def to_frame(obj: Any) -> Frame:
    """Idempotent coercion: raw ``pl.DataFrame`` or ``Frame`` -> ``Frame``."""
    if isinstance(obj, PolarsFrame):
        return obj
    if isinstance(obj, pl.DataFrame):
        return PolarsFrame(obj)
    raise TypeError(f"to_frame expects a polars DataFrame or Frame, got {type(obj)!r}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `RUNPY -m pytest tests/test_frame_seam.py -v`
Expected: 5 PASS

- [ ] **Step 5: Verify the gate still holds (frame.py must not load polars at import)**

Run: `RUNPY -m pytest tests/test_lazy_import_gate.py -v`
Expected: 2 PASS

- [ ] **Step 6: Commit**

```bash
git add packages/python/goldenmatch/goldenmatch/core/frame.py packages/python/goldenmatch/tests/test_frame_seam.py
git commit -m "feat(goldenmatch): Frame/Column seam scaffold with delegating Polars backend (eviction W0)"
```

---

### Task 5: Op audit script + inventory doc

**Files:**
- Create: `scripts/audit_goldenmatch_polars_ops.py` (repo root `scripts/`)
- Create: `docs/design/2026-07-09-goldenmatch-polars-op-inventory.md` (repo root `docs/`)

This is the W0 deliverable that sizes W1-W4: an AST census of every `pl.<attr>` use and every method call on frame/series receivers in files that import polars, grouped per file, plus a hand-written summary mapping op families to waves. No tests (a read-only dev tool); the doc is the artifact.

- [ ] **Step 1: Write the audit script**

```python
# scripts/audit_goldenmatch_polars_ops.py
"""AST census of Polars usage in goldenmatch -- W0 op audit for the eviction.

Emits markdown: per-file counts of (a) `pl.<attr>` attribute uses and (b) calls
to a curated list of DataFrame/Series/LazyFrame relational methods. The curated
list makes (b) high-signal: bare method names can collide with non-Polars
objects, so treat per-file counts as an upper bound to hand-verify per wave.

Usage: python scripts/audit_goldenmatch_polars_ops.py > /tmp/op_audit.md
"""
from __future__ import annotations

import ast
import sys
from collections import Counter
from pathlib import Path

PKG = Path(__file__).resolve().parents[1] / "packages" / "python" / "goldenmatch" / "goldenmatch"

# Relational / frame-shaped methods whose ports define the W1-W4 seam ops.
FRAME_METHODS = {
    "join", "join_asof", "group_by", "groupby", "partition_by", "filter",
    "with_columns", "select", "sort", "unique", "drop_nulls", "concat",
    "explode", "pivot", "melt", "unpivot", "vstack", "hstack", "rename",
    "cast", "lazy", "collect", "scan_csv", "read_csv", "read_parquet",
    "write_csv", "write_parquet", "read_excel", "to_arrow", "from_arrow",
    "agg", "over", "replace_strict", "value_counts", "n_unique",
    "null_count", "is_in", "concat_str", "map_elements", "map_batches",
}


def audit_file(path: Path) -> tuple[Counter, Counter]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    pl_attrs: Counter = Counter()
    methods: Counter = Counter()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name) and node.value.id == "pl":
                pl_attrs[node.attr] += 1
            elif node.attr in FRAME_METHODS:
                methods[node.attr] += 1
    return pl_attrs, methods


def main() -> int:
    rows = []
    total_attrs: Counter = Counter()
    total_methods: Counter = Counter()
    for path in sorted(PKG.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if "_polars_lazy import pl" not in text and "import polars" not in text:
            continue
        pl_attrs, methods = audit_file(path)
        if not pl_attrs and not methods:
            continue
        rel = path.relative_to(PKG)
        rows.append((str(rel), sum(pl_attrs.values()), sum(methods.values())))
        total_attrs.update(pl_attrs)
        total_methods.update(methods)

    print("# goldenmatch Polars op census (generated)\n")
    print("| file | pl.* uses | relational method calls |")
    print("| --- | ---: | ---: |")
    for rel, a, m in sorted(rows, key=lambda r: -(r[1] + r[2])):
        print(f"| {rel} | {a} | {m} |")
    print("\n## pl.* attribute totals\n")
    for name, n in total_attrs.most_common():
        print(f"- `pl.{name}`: {n}")
    print("\n## Relational method totals (upper bound; hand-verify per wave)\n")
    for name, n in total_methods.most_common():
        print(f"- `.{name}(...)`: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Run it and sanity-check the output**

Run (from the worktree root): `D:/show_case/goldenmatch/.venv/Scripts/python.exe scripts/audit_goldenmatch_polars_ops.py | head -40`
Expected: a markdown table with 100+ rows; `core/pipeline.py`, `core/scorer.py`, `core/golden.py`, `core/blocker.py`, `distributed/clustering.py` near the top.

- [ ] **Step 3: Write the inventory doc**

Create `docs/design/2026-07-09-goldenmatch-polars-op-inventory.md` with:
1. A header explaining this is the W0 audit deliverable of the eviction spec and how to regenerate (`python scripts/audit_goldenmatch_polars_ops.py`).
2. The generated census output pasted verbatim under a `## Generated census` heading.
3. A hand-written `## Op families -> waves` section that groups the observed usage into the spec's porting fronts, naming which wave owns each family:
   - IO (`read_csv`/`scan_csv`/`read_parquet`/`write_*`/`read_excel`) -> W1 `io_arrow`
   - Kernel boundary (`to_arrow`, `from_arrow`) -> already Arrow-shaped, W1 wiring
   - Relational glue (`join`, `group_by`, `partition_by`, `unique`, `concat`) -> W2 seam ops
   - Expression chains (`with_columns`, `select`, `filter`, `cast`, `concat_str`, `map_*`) -> W2/W3 semantic ops (named per call-site intent)
   - Column reductions (`n_unique`, `null_count`, `value_counts`) -> W3 (goldencheck-proven seam ops)
   - Distributed/tails usage -> W4
4. A `## Module-level import-time hazards` section recording the W0 findings: exactly 2 dtype-set constants (fixed in this wave, `core/indicators.py`), all 112 files already carried `from __future__ import annotations`, zero `from polars import X` variants.

- [ ] **Step 4: Commit**

```bash
git add scripts/audit_goldenmatch_polars_ops.py docs/design/2026-07-09-goldenmatch-polars-op-inventory.md
git commit -m "docs(goldenmatch): W0 Polars op audit script + inventory (eviction W0)"
```

---

### Task 6: Full W0 verification

**Files:** none (verification only)

- [ ] **Step 1: Lint + typecheck the touched surface**

```bash
cd /d/show_case/goldenmatch/.worktrees/gm-polars-evict
D:/show_case/goldenmatch/.venv/Scripts/python.exe -m ruff check packages/python/goldenmatch/goldenmatch packages/python/goldenmatch/tests scripts/audit_goldenmatch_polars_ops.py
D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pyright packages/python/goldenmatch/goldenmatch/_polars_lazy.py packages/python/goldenmatch/goldenmatch/core/frame.py packages/python/goldenmatch/goldenmatch/core/indicators.py 2>&1 | tail -5
```

Expected: ruff `All checks passed!`. Pyright: no NEW errors in the three files (local pyright shows pre-existing `reportMissingImports` noise absent on CI -- ignore exactly that class). The full-package pyright gate runs in CI.

- [ ] **Step 2: Wider targeted test batches (spot the sweep's blast radius)**

```bash
RUNPY -m pytest tests/test_autoconfig_regressions.py tests/test_scorer.py tests/test_blocker.py -v --timeout=120 -x
RUNPY -m pytest tests/test_lazy_import_gate.py tests/test_polars_lazy.py tests/test_frame_seam.py tests/test_indicators_dtype_defer.py -v
```

Expected: all PASS. (If a listed file does not exist under those names, substitute `-k autoconfig`, `-k scorer`, `-k blocker` selections.) Do NOT run the full suite locally (xdist OOMs the box; CI owns the full matrix).

- [ ] **Step 3: The three W0 invariants, one command each**

```bash
# 1. Gate: import goldenmatch loads no polars (already tested; re-run standalone)
RUNPY -m pytest tests/test_lazy_import_gate.py -q
# 2. Sweep completeness: zero column-0 polars imports in the package
grep -rnE "^import polars|^from polars" --include=*.py packages/python/goldenmatch/goldenmatch/ ; echo "exit=$?"   # expect exit=1
# 3. Byte-identical intent: the diff contains ONLY import-line swaps, the indicators
#    deferral, and new files
git diff origin/main --stat | tail -5
git diff origin/main -- packages/python/goldenmatch/goldenmatch/ | grep "^-" | grep -vE "^---|^-import polars as pl$|_BOOLEAN_DTYPES|_NON_IDENTITY_DTYPES" ; echo "exit=$?"   # expect exit=1 (no other deletions)
```

- [ ] **Step 4: Commit any stragglers**

```bash
git status --short   # expect clean; commit with a fix message if not
```

---

### Task 7: PR + auto-merge

**Files:** none

- [ ] **Step 1: Push with the auth dance**

```bash
cd /d/show_case/goldenmatch/.worktrees/gm-polars-evict
unset GH_TOKEN
gh auth switch --user benzsevern
git push -u origin feat/goldenmatch-polars-eviction-w0
```

- [ ] **Step 2: Open the PR**

```bash
GH_TOKEN=$(gh auth token --user benzsevern) gh pr create \
  --title "refactor(goldenmatch): Polars eviction W0 -- lazy-import linchpin + Frame seam scaffold" \
  --body "$(cat <<'EOF'
## Summary
- W0 of the Polars-eviction program (spec: docs/superpowers/specs/2026-07-09-goldenmatch-polars-eviction-design.md)
- `import goldenmatch` no longer loads Polars: `_polars_lazy` proxy (goldenflow template + TYPE_CHECKING dual-path so pyright keeps real Polars types) swept across all 112 module-level import sites; the 2 module-level dtype-set constants in core/indicators.py deferred via lru_cache
- `core/frame.py` Frame/Column seam scaffold with delegating PolarsFrame backend + idempotent `to_frame()` (no call sites ported -- that is W1+)
- W0 audits checked in: `scripts/audit_goldenmatch_polars_ops.py` + `docs/design/2026-07-09-goldenmatch-polars-op-inventory.md`

Byte-identical: no behavior change, no version bump, existing tests unedited.

## Test plan
- [x] New: tests/test_lazy_import_gate.py (subprocess gate: polars not in sys.modules after `import goldenmatch`)
- [x] New: tests/test_polars_lazy.py, tests/test_frame_seam.py, tests/test_indicators_dtype_defer.py
- [x] Targeted local batches green; full suite in CI

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 3: Arm auto-merge and STOP (never poll CI)**

```bash
GH_TOKEN=$(gh auth token --user benzsevern) gh pr merge --auto --squash
gh auth switch --user benzsevern-mjh
```

The merge queue runs the full goldenmatch matrix (the real parity gate: 1300+ tests unedited). Per standing SOP, do not poll; the queue reports.

---

## Out of scope for W0 (explicitly)

- NO pipeline/controller call site moves onto the seam (W1+).
- NO ArrowFrame backend, NO `GOLDENMATCH_FRAME` env var (W1).
- NO pyproject dependency change -- `polars>=1.0` stays a base dep until W5.
- NO CHANGELOG entry / docs-site change: zero user-visible behavior change.
- Function-local `import polars as pl` / `as _pl` sites stay as-is (already lazy).
