# GoldenCheck Community Types

Community-contributed semantic type definitions for [GoldenCheck](https://github.com/benzsevern/goldencheck).

Domain packs teach GoldenCheck about industry-specific column types, improving detection accuracy and reducing false positives.

## Available Domains

| Domain | Types | Description |
|--------|-------|-------------|
| [healthcare](domains/healthcare.yaml) | 10 | NPI, ICD codes, insurance IDs, patient demographics, CPT, DRG |
| [finance](domains/finance.yaml) | 8 | Account numbers, routing numbers, CUSIP/ISIN, currency, transactions |
| [ecommerce](domains/ecommerce.yaml) | 9 | SKUs, order IDs, tracking numbers, categories, shipping |

## Usage

### Bundled (built into GoldenCheck)

```bash
goldencheck scan data.csv --domain healthcare
```

### Community domains (download and use)

```bash
curl -o goldencheck_domain.yaml https://raw.githubusercontent.com/benzsevern/goldencheck-types/main/domains/telecom.yaml
goldencheck scan data.csv
```

### Via MCP (Claude Desktop)

Use the `install_domain` tool to browse and install community domains.

## Contributing

1. Fork this repo
2. Add a YAML file in `domains/` following the format below
3. Open a PR — CI validates your YAML automatically

### YAML Format

```yaml
description: "Short description of the domain"

types:
  my_type:
    name_hints: ["column_name_hint", "another_hint"]
    value_signals:
      min_unique_pct: 0.90    # optional: minimum uniqueness
      max_unique: 20          # optional: maximum unique values
      format_match: "email"   # optional: regex format
      mixed_case: true        # optional: mixed case values
      avg_length_min: 15      # optional: minimum average string length
      short_strings: true     # optional: short string values
      numeric: true           # optional: numeric values
    suppress: ["pattern_consistency", "cardinality"]  # checks to suppress
```

### Name Hints

- Plain string: substring match (`"npi"` matches `npi_number`, `provider_npi`)
- Ending with `_`: prefix-only match (`"is_"` matches `is_active` but NOT `diagnosis`)
- Starting with `_`: suffix-only match (`"_id"` matches `patient_id`)

### Valid Suppress Values

`uniqueness`, `nullability`, `format_detection`, `type_inference`, `range_distribution`, `cardinality`, `pattern_consistency`, `temporal_order`, `encoding_detection`, `sequence_detection`, `drift_detection`

## License

MIT
