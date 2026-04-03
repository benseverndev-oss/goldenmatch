---
layout: default
title: Deep Profiling Baseline
nav_order: 7
---

# Deep Profiling Baseline

Learn dataset statistical properties once. Detect drift forever.

GoldenCheck's baseline system goes beyond the per-scan drift profiler. It runs **6 deep analysis techniques** over a known-good dataset, saves a rich YAML profile, and then detects **13 types of drift** on every future scan — automatically.

---

## Quick Start

```bash
pip install goldencheck[baseline]

# Step 1: Create baseline from clean/known-good data
goldencheck baseline data.csv

# Step 2: Scan new data — drift is detected automatically
goldencheck scan new_data.csv
```

GoldenCheck saves the baseline to `goldencheck_baseline.yaml` and picks it up automatically on subsequent scans. No flags required once the file exists.

---

## How It Works

The baseline workflow follows a **learn-once, monitor-forever** pattern:

```
LEARN (once)
  goldencheck baseline data.csv
      │
      ├─ StatisticalProfiler   — mean, std, quantiles, IQR for numeric columns
      ├─ ConstraintMiner        — infer NOT NULL, unique, range, enum, regex constraints
      ├─ SemanticTypeInferrer   — detect email, phone, date, ID, currency patterns
      ├─ CorrelationAnalyzer    — record column pair correlations (Pearson / Spearman)
      ├─ PatternGrammarInducer  — extract structural patterns (e.g. XXX-DDDD)
      └─ ConfidencePriorBuilder — assign per-check confidence weights from data evidence
              │
              ▼
      goldencheck_baseline.yaml   ← human-readable, version-controllable

MONITOR (every scan)
  goldencheck scan new_data.csv
      │
      ├─ [all standard profilers run as normal]
      │
      └─ drift/detector.py  (13 check types vs baseline)
              │
              ▼
      Drift findings surface alongside standard findings
```

The baseline file is **plain YAML**. You can edit it, commit it, diff it in PRs, and share it across environments.

---

## The 6 Techniques

### 1. Statistical Profiler

**Module:** `goldencheck/baseline/statistical.py`

Records the full distributional shape of every numeric column: mean, standard deviation, min, max, and the 5th, 25th, 50th, 75th, and 95th percentiles.

**What it discovers:**
- Expected numeric range and central tendency
- Interquartile range (used for outlier bounds)
- Skew indicators from percentile spread

**Example YAML output:**

```yaml
columns:
  age:
    statistical:
      mean: 34.21
      std: 12.87
      min: 18
      max: 92
      p05: 20.0
      p25: 24.0
      p50: 32.0
      p75: 44.0
      p95: 58.0
```

---

### 2. Constraint Miner

**Module:** `goldencheck/baseline/constraints.py`

Infers hard constraints from the data by observing what is universally true across all rows:

- **NOT NULL** — column had zero nulls
- **UNIQUE** — column had 100% unique values
- **RANGE** — observed min/max bounds for numeric columns
- **ENUM** — column had low cardinality (≤20 values); records the allowed set
- **REGEX** — consistent structural pattern detected (e.g., `^\d{5}$` for US zip codes)

**Example YAML output:**

```yaml
columns:
  status:
    constraints:
      not_null: true
      enum:
        - active
        - inactive
        - pending
  zip_code:
    constraints:
      regex: '^\d{5}(-\d{4})?$'
      not_null: true
```

---

### 3. Semantic Type Inferrer

**Module:** `goldencheck/baseline/semantic.py`

Applies GoldenCheck's semantic type classifier to the baseline data and **locks in** the detected type. On future scans, if the semantic type changes or the column format drifts from the baseline type, it is flagged.

Requires `goldencheck[semantic]` for embedding-based detection.

**What it discovers:**
- Column semantic type (`email`, `phone`, `url`, `name`, `id`, `currency`, `date`, `category`)
- Format match rate (how consistently the column matched the type)
- Whether the type was detected by name hint, format, or embedding similarity

**Example YAML output:**

```yaml
columns:
  customer_email:
    semantic:
      type: email
      match_rate: 0.994
      detection_method: format
```

---

### 4. Correlation Analyzer

**Module:** `goldencheck/baseline/correlation.py`

Records pairwise correlations between numeric columns using Pearson and Spearman coefficients. Captures the expected correlation structure of the dataset.

**What it discovers:**
- Strong positive or negative correlations between column pairs
- Which correlations are stable enough to enforce as drift checks

