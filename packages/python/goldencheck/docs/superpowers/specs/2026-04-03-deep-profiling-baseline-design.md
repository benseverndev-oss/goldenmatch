# GoldenCheck Deep Profiling & Baseline Drift Detection

**Date:** 2026-04-03
**Status:** Draft
**Scope:** New `baseline` subsystem — statistical analysis, constraint mining, drift detection

---

## Problem

GoldenCheck's 14 profilers are heuristic pattern-matchers. They catch common issues well (88.40 DQBench score) but lack deeper statistical intelligence. They can't discover functional dependencies, fit distributions, learn value grammars, or detect correlation structure. Every scan is stateless — no memory of what "normal" looks like for a dataset.

## Solution

A **learn-once, monitor-forever** system:

1. `goldencheck baseline data.csv` — runs 6 deep analysis techniques, saves discovered intelligence as a human-readable YAML profile
2. `goldencheck scan data.csv` — if a baseline exists, runs fast drift/violation detection against it. If no baseline, runs existing heuristic profilers as today.

The baseline is human-readable and editable. Users can delete constraints they don't care about, tighten thresholds, or add manual overrides.

## Design Principles

- **No LLM dependency.** All 6 techniques are statistical/algorithmic. The existing `learn` command handles LLM-based rule generation separately.
- **Optional heavy deps.** `scipy` and `sentence-transformers` are optional extras under `[baseline]` and `[semantic]`. Core GoldenCheck installs remain lightweight.
- **Isolation.** `numpy`/`scipy` usage is confined to `baseline/` and `drift/` subpackages. Existing profilers remain numpy-free, preserving the Polars-native convention.
- **Baseline is a config file.** YAML, version-controlled, hand-editable. Same philosophy as GoldenCheck's existing rule files.
- **Backward compatible.** `scan_file()` works identically without a baseline. Baseline detection is additive.

---

## Technique Execution Order

Techniques run in a defined order because some depend on outputs of earlier ones:

1. **Semantic Type Inferrer** — runs first so downstream techniques can use type info
2. **Statistical Profiler** — uses semantic types for Benford's Law eligibility
3. **Constraint Miner** — independent, but benefits from type info for pruning
4. **Correlation Analyzer** — independent
5. **Pattern Grammar Inducer** — independent
6. **Confidence Prior Builder** — runs last, consumes outputs from all other techniques

---

## Deep Analysis Techniques

### 1. Statistical Profiler

**Purpose:** Fit distributions, detect outliers via statistical tests, apply Benford's Law, measure entropy.

**What it discovers:**
- Best-fit distribution per numeric column (normal, log-normal, exponential, uniform, Zipf)
- Distribution parameters (mu, sigma, shape, scale)
- Benford's Law conformance (chi-squared test) for eligible columns
- Shannon entropy per column (low entropy = suspicious uniformity, high entropy = possible noise)
- Robust percentile bounds (p01, p99) for future outlier detection

**Benford's Law eligibility:** Applied only to numeric columns that are non-negative, span at least 2 orders of magnitude, and are semantically classified as amounts/counts/populations (not IDs, codes, or percentages). If semantic type inference is skipped, falls back to column-name heuristics (keywords: amount, total, revenue, population, count, price, salary).

**Drift check:** KS-test (Kolmogorov-Smirnov) against saved distribution. Flag if p < 0.05. Compare entropy delta. Flag values outside saved percentile bounds.

**Dependencies:** `scipy.stats` (distribution fitting, KS-test, chi-squared), `numpy`

### 2. Constraint Miner

**Purpose:** Discover functional dependencies (FDs) and temporal ordering constraints automatically.

**Algorithm:** Simplified TANE variant with column-count guard. Discovers FDs from data without user input.

**Column limit:** FD mining runs only on columns with cardinality < 1000. If a dataset has > 30 such columns, only the 30 lowest-cardinality columns are considered. This prevents the O(2^n) worst case from blowing up on wide datasets.

**What it discovers:**
- Functional dependencies with confidence scores (e.g., `zip_code -> city, state` at 97% confidence)
- Approximate FDs (allow a violation threshold, e.g., 2%)
- Candidate keys (columns or column combinations that are unique)
- Temporal order constraints (e.g., `start_date < end_date`) — discovered by checking all date column pairs identified by the Semantic Type Inferrer, recording violation rates

