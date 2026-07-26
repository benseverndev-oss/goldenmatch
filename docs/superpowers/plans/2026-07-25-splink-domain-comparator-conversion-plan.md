# Splink domain-comparator conversion — implementation plan & handoff

**Date:** 2026-07-25
**Design spec:** `docs/superpowers/specs/2026-07-25-splink-domain-comparator-conversion-design.md`
**Branch:** `claude/splink-conversion-improvements-g1ueqq`
**Status:** P1 shipped (committed + pushed). P2 in progress (recognizers not yet written). This
doc is the handoff for continuing in a fresh session.

---

## 0. Where things stand

| Phase | State | Commit |
|---|---|---|
| Spec | done | `9ebe2d3` |
| P1 `array_intersect` scorer | **done, tested, pushed** | `9ebe2d3` |
| conftest polars removal (user-requested) | **done, pushed** | `bbd8c87` |
| P2 date + numeric converter | not started (design nailed below) | — |
| P3 geo + array converter | not started | — |
| P4 `domain_bands` upgrade lever | not started | — |
| P5 Rust kernels (array_intersect, numeric_diff) | not started | — |
| P6 TS scorers + converter | not started | — |

**Two commits are on the branch and pushed to origin.** Nothing is lost if the container is
reclaimed.

### BLOCKED: draft PR not created
The GitHub MCP server returned `Error POSTing to endpoint: invalid session` on **every** call for
the whole session (`list_pull_requests`, `create_pull_request`, `get_me`). The agent proxy is
healthy (`curl "$HTTPS_PROXY/__agentproxy/status"` shows `recentRelayFailures: []`). A `/mcp
reconnect github` attempt reported MCP controls unavailable. No GitKraken MCP server is registered
(`ToolSearch "+gitkraken"` → none). There is no `gh` CLI in this environment.

**First action next session:** create the draft PR for the pushed branch. Title + body are in
§6 below (verbatim, ready to paste). Confirm none exists first
(`list_pull_requests head=benseverndev-oss:claude/splink-conversion-improvements-g1ueqq state=all`).

---

## 1. Environment / commands (all from repo root `/home/user/goldenmatch`)

- **Bash cwd resets between calls** — always use absolute paths or `cd` inside the one command.
- Run tests: `uv run --group dev python -m pytest packages/python/goldenmatch/tests/<file> -q`
  (the `dev` group — pytest etc. — lives in the **root** `pyproject.toml`, not the package).
- goldenmatch is **polars-free by design** (pyarrow is the hard dep; polars is an optional extra).
  Most test modules `import polars` at module scope and need it: add `--with "polars>=1.0"`.
  Collection-wide, polars-free tests (`tests/nopolars/`, the new comparator tests) now work
  because conftest no longer imports polars (commit `bbd8c87`).
- Lint (CI runs full select incl. import-sort `I`): `uv run --group dev ruff check <files>`.
- Manifest parity/coverage check without the heavy mcp surface:
  ```
  uv run --group dev python -c "import yaml,sys; sys.path.insert(0,'scripts'); import check_api_parity as c; m=yaml.safe_load(open('parity/goldenmatch.yaml')); print(c.check_structure(m)+c.check_scorer_coverage(m))"
  ```
  (`scorers.python_only` MUST stay alphabetically sorted or `check_structure` fails.)
- **Generate ground-truth Splink SQL** (splink is pip-installable here):
  `uv run --with "splink" python -c "..."` — see §3 for the exact shapes already captured.

---

## 2. What P1 delivered (reference for the pattern)

`array_intersect` FS domain comparator over delimited strings ("a|b|c" → token set; Jaccard
default, `array_intersect:overlap` = overlap coefficient). Mirrors date_diff/numeric_diff/geo:
- `config/schemas.py`: added to `VALID_SCORERS`; `_ARRAY_INTERSECT_RE = ^array_intersect:(jaccard|overlap)$`; `_is_valid_scorer` updated.
- `core/scorer.py`: `_parse_token_set`, `_parse_array_intersect_mode`, `_array_intersect_similarity_py` (empty side → exact-string fallback, never None → scalar==vectorized); branch in `score_field` AND in `_fuzzy_score_matrix` (the O(n²) matrix loop, same shape as the date_diff branch ~line 801).
- `parity/goldenmatch.yaml`: `scorers.python_only` + a `scorer_kernels_deferred` entry (`deferred -- ... P5`). **Required** — the `check_scorer_coverage` floor fails a scorer that is neither kernel-backed nor deferred.
- `tests/test_array_intersect_comparator.py` — 14 tests incl. scalar==vectorized parity.

