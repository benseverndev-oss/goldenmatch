# CLI Reference

## Commands

GoldenCheck exposes a single top-level command with three subcommands. Passing a file directly (without a subcommand) is equivalent to `scan`.

### Default shorthand

```bash
goldencheck data.csv [flags]
```

Equivalent to `goldencheck scan data.csv [flags]`. Useful for the interactive day-to-day workflow.

---

### `scan`

Profile a data file and report findings. Does not require an existing `goldencheck.yml`.

```bash
goldencheck scan <file> [flags]
```

**Examples:**

```bash
# Launch interactive TUI
goldencheck scan data.csv

# Print to console, no TUI
goldencheck scan data.csv --no-tui

# JSON output (suitable for piping)
goldencheck scan data.csv --no-tui --json

# With LLM enhancement
goldencheck scan data.csv --llm-boost --no-tui

# Choose OpenAI instead of the default Anthropic provider
goldencheck scan data.csv --llm-boost --llm-provider openai --no-tui
```

---

### `validate`

Validate a data file against pinned rules in `goldencheck.yml`. Exits with code 1 if violations are found at or above the configured `fail_on` severity.

```bash
goldencheck validate <file> [flags]
```

Requires an existing `goldencheck.yml` (or a path passed via `--config`). If no config is found, the command exits with an error.

**Examples:**

```bash
# Validate against goldencheck.yml in the current directory
goldencheck validate data.csv

# Use a specific config file
goldencheck validate data.csv --config configs/production.yml

# Validate with no TUI (CI use case)
goldencheck validate data.csv --no-tui

# JSON output for downstream processing
goldencheck validate data.csv --no-tui --json
```

---

### `review`

Profile the file AND validate against existing rules, then launch the TUI for interactive review. This is the recommended command when iterating on a dataset that already has a config.

```bash
goldencheck review <file> [flags]
```

If no `goldencheck.yml` exists, `review` behaves like `scan`. When a config is found, validation findings take precedence over scan findings for the same column+check pair.

**Examples:**

```bash
# Standard review workflow
goldencheck review data.csv

# Review with LLM boost
goldencheck review data.csv --llm-boost

# Review with custom config path
goldencheck review data.csv --config configs/staging.yml
```

---

## Flags

All flags are available on `scan` and `review`. `validate` supports `--no-tui`, `--json`, and `--config`.

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--no-tui` | bool | false | Disable the interactive TUI and print Rich console output instead |
| `--json` | bool | false | Output results as JSON to stdout. Implies `--no-tui` |
| `--fail-on <level>` | string | `error` | Exit code 1 when findings at or above this severity exist. Values: `error`, `warning` |
| `--llm-boost` | bool | false | Run an LLM enhancement pass after profiling |
| `--llm-provider <name>` | string | `anthropic` | LLM provider to use. Values: `anthropic`, `openai` |
| `--config <path>` | path | `goldencheck.yml` | Path to config file (validate and review only) |
| `--verbose` | bool | false | Show INFO-level log messages |
| `--debug` | bool | false | Show DEBUG-level log messages |
| `--version`, `-V` | bool | — | Print version and exit |

---

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success — no violations at or above `fail_on` severity |
| 1 | Violations found at or above `fail_on` severity |
| 2 | Usage error (unknown flag, missing argument) |

The exit code makes GoldenCheck usable in CI pipelines:

```bash
goldencheck validate data.csv --no-tui
echo "Exit code: $?"
```

---

## Environment Variables

| Variable | Purpose | Example |
|----------|---------|---------|
| `ANTHROPIC_API_KEY` | Anthropic API key for LLM boost | `sk-ant-...` |
| `OPENAI_API_KEY` | OpenAI API key for LLM boost | `sk-...` |
| `GOLDENCHECK_LLM_BUDGET` | Maximum USD spend per scan. Scan aborts LLM call if estimated cost exceeds this | `0.50` |
| `GOLDENCHECK_LLM_MODEL` | Override the default model for the selected provider | `claude-sonnet-4-20250514` |

### Default models

| Provider | Default model |
|----------|--------------|
| anthropic | `claude-haiku-4-5-20251001` |
| openai | `gpt-4o-mini` |

---

## File Format Support

GoldenCheck reads the format based on the file extension:

| Extension | Format |
|-----------|--------|
| `.csv` | CSV |
| `.tsv` | Tab-separated values |
| `.parquet` | Apache Parquet |
| `.xlsx`, `.xls` | Excel (requires openpyxl) |

---

## CI / Pipeline Integration

### GitHub Actions example

```yaml
- name: Validate data quality
  run: |
    pip install goldencheck
    goldencheck validate data/output.csv --no-tui --fail-on error
```

### Pre-commit hook example

```yaml
- repo: local
  hooks:
    - id: goldencheck
      name: GoldenCheck data validation
      entry: goldencheck validate
      args: [data/output.csv, --no-tui]
      language: system
      pass_filenames: false
```
