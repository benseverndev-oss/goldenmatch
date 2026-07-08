# goldencheck-types `hr` domain pack — design

**Status:** approved (design gate)
**Date:** 2026-07-07
**Motivation:** Dogfooding the goldenpipe brain on a real `Messy_Employee_dataset.csv` (1020×12) showed the brain correctly picks `default` — because no shipped domain pack covers employee/HR data, so `detect_domain` returns None and the `confident_schema` path (which prepends `infer_schema`) never fires. This adds an `hr` pack so HR data auto-detects, exercising that path.

## 1. Goal

Ship an `hr` domain pack in goldencheck-types so InferMap `detect_domain` recognizes employee/HR datasets (score ≥ 0.5 → the goldenpipe brain's `confident_schema` rule → `infer_schema` prepended). Cross-surface (Python + TS) by construction: the pack is shared data read by the byte-parity-gated detect kernel.

## 2. The key design decision: discriminative hints

Detect scores each domain `hits / n_columns`, where a hint matches iff its tokens are a contiguous run in a column's tokens (`_hint_matches`). `confident_schema` needs the winning `score ≥ 0.5`.

**Use HR-DISCRIMINATIVE hints, deliberately excluding generic person tokens** (`first_name`, `last_name`, `email`, `phone`, `name`, `address`) AND favoring **multi-token / compound forms** over bare generic single tokens. Rationale: broad person tokens would false-positive on any customer/person dataset; and bare single tokens like `title`, `role`, `grade`, `band`, `rating` collide with ecommerce/other columns (e.g. `product_title`→`title`, retail `department`), letting a wide non-HR df creep toward 0.5. So the pack uses `job_title` (not `title`), `performance_score`/`performance_rating` (not `rating`), `pay_grade`/`job_level` (not `grade`/`band`), and drops bare `role`/`position`/`team`/`experience`/`start_date`/`wage`. `department` (org-core to HR) and `status` (employment status) are the two broader tokens kept — each contributes at most 1-2/n to a non-HR df, far below threshold.

**Verified on the box** (`_detect_core_pure`, the real kernel) with the tightened §3 hint set:

| Dataset | Detect result |
|---|---|
| Target employee (12 col) | `hr` **0.58**, confident |
| Typical HR export (`emp_id, designation, department, date_of_joining, salary, manager, employment_status`) | `hr` **0.875** |
| Generic person (`first_name, last_name, email, phone, city, state`) | **None** (no false positive) |
| Customer (`customer_id, name, email, phone, address, status`) | **None** (hr only 0.17) |
| **Wide ecommerce** (`product, sku, price, category, department, product_title, brand, rating, order_status, shipping_address`) | `ecommerce` **0.9**, hr **0.2** (no creep) |
| Finance (`account_number, currency, amount, iban, transaction_type`) | `finance` 0.6, hr **0.0** |
| Healthcare (incl `claim_status`) | `healthcare` 0.83, hr **0.17** |

hr wins only on HR data (0.58-0.875); on every non-HR dataset it stays ≤0.2 (or 0), so it never steals a detection.

## 3. The pack — `packages/typescript/goldencheck-types/domains/hr.yaml` (canonical)

TS `domains/` is the **canonical** source; Python `_domains/` is a synced copy (§4). Mirror the existing packs' shape (`description` + `types` with `name_hints` + `value_signals` + `suppress`). `detect` uses only `name_hints`; `value_signals`/`suppress` are for goldencheck's semantic scanning + a well-formed pack (kept minimal, patterned on `finance.yaml`).

Types (the union of `name_hints` is what detect scores — the verified TIGHTENED discriminative set). Bare generic single tokens (`title`, `role`, `position`, `team`, `grade`, `band`, `rating`, `experience`, `start_date`, `wage`) are deliberately EXCLUDED (see §2):

- **employee_id**: `[employee_id, emp_id, staff_id, worker_id, personnel_id]` — high-uniqueness id.
- **job_title**: `[job_title, designation]`
- **department**: `[department, dept, division]`
- **salary**: `[salary, compensation, annual_salary, base_salary, ctc]` — numeric.
- **pay_grade**: `[pay_grade, job_level]` — low-cardinality.
- **hire_date**: `[hire_date, join_date, joining_date, date_of_joining, doj]`
- **employment_status**: `[employment_status, employee_status, employment_type, status]` — low-cardinality.
- **manager**: `[manager, supervisor, reports_to, reporting_manager]`
- **performance_rating**: `[performance, performance_score, performance_rating, appraisal]` — low-cardinality.
- **work_arrangement**: `[remote_work, work_mode, work_location]`
- **tenure**: `[tenure, years_of_service]` — numeric.

At implementation, the FINAL `hr.yaml` is validated by the real load path (write it, run `detect_domain_detailed` on the employee df → assert `domain == "hr"`, `score ≥ 0.5`, AND a wide-ecommerce df does NOT detect hr); if the type-organized token set shifts the score, adjust to keep the §2 verified outcomes. (loader shape-validates only that `name_hints` is a list / `value_signals` a dict / `suppress` a list — no key allowlist — so any reasonable `value_signals`/`suppress` load cleanly; detect reads only `name_hints`.)

## 4. Sync (TS canonical → Python)

Run `scripts/sync_domain_packs.py` to mirror `domains/hr.yaml` → `packages/python/goldencheck-types/goldencheck_types/_domains/hr.yaml` (byte-identical copy). Confirm `python scripts/sync_domain_packs.py --check` exits 0 (the CI drift gate). Box-runnable.

Note: a THIRD copy exists at `packages/python/goldencheck/goldencheck/semantic/domains/` (goldencheck's own semantic scanning — NOT read by InferMap detect). It has finance/healthcare/ecommerce but not generic; whether to add `hr` there is out of scope (InferMap detect + the goldenpipe brain read goldencheck-types, which the sync script covers). Note it and leave it.

## 5. Tests

- **Python detect test** (box-runnable) — `packages/python/infermap/tests/test_hr_domain.py` (a NEW file, not `test_dictionaries.py` — that tests the separate InferMap *alias-dictionary* registry, not the goldencheck-types packs). Assert `goldencheck_types.list_domains()` includes `"hr"` (the registry `detect` actually reads — NOT `infermap.dictionaries.available_domains()`, which globs a different dir and will NOT contain `hr`). Assert `detect_domain_detailed(df)` on an employee-column df returns `domain == "hr"` with `score >= 0.5`; assert a generic person df does NOT detect `hr`; assert a wide-ecommerce df detects `ecommerce`, not `hr`. Uses the real shipped pack via goldencheck-types.
- **Regression check** (in the plan): confirm no test asserts the EXACT shipped-domain set/count (grep — `test_dictionaries.py` uses membership `assert "finance" in domains`, which a new domain doesn't break; `test_detect_dispatch.py`/`test_native_parity.py` pass synthetic packs explicitly, unaffected).
- **Parity is by construction** — `hr` is data read by the shared kernel; native/WASM byte-parity holds automatically (no new symbol). No separate TS test needed beyond the existing parity gate; a TS `domainPack.test.ts`-style smoke (loads `hr`) MAY be added if the TS suite asserts loadable packs, but is not required.

## 6. Dogfood (validation, not a gate)

Re-run the real `Messy_Employee_dataset.csv` through the goldenpipe brain → expect `rule=confident_schema`, `inferred_domain="hr"`, and `infer_schema` prepended + executed. Confirms the end-to-end payoff.

## 7. Non-goals

- No change to detect scoring, the brain, or `parseCsv`.
- No `hr` in the 3rd goldencheck semantic copy (out of scope; not read by detect).
- No broad person-token hints (the whole point — avoids customer/person false positives).

## 8. File touch list

- `packages/typescript/goldencheck-types/domains/hr.yaml` — **new** (canonical, box-validated).
- `packages/python/goldencheck-types/goldencheck_types/_domains/hr.yaml` — **new** (synced copy via the script — do not hand-edit).
- `packages/python/infermap/tests/test_hr_domain.py` — **new** (Python detect test, box).
