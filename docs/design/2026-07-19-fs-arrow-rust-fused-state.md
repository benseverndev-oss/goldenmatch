# GoldenMatch Fellegi-Sunter: Arrow-native, Rust, and fused compute — current state

Status snapshot as of 2026-07-19. Written from a full read of the working tree
(branch `claude/fs-net-zero-evidence-filter`, i.e. `main` + the net-zero-evidence
filter of PR #1899). Every claim is tagged `file:line`. Where a capability lives on
an in-flight branch and NOT on `main`, it says so explicitly — see
[§9 On main vs in-flight](#9-whats-on-main-vs-in-flight).

Two things are called "Arrow" in the FS path and they are NOT the same:

- **(a) Arrow as kernel INPUT** — zero-copy Arrow buffers handed *into* the Rust
  kernel (`score_block_pairs_fs_arrow`). **Present and active on `main`.**
- **(b) Arrow pair-stream as scoring OUTPUT** — a `pa.Table`/`pl.DataFrame` of
  scored pairs *out of* `score_buckets` instead of `list[tuple]`. **Not in the
  tree** (`score_buckets_arrow` / `pairs_to_pair_stream` / `_split_pair_stream`
  live on PR #1896's branch); the *pair-stream schema* and the fuzzy-path
  pair-stream do exist.

---

## 1. The layer cake

FS scoring is four cooperating layers. A `type: probabilistic` matchkey enters at
the top; the bottom is the arithmetic.

| Layer | Where | Role |
|-------|-------|------|
| **Pipeline lane** | `pipeline.py::_score_probabilistic_matchkey` | Picks bucket vs external-blocks vs bench vs batched route |
| **Scorer kind** | `probabilistic.py::probabilistic_block_scorer` | Picks native-Rust vs numpy-vectorized vs numpy-batch vs scalar |
| **Native caller** | `probabilistic.py::_score_fs_native_frame` | Marshals a block/bucket to the kernel — Arrow columns or plain lists |
| **Rust kernel** | `fs-core::score_fs_pair` (+ `native`/`fused` pyo3 entries) | The FS math: similarity → level → weight → normalize |

"Fused" compute is a *fifth* path that collapses the bottom three into one FFI
crossing for the dedupe case — see [§8](#8-fused-fs-compute).

---

## 2. The Rust FS kernel (`fs-core`)

`fs-core` is the pyo3-free single source of truth for the FS scoring math, shared
by the `native` extension, the `fs-wasm` surface, and (transitively) the DuckDB /
Postgres surfaces; per-string similarity lives in `score-core`, reference data is
host-injected and never bundled (`fs-core/src/lib.rs:1-15`). **Rust is the
reference implementation; pure-Python/numpy is the lossy fallback**
(`fs-core/src/lib.rs:34-37`).

### 2.1 `score_fs_pair` — the per-pair pipeline

`score_fs_pair` (`fs-core/src/lib.rs:551-641`) is generic over two field accessors
`get_field`/`get_ne` returning `Option<&str>`, so the Vec and Arrow entry points
share one implementation (`fs-core/src/lib.rs:551-561`). Per pair:

1. Loop regular fields; a field contributes only when BOTH sides are non-null,
   setting `has_regular_evidence` (`fs-core/src/lib.rs:566-567`).
2. Per observed field: compute `sim` (embedding branch vs `field_similarity`),
   band it to a level via `fs_level_from_sim`, add `match_weights[f][level]` to
   `total_weight`, and add `field_mins[f]`/`field_maxs[f]` to the pair's
   `pair_min`/`pair_max` (`fs-core/src/lib.rs:573-597`).
3. Term-frequency: on exact-equal top-level agreement, add `tf.adjustment(a)`
   (`fs-core/src/lib.rs:602-609`).
4. Negative-evidence loop (`fs-core/src/lib.rs:614-624`), then normalize.

Level banding — `fs_level_from_sim` (`fs-core/src/lib.rs:274-306`): custom
`level_thresholds` = count of thresholds `<= sim` (inclusive); else legacy 2-level
(`sim >= partial`), 3-level (`>=0.95`→2, `>=partial`→1), or N-even.

Normalize — `fs_normalize` (`fs-core/src/lib.rs:250-265`): if `calibrated`,
posterior `1/(1+2^-(prior_w+W))` with logodds clamped `[-60,60]`; else linear
min-max `clamp((W - min_weight)/weight_range, 0, 1)`; degenerate range → `0.5`.

Semantics edges the kernel encodes:
- **Missing/null** — a field with either side null contributes no weight and is
  excluded from the pair's min-max range (`fs-core/src/lib.rs:567`).
- **Negative evidence** — fires iff both NE values present AND both non-empty AND
  `field_similarity(...) < ne_threshold` (strictly below); empty string is
  inconclusive, deliberately unlike regular fields' null→level-0
  (`fs-core/src/lib.rs:614-624`).
- **TF (Winkler)** — non-embedding field, exact top-level agreement only;
  `adjustment = clamp(log2(collision/freq(value)), ±10)`, 0.0 for OOV
  (`fs-core/src/lib.rs:602-609`, `TfTable` at `391-414`).
- **Embeddings** — for scorer id 7, `sim = dot` of the two rows' host-precomputed
  L2-normalized vectors; missing vectors degrade to 0.0
  (`fs-core/src/lib.rs:573-585`).
- **`require_positive_evidence`** (net-zero filter, linear only) — if
  `total_weight <= 0.0` returns the below-cut sentinel `-1.0`; else the
  no-regular-evidence/zero-weight pair returns neutral `0.5`; else normal
  `fs_normalize` (`fs-core/src/lib.rs:625-640`). No-op under `calibrated`.

### 2.2 `FsPairParams` — the full field set

`FsPairParams` (`fs-core/src/lib.rs:315-373`): `scorer_ids`, `levels`,
`partial_thresholds`, `field_thresholds`, `match_weights`, `field_mins`,
`field_maxs`, `base_min`, `base_max`, `ne_scorer_ids`, `ne_thresholds`,
`ne_weights`, `calibrated`, `prior_w`, `surname_freq`, `name_aliases`,
`tf_tables`, `emb_vectors`, `emb_dims`, `require_positive_evidence`. `base_min`/
`base_max` are NE-aware range endpoints minus the summed regular-field min/max, so
only observed fields' ranges are re-added per pair (`fs-core/src/lib.rs:308-314`).

### 2.3 Scorer IDs and the id-4 collision

`score-core::score_one` (`score-core/src/lib.rs:140-156`): `0`=jaro_winkler,
`1`=levenshtein, `2`=token_sort (**unscaled** `fuzz::ratio` on `[0,1]`, pinned),
`3`=exact, `4`=date, catch-all→`0.0`.

`fs-core::field_similarity` (`fs-core/src/lib.rs:513-533`) intercepts reserved ids
before delegating: `4`=name_freq_weighted (surname table, else JW), `5`=given_name_
aliased, `6`=ensemble (`max(JW, token_sort, 0.8·soundex)`), `7`=embedding cosine,
else `score_one`. **So within the FS field dispatch, id 4 means name-freq, not
date** — a documented meaning collision (`fs-core/src/lib.rs:419,522-525` vs
`score-core/src/lib.rs:133,153`).

Other documented parity edges: non-ASCII `normalize_name`/`soundex`
(`fs-core/src/lib.rs:44-47,435-448`); FS vs numpy is within float tolerance, not
bit-exact — a pair can diverge only if its normalized score sits within tolerance
of the threshold (`score.rs:377-379`).

### 2.4 pyo3 entry points and capability consts

Two block FS entries in `native/src/score.rs`, byte-identical to each other:
- **`score_block_pairs_fs`** (`score.rs:382-608`) — plain Python lists in,
  `list[(i64,i64,f64)]` out.
- **`score_block_pairs_fs_arrow`** (`score.rs:835-1101`) — **the zero-copy Arrow
  entry.** Takes `row_ids` + `field_arrays` as `PyArrowType<ArrayData>`
  (Int64 / Utf8), read via the C Data Interface; same `score_fs_pair` math; same
  `#688` sequential-vs-rayon guard (`score.rs:1088-1100`).

Wheel-skew capability flags in `native/src/lib.rs` (all `true`; Python passes a
gated kwarg only when its flag is present, so an older wheel degrades gracefully):
`FS_SUPPORTS_LEVEL_THRESHOLDS` (`lib.rs:30`), `FS_SUPPORTS_NE` (`35`),
`FS_SUPPORTS_MISSING_NEUTRAL` (`38`), `FUSED_FS_SUPPORTS_LEVEL_THRESHOLDS` (`41`),
`FS_SUPPORTS_EXCLUDE_SET` (`45`), `FS_SUPPORTS_ARROW` (`49`),
`FS_SUPPORTS_NAME_SCORERS` (`56`), `FS_SUPPORTS_TF_ADJUSTMENT` (`61`),
`FS_SUPPORTS_ENSEMBLE` (`67`), `FS_SUPPORTS_EMBEDDING` (`73`),
`FS_SUPPORTS_REQUIRE_POSITIVE_EVIDENCE` (`78`).

### 2.5 EM is NOT in Rust

There is **no `em_core.rs`** and no EM implementation (full or scaffold) anywhere
in the extensions tree — `score-core/src` is a single `lib.rs`. The kernel only
*consumes* EM outputs (`match_weights`, `prior_w`, weight envelope, host-built
`tf_freqs`/`tf_collision`; `fs-core/src/lib.rs:385-390`). EM training stays
host-side (see [§7](#7-em-training-host-side)).

---

## 3. Python routing — lane, then scorer kind

All refs `packages/python/goldenmatch/goldenmatch/`.

### 3.1 Lane selection — `_score_probabilistic_matchkey`

The one shared body used by dedupe + both match lanes + the TUI engine
(`core/pipeline.py:273-442`; call sites `pipeline.py:2991,4391,4588`), in order:

1. `_fs_use_bucket_route(config, mk)` → **bucket route** via `score_buckets(...)`.
   **This is the default** (`pipeline.py:331,363-394`).
2. else external-blocks strategies (lsh/ann/learned/canopy/sorted_neighborhood) →
   `score_probabilistic_external_blocks(...)` (`pipeline.py:398-412`).
3. else `bench_dump_dir` set → per-block `probabilistic_block_scorer` loop
   (`pipeline.py:415-429`).
4. else → `score_probabilistic_blocks_batched(...)` (legacy batched numpy)
   (`pipeline.py:433-442`).

`_fs_use_bucket_route` (`pipeline.py:177-236`): `backend=="bucket"` → True;
another explicit scale backend → False; `GOLDENMATCH_FS_DEFAULT_BUCKET` opt-out
(default `"1"`) → False; blocking strategy not in `{None,static,multi_pass}` →
False; an active profile emitter (auto-config sample runs) → False. **The bucket
route does not require the native kernel** — its non-native lane still scores via
the numpy per-block scorer (`pipeline.py:188-191`).

### 3.2 Scorer kind — `probabilistic_block_scorer`

`probabilistic.py:3635-3665`, preference order:

1. **Native Rust** if `_fs_native_eligible(mk)` → closure over
   `score_probabilistic_native` (`probabilistic.py:3645-3648`).
2. else **numpy vectorized** if `use_vec` (`probabilistic.py:3654-3661`);
   `requires_vec` forces this for model-backed scorers even under
   `GOLDENMATCH_FS_VECTORIZED=0` (`probabilistic.py:3650-3657`).
3. else **scalar** `score_probabilistic` (`probabilistic.py:3663-3665`).

Gates and their defaults:
- `_fs_native_enabled()` (`probabilistic.py:3136-3159`) — **DEFAULT ON**
  ("reference mode"); `GOLDENMATCH_FS_NATIVE=0` forces the fallback.
- `_fs_native_eligible(mk)` (`probabilistic.py:3162-3257`) — requires native
  enabled, non-empty fields, `fs_missing_mode(mk) != "disagree"` (native does
  neutral-missing only), every field + NE scorer in `_NATIVE_FS_SCORER_IDS`, and
  per-feature wheel capability-const checks.
- `_fs_vectorized_enabled()` (`probabilistic.py:3044-3054`) — **DEFAULT ON**;
  `GOLDENMATCH_FS_VECTORIZED=0` drops to scalar for non-model-backed matchkeys.
- Bucket internal dispatch: `fs_bucket_native = _fs_bucket_native_enabled() and
  _fs_native_eligible(mk)` (`backends/score_buckets.py:935`); when true a whole
  block-sorted bucket is scored in ONE `score_probabilistic_bucket_native` call
  (`score_buckets.py:1380-1400`); `_fs_bucket_native_enabled()` is **DEFAULT ON**,
  `GOLDENMATCH_FS_BUCKET_NATIVE=0` forces the per-block loop
  (`score_buckets.py:57-67`).

**Net effect at default env:** the native kernel runs whenever the matchkey's
scorers are all kernel-implementable and the installed wheel advertises them;
numpy-vectorized runs when a scorer/feature isn't kernel-eligible or
`GOLDENMATCH_FS_NATIVE=0`; scalar only under `GOLDENMATCH_FS_VECTORIZED=0` (or the
bench/per-block loops) for non-model-backed matchkeys.

### 3.3 The native caller — Arrow columns vs plain lists

`score_probabilistic_native` (`probabilistic.py:3576-3597`) and the whole-bucket
`score_probabilistic_bucket_native` (`probabilistic.py:3600-3632`) both funnel into
`_score_fs_native_frame` (`probabilistic.py:3363-3573`), which carries **both**
marshaling paths, selected by `use_arrow = bool(getattr(mod, "FS_SUPPORTS_ARROW",
False))` (`probabilistic.py:3420`):
- **Arrow** → `score_block_pairs_fs_arrow`, per-field Arrow columns via
  `_fs_arrow_column` (zero-copy for untransformed utf8)
  (`probabilistic.py:3525-3557`).
- **plain list** → `score_block_pairs_fs`, `field_values` via
  `_field_values_for_block` (`probabilistic.py:3559-3573`).

`opt_kwargs` is built capability-gated — each group added only when the feature is
used AND the wheel advertises its const (`probabilistic.py:3439-3523`):
`require_positive_evidence` (`3448-3451`), `level_thresholds` (`3453-3459`), NE
arrays (`3469-3487`), `tf_freqs`/`tf_collision` (`3494-3512`),
`emb_vectors`/`emb_dims` (`3519-3523`).

### 3.4 The numpy scorers

- `score_probabilistic_vectorized` (`probabilistic.py:2653-2772`) — one NxN
  `cdist`/`_field_score_matrix_dedup` matrix per field + numpy
  level/weight/normalize; single block, upper-triangle emit.
- `score_probabilistic_vectorized_batch` (`probabilistic.py:2794-2912`) —
  coalesces blocks into one SxS matrix, slices each block's diagonal sub-matrix;
  row cap `GOLDENMATCH_FS_BATCH_ROWS`, **default 256** (`probabilistic.py:2775-2791`).
- scalar `score_probabilistic` (`probabilistic.py:2244-2351`) — per-pair Python
  double loop; only the vectorized-disabled/unsupported non-model-backed path.
- `_fs_vec_guard(n, fn)` (`probabilistic.py:2640-2650`) refuses a block whose
  `n*n` exceeds `_fs_vec_max_elems()` (default **5e7** ≈ 7,071 rows;
  `GOLDENMATCH_FS_VEC_MAX_ELEMS`).

---

## 4. Arrow (a): kernel INPUT — present

The Arrow-input path is live on both scale routes:
- Weighted fast path → `score_block_pairs_arrow`.
- **FS path → `score_block_pairs_fs_arrow` / `score_probabilistic_bucket_native`**
  when `FS_SUPPORTS_ARROW` (`probabilistic.py:3420,3525-3557`;
  `score_buckets.py:1380-1400`).

`score_buckets` branches its Arrow-vs-Polars lanes via the import-free
`is_polars_dataframe` type guard rather than a bare `isinstance` (e.g.
`score_buckets.py:1094-1101,1370-1377`), which keeps the arrow lane polars-free
(see [§6](#6-the-polars-free-frame-seam)).

---

## 5. Arrow (b): pair-stream OUTPUT — partial

A grep of the working tree:

| Symbol | Status | Evidence |
|--------|--------|----------|
| `score_buckets_arrow` | **ABSENT** | 0 occurrences — on PR #1896's branch |
| `pairs_to_pair_stream` | **ABSENT** | 0 occurrences |
| `_split_pair_stream` | **ABSENT** | 0 occurrences |
| `PAIR_STREAM_SCHEMA` | PRESENT | `core/scorer.py:1744-1786`; used in `pairs.py`, `cluster.py` |
| `PAIR_STREAM_SCHEMA_SPEC` | PRESENT | `core/frame.py:2138-2139` — `{id_a:int64,id_b:int64,score:float64}` |

`score_buckets` still returns `list[tuple[int,int,float]]`
(`score_buckets.py:701,1693`). The pair-stream *schema* exists, and the *fuzzy*
scorer/cluster path already emits a `PAIR_STREAM_SCHEMA` frame
(`scorer.py:1095-1121`), but the **bucket FS scorer does not emit a pair-stream on
this branch** — that is PR #1896's work.

---

## 6. The polars-free Frame seam

Orthogonal to FS but load-bearing for the Arrow lane. `core/frame.py` is a
backend-neutral `Frame`/`Column` seam (`frame.py:1-14`) with `PolarsFrame` and
`ArrowFrame` (over `pa.Table`) backends. `resolve_frame_backend()`
(`frame.py:28-55`) reads `GOLDENMATCH_FRAME`, **default `"arrow"` since v3.0.0**
(measured faster: 100K zero-config 76.4s arrow vs 119.1s polars); `"polars"` is now
the opt-out escape hatch. `tests/test_zero_polars_gate.py` asserts an eligible
arrow-lane dedupe (both native gates) plus the CLI/web imports never import polars.
Note the *separate* `GOLDENMATCH_FRAME_LANE` (default `"1"`) gates the arrow spine
collect — don't conflate the two.

---

## 7. EM training (host-side)

`train_em` (`probabilistic.py:1067-1408+`) is **pure Python/numpy — no native
Rust**: u from random pairs via numpy counts (`1158-1167`); the EM loop is a Python
`for` with numpy E-step (`1265-1294`) and M-step (`1300-1331`); weights `log2(m/u)`
via `math.log2` (`1354-1386`). `train_em_continuous` (`1746-1795+`) is likewise
pure numpy. Native code is invoked **only at scoring time**
(`native_module()` in `_score_fs_native_frame`, `probabilistic.py:3387,3412`).

---

## 8. Fused FS compute

Fused is a *separate, opt-in* path that does block-build + score + dedup + cluster
in one Rust FFI crossing (`native/src/fused.rs:1-2`), reusing the same
source-of-truth kernels (`group_block_positions`, `fs_level_from_sim` +
`fs_normalize`, `dedup_pairs_max_score` + `connected_components`;
`fused.rs:5-9,24-28`).

- **Entry:** `match_fused_fs` (`fused.rs:262-281`, registered `lib.rs:93`) — Arrow
  `row_ids`/`key_fields`/`score_fields`/`ne_fields` in, `list[list[i64]]`
  (connected components incl. singletons) out; all under `py.detach`.
- **Scoring is byte-identical** to `score_block_pairs_fs` — but note `match_fused_fs`
  re-implements the loop with `score_one` directly rather than calling
  `score_fs_pair` (`fused.rs:410-445`).
- **DEDUPE only.** The short-circuit is wired into `_run_dedupe_pipeline`
  (`pipeline.py:2601-2645`) via `_run_fused_fs_match_short_circuit`
  (`pipeline.py:2101`); the across-files (record-linkage) case returns None
  (`pipeline.py:2136-2137`).
- **Condition:** `config._use_fused_match and not config_needs_artifacts(config)`
  (`pipeline.py:2619`). Readiness: `match_fused_fs_ready` /
  `_multipass_ready` (`fused_match.py:258,345`) require a static/multi_pass single
  blocking key, one `probabilistic` matchkey, and every field on an FS-native
  scorer in `_FUSED_FS_SCORER_IDS = {jaro_winkler,levenshtein,token_sort,exact}`
  (`fused_match.py:296-341`).
- **When it wins:** a MEMORY/composability escape hatch, **not a speed win**
  (`fused_match.py:4-11`); ~2x lower peak RSS at wall-neutral, clusters
  byte-identical. The planner fires it only under memory pressure and only when
  `config_needs_artifacts` is False (rare — `auto_split` defaults True)
  (`fused_routing.py:130-135,219-259`).
- **Capability subset — SMALLER than the block kernel.** Fused carries NE
  (`fused.rs:425-440`) and level_thresholds (`FUSED_FS_SUPPORTS_LEVEL_THRESHOLDS`,
  `lib.rs:41`) only. **NO TF, NO embeddings, NO name/ensemble scorers, NO
  `require_positive_evidence`** — a config needing any of those fails
  `_fused_fs_matchkey_covered` and declines to the classic block/bucket FS path
  (`fused_match.py:315`).

---

## 9. What's on main vs in-flight

| Capability | State |
|------------|-------|
| Rust `fs-core` reference kernel + `score_fs_pair` | **main** |
| Arrow-INPUT entry `score_block_pairs_fs_arrow` (`FS_SUPPORTS_ARROW`) | **main** |
| Native reference-mode default-on routing | **main** |
| Fused FS dedupe short-circuit (`match_fused_fs`) | **main** |
| Bucket route as default FS lane | **main** |
| Net-zero-evidence filter (`require_positive_evidence`), numpy + Rust, default ON | **this branch — PR #1899** (`fs-core/src/lib.rs:625-640`, `lib.rs:78`, commits 636392a/73217d3) |
| Arrow pair-STREAM OUTPUT (`score_buckets_arrow`, `pairs_to_pair_stream`, `_split_pair_stream`) | **PR #1896 branch — NOT on main, NOT here** |

The doc above describes the working tree (main + PR #1899). Treat the last two rows
as the moving edge.

---

## 10. Semantics knobs and env vars

| Env var | Controls | Default | Ref |
|---------|----------|---------|-----|
| `GOLDENMATCH_FS_NATIVE` | native kernel on/off (reference mode) | **on** | `probabilistic.py:3136-3159` |
| `GOLDENMATCH_FS_BUCKET_NATIVE` | whole-bucket native call vs per-block | **on** | `score_buckets.py:57-67` |
| `GOLDENMATCH_FS_VECTORIZED` | numpy-vectorized vs scalar | **on** | `probabilistic.py:3044-3054` |
| `GOLDENMATCH_FS_DEFAULT_BUCKET` | bucket route as default lane | **on** (`"1"`) | `pipeline.py:212-223` |
| `GOLDENMATCH_FS_CALIBRATED` | `linear` vs `posterior` normalization | **`linear`** | `probabilistic.py:65-75` |
| `GOLDENMATCH_FS_MISSING` | `unobserved` vs `disagree` null handling | **`unobserved`** | `probabilistic.py:113-134` |
| `GOLDENMATCH_FS_REQUIRE_POSITIVE_EVIDENCE` | drop net-zero (W≤0) pairs, linear only | **on** | `probabilistic.py:78-107` |
| `GOLDENMATCH_FS_BATCH_ROWS` | batched-path SxS row cap | **256** | `probabilistic.py:2775-2791` |
| `GOLDENMATCH_FS_VEC_MAX_ELEMS` | vectorized block guard | **5e7** | `probabilistic.py:2614-2637` |
| `GOLDENMATCH_FRAME` | arrow vs polars Frame backend | **`arrow`** | `frame.py:28-55` |

`prior_weight` / `posterior_from_weight` (`probabilistic.py:137-159`) apply only
under `calibrated`; posterior uses a 0.99 link cut. `require_positive_evidence` is
a no-op under posterior (`probabilistic.py:95-96`).