**Drift check:** Re-validate discovered FDs against new data. Flag new violations above the saved threshold. Re-check temporal orders and flag violation rate increases.

**Dependencies:** Pure Python. No external deps beyond numpy for performance.

### 3. Semantic Type Inferrer

**Purpose:** Classify columns by semantic type using lightweight local embeddings instead of regex/keyword heuristics.

**What it discovers:**
- Semantic type per column (email, phone, person_name, address, date, currency, identifier, etc.)
- Confidence score based on embedding similarity between column name + sample values and type exemplars
- Catches synonyms the keyword matcher misses: "DOB", "birth_date", "date_of_birth" all map to `date`

**Drift check:** Verify column still matches inferred type. Flag if new data shifts a column's semantic signature (e.g., a `phone` column starts containing emails).

**Dependencies:** `sentence-transformers` (optional). Falls back to existing keyword-based classifier in `classify_columns()` if not installed. Uses a small model like `all-MiniLM-L6-v2` (80MB).

### 4. Correlation Analyzer

**Purpose:** Discover relationships between column pairs using information-theoretic and statistical measures.

**What it discovers:**
- Mutual information between all column pairs (handles numeric and categorical)
- Cramer's V for categorical-categorical pairs
- Chi-squared test for independence
- Pearson/Spearman correlation for numeric-numeric pairs
- Flags: strong correlations (possible redundancy), expected correlations that are weak (data quality issue), and derived columns (age vs. date_of_birth)

**Drift check:** Re-compute correlation coefficients. Flag significant drops (> 0.1 delta) — a correlation that breaks often means a data source changed or a join went wrong.

**Dependencies:** `scipy.stats`, `numpy`

### 5. Pattern Grammar Inducer

**Purpose:** Learn the structural grammar of values in a column, rather than matching against a fixed regex library.

**What it discovers:**
- Induced grammar per column (e.g., `[A-Z]{3}-[0-9]{4}` for product codes)
- Coverage percentage (what fraction of values match the grammar)
- Multiple grammars if the column has legitimate format variants

**Algorithm:** Character-class generalization with frequency-based merging. Group values by character-class skeleton (e.g., `AAA-0000`), merge similar skeletons, output the dominant grammar(s).

**Interaction with existing PatternConsistencyProfiler:** When a baseline is present and a column has a learned grammar, `PatternConsistencyProfiler` findings for that column are suppressed in favor of the more precise `pattern_drift` findings. Both use distinct `check` names so there's no ambiguity.

**Drift check:** Check grammar coverage against new data. Flag if coverage drops below saved threshold (e.g., was 98%, now 85% — new format variant introduced).

**Dependencies:** Pure Python. Regex for grammar expression.

### 6. Confidence Prior Builder

**Purpose:** Build calibration priors for each check type based on the baseline scan, so future scans weight findings appropriately.

**Model:** Per-check-type lookup table keyed by column name. Each entry stores `(base_confidence, evidence_count)`. When a future scan produces a finding, the prior adjusts the raw confidence via:

```
adjusted = (raw_confidence * evidence_weight + prior_confidence * prior_weight) / (evidence_weight + prior_weight)
```

Where `evidence_weight = 1.0` and `prior_weight = min(evidence_count / 100, 1.0)` — priors with more baseline evidence carry more weight, capped at equal influence.

**Interaction with corroboration boost:** Priors are applied BEFORE `apply_corroboration_boost()`. Corroboration then further adjusts based on multi-check agreement. This avoids double-counting — priors calibrate individual findings, corroboration rewards convergence.

**Dependencies:** Pure Python.

---

## Baseline YAML Format

