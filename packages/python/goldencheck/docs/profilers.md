---
title: Profilers
layout: default
nav_order: 6
---

GoldenCheck runs 10 column-level profilers and 2 cross-column profilers on every scan. Each profiler is independent — they do not share state and can be extended without touching any other profiler.

---

## Column-Level Profilers

Column profilers implement `BaseProfiler` and receive a single column at a time:

```python
class BaseProfiler(ABC):
    @abstractmethod
    def profile(self, df: pl.DataFrame, column: str) -> list[Finding]:
        ...
```

---

### TypeInferenceProfiler

**File:** `goldencheck/profilers/type_inference.py`

Detects string columns where most values are actually numeric. This happens when a CSV is read without type inference or when a numeric column has been stored as text.

**Triggers on:** `String` / `Utf8` dtype columns only.

**Logic:** Attempts to cast the column to `Float64`. If 80%+ of non-null values cast successfully, a finding is raised. A secondary cast to `Int64` determines whether to label the type as `integer` or `numeric`.

| Severity | Condition |
|----------|-----------|
| WARNING | >=80% of string values are numeric |

**Example finding:**
```
Column is string but 98% of values are integer (2 non-integer values)
Suggestion: Consider casting to integer
```

---

### NullabilityProfiler

**File:** `goldencheck/profilers/nullability.py`

Classifies whether a column is required (no nulls), optional (some nulls), or entirely null.

**Triggers on:** All column types.

| Severity | Condition |
|----------|-----------|
| ERROR | 100% of rows are null |
| INFO | 0 nulls and row count >= 10 (likely required) |
| INFO | Some nulls but not all (optional column) |

**Example findings:**
```
0 nulls across 50,000 rows — likely required
12 nulls (0.2%) — column is optional
Column is entirely null (100 rows)
```

---

### UniquenessProfiler

**File:** `goldencheck/profilers/uniqueness.py`

Identifies columns that are likely primary keys (100% unique) and columns that are nearly unique but have a small number of duplicates.

**Triggers on:** All column types. Requires at least 10 rows.

| Severity | Condition |
|----------|-----------|
| INFO | 100% unique across all non-null rows |
| WARNING | >95% unique but not 100% (near-unique with duplicates) |

**Example findings:**
```
100% unique across 10,000 rows — likely primary key
Near-unique column (98.3% unique) with 17 duplicates
```

---

### FormatDetectionProfiler

**File:** `goldencheck/profilers/format_detection.py`

Checks string columns for known formats: email addresses, US phone numbers, and URLs. When a column is predominantly one format, any non-matching values are flagged as a separate WARNING.

**Triggers on:** `String` / `Utf8` dtype columns only.

**Detected formats:**

| Format | Pattern used |
|--------|-------------|
| email | `^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$` |
| phone | `^\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}$` |
| url | `^https?://` |

**Threshold:** 70% match to classify the column as that format.

| Severity | Condition |
|----------|-----------|
| INFO | >=70% of values match a known format (column classified) |
| WARNING | Non-matching values present in a classified column |

**Example findings:**
```
Column appears to contain email values (94.3% match)
6 value(s) do not match expected email format
  Sample: [bad@, notanemail, user @domain.com]
```

---

### RangeDistributionProfiler

**File:** `goldencheck/profilers/range_distribution.py`

Reports the numeric range and detects statistical outliers using a 3-standard-deviation threshold.

**Triggers on:** Numeric dtypes (`Int8` through `Float64`). Requires at least 2 non-null values.

| Severity | Condition |
|----------|-----------|
| INFO | Always emitted — reports min, max, mean |
| WARNING | Values beyond 3 standard deviations from the mean |

**Example findings:**
```
Range: min=1, max=120, mean=34.21
3 outlier(s) detected beyond 3 standard deviations
  Sample: [999, 1050, -5]
```

---

### CardinalityProfiler

**File:** `goldencheck/profilers/cardinality.py`

Flags low-cardinality columns as enum candidates. Columns with fewer than 20 unique values and at least 50 rows are surfaced as potential enums.

