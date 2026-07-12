# Spec: GoldenPipe in-process moves (Move 1 + Move 2)

**Status:** Design. Not yet implemented.
**Branch:** `spec/goldenpipe-inprocess` (off `origin/main` HEAD).
**Author context:** architect sounding-board follow-up to the "arrow-native suite opens pipeline optimization" thread.

## Thesis

The suite's in-process currency is already a `pl.DataFrame` (Arrow-backed), so
frame handoffs between check/flow/match are cheap. The remaining *disk* tax is
in two specific places, and each closes with a small, local edit:

- **Move 1** — GoldenPipe's own check stage re-reads the source file from disk
  (`scan_file(path)`) mid-pipeline instead of scanning the frame it already
  holds (`scan_dataframe(ctx.df)`). This is *also a latent correctness bug*: the
  check stage silently produces no profile whenever the pipeline source is a
  DataFrame or a DuckDB table (not a file path). Fixing it is the prerequisite
  for profile-sharing (Move 3, out of scope here).
- **Move 2** — the `goldensuite-mcp` composite tools chain check→flow→match by
  writing intermediate CSVs to disk and re-reading them through separate MCP
  tool calls. Calling GoldenPipe **in-process** collapses those round-trips to a
  single frame kept in memory, with one CSV write at the end.

Neither move changes any published algorithm; both are wiring + one real bug fix.

---

## Move 1 — close GoldenPipe's check-stage file seam

### Current behavior (the seam + the bug)

`packages/python/goldenpipe/goldenpipe/pipeline.py` loads the source into
`ctx.df` before any stage runs, and records the origin string in
`ctx.metadata["source"]`:

| source kind        | `ctx.df`                              | `ctx.metadata["source"]` |
|--------------------|----------------------------------------|--------------------------|
| file path          | `pl.read_csv(source, …)` (`:95`)       | the path (`:96`)         |
| `run_df(df)`        | `df` (`:90`)                           | `"<DataFrame>"` (`:91`)  |
| DuckDB table       | engine-resident `DuckDBFrame` (`:74`)  | `"duckdb:<table>"` (`:76`) |

`packages/python/goldenpipe/goldenpipe/adapters/check.py::ScanStage.run`
then **ignores `ctx.df`** and re-reads from the source string:

```python
source = ctx.metadata.get("source", "")   # :28
result = _scan(source, **stage_cfg)        # :32  (_scan = goldencheck.scan_file)
# ...
result = _scan(source)                     # :34
```

Two problems:

1. **Redundant disk round-trip (the perf seam).** For a file source, the file
   is parsed twice — once by `pl.read_csv` into `ctx.df`, once again by
   `scan_file`. GoldenCheck's own `scan_dataframe` docstring cites a bench where
   this redundant CSV round-trip cost **121s of `pipeline_prep_quality_scan`
   wall at 10M rows**. It also means the scan sees GoldenCheck's parse of the
   bytes while the dedupe stage sees GoldenPipe's `utf8-lossy, ignore_errors`
   parse — on dirty government CSVs those can diverge.
2. **Silent no-profile bug (the correctness seam).** For the `run_df` and DuckDB
   paths, `source` is `"<DataFrame>"` / `"duckdb:<table>"` — not a readable path.
   `scan_file("<DataFrame>")` raises; the `isinstance(result, tuple)` guard at
   `check.py:37` falls through to `profile = None` (`:41`), `column_contexts`
   becomes `[]` (`:70`), and the downstream dedupe loses the profile-driven
   config path entirely (`adapters/match.py:58` `column_contexts` is empty →
   Priority 3 blind auto-configure). So today the check stage only actually
   works for **file** sources; `goldenpipe.run_df(df)` runs a degraded pipeline.

### Target behavior

Scan the in-memory frame the pipeline already holds:

```python
# adapters/check.py — replace the _scan(source, …) block
from goldencheck import scan_dataframe as _scan_df  # module-top, guarded by HAS_CHECK

source = ctx.metadata.get("source", "")
if ctx.df is not None:
    if stage_cfg:
        result = _scan_df(ctx.df, file_path=source, **stage_cfg)
    else:
        result = _scan_df(ctx.df, file_path=source)
else:
    # Defensive fallback: no in-memory frame (should not happen for a LOCAL
    # stage — the Runner materializes an engine frame to df on the
    # remote→local transition — but keep the old path readable if it does).
    result = _scan(source, **stage_cfg) if stage_cfg else _scan(source)
```

`scan_dataframe` is already exported by GoldenCheck and is a drop-in:

```
scan_dataframe(df, file_path="<dataframe>", sample_size=100_000,
               return_sample=False, domain=None, baseline=None,
               schema=None, deep=False, denial=False)
    -> (findings, profile[, sample])
```

- Accepts a `pl.DataFrame` **or** a `pa.Table` natively (Arrow-native; the
  polars overload converts via `.to_arrow()`). So when GoldenPipe's currency
  becomes Arrow (the W5 flip), this call needs **no change** — pass the Table.
- Same `(findings, profile)` return shape `scan_file` produces, so the entire
  downstream block in `ScanStage.run` (`:37`–`:85`, the tuple unpack, findings
  normalization, `build_contexts_from_check`, `attach_repair_plan`) is untouched.
- `file_path=source` keeps `DatasetProfile.file_path` populated (cosmetic; some
  repair/context code reads it) even when `source` is `"<DataFrame>"`.
- Every `scan_dataframe` kwarg is a superset-match of `scan_file`'s except the
  leading positional, so `**stage_cfg` forwards unchanged (guard: `stage_cfg`
  must not carry a `path`/`df` key — it never does today; add a defensive pop
  or a validate-time check if we want belt-and-suspenders).

### Edge cases

- **`ctx.df is None`.** `ScanStage` is a `location="local"` stage
  (`StageInfo(consumes=["df"])`). The Runner materializes an engine-resident
  `_frame` → `df` on the remote→local transition *before* a local stage runs
  (Phase C contract, `models/context.py:65`–`79` + runner). So `ctx.df` is
  populated when `ScanStage.run` executes. The fallback branch above is
  belt-and-suspenders only.
- **`HAS_CHECK` false.** `validate()` already raises before `run()`; unchanged.
- **Byte-parity on the file path.** `scan_dataframe` is documented as "same
  semantics as `scan_file` but skips the CSV round-trip." Findings on a *clean*
  fixture are equivalent. On a *dirty* fixture, findings may differ slightly
  because the scan now sees GoldenPipe's own `utf8-lossy` frame rather than
  GoldenCheck's independent parse — this is the intended consistency win (the
  scan and the dedupe now agree on the bytes), but it is a behavior change and
  gets its own test.

### Tests (`packages/python/goldenpipe/tests/`)

1. **Regression for the bug:** `goldenpipe.run_df(df)` over a frame with a known
   quality issue now yields a non-empty `result.artifacts["profile"]` and
   non-empty `column_contexts` (today: empty).
2. **File-path equivalence:** on a clean CSV fixture, findings/profile from the
   new path match the pre-change file-scan (or an explicitly-blessed diff).
3. **stage_cfg forwarding:** `deep=True` / `domain=…` in the stage config reach
   `scan_dataframe`.
4. **`ctx.df is None` fallback** doesn't raise.

### Blast radius

One file (`adapters/check.py`), ~8 lines. No public API change. No new
dependency (both `scan_file` and `scan_dataframe` are already exported by the
`goldencheck` the adapter imports).

---

## Move 2 — MCP composites call GoldenPipe in-process

### Current behavior (the disk tax)

`packages/python/goldensuite-mcp/goldensuite_mcp/composites.py` chains suite
tools by writing `.csv` files between every stage
(`_gen_output_path` → `f"{stem}.{suffix}.csv"`) and re-reading them through
separate MCP tool dispatches:

- `orchestrate_clean_and_dedupe`: `upload` → `run_transforms(file → cleaned.csv)`
  → `agent_deduplicate(cleaned.csv → golden.csv)`. **Two** intermediate CSV
  writes + reads (write cleaned, read cleaned, write golden).
- `orchestrate_dedupe_file`: `upload` → `auto_configure(file)` →
  `agent_deduplicate(file → golden.csv)`. `auto_configure` reads the file, then
  `agent_deduplicate` reads the **same file again**.

Each `run_step` routes to a sub-package MCP dispatcher (goldenmatch /
goldenflow), each of which is a `read_csv → work → write_csv` boundary.

### Target behavior

