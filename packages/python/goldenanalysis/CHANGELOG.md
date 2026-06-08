# Changelog

All notable changes to GoldenAnalysis are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/); this project uses semantic
versioning.

## [0.2.0] - unreleased

Phase 2a — suite consumption. Produce an `AnalysisReport` from real suite outputs.

### Added
- `ga.analyze_match(result, *, certificate=None)` — analyze a GoldenMatch
  `DedupeResult` (`match.rates` + `cluster.distribution`).
- `ga.analyze_pipeline(result)` — analyze a GoldenPipe `PipeResult`, fanning out to
  every analyzer whose consumed artifacts are present.
- Analyzers: `match.rates` (pair count, match rate, threshold, recall estimate +
  safe bound from a certificate, mean score, score histogram), `cluster.distribution`
  (count, singleton ratio, size quantiles, reduction ratio, size histogram),
  `quality.rollup` (findings totals + GoldenCheck score + GoldenFlow rows-changed /
  rules-fired, degrading per-producer).
- Adapters: `match` / `flow` / `pipe` (duck-typed, no eager suite imports) and
  `check` (lazy `goldencheck` import behind the `[check]` extra; pure `from_scan`
  seam). They populate a standardized `AnalyzerInput.artifacts` vocabulary.

### Notes
- `match.recall_estimate` flows automatically once `goldenmatch.dedupe_df(...,
  certify=True)` attaches a `RecallEstimate` (goldenmatch PR); `match.recall_safe_bound`
  needs a labelled audit and is supplied via `certificate=`. Both degrade silently
  when absent.
- `frame.summary` does not run under `analyze_pipeline` (a `PipeResult` exposes no
  input frame). Cross-run `ReportHistory` / regression detection / narrative are
  Phase 2b.

## [0.1.0] - 2026-06-08

Phase 1 — Python core. The generic frame path, end to end.

### Added
- `ga.analyze(df, analyzers=[...])` — run analyzers over a polars DataFrame and
  assemble a single `AnalysisReport`. Works with zero other suite packages
  installed.
- Model layer: `Metric`, `AnalysisTable`, `AnalysisReport` (`schema_version=1`
  cross-surface contract anchor), and analyzer I/O types.
- `frame.summary` analyzer — row/column counts, mean null ratio, exact-duplicate
  row ratio, estimated memory, and a `per_column` table.
- Pure-Python/Polars aggregation primitives (`null_ratio_per_column`,
  `duplicate_row_ratio`, `histogram`, `quantile`) — the byte-identical reference
  for the future Rust accelerator.
- Analyzer registry over the `goldenanalysis.analyzers` entry-point group, with an
  editable-install fallback map.
- Exporters: `to_json` / `from_json` (lossless round-trip), `to_markdown`,
  `to_parquet` (long-form metric frame + per-table sidecars).
- `goldenanalysis` CLI: `report` command; `trend` / `regressions` stubbed to
  `0.2.0`.
- Native-loader gate (`GOLDENANALYSIS_NATIVE`) with an empty `_GATED_ON` — the
  Phase 4 seam, under contract test from day one (pure-Python fallback).

### Deferred (later phases)
- Suite adapters + `match.rates` / `cluster.distribution` / `quality.rollup`,
  `ReportHistory` + regression detection + narrative (Phase 2).
- TypeScript parity port (Phase 3).
- Rust `analysis-core` / `analysis-native` accelerator (Phase 4).
- GoldenPipe terminal stage + goldensuite-mcp surfacing, and the
  `publish-goldenanalysis*` workflows (Phase 5 / follow-up).

[0.1.0]: https://github.com/benseverndev-oss/goldenmatch/releases/tag/goldenanalysis-v0.1.0
