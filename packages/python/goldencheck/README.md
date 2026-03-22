# GoldenCheck

Data validation that discovers rules from your data so you don't have to write them.

> Every competitor makes you write rules first. GoldenCheck flips it: validate first, keep the rules you care about.

## Install

```bash
pip install goldencheck
```

## Quick Start

```bash
# Scan a file — discovers issues, launches interactive TUI
goldencheck data.csv

# CLI-only output (no TUI)
goldencheck data.csv --no-tui

# Validate against saved rules (for CI/pipelines)
goldencheck validate data.csv

# JSON output for CI integration
goldencheck data.csv --no-tui --format json
```

## How It Works

1. **Scan** — GoldenCheck profiles your data and discovers what "healthy" looks like
2. **Review** — findings appear in an interactive TUI sorted by severity
3. **Pin** — press Space to promote findings into permanent validation rules
4. **Export** — press F2 to save rules to `goldencheck.yml`
5. **Validate** — run `goldencheck validate` in CI to enforce rules with exit codes

## What It Detects

### Column-Level
- **Type inference** — string columns that are actually numeric
- **Nullability** — required vs. optional columns
- **Uniqueness** — primary key candidates, near-duplicates
- **Format detection** — emails, phones, URLs, dates
- **Range & distribution** — outliers, min/max bounds
- **Cardinality** — low-cardinality columns (enum suggestions)
- **Pattern consistency** — mixed formats within a column

### Cross-Column
- **Temporal ordering** — start_date < end_date
- **Null correlation** — columns that are null together

## Configuration (goldencheck.yml)

```yaml
version: 1

settings:
  sample_size: 100000
  fail_on: error

columns:
  email:
    type: string
    required: true
    format: email
    unique: true

  age:
    type: integer
    range: [0, 120]

  status:
    type: string
    enum: [active, inactive, pending, closed]

ignore:
  - column: notes
    check: nullability
```

## CLI Reference

| Command | Description |
|---------|------------|
| `goldencheck <file>` | Scan and launch TUI |
| `goldencheck scan <file>` | Explicit scan |
| `goldencheck validate <file>` | Validate against goldencheck.yml |
| `goldencheck review <file>` | Scan + validate, launch TUI |

### Flags

| Flag | Description |
|------|------------|
| `--no-tui` | Print results to console |
| `--format json` | JSON output |
| `--fail-on <level>` | Exit 1 on severity: error or warning |
| `--verbose` | Show info-level logs |
| `--debug` | Show debug-level logs |
| `--version` | Show version |

## Comparison

| | GoldenCheck | Great Expectations | Pandera | Pointblank |
|---|---|---|---|---|
| Rules | Discovered from data | Written by hand | Written by hand | Written by hand |
| Config | Zero to start | Heavy setup | Decorators | YAML/Python |
| Interface | CLI + TUI | HTML reports | Exceptions | HTML/notebook |
| Learning curve | One command | Hours/days | Moderate | Moderate |

## License

MIT
