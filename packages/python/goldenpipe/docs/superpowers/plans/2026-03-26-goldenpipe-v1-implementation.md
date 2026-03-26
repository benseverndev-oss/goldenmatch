# GoldenPipe v1.0 Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite GoldenPipe from a hardcoded Golden Suite wrapper into a pluggable pipeline framework with stage discovery, wiring validation, adaptive routing, and five interfaces (CLI, TUI, MCP, A2A, REST).

**Architecture:** Layered engine (Registry -> Resolver -> Runner -> Router -> Reporter) with typed PipeContext flowing through stages. Stages implement a Protocol or use a `@stage` decorator. Golden Suite tools ship as built-in adapters with lazy imports.

**Tech Stack:** Python 3.11+, Polars, Pydantic, PyYAML, Typer, Rich, Textual, FastAPI, aiohttp, MCP SDK

**Spec:** `docs/superpowers/specs/2026-03-26-goldenpipe-v1-framework-design.md`

**Working directory:** `D:\show_case\goldenpipe`

---

## File Map

### New files to create

| File | Responsibility |
|------|---------------|
| `goldenpipe/models/__init__.py` | Re-export all model types |
| `goldenpipe/models/context.py` | PipeContext, StageResult, Decision, StageStatus, PipeStatus, PipeResult |
| `goldenpipe/models/stage.py` | StageInfo, Stage Protocol, @stage decorator |
| `goldenpipe/models/config.py` | StageSpec, PipelineConfig (Pydantic) |
| `goldenpipe/engine/__init__.py` | Re-export engine classes |
| `goldenpipe/engine/registry.py` | StageRegistry — discover + register + get stages |
| `goldenpipe/engine/resolver.py` | Resolver — build ExecutionPlan, validate wiring |
| `goldenpipe/engine/runner.py` | Runner — execute stages, handle errors + decisions |
| `goldenpipe/engine/router.py` | Router — apply Decision objects to execution plan |
| `goldenpipe/engine/reporter.py` | Reporter — build PipeResult from PipeContext |
| `goldenpipe/adapters/__init__.py` | HAS_CHECK/HAS_FLOW/HAS_MATCH guards, adapter discovery |
| `goldenpipe/adapters/check.py` | ScanStage — wraps goldencheck.scan_file() |
| `goldenpipe/adapters/flow.py` | TransformStage — wraps goldenflow.transform_df() |
| `goldenpipe/adapters/match.py` | DedupeStage — wraps goldenmatch.dedupe_df() |
| `goldenpipe/config/loader.py` | load_config() — YAML loading + normalization |
| `goldenpipe/_api.py` | run(), run_df(), run_stages() — Python API |
| `goldenpipe/api/__init__.py` | Empty init |
| `goldenpipe/api/server.py` | FastAPI REST endpoints |
| `goldenpipe/mcp/__init__.py` | Empty init |
| `goldenpipe/mcp/server.py` | MCP server with 4 tools |
| `goldenpipe/a2a/__init__.py` | Empty init |
| `goldenpipe/a2a/server.py` | A2A aiohttp server on port 8250 |
| `goldenpipe/tui/__init__.py` | Empty init |
| `goldenpipe/tui/app.py` | Textual TUI with 4 tabs |
| `tests/conftest.py` | Shared fixtures: sample_csv, mock stages, mock registry |
| `tests/test_models.py` | Tests for PipeContext, StageResult, Decision, PipeResult |
| `tests/test_stage.py` | Tests for StageInfo, Stage Protocol, @stage decorator |
| `tests/test_config.py` | Tests for StageSpec, PipelineConfig, loader |
| `tests/test_registry.py` | Tests for StageRegistry discovery |
| `tests/test_resolver.py` | Tests for wiring validation, ExecutionPlan |
| `tests/test_runner.py` | Tests for stage execution, error handling |
| `tests/test_router.py` | Tests for Decision application |
| `tests/test_reporter.py` | Tests for PipeResult construction |
| `tests/test_adapters.py` | Tests for Golden Suite adapters (mocked) |
| `tests/test_api.py` | Tests for Python API |
| `tests/test_cli.py` | Tests for CLI commands |
| `tests/test_rest.py` | Tests for FastAPI endpoints |
| `tests/test_mcp.py` | Tests for MCP tools |
| `tests/test_a2a.py` | Tests for A2A server |
| `tests/test_tui.py` | Tests for Textual TUI |

### Files to modify

| File | Change |
|------|--------|
| `pyproject.toml` | Version bump, new deps, optional extras, entry points |
| `goldenpipe/__init__.py` | New exports for v1.0 API |
| `goldenpipe/pipeline.py` | Rewrite to thin wrapper over engine |
| `goldenpipe/decisions.py` | Evolve to new Decision dataclass + named functions |
| `goldenpipe/cli/main.py` | Add validate, stages, init, serve, mcp-serve, agent-serve, interactive commands |
| `tests/test_decisions.py` | Update to new Decision model |
| `tests/test_pipeline.py` | Update to new Pipeline/PipeResult API |

### Files to delete

| File | Reason |
|------|--------|
| `goldenpipe/config/__init__.py` | Replaced by `goldenpipe/config/loader.py` (keep dir, replace empty init) |

---

## Task 1: Update pyproject.toml

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Update pyproject.toml**

```toml
[project]
name = "goldenpipe"
version = "1.0.0"
description = "Pluggable pipeline framework for data quality workflows"
readme = "README.md"
license = {text = "MIT"}
requires-python = ">=3.11"
classifiers = [
    "Development Status :: 4 - Beta",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Programming Language :: Python :: 3.13",
]
dependencies = [
    "polars>=1.0",
    "typer>=0.12",
    "rich>=13.0",
    "pydantic>=2.0",
    "pyyaml>=6.0",
]

[project.optional-dependencies]
check = ["goldencheck>=0.5.0"]
flow = ["goldenflow>=0.1.0"]
match = ["goldenmatch>=1.2.0"]
golden-suite = ["goldencheck>=0.5.0", "goldenflow>=0.1.0", "goldenmatch>=1.2.0"]
tui = ["textual>=1.0"]
api = ["fastapi>=0.110", "uvicorn>=0.29"]
mcp = ["mcp>=1.0"]
agent = ["aiohttp>=3.9"]
all = ["goldenpipe[golden-suite,tui,api,mcp,agent]"]
dev = ["pytest>=8.0", "pytest-asyncio>=0.23", "httpx>=0.27"]

[project.scripts]
goldenpipe = "goldenpipe.cli.main:app"

[project.entry-points."goldenpipe.stages"]
"goldencheck.scan" = "goldenpipe.adapters.check:ScanStage"
"goldenflow.transform" = "goldenpipe.adapters.flow:TransformStage"
"goldenmatch.dedupe" = "goldenpipe.adapters.match:DedupeStage"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
```

- [ ] **Step 2: Install in dev mode**

Run: `pip install -e ".[dev]"`
Expected: Successfully installed goldenpipe with dev deps

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "chore: update pyproject.toml for v1.0 — new deps, extras, entry points"
```

---

## Task 2: Core Models — context.py

**Files:**
- Create: `goldenpipe/models/__init__.py`
- Create: `goldenpipe/models/context.py`
- Create: `tests/test_models.py`
- Create: `tests/conftest.py`

- [ ] **Step 1: Create conftest.py with shared fixtures**

```python
"""Shared test fixtures for GoldenPipe."""
import pytest
import polars as pl
from pathlib import Path


@pytest.fixture
def sample_csv(tmp_path: Path) -> Path:
    """5-row CSV with duplicates for pipeline testing."""
    data = (
        "name,email,phone\n"
        "John Smith,john@example.com,555-1234\n"
        "John Smith,jsmith@example.com,5551234\n"
        "John Smith,john.smith@example.com,555-1234\n"
        "Jane Doe,jane@example.com,555-5678\n"
        "Bob Jones,bob@example.com,555-9012\n"
    )
    p = tmp_path / "customers.csv"
    p.write_text(data)
    return p


@pytest.fixture
def sample_df() -> pl.DataFrame:
    """5-row DataFrame matching sample_csv."""
    return pl.DataFrame({
        "name": ["John Smith", "John Smith", "John Smith", "Jane Doe", "Bob Jones"],
        "email": ["john@example.com", "jsmith@example.com", "john.smith@example.com",
                   "jane@example.com", "bob@example.com"],
        "phone": ["555-1234", "5551234", "555-1234", "555-5678", "555-9012"],
    })
```

- [ ] **Step 2: Write failing tests for context models**

File: `tests/test_models.py`

```python
"""Tests for goldenpipe.models.context."""
import polars as pl
from goldenpipe.models.context import (
    PipeContext, StageResult, Decision, PipeResult,
    StageStatus, PipeStatus,
)


class TestStageStatus:
    def test_values(self):
        assert StageStatus.SUCCESS == "success"
        assert StageStatus.SKIPPED == "skipped"
        assert StageStatus.FAILED == "failed"

    def test_is_str(self):
        assert isinstance(StageStatus.SUCCESS, str)


class TestPipeStatus:
    def test_values(self):
        assert PipeStatus.SUCCESS == "success"
        assert PipeStatus.PARTIAL == "partial"
        assert PipeStatus.FAILED == "failed"


class TestPipeContext:
    def test_defaults(self):
        ctx = PipeContext()
        assert ctx.df is None
        assert ctx.artifacts == {}
        assert ctx.metadata == {}
        assert ctx.timing == {}
        assert ctx.reasoning == {}

    def test_with_df(self, sample_df):
        ctx = PipeContext(df=sample_df)
        assert ctx.df is not None
        assert len(ctx.df) == 5

    def test_artifacts_independent(self):
        ctx1 = PipeContext()
        ctx2 = PipeContext()
        ctx1.artifacts["key"] = "value"
        assert "key" not in ctx2.artifacts


class TestStageResult:
    def test_success(self):
        r = StageResult(status=StageStatus.SUCCESS)
        assert r.status == StageStatus.SUCCESS
        assert r.decision is None
        assert r.error is None

    def test_failed_with_error(self):
        r = StageResult(status=StageStatus.FAILED, error="boom")
        assert r.error == "boom"

    def test_with_decision(self):
        d = Decision(skip=["match"], reason="PII detected")
        r = StageResult(status=StageStatus.SUCCESS, decision=d)
        assert r.decision.skip == ["match"]


class TestDecision:
    def test_defaults(self):
        d = Decision()
        assert d.skip == []
        assert d.abort is False
        assert d.insert == []
        assert d.reason == ""

    def test_skip(self):
        d = Decision(skip=["flow", "match"])
        assert d.skip == ["flow", "match"]

    def test_abort(self):
        d = Decision(abort=True, reason="fatal")
        assert d.abort is True

    def test_insert(self):
        d = Decision(skip=["match"], insert=["match_pprl"], reason="PII")
        assert d.insert == ["match_pprl"]

    def test_independent_lists(self):
        d1 = Decision()
        d2 = Decision()
        d1.skip.append("x")
        assert "x" not in d2.skip


class TestPipeResult:
    def test_success(self):
        r = PipeResult(
            status=PipeStatus.SUCCESS,
            source="test.csv",
            input_rows=5,
            stages={},
            artifacts={},
            skipped=[],
            errors=[],
            reasoning={},
            timing={},
        )
        assert r.status == PipeStatus.SUCCESS
        assert r.source == "test.csv"
        assert r.input_rows == 5

    def test_repr_contains_source(self):
        r = PipeResult(
            status=PipeStatus.SUCCESS, source="test.csv", input_rows=5,
            stages={}, artifacts={}, skipped=[], errors=[], reasoning={}, timing={},
        )
        assert "test.csv" in repr(r)

    def test_repr_html(self):
        r = PipeResult(
            status=PipeStatus.SUCCESS, source="test.csv", input_rows=5,
            stages={}, artifacts={}, skipped=[], errors=[], reasoning={}, timing={},
        )
        html = r._repr_html_()
        assert "<table" in html
        assert "test.csv" in html
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'goldenpipe.models'`

- [ ] **Step 4: Create models/__init__.py**

File: `goldenpipe/models/__init__.py`

```python
"""Data models for GoldenPipe."""
from goldenpipe.models.context import (
    PipeContext, StageResult, Decision, PipeResult,
    StageStatus, PipeStatus,
)

__all__ = [
    "PipeContext", "StageResult", "Decision", "PipeResult",
    "StageStatus", "PipeStatus",
]
```

- [ ] **Step 5: Implement context.py**

File: `goldenpipe/models/context.py`

```python
"""Core data models: PipeContext, StageResult, Decision, PipeResult."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import polars as pl


class StageStatus(str, Enum):
    SUCCESS = "success"
    SKIPPED = "skipped"
    FAILED = "failed"


class PipeStatus(str, Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


@dataclass
class Decision:
    """Routing instruction from a stage to the framework."""
    skip: list[str] = field(default_factory=list)
    abort: bool = False
    insert: list[str] = field(default_factory=list)
    reason: str = ""


@dataclass
class StageResult:
    """Result returned by every stage's run() method."""
    status: StageStatus
    decision: Decision | None = None
    error: str | None = None


