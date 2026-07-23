# FS domain comparators (date / numeric / geo), scale-neutral by construction

**Status:** draft (design)
**Date:** 2026-07-23
**Scope:** the probabilistic (Fellegi-Sunter) path only. No change to weighted/DQbench.

## Problem

FS comparison levels are assigned from a single **generic string similarity** (`_levels_from_similarity`
buckets one number in [0,1]). Auto-config v2 admits `date` columns as `levenshtein` — a crude proxy:
`levenshtein("1990-01-02","1991-01-02")` is ~0.9 (looks like near-agreement) while `"1990-01-02"` vs
`"1990-02-01"` (day/month transposition — a common data-entry error, *should* be a strong partial) is
also ~0.8. The comparator can't see that one pair is 365 days apart and the other is a transposition.
The same blindness applies to numeric fields (no absolute/percentage bands) and lat/long (string
similarity on coordinates is meaningless; haversine distance is the signal). This is Splink's single
biggest quality lever and where GM is weakest, most visibly on person/DOB data (historical_50k, NCVR
birth_year) and any geo-bearing set.

## Hard constraint: it cannot affect the scale story

Scale-invariant correctness is the north star (the QIS gate, bounded memory, the 100M-in-9.2min
envelope). A quality change qualifies **only if** it leaves the pair set, memory footprint, EM shape,
clustering, and the distributed/native path unchanged. This design is chosen precisely because it
satisfies that by construction — see "Scale-neutrality proof" below. The alternative quality lever
(global transitivity-aware clustering / correlation clustering) was **rejected for exactly this
reason**: it needs the full weighted graph with driver-side materialization — the wall
`two_phase_wcc` hits when it "collects to driver and wedges the head at 100M" — so connected-components
stays the only clustering that survives the envelope. Comparators do not touch clustering at all.

## Definition

A **domain comparator** is a scorer that maps a *domain distance* between two parsed values to a
monotone graded similarity in `[0,1]`, so the existing level machinery (`comparison_vector` +
`_levels_from_similarity` + `level_thresholds`) buckets it identically to every other scorer. It is
**not** a new code path — it is a new entry in the scorer routing that already exists in two mirrored
places:

- scalar / EM E-step: `score_field(val_a, val_b, scorer)` (used by `comparison_vector`, `probabilistic.py:498`)
- vectorized block: `_field_score_matrix(vals, scorer)` → `_exact_/_soundex_/_fuzzy_score_matrix`
  (`probabilistic.py:2426`), with `_field_score_matrix_dedup` distinct-value collapse in front.

Both feed the SAME thresholds → the SAME m/u levels → the SAME weights. A comparator that returns a
`[0,1]` monotone similarity requires **zero** change to EM, weighting, calibration, missing-handling,
TF, monotonicity enforcement, or clustering.

### The three comparators

1. **`date_diff`** — parse both operands to an ordinal day count; distance = `|days_a − days_b|`.
   Similarity is a monotone non-increasing step over day-bands, tuned for person data:
   `same day → 1.0`, `≤1 day → 0.92`, `≤31 days → 0.80`, `≤366 days → 0.60`, `≤~5y → 0.30`, else `0.0`.
   Transposition guard: if the day/month swap (`YYYY-DD-MM`) yields distance 0, floor similarity at the
   `≤31-day` band (a MM/DD transposition is a partial, not a disagree). Unparseable / null → `None`
   (→ level −1/0 per `fs_missing_mode`), preserving today's missing semantics.

2. **`numeric_diff`** — parse to `float64`; `numeric_diff:abs:<eps>` (absolute band) or
   `numeric_diff:pct:<frac>` (relative band, `|a−b| / max(|a|,|b|,ε)`). Monotone: `0 → 1.0`, within band
   → graded, beyond → `0.0`. Handles measurements, amounts, ages.

3. **`geo_haversine`** — parse a `lat,long` pair (or two paired fields via a `record`-style concat);
   great-circle distance in km; monotone bands (`≤0.1km → 1.0`, `≤1km → 0.85`, `≤10km → 0.5`, …).