Only pairs with |correlation| ≥ 0.7 are recorded, to keep the baseline focused on meaningful relationships.

**Example YAML output:**

```yaml
correlations:
  - columns: [age, years_experience]
    pearson: 0.84
    spearman: 0.81
  - columns: [order_total, item_count]
    pearson: 0.91
    spearman: 0.89
```

---

### 5. Pattern Grammar Inducer

**Module:** `goldencheck/baseline/patterns.py`

Generalises the structural patterns of string columns into a grammar: digits become `D`, letters become `L`, punctuation is preserved. Records the dominant pattern and its coverage.

**What it discovers:**
- The structural "shape" expected for each string column
- How consistently values conform to that shape
- Whether multiple patterns coexist (e.g., short and long formats)

**Example YAML output:**

```yaml
columns:
  product_code:
    patterns:
      dominant: 'LLL-DDDD'
      coverage: 0.983
      alternatives:
        - pattern: 'LLLLL-DDDD'
          coverage: 0.017
```

---

### 6. Confidence Prior Builder

**Module:** `goldencheck/baseline/priors.py`

Assigns a per-check confidence weight to every column based on the evidence seen in the baseline data. These priors are used to **calibrate** drift findings: a check backed by 50,000 rows of consistent data gets higher confidence than one backed by 200.

**What it discovers:**
- Evidence strength for each inferred constraint
- Row count at baseline time (used to weight future comparisons)
- Which checks should fire at ERROR vs WARNING given the confidence level

**Example YAML output:**

```yaml
columns:
  transaction_id:
    priors:
      uniqueness:
        confidence: high
        evidence_rows: 125000
      not_null:
        confidence: high
        evidence_rows: 125000
```

---

## Drift Detection

When `goldencheck_baseline.yaml` exists, the drift detector (`goldencheck/drift/detector.py`) runs **13 check types** on every scan. Drift findings are returned alongside standard profiler findings.

| Check | Severity | What it detects |
|-------|----------|-----------------|
| `null_rate_increase` | WARNING | Null rate grew above baseline null rate |
| `null_rate_introduced` | ERROR | Column was NOT NULL in baseline but now has nulls |
| `enum_violation` | ERROR | Value not in the baseline enum set |
| `range_min_violation` | WARNING | Minimum value dropped below baseline min |
| `range_max_violation` | WARNING | Maximum value exceeded baseline max |
| `mean_shift` | WARNING | Mean shifted more than 2 standard deviations from baseline |
| `std_increase` | WARNING | Standard deviation more than doubled vs baseline |
| `semantic_type_change` | ERROR | Column semantic type changed from baseline |
| `format_rate_drop` | WARNING | Format match rate dropped >10 percentage points |
| `pattern_drift` | WARNING | Dominant structural pattern changed |
| `new_pattern_appeared` | INFO | New pattern not seen in baseline |
| `correlation_broken` | WARNING | Column pair correlation dropped below 0.5 (was ≥0.7) |
| `cardinality_explosion` | WARNING | Cardinality increased >5x vs baseline (enum drift) |

**Severity logic:** Checks derived from strict constraints (NOT NULL, enum, semantic type) are always `ERROR`. Distribution-based checks (mean shift, std, format rate) fire as `WARNING` by default, upgraded to `ERROR` if the deviation is severe (>3 standard deviations or >3x baseline std).

---

## CLI Reference

### `baseline` command

Create or update a baseline from a data file:

```bash
goldencheck baseline <file> [flags]
```

**Examples:**

```bash
# Create baseline from production data export
goldencheck baseline data.csv

# Save to a custom path
goldencheck baseline data.csv --output baselines/production.yaml

# Update an existing baseline (merges new statistics)
goldencheck baseline data.csv --update

# Skip specific techniques
goldencheck baseline data.csv --skip correlation,patterns
```

**Flags:**

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--output`, `-o` | path | `goldencheck_baseline.yaml` | Output path for the baseline file |
| `--update` | bool | false | Merge new statistics into an existing baseline instead of overwriting |
| `--skip <techniques>` | string | — | Comma-separated list of techniques to skip: `statistical`, `constraints`, `semantic`, `correlation`, `patterns`, `priors` |

Requires `goldencheck[baseline]` installed:

```bash
pip install goldencheck[baseline]
```

---

### `scan` with baseline flags

```bash
# Use a specific baseline file (instead of goldencheck_baseline.yaml)
goldencheck scan new_data.csv --baseline baselines/production.yaml