```yaml
version: "1.0"
created: "2026-04-03T10:00:00Z"
source: "data.csv"
rows: 50000
columns: 30

statistical_profiles:
  income:
    distribution: log_normal
    params: {mu: 10.2, sigma: 0.8}
    benford: {passes: false, chi2: 45.2}
    entropy: 4.82
    bounds: {p01: 12000, p99: 250000}
  age:
    distribution: normal
    params: {mu: 42.3, sigma: 15.1}
    entropy: 3.91
    bounds: {p01: 18, p99: 95}

constraints:
  functional_dependencies:
    - determinant: [zip_code]
      dependent: [city, state]
      confidence: 0.97
    - determinant: [employee_id]
      dependent: [department, manager]
      confidence: 1.0
  candidate_keys:
    - columns: [employee_id]
      unique: true
    - columns: [email]
      unique: true
  temporal_orders:
    - before: start_date
      after: end_date
      violation_rate: 0.002

semantic_types:
  email: [email, backup_email]
  person_name: [first_name, last_name]
  phone: [phone, mobile]
  date: [start_date, end_date, created_at]
  currency: [salary, bonus]
  identifier: [employee_id, department_id]

correlations:
  strong:
    - columns: [city, state]
      cramers_v: 0.94
    - columns: [age, date_of_birth]
      mutual_info: 0.88
      note: derived
  weak_unexpected:
    - columns: [department, salary]
      cramers_v: 0.12
      note: expected correlation not found

patterns:
  product_code:
    grammars:
      - pattern: "[A-Z]{3}-[0-9]{4}"
        coverage: 0.98
    total_coverage: 0.98
  phone:
    grammars:
      - pattern: "\\([0-9]{3}\\) [0-9]{3}-[0-9]{4}"
        coverage: 0.91
      - pattern: "[0-9]{3}-[0-9]{3}-[0-9]{4}"
        coverage: 0.07
    total_coverage: 0.98

confidence_priors:
  format_detection:
    email: {confidence: 0.95, evidence_count: 4800}
    phone: {confidence: 0.85, evidence_count: 4500}
  nullability:
    default: {confidence: 0.9, evidence_count: 50000}
  range_distribution:
    income: {confidence: 0.75, evidence_count: 49000}
    age: {confidence: 0.8, evidence_count: 49500}
```

### Format Versioning

The loader handles unknown keys by ignoring them with a warning log. This allows forward compatibility — a baseline created by a newer version of GoldenCheck can be partially used by an older version.

When the format changes in a breaking way (field renames, semantic changes), the `version` field is bumped. The loader checks `version` and emits a clear error with instructions to regenerate: `"Baseline version 2.0 requires GoldenCheck >= X.Y. Regenerate with: goldencheck baseline data.csv"`.

---

## CLI Interface

```bash
# Create baseline (deep analysis — runs once)
goldencheck baseline data.csv                        # saves goldencheck_baseline.yaml
goldencheck baseline data.csv -o custom.yaml         # custom output path
goldencheck baseline data.csv --skip semantic        # skip embedding-based inference
goldencheck baseline data.csv --skip correlation     # skip expensive correlation matrix
goldencheck baseline data.csv --skip semantic --skip correlation  # multiple skips

# Scan with baseline (fast drift detection)
goldencheck scan data.csv                            # auto-detects goldencheck_baseline.yaml
goldencheck scan data.csv --baseline path/to.yaml    # explicit baseline path
goldencheck scan data.csv --no-baseline              # ignore baseline, force heuristic-only

# Update baseline with new data
goldencheck baseline data.csv --update               # merges new data into existing baseline
```

**Valid `--skip` values:** `statistical`, `constraints`, `semantic`, `correlation`, `patterns`, `priors`. The flag is repeatable.

### Hand-Rolled Parser Update

The `main()` callback in `cli/main.py` has a hand-rolled arg parser for the `goldencheck data.csv` shorthand. This parser must be updated to pass through `--baseline` and `--no-baseline` flags to the `scan` command when invoked via shorthand.

### Baseline Auto-Detection

When `scan_file()` or `goldencheck scan` runs, it looks for a baseline in this order:

1. Explicit `--baseline` flag
2. `goldencheck_baseline.yaml` in the current directory
3. `goldencheck_baseline.yaml` next to the input file
4. No baseline found — run existing heuristic profilers only

**Source validation:** When a baseline is loaded, a warning is emitted if the baseline's `source` field doesn't match the filename being scanned. The baseline is still used — the warning helps catch accidental mismatches.

---

## Python API

```python
from goldencheck import create_baseline, load_baseline, scan_file

# Create and save baseline
baseline = create_baseline("data.csv")
baseline.save("goldencheck_baseline.yaml")

# Load existing baseline
baseline = load_baseline("goldencheck_baseline.yaml")

# Scan with baseline object
findings, profile = scan_file("data.csv", baseline=baseline)

# Scan with baseline path (convenience — loads internally)
findings, profile = scan_file("data.csv", baseline=Path("goldencheck_baseline.yaml"))

# Scan without baseline — existing behavior unchanged
findings, profile = scan_file("data.csv")

# Create baseline with options
baseline = create_baseline(
    "data.csv",
    skip=["semantic"],          # skip techniques
    sample_size=100_000,        # sample for large files
)

# Update baseline with new data
baseline = load_baseline("goldencheck_baseline.yaml")
baseline.update("new_data.csv")
baseline.save("goldencheck_baseline.yaml")
```