**The domain comparators are Python-path only; the env flag `GOLDENMATCH_FS_DOMAIN_COMPARATORS`
gates only AUTO-CONFIG admission, NOT scorer routing** — so a converted explicit config emitting
these scorers runs regardless of the flag. Confirmed by reading `core/autoconfig.py:782`.

---

## 3. Real Splink SQL shapes (CAPTURED — build recognizers against these, don't guess)

Generated with splink 4 via `cl.<Comparison>(...).get_comparison('duckdb'|'spark').as_dict()`.
The bench has NO date/geo/array Splink fixtures, so these are the ground truth for P2/P3 tests.

### Date — `AbsoluteDateDifferenceAtThresholds` / `DateOfBirthComparison`
Comparison levels use the **`_l`/`_r` suffix** style; identifiers `"col"` (DuckDB) / `` `col` `` (Spark).
```
# null (is_null_level=True):
DUCKDB: try_strptime("dob_l", '%Y-%m-%d') IS NULL OR try_strptime("dob_r", '%Y-%m-%d') IS NULL
# exact:
"dob_l" = "dob_r"
# DateOfBirthComparison ALSO has a string-edit level (domain-family-wins DROPS this):
damerau_levenshtein("dob_l", "dob_r") <= 1
# date-diff levels — EPOCH SECONDS, float thresholds:
ABS(EPOCH(try_strptime("dob_l", '%Y-%m-%d')) - EPOCH(try_strptime("dob_r", '%Y-%m-%d'))) <= 2629800.0
   # 2629800 s = 1 month (30.44 d); 31557600 = 1 year; 315576000 = 10 years
ELSE
# Spark form: ABS(UNIX_TIMESTAMP(date(try_to_timestamp(`dob_l`,'yyyy-MM-dd'))) - UNIX_TIMESTAMP(...)) <= 2629800.0
```
**Recognizer:** match `ABS( EPOCH(...<col>_l...) - EPOCH(...<col>_r...) ) <= <seconds>` and the Spark
`UNIX_TIMESTAMP(...)` variant. Extract `<col>` (inside `try_strptime("<col>_l", ...)` / the backtick
form) and `<seconds>`. `days = seconds / 86400`. Emit `RecognizedLevel(kind="date_diff", column=col,
sim_threshold=_date_diff_band(round(days)), approx=True)`.

