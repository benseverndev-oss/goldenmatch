# GoldenAnalysis Phase 5 — GoldenPipe stage + MCP + publish Plan

> Use superpowers:executing-plans. Phase 5 is three independent subsystems → **three focused PRs** (the repo SOP + the design, which flags the GoldenPipe stage as "a separate follow-up PR"). Each PR is green on its own.

**Goal:** Finish GoldenAnalysis's suite integration + shipping surface: (A) an MCP server + goldensuite-mcp surfacing, (B) the PyPI + npm + MCP-registry publish workflows, (C) the optional GoldenPipe terminal reporting stage.

**Reference:** design `docs/superpowers/specs/2026-06-08-goldenanalysis-cross-cutting-analysis-engine-design.md` §Phase 5 + §"CI + publish wiring". Templates: `goldenpipe/mcp/server.py`, `goldensuite_mcp/server.py`, `publish-goldenpipe.yml` / `publish-goldenpipe-js.yml`, `goldenpipe/stages/` + `goldenpipe/adapters/`.

---

## PR A — MCP server + goldensuite-mcp surfacing  (branch `feat/goldenanalysis-p5` → first PR)

The package already ships `server.json` (registry manifest, `uvx` stdio) but has no MCP server backing it. Mirror goldenpipe's module-level `TOOLS` + `HANDLERS` so the aggregator surfaces it transitively.

### A.0 — `goldenanalysis/mcp/{__init__.py,server.py}`
- [ ] `server.py` mirrors `goldenpipe/mcp/server.py`: `HAS_MCP` guard; tool fns `list_analyzers()`, `analyze_frame(path, analyzers?, output_format?)`, `get_trend(history, metric, dataset, last?)`, `detect_regressions(history, dataset, baseline?, window?, policy?)` — each lazy-imports polars/the package internals so module import stays light; `_build_tools()`; module-level `TOOLS` + `HANDLERS` (name→`lambda args: fn(**args)`); `create_server()`/`run_server()`/`run_server_http(port=8300)` (A2A convention: Check 8100 / Flow 8150 / Match 8200 / Pipe 8250 → Analysis 8300).
- [ ] Test `tests/test_mcp_server.py`: `TOOLS` non-empty + names; `HANDLERS["list_analyzers"]({})` returns the 4 analyzers; `analyze_frame` on a tmp CSV returns a report dict with `frame.row_count`; `get_trend`/`detect_regressions` over a tmp jsonl seeded via `ReportHistory`. (`create_server` only if mcp installed.)
- [ ] Commit `feat(goldenanalysis): MCP server (analyze_frame / get_trend / detect_regressions / list_analyzers)`

### A.1 — CLI `mcp-serve`
- [ ] `cli/main.py`: add `mcp-serve` (`--transport stdio|http`, `--host`, `--port 8300`) calling `run_server()` / `run_server_http()`; lazy-import the server. Test: command registered (introspect the Typer app — do NOT scrape `--help`, per the narrow-terminal-wrap flake).
- [ ] Commit `feat(goldenanalysis): goldenanalysis mcp-serve command`

### A.2 — goldensuite-mcp surfacing
- [ ] `goldensuite_mcp/server.py`: add `_adapt_goldenanalysis()` (HANDLERS dispatch, like `_adapt_goldenpipe`) + append `("goldenanalysis", _adapt_goldenanalysis)` to `_SUITE_ORDER`.
- [ ] `goldensuite-mcp/pyproject.toml`: add `goldenanalysis[mcp]` to `dependencies` + `goldenanalysis = { workspace = true }` to `[tool.uv.sources]`.
- [ ] `tests/test_aggregator_smoke.py`: add `_adapt_goldenanalysis` to the adapter loop; assert a goldenanalysis tool (e.g. `analyze_frame`) surfaces through `_adapt_goldenanalysis`.
- [ ] Commit `feat(goldensuite-mcp): surface goldenanalysis tools`

### A.3 — verify + PR
- [ ] Local (targeted, `POLARS_SKIP_CPU_CHECK=1`): import the mcp server, assert `TOOLS`/`HANDLERS`, run `list_analyzers` (no polars). ruff clean. Heavy tools (analyze_frame/trend) verified in CI.
- [ ] Push (auth dance); PR vs main; babysit (`ci-required` + `CodeQL` + the `python (goldenanalysis)` + `python (goldensuite-mcp)` lanes); merge.

## PR B — publish workflows  (off main after A merges)
- [ ] `.github/workflows/publish-goldenanalysis.yml` — mirror `publish-goldenpipe.yml`: `release: published` on `goldenanalysis-v*` (skip the other tag prefixes), PyPI via `PYPI_TOKEN`, version from the git tag (the #167 duplicate-version race), `skip-existing: true`.
- [ ] `.github/workflows/publish-goldenanalysis-js.yml` — mirror `publish-goldenpipe-js.yml`: npm on `goldenanalysis-js-v*`, `NPM_TOKEN`, `pnpm publish --no-git-checks`.
- [ ] `publish-mcp.yml` — add `goldenanalysis` to the `package` enum + the sync mapping so `server.json` syncs under `io.github.benseverndev-oss/goldenanalysis` (version from the git tag, not PyPI).
- [ ] Verify the three workflow files parse (YAML); confirm tag-prefix guards don't cross-trigger. Push; PR; babysit; merge.

## PR C — GoldenPipe terminal reporting stage  (off main after B)
- [ ] `goldenpipe`: a `goldenanalysis.report` stage (register at `goldenpipe.stages`), `consumes=["df","clusters","identity_summary"]`, `produces=["analysis_report"]`, appends `PipeResult.artifacts["analysis_report"]`, writes nothing back. Optional `[analysis]` extra on goldenpipe → `goldenanalysis`. Degrades when goldenanalysis absent (HAS_ANALYSIS guard, like HAS_CHECK/FLOW/MATCH).
- [ ] Test: the stage runs after a dedupe and attaches an `analysis_report` artifact; a config omitting it still works; goldenpipe without `[analysis]` skips gracefully.
- [ ] Verify; push; PR; babysit; merge.

## Acceptance
- [ ] A: `goldenanalysis.mcp.server.TOOLS`/`HANDLERS` exist; `mcp-serve` runs; goldensuite-mcp surfaces the tools (aggregator smoke green); `python (goldenanalysis)` + `python (goldensuite-mcp)` lanes green.
- [ ] B: three publish workflows parse + tag-guarded; MCP-registry enum includes goldenanalysis.
- [ ] C: GoldenPipe can run Check→Flow→Match→Identity→**Analysis** as one chain; the stage is read-only + optional; absent-dep path degrades.

### Notes
- snake_case wire types stay snake_case across the JSON the MCP tools emit (the `AnalysisReport`/`Metric` exception). MCP tools are file-path based (stdio-friendly).
- Don't scrape Typer/Rich `--help` in tests (CI narrow-terminal wrap flake) — introspect the Typer app's registered commands.
