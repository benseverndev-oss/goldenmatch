# Plan — FS domain comparators, Phase 1: `date_diff`

**Spec:** `docs/superpowers/specs/2026-07-23-fs-domain-comparators-design.md`
**Scope:** ship the `date_diff` comparator (pure-Python scalar + vectorized numpy matrix), wire it into
FS scoring + vectorized/bucket lanes + auto-config `date` admission behind a default-OFF flag. Native
kernel is Phase 3; TS is Phase 4. Every task is failing-test → minimal-impl → commit.

## Invariants held throughout

- Default OFF: `GOLDENMATCH_FS_DOMAIN_COMPARATORS` unset ⇒ auto-config keeps `date → levenshtein`,
  byte-identical to today. The scorer itself is always *registered* (usable via explicit config) but
  auto-config only emits it under the flag.
- `date_diff` returns `float | None` in `[0,1]`, monotone non-increasing in day-distance, `None` on
  null/unparseable (so `fs_missing_mode` handles it exactly like any missing scorer).
- Scalar `score_field(a,b,"date_diff")` and the NxN `_date_diff_matrix` are byte-parity (asserted).
- Commands run from `packages/python/goldenmatch/`. Run tests with
  `python -m pytest <path> -q -p no:cacheprovider`.

---

## Task 1 — the comparator core (`_date_diff_similarity`) + parse

**Test first** — `tests/test_date_diff_comparator.py`:
- `_parse_date_ordinal("1990-01-02")` → an int day-ordinal; `"1990/01/02"`, `"19900102"`, `"1990-1-2"`
  parse equal; `"not a date"`, `None`, `""` → `None`.
- `_date_diff_similarity("1990-01-02","1990-01-02") == 1.0`
- same-day > 1-day > 31-day > 366-day > 5y > beyond, strictly non-increasing across the bands.
- **Transposition floor:** `_date_diff_similarity("1990-01-02","1990-02-01")` ≥ the `≤31-day` band value
  (MM/DD swap distance 0 → floored to a partial, not a disagree).
- unparseable/null operand → `None`.

**Impl** — `goldenmatch/core/comparators.py` (new module; keeps scorer.py lean):
- `_parse_date_ordinal(s: str | None) -> int | None` — tolerant parse (ISO, `/`-sep, compact `YYYYMMDD`,
  1-digit month/day). Pure stdlib `datetime`; return `None` on failure. Cache nothing (per-value, cheap).
- `_DATE_BANDS: tuple[tuple[int,float],...]` = `((0,1.0),(1,0.92),(31,0.80),(366,0.60),(1827,0.30))`,
  else `0.0`. Document the tuning rationale inline (person-data defaults; override via `level_thresholds`).
- `_date_diff_similarity(a, b) -> float | None`: parse both; `None` if either fails; `d=|oa-ob|`;
  transposition check (swap month/day on the *string* forms, re-parse, if that distance is 0 set
  `d=min(d,31)`); return the band value for `d`.

**Commit:** `feat(fs): date_diff comparator core (parse + banded similarity)`

---

## Task 2 — register the scorer + scalar `score_field` branch

**Test first** — extend `tests/test_date_diff_comparator.py`:
- `from goldenmatch.core.scorer import score_field` — `score_field("1990-01-02","1990-01-02","date_diff") == 1.0`;
  `score_field(None, x, "date_diff") is None`; unparseable → `None`.
- `"date_diff" in goldenmatch.config.schemas.VALID_SCORERS`.
- A `MatchkeyField(scorer="date_diff", levels=3, partial_threshold=0.6)` constructs without a
  validation error.

**Impl:**
- `config/schemas.py:21` — add `"date_diff"` to `VALID_SCORERS`.
- `core/scorer.py:173` `score_field` — add a branch: `if scorer == "date_diff": return _date_diff_similarity(val_a, val_b)`
  (import from `core.comparators`). Placed so it short-circuits before the generic fuzzy routing.

**Commit:** `feat(fs): register date_diff scorer + scalar score_field branch`

---

## Task 3 — vectorized NxN matrix + FS routing + vec-support

**Test first** — `tests/test_date_diff_comparator.py`:
- `_date_diff_matrix(vals)` for `vals=["1990-01-02","1990-01-02","1991-01-02", None]` equals, cell by
  cell, `score_field(vals[i], vals[j], "date_diff")` (with `None`→0.0 in the matrix, matching the
  `_fuzzy_score_matrix` null-mask convention; the `-1/0` level decision stays in `comparison_vector`).
- `probabilistic._field_score_matrix(vals, "date_diff")` returns that matrix.
- `probabilistic.vectorized_scorer_supported("date_diff") is True`.
- Parity: for a random list of parseable+null dates, `_field_score_matrix_dedup(vals,"date_diff")`
  equals the full `_date_diff_matrix(vals)` (distinct-collapse is bit-identical).

**Impl:**
- `core/scorer.py` — `_date_diff_matrix(vals: list[str|None]) -> np.ndarray`: parse each value ONCE to an
  ordinal array (+ a null mask), then vectorized `|oa-ob|` band lookup via `np.select` / `np.digitize`
  over the bands; null rows/cols → 0.0. (Transposition handled at parse time by storing both the
  ordinal and the swapped ordinal, then taking the min-distance — vectorized.)
