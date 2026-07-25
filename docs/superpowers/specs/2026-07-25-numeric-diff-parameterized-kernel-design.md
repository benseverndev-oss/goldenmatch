# numeric_diff parameterized native kernel — design

**Date:** 2026-07-25
**Status:** design (approved direction; not yet implemented)
**Related:**
- `docs/superpowers/specs/2026-07-23-fs-domain-comparators-design.md` (the FS domain-comparator arc: date_diff / geo_haversine / array_intersect / numeric_diff)
- `docs/superpowers/specs/2026-07-25-splink-domain-comparator-conversion-design.md` (the Splink-conversion arc P1–P6)
- PRs #2130 / #2134 / #2135 ("one Rust kernel per capability, fed to every surface") — date_diff/geo (Phase 4), array_intersect (P5/P6), lsh/perceptual (Part C)

## 1. Problem

`numeric_diff` is the **last non-model string scorer** with no `-core` kernel on any
surface. After #2134/#2135 the parity manifest (`parity/goldenmatch.yaml`)
`scorer_kernels_deferred` holds exactly:

```
embedding        : n/a  -- model-backed
record_embedding : n/a  -- model-backed
numeric_diff     : deferred -- needs the parameterized score_one contract
```

So closing `numeric_diff` completes the "every non-model scorer is kernel-backed,
single-sourced across Python-native / Rust / TS / WASM" thesis. It is the only
deferral left that is *deferred* (will kernelize) rather than *n/a* (model-backed).

### Why it wasn't done with the others

Every kernelized scorer today is a `score_one` arm:

```rust
// packages/rust/extensions/score-core/src/lib.rs
pub fn score_one(scorer_id: u8, a: &str, b: &str) -> f64 { ... }
//  ...17 => date_diff_similarity, 18 => geo_haversine_similarity, 19 => array_intersect_similarity
```

`score_one` is **fixed-id, two-string**: a `u8` selects the scorer, the only data
inputs are `a` and `b`. `numeric_diff` carries a *band* parameter on the scorer
string —

```
numeric_diff:abs:<eps>   distance = |a-b|,                    similarity 1.0 → 0 at eps
numeric_diff:pct:<frac>  distance = |a-b| / max(|a|,|b|,1e-9), similarity 1.0 → 0 at frac
numeric_diff             = numeric_diff:pct:0.1   (bare default)
```

(`core/scorer.py::_parse_numeric_diff_spec` → `(mode, band)`; `_numeric_diff_similarity_py`
is the byte-parity reference, exact-string fallback on unparseable input.)

`score_one(id, a, b)` has nowhere to put `<eps>`/`<frac>`. And unlike
`array_intersect`'s mode (discrete `{jaccard, overlap}`, enumerable as ids), the band
is **continuous** — you can't enumerate it as `u8` ids. #2134 sidestepped this for
`array_intersect` by kernelizing only the default (jaccard, id 19) and declining
`:overlap` to the per-pair mirror. That trick does **not** transfer: `numeric_diff`'s
bare default is itself parameterized (`pct:0.1`), and auto-config emits the explicit
form, so "kernelize only the bare form" would decline on the common path.

## 2. Key insight — the band is *field-constant*, not per-pair

`_parse_numeric_diff_spec` runs **once** off the scorer string. Every pair scored for
a given field uses the same `(mode, band)`. So the parameter is not per-pair data that
must ride through `score_one(id, a, b)` — it is **per-field config**.

And the block kernel is already field-oriented:

```rust
// packages/rust/extensions/native/src/score.rs
pub fn score_block_pairs(
    py, row_ids, block_sizes, field_values,
    scorer_ids: Vec<u8>,   // ONE id per field
    weights: Vec<f64>, total_weight, threshold, exclude,
) -> Vec<(i64, i64, f64)>
// dispatch: score_bucket_field(scorer_ids[f], a, b, &name_data)   // per field
```

The name scorers (bucket ids 15/16) are already **not** `score_one` arms — they are
intercepted inside `score_bucket_field` and reach process-global census/alias tables
(`current_name_refdata()`). `numeric_diff` follows that precedent exactly, with one
difference: its extra state (`band`) is **per-field**, not process-global (different
fields can carry different bands), so it must travel alongside `scorer_ids`, not as a
global.