@dataclass
class PipeContext:
    """The object flowing through the pipeline. Stages mutate it in place."""
    df: pl.DataFrame | None = None
    artifacts: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    timing: dict[str, float] = field(default_factory=dict)
    reasoning: dict[str, str] = field(default_factory=dict)


@dataclass
class PipeResult:
    """Final output returned to the caller."""
    status: PipeStatus
    source: str
    input_rows: int
    stages: dict[str, StageResult] = field(default_factory=dict)
    artifacts: dict[str, Any] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    reasoning: dict[str, str] = field(default_factory=dict)
    timing: dict[str, float] = field(default_factory=dict)

    def __repr__(self) -> str:
        stage_summary = ", ".join(
            f"{name}: {r.status.value}" for name, r in self.stages.items()
        )
        return (
            f"PipeResult(status={self.status.value!r}, source={self.source!r}, "
            f"rows={self.input_rows}, stages=[{stage_summary}])"
        )

    def _repr_html_(self) -> str:
        rows = ""
        for name, r in self.stages.items():
            color = {"success": "green", "skipped": "orange", "failed": "red"}.get(
                r.status.value, "gray"
            )
            rows += (
                f"<tr><td>{name}</td>"
                f"<td style='color:{color}'>{r.status.value}</td>"
                f"<td>{r.error or ''}</td></tr>"
            )
        return (
            f"<table><caption>GoldenPipe: {self.source} "
            f"({self.input_rows} rows) - {self.status.value}</caption>"
            f"<tr><th>Stage</th><th>Status</th><th>Error</th></tr>"
            f"{rows}</table>"
        )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_models.py -v`
Expected: All 16 tests PASS

- [ ] **Step 7: Commit**

```bash
git add goldenpipe/models/ tests/conftest.py tests/test_models.py
git commit -m "feat: core data models — PipeContext, StageResult, Decision, PipeResult"
```

---

## Task 3: Core Models — stage.py (Protocol + @stage decorator)

**Files:**
- Create: `goldenpipe/models/stage.py`
- Create: `tests/test_stage.py`
- Modify: `goldenpipe/models/__init__.py`

- [ ] **Step 1: Write failing tests**

File: `tests/test_stage.py`

```python
"""Tests for Stage protocol and @stage decorator."""
from goldenpipe.models.stage import StageInfo, stage
from goldenpipe.models.context import PipeContext, StageResult, StageStatus


class TestStageInfo:
    def test_create(self):
        info = StageInfo(name="test", produces=["df"], consumes=["df"])
        assert info.name == "test"
        assert info.produces == ["df"]
        assert info.consumes == ["df"]
        assert info.config_schema is None

    def test_with_config_schema(self):
        info = StageInfo(name="test", produces=[], consumes=[], config_schema=dict)
        assert info.config_schema is dict


class TestStageDecorator:
    def test_basic_decoration(self):
        @stage(name="my_stage", produces=["df"], consumes=["df"])
        def my_stage(ctx: PipeContext) -> StageResult:
            return StageResult(status=StageStatus.SUCCESS)

        assert my_stage.info.name == "my_stage"
        assert my_stage.info.produces == ["df"]
        assert my_stage.info.consumes == ["df"]

    def test_run(self, sample_df):
        @stage(name="noop", produces=["df"], consumes=["df"])
        def noop(ctx: PipeContext) -> StageResult:
            return StageResult(status=StageStatus.SUCCESS)

        ctx = PipeContext(df=sample_df)
        result = noop.run(ctx)
        assert result.status == StageStatus.SUCCESS

    def test_validate_exists(self):
        @stage(name="test", produces=[], consumes=[])
        def test_fn(ctx: PipeContext) -> StageResult:
            return StageResult(status=StageStatus.SUCCESS)

        # validate is a no-op but callable
        ctx = PipeContext()
        test_fn.validate(ctx)  # should not raise

    def test_no_rollback_by_default(self):
        @stage(name="test", produces=[], consumes=[])
        def test_fn(ctx: PipeContext) -> StageResult:
            return StageResult(status=StageStatus.SUCCESS)

        assert not hasattr(test_fn, "rollback") or test_fn.rollback is None

    def test_mutates_context(self, sample_df):
        @stage(name="upper", produces=["df"], consumes=["df"])
        def upper_names(ctx: PipeContext) -> StageResult:
            ctx.df = ctx.df.with_columns(pl.col("name").str.to_uppercase())
            return StageResult(status=StageStatus.SUCCESS)

        import polars as pl
        ctx = PipeContext(df=sample_df)
        upper_names.run(ctx)
        assert ctx.df["name"][0] == "JOHN SMITH"

    def test_produces_artifact(self):
        @stage(name="producer", produces=["findings"], consumes=[])
        def produce(ctx: PipeContext) -> StageResult:
            ctx.artifacts["findings"] = [{"col": "name", "issue": "nulls"}]
            return StageResult(status=StageStatus.SUCCESS)

        ctx = PipeContext()
        produce.run(ctx)
        assert "findings" in ctx.artifacts

    def test_returns_decision(self):
        from goldenpipe.models.context import Decision

        @stage(name="gate", produces=[], consumes=[])
        def gate(ctx: PipeContext) -> StageResult:
            return StageResult(
                status=StageStatus.SUCCESS,
                decision=Decision(abort=True, reason="stop"),
            )

        ctx = PipeContext()
        result = gate.run(ctx)
        assert result.decision is not None
        assert result.decision.abort is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_stage.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'goldenpipe.models.stage'`

- [ ] **Step 3: Implement stage.py**

File: `goldenpipe/models/stage.py`

```python
"""Stage protocol and @stage decorator."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol, runtime_checkable

from goldenpipe.models.context import PipeContext, StageResult


@dataclass
class StageInfo:
    """Metadata for stage registry and wiring validation."""
    name: str
    produces: list[str]
    consumes: list[str]
    config_schema: type | None = None


@runtime_checkable
class Stage(Protocol):
    """Full contract for pipeline stages."""
    info: StageInfo

    def validate(self, ctx: PipeContext) -> None: ...
    def run(self, ctx: PipeContext) -> StageResult: ...
    def rollback(self, ctx: PipeContext) -> None: ...


class _FunctionStage:
    """Wraps a plain function into a Stage-compatible object."""

    def __init__(
        self,
        fn: Callable[[PipeContext], StageResult],
        info: StageInfo,
    ):
        self._fn = fn
        self.info = info
        self.rollback = None

    def validate(self, ctx: PipeContext) -> None:
        pass

    def run(self, ctx: PipeContext) -> StageResult:
        return self._fn(ctx)


def stage(
    *,
    name: str,
    produces: list[str],
    consumes: list[str],
    config_schema: type | None = None,
) -> Callable[[Callable[[PipeContext], StageResult]], _FunctionStage]:
    """Decorator to create a stage from a plain function."""
    def decorator(fn: Callable[[PipeContext], StageResult]) -> _FunctionStage:
        info = StageInfo(
            name=name,
            produces=produces,
            consumes=consumes,
            config_schema=config_schema,
        )
        return _FunctionStage(fn, info)
    return decorator
```

- [ ] **Step 4: Update models/__init__.py**

Add to `goldenpipe/models/__init__.py`:

```python
from goldenpipe.models.stage import StageInfo, Stage, stage
```

And add `"StageInfo", "Stage", "stage"` to `__all__`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_stage.py -v`
Expected: All 8 tests PASS

- [ ] **Step 6: Commit**

```bash
git add goldenpipe/models/stage.py goldenpipe/models/__init__.py tests/test_stage.py
git commit -m "feat: Stage protocol and @stage decorator"
```

---

## Task 4: Core Models — config.py (Pydantic)

**Files:**
- Create: `goldenpipe/models/config.py`
- Create: `tests/test_config.py`
- Modify: `goldenpipe/models/__init__.py`

- [ ] **Step 1: Write failing tests**

File: `tests/test_config.py`

```python
"""Tests for config models and YAML loader."""
from goldenpipe.models.config import StageSpec, PipelineConfig


class TestStageSpec:
    def test_minimal(self):
        s = StageSpec(use="goldencheck.scan")
        assert s.use == "goldencheck.scan"
        assert s.name is None
        assert s.needs == []
        assert s.skip_if is None
        assert s.on_error == "continue"
        assert s.config == {}

    def test_rich(self):
        s = StageSpec(
            name="validate",
            use="goldencheck.scan",
            needs=["load"],
            skip_if="findings",
            on_error="abort",
            config={"severity_threshold": "warning"},
        )
        assert s.name == "validate"
        assert s.on_error == "abort"
        assert s.config["severity_threshold"] == "warning"

    def test_on_error_validation(self):
        import pytest
        with pytest.raises(Exception):  # Pydantic ValidationError
            StageSpec(use="test", on_error="invalid")


class TestPipelineConfig:
    def test_minimal_string_stages(self):
        c = PipelineConfig(
            pipeline="test",
            stages=["goldencheck.scan", "goldenflow.transform"],
        )
        assert c.pipeline == "test"
        assert len(c.stages) == 2
        assert c.source is None
        assert c.decisions == []

    def test_mixed_stages(self):
        c = PipelineConfig(
            pipeline="test",
            stages=[
                "goldencheck.scan",
                StageSpec(name="clean", use="goldenflow.transform"),
            ],
        )
        assert isinstance(c.stages[0], str)
        assert isinstance(c.stages[1], StageSpec)

    def test_rich_config(self):
        c = PipelineConfig(
            pipeline="customers-dedupe",
            source="customers.csv",
            output="golden.csv",
            stages=[
                StageSpec(
                    name="validate",
                    use="goldencheck.scan",
                    config={"severity_threshold": "warning"},
                ),
            ],
            decisions=["severity_gate", "pii_router"],
        )
        assert c.source == "customers.csv"
        assert c.output == "golden.csv"
        assert len(c.decisions) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement config.py**

File: `goldenpipe/models/config.py`

```python
"""Pipeline configuration models (Pydantic)."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


class StageSpec(BaseModel):
    """Configuration for a single pipeline stage."""
    name: str | None = None
    use: str
    needs: list[str] = []
    skip_if: str | None = None
    on_error: Literal["continue", "abort"] = "continue"
    config: dict[str, Any] = {}


class PipelineConfig(BaseModel):
    """Top-level pipeline configuration."""
    pipeline: str
    source: str | None = None
    output: str | None = None
    stages: list[StageSpec | str]
    decisions: list[str] = []
```

- [ ] **Step 4: Update models/__init__.py**

Add imports for `StageSpec`, `PipelineConfig` and add to `__all__`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_config.py -v`
Expected: All 6 tests PASS

- [ ] **Step 6: Commit**

```bash
git add goldenpipe/models/config.py goldenpipe/models/__init__.py tests/test_config.py
git commit -m "feat: Pydantic config models — StageSpec, PipelineConfig"
```

---

## Task 5: Config Loader (YAML)

**Files:**
- Create: `goldenpipe/config/loader.py`
- Modify: `tests/test_config.py` (add loader tests)

- [ ] **Step 1: Write failing tests**

Append to `tests/test_config.py`:

```python
from goldenpipe.config.loader import load_config


class TestLoadConfig:
    def test_load_minimal_yaml(self, tmp_path):
        f = tmp_path / "pipe.yml"
        f.write_text(
            "pipeline: test\n"
            "stages:\n"
            "  - goldencheck.scan\n"
            "  - goldenflow.transform\n"
        )
        config = load_config(str(f))
        assert config.pipeline == "test"
        assert len(config.stages) == 2
        # Bare strings normalized to StageSpec
        assert all(isinstance(s, StageSpec) for s in config.stages)

    def test_load_rich_yaml(self, tmp_path):
        f = tmp_path / "pipe.yml"
        f.write_text(
            "pipeline: test\n"
            "source: data.csv\n"
            "stages:\n"
            "  - name: validate\n"
            "    use: goldencheck.scan\n"
            "    config:\n"
            "      severity_threshold: warning\n"
            "decisions:\n"
            "  - severity_gate\n"
        )
        config = load_config(str(f))
        assert config.source == "data.csv"
        assert config.stages[0].name == "validate"
        assert config.decisions == ["severity_gate"]

    def test_load_mixed_yaml(self, tmp_path):
        f = tmp_path / "pipe.yml"
        f.write_text(
            "pipeline: test\n"
            "stages:\n"
            "  - goldencheck.scan\n"
            "  - name: clean\n"
            "    use: goldenflow.transform\n"
        )
        config = load_config(str(f))
        assert config.stages[0].use == "goldencheck.scan"
        assert config.stages[1].name == "clean"

    def test_load_nonexistent_file(self):
        import pytest
        with pytest.raises(FileNotFoundError):
            load_config("/nonexistent/pipe.yml")

    def test_load_invalid_yaml(self, tmp_path):
        f = tmp_path / "pipe.yml"
        f.write_text("pipeline: test\nstages: not_a_list\n")
        import pytest
        with pytest.raises(Exception):  # Pydantic ValidationError
            load_config(str(f))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_config.py::TestLoadConfig -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement loader.py**

File: `goldenpipe/config/loader.py`

```python
"""YAML config loading and normalization."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from goldenpipe.models.config import PipelineConfig, StageSpec


def load_config(path: str) -> PipelineConfig:
    """Load and validate a pipeline config from YAML."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(p) as f:
        raw: dict[str, Any] = yaml.safe_load(f)

    # Normalize bare strings to StageSpec
    normalized_stages: list[StageSpec | str] = []
    for s in raw.get("stages", []):
        if isinstance(s, str):
            normalized_stages.append(StageSpec(use=s))
        elif isinstance(s, dict):
            if "use" not in s and "name" not in s:
                raise ValueError(f"Stage spec must have 'use' field: {s}")
            normalized_stages.append(StageSpec(**s))
        else:
            raise ValueError(f"Invalid stage spec: {s}")

    raw["stages"] = normalized_stages
    return PipelineConfig(**raw)
