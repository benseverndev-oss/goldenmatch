# goldencheck-types "messier" vertical packs (logistics, energy, automotive, legal, hospitality, manufacturing, marketing) — design

**Status:** approved (design gate)
**Date:** 2026-07-07
**Builds on:** the clean-4 batch (#1560 → 9 packs). Adds the 7 deferred higher-interference verticals, taking the total to 16.

## 1. Goal

Add 7 discriminative vertical domain packs — `logistics`, `energy`, `automotive`, `legal`, `hospitality`, `manufacturing`, `marketing` — so InferMap `detect_domain` recognizes them (≥0.5 → the goldenpipe brain's `confident_schema` path). These were deferred as "messier" (higher collision risk); empirical cross-verification (below) shows all 7 are viable clean.

## 2. Empirical verification (the whole point — done FIRST)

All 7 candidate hint sets were cross-verified together against the 9 shipped packs + representative + adversarial datasets via `_detect_core_pure` (the real kernel). Results:

**Pure-vertical data** (each detects its own domain, ≥0.5):
| Vertical | score | Vertical | score |
|---|---|---|---|
| logistics | 1.0 | hospitality | 1.0 |
| energy | 0.9 | manufacturing | 1.0 |
| automotive | 0.9 | marketing | 0.8 |
| legal | 0.9 | | |

**No false positives / no steals:**
- Generic person (`first_name,last_name,email,phone,city,state,address`) → **None**; plain sales contact (`contact_name,email,phone,company,title,notes`) → **None**.
- Existing 9 unchanged: finance→finance 0.6, ecommerce→ecommerce 0.71, hr→hr 0.67, etc.

**Two inherent overlaps — both resolve CORRECTLY (documented, not bugs):**
1. **logistics ↔ ecommerce** (order fulfillment genuinely shares `tracking/carrier/shipping`): pure-logistics `[tracking_number,shipment_id,carrier,waybill,container_number,consignee,freight,warehouse_id,delivery_status,dispatch_date]` → **logistics 1.0**; an order-flavored set `[order_id,tracking_number,carrier,shipping_address,delivery_status,warehouse_id]` → **ecommerce 0.83** (logistics 0.67). Order-centric data resolving to ecommerce is the RIGHT call; neither steals the other's pure data (`ecom_with_tracking` → ecommerce 0.71, logistics 0.29).
2. **marketing on light-CRM**: a contact list with 1-2 campaign fields (`contact_name,email,phone,company,lead_source,campaign_name`) → marketing **0.33** — below the brain's 0.5 `confident_schema` threshold, so the brain does NOT act. Pure marketing (`lead/opportunity/mql/utm/funnel`) → 0.8; plain sales contact → None. Marketing only meaningfully fires on genuine marketing data.

**Adversarial robustness confirmed:** `energy_with_account` (`account_number,meter_reading,kwh_consumed,tariff,billing_period`) → **energy 0.8** (finance 0.2) — energy wins even sharing `account_number`, because it uses `utility_account`/`meter`/`kwh`/`tariff` discriminators. `auto_minimal` (`make,model,year,color,mileage,price`) → automotive 0.5 (make/model/mileage kept for recall on VIN-less used-car lists).

The discriminative choices that keep it clean: `utility_account` not bare `account_number` (energy); no bare `sku` (logistics); `lead/campaign/utm/mql/funnel` not `email/name/contact` (marketing); `guest_name` compound (hospitality). Verified, not assumed.

## 3. The packs (verified `name_hints`, organized into types)

Each `hr.yaml`-shaped (`description` + `types` with `name_hints` + minimal `value_signals`/`suppress` from the vocab: `min_unique_pct`/`max_unique`/`numeric`/`short_strings`/`mixed_case`; suppress: `cardinality`/`drift_detection`/`pattern_consistency`/`range_distribution`/`type_inference`/`uniqueness`). Detect uses only `name_hints`.

### logistics
- **tracking**: `tracking_number, tracking_id, shipment_id, shipment_number` — high-uniqueness.
- **document**: `waybill, awb, bill_of_lading`
- **container**: `container_number, consignment, consignee`
- **freight**: `carrier, freight, warehouse_id`
- **status**: `delivery_status, dispatch_date`

### energy
- **meter**: `meter_id, meter_number, meter_reading` — high-uniqueness.
- **consumption**: `kwh, kwh_consumed, consumption_kwh, usage_kwh` — numeric.
- **tariff**: `tariff, tariff_rate`
- **account**: `utility_account, billing_period, peak_demand, service_point`

### automotive
- **identifier**: `vin, license_plate, registration_number` — high-uniqueness.
- **odometer**: `odometer, mileage` — numeric.
- **engine**: `engine_number, chassis_number`
- **vehicle**: `make, model, trim, fuel_type`

### legal
- **case**: `case_number, case_id, docket, docket_number` — high-uniqueness.
- **matter**: `matter_id, matter_number`
- **parties**: `plaintiff, defendant, attorney, counsel`
- **court**: `jurisdiction, court, filing_date, cause_of_action`

### hospitality
- **reservation**: `reservation_id, reservation_number, booking_id, booking_reference` — high-uniqueness.
- **stay**: `check_in, check_out`
- **room**: `room_type, room_number, guest_name`
- **billing**: `nights, occupancy, rate_per_night, confirmation_number`

### manufacturing
- **part**: `part_number, part_id`
- **production**: `work_order, batch_number, lot_number, serial_number`
- **bom**: `bom, bill_of_materials, assembly_id`
- **quality**: `defect_rate, yield_rate, production_date, machine_id, shift`

### marketing
- **lead**: `lead_id, lead_source`
- **campaign**: `campaign_id, campaign_name`
- **pipeline**: `opportunity_id, mql, sql_lead`
- **metrics**: `conversion_rate, funnel_stage, click_through_rate, impressions`
- **attribution**: `utm_source, utm_campaign, cost_per_lead`

**Every type MUST carry a `suppress` key** (from the allowlist above) — the manual `tests/validate_yaml.py` requires it per-type and allowlists the values (even though it is not CI-wired; `hr.yaml` follows it). Do NOT ship a `name_hints`-only type. The §3 shorthand lists only `name_hints`; the actual YAMLs include `value_signals` + `suppress` per type, exactly like `hr.yaml`/`insurance.yaml`.

At implementation, the FINAL YAMLs are validated by the real load path (write all 7, run the full 16-pack matrix from §2 → each vertical ≥0.5, person/sales-contact→None, existing 9 unchanged, plus the two overlap assertions); adjust hints if a type-organized token set shifts an outcome. (The runtime loader shape-validates only list/dict/list with no key allowlist; the manual validator additionally requires `suppress` — see above.)

## 4. Sync (TS canonical → Python)

Add the 7 YAMLs to `packages/typescript/goldencheck-types/domains/`, run `scripts/sync_domain_packs.py`, confirm `--check` exits 0 and all 7 pairs are byte-identical (git blob SHA). **Do NOT hand-edit the Python copies.** The 3rd copy (`goldencheck/semantic/domains/`) is left as-is (not read by detect).

## 5. Tests

- **Python detect test** (box-runnable) — `packages/python/infermap/tests/test_messy_vertical_domains.py`. Parametrized over the 7: each in `list_domains()`; `detect_domain_detailed(<vertical df>)` → `domain == <vertical>` with `score >= 0.5`; generic person → not any of the 7. Cross-steal guard: the existing verticals (finance, ecommerce, hr) still detect correctly with the 7 new packs present. Plus the two overlap assertions: pure-logistics → logistics; order-flavored → ecommerce (logistics does NOT steal it); pure-marketing → marketing; sales-contact → None (marketing does NOT false-positive). Note in a comment that single-winner detect means self-detection already proves no sibling-steal.
- **Regression** (in the plan): no exact domain-set/count assertion anywhere (membership/synthetic tests). Adding 7 domains doesn't perturb them.
- **Parity by construction** — shared data + byte-parity kernel.

## 6. Non-goals

- No changes to detect scoring, the brain, or `parseCsv`.
- No engineering-away of the two inherent overlaps (they resolve correctly — see §2).
- No bare `account_number`/`sku`/`email`/`name`/`contact`/`price`/`status` collision tokens.
- Marketing's sub-0.5 signal on light-CRM data is intended (the brain won't act on it); not a defect.

## 7. File touch list

- `packages/typescript/goldencheck-types/domains/{logistics,energy,automotive,legal,hospitality,manufacturing,marketing}.yaml` — **new** (canonical).
- `packages/python/goldencheck-types/goldencheck_types/_domains/{...}.yaml` (×7) — **new** (synced copies — do not hand-edit).
- `packages/python/infermap/tests/test_messy_vertical_domains.py` — **new**.
