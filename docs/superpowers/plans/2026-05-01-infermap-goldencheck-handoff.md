# InferMap → GoldenCheck Handoff Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire InferMap into GoldenPipe as stage 0 with `goldencheck-types` as the shared type registry, so all four products (Python and TS) read canonical field types from one source.

**Architecture:** New Python `goldencheck-types` package mirrors the existing TS one (yaml is canonical, Py and TS are bindings). InferMap gains a `DomainPackTarget` adapter and `soft=True` mode. GoldenCheck accepts an optional `schema` argument routing rules by canonical type and emitting `unmapped_column` findings. GoldenPipe gets a new `infer_schema` stage and `--domain` / `--no-infer` / `--schema` flags.

**Tech Stack:** Python 3.11+, TypeScript, uv workspace, pytest, vitest, the four monorepo packages.

**Spec:** `docs/superpowers/specs/2026-05-01-infermap-goldencheck-handoff-design.md`

---

## Adaptation note: actual code surface vs spec terminology

The spec describes a notional `goldencheck.check(df, schema=...)`. In the existing code, the entry points are `scan_file`, `validate_file`, and `classify_columns`. This plan extends those. Likewise, `goldenpipe` is a stage-based pipeline framework (not a hardcoded function), so "stage 0" is implemented as a registered stage named `infer_schema`. The data-flow contracts from the spec are honored exactly; only the function names differ.

---

## File map

**New files:**
- `packages/python/goldencheck-types/pyproject.toml`
- `packages/python/goldencheck-types/goldencheck_types/__init__.py`
- `packages/python/goldencheck-types/goldencheck_types/types.py` — `FieldSpec`, `DomainPack`, `InferredSchema`, `FieldMapping`
- `packages/python/goldencheck-types/goldencheck_types/loader.py` — `load_domain`, `list_domains`
- `packages/python/goldencheck-types/tests/test_loader.py`
- `packages/python/goldencheck-types/tests/test_types.py`
- `packages/typescript/goldencheck-types/src/index.ts` — public exports
- `packages/typescript/goldencheck-types/src/types.ts`
- `packages/typescript/goldencheck-types/src/loader.ts`
- `packages/typescript/goldencheck-types/tests/loader.test.ts`
- `packages/python/infermap/infermap/domain_pack.py` — `DomainPackTarget` adapter
- `packages/python/infermap/infermap/detect.py` — `detect_domain` helper
- `packages/python/infermap/tests/test_domain_pack.py`
- `packages/python/infermap/tests/test_soft_mode.py`
- `packages/python/infermap/tests/test_detect.py`
- `packages/typescript/infermap/src/core/domainPack.ts`
- `packages/typescript/infermap/src/core/detect.ts`
- `packages/typescript/infermap/tests/domainPack.test.ts`
- `packages/python/goldencheck/tests/test_schema_aware.py`
- `packages/typescript/goldencheck/tests/schemaAware.test.ts`
- `packages/python/goldenpipe/goldenpipe/stages/infer_schema.py`
- `packages/python/goldenpipe/tests/test_infer_schema_stage.py`
- `packages/python/goldenpipe/tests/test_cli_flags.py`
- `tests/integration/test_pipe_end_to_end.py` (top-level integration)
- `tests/parity/test_python_ts_parity.py`
- `tests/fixtures/finance_clean.csv`
- `tests/fixtures/healthcare_clean.csv`
- `tests/fixtures/mixed_unknown.csv`
- `packages/typescript/goldencheck-types/domains/generic.yaml` — empty placeholder pack

**Modified files:**
- `packages/python/infermap/infermap/__init__.py` — add `DomainPackTarget`, `detect_domain` to exports
- `packages/python/infermap/infermap/engine.py` — add `soft` parameter, low-confidence → `unknown` mapping
- `packages/typescript/infermap/src/index.ts` — same exports as Python
- `packages/typescript/infermap/src/core/engine.ts` (or equivalent) — soft mode
- `packages/python/goldencheck/goldencheck/engine/scanner.py` — accept `schema` argument
- `packages/python/goldencheck/goldencheck/engine/validator.py` — accept `schema` argument
- `packages/python/goldencheck/goldencheck/semantic/classifier.py` — short-circuit when schema provided
- `packages/python/goldencheck/goldencheck/models/finding.py` — register `unmapped_column` code
- `packages/python/goldenpipe/goldenpipe/cli/__init__.py` (or wherever `run` CLI lives) — add `--domain`, `--no-infer`, `--schema` flags + precedence enforcement
- `packages/python/goldenpipe/goldenpipe/_api.py` — pass new args through to pipeline
- `packages/python/goldenpipe/goldenpipe/engine/registry.py` (or wherever stages register) — register `infer_schema` stage
- `pyproject.toml` (workspace root) — add `goldencheck-types` to `[tool.uv.sources]`

---

## Phase 1 — `goldencheck-types` Python bindings

Goal: typed Python objects backed by the existing TS-package yaml.

### Task 1.1: Scaffold the Python package

**Files:**
- Create: `packages/python/goldencheck-types/pyproject.toml`
- Create: `packages/python/goldencheck-types/goldencheck_types/__init__.py` (empty)

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "goldencheck-types"
version = "0.1.0"
description = "Shared canonical field types for the Golden Suite"
requires-python = ">=3.11"
dependencies = ["pyyaml>=6.0", "pydantic>=2.0"]

[project.optional-dependencies]
dev = ["pytest>=8.0"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build]
include = ["goldencheck_types/**", "domains/**"]

[tool.hatch.build.targets.wheel]
packages = ["goldencheck_types"]

[tool.hatch.build.targets.wheel.force-include]
"../../typescript/goldencheck-types/domains" = "goldencheck_types/_domains"
```

The `force-include` line copies the canonical yaml from the TS package into the Python wheel at build time, so the installed package is self-contained but the source of truth stays in one place.

- [ ] **Step 2: Add to workspace**

Update `pyproject.toml` at monorepo root:

```toml
[tool.uv.sources]
goldenmatch = { workspace = true }
goldencheck = { workspace = true }
goldenflow = { workspace = true }
goldenpipe = { workspace = true }
infermap = { workspace = true }
goldencheck-types = { workspace = true }
```

- [ ] **Step 3: Verify uv picks it up**

```bash
cd /d/mr/cleanup-staging
uv sync 2>&1 | tail -3
```

Expected: succeeds, `goldencheck-types` listed among workspace members.

- [ ] **Step 4: Commit**

```bash
git add packages/python/goldencheck-types/pyproject.toml packages/python/goldencheck-types/goldencheck_types/__init__.py pyproject.toml
git commit -m "feat(goldencheck-types): scaffold Python package"
```

### Task 1.2: Implement `FieldSpec`, `DomainPack`, `InferredSchema`, `FieldMapping` types

**Files:**
- Create: `packages/python/goldencheck-types/goldencheck_types/types.py`
- Create: `packages/python/goldencheck-types/tests/test_types.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_types.py
from goldencheck_types import FieldSpec, DomainPack, FieldMapping, InferredSchema

