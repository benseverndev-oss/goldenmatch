# Probabilistic (Fellegi-Sunter) Pipeline — Full Analysis

> **Staleness note (added on commit, 2026-09-04):** this audit is ~2 months old. Several
> FS-pipeline projects have shipped against `main` since (bucket/native scoring defaults,
> columnar clustering, per-stage RSS profiling, quality-weighting fixes — see
> `project_fs_*` history), so findings below should be spot-checked against current `main`
> before acting on them rather than assumed still open.

**Date:** 2026-07-15
**Base commit:** `bdd5071c3` (main; includes #1794, #1790, #1795) + PR #1799 (#1798 fix, in merge queue)
**Scope:** every code path that touches `mk.type == "probabilistic"` — entry points, routing,
EM training, scoring variants, native kernel surface, memory behavior, feature interactions,
test/parity coverage, benches, env knobs.
**Method:** 4 parallel code-audit sweeps (entry-points/routing, FS core internals, bucket+native
path, test coverage), findings spot-verified at source. File:line references are against
`bdd5071c3` + #1799 unless noted.

---

## 1. Executive summary

The FS pipeline works well on its **main lane** (dedupe → bucket → native kernel), which is
where all the recent fixes landed (#1794 memory-bounded scoring, #1790 auto-split, #1798
build_blocks skip, #823 Splink-beating accuracy). The problems are at the **edges**:

1. **Two lanes silently drop FS matchkeys entirely** (distributed, chunked) — configs that work
   single-box produce zero FS pairs with no warning when routed through them.
2. **One silent quality bug on the main lane**: `tf_adjustment` is a no-op on the scalar
   fallback path while working on the vectorized path — same config, different results,
   depending on an env var / scorer choice.
3. **A latent mis-calibration footgun**: `EMResult.proportion_matched` carries two incompatible
   semantics (within-block rate vs random-pair prior) with no type-level distinction; only the
   Splink *upgrade* lever normalizes it. Direct `from_splink` → dedupe still carries the wrong
   prior into thresholds (the exact bug that cratered F1 to 0.157 in #1760's testing).
4. **The FS native path never received two optimizations the weighted path got**: the
   exclude-set Arc handle (#552/#688 — FS still rebuilds a Rust HashSet per bucket call) and
   zero-copy Arrow marshaling (FS materializes `Vec<Vec<Option<String>>>` per bucket).
5. **6 scoring implementations, ~20 env knobs, and 3 near-duplicated pipeline blocks** with
   already-drifted gates between them. The routing decision has no single owner function.
6. **Coverage is dense at tiny scale on the happy path, absent elsewhere**: no route-vs-route
   parity above ~150 rows, no match-mode tests on any FS route, TF tested on exactly one route,
   no FS arrow-vs-polars parity, and every FS bench workflow is manual-dispatch only.

**Recommended arc (details in §6):** Phase 0 kills the silent-wrong-results class (small,
this-week fixes). Phase 1 finishes scale parity with the weighted path and makes bucket the
default FS route everywhere (measured). Phase 2 consolidates the 3 duplicated pipeline blocks
+ 6 variants behind one routing function. Phase 3 extends the parity matrix + adds a scheduled
scale gate so this audit doesn't rot.

---

## 2. Architecture map

### 2.1 Entry points and their routes

| # | Entry point | FS branch | Routes available | Bucket gate | #1798 skip | Notes |
|---|---|---|---|---|---|---|
| A | `_run_dedupe_pipeline` (`pipeline.py:2585`) | yes | bucket (`:2643`) / batched (`:2707`) / per-block (bench only, `:2681`) | `_use_bucket_scorer OR _fs_default_bucket` (`:2603`) | YES (`:2606-2616`) | canonical site; bench dump hooks here only |
| B | `_run_match_pipeline` polars lane (`pipeline.py:4099`) | yes | bucket (`:4134`) / batched (`:4149`) | `backend=="bucket" OR _fs_default_bucket` (`:4111`) | YES (`:4116`) | does NOT consult `_use_bucket_scorer` — gate drift vs A |
| C | `_run_match_scoring_and_output` arrow lane (`pipeline.py:4338`) | yes | bucket (`:4372`) / batched (`:4385`) | same as B (`:4350`) | YES (`:4355`) | same drift |
| D | `_score_partition_with_config` distributed kernel (`pipeline.py:4588`) | **NO — `if mk.type != "weighted": continue` (`:4689`)** | exact + weighted only | n/a | n/a | **FS silently dropped** (used by `distributed/scoring.py:249,266,401,421`) |
| E | `ChunkedProcessor` (`chunked.py:120-132`, `_match_against_index` `:340+`) | **NO** | exact + weighted only | n/a | n/a | **FS silently dropped** |
| F | TUI engine (`tui/engine.py:248`) | yes | oldest per-block `score_probabilistic` only (`:266`) | none | NO — eager `build_blocks` always (`:252`) | stale: no bucket/native/batched, stale `block.df` (`:265`) |
| G | `StreamingMatcher.process_record` (`streaming.py:128`) | yes | `match_one` per-record surface | n/a | n/a | separate scoring surface, no EM/blocks |
| H | fused FS Arrow kernel (`fused_match.py:255,334`) | implemented | — | — | — | **ORPHANED: never called** (weighted fused short-circuit has no FS analog; `fused_routing.py:203-205` documents FS out of v1) |

Adjacent config-shaping (not scorers): workbench preview **demotes probabilistic →
jaro_winkler** (`web/routers/autoconfig.py:66,94`); DataFusion backend raises
NotImplementedError for FS; Sail distributes weighted-only (FS falls back to one box).

### 2.2 Scoring-variant decision tree (per block/bucket)

`probabilistic_block_scorer` (`probabilistic.py:2490-2513`), preference order:

1. **native** — `_fs_native_eligible(mk)`: all field+NE scorers in {jaro_winkler, levenshtein,
   token_sort, exact}; **no `tf_adjustment`**; `level_thresholds` needs wheel const
   `FS_SUPPORTS_LEVEL_THRESHOLDS` (≥0.1.14); NE needs `FS_SUPPORTS_NE` (≥0.1.15)
2. **vectorized** NxN rapidfuzz (`GOLDENMATCH_FS_VECTORIZED`, default on; declines model-backed
   scorers)
3. **scalar** per-pair Python (the universal fallback)

Above that, orchestration: **bucket** (`score_buckets` — internally batched-native
`score_probabilistic_bucket_native` when `GOLDENMATCH_FS_BUCKET_NATIVE=1` + eligible, else
per-block loop) vs **batched** (`score_probabilistic_blocks_batched` — coalesces small blocks,
threads via `GOLDENMATCH_FS_WORKERS`). Plus `train_em_continuous`/`score_probabilistic_continuous`
(Winkler variant; rejects NE) and the single-pair `match_one`/`score_pair_probabilistic` surface.

That is **six scoring implementations** + two orchestrators, selected by ~8 env knobs and 4
config properties. No single function owns the decision; sites A/B/C each re-derive it.

---

## 3. Findings — correctness (P0)

### F1. Distributed lane silently drops FS matchkeys — `pipeline.py:4689`
The per-partition kernel loops `if mk.type != "weighted": continue`. A probabilistic matchkey
routed through `GOLDENMATCH_DISTRIBUTED_PIPELINE` / `distributed/scoring.py` contributes zero
pairs — no error, no log. Any exact matchkeys still emit, so output looks plausible.
**Fix shape:** raise `NotImplementedError` (like DataFusion does) or route FS partitions to
`score_buckets`; never silently skip.

### F2. Chunked lane silently drops FS matchkeys — `chunked.py:120-132`, `:340+`
Same class: within-chunk and cross-chunk index matching handle exact + weighted only.
**Fix shape:** same as F1 — refuse loudly or implement.

### F3. `tf_adjustment` is a silent no-op on the scalar path — `probabilistic.py:1682-1685`
The vectorized path applies `_apply_tf_adjustment` (`:1959`, `:2073`); the scalar loop and
`score_pair_probabilistic` (`:2529-2532`) never call it. TF also forces native-ineligibility
(`:2312-2313`), so a TF matchkey with a model-backed scorer (forced scalar) or
`GOLDENMATCH_FS_VECTORIZED=0` silently loses TF downweighting — same config, different scores by
route. **Fix shape:** implement TF in the scalar loop (small) or decline loudly; add
scalar-vs-vectorized TF parity test either way.

### F4. `proportion_matched` dual semantics unencoded — `from_splink.py:774-791` vs `splink_upgrade.py:463-491`
`train_em` produces a **within-block** match rate; `from_splink` imports Splink's
**random-pair** prior raw into the same field. Consumers (`prior_weight` `probabilistic.py:77-85`,
`compute_thresholds` `:1604-1611`, posterior scoring at 5 call sites) all assume within-block.
Only the *upgrade* lever re-estimates (`_estimate_within_block_prior`) — the plain
`from_splink()` / `import-splink` (no `--upgrade`) path carries the mis-scaled prior straight
into scoring. This is the exact mechanism behind the F1 0.482→0.157 crater found in #1760.
**Fix shape:** normalize at import (run the equal-odds re-estimation in `from_splink` model
import), or add `prior_kind: within_block|random_pair` to `EMResult` and make consumers
refuse/convert. Also note `estimate_m_from_labels` introduces a third convention (fixed 0.02
default, `:1129`).

---

## 4. Findings — scale & performance (P1)

### F5. FS bucket-native missing the exclude-set Arc handle (#552/#688 pathology)
Weighted native builds an `ExcludeSet` Arc ONCE per `score_buckets` call
(`score_buckets.py:772-791`) and passes the handle per bucket. The FS path passes
`frozen_exclude` as a raw `Vec<(i64,i64)>` into `score_block_pairs_fs`, which rebuilds a Rust
HashSet **on every bucket call** (`score.rs:301,308`; call at `score_buckets.py:1102-1103`).
O(buckets × |exclude|) — the exact pathology #552 fixed for weighted, and with prior exact-match
passes producing millions of matched pairs this bites hard at scale. No stale-wheel warning
analog either.

### F6. FS native marshaling is not zero-copy
`_field_values_for_block` materializes every field via `.to_list()` →
`Vec<Vec<Option<String>>>` PyO3 clone (`probabilistic.py:2361,2380`) — the pattern
`score_block_pairs_arrow` (weighted) was built to eliminate (~58% of native wall at 1M in the
weighted measurements). FS needs a `score_block_pairs_fs_arrow` twin.

### F7. `train_em` materializes a whole-dataframe row dict — `probabilistic.py:673-675`
`row_lookup` holds every row of the input as Python dicts before sampling. The pair *sampling*
is well-bounded (`_sample_blocked_pairs` stops at `n_pairs*3`, caps blocks at 100 rows,
`:563-619`), so training only needs a few thousand rows — but the lookup is O(all rows).
At 10M+ with no `model_path`, EM training OOMs on this dict (plus eager `build_blocks`
upstream). Same pattern in `estimate_m_from_labels` (`:1009-1011`) and
`train_em_continuous` (`:1244-1245`). **Fix shape:** sample pair IDs first, then look up only
the sampled rows.

### F8. Driver-side pair accumulators are candidate-count-unbounded
`all_pairs` list + `matched_pairs` set (`pipeline.py:2246-2247`, extended at 7+ sites),
`build_cluster_frames`'s `pairs_list = list(pairs)` + three per-pair column comprehensions
(`cluster.py:550,562-570`), `ClusterPairScores.from_frames(…, all_pairs)` (`pipeline.py:3090`),
`dedup_pairs_max_score` (`:3673`). This is the #1792 "driver-side clustering" residue #1795
partially addressed. `frozen_exclude` in score_buckets is called out in-code as "the dominant
Python-side accumulator" at 10M (`score_buckets.py:704-706`).

### F9. `n_buckets` never scales up with data — `score_buckets.py:211-214`
`min(cpu_count*4, 1024)`, adapts downward for tiny inputs only. At 25M rows per-bucket frames
grow linearly; bucket count should track rows (e.g. `max(cpu*4, rows // TARGET_ROWS_PER_BUCKET)`).

### F10. Bucket-default gates are inconsistent and partly stale
- Dedupe consults `_use_bucket_scorer` (750K row cap, emitter check, strategy check) OR
  `_fs_default_bucket` (native-gated); match lanes consult only
  `backend=="bucket" OR _fs_default_bucket` — no row band, no emitter check (`:4111`, `:4350`).
- The 750K cap's stated rationale ("legacy streaming path stays the default above the band",
  `pipeline.py:86-96`) is stale: since D5b, legacy `build_blocks` is eager and is the path that
  OOMs first (#1798 proof). The cap now protects the wrong side.
- `_fs_default_bucket` requires the native kernel; without it FS falls to the batched route,
  which needs eager `build_blocks` — memory-unbounded — even though non-native bucket would be
  frame-bounded.
**Fix shape:** one shared `\_fs_route(config, mk, frame)` used by all three sites; bucket by
default with the correctness exclusions (non-field blocking strategies, active profile emitter,
explicit backends) and measured removal of the row cap + native gate. (Discussed with Ben
2026-07-15 — burden of proof now on the cap.)

### F11. Single oversized block: dense NxN or dropped recall
Vectorized `total_weight = np.zeros((n,n))` has no per-block size cap
(`probabilistic.py:1945`; `_fs_batch_rows` caps *coalescing*, not a single big block); NE and TF
add more NxN broadcasts (`:1558-1560`, `:1864`). On the bucket path oversized FS blocks are
skipped, never auto-split (`score_buckets.py:1128-1133` FOLLOW-UP note) — #1790's auto-split
exists only on the polars-direct `build_blocks` side. So a mega-block either allocates dense
NxN (legacy) or silently loses its pairs (bucket).

---

## 5. Findings — architecture & coverage (P2/P3)

### P2 — structure
- **F12. The pipeline FS block is near-duplicated 3×** (A/B/C above) and the gates have already
  drifted (F10). The #1798 fix had to be applied three times.
- **F13. Core math duplicated:** calibrate/normalize prologue 5× (`probabilistic.py:1655,1933,
  2049,2366,2525`), triu-emit+exclude 3× (`:1976,2090,1414`), `row_lookup`+NE-extension 4×,
  monotonicity repair 2× (`:866-885` vs `:1100-1118`). `train_em` is ~277 lines.
- **F14. Fused FS kernel is dead code** (`fused_match.py:255-411` implemented + kernel consts
  shipped, zero callers). Wire it into the fused short-circuit or delete it.
- **F15. TUI engine is a stale fourth implementation** (F above): eager blocks, oldest scalar
  scorer, no model-reuse skip. Route it through the shared path from F12's consolidation.
- **F16. Env-knob sprawl:** ~20 `GOLDENMATCH_*` knobs influence FS routing/scoring (inventory
  §7). Several combinations are untested (e.g. `FS_VECTORIZED=0` × TF = F3).
- **F17. NE fallback loses learned weights:** too-few-pairs EM fallback pins NE at fixed
  −3.0 bits (`_fallback_result` `:1459-1466`) — reasonable, but silent.
- Note: an **uncommitted diff on the stale `audit/gh-issues-review` branch** (main checkout)
  adds `score_probabilistic_blocks_parallel` — in-flight work that overlaps the existing batched
  scorer's `GOLDENMATCH_FS_WORKERS` threading; reconcile before landing either.

### P3 — test/parity/bench coverage
- **F18. TF adjustment tested on exactly one route** (vectorized unit, 12 rows). No
  scalar↔vectorized TF parity (would have caught F3), no TF-matchkey E2E through `dedupe_df`.
- **F19. Match-mode (`across_files_only` / `target_ids`) has zero tests on any FS route** —
  the only match-mode tests exercise the *weighted* generic fast path.
- **F20. No FS arrow-lane vs polars-lane parity** (explicitly out of scope in
  `test_bucket_legacy_parity_matrix.py`, never covered FS-side elsewhere).
- **F21. All route-vs-route parity is ≤150 rows**, and every native-FS parity test is
  `skipif`-gated on the kernel being built; the dedicated `GOLDENMATCH_NATIVE=0` CI lane runs
  only `test_probabilistic.py` — the NE/nlevel/bucket-native suites have no dedicated
  pure-Python lane despite native being "authoritative by default".
- **F22. Every FS bench workflow is `workflow_dispatch`-only** (`bench-fs-stages`,
  `bench-fs-distributed` (5M), `bench-probabilistic`, `bench-native-bucket`, `scale-audit*`).
  Nothing runs on a schedule; scale regressions surface only when a user files an issue
  (#1792, #1798 were both user-found).
- **F23. `model_path` reuse untested on native / bucket-native routes** (persistence parity is
  pinned on legacy/bucket-python; #1799 added the bucket-route skip tests).

---

## 6. Remediation roadmap

**Phase 0 — stop silent wrong results (small diffs, high urgency)**
1. F1/F2: FS in distributed + chunked lanes → raise `NotImplementedError` with a clear message
   (or fall back to the single-box pipeline). Tests: config with FS matchkey through each lane.
2. F3: implement `_apply_tf_adjustment` in the scalar loop + scalar↔vectorized TF parity test.
3. F4: normalize the prior at `from_splink` import (reuse `_estimate_within_block_prior`) or
   tag `EMResult.prior_kind` and convert in `prior_weight`/`compute_thresholds`.

**Phase 1 — finish scale parity with the weighted path**
4. F5: FS exclude-set Arc handle (add `exclude_set=` to `score_block_pairs_fs`, build once at
   `score_buckets` entry; wheel-skew gate const + stale-wheel warning like weighted).
5. F6: `score_block_pairs_fs_arrow` zero-copy twin (row_ids + field arrays as Arrow buffers).
6. F10: unify the bucket gate (one `_fs_route()`), drop the native requirement, remove/raise the
   750K cap — each behind a measured parity + RSS + wall run at 1M–5M on person + biblio shapes
   (bench-fs-distributed / scale-envelope harness exist).
7. F7: sample-then-lookup in `train_em` (bounded row materialization).
8. F9: data-scaled `n_buckets`.
9. F11: bucket-side oversized auto-split (port #1790's `_auto_split_block` to the bucket keep-mask
   stage) + per-block NxN cap routing big blocks to the native/batched lane.

**Phase 2 — consolidation**
10. F12/F15: extract one `score_probabilistic_matchkey(...)` used by dedupe, both match lanes,
    and the TUI engine (kills the 3× duplication and the gate drift class).
11. F13: extract shared normalize/emit/row-lookup helpers (bit-identical proven, like
    `fs_weight_range` was).
12. F14: wire fused FS into the fused short-circuit behind its readiness gate, or delete it.
13. F16: document the knob inventory (tuning.mdx) and fold redundant knobs where possible.

**Phase 3 — keep it honest**
14. F18-F20/F23: extend the parity matrix — TF, match-mode, arrow-vs-polars, model-reuse ×
    {scalar, vectorized, batched, bucket-python, bucket-native, native} at a few hundred rows.
15. F21: run the FS-NE/nlevel/bucket suites in the `GOLDENMATCH_NATIVE=0` lane.
16. F22: weekly scheduled `bench-fs-distributed` (5M, `large-new-64GB`) with wall/RSS/F1
    thresholds; fold in a 14M lane once #1798 is confirmed on the runner.

Suggested issue slicing: one issue per Phase-0 item (3), one epic per Phase 1/2/3 with the
numbered items as tasks. Phase 0 items are each ≤1 day; Phase 1 items 6 and 4/5 are the big
levers for the "always bucket" direction and the next scale envelope run.

---

## 7. Appendix — env knobs on FS paths

Routing: `GOLDENMATCH_FS_DEFAULT_BUCKET` (pipeline.py:164), `GOLDENMATCH_BUCKET_DEFAULT`
(:136), `GOLDENMATCH_COLUMNAR_PIPELINE` (:109+), `GOLDENMATCH_DISTRIBUTED_PIPELINE`
(distributed/pipeline.py), `GOLDENMATCH_ENABLE_DISTRIBUTED_RAY` (pipeline.py:42),
`GOLDENMATCH_MATCH_FUSED` (:1654, weighted-only).
Scorer selection: `GOLDENMATCH_NATIVE` (_native_loader.py), `GOLDENMATCH_FS_NATIVE`
(probabilistic.py:2259), `GOLDENMATCH_FS_BUCKET_NATIVE` (score_buckets.py:60),
`GOLDENMATCH_FS_VECTORIZED` (probabilistic.py:2234), `GOLDENMATCH_FS_WORKERS` (:2216),
`GOLDENMATCH_FS_BATCH_ROWS` (:1996), `GOLDENMATCH_FS_CALIBRATED` (:54),
`GOLDENMATCH_FS_MONOTONIC` (:135).
Bucket internals: `GOLDENMATCH_BUCKET_DEBUG`, `GOLDENMATCH_BUCKET_VEC_MIN/MAX`,
`GOLDENMATCH_BUCKET_SLIM_PROJECTION` (score_buckets.py:52,621-622,636).
Bench/misc: `GOLDENMATCH_BENCH_DUMP_PAIRS` (pipeline.py:2581), `GOLDENMATCH_FRAME`
(fused_match.py:162), `GOLDENMATCH_PREPARED_RECORD_STORE_DIR/_PERSIST` (pipeline.py:3787-3789).

## 8. Appendix — coverage matrix (route × feature)

✅ tested · ⚠️ partial/indirect · ❌ none found

| Route | NE | level_thresholds | TF | model reuse | match mode | multi_pass | oversized |
|---|---|---|---|---|---|---|---|
| scalar | ✅ | ✅ | ⚠️ (no-op, F3) | ✅ | ❌ | ⚠️ | ⚠️ |
| vectorized | ✅ | ✅ | ✅ (only route) | ✅ | ❌ | ⚠️ | ❌ |
| batched | ✅ | ⚠️ | ❌ | ✅ | ❌ | ✅ | ⚠️ |
| bucket-python | ✅ | ⚠️ | ✅ (weighted only) | ✅ (#1799) | ❌ | ✅ | ✅ |
| bucket-native | ✅ | ✅ | ❌ (declines) | ⚠️ | ❌ | ⚠️ | ✅ (skip, no split) |
| native per-block | ✅ | ✅ | ❌ (declines) | ⚠️ | ❌ | ❌ | ⚠️ |
| fused FS | ✅ (kernel) | ✅ (kernel) | ❌ | ❌ | ❌ | ❌ | ❌ (orphaned anyway) |
| continuous | ✅ (rejects) | ⚠️ | ❌ | ⚠️ | ❌ | ⚠️ | ❌ |

All native parity `skipif` kernel-built; max parity scale ~150 rows (Febrl3 ~5k in the opt-in
benchmark lane).
