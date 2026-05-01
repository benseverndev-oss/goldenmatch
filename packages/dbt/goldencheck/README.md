# dbt-goldencheck

dbt integration for [GoldenCheck](https://github.com/benzsevern/goldencheck) — zero-config data validation as a dbt test.

## Install

```bash
pip install goldencheck
```

Add to your `packages.yml`:

```yaml
packages:
  - git: "https://github.com/benzsevern/dbt-goldencheck.git"
    revision: main
```

Then `dbt deps`.

## Usage

### As a dbt test

```yaml
# models/schema.yml
models:
  - name: orders
    tests:
      - goldencheck
      - goldencheck:
          fail_on: warning
          sample_size: 50000
```

### As a standalone script

```bash
python scripts/run_goldencheck.py orders --fail-on error --domain ecommerce
```

## How It Works

1. The dbt test macro provides a basic structural check
2. The Python helper script (`scripts/run_goldencheck.py`) runs the full GoldenCheck scan:
   - Uses `dbt show` to query the model output
   - Writes to a temp CSV
   - Runs `goldencheck scan` with full profiler pipeline
   - Returns pass/fail based on findings

## Requirements

- dbt-core >= 1.5
- goldencheck >= 0.5.0
- Python >= 3.11

## Domain Packs

Use domain-specific type definitions for better detection:

```bash
python scripts/run_goldencheck.py patients --domain healthcare
```

## License

MIT
