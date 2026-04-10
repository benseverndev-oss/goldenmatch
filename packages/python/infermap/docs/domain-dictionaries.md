---
layout: default
title: Domain Dictionaries
nav_order: 8
---

# Domain Dictionaries

`infermap` ships curated alias dictionaries for common business domains. Loading one boosts matching accuracy on schemas that use standard terminology for that domain, with zero runtime cost.

## Shipped dictionaries

| Domain | Scope |
|---|---|
| `generic` | Common PII, customer, order, system fields (email, phone, address, name, id, created_at, ...). Loaded by default. |
| `healthcare` | HL7/FHIR/EHR conventions: MRN ↔ patient_id, ICD/CPT codes, observations, medications, providers. |
| `finance` | ISO 20022 / banking / trading: account_id ↔ IBAN, amt ↔ amount, ccy ↔ currency, ISIN/CUSIP, ledger entries. |
| `ecommerce` | Catalog/order/fulfillment: SKU ↔ product_id, qty ↔ quantity, shipping/billing address variants, discount codes. |

## Using them

Python:

```python
from infermap import MapEngine

# Opt into one or more domains. `generic` is always included.
engine = MapEngine(domains=["healthcare"])
result = engine.map(source_csv, target_csv)
```

From `infermap.yaml`:

```yaml
domains:
  - healthcare
  - finance
```

CLI:

```bash
infermap map --config infermap.yaml source.csv target.csv
```

## What you get

Consider a typical EHR mapping task: source schema from Epic has `MRN`, `DOB`, `admit_dt`, `dx_code`; target is a research data mart with `patient_id`, `date_of_birth`, `admission_date`, `diagnosis_code`. Without the healthcare dictionary, `infermap` has to rely on fuzzy name similarity and data profiling alone — often enough, but noisy when column names diverge heavily.

With `domains=["healthcare"]`:

- `MRN ↔ patient_id` is a direct alias match (score 0.95).
- `DOB ↔ date_of_birth` is a direct alias match.
- `admit_dt ↔ admission_date` matches via the `admit_dt` alias.
- `dx_code ↔ diagnosis_code` matches via the `dx_code` alias.

Confidence on these goes from ~0.25-0.40 (fuzzy alone) to 0.95 (alias match), and the Hungarian assignment becomes effectively deterministic.

## Extending a dictionary

Users can add their own aliases via `infermap.yaml` on top of the shipped dictionaries:

```yaml
domains:
  - healthcare

aliases:
  patient_id:
    - our_patient_uid
    - legacy_pt_id
  diagnosis_code:
    - primary_dx_code
```

The custom aliases are merged into the loaded domain dicts. Mutations go through the module-level `ALIASES` dict and are applied before scoring.

## Why these three and not more?

These were chosen because they have **well-established field name conventions** (HL7/FHIR, ISO 20022, Shopify/WooCommerce) and cover the largest chunks of the real-world schema-matching workload. Other domains we considered but didn't ship:

- **Public sector / government**: conventions vary wildly by jurisdiction; no universal standard.
- **Biotech research**: terminology shifts by lab and instrument; a generic dictionary would be either shallow or wrong.
- **Logistics / supply chain**: partially covered by `ecommerce`; dedicated version would duplicate significantly.

Contributions welcome — see [`infermap/dictionaries/`](https://github.com/benzsevern/infermap/tree/main/infermap/dictionaries) for the format.

## What domain dictionaries are NOT

Domain dictionaries are **curated synonym tables**, not learned models. They:

- Do **not** help when target column names are arbitrary (e.g. `assay_id → ss_d`, `relationship_type → RELATIT`). For those, see the `InitialismScorer` and the planned LLM scorer integration.
- Do **not** adapt to your specific schema vocabulary automatically. You get what's in the shipped YAML file, plus anything you extend via `infermap.yaml`.
- Do **not** replace the core scorer pipeline. They feed into the `AliasScorer` as one contributor among many; the weighted combination still decides the final mapping.

## Building a custom dictionary

If your organization has internal vocabulary (e.g. a legacy system's field names that your data warehouse maps to), the easiest path is:

1. Copy [`infermap/dictionaries/generic.yaml`](https://github.com/benzsevern/infermap/blob/main/infermap/dictionaries/generic.yaml) as a template.
2. Write your own file (e.g. `my_org.yaml`) with your canonical field names and aliases.
3. Load it alongside the shipped dictionaries in `infermap.yaml`:
   ```yaml
   domains: [generic, healthcare]
   aliases:  # your custom vocab merged on top
     <your canonicals and aliases here>
   ```

Or contribute it upstream via PR — domain dictionaries are opt-in, so adding one doesn't affect other users.
