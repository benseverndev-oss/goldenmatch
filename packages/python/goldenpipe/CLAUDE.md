# GoldenPipe

Golden Suite orchestrator -- chains GoldenCheck, GoldenFlow, GoldenMatch.

## Related Projects
Sibling packages live in this monorepo at `packages/python/{goldencheck,goldenflow,goldenmatch,infermap}/`. Pre-fold standalone repos lived at `D:\show_case\<name>`; their history is in `_archive/goldenmatch-pre-fold/`.
- **GitHub:** `benseverndev-oss/goldenmatch` (single monorepo since 2026-05-02)

## Architecture
- `goldenpipe/pipeline.py` -- Pipeline class, run() function. ONLY file that imports from tools.
- `goldenpipe/decisions.py` -- Adaptive logic (decide_flow, decide_match). NO tool imports. Testable independently.
- `goldenpipe/cli/main.py` -- Typer CLI
- `goldenpipe/tui/app.py` -- Textual TUI (`[tui]` extra). **Wave 2.2 (2026-06-05): wired from stub to real.** 4 tabs (Pipeline/Config/Results/Log) populate from a `PipeResult`: `r` runs `goldenpipe.run(source)` in a worker thread, then `_render_result` fills the Pipeline tab (stage status + per-stage timing), Config (realized stage chain), Results (artifacts browser via `_summarize`), and Log (reasoning + errors + total time). `goldenpipe interactive [SOURCE] [-c CONFIG]` now takes an optional data file (was no-arg). Test seam: call `_render_result(result)` directly with a real `PipeResult`; for the full path `app.action_run()` then `await app.workers.wait_for_complete()`.
- Tools imported with try/except ImportError guards (HAS_CHECK, HAS_FLOW, HAS_MATCH)
- Data flows as Polars DataFrames in memory between stages

### Relocatable-stage seam (contract Phase A)
Design: `docs/design/2026-07-06-goldenpipe-relocatable-stage-contract.md` (motivated
by the Stage 0 finding: the single-process handoff is 0.2% of the wall, so the
streaming executor is premature — but the *contract* that makes future
out-of-core / cross-process / cross-language expansion a build-forward is worth
laying now). **Phase A is inert groundwork — no behavior/perf change:**
- `goldenpipe/models/frame.py` — `Frame` protocol + `LocalFrame`. **Arrow-capable,
  not Arrow-mandatory:** `LocalFrame.polars()` returns the backing DataFrame BY
  REFERENCE (zero copy); `arrow_batches()`/`from_arrow()` materialize Arrow ONLY
  for a boundary-crossing (remote) stage. Streaming/remote frames (Phases B/C)
  plug in behind this contract without touching the in-process path.
- `PipeContext.frame` — a derived property over `df` (the canonical store stays
  `df`, so existing `ctx.df` stages + `PipeContext(df=...)` are untouched). The
  pipeline path never calls it today; it's the accessor a remote adapter will use.
- `StageInfo.location` (default `"local"`) + a Runner guard. In Phase A this raised
  `NotImplementedError` for ANY non-local stage; **Phase C (below) made `location="remote"`
  run in-engine**, so the guard now raises only for a plain remote stage *without* a
  `RemoteStage` marker (the "declared-but-not-implemented placement" case). The
  `ExecutionPlan`/planner is unchanged — placement is orthogonal to ordering.
- Guardrails: no forced Arrow round-trip in-process; Stage 0 numbers verified
  unchanged (`benchmarks/stage0_handoff_profile.py`). Tests: `tests/test_relocatable_stage.py`.

#### Phase C — in-engine (remote) stages
Baseline (`docs/design/2026-07-06-goldenpipe-phasec-baseline-findings.md`): the
DuckDB↔Python crossing was **~89% of the pull path** at 5M rows, so keeping a stage
in-engine pays (unlike Stage 0 / Phase B). Phase C v1 turns `location="remote"` from
"raises" into "runs in the engine":
- `models/frame.py` — **`DuckDBFrame`**: a `Frame` backed by a **lazy** DuckDB
  relation. `.polars()` = materialize (the egress crossing, paid ONCE); `.project()`
  = in-engine SQL transform → a new lazy `DuckDBFrame`; `.arrow_batches()` streams.
- `PipeContext.frame` now holds an **engine-resident** frame in `_frame` **without
  materializing** (the setter keeps a `DuckDBFrame` as-is; a `LocalFrame`/None still
  goes to `df`). So a chain of remote stages stays in the engine.
