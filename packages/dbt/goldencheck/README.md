# dbt-goldencheck

dbt package for the [Golden Suite](https://github.com/benseverndev-oss/goldenmatch). Ships:

- **`goldencheck_not_empty` test** — a SQL-native sanity check usable from `schema.yml`.
- **`scripts/run_goldencheck.py`** — the canonical full GoldenCheck scan against a dbt model's output. Run it alongside `dbt test` in CI / Airflow.

> **GoldenCheck's data-quality scanning is fundamentally Python-side** — encoding detection, anomaly detection, format-ID validation, etc. require the GoldenCheck library at runtime. dbt tests are SQL-native, so this package's macro is intentionally narrow; the Python runner is where real coverage lives.

## Install

```bash
pip install goldencheck>=0.5.0
```

Add to your `packages.yml`:

```yaml
packages:
  - git: "https://github.com/benseverndev-oss/goldenmatch.git"
    subdirectory: "packages/dbt/goldencheck"
    revision: main
```

Then `dbt deps`. (`subdirectory:` requires dbt 1.6+; this package's `require-dbt-version` is `>=1.7`.)

## Usage

### As a dbt test (SQL-native sanity check)

```yaml
# models/schema.yml
models:
  - name: orders
    tests:
      - goldencheck_not_empty
```

That's it. The test fails if the model has zero rows. It's deliberately narrow — anything more sophisticated requires the Python runner below.

### Full data-quality scan (canonical)

```bash
python scripts/run_goldencheck.py orders --fail-on error
python scripts/run_goldencheck.py patients --fail-on warning --domain healthcare
```

Wire that into CI right after `dbt run` / `dbt test`. The script:

1. Connects to the warehouse via dbt's `profiles.yml`.
2. Queries the model output (`dbt show --select <model> --limit <N>`).
3. Writes the sample to a temp CSV.
4. Runs `goldencheck scan` with the full profiler pipeline.
5. Exits non-zero if findings at or above `--fail-on` severity.

For an Airflow-shaped wiring of this pattern, see [`examples/airflow/golden_suite_quality_gate.py`](https://github.com/benseverndev-oss/goldenmatch/blob/main/examples/airflow/golden_suite_quality_gate.py) in the monorepo — same idea (GoldenCheck as gatekeeper, threshold-based) but operator-driven.

## Migrating from 0.1.x

The old `goldencheck` test was misleading: it claimed to run the full GoldenCheck scan but only checked for emptiness. **0.2.0 renames it to `goldencheck_not_empty`**.

```diff
 models:
   - name: orders
     tests:
-      - goldencheck
-      - goldencheck:
-          fail_on: warning
-          sample_size: 50000
+      - goldencheck_not_empty
```

The `fail_on` and `sample_size` arguments are gone — they were captured by the Python runner all along. Pass them on the CLI to `scripts/run_goldencheck.py` instead.

## Requirements

- dbt-core >= 1.7
- goldencheck >= 0.5.0
- Python >= 3.11

## Domain packs

Pass `--domain` to the Python runner to use a domain-specific GoldenCheck rulebook:

```bash
python scripts/run_goldencheck.py patients --domain healthcare
python scripts/run_goldencheck.py transactions --domain finance
python scripts/run_goldencheck.py products --domain ecommerce
```

Built-in domains: `healthcare`, `finance`, `ecommerce`, `real_estate`, `people_hr`. See the GoldenCheck README for the full list.

## Part of the Golden Suite

This package is part of the Golden Suite monorepo at [`benseverndev-oss/goldenmatch`](https://github.com/benseverndev-oss/goldenmatch).

| Surface | Use it for |
|---|---|
| **dbt-goldencheck** (this package) | Data-quality test inside dbt projects |
| [`golden_suite_quality_gate.py`](https://github.com/benseverndev-oss/goldenmatch/blob/main/examples/airflow/golden_suite_quality_gate.py) | Airflow-shaped gatekeeper that fails downstream pipelines on regressions |
| [`golden_suite_daily_dedupe.py`](https://github.com/benseverndev-oss/goldenmatch/blob/main/examples/airflow/golden_suite_daily_dedupe.py) | Daily Check → Flow → Match → load pipeline |
| [`ghcr.io/benseverndev-oss/goldencheck-mcp`](https://github.com/benseverndev-oss/goldenmatch/pkgs/container/goldencheck-mcp) | GoldenCheck as a stand-alone MCP server (Claude Desktop / agents) |
| [`ghcr.io/benseverndev-oss/goldensuite-mcp`](https://github.com/benseverndev-oss/goldenmatch/pkgs/container/goldensuite-mcp) | All Suite tools (incl. GoldenCheck) under one MCP endpoint |

## License

MIT
