# GoldenPipe

Golden Suite orchestrator -- chains GoldenCheck, GoldenFlow, GoldenMatch.

## Related Projects
Sibling packages live in this monorepo at `packages/python/{goldencheck,goldenflow,goldenmatch,infermap}/`. Pre-fold standalone repos lived at `D:\show_case\<name>`; their history is in `_archive/goldenmatch-pre-fold/`.
- **GitHub:** `benseverndev-oss/goldenmatch` (single monorepo since 2026-05-02)

## Architecture
- `goldenpipe/pipeline.py` -- Pipeline class, run() function. ONLY file that imports from tools.
- `goldenpipe/decisions.py` -- Adaptive logic (decide_flow, decide_match). NO tool imports. Testable independently.
- `goldenpipe/cli/main.py` -- Typer CLI
- Tools imported with try/except ImportError guards (HAS_CHECK, HAS_FLOW, HAS_MATCH)
- Data flows as Polars DataFrames in memory between stages

## Pipeline Flow
```
load_file -> GoldenCheck.scan_file(path) -> decide_flow(findings)
  -> if fixable: GoldenFlow.transform_df(df) -> updated df
  -> decide_match(findings, row_count, strategy_override)
  -> GoldenMatch.dedupe_df(df) or AgentSession.deduplicate(path)
  -> PipeResult
```

## Testing
- `pytest --tb=short` from project root
- test_decisions.py: no tool deps, tests pure decision logic
- test_pipeline.py: requires goldencheck, goldenflow, goldenmatch installed

## A2A Port Convention
- GoldenCheck: 8100, GoldenFlow: 8150, GoldenMatch: 8200, GoldenPipe: 8250

## Remote MCP Server

Hosted on Railway, registered on Smithery:
- **Endpoint:** `https://goldenpipe-mcp-production.up.railway.app/mcp/`
- **Smithery:** `https://smithery.ai/servers/benzsevern/goldenpipe`
- **Server card:** `https://goldenpipe-mcp-production.up.railway.app/.well-known/mcp/server-card.json`
- **Transport:** Streamable HTTP (via `StreamableHTTPSessionManager`)
- **Dockerfile:** `Dockerfile.mcp` (Python 3.12-slim, installs `.[mcp]`)
- **Railway project:** `golden-suite-mcp` (service: `goldenpipe-mcp`, port 8250)
- **Local HTTP:** `goldenpipe mcp-serve --transport http --port 8250`

## API Gotchas

### Pipeline.run() does NOT return the DataFrame
`Pipeline.run()` returns `PipeResult` which has `status`, `stages`, `artifacts`, `errors`, `reasoning`, `timing` — but NOT the output DataFrame. The `PipeContext.df` holds the final data but is not exposed in the result.

**Workaround for getting the output DataFrame:** Run the stages directly:
```python
import goldenflow, goldenmatch, polars as pl

df = pl.read_csv("data.csv")

# Stage 1: Transform
result = goldenflow.transform_df(df)
cleaned = result.df

# Stage 2: Deduplicate
deduped = goldenmatch.dedupe_df(cleaned, fuzzy={"first_name": 0.8}, exact=["email"])

# Output: unique records + golden records
output = deduped.unique  # or deduped.golden for canonical records
```

### PipeResult fields
```python
result.status        # PipeStatus enum: SUCCESS, PARTIAL, FAILED
result.input_rows    # int
result.stages        # dict[str, StageResult] — NOT result.stage_results
result.artifacts     # dict[str, Any] — e.g. {"manifest": Manifest}
result.errors        # list[str]
result.reasoning     # dict[str, str] — why each stage was run/skipped
result.timing        # dict[str, float]
result.skipped       # list[str]
```

### PipelineConfig for selective stages
```python
from goldenpipe import Pipeline, PipelineConfig, StageSpec

config = PipelineConfig(
    pipeline="check-and-flow-only",
    stages=[
        StageSpec(use="goldencheck.scan"),
        StageSpec(use="goldenflow.transform"),
        # omit goldenmatch.dedupe to skip dedup
    ],
)
pipeline = Pipeline(config=config)
result = pipeline.run(source="data.csv")
```

### Stage names
Available stages (discovered via entry points):
- `goldencheck.scan` — validate data quality
- `goldenflow.transform` — fix issues
- `goldenmatch.dedupe` — deduplicate records

### GoldenFlow config for pipeline use
Zero-config GoldenFlow may not fix all issues. For pipeline benchmarks, configure explicit transforms:
```python
from goldenflow.config.schema import GoldenFlowConfig, TransformSpec

config = GoldenFlowConfig(transforms=[
    TransformSpec(column="first_name", ops=["strip", "title_case"]),
    TransformSpec(column="last_name", ops=["strip", "title_case"]),
    TransformSpec(column="email", ops=["strip", "lowercase"]),
    TransformSpec(column="phone", ops=["strip", "phone_national"]),
    TransformSpec(column="city", ops=["strip", "title_case"]),
])
result = goldenflow.transform_df(df, config=config)
```

