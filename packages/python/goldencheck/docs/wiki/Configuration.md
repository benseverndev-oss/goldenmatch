# Configuration

GoldenCheck configuration lives in `goldencheck.yml` in your working directory. The file is optional — GoldenCheck works without one. You create it by pinning findings in the TUI and pressing F2, or by writing it by hand.

---

## Full Reference

```yaml
version: 1

settings:
  sample_size: 100000        # rows to sample for large files
  severity_threshold: warning
  fail_on: error             # exit code 1 on: error | warning

columns:
  email:
    type: string
    required: true
    format: email
    unique: true

  age:
    type: integer
    range: [0, 120]
    outlier_stddev: 3.0

  status:
    type: string
    enum: [active, inactive, pending, closed]

  notes:
    type: string
    nullable: true

relations:
  - type: temporal_order
    columns: [start_date, end_date]

  - type: null_correlation
    columns: [billing_address, billing_zip]

ignore:
  - column: phone
    check: pattern_consistency
  - column: notes
    check: nullability
```

---

## `settings`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `sample_size` | int | `100000` | Maximum rows used for profiling. Files larger than this are randomly sampled. |
| `severity_threshold` | string | `"warning"` | Minimum severity level to include in reports. Values: `info`, `warning`, `error`. |
| `fail_on` | string | `"error"` | Exit code 1 is returned when any finding at or above this severity is present during `validate`. Values: `error`, `warning`. |

---

## `columns`

Each key is a column name. All column fields are optional — you only need to specify the constraints you care about.

| Field | Type | Description |
|-------|------|-------------|
| `type` | string | Expected data type. Values: `string`, `integer`, `float`, `boolean`, `date`, `datetime`. |
| `required` | bool | If `true`, no null values are allowed. |
| `nullable` | bool | Explicit override — if `true`, nulls are expected and not flagged. |
| `format` | string | Expected string format. Values: `email`, `phone`, `url`. |
| `unique` | bool | If `true`, all values must be unique. |
| `range` | [min, max] | Inclusive numeric range. Values outside this range are flagged as errors. |
| `enum` | list[string] | Allowed values. Any value not in this list is flagged as an error. |
| `outlier_stddev` | float | Standard deviations threshold for outlier detection. Default is 3.0. |

### Column rule examples

```yaml
columns:
  # Email column — required, unique, must be valid email format
  email:
    type: string
    required: true
    format: email
    unique: true

  # Age — integer, bounded range, custom outlier threshold
  age:
    type: integer
    range: [0, 120]
    outlier_stddev: 4.0

  # Status — fixed set of allowed values
  status:
    type: string
    enum: [active, inactive, pending, closed]

  # Notes — explicitly nullable, no format constraint
  notes:
    type: string
    nullable: true
```

---

## `relations`

Cross-column rules. Each relation has a `type` and a `columns` list.

### `temporal_order`

Validates that the first column is always less than or equal to the second column. Both columns must be date or datetime types (or parseable as `%Y-%m-%d` strings).

```yaml
relations:
  - type: temporal_order
    columns: [start_date, end_date]

  - type: temporal_order
    columns: [created_at, updated_at]
```

### `null_correlation`

Documents that these columns are expected to be null/non-null together. Violations (one null, the other non-null) are flagged as warnings.

```yaml
relations:
  - type: null_correlation
    columns: [billing_address, billing_city, billing_zip]
```

---

## `ignore`

The ignore list prevents specific findings from appearing in future scans. Use this to dismiss false positives permanently. Each entry requires both a `column` name and a `check` name.

```yaml
ignore:
  - column: phone
    check: pattern_consistency    # mixed formats are expected

  - column: notes
    check: nullability            # nulls are fine in free-text fields

  - column: legacy_id
    check: type_inference         # intentionally stored as string
```

**Check names** correspond to the `check` field on a `Finding`:

| Check name | Raised by |
|------------|-----------|
| `type_inference` | TypeInferenceProfiler |
| `nullability` | NullabilityProfiler |
| `uniqueness` | UniquenessProfiler |
| `format_detection` | FormatDetectionProfiler |
| `range_distribution` | RangeDistributionProfiler |
| `cardinality` | CardinalityProfiler |
| `pattern_consistency` | PatternConsistencyProfiler |
| `temporal_order` | TemporalOrderProfiler |
| `null_correlation` | NullCorrelationProfiler |

---

## Semantic Types

GoldenCheck ships with built-in semantic type definitions (`goldencheck/semantic/types.py`). You can override or extend these by placing a `goldencheck_types.yaml` file in your project directory.

### `types.yaml` (built-in, read-only)

Located at `goldencheck/semantic/types.yaml`. Contains the canonical list of built-in semantic types and their detection heuristics:

```yaml
version: 1

types:
  email:
    keywords: [email, e_mail, mail]
    format: email
  phone:
    keywords: [phone, mobile, cell, tel]
    format: phone
  name:
    keywords: [name, first_name, last_name, full_name, surname]
  id:
    keywords: [id, uuid, guid, identifier]
    unique: true
  currency:
    keywords: [price, amount, cost, total, revenue, fee]
    numeric: true
  date:
    keywords: [date, created_at, updated_at, timestamp]
    temporal: true
  category:
    keywords: [status, type, category, tier, group]
    low_cardinality: true
```

### `goldencheck_types.yaml` (project-level, user-defined)

Place this file in your project root to add custom semantic types or override built-in ones. Custom types are merged on top of the built-in types:

```yaml
version: 1

types:
  sku:
    keywords: [sku, product_code, item_code]
    pattern: "^[A-Z]{2,4}-\\d{4,8}$"

  customer_tier:
    keywords: [tier, plan, subscription_level]
    enum: [free, pro, enterprise]
    low_cardinality: true
```

Custom type definitions support:

| Field | Description |
|-------|-------------|
| `keywords` | Column name substrings that trigger this type (case-insensitive) |
| `format` | Expected format: `email`, `phone`, `url` |
| `pattern` | Regex pattern that values should match |
| `enum` | List of allowed values |
| `unique` | Whether values are expected to be unique |
| `numeric` | Whether values are expected to be numeric |
| `temporal` | Whether values are expected to be dates/datetimes |
| `low_cardinality` | Whether the column is expected to be a small enum |

---

## Layering Strategy

GoldenCheck does not auto-generate a config on scan. The file only contains what you explicitly pin. This keeps configs small and meaningful.

**Recommended workflow:**

1. `goldencheck scan data.csv` — explore findings, no config needed
2. In the TUI, pin findings that represent real business rules (press Space)
3. Press F2 to export to `goldencheck.yml`
4. Edit the YAML by hand if needed (e.g., tighten a `range`, add an `enum`)
5. `goldencheck validate data.csv` in CI — only the rules you care about are enforced

**Multiple environments:** Use `--config` to point at different rule files:

```bash
# Stricter rules for production
goldencheck validate data.csv --config configs/production.yml --fail-on warning

# Relaxed rules for development data
goldencheck validate data.csv --config configs/dev.yml
```