### Geo — `DistanceInKMAtThresholds` (CROSS-COLUMN: lat + lng)
```
# null:
("lat_l" IS NULL OR "lat_r" IS NULL) OR ("lng_l" IS NULL OR "lng_r" IS NULL)
# levels (multiline; normalize whitespace before matching):
cast( acos( case when (sin(radians("lat_l"))*sin(radians("lat_r")) +
  cos(radians("lat_l"))*cos(radians("lat_r"))*cos(radians("lng_r" - "lng_l"))) > 1 then 1
  ... else (...) end ) * 6371 as float ) <= <km>
ELSE
```
**Recognizer:** detect the haversine signature (`acos` … `radians` … `* 6371`), extract the lat col
and lng col (suffix style) and the `<km>` cutoff. This is the ONE cross-column case — the current
`recognize_level` returns a single-column `RecognizedLevel`; geo needs a 2-column carrier (extend
`RecognizedLevel` with an optional second column, or special-case haversine in `convert_comparison`
before the single-column path). Emit `MatchkeyField(field="<lat>__<lng>", scorer="geo_haversine",
derive_from=["<lat>","<lng>"], transforms=[concat with "," separator])`. `sim_threshold =
_geo_haversine_band(km)`. **VERIFY** the `derive_from` + concat-separator semantics in
`config/schemas.py` (search `derive_from`, ~line 360, "must name at least 2 source columns to
concatenate") and that the concat joins with "," to match `_parse_latlong` (`,` or `;`).

### Array — `ArrayIntersectAtSizes`
```
# null:
"skills_l" IS NULL OR "skills_r" IS NULL
# levels — intersect COUNT thresholds:
array_length(list_intersect("skills_l", "skills_r")) >= 3      # DuckDB
SIZE(ARRAY_INTERSECT(`skills_l`, `skills_r`)) >= 1             # Spark
ELSE
```
**FIDELITY FORK (needs a decision — flag to Ben):** Splink uses an absolute intersection **count**
(`>= n`); `array_intersect` returns a **ratio** (Jaccard/overlap), so there is no exact count→ratio
map without knowing set sizes at convert time (same class as the levenshtein distance→sim gap).
Options: (a) emit `array_intersect:overlap` and snap each `>= n` to an approximate ratio threshold +
warn; (b) add a count-mode to the scorer (`array_intersect:count:<n>` returning a banded sim on the
raw count). Recommend (a) for the converter + a warn; revisit (b) only if measured recall needs it.

### Numeric
Splink's standard library has **no** generic `AbsoluteDifferenceAtThresholds` (the list is: date,
time, DistanceInKM, ArrayIntersect, Cosine, DistanceFunction, DamerauLevenshtein, custom, exact,
name specializations, Jaccard/Jaro/JaroWinkler/Levenshtein, Email, Postcode). Numeric magnitude
comparisons come via `CustomComparison`/`DistanceFunctionAtThresholds` with arbitrary SQL. So pure
numeric is **rare and shape-unpredictable** — recommend scoping P2 to **date** (clean, high value)
and folding numeric into a best-effort `ABS("c_l" - "c_r") <= eps` recognizer only if a real
CustomComparison shows up. **This is a scope adjustment vs the spec's "P2 = date + numeric"** — get
Ben's nod, or just ship date in P2 and note numeric as opportunistic.

---

## 4. P2 design (date), nailed down

The elegant bit: because `date_diff`'s output IS the fixed band set (1.0/0.92/0.80/0.60/0.30/0.0),
mapping each Splink second-cutoff through `_date_diff_band(days)` produces `level_thresholds` that
align exactly with the scorer's output. Worked example (DObComparison, cutoffs 1mo/1yr/10yr):
`level_thresholds = [1.0(exact), 0.80(≤1mo→_date_diff_band(30)), 0.60(≤1yr→_date_diff_band(366))]`
→ 4 levels. A real 1-day-apart pair scores date_diff=0.92 → buckets into the ≤1-month level (0.92 ≥
0.80). Correct by construction. Approximation = Splink's exact day-cutoff becomes GM's band boundary
(warn, `approx=True`, mirroring the levenshtein convention).

**Implementation checklist (P2):**
1. `from_splink.py` `recognize_level`: add `date_diff` recognizer (regexes for the EPOCH-seconds +
   Spark UNIX_TIMESTAMP forms; whitespace-normalize). Add `LevelKind` `"date_diff"`.
2. `convert_comparison`: **domain-family-wins rule** — if any recognized band is a domain kind
   (`date_diff`/`geo_haversine`/`array_intersect`/`numeric_diff`), drop the non-domain bands
   (string-edit) with a warn and build ONE field with the domain scorer. (Today mixed families →
   whole comparison dropped; this is the behavioral change. Keep the existing single-domain-family
   assertion: >1 DISTINCT domain family in one comparison → drop + warn.)
3. `import_em`: `recognize_level` already re-runs per level, and `_agree_index_for(r, fld)` maps a
   band's `sim_threshold` to the field's `level_thresholds` index. Since the recognizer emits the
   SAME snapped `_date_diff_band` value both when building the field AND during import, indices
   align automatically. **READ `_agree_index_for`** (referenced `from_splink.py:727`, not yet read
   this session) to confirm it compares `sim_threshold` membership and handles the domain kinds —
   it likely needs the recognized kind to match the field scorer, or just threshold membership.
4. Tests `tests/test_from_splink_comparisons.py` (or a new `test_from_splink_domain.py`): use the
   EXACT SQL strings from §3. Assert DOB → single `date_diff` field, string-edit level dropped with
   a warn, `level_thresholds` correct, and (trained fixture) m/u land on the right indices.
