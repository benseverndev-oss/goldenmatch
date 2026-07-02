# Discriminative-power veto for exact/identity matchkeys (#1351)

**Date:** 2026-07-02
**Issue:** benseverndev-oss/goldenmatch#1351
**Status:** design approved, pending spec review

## Problem

`AutoConfigController`'s zero-config path commits a standalone `exact` matchkey
on low-cardinality, high-density columns (observed: `exact[zip]` on a
19,278-row circulation dataset where thousands of records share a zip),
collapsing dozens of distinct people into one cluster (~55% over-merge,
52-member false clusters).

The proximate mechanism is a cardinality-driven identifier promotion in
`_classify_by_data` (autoconfig.py ~297–314) fed a **sample-inflated** cardinality
(profiling samples 1,000 rows; a moderate-cardinality `zip` reads ~0.96 in a
1k sample vs a true ~0.26), which reclassifies `zip -> "identifier"`, defeating
the zip/geo blocking-signal guard, and lets `exact[zip]` through.

### Why cardinality is the wrong signal (the real root cause)

Making cardinality honest (full-column `n_unique`) was implemented and **failed
the auto-config accuracy gate**: `npi` and `phone_number` (genuine identity keys
that legitimately repeat — the same provider/phone recurs across records) have
*moderate* full-data cardinality too, so honest cardinality demoted them
`identifier -> phone`, exploding blocking (candidate pairs 1,529 -> 415,097) and
dropping F1 (`ncvr_synthetic` 0.983 -> 0.921, `historical_50k` 0.466 -> 0.340).

`zip` and `npi` have **the same moderate cardinality but opposite correct
answers** — so no cardinality threshold (sampled or honest) can separate them.
Cardinality conflates "shared attribute" (zip: sharers are different people in
one area) with "shared identity key" (npi: sharers are the same entity).

### The distinguishing signal

Discriminative power: **among records that share a candidate value, do they also
agree on the rest of the identity?**
- `npi` sharers also agree on name/other ids -> shared value predicts identity
  -> valid exact key.
- `zip` sharers have unrelated names -> shared value does NOT predict identity
  -> blocking-only attribute.

Label-free, cheap, sample-estimable, and generic across entity types (no name
lists, no per-column special-casing).

## Existing machinery (reused / not reused)

- `ColumnPrior.identity_score` (complexity_profile.py) — type + high-cardinality
  based, so it shares the zip/npi blind spot. **Not** the basis for this fix.
- `_make_weak_positive_fn` / `_default_discriminative_fields`
  (blocking_pass_selection.py) — an existing "pair agrees on >=2 discriminative
  fields" co-agreement predicate, currently used only for blocking-pass pruning.
  Close in spirit; reuse its shape where practical rather than inventing.
- Chao1 sample->full cardinality extrapolation (autoconfig_verify.py) — a
  cardinality axis; not applicable (cardinality is the wrong signal here).

## Design

### Posture: veto layer (not replace)

Keep the current identifier promotion as the CANDIDATE generator. Add a
discriminative-power gate that only **demotes** a proposed standalone `exact`
key when records sharing its value do not co-agree on other identity fields.
It never promotes new keys. By construction it cannot regress the npi/email
promotions the gate checks; it only closes the `zip` hole.

### 1. Insertion point

A veto step in `build_matchkeys` (autoconfig.py; `df` is a parameter, passed by
the production caller `auto_configure_df`), adjacent to the existing exact
machinery — approximate anchors: zip/geo guard ~line 1089, exact-matchkey floor
gate ~1108, standalone-exact emit site ~1199. `df` is in scope here.
It runs after a column is proposed as a standalone `exact` key. Scope is
deliberately narrow:
- Only removes the `exact` MATCHKEY for the vetoed column.
- Leaves `col_type` classification and blocking untouched — a vetoed `zip` can
  still be a blocking key (blocking only groups candidates; the matchkey decides
  merges).

This is the minimal surface that fixes the over-merge and keeps the accuracy
gate's classification/blocking metrics unchanged for non-vetoed columns.

### 2. Estimator — `discriminative_power(col, df, basket)`

For a proposed exact key `C`, on the `df` passed to `build_matchkeys`:

1. Group rows by `C`'s value; keep groups with >=2 rows (shared-value groups).
2. Deterministically (seeded) sample up to `MAX_PAIRS` record-pairs from those
   groups — bounded cost.
