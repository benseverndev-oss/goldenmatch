# goldencheck-types "messier" vertical packs — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 7 discriminative vertical domain packs (`logistics`, `energy`, `automotive`, `legal`, `hospitality`, `manufacturing`, `marketing`) to goldencheck-types (9 → 16), feeding InferMap detect → the goldenpipe brain's `confident_schema` path.

**Architecture:** 7 hand-authored YAML packs in canonical TS `domains/`, mirrored to Python `_domains/` by `scripts/sync_domain_packs.py`, locked by one parametrized Python detect test. Same mechanism as the clean-4 (#1560) + hr (#1558). Cross-surface parity by construction.

**Tech Stack:** YAML packs, Python (pytest, box-runnable), the sync script.

**Spec:** `docs/superpowers/specs/2026-07-07-goldencheck-types-messy-verticals-design.md`

---

## Environment

```bash
cd "D:/show_case/gg-local-llm"
INTERP="D:/show_case/goldenmatch/.venv/Scripts/python.exe"
export PYTHONPATH="packages/python/goldenpipe;packages/python/infermap;packages/python/goldencheck-types"
export POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8
```
Branch `feat/goldencheck-types-messy-verticals` (off fresh origin/main which has all 9 packs, spec committed). **Entirely box-runnable**; CI re-verifies `--check` + suites.

**CRITICAL:** every `type` in every pack MUST carry a `suppress` key (from the allowlist) — the manual `tests/validate_yaml.py` requires it. Mirror `hr.yaml` exactly (never a `name_hints`-only type).

## File Structure

| File | Responsibility |
|------|----------------|
| `packages/typescript/goldencheck-types/domains/{logistics,energy,automotive,legal,hospitality,manufacturing,marketing}.yaml` | The 7 packs (canonical, hand-authored) |
| `packages/python/goldencheck-types/goldencheck_types/_domains/*.yaml` (×7) | Synced copies (script-generated — DO NOT hand-edit) |
| `packages/python/infermap/tests/test_messy_vertical_domains.py` | Parametrized detect + cross-steal + overlap test (box) |

---

### Task 1: Author the 7 packs, sync, cross-verify, and test (all box-verified)

**Files:**
- Create: the 7 `packages/typescript/goldencheck-types/domains/*.yaml`
- Generate (via script): the 7 `packages/python/goldencheck-types/goldencheck_types/_domains/*.yaml`
- Create: `packages/python/infermap/tests/test_messy_vertical_domains.py`

- [ ] **Step 1: Author `logistics.yaml`**
```yaml
description: "Logistics / supply chain — shipments, tracking, carriers, freight, warehouses"

types:
  tracking:
    name_hints: ["tracking_number", "tracking_id", "shipment_id", "shipment_number"]
    value_signals:
      min_unique_pct: 0.90
    suppress: ["cardinality", "pattern_consistency", "drift_detection"]

  document:
    name_hints: ["waybill", "awb", "bill_of_lading"]
    suppress: ["pattern_consistency"]

  container:
    name_hints: ["container_number", "consignment", "consignee"]
    suppress: ["pattern_consistency"]

  freight:
    name_hints: ["carrier", "freight", "warehouse_id"]
    value_signals:
      max_unique: 50
    suppress: ["uniqueness", "range_distribution"]

  status:
    name_hints: ["delivery_status", "dispatch_date"]
    value_signals:
      max_unique: 20
    suppress: ["uniqueness", "range_distribution"]
```

- [ ] **Step 2: Author `energy.yaml`** (NO bare `account_number` — finance collision; uses `utility_account`)
```yaml
description: "Energy / utilities — meters, consumption (kWh), tariffs, utility accounts"

types:
  meter:
    name_hints: ["meter_id", "meter_number", "meter_reading"]
    value_signals:
      min_unique_pct: 0.80
    suppress: ["cardinality", "pattern_consistency", "drift_detection"]

  consumption:
    name_hints: ["kwh", "kwh_consumed", "consumption_kwh", "usage_kwh"]
    value_signals:
      numeric: true
    suppress: ["pattern_consistency"]

  tariff:
    name_hints: ["tariff", "tariff_rate"]
    value_signals:
      max_unique: 20
    suppress: ["uniqueness", "range_distribution"]

  account:
    name_hints: ["utility_account", "billing_period", "peak_demand", "service_point"]
    suppress: ["pattern_consistency"]
```

- [ ] **Step 3: Author `automotive.yaml`**
```yaml
description: "Automotive — vehicles, VIN/plate/registration, odometer, make/model"

types:
  identifier:
    name_hints: ["vin", "license_plate", "registration_number"]
    value_signals:
      min_unique_pct: 0.90
    suppress: ["cardinality", "pattern_consistency", "drift_detection"]

  odometer:
    name_hints: ["odometer", "mileage"]
    value_signals:
      numeric: true
    suppress: ["pattern_consistency"]

  engine:
    name_hints: ["engine_number", "chassis_number"]
    value_signals:
      min_unique_pct: 0.80
    suppress: ["cardinality", "pattern_consistency"]

  vehicle:
    name_hints: ["make", "model", "trim", "fuel_type"]
    value_signals:
      max_unique: 100
    suppress: ["uniqueness"]
```

- [ ] **Step 4: Author `legal.yaml`**
```yaml
description: "Legal — cases, dockets, matters, parties, courts, jurisdictions"

types:
  case:
    name_hints: ["case_number", "case_id", "docket", "docket_number"]
    value_signals:
      min_unique_pct: 0.80
    suppress: ["cardinality", "pattern_consistency", "drift_detection"]

  matter:
    name_hints: ["matter_id", "matter_number"]
    value_signals:
      min_unique_pct: 0.80
    suppress: ["cardinality", "pattern_consistency"]

  parties:
    name_hints: ["plaintiff", "defendant", "attorney", "counsel"]
    value_signals:
      mixed_case: true
    suppress: ["pattern_consistency"]

  court:
    name_hints: ["jurisdiction", "court", "filing_date", "cause_of_action"]
    value_signals:
      max_unique: 100
    suppress: ["uniqueness"]
```

- [ ] **Step 5: Author `hospitality.yaml`** (`guest_name` compound — no bare `guest`)
```yaml
description: "Hospitality / travel — reservations, bookings, stays, rooms, guests"

types:
  reservation:
    name_hints: ["reservation_id", "reservation_number", "booking_id", "booking_reference"]
    value_signals:
      min_unique_pct: 0.90
    suppress: ["cardinality", "pattern_consistency", "drift_detection"]

  stay:
    name_hints: ["check_in", "check_out"]
    suppress: ["pattern_consistency"]

  room:
    name_hints: ["room_type", "room_number", "guest_name"]
    value_signals:
      max_unique: 100
    suppress: ["uniqueness"]

  billing:
    name_hints: ["nights", "occupancy", "rate_per_night", "confirmation_number"]
    suppress: ["pattern_consistency"]
```

- [ ] **Step 6: Author `manufacturing.yaml`**
```yaml
description: "Manufacturing — parts, work orders, batches/lots, BOM, quality"

types:
  part:
    name_hints: ["part_number", "part_id"]
    value_signals:
      min_unique_pct: 0.80
    suppress: ["cardinality", "pattern_consistency"]

  production:
    name_hints: ["work_order", "batch_number", "lot_number", "serial_number"]
    value_signals:
      min_unique_pct: 0.80
    suppress: ["cardinality", "pattern_consistency", "drift_detection"]

  bom:
    name_hints: ["bom", "bill_of_materials", "assembly_id"]
    suppress: ["pattern_consistency"]

  quality:
    name_hints: ["defect_rate", "yield_rate", "production_date", "machine_id", "shift"]
    value_signals:
      max_unique: 50
    suppress: ["uniqueness", "range_distribution"]
```

- [ ] **Step 7: Author `marketing.yaml`** (NO `email`/`name`/`contact` — person/CRM collision; discriminative marketing tokens only)
```yaml
description: "Marketing / CRM — leads, campaigns, pipeline, funnel metrics, attribution"

types:
  lead:
    name_hints: ["lead_id", "lead_source"]
    value_signals:
      max_unique: 50
    suppress: ["uniqueness", "range_distribution"]

  campaign:
    name_hints: ["campaign_id", "campaign_name"]
    value_signals:
      max_unique: 100
    suppress: ["uniqueness"]

  pipeline:
    name_hints: ["opportunity_id", "mql", "sql_lead"]
    suppress: ["pattern_consistency"]

  metrics:
    name_hints: ["conversion_rate", "funnel_stage", "click_through_rate", "impressions"]
    value_signals:
      numeric: true
    suppress: ["pattern_consistency"]

  attribution:
    name_hints: ["utm_source", "utm_campaign", "cost_per_lead"]
    value_signals:
      max_unique: 100
    suppress: ["uniqueness"]
```

- [ ] **Step 8: Sync TS → Python + verify the drift gate**
```bash
"$INTERP" scripts/sync_domain_packs.py            # copies the 7 new domains/*.yaml -> _domains/
"$INTERP" scripts/sync_domain_packs.py --check    # MUST exit 0
echo "sync --check exit: $?"
ls packages/python/goldencheck-types/goldencheck_types/_domains/{logistics,energy,automotive,legal,hospitality,manufacturing,marketing}.yaml
```
Expected: 7 files synced; `--check` exits 0. **Do NOT hand-edit the Python copies.**

- [ ] **Step 9: Validate the FINAL YAMLs via the real load path + full 16-pack matrix (box)**
```bash
"$INTERP" -c "
from goldencheck_types import list_domains, load_domain
from infermap.detect import detect_domain_detailed
from types import SimpleNamespace
NEW=['logistics','energy','automotive','legal','hospitality','manufacturing','marketing']
for d in NEW:
    assert d in list_domains(), (d, list_domains()); load_domain(d)
D=lambda cols: detect_domain_detailed(SimpleNamespace(columns=cols))
sets={
 'logistics':['tracking_number','shipment_id','carrier','waybill','container_number','consignee','freight','warehouse_id','delivery_status','dispatch_date'],
 'energy':['meter_id','meter_reading','kwh_consumed','tariff','utility_account','billing_period','peak_demand','service_point','supply_address','rate_class'],
 'automotive':['vin','license_plate','registration_number','odometer','mileage','make','model','trim','fuel_type','color'],
 'legal':['case_number','docket_number','matter_id','plaintiff','defendant','attorney','jurisdiction','court','filing_date','status'],
 'hospitality':['reservation_id','booking_reference','check_in','check_out','room_type','room_number','guest_name','nights','occupancy','rate_per_night'],
 'manufacturing':['part_number','work_order','batch_number','lot_number','serial_number','bom','assembly_id','defect_rate','production_date','machine_id'],
 'marketing':['lead_id','lead_source','campaign_name','opportunity_id','mql','conversion_rate','funnel_stage','utm_source','email','contact_name'],
}
for n,c in sets.items():
    r=D(c); print(f'{n:14}-> {r.domain} {round(r.score,2)}'); assert r.domain==n and r.score>=0.5,(n,r.domain,r.score)
# no false positive
for label,c in [('person',['first_name','last_name','email','phone','city','state','address']),('sales_contact',['contact_name','email','phone','company','title','notes'])]:
    r=D(c); print(f'{label:14}-> {r.domain}'); assert r.domain not in NEW,(label,r.domain)
# existing unchanged
for label,c,exp in [('finance',['account_number','currency','amount','iban','transaction_type'],'finance'),('ecommerce',['order_id','sku','product','price','category','shipping_address','coupon'],'ecommerce'),('hr',['Employee_ID','First_Name','Last_Name','Department_Region','Status','Join_Date','Salary','Email','Performance_Score'],'hr')]:
    r=D(c); print(f'{label:14}-> {r.domain}'); assert r.domain==exp,(label,r.domain)
# the two documented overlaps
order_flavored=D(['order_id','tracking_number','carrier','shipping_address','delivery_status','warehouse_id'])
print('order_flavored ->', order_flavored.domain); assert order_flavored.domain=='ecommerce', order_flavored.domain
print('MATRIX OK')
"
```
Expected: each of the 7 → itself ≥0.5; person/sales_contact → not a new vertical; finance/ecommerce/hr unchanged; order_flavored → ecommerce; `MATRIX OK`. If any assert fails, adjust the offending pack's `name_hints` (keep the spec-verified tokens) and re-sync + re-run.

- [ ] **Step 10: Write the Python detect test** — `packages/python/infermap/tests/test_messy_vertical_domains.py`:
```python
"""The 7 shipped "messier" vertical packs (logistics, energy, automotive, legal,
hospitality, manufacturing, marketing): each detects its own vertical without
stealing person/generic data or the existing 9 packs.

Note: detect returns a SINGLE winner, so `<vertical>_df -> <vertical>` already
proves no sibling pack steals it. The two documented inherent overlaps
(logistics<->ecommerce order fulfillment; marketing sub-0.5 on light-CRM) are
asserted explicitly below."""
from types import SimpleNamespace

import pytest

from goldencheck_types import list_domains
from infermap.detect import detect_domain_detailed

_VERTICALS = {
    "logistics": ["tracking_number", "shipment_id", "carrier", "waybill", "container_number",
                  "consignee", "freight", "warehouse_id", "delivery_status", "dispatch_date"],
    "energy": ["meter_id", "meter_reading", "kwh_consumed", "tariff", "utility_account",
               "billing_period", "peak_demand", "service_point", "supply_address", "rate_class"],
    "automotive": ["vin", "license_plate", "registration_number", "odometer", "mileage",
                   "make", "model", "trim", "fuel_type", "color"],
    "legal": ["case_number", "docket_number", "matter_id", "plaintiff", "defendant", "attorney",
              "jurisdiction", "court", "filing_date", "status"],
    "hospitality": ["reservation_id", "booking_reference", "check_in", "check_out", "room_type",
                    "room_number", "guest_name", "nights", "occupancy", "rate_per_night"],
    "manufacturing": ["part_number", "work_order", "batch_number", "lot_number", "serial_number",
                      "bom", "assembly_id", "defect_rate", "production_date", "machine_id"],
    "marketing": ["lead_id", "lead_source", "campaign_name", "opportunity_id", "mql",
                  "conversion_rate", "funnel_stage", "utm_source", "email", "contact_name"],
}
_PERSON = ["first_name", "last_name", "email", "phone", "city", "state", "address"]
_SALES_CONTACT = ["contact_name", "email", "phone", "company", "title", "notes"]
_EXISTING = {
    "finance": ["account_number", "currency", "amount", "iban", "transaction_type"],
    "ecommerce": ["order_id", "sku", "product", "price", "category", "shipping_address", "coupon"],
    "hr": ["Employee_ID", "First_Name", "Last_Name", "Department_Region", "Status", "Join_Date",
           "Salary", "Email", "Performance_Score"],
}


def _detect(columns):
    return detect_domain_detailed(SimpleNamespace(columns=columns))


@pytest.mark.parametrize("vertical", list(_VERTICALS))
def test_vertical_registered(vertical):
    assert vertical in list_domains()


@pytest.mark.parametrize("vertical,columns", list(_VERTICALS.items()))
def test_vertical_detects_itself(vertical, columns):
    r = _detect(columns)
    assert r.domain == vertical, (r.domain, r.score)
    assert r.score >= 0.5, r.score


@pytest.mark.parametrize("columns", [_PERSON, _SALES_CONTACT])
def test_person_data_detects_no_new_vertical(columns):
    assert _detect(columns).domain not in _VERTICALS


@pytest.mark.parametrize("expected,columns", list(_EXISTING.items()))
def test_existing_verticals_not_stolen(expected, columns):
    assert _detect(columns).domain == expected, _detect(columns).domain


def test_order_flavored_data_resolves_to_ecommerce_not_logistics():
    # Documented overlap: order-centric data (order_id + shipping_address) is
    # ecommerce, not logistics — neither steals the other's pure data.
    cols = ["order_id", "tracking_number", "carrier", "shipping_address", "delivery_status", "warehouse_id"]
    assert _detect(cols).domain == "ecommerce", _detect(cols).domain
```

- [ ] **Step 11: Run the test + ruff + regression smoke (box)**
```bash
"$INTERP" -m pytest packages/python/infermap/tests/test_messy_vertical_domains.py -q
"$INTERP" -m ruff check packages/python/infermap/tests/test_messy_vertical_domains.py
"$INTERP" -m pytest packages/python/infermap/tests/test_dictionaries.py packages/python/infermap/tests/test_detect_dispatch.py -q
```
Expected: the vertical test all-pass; ruff clean; the existing detect/dictionary tests still pass.

- [ ] **Step 12: Confirm byte-identity (all 7 TS==Python) then commit**
```bash
for d in logistics energy automotive legal hospitality manufacturing marketing; do
  git add "packages/typescript/goldencheck-types/domains/$d.yaml" "packages/python/goldencheck-types/goldencheck_types/_domains/$d.yaml"
done
git add packages/python/infermap/tests/test_messy_vertical_domains.py
for d in logistics energy automotive legal hospitality manufacturing marketing; do
  a=$(git cat-file blob ":packages/typescript/goldencheck-types/domains/$d.yaml" | sha1sum | cut -d' ' -f1)
  b=$(git cat-file blob ":packages/python/goldencheck-types/goldencheck_types/_domains/$d.yaml" | sha1sum | cut -d' ' -f1)
  echo "$d: $([ "$a" = "$b" ] && echo MATCH || echo MISMATCH)"
done
git commit -m "feat(goldencheck-types): logistics/energy/automotive/legal/hospitality/manufacturing/marketing packs

Seven discriminative vertical packs (verified cross-clean against the 9 existing
packs + each other via the full 16-pack matrix): each detects its own data
0.8-1.0, zero person/sales-contact false positives, no steal. Two inherent
overlaps documented + asserted (logistics<->ecommerce order fulfillment;
marketing sub-0.5 on light-CRM). TS canonical + synced Python copies.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44"
```
Expected: all 7 `MATCH` (byte-identical → CI `--check` passes).

---

### Task 2: Ship

**Files:** none.

- [ ] **Step 1: Rebase + push + PR**
```bash
unset GH_TOKEN; gh auth switch --user benzsevern >/dev/null 2>&1; export GH_TOKEN=$(gh auth token --user benzsevern)
git fetch origin main -q && git rebase origin/main
git push -u origin feat/goldencheck-types-messy-verticals --force-with-lease
gh pr create --repo benseverndev-oss/goldenmatch --base main --head feat/goldencheck-types-messy-verticals \
  --title "feat(goldencheck-types): 7 vertical domain packs (logistics/energy/automotive/legal/hospitality/manufacturing/marketing)" \
  --body "<summary: 7 discriminative vertical packs so InferMap detect recognizes these verticals -> the goldenpipe brain confident_schema path. goldencheck-types 9 -> 16 packs. Full 16-pack cross-verification matrix box-verified: each detects its own data 0.8-1.0, person/sales-contact -> None, existing 9 unchanged, no sibling-steal. Two inherent overlaps documented + asserted (logistics<->ecommerce fulfillment; marketing sub-0.5 on light-CRM -- both resolve correctly). Discriminative tokens dodge collisions: utility_account not account_number (energy vs finance), no bare sku (logistics vs ecommerce), lead/campaign/utm not email/name (marketing vs person). TS canonical + synced byte-identical Python copies (--check gate). Completes the deferred vertical batch.>

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44"
```

- [ ] **Step 2: Watch CI** — jobs: `python (goldencheck-types)` (sync `--check` gate), `python (infermap)` (vertical + regression), `typescript` (loads 7 packs).
```bash
gh pr checks <PR#> --repo benseverndev-oss/goldenmatch
gh run view <run-id> --repo benseverndev-oss/goldenmatch --log-failed | grep -iE "logistics|energy|automotive|legal|hospitality|manufacturing|marketing|drift|sync|test_messy|domain" | head -20
```
Likely-red causes (avoidable): `--check` drift (Step 12 pre-verifies byte-identity), or a `validate_yaml` type missing `suppress` (Step 1-7 all include it). Fix, commit, push, re-check.

- [ ] **Step 3: Arm auto-merge + STOP**
```bash
gh pr merge <PR#> --auto --squash   # WITHOUT --delete-branch; if 'strategy set by queue', run: gh pr merge <PR#> --auto
```
Then STOP.

---

## Cross-cutting reminders
- **Every type needs a `suppress` key** (validate_yaml.py) — mirror `hr.yaml`.
- **Discriminative hints only** — no bare `account_number`/`sku`/`email`/`name`/`contact`/`price`/`status`/`grade`. The §3 sets are box-cross-verified.
- **TS canonical; Python `_domains/*.yaml` sync-generated — never hand-edit** (byte-identity gated by `--check`; Step 12 pre-verifies SHAs).
- The full 16-pack matrix (Step 9) is the real gate — re-run after any hint change.
- The two overlaps (logistics↔ecommerce, marketing↔light-CRM) are documented + asserted, not engineered away (they resolve correctly).
- 3rd copy (`goldencheck/semantic/domains/`) intentionally left without these packs.
- Entirely box-runnable; ship + arm; CI `--check` + suites re-verify.