- Runner: routes `remote_capable` stages (a real `RemoteStage`) instead of raising;
  a plain `location="remote"` stage without the marker still raises (Phase A guard).
  On the **remote→local transition**, the Runner materializes `_frame` → `df` once
  (the boundary crossing, exactly when a local stage needs the data).
- `adapters/engine.py` — **`RemoteStage`** marker + **`EngineNormalizeStage`** (a
  `lower(trim(col))` transform run in DuckDB), byte-identical to the local Polars
  path, reusing one engine connection via `ctx.metadata["duckdb_con"]`. Tests:
  `tests/test_engine_stage.py`.
Phase C **v2** closes the two documented gaps (`tests/test_engine_stage_v2.py`):
- `adapters/engine.py` — **`EngineFlowTransformStage`**: runs a **real shipped**
  `goldenflow_*` DuckDB UDF (from `goldenmatch_duckdb` — the same kernel on the
  DuckDB/Postgres/dbt surfaces) as an in-engine projection, byte-identical to the
  goldenflow Python transform. `transform` is a friendly alias (`email`/`strip`/…);
  the UDFs register ONCE per engine con (`_goldenflow_udfs_registered` guard).
  **Honest caveat:** the DuckDB `goldenflow_*` UDFs are per-value **Python**
  callbacks (in-process polars), NOT the compiled zero-Python `goldenflow-duckdb`
  cdylib — so v2 removes the DataFrame **materialization** boundary *between* stages
  (the 89% pull), not Python from the per-value path. `validate()` raises if
  `goldenmatch-duckdb`/`goldenflow` are absent.
- `pipeline.py` — **DuckDB-table source**: `Pipeline.run(duckdb_con=, duckdb_table=)`
  seeds `ctx.frame` as an engine-resident `DuckDBFrame` (table name regex-validated;
  row count via a scalar `COUNT`, not a full pull). A remote stage right after pays
  **no** ingress crossing (proven by a `.polars()` spy staying at 0). Invariant: **a
  `DuckDBFrame` on `ctx` is always paired with its con in `ctx.metadata["duckdb_con"]`**
  (both the source and the engine stages maintain this — the in-engine `.project`
  runs on the relation's own con, which must be where the UDFs were registered).
  Caveat: with the default auto-config (local `load` first), the source materializes
  at `load` — the WIN needs the first stage to be remote.
- The dominant `goldenmatch.dedupe` scoring stage has no in-engine surface, so a full
  ER pipeline crosses for it — Phase C wins every *other* stage + keeps data in-engine
  between them. **Building an in-engine dedupe was scoped + measured — verdict: DON'T**
  (`docs/design/2026-07-06-goldenpipe-in-engine-dedupe-scope.md`, probe
  `benchmarks/stage0_inengine_dedupe_probe.py`): a warehouse-resident dedupe's crossing
  is **0.4–0.6% of wall and shrinking** with rows (rapidfuzz scoring is superlinear and
  dominates; the crossing is linear). And the in-engine kernels would be the SAME
  `score-core` / `graph-core` the host path already calls, so there's **no compute win
  on top** — only the sub-1% crossing to save. So the caveat is correct by construction,
  not a gap: "smart pipe, dumb kernels" — dedupe stays a host stage calling native
  kernels. (Surface map: Postgres is ~80% native-direct already; DuckDB has no compiled
  goldenmatch cdylib at all — a disproportionate lift for <1%.)

**Contract status (2026-07-06): COMPLETE through Phase C.** Four measure-first "don'ts"
(Stage 0 handoff 0.2%, Phase B frame 250–310× smaller than peak RSS, in-engine dedupe
crossing 0.4%) vs Phase C's one "do" — the discipline earning its keep. **Not built, by
decision (each with a baseline that said don't):** the streaming executor (Stage 0),
the out-of-core `Frame` (Phase B), the in-engine dedupe (above). **Stretch ambition,
deliberately deferred:** a true **cross-process / cross-language** `RemoteStage` over a
transport. Phase C placed stages *in-engine* (DuckDB, same-process); the relocatable
contract (`Frame` / `StageInfo.location` / Runner routing) was laid precisely so a
transport-backed placement is a **build-forward, not a rewrite**. Gate it on a measured
distributed/polyglot workload before building — none exists today.

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
