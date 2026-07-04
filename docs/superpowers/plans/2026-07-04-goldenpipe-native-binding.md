# goldenpipe-native binding + parity gate (SP2) — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a `goldenpipe-native` abi3 wheel over the SP1 `goldenpipe-core` planner + a reference-mode loader, and make the core the Python REFERENCE via a pure-Python==core parity gate (the pure-Python planner stays the runtime — parity-gate-only).

**Architecture:** Mirror goldenflow Wave 0a (#1405). A tiny pyo3 crate wraps the core's five `*_json` fns. A `_planner_json.py` helper puts the REAL pure-Python planner behind the same JSON interface so a parity test can replay the SP1 golden vectors through both and assert equal. No runtime routing; one additive `WiringError` change.

**Tech Stack:** Rust (pyo3 abi3-py311, edition 2021), Python 3.11+ (pydantic, polars), maturin, pytest.

**Spec:** `docs/superpowers/specs/2026-07-04-goldenpipe-native-binding-design.md`

---

## Ground truth (confirmed by reading the code)

- `resolver.py`: `WiringError(Exception)` is plain (no attrs). `PlannedStage{name, stage, spec: StageSpec, config: dict}`. `ExecutionPlan{stages: [PlannedStage]}`. Resolver auto-prepends load via `try: registry.get("load") except KeyError`. Serialize a PlannedStage → core `PlannedSpec`: `{name: p.name, use: p.spec.use, config: p.config, on_error: p.spec.on_error}` (+ `skip_if` iff `p.spec.skip_if` is set). `on_error` is already the string `"continue"`/`"abort"` (a `Literal`).
- `registry.py`: `StageRegistry._stages: dict` keyed by `info.name` via `register()`. `get(name)` → `KeyError`. `list_all()` → `{name: StageInfo}`. NO `has()` method. To key by a `key != info.name` (SP1's `key` field), write `_stages[key] = stub` DIRECTLY.
- `pipeline.py`: `Pipeline(config=None, registry=None, identity_opts=None)`; passing a non-None `registry` SKIPS `discover()`. `_auto_config(self)` reads `self._registry.list_all()` + `self._identity_opts` (an instance method — construct a stub-registry Pipeline).
- `decisions.py`: three free fns `severity_gate`/`pii_router`/`row_count_gate(ctx) -> Decision|None`. No dispatch/unknown handling (the helper adds the name table + unknown→None).
- SP1 golden vectors (the shared truth, already on main): `packages/rust/extensions/goldenpipe-core/tests/vectors/{resolve,apply_decision,evaluate_builtin,auto_config,skip_if}.json`, arrays of `{input, expected}` (some carry a `comment` the harness ignores).
- Core JSON shapes (from SP1): PlannedSpec `{name,use,config,on_error}` (+skip_if if Some); Decision `{skip,abort,insert,reason}`; StageSpec `{use,needs,on_error,config}` (+name,skip_if if Some); PlanError `{kind:"wiring"|"unknown_stage",...}`; resolve envelope `{"ok":...}|{"err":...}`; evaluate None → JSON `null`.
- Mirror targets: `packages/rust/extensions/native-flow/{Cargo.toml,src/lib.rs,pyproject.toml}`, `packages/python/goldenflow/goldenflow/core/_native_loader.py`, `.../scripts/build_native.py`, `tests/core/test_native_loader.py`.
- `packages/python/goldenpipe/tests/` exists; NO `tests/core/` yet (create it). `goldenpipe/goldenpipe/core/` does NOT exist (create it + `__init__.py`).

## File structure

- Create `packages/rust/extensions/goldenpipe-native/{Cargo.toml, src/lib.rs, pyproject.toml}`
- Create `packages/python/goldenpipe/goldenpipe/core/__init__.py`, `.../core/_native_loader.py`, `.../core/_planner_json.py`
- Modify `packages/python/goldenpipe/goldenpipe/engine/resolver.py` (additive `WiringError` attrs)
- Create `packages/python/goldenpipe/tests/core/__init__.py` (if the test dir needs it), `.../tests/core/test_native_loader.py`, `.../tests/core/test_planner_parity.py`
- Create `packages/python/goldenpipe/scripts/build_native.py`
- Modify `.github/workflows/ci.yml` (parity lane + paths-filter — Task 8)

## Box runners

Python (box-safe):
```
cd packages/python/goldenpipe
POLARS_SKIP_CPU_CHECK=1 GOLDENMATCH_NATIVE=0 GOLDENPIPE_NATIVE=0 \
  /d/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest tests/core/ -q
```
(`POLARS_SKIP_CPU_CHECK=1` — goldenpipe imports polars via pipeline.py. `GOLDENPIPE_NATIVE=0` keeps Leg B skipped locally unless the wheel is present.)
Rust wheel (box BEST-EFFORT, do NOT block SP2):
```
cd packages/rust/extensions/goldenpipe-native
export PATH="/d/.rustup/toolchains/1.94.0-x86_64-pc-windows-msvc/bin:$PATH" CARGO_HOME=/d/.cargo
maturin build   # or: /d/show_case/goldenmatch/.venv/Scripts/python.exe ../../../packages/python/goldenpipe/scripts/build_native.py
```
Reference skills: @superpowers:test-driven-development, @superpowers:subagent-driven-development. Auth: benzsevern (`unset GH_TOKEN` before push).

---

## Task 1: `goldenpipe-native` crate (the pyo3 shim over the core)

**Files:** Create `packages/rust/extensions/goldenpipe-native/{Cargo.toml, src/lib.rs, pyproject.toml}`

- [ ] **Step 1: `Cargo.toml`**

```toml
# Standalone workspace (empty [workspace]) so pyo3's `extension-module` feature isn't
# unified with any sibling crate's features. Mirrors native-flow / goldenmatch's native.
[workspace]

[package]
name = "goldenpipe-native"
version = "0.1.0"
edition = "2021"
license = "MIT"
authors = ["Ben Severn <benzsevern@gmail.com>"]
description = "Native binding for the GoldenPipe planner kernel (PyO3 extension module over goldenpipe-core)"

[lib]
# Produces _native.{so,dll,dylib}; the #[pymodule] is `_native` -> init PyInit__native.
name = "_native"
crate-type = ["cdylib"]

[dependencies]
# abi3-py311: one stable-ABI artifact spans CPython 3.11-3.13.
# extension-module: don't link libpython (imported BY CPython).
pyo3 = { version = ">=0.28, <0.29", features = ["extension-module", "abi3-py311"] }
# The core is the single source of truth; this crate is a pure JSON-string marshaling
# shim. No arrow (the planner is JSON, not columnar).
goldenpipe-core = { path = "../goldenpipe-core" }

[profile.release]
opt-level = 3
lto = "thin"
```

- [ ] **Step 2: `src/lib.rs`**

```rust
//! `goldenpipe._native` / `goldenpipe_native._native` — the PyO3 binding for the
//! GoldenPipe planner kernel. Pure marshaling shim: `&str` in -> goldenpipe-core
//! json fn -> `String` out. The core owns all logic; the pure-Python planner is a
//! non-authoritative fallback proven to reproduce these bytes (SP2 parity gate).
use pyo3::prelude::*;

#[pyfunction]
fn resolve_json(input: &str) -> String {
    goldenpipe_core::json::resolve_json(input)
}
#[pyfunction]
fn apply_decision_json(input: &str) -> String {
    goldenpipe_core::json::apply_decision_json(input)
}
#[pyfunction]
fn evaluate_builtin_json(input: &str) -> String {
    goldenpipe_core::json::evaluate_builtin_json(input)
}
#[pyfunction]
fn auto_config_json(input: &str) -> String {
    goldenpipe_core::json::auto_config_json(input)
}
#[pyfunction]
fn skip_if_falsy_json(input: &str) -> String {
    goldenpipe_core::json::skip_if_falsy_json(input)
}

#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    m.add_function(wrap_pyfunction!(resolve_json, m)?)?;
    m.add_function(wrap_pyfunction!(apply_decision_json, m)?)?;
    m.add_function(wrap_pyfunction!(evaluate_builtin_json, m)?)?;
    m.add_function(wrap_pyfunction!(auto_config_json, m)?)?;
    m.add_function(wrap_pyfunction!(skip_if_falsy_json, m)?)?;
    Ok(())
}
```

- [ ] **Step 3: `pyproject.toml`** (mirror native-flow; separate `goldenpipe-native` wheel)

```toml
# goldenpipe-native — optional native binding for the GoldenPipe planner, shipped as a
# SEPARATE maturin/abi3 package (mirrors goldenflow-native). `goldenpipe` stays pure
# Python; `pip install goldenpipe[native]` pulls this; goldenpipe.core._native_loader
# discovers it. Note: this exposes the core as the REFERENCE; it does not accelerate.
[build-system]
requires = ["maturin>=1.7,<2"]
build-backend = "maturin"

[project]
name = "goldenpipe-native"
version = "0.1.0"
description = "Native binding (Rust/PyO3) for the goldenpipe planner kernel"
readme = "README.md"
requires-python = ">=3.11"
license = { text = "MIT" }
authors = [{ name = "Ben Severn", email = "ben@bensevern.dev" }]
classifiers = [
    "Programming Language :: Rust",
    "Programming Language :: Python :: 3 :: Only",
    "License :: OSI Approved :: MIT License",
]

[project.urls]
Homepage = "https://github.com/benseverndev-oss/goldenmatch"

[tool.maturin]
python-source = "python"
module-name = "goldenpipe_native._native"
```
Also create `packages/rust/extensions/goldenpipe-native/python/goldenpipe_native/__init__.py` (empty) and a one-line `README.md` so maturin's mixed layout resolves (mirror native-flow's `python/goldenflow_native/`).

- [ ] **Step 4: Verify it compiles** (box best-effort):
```
cd packages/rust/extensions/goldenpipe-native && export PATH="/d/.rustup/toolchains/1.94.0-x86_64-pc-windows-msvc/bin:$PATH" CARGO_HOME=/d/.cargo
maturin build 2>&1 | tail -5
```
Expected: a wheel builds (goldenpipe-native has only pyo3 + the serde-only core; no ort/arrow, so it should link on Windows/NTFS unlike goldenmatch-native). **If maturin/linking fails locally, SKIP** — the crate is validated in CI (Task 8); do NOT block SP2. `cargo build` alone may complain about linking libpython under `extension-module`; prefer `maturin build`.

- [ ] **Step 5: Commit** `feat(goldenpipe-native): pyo3 binding crate over goldenpipe-core`

---

## Task 2: `_native_loader.py` + `core/` package + loader tests

**Files:**
- Create: `packages/python/goldenpipe/goldenpipe/core/__init__.py` (empty), `.../core/_native_loader.py`
- Create: `packages/python/goldenpipe/tests/core/__init__.py` (empty), `.../tests/core/test_native_loader.py`

- [ ] **Step 1: Write the failing loader tests** (`tests/core/test_native_loader.py`, mirror goldenflow's)

```python
from goldenpipe.core import _native_loader as L


def test_planner_enabled_when_symbol_present(monkeypatch):
    class FakeNative:
        def resolve_json(self, s): ...
    monkeypatch.setenv("GOLDENPIPE_NATIVE", "auto")
    monkeypatch.setattr(L, "_native", FakeNative())
    assert L.native_enabled("planner") is True


def test_force_off(monkeypatch):
    monkeypatch.setenv("GOLDENPIPE_NATIVE", "0")
    monkeypatch.setattr(L, "_native", object())
    assert L.native_enabled("planner") is False


def test_require_raises_when_absent(monkeypatch):
    monkeypatch.setenv("GOLDENPIPE_NATIVE", "1")
    monkeypatch.setattr(L, "_native", None)
    import pytest
    with pytest.raises(RuntimeError):
        L.native_enabled("planner")


def test_auto_disabled_when_symbol_missing(monkeypatch):
    class Bare:  # native present but no resolve_json symbol
        pass
    monkeypatch.setenv("GOLDENPIPE_NATIVE", "auto")
    monkeypatch.setattr(L, "_native", Bare())
    assert L.native_enabled("planner") is False
```

- [ ] **Step 2: Run** the box runner on `tests/core/test_native_loader.py` — FAIL (module missing).

- [ ] **Step 3: Implement `_native_loader.py`** (mirror goldenflow, trimmed — one component `planner`, no `_FALLBACK_ONLY`)

```python
"""Loader + gate for the optional ``goldenpipe._native`` binding.

Mirrors ``goldenflow.core._native_loader``. The binding (Rust/PyO3, built from
``packages/rust/extensions/goldenpipe-native``) exposes ``goldenpipe-core`` — the
REFERENCE planner kernel — to Python. It is NOT a runtime accelerator: the
pure-Python planner stays the runtime; this loader exists so the parity gate can
reach the kernel (and so a future reference-mode flip has the seam ready).

``GOLDENPIPE_NATIVE`` env:
- ``"0"``   -> force pure (never use native).
- ``"1"``   -> require native; raise if not importable (the CI parity lane).
- ``"auto"`` / unset -> native available iff the floor symbol exists. Default.

Reachable two ways, tried in order (like goldenflow/goldenmatch):
  1. ``goldenpipe._native``        — in-tree build (scripts/build_native.py).
  2. ``goldenpipe_native._native`` — the separate ``goldenpipe-native`` abi3 wheel.
"""
from __future__ import annotations

import os
from typing import Any

try:
    import goldenpipe._native as _native  # type: ignore[import-not-found]
except Exception:  # noqa: BLE001 - any import/load failure falls back below
    try:
        from goldenpipe_native import _native  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001 - neither path available -> pure Python
        _native = None

# Floor symbols per component (wheel-skew safe: probe the actual module).
_COMPONENT_SYMBOLS: dict[str, tuple[str, ...]] = {
    "planner": ("resolve_json",),
}


def _has_symbol(component: str) -> bool:
    if _native is None:
        return False
    syms = _COMPONENT_SYMBOLS.get(component)
    if not syms:
        return False
    return any(hasattr(_native, s) for s in syms)


def native_module() -> Any:
    """The imported native module, or ``None``. Guard with ``native_enabled`` first."""
    return _native


def native_available() -> bool:
    return _native is not None


def native_enabled(component: str) -> bool:
    mode = os.environ.get("GOLDENPIPE_NATIVE", "auto").lower()
    if mode == "0":
        return False
    if mode == "1":
        if _native is None:
            raise RuntimeError(
                "GOLDENPIPE_NATIVE=1 but goldenpipe._native is not built/importable"
            )
        return True
    return _native is not None and _has_symbol(component)


# Thin pass-throughs the parity test's Leg B calls (guard with native_enabled first).
def resolve_json(input: str) -> str:
    return _native.resolve_json(input)


def apply_decision_json(input: str) -> str:
    return _native.apply_decision_json(input)


def evaluate_builtin_json(input: str) -> str:
    return _native.evaluate_builtin_json(input)


def auto_config_json(input: str) -> str:
    return _native.auto_config_json(input)


def skip_if_falsy_json(input: str) -> str:
    return _native.skip_if_falsy_json(input)
```

- [ ] **Step 4: Run** the loader tests — 4 PASS.
- [ ] **Step 5: Commit** `feat(goldenpipe): native-loader reference-mode gate (mirror goldenflow)`

---

## Task 3: `WiringError` structured attrs (additive)

**Files:** Modify `packages/python/goldenpipe/goldenpipe/engine/resolver.py`
- Test: `packages/python/goldenpipe/tests/core/test_wiring_error_attrs.py` (NEW)

- [ ] **Step 1: Write the failing test**

```python
from goldenpipe.engine.resolver import WiringError


def test_wiring_error_is_additive():
    # legacy raise (message only) still works
    e0 = WiringError("some message")
    assert str(e0) == "some message"
    assert e0.stage is None and e0.missing is None and e0.available is None
    # structured raise carries attrs, message preserved
    e1 = WiringError("msg", stage="s", missing="df", available=["a", "b"])
    assert str(e1) == "msg"
    assert (e1.stage, e1.missing, e1.available) == ("s", "df", ["a", "b"])
```

- [ ] **Step 2: Run** — FAIL (`WiringError` has no `.stage`).

- [ ] **Step 3: Implement** — replace the `WiringError` class + the raise site in `resolver.py`

```python
class WiringError(Exception):
    """Raised when a stage's consumes can't be satisfied. Carries OPTIONAL structured
    attrs (stage/missing/available) for the parity helper; the message is unchanged and
    the single-positional-message raise still works (existing consumers read str(e))."""

    def __init__(self, message: str, *, stage: str | None = None,
                 missing: str | None = None, available: list[str] | None = None) -> None:
        super().__init__(message)
        self.stage = stage
        self.missing = missing
        self.available = available
```
And at the raise site (currently `resolver.py:61-64`):
```python
                    raise WiringError(
                        f"Stage '{name}' consumes '{dep}' but no prior stage "
                        f"produces it. Available: {sorted(available_artifacts)}",
                        stage=name, missing=dep, available=sorted(available_artifacts),
                    )
```

- [ ] **Step 4: Run** the new test + the existing `tests/test_config.py`/any resolver test to confirm nothing broke (`str(e)` consumers unaffected). PASS.
- [ ] **Step 5: Commit** `feat(goldenpipe): WiringError gains additive structured attrs`

---

## Task 4: `_planner_json.py` helper + Leg A parity gate (the deliverable)

**Files:**
- Create: `packages/python/goldenpipe/goldenpipe/core/_planner_json.py`
- Create: `packages/python/goldenpipe/tests/core/test_planner_parity.py`

- [ ] **Step 1: Write the Leg A parity harness** (`test_planner_parity.py`) — it drives the whole task

```python
"""SP2 Leg A: the pure-Python planner (via _planner_json) must reproduce the SP1
golden vectors (the core's output) byte-for-byte. Box-safe, no wheel."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from goldenpipe.core import _planner_json as PJ

# repo-relative: tests/core/ -> goldenpipe pkg -> python -> packages -> repo root (parents[4])
_VECTORS = Path(__file__).resolve().parents[4] / "packages/rust/extensions/goldenpipe-core/tests/vectors"


def _load(name: str) -> list[dict]:
    return json.loads((_VECTORS / f"{name}.json").read_text())


_CASES = [
    ("resolve", PJ.resolve_json),
    ("apply_decision", PJ.apply_decision_json),
    ("evaluate_builtin", PJ.evaluate_builtin_json),
    ("auto_config", PJ.auto_config_json),
    ("skip_if", PJ.skip_if_falsy_json),
]


@pytest.mark.parametrize("name,fn", _CASES)
def test_pure_python_matches_core_vectors(name, fn):
    for i, case in enumerate(_load(name)):
        got = json.loads(fn(json.dumps(case["input"])))
        assert got == case["expected"], f"{name}[{i}] input={case['input']!r}"
```

- [ ] **Step 2: Run** — FAIL (no `_planner_json`).

- [ ] **Step 3: Implement `_planner_json.py`** (calls the REAL planner; serializes to the core's shapes)

```python
"""The JSON face of the pure-Python planner — the SP2 parity surface (SHIPPED).

Each ``*_json`` fn CALLS the real Resolver/Router/decisions/_auto_config and serializes
to goldenpipe-core's exact JSON shapes, so the parity gate tests the actual planner
(not a re-implementation). It does NOT run at pipeline runtime; ordering/validation/
routing stay in the engine modules. Mirrors goldenpipe-core/src/json.rs.
"""
from __future__ import annotations

import json
from typing import Any

from goldenpipe import decisions as _dec
from goldenpipe.engine.resolver import Resolver, WiringError
from goldenpipe.engine.router import Router
from goldenpipe.models.config import PipelineConfig, StageSpec
from goldenpipe.models.context import Decision, PipeContext
from goldenpipe.models.stage import StageInfo


class _Stub:
    """A minimal Stage object carrying .info (all the planner reads)."""
    def __init__(self, info: StageInfo) -> None:
        self.info = info


class _StubRegistry:
    """Registry keyed EXPLICITLY (bypasses register()'s key-by-info.name) so
    `key != info.name` vectors resolve. Provides get()/list_all()."""
    def __init__(self) -> None:
        self._stages: dict[str, Any] = {}

    def add(self, key: str, info: StageInfo) -> None:
        self._stages[key] = _Stub(info)

    def get(self, name: str) -> Any:
        if name not in self._stages:
            raise KeyError(f"Stage '{name}' not found in registry")
        return self._stages[name]

    def list_all(self) -> dict[str, StageInfo]:
        return {k: s.info for k, s in self._stages.items()}


def _info(d: dict) -> StageInfo:
    return StageInfo(name=d["name"], produces=list(d["produces"]), consumes=list(d["consumes"]))


def _planned_to_dict(p: Any) -> dict:
    out = {"name": p.name, "use": p.spec.use, "config": p.config or {}, "on_error": p.spec.on_error}
    if p.spec.skip_if is not None:
        out["skip_if"] = p.spec.skip_if
    return out


def resolve_json(input_str: str) -> str:
    arg = json.loads(input_str)
    reg = _StubRegistry()
    for s in arg["stages"]:
        reg.add(s["key"], _info(s))  # key by the registry KEY, not info.name
    config = PipelineConfig(**arg["config"])
    try:
        plan = Resolver.resolve(config, reg)
    except WiringError as e:
        return json.dumps({"err": {"kind": "wiring", "stage": e.stage,
                                   "missing": e.missing, "available": e.available}})
    except KeyError:
        # unknown `use`: the first configured stage whose use isn't registered
        # (Resolver fails on the first such stage, in order).
        for raw in config.stages:
            use = raw if isinstance(raw, str) else raw.use
            if use not in reg._stages:
                return json.dumps({"err": {"kind": "unknown_stage", "use": use}})
        raise  # unreachable
    return json.dumps({"ok": {"stages": [_planned_to_dict(p) for p in plan.stages]}})


def apply_decision_json(input_str: str) -> str:
    arg = json.loads(input_str)
    d = arg["decision"]
    decision = Decision(skip=d.get("skip", []), abort=d.get("abort", False),
                        insert=d.get("insert", []), reason=d.get("reason", ""))
    remaining = []
    from goldenpipe.engine.resolver import PlannedStage
    for r in arg["remaining"]:
        remaining.append(PlannedStage(name=r["name"], stage=None,
                                      spec=StageSpec(use=r["use"]), config=r.get("config", {})))
    reg = _StubRegistry()
    for name in decision.insert:  # Router.get(name) for each inserted stage
        reg.add(name, StageInfo(name=name, produces=[], consumes=[]))
    ctx = PipeContext()
    new_remaining = Router.apply(decision, remaining, ctx, reg)
    out: dict = {"remaining": [_planned_to_dict(p) for p in new_remaining]}
    note = ctx.reasoning.get("_router")
    if note is not None:
        out["router_note"] = note
    return json.dumps(out)


_BUILTINS = {
    "severity_gate": _dec.severity_gate,
    "pii_router": _dec.pii_router,
    "row_count_gate": _dec.row_count_gate,
}


def evaluate_builtin_json(input_str: str) -> str:
    arg = json.loads(input_str)
    fn = _BUILTINS.get(arg["name"])
    if fn is None:
        return "null"
    ctx = PipeContext(artifacts=arg.get("ctx", {}).get("artifacts", {}),
                      metadata=arg.get("ctx", {}).get("metadata", {}))
    d = fn(ctx)
    if d is None:
        return "null"
    return json.dumps({"skip": d.skip, "abort": d.abort, "insert": d.insert, "reason": d.reason})


def auto_config_json(input_str: str) -> str:
    from goldenpipe.pipeline import Pipeline
    arg = json.loads(input_str)
    reg = _StubRegistry()
    for name in arg["available"]:
        reg.add(name, StageInfo(name=name, produces=[], consumes=[]))
    p = Pipeline(registry=reg, identity_opts=arg.get("identity_opts"))
    cfg = p._auto_config()
    stages = []
    for spec in cfg.stages:  # each is a StageSpec
        stages.append({"use": spec.use, "needs": spec.needs,
                       "on_error": spec.on_error, "config": spec.config})
    return json.dumps({"pipeline": cfg.pipeline, "stages": stages, "decisions": cfg.decisions})


def skip_if_falsy_json(input_str: str) -> str:
    return json.dumps(not json.loads(input_str))
```

- [ ] **Step 4: Run** the Leg A parity test. Iterate until all 5 families pass. If a case fails, the fix is in `_planner_json` (make it faithfully match the core), NEVER in the fixture. Likely first failures + fixes:
  - `auto_config` stage shape: confirm `StageSpec.on_error` serializes as the string (it's a `Literal`), and `spec.needs` is `[]`. If the core emitted `config` key order differently, the harness compares parsed dicts (order-insensitive) so it's fine.
  - `apply_decision` inserted stage: `PlannedStage(config default {})` → `_planned_to_dict` emits `config:{}`, `on_error:"continue"`, `use:name`. Matches core.
  - `resolve` `unknown_stage` empty-registry vector (`stages:[]`, config `["nope"]`): `Resolver.resolve` catches the load KeyError internally, then `registry.get("nope")` raises KeyError → helper derives `use:"nope"`. Confirm.
  Expected end state: 5 parametrized cases PASS (all vectors).

- [ ] **Step 5: Commit** `feat(goldenpipe): _planner_json helper + Leg A pure==core parity gate`

---

## Task 5: `build_native.py` + Leg B (wheel==core) parity

**Files:**
- Create: `packages/python/goldenpipe/scripts/build_native.py`
- Modify: `packages/python/goldenpipe/tests/core/test_planner_parity.py` (add Leg B)

- [ ] **Step 1: `build_native.py`** — mirror goldenflow's (cargo build + copy), with a Windows `.dll -> _native.pyd` branch

Copy goldenflow's `scripts/build_native.py` structure verbatim, changing: `CRATE = REPO/"packages/rust/extensions/goldenpipe-native"`, `PKG = REPO/"packages/python/goldenpipe/goldenpipe"`, artifact `lib_native.so`/`.dylib` (Linux/mac) OR `_native.dll` (Windows) → `DEST = PKG/"_native.abi3.so"` (Linux/mac) / `PKG/"_native.pyd"` (Windows). Keep the atomic `os.replace` write and the `PYO3_PYTHON=sys.executable` env. (CI Linux is the canonical path; the Windows branch is box convenience.)

- [ ] **Step 2: Add Leg B** to `test_planner_parity.py`

```python
from goldenpipe.core import _native_loader as NL


@pytest.mark.parametrize("name,fn_name", [
    ("resolve", "resolve_json"), ("apply_decision", "apply_decision_json"),
    ("evaluate_builtin", "evaluate_builtin_json"), ("auto_config", "auto_config_json"),
    ("skip_if", "skip_if_falsy_json"),
])
def test_native_wheel_matches_core_vectors(name, fn_name):
    import os
    if not NL.native_available():
        if os.environ.get("GOLDENPIPE_NATIVE") == "1":
            pytest.fail("GOLDENPIPE_NATIVE=1 but the native wheel is not importable")
        pytest.skip("native wheel not built (Leg B is CI-primary)")
    fn = getattr(NL, fn_name)
    for i, case in enumerate(_load(name)):
        got = json.loads(fn(json.dumps(case["input"])))
        assert got == case["expected"], f"native {name}[{i}] input={case['input']!r}"
```

- [ ] **Step 3: Run** the full parity suite locally (`GOLDENPIPE_NATIVE=0` or unset) — Leg A PASS, Leg B SKIP (no wheel). If you got the wheel to build in Task 1, run once with the wheel importable (build_native.py) to see Leg B PASS too — best-effort.
- [ ] **Step 4: Commit** `feat(goldenpipe): build_native.py + Leg B wheel parity (skip-guarded)`

---

## Task 6: CI parity lane

**Files:** Modify `.github/workflows/ci.yml`

- [ ] **Step 1:** Add a `dorny/paths-filter` output (e.g. `goldenpipe_native`) triggered by:
  `packages/rust/extensions/goldenpipe-core/**`, `packages/rust/extensions/goldenpipe-native/**`, `packages/python/goldenpipe/**`, and `ci.yml` itself.
- [ ] **Step 2:** Add a job gated on that output that: sets up Rust + Python 3.11, `maturin build`s the `goldenpipe-native` wheel + `pip install`s it, then runs
  `GOLDENPIPE_NATIVE=1 POLARS_SKIP_CPU_CHECK=1 pytest packages/python/goldenpipe/tests/core/ -q`
  (Leg A always + Leg B mandatory under `=1`). Mirror the existing goldenflow-native parity lane's shape. This is ALSO where goldenpipe-core gets its first CI coverage (the SP1 gap).
- [ ] **Step 3:** Note in the PR that editing `ci.yml` forces all jobs to re-run (per CLAUDE.md); that's expected.
- [ ] **Step 4: Commit** `ci(goldenpipe): GOLDENPIPE_NATIVE=1 parity lane (Leg A+B) + paths-filter`

---

## Wrap-up

- [ ] Full box suite green: `pytest packages/python/goldenpipe/tests/core/ -q` (Leg A + loader + WiringError, Leg B skipped). Push branch, open PR against main, arm `gh pr merge --auto --squash`, STOP. Auth: `GH_TOKEN=$(gh auth token --user benzsevern)` for `gh pr create`; `unset GH_TOKEN` before push.
- [ ] PR body: SP2 of goldenpipe→Rust; parity-gate-only (core=oracle, pure-Python stays runtime); the anti-drift value = Leg A locks Python to the core; SP3 (TS) is the drift-kill; the one additive WiringError change; box-verified Leg A, wheel/Leg B CI-primary.
- [ ] Update memory `project_goldenpipe_core_cross_surface`: SP2 shipped, what it delivers, SP3 remaining.
- [ ] `goldenpipe[native]` extra: if `packages/python/goldenpipe/pyproject.toml` has an extras table, add a `native = ["goldenpipe-native"]` extra (mirror goldenflow). Check before adding.

## Notes / risks

- **Leg A is the load-bearing gate** — it must call the REAL Resolver/Router/decisions (the helper is glue, not a re-impl). If a vector fails, fix `_planner_json` to match the core, never the fixture.
- **WiringError change is additive** — optional kwargs, message unchanged; existing `str(e)` consumers (server.py/mcp/cli/test_resolver.py) unaffected.
- **Wheel build is box best-effort** — goldenpipe-native has no heavy deps so it likely links on Windows, but if not, Leg B is CI-only; don't block SP2.
- **`PipelineConfig(**arg["config"])`** relies on pydantic coercing bare-string stage entries to `str` and dict entries to `StageSpec` (union, StageSpec-first). Confirm in Task 4 Step 4; if pydantic mis-coerces a bare string, normalize entries explicitly before constructing.
- **Scope stays SP2** — no runtime routing through the core; SP3 (TS/WASM) is separate.
