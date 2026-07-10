# goldencheck-types vertical domain packs — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add four discriminative domain packs (`insurance`, `telecom`, `real_estate`, `education`) to goldencheck-types so InferMap detect recognizes those verticals → the goldenpipe brain's `confident_schema` path.

**Architecture:** Four hand-authored YAML packs in the canonical TS `domains/` dir, mirrored to Python `_domains/` by `scripts/sync_domain_packs.py`, locked by one parametrized Python detect test. Same mechanism as `hr` (#1558). Cross-surface parity by construction.

**Tech Stack:** YAML (domain packs), Python (pytest, box-runnable), the sync script.

**Spec:** `docs/superpowers/specs/2026-07-07-goldencheck-types-vertical-packs-design.md`

---

## Environment

```bash
cd "D:/show_case/gg-local-llm"
INTERP="D:/show_case/goldenmatch/.venv/Scripts/python.exe"
export PYTHONPATH="packages/python/goldenpipe;packages/python/infermap;packages/python/goldencheck-types"
export POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8
```
(`;` separator.) Branch `feat/goldencheck-types-vertical-packs` (off fresh origin/main which has `hr`, spec committed). **Entirely box-runnable** (author → sync → detect matrix → test); CI re-verifies `--check` + suites.

**Representative datasets** (from spec §2, used to validate — each should detect its own vertical ≥0.5):
- `INSURANCE = [policy_number, claim_number, premium, deductible, coverage_type, policyholder, underwriter, claim_status, insured_name, payout]`
- `TELECOM = [subscriber_id, msisdn, imei, imsi, data_usage, call_duration, plan_name, network_type, billing_cycle, sim_id]`
- `REAL_ESTATE = [listing_id, mls, property_type, bedrooms, bathrooms, square_feet, lot_size, year_built, asking_price, city]`
- `EDUCATION = [student_id, enrollment_id, course_code, course_name, gpa, grade_level, credits, semester, major, attendance]`

## File Structure

| File | Responsibility |
|------|----------------|
| `packages/typescript/goldencheck-types/domains/{insurance,telecom,real_estate,education}.yaml` | The 4 packs (canonical, hand-authored) |
| `packages/python/goldencheck-types/goldencheck_types/_domains/{...}.yaml` | Synced copies (script-generated — DO NOT hand-edit) |
| `packages/python/infermap/tests/test_vertical_domains.py` | Parametrized detect + cross-steal test (box) |

---

### Task 1: Author the 4 packs, sync, cross-verify, and test (all box-verified)

**Files:**
- Create: `packages/typescript/goldencheck-types/domains/insurance.yaml`, `telecom.yaml`, `real_estate.yaml`, `education.yaml`
- Generate (via script): the 4 `packages/python/goldencheck-types/goldencheck_types/_domains/*.yaml` copies
- Create: `packages/python/infermap/tests/test_vertical_domains.py`

- [ ] **Step 1: Author `insurance.yaml`** (mirror `hr.yaml` shape; `value_signals`/`suppress` from the vocab; hints are the SPEC-VERIFIED set):
```yaml
description: "Insurance — policies, claims, premiums, coverage, underwriting, beneficiaries"

types:
  policy:
    name_hints: ["policy_number", "policy_id", "policy_no"]
    value_signals:
      min_unique_pct: 0.90
    suppress: ["cardinality", "pattern_consistency", "drift_detection"]

  claim:
    name_hints: ["claim_number", "claim_id", "claim_no", "claim_status"]
    suppress: ["pattern_consistency"]

  premium:
    name_hints: ["premium", "premium_amount", "annual_premium"]
    value_signals:
      numeric: true
    suppress: ["pattern_consistency"]

  deductible:
    name_hints: ["deductible"]
    value_signals:
      numeric: true
    suppress: ["pattern_consistency"]

  coverage:
    name_hints: ["coverage", "coverage_amount", "coverage_type", "sum_insured"]
    value_signals:
      max_unique: 30
    suppress: ["uniqueness", "range_distribution"]

  policyholder:
    name_hints: ["policyholder", "insured"]
    value_signals:
      mixed_case: true
    suppress: ["pattern_consistency"]

  underwriter:
    name_hints: ["underwriter"]
    value_signals:
      mixed_case: true
    suppress: ["pattern_consistency"]

  beneficiary:
    name_hints: ["beneficiary"]
    value_signals:
      mixed_case: true
    suppress: ["pattern_consistency"]

  insurance_type:
    name_hints: ["insurance_type", "policy_type"]
    value_signals:
      max_unique: 20
    suppress: ["uniqueness", "range_distribution"]
```

- [ ] **Step 2: Author `telecom.yaml`**:
```yaml
description: "Telecom — subscribers, device identifiers (IMEI/IMSI/ICCID), usage, plans, network"

types:
  subscriber:
    name_hints: ["subscriber_id", "subscriber_number", "msisdn"]
    value_signals:
      min_unique_pct: 0.90
    suppress: ["cardinality", "pattern_consistency", "drift_detection"]

  device:
    name_hints: ["imei", "imsi", "iccid", "sim_id"]
    value_signals:
      min_unique_pct: 0.90
    suppress: ["cardinality", "pattern_consistency", "drift_detection"]

  usage:
    name_hints: ["data_usage", "call_duration", "sms_count", "minutes_used"]
    value_signals:
      numeric: true
    suppress: ["pattern_consistency"]

  plan:
    name_hints: ["plan_id", "plan_name", "rate_plan"]
    value_signals:
      max_unique: 30
    suppress: ["uniqueness", "range_distribution"]

  network:
    name_hints: ["network_type"]
    value_signals:
      max_unique: 15
    suppress: ["uniqueness", "range_distribution"]
```

- [ ] **Step 3: Author `real_estate.yaml`** (NO bare `address`/`city`/`price` — person/ecommerce collision):
```yaml
description: "Real estate — listings, properties, features (beds/baths/sqft), sale prices"

types:
  listing:
    name_hints: ["listing_id", "listing_price", "mls", "mls_number"]
    value_signals:
      min_unique_pct: 0.80
    suppress: ["cardinality", "pattern_consistency"]

  property:
    name_hints: ["property_id", "property_type", "property_address"]
    suppress: ["pattern_consistency"]

  features:
    name_hints: ["bedrooms", "bathrooms", "square_feet", "sqft", "lot_size", "year_built", "garage"]
    value_signals:
      numeric: true
    suppress: ["pattern_consistency"]

  price:
    name_hints: ["asking_price", "list_price", "sale_price", "sold_price"]
    value_signals:
      numeric: true
    suppress: ["pattern_consistency"]
```

- [ ] **Step 4: Author `education.yaml`** (`grade_level` compound, not bare `grade`):
```yaml
description: "Education — students, enrollment, courses, grades/GPA, terms, programs"

types:
  student:
    name_hints: ["student_id", "student_number", "enrollment_id", "enrollment"]
    value_signals:
      min_unique_pct: 0.80
    suppress: ["cardinality", "pattern_consistency", "drift_detection"]

  course:
    name_hints: ["course_id", "course_code", "course_name"]
    value_signals:
      max_unique: 200
    suppress: ["uniqueness"]

  grades:
    name_hints: ["gpa", "grade_level", "grade_point", "credits", "credit_hours"]
    value_signals:
      numeric: true
    suppress: ["pattern_consistency"]

  term:
    name_hints: ["semester", "term", "academic_year"]
    value_signals:
      max_unique: 30
    suppress: ["uniqueness", "range_distribution"]

  program:
    name_hints: ["major", "transcript", "degree"]
    value_signals:
      max_unique: 100
    suppress: ["uniqueness"]
```

- [ ] **Step 5: Sync TS → Python + verify the drift gate**
```bash
"$INTERP" scripts/sync_domain_packs.py            # copies the 4 new domains/*.yaml -> _domains/
"$INTERP" scripts/sync_domain_packs.py --check    # MUST exit 0
echo "sync --check exit: $?"
ls packages/python/goldencheck-types/goldencheck_types/_domains/{insurance,telecom,real_estate,education}.yaml
```
Expected: 4 files synced; `--check` exits 0. **Do NOT hand-edit the Python `_domains/*.yaml`.**

- [ ] **Step 6: Validate the FINAL YAMLs via the real load path + full cross-verification matrix (box)**
```bash
"$INTERP" -c "
from goldencheck_types import list_domains, load_domain
from infermap.detect import detect_domain_detailed
from types import SimpleNamespace
for d in ['insurance','telecom','real_estate','education']:
    assert d in list_domains(), (d, list_domains())
    load_domain(d)  # parses
D=lambda cols: detect_domain_detailed(SimpleNamespace(columns=cols))
sets = {
 'insurance':['policy_number','claim_number','premium','deductible','coverage_type','policyholder','underwriter','claim_status','insured_name','payout'],
 'telecom':['subscriber_id','msisdn','imei','imsi','data_usage','call_duration','plan_name','network_type','billing_cycle','sim_id'],
 'real_estate':['listing_id','mls','property_type','bedrooms','bathrooms','square_feet','lot_size','year_built','asking_price','city'],
 'education':['student_id','enrollment_id','course_code','course_name','gpa','grade_level','credits','semester','major','attendance'],
}
for name, cols in sets.items():
    r = D(cols); print(name, '->', r.domain, round(r.score,2)); assert r.domain==name and r.score>=0.5, (name, r.domain, r.score)
# no false positive
for label, cols in [('person',['first_name','last_name','email','phone','city','state','address']),('customer',['customer_id','name','email','phone','address','status'])]:
    r=D(cols); print(label,'->',r.domain); assert r.domain not in ('insurance','telecom','real_estate','education'), (label, r.domain)
# existing 5 unchanged
for label, cols, exp in [
  ('employee',['Employee_ID','First_Name','Last_Name','Age','Department_Region','Status','Join_Date','Salary','Email','Phone','Performance_Score','Remote_Work'],'hr'),
  ('finance',['account_number','currency','amount','iban','transaction_type'],'finance'),
  ('ecommerce',['order_id','sku','product','price','category','shipping_address','coupon'],'ecommerce'),
  ('healthcare',['patient_id','mrn','diagnosis','provider','medication','claim_status'],'healthcare'),
]:
    r=D(cols); print(label,'->',r.domain); assert r.domain==exp, (label, r.domain)
print('MATRIX OK')
"
```
Expected: each vertical → itself ≥0.5; person/customer → not a new vertical; employee→hr, finance→finance, ecommerce→ecommerce, healthcare→healthcare; `MATRIX OK`. (The 4 new packs not stealing each other is proven by each vertical detecting its OWN domain — single-winner detect.) If any assert fails, adjust the offending pack's `name_hints` (keep the spec-verified tokens) and re-sync + re-run before proceeding.

- [ ] **Step 7: Write the Python detect test** — `packages/python/infermap/tests/test_vertical_domains.py`:
```python
"""The 4 shipped vertical domain packs (insurance, telecom, real_estate,
education): each registered in goldencheck-types + detects its own vertical
without stealing person/generic data or the existing 5 packs.

Note: detect returns a SINGLE winner, so asserting `<vertical>_df -> <vertical>`
already proves no sibling pack steals it — no separate no-sibling-steal case
needed."""
from types import SimpleNamespace

import pytest

from goldencheck_types import list_domains
from infermap.detect import detect_domain_detailed

_VERTICALS = {
    "insurance": ["policy_number", "claim_number", "premium", "deductible", "coverage_type",
                  "policyholder", "underwriter", "claim_status", "insured_name", "payout"],
    "telecom": ["subscriber_id", "msisdn", "imei", "imsi", "data_usage", "call_duration",
                "plan_name", "network_type", "billing_cycle", "sim_id"],
    "real_estate": ["listing_id", "mls", "property_type", "bedrooms", "bathrooms", "square_feet",
                    "lot_size", "year_built", "asking_price", "city"],
    "education": ["student_id", "enrollment_id", "course_code", "course_name", "gpa",
                  "grade_level", "credits", "semester", "major", "attendance"],
}
_PERSON = ["first_name", "last_name", "email", "phone", "city", "state", "address"]
# existing verticals must still detect correctly with the 4 new packs present
_EXISTING = {
    "hr": ["Employee_ID", "First_Name", "Last_Name", "Age", "Department_Region", "Status",
           "Join_Date", "Salary", "Email", "Phone", "Performance_Score", "Remote_Work"],
    "finance": ["account_number", "currency", "amount", "iban", "transaction_type"],
    "ecommerce": ["order_id", "sku", "product", "price", "category", "shipping_address", "coupon"],
    "healthcare": ["patient_id", "mrn", "diagnosis", "provider", "medication", "claim_status"],
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


def test_generic_person_detects_no_new_vertical():
    assert _detect(_PERSON).domain not in _VERTICALS


@pytest.mark.parametrize("expected,columns", list(_EXISTING.items()))
def test_existing_verticals_not_stolen(expected, columns):
    assert _detect(columns).domain == expected, _detect(columns).domain
```

- [ ] **Step 8: Run the test + ruff + regression smoke (box)**
```bash
"$INTERP" -m pytest packages/python/infermap/tests/test_vertical_domains.py -q
"$INTERP" -m ruff check packages/python/infermap/tests/test_vertical_domains.py
"$INTERP" -m pytest packages/python/infermap/tests/test_dictionaries.py packages/python/infermap/tests/test_detect_dispatch.py -q
```
Expected: the vertical test all-pass; ruff clean; the existing detect/dictionary tests still pass (membership/synthetic-pack — 4 new domains don't perturb them).

- [ ] **Step 9: Confirm byte-identity (TS canonical == Python copy) for all 4, then commit**
```bash
for d in insurance telecom real_estate education; do
  git add "packages/typescript/goldencheck-types/domains/$d.yaml" "packages/python/goldencheck-types/goldencheck_types/_domains/$d.yaml"
done
git add packages/python/infermap/tests/test_vertical_domains.py
# byte-identity check (the CI --check gate compares these):
for d in insurance telecom real_estate education; do
  a=$(git cat-file blob ":packages/typescript/goldencheck-types/domains/$d.yaml" | sha1sum | cut -d' ' -f1)
  b=$(git cat-file blob ":packages/python/goldencheck-types/goldencheck_types/_domains/$d.yaml" | sha1sum | cut -d' ' -f1)
  echo "$d TS=$a PY=$b $([ "$a" = "$b" ] && echo MATCH || echo MISMATCH)"
done
git commit -m "feat(goldencheck-types): insurance/telecom/real_estate/education domain packs

Four discriminative vertical packs (verified cross-clean against the 5 existing
packs + each other): each detects its own data ~0.9, zero person/generic false
positives, no steal. TS canonical + synced Python copies. Feeds InferMap detect
-> the goldenpipe brain confident_schema path.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44"
```
Expected: all 4 show `MATCH` (byte-identical → CI `--check` passes).

---

### Task 2: Ship

**Files:** none.

- [ ] **Step 1: Rebase + push + PR**
```bash
unset GH_TOKEN; gh auth switch --user benzsevern >/dev/null 2>&1; export GH_TOKEN=$(gh auth token --user benzsevern)
git fetch origin main -q && git rebase origin/main
git push -u origin feat/goldencheck-types-vertical-packs --force-with-lease
gh pr create --repo benseverndev-oss/goldenmatch --base main --head feat/goldencheck-types-vertical-packs \
  --title "feat(goldencheck-types): insurance/telecom/real_estate/education domain packs" \
  --body "<summary: 4 discriminative vertical domain packs so InferMap detect recognizes insurance/telecom/real_estate/education data -> the goldenpipe brain confident_schema path. Cross-verified on the box against all 9 packs: each detects its own data ~0.9, person/generic -> None, existing 5 (finance/ecommerce/healthcare/hr) detect unchanged, no sibling-steal. TS canonical + synced Python copies (byte-identical, --check gate). Extends the hr pack (#1558). Deferred verticals (logistics/energy/automotive/legal/hospitality/manufacturing/marketing) noted in the spec.>

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44"
```

- [ ] **Step 2: Watch CI** — relevant jobs: `python (goldencheck-types)` (the sync `--check` gate), `python (infermap)` (the vertical + regression tests), `typescript` (loads the 4 new packs).
```bash
gh pr checks <PR#> --repo benseverndev-oss/goldenmatch
# for a failing job:
gh run view <run-id> --repo benseverndev-oss/goldenmatch --log-failed | grep -iE "insurance|telecom|real_estate|education|drift|sync|test_vertical|domain" | head -20
```
Likely-red causes (all avoidable): the `--check` gate red (a Python copy drifted → re-run `sync_domain_packs.py`, commit) — but Step 9's byte-identity check pre-empts this. Fix, commit, push, re-check.

- [ ] **Step 3: Arm auto-merge + STOP**
```bash
gh pr merge <PR#> --auto --squash   # WITHOUT --delete-branch; if 'strategy set by queue', run: gh pr merge <PR#> --auto
```
Then STOP.

---

## Cross-cutting reminders
- **Discriminative hints only** — the §3 sets are box-cross-verified (each vertical 0.9, non-verticals ≤0.2). No bare `address`/`city`/`price`/`grade`/`name`/`status`.
- **TS `domains/*.yaml` is canonical**; the Python `_domains/*.yaml` are **sync-generated — never hand-edit** (byte-identity gated by `--check`; Step 9 pre-verifies the SHAs match).
- **`list_domains()`** (goldencheck-types) is the registry detect reads.
- The full cross-verification matrix (Step 6) is the real gate — run it after any hint change.
- 3rd copy (`goldencheck/semantic/domains/`) intentionally left without these packs (not read by detect).
- Entirely box-runnable; ship + arm; CI `--check` + suites re-verify.
