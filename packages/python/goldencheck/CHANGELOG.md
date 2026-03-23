# Changelog

All notable changes to GoldenCheck will be documented in this file.

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