### GoldenMatch config for pipeline use
Use the full config for best results — don't rely on auto-configure for synthetic/benchmark data:
```python
from goldenmatch.config.schemas import (
    GoldenMatchConfig, MatchkeyConfig, MatchkeyField,
    BlockingConfig, BlockingKeyConfig, StandardizationConfig,
)

config = GoldenMatchConfig(
    standardization=StandardizationConfig(
        email=["email"], phone=["phone"],
        first_name=["strip", "name_proper"],
        last_name=["strip", "name_proper"],
    ),
    blocking=BlockingConfig(
        strategy="multi_pass",
        keys=[BlockingKeyConfig(fields=["email"], transforms=["lowercase", "strip"])],
        passes=[
            BlockingKeyConfig(fields=["email"], transforms=["lowercase", "strip"]),
            BlockingKeyConfig(fields=["last_name"], transforms=["soundex"]),
            BlockingKeyConfig(fields=["last_name"], transforms=["substring:0:3"]),
        ],
    ),
    matchkeys=[MatchkeyConfig(
        name="identity", type="weighted", threshold=0.75,
        fields=[
            MatchkeyField(field="first_name", scorer="ensemble", weight=1.0, transforms=["lowercase", "strip"]),
            MatchkeyField(field="last_name", scorer="ensemble", weight=1.0, transforms=["lowercase", "strip"]),
            MatchkeyField(field="email", scorer="jaro_winkler", weight=0.8, transforms=["lowercase", "strip"]),
        ],
    )],
)
result = goldenmatch.dedupe_df(df, config=config)
```

## Column Context Pipeline
- `goldenpipe/models/column_context.py` — ColumnType/CardinalityBand enums, context builders
- ScanStage builds ColumnContext from GoldenCheck profile + name heuristics
- TransformStage enriches contexts (date_iso8601 confirms date type)
- DedupeStage builds targeted GoldenMatch config from contexts
- Cardinality IQR bands: mid=identifier, low=attribute, high=unique ID
- All enrichment is best-effort (try/except) — failures never break existing stages
- `golden` records != total output. `unique + golden = total distinct people`

## Gotchas
- Never run Polars tools while parallel subagents/Next.js builds are active — OOM/segfault
- `utf8-lossy` encoding required for all CSV reads (government data has Latin-1 chars)
- Railway custom domains need Cloudflare DNS Only (not proxied) — proxy breaks domain verification
- Cloudflare CNAME record IDs change when toggling proxy — must re-fetch IDs before patching
- `next-mdx-remote` v5/v6 requires React 19 — use `react-markdown` + `remark-gfm` for React 18
- Hardcoded version in `tests/test_pipeline.py::test_public_api` — must update when bumping version
- `server.json` version must match PyPI — update and `mcp-publisher publish` after every release
- `mcp-publisher login github` required before publish (JWT expires)
- Railway Docker caches pip install layer — add a comment change to Dockerfile to bust cache on new releases
- `gp.run(path)` for demos, NOT `gp.run_df(df)` — GoldenCheck needs file extension
- `publish.yml` should have `skip-existing: true` to handle manual+workflow publish conflicts
- Polars schema mismatch: mixed-type columns (birth_year as i64 vs string) crash GoldenMatch — cast to string before dedup

## DQBench Integration

GoldenPipe is benchmarked by DQBench Pipeline category:
- **DQBench Pipeline Score: 88.07** (without LLM)
- Adapter: `dqbench/adapters/goldenpipe_adapter.py`
- Run: `pip install dqbench && dqbench run goldenpipe`
- The adapter runs GoldenFlow + GoldenMatch directly (not Pipeline.run()) because PipeResult doesn't expose the DataFrame

## External Benchmarks (GoldenMatch component)

- **BPID (EMNLP 2024, PII deduplication):** GoldenMatch scores 75.0% F1 on 10K adversarial PII pairs — matches Ditto (75.2%) with zero training data. DOB parsing was the biggest lever (+0.08 F1). LLM boost hurt on adversarial data. See `D:\show_case\bpid_bench\` and blog post at bensevern.dev.

## Examples
- `examples/full_suite_demo.py` — each tool individually + pipeline
- `examples/benchmark_suite.py` — DQBench scores for all 4 tools
- `examples/custom_pipeline.py` — zero-config vs custom PipelineConfig
- `examples/basic_pipeline.py` — simple pipeline on a CSV