```

- [ ] **Step 4: Delete old empty config/__init__.py and ensure loader is importable**

Delete `goldenpipe/config/__init__.py` or replace with:

```python
"""Configuration loading."""
from goldenpipe.config.loader import load_config

__all__ = ["load_config"]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_config.py -v`
Expected: All 11 tests PASS

- [ ] **Step 6: Commit**

```bash
git add goldenpipe/config/ tests/test_config.py
git commit -m "feat: YAML config loader with normalization"
```

---

## Task 6: Engine — Registry

**Files:**
- Create: `goldenpipe/engine/__init__.py`
- Create: `goldenpipe/engine/registry.py`
- Create: `tests/test_registry.py`

- [ ] **Step 1: Write failing tests**

File: `tests/test_registry.py`

```python
"""Tests for stage registry."""
import pytest
from goldenpipe.engine.registry import StageRegistry
from goldenpipe.models.stage import StageInfo, stage
from goldenpipe.models.context import PipeContext, StageResult, StageStatus


@stage(name="dummy", produces=["df"], consumes=["df"])
def dummy_stage(ctx: PipeContext) -> StageResult:
    return StageResult(status=StageStatus.SUCCESS)


class TestStageRegistry:
    def test_register_and_get(self):
        reg = StageRegistry()
        reg.register(dummy_stage)
        assert reg.get("dummy") is dummy_stage

    def test_get_missing_raises(self):
        reg = StageRegistry()
        with pytest.raises(KeyError, match="not found"):
            reg.get("nonexistent")

    def test_list_all(self):
        reg = StageRegistry()
        reg.register(dummy_stage)
        all_stages = reg.list_all()
        assert "dummy" in all_stages
        assert all_stages["dummy"].name == "dummy"

    def test_register_duplicate_overwrites(self):
        reg = StageRegistry()
        reg.register(dummy_stage)

        @stage(name="dummy", produces=[], consumes=[])
        def dummy_v2(ctx: PipeContext) -> StageResult:
            return StageResult(status=StageStatus.SUCCESS)

        reg.register(dummy_v2)
        assert reg.get("dummy") is dummy_v2

    def test_discover_entry_points(self):
        reg = StageRegistry()
        reg.discover()
        # Should find built-in adapters if installed
        all_stages = reg.list_all()
        # At minimum, entry point discovery shouldn't crash
        assert isinstance(all_stages, dict)

    def test_discover_local_stages_dir(self, tmp_path):
        stages_dir = tmp_path / "stages"
        stages_dir.mkdir()
        (stages_dir / "my_stage.py").write_text(
            "from goldenpipe.models.stage import stage\n"
            "from goldenpipe.models.context import PipeContext, StageResult, StageStatus\n"
            "\n"
            "@stage(name='my_local', produces=['df'], consumes=['df'])\n"
            "def my_local(ctx: PipeContext) -> StageResult:\n"
            "    return StageResult(status=StageStatus.SUCCESS)\n"
        )
        reg = StageRegistry()
        reg.discover(stages_dir=stages_dir)
        assert reg.get("my_local") is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_registry.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement registry.py**

File: `goldenpipe/engine/__init__.py`

```python
"""Pipeline engine modules."""
```

File: `goldenpipe/engine/registry.py`

```python
"""Stage registry — discover, register, and retrieve stages."""
from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import sys
from pathlib import Path
from typing import Any

from goldenpipe.models.stage import StageInfo


class StageRegistry:
    """Discovers and stores pipeline stages."""

    def __init__(self) -> None:
        self._stages: dict[str, Any] = {}

    def register(self, stage: Any) -> None:
        """Register a stage by its info.name."""
        self._stages[stage.info.name] = stage

    def get(self, name: str) -> Any:
        """Retrieve a stage by name. Raises KeyError if not found."""
        if name not in self._stages:
            raise KeyError(f"Stage '{name}' not found in registry")
        return self._stages[name]

    def list_all(self) -> dict[str, StageInfo]:
        """Return {name: StageInfo} for all registered stages."""
        return {name: s.info for name, s in self._stages.items()}

    def discover(self, stages_dir: Path | None = None) -> None:
        """Discover stages from entry points and optional local directory."""
        self._discover_entry_points()
        if stages_dir is not None:
            self._discover_local(stages_dir)

    def _discover_entry_points(self) -> None:
        """Load stages from goldenpipe.stages entry points."""
        try:
            eps = importlib.metadata.entry_points(group="goldenpipe.stages")
        except TypeError:
            # Python 3.11 compat
            eps = importlib.metadata.entry_points().get("goldenpipe.stages", [])

        for ep in eps:
            try:
                stage_cls = ep.load()
                if hasattr(stage_cls, "info"):
                    self._stages[ep.name] = stage_cls
            except Exception:
                pass  # Skip broken entry points

    def _discover_local(self, stages_dir: Path) -> None:
        """Load stages from .py files in a local directory."""
        if not stages_dir.is_dir():
            return

        for py_file in sorted(stages_dir.glob("*.py")):
            if py_file.name.startswith("_"):
                continue
            module_name = f"goldenpipe._local_stages.{py_file.stem}"
            spec = importlib.util.spec_from_file_location(module_name, py_file)
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            try:
                spec.loader.exec_module(module)
            except Exception:
                continue

            # Find stages in module
            for attr_name in dir(module):
                obj = getattr(module, attr_name)
                if hasattr(obj, "info") and hasattr(obj, "run"):
                    self._stages[obj.info.name] = obj
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_registry.py -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add goldenpipe/engine/ tests/test_registry.py
git commit -m "feat: StageRegistry — entry points + local stages/ discovery"
```

---

## Task 7: Engine — Resolver

**Files:**
- Create: `goldenpipe/engine/resolver.py`
- Create: `tests/test_resolver.py`

- [ ] **Step 1: Write failing tests**

File: `tests/test_resolver.py`

```python
"""Tests for pipeline resolver and wiring validation."""
import pytest
from goldenpipe.engine.resolver import Resolver, ExecutionPlan, PlannedStage, WiringError
from goldenpipe.engine.registry import StageRegistry
from goldenpipe.models.config import PipelineConfig, StageSpec
from goldenpipe.models.stage import stage
from goldenpipe.models.context import PipeContext, StageResult, StageStatus


@stage(name="load", produces=["df"], consumes=[])
def load_stage(ctx: PipeContext) -> StageResult:
    return StageResult(status=StageStatus.SUCCESS)


@stage(name="check", produces=["findings"], consumes=["df"])
def check_stage(ctx: PipeContext) -> StageResult:
    return StageResult(status=StageStatus.SUCCESS)


@stage(name="transform", produces=["df", "manifest"], consumes=["df"])
def transform_stage(ctx: PipeContext) -> StageResult:
    return StageResult(status=StageStatus.SUCCESS)


@stage(name="dedupe", produces=["clusters", "golden"], consumes=["df"])
def dedupe_stage(ctx: PipeContext) -> StageResult:
    return StageResult(status=StageStatus.SUCCESS)


@pytest.fixture
def registry():
    reg = StageRegistry()
    reg.register(load_stage)
    reg.register(check_stage)
    reg.register(transform_stage)
    reg.register(dedupe_stage)
    return reg


class TestResolver:
    def test_resolve_minimal(self, registry):
        config = PipelineConfig(
            pipeline="test",
            stages=[
                StageSpec(use="check"),
                StageSpec(use="transform"),
                StageSpec(use="dedupe"),
            ],
        )
        plan = Resolver.resolve(config, registry)
        assert isinstance(plan, ExecutionPlan)
        # load is auto-prepended
        assert len(plan.stages) == 4
        assert plan.stages[0].name == "load"

    def test_resolve_bare_strings(self, registry):
        config = PipelineConfig(
            pipeline="test",
            stages=[StageSpec(use="check"), StageSpec(use="dedupe")],
        )
        plan = Resolver.resolve(config, registry)
        names = [s.name for s in plan.stages]
        assert "check" in names
        assert "dedupe" in names

    def test_stage_aliased_name(self, registry):
        config = PipelineConfig(
            pipeline="test",
            stages=[StageSpec(name="validate", use="check")],
        )
        plan = Resolver.resolve(config, registry)
        assert plan.stages[1].name == "validate"

    def test_wiring_error_missing_dependency(self, registry):
        @stage(name="needs_clusters", produces=[], consumes=["clusters"])
        def bad_stage(ctx: PipeContext) -> StageResult:
            return StageResult(status=StageStatus.SUCCESS)
        registry.register(bad_stage)

        config = PipelineConfig(
            pipeline="test",
            stages=[StageSpec(use="needs_clusters")],
        )
        with pytest.raises(WiringError, match="clusters"):
            Resolver.resolve(config, registry)

    def test_wiring_valid_chain(self, registry):
        config = PipelineConfig(
            pipeline="test",
            stages=[
                StageSpec(use="check"),
                StageSpec(use="transform"),
                StageSpec(use="dedupe"),
            ],
        )
        # Should not raise
        plan = Resolver.resolve(config, registry)
        assert len(plan.stages) == 4

    def test_unknown_stage_raises(self, registry):
        config = PipelineConfig(
            pipeline="test",
            stages=[StageSpec(use="nonexistent")],
        )
        with pytest.raises(KeyError, match="not found"):
            Resolver.resolve(config, registry)

    def test_planned_stage_has_config(self, registry):
        config = PipelineConfig(
            pipeline="test",
            stages=[StageSpec(use="check", config={"threshold": 0.5})],
        )
        plan = Resolver.resolve(config, registry)
        assert plan.stages[1].config == {"threshold": 0.5}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_resolver.py -v`
Expected: FAIL

- [ ] **Step 3: Implement resolver.py**

File: `goldenpipe/engine/resolver.py`

```python
"""Pipeline resolver — build ExecutionPlan, validate wiring."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from goldenpipe.engine.registry import StageRegistry
from goldenpipe.models.config import PipelineConfig, StageSpec


class WiringError(Exception):
    """Raised when a stage's consumes can't be satisfied."""


@dataclass
class PlannedStage:
    """A resolved stage ready for execution."""
    name: str
    stage: Any  # Stage protocol object
    spec: StageSpec
    config: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExecutionPlan:
    """Ordered list of stages to execute."""
    stages: list[PlannedStage] = field(default_factory=list)


class Resolver:
    """Builds and validates an ExecutionPlan from config + registry."""

    @staticmethod
    def resolve(config: PipelineConfig, registry: StageRegistry) -> ExecutionPlan:
        plan = ExecutionPlan()
        available_artifacts: set[str] = set()

        # Auto-prepend load stage if available
        try:
            load = registry.get("load")
            load_spec = StageSpec(use="load")
            plan.stages.append(PlannedStage(
                name="load", stage=load, spec=load_spec,
            ))
            available_artifacts.update(load.info.produces)
        except KeyError:
            # "df" is always implicitly available
            available_artifacts.add("df")

        # Resolve each configured stage
        for raw_spec in config.stages:
            if isinstance(raw_spec, str):
                spec = StageSpec(use=raw_spec)
            else:
                spec = raw_spec

            stage_obj = registry.get(spec.use)
            name = spec.name or stage_obj.info.name

            # Validate wiring
            for dep in stage_obj.info.consumes:
                if dep not in available_artifacts:
                    raise WiringError(
                        f"Stage '{name}' consumes '{dep}' but no prior stage "
                        f"produces it. Available: {sorted(available_artifacts)}"
                    )

            plan.stages.append(PlannedStage(
                name=name,
                stage=stage_obj,
                spec=spec,
                config=spec.config,
            ))
            available_artifacts.update(stage_obj.info.produces)

        return plan
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_resolver.py -v`
Expected: All 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add goldenpipe/engine/resolver.py tests/test_resolver.py
git commit -m "feat: Resolver — ExecutionPlan, wiring validation"
```

---

## Task 8: Engine — Router

**Files:**
- Create: `goldenpipe/engine/router.py`
- Create: `tests/test_router.py`

- [ ] **Step 1: Write failing tests**

File: `tests/test_router.py`

```python
"""Tests for decision routing."""
import pytest
from goldenpipe.engine.router import Router
from goldenpipe.engine.resolver import PlannedStage, ExecutionPlan
from goldenpipe.engine.registry import StageRegistry
from goldenpipe.models.config import StageSpec
from goldenpipe.models.context import Decision, PipeContext, StageResult, StageStatus
from goldenpipe.models.stage import stage


