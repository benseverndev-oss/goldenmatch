# Quality-Aware Blocking — design spec

Date: 2026-06-07
Branch: `claude/goldenmatch-quality-aware-blocking` (stacked on `claude/goldenmatch-quality-survivorship` → `claude/goldencheck-rust-expansion-NmjWl`)
Status: PROPOSED — spec for review before implementation.

This is **Door #1** of the GoldenCheck → GoldenMatch integration map. Phase 4
(PR #794) built the channel (per-cell quality → survivorship). This door uses
GoldenCheck's column-quality signal to improve **blocking-key selection**, which
sets the recall ceiling of the whole ER run.

---

## 1. Problem & hypothesis

Blocking decides which record pairs are ever compared. A pair that never shares
a block can never match — so the blocking key is the hard ceiling on **recall**,
the dominant ER failure mode.

GoldenMatch chooses blocking keys (`core/autoconfig.py::build_blocking`, line
~1407) from two profile signals:
- `null_rate ≤ 0.20` (`NULL_RATE_CEILING`, line ~1438) — high-null keys shard
  records into a giant null block / leave nulls unblockable.
- a cardinality ceiling (`blocking_candidates.DEFAULT_BLOCKING_MAX_RATIO = 0.5`)
  — near-unique keys produce singleton blocks.

**It is blind to a third recall-killer: value fuzziness.** If `state` holds
`California` / `Californa` / `CALIFORNIA`, those records hash to *different*
exact blocks. The two records that should match never get compared — recall is
lost *before scoring runs*, and no threshold tuning can recover it.

GoldenMatch already lowercases/strips blocking keys (`BlockingKeyConfig.
transforms = ["lowercase","strip"]`), so **case/whitespace** variants already
collapse. The unaddressed case is **edit-distance** variants that survive
normalization (`Californa`, `Jon`/`John`) — exactly what GoldenCheck's
`fuzzy_duplicate_values` profiler (PR #793) detects.

**Hypothesis:** feeding GoldenCheck's per-column fuzziness into blocking-key
selection raises recall on dirty data, with no precision cost (blocking only
changes *which pairs are compared*, not the match decision).

---

## 2. The signal

A per-column **block-shatter risk** `r(col) ∈ [0, 1]`: the fraction of rows whose
value is an edit-distance variant of a more-frequent value in the same column
(i.e. would shard off its canonical block). Derived from GoldenCheck's existing
fuzzy clustering — `goldencheck.cell_quality(df)` (PR #794) already flags those
cells per column; the bridge aggregates them to a per-column rate, filtered to
string columns (date/future-dated penalties are irrelevant to blocking).

`r(col)` is **fail-open**: 0 for every column when goldencheck is absent, too
old to expose the API, or the column is clean → exact no-op.

---

## 3. Responses (recall-safety is the whole game)

A naive "skip fuzzy columns" would *also* hurt recall (it removes a comparison
path). The actions are ordered so the default **never reduces recall**:

1. **Normalize-first (primary).** When a chosen blocking key has
   `r(col) ≥ R_NORMALIZE` (e.g. 0.02), add a **phonetic / fuzzy-tolerant pass**
   for it using GoldenMatch's *existing* machinery — a multi-pass
   `BlockingKeyConfig` with a `soundex` transform (and/or a `qgram` /
   sorted-neighborhood pass). Edit-distance variants then co-block, *recovering*
   the pairs exact blocking would have lost. This is additive — it never removes
   the original key.
2. **Re-rank (secondary).** When several candidates are otherwise viable
   (cardinality/null comparable), prefer the lower-`r` key as the *primary* exact
   block; relegate higher-`r` keys to additional passes. A clean key beats a
   fuzzy one of equal discriminative power.
3. **Demote (last resort, guarded).** Only *drop* a candidate when
   `r(col) ≥ R_DROP` (severe, e.g. 0.30) **AND** at least one other viable
   blocking key remains. Never leave the dataset with zero blocking keys.

The common, safe path is #1 + #2. #3 fires rarely and is guarded against
zero-key outcomes.

---

## 4. Architecture / data flow

```
auto_configure_df(df)                      # runs on the sample frame
  └─ profile_columns(df) -> [ColumnProfile]
       + (NEW, optional) attach block_shatter_risk per column
  └─ build_blocking(profiles, df, ...)     # core/autoconfig.py:1407
       ├─ candidate gate (null/cardinality)  ← unchanged
       ├─ re-rank candidates by (discriminative power, -shatter_risk)   ← NEW
       └─ for a selected key with risk ≥ R_NORMALIZE: append a soundex/qgram pass ← NEW
```

- **Bridge:** `core/quality.py::blocking_risk(df) -> dict[str, float] | None`
  (new) — mirrors `compute_quality_scores`: fail-open, reuses
  `goldencheck.cell_quality`, aggregates per **string** column to a rate.
  Returns `None` when goldencheck unavailable/clean (caller treats as all-zero).
- **Profile field:** add `block_shatter_risk: float = 0.0` to `ColumnProfile`
  (`core/autoconfig.py:143`), populated from the bridge during profiling (or
  lazily inside `build_blocking`). Default 0.0 ⇒ existing behaviour byte-for-byte.
- **Gate:** new `quality_aware_blocking: bool` on `QualityConfig` (default value
  TBD — see §7), plus env kill-switch `GOLDENMATCH_QUALITY_AWARE_BLOCKING=0`
  (mirrors the `GOLDENMATCH_NOISE_AWARE_SCORERS` pattern, #662).
- **Cost containment:** the risk scan runs once on the auto-config *sample*
  (≤20K rows), and the native fuzzy kernel makes it ~tens of ms (measured 42ms /
  50k rows in #794). Fail-open on any error.

No new GoldenCheck public API is strictly required for v1 (reuse `cell_quality`).
A dedicated `goldencheck.column_fuzz_rate(df)` is a possible later refinement if
the aggregation logic wants to live on the GoldenCheck side.

---

## 5. Changes (by file)

GoldenMatch:
- `core/quality.py` — `blocking_risk(df)` bridge (fail-open).
- `core/autoconfig.py` — `ColumnProfile.block_shatter_risk` field; populate it;
  re-rank in `build_blocking`'s candidate ordering; append a phonetic/qgram pass
  for high-risk selected keys (reusing `BlockingKeyConfig` + `soundex`/`qgram`
  transforms that already exist).
- `config/schemas.py` — `QualityConfig.quality_aware_blocking` flag.
- Docs: this spec + `CLAUDE.md` notes.

GoldenCheck:
- None required for v1 (reuse `cell_quality`). Optional: `column_fuzz_rate`.

---

## 6. Test + measurement plan (BINDING — the gate)

Blocking changes are only trustworthy when measured on real ER benchmarks.
Mirror the #662 noise-aware-scorer posture: benchmark-validated, env kill-switch,
**not** a CI perf gate.

Unit / behaviour:
- `blocking_risk` fail-open (no goldencheck → None; clean df → None/zeros).
- `build_blocking` with a synthetic fuzzy column: asserts a soundex/qgram pass is
  added and the original key is retained (no key dropped without an alternative).
- Re-rank: a clean and a fuzzy candidate of equal cardinality → clean is primary.
- Off (flag/env) → blocking config byte-identical to today.

Accuracy (the real gate), using GoldenMatch's wired benchmarks:
- **Febrl3, DBLP-ACM, NCVR** (`tests/benchmarks/`): run ON vs OFF.
- **Pass criteria:** recall **non-decreasing** on all three AND F1 non-decreasing;
  precision **must not regress** (blocking shouldn't change precision, so any
  precision drop is a bug). Target a measurable **recall gain on the dirty/
  high-corruption sets** (NCVR), the whole point of the door.
- **Pair-count budget:** the added phonetic passes increase comparisons; record
  `fuzzy_pair_count` via the existing bench harness and require the increase to
  stay within a bound (e.g. ≤ 1.5× on the benchmark sets) — recall recovery must
  not come from an unbounded comparison blow-up.

---

## 7. Default posture & kill criteria

- **Default OFF for v1** until the §6 benchmark sweep shows recall↑/flat + no
  precision regression on all three datasets, then flip to ON with the env
  kill-switch retained (the #662 precedent). Honest default beats an assumed win.
- **Kill criterion:** if any benchmark's F1 regresses with the feature ON and the
  cause isn't a fixable bug, the feature ships OFF-by-default (opt-in) and the
  spec records the negative result.

---

## 8. Risks

- **Skipping reduces recall.** Mitigated: normalize-first; demote only with a
  surviving alternative; never zero-key.
- **Sample-noise.** Auto-config profiles a sample; a fuzz rate on a small sample
  is noisy. Act only above a clear `R_NORMALIZE`, and lean on additive passes
  (which are recall-safe even if the estimate is slightly off).
- **Comparison blow-up.** Phonetic passes enlarge blocks. Bounded by the existing
  oversized-block guards + the §6 pair-count budget.
- **Coupling.** Another GoldenMatch path that hard-depends on goldencheck — kept
  fail-open + flagged, exactly like quality.py / transform.py.

---

## 9. Scope boundary

In scope (Door #1): blocking-candidate **selection** — re-rank + fuzzy-tolerant
pass + guarded demote.

Out of scope (later doors): general standardization-transform selection across
the whole pipeline (Door #2), FD-based negative evidence (#3), threshold priors
(#4), quality-gated review routing (#5). This spec touches only `build_blocking`
and its inputs.

---

## 10. Implementation notes & deviations (as built, 2026-06-07)

Status: IMPLEMENTED (mechanism + unit tests). Accuracy sweep deferred to CI.

- **Gate is env-only for v1.** `GOLDENMATCH_QUALITY_AWARE_BLOCKING=1` enables it
  (default OFF, per §7). The `QualityConfig.quality_aware_blocking` flag in §4
  was **not** added — the two `build_blocking` call sites don't thread a quality
  config, so a config field would be dead API. Config-driven enablement is a
  follow-up if wanted. (Honest-default principle: no unwired flags.)
- **No `ColumnProfile.block_shatter_risk` field.** `apply_quality_aware_blocking`
  reads the `blocking_risk(df)` dict directly (less surface); profiles are used
  only for `col_type` → transform choice.
- **Implemented as a post-pass on the built config**, not inside `build_blocking`:
  `apply_quality_aware_blocking(blocking, profiles, df)` runs right after each
  `build_blocking(...)` call (before adaptive promotion). Converts `static` /
  `multi_pass` to an explicit `multi_pass` union (original keys → passes + a
  fuzzy-tolerant pass); other strategies pass through untouched.
- **Conservative "already-tolerant" rule:** a field that already appears in a
  pass carrying `soundex`/`metaphone`/`substring:*` is treated as fuzzy-tolerant
  and gets no extra pass. Observed in the person-path e2e: the existing
  geo/name fallback already emits `substring`/`soundex` passes, so the augmenter
  correctly no-ops there. The add fires on the case it targets: a plain exact key
  on a fuzzy categorical (e.g. product `brand`, `state`) with no tolerant pass.
- **Fuzzy threshold inheritance:** the signal comes from `goldencheck.cell_quality`
  (Levenshtein-ratio ≥ 0.82), so very short typos (`Jon`/`John`, ratio 0.75) are
  intentionally NOT flagged — that threshold is tuned for precision in GoldenCheck.
  Longer 1-char typos (`Californa`, `Catherina`) are caught.
- **Transform by col_type:** `name` → `["lowercase","soundex"]` (phonetic, catches
  early-character typos); else → `["lowercase","strip","substring:0:6"]` (prefix
  block).
- Tests: `tests/test_quality_aware_blocking.py` (9). Regression: autoconfig +
  golden + pipeline + config suites green with default-OFF (byte-identical).
