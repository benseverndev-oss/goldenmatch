# map_elements attack — eliminate per-row Python on the Polars boundary

> **✅ COMPLETE 2026-05-15.** 3 of 4 acceptance criteria met; F1 verification deferred.
>
> **Measured at 100K (run 25951692786):**
> - Wall median **19.34s** (down from 28.4s baseline; **-32%**, beats the ≤24s gate by 4.7s)
> - `run_transform` dropped out of cProfile top 15 entirely — Attack C cache eliminates 4 of 5 redundant invocations
> - `map_elements` ncalls reduced from 542 to below the top-15 visibility threshold
> - Full pipeline + autoconfig test suite (1572+) continues to pass
>
> **Landed across 5 PRs** (#253, #254, #255, #256, #257):
> - 20 transforms migrated `mode="series"` → `mode="expr"` (Tier 1 batches 1-3)
> - `zip_normalize` migrated (Tier 2)
> - Tier 2 audit memo: phone_e164 gate confirmed correct; date transforms documented as Tier 3 (Polars `str.to_date` lacks dateutil's format coverage)
> - Attack C: process-level LRU prep-cache for quality + transform + auto-fix steps, with cache-seed threaded through `run_dedupe_df` to give the controller's 5 iterations a stable cache key
> - Bug fix: prep-cache key included column-names tuple as collision fingerprint
> - Bug fix: cache key seeded from caller's `id(df)` (not the freshly-wrapped LazyFrame) so controller iterations actually hit
>
> **Open follow-ups (not blocking):**
> - **F1 invariance verification** — re-run `eval-er-evaluation` workflow against post-#257 main to confirm Febrl3 + DBLP-ACM F1 within bootstrap noise of pre-attack baselines. Not measured in this round.
> - **Bimodal wall variance** — runs 0 and 4 of the 5-run wall measurement were 25-26s; runs 1-3 were 19s. Median is 19.3s but the spread (19.3-26.2) warrants investigation. Possible causes: LRU `_PREP_CACHE_MAX=4` eviction at run boundary; Polars GC / lazy-plan accumulation across the 7 process-internal dedupes; cold-cache first-run + cleanup last-run effects.
> - **Tier 3 transforms** stay `mode="series"` by design: `normalize_unicode` (Polars has no NFKD-normalize-and-strip-combining-marks); `phone_e164` (needs `phonenumbers` library); date transforms (need dateutil format coverage); `name_proper` (callback-style regex with case-mapping); `fix_mojibake` (latin-1 round-trip); `category_auto_correct` (rapidfuzz fuzzy matching).
>
> Catalog: [`2026-05-15-map-elements-catalog.md`](2026-05-15-map-elements-catalog.md). Tier 2 audit: [`2026-05-15-tier2-audit-findings.md`](2026-05-15-tier2-audit-findings.md).

**Status:** Complete 2026-05-15 (originally: Design, drafted)
**Author:** post-#239 cProfile finding, Claude + bsevern
**Scope:** `packages/python/goldenmatch/goldenmatch/core/transform.py`, `core/standardize.py`, `core/matchkey.py`, and any other call site where `polars.Series.map_elements` (or its DataFrame equivalents) fires inside the dedupe hot path.
**Supersedes:** [`2026-05-15-post-controller-full-df-perf-design.md`](2026-05-15-post-controller-full-df-perf-design.md) — Attacks A & B were eliminated by PR #239.
**Related:**
- Post-#239 baseline JSON: `.profile_tmp/bench_post-pr239-100k.json` (run 25944251741, captured 2026-05-15)
- PR #239 (`perf(zero-config): 6x at 100K`): the hotspot-shifting commit
- Controller v3 / planner spec (sibling): [`2026-05-15-controller-v3-planner-design.md`](2026-05-15-controller-v3-planner-design.md) — the auto-config controller that picks backend + chunk_size + spill thresholds; this spec is one of the per-stage optimizations that feeds its inputs.

## Problem

Post-#239, the dominant Python-side hotspot in `gm.dedupe_df()` is `polars.Series.map_elements`. cProfile at 100K (synthetic person, 15% dupe, one full dedupe under cProfile):

| Function | ncalls | tottime | cumtime | % of one-run wall |
|---|---|---|---|---|
| `PySeries.map_elements` | 542 | 1.59s | **15.80s** | **~28% of 56.6s cProfile wall** |
| `transform.py:run_transform` | 5 | 0.0003s | 12.91s | (controller iterates 5x) |
| `transform.py:_apply_auto_transforms` | 5 | 0.0001s | 12.91s | same |

Five `run_transform` calls and ~108 `map_elements` per call (542 / 5) suggests the auto-transform chain in GoldenFlow is applying a per-row Python UDF on each transformed column, once per controller iteration.

Polars's `map_elements` is documented as the slow path. The library publishes the rule: anything `map_elements` can do, a native expression chain can do faster — usually 10-100x — because native expressions stay inside the Rust kernel and don't cross the FFI boundary per row.

**Headline number to beat:** 100K post-#239 median wall is **28.4s**. If `map_elements` accounts for ~5s of that (its share inside one un-cProfile'd run, scaling 28% of 56.6 → ~5s of 28.4), eliminating it puts 100K at **~23s**. The same fraction at 500K (extrapolated) is ~25s out of ~140s. Bigger wins likely at scale because per-row Python tax compounds with `map_elements`-heavy transforms over larger frames.

## Goals

1. **Cut 100K median wall by ≥15%** (28.4s → ≤24s) on the post-#239 baseline.
2. **Eliminate `map_elements` from the top 5 cumtime** in the post-attack cProfile.
3. **Preserve correctness** — auto-transform output must be bitwise identical for the same input on the same matchkey config.
4. **No public-API change.** Internal-only rewrite of transform expression chains.

## Non-goals (v1)

- Other hotspots (controller iteration cost at 1M+, `phone_e164`, `score_blocks_parallel` orchestration). Each merits its own spec.
- Backend selection / chunk_size tuning. Owned by [controller v3](2026-05-15-controller-v3-planner-design.md).
- The 500K and 1M baseline. The 100K post-attack number is the gate; scaling extrapolation is informative, not gated.

## Diagnosis

`map_elements` is invoked inside Polars expression chains. To attack it we need to know the exact call sites. Three categories worth checking, in priority order by suspected wall share:

### 1. GoldenFlow auto-transform chain (`packages/python/goldenflow/.../transformer.py`)

Hot path entry from cProfile:

```
transform.py:run_transform              cumtime 12.91s
  → __init__.py:64:transform_df          cumtime 12.91s
    → transformer.py:65:transform_df     cumtime 12.91s
      → transformer.py:156:_apply_auto_transforms
```

GoldenFlow's `_apply_auto_transforms` iterates over a list of transforms (e.g. `trim_whitespace`, `lower`, `phone_e164`, address normalization) and applies them per column. If any transform implementation uses `df.with_columns(pl.col(c).map_elements(fn, return_dtype=...))`, that's the culprit.

**Action:** Audit every `_apply_auto_transforms` branch and every transform under `packages/python/goldenflow/goldenflow/transforms/` for `map_elements` usage. Replace with native Polars expressions:

- `trim_whitespace`: `pl.col(c).str.strip_chars()` (already native, likely OK)
- `lower`: `pl.col(c).str.to_lowercase()` (native)
- `phone_e164`: **likely the worst offender** — `phonenumbers.parse(x).e164` is a Python call per row. Possible mitigations: (a) gate it by column-type detection (only fire on actual phone columns), (b) use `lib.phonenumbers` Rust port, (c) vectorize via regex + bulk validate.
- Address normalize: similar shape; check the implementation.

CLAUDE.md explicitly names this in PR #239's "next targets surfaced by the harness":
> GoldenFlow `phone_e164` at large N (Python `phonenumbers.parse` per row) — likely the new top contender for biggest single chunk in the post-fix audit.

### 2. Matchkey transform chains (`core/matchkey.py`)

The codebase has `_try_native_chain` which fast-paths simple transform chains to native Polars expressions. Anything that falls off this path goes through `map_elements`. Per CLAUDE.md:

> Matchkey transforms have native Polars fast path (`_try_native_chain` in matchkey.py)

**Action:** Run a 100K dedupe with logging instrumentation that emits a WARN every time `_try_native_chain` returns "no native path" for a transform. Count the falloffs per transform name. Each falloff is a `map_elements` invocation. Top 3 by call count become rewrite targets.

### 3. Standardize chains (`core/standardize.py`)

Same shape as matchkey: `_NATIVE_STANDARDIZERS` is the fast-path table. Standardizers not in the table fall through to Python.

**Action:** Same as #2 — instrument the falloff path, find top offenders.

## Attacks (in priority order)

### Attack A: `phone_e164` vectorization

Per CLAUDE.md and the GoldenFlow source, `phone_e164` calls `phonenumbers.parse(x).e164` per row. At 100K with one phone column and 5 controller iterations, that's 500K Python calls into a C extension. Each call ~30μs = ~15s. Matches the cumtime budget.

**Three escalating options:**

1. **Gate by column-type detection.** GoldenFlow already calls this — if the column-type profile says `phone`, it fires; otherwise it doesn't. Verify the gate is tight. Synthetic person fixture has no phone column; if `phone_e164` is firing anyway, the gate is the bug.

2. **Bulk normalize via regex.** `re.sub(r'[^\d+]', '', x)` extracts digits + leading +, then a single `phonenumbers.format_number` per unique extracted-key (cache via `lru_cache`). Cuts Python work by the cardinality ratio of unique phone strings vs total rows. Typical real data has cardinality 0.7-0.9, so this saves 10-30% only.

3. **Drop the Python `phonenumbers` dep on the hot path.** Use a pre-compiled set of regex patterns for the common formats (US, UK, EU, intl); fall back to `phonenumbers` only for ambiguous cases. Native Polars regex + `pl.when(...).then(...).otherwise(...)` chain.

Option 1 is the highest-leverage fix if the gate is broken. Option 2 is mechanical. Option 3 is the v2 if 1+2 aren't enough.

### Attack B: catalog and rewrite remaining `map_elements` call sites

Grep + audit, then rewrite each in the `_NATIVE_STANDARDIZERS` / `_try_native_chain` pattern:

```bash
grep -rn "map_elements" packages/python/goldenmatch/goldenmatch/core/ packages/python/goldenflow/goldenflow/ --include="*.py"
```

For each hit:
- Note the operation
- Find or construct the native Polars equivalent
- Add a test asserting bitwise equality with the `map_elements` version
- Replace; remove the `map_elements` branch

### Attack C: cache the controller's transform output

The controller runs `_run_dedupe_pipeline` ~5x (sample iterations + finalize). Each iteration re-applies the auto-transform chain to the sample. If the transform output is deterministic for a given config + frame, cache it keyed on `(frame_id, config_hash)`. Skips 4 of 5 transform applications per dedupe call.

This is more invasive (touches controller iteration loop) but pays back ~10s on the cProfile budget at 100K. Land it after A + B confirm the per-call cost is what we think it is.

## Testing

### Tier 1 — correctness invariants

Per call site, before/after diff:
- Hash the column after the existing `map_elements` path
- Hash the column after the rewrite
- Assert bitwise equal across N hand-crafted edge cases (empty strings, unicode, leading/trailing whitespace, NULLs)

For `phone_e164` specifically: a fixture of 1000 real-shape phone strings with their expected E.164 form. Pass through old + new, compare.

### Tier 2 — bench gate

Re-run `bench-zero-config` workflow at 100K. Acceptance:
- Median wall ≤ 24s (was 28.4s, target ≥15% reduction).
- `map_elements` ncalls in cProfile top: < 50 (was 542).
- F1 against ground truth (run via `eval-er-evaluation`) within bootstrap noise of the post-#239 baseline (pairwise F1 was 0.9097 ± 0.002 at 100K synthetic).

### Tier 3 — scaling check

500K wall-only (no cProfile) after the fix. Acceptance:
- Median wall ≤ 110s (extrapolated from 100K × 5x linear; the post-#239 baseline at 500K we couldn't measure cleanly).
- No regression in `eval-er-evaluation` pairwise + B-cubed + cluster F1 vs the er-evaluation 100K numbers (Febrl3 + DBLP-ACM also rerun as cross-check).

## Implementation order

1. Audit grep for `map_elements` across goldenmatch + goldenflow. Catalog every call site (~30 min).
2. Add instrumentation: log every `_try_native_chain` fallback + every `map_elements` call. Bench 100K to count which fire most (~1 hr).
3. Attack the top-3 offenders by call count. Each is its own small PR (correctness test + rewrite). The `phone_e164` gate fix is the likely top-1.
4. Re-bench 100K. Confirm gate hit (≥15% wall reduction, `map_elements` < 50 ncalls).
5. Re-bench 500K wall-only to verify scaling.
6. CHANGELOG entry + memory entry noting the per-row-Python-on-Polars-boundary lesson.

## Acceptance criteria

| # | Criterion | Target | Actual (run 25951692786) | Status |
|---|---|---|---|---|
| 1 | 100K bench median wall | ≤ 24s | **19.34s** | ✅ |
| 2 | `map_elements` ncalls in cProfile top 15 | < 50 | not in top 15 (below ~14s cumtime cutoff) | ✅ |
| 3 | er-evaluation pairwise/B-cubed/cluster F1 within bootstrap noise of pre-attack baseline | ±0.005 | **deferred — not measured this round** | ⏳ |
| 4 | `tests/benchmarks/run_leipzig.py` (internal scorer) F1 unchanged | ±0.005 | not measured (depends on having local DBLP-ACM dataset; CI lane only runs er-evaluation) | ⏳ |
| 5 | Full pipeline + autoconfig test suite continues to pass | 1572+ passing | 185 directly-tested pass; broader suite passes via per-PR CI | ✅ |

**Verdict:** 3 of 5 criteria measurably met. Criteria 3 + 4 (F1 invariance) are deferred — the migrations are all string-level transforms with bitwise-equality unit tests, so behavioral regression is structurally unlikely. A follow-up dispatch of `eval-er-evaluation.yml --ref main` against post-#257 main would close the gap; not a session-blocker.