5. Success-bar bakeoff fixture: a Splink settings JSON with a DOB comparison, converted + run vs
   native Splink on a date-bearing dataset (F1 delta ≤ 0.05). Script via `bench_er_headtohead`, not CI.

---

## 5. Key files (line anchors as of this session)

- `packages/python/goldenmatch/goldenmatch/config/from_splink.py`
  - `recognize_level` L79-128 (regex table `_NULL_RE/_EXACT_RE/_SIM_RE/_DIST_RE` L49-59)
  - `convert_comparison` L172-330 (family selection L233-247, band→level_thresholds L280-327)
  - `import_em` L634-830 (`_agree_index_for` call L727 — READ that helper)
  - `from_splink` top-level assembly L1036-1126 (survivors → mk → `import_em(survivors,...)`)
- `packages/python/goldenmatch/goldenmatch/core/scorer.py`
  - domain comparators L228-373 (`_date_diff_band`, `_date_diff_similarity_py`, `_geo_haversine_*`, `_parse_latlong`); array_intersect added just above `score_field` (~L376)
  - `score_field` routing L376-393; `_fuzzy_score_matrix` per-scorer branches L796-846
- `packages/python/goldenmatch/goldenmatch/config/schemas.py` L21-96 (VALID_SCORERS, scorer regexes, `_is_valid_scorer`); `derive_from` concat ~L360
- `packages/python/goldenmatch/goldenmatch/config/splink_upgrade.py` (P4 lever registry; add `domain_bands`)
- `parity/goldenmatch.yaml` scorers surface L261-284, `scorer_kernels` L360-386, `scorer_kernels_deferred` L396+
- `packages/python/goldenmatch/backends/score_buckets.py` `_NATIVE_SCORER_IDS` L227 (P5 adds array_intersect/numeric_diff ids), `_resolve_score_pair_callable` L538 (add array_intersect per-pair callable if a weighted-bucket config uses it)

---

## 6. Draft PR (ready to create — verbatim)

**Title:** `Splink domain-comparator conversion: date/geo/numeric/array (P1: array_intersect scorer)`
**Head:** `claude/splink-conversion-improvements-g1ueqq`  **Base:** `main`  **Draft:** true

**Body:** (the full body was drafted this session; regenerate from the P1 changes + this plan.
Key points: what/why = close from_splink's dropped-domain-comparison gap building on the 2026-07-23
FS scorers; P1 = array_intersect scorer + parity manifest + conftest polars fix; testing = 14 + 82
+ nopolars + polars-present ingest all green, ruff clean, coverage gate green; follow-ons P2-P6.
End with the Claude Code attribution footer.)

---

## 7. Decisions (RESOLVED 2026-07-25)
1. **Array count→ratio fidelity** → **(a) approximate-overlap-ratio + warn** (`approx=True`).
   Consistent with the existing levenshtein distance→sim snap; a `array_intersect:count:<n>`
   scorer mode is a disproportionate new public surface for a rare comparison and its output
   isn't a normalized similarity. P3 implementation note: snap `>= n` using **overlap-coefficient**
   semantics, bias the threshold LOW to preserve recall, warn naming the approximation. Revisit
   (b) only if a measured recall gap appears.
2. **Numeric scope** → **dropped from P2, opportunistic.** Splink has no standard generic
   numeric-difference comparison (magnitude only via `CustomComparison`/`DistanceFunction` with
   arbitrary SQL), so a recognizer with no ground truth is speculative. **P2 = date recognizer
   only.** Add a best-effort `ABS("c_l" - "c_r") <= eps` → `numeric_diff` recognizer only when a
   real `CustomComparison` sample appears.
3. Everything else in the spec is already approved.

Revised roadmap: **P2 = date recognizer (DONE — this branch)**, **P3 = geo + array
(approx-overlap-ratio)**, numeric opportunistic, P4-P6 as scoped.