def test_fieldspec_minimal():
    fs = FieldSpec(name_hints=["ssn"], value_signals={}, suppress=[])
    assert fs.confidence_threshold is None  # optional

def test_domainpack_holds_types():
    fs = FieldSpec(name_hints=["ssn"], value_signals={}, suppress=[])
    pack = DomainPack(name="hc", description="", types={"ssn": fs})
    assert pack.types["ssn"].name_hints == ["ssn"]

def test_fieldmapping_unknown():
    m = FieldMapping(source_col="x", canonical=None, type="unknown",
                     confidence=0.4, evidence={})
    assert m.is_unknown

def test_inferred_schema_unmapped_list():
    m_known = FieldMapping("a", "ssn", "ssn", 0.9, {})
    m_unk = FieldMapping("b", None, "unknown", 0.3, {})
    s = InferredSchema(domain="hc", fields={"a": m_known, "b": m_unk}, confidence=0.3)
    assert s.unmapped == ["b"]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest packages/python/goldencheck-types/tests/test_types.py -v
```

Expected: ImportError / ModuleNotFoundError for the missing types.

- [ ] **Step 3: Write minimal implementation**

```python
# goldencheck_types/types.py
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

@dataclass(frozen=True)
class FieldSpec:
    name_hints: list[str]
    value_signals: dict[str, Any]
    suppress: list[str]
    confidence_threshold: float | None = None
    description: str | None = None

@dataclass(frozen=True)
class DomainPack:
    name: str
    description: str
    types: dict[str, FieldSpec]

@dataclass
class FieldMapping:
    source_col: str
    canonical: str | None
    type: str  # canonical type name or "unknown"
    confidence: float
    evidence: dict[str, Any]  # InferMap-internal; consumers must not depend on shape

    @property
    def is_unknown(self) -> bool:
        return self.type == "unknown"

@dataclass
class InferredSchema:
    domain: str
    fields: dict[str, FieldMapping]
    confidence: float

    @property
    def unmapped(self) -> list[str]:
        return [k for k, v in self.fields.items() if v.is_unknown]
```

- [ ] **Step 4: Re-export from `__init__.py`**

```python
# goldencheck_types/__init__.py
from goldencheck_types.types import (
    FieldSpec, DomainPack, FieldMapping, InferredSchema,
)
__all__ = ["FieldSpec", "DomainPack", "FieldMapping", "InferredSchema"]
__version__ = "0.1.0"
```

- [ ] **Step 5: Run tests, expect PASS, commit**

```bash
uv run pytest packages/python/goldencheck-types/tests/test_types.py -v
```

Then:
```bash
git add packages/python/goldencheck-types
git commit -m "feat(goldencheck-types): add FieldSpec, DomainPack, InferredSchema, FieldMapping"
```

### Task 1.3: `load_domain` and `list_domains`

**Files:**
- Create: `packages/python/goldencheck-types/goldencheck_types/loader.py`
- Create: `packages/python/goldencheck-types/tests/test_loader.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_loader.py
import pytest
from goldencheck_types import load_domain, list_domains

def test_list_domains_includes_finance():
    domains = list_domains()
    assert "finance" in domains
    assert "healthcare" in domains
    assert "ecommerce" in domains
    assert "generic" in domains

def test_load_finance_pack():
    pack = load_domain("finance")
    assert pack.name == "finance"
    assert "account_number" in pack.types
    assert "account_number" in pack.types["account_number"].name_hints or \
        any("account" in h for h in pack.types["account_number"].name_hints)

def test_load_unknown_raises():
    with pytest.raises(KeyError):
        load_domain("does_not_exist")

def test_generic_pack_is_empty():
    pack = load_domain("generic")
    assert pack.types == {}

def test_confidence_threshold_parses():
    # Add a stub yaml below before this passes
    pack = load_domain("_test_threshold")
    assert pack.types["ssn"].confidence_threshold == 0.85
```

- [ ] **Step 2: Add `generic.yaml` placeholder**

Path: `packages/typescript/goldencheck-types/domains/generic.yaml`

```yaml
description: "Generic — placeholder pack with no canonical types. Used when no domain matches."
types: {}
```

- [ ] **Step 3: Add a test fixture pack**

Path: `packages/python/goldencheck-types/tests/fixtures/_test_threshold.yaml`

```yaml
description: "Test fixture for confidence_threshold parsing"
types:
  ssn:
    name_hints: ["ssn"]
    value_signals: {}
    suppress: []
    confidence_threshold: 0.85
```

- [ ] **Step 4: Implement `loader.py`**

```python
# goldencheck_types/loader.py
from __future__ import annotations
from pathlib import Path
import yaml
from goldencheck_types.types import DomainPack, FieldSpec

def _domains_dir() -> Path:
    """Resolve domains/ at runtime.

    Order:
    1. Bundled with installed wheel: <pkg>/_domains/
    2. Source layout (monorepo dev): ../../typescript/goldencheck-types/domains/
    3. Test fixture override via env var: GOLDENCHECK_TYPES_TEST_DIR
    """
    import os
    if override := os.environ.get("GOLDENCHECK_TYPES_TEST_DIR"):
        return Path(override)
    here = Path(__file__).resolve().parent
    bundled = here / "_domains"
    if bundled.exists():
        return bundled
    source_layout = here.parent.parent.parent / "typescript" / "goldencheck-types" / "domains"
    if source_layout.exists():
        return source_layout
    raise FileNotFoundError(f"Could not locate domains/ near {here}")

def list_domains() -> list[str]:
    return sorted(p.stem for p in _domains_dir().glob("*.yaml"))

