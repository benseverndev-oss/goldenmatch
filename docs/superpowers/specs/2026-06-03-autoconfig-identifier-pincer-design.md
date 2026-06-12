# Auto-config identifier pincer (#715) -- design

Date: 2026-06-03
Issue: benseverndev-oss/goldenmatch#715
Status: design (approved in brainstorming, pending spec review)

## Problem

`dedupe_df(df, config=None)` on healthcare-provider-shape data (~1M rows;
sparse high-cardinality identifier columns: `npi`, `email`, `phone_number`)
commits a fuzzy-only, mega-block config that severely over-merges (582K raw
clusters from 996K input rows) and runs for ~16 minutes before the prune step
crashes. A hand-tuned config solves the same data in ~30s at 98.8% precision.

### Confirmed mechanism (the "pincer")

Reproduced on CI (workflow `repro-issue-715.yml`, run 26922192773) with synthetic
healthcare-shape data. Every identity-bearing column is excluded from matchkeys
by one of three gates, and the leftover blocking key is unusable:

| column         | col_type     | card  | excluded from matchkeys by |
|----------------|--------------|-------|----------------------------|
| `npi`          | identifier   | 0.618 | `col_type=="identifier"` skip (`autoconfig.py:566`) |
| `phone_number` | identifier   | 0.472 | same identifier skip |
| `email`        | email        | 0.699 | Guard 1: `df.height > 10000` (`:647`) |
| `zip5`         | zip          | 0.875 | "blocking signal, not identity" (`:612`) |
| `matching_id`  | identifier   | 1.000 | identifier skip (correct -- surrogate key) |

