# Splink date/geo/numeric/array comparisons → GoldenMatch domain comparators

**Date:** 2026-07-25
**Status:** draft (design) — pending Ben's approval
**Predecessors:**
- Splink config converter — `specs/2026-07-13-splink-config-converter-design.md` (shipped 3.2.0)
- Splink migration upgrade pass — `specs/2026-07-14-splink-migration-upgrade-design.md`
- FS domain comparators — `specs/2026-07-23-fs-domain-comparators-design.md` (`date_diff`/`numeric_diff`/`geo_haversine` scorers)

## Problem

The `from_splink` converter (a faithful, data-free, parity-gated Python+TS surface) is the
trust anchor for Splink migration. Its documented gap: it **drops with a warning** every
comparison it cannot express as one of GoldenMatch's string-similarity scorer families —
specifically Splink's *domain* comparisons:

- `DateOfBirthComparison` / `AbsoluteDateDifferenceLevel` / `AbsoluteTimeDifferenceLevel`
- `DistanceInKMComparison` / `DistanceInKMLevel`
- absolute/percentage numeric-difference levels
- `ArrayIntersectAtSizesComparison` / `ArrayIntersectLevel`

Two of these gaps are now closable because GoldenMatch grew the matching scorers:
`date_diff`, `numeric_diff`, `geo_haversine` landed on the FS path 2026-07-23 (`date_diff` /
`geo_haversine` are native-kerneled, bucket IDs 17/18). The `array` family has **no** GM
scorer yet — closing it requires building one first.

This initiative turns "drop + warn" into "map + (best-effort) warn" for all four families,
adds a data-aware upgrade lever that tunes the emitted bands on the user's data, and does so
Python-first with the Rust kernels and TS port following in that order.

## Decisions (from brainstorming)

- **Scope:** all four families — date, geo, numeric, **and array** (which requires a new
  `array_intersect` scorer as a prerequisite). Array over **delimited strings**, not Arrow
  list columns (the FS scoring path is string-only; a true list-column type is out of scope).
- **Surface ordering:** Python first, then the Rust native kernels, then TS/other surfaces
  fall out of the shared core. The converter stays a `shared` surface; the api_parity
  *surface* gate (`convert_splink_config` exists on both) stays green throughout. Behavioral
  parity of the new mappings is reached when the TS phase lands; until then the TS converter
  continues to drop these families with a warning (a tracked, documented divergence).
- **Upgrade lever:** yes — a data-aware lever in `splink_upgrade.py` that measures the user's
  date/geo/numeric distributions and refines the emitted `level_thresholds`, reporting the
  before/after delta, alongside the existing TF/distance/calibration levers.
- **Fidelity posture:** best-effort + structured `ConversionReport`; `strict=True` raises on
  any lossy mapping (unchanged convention). Domain mappings are approximate by construction
  (see "Fidelity model") and always emit a `warning` finding.

## Known gap map (what converts vs what still drops)

**Converts (new):**
- Splink absolute date-difference levels → `date_diff` field.
- Splink `DistanceInKM` haversine levels → `geo_haversine` field over a derived `lat,long`
  column (via `derive_from` concat of the two source columns).
- Splink absolute/pct numeric-difference levels → `numeric_diff:abs:<eps>` / `:pct:<frac>`.
- Splink `array_intersect(...) >= n` levels → new `array_intersect` field (delimited strings).

**Still drops (warn), unchanged:**
- Cross-column string levels (`ColumnsReversedLevel`, name-transposition) — separate design.
- Arbitrary-SQL blocking (arithmetic/range/asymmetric).
- Array comparisons over true Arrow list columns without a delimited-string representation
  (documented data-shape requirement; the converter emits the field + a warning telling the
  user to materialize the array as a delimited string, e.g. `"a|b|c"`).
- `tf_adjustment_column` on a different column / `tf_adjustment_weight != 1.0`.

## Fidelity model (why every domain mapping warns)

The GM domain comparators return a **fixed-band** graded similarity from the *parsed domain
distance*; the bands live inside the scorer, not in config:

- `date_diff`: `same→1.0, ≤1d→0.92, ≤31d→0.80, ≤366d→0.60, ≤~5y→0.30, else 0`.
- `geo_haversine`: `≤0.1km→1.0, ≤1km→0.85, ≤10km→0.5, …`.
- `numeric_diff:abs:<eps>` / `:pct:<frac>`: graded within the band, `0` beyond.

Splink expresses these as **arbitrary exact cutoffs** (`≤ 30 days`, `≤ 1 year`, `≤ 10 km`,
`≤ 0.05` pct). So we cannot reproduce an arbitrary Splink cutoff precisely — the converter
maps the *set of Splink agree-cutoffs* to a `level_thresholds` list drawn from the GM
comparator's band values (snap each Splink cutoff to the GM band that contains it), preserving
the **number and ordering** of agree levels and the imported m/u per level, and emits a
`warning` finding stating the snap (`Splink ≤30 days → date_diff band ≤31 days (sim 0.80)`).
This is the same "best-effort + warn" class as the existing `_LEV_ASSUMED_LEN` levenshtein
approximation, and reuses the converter's existing threshold-dedup/merge machinery when two
Splink cutoffs snap to the same GM band (merge their imported m/u the way `import_em` does).