**Parameter type:** `scan_file()` accepts `baseline: BaselineProfile | Path | None = None`.

**Public API exports:** `create_baseline` and `load_baseline` are added to `__init__.py` behind a lazy import guard. If `scipy` is not installed, importing them raises `ImportError` with: `"Install goldencheck[baseline] for deep profiling: pip install goldencheck[baseline]"`.

### Sampling Strategy

`create_baseline()` defaults to `sample_size=500_000` (5x the regular `scan_file` default of 100K) because distribution fitting and correlation analysis benefit from more data. For files smaller than the sample size, all rows are used. The same deterministic `maybe_sample(seed=42)` sampler is used for reproducibility.

---

## Baseline Update Semantics

`baseline.update("new_data.csv")` merges new observations into the existing baseline:

| Technique | Merge Strategy |
|---|---|
| Statistical profiles | Refit distribution from scratch on new data. If the new best-fit distribution type changes (e.g., normal → log-normal), update it. Percentile bounds are recalculated. |
| Constraints (FDs) | Re-validate existing FDs against new data. If an FD's confidence drops below 0.8, remove it. New FDs discovered in the new data are added if confidence >= 0.9. |
| Constraints (temporal) | Recalculate violation rates. Keep the constraint; update the rate. |
| Semantic types | Keep existing mappings unless new data shows > 50% of a column no longer matches. |
| Correlations | Recompute on new data. Significant changes (> 0.15 delta) replace the old value. |
| Patterns | Union grammars. If a new grammar covers > 5% of values, add it. Recalculate coverage. |
| Confidence priors | Weighted average: `new = (old * old_count + new * new_count) / (old_count + new_count)`. |

The `created` timestamp updates. The `source` field updates to the new file. A `history` list appends the previous source and timestamp for audit.

---

## Module Layout

```
goldencheck/
├── baseline/
│   ├── __init__.py              # create_baseline(), load_baseline()
│   ├── models.py                # BaselineProfile, StatProfile, Constraint, etc. (Pydantic)
│   ├── statistical.py           # distribution fitting, Benford's Law, entropy
│   ├── constraints.py           # TANE functional dependency mining + temporal orders
│   ├── semantic.py              # embedding-based type inference (optional dep)
│   ├── correlation.py           # mutual info, Cramer's V, chi-squared
│   ├── patterns.py              # character-class grammar induction
│   └── priors.py                # confidence prior builder
├── drift/
│   ├── __init__.py              # run_drift_checks()
│   └── detector.py              # compare current scan against baseline
│       ├── StatisticalDriftDetector   # KS-test, entropy delta, bound violations
│       ├── ConstraintDriftDetector    # FD violation rate changes + temporal order checks
│       ├── SemanticDriftDetector      # type signature shifts
│       ├── CorrelationDriftDetector   # correlation coefficient deltas
│       ├── PatternDriftDetector       # grammar coverage drops
│       └── ConfidenceDriftDetector    # prior-adjusted finding weights
```

### Integration Points

- **Scanner:** `scanner.py` accepts `baseline` parameter in `scan_file()`. If provided, runs `drift.run_drift_checks()` alongside existing profilers. Drift findings are merged into the findings list with `source="baseline_drift"`.
- **CLI:** New `baseline` command in `cli/main.py`. `scan` command gets `--baseline` and `--no-baseline` flags. Hand-rolled parser in `main()` callback updated to forward these flags.
- **TUI:** Drift findings render with a distinct "DRIFT" badge so users can distinguish them from heuristic findings.
- **Notebook:** `Finding._repr_html_()` updated to render `source="baseline_drift"` with a "[DRIFT]" label, alongside the existing "[LLM]" label for `source="llm"`.
- **Public API:** `create_baseline()` and `load_baseline()` added to `__init__.py` exports (lazy import).
- **Pattern suppression:** When baseline is present, `PatternConsistencyProfiler` findings are suppressed for columns that have learned grammars in the baseline, avoiding duplicate/contradictory pattern findings.

