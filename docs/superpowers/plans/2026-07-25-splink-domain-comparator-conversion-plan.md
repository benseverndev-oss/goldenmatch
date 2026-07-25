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