**Triggers on:** All column types.

**Thresholds:**
- `ENUM_UNIQUE_THRESHOLD = 20` unique values
- `ENUM_MIN_ROWS = 50` minimum row count

| Severity | Condition |
|----------|-----------|
| INFO | Low cardinality — enum candidate |
| INFO | Standard cardinality report |

**Example findings:**
```
Low cardinality: 4 unique value(s) across 5,000 rows — consider using an enum type
  Sample: [active, closed, inactive, pending]
Suggestion: Define an enum or categorical constraint for this column
```

---

### PatternConsistencyProfiler

**File:** `goldencheck/profilers/pattern_consistency.py`

Detects mixed structural patterns within a string column. Values are generalized to a pattern signature (digits become `D`, letters become `L`, punctuation preserved), and minority patterns are flagged.

**Triggers on:** `String` / `Utf8` dtype columns only.

**Logic:** Builds a frequency distribution of generalized patterns. Any pattern representing less than 30% of values is a minority pattern and gets its own finding.

**Threshold:** `MINORITY_THRESHOLD = 0.30`

| Severity | Condition |
|----------|-----------|
| WARNING | A minority pattern (<30% of values) is present |

**Example finding:**
```
Inconsistent pattern detected: 'DDDDDDDDDD' appears in 47 row(s) (0.9%)
  vs dominant pattern 'LLL LLL-LLLL' (5,100 row(s))
  Sample: [2025551234, 8005559999]
Suggestion: Standardize values to a single format/pattern
```

---

### EncodingDetectionProfiler

**File:** `goldencheck/profilers/encoding_detection.py`

Detects encoding artifacts and invisible character issues in string columns. These are common when data has been exported from Excel, copy-pasted from web pages, or converted between character sets.

**Triggers on:** `String` / `Utf8` dtype columns only.

**Detected issues:**

| Issue | Characters | Description |
|-------|-----------|-------------|
| Zero-width characters | U+200B, U+200C, U+200D, U+FEFF | Invisible characters that cause silent comparison failures |
| Smart quotes | `"`, `"`, `'`, `'` | Typographic quotes that break exact-match lookups |
| Latin-1 mojibake | `Ã`, `Â`, `â€` | UTF-8 bytes decoded as Latin-1 — garbled accented characters |

| Severity | Condition |
|----------|-----------|
| WARNING | Any of the above patterns detected in one or more values |

**Example finding:**
```
3 value(s) contain zero-width Unicode characters (U+200B/U+FEFF)
  Sample: ["John​Smith", "Alice​"]
Suggestion: Strip zero-width characters before storing or comparing values
```

---

### SequenceGapProfiler

**File:** `goldencheck/profilers/sequence_gap.py`

Detects gaps in numeric sequences. Useful for identifying missing records in ID columns, invoice numbers, order sequences, or any column expected to be a contiguous integer range.

**Triggers on:** Integer dtype columns that are 100% unique and have low cardinality relative to the row count.

**Logic:** Computes `expected_count = max - min + 1` and compares against `actual_count`. If the ratio is below 0.98 (more than 2% of values are missing), a finding is raised. The first few missing values are included as samples.

| Severity | Condition |
|----------|-----------|
| WARNING | Sequence has gaps (missing integers between min and max) |

**Example finding:**
```
Sequence gaps detected: 47 missing value(s) between 1 and 10000
  Missing sample: [23, 47, 102, 891, 1204]
Suggestion: Investigate whether records were deleted or IDs were never assigned
```

---

### DriftDetectionProfiler

**File:** `goldencheck/profilers/drift_detection.py`

Detects statistical drift between the first and second half of a dataset. This surfaces data that changes character over time — common in logs, event streams, or pipelines that append data from different sources.

**Triggers on:** All columns. Requires at least 100 rows.

**Categorical drift:** Compares the top-value distribution between the first and second half. If a dominant value in the first half disappears or a new dominant value appears in the second half, drift is flagged.

