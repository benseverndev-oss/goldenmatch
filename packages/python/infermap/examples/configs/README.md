# Example YAML Configs

Pre-built infermap configurations for common industry verticals. Each config extends the built-in alias registry with domain-specific field name synonyms and tunes scorer weights for the domain.

## Usage

```python
engine = infermap.MapEngine(config_path="configs/healthcare.yaml")
result = engine.map("patient_records.csv", "target_schema.csv")
```

Or via CLI:

```bash
# Copy a config to your project root as infermap.yaml
cp configs/healthcare.yaml infermap.yaml
infermap map patient_records.csv target_schema.csv
```

## Available Configs

| Config | Domain | Key aliases |
|--------|--------|-------------|
| `crm-to-erp.yaml` | CRM / ERP integration | customer_id, email, phone, organization |
| `healthcare.yaml` | Healthcare / EHR / claims | mrn, npi, icd_code, cpt_code, blood_type |
| `ecommerce.yaml` | E-commerce / orders | order_id, sku, tracking_number, discount |
| `hr-payroll.yaml` | HR / payroll / HRIS | employee_id, hire_date, salary, department |
| `financial.yaml` | Banking / fintech | account_id, routing_number, transaction_id |

## Customizing

Copy any config and modify it:

```yaml
# my-config.yaml
scorers:
  FuzzyNameScorer:
    weight: 0.1       # our field names never fuzzy-match

aliases:
  mrn: [chart_number, patient_id, medical_record_no]
  # add your domain-specific aliases here
```
