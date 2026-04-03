# Baseline

GoldenCheck's baseline feature lets you deep-profile a known-good dataset once and then automatically detect drift whenever new data arrives. It answers the question: *"Has my data changed in a meaningful way since the last time I checked?"*

---

## Overview

| Step | Command | What happens |
|------|---------|-------------|
| 1. Profile | `goldencheck baseline data.csv` | Runs 6 analysis techniques, saves `goldencheck_baseline.yaml` |
| 2. Scan for drift | `goldencheck scan data.csv --baseline goldencheck_baseline.yaml` | Runs 13 drift checks against the saved profile |

---

## Installation

The baseline feature requires optional dependencies:

```bash
pip install goldencheck[baseline]
```

For embedding-based semantic typing (optional — falls back to keyword heuristics):

```bash
pip install goldencheck[semantic]
```

---

## Creating a Baseline

### CLI

```bash
goldencheck baseline data.csv
```

By default this saves to `goldencheck_baseline.yaml` in the current directory. Use `--output` to change the path:

```bash
goldencheck baseline data.csv --output baselines/prod_2026-04.yaml
```

Skip specific techniques with `--skip` (repeatable):

```bash
# Skip the slow embedding-based semantic pass
goldencheck baseline data.csv --skip semantic

# Skip both semantic and priors
goldencheck baseline data.csv --skip semantic --skip priors
```

### Python API

```python
from goldencheck import create_baseline

baseline = create_baseline("data.csv")
baseline.save("goldencheck_baseline.yaml")
```

Pass a Polars DataFrame directly:

```python
import polars as pl
from goldencheck import create_baseline

df = pl.read_csv("data.csv")
baseline = create_baseline(df, source="prod_snapshot_2026-04")
baseline.save("baselines/prod.yaml")
```

Skip techniques via the `skip` parameter:

```python
baseline = create_baseline("data.csv", skip=["semantic", "priors"])
```

---

## The 6 Analysis Techniques

### 1. Statistical (`baseline/statistical.py`)

Profiles every numeric column for distribution shape, outlier bounds, and information content:

| What is stored | Details |
|---------------|---------|
| **Distribution fit** | Best-matching parametric distribution (`normal`, `log_normal`, `exponential`, `uniform`) chosen by KS-test p-value. Parameters saved for later reconstruction. |
| **Percentile bounds** | p01 and p99 values to define the expected value range. |
| **Shannon entropy** | Computed from a histogram (numeric) or value frequencies (categorical). |
| **Benford's law** | Chi-squared p-value for first-digit conformance — only applied to columns whose names contain financial/count keywords (e.g. `amount`, `revenue`, `price`). |

### 2. Constraints (`baseline/constraints.py`)

Mines the structural rules present in the data:

| What is stored | Details |
|---------------|---------|
| **Functional dependencies** | Column groups where one set determines another (e.g. `zip_code → city`). Each FD is stored with a confidence score (fraction of rows that agree). |
| **Candidate keys** | Single or composite columns that are 100% unique across all rows. |
| **Temporal orders** | Date/time column pairs that are consistently ordered (e.g. `created_at` before `updated_at`), stored with the observed violation rate. |

### 3. Semantic (`baseline/semantic.py`)

Classifies each column's semantic type using keyword heuristics and, when `sentence-transformers` is installed, embedding-based similarity. The result is a `column → type` mapping (e.g. `email_address → email`).

Falls back to keyword-only classification when `goldencheck[semantic]` is not installed.

### 4. Correlation (`baseline/correlation.py`)

Computes pairwise correlations for all column pairs and stores the strong ones:

| Measure | Used for | Threshold |
|---------|---------|-----------|
| Pearson r | Numeric–numeric | `\|r\| >= 0.7` |
| Cramér's V | Categorical–categorical | `V >= 0.3` |

Only correlations classified as `"strong"` are written to the baseline to avoid noise.

### 5. Patterns (`baseline/patterns.py`)

Induces a regex grammar for each string column. Values are generalised to pattern tokens (`D` for digit, `L` for letter, punctuation kept as-is) and the most-common grammar is stored along with its coverage fraction.

Example: a column with values like `GB-12345` and `US-67890` would produce the dominant grammar `LL-DDDDD` at 95% coverage.

### 6. Priors (`baseline/priors.py`)

Runs a full `scan_file()` pass on the data and converts the profiler findings into Bayesian-style confidence priors. These are stored as `{check}:{column}` keys with a confidence value and evidence count. On future scans the priors shift the confidence of matching findings, suppressing recurring low-signal noise while preserving novel anomalies.

---

## Scanning for Drift

### CLI

```bash
goldencheck scan data.csv --baseline goldencheck_baseline.yaml --no-tui
```

The 13 drift checks run after the standard profilers. Drift findings are intermixed with profiler findings in the output, tagged with `source=baseline_drift` in JSON output.

### Python API

