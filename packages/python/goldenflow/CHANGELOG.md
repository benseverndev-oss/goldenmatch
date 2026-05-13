# Changelog

## 1.1.6 (2026-05-13)

Bug-fix release. Resolves a panic on large datasets surfaced by the
scale audit work.

### Fixed

- `category_auto_correct` panic at 1M+ rows under memory pressure (PR
  #174 / #175). The old path called `series.to_list()` + Python
  `Counter`, materialising one `PyString` per input row. At ~2 GB
  measured peak RSS following a `goldenmatch.auto_configure` sample
  run, the PyString allocations inside Polars' `to_list` path returned
  NULL and pyo3 0.28.2 mapped that to `PanicException("PyObject pointer
  is null")` rather than `MemoryError`. The function only ever needed
  `n_unique` distinct values, so the rewrite uses
  `series.value_counts()` instead -- stays in Rust, no per-row Python
  allocation, fixes the crash on the goldenmatch scale-audit Round 2
  1M synthetic fixture.

## 1.1.5 (2026-05-11)

Maintenance release. No transform / API behaviour changes.

### Fixed

- `goldenflow/__init__.py` `__version__` was `1.1.1`, lagging behind the
  PyPI `1.1.2` release. Both now report `1.1.5`.

### Infrastructure

- New `.github/workflows/publish-goldenflow.yml` mirrors the per-package
  PyPI publish workflows used by goldenmatch / goldencheck / goldenpipe /
  infermap. Fires on `release: published` for `goldenflow-v*` tags;
  `workflow_dispatch` with `ref` input for retro-publish. Brings goldenflow
  into the same release pipeline the rest of the suite uses.
- Companion MCP Registry sync (`.github/workflows/publish-mcp.yml`,
  added in monorepo PR #165) flips
  `registry.modelcontextprotocol.io/v0/servers?search=io.github.benzsevern/goldenflow`
  from 1.1.4 → 1.1.5 automatically after this release lands on PyPI.

## 1.1.0 (2026-04-03)

### New Transforms (33 new, 43 → 76 total)

- **Email** (new module): `email_lowercase`, `email_normalize`, `email_extract_domain`, `email_validate`
- **Identifiers** (new module): `ssn_format`, `ssn_mask`, `ein_format`
- **URL** (new module): `url_normalize`, `url_extract_domain`
- **Text**: `remove_html_tags`, `remove_urls`, `remove_digits`, `remove_emojis`, `fix_mojibake`, `normalize_line_endings`, `extract_numbers`, `pad_left`, `pad_right`
- **Phone**: `phone_country_code`
- **Name**: `nickname_standardize`, `merge_name`
- **Address**: `country_standardize`, `unit_normalize`
- **Date**: `datetime_iso8601`, `extract_year`, `extract_month`, `extract_day`, `extract_quarter`, `extract_day_of_week`, `date_shift`, `date_validate`
- **Numeric**: `to_integer`, `abs_value`, `fill_zero`, `comma_decimal`, `scientific_to_decimal`

### Fixed GoldenCheck Integration

- `FINDING_TRANSFORM_MAP` now uses real GoldenCheck check names (was using invented labels that matched nothing)
- Profiler bridge uses GoldenCheck's semantic type system (`person_name` → `name`, `currency` → `numeric`, etc.)
- Safer LLM fallback with logged warnings instead of bare `except Exception`

### Bug Fixes

- `comma_decimal` no longer corrupts US-format decimals
- `email_normalize` preserves invalid emails instead of silently dropping to None
- `merge_name` handles missing `last_name` column gracefully
- Fixed `NameError` on `os` when `use_llm=True` in profiler bridge

### Stats

- 234 tests passing (up from 158)
- 11 transform categories (up from 8)

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
