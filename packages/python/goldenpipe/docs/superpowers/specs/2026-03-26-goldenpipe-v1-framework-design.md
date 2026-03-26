# GoldenPipe v1.0 — Pipeline Framework Design

**Date:** 2026-03-26
**Status:** Approved
**Author:** Ben Severn + Claude
**Supersedes:** 2026-03-25-goldenpipe-design.md (v0.1 skeleton)

## Overview

GoldenPipe v1.0 is a **pluggable pipeline framework** for data quality workflows. Users compose stages from any source — Golden Suite tools ship as default adapters, but third-party and custom stages are first-class citizens. The framework handles discovery, wiring validation, execution, adaptive routing, and multi-interface output.

**Design principles:**
- Pipeline framework, not a wrapper around the Golden Suite
- Works standalone without GoldenCheck/GoldenFlow/GoldenMatch installed
- Zero-config default with rich YAML override
- Comparable caliber to GoldenCheck and GoldenMatch (CLI, TUI, MCP, A2A, REST, 140+ tests)

## Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Stage granularity | Hybrid — coarse or fine | Framework calls `stage.run(ctx)`, doesn't care about internals |
| Stage discovery | Entry points + local `stages/` dir | Entry points for distribution, local dir for quick custom stages |
| Data contract | Typed `PipeContext` with first-class DataFrame + artifacts | DataFrame is the primary payload; artifacts carry stage-specific outputs |
| Routing | Callback decisions from stages | Stages return `Decision` objects; framework applies them |
| YAML config | Layered — minimal list or rich per-stage | Simple `stages: [scan, transform, dedupe]` works; power users add config |
| Golden Suite integration | Built-in adapters, lazy imports, extras | `pip install goldenpipe[golden-suite]` adds tools; no separate package |
| Interfaces | All five in v1.0 | CLI, TUI, MCP, A2A, REST — full suite parity |
| Stage lifecycle | Protocol + `@stage()` shorthand | Full Protocol for complex stages, decorator for simple functions |
| Architecture | Layered engine | Registry → Resolver → Runner → Router → Reporter |

## Core Data Model

### PipeContext

The object flowing through the pipeline. Stages mutate it in place.

```python
@dataclass
class PipeContext:
    df: pl.DataFrame | None = None
    artifacts: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    timing: dict[str, float] = field(default_factory=dict)
    reasoning: dict[str, str] = field(default_factory=dict)
```

- `df` — primary tabular payload, first-class status
- `artifacts` — stage-specific outputs keyed by name (e.g., `"findings"`, `"clusters"`, `"manifest"`)
- `metadata` — pipeline-level info (source path, row count, pipeline name)
- `timing` — stage name to elapsed seconds
- `reasoning` — stage name to human-readable decision explanation

### StageResult

Returned by every stage's `run()` method.

```python
@dataclass
class StageResult:
    status: str                        # "success" | "skipped" | "failed"
    decision: Decision | None = None   # optional routing instruction
    error: str | None = None           # error message if failed
```

### Decision

Routing instruction from a stage to the framework.

```python
@dataclass
class Decision:
    skip: list[str] = field(default_factory=list)    # stage names to skip
    abort: bool = False                               # stop pipeline
    insert: list[str] = field(default_factory=list)  # stage names to add
    reason: str = ""                                  # explanation
```

### StageInfo

Metadata for registry and wiring validation.

```python
@dataclass
class StageInfo:
    name: str
    produces: list[str]                    # artifact keys this stage writes
    consumes: list[str]                    # artifact keys this stage reads
    config_schema: type | None = None      # optional Pydantic model for per-stage config
```

### PipeResult

Final output returned to the caller.

```python
@dataclass
class PipeResult:
    status: str                            # "success" | "partial" | "failed"
    source: str
    input_rows: int
    stages: dict[str, StageResult]         # stage name to result
    artifacts: dict[str, Any]              # all artifacts from context
    skipped: list[str]
    errors: list[str]
    reasoning: dict[str, str]
    timing: dict[str, float]
```

Status logic: all succeeded → `"success"`, some failed → `"partial"`, all failed → `"failed"`.

## Stage System

### Stage Protocol

The full contract for complex stages.

```python
class Stage(Protocol):
    info: StageInfo

    def validate(self, ctx: PipeContext) -> None:
        """Pre-run check. Raise if preconditions not met."""
        ...

    def run(self, ctx: PipeContext) -> StageResult:
        """Execute the stage. Mutate ctx.df/artifacts in place."""
        ...

    def rollback(self, ctx: PipeContext) -> None:
        """Optional cleanup on failure."""
        ...
```

`validate` and `rollback` are optional — the framework calls them if defined, skips if not.

### @stage Decorator

Shorthand for simple stages. Wraps a function into a Protocol-compliant object.