**Design in one line:** split `numeric_diff`'s parameter — the discrete **mode** folds
into two ids; the continuous **band** rides a new per-field `scorer_params` array
parallel to `scorer_ids`. `score_one` itself never changes.

## 3. Design

### 3.1 score-core (the reference)

Add a pure, pyo3-free reference mirroring `_numeric_diff_similarity_py`:

```rust
// mode: false = abs, true = pct   (matches the two ids below)
pub fn numeric_diff_similarity(a: &str, b: &str, pct: bool, band: f64) -> f64
```

- Parse both sides to `f64` (reject non-finite → exact-string fallback: `1.0 if a==b else 0.0`,
  never a distance — mirror `_parse_float` + the `NaN`/`inf` guard).
- `dist = |a-b|`; if `pct`, `dist /= max(|a|,|b|,1e-9)` (the `_PCT_EPS` guard, byte-identical).
- `if dist >= band { 0.0 } else { 1.0 - dist/band }`.
- Inline `#[cfg(test)]` unit tests (band edges, pct denom guard, unparseable, `-0.0`).

**No `score_one` arm.** `numeric_diff` is a bucket-dispatch-only kernel (like the name
scorers), because `score_one`'s signature can't carry `band`. Reserve **bucket ids 21
(abs) / 22 (pct)** — used only inside `score_bucket_field`, never `score_one`.
(20 is intentionally left free: an earlier closed PR used it for an `array_intersect:overlap`
id that did not land; leaving a gap avoids a future collision with that history.)

### 3.2 native pyo3

1. **Capability marker** — `#[pyfunction] numeric_diff_similarity(a, b, pct, band) -> f64`
   registered via `wrap_pyfunction!`. Doubles as (a) a direct byte-parity target and
   (b) the **wheel-skew signal** the host gates on (mirrors `array_intersect_jaccard_similarity`
   et al.). A stale wheel lacks it → host declines `numeric_diff` to the pure mirror
   instead of `score_bucket_field`'s catch-all silently scoring 0.0.