```python
from goldencheck import load_baseline, scan_file

baseline = load_baseline("goldencheck_baseline.yaml")
findings, profile = scan_file("new_data.csv", baseline=baseline)

drift_findings = [f for f in findings if f.source == "baseline_drift"]
for f in drift_findings:
    print(f"{f.severity.name}: [{f.column}] {f.check}")
```

---

## The 13 Drift Check Types

All drift findings carry `source="baseline_drift"` and a `metadata` dict with `"technique"` and `"drift_type"` keys for programmatic filtering.

### Statistical checks

| Check | Severity | Description |
|-------|----------|-------------|
| `distribution_drift` | ERROR / WARNING | KS-test p-value against the saved fitted distribution. ERROR if p < 0.01, WARNING if p < 0.05. |
| `entropy_drift` | WARNING | Shannon entropy has changed by more than 0.5 bits. |
| `bound_violation` | ERROR | More than 5% of values fall outside the baseline p01–p99 range. |
| `benford_drift` | WARNING | Benford's law conformance has flipped (passed in baseline, failed now or vice versa). |

### Constraint checks

| Check | Severity | Description |
|-------|----------|-------------|
| `fd_violation` | ERROR | Functional dependency violation rate exceeds 5% or is more than 2× the baseline rate. |
| `key_uniqueness_loss` | ERROR | A candidate key now has duplicate values. |
| `temporal_order_drift` | WARNING | Temporal order violation rate exceeds 5% or is more than 2× the baseline rate. |

### Pattern checks

| Check | Severity | Description |
|-------|----------|-------------|
| `pattern_drift` | WARNING | The dominant baseline pattern's coverage has dropped by more than 5 percentage points. |
| `new_pattern` | INFO | A new format variant (not in baseline) covers more than 5% of current values. |

### Correlation checks

| Check | Severity | Description |
|-------|----------|-------------|
| `correlation_break` | WARNING | A previously strong correlation has dropped by more than 0.1. |
| `new_correlation` | INFO | A new strong correlation (Pearson `\|r\| >= 0.7`) has emerged between two columns that were not correlated in the baseline. |

### Semantic checks

| Check | Severity | Description |
|-------|----------|-------------|
| `type_drift` | WARNING | A column's inferred semantic type has changed from the baseline (e.g. `email` → `string`). |

---

## YAML Baseline Format

A saved baseline file is human-readable YAML. Example structure:

```yaml
version: "1.0"
created: "2026-04-03T10:00:00+00:00"
source: "data.csv"
rows: 50000
columns:
  - id
  - email
  - amount
  - status
  - created_at

stat_profiles:
  amount:
    distribution: log_normal
    params:
      s: 0.82
      loc: 0.0
      scale: 142.3
    benford:
      chi2_pvalue: 0.43
    entropy: 6.12
    bounds:
      p01: 2.50
      p99: 980.00

constraints:
  functional_dependencies:
    - determinant: [zip_code]
      dependent: [city]
      confidence: 0.98
  candidate_keys:
    - [id]
  temporal_orders:
    - before: created_at
      after: updated_at
      violation_rate: 0.0

correlations:
  - columns: [amount, discount]
    measure: pearson
    value: 0.74
    strength: strong

patterns:
  email:
    grammars:
      - pattern: "L+@L+.LL"
        coverage: 0.94
    total_coverage: 0.94

semantic_types:
  email: email
  created_at: date
  updated_at: date

priors:
  "nullability:status":
    confidence: 0.3
    evidence_count: 12
```

---

## Updating a Baseline

When your data intentionally changes (e.g. after a schema migration), update the baseline instead of ignoring drift findings:

```python
from goldencheck import create_baseline, load_baseline

old_baseline = load_baseline("goldencheck_baseline.yaml")
new_baseline = create_baseline("new_reference_data.csv")

old_baseline.update_from(new_baseline)
old_baseline.save("goldencheck_baseline.yaml")
```

`update_from()` applies merge rules rather than a straight overwrite:

| Data | Merge behaviour |
|------|----------------|
| Statistical profiles | Replaced with new values |
| Functional dependencies | Existing FDs kept if new confidence >= 0.8; new FDs added if confidence >= 0.9 |
| Candidate keys / temporal orders | Replaced with new values |
| Semantic types | Replaced with new values |
| Correlations | Replaced with new values |
| Patterns | Replaced with new values |
| Priors | Weighted average by `evidence_count` |
| History | Previous `source` and `created` appended to `history` list |

---

## CI / Pipeline Integration

```yaml
# .github/workflows/data-quality.yml
- name: Check data drift
  run: |
    pip install goldencheck[baseline]
    goldencheck scan data/output.csv \
      --baseline baselines/prod.yaml \
      --no-tui \
      --fail-on error
```

Commit the baseline YAML to your repository alongside your data pipeline code so that drift thresholds are version-controlled.

---

## See Also

- [Profilers](Profilers) — the 6 baseline profilers in detail
- [CLI Reference](CLI) — `baseline` command flags, `--baseline` / `--no-baseline` scan flags
- [Installation](Installation) — `[baseline]` and `[semantic]` extras
- [Architecture](Architecture) — module layout and baseline data flow