Result: `weighted t=0.8 fields=['source','first_name','last_name']` (identical
field set to the #715 report) and a single blocking key `first_name`
(`card=0.02` -> ~50K-row blocks at 1M rows -> the over-merge and the 973s wall).

### Why the guards are wrong

1. **Exact matchkeys are Polars hash self-joins** (`find_exact_matches`), not
   nested loops, and they do NOT pass through fuzzy blocking. Their real cost is
   the number of *emitted equal-pairs*, which is bounded by cardinality. A
   high-cardinality column (few records per value) emits few pairs and is both
   cheap and mega-cluster-safe. Guard 1's `O(N^2)` framing models a nested loop
   that the kernel never runs.

2. **The mega-cluster risk Guard 1 fears is the opposite shape** -- a value
   shared by a large fraction of rows, i.e. a *low*-cardinality column. That is
   already caught by the separate `cardinality_ratio >= 0.5` gate at `:631`.
   Guard 1 is therefore redundant where it is safe and harmful where it is not:
   it rejects exactly the high-cardinality columns that are the cheapest and
   safest exact matchkeys.

3. **`col_type="identifier"` conflates two different things.** Near-unique
   numeric columns are classified `identifier` (`:213-216`) and skipped from
   matchkeys entirely (`:566`). But that bucket mixes real shared identifiers
   (NPI, SSN, MRN -- appear on multiple rows for one entity, so `card < 1.0`,
   SHOULD anchor exact matchkeys) with per-record surrogate keys (`matching_id`
   -- `card == 1.0`, never shared, useless as an identity claim).

## Design

One root cause (identifier cost model), four touch-points.

### Component 1 -- matchkey side (`build_matchkeys`, core fix)

- **Remove the blanket row-count Guard 1** (`:644-655`). It is redundant with the
  `cardinality_ratio >= 0.5` mega-cluster gate.
- **Admit exact matchkeys via a cardinality band: `cardinality_ratio >= 0.5 and
  cardinality_ratio < 1.0`** (exact comparison, matching the `:631` style so the
  `card == 1.0` boundary is unambiguous in the failing-test task).
  - Lower bound = the existing mega-cluster guard (`:631`).
  - Upper bound excludes perfectly-unique surrogate keys (`card == 1.0`), which
    emit zero useful pairs. Strict `< 1.0` (a column at `card=0.9999` has a few
    real dupes worth catching -- admit it).
- **Stop skipping `col_type="identifier"` outright** (`:566`). Route `identifier`
  (already mapped to the `exact` scorer in `_SCORER_MAP`) plus the existing
  `email`/`phone` exact-eligible types through the exact path under the same
  band. `numeric`, `date`, `year` remain skipped (not identity claims).
- **Update the aggregate "all exact-eligible columns excluded" warning**
  (`:669-697`) to include `identifier` in the eligible set and reflect the new
  admission outcome.

**Sample-cardinality behavior (not a new projection):** the controller profiles
a ~1-5K sample. A real shared identifier with nulls (npi, ~60% non-null) lands
~0.6 on the sample (admitted); a surrogate key lands 1.0 (excluded). A sample
too small to surface dupes can push a real identifier to `card=1.0` and exclude
it -- but that is a *conservative* false-negative (falls back to today's
behavior, no mega-cluster), so no projection is added in the matchkey path.

### Component 2 -- blocking side (VERIFICATION, not new code)

**Finding (revised during planning):** `build_blocking` already has the full
compose stack -- `_build_compound_blocking` (greedy compound-key search), the
`safe_exact` oversized-block filter (`:1348`), the `_all_single_oversized` check,
geo-compound, and soundex/substring multi-pass fallback. Traced at true 1M
scale: `max_safe_block` scales to ~5000, so `first_name` (~50K-row block) IS
rejected (the repro only kept it because at 20K its ~1000-row block equals the
cap); and `zip5` (col_type `zip`, ~5K distinct at 1M -> ~200 rows/block, #410
projection drops its sample-inflated ratio under the 0.5 gate) would be selected
as a clean single key. So `build_blocking` is already capable.

`#715`'s reported `blocking: (none)` is therefore a **controller-commit
artifact**, not a `build_blocking` deficiency: with zero exact matchkeys the
config is fuzzy-only -> RED -> the controller's iteration thrashes and commits a
v0/degenerate entry that drops blocking. Component 1 (real exact matchkeys on
`npi`/`email`) makes the committed config healthy, so blocking (`zip5`) is
retained.

- **No new blocking code.** Do not reimplement the compose machinery.
- **Verification task:** after Component 1, assert (regression test + at-scale
  workflow) that the healthcare shape commits a healthy config that RETAINS a
  bounded blocking key (e.g. `zip5`, max block size <= cap). Only add code if
  this assertion fails -- in which case the gap is in the controller-commit path,
  tracked as a follow-up, not in `build_blocking`.

### Component 3 -- raise-on-RED (#3)

- **No new refuse logic.** The existing path already raises
  `ControllerNotConfidentError` at `df.height >= REFUSE_AT_N` on a RED commit
  when `confidence_required=True` (default). The #715 reporter passed
  `confidence_required=False` (the documented opt-out). The matchkey + blocking
  fixes flip this shape RED -> GREEN/YELLOW, so it works rather than refusing.
- **Deliverable: a regression assertion.** Healthcare-shape df ->
  `auto_configure_df` commits a non-RED config with >= 1 exact matchkey on an
  identifier column AND blocking with bounded max block size. `confidence_required`
  semantics are untouched.

### Component 4 -- docs (#4)

- New user-facing page under `packages/python/goldenmatch/docs/` (sibling to
  `blocking.md`): "Auto-config cost model -- why exact matchkeys are
  cardinality-gated, not row-count-gated." Covers: the hash-join cost model, the
  `0.5 <= card < 1.0` band, the blocking block-size cap + compose behavior, and
  the relevant override env vars (`GOLDENMATCH_BLOCKING_MAX_RATIO`, etc.).

## Testing and validation

- **Unit (`build_matchkeys`):** identifier@0.6 -> exact matchkey; identifier@1.0
  -> excluded; email@0.7 at `df.height=1_000_000` -> exact matchkey (no Guard 1
  skip); low-card@0.3 -> still skipped (mega-cluster gate intact).
- **Verification (no new `build_blocking` code):** the regression test asserts
  the committed config retains a bounded blocking key after Component 1. If it
  does not, the gap is in the controller-commit path (follow-up), not
  `build_blocking`.
- **Regression:** promote the repro generator to a shared `_healthcare_df`
  fixture in `tests/test_autoconfig_regressions.py` (sibling to `_person_df`/
  `_gate_test_df`); assert Component 3's committed-config invariant. Fix the
  repro script's too-strict verdict line (it required zero blocking too; the
  matchkey pincer is the primary signal).
- **Quality-gate guard (primary risk):** dropping Guard 1 changes behavior for
  ANY large dataset with a high-card email/identifier column. Before merge, run
  the in-house backend-parity quality gate (#528) and DQbench T1/T2/T3 to confirm
  no precision regression (notably an adversarial same-email/same-id collision
  shape, which is the closest thing to the mega-cluster risk). Hard pre-merge
  gate.
- **At-scale:** keep `repro-issue-715.yml` as the confirmation harness; add a
  post-fix assertion (exact matchkey present, blocking max block size bounded).

## Risks and mitigations

- **Behavior change for existing large datasets** (the main risk): a high-card
  email/identifier column now backs an exact matchkey it previously did not.
  Mitigation: the `0.5 <= card < 1.0` band + the existing `:631` gate bound the
  mega-cluster risk; the DQbench/quality-gate pre-merge run is the empirical
  backstop.
- **Composite blocking is the least-certain piece.** It reuses existing
  projection + block-size machinery and falls back to adaptive promotion, so the
  worst case is today's behavior, not worse.

## Out of scope

- Changing `confidence_required` / `ControllerNotConfidentError` semantics.
- A new shared-identifier-vs-surrogate-key classifier (the cardinality band
  achieves the same separation without one).
- Distributed / Ray / Sail paths (this is a controller-side config decision,
  upstream of backend selection).