@stage(name="a", produces=["df"], consumes=[])
def stage_a(ctx: PipeContext) -> StageResult:
    return StageResult(status=StageStatus.SUCCESS)


@stage(name="b", produces=["findings"], consumes=["df"])
def stage_b(ctx: PipeContext) -> StageResult:
    return StageResult(status=StageStatus.SUCCESS)


@stage(name="c", produces=["clusters"], consumes=["df"])
def stage_c(ctx: PipeContext) -> StageResult:
    return StageResult(status=StageStatus.SUCCESS)


@stage(name="c_alt", produces=["clusters"], consumes=["df"])
def stage_c_alt(ctx: PipeContext) -> StageResult:
    return StageResult(status=StageStatus.SUCCESS)


def _make_planned(stg) -> PlannedStage:
    return PlannedStage(name=stg.info.name, stage=stg, spec=StageSpec(use=stg.info.name))


class TestRouter:
    def test_skip_stages(self):
        remaining = [_make_planned(stage_b), _make_planned(stage_c)]
        ctx = PipeContext()
        decision = Decision(skip=["c"], reason="not needed")

        result = Router.apply(decision, remaining, ctx, StageRegistry())
        names = [s.name for s in result]
        assert "c" not in names
        assert "b" in names
        assert "not needed" in ctx.reasoning.values()

    def test_abort_clears_plan(self):
        remaining = [_make_planned(stage_b), _make_planned(stage_c)]
        ctx = PipeContext()
        decision = Decision(abort=True, reason="fatal")

        result = Router.apply(decision, remaining, ctx, StageRegistry())
        assert result == []

    def test_insert_stages(self):
        remaining = [_make_planned(stage_c)]
        ctx = PipeContext()
        reg = StageRegistry()
        reg.register(stage_c_alt)
        decision = Decision(insert=["c_alt"], reason="adding alt")

        result = Router.apply(decision, remaining, ctx, reg)
        names = [s.name for s in result]
        assert names[0] == "c_alt"
        assert names[1] == "c"

    def test_skip_and_insert_replacement(self):
        remaining = [_make_planned(stage_c)]
        ctx = PipeContext()
        reg = StageRegistry()
        reg.register(stage_c_alt)
        decision = Decision(skip=["c"], insert=["c_alt"], reason="swap")

        result = Router.apply(decision, remaining, ctx, reg)
        names = [s.name for s in result]
        assert names == ["c_alt"]

    def test_empty_decision_no_change(self):
        remaining = [_make_planned(stage_b), _make_planned(stage_c)]
        ctx = PipeContext()
        decision = Decision()

        result = Router.apply(decision, remaining, ctx, StageRegistry())
        assert len(result) == 2

    def test_skip_nonexistent_stage_is_noop(self):
        remaining = [_make_planned(stage_b)]
        ctx = PipeContext()
        decision = Decision(skip=["nonexistent"])

        result = Router.apply(decision, remaining, ctx, StageRegistry())
        assert len(result) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_router.py -v`
Expected: FAIL

- [ ] **Step 3: Implement router.py**

File: `goldenpipe/engine/router.py`

```python
"""Decision router — apply routing decisions to execution plan."""
from __future__ import annotations

from goldenpipe.engine.registry import StageRegistry
from goldenpipe.engine.resolver import PlannedStage
from goldenpipe.models.config import StageSpec
from goldenpipe.models.context import Decision, PipeContext


class Router:
    """Applies Decision objects to modify the remaining execution plan."""

    @staticmethod
    def apply(
        decision: Decision,
        remaining: list[PlannedStage],
        ctx: PipeContext,
        registry: StageRegistry,
    ) -> list[PlannedStage]:
        """Return modified remaining plan after applying the decision."""
        if decision.reason:
            ctx.reasoning[f"_router"] = decision.reason

        if decision.abort:
            ctx.reasoning["_router"] = f"ABORT: {decision.reason}"
            return []

        # Remove skipped stages
        if decision.skip:
            remaining = [s for s in remaining if s.name not in decision.skip]

        # Insert new stages at the front (immediately after current)
        if decision.insert:
            inserted = []
            for name in reversed(decision.insert):
                stage_obj = registry.get(name)
                inserted.insert(0, PlannedStage(
                    name=name,
                    stage=stage_obj,
                    spec=StageSpec(use=name),
                ))
            remaining = inserted + remaining

        return remaining
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_router.py -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add goldenpipe/engine/router.py tests/test_router.py
git commit -m "feat: Router — skip, insert, abort decision handling"
```

---

## Task 9: Engine — Runner

**Files:**
- Create: `goldenpipe/engine/runner.py`
- Create: `tests/test_runner.py`

- [ ] **Step 1: Write failing tests**

File: `tests/test_runner.py`

```python
"""Tests for pipeline runner."""
import pytest
from goldenpipe.engine.runner import Runner
from goldenpipe.engine.resolver import PlannedStage, ExecutionPlan
from goldenpipe.engine.registry import StageRegistry
from goldenpipe.models.config import StageSpec
from goldenpipe.models.context import (
    PipeContext, StageResult, StageStatus, Decision,
)
from goldenpipe.models.stage import stage


@stage(name="success_stage", produces=["df"], consumes=[])
def success_stage(ctx: PipeContext) -> StageResult:
    return StageResult(status=StageStatus.SUCCESS)


@stage(name="fail_stage", produces=[], consumes=[])
def fail_stage(ctx: PipeContext) -> StageResult:
    raise RuntimeError("boom")


@stage(name="skip_decider", produces=[], consumes=[])
def skip_decider(ctx: PipeContext) -> StageResult:
    return StageResult(
        status=StageStatus.SUCCESS,
        decision=Decision(skip=["fail_stage"], reason="skip it"),
    )


@stage(name="abort_decider", produces=[], consumes=[])
def abort_decider(ctx: PipeContext) -> StageResult:
    return StageResult(
        status=StageStatus.SUCCESS,
        decision=Decision(abort=True, reason="fatal"),
    )


def _plan(*stages) -> ExecutionPlan:
    planned = []
    for s in stages:
        planned.append(PlannedStage(
            name=s.info.name, stage=s,
            spec=StageSpec(use=s.info.name),
        ))
    return ExecutionPlan(stages=planned)


class TestRunner:
    def test_run_single_success(self):
        ctx = PipeContext()
        runner = Runner(registry=StageRegistry())
        result = runner.run(_plan(success_stage), ctx)
        assert "success_stage" in result
        assert result["success_stage"].status == StageStatus.SUCCESS

    def test_run_records_timing(self):
        ctx = PipeContext()
        runner = Runner(registry=StageRegistry())
        runner.run(_plan(success_stage), ctx)
        assert "success_stage" in ctx.timing
        assert ctx.timing["success_stage"] >= 0

    def test_run_failure_continues(self):
        ctx = PipeContext()
        runner = Runner(registry=StageRegistry())
        result = runner.run(_plan(fail_stage, success_stage), ctx)
        assert result["fail_stage"].status == StageStatus.FAILED
        assert result["fail_stage"].error == "boom"
        assert result["success_stage"].status == StageStatus.SUCCESS

    def test_run_failure_aborts_on_error(self):
        ctx = PipeContext()
        runner = Runner(registry=StageRegistry())
        plan = _plan(fail_stage, success_stage)
        plan.stages[0].spec = StageSpec(use="fail_stage", on_error="abort")
        result = runner.run(plan, ctx)
        assert result["fail_stage"].status == StageStatus.FAILED
        assert "success_stage" not in result

    def test_decision_skip(self):
        ctx = PipeContext()
        runner = Runner(registry=StageRegistry())
        result = runner.run(_plan(skip_decider, fail_stage), ctx)
        assert "skip_decider" in result
        assert "fail_stage" not in result

    def test_decision_abort(self):
        ctx = PipeContext()
        runner = Runner(registry=StageRegistry())
        result = runner.run(_plan(abort_decider, success_stage), ctx)
        assert "abort_decider" in result
        assert "success_stage" not in result

    def test_skip_if_artifact_missing(self):
        ctx = PipeContext()
        runner = Runner(registry=StageRegistry())
        plan = _plan(success_stage)
        plan.stages[0].spec = StageSpec(use="success_stage", skip_if="findings")
        result = runner.run(plan, ctx)
        assert result["success_stage"].status == StageStatus.SKIPPED

    def test_skip_if_artifact_present(self):
        ctx = PipeContext()
        ctx.artifacts["findings"] = [{"issue": "nulls"}]
        runner = Runner(registry=StageRegistry())
        plan = _plan(success_stage)
        plan.stages[0].spec = StageSpec(use="success_stage", skip_if="findings")
        result = runner.run(plan, ctx)
        assert result["success_stage"].status == StageStatus.SUCCESS
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_runner.py -v`
Expected: FAIL

- [ ] **Step 3: Implement runner.py**

File: `goldenpipe/engine/runner.py`

```python
"""Pipeline runner — execute stages with error handling and routing."""
from __future__ import annotations

import time

from goldenpipe.engine.registry import StageRegistry
from goldenpipe.engine.resolver import ExecutionPlan, PlannedStage
from goldenpipe.engine.router import Router
from goldenpipe.models.context import PipeContext, StageResult, StageStatus


class Runner:
    """Executes an ExecutionPlan against a PipeContext."""

    def __init__(self, registry: StageRegistry) -> None:
        self._registry = registry

    def run(
        self, plan: ExecutionPlan, ctx: PipeContext,
    ) -> dict[str, StageResult]:
        """Execute all stages, return {name: StageResult}."""
        results: dict[str, StageResult] = {}
        remaining = list(plan.stages)

        while remaining:
            planned = remaining.pop(0)

            # Check skip_if condition
            if planned.spec.skip_if:
                artifact = ctx.artifacts.get(planned.spec.skip_if)
                if not artifact:
                    result = StageResult(status=StageStatus.SKIPPED)
                    results[planned.name] = result
                    ctx.reasoning[planned.name] = (
                        f"Skipped: artifact '{planned.spec.skip_if}' is missing/falsy"
                    )
                    continue

            # Execute stage
            start = time.perf_counter()
            try:
                if hasattr(planned.stage, "validate") and callable(planned.stage.validate):
                    planned.stage.validate(ctx)
                result = planned.stage.run(ctx)
                elapsed = time.perf_counter() - start
                ctx.timing[planned.name] = elapsed
                results[planned.name] = result

                # Handle decision
                if result.decision is not None:
                    remaining = Router.apply(
                        result.decision, remaining, ctx, self._registry,
                    )

            except Exception as e:
                elapsed = time.perf_counter() - start
                ctx.timing[planned.name] = elapsed
                result = StageResult(
                    status=StageStatus.FAILED,
                    error=str(e),
                )
                results[planned.name] = result
                ctx.reasoning[planned.name] = f"Failed: {e}"

                # Check on_error policy
                if planned.spec.on_error == "abort":
                    break

        return results
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_runner.py -v`
Expected: All 8 tests PASS

- [ ] **Step 5: Commit**

```bash
git add goldenpipe/engine/runner.py tests/test_runner.py
git commit -m "feat: Runner — stage execution with decisions, skip_if, on_error"
```

---

## Task 10: Engine — Reporter

**Files:**
- Create: `goldenpipe/engine/reporter.py`
- Create: `tests/test_reporter.py`

- [ ] **Step 1: Write failing tests**

File: `tests/test_reporter.py`

```python
"""Tests for PipeResult construction."""
from goldenpipe.engine.reporter import Reporter
from goldenpipe.models.context import (
    PipeContext, StageResult, StageStatus, PipeStatus, PipeResult,
)


