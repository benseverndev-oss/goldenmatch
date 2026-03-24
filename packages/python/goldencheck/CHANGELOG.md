# Changelog

All notable changes to GoldenCheck will be documented in this file.

## [0.3.0] - 2025-03-24

### Added
- **MCP server** — `goldencheck mcp-serve` exposes 6 tools (scan, validate, profile, health_score, get_column_detail, list_checks) for Claude Desktop integration. Install with `pip install goldencheck[mcp]`
- **LLM rule generation** — `goldencheck learn` sends data samples to an LLM and generates domain-specific validation rules (regex, length, value lists, cross-column). Rules saved to `goldencheck_rules.json` and auto-applied on future scans
- **Jupyter / Colab support** — `_repr_html_()` on Finding and DatasetProfile, plus `ScanResult` wrapper in `goldencheck.notebook` for rich HTML display
- **Colab demo notebook** — `scripts/goldencheck_demo.ipynb` with "Open in Colab" badge
- **DevContainer** — `.devcontainer/devcontainer.json` for Codespaces (Python 3.12, ruff, Jupyter)
- **Try-It GitHub Action** — zero-install demo via `workflow_dispatch`, paste a CSV URL and get results
- **Numeric cross-column profiler** — detects value > max constraint violations (e.g., claim_amount > policy_max)
- **Digits-in-name detection** — flags numeric characters in person_name columns as WARNING
- **Mixed coding standard detection** — pattern_consistency now detects structural pattern shifts (letter-first vs digit-first)

### Improved
- **DQBench Score: 72.00 → 87.71** (+15.71 points)
- Temporal order heuristics expanded: admission/discharge, service/submit, and 15+ new pairs
- Drift detection skips high-cardinality string columns (>90% unique) — eliminates false positives on IPs, UUIDs, session IDs
- Drift detection suppressed on datetime columns via semantic types
- Date-pair fallback tightened (6-column guard) — prevents noisy combinatorial pairs
- CI badge added to README

## [0.2.0] - 2025-03-23

### Added
- **Semantic type classification** — auto-detects 11 column types (email, phone, address, free_text, etc.) via name heuristics and value-based inference
- **Suppression engine** — suppresses irrelevant findings based on semantic type (e.g., uniqueness warnings on email columns)
- **Confidence scoring** — every finding gets a 0.0–1.0 confidence score displayed as H/M/L in the TUI
- **Corroboration boost** — multiple profilers flagging the same column increases confidence (+0.1 for 2 checks, +0.2 for 3+)
- **Confidence downgrade** — low-confidence findings demoted to INFO when LLM boost is not active
- **LLM boost** — `--llm-boost` flag sends representative sample blocks to an LLM for enhanced validation
  - Supports Anthropic (Claude) and OpenAI providers
  - Budget tracking with `GOLDENCHECK_LLM_BUDGET` env var
  - Standardized check names for consistent LLM ↔ profiler merging
- **Cross-column profilers** — temporal ordering and null correlation detection
- **Encoding detection profiler** — detects mojibake, mixed encodings, control characters
- **Sequence detection profiler** — identifies broken auto-increment sequences and gaps
- **Drift detection profiler** — finds temporal distribution shifts within a column
- **DQBench Score: 72.00** — beating Great Expectations (21.68), Pandera (32.51), and Soda Core (22.36)

### Improved
- Range profiler now chains with type inference for better numeric detection
- Minority wrong-type detection catches columns that are "mostly numeric with a few strings"
- Temporal ordering heuristics expanded (signup→login, open→close, etc.)
- Profiler-only column recall improved from 87% to 100%

## [0.1.0] - 2025-03-22

### Added
- **Core profiler pipeline** — 7 column profilers: type inference, nullability, uniqueness, format detection, range/distribution, cardinality, pattern consistency
- **Interactive TUI** — 4-tab Textual interface (Overview, Findings, Column Detail, Rules)
- **Rule pinning** — Space to pin findings, F2 to export to `goldencheck.yml`
- **Validation mode** — `goldencheck validate` enforces saved rules with CI-friendly exit codes
- **CLI** — `goldencheck <file>` shorthand, `--no-tui`, `--json`, `--fail-on`, `--verbose`, `--debug`
- **File formats** — CSV, Parquet, Excel (.xlsx/.xls)
- **Polars-native** — all data operations use Polars for speed
- **Deterministic sampling** — seed=42 for reproducible results on large files
- **Rich CLI output** — severity-colored findings with sample values
- **JSON reporter** — machine-readable output for CI pipelines