def load_domain(name: str) -> DomainPack:
    path = _domains_dir() / f"{name}.yaml"
    if not path.exists():
        raise KeyError(f"domain pack {name!r} not found in {_domains_dir()}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    types = {}
    for type_name, spec in (raw.get("types") or {}).items():
        threshold = spec.get("confidence_threshold")
        if threshold is not None and not (0.0 <= threshold <= 1.0):
            raise ValueError(
                f"confidence_threshold for {name}.{type_name} must be in [0,1], got {threshold!r}"
            )
        types[type_name] = FieldSpec(
            name_hints=spec.get("name_hints", []),
            value_signals=spec.get("value_signals", {}) or {},
            suppress=spec.get("suppress", []) or [],
            confidence_threshold=threshold,
            description=spec.get("description"),
        )
    return DomainPack(
        name=name,
        description=raw.get("description", ""),
        types=types,
    )
```

- [ ] **Step 5: Re-export and run tests**

Update `__init__.py` to export `load_domain`, `list_domains`. The threshold test needs the fixture dir on PYTHONPATH; configure via conftest.py:

```python
# packages/python/goldencheck-types/tests/conftest.py
import os
import pytest
from pathlib import Path

@pytest.fixture(autouse=True)
def _domain_dir(monkeypatch, tmp_path):
    """Compose real domains + test fixtures into one dir."""
    real = Path(__file__).resolve().parents[3] / "typescript/goldencheck-types/domains"
    fixtures = Path(__file__).resolve().parent / "fixtures"
    composite = tmp_path / "domains"
    composite.mkdir()
    for src in [real, fixtures]:
        if src.exists():
            for f in src.glob("*.yaml"):
                (composite / f.name).write_bytes(f.read_bytes())
    monkeypatch.setenv("GOLDENCHECK_TYPES_TEST_DIR", str(composite))
```

- [ ] **Step 6: Run tests, expect PASS, commit**

```bash
uv run pytest packages/python/goldencheck-types/tests -v
git add packages/python/goldencheck-types packages/typescript/goldencheck-types/domains/generic.yaml
git commit -m "feat(goldencheck-types): load_domain + list_domains + generic pack"
```

---

## Phase 2 — `goldencheck-types` TS bindings

Goal: mirror the Python bindings.

### Task 2.1: Scaffold TS source layout

**Files:**
- Create: `packages/typescript/goldencheck-types/src/index.ts`
- Create: `packages/typescript/goldencheck-types/src/types.ts`
- Create: `packages/typescript/goldencheck-types/src/loader.ts`
- Create: `packages/typescript/goldencheck-types/tsconfig.json` (if absent)
- Create: `packages/typescript/goldencheck-types/vitest.config.ts`
- Modify: `packages/typescript/goldencheck-types/package.json` (add deps + scripts)

- [ ] **Step 1: Verify current package.json**

```bash
cat packages/typescript/goldencheck-types/package.json
```

If it lacks `vitest`, `js-yaml`, `typescript`, add them via `npm --prefix packages/typescript/goldencheck-types install --save-dev vitest typescript @types/node @types/js-yaml` and `--save js-yaml`.

- [ ] **Step 2: Write `types.ts`**

```typescript
// src/types.ts
export interface FieldSpec {
  name_hints: string[];
  value_signals: Record<string, unknown>;
  suppress: string[];
  confidence_threshold?: number;
  description?: string;
}

export interface DomainPack {
  name: string;
  description: string;
  types: Record<string, FieldSpec>;
}

export interface FieldMapping {
  source_col: string;
  canonical: string | null;
  type: string;          // canonical type name or "unknown"
  confidence: number;
  evidence: Record<string, unknown>;  // InferMap-internal; do not depend on shape
}

export interface InferredSchema {
  domain: string;
  fields: Record<string, FieldMapping>;
  confidence: number;
}

export const isUnknown = (m: FieldMapping): boolean => m.type === "unknown";
export const unmappedCols = (s: InferredSchema): string[] =>
  Object.entries(s.fields).filter(([, m]) => isUnknown(m)).map(([k]) => k);
```

- [ ] **Step 3: Write `loader.ts`**

```typescript
// src/loader.ts
import * as fs from "fs";
import * as path from "path";
import * as yaml from "js-yaml";
import type { DomainPack, FieldSpec } from "./types";

function domainsDir(): string {
  if (process.env.GOLDENCHECK_TYPES_TEST_DIR) return process.env.GOLDENCHECK_TYPES_TEST_DIR;
  // sibling to src/ at install time
  return path.resolve(__dirname, "..", "domains");
}

export function listDomains(): string[] {
  return fs.readdirSync(domainsDir())
    .filter(f => f.endsWith(".yaml"))
    .map(f => f.replace(/\.yaml$/, ""))
    .sort();
}

export function loadDomain(name: string): DomainPack {
  const filePath = path.join(domainsDir(), `${name}.yaml`);
  if (!fs.existsSync(filePath)) {
    throw new Error(`domain pack '${name}' not found in ${domainsDir()}`);
  }
  const raw = yaml.load(fs.readFileSync(filePath, "utf-8")) as any || {};
  const types: Record<string, FieldSpec> = {};
  for (const [typeName, spec] of Object.entries((raw.types ?? {}) as Record<string, any>)) {
    const threshold = spec.confidence_threshold;
    if (threshold !== undefined && (threshold < 0 || threshold > 1)) {
      throw new Error(`confidence_threshold for ${name}.${typeName} must be in [0,1], got ${threshold}`);
    }
    types[typeName] = {
      name_hints: spec.name_hints ?? [],
      value_signals: spec.value_signals ?? {},
      suppress: spec.suppress ?? [],
      confidence_threshold: threshold,
      description: spec.description,
    };
  }
  return { name, description: raw.description ?? "", types };
}
```

- [ ] **Step 4: `index.ts` exports**

```typescript
// src/index.ts
export { loadDomain, listDomains } from "./loader";
export type { FieldSpec, DomainPack, FieldMapping, InferredSchema } from "./types";
export { isUnknown, unmappedCols } from "./types";
```

- [ ] **Step 5: Vitest test**

```typescript
// tests/loader.test.ts
import { describe, it, expect } from "vitest";
import { loadDomain, listDomains } from "../src/loader";

describe("loadDomain", () => {
  it("lists includes finance, healthcare, ecommerce, generic", () => {
    const d = listDomains();
    expect(d).toContain("finance");
    expect(d).toContain("healthcare");
    expect(d).toContain("ecommerce");
    expect(d).toContain("generic");
  });

  it("loads finance pack", () => {
    const pack = loadDomain("finance");
    expect(pack.name).toBe("finance");
    expect(pack.types["account_number"]).toBeDefined();
  });

  it("throws on unknown pack", () => {
    expect(() => loadDomain("does_not_exist")).toThrow();
  });

  it("generic pack is empty", () => {
    const pack = loadDomain("generic");
    expect(pack.types).toEqual({});
  });
});
```

- [ ] **Step 6: Run, expect PASS, commit**

```bash
npm --prefix packages/typescript/goldencheck-types test
git add packages/typescript/goldencheck-types
git commit -m "feat(goldencheck-types): TS bindings (loadDomain, types)"
```

---

## Phase 3 — InferMap Python: DomainPackTarget + soft mode + detect_domain

### Task 3.1: `DomainPackTarget` adapter

**Files:**
- Create: `packages/python/infermap/infermap/domain_pack.py`
- Create: `packages/python/infermap/tests/test_domain_pack.py`
- Modify: `packages/python/infermap/infermap/__init__.py`
- Modify: `packages/python/infermap/pyproject.toml` (add `goldencheck-types` to deps)

- [ ] **Step 1: Failing test**

```python
# tests/test_domain_pack.py
import pandas as pd
from goldencheck_types import load_domain
from infermap import map as infermap_map
from infermap.domain_pack import DomainPackTarget

def test_map_with_domain_pack_target():
    df = pd.DataFrame({
        "account_number": ["1234", "5678"],
        "currency": ["USD", "EUR"],
    })
    pack = load_domain("finance")
    result = infermap_map(df, DomainPackTarget(pack))
    # Should map at least one column to a canonical type
    canonical_types = {f.canonical for f in result.fields if f.canonical}
    assert canonical_types & {"account_number", "currency_code"}
```

- [ ] **Step 2: Implement `domain_pack.py`**

The adapter needs to convert `DomainPack` into the `SchemaInfo`/`FieldInfo` shape that the existing engine consumes. Each canonical type becomes a target field; the `name_hints` populate sample values that the existing `FuzzyNameScorer` will fire on.

```python
# infermap/domain_pack.py
from __future__ import annotations
from goldencheck_types import DomainPack
from infermap.types import FieldInfo, SchemaInfo

class DomainPackTarget:
    """Wraps a goldencheck-types DomainPack as an InferMap target schema."""
    def __init__(self, pack: DomainPack):
        self.pack = pack

    def to_schema_info(self) -> SchemaInfo:
        fields = []
        for type_name, spec in self.pack.types.items():
            fields.append(FieldInfo(
                name=type_name,
                dtype="string",
                sample_values=list(spec.name_hints),  # used by FuzzyNameScorer
                metadata={
                    "value_signals": spec.value_signals,
                    "confidence_threshold": spec.confidence_threshold,
                },
            ))
        return SchemaInfo(fields=fields, source_name=f"domain:{self.pack.name}")
```

- [ ] **Step 3: Wire DomainPackTarget into MapEngine.map**

Locate `MapEngine.map` in `infermap/engine.py`. It branches on target type (path, df, dict). Add a branch:

```python
# in MapEngine.map(...) target dispatch
from infermap.domain_pack import DomainPackTarget
if isinstance(target, DomainPackTarget):
    target_schema = target.to_schema_info()
    return self._map_schemas(source_schema, target_schema, ...)
```

Read the existing branch structure first; insert the new isinstance check above the catch-all "unknown target" error.

- [ ] **Step 4: Add `goldencheck-types` to infermap deps**

In `packages/python/infermap/pyproject.toml`:
```toml
dependencies = [
    # ... existing ...
    "goldencheck-types",
]
```

- [ ] **Step 5: Re-export and run**

```python
# infermap/__init__.py
from infermap.domain_pack import DomainPackTarget
```

```bash
uv sync
uv run pytest packages/python/infermap/tests/test_domain_pack.py -v
git add ...
git commit -m "feat(infermap): DomainPackTarget adapter for goldencheck-types packs"
```

### Task 3.2: `soft=True` mode in MapEngine

**Files:**
- Modify: `packages/python/infermap/infermap/engine.py`
- Create: `packages/python/infermap/tests/test_soft_mode.py`

- [ ] **Step 1: Failing test**

```python
# tests/test_soft_mode.py
import pandas as pd
from goldencheck_types import load_domain
from infermap import map as infermap_map
from infermap.domain_pack import DomainPackTarget

def test_soft_mode_marks_low_confidence_unknown():
    df = pd.DataFrame({
        "account_number": ["1234"],
        "totally_random_xyz": ["zzz"],
    })
    pack = load_domain("finance")
    result = infermap_map(df, DomainPackTarget(pack), soft=True)
    by_col = {f.source_col: f for f in result.fields}
    assert by_col["totally_random_xyz"].type == "unknown"
    assert by_col["totally_random_xyz"].canonical is None
    assert by_col["account_number"].canonical == "account_number"

def test_soft_mode_default_off_for_legacy():
    # Without soft=True, legacy hard-fail behavior on unmappable
    # (or whatever the existing default is — confirm via passing test
    # against current engine behavior, no behavior change for default).
    pass
```

- [ ] **Step 2: Inspect real `MapResult` shape**

Before writing code, read `infermap/types.py` to confirm the actual fields on `MapResult` and InferMap's internal `FieldMapping`. Use `dataclasses.replace(...)` for any clones — do not reflect via `vars()` since frozen / computed fields will break. If InferMap's internal `FieldMapping` differs from `goldencheck_types.FieldMapping`, the boundary converter is its own deliverable (Step 3 below).

- [ ] **Step 3: Build a boundary converter (explicit deliverable)**

Path: `packages/python/infermap/infermap/_gct_bridge.py`

```python
# Converts InferMap-internal FieldMapping ↔ goldencheck_types.FieldMapping.
# Lives in infermap so InferMap is the only package that knows both shapes.
from infermap.types import FieldMapping as _InternalFM
from goldencheck_types import FieldMapping as _PublicFM

def to_public(internal: _InternalFM) -> _PublicFM:
    return _PublicFM(
        source_col=internal.source_name,         # confirm exact attr name
        canonical=internal.target_name,
        type=internal.target_name or "unknown",
        confidence=internal.score,
        evidence=internal.metadata or {},
    )
```

Adjust attribute names after reading `infermap/types.py`. The public API of `infermap.map(...)` returns `MapResult` whose `fields` are `goldencheck_types.FieldMapping` instances. The internal scoring code keeps using `_InternalFM`; conversion happens at the public boundary.

- [ ] **Step 4: Implement soft mode**

```python
import dataclasses
from infermap.domain_pack import DomainPackTarget

def map(self, source, target, *, soft: bool = False, **kwargs) -> MapResult:
    result = self._map_internal(source, target, **kwargs)
    # Convert internal mappings to public goldencheck_types.FieldMapping at boundary
    public_fields = [to_public(fm) for fm in result.fields]
    result = dataclasses.replace(result, fields=public_fields)
    if soft:
        result = self._apply_soft(result, target)
    return result

def _apply_soft(self, result: "MapResult", target) -> "MapResult":
    if isinstance(target, DomainPackTarget):
        thresholds = {
            t: (spec.confidence_threshold if spec.confidence_threshold is not None
                else self.default_threshold)
            for t, spec in target.pack.types.items()
        }
    else:
        thresholds = {}
    new_fields = []
    for fm in result.fields:
        threshold = thresholds.get(fm.canonical, self.default_threshold)
        if fm.confidence < threshold:
            fm = dataclasses.replace(fm, canonical=None, type="unknown")
        new_fields.append(fm)
    return dataclasses.replace(result, fields=new_fields)
```

If `MapResult` is frozen, `dataclasses.replace` still works. If it's not a dataclass, fall back to constructing a new one explicitly with all known fields.

- [ ] **Step 3: Run, expect PASS, commit**

```bash
uv run pytest packages/python/infermap/tests/test_soft_mode.py -v
git add packages/python/infermap
git commit -m "feat(infermap): soft mode marks low-confidence columns unknown"
```

### Task 3.3: `detect_domain`

**Files:**
- Create: `packages/python/infermap/infermap/detect.py`
- Create: `packages/python/infermap/tests/test_detect.py`
- Modify: `packages/python/infermap/infermap/__init__.py`

- [ ] **Step 1: Failing test**

```python
# tests/test_detect.py
import pandas as pd
from infermap import detect_domain

def test_detect_finance():
    df = pd.DataFrame(columns=["account_number", "routing", "currency"])
    assert detect_domain(df) == "finance"

def test_detect_healthcare():
    df = pd.DataFrame(columns=["patient_id", "diagnosis", "icd10"])
    assert detect_domain(df) == "healthcare"

def test_detect_no_match_returns_none():
    df = pd.DataFrame(columns=["foo", "bar", "baz"])
    assert detect_domain(df) is None
```

- [ ] **Step 2: Implement**

```python
# infermap/detect.py
from __future__ import annotations
import pandas as pd
from goldencheck_types import list_domains, load_domain

DEFAULT_MIN_SCORE = 0.3

def detect_domain(df: pd.DataFrame, candidates: list[str] | None = None,
                  min_score: float = DEFAULT_MIN_SCORE) -> str | None:
    cols_lc = [c.lower() for c in df.columns]
    domains = candidates or [d for d in list_domains() if d != "generic"]
    best, best_score = None, 0.0
    for d in domains:
        pack = load_domain(d)
        all_hints = {h.lower() for spec in pack.types.values() for h in spec.name_hints}
        if not all_hints:
            continue
        hits = sum(1 for c in cols_lc if any(h in c or c in h for h in all_hints))
        score = hits / max(len(cols_lc), 1)
        if score > best_score:
            best, best_score = d, score
    return best if best_score >= min_score else None
```

- [ ] **Step 3: Re-export, run, commit**

```python
# infermap/__init__.py
from infermap.detect import detect_domain
```

```bash
uv run pytest packages/python/infermap/tests/test_detect.py -v
git add packages/python/infermap
git commit -m "feat(infermap): detect_domain helper for auto-detection"
```

---

## Phase 4 — InferMap TS: same surface

### Task 4.0: Confirm TS public API names

Before writing tests in 4.1–4.3, read `packages/typescript/infermap/src/index.ts` to confirm the exact name of the public mapping function (`mapDataFrame`, `map`, etc.). The placeholder names `mapDataFrame` / `infermap.map` in this plan must be replaced with whatever the package actually exports. Update tests and examples accordingly. Same check applies to `goldencheck-js` in Phase 6.

### Task 4.1: `DomainPackTarget` (TS)

**Files:**
- Create: `packages/typescript/infermap/src/core/domainPack.ts`
- Create: `packages/typescript/infermap/tests/domainPack.test.ts`
- Modify: `packages/typescript/infermap/src/index.ts` (add export)
- Modify: `packages/typescript/infermap/package.json` (add dependency on `@golden-suite/goldencheck-types` or relative path)

- [ ] **Step 1: Add goldencheck-types dep**

Since the monorepo doesn't use real npm workspaces, add by relative path:

```json
"dependencies": {
  "goldencheck-types": "file:../goldencheck-types"
}
```

Then `npm --prefix packages/typescript/infermap install`.

- [ ] **Step 2: Failing test, mirroring Python test**

```typescript
// tests/domainPack.test.ts
import { describe, it, expect } from "vitest";
import { loadDomain } from "goldencheck-types";
import { mapDataFrame } from "../src";   // existing public API
import { DomainPackTarget } from "../src/core/domainPack";

describe("DomainPackTarget", () => {
  it("maps account_number column to finance.account_number", async () => {
    const df = [
      { account_number: "1234", currency: "USD" },
      { account_number: "5678", currency: "EUR" },
    ];
    const pack = loadDomain("finance");
    const result = await mapDataFrame(df, new DomainPackTarget(pack));
    const canonicals = new Set(result.fields.map(f => f.canonical).filter(Boolean));
    expect(canonicals.has("account_number") || canonicals.has("currency_code")).toBe(true);
  });
});
```

- [ ] **Step 3: Implement `domainPack.ts`**

Mirror Python: convert `DomainPack` to whatever target shape the TS engine consumes.

```typescript
// src/core/domainPack.ts
import type { DomainPack } from "goldencheck-types";

export class DomainPackTarget {
  constructor(public readonly pack: DomainPack) {}

  toSchemaInfo() {
    return {
      source_name: `domain:${this.pack.name}`,
      fields: Object.entries(this.pack.types).map(([type_name, spec]) => ({
        name: type_name,
        dtype: "string",
        sample_values: spec.name_hints,
        metadata: {
          value_signals: spec.value_signals,
          confidence_threshold: spec.confidence_threshold,
        },
      })),
    };
  }
}
```

- [ ] **Step 4: Wire into engine + commit**

Locate the TS equivalent of `MapEngine.map`'s target dispatch (likely in `src/core/`). Add the `DomainPackTarget` branch. Re-export from `index.ts`.

```bash
npm --prefix packages/typescript/infermap test
git add packages/typescript/infermap
git commit -m "feat(infermap-js): DomainPackTarget adapter"
```

### Task 4.2: `soft` mode (TS)

**Files:**
- Modify: `packages/typescript/infermap/src/core/engine.ts` (or equivalent)
- Create: `packages/typescript/infermap/tests/softMode.test.ts`

Mirror Python Task 3.2. Same logic, TypeScript-flavored.

- [ ] **Step 1: Failing test** (same shape as Python's `test_soft_mode_marks_low_confidence_unknown`)
- [ ] **Step 2: Add `soft?: boolean` parameter to map function**
- [ ] **Step 3: Apply threshold post-process; mark below-threshold columns `type: "unknown"`, `canonical: null`**
- [ ] **Step 4: Run + commit**

```bash
npm --prefix packages/typescript/infermap test
git commit -m "feat(infermap-js): soft mode for low-confidence columns"
```

### Task 4.3: `detectDomain` (TS)

**Files:**
- Create: `packages/typescript/infermap/src/core/detect.ts`
- Create: `packages/typescript/infermap/tests/detect.test.ts`

Mirror Python Task 3.3.

- [ ] **Step 1: Failing test** (same fixture columns as Python)
- [ ] **Step 2: Implement using `loadDomain` + `listDomains`**
- [ ] **Step 3: Re-export from `index.ts`**
- [ ] **Step 4: Run + commit**

```bash
npm --prefix packages/typescript/infermap test
git commit -m "feat(infermap-js): detectDomain helper"
```

---

## Phase 5 — GoldenCheck Python: schema-aware mode

### Task 5.1: Accept `schema` parameter in `scan_file` and `validate_file`

**Files:**
- Modify: `packages/python/goldencheck/goldencheck/engine/scanner.py`
- Modify: `packages/python/goldencheck/goldencheck/engine/validator.py`
- Modify: `packages/python/goldencheck/goldencheck/semantic/classifier.py`
- Modify: `packages/python/goldencheck/pyproject.toml` (add `goldencheck-types` dep)
- Create: `packages/python/goldencheck/tests/test_schema_aware.py`

- [ ] **Step 1: Add dep + failing test**

```python
# tests/test_schema_aware.py
import pandas as pd
from goldencheck import scan_file
from goldencheck_types import InferredSchema, FieldMapping

def _make_schema(domain="finance", **fields):
    fm = {col: FieldMapping(col, t, t, 0.9, {}) for col, t in fields.items()}
    return InferredSchema(domain=domain, fields=fm, confidence=0.9)

def test_scan_routes_rules_by_canonical_type(tmp_path):
    p = tmp_path / "f.csv"
    p.write_text("account_number,currency\n1234,USD\n")
    schema = _make_schema(account_number="account_number", currency="currency_code")
    result = scan_file(str(p), schema=schema)
    # account_number type has suppress: ["cardinality", ...] in finance pack
    codes = {f.code for f in result.findings}
    assert "cardinality" not in codes  # suppressed for account_number

def test_unknown_column_emits_finding(tmp_path):
    p = tmp_path / "f.csv"
    p.write_text("account_number,zzz_unknown\n1234,abc\n")
    schema = _make_schema(account_number="account_number", zzz_unknown="unknown")
    result = scan_file(str(p), schema=schema)
    codes = {f.code for f in result.findings}
    assert "unmapped_column" in codes
    unmapped = [f for f in result.findings if f.code == "unmapped_column"]
    assert any("zzz_unknown" in (f.column or "") for f in unmapped)

def test_legacy_mode_still_works(tmp_path):
    p = tmp_path / "f.csv"
    p.write_text("a,b\n1,2\n")
    result = scan_file(str(p))  # no schema arg
    # Should not raise; should not emit unmapped_column findings
    codes = {f.code for f in result.findings}
    assert "unmapped_column" not in codes
```

- [ ] **Step 2: Add `schema` parameter to `scan_file`**

```python
# scanner.py (signature change)
def scan_file(path, *, schema: "InferredSchema | None" = None, ...):
    ...
    columns_to_classify = []
    semantic_types = {}
    if schema is not None:
        for col, mapping in schema.fields.items():
            if mapping.type != "unknown":
                semantic_types[col] = mapping.type
            else:
                columns_to_classify.append(col)
    else:
        columns_to_classify = list(df.columns)

    if columns_to_classify:
        # existing classify_columns path for unknown / legacy columns
        heuristic = classify_columns(df[columns_to_classify], ...)
        semantic_types.update(heuristic)

    # rule routing now uses semantic_types (canonical) instead of header-only
    ...
```

- [ ] **Step 3: Emit `unmapped_column` finding**

Add to `models/finding.py` (registered code list), then in `scan_file`:

```python
if schema is not None:
    for col in schema.unmapped:
        findings.append(Finding(
            severity=Severity.INFO,
            code="unmapped_column",
            column=col,
            message=(
                f"Column {col!r} could not be typed against domain pack "
                f"{schema.domain!r}. Consider adding name_hints to the pack."
            ),
        ))
```

- [ ] **Step 4: Mirror in `validate_file`**

Same pattern: accept `schema=`, route rules by `mapping.type` for known columns, fall back to current logic for unknowns, emit `unmapped_column` for the unmapped list.

- [ ] **Step 5: Add `goldencheck-types` to deps**

In `packages/python/goldencheck/pyproject.toml`:
```toml
dependencies = [
    # ... existing ...
    "goldencheck-types",
]
```

- [ ] **Step 6: Run, expect PASS, commit**

```bash
uv sync
uv run pytest packages/python/goldencheck/tests/test_schema_aware.py -v
git add packages/python/goldencheck
git commit -m "feat(goldencheck): accept InferredSchema, emit unmapped_column finding"
```

---

## Phase 6 — GoldenCheck TS: same surface

### Task 6.1: Schema-aware mode (TS)

**Files:**
- Modify: `packages/typescript/goldencheck/src/core/...` (whichever file holds the equivalent of scan_file)
- Create: `packages/typescript/goldencheck/tests/schemaAware.test.ts`

Mirror Phase 5 step-by-step in TypeScript:

- [ ] **Step 1: Add `goldencheck-types` dep** (`file:../goldencheck-types`)
- [ ] **Step 2: Write failing tests** mirroring `test_scan_routes_rules_by_canonical_type`, `test_unknown_column_emits_finding`, `test_legacy_mode_still_works`
- [ ] **Step 3: Add optional `schema?: InferredSchema` parameter to public scan/validate function**
- [ ] **Step 4: Route rules by canonical type when schema is present; fall back to header heuristics for `unknown`-typed columns**
- [ ] **Step 5: Emit `unmapped_column` findings for `schema.unmapped`**
- [ ] **Step 6: Run + commit**

```bash
npm --prefix packages/typescript/goldencheck test
git commit -m "feat(goldencheck-js): schema-aware mode + unmapped_column finding"
```

---

## Phase 7 — GoldenPipe: `infer_schema` stage + CLI flags

### Task 7.1: Register `infer_schema` stage

**Files:**
- Create: `packages/python/goldenpipe/goldenpipe/stages/infer_schema.py`
- Modify: `packages/python/goldenpipe/goldenpipe/engine/registry.py`
- Modify: `packages/python/goldenpipe/pyproject.toml` (add deps: `goldencheck-types`, `infermap`)
- Create: `packages/python/goldenpipe/tests/test_infer_schema_stage.py`

- [ ] **Step 1: Failing test**

```python
# tests/test_infer_schema_stage.py
import pandas as pd
from goldenpipe import run_df

def test_infer_schema_runs_first(tmp_path):
    df = pd.DataFrame({"account_number": ["1"], "currency": ["USD"]})
    result = run_df(df, domain="finance")
    assert result.context.inferred is not None
    assert result.context.inferred.domain == "finance"

def test_no_infer_skips_stage(tmp_path):
    df = pd.DataFrame({"account_number": ["1"]})
    result = run_df(df, no_infer=True)
    assert result.context.inferred is None

def test_user_schema_skips_stage(tmp_path):
    from goldencheck_types import InferredSchema, FieldMapping
    df = pd.DataFrame({"x": ["1"]})
    user_schema = InferredSchema(
        domain="user",
        fields={"x": FieldMapping("x", "ssn", "ssn", 1.0, {})},
        confidence=1.0,
    )
    result = run_df(df, schema=user_schema)
    assert result.context.inferred is user_schema  # passed through
```

- [ ] **Step 2: Implement the stage**

`infermap.map(...)` returns a `MapResult`, not an `InferredSchema`. The stage converts it into the `goldencheck_types.InferredSchema` dataclass that downstream consumers expect.

```python
# goldenpipe/stages/infer_schema.py
from goldenpipe import stage
import infermap
from goldencheck_types import load_domain, InferredSchema

def _to_inferred_schema(result, domain: str) -> InferredSchema:
    fields = {fm.source_col: fm for fm in result.fields}
    confidence = min((fm.confidence for fm in result.fields), default=0.0)
    return InferredSchema(domain=domain, fields=fields, confidence=confidence)

@stage(name="infer_schema", order=0)
def infer_schema(ctx):
    if ctx.config.get("no_infer"):
        ctx.inferred = None
        return ctx
    if ctx.config.get("schema") is not None:
        ctx.inferred = ctx.config["schema"]   # already an InferredSchema
        return ctx
    domain = ctx.config.get("domain") or infermap.detect_domain(ctx.df) or "generic"
    pack = load_domain(domain)
    result = infermap.map(ctx.df, infermap.DomainPackTarget(pack), soft=True)
    ctx.inferred = _to_inferred_schema(result, domain)
    return ctx
```

Where `ctx.inferred` is a new attribute on `PipeContext` typed as `InferredSchema | None`. Update `goldenpipe/models/context.py` to add it (default `None`).

- [ ] **Step 3: Make downstream stages consume `ctx.inferred`**

Update the existing GoldenCheck stage in goldenpipe to pass `schema=ctx.inferred` to `scan_file`. Same for any GoldenMatch stage that benefits from typed columns.

- [ ] **Step 4: Update `_api.py` `run_df` / `run` signatures**

```python
def run_df(df, *, domain=None, no_infer=False, schema=None, **kw):
    cfg = {**kw, "domain": domain, "no_infer": no_infer, "schema": schema}
    ...
```

- [ ] **Step 5: Run + commit**

```bash
uv sync
uv run pytest packages/python/goldenpipe/tests/test_infer_schema_stage.py -v
git add packages/python/goldenpipe
git commit -m "feat(goldenpipe): infer_schema stage runs InferMap as stage 0"
```

### Task 7.2: CLI flags + precedence enforcement

**Files:**
- Modify: `packages/python/goldenpipe/goldenpipe/cli/__init__.py`
- Create: `packages/python/goldenpipe/tests/test_cli_flags.py`

- [ ] **Step 1: Failing test for precedence**

```python
# tests/test_cli_flags.py
import subprocess
import sys

def _cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "goldenpipe", *args],
        capture_output=True, text=True,
    )