class TestReporter:
    def test_all_success(self):
        ctx = PipeContext(metadata={"source": "test.csv", "input_rows": 5})
        ctx.timing = {"a": 1.0, "b": 2.0}
        ctx.reasoning = {"a": "ran"}
        stages = {
            "a": StageResult(status=StageStatus.SUCCESS),
            "b": StageResult(status=StageStatus.SUCCESS),
        }
        result = Reporter.build(ctx, stages)
        assert result.status == PipeStatus.SUCCESS
        assert result.source == "test.csv"
        assert result.input_rows == 5

    def test_all_failed(self):
        ctx = PipeContext(metadata={"source": "test.csv", "input_rows": 0})
        stages = {
            "a": StageResult(status=StageStatus.FAILED, error="err"),
        }
        result = Reporter.build(ctx, stages)
        assert result.status == PipeStatus.FAILED
        assert result.errors == ["a: err"]

    def test_partial(self):
        ctx = PipeContext(metadata={"source": "test.csv", "input_rows": 5})
        stages = {
            "a": StageResult(status=StageStatus.SUCCESS),
            "b": StageResult(status=StageStatus.FAILED, error="boom"),
        }
        result = Reporter.build(ctx, stages)
        assert result.status == PipeStatus.PARTIAL
        assert "b: boom" in result.errors

    def test_skipped_collected(self):
        ctx = PipeContext(metadata={"source": "test.csv", "input_rows": 5})
        stages = {
            "a": StageResult(status=StageStatus.SUCCESS),
            "b": StageResult(status=StageStatus.SKIPPED),
        }
        result = Reporter.build(ctx, stages)
        assert result.status == PipeStatus.SUCCESS
        assert "b" in result.skipped

    def test_artifacts_copied(self):
        ctx = PipeContext(metadata={"source": "test.csv", "input_rows": 5})
        ctx.artifacts["findings"] = [1, 2, 3]
        stages = {"a": StageResult(status=StageStatus.SUCCESS)}
        result = Reporter.build(ctx, stages)
        assert result.artifacts["findings"] == [1, 2, 3]

    def test_timing_and_reasoning(self):
        ctx = PipeContext(metadata={"source": "test.csv", "input_rows": 5})
        ctx.timing = {"a": 1.5}
        ctx.reasoning = {"a": "auto-detected"}
        stages = {"a": StageResult(status=StageStatus.SUCCESS)}
        result = Reporter.build(ctx, stages)
        assert result.timing["a"] == 1.5
        assert result.reasoning["a"] == "auto-detected"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_reporter.py -v`
Expected: FAIL

- [ ] **Step 3: Implement reporter.py**

File: `goldenpipe/engine/reporter.py`

```python
"""Reporter — builds PipeResult from PipeContext after execution."""
from __future__ import annotations

from goldenpipe.models.context import (
    PipeContext, PipeResult, PipeStatus, StageResult, StageStatus,
)


