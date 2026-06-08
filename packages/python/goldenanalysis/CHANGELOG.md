# Changelog

All notable changes to GoldenAnalysis are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/); this project uses semantic
versioning.

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