def test_conflict_schema_and_domain_errors():
    r = _cli("run", "x.csv", "--schema", "s.yaml", "--domain", "finance")
    assert r.returncode == 2
    assert "conflict" in (r.stderr + r.stdout).lower()

def test_conflict_no_infer_and_domain_errors():
    r = _cli("run", "x.csv", "--no-infer", "--domain", "finance")
    assert r.returncode == 2

def test_conflict_no_infer_and_schema_errors():
    r = _cli("run", "x.csv", "--no-infer", "--schema", "s.yaml")
    assert r.returncode == 2
```

- [ ] **Step 2: Add the three flags + precedence check**

```python
# in CLI parser
parser.add_argument("--domain", default=None)
parser.add_argument("--no-infer", action="store_true")
parser.add_argument("--schema", default=None)

args = parser.parse_args()

# Precedence enforcement: at most one of {schema, no_infer, domain} can be set
exclusive_count = sum([
    args.schema is not None,
    args.no_infer,
    args.domain is not None,
])
if exclusive_count > 1:
    parser.error(
        "conflict: --schema, --no-infer, --domain are mutually exclusive. "
        "Precedence: --schema > --no-infer > --domain > auto-detect."
    )
```

- [ ] **Step 3: Run + commit**

```bash
uv run pytest packages/python/goldenpipe/tests/test_cli_flags.py -v
git add packages/python/goldenpipe
git commit -m "feat(goldenpipe): --domain / --no-infer / --schema flags with precedence"
```

---

## Phase 8 — Integration + parity tests

### Task 8.1: End-to-end fixtures and integration test

**Files:**
- Create: `tests/fixtures/finance_clean.csv`
- Create: `tests/fixtures/healthcare_clean.csv`
- Create: `tests/fixtures/mixed_unknown.csv`
- Create: `tests/integration/test_pipe_end_to_end.py`

- [ ] **Step 1: Write fixtures**

```
# finance_clean.csv
account_number,routing_number,currency,amount
1234567890,021000021,USD,99.99
9876543210,021000021,EUR,42.00
```

```
# healthcare_clean.csv
patient_id,diagnosis,icd10,visit_date
P001,Hypertension,I10,2026-01-05
P002,Asthma,J45,2026-02-12
```

```
# mixed_unknown.csv  (has at least one column no pack will type)
account_number,xyz_internal_code,currency
1234,Q9-ALPHA,USD
5678,R7-BETA,EUR
```

- [ ] **Step 2: Integration test**

```python
# tests/integration/test_pipe_end_to_end.py
import pandas as pd
import pytest
from goldenpipe import run