GoldenPipe **is importable** here — `goldensuite-mcp` depends on
`goldenpipe[mcp]`. Its `run()` already does check→flow→dedupe in-process on one
`ctx.df`, and surfaces the golden output as a **full `pl.DataFrame`** on
`PipeResult.artifacts["golden"]` (the `DedupeStage` casts every column to
string, so it's CSV/JSON-safe). Replace the internal CSV chain with a single
in-process pipeline call, and write golden **once**:

```python
# composites.py — new helper, guarded like the adapters
try:
    import goldenpipe as _gp
    HAS_PIPE = True
except ImportError:
    HAS_PIPE = False

def _run_pipeline_inprocess(path, excl, golden_path):
    """check→flow→dedupe in one process; write golden once. Returns the
    (golden_records, total_records, confidence_distribution) the composite
    summary needs, reconstructing the review buckets from scored_pairs."""
    from contextlib import nullcontext
    _excl_ctx = nullcontext()
    if excl:
        from goldenmatch.core.autoconfig import _RUNTIME_EXCLUDE_COLUMNS
        # same mechanism agent_deduplicate uses; threads into GoldenPipe's
        # internal auto-config because DedupeStage calls goldenmatch's controller.
        token = _RUNTIME_EXCLUDE_COLUMNS.set(list(excl))
    try:
        result = _gp.run(path)               # zero-config check→flow→dedupe
    finally:
        if excl:
            _RUNTIME_EXCLUDE_COLUMNS.reset(token)

    golden = result.artifacts.get("golden")
    stats = result.artifacts.get("match_stats") or {}
    scored = result.artifacts.get("scored_pairs")
    if golden is not None:
        golden.write_csv(golden_path)        # the ONE write
    conf = _confidence_from_scored_pairs(scored)   # gate_pairs reconstruction
    return {
        "golden_path": golden_path if golden is not None else None,
        "golden_records": golden.height if golden is not None else None,
        "total_records": stats.get("total_records"),
        "confidence_distribution": conf,
        "status": result.status.value,
        "errors": result.errors,
    }
```

`clean_and_dedupe` then becomes `upload → _run_pipeline_inprocess` (the
GoldenFlow transform is part of GoldenPipe's default chain, so "clean" happens
in-memory — the `cleaned.csv` disappears entirely). `dedupe_file` becomes
`upload → _run_pipeline_inprocess` (the double file-read collapses to one).

### The three compatibility gaps (the real design decisions)

The composites' return contract is what LLM callers depend on, so the gaps
between `agent_deduplicate`'s return and GoldenPipe's artifacts must be closed,
not papered over.

1. **`confidence_distribution` (auto_merged / review / auto_rejected).**
   `agent_deduplicate` returns these from the AgentSession review-gating layer;
   plain `dedupe_df` (what GoldenPipe's `DedupeStage` runs) does **not** gate —
   it surfaces `match_stats` (total_records/clusters/match_rate) but no buckets.
   The composite summary's "*X merged, Y to review*" line loses its source.
   **Resolution:** reconstruct from `artifacts["scored_pairs"]` (GoldenPipe
   already surfaces it, `adapters/match.py:84`) via
   `goldenmatch.core.review_queue.gate_pairs` (the same >0.95 auto-merge /
   0.75–0.95 review / <0.75 reject thresholds the AgentSession uses). This keeps
   the summary contract whole. If `scored_pairs` is absent, degrade the summary
   to golden/total counts rather than fabricating buckets.

2. **`exclude_columns`.** `agent_deduplicate`/`auto_configure` honor it through
   the `goldenmatch.core.autoconfig._RUNTIME_EXCLUDE_COLUMNS` ContextVar
   (`agent_tools.py:618`–`657`). GoldenPipe's `run()` has no such param.
   **Resolution:** set the *same* ContextVar around the `_gp.run(path)` call in a
   `try/finally` (shown above). It threads through because `DedupeStage` calls
   goldenmatch's controller internally — byte-for-byte the mechanism
   `agent_deduplicate` uses. (Rejected alternative: `run_df(df.drop(excl))` —
   works but drops the columns from the golden output too, changing the file
   schema; the ContextVar excludes them from *matching* only, which is the
   agent_deduplicate semantics.)

3. **`config` transparency block (dedupe_file only).** Today sourced from
   `auto_configure`'s display dict; the code already deliberately does **not**
   pass it to the deduper (`composites.py:85`–`90`). GoldenPipe doesn't return
   the goldenmatch config object. **Resolution:** drop the display `config` block
   on the in-process path (it was display-only), or reconstruct a thin summary
   from `match_stats` + `artifacts["matchkey_used"]`. Recommend dropping it and
   noting the removed field in the composite description — it never fed anything.

### Which composites change, and which don't

| composite         | change | why |
|-------------------|--------|-----|
| `clean_and_dedupe` | **convert** | biggest win — kills the `cleaned.csv` write+read *and* the double dedupe read |
| `dedupe_file`      | **convert** | kills the `auto_configure`+`agent_deduplicate` double file-read |
| `match_sources`    | leave as-is | GoldenPipe has no cross-source *match* stage (it's a dedupe orchestrator); not convertible today |
| `assess_file`      | leave as-is | read-only (`analyze` + `scan`), no CSV chaining tax; benefits indirectly from Move 1 only if routed through GoldenPipe, which it isn't |

### Rejected alternative: dispatch to GoldenPipe's `run_pipeline` MCP tool

GoldenPipe already registers a `run_pipeline` MCP tool (surfaced by the
aggregator). Calling it via `run_step(dispatch, "run_pipeline", …)` would keep
everything dict-based, but:

- `run_pipeline` returns golden as a **≤100-row `golden_preview`** over the wire,
  not the full frame — it **cannot** produce the full `golden.csv` file the
  composite's `outputs.golden_path` contract promises.
- It re-serializes to a dict, forcing a re-parse.

So Move 2 uses the **direct in-process import** (`goldenpipe.run`), reading the
full `artifacts["golden"]` frame and writing it once. The dispatch route is the
wrong tool for a file-producing composite.

### Contract stability

The composite return schema (`workflow`, `ok`, `summary`, `steps`, `outputs`,
`config`) is the LLM-facing contract. Keep `outputs.*` and the `summary` string
**stable**; the visible change is the `steps[]` shape (fewer steps — one
`"pipeline"` step replaces `"clean"`+`"deduplicate"`, or `"auto_configure"`+
`"deduplicate"`). Options:

- **(a)** emit a single `"pipeline"` step and update `_summarize` for the new
  name — simplest, one visible break in `steps[]`.
- **(b)** synthesize the old step entries from the single GoldenPipe result so
  `steps[]` looks unchanged — more code, zero visible break.

Recommend **(a)** with `outputs`/`summary` held stable; document the `steps[]`
change in the composite description.

### Operational note — mimalloc guard

`goldenpipe.run` spawns polars/`ThreadPoolExecutor` workers (the goldenmatch
pipeline) — the recurring pyarrow-mimalloc SIGSEGV class. `goldensuite-mcp` on
Railway installs from the workspace (not a fresh PyPI wheel), which historically
does *not* trip it, but this composite is exactly the "install + run the
pipeline" shape the standing rule covers. **Action:** confirm the
`goldensuite-mcp` Railway service / `Dockerfile.mcp` sets
`ARROW_DEFAULT_MEMORY_POOL=system` at the env level; add it if absent. Harmless
where unneeded.

### Tests (`packages/python/goldensuite-mcp/tests/`)

1. `clean_and_dedupe` produces `outputs.golden_path` with the same
   `golden_records` as the old chain on a fixture — **and asserts no
   intermediate `*.cleaned.csv` is written** (the disk-tax removal is the point).
2. `dedupe_file` in-process matches the old golden count on a fixture.
3. `exclude_columns` honored (ContextVar path) — an excluded column does not
   drive matching.
4. `confidence_distribution` reconstructed from `scored_pairs` matches
   `gate_pairs` buckets.
5. `HAS_PIPE` false → graceful fallback to the current dispatch chain (or a clean
   composite failure), never a crash.
6. Return schema: `outputs` keys and `summary` shape unchanged; `steps[]`
   documented change asserted.

### Blast radius

One file (`composites.py`) plus a guarded import and one helper. No change to
any sub-package MCP tool. `match_sources`/`assess_file` untouched.

---

## Sequencing

1. **Move 1 first.** It's a one-file bug fix, independent, and it makes
   GoldenPipe's own check stage correct on an in-memory frame — which is exactly
   what Move 2's in-process runs exercise. Landing it first de-risks Move 2 and
   unblocks Move 3 (profile-sharing).
2. **Move 2 second.** Depends on nothing from Move 1 mechanically, but benefits
   from the check stage being correct.

## Explicitly out of scope

- **Move 3 (profile-sharing):** have GoldenPipe pass the GoldenCheck
  `DatasetProfile` into GoldenMatch's autoconfig so the frame is profiled once,
  not twice. Blocked on GoldenCheck's `DatasetProfile` exposing the semantic
  `col_type` + `avg_len` fields GoldenMatch's `ColumnProfile` needs — a separate
  spec. Move 1 is its prerequisite (the check stage must profile the same frame
  the deduper will consume).
- The Arrow W5 flip (removing autoconfig's arrow→polars bounce). `scan_dataframe`
  already accepts `pa.Table`, so Move 1 is forward-compatible with it.