- `probabilistic._field_score_matrix:2426` — route `if scorer == "date_diff": return _date_diff_matrix(vals)`
  before the fuzzy fallthrough.
- `probabilistic.vectorized_scorer_supported` — add `"date_diff"`.
- `backends/score_buckets.py:117` `_VEC_SUPPORTED` — add `"date_diff"` so the bucket vec lane takes it
  (native-absent path; correct either way).

**Commit:** `feat(fs): vectorized date_diff matrix + FS/bucket routing`

---

## Task 4 — end-to-end FS parity: date_diff levels == expected through EM + scoring

**Test first** — `tests/test_date_diff_comparator.py` (or extend `test_probabilistic.py`):
- Build a tiny frame with a `dob` column + an explicit `MatchkeyConfig` using
  `MatchkeyField(field="dob", scorer="date_diff", levels=3, partial_threshold=0.6)` alongside a name
  field. Train EM (fixed seed / `confidence_required=False`) and assert:
  - a same-DOB pair lands in the top level, a 1-year-apart pair in a lower level, byte-consistent
    between the scalar `comparison_vector` path and the vectorized `score_probabilistic_vectorized`
    path (the existing scalar-vs-vectorized parity assertion, extended to date_diff).
  - `fs_missing_mode`: a null-DOB pair → level −1 (unobserved) under default missing mode.

**Impl:** none expected beyond Tasks 1–3 (this is the integration guard). If the scalar/vectorized paths
disagree, fix the matrix null/transposition handling until parity holds.

**Commit:** `test(fs): end-to-end date_diff level parity (scalar == vectorized, missing)`

---

## Task 5 — auto-config admits `date → date_diff` behind the flag

**Test first** — `tests/test_fs_autoconfig_v2.py` (new cases):
- With `GOLDENMATCH_FS_DOMAIN_COMPARATORS` unset: `build_probabilistic_matchkeys` on a frame with a
  detected `date` column emits that field with `scorer="levenshtein"` (unchanged — byte-identical guard).
- With `GOLDENMATCH_FS_DOMAIN_COMPARATORS=1`: the same `date` column is emitted with
  `scorer="date_diff"` (levels/threshold carried through); non-date fields unchanged.

**Impl:**
- `core/autoconfig.py::build_probabilistic_matchkeys` — at the `col_type == "date"` admission site
  (near `_DATE_PATTERNS` usage), branch on a new `_fs_domain_comparators_enabled()` helper
  (`GOLDENMATCH_FS_DOMAIN_COMPARATORS` truthy) to pick `"date_diff"` vs the current `"levenshtein"`.
  Gate composes with `_fs_autoconfig_v2_enabled()` (v2 is the path that admits dates at all).

**Commit:** `feat(fs): auto-config admits date columns as date_diff (flag, default off)`

---

## Task 6 — scale-neutrality guard + docs

**Test first** — `tests/test_date_diff_comparator.py`:
- **Determinism:** `date_diff` scoring is a pure function — assert two runs of `_date_diff_matrix` on the
  same input are identical (guards against accidental N-dependence).
- (Scale-invariance itself is proven by `scripts/qis_gate.py`, run in CI, not a unit test — see below.)

**Impl / docs:**
- `packages/python/goldenmatch/CLAUDE.md` — one bullet under Auto-Config: the `date_diff` comparator +
  `GOLDENMATCH_FS_DOMAIN_COMPARATORS` flag (default off), pointing at the spec.
- `config/schemas.py` `VALID_SCORERS` doc / any scorer list that mirrors it (grep `VALID_SCORERS` +
  `SCORERS` const in `web/frontend/src/lib/types.ts` — **note only**, TS wiring is Phase 4).
- `parity/goldenmatch.yaml` — add `date_diff` to the `scorers` surface and to
  `scorer_kernels_deferred` with `deferred -- native score-core kernel is Phase 3` (so
  `check_scorer_coverage` passes: a scorer with no kernel must be classified).

**Commit:** `docs(fs): date_diff comparator flag + scorer-coverage deferral`

---

## Out-of-band validation (not unit tests — run before flipping the default, Phase 4)

- **Accuracy:** `scripts/bench_er_headtohead` off-vs-on on historical_50k / febrl3 / synthetic /
  dblp_acm via `compare_panels.py`. Gate: no F1 regression anywhere, measurable lift on ≥1 DOB set.
- **Scale-neutrality:** `python scripts/qis_gate.py` (or the `bench-quality-scale.yml` lane) at
  50K/100K/500K/1M with the flag ON — assert scale-invariance + absolute-floor hold and wall/peak-RSS
  are within noise of OFF. This is the measured proof of the spec's structural claim.
- **Oversized blocks:** `bench-probabilistic` panel unchanged (guard).

## Definition of done (Phase 1)

- `date_diff` usable via explicit config on every FS route (scalar / vectorized / bucket), native-absent
  fallback correct; scalar == vectorized parity gated.
- Auto-config emits it for `date` columns only under `GOLDENMATCH_FS_DOMAIN_COMPARATORS=1`; default OFF
  is byte-identical to today.
- `check_scorer_coverage` green (deferred kernel entry); full pytest green.
- Spec's scale claim has a runnable proof (`qis_gate` with the flag on).
