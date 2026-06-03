# Arrow-native finish-line roadmap

**Date:** 2026-06-01
**Status:** design (approved, pre-plan)
**Supersedes execution of:** `2026-05-31-arrow-native-roadmap.md` (the original
7-phase design). This document does NOT redefine the phases. The kill criteria
are carried over unchanged from the original spec, with one clarification: the
Phase 3 `dedup_pairs >= 5x` gate is taken from issue #625 (where `dedup_pairs`
became a real shipped kernel, PR #643), not from the original roadmap doc, which
listed `dedup_pairs` only as a future deliverable with no number. It re-casts
the work as a completion plan grounded in the actual state after the first-day
implementation burst (PRs #630-#653).

## Why this document exists

The original roadmap defined 7 phases, each with a binding kill criterion
measured in CI. Over 2026-05-31 -> 06-01, PRs #630-#653 landed a large amount
of the capability, but an audit on 2026-06-01 found a consistent pattern:

- Capability + parity tests were built as **additive siblings behind opt-in
  gates** (`GOLDENMATCH_COLUMNAR_PIPELINE`, the `native_enabled(...)` flags).
- The **cutover was not done**: the legacy `list[tuple]` pair stream and
  `dict[int, dict]` cluster representation are still the defaults, ~20 callers
  still consume the legacy shapes, and `materialize_cluster_dict` is still on
  the distributed path.
- The **binding kill-criterion benches were never run**. Issues #623-#628 are
  all open and partial; #629 (Phase 7) is correctly unstarted.

So "cross the finish line" is not new design. It is: measure the built-but-gated
capability, then cut it over to default in dependency order, retiring the legacy
paths and closing the issues.

## Decisions that shape this plan

1. **Finish line = validate, then full cutover.** Columnar/native becomes the
   DEFAULT path; the legacy `list[tuple]` / `dict[int, dict]` paths are retired;
   the ~20 callers are migrated; issues #623-#629 are resolved.
2. **Gate policy = hold and optimize to the gate.** A phase does not cut over
   until its kill-criterion bench clears the original target. If a bench comes
   in below target, profile and tune until it passes. (Stall backstop below.)
3. **Phase 7 (DataFusion B2 / #629) stays gated.** No DataFusion code until a
   100M-on-one-box bucket bench (run after Phases 1-3 cut over) says bucket
   strains at >100M. Otherwise shelve and close #629.

## Approach: measurement-first sweep, then dependency-ordered cutover

```
Stage 0  Bench sweep ── measures Phases 1,2,3,4,6 against current gated capability
   |                    (Phase 5 not built enough to sweep; benched after it lands)
   v
Phase 1  Pair stream ──► Phase 2  Cluster columnar ──► [ Phase 3  Native kernels
   (substrate)                                          [ Phase 4  Golden columnar
                                                              |
                                                              v
Phase 6  Standardization (parallel, independent surface)  Phase 5  Identity per-partition
                                                              |
                                                              v
                                                        Phase 7  DataFusion B2 (GATED)
```

Rationale: the "hold and optimize to the gate" policy makes "which gates pass?"
the pivot of the entire plan, and that is currently unknown. A cheap upfront
sweep (the capability already exists behind flags) turns that unknown into a map
before investing in the wide-surface cutover and caller-migration work. Cutover
then proceeds in strict dependency order so Phase 1's columnar pair stream (the
substrate the rest consume) lands before anything builds on it.

## Stage 0: Bench sweep

A single job that runs every existing kill-criterion bench against the
capability already behind the columnar/native flags, producing one decision
artifact before any cutover starts. Estimated 2-3 days.

- **Fixture:** `realistic_person` (the profiler already uses it). The original
  spec's degenerate Day-3 fixture is retired. Confirm the fixture scales to
  5M / 25M; generate larger inputs via the Railway `goldenmatch-bench-gen`
  service if the in-repo fixture is too slow to build at scale.
- **Scales / box:** 5M and 25M on `large-new-64GB`, matching each phase's kill
  criterion (Phase 1: 5M; Phases 2/4: 25M; Phase 3: 5M; Phase 6: 10M).
- **Per phase, record:** columnar/native wall + peak RSS vs the legacy path,
  and parity (byte-identical pairs/fingerprints, Rand index 1.0 on cluster
  assignments).
- **Output:** a results table appended to this spec, classifying each phase:
  - **PASS** - gate already met; cutover is pure plumbing.
  - **CLOSE** - beats legacy but under target; needs optimization.
  - **BLOCKED** - parity fails, or not measurable yet (Phase 5).
  This table drives all downstream effort allocation. Because
  `docs/superpowers/specs/` is gitignored (commit with `git add -f`), the
  Stage-0 results table is committed back into this spec so the gate decisions
  are version-controlled and do not get lost.

## The per-phase cutover unit

Every phase after Stage 0 runs the same four steps:

1. **Bench** - take the Stage-0 number (or run it fresh, for Phase 5).
2. **Optimize to gate** - if below target, profile + tune until it clears.
   **Stall backstop:** if two optimization passes do not reach the gate, stop
   and escalate with the measured ceiling and a recommendation
   (cut-over-anyway / re-scope / shelve). This is the only place "hold and
   optimize" is allowed to terminate without clearing the bar.
3. **Cut over** - flip the default to columnar/native, migrate the callers,
   retire the legacy path (see retirement strategy below).
4. **Close** - record the passing bench in this spec; close the issue.

### What cutover means per phase (from the audit gap list)

| Phase | Gate (must clear first) | Cutover work |
|---|---|---|
| **1** pair stream (#623) | handoff wall <= 50%, RSS <= 25% @5M, byte-identical | `find_fuzzy_matches` returns `pl.DataFrame` by default; migrate ~20 `scored_pairs: list[tuple]` callers (web/MCP/REST/lineage); add CI lint banning the annotation |
| **2** cluster columnar (#624) | RSS -30% @25M, `materialize_cluster_dict` retired | `build_clusters` returns the two-frame shape; move `pair_scores` + confidence to the lazy view; adapt `unmerge_record`/`unmerge_cluster` |
| **3** native kernels (#625) | dedup >= 5x, build_clusters >= 2x, fingerprints >= 3x @5M (each vs its pure-Python loop baseline); parity | wire native default; close the `build_clusters_arrow` scope gap (auto-split, weak-cluster downgrade, quality) so it is a true drop-in for `build_clusters` |
| **4** golden columnar (#626) | golden <= 60s, RSS <= 60% @25M | distributed `build_golden_records` consumes `ClusterFrames`; drop `materialize_cluster_dict` from the distributed path |
| **5** identity per-partition (#627) | wall <= 50%, driver RSS <= 10% @25M/4-worker | real `ds.map_batches(per_partition_resolver)` with a per-worker pool budget (only the `partition_cluster_frames` helper exists today) |
| **6** standardization (#628) | `apply_standardization` <= 20s @10M, zero full-df `map_elements` | replace the justified-but-present `map_elements` sites in `standardize.py`/`matchkey.py` with native Polars/Arrow; the `check_map_elements.py` CI lint already guards regressions |

## Sequencing and dependencies

- Strict dependency order: **1 -> 2 -> {3, 4} -> 5**. Phase 1's columnar pair
  stream is the substrate Phases 2-5 consume, so it lands first despite being
  the widest surface; Stage 0 gives early signal to offset that front-loaded
  risk.
- **Phase 6 runs in parallel from day one** (prep stage, independent surface).
- **Phase 5** is gated on its bench being run after it is actually built (it is
  the least-complete phase today: only the partitioner helper exists).
- **Phase 7** decision happens after Phases 1-3 cut over.

## Cross-cutting concerns

### Legacy retirement: one-release deprecation window per phase

Full cutover means deleting the legacy `list[tuple]` / `dict[int, dict]` paths,
but deleting them in the same PR that flips the default is where silent breakage
lives (the audit counted ~20 live `scored_pairs` consumers across web/MCP/REST/
lineage). So per phase:

- **Release N:** columnar/native becomes the default; the legacy path stays
  reachable behind the existing flag as an escape hatch; callers migrated; a CI
  lint bans new legacy-shape annotations.
- **Release N+1:** delete the legacy path + the flag once the default has soaked
  with no regressions.

This keeps a one-release rollback lever and turns "did we miss a caller?" from a
silent behavior change into a flag-toggle diagnosis.

### Parity discipline

Cutover is blocked until parity is proven at bench scale, not just unit scale:
byte-identical pairs/fingerprints, Rand index 1.0 on cluster assignments,
identical golden output. The parity tests from PRs #631-#650 already exist per
phase; Stage 0 and each cutover re-run them at 5M / 25M, not only the fixture
sizes they currently cover.

### Phase 7 decision gate (DataFusion B2 / #629)

Stays closed-but-open. After Phases 1-3 cut over, run a **100M-on-one-box
bucket+native bench** on `large-new-64GB` (the strategic "do we need a query
engine at all?" question). Then a one-page decision artifact:

- If bucket holds 100M in <= 30 min: **shelve Phase 7, close #629** ("DataFusion
  not needed at this scale"), and remove the `[datafusion]` spike extra.
- If bucket strains: green-light the Rust-ScalarUDF / PyCapsule build per the
  #629 deliverables.

No DataFusion code is written until that bench decides.

## Definition of done (the finish line)

- Stage 0 sweep recorded; every phase bench at its kill scale recorded in this
  spec.
- Phases 1-6 cut over: columnar/native is the DEFAULT, legacy paths deleted
  (after the N+1 window), issues #623-#628 closed.
- Phase 7 decided (shipped or shelved); #629 resolved either way.
- The 100M-on-one-box number is published, which also answers the standing
  "do we need Ray?" question from 2026-05-30.

## Effort estimate

- Stage 0 sweep: 2-3 days.
- Cutover per phase: the original spec's estimates still hold for the
  optimize+migrate work (Phase 1: 1-2 wk, Phase 2: 1-2 wk, Phase 3: ~1 wk,
  Phase 4: 3-5 d, Phase 5: ~1 wk, Phase 6: 1-2 wk parallel). Phases that come
  back PASS in Stage 0 collapse to plumbing + caller migration only (days, not
  weeks).
- Total: ~5-7 weeks solo, less if Stage 0 shows several gates already PASS.

## Stage 0 results (2026-06-01)

First sweep run (`arrow-finish-line-sweep.yml`, `large-new-64GB`). Source runs:
26770239634 (phase1 @ kill, FAILED) + 26770594830 (phase2,3,4,6 @ kill, success)
+ profiler 26759043853 (phase1 @ 1M, from the earlier hotspot run).

| Phase | Verdict | Numbers | Read |
|---|---|---|---|
| **1** pair stream | CLOSE (and a hard signal) | 1M profiler: columnar 354.9s vs list 564.3s = **0.63 ratio** (misses 0.50). At **5M kill scale the legacy `list` path OOM'd the 64 GB box** (SIGTERM, ~2.5 min) -- the bench could not produce a baseline. | Cut over. The OOM IS the result: the legacy list pair-stream is untenable at 5M, which is exactly Phase 1's thesis. The 0.63 ratio at 1M means columnar wins but misses the 50% wall target -> optimize wall during cutover. |
| **3** native kernels | CLOSE | `dedup` **10.59x** (>= 5x PASS); `build_clusters` **1.09x** (< 2x); `fingerprints` **0.71x** (< 3x, i.e. native is SLOWER than Python here); parity OK. | Mixed. dedup is a clean win. build_clusters confirms the dict-floor (matches the original 1.09x failed gate). fingerprints REGRESSED -- needs investigation before any cutover (verify the bench shape + that the kernel actually releases the GIL / uses Arrow zero-copy). |
| **2** cluster columnar | BLOCKED | no kill-criterion bench wired yet. | Build the 25M cluster-RSS bench as the first step of the Phase 2 cutover. |
| **4** golden columnar | BLOCKED | no bench wired. | Build the 25M golden bench during Phase 4 cutover. |
| **6** standardization | BLOCKED | no bench wired. | Build the 10M prep-stage bench during Phase 6 cutover. |
| 5 identity | BLOCKED (excluded) | not built. | Per-partition `map_batches` not implemented; benches once built. |

**Follow-ups this sweep surfaced:**
1. **phase1 bench scale**: the driver runs phase1's legacy `list` path at 5M, which OOMs. Lower `PHASE_BENCH_SCALE["phase1"]` to 1M (where legacy still fits) OR cap only the legacy path, so the ratio is measurable. Small driver change.
2. **phase3 fingerprints 0.71x**: a native kernel slower than Python is a red flag -- confirm it's not a GIL-reacquire / non-zero-copy bug (Risk item in the original Phase 3 spec) before relying on the verdict.
3. **phase2/4/6 benches**: each phase's cutover must FIRST wire its kill-criterion bench (they don't exist yet); the sweep correctly reports BLOCKED, not a false pass.

**Cutover order decision:** Phase 1 first (substrate + proven-untenable legacy), then Phase 3
dedup (clean win) while build_clusters/fingerprints get the hold-and-optimize treatment.

## Phase-2 parity policy (2026-06-03 amendment)

**Why this amendment exists.** The recurring Phase-2 parity traps and a chunk of
the perf regressions trace to one root cause: we have been enforcing **bit-exact
ARTIFACT parity** with the legacy Python dict path, not just **SEMANTIC parity**.
Artifact parity (reproducing float summation order, last-wins dedup, pair-fill
order, first-in-pairs-order tie-breaks) is self-defeating for a columnar cutover:
bit-exact float confidence FORBIDS re-association (sort / vectorize / parallel
reduce), which is exactly the operation that makes columnar fast and memory-light.
The team already relaxed one artifact correctly — member order → members-as-set
(#598), because native UF order is arbitrary. That is the template for the rest.

This policy classifies each dict artifact keep-semantic vs relax-to-semantic,
grounded in the actual code path (read 2026-06-03, current through #693).

### The parity-policy table (grounded in code)

| Artifact | Code path | Currently pinned by | Classify | Resolution |
|---|---|---|---|---|
| cluster partition / membership | members `list` w/ order declared non-load-bearing (`cluster.py:800-814`); `_frames_iter` group_by-agg, set-compared (`resolve.py:231-245`) | `test_cluster_frames_out_parity.py:17-21` (`_norm`→`frozenset`); Rand index | **keep-semantic** | strict: set equality / Rand 1.0. Already relaxed (#598). |
| golden record content / entity IDs | `build_golden_records_from_frames` delegates to shared `build_golden_records_df` (`golden.py:1089-1171`); `new_entity_id` UUIDv7 (`store.py:110-122`) | `test_golden_from_frames_parity.py`; `test_identity_from_frames_parity.py` (partition + det-mint legs) | **keep-semantic** | strict: golden content equality; entity = **partition** equality (random id) + deterministic-mint leg for literal |
| multi-matchkey pair dedup | MAX: `dedup_pairs_max_score` (`pairs.py:35-48`) → `scored_pairs` ONLY (`pipeline.py:1932-1937`). LAST-WINS: `_bucket_pairs` (`cluster_pairscores.py:21-26`) + dict fill (`cluster.py:816-819`), both over RAW `all_pairs` | **No test truly pins last-wins** — every dup fixture puts the higher score last, so last-wins==max on value (`test_columnar_drop_pairscores_parity.py:27`, `test_cluster_frames_out_parity.py:53`). Contract only *documented* (`cluster_pairscores.py:59-62`) | **RELAX → canonical MAX** | switch cluster `pair_scores` fill to MAX so both pipeline halves agree. **OUTPUT-CHANGING — sign-off + count below.** |
| confidence float value | `confidence = 0.4*min_edge + 0.3*avg_edge + 0.3*connectivity` (`cluster.py:1261`); `avg_edge = sum(scores)/len` (`:1251`) | `test_columnar_cluster_build_parity.py` (EXACT float, `_norm` keeps `confidence`); `test_native_cluster_orchestration_parity.py` (`approx, abs=1e-5`) | **RELAX → ε-parity** | order-free reduction; ε tolerance. `min_edge`/`connectivity` already order-free; only `avg_edge` sum is order-dependent. EXACT handling reserved for the threshold boundary (next row). |
| pair-fill / sequential-sum order | dict fill `cluster.py:816-819`; off-native `replace_strict`-for-order `cluster.py:1177-1190` (comment: "matching the dict path") | implied by the confidence-EXACT tests | **RELAX → order-free** | vectorize `avg_edge` (multiset sum is deterministic regardless of order, just ≠ the sequential sum by ≤~1e-9 f64 / ~1e-6 f32); drop the `replace_strict`-for-order machinery |
| bottleneck tie-break | `min(pair_scores.items(), key=itemgetter(1))` = **first-in-pairs-order** on ties (`cluster.py:1258`); MST weakest edge `min(mst,...)` + stable score-desc sort (`cluster.py:144,179`) — tie picks first-in-pairs-order, and can change WHICH edge is cut → **split membership** | `test_cluster.py:217` (no tie); `test_native_cluster_orchestration_parity.py:82-85` encodes first-in-order as contract | **INVESTIGATE → define order-free rule** | replace first-in-pairs-order with a **deterministic order-free** tie-break, e.g. lexicographic `(min_id, max_id)`, applied to BOTH bottleneck selection and MST equal-weight edge choice, so split membership is pair-order-independent. **OUTPUT-CHANGING on ties — sign-off + count.** |

### The one genuinely SEMANTIC float boundary (ε-parity must pin it)

`cluster.py:954` (and frames twins `:677`, dict-via-frames `:943`):
`(avg_edge - min_edge) > weak_cluster_threshold` (default 0.3) → `cluster_quality`
`"weak"`/`"strong"` + `confidence *= 0.7`. `min_edge` is order-free; `avg_edge` is
the order-dependent sum. A ≤1e-9 reassociation can flip the label for any cluster
whose `(avg_edge - min_edge)` sits within ε of 0.3. **The label flip is semantic
even though it is gated on a float artifact.** ε-parity rule: compute `avg_edge`
order-free (deterministic), and treat the weak/strong LABEL as a semantic
invariant — the count below measures how many clusters actually sit in the ε-band.

### THE RESIDUAL — confirmed absent (the cutover can show the RSS win)

Traced (Agent, 2026-06-03): with `GOLDENMATCH_CLUSTER_FRAMES_OUT=1` (identity ON,
output flags OFF), cluster→golden→identity runs **dict-free**. No `dict[int,dict]`
is rebuilt on the hot path: golden goes `build_golden_records_from_frames` (frame
in), identity consumes `_frames_iter(cluster_frames)` directly (`resolve.py:317`),
`pair_score_view` is `from_frames` (`pipeline.py:1904-1908`). The only `dict[int,
dict]` materializations are (a) the oversized-split minority inside
`build_cluster_frames`, and (b) the terminal `results["clusters"]` rebuild at
`pipeline.py:1940` — **after** the identity stage. So the dict-rebuild residual
that would hide the -30% RSS @25M win is absent on the measured window, PROVIDED
the bench does not force `results["clusters"]` into that window.

### Gate re-framing (supersedes the blanket "byte-identical" parity discipline for Phase 2)

The "Parity discipline" section above said "byte-identical pairs/fingerprints …
identical golden output" as a blanket cutover gate. For Phase 2 specifically,
split it:

- **Intermediate / per-SP gates = SEMANTIC parity + FEASIBILITY.** Rand index 1.0
  on partition, identical golden content, entity-partition equality — PLUS "does
  it run / not-OOM at scale." NOT float-exact confidence, NOT artifact (dedup
  order, fill order, tie order). A half-cutover's small-N RSS is a guaranteed
  false negative (pays for frames AND dict) and does NOT gate.
- **The RSS/wall VERDICT moves to the COMPLETE path at 25M+** — the roadmap's
  -30% RSS @25M criterion — measured on the most-complete frames path
  (`GOLDENMATCH_CLUSTER_FRAMES_OUT=1`), accounting for the terminal-dict residual
  (exclude `results["clusters"]` from the measured window).
- **`cluster_quality` label and the weak/strong boundary stay SEMANTIC** (ε-parity
  pins behavior at the 0.3 boundary; everything else about confidence is ε).

### Output-changing items awaiting explicit human sign-off (DO NOT ship silently)

Each is a customer-facing behavior change. Blast radius = count of affected
rows/clusters at 1M and 5M (measurement dispatched 2026-06-03; table filled on
return). NONE is implemented until reviewed.

1. **Cluster `pair_scores` LAST-WINS → MAX.** Aligns cluster metadata with the
   already-MAX `scored_pairs`. Affects: `confidence`, `bottleneck_pair`,
   `cluster_quality` (via `avg_edge`/`min_edge`), MST-split membership, and
   identity evidence-edge scores (`resolve.py:493/660`). Blast radius = clusters
   containing a different-score duplicate canonical pair. **Count @1M/5M: PENDING.**
2. **Order-free `avg_edge` (ε vs bit-exact confidence).** Affects: `confidence`
   float on every multi-edge cluster (≤~1e-6); `cluster_quality` LABEL flips ONLY
   for clusters within ε of the 0.3 weak boundary. Blast radius = clusters in the
   ε-band of the boundary. **Count of label flips @1M/5M: PENDING.**
3. **Order-free bottleneck / MST tie-break (lexicographic).** Affects:
   `bottleneck_pair` on min-edge ties; split partition membership on MST
   equal-weight ties. Blast radius = clusters with a tied min-edge or tied MST
   weight. **Count @1M/5M: PENDING.**

Items 1-3 are coupled (all flow from relaxing pair-order artifact parity); the
count script measures all three in one pass. **No cluster code changes until this
table is filled and the three items are signed off.**

## Scale mode decision (2026-06-03)

**Decision:** stop chasing bit-identical parity between the Arrow path and the
legacy Python dict path. Reframe `GOLDENMATCH_CLUSTER_FRAMES_OUT` into an
explicit, documented **mode** — `mode="scale"` vs `mode="standard"` — with
`"standard"` (today's mature exact path) remaining the default. Scale mode is the
frames-out complete path with relaxed *artifact* parity, dropped exotic features,
and a HARD determinism requirement.

**Why this is the right bar, not a cop-out.** At the scale this path is FOR, the
legacy dict path OOMs and produces NO output, so "bit-identical to legacy" is
*undefined* there. And no scale-oriented ER product offers identity at scale:
Splink guarantees reproducibility only given a fixed backend (results differ
across Spark/DuckDB/SQLite); Spark float aggregations are order-non-deterministic
unless forced. Holding the Arrow path to bit-identity was holding it to a bar no
competitor in the category meets. The win we keep: **the partition (membership)
stays exact** — connected components are order-free, so scale mode yields the
*identical clusters* as standard mode, deterministically, with only confidence
within ε. That is a contract *stronger* than Splink (whose clusters can shift
across backends): we match the category where identity is impossible (floats) and
beat it where customers care (membership).

### The scale-mode contract

| Property | Contract | Grounded in |
|---|---|---|
| Cluster **partition** / membership | **Strict — Rand index 1.0** | order-free (UF); members-as-set already (#598); `test_cluster_frames_out_parity.py:17-21` (frozenset) |
| record→cluster, entity-ID stability **within a run** | **Strict** | identity partition + det-mint legs (`test_identity_from_frames_parity.py`) |
| golden record content | **Strict (semantic)** | shared `build_golden_records_df` (`golden.py:1162`) |
| confidence floats | **ε-tolerance, order-free reduction; EXACT only at the weak/quality boundary** | only `avg_edge=sum/len` is order-dependent (`cluster.py:1251`); the boundary `(avg_edge-min_edge)>0.3` (`cluster.py:954`) is semantic |
| multi-matchkey dedup | **MAX (`dedup_pairs_max_score`)**, not last-wins | resolves the MAX≠LAST contradiction toward the canonical `scored_pairs` (`pairs.py:35` vs `cluster_pairscores.py:25`) |
| bottleneck tie-break, member order, pair-fill order | **Deterministic order-free rules** (e.g. lexicographic `(min_id,max_id)`) | frees vectorization; current rule is first-in-pairs-order (`cluster.py:1258`) |
| LLM/rerank/boost, NE post-filters, exotic matchkeys | **Drop EXPLICITLY** — error or warn, never silent | mirror the DataFusion-backend `NotImplementedError`-on-out-of-scope pattern |
| **Determinism** | **HARD requirement: same input → same output across runs AND across `--workers`** | the one property we must not lose |

**Determinism trap (must verify, not assume):** "deterministic per run" is
insufficient — it must survive `--workers` changes. The native kernel accumulates
`avg_edge` in f32 and `bucket_score` runs `max_workers=16`; parallel float
reduction changes the reduction-tree shape with worker count and can drift the
output. Pin the reduction order (sort-then-reduce or order-independent
accumulation) so parallelism cannot change results. **Gate: run twice at
different `--workers`, assert identical partition + ε-equal confidence.**

### Gates (replace the bit-identical parity gate)

- **Correctness gate** = partition Rand index 1.0 + golden-record validity +
  **deterministic (run twice, incl. across `--workers`, → identical)** + every
  dropped feature errors/warns. NOT byte-identical confidence/pair_scores.
- **Perf verdict** = SP-C complete-path bench at **25M and 100M**, on the
  **post-#691/#692 stack** (zero-copy round-trip removed, rayon park fixed),
  numbers **committed back here**. First clean test of the thesis.

#### Verdict — RECORDED 2026-06-03 (run 26878555131, dict-backed view, post-#691/#692)

| pairs | variant | build s | golden s | id_prep s | peak RSS MB |
|---|---|---|---|---|---|
| 25M | legacy | 65.2 | 13.4 | 11.8 | 16,169 |
| 25M | columnar | 31.4 | 0.92 | 45.8 | **14,155 (−12.5%)** |
| 100M | legacy | 284.8 | 56.7 | 47.6 | 61,082 |
| 100M | columnar | 136.8 | 3.87 | 566.0 | **56,333 (−7.8%)** |

**Honest read (the first clean test, and it's mixed):**
1. **RSS win CONFIRMED but MODEST.** −12.5% @25M, −7.8% @100M — real and
   consistent, but **below the −30% @25M target**. The shortfall is the residual
   we still pay: the per-pair view dict-of-dicts is *still materialized* (this is
   the half-state, not the complete path). The −12.5% is what's left after the
   build skips legacy's `pair_scores`-into-clusters fill; the view is the rest.
2. **The OOM trump card did NOT fire.** The legacy dict **fit at 100M** (61 GB on
   the 64 GB box) — it did not OOM. So "the dict doesn't finish" is **unproven at
   this scale**; the cluster-dict OOM cliff is beyond 100M pairs / 64 GB. Either
   the workload must push past it, or the RSS win is real-but-non-binding at
   operating scale (the §6 question, now empirical).
3. **Wall splits on scale.** Columnar build 2.1× + golden 14.6× are large and
   real. But `id_prep` (the view-build) is **super-linear near the memory
   ceiling**: 45.8s @25M (far from ceiling) → 566s @100M (near 56 GB). So
   **columnar wins total wall at 25M (+13.6%) but loses 1.8× at 100M.** Legacy
   `from_pairs` id_prep stays linear (11.8→47.6); the columnar `from_frames`
   view-build is intrinsically ~4× slower AND blows up under memory pressure.
4. **The view-build is the limiter on BOTH axes** — it is the residual RSS AND
   the wall regression. Eliminating it (identity emits evidence edges per-cluster
   directly from frames, never a `dict[int,dict]` view) is the single lever that
   removes the 566s wall and pushes RSS past the remaining view cost toward −30%.

**Decision impact:** this verdict does **NOT** clear the −30% gate and does NOT
demonstrate the OOM trump card, so it does **not** justify flipping the default by
itself. It confirms direction (RSS down, build/golden fast) and identifies the
unlock (view-elimination). Recommend: build scale mode WITH view-elimination as a
first-class component (not a follow-on), then re-verdict at a scale/box that
actually stresses the cluster dict to settle binding-vs-non-binding.

### Feature matrix (scale mode supported / dropped) — SHIPPED (Stage D)

Enforced by `backends/datafusion_spine.py::_validate_scale_mode_supported`
(called first in `run_spine`); raises `NotImplementedError` on every ❌ row and
`ValueError` when invoked without `config.mode == "scale"`. Tests:
`tests/test_datafusion_spine_scale_mode.py`.

| Capability | standard | scale | Note |
|---|---|---|---|
| exact + fuzzy matchkeys (jw/token_sort/ensemble) | ✅ | ✅ | the columnar fast path (`_resolve_fast_path`) covers these |
| weighted multi-field matchkey | ✅ | ✅ | single combined score (no dup canonical pairs — see counts) |
| multiple independent matchkeys | ✅ | ✅ (MAX dedup) | dedup switches last-wins → MAX |
| auto-config / controller | ✅ | ✅ | unchanged |
| cluster partition + golden + identity (within-run) | ✅ | ✅ | partition strict; identity from frames |
| confidence / cluster_quality | ✅ exact | ✅ ε + exact-at-boundary | label semantic, float ε |
| LLM scorer / cluster | ✅ | ❌ explicit error | not vectorizable; out of scale scope (`llm_scorer.enabled`, `llm_auto`) |
| cross-encoder rerank, boost (active-learning) | ✅ | ❌ explicit error | model bootstrap; out of scope (`mk.rerank`, `llm_boost`) |
| negative-evidence post-filters | ✅ | ❌ explicit error | per-pair Python post-filter; drop-loud (`mk.negative_evidence`) |
| PPRL / probabilistic / domain-extraction matchkeys | ✅ | ❌ explicit error | exotic; non-weighted `mk.type` + `domain.enabled` error |

### A measurement finding that shapes the verdict (verify-don't-trust)

The frames-out RSS win is real but it is **sensitive to the view representation**,
and the #693 default is the *wrong* one. At 100M (post-#691/#692):

| view backing | id_prep wall | peak RSS vs legacy |
|---|---|---|
| #693 `partition_by(as_dict=True)` | 257s | **+1% (regression)** |
| simple dict-backed (`_by_cid`) | 539s | **−7.8% (the win)** |

`partition_by(as_dict=True)` at ~33M size-3 clusters makes ~33M tiny Arrow frames;
per-frame overhead dwarfs a single dict → it *increased* RSS. The lean dict-backed
view is what shows the thesis (−7.8%). **Implication for scale mode:** use the
dict-backed view, NOT the partition-backed one #693 shipped. **Bigger lever (out
of scale-mode-as-relabel scope, flagged):** the view is materialized at all only
because identity emits one evidence edge per pair from a `dict[int,dict]`. The id_prep
wall (257–539s, the whole columnar wall regression) IS the view-build. Eliminating
the view — identity reads per-cluster pairs directly from frames — removes that wall
AND drops RSS further. Scale-mode-as-relabel banks the RSS win and keeps the wall;
killing the view is the follow-on that also wins wall. Recommend sequencing the
relabel first (it's the verdict), then the view-elimination as a distinct unit.

### Human sign-off — APPROVED 2026-06-03

Ben approved the scale-mode decision + the §7 contract + the feature matrix + the
three product items below. Remaining gate before flipping the default (per the
handoff): the perf verdict recorded + determinism verified.

1. **Feature matrix** (above) — APPROVED. The two ⚠️ (NE post-filters;
   PPRL/probabilistic/domain matchkeys) resolve to **explicit error in scale
   mode** unless a later need surfaces (conservative: drop-loud, not silent).
2. **Customer-facing statement** — APPROVED: "scale mode is deterministic and
   semantically correct (identical clusters, ε-equal confidence) but not
   bit-identical to standard mode." Document at the mode's public entry point +
   the scale-mode section of the README/docs. **SHIPPED (Stage D):** the
   statement now lives in code as the `mode` field docstring
   (`config/schemas.py::GoldenMatchConfig.mode`) and the
   `ValueError`/`NotImplementedError` messages in
   `_validate_scale_mode_supported`. Determinism is enforced by
   `tests/test_datafusion_spine_parity.py::test_spine_deterministic_across_target_partitions`
   (pair set + cluster partition + id_prep edges identical across
   `target_partitions ∈ {1,3,17}`). The MAX-dedup contract (item 3) is the
   scale-mode dedup; on the default single-weighted-matchkey path MAX≡last-wins
   (R1=0), so the switch is a no-op there and only differs on explicit
   multi-matchkey configs.
3. **MAX vs last-wins** dedup switch — APPROVED (blast radius ≈ 0; 50k R1=0,
   1M/5M confirming). Gated behind scale mode; standard keeps last-wins.

**Blast radius (measured, `count_max_vs_last.py`):**
- **R1 (last-wins → MAX):** 50k = **0 clusters** (the default single-weighted-
  matchkey path produces NO duplicate canonical pairs; MAX≡LAST). Non-zero only
  for explicit multi-matchkey configs, where MAX is the principled choice anyway.
- **R2 (order-free confidence):** 50k = **0 label flips** (closest cluster 0.254
  from the 0.30 boundary; nothing in the ε-band).
- **R3 (tie-break):** 50k = **1.0% min-edge ties**, semantically inert (no weak
  clusters → bottleneck feeds only explanations). MST-tie leg 0 (fixture has no
  oversized clusters — denser fixture needed if that artifact matters).
- 1M/5M: PENDING (re-dispatched with `confidence_required=False`).

## Stage E — out-of-core spill verdict (2026-06-03): HONEST-NULL on one-box survival

**Verdict: the relational stages spill correctly, but the one-box "spine survives
where in-memory OOMs" thesis does NOT bind with a one-box workload — the
non-relational UF break is the binding constraint, exactly as this spec's Risk
("Spill may still not bind") and the engine-portability map (the UF holdout)
anticipated.** Measured on `large-new-64GB`, `bench-datafusion-spine-spill.yml`,
run `26911207018` (driver `scripts/bench_datafusion_spine_spill.py`, 3 variants:
in-memory `bucket` / `spine_nospill` / `spine_spill`).

**Measured @ 200K rows, soundex-on-`last_name` blocking, jw≥0.85, pool 2048 MB:**

| variant | wall | peak RSS | pairs (raw) / dupes | clusters |
|---|---|---|---|---|
| bucket (in-memory) | 17.6s | 3572 MB | 199,979 (dupes) | 5628 |
| spine_nospill | 108.1s | 4765 MB | 5,203,861 (raw pairs) | 5606 |
| spine_spill (pool 2 GB) | 111.0s | 4820 MB | 5,203,861 (raw pairs) | 5606 |

**@ 1M rows:** the spine process was OOM-killed (job exit 143, runner-level OOM)
during the run — see mechanism below.

**What binds and what doesn't:**
1. **The relational spill path is CORRECT.** `spine_spill` produced output
   byte-identical to `spine_nospill` (5,203,861 raw pairs, 5606 clusters) at a
   bounded ~4.8 GB peak RSS — the `fair_spill_pool` (2 GB) routes score+dedup
   through the OS disk manager without changing results. The engine-portability
   claim (these stages plan + spill on DataFusion, and would distribute on Sail)
   holds.
2. **One-box survival does NOT bind.** The spine emits ~5.2M RAW above-threshold
   pairs at 200K rows (~26/row) and collects them to a driver-side Python list to
   feed `build_cluster_frames` (the UF break) — the in-memory island the spill
   pool does NOT cover (this spec's UF holdout). With soundex blocking that island
   grows ~O(N²) in block size, so it OOMs the 64 GB box at ~1M rows — BEFORE the
   in-memory `bucket` comparand (only 3.5 GB at 200K) would. The spine's own
   non-relational UF collection is the binding constraint.
3. **The asymmetry is architecturally precluded on one box for this workload.**
   `bucket` OOMs only when blocks are large (its O(block²) within-block score
   matrix), but large blocks ALSO explode the spine's above-threshold pair set →
   the UF island OOMs first. You cannot make in-memory OOM without making the
   spine's pair-collection island OOM sooner. So no reachable sub-50M-pair one-box
   scale shows "in-memory dies, spine survives" with realistic_person-shape data.
   More billable runs cannot change this; it is the spec's anticipated honest-null.

**Implication (consistent with the gate reframe below):** the spine's value is
**engine portability**, not one-box survival. Removing the UF island — routing the
cluster build to distributed label-prop at Sail scale (the existing ≥50M path) —
is precisely what unlocks beyond-one-box scale; that is the Sail tier, OUT of this
spec. **Do NOT flip the `mode` default on a one-box survival claim** (the sign-off
gate is not met for one-box survival; it IS met for relational-spill correctness +
determinism). Recommend: keep `mode="scale"` opt-in; revisit the default when the
Sail tier removes the island.

**Follow-ups (scoped, not done here):** (a) a relational-stages-ONLY spill bench
(score+dedup under a cgroup `MemoryMax` cap, excluding the UF collection) to show
the relational spill survival crisply in isolation; (b) a large-but-SPARSE-block
workload (big soundex blocks of dissimilar surnames, most pairs below threshold)
where in-memory matrices OOM while the spine's above-threshold output stays small —
the only one-box shape that could bind, and only if such a workload is
representative. Both are deferred.

## Gate reframe: engine portability, not one-box RSS (2026-06-03, supersedes the RSS gate for this track)

**The Arrow-native arc's destination is engine portability** — making every
pipeline stage a frames-in/frames-out relational operation a query engine
(DataFusion single-box out-of-core; Sail distributed) can plan, spill, and
distribute. One-box peak RSS was always tactical. **RETIRE the Phase-2 "RSS
−30% @25M" gate for this track** (it measured the wrong axis for a distributed
destination — Splink/Spark/Sail scale *out*; one box always has a ceiling). The
compact columnar representation is NOT wasted: at distributed scale it reappears
as shuffle/spill efficiency (packed Arrow frames shuffle/spill far cheaper than
dict-of-dicts).

**Replacement gate:**
1. **Engine-portability** — every stage is a relational plan an engine can own
   (or, for the one non-relational stage, routes to label-prop — below).
2. **Out-of-core / distributed throughput** — wall holds at 100M out-of-core
   (DataFusion spill) and scales across nodes (Sail).

### Engine-portability map

| Stage | Relational? | DataFusion + Sail |
|---|---|---|
| score / block / dedup | ✅ | rides the engine; native scorers as Arrow UDFs (`datafusion_backend.py`) |
| golden | ✅ | rides the engine (already 14.6× as a frame op) |
| **id_prep** | ✅ **only after the group-by rewrite** | the ENABLING step (below) |
| **Union-Find cluster build** | ❌ not relational | the genuine holdout — routes to the EXISTING distributed **label-propagation** path (`goldenmatch.distributed.clustering`, Splink-style ≥50M routing), NOT DataFusion. By nature, not a gap. |

### id_prep-as-group-by is the critical-path ENABLING step (not a perf detour)

The §1 verdict (recorded above) showed `id_prep` is the columnar wall regression:
566s at 100M = 80% of the columnar wall; flips 100M end-to-end from a 2.07× win
to a 1.8× loss. Root cause: SP4 relocated the per-cluster `pair_scores` fill into
id_prep as a per-pair Python dict-of-dicts build — **un-distributable**. A Python
per-cluster/per-pair loop cannot ride DataFusion or Sail; a
`group_by("cluster_id").agg(...)` can. So the rewrite is not "optimize id_prep for
RSS" — it is **"make id_prep plannable."** The ~2× end-to-end win is just the
local proof it's now in the right shape.

**Status (2026-06-03): PROVEN.** `ClusterPairScores.from_frames` rewritten as a
`group_by("cluster_id", maintain_order=True).agg(...)` view with deferred
per-cluster materialization (PR #696, last-wins parity GREEN). §1 complete-path
re-run (run 26888605952, post-#691/#692, dict-free frames path):

| pairs | variant | build s | golden s | id_prep s | peak RSS MB |
|---|---|---|---|---|---|
| 25M | legacy | 78.7 | 16.6 | 13.3 | 16,216 |
| 25M | columnar | 39.6 | 0.93 | **8.2** | 19,307 |
| 100M | legacy | 357.1 | 68.2 | 56.8 | 61,089 |
| 100M | columnar | 190.4 | 4.23 | **34.3** | 61,554 |

- **id_prep collapsed 566s → 34.3s @100M (16.5×), now BELOW legacy** (34 vs 57). The
  un-distributable per-pair dict-of-dicts is gone; the stage is a vectorized
  group-by — i.e. **plannable.**
- **100M end-to-end flipped from a 1.8× loss to a 2.11× win** (columnar 229s vs
  legacy 482s); 25M = 2.23×. Validates the thesis on every stage the columnar path
  owns (build 1.9×, golden 16×, id_prep now a win).
- RSS ≈ parity @100M (+0.8%), +19% @25M — NO LONGER THE GATE (engine-portability
  gate above); at distributed scale this is shuffle/spill efficiency.

**Consequence:** score/golden/id_prep are now ALL relational/plannable. The
load-bearing precondition ("id_prep plannable BEFORE DataFusion") is MET →
green-light the DataFusion spine (PR #695, the edge-group-by) as step 2. UF
clustering still routes to label-prop (non-relational holdout, by nature).

## What this plan explicitly does NOT do

- Re-open the phase definitions or kill criteria (unchanged from the original).
- Adopt DataFusion/Sail/Polars Cloud as a default backend (Phase 7 is gated and
  exploratory; bucket+native stays the production winner).
- Touch the Identity Graph schema or the auto-config controller.

## References

- Original design: `2026-05-31-arrow-native-roadmap.md`.
- Implementation burst: PRs #630-#653 (capability + parity behind gates).
- Audit: 2026-06-01 open-issue audit (per-phase shipped/partial/open verdicts).
- Profiler evidence: run 26759043853 (columnar 354.94s vs list 564.33s @1M;
  remaining cluster-build hotspot is `compute_cluster_confidence` + the
  131M-pair min/max/set.add fill -- exactly the Phase 2/3 cutover lever).
- Bucket+native baseline: 25M-on-one-node 6.5min / 57.7 GB (run 26095134836).
