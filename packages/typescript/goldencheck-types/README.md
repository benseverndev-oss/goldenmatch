# GoldenCheck Community Types

Community-contributed semantic type definitions for [GoldenCheck](https://github.com/benseverndev-oss/goldencheck).

Domain packs teach GoldenCheck about industry-specific column types, improving detection accuracy and reducing false positives. The same packs also drive InferMap's `detect_domain` (which industry a dataset belongs to) and, through it, GoldenPipe's auto-config — a dataset that scores a domain confidently gets a schema-inference stage prepended.

## Available Domains

15 industry packs (plus a `generic` fallback used when no domain matches):

| Domain | Types | Description |
|--------|-------|-------------|
| [automotive](domains/automotive.yaml) | 4 | Vehicles: VIN, license plate, registration, odometer, make/model |
| [ecommerce](domains/ecommerce.yaml) | 9 | SKUs, order IDs, tracking numbers, product categories, shipping |
| [education](domains/education.yaml) | 5 | Students, enrollment, courses, grades/GPA, terms, programs |
| [energy](domains/energy.yaml) | 4 | Meters, consumption (kWh), tariffs, utility accounts |
| [finance](domains/finance.yaml) | 8 | Account numbers, routing numbers, CUSIP/ISIN, currency, transactions |
| [healthcare](domains/healthcare.yaml) | 10 | NPI, ICD codes, insurance IDs, patient demographics, CPT, DRG |
| [hospitality](domains/hospitality.yaml) | 4 | Reservations, bookings, stays, rooms, guests |
| [hr](domains/hr.yaml) | 11 | Employee IDs, job titles, departments, compensation, hire dates, status, org hierarchy, performance |
| [insurance](domains/insurance.yaml) | 9 | Policies, claims, premiums, coverage, underwriting, beneficiaries |
| [legal](domains/legal.yaml) | 4 | Cases, dockets, matters, parties, courts, jurisdictions |
| [logistics](domains/logistics.yaml) | 5 | Shipments, tracking, carriers, freight, warehouses |
| [manufacturing](domains/manufacturing.yaml) | 4 | Parts, work orders, batches/lots, BOM, quality |
| [marketing](domains/marketing.yaml) | 5 | Leads, campaigns, pipeline, funnel metrics, attribution |
| [real_estate](domains/real_estate.yaml) | 4 | Listings, properties, features (beds/baths/sqft), sale prices |
| [telecom](domains/telecom.yaml) | 5 | Subscribers, device IDs (IMEI/IMSI/ICCID), usage, plans, network |

## Usage

### Bundled (built into GoldenCheck)

```bash
goldencheck scan data.csv --domain healthcare
```

### Community domains (download and use)

```bash
curl -o goldencheck_domain.yaml https://raw.githubusercontent.com/benseverndev-oss/goldencheck-types/main/domains/telecom.yaml
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