2. **Optional per-field params on the hot path** — extend `score_block_pairs` (and, only
   if the FS path ever wants it — see §5, it does not today — `score_block_pairs_prob`)
   with an **optional trailing** arg:

   ```rust
   #[pyo3(signature = (row_ids, block_sizes, field_values, scorer_ids, weights,
                       total_weight, threshold, exclude, scorer_params = vec![]))]
   pub fn score_block_pairs(..., scorer_params: Vec<f64>) -> ...
   ```

   `scorer_params[f]` is the band for field `f` (ignored / `NaN` for non-numeric fields).
   `score_bucket_field` gains the band:

   ```rust
   21 => numeric_diff_similarity(a, b, false, params[f]),  // abs
   22 => numeric_diff_similarity(a, b, true,  params[f]),  // pct
   ```

   **Why optional-trailing, not a signature break or a parallel `_v2`:** the existing
   `score_block_pairs` signature is load-bearing for wheel-skew — its own source comment
   flags that changing it skews a published wheel against the caller (the #688 class).
   An *optional trailing* arg (pyo3 `signature=` default) keeps every existing 9-arg call
   working on the new wheel, so it is **not** a breaking change; a parallel
   `score_block_pairs_v2` would duplicate the whole hot loop for one dispatch arm. The
   host still needs to know whether the *loaded* wheel accepts the 10th arg — that's what
   the `numeric_diff_similarity` capability marker answers (present ⇒ new enough to accept
   `scorer_params`).

### 3.3 host wiring (`backends/score_buckets.py`)

Mirror the `array_intersect` P5 wiring, plus the params array:

1. `_NATIVE_SCORER_IDS`: add ONE **bare marker** key `"numeric_diff"` (→ 22, the pct
   default). Do **not** add per-band keys (infinite) — the emitter stays clean (`sorted(_NATIVE_SCORER_IDS)`
   yields bare `numeric_diff`, matching the `scorers` surface granularity, exactly as
   `array_intersect` is one key). Perf-guard / eligibility use `startswith("numeric_diff")`.

2. **id + band resolver** at the `score_block_pairs` call site — replace the plain
   `_NATIVE_SCORER_IDS.get(spec[3])` for numeric fields with a small helper:

   ```python
   def _native_numeric_field(scorer):        # None if not numeric_diff
       mode, band = _parse_numeric_diff_spec(scorer)   # reuse the Python parser
       return (21 if mode == "abs" else 22, band)
   ```

   Build the parallel `scorer_params: list[float]` (band per field; `0.0`/`nan` for
   non-numeric fields) and pass it as the new trailing arg **only when** the capability
   marker is present.

3. **Wheel-skew guard** — `_numeric_ok = _mod is not None and hasattr(_mod, "numeric_diff_similarity")`,
   `has_numeric = any((spec[3] or "").startswith("numeric_diff") for spec in _field_specs)`,
   folded into `_skew_block`. When the marker is absent, decline native for the whole
   block (numeric field present) → pure per-pair mirror.

4. **fast-path eligibility** — add a `_resolve_score_pair_callable` branch:

   ```python
   if scorer_name == "numeric_diff" or scorer_name.startswith("numeric_diff:"):
       from goldenmatch.core.scorer import _numeric_diff_similarity_py
       return lambda a, b: _numeric_diff_similarity_py(a, b, scorer_name)
   ```

   Today `numeric_diff` has **no** callable here, so it declines the fast path entirely
   and runs `find_fuzzy_matches` (the `_fuzzy_score_matrix` numeric branch). Adding the
   mode-bound mirror makes it bucket-native-eligible (native id 21/22 when the wheel
   supports params, else this per-pair mirror), parallel to `array_intersect`.

5. **Republish the native wheel in the same change** (the documented "new depended-on
   symbol ⇒ republish `goldenmatch-native`" rule) so `pip install goldenmatch[native]`
   users get params rather than silently declining. The `native-wheel-drift` advisory
   will warn until then.

### 3.4 cross-surface

- **Rust / Python-native:** §3.1–§3.3. Lands `numeric_diff` on the WEIGHTED-BUCKET
  route; manifest `numeric_diff` → `scorer_kernels.python_only`.
- **Pure-TS:** `numericDiffSimilarity(a, b, scorer)` in `core/scorer.ts` (faithful port
  of the Rust bands + pct guard) + a `scoreField` case + `VALID_SCORERS`. Cross-language
  oracle appended to `tests/parity/fixtures/scorer-domain-comparators.json` (emitted from
  the byte-verified Python reference by `scripts/emit_domain_comparator_fixtures.py`).
- **WASM:** the wasm `score_matrix` dispatch is parameterless like `score_one`, so it
  needs the same per-field-band threading as `score_block_pairs`. This is the one piece
  with non-trivial surface. **Scoping decision (§4):** ship Python-native + pure-TS first
  (`numeric_diff` = `python_only` — TS has a pure impl, WASM declines to it), then thread
  the band through wasm to promote to `shared`. Matches how date_diff/geo went
  `python_only` (Phase 3) → `shared` (Phase 4).
- **SQL (pgrx / DuckDB):** the SQL scorer surfaces delegate to the Python `score_strings`
  / score-core reference, so `numeric_diff` already works there via the reference — no
  per-band pgrx signature needed. Follow-on / no-op for the gate.

## 4. Scoping & sequencing (proposed P7a–P7d)

- **P7a — score-core kernel only.** `numeric_diff_similarity` + inline tests. Pure,
  mergeable alone, no wiring, no wheel change. Zero runtime effect.
- **P7b — native + host + wheel.** Capability marker, `score_block_pairs` optional
  `scorer_params`, host resolver/params/guard/eligibility, `tests/test_native_numeric_diff_parity.py`
  (native==pure per mode across a band-edge corpus + block-dispatch of ids 21/22 +
  the wheel-skew decline path), `check_native_symbols` reconcile, **republish native**.
  Manifest `numeric_diff` `scorer_kernels_deferred` → `scorer_kernels.python_only`.
- **P7c — pure-TS + fixture.** Keeps `numeric_diff` `python_only` (TS pure impl present).
- **P7d — wasm band threading.** Promote `numeric_diff` → `scorer_kernels.shared`;
  `scorer_kernels_deferred` then holds only the two model-backed `n/a` entries — the arc
  is complete.

Each phase is one additive PR, byte-identical when the wheel/flag is absent.

## 5. Scope boundary — this is NOT an FS scale change

`numeric_diff` (like date_diff/geo/array) **declines native on the Fellegi-Sunter path**
— it is not in `_NATIVE_FS_SCORER_IDS`, so FS scores it on the numpy path exactly as
`soundex_match` does. The kernel serves the **weighted-bucket route** + the cross-surface
parity thesis, NOT the FS memory/scale story. Consequences:

- `score_block_pairs_prob` (the FS variant) does **not** need the `scorer_params` arg —
  leave it unchanged. Only the weighted `score_block_pairs` gets the trailing param.
- No blocking / pair-set / EM / clustering behavior changes. Because the per-pair mirror
  and the vectorized `_fuzzy_score_matrix` numeric branch call the SAME
  `_numeric_diff_similarity_py`, scalar==vectorized parity is automatic and the native
  kernel is byte-checked against it — so the WEIGHTED path output is unchanged whether
  native is on or off (native is the reference; pure is the lossy-free mirror here).

## 6. Alternatives considered

1. **Process-global band (like name refdata).** Rejected: different fields carry
   different bands in one config; a global can't represent that.
2. **Quantize bands to a fixed id set.** Rejected: continuous parameter; auto-config
   emits arbitrary bands (`pct:0.1` default but any float is legal).
3. **Kernelize only the bare default, decline parameterized** (the `array_intersect:overlap`
   posture). Rejected: `numeric_diff`'s bare default *is* parameterized (`pct:0.1`) and
   auto-config emits the explicit form, so the common path would decline — near-zero payoff.
4. **Break / duplicate `score_block_pairs`.** Rejected: signature break is the #688
   wheel-skew hazard; a parallel `_v2` duplicates the hot loop. Optional trailing arg +
   capability marker is the minimal, wheel-safe path.
5. **Widen `score_one` to `score_one(id, a, b, param)`.** Rejected: `score_one` is the
   shared per-pair reference across all surfaces; widening it ripples into every caller
   and every surface for one scorer's benefit. Field-level `scorer_params` isolates the
   change to the block kernel, where the parameter actually lives.

## 7. Testing & gates

- `score-core`: inline `#[cfg(test)]` for `numeric_diff_similarity` (P7a).
- `tests/test_native_numeric_diff_parity.py` (P7b): native `numeric_diff_similarity`
  == `_numeric_diff_similarity_py` per mode across a band-edge/unparseable/pct-denom
  corpus; `score_block_pairs` dispatch of ids 21/22 with a matching `scorer_params`
  == per-pair mirror; the capability-absent decline path.
- `scripts/check_native_symbols.py goldenmatch` reconciles the new host reference
  against the wheel export (P7b).
- `scripts/check_api_parity.py goldenmatch` (`check_structure` + `check_scorer_coverage`
  + partition): `numeric_diff` present in `scorer_kernels` (python_only → shared),
  removed from `scorer_kernels_deferred`; alphabetical sort maintained.
- `tests/parity/scorer-domain-comparators.test.ts` + `wasm-scorer.test.ts` (P7c/P7d).
- Regenerate `docs-site/suite-matrix.mdx` + codemap; the `config_matrix` gates.

## 8. Risks

- **Wheel skew (primary).** The trailing `scorer_params` is only useful once the wheel
  ships it. Mitigated by: optional-trailing (never breaks the old signature) + the
  capability-marker gate (host declines to the pure mirror on an old wheel) + the
  republish-in-the-same-change rule + the `native-wheel-drift` advisory.
- **Band as `f64` NaN sentinel.** Using `NaN` for "no band" in `scorer_params` risks a
  `NaN`-compare footgun in Rust. Prefer `0.0` for non-numeric fields and guard
  `numeric_diff_similarity` so `band <= 0.0` → exact-string fallback (a zero/negative
  band is already meaningless — mirrors the Python `if band > 0.0` validation in
  `_parse_numeric_diff_spec`).
- **WASM promotion scope.** P7d is the only piece touching the wasm dispatch signature;
  if it proves heavy, `numeric_diff` can rest at `python_only` (Python-native + pure-TS)
  indefinitely without blocking the arc — the manifest already supports that split.