**Numeric drift:** Compares the mean of the first half vs. the second half. If the means differ by more than 20% of the overall standard deviation, drift is flagged.

| Severity | Condition |
|----------|-----------|
| WARNING | Categorical distribution shift between first and second half |
| WARNING | Numeric mean shift > 20% of standard deviation between halves |

**Example findings:**
```
Categorical drift: value 'active' drops from 72% to 31% between halves
Suggestion: Investigate whether data was loaded from different time periods or sources

Numeric drift: mean shifts from 42.3 to 61.7 between first and second half of dataset
Suggestion: Check for batch effects or pipeline changes that may have altered values over time
```

---

## Cross-Column Profilers

Cross-column profilers receive the full DataFrame and look at relationships between columns. They implement a compatible `profile(df)` interface but are not subclasses of `BaseProfiler`.

---

### TemporalOrderProfiler

**File:** `goldencheck/relations/temporal.py`

Detects column pairs where start-like columns have values later than their corresponding end-like columns.

**Heuristics for pairing columns by name:**

| Start keyword | End keyword |
|--------------|------------|
| `start` | `end` |
| `created` | `updated` |
| `begin` | `finish` |

Pairing is done by substring match on lowercased column names.

**Type handling:** String columns are attempted to be parsed as `%Y-%m-%d` dates. Non-date columns are skipped.

| Severity | Condition |
|----------|-----------|
| ERROR | Any row where start > end |

**Example finding:**
```
Column 'start_date' has 3 row(s) where its value is later than 'end_date',
violating expected temporal order.
  Sample: [2024-06-01 > 2024-05-15, ...]
Suggestion: Ensure 'start_date' <= 'end_date' for all rows.
```

---

### NullCorrelationProfiler

**File:** `goldencheck/relations/null_correlation.py`

Identifies pairs of columns whose null/non-null patterns are highly correlated. This surfaces logical groups where fields should always be populated together (e.g., `shipping_address` and `shipping_city`).

**Threshold:** `_DEFAULT_THRESHOLD = 0.90` (90% agreement on null/non-null pattern).

Pairs where neither column has any nulls are skipped — there is no interesting signal.

| Severity | Condition |
|----------|-----------|
| INFO | Null pattern agreement >= 90% |

**Example finding:**
```
Columns 'billing_address' and 'billing_zip' have strongly correlated null patterns
(96.2% agreement). They may represent a logical group.
Suggestion: Consider treating 'billing_address' and 'billing_zip' as a unit —
validate that they are both populated or both absent together.
```

---

## Severity Levels

| Level | Integer value | Meaning |
|-------|--------------|---------|
| INFO | 1 | Informational observation, no action required |
| WARNING | 2 | Potential issue worth reviewing |
| ERROR | 3 | Definite data quality problem |

---

## Adding a Custom Profiler

1. Create a new file in `goldencheck/profilers/`:

```python
# goldencheck/profilers/my_profiler.py
from __future__ import annotations
import polars as pl
from goldencheck.models.finding import Finding, Severity
from goldencheck.profilers.base import BaseProfiler

class MyProfiler(BaseProfiler):
    def profile(self, df: pl.DataFrame, column: str) -> list[Finding]:
        findings: list[Finding] = []
        col = df[column]

        # Your logic here
        if some_condition:
            findings.append(Finding(
                severity=Severity.WARNING,
                column=column,
                check="my_check_name",
                message="Description of the issue",
                affected_rows=0,
                sample_values=[],
                suggestion="What the user should do",
            ))

        return findings
```

2. Register it in `goldencheck/engine/scanner.py`:

```python
from goldencheck.profilers.my_profiler import MyProfiler

COLUMN_PROFILERS = [
    TypeInferenceProfiler(),
    NullabilityProfiler(),
    # ... existing profilers ...
    MyProfiler(),
]
```

That is all. The scanner loops over `COLUMN_PROFILERS` for every column automatically.

For a cross-column profiler, add it to `RELATION_PROFILERS` instead. The only requirement is a `profile(df: pl.DataFrame) -> list[Finding]` method.
