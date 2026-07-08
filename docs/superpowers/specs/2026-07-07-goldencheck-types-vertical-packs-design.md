# goldencheck-types vertical domain packs (insurance, telecom, real_estate, education) — design

**Status:** approved (design gate)
**Date:** 2026-07-07
**Builds on:** the `hr` domain pack (#1558). Same mechanism, extended to four more verticals.

## 1. Goal

Add four discriminative domain packs — `insurance`, `telecom`, `real_estate`, `education` — so InferMap `detect_domain` recognizes those verticals (score ≥ 0.5 → the goldenpipe brain's `confident_schema` rule → `infer_schema` prepended). Cross-surface by construction (shared packs read by the byte-parity-gated detect kernel).

## 2. Design constraint: discriminative + cross-verified

Detect scores `hits / n_columns`; a hint matches iff its tokens are a contiguous token-run in a column. With N packs, each new pack must not only detect its own data ≥0.5 and avoid person/generic false positives, but also **not steal from or get stolen by the other 8 packs** (5 existing + 3 sibling new). The whole 9-pack set was cross-verified together on the box (`_detect_core_pure` with the real shipped packs + the 4 candidates) against representative datasets:

| Dataset | Detects | Notes |
|---|---|---|
| Insurance | `insurance` **0.9** | healthcare 0.2 (shared `claim_status`), non-deciding |
| Telecom | `telecom` **0.9** | ~0 interference (msisdn/imei/imsi globally unique) |
| Real estate | `real_estate` **0.9** | ~0 |
| Education | `education` **0.9** | ~0 |
| Generic person (`first_name,last_name,email,phone,city,state,address`) | **None** | no false positive |
| Customer (`customer_id,name,email,phone,address,status`) | **None** (hr 0.17) | no false positive |
| Employee (12-col) | `hr` **0.58** | new packs don't steal HR |
| Finance | `finance` **0.6** | unchanged |
| Ecommerce | `ecommerce` **0.71** | unchanged |
| Healthcare (incl `claim_status`) | `healthcare` **0.67** | insurance 0.17, non-deciding |

Discriminative choices that keep this clean: **no bare `address`/`city`/`state`/`zip`** in real_estate (person collision — use `property_address` compound); **no bare `price`** (ecommerce — use `asking_price`/`list_price`); **no bare `grade`** in education (`grade_level` compound); **no bare `status`** except where already owned. The verified hint sets below are the exact tokens tested.

## 3. The packs (verified `name_hints`, organized into types)

Each `hr.yaml`-shaped: `description` + `types` (each with `name_hints` + minimal `value_signals` + `suppress` from the existing vocab: `min_unique_pct`/`max_unique`/`numeric`/`short_strings`/`mixed_case`; suppress: `cardinality`/`drift_detection`/`pattern_consistency`/`range_distribution`/`type_inference`/`uniqueness`). Detect uses only `name_hints`.

### insurance
- **policy**: `policy_number, policy_id, policy_no` — high-uniqueness.
- **claim**: `claim_number, claim_id, claim_no, claim_status`
- **premium**: `premium, premium_amount, annual_premium` — numeric.
- **deductible**: `deductible` — numeric.
- **coverage**: `coverage, coverage_amount, coverage_type, sum_insured`
- **policyholder**: `policyholder, insured`
- **underwriter**: `underwriter`
- **beneficiary**: `beneficiary`
- **insurance_type**: `insurance_type, policy_type` — low-cardinality.

### telecom
- **subscriber**: `subscriber_id, subscriber_number, msisdn` — high-uniqueness.
- **device**: `imei, imsi, iccid, sim_id` — high-uniqueness.
- **usage**: `data_usage, call_duration, sms_count, minutes_used` — numeric.
- **plan**: `plan_id, plan_name, rate_plan`
- **network**: `network_type` — low-cardinality.

### real_estate
- **listing**: `listing_id, listing_price, mls, mls_number`
- **property**: `property_id, property_type, property_address`
- **features**: `bedrooms, bathrooms, square_feet, sqft, lot_size, year_built, garage` — numeric/low-card.
- **price**: `asking_price, list_price, sale_price, sold_price` — numeric.

### education
- **student**: `student_id, student_number, enrollment_id, enrollment` — high-uniqueness.
- **course**: `course_id, course_code, course_name`
- **grades**: `gpa, grade_level, grade_point, credits, credit_hours` — numeric/low-card.
- **term**: `semester, term, academic_year`
- **program**: `major, transcript, degree`

At implementation, the FINAL YAMLs are validated by the real load path (write all 4, run the full cross-verification matrix from §2 → assert each vertical detects itself ≥0.5, person→not-vertical, and the existing 5 detect unchanged); adjust hints if the type-organized token set shifts an outcome. (Loader shape-validates only that `name_hints` is a list / `value_signals` a dict / `suppress` a list — no key allowlist — so any reasonable signals load.)

## 4. Sync (TS canonical → Python)

Add the 4 YAMLs to `packages/typescript/goldencheck-types/domains/`, run `scripts/sync_domain_packs.py` to mirror them to `packages/python/goldencheck-types/goldencheck_types/_domains/`, confirm `--check` exits 0. **Do NOT hand-edit the Python copies** (sync-generated, byte-identical, git blob SHA-verified).

The 3rd copy (`goldencheck/semantic/domains/`) is left as-is (not read by InferMap detect) — same call as `hr`.

## 5. Tests

- **Python detect test** (box-runnable) — `packages/python/infermap/tests/test_vertical_domains.py`. Parametrized over the 4 verticals: assert each is in `goldencheck_types.list_domains()`; assert `detect_domain_detailed(<vertical df>)` → `domain == <vertical>` with `score >= 0.5`; assert a generic person df does NOT detect any of the 4. Plus a **cross-steal guard**: assert the existing verticals still detect correctly (employee→hr, finance→finance, ecommerce→ecommerce, healthcare→healthcare) with the 4 new packs present.
- **Regression** (in the plan): confirm no test asserts the exact shipped-domain set/count (grep — `test_dictionaries.py` is membership-only; `test_detect_dispatch.py`/`test_native_parity.py` pass synthetic packs). Adding 4 domains doesn't perturb them.
- **Parity by construction** — data read by the shared kernel; native/WASM byte-parity automatic. No new symbol.

## 6. Dogfood (optional validation)

If a representative CSV for any vertical is handy, run it through the goldenpipe brain to confirm `confident_schema` + the right `inferred_domain` + `infer_schema` prepended. Not a gate.

## 7. Non-goals

- No changes to detect scoring, the brain, or `parseCsv`.
- No `hr`/vertical entries in the 3rd goldencheck semantic copy.
- No bare person/generic tokens (`address`, `city`, `name`, `price`, `grade`, `status`) — the whole point.
- The other candidate verticals (logistics, energy, automotive, legal, hospitality, manufacturing, marketing) are deferred — higher interference (e.g. logistics `sku`↔ecommerce, energy `account_number`↔finance) or lower value; revisit as a later batch if wanted.

## 8. File touch list

- `packages/typescript/goldencheck-types/domains/insurance.yaml` — **new** (canonical).
- `packages/typescript/goldencheck-types/domains/telecom.yaml` — **new**.
- `packages/typescript/goldencheck-types/domains/real_estate.yaml` — **new**.
- `packages/typescript/goldencheck-types/domains/education.yaml` — **new**.
- `packages/python/goldencheck-types/goldencheck_types/_domains/{insurance,telecom,real_estate,education}.yaml` — **new** (synced copies — do not hand-edit).
- `packages/python/infermap/tests/test_vertical_domains.py` — **new** (Python detect + cross-steal test).