class Reporter:
    """Constructs a PipeResult from execution context."""

    @staticmethod
    def build(ctx: PipeContext, stages: dict[str, StageResult]) -> PipeResult:
        errors = [
            f"{name}: {r.error}" for name, r in stages.items()
            if r.status == StageStatus.FAILED and r.error
        ]
        skipped = [
            name for name, r in stages.items()
            if r.status == StageStatus.SKIPPED
        ]

        # Determine overall status
        statuses = [r.status for r in stages.values()]
        non_skip = [s for s in statuses if s != StageStatus.SKIPPED]

        if not non_skip or all(s == StageStatus.FAILED for s in non_skip):
            status = PipeStatus.FAILED
        elif all(s == StageStatus.SUCCESS for s in non_skip):
            status = PipeStatus.SUCCESS
        else:
            status = PipeStatus.PARTIAL

        return PipeResult(
            status=status,
            source=ctx.metadata.get("source", ""),
            input_rows=ctx.metadata.get("input_rows", 0),
            stages=stages,
            artifacts=dict(ctx.artifacts),
            skipped=skipped,
            errors=errors,
            reasoning=dict(ctx.reasoning),
            timing=dict(ctx.timing),
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_reporter.py -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add goldenpipe/engine/reporter.py tests/test_reporter.py
git commit -m "feat: Reporter — PipeResult construction with status logic"
```

---

## Task 11: Decisions (evolved from v0.1)

**Files:**
- Modify: `goldenpipe/decisions.py`
- Modify: `tests/test_decisions.py`

- [ ] **Step 1: Rewrite test_decisions.py**

File: `tests/test_decisions.py`

```python
"""Tests for built-in decision functions."""
from goldenpipe.decisions import severity_gate, pii_router, row_count_gate
from goldenpipe.models.context import PipeContext, Decision


class TestSeverityGate:
    def test_no_findings(self):
        ctx = PipeContext()
        d = severity_gate(ctx)
        assert d is None

    def test_no_critical(self):
        ctx = PipeContext()
        ctx.artifacts["findings"] = [
            {"severity": "warning", "check": "nulls"},
        ]
        d = severity_gate(ctx)
        assert d is None

    def test_critical_aborts(self):
        ctx = PipeContext()
        ctx.artifacts["findings"] = [
            {"severity": "critical", "check": "schema_mismatch"},
        ]
        d = severity_gate(ctx)
        assert d is not None
        assert d.abort is True

    def test_mixed_does_not_abort(self):
        ctx = PipeContext()
        ctx.artifacts["findings"] = [
            {"severity": "critical", "check": "schema"},
            {"severity": "warning", "check": "nulls"},
        ]
        # Mixed: has non-critical findings, so don't abort
        d = severity_gate(ctx)
        assert d is None


class TestPiiRouter:
    def test_no_findings(self):
        ctx = PipeContext()
        d = pii_router(ctx)
        assert d is None

    def test_no_pii(self):
        ctx = PipeContext()
        ctx.artifacts["findings"] = [
            {"check": "nulls"},
        ]
        d = pii_router(ctx)
        assert d is None

    def test_pii_detected(self):
        ctx = PipeContext()
        ctx.artifacts["findings"] = [
            {"check": "pii_detection"},
        ]
        d = pii_router(ctx)
        assert d is not None
        assert "goldenmatch.dedupe" in d.skip
        assert "goldenmatch.dedupe_pprl" in d.insert

    def test_pii_preserves_other_stages(self):
        ctx = PipeContext()
        ctx.artifacts["findings"] = [
            {"check": "pii_detection"},
        ]
        d = pii_router(ctx)
        assert d.abort is False


class TestRowCountGate:
    def test_zero_rows(self):
        ctx = PipeContext(metadata={"input_rows": 0})
        d = row_count_gate(ctx)
        assert d is not None
        assert "goldenmatch.dedupe" in d.skip

    def test_one_row(self):
        ctx = PipeContext(metadata={"input_rows": 1})
        d = row_count_gate(ctx)
        assert d is not None

    def test_two_rows_no_skip(self):
        ctx = PipeContext(metadata={"input_rows": 2})
        d = row_count_gate(ctx)
        assert d is None

    def test_many_rows_no_skip(self):
        ctx = PipeContext(metadata={"input_rows": 1000})
        d = row_count_gate(ctx)
        assert d is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_decisions.py -v`
Expected: FAIL

- [ ] **Step 3: Rewrite decisions.py**

File: `goldenpipe/decisions.py`

```python
"""Built-in decision functions for pipeline routing."""
from __future__ import annotations

from goldenpipe.models.context import Decision, PipeContext


def severity_gate(ctx: PipeContext) -> Decision | None:
    """Abort pipeline if ALL findings are critical severity."""
    findings = ctx.artifacts.get("findings")
    if not findings:
        return None

    severities = {f.get("severity", "") for f in findings}
    if severities == {"critical"}:
        return Decision(abort=True, reason="All findings are critical severity")
    return None


def pii_router(ctx: PipeContext) -> Decision | None:
    """Route to PPRL matching if PII is detected."""
    findings = ctx.artifacts.get("findings")
    if not findings:
        return None

    has_pii = any(f.get("check") == "pii_detection" for f in findings)
    if has_pii:
        return Decision(
            skip=["goldenmatch.dedupe"],
            insert=["goldenmatch.dedupe_pprl"],
            reason="PII detected, routing to PPRL matching",
        )
    return None


def row_count_gate(ctx: PipeContext) -> Decision | None:
    """Skip matching if fewer than 2 rows."""
    row_count = ctx.metadata.get("input_rows", 0)
    if row_count < 2:
        return Decision(
            skip=["goldenmatch.dedupe"],
            reason=f"Only {row_count} row(s), skipping deduplication",
        )
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_decisions.py -v`
Expected: All 11 tests PASS

- [ ] **Step 5: Commit**

```bash
git add goldenpipe/decisions.py tests/test_decisions.py
git commit -m "feat: decision functions — severity_gate, pii_router, row_count_gate"
```

---

## Task 12: Golden Suite Adapters

**Files:**
- Create: `goldenpipe/adapters/__init__.py`
- Create: `goldenpipe/adapters/check.py`
- Create: `goldenpipe/adapters/flow.py`
- Create: `goldenpipe/adapters/match.py`
- Create: `tests/test_adapters.py`

- [ ] **Step 1: Write failing tests**

File: `tests/test_adapters.py`

```python
"""Tests for Golden Suite adapters (mocked)."""
import pytest
from unittest.mock import MagicMock, patch
import polars as pl

from goldenpipe.models.context import PipeContext, StageStatus


class TestScanStage:
    def test_info(self):
        from goldenpipe.adapters.check import ScanStage
        s = ScanStage()
        assert s.info.name == "goldencheck.scan"
        assert "findings" in s.info.produces
        assert "df" in s.info.consumes

    @patch("goldenpipe.adapters.check.HAS_CHECK", False)
    def test_validate_raises_without_tool(self):
        from goldenpipe.adapters.check import ScanStage
        s = ScanStage()
        with pytest.raises(RuntimeError, match="not installed"):
            s.validate(PipeContext())

    @patch("goldenpipe.adapters.check.HAS_CHECK", True)
    def test_run_success(self, sample_df):
        from goldenpipe.adapters import check
        mock_result = MagicMock()
        mock_result.findings = [{"severity": "warning", "check": "nulls"}]
        with patch.object(check, "_scan", return_value=mock_result):
            from goldenpipe.adapters.check import ScanStage
            s = ScanStage()
            ctx = PipeContext(df=sample_df, metadata={"source": "test.csv"})
            result = s.run(ctx)
            assert result.status == StageStatus.SUCCESS
            assert "findings" in ctx.artifacts


class TestTransformStage:
    def test_info(self):
        from goldenpipe.adapters.flow import TransformStage
        s = TransformStage()
        assert s.info.name == "goldenflow.transform"
        assert "df" in s.info.produces

    @patch("goldenpipe.adapters.flow.HAS_FLOW", False)
    def test_validate_raises_without_tool(self):
        from goldenpipe.adapters.flow import TransformStage
        s = TransformStage()
        with pytest.raises(RuntimeError, match="not installed"):
            s.validate(PipeContext())


class TestDedupeStage:
    def test_info(self):
        from goldenpipe.adapters.match import DedupeStage
        s = DedupeStage()
        assert s.info.name == "goldenmatch.dedupe"
        assert "clusters" in s.info.produces
        assert "golden" in s.info.produces
        assert "df" in s.info.consumes

    @patch("goldenpipe.adapters.match.HAS_MATCH", False)
    def test_validate_raises_without_tool(self):
        from goldenpipe.adapters.match import DedupeStage
        s = DedupeStage()
        with pytest.raises(RuntimeError, match="not installed"):
            s.validate(PipeContext())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_adapters.py -v`
Expected: FAIL

- [ ] **Step 3: Implement adapters**

File: `goldenpipe/adapters/__init__.py`

```python
"""Golden Suite adapters with lazy imports."""
```

File: `goldenpipe/adapters/check.py`

```python
"""GoldenCheck adapter — wraps scan_file()."""
from __future__ import annotations

from goldenpipe.models.context import PipeContext, StageResult, StageStatus
from goldenpipe.models.stage import StageInfo

try:
    from goldencheck import scan_file as _scan
    HAS_CHECK = True
except ImportError:
    HAS_CHECK = False
    _scan = None  # type: ignore[assignment]


class ScanStage:
    """Wraps goldencheck.scan_file() as a pipeline stage."""

    info = StageInfo(
        name="goldencheck.scan",
        produces=["findings"],
        consumes=["df"],
    )
    rollback = None

    def validate(self, ctx: PipeContext) -> None:
        if not HAS_CHECK:
            raise RuntimeError(
                "GoldenCheck not installed. Run: pip install goldenpipe[check]"
            )

    def run(self, ctx: PipeContext) -> StageResult:
        source = ctx.metadata.get("source", "")
        result = _scan(source)
        findings = []
        if hasattr(result, "findings"):
            for f in result.findings:
                if isinstance(f, dict):
                    findings.append(f)
                else:
                    findings.append({
                        "severity": getattr(f, "severity", "info"),
                        "check": getattr(f, "check", "unknown"),
                        "column": getattr(f, "column", ""),
                        "message": getattr(f, "message", ""),
                    })
        ctx.artifacts["findings"] = findings
        return StageResult(status=StageStatus.SUCCESS)
```

File: `goldenpipe/adapters/flow.py`

```python
"""GoldenFlow adapter — wraps transform_df()."""
from __future__ import annotations

from goldenpipe.models.context import PipeContext, StageResult, StageStatus
from goldenpipe.models.stage import StageInfo

try:
    from goldenflow import transform_df as _transform
    HAS_FLOW = True
except ImportError:
    HAS_FLOW = False
    _transform = None  # type: ignore[assignment]


class TransformStage:
    """Wraps goldenflow.transform_df() as a pipeline stage."""

    info = StageInfo(
        name="goldenflow.transform",
        produces=["df", "manifest"],
        consumes=["df"],
    )
    rollback = None

    def validate(self, ctx: PipeContext) -> None:
        if not HAS_FLOW:
            raise RuntimeError(
                "GoldenFlow not installed. Run: pip install goldenpipe[flow]"
            )

    def run(self, ctx: PipeContext) -> StageResult:
        result = _transform(ctx.df)
        if hasattr(result, "df"):
            ctx.df = result.df
        if hasattr(result, "manifest"):
            ctx.artifacts["manifest"] = result.manifest
        return StageResult(status=StageStatus.SUCCESS)
```

File: `goldenpipe/adapters/match.py`

```python
"""GoldenMatch adapter — wraps dedupe_df()."""
from __future__ import annotations

from goldenpipe.models.context import PipeContext, StageResult, StageStatus
from goldenpipe.models.stage import StageInfo

try:
    from goldenmatch import dedupe_df as _dedupe
    HAS_MATCH = True
except ImportError:
    HAS_MATCH = False
    _dedupe = None  # type: ignore[assignment]


class DedupeStage:
    """Wraps goldenmatch.dedupe_df() as a pipeline stage."""

    info = StageInfo(
        name="goldenmatch.dedupe",
        produces=["clusters", "golden"],
        consumes=["df"],
    )
    rollback = None

    def validate(self, ctx: PipeContext) -> None:
        if not HAS_MATCH:
            raise RuntimeError(
                "GoldenMatch not installed. Run: pip install goldenpipe[match]"
            )

    def run(self, ctx: PipeContext) -> StageResult:
        result = _dedupe(ctx.df)
        if hasattr(result, "clusters"):
            ctx.artifacts["clusters"] = result.clusters
        if hasattr(result, "golden"):
            ctx.artifacts["golden"] = result.golden
        return StageResult(status=StageStatus.SUCCESS)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_adapters.py -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add goldenpipe/adapters/ tests/test_adapters.py
git commit -m "feat: Golden Suite adapters — check, flow, match with lazy imports"
```

---

## Task 13: Pipeline Wrapper + Python API

**Files:**
- Modify: `goldenpipe/pipeline.py`
- Create: `goldenpipe/_api.py`
- Create: `tests/test_api.py`
- Modify: `tests/test_pipeline.py`
- Modify: `goldenpipe/__init__.py`

- [ ] **Step 1: Write failing tests for Python API**

File: `tests/test_api.py`

```python
"""Tests for Python API."""
import polars as pl
import pytest
from goldenpipe._api import run, run_df, run_stages
from goldenpipe.models.context import PipeContext, StageResult, StageStatus, PipeStatus
from goldenpipe.models.stage import stage


@stage(name="upper", produces=["df"], consumes=["df"])
def upper_stage(ctx: PipeContext) -> StageResult:
    ctx.df = ctx.df.with_columns(pl.col("name").str.to_uppercase())
    return StageResult(status=StageStatus.SUCCESS)


class TestRun:
    def test_run_csv(self, sample_csv):
        result = run(str(sample_csv))
        assert result.source == str(sample_csv)
        assert result.input_rows == 5
        # No Golden Suite installed, so stages will fail/skip
        assert result.status in (PipeStatus.SUCCESS, PipeStatus.PARTIAL, PipeStatus.FAILED)

    def test_run_nonexistent(self):
        result = run("/nonexistent.csv")
        assert result.status == PipeStatus.FAILED


class TestRunDf:
    def test_run_df(self, sample_df):
        result = run_df(sample_df)
        assert result.input_rows == 5

    def test_run_df_empty(self):
        df = pl.DataFrame({"a": []})
        result = run_df(df)
        assert result.input_rows == 0


class TestRunStages:
    def test_custom_stages(self, sample_df):
        result = run_stages([upper_stage], sample_df)
        assert result.status == PipeStatus.SUCCESS
        assert result.artifacts.get("df") is None  # df is on ctx.df, not artifacts
        # Check the stage ran
        assert "upper" in result.stages

    def test_empty_stages(self, sample_df):
        result = run_stages([], sample_df)
        assert result.status == PipeStatus.SUCCESS
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_api.py -v`
Expected: FAIL

- [ ] **Step 3: Rewrite pipeline.py as thin wrapper**

File: `goldenpipe/pipeline.py`

```python
"""Pipeline — thin wrapper over the engine layer."""
from __future__ import annotations

from pathlib import Path

import polars as pl

from goldenpipe.engine.registry import StageRegistry
from goldenpipe.engine.resolver import Resolver
from goldenpipe.engine.runner import Runner
from goldenpipe.engine.reporter import Reporter
from goldenpipe.models.config import PipelineConfig, StageSpec
from goldenpipe.models.context import PipeContext, PipeResult, PipeStatus, StageStatus


class Pipeline:
    """High-level pipeline orchestrator."""

    def __init__(
        self,
        config: PipelineConfig | None = None,
        registry: StageRegistry | None = None,
    ) -> None:
        self._config = config
        self._registry = registry or StageRegistry()
        if registry is None:
            self._registry.discover()

    def run(self, source: str | None = None, df: pl.DataFrame | None = None) -> PipeResult:
        """Execute the pipeline."""
        ctx = PipeContext()

        # Load data
        if df is not None:
            ctx.df = df
            ctx.metadata["source"] = "<DataFrame>"
            ctx.metadata["input_rows"] = len(df)
        elif source:
            try:
                ctx.df = pl.read_csv(source, ignore_errors=True)
                ctx.metadata["source"] = source
                ctx.metadata["input_rows"] = len(ctx.df)
            except Exception as e:
                return PipeResult(
                    status=PipeStatus.FAILED,
                    source=source or "",
                    input_rows=0,
                    errors=[f"Failed to load data: {e}"],
                )
        else:
            return PipeResult(
                status=PipeStatus.FAILED,
                source="",
                input_rows=0,
                errors=["No source file or DataFrame provided"],
            )

        # Build config if not provided
        config = self._config or self._auto_config()

        # Resolve and run
        try:
            plan = Resolver.resolve(config, self._registry)
        except Exception as e:
            return PipeResult(
                status=PipeStatus.FAILED,
                source=ctx.metadata.get("source", ""),
                input_rows=ctx.metadata.get("input_rows", 0),
                errors=[f"Pipeline resolution failed: {e}"],
            )

        runner = Runner(registry=self._registry)
        stages = runner.run(plan, ctx)
        return Reporter.build(ctx, stages)

    def _auto_config(self) -> PipelineConfig:
        """Build config from discovered stages."""
        available = self._registry.list_all()
        stage_specs: list[StageSpec | str] = []
        for name in ["goldencheck.scan", "goldenflow.transform", "goldenmatch.dedupe"]:
            if name in available:
                stage_specs.append(StageSpec(use=name))
        return PipelineConfig(pipeline="auto", stages=stage_specs)
```

- [ ] **Step 4: Implement _api.py**

File: `goldenpipe/_api.py`

```python
"""Python API — convenience functions for running pipelines."""
from __future__ import annotations

from typing import Any

import polars as pl

from goldenpipe.config.loader import load_config
from goldenpipe.engine.registry import StageRegistry
from goldenpipe.engine.resolver import Resolver
from goldenpipe.engine.runner import Runner
from goldenpipe.engine.reporter import Reporter
from goldenpipe.models.config import PipelineConfig, StageSpec
from goldenpipe.models.context import PipeContext, PipeResult, PipeStatus


def run(source: str, config: str | None = None) -> PipeResult:
    """Run a pipeline on a file. Zero-config or from YAML."""
    from goldenpipe.pipeline import Pipeline

    pipeline_config = load_config(config) if config else None
    pipe = Pipeline(config=pipeline_config)
    return pipe.run(source=source)


def run_df(
    df: pl.DataFrame,
    config: str | PipelineConfig | None = None,
) -> PipeResult:
    """Run a pipeline on a DataFrame."""
    from goldenpipe.pipeline import Pipeline

    if isinstance(config, str):
        pipeline_config = load_config(config)
    else:
        pipeline_config = config
    pipe = Pipeline(config=pipeline_config)
    return pipe.run(df=df)


def run_stages(
    stages: list[Any],
    df: pl.DataFrame,
) -> PipeResult:
    """Run specific stages programmatically."""
    registry = StageRegistry()
    for s in stages:
        registry.register(s)

    stage_specs = [StageSpec(use=s.info.name) for s in stages]
    config = PipelineConfig(pipeline="programmatic", stages=stage_specs)

    ctx = PipeContext(
        df=df,
        metadata={"source": "<programmatic>", "input_rows": len(df)},
    )

    plan = Resolver.resolve(config, registry)
    # Remove auto-prepended load stage (we already have df)
    plan.stages = [s for s in plan.stages if s.name != "load"]

    runner = Runner(registry=registry)
    results = runner.run(plan, ctx)
    return Reporter.build(ctx, results)
```

- [ ] **Step 5: Update __init__.py**

File: `goldenpipe/__init__.py`

```python
"""GoldenPipe — pluggable pipeline framework for data quality."""
__version__ = "1.0.0"

from goldenpipe._api import run, run_df, run_stages
from goldenpipe.pipeline import Pipeline
from goldenpipe.models.context import (
    PipeContext, PipeResult, StageResult, Decision,
    StageStatus, PipeStatus,
)
from goldenpipe.models.stage import StageInfo, Stage, stage
from goldenpipe.models.config import StageSpec, PipelineConfig
from goldenpipe.config.loader import load_config
from goldenpipe.decisions import severity_gate, pii_router, row_count_gate

__all__ = [
    "run", "run_df", "run_stages",
    "Pipeline",
    "PipeContext", "PipeResult", "StageResult", "Decision",
    "StageStatus", "PipeStatus",
    "StageInfo", "Stage", "stage",
    "StageSpec", "PipelineConfig",
    "load_config",
    "severity_gate", "pii_router", "row_count_gate",
    "__version__",
]
```

- [ ] **Step 6: Rewrite test_pipeline.py**

File: `tests/test_pipeline.py`

```python
"""Tests for Pipeline class."""
from goldenpipe.pipeline import Pipeline
from goldenpipe.models.config import PipelineConfig, StageSpec
from goldenpipe.models.context import PipeStatus, StageStatus
from goldenpipe.models.stage import stage
from goldenpipe.models.context import PipeContext, StageResult
from goldenpipe.engine.registry import StageRegistry


@stage(name="noop", produces=["df"], consumes=["df"])
def noop_stage(ctx: PipeContext) -> StageResult:
    return StageResult(status=StageStatus.SUCCESS)


class TestPipeline:
    def test_run_csv(self, sample_csv):
        reg = StageRegistry()
        reg.register(noop_stage)
        config = PipelineConfig(pipeline="test", stages=[StageSpec(use="noop")])
        pipe = Pipeline(config=config, registry=reg)
        result = pipe.run(source=str(sample_csv))
        assert result.status == PipeStatus.SUCCESS
        assert result.input_rows == 5

    def test_run_df(self, sample_df):
        reg = StageRegistry()
        reg.register(noop_stage)
        config = PipelineConfig(pipeline="test", stages=[StageSpec(use="noop")])
        pipe = Pipeline(config=config, registry=reg)
        result = pipe.run(df=sample_df)
        assert result.status == PipeStatus.SUCCESS
        assert result.input_rows == 5

    def test_run_no_source(self):
        pipe = Pipeline()
        result = pipe.run()
        assert result.status == PipeStatus.FAILED

    def test_run_nonexistent_file(self):
        pipe = Pipeline()
        result = pipe.run(source="/nonexistent.csv")
        assert result.status == PipeStatus.FAILED

    def test_auto_config(self):
        pipe = Pipeline()
        config = pipe._auto_config()
        assert config.pipeline == "auto"


class TestImports:
    def test_public_api(self):
        import goldenpipe as gp
        assert hasattr(gp, "run")
        assert hasattr(gp, "run_df")
        assert hasattr(gp, "run_stages")
        assert hasattr(gp, "Pipeline")
        assert hasattr(gp, "PipeResult")
        assert hasattr(gp, "stage")
        assert hasattr(gp, "__version__")
        assert gp.__version__ == "1.0.0"
```

- [ ] **Step 7: Run all tests**

Run: `pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 8: Commit**

```bash
git add goldenpipe/__init__.py goldenpipe/pipeline.py goldenpipe/_api.py tests/test_api.py tests/test_pipeline.py
git commit -m "feat: Pipeline wrapper + Python API — run(), run_df(), run_stages()"
```

---

## Task 14: CLI (expanded)

**Files:**
- Modify: `goldenpipe/cli/main.py`
- Create: `tests/test_cli.py`

- [ ] **Step 1: Write failing tests**

File: `tests/test_cli.py`

```python
"""Tests for CLI commands."""
from typer.testing import CliRunner
from goldenpipe.cli.main import app

runner = CliRunner()


class TestRunCommand:
    def test_run_csv(self, sample_csv):
        result = runner.invoke(app, ["run", str(sample_csv)])
        assert result.exit_code == 0

    def test_run_nonexistent(self):
        result = runner.invoke(app, ["run", "/nonexistent.csv"])
        assert result.exit_code == 0  # graceful failure
        assert "FAILED" in result.stdout or "failed" in result.stdout.lower()

    def test_run_verbose(self, sample_csv):
        result = runner.invoke(app, ["run", str(sample_csv), "--verbose"])
        assert result.exit_code == 0

    def test_run_with_config(self, sample_csv, tmp_path):
        config = tmp_path / "pipe.yml"
        config.write_text(
            f"pipeline: test\nsource: {sample_csv}\nstages: []\n"
        )
        result = runner.invoke(app, ["run", str(sample_csv), "--config", str(config)])
        assert result.exit_code == 0


class TestStagesCommand:
    def test_list_stages(self):
        result = runner.invoke(app, ["stages"])
        assert result.exit_code == 0


class TestValidateCommand:
    def test_validate(self, tmp_path):
        config = tmp_path / "pipe.yml"
        config.write_text("pipeline: test\nstages: []\n")
        result = runner.invoke(app, ["validate", "--config", str(config)])
        assert result.exit_code == 0


class TestInitCommand:
    def test_init(self, tmp_path):
        result = runner.invoke(app, ["init", "--dir", str(tmp_path)])
        assert result.exit_code == 0
        assert (tmp_path / "goldenpipe.yml").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli.py -v`
Expected: FAIL

- [ ] **Step 3: Rewrite cli/main.py**

File: `goldenpipe/cli/main.py`

```python
"""GoldenPipe CLI — pipeline framework for data quality."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(help="GoldenPipe -- pluggable pipeline framework for data quality")
console = Console()


@app.command()
def run(
    source: str = typer.Argument(..., help="Input file path"),
    config: Optional[str] = typer.Option(None, "--config", "-c", help="Pipeline YAML config"),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Output file path"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show reasoning and timing"),
) -> None:
    """Run a pipeline on a data file."""
    from goldenpipe._api import run as gp_run
    result = gp_run(source, config=config)

    # Status table
    table = Table(title=f"GoldenPipe: {result.source}")
    table.add_column("Stage", style="bold")
    table.add_column("Status")
    table.add_column("Details")

    for name, sr in result.stages.items():
        color = {"success": "green", "skipped": "yellow", "failed": "red"}.get(
            sr.status.value, "dim"
        )
        details = sr.error or ""
        table.add_row(name, f"[{color}]{sr.status.value}[/{color}]", details)

    console.print(table)
    console.print(
        f"\n[bold]{result.status.value.upper()}[/bold] | "
        f"{result.input_rows} rows | {result.source}"
    )

    if result.errors:
        console.print("\n[red]Errors:[/red]")
        for e in result.errors:
            console.print(f"  - {e}")

    if verbose:
        if result.reasoning:
            console.print("\n[bold]Reasoning:[/bold]")
            for k, v in result.reasoning.items():
                if not k.startswith("_"):
                    console.print(f"  {k}: {v}")
        if result.timing:
            console.print("\n[bold]Timing:[/bold]")
            for k, v in result.timing.items():
                console.print(f"  {k}: {v:.2f}s")

    if output and result.artifacts.get("golden") is not None:
        result.artifacts["golden"].write_csv(output)
        console.print(f"\nGolden records written to {output}")


@app.command()
def stages() -> None:
    """List all discovered stages."""
    from goldenpipe.engine.registry import StageRegistry

    reg = StageRegistry()
    reg.discover()
    all_stages = reg.list_all()

    table = Table(title="Discovered Stages")
    table.add_column("Name", style="bold")
    table.add_column("Produces")
    table.add_column("Consumes")

    for name, info in sorted(all_stages.items()):
        table.add_row(name, ", ".join(info.produces), ", ".join(info.consumes))

    console.print(table)
    console.print(f"\n{len(all_stages)} stage(s) found")


@app.command()
def validate(
    config: str = typer.Option(..., "--config", "-c", help="Pipeline YAML config"),
) -> None:
    """Dry-run wiring validation without executing."""
    from goldenpipe.config.loader import load_config
    from goldenpipe.engine.registry import StageRegistry
    from goldenpipe.engine.resolver import Resolver, WiringError

    try:
        cfg = load_config(config)
        reg = StageRegistry()
        reg.discover()
        plan = Resolver.resolve(cfg, reg)
        console.print(f"[green]Valid[/green] -- {len(plan.stages)} stages resolved")
        for s in plan.stages:
            console.print(f"  {s.name}")
    except WiringError as e:
        console.print(f"[red]Wiring Error:[/red] {e}")
        raise typer.Exit(code=1)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(code=1)


@app.command()
def init(
    dir: str = typer.Option(".", "--dir", "-d", help="Directory to create config in"),
) -> None:
    """Generate a starter goldenpipe.yml from installed tools."""
    from goldenpipe.engine.registry import StageRegistry

    reg = StageRegistry()
    reg.discover()
    all_stages = reg.list_all()

    lines = ["pipeline: my-pipeline", "stages:"]
    for name in sorted(all_stages.keys()):
        lines.append(f"  - {name}")

    if not all_stages:
        lines.append("  # No stages discovered. Install goldenpipe[golden-suite] or add custom stages.")

    out = Path(dir) / "goldenpipe.yml"
    out.write_text("\n".join(lines) + "\n")
    console.print(f"Created {out}")


@app.command()
def serve(
    port: int = typer.Option(8000, help="Port for REST API"),
) -> None:
    """Start the REST API server."""
    try:
        import uvicorn
        from goldenpipe.api.server import create_app
        uvicorn.run(create_app(), host="0.0.0.0", port=port)
    except ImportError:
        console.print("[red]FastAPI not installed. Run: pip install goldenpipe[api][/red]")
        raise typer.Exit(code=1)


@app.command(name="mcp-serve")
def mcp_serve() -> None:
    """Start the MCP server."""
    try:
        from goldenpipe.mcp.server import run_server
        run_server()
    except ImportError:
        console.print("[red]MCP not installed. Run: pip install goldenpipe[mcp][/red]")
        raise typer.Exit(code=1)


@app.command(name="agent-serve")
def agent_serve(
    port: int = typer.Option(8250, help="Port for A2A server"),
) -> None:
    """Start the A2A agent server."""
    try:
        from goldenpipe.a2a.server import run_server
        run_server(port=port)
    except ImportError:
        console.print("[red]aiohttp not installed. Run: pip install goldenpipe[agent][/red]")
        raise typer.Exit(code=1)


@app.command()
def interactive() -> None:
    """Launch the TUI."""
    try:
        from goldenpipe.tui.app import GoldenPipeApp
        GoldenPipeApp().run()
    except ImportError:
        console.print("[red]Textual not installed. Run: pip install goldenpipe[tui][/red]")
        raise typer.Exit(code=1)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cli.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add goldenpipe/cli/main.py tests/test_cli.py
git commit -m "feat: expanded CLI — run, stages, validate, init, serve, mcp-serve, agent-serve, interactive"
```

---

## Task 15: REST API

**Files:**
- Create: `goldenpipe/api/__init__.py`
- Create: `goldenpipe/api/server.py`
- Create: `tests/test_rest.py`

- [ ] **Step 1: Write failing tests**

File: `tests/test_rest.py`

```python
"""Tests for REST API."""
import pytest

try:
    from fastapi.testclient import TestClient
    from goldenpipe.api.server import create_app
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False

pytestmark = pytest.mark.skipif(not HAS_FASTAPI, reason="fastapi not installed")


@pytest.fixture
def client():
    return TestClient(create_app())


class TestHealthEndpoint:
    def test_health(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


class TestStagesEndpoint:
    def test_list_stages(self, client):
        r = client.get("/stages")
        assert r.status_code == 200
        assert isinstance(r.json(), dict)


class TestValidateEndpoint:
    def test_validate_empty(self, client):
        r = client.post("/validate", json={"pipeline": "test", "stages": []})
        assert r.status_code == 200


class TestRunEndpoint:
    def test_run_no_source(self, client):
        r = client.post("/run", json={"pipeline": "test", "stages": []})
        assert r.status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_rest.py -v`
Expected: FAIL

- [ ] **Step 3: Implement server.py**

File: `goldenpipe/api/__init__.py`

```python
"""REST API server."""
```

File: `goldenpipe/api/server.py`

```python
"""FastAPI REST API for GoldenPipe."""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel

from goldenpipe.engine.registry import StageRegistry
from goldenpipe.engine.resolver import Resolver, WiringError
from goldenpipe.models.config import PipelineConfig


class RunRequest(BaseModel):
    pipeline: str
    source: str | None = None
    stages: list[Any] = []
    decisions: list[str] = []


def create_app() -> FastAPI:
    app = FastAPI(title="GoldenPipe", version="1.0.0")

    @app.get("/health")
    def health():
        return {"status": "ok", "version": "1.0.0"}

    @app.get("/stages")
    def list_stages():
        reg = StageRegistry()
        reg.discover()
        return {
            name: {"produces": info.produces, "consumes": info.consumes}
            for name, info in reg.list_all().items()
        }

    @app.post("/validate")
    def validate(req: RunRequest):
        try:
            config = PipelineConfig(
                pipeline=req.pipeline, stages=req.stages, decisions=req.decisions,
            )
            reg = StageRegistry()
            reg.discover()
            plan = Resolver.resolve(config, reg)
            return {
                "valid": True,
                "stages": [s.name for s in plan.stages],
            }
        except (WiringError, KeyError) as e:
            return {"valid": False, "error": str(e)}

    @app.post("/run")
    def run_pipeline(req: RunRequest):
        from goldenpipe._api import run
        result = run(req.source or "", config=None)
        return {
            "status": result.status.value,
            "source": result.source,
            "input_rows": result.input_rows,
            "errors": result.errors,
            "skipped": result.skipped,
            "timing": result.timing,
        }

    return app
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_rest.py -v`
Expected: All 4 tests PASS (or skipped if FastAPI not installed)

- [ ] **Step 5: Commit**

```bash
git add goldenpipe/api/ tests/test_rest.py
git commit -m "feat: REST API — /health, /stages, /validate, /run"
```

---

## Task 16: MCP Server

**Files:**
- Create: `goldenpipe/mcp/__init__.py`
- Create: `goldenpipe/mcp/server.py`
- Create: `tests/test_mcp.py`

- [ ] **Step 1: Write failing tests**

File: `tests/test_mcp.py`

```python
"""Tests for MCP server tools."""
import pytest

try:
    from goldenpipe.mcp.server import list_stages_tool, validate_pipeline_tool
    HAS_MCP = True
except ImportError:
    HAS_MCP = False

pytestmark = pytest.mark.skipif(not HAS_MCP, reason="mcp not installed")


class TestListStagesTool:
    def test_returns_dict(self):
        result = list_stages_tool()
        assert isinstance(result, dict)


class TestValidatePipelineTool:
    def test_empty_pipeline(self):
        result = validate_pipeline_tool(pipeline="test", stages=[])
        assert "valid" in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_mcp.py -v`
Expected: FAIL

- [ ] **Step 3: Implement MCP server**

File: `goldenpipe/mcp/__init__.py`

```python
"""MCP server for GoldenPipe."""
```

File: `goldenpipe/mcp/server.py`

```python
"""MCP server with pipeline tools."""
from __future__ import annotations

from typing import Any

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import Tool, TextContent
    HAS_MCP = True
except ImportError:
    HAS_MCP = False

from goldenpipe.engine.registry import StageRegistry
from goldenpipe.engine.resolver import Resolver, WiringError
from goldenpipe.models.config import PipelineConfig, StageSpec


def list_stages_tool() -> dict[str, Any]:
    """List all discovered stages."""
    reg = StageRegistry()
    reg.discover()
    return {
        name: {"produces": info.produces, "consumes": info.consumes}
        for name, info in reg.list_all().items()
    }


def validate_pipeline_tool(pipeline: str, stages: list[str]) -> dict[str, Any]:
    """Validate pipeline wiring."""
    try:
        specs = [StageSpec(use=s) for s in stages]
        config = PipelineConfig(pipeline=pipeline, stages=specs)
        reg = StageRegistry()
        reg.discover()
        plan = Resolver.resolve(config, reg)
        return {"valid": True, "stages": [s.name for s in plan.stages]}
    except (WiringError, KeyError) as e:
        return {"valid": False, "error": str(e)}


def run_pipeline_tool(source: str, config_path: str | None = None) -> dict[str, Any]:
    """Run a pipeline and return results."""
    from goldenpipe._api import run
    result = run(source, config=config_path)
    return {
        "status": result.status.value,
        "source": result.source,
        "input_rows": result.input_rows,
        "errors": result.errors,
        "skipped": result.skipped,
    }


def explain_pipeline_tool(config_path: str) -> dict[str, Any]:
    """Explain what a pipeline config will do."""
    from goldenpipe.config.loader import load_config
    config = load_config(config_path)
    reg = StageRegistry()
    reg.discover()
    try:
        plan = Resolver.resolve(config, reg)
        return {
            "pipeline": config.pipeline,
            "stages": [
                {"name": s.name, "produces": s.stage.info.produces, "consumes": s.stage.info.consumes}
                for s in plan.stages
            ],
        }
    except Exception as e:
        return {"error": str(e)}


def run_server() -> None:
    """Start the MCP server."""
    if not HAS_MCP:
        raise ImportError("MCP not installed. Run: pip install goldenpipe[mcp]")

    import asyncio

    server = Server("goldenpipe")

    @server.list_tools()
    async def handle_list_tools():
        return [
            Tool(name="list_stages", description="List all discovered pipeline stages",
                 inputSchema={"type": "object", "properties": {}}),
            Tool(name="validate_pipeline", description="Validate pipeline wiring",
                 inputSchema={"type": "object", "properties": {
                     "pipeline": {"type": "string"},
                     "stages": {"type": "array", "items": {"type": "string"}},
                 }, "required": ["pipeline", "stages"]}),
            Tool(name="run_pipeline", description="Run a pipeline on a file",
                 inputSchema={"type": "object", "properties": {
                     "source": {"type": "string"},
                     "config_path": {"type": "string"},
                 }, "required": ["source"]}),
            Tool(name="explain_pipeline", description="Explain what a pipeline config does",
                 inputSchema={"type": "object", "properties": {
                     "config_path": {"type": "string"},
                 }, "required": ["config_path"]}),
        ]

    @server.call_tool()
    async def handle_call_tool(name: str, arguments: dict):
        if name == "list_stages":
            result = list_stages_tool()
        elif name == "validate_pipeline":
            result = validate_pipeline_tool(**arguments)
        elif name == "run_pipeline":
            result = run_pipeline_tool(**arguments)
        elif name == "explain_pipeline":
            result = explain_pipeline_tool(**arguments)
        else:
            result = {"error": f"Unknown tool: {name}"}

        import json
        return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

    async def main():
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    asyncio.run(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_mcp.py -v`
Expected: PASS (or skipped)

- [ ] **Step 5: Commit**

```bash
git add goldenpipe/mcp/ tests/test_mcp.py
git commit -m "feat: MCP server — list_stages, validate, run, explain tools"
```

---

## Task 17: A2A Server

**Files:**
- Create: `goldenpipe/a2a/__init__.py`
- Create: `goldenpipe/a2a/server.py`
- Create: `tests/test_a2a.py`

- [ ] **Step 1: Write failing tests**

File: `tests/test_a2a.py`

```python
"""Tests for A2A server."""
import pytest
import json

try:
    from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop
    from aiohttp import web
    from goldenpipe.a2a.server import create_app
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False

pytestmark = pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")


@pytest.fixture
def a2a_client(aiohttp_client):
    return aiohttp_client(create_app())


class TestAgentCard:
    async def test_agent_card(self, a2a_client):
        client = await a2a_client
        resp = await client.get("/.well-known/agent.json")
        assert resp.status == 200
        data = await resp.json()
        assert data["name"] == "GoldenPipe"
        assert "skills" in data


class TestHealthEndpoint:
    async def test_health(self, a2a_client):
        client = await a2a_client
        resp = await client.get("/health")
        assert resp.status == 200
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_a2a.py -v`
Expected: FAIL

- [ ] **Step 3: Implement A2A server**

File: `goldenpipe/a2a/__init__.py`

```python
"""A2A agent server."""
```

File: `goldenpipe/a2a/server.py`

```python
"""A2A protocol server for GoldenPipe (aiohttp)."""
from __future__ import annotations

import json
from typing import Any

try:
    from aiohttp import web
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False


AGENT_CARD = {
    "name": "GoldenPipe",
    "description": "Pluggable pipeline framework for data quality workflows",
    "provider": {"organization": "Golden Suite"},
    "version": "1.0.0",
    "url": "http://localhost:8250",
    "skills": [
        {
            "id": "run-pipeline",
            "name": "Run Pipeline",
            "description": "Execute a data quality pipeline",
            "inputModes": ["text"],
            "outputModes": ["text"],
        },
        {
            "id": "validate-pipeline",
            "name": "Validate Pipeline",
            "description": "Validate pipeline wiring without executing",
            "inputModes": ["text"],
            "outputModes": ["text"],
        },
        {
            "id": "list-stages",
            "name": "List Stages",
            "description": "List all discovered pipeline stages",
            "inputModes": ["text"],
            "outputModes": ["text"],
        },
        {
            "id": "explain-pipeline",
            "name": "Explain Pipeline",
            "description": "Describe what a pipeline config will do",
            "inputModes": ["text"],
            "outputModes": ["text"],
        },
    ],
}


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/.well-known/agent.json", agent_card)
    app.router.add_get("/health", health)
    app.router.add_post("/tasks", handle_task)
    return app


async def agent_card(request: web.Request) -> web.Response:
    return web.json_response(AGENT_CARD)


async def health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "version": "1.0.0"})


async def handle_task(request: web.Request) -> web.Response:
    body = await request.json()
    skill_id = body.get("skill", "")
    params = body.get("params", {})

    if skill_id == "list-stages":
        from goldenpipe.mcp.server import list_stages_tool
        result = list_stages_tool()
    elif skill_id == "validate-pipeline":
        from goldenpipe.mcp.server import validate_pipeline_tool
        result = validate_pipeline_tool(**params)
    elif skill_id == "run-pipeline":
        from goldenpipe.mcp.server import run_pipeline_tool
        result = run_pipeline_tool(**params)
    elif skill_id == "explain-pipeline":
        from goldenpipe.mcp.server import explain_pipeline_tool
        result = explain_pipeline_tool(**params)
    else:
        result = {"error": f"Unknown skill: {skill_id}"}

    return web.json_response({
        "id": body.get("id", ""),
        "status": "completed",
        "result": result,
    })


def run_server(port: int = 8250) -> None:
    if not HAS_AIOHTTP:
        raise ImportError("aiohttp not installed. Run: pip install goldenpipe[agent]")
    web.run_app(create_app(), port=port)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_a2a.py -v`
Expected: PASS (or skipped)

- [ ] **Step 5: Commit**

```bash
git add goldenpipe/a2a/ tests/test_a2a.py
git commit -m "feat: A2A server — agent card, health, task handler on port 8250"
```

---

## Task 18: TUI

**Files:**
- Create: `goldenpipe/tui/__init__.py`
- Create: `goldenpipe/tui/app.py`
- Create: `tests/test_tui.py`

- [ ] **Step 1: Write failing tests**

File: `tests/test_tui.py`

```python
"""Tests for Textual TUI."""
import pytest

try:
    from textual.app import App
    from goldenpipe.tui.app import GoldenPipeApp
    HAS_TEXTUAL = True
except ImportError:
    HAS_TEXTUAL = False

pytestmark = pytest.mark.skipif(not HAS_TEXTUAL, reason="textual not installed")


class TestGoldenPipeApp:
    async def test_app_launches(self):
        app = GoldenPipeApp()
        async with app.run_test(size=(120, 40)) as pilot:
            assert app.title == "GoldenPipe"

    async def test_tabs_exist(self):
        app = GoldenPipeApp()
        async with app.run_test(size=(120, 40)) as pilot:
            tabs = app.query("Tab")
            assert len(tabs) == 4

    async def test_tab_labels(self):
        app = GoldenPipeApp()
        async with app.run_test(size=(120, 40)) as pilot:
            tab_labels = [t.label.plain for t in app.query("Tab")]
            assert "Pipeline" in tab_labels
            assert "Config" in tab_labels
            assert "Results" in tab_labels
            assert "Log" in tab_labels
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_tui.py -v`
Expected: FAIL

- [ ] **Step 3: Implement TUI**

File: `goldenpipe/tui/__init__.py`

```python
"""Textual TUI for GoldenPipe."""
```

File: `goldenpipe/tui/app.py`

```python
"""GoldenPipe TUI — 4-tab pipeline interface."""
from __future__ import annotations

try:
    from textual.app import App, ComposeResult
    from textual.containers import Container
    from textual.widgets import Header, Footer, Static, TabbedContent, TabPane, TextArea
    HAS_TEXTUAL = True
except ImportError:
    HAS_TEXTUAL = False


if HAS_TEXTUAL:
    class GoldenPipeApp(App):
        """GoldenPipe interactive TUI."""

        TITLE = "GoldenPipe"
        CSS_PATH = None

        BINDINGS = [
            ("q", "quit", "Quit"),
            ("1", "tab_pipeline", "Pipeline"),
            ("2", "tab_config", "Config"),
            ("3", "tab_results", "Results"),
            ("4", "tab_log", "Log"),
        ]

        def compose(self) -> ComposeResult:
            yield Header()
            with TabbedContent():
                with TabPane("Pipeline", id="tab-pipeline"):
                    yield Static("Pipeline stages will appear here.\nPress 'r' to run.")
                with TabPane("Config", id="tab-config"):
                    yield Static("YAML config editor.\nLoad a config file to edit.")
                with TabPane("Results", id="tab-results"):
                    yield Static("Artifacts browser.\nRun a pipeline to see results.")
                with TabPane("Log", id="tab-log"):
                    yield Static("Reasoning and timing log.\nRun a pipeline to see logs.")
            yield Footer()

        def action_tab_pipeline(self) -> None:
            self.query_one(TabbedContent).active = "tab-pipeline"

        def action_tab_config(self) -> None:
            self.query_one(TabbedContent).active = "tab-config"

        def action_tab_results(self) -> None:
            self.query_one(TabbedContent).active = "tab-results"

        def action_tab_log(self) -> None:
            self.query_one(TabbedContent).active = "tab-log"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_tui.py -v`
Expected: All 3 tests PASS (or skipped)

- [ ] **Step 5: Commit**

```bash
git add goldenpipe/tui/ tests/test_tui.py
git commit -m "feat: TUI — 4-tab Textual app (Pipeline, Config, Results, Log)"
```

---

## Task 19: Full Integration Tests + Final Validation

**Files:**
- Modify: `tests/conftest.py` (add aiohttp_client fixture if needed)

- [ ] **Step 1: Run entire test suite**

Run: `pytest tests/ -v --tb=short`
Expected: All tests PASS (interface tests skip if optional deps missing)

- [ ] **Step 2: Verify test count**

Run: `pytest tests/ --co -q | tail -1`
Expected: 80+ tests collected (140+ target is across all optional deps)

- [ ] **Step 3: Verify imports work**

Run: `python -c "import goldenpipe as gp; print(gp.__version__); print(dir(gp))"`
Expected: Prints `1.0.0` and all public exports

- [ ] **Step 4: Verify CLI works**

Run: `goldenpipe --help`
Expected: Shows all commands (run, stages, validate, init, serve, mcp-serve, agent-serve, interactive)

- [ ] **Step 5: Smoke test**

Run: `echo "name,email\nJohn,john@x.com\nJohn,j@x.com" > /tmp/test.csv && goldenpipe run /tmp/test.csv --verbose`
Expected: Runs pipeline, shows status table

- [ ] **Step 6: Final commit — version bump**

```bash
git add -A
git commit -m "chore: GoldenPipe v1.0.0 — pluggable pipeline framework"
```

---

## Execution Order Summary

| Task | Component | Depends On | Est. Tests |
|------|-----------|------------|-----------|
| 1 | pyproject.toml | — | 0 |
| 2 | models/context.py | — | 16 |
| 3 | models/stage.py | Task 2 | 8 |
| 4 | models/config.py | — | 6 |
| 5 | config/loader.py | Task 4 | 5 |
| 6 | engine/registry.py | Task 3 | 6 |
| 7 | engine/resolver.py | Task 6 | 7 |
| 8 | engine/router.py | Task 7 | 6 |
| 9 | engine/runner.py | Task 8 | 8 |
| 10 | engine/reporter.py | Task 2 | 6 |
| 11 | decisions.py | Task 2 | 11 |
| 12 | adapters/ | Task 3 | 6 |
| 13 | pipeline.py + _api.py | Tasks 5-10 | 11 |
| 14 | cli/main.py | Task 13 | 4+ |
| 15 | api/server.py | Task 13 | 4 |
| 16 | mcp/server.py | Task 13 | 2+ |
| 17 | a2a/server.py | Task 16 | 2+ |
| 18 | tui/app.py | — | 3 |
| 19 | Integration + validation | All | — |
| **Total** | | | **~110+** |

Tasks 1-12 can be parallelized in groups:
- **Group A (independent):** Tasks 1, 2, 4, 18
- **Group B (needs models):** Tasks 3, 5, 10, 11
- **Group C (needs engine):** Tasks 6, 7, 8, 9
- **Group D (needs all core):** Tasks 12, 13
- **Group E (needs API):** Tasks 14, 15, 16, 17
- **Group F (final):** Task 19