```python
@stage(name="normalize_phones", produces=["df"], consumes=["df"])
def normalize_phones(ctx: PipeContext) -> StageResult:
    ctx.df = ctx.df.with_columns(pl.col("phone").str.replace_all(r"[^\d]", ""))
    return StageResult(status="success")
```

### Built-in Golden Suite Adapters

In `goldenpipe/adapters/`:

| Adapter | Wraps | Consumes | Produces | Guard |
|---------|-------|----------|----------|-------|
| `check.py` | `goldencheck.scan_file()` | — | `["findings"]` | `HAS_CHECK` |
| `flow.py` | `goldenflow.transform_df()` | `["findings"]` | `["df", "manifest"]` | `HAS_FLOW` |
| `match.py` | `goldenmatch.dedupe_df()` | `["df"]` | `["clusters", "golden"]` | `HAS_MATCH` |

Each adapter's `validate()` raises a clear error if the tool isn't installed.

### Built-in Decision Functions

In `goldenpipe/decisions.py` (evolved from v0.1):

- `severity_gate` — critical findings → `Decision(abort=True)`
- `pii_router` — PII detected → `Decision(skip=["match"], insert=["match_pprl"])`
- `row_count_gate` — <2 rows → `Decision(skip=["match"])`

Wired into adapters by default, overridable in YAML.

## Layered Engine

Five modules, each with one job.

### Registry (`goldenpipe/engine/registry.py`)

Discovers stages from three sources (priority order):
1. **YAML config** — explicit `use: "module:callable"` references
2. **Local `stages/` directory** — Python files with `@stage` or `Stage` classes
3. **Entry points** — `goldenpipe.stages` namespace in installed packages

API: `register(stage)`, `get(name)`, `list_all()`, `discover()`.

Local `stages/` overrides entry points by name — users can replace built-in stages by dropping a same-named file.

### Resolver (`goldenpipe/engine/resolver.py`)

Takes pipeline config + registry, produces an `ExecutionPlan` (ordered list of `PlannedStage`).

- Normalizes bare strings into `StageSpec` objects
- Validates wiring: each stage's `consumes` must be satisfied by a prior stage's `produces`
- Raises `WiringError` at build time for unsatisfied dependencies

### Runner (`goldenpipe/engine/runner.py`)

Executes an `ExecutionPlan` against a `PipeContext`.

For each stage:
1. Call `validate(ctx)` if defined
2. Call `run(ctx)` — record timing and result
3. If `Decision` returned, pass to Router
4. If exception raised, call `rollback(ctx)` if defined, record error, continue

Returns `PipeResult` when all stages complete or pipeline aborts.

### Router (`goldenpipe/engine/router.py`)

Receives `Decision` objects, modifies the remaining execution plan:
- Removes skipped stages
- Inserts new stages (resolved from registry)
- Records routing decisions in `ctx.reasoning`

Pure logic, no I/O.

### Reporter (`goldenpipe/engine/reporter.py`)

Builds `PipeResult` from `PipeContext` after execution. Formats output for CLI (Rich table), API (JSON), Python (dict).

### Execution Flow

```
YAML config
  → Resolver.resolve(config, registry) → ExecutionPlan
  → Runner.run(plan, PipeContext)
       ├── stage.validate(ctx)
       ├── stage.run(ctx) → StageResult
       ├── Router.apply(decision, remaining_plan)
       └── ... next stage
  → Reporter.build(ctx) → PipeResult
```

## Configuration

### YAML Format

Layered — both minimal and rich forms valid in the same file.

```yaml
# Minimal
pipeline: customers-dedupe
stages:
  - goldencheck.scan
  - goldenflow.transform
  - goldenmatch.dedupe

# Rich
pipeline: customers-dedupe
source: customers.csv
output: golden_records.csv

stages:
  - name: validate
    use: goldencheck.scan
    config:
      severity_threshold: warning

  - name: clean
    use: goldenflow.transform
    needs: [validate]
    skip_if: no_findings
    config:
      from_findings: true

  - name: resolve
    use: goldenmatch.dedupe
    needs: [clean]
    config:
      strategy: auto
      threshold: 0.85

decisions:
  - severity_gate
  - pii_router
```

### Config Model (Pydantic)

```python
class StageSpec(BaseModel):
    name: str | None = None
    use: str
    needs: list[str] = []
    skip_if: str | None = None
    config: dict[str, Any] = {}

class PipelineConfig(BaseModel):
    pipeline: str
    source: str | None = None
    output: str | None = None
    stages: list[StageSpec | str]
    decisions: list[str] = []
```

### Zero-Config Mode

```python
import goldenpipe as gp
result = gp.run("customers.csv")  # auto-discovers installed tools, runs all
```

Builds a default `PipelineConfig` from whatever Golden Suite tools are installed.

## Stage Discovery

Three sources, checked in priority order:

1. **YAML config references** — `use: "mymodule:my_function"` imports directly; dotted names resolve from registry
2. **Local `stages/` directory** — Python files imported at pipeline load time; stages self-register via `@stage` or Protocol class
3. **Entry points** — `goldenpipe.stages` namespace in `pyproject.toml`

Resolution order: YAML explicit > local `stages/` > entry points. Users override built-in stages by dropping a same-named file in `stages/`.

## Interfaces

### CLI (`goldenpipe/cli/main.py`)

Typer + Rich. Commands:
- `goldenpipe run <source>` — run pipeline (zero-config or `--config`)
- `goldenpipe validate --config pipeline.yml` — dry-run wiring validation
- `goldenpipe stages` — list discovered stages with produces/consumes
- `goldenpipe init` — generate starter YAML from installed tools
- `goldenpipe serve` — start REST API
- `goldenpipe mcp-serve` — start MCP server
- `goldenpipe agent-serve --port 8250` — start A2A server
- `goldenpipe interactive` — launch TUI

### Python API (`goldenpipe/_api.py`)

```python
import goldenpipe as gp

result = gp.run("customers.csv")                      # zero-config
result = gp.run("customers.csv", config="pipe.yml")   # from YAML
result = gp.run_df(df)                                 # DataFrame input
result = gp.run_stages([my_stage, other_stage], df)    # programmatic
```

### REST API (`goldenpipe/api/server.py`)

FastAPI:
- `POST /run` — execute pipeline
- `GET /stages` — list registered stages
- `POST /validate` — dry-run wiring check
- `GET /health` — health check

### MCP (`goldenpipe/mcp/server.py`)

Tools: `run_pipeline`, `list_stages`, `validate_pipeline`, `explain_pipeline`.

### A2A (`goldenpipe/a2a/server.py`)

aiohttp on port 8250. Agent card at `/.well-known/agent.json`. Skills: `run-pipeline`, `validate-pipeline`, `list-stages`, `explain-pipeline`. SSE streaming for stage-by-stage progress.

### TUI (`goldenpipe/tui/app.py`)

Textual, 4 tabs: Pipeline (stage list + status), Config (YAML editor), Results (artifacts browser), Log (reasoning + timing). Stages update live during execution.

## Project Structure

```
goldenpipe/
├── __init__.py
├── _api.py
├── pipeline.py
├── engine/
│   ├── __init__.py
│   ├── registry.py
│   ├── resolver.py
│   ├── runner.py
│   ├── router.py
│   └── reporter.py
├── models/
│   ├── __init__.py
│   ├── context.py
│   ├── config.py
│   └── stage.py
├── adapters/
│   ├── __init__.py
│   ├── check.py
│   ├── flow.py
│   └── match.py
├── decisions.py
├── config/
│   └── loader.py
├── cli/
│   └── main.py
├── tui/
│   └── app.py
├── api/
│   └── server.py
├── mcp/
│   └── server.py
└── a2a/
    └── server.py
```

## Testing

**Target:** 140+ tests, >80% coverage on engine/models/decisions.

| Category | Scope | Count |
|----------|-------|-------|
| Unit (engine, models, decisions) | No external deps | ~80 |
| Adapter | Mock Golden Suite tools | ~20 |
| Interface (CLI, REST, TUI, MCP, A2A) | Test clients/runners | ~30 |
| Integration | Real Golden Suite tools (skip if absent) | ~10 |

Test files: `test_models.py`, `test_stage.py`, `test_registry.py`, `test_resolver.py`, `test_runner.py`, `test_router.py`, `test_reporter.py`, `test_decisions.py`, `test_config.py`, `test_adapters.py`, `test_pipeline.py`, `test_api.py`, `test_cli.py`, `test_tui.py`, `test_rest.py`, `test_mcp.py`, `test_a2a.py`, `conftest.py`.

## Dependencies

```toml
[project]
dependencies = ["polars>=1.0", "typer>=0.12", "rich>=13.0", "pydantic>=2.0", "pyyaml>=6.0"]

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
```

## Migration from v0.1

**Kept:** Decision logic (becomes built-in decision functions), PipeResult shape and status logic, CLI Rich output pattern, test fixtures, graceful degradation pattern.

**Rewritten:** Pipeline class (230 lines → ~30 line wrapper), stage execution (hardcoded methods → generic Runner loop), tool integration (direct imports → adapter classes).

**New:** Engine layer (registry, resolver, runner, router, reporter), models (context, stage protocol, decorator), config system, stage discovery, TUI, REST API, MCP, A2A, ~120 new tests.

**Scale:** v0.1 is 601 SLOC. v1.0 estimated at ~3,500-4,000 SLOC production + ~2,500 test code. Comparable to GoldenFlow.

## Future (v2.0)

The layered architecture supports agent-native extensions without rewriting:
- Agents call Resolver to inspect pipeline wiring
- Agents inject Decisions through the Router to steer execution
- Agents swap the Runner for parallel/distributed execution
- Event-driven observability added as a Reporter plugin