FIXTURES = "tests/fixtures"

def test_finance_clean_auto_detects():
    result = run(f"{FIXTURES}/finance_clean.csv")
    assert result.context.inferred.domain == "finance"
    assert result.context.inferred.confidence > 0
    # Should have no unmapped_column findings on a clean fixture
    codes = {f.code for f in result.check.findings}
    assert "unmapped_column" not in codes

def test_healthcare_clean_auto_detects():
    result = run(f"{FIXTURES}/healthcare_clean.csv")
    assert result.context.inferred.domain == "healthcare"

def test_mixed_unknown_emits_finding_but_runs():
    result = run(f"{FIXTURES}/mixed_unknown.csv")
    codes = {f.code for f in result.check.findings}
    assert "unmapped_column" in codes
    # Pipe still completes — match step ran
    assert result.match is not None
```

- [ ] **Step 3: Snapshot the InferredSchema for regression**

Use `pytest`'s `tmp_path` + simple JSON dump for first run, compare on subsequent runs.

- [ ] **Step 4: Run + commit**

```bash
uv run pytest tests/integration/test_pipe_end_to_end.py -v
git add tests/integration tests/fixtures
git commit -m "test: integration tests for pipe end-to-end with InferMap"
```

### Task 8.2: Python ↔ TS parity test

**Files:**
- Create: `tests/parity/test_python_ts_parity.py`
- Create: `tests/parity/run_ts_parity.ts` (helper invoked from python)

- [ ] **Step 1: Write a TS helper that consumes the same fixture**

```typescript
// tests/parity/run_ts_parity.ts
import { loadDomain } from "goldencheck-types";
import { mapDataFrame } from "infermap";
import { DomainPackTarget } from "infermap";
import * as fs from "fs";
import * as path from "path";
import { parse as csvParse } from "csv-parse/sync";

