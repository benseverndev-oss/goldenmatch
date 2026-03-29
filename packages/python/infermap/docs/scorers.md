---
layout: default
title: Scorers
nav_order: 4
---

# Scorers

Scorers are the core of infermap's mapping pipeline. Each scorer independently evaluates how likely a source field maps to a target field, returning a confidence score (0.0--1.0) and human-readable reasoning.

## Built-in Scorers

### ExactScorer (weight: 1.0)

Case-insensitive exact name match. Returns 1.0 if the names match after lowercasing and stripping whitespace, 0.0 otherwise.

### AliasScorer (weight: 0.95)

Checks source name against a built-in synonym registry and any aliases declared in schema definition files.

Built-in aliases include:

| Canonical | Aliases |
|-----------|---------|
| first_name | fname, first, given_name, first_nm, forename |
| last_name | lname, last, surname, family_name, last_nm |
| email | email_address, e_mail, email_addr, mail, contact_email |
| phone | phone_number, ph, telephone, tel, mobile, cell |
| zip | zipcode, zip_code, postal_code, postal, postcode |
| address | addr, street_address, addr_line_1, address_line_1, mailing_address |
| dob | date_of_birth, birth_date, birthdate, birthday |
| ... | 15 canonical groups total |

Returns `None` (abstains) if neither field has known aliases.

### PatternTypeScorer (weight: 0.7)

Samples field values and classifies them using regex patterns:

| Type | Pattern |
|------|---------|
| email | Standard email format |
| phone | 7-15 digit phone numbers with common separators |
| zip_us | 5-digit or 5+4 US ZIP codes |
| date_iso | ISO 8601 dates (YYYY-MM-DD) |
| uuid | Standard UUID format |
| url | http:// or https:// URLs |
| currency | Dollar/Euro/Pound/Yen + number |
| ip_v4 | IPv4 addresses |

A field is classified when 60%+ of sampled values match a pattern. If both source and target classify to the same type, score = min(source_match_pct, target_match_pct). Returns `None` if no samples are available; returns 0.0 if samples exist but don't match any pattern.

### ProfileScorer (weight: 0.5)

Compares statistical profiles:

| Component | Weight | What it measures |
|-----------|--------|-----------------|
| dtype match | 0.4 | Same normalized data type? |
| null rate | 0.2 | Similar proportion of nulls? |
| uniqueness | 0.2 | Similar cardinality ratio? |
| value length | 0.1 | Similar average string length? |
| cardinality | 0.1 | Similar total value counts? |

Returns `None` if either field has no values.

### FuzzyNameScorer (weight: 0.4)

Jaro-Winkler similarity on normalized column names (lowercased, underscores/hyphens/spaces removed). Low weight prevents false positives like "description" matching "destination".

## Score Combination

For each (source, target) pair:

```
score = sum(scorer.weight * result.score) / sum(scorer.weight)
```

- Scorers returning `None` are excluded from both numerator and denominator
- Scorers returning 0.0 are included (a real "no match" signal)
- Pairs with fewer than 2 non-None scorers get score 0.0

## Custom Scorers

Register a custom scorer with the `@infermap.scorer` decorator:

```python
import infermap
from infermap.types import FieldInfo, ScorerResult

@infermap.scorer(name="fhir_type", weight=0.8)
def fhir_scorer(source: FieldInfo, target: FieldInfo) -> ScorerResult | None:
    # Your domain logic here
    return ScorerResult(score=0.9, reasoning="FHIR Patient.name match")
```

Custom scorers are automatically included in the pipeline.

## Configuration

Override weights or disable scorers via `infermap.yaml`:

```yaml
scorers:
  FuzzyNameScorer:
    weight: 0.2
  LLMScorer:
    enabled: false

aliases:
  mrn: [medical_record_number, patient_id, chart_number]
```