Bands are the *defaults*; a user (or auto-config) can override with explicit `level_thresholds` — the
comparator returns the raw graded similarity and the existing `level_thresholds` path buckets it, so the
knob surface is unchanged.

### Name transposition — explicitly deferred

Cross-column name swap (`first_a==last_b ∧ last_a==first_b`) and "one name is the initial of the other"
are **cross-field**, so they don't fit the single-field `score_field(a,b,scorer)` signature; they need a
composite comparator or a post-level bump reaching two columns. Auto-config v2 already drops redundant
name *composites* (the correlated-comparison fix), which captures part of this. Transposition is a
separate, higher-cost design (its own field-pair comparator) and is out of scope here — noted so the
comparator work isn't blocked on it.

## Where each comparator plugs in (the full wiring checklist)

Mirrors how `soundex_match` (a scorer with a numpy matrix but **no** native kernel) already threads the
system, so the bucket planner and fallbacks are known-good:

1. `config/schemas.py::VALID_SCORERS` — register the three names (+ the `:abs:`/`:pct:` suffix parse).
2. `core/scorer.py` — `score_field` scalar branch + a `_date_diff_matrix` / `_numeric_diff_matrix` /
   `_geo_haversine_matrix` (NxN, mirroring `_fuzzy_score_matrix`), routed from
   `probabilistic._field_score_matrix`. The parse-once step (string → day-ordinal / float / (lat,long))
   happens on the block's **distinct** values via the existing `_field_score_matrix_dedup` collapse, so
   a constant/low-cardinality field parses O(distinct), not O(rows).
3. `probabilistic.vectorized_scorer_supported` + `score_buckets._VEC_SUPPORTED` — add the three so the
   vectorized/bucket lanes take them (else they fall back to the per-pair loop, still correct).
4. `core/autoconfig.py::build_probabilistic_matchkeys` (behind `_fs_autoconfig_v2_enabled()` + a new
   sub-flag `GOLDENMATCH_FS_DOMAIN_COMPARATORS`, default OFF until the panel proves it): replace the
   `date → levenshtein` admission with `date → date_diff`; admit detected `lat/long` as `geo_haversine`;
   admit numeric-identity-ish columns as `numeric_diff`. Requires a lat/long `col_type` (today
   `_GEO_PATTERNS` matches zip/city, not coordinates — small classifier add).
5. **Native parity (the thesis path):** add `date_diff` / `numeric_diff` / `geo_haversine` string
   kernels to `goldenmatch-score-core` (pyo3-free) + register in `native/src/lib.rs`
   (`wrap_pyfunction!`), so the `bucket`/native route is byte-parity with the pure path. Until the
   kernel ships, the scorer runs the numpy matrix (native-absent fallback), exactly like
   `soundex_match` — so this is NOT a blocking dependency, and it satisfies `check_native_symbols` /
   `check_scorer_coverage` (the new scorers go in `scorer_kernels` once kerneled, or
   `scorer_kernels_deferred` with a `deferred --` reason meanwhile).
6. TS parity (`packages/typescript/goldenmatch`) — follow-up, mirror the bands (parity harness).

## Scale-neutrality proof (the point of the design)

Each claim is structural, not empirical:

- **Pair set unchanged.** Comparators live at the comparison-vector cell, *inside* a block. Blocking,
  candidate generation, and the `bucket` route are untouched → identical blocks, identical pairs at
  every scale. The QIS scale-invariance check (a larger rung's F1 must not fall below the smallest
  rung's) holds **by construction**: a comparator's output depends only on the two values, never on N,
  so its behavior at 1M is identical to 1K.
- **Memory footprint unchanged / lower.** The parsed representation (day-ordinal `int32`, `float64`,
  or two `float64`) is *narrower* than the source string, and parsing runs over `_field_score_matrix_dedup`
  distinct values — no new wide frame, no new driver-resident accumulator. The FS memory peaks
  (`build_blocks` for EM, `score_buckets` frame-residency) are unaffected: the block frames and their
  width are the same; only the per-cell scalar computation changes.
- **EM shape unchanged.** EM trains on the same sampled blocks (`GOLDENMATCH_FS_EM_SAMPLE_ROWS` cap);
  the comparator only changes the similarity→level mapping fed to the identical m-estimation loop. No
  extra passes, no extra sampling.
- **Clustering unchanged.** Still connected-components (union-find / native `connected_components`) over
  pairs above `link_threshold`, MST split for oversized. Comparators change *which* pairs clear the
  threshold (accuracy), never *how* clustering scales.
- **Distributed / native path unchanged.** No new driver materialization; the scorer dispatches
  per-field the same way; native-absent falls back to numpy (bucket-compatible). Nothing crosses the
  Ray boundary that didn't before.
- **cost is per-cell arithmetic, and cheaper.** A day-difference or float-subtract is *less* work than
  a `levenshtein` FFI call — so the scoring wall does not rise; if anything it drops on date/numeric
  fields.

Net: the change is confined to the value→similarity function. It can move F1 (the goal) but is
*mechanically incapable* of moving the pair count, the memory gates, or the scale-invariance gate.

## Correctness / gotchas

- **Monotonicity.** Similarity must be non-increasing in distance so `enforce_weight_monotonicity`
  (isotonic) and the level ordering stay well-behaved. Banded step functions are monotone by
  construction.
- **Missing = None, preserved.** Parse failure or null → `score_field` returns `None` → level −1
  (unobserved) or 0 per `fs_missing_mode`. A comparator must return `None`, never a sentinel distance,
  on unparseable input — otherwise a garbage date reads as a strong disagree instead of no-evidence.
- **Determinism + parity.** Native == pure byte-parity is the merge gate (mirror
  `tests/test_native_bloom_parity.py`); the numpy matrix == scalar `score_field` parity is asserted
  scorer-by-scorer like `_VEC_SUPPORTED` today.
- **TF interaction.** TF adjustment fires only on the exact-agreement top level; a `date_diff` exact
  agreement can still take TF if the field is skewed (rare exact DOB), or opt out — no special-casing
  needed, it flows through `_apply_tf_adjustment` unchanged.
- **Default OFF at ship.** `GOLDENMATCH_FS_DOMAIN_COMPARATORS=0` is byte-identical to today
  (auto-config keeps `date → levenshtein`); flip only on measured panel + QIS non-regression, per the
  v2-flag precedent.

## Benchmark plan

1. **Accuracy panel** (the standing FS harness): `scripts/bench_er_headtohead` on Febrl3,
   historical_50k, synthetic, dblp_acm, with a `comparators-off vs -on` diff
   (`compare_panels.py`, mirroring the `panel-v1-v2` lane). DOB-heavy sets (historical_50k, NCVR
   birth_year) are where `date_diff` should show the largest lift; dblp_acm should be flat (no dates).
   Ship gate: no F1 regression on any panel dataset, measurable lift on ≥1 DOB set.
2. **Scale-neutrality gate** (`scripts/qis_gate.py`): run the 50K/100K/500K/1M matrix with comparators
   ON; assert (a) scale-invariance still holds, (b) absolute-floor holds, (c) wall + peak RSS within
   noise of comparators-OFF. This is the explicit proof the design's structural claim survives
   measurement.
3. **Oversized-block behavior** (`bench-probabilistic` panel) — unchanged expectation; comparators
   don't touch block sizing, so this is a guard, not a target.
4. **Native parity** once kerneled: `native == pure` on a date/numeric/geo fixture in the native lane.

## Phased delivery

- **Phase 1:** `date_diff` (scalar + numpy matrix + parse-on-distinct) + `VALID_SCORERS` + vectorized
  support + auto-config `date` admission behind the flag. Accuracy panel + QIS gate. Highest-value,
  smallest surface.
- **Phase 2:** `numeric_diff` + `geo_haversine` (incl. the lat/long `col_type` classifier).
- **Phase 3:** native score-core kernels for all three (byte-parity gate) → `scorer_kernels`.
- **Phase 4:** TS parity port; flip the default after the panel + QIS non-regression.
- **Deferred:** name transposition (cross-field comparator, separate design).

## Non-goals

- Not touching the weighted/DQbench path or the clustering algorithm.
- Not a global relational/collective resolution change (that's the scale-hostile lever we rejected).
- Not name-transposition (cross-field, separate design).