# Disable automatic baseline loading
goldencheck scan new_data.csv --no-baseline
```

**Flags added by v1.1.0:**

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--baseline <path>` | path | `goldencheck_baseline.yaml` | Path to baseline file. Auto-loaded if file exists |
| `--no-baseline` | bool | false | Disable baseline loading even if `goldencheck_baseline.yaml` exists |

---

## Python API

### `create_baseline()`

```python
from goldencheck.baseline import create_baseline

baseline = create_baseline("data.csv")
# Saves to goldencheck_baseline.yaml by default

baseline = create_baseline(
    "data.csv",
    output="baselines/production.yaml",
    skip=["correlation"],   # skip expensive technique
)
```

### `load_baseline()`

```python
from goldencheck.baseline import load_baseline

baseline = load_baseline("goldencheck_baseline.yaml")
print(baseline.columns["age"].statistical.mean)
print(baseline.columns["status"].constraints.enum)
```

### `scan_file()` with baseline

```python
from goldencheck.engine.scanner import scan_file
from goldencheck.baseline import load_baseline

baseline = load_baseline("goldencheck_baseline.yaml")
findings, profile = scan_file("new_data.csv", baseline=baseline)

# Drift findings have check names like "null_rate_introduced", "mean_shift", etc.
drift_findings = [f for f in findings if f.source == "baseline"]
for f in drift_findings:
    print(f"{f.severity.name}: [{f.column}] {f.check} — {f.message}")
```

---

## Baseline YAML Format

A complete `goldencheck_baseline.yaml` has four top-level sections:

```yaml
goldencheck_baseline: 1           # format version
created_at: "2025-11-12T09:14:32"
source_file: data.csv
row_count: 125000
column_count: 14

columns:
  customer_id:
    statistical: null             # not numeric — omitted
    constraints:
      not_null: true
      unique: true
    semantic:
      type: id
      match_rate: 1.0
      detection_method: heuristic
    patterns:
      dominant: 'LLLL-DDDDDDDD'
      coverage: 1.0
      alternatives: []
    priors:
      uniqueness:
        confidence: high
        evidence_rows: 125000
      not_null:
        confidence: high
        evidence_rows: 125000

  age:
    statistical:
      mean: 34.21
      std: 12.87
      min: 18
      max: 92
      p05: 20.0
      p25: 24.0
      p50: 32.0
      p75: 44.0
      p95: 58.0
    constraints:
      not_null: true
      range:
        min: 18
        max: 92
    semantic:
      type: null
    patterns: null
    priors:
      not_null:
        confidence: high
        evidence_rows: 125000
      range:
        confidence: medium
        evidence_rows: 125000

correlations:
  - columns: [age, years_experience]
    pearson: 0.84
    spearman: 0.81
  - columns: [order_total, item_count]
    pearson: 0.91
    spearman: 0.89
```

---

## Configuration as Code

The baseline YAML is designed to be **human-editable and version-controllable**:

- **Edit constraints manually** — tighten an enum, add a regex, lower a range bound
- **Commit to version control** — diff baselines across releases to see what changed
- **Share across environments** — one baseline file works in dev, staging, and CI
- **Review in PRs** — baseline changes are visible in GitHub/GitLab diffs

**Example: tighten an enum in the baseline**

```yaml
# Before (mined from data)
columns:
  status:
    constraints:
      enum:
        - active
        - inactive
        - pending
        - legacy          # you want to retire this value

# After (manually edited)
columns:
  status:
    constraints:
      enum:
        - active
        - inactive
        - pending
        # "legacy" removed — will now trigger ERROR on any new rows with this value
```

**Committing the baseline:**

```bash
git add goldencheck_baseline.yaml
git commit -m "chore: tighten status enum, retire legacy value"
```

---

## Optional Extras

| Extra | Command | Adds |
|-------|---------|------|
| `[baseline]` | `pip install goldencheck[baseline]` | scipy, numpy — required for statistical profiling and correlation analysis |
| `[semantic]` | `pip install goldencheck[semantic]` | sentence-transformers — enables embedding-based semantic type detection in the baseline |

Install both:

```bash
pip install goldencheck[baseline,semantic]
```

---

## Next Steps

- [CLI Reference]({% link cli.md %}#baseline) — full flag reference
- [Profilers]({% link profilers.md %}#baseline-profilers) — how baseline techniques relate to scan profilers
- [Architecture]({% link architecture.md %}#baseline-and-drift-modules) — module layout for `baseline/` and `drift/`