`numeric_diff` is the one family where the cutoff **can** be honored exactly: Splink's
`abs`/`pct` epsilon becomes the scorer's `:abs:<eps>`/`:pct:<frac>` parameter directly (no
snap for the band edge; only intermediate agree-levels snap). Recognizer records which mode.

## Architecture

Five stages. Stages A–D are Python; E is the Rust/TS port ordering.

### Stage A — new `array_intersect` scorer (prerequisite)

Mirrors the FS-domain-comparator wiring checklist (how `soundex_match`/`date_diff` thread the
system), over **delimited strings** (split on the field's delimiter, default `|`, also `;`/`,`):

- `config/schemas.py::VALID_SCORERS` — register `array_intersect` (+ optional
  `array_intersect:<delim>` / `array_intersect:jaccard` vs `:overlap` mode parse, like
  `numeric_diff`'s suffix).
- `core/scorer.py` — `_array_intersect_similarity_py(a, b, scorer)` scalar branch
  (`|A∩B| / |A∪B|` Jaccard by default, monotone in overlap; `None` on empty/null) +
  `_array_intersect_matrix` NxN, routed from `probabilistic._field_score_matrix`, parse-once
  on distinct values via the existing dedup collapse.
- `probabilistic.vectorized_scorer_supported` + `score_buckets._VEC_SUPPORTED` — add it.
- `core/autoconfig.py::build_probabilistic_matchkeys` — admit detected delimited-set columns
  behind the existing `GOLDENMATCH_FS_DOMAIN_COMPARATORS` flag (default OFF; converter output
  is unaffected by the flag — it emits explicit configs).
- Scale-neutral by the same proof as the other comparators (per-cell arithmetic inside a
  block; pair set / memory / EM / clustering untouched).

### Stage B — converter recognizers (`config/from_splink.py`)

Add `LevelKind`s `date_diff`, `geo_haversine`, `numeric_diff`, `array_intersect` and their
anchored recognizers to `recognize_level`, tolerant of DuckDB and Spark dialect spellings:

- **date:** `ABS(DATEDIFF('day'|'month'|'year', "c_l", "c_r")) <= n`, the Splink 4
  `EPOCH`/`EXTRACT(EPOCH …)` seconds form, and the custom `datediff("c_l","c_r") <= n` form.
  Convert the unit to days; record `(column, day_cutoff)`.
- **geo:** the haversine formula level `... acos(... radians("lat_l") ... "long_r" ...) * 6371
  <= km`. This is **cross-column** (lat + long, both sides). The recognizer extracts the four
  columns (`lat_l/lat_r/long_l/long_r` → source cols `lat`,`long`) and the km cutoff. Emits a
  *pending* geo band carrying both source columns (resolved in Stage C into one derived field).
- **numeric:** `ABS("c_l" - "c_r") <= eps` (abs) and the relative form
  `ABS("c_l"-"c_r")/NULLIF(GREATEST(ABS("c_l"),ABS("c_r")),0) <= frac` (pct). Record mode+eps.
- **array:** `array_length(list_intersect("c_l","c_r")) >= n` (DuckDB) /
  `size(array_intersect(...)) >= n` (Spark). Record `(column, min_intersect_n)`.

### Stage C — `convert_comparison` extensions

Two behavioral changes, both additive:

1. **Domain family selection.** A real `DateOfBirthComparison` mixes `damerau_levenshtein`
   (string-edit) levels with absolute-date-difference levels. Today mixed families → whole
   comparison dropped. New rule: if a comparison contains **any** domain-comparator levels,
   the domain family wins — build one field with the domain scorer, **drop the string-edit
   levels with a warning** (`DOB comparison: string-edit levels dropped in favor of date_diff`).
   The imported m/u for the kept (date) levels transfer; the dropped string-level m/u are
   discarded (warned). Precedence when >1 domain family appears (rare/malformed): drop the
   comparison + warn, as today.
2. **Cross-column geo resolution.** A geo comparison's pending band names two source columns.
   The field is emitted as `MatchkeyField(field="<lat>__<long>", scorer="geo_haversine",
   derive_from=["<lat>","<long>"], transform="concat:,")` (separator `,` to match
   `_parse_latlong`). Emit an `info` finding documenting the derived field + a `warning` that
   the source columns must be numeric lat/long. If the two sides reference inconsistent
   columns → drop + warn (existing convention).

Band→`level_thresholds` snapping per the Fidelity model; `numeric_diff` carries its
`:abs:/:pct:` parameter through the scorer string.

### Stage D — data-aware upgrade lever (`config/splink_upgrade.py`)

New lever `domain_bands` in the lever registry, running after `distance_thresholds` and
before `calibration` (it consumes the post-import model, produces refined config). For every
`date_diff`/`geo_haversine`/`numeric_diff` field in the converted config:

- Measure the post-transform distribution of the field on the seeded sample (day-diffs for
  date over blocked candidate pairs; km for geo; abs/pct for numeric).
- Refine `level_thresholds` so the emitted agree-bands sit at empirically meaningful
  separations of the match/non-match distance distributions (reuse the sample + candidate-pair
  enumeration the `calibration` lever already builds; no new full-frame pass).
- Copy-on-write (baseline config untouched); one finding per field with old→new bands and the
  distance evidence (percentiles, n pairs). Skips with a warning when the column is missing /
  the sample is empty / bands would collapse — levers never fail the pass (existing contract).
- Bare-settings input (`em_model is None`): skips with an info note (GM trains EM on the user's
  data at run time, which already sees these distributions) — mirrors the TF/calibration levers.

### Stage E — Rust then TS

- **Rust:** `array_intersect` + `numeric_diff` native kernels in `goldenmatch-score-core`
  (pyo3-free) + `wrap_pyfunction!` in `native/src/lib.rs`; `score_one` ids after 18; wheel-skew
  symbol guards; move from `scorer_kernels_deferred` → `scorer_kernels` in
  `parity/goldenmatch.yaml`. `date_diff`/`geo_haversine` kernels already exist. Byte-parity
  gate (native == pure) per `test_native_date_diff_geo_parity.py` pattern.
- **TS:** port the four scorers (bands mirrored, parity harness) to
  `packages/typescript/goldenmatch`, then the four converter recognizers to `from-splink.ts`.
  This closes the shared-surface behavioral divergence. Default flip (auto-config admission)
  follows the panel + QIS non-regression, per the v2-flag precedent.

## Surfaces

- **Library:** no new entry point — `from_splink` / `upgrade_splink_conversion` gain the
  mappings + lever. New scorer names are usable in any explicit config.
- **CLI:** `import-splink` unchanged in shape; the `--upgrade` path runs the new `domain_bands`
  lever; findings/delta tables include the new mappings.
- **MCP:** `convert_splink_config` returns the new mappings/findings inline (no shape change).

## Testing / success bar

1. **Recognizer unit tests** (`test_from_splink_recognizers.py`): per SQL shape, DuckDB +
   Spark spellings, positive + negative rows (garbage haversine, half-recognized numeric).
2. **Comparison conversion** (`test_from_splink_comparisons.py`): DOB mixed-family →
   single `date_diff` field + string-levels-dropped warning; `DistanceInKM` → derived
   `lat,long` `geo_haversine` field; numeric abs & pct; `array_intersect`. Golden config +
   expected findings. Trained-model m/u transfer to the kept domain levels (parity to 1e-9).
3. **New scorer** (`test_array_intersect_comparator.py`): scalar==vectorized parity, Jaccard
   bands, delimiter parse, null/empty→None, monotonicity.
4. **Upgrade lever** (`test_splink_upgrade_levers.py`): synthetic frames with known date/geo/
   numeric separations; copy-on-write invariants; skip paths; bare-settings skip.
5. **Native parity** (Stage E): `native == pure` for `array_intersect`/`numeric_diff`.
6. **Success bar (bakeoff):** build a Splink settings file exercising all four domain
   comparisons (DOB + a geo set), convert via `from_splink` + `--upgrade`, run converted-GM vs
   native Splink on the `bench_er_headtohead` harness; require **F1 delta ≤ 0.05** on a
   date-bearing and a geo-bearing dataset, and **no regression** on the existing non-domain
   panel datasets (Febrl3 / dblp_acm flat — no dates/geo).
7. **Scale-neutrality** (`qis_gate.py`): domain scorers ON, assert scale-invariance +
   absolute-floor + wall/RSS within noise (guards Stage A's structural claim).

## Phasing (deliverable increments, one PR each)

1. **P1** — `array_intersect` scorer (Stage A), Python only, behind the existing flag for
   auto-config; scalar+matrix+parity tests. (Unblocks the array converter mapping.)
2. **P2** — converter recognizers + `convert_comparison` for **date + numeric** (Stage B/C
   subset): no cross-column wrinkle, highest value. Golden tests + a bakeoff date fixture.
3. **P3** — converter geo (cross-column → derived field) + array mapping (Stage B/C rest).
4. **P4** — `domain_bands` upgrade lever (Stage D) + measurement delta.
5. **P5** — Rust kernels for `array_intersect`/`numeric_diff` (Stage E Rust).
6. **P6** — TS scorers + converter recognizers (Stage E TS); close the shared-surface gap.

## Out of scope

- True Arrow list-column type through blocking/scoring (array stays delimited-string).
- Cross-column string comparisons (`ColumnsReversedLevel`, name transposition) — separate design.
- Reverse direction (GoldenMatch → Splink export).
- Flipping the `GOLDENMATCH_FS_DOMAIN_COMPARATORS` auto-config default (governed by its own
  panel + QIS gate, independent of this converter work).