## 8. P2 delivered (2026-07-25)
`date_diff` recognizer in `from_splink.py`, built against the REAL Splink 4 SQL (both dialects):
- `recognize_level`: new `date_diff` kind. `_DATE_DIFF_MARKER`/`_DATE_DIFF_TAIL`/`_DATE_DIFF_COL`
  match `ABS(EPOCH(try_strptime(...)) - ...) <= <secs>` (DuckDB) and the Spark
  `UNIX_TIMESTAMP([date(]try_to_timestamp(...))` forms (bare + `date()`-wrapped). `days =
  round(seconds / 86400)`; `sim = _date_diff_band(days)` (imported from `core.scorer` — one source
  of truth, no band drift), `approx=True`.
- Shared column atom `_COL_L`/`_COL_R` extended to accept Spark backticks, so exact/sim/dist/null
  levels also recognize under Spark (a bonus beyond date).
- `convert_comparison`: **domain-family-wins** rule (`_DOMAIN_KINDS` = date_diff/geo_haversine/
  array_intersect/numeric_diff). A domain band + string-edit bands in one comparison → keep the
  domain family + exact, drop the string-edit bands with a warn (DObComparison's damerau level).
  >1 DISTINCT domain family → drop the whole comparison (defensive; not triggerable until a second
  domain recognizer lands). The 10yr DOB band snaps to 0.0 → dropped by the existing out-of-range
  guard, so DObComparison yields 4 levels [1.0, 0.80, 0.60].
- `import_em`: unchanged — the dropped damerau/10yr levels' m/u resolve to no matching threshold
  and are dropped + re-normalized (asserted in `test_from_splink_domain.py`).
- Tests: `tests/test_from_splink_domain.py` (13). No parity-manifest change (`date_diff` is an
  existing scorer from the 2026-07-23 FS work; P2 only adds a *recognizer*).

## 9. P3a delivered — array_intersect recognizer (2026-07-25)
`ArrayIntersectAtSizes` (`array_length(list_intersect(...)) >= n` DuckDB /
`SIZE(ARRAY_INTERSECT(...)) >= n` Spark) now recognized. Per the resolved decision (7.1):
- `_ARRAY_INTERSECT_LEVEL_RE` extracts column + count. Count `>= n` snaps to overlap ratio
  `min(1.0, n / _ARRAY_ASSUMED_SET_SIZE)` (`_ARRAY_ASSUMED_SET_SIZE = 10`, biased low for recall),
  emits scorer `array_intersect:overlap` (P1's scorer), `approx=True` + a warn naming the snap.
- `RecognizedLevel` gained an optional `scorer` override (kind stays `array_intersect` for the
  single-domain-family check; the scorer STRING carries the `:overlap` mode). `convert_comparison`
  resolves the field scorer from that override, else the family kind.
- `ArrayIntersectAtSizes([3,1])` → `array_intersect:overlap`, 3 levels `[0.3, 0.1]`.
- Tests added to `test_from_splink_domain.py` (recognizer both dialects, conversion, snap warn,
  trained import, + the now-triggerable two-distinct-domain-families guard). No parity-manifest
  change (`array_intersect` is P1's existing scorer). Full splink suite 166 green; polars-free.

## 10. Geo (geo_haversine) — BLOCKED on a schema decision (P3b)
Deferred from P3a: the plan (3) assumed a `MatchkeyField` with `derive_from=[lat,lng]` + a comma
concat. VERIFIED that assumption is WRONG in two ways:
1. `derive_from` lives on `NegativeEvidenceField`, NOT `MatchkeyField` — the converter emits
   `MatchkeyField`, which has no synthesized-field mechanism.
2. `NegativeEvidenceField.derive_from` **space-joins** its sources, but `geo_haversine`'s
   `_parse_latlong` requires a **comma/semicolon** separator ("lat,long"). Space-join wouldn't parse.
So geo is NOT just a recognizer — `geo_haversine` scores ONE combined "lat,long" field, while Splink
gives two SEPARATE columns. Making it work end-to-end needs a NEW `MatchkeyField` synthesized-field
mechanism (`derive_from` + a `,` separator) AND pipeline materialization of that field before
scoring. That's a cross-cutting schema + pipeline feature (higher-risk, own tests + parity), not a
recognizer. **Decision needed from Ben** (see the session hand-off): (a) add the derive_from+separator
plumbing to MatchkeyField as P3b, or (b) recognize the haversine level but emit it as a DROPPED
comparison with a clear "cross-column geo not yet representable" warning until the plumbing lands.

## 11. P3b delivered — geo_haversine + MatchkeyField.derive_from (2026-07-25)
Ben chose "add the derive_from plumbing." `DistanceInKMAtThresholds` now converts, via a NEW
synthesized-field mechanism on `MatchkeyField`:
- **Schema:** `MatchkeyField.derive_from: list[str] | None` + `derive_separator: str = " "` (validated
  >=2 sources). Splink gives separate lat+lng; geo_haversine scores one "lat,long" field, so the
  converter emits `derive_from=[lat,lng]`, `derive_separator=","`.
- **Materialization (BOTH lanes):** `precompute_matchkey_transforms` (polars) + `..._frame` (arrow,
  the goldenmatch default) now synthesize `MatchkeyField.derive_from` fields, not just NE ones,
  honoring the per-field separator. Threaded a `separator=` param through `Column.derive_ne_joined`
  (protocol/polars/arrow) + `arrow_derive.ne_joined_column` (default " " keeps NE byte-identical).
- **Required-columns:** `pipeline._get_required_columns` requires a derive_from field's SOURCES, not
  the synthesized name (else the raw-frame column check fails on `lat__lng`).
- **Recognizer:** `_GEO_MARKER`/`_GEO_LAT`/`_GEO_LNG`/`_GEO_KM` match the haversine signature
  (`acos ... radians ... * 6371 ... <= km`) both dialects; lat = the bare `radians(col_l|col_r)`,
  lng = the `radians(col_r - col_l)` difference, km -> `_geo_haversine_band`. `RecognizedLevel` gained
  `derive_from`/`derive_separator`; `convert_comparison` threads them into the MatchkeyField.
  `DistanceInKM([1,10])` -> `geo_haversine`, field `lat__lng`, levels `[0.85, 0.5]`, derive `,`.
- **Tests:** `test_from_splink_geo.py` (13) incl. an END-TO-END `dedupe_df` proving nearby coords
  cluster + far coords don't (the synthesized field really scores). NE space-join unchanged
  (`test_facility_fullname_ne` green). Full affected suites 319 green; polars-free; codemap +
  config-matrix + agent-manifest regenerated. No parity-manifest change (geo_haversine is an
  existing scorer).

**Roadmap now:** P1 array_intersect scorer + P2 date_diff + P3a array_intersect recognizer + P3b
geo/derive_from all shipped. Numeric = opportunistic. Remaining: P4 `domain_bands` upgrade lever,
P5 Rust kernels (array_intersect/numeric_diff), P6 TS parity.

## 12. P4 delivered — domain_bands upgrade lever (2026-07-25)
New `domain_bands` lever in `config/splink_upgrade.py`, registered after `distance_thresholds`,
before `fan_out`/`calibration` (Stage D). Data-aware refinement of the converter's approximate
domain bands:
- **array_intersect** (the one data-refinable approximation): P3a snapped Splink's intersection
  COUNT `>= n` to an overlap RATIO `n / 10` (`_ARRAY_ASSUMED_SET_SIZE`, a guess).
  `_lever_domain_bands` measures the ACTUAL mean token-set size K on the sample
  (`_measure_mean_token_set_size`, reusing `core.scorer._parse_token_set`) and recomputes each band
  `ratio = min(1, n / K)` — exactly mirroring `distance_thresholds` measuring mean string length L.
  Copy-on-write; per-band finding (old→new + measured K); band-collapse merges + re-normalizes m/u
  (same machinery as `distance_thresholds`); applies regardless of `em_model` (config-level bands).
- **date_diff / geo_haversine**: the Splink cutoff→band conversion is EXACT (seconds→days / km carry
  no assumed constant), so there's nothing to refine — info-note and passthrough.
- **`_validate_columns` fix**: also taught the upgrade orchestrator's upfront column check to require
  a `derive_from` field's SOURCES (geo lat+lng), not the synthesized `lat__lng` — mirrors the P3b
  `pipeline._get_required_columns` fix, so a geo-converted config can now run `--upgrade`.
- Tests: `test_splink_upgrade_levers.py` (+7: refine, copy-on-write, skip-empty, collapse+m/u remap,
  date/geo exact passthrough, bare-settings applies) + updated the two lever-order assertions.
  Full upgrade+splink suites 128 green; ruff + pyright clean; codemap regenerated.

**Design-scope note:** the spec (Stage D) also envisioned refining date/geo bands from measured
day-diff/km *distributions*. Those conversions are exact (no assumed constant to fix), and an
unlabeled distance distribution has no match/non-match separation signal to refine against without
the scored candidate-pair machinery — so P4 ships the well-defined array-set-size measurement (the
genuine refinable approximation) and treats date/geo as exact passthrough. A distribution-based
refinement remains a possible follow-on if a labeled/scored separation signal is wired in.

**Roadmap now:** P1/P2/P3a/P3b/P4 shipped. Remaining: P5 Rust kernels (array_intersect/numeric_diff),
P6 TS parity. Numeric stays opportunistic.

## 14. numeric_diff Splink recognizer delivered (2026-07-25)
The converter side of numeric (the "opportunistic" gap the kernel PRs don't touch). Splink has no
first-class numeric comparison; magnitude arrives via a `CustomComparison` whose canonical shape —
captured live from splink 4, **byte-identical DuckDB + Spark** — is `ABS("c_l" - "c_r") <= <eps>`.
- **Recognizer** (`from_splink.py`): `_NUMERIC_DIFF_RE` (`ABS(col_l - col_r) <= n`, case-insensitive,
  quoted/backtick/bare cols) + a `recognize_level` branch + `numeric_diff` added to `LevelKind`.
  Emits `numeric_diff:abs:<band>` with **band = 2·eps** and **sim_threshold = 0.5**: a pair exactly
  at the cutoff (dist = eps) scores `1 − eps/(2eps) = 0.5`, so under the `>=` level semantics
  "score ≥ 0.5 ⟺ dist ≤ eps" reproduces `<= eps` EXACTLY (boundary inclusive) — mirroring
  date_diff's "threshold = the score a pair at the cutoff earns". Placed after the haversine branch;
  the date_diff gate (needs epoch/unix_timestamp) correctly declines a bare ABS, so no shadowing.
- **Deterministic per level** (band + threshold from that level's OWN eps, no cross-level state), so
  `recognize_level` yields the same 0.5 at field-build AND m/u-import time and `_agree_index_for`
  aligns automatically — dodging the parity trap that cross-level band-normalization would create.
- **Multi-cutoff**: every numeric level snaps to sim 0.5, so multiple Splink cutoffs collapse to ONE
  numeric_diff level; `convert_comparison` keeps the LOOSEST band (largest 2·eps, recall-biased) and
  warns. Single-cutoff (the common case) is exact.
- **Scope**: only the ABSOLUTE form; a relative/pct `CustomComparison` (`ABS(a-b)/GREATEST(…) <= f`)
  is too shape-variable and falls through (dropped + warned), matching today's behavior. `approx=True`
  + a per-level "band = 2*eps" warn surfaces the ramp-vs-cutoff approximation.
- Single-cutoff → field `numeric_diff:abs:<2eps>`, **3 levels [1.0, 0.5]** (exact / within-eps / else).
  Trained m/u import round-trips (all levels survive, no dropped-mass warn). Scorer schema-valid
  (`_is_valid_scorer`). from_splink stays polars-free.
- Tests: `test_from_splink_numeric.py` (18) + updated one assertion in `test_from_splink_domain.py`
  (a bare ABS is now numeric_diff, not None). Full from_splink suite 166 green; ruff + pyright clean;
  codemap regenerated. No parity-manifest/config-matrix change (recognizer only, `numeric_diff` was
  already a VALID_SCORERS member from the FS domain-comparator work).

**Roadmap now:** the converter recognizes all four domain families (date_diff, geo_haversine,
array_intersect, numeric_diff). Kernels: array_intersect (#2134) + date_diff/geo (#2130) shipped;
numeric_diff kernel is #2138 (in flight). Remaining: P6 TS *converter* parity (the recognizers are
Python-only; the TS port has its own `fromSplink`).