const fixture = process.argv[2];
const rows = csvParse(fs.readFileSync(fixture, "utf-8"), { columns: true });
const pack = loadDomain("finance");
const result = await mapDataFrame(rows, new DomainPackTarget(pack), { soft: true });
process.stdout.write(JSON.stringify({
  domain: pack.name,
  fields: Object.fromEntries(result.fields.map((f: any) => [
    f.source_col, { canonical: f.canonical, type: f.type }
  ])),
}));
```

- [ ] **Step 2: Python harness**

```python
# tests/parity/test_python_ts_parity.py
import json
import subprocess
import pandas as pd
from goldencheck_types import load_domain
from infermap import map as infermap_map, DomainPackTarget

FIXTURE = "tests/fixtures/finance_clean.csv"

def _run_ts():
    out = subprocess.check_output(
        ["npx", "tsx", "tests/parity/run_ts_parity.ts", FIXTURE],
        text=True,
    )
    return json.loads(out)

def test_python_ts_parity_finance():
    df = pd.read_csv(FIXTURE)
    pack = load_domain("finance")
    py = infermap_map(df, DomainPackTarget(pack), soft=True)
    py_typed = {f.source_col: {"canonical": f.canonical, "type": f.type} for f in py.fields}

    ts = _run_ts()
    assert py_typed == ts["fields"], (py_typed, ts["fields"])