3. **Basket B** = the OTHER columns whose `col_type` is an identity signal,
   drawn from the ACTUAL classifier taxonomy (full set: `email, name,
   multi_name, phone, zip, address, geo, identifier, description, numeric, date,
   string, year`):
   - **Identity basket (include):** `{name, multi_name, email, phone,
     identifier}`.
   - **Exclude (locality/attribute + non-discriminative):** `{zip, geo,
     address, description, numeric, date, year, string}`.
   - Load-bearing exclusion: `zip`/`geo`/`address` are locality signals — if they
     were in the basket, `zip`-sharers would spuriously co-agree (same locality)
     and never be vetoed. When judging an *identity* column, geo columns in the
     basket are harmless; when judging a *geo* column, the basket must be
     identity-only. Basket membership is decided from the existing `col_type`
     values above — generic, no per-name logic. (Note: `city`/`state`/`postcode`
     are not distinct types — they fold into `geo`/`zip`.)
4. `discriminative_power(C)` = mean over sampled shared-value pairs of their
   agreement rate across B (cheap normalized/exact compare per basket field).

### 3. Veto decision + fail-safes

- Demote `exact[C]` -> blocking-only iff
  **support >= `MIN_SHARED_PAIRS`** AND **`discriminative_power(C) < TAU`**
  (start `TAU` ≈ 0.5, tuned against the gate; both env-overridable per repo
  convention).
- **Fail-safe = keep** whenever evidence is thin: `df is None` (unit-test /
  ad-hoc callers — same guard as the existing `df is not None` blocks in
  `build_matchkeys`), too few shared-value pairs, or an empty basket. This is why
  near-unique identity keys (`npi`, `email`) are
  protected automatically: they have few shared-value pairs -> low support ->
  insufficient evidence -> kept. The veto only ever fires on **high-density**
  columns (many value collisions) with low co-agreement — exactly the `zip`
  shape. (The support-gate is the primary protection for legit identity keys;
  no explicit type carve-out is added — keep it simple.)
- Kill-switch `GOLDENMATCH_DISCRIMINATIVE_VETO=0` disables the veto (default on).
- Deterministic: seeded pair sampling so the quality gate is reproducible.

### 4. Constants (initial, env-overridable, tuned against the gate)

- `MIN_SHARED_PAIRS` — minimum sampled shared-value pairs required to veto
  (start ~20).
- `MAX_PAIRS` — cap on sampled pairs per candidate (start ~200).
- `TAU` — discriminative-power floor below which a key is vetoed (start ~0.5).
- Env: `GOLDENMATCH_DISCRIMINATIVE_VETO`, `GOLDENMATCH_DISCRIMINATIVE_TAU`.

## Testing / validation

- **Local auto-config quality gate** (`python -m scripts.autoconfig_quality
  gate`, ~1 min, no OOM) is the primary harness. Bar: `anchor_sparse_zip` keeps
  `exact['email','npi']`, `npi`/`phone_number` stay `identifier`,
  `ncvr_synthetic`/`historical_50k` F1 within tolerance of the main baseline
  (no regression), verdict PASS.
- **New gate anchor** reproducing the DERM shape (a zip-dense person dataset
  where `zip` currently promotes to an exact key) with an assertion that `zip`
  is vetoed out of the exact matchkeys — regression-protects the fix going
  forward.
- **Unit tests** on `discriminative_power` with tiny in-memory frames: zip-like
  (low co-agreement, high support -> veto), npi-like (high co-agreement -> keep),
  thin-support near-unique (keep), empty-basket (keep), kill-switch off (keep).
- CI's full accuracy gates (Febrl / DBLP-ACM / NCVR / #528) remain the final
  cross-dataset validation.

## Non-goals

- Not replacing the cardinality-based promotion (veto only).
- Not changing profiling/sampling, `_classify_by_data`'s promotion, the
  `exact_matchkey_floor` oracle, or blocking selection.
- Not addressing the near-unique-at-huge-scale promotion-floor limitation noted
  in #1351 (separate, low-harm, pre-existing).
- Not touching probabilistic (Fellegi-Sunter) matchkeys.

## Risks

- **Basket selection depends on the col_type taxonomy** being roughly right
  about identity-vs-locality types. If a genuine identity column is typed as
  locality (or vice versa) the basket could mislead — mitigated by the
  support-gate + fail-safe-keep and validated by the gate.
- **Thin sample support** on the controller's ~1.5k-row sample could make the
  veto inert for mid-density columns; acceptable (fail-safe keep) and the DERM
  over-merge case is high-density (ample support).
- **Threshold tuning** (`TAU`, `MIN_SHARED_PAIRS`) is empirical; the local gate
  gives a fast loop, and env overrides allow tuning without code changes.
