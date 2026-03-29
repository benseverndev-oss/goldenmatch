# Changelog

## 1.0.0 (2026-03-29)

First stable release. GoldenFlow is production-ready with 170 tests passing and DQBench Transform Score of 100/100.

### Features
- **43+ built-in transforms** across 8 categories: text, phone, name, address, date, categorical, numeric, auto-correct
- **5 domain packs**: people/HR, healthcare, finance, e-commerce, real estate
- **Zero-config mode**: auto-detects and fixes common data issues
- **Config-driven mode**: YAML config with `goldenflow learn` to generate from data
- **Schema mapping**: auto-map columns between source and target files
- **Streaming/batch processing**: handle large files without memory issues
- **Cloud connectors**: S3 and GCS read/write
- **MCP server**: 10 tools for AI agent integration (stdio + HTTP)
- **REST API**: FastAPI-based `goldenflow serve`
- **TUI**: Textual-based interactive mode
- **Watch mode**: auto-transform on file changes
- **Scheduling**: cron-like repeating transforms
- **Audit trail**: JSON manifest for every transformation run
- **History tracking**: with actual duration measurement
- **GoldenCheck integration**: map findings to recommended transforms via `select_from_findings`

### Golden Suite Pipeline
```
GoldenCheck (scan) -> GoldenFlow (transform) -> GoldenMatch (dedupe)
```

## 0.1.0 (2026-03-15)

Initial release.