```

- [ ] **Step 3: Run + commit**

```bash
uv run pytest tests/parity/test_python_ts_parity.py -v
git add tests/parity
git commit -m "test: Python ↔ TS parity harness for InferMap + goldencheck-types"
```

---

## Phase 9 — Push via feature branch + PR

This change touches five packages and adds a runtime dep edge (pipe → infermap). Land it on a branch and open a PR rather than pushing straight to `main`, so CI runs end-to-end before merging.

### Task 9.1: Branch + push + PR

- [ ] **Step 1: Branch from main at the start of Phase 1**

(Done at Phase 1 commit time, recorded here for clarity.)
```bash
cd /d/mr/cleanup-staging
git checkout -b feat/infermap-handoff
```

All Phase 1–8 commits land on this branch.

- [ ] **Step 2: Run full test suite locally**

```bash
just test 2>&1 | tee /tmp/full-test.log
tail -40 /tmp/full-test.log
```

Expected: all new tests pass; pre-existing failures unchanged.

- [ ] **Step 3: Push branch + open PR**

```bash
git push -u origin feat/infermap-handoff
gh pr create --title "feat: InferMap → GoldenCheck handoff (stage 0 + shared type registry)" \
  --body "Implements docs/superpowers/specs/2026-05-01-infermap-goldencheck-handoff-design.md."
```

Wait for CI to pass before merge. If the rust/dbt/action jobs are unaffected, only the python and typescript jobs need to be green.

- [ ] **Step 4: Merge via squash or merge commit (user choice)**

Do NOT force-push to main. Use the GitHub merge UI or `gh pr merge`.

---

## Out of scope (follow-ups)

- TS port of `goldenpipe` so the orchestration is parity-complete.
- Multi-source `align()` — separate spec.
- Domain-pack auto-learning from `unmapped_column` findings.
- Per-run threshold tuning UI.
- Migrating GoldenMatch to consume `InferredSchema` (currently the spec only requires it to *accept* it; matching logic improvements come later).