---

## Drift Finding Format

Drift findings use the existing `Finding` dataclass with specific conventions:

```python
Finding(
    severity=Severity.WARNING,
    column="income",
    check="distribution_drift",
    message="Distribution shifted from log_normal(mu=10.2) — KS p-value: 0.003",
    source="baseline_drift",
    confidence=0.92,
    metadata={
        "technique": "statistical",
        "baseline_distribution": "log_normal",
        "ks_pvalue": 0.003,
        "drift_type": "distribution_shift",
    },
)
```

### Drift Check Types

| Check Name | Technique | Severity Logic |
|---|---|---|
| `distribution_drift` | Statistical | ERROR if p < 0.01, WARNING if p < 0.05 |
| `entropy_drift` | Statistical | WARNING if delta > 0.5 |
| `bound_violation` | Statistical | ERROR if outside p01/p99 bounds |
| `benford_drift` | Statistical | WARNING if conformance flips |
| `fd_violation` | Constraint | ERROR if violation rate > 2x baseline |
| `new_fd_violation` | Constraint | WARNING for newly broken dependencies |
| `key_uniqueness_loss` | Constraint | ERROR if candidate key gains duplicates |
| `temporal_order_drift` | Constraint | WARNING if violation rate > 2x baseline |
| `type_drift` | Semantic | WARNING if semantic type changes |
| `correlation_break` | Correlation | WARNING if strong correlation drops > 0.1 |
| `new_correlation` | Correlation | INFO for newly emerged correlations |
| `pattern_drift` | Pattern | WARNING if grammar coverage drops > 5% |
| `new_pattern` | Pattern | INFO for new format variants |

---

## Dependencies

| Package | Required? | Purpose | Size |
|---|---|---|---|
| `scipy` | Optional (`[baseline]`) | Distribution fitting, KS-test, chi-squared, mutual info | ~40MB — new dependency, not currently in the dependency tree |
| `numpy` | Optional (`[baseline]`) | Numeric operations for baseline/drift modules only | ~20MB — likely already installed via Polars, but not a declared dep |
| `sentence-transformers` | Optional (`[semantic]`) | Semantic type inference via embeddings | ~80MB model download |

```toml
[project.optional-dependencies]
baseline = ["scipy>=1.10", "numpy>=1.24"]
semantic = ["sentence-transformers>=2.0"]
```

Install with: `pip install goldencheck[baseline]` or `pip install goldencheck[baseline,semantic]`

Without `[baseline]`, the `goldencheck baseline` command exits with a clear error: `"Install goldencheck[baseline] for deep profiling: pip install goldencheck[baseline]"`.

Without `[semantic]`, semantic type inference falls back to the existing `classify_columns()` keyword matcher.

---

## Out of Scope

- **LLM integration** — handled by existing `learn` command
- **Multi-file referential integrity** — future feature, not part of this spec
- **Web dashboard for drift trends** — future feature
- **Auto-fix based on drift** — that's `goldencheck fix`
- **Real-time / streaming drift detection** — batch only for v1
- **Custom technique plugins** — keep it simple, all 6 are built-in
- **Multi-file baseline** — one baseline per dataset; scanning multiple files requires explicit `--baseline` per file

---

## Testing Strategy

- **Unit tests per technique:** Each of the 6 modules gets its own test file with synthetic data that has known properties (known distribution, known FDs, known patterns)
- **Baseline round-trip:** Create baseline, save to YAML, load, verify equality
- **Drift detection:** Create baseline from clean data, introduce known drift, verify correct findings
- **Fallback behavior:** Verify `scan_file()` works identically without baseline
- **Optional dep handling:** Verify baseline module raises clear `ImportError` without scipy. Verify semantic module falls back gracefully without `sentence-transformers`.
- **Pattern suppression:** Verify `PatternConsistencyProfiler` findings are suppressed for baseline-covered columns
- **Update semantics:** Create baseline, update with modified data, verify merge behavior for each technique
- **DQBench integration:** Baseline should not regress the existing 88.40 DQBench Detect score (drift findings are additive, not replacing existing profiler findings)
- **Hand-rolled parser:** Verify `goldencheck data.csv --baseline path.yaml` routes correctly
