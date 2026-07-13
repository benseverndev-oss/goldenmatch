# GoldenMatch 3.x — engine-internals arrow descent (polars out of runtime deps)

Follow-on to the shipped 3.0.0 (results = pa.Table, arrow default). Recon
2026-07-12 mapped `_run_dedupe_pipeline`'s engine segment (terminal collect →
result dict): **the arrow lane (fused short-circuit) is ALREADY Arrow at the
compute core** (`run_match_fused_arrow` → pa.Table; `run_golden_fused_arrow`)
and re-materializes to polars at exactly three seams. The classic lane's
scorer/cluster stages are genuine polars logic the fused kernel bypasses.

Counts: prep segment 32 `pl.` uses; engine segment 24; fused helper ~15
(the D1 targets). `score_blocks_parallel` holds per-block `pl.LazyFrame`s;
array-ization is leaf-local (`to_list`/`to_numpy` before rapidfuzz).

## Batches (each its own PR; no stacking on unmerged predecessors)

- **D1 — fused-helper tail stays pa.Table** (pipeline.py ~1452-1543): drop the
  `pl.from_arrow(fused_tbl)` round-trip; sizes (`group_len`), dupe/golden/
  oversized id sets, and the collected-frame splits run on ArrowFrame seam ops
  (`filter_in`/`filter_not_in`/`with_gt_column`). `_to_result_table` already
  passes pa.Table through — no consumer breaks. ~10 pl uses out of the lane.
- **D2 — fused golden slot arrow (RE-ORDERED after D3/D4, recon 2026-07-12)**:
  run_golden_fused_arrow's kernel returns INDICES and the Python side GATHERS
  on the multi_df frame (_gather_with_nulls over pl.Series) -- an arrow output
  is only an end-conversion until multi_df itself arrives as arrow, which
  requires collected_df unification (D3) / ClusterFrames (D4). Deep D2 =
  gather on ArrowColumn once the input is arrow.
- **D3 — terminal splits + result dict via the seam** (pipeline.py ~3098-3230):
  dupes/unique splits via `filter_in` on `to_frame(collected_df)`; BOTH lanes
  emit pa.Table in the INTERNAL dict. Must migrate the internal-dict consumers
  in the SAME batch: dbt materialize (the W5-stamped patch loop flips here),
  tui/engine.py EngineResult reads, web/routers run+match.
- **D4 — ClusterFrames + golden-from-frames dual-backend** (pipeline.py ~2650,
  ~2925): ClusterFrames.metadata/assignments as Frames; the stats/member-id
  reads (`pl.col("size")>1 & ~oversized`) via seam ops. Real port work.
- **D5 — classic fuzzy scoring on the seam (SUB-BATCHED, recon 2026-07-12;
  order D5a → D5c → D5b → D5d)**. Findings: BlockResult.df is an
  eagerly-collected group re-wrapped `.lazy()` (blocker.py:470-473), NOT a
  live filter; grouping already seam-routed (group_partitions :378);
  score_buckets' kernel is ALREADY arrow at the FFI (:762-766) -- only its
  polars scaffolding ports; _find_exact_match_ids + cross-source filter
  already seam.
  - D5a (wall-neutral, zero new ops): leaf-extraction migration --
    block_df["x"].to_list()/.to_numpy()/.unique().to_list() sites in
    find_fuzzy_matches (:1096), _score_one_block (:1350-1353), probabilistic
    (:1263-1339), bucket fallback (score_buckets:822-825) → seam column reads.
  - D5c (narrow): _get_transformed_values fallback (:429) →
    derive_transformed_column (seam twin exists); probabilistic .to_dicts()
    (:1263) needs the ONE missing seam op.
  - D5b (WALL-GATED 100K/1M): BlockResult.df becomes a seam Frame
    (materialized, score_buckets slice model); drop .lazy()/.collect()
    round-trips; candidate probe → Frame.height.
  - D5d (heaviest, THE hot path, strictest gate) PORT SPEC (read 2026-07-12):
    _score_single_pass (score_buckets.py ~:985-1070) stage-by-stage --
    (1) keyed = slim_df.with_columns(key_expr) → frame.with_column("__block_key__",
    frame.derive_block_key(...)) (W2a twin of _build_block_key_expr);
    (2) PRESERVE the #422 small-block fast path (height < n_buckets skips
    hash+partition) verbatim; (3) bucket hash stays PER-LANE native (W4e
    precedent: shard-internal, not output-visible; sorted-slice blocks are
    hash-independent); (4) partition_by(as_dict) → group_partitions;
    (5) per-bucket sort(__block_key__)+slice → frame.sort/slice (already
    ops); (6) MUST PRESERVE: the del keyed/del bucketed RSS points, every
    stage() wrapper, and the print instrumentation (RSS bench attribution).
    GATES: 100K A/B + 1M dispatch + RSS comparison (rss workstream
    constraint: hold wall+accuracy, don't regress peak RSS); the 5M
    distributed stack re-run if the bucket lane is touched by ray_backend.
- **D6 — the deletion**: default already arrow; delete the `polars` opt-out
  value + PolarsFrame + `_polars_dtype` + polars constructor branches;
  `_polars_lazy` proxy SURVIVES only in the extra-gated integration modules
  (quality/transform — verified they import via the proxy, so polars loads
  only when the gated stages run); polars moves runtime→dev-dep group; parity
  suites collapse to single-backend; docs sweep; 3.x minor release.

## Recon 2026-07-12 (post-D5d): the spine blocks deep-D2 and D6

D1/D3/D4/D5a-d are MERGED (#1701/#1708/#1712/#1714/#1717/#1718+#1719/#1720).
Deep-D2's precondition is NOT met: `combined_lf` is a polars LazyFrame on BOTH
lanes (pipeline.py ~1864 `combined_lf.collect()` -> `collected_df:
pl.DataFrame` -> `multi_df` polars everywhere). The GOLDENMATCH_FRAME=arrow
lane flips seam ops, but the pipeline SPINE (ingest scan_csv/scan_parquet ->
prep exprs -> precompute_matchkey_transforms -> collect) still materializes
polars. Consequently:

- Deep-D2 (gather on ArrowColumn) would be dead code today -- no caller can
  hand golden_fused an arrow multi_df, and no gate can exercise it e2e.
- D6 (polars out of runtime deps) is blocked on the same spine: as long as
  the collect boundary yields pl.DataFrame, polars is load-bearing on every
  run regardless of lane.

**Spine map (Explore recon 2026-07-12).** Ingest front (load_file rename/
validate/__source__/ensure_row_ids) + eager standardize/exact-matchkeys
ALREADY run on ArrowFrame when `_eager_ok` (pipeline.py:811-897); the
arrow->polars shim is `pl.from_arrow(_combined.native)` at pipeline.py:900
(W5b-1). Between the shim and the collect at :1864: prep-cache, gated
quality/transform (default-ON, decline the eager lane), auto_fix
(validation.auto_fix), quarantine split (validation.rules), semantic raw
capture, standardize, domain, compute_matchkeys, then
`precompute_matchkey_transforms` (matchkey.py:291 -- ALWAYS runs, polars-only;
per-column seam twins exist via derive_transformed_column but the batched
one-pass with_columns orchestration does not). Post-re-wrap (:1868) lazy
consumers: _find_exact_match_ids, find_exact_matches, build_blocks x2,
_semantic_blocking_pairs; everything else drives off eager collected_df.

**Next batch series (D2s -- spine descent, CONSUMERS-FIRST; the boundary
cannot move while downstream still takes pl.LazyFrame):**
- D2s-a: exact-match consumers dual-rep -- _find_exact_match_ids /
  find_exact_matches accept Frame (seam group ops exist); build_blocks entry
  Frame-typed (blocker grouping already seam-routed).
- D2s-b: Frame-level precompute_matchkey_transforms -- arrow path loops
  derive_transformed_column (column-append is cheap on arrow); polars path
  keeps the existing batched one-pass with_columns VERBATIM (the 90s-at-10M
  lesson in its docstring). NE derived columns need a fill_null("")
  space-join twin (check derive_block_key sep-null semantics first).
- D2s-c: bucket hash + remaining engine-segment `pl.` residue per-lane
  (score_buckets keyed hash, _run_fused_match_short_circuit entry columns
  `collected_df[c].to_arrow()` -> seam column reads).
- D2s-d: move the :900 shim below collect for the eager-eligible arrow path
  (collected_df becomes Frame-typed); wall+RSS gate at 100K/1M.
  **CONSUMER AUDIT (Explore, 2026-07-12) -- sub-batch spec:**
  - D2s-d1 (mechanical, behavior-preserving both lanes): rewrite the B-class
    collected_df reads via to_frame -- .height 1916/2601/3159 (+len at
    3200/3203, quarantine 1784), .columns 2404/2473/2539/2913,
    [col].to_list 2449/2615, select+to_dicts 1929 (select_dicts twin),
    filter(is_in) 2861/2983 (filter_in), cast/fill_null/to_list 2475/2546
    (Column.cast_str+fill_null), schema.items 2536 (rewrite over columns +
    semantic_dtype). score_buckets prepared_df/slim_df: .height 486/528/
    1012/1018, .columns 335/418/577-594/1097, .select 595; workers:
    filter(pl.Series(mask)) 756 -> filter_mask, [col].to_list 831,
    null_count 844; native-kernel extraction 760-764 is a no-op on arrow
    (ArrowColumn.to_arrow already returns the pa array).
  - D2s-d2 (the flip): eager-arrow path keeps _combined as ArrowFrame;
    collected via precompute_matchkey_transforms_frame (producer twin
    DONE); exact lane already dual-rep (D2s-a: _find_exact_match_ids
    accepts Frame -- kill the combined_lf=collected_df.lazy() alias for
    this path); score_buckets Frame entry (post-d1); GOLDEN BRIDGE:
    multi_df -> pl.from_arrow at the golden-builder boundary ONLY (the
    shim moves DOWN, not out; deep-D2 removes it). DECLINE the Frame lane
    (fall back to today's :900 shim) when any C-class flag is set:
    auto_suggest, memory store, pre/postflight, adaptive golden rules,
    quality_weighting, rerank, llm scorer/boost, probabilistic EM,
    NE-on-exact, identity, lineage. _semantic_blocking_pairs already
    excluded by _eager_ok. _run_fused_match_short_circuit is seam-clean;
    result frames already sink via _dict_frame_to_arrow.
  - GATES: differential harness (bucket + fused datasets) + 100K/1M wall
    A/B + RSS hold; the decline list must be asserted by a fixture per
    flag (Frame lane refuses, classic lane output identical).
- Deep-D2 proper (golden_fused dual-rep: seam sort/filter/gather;
  arrow gather twin = `take` with null indices, PROBED) once multi_df is
  arrow. **PARTIAL 2026-07-13: run_golden_fused_arrow is dual-rep (pa in ->
  pa out, no round-trip; provenance bridges at entry); bridge removed on the
  FUSED-HELPER and DICT golden paths (decline replays the polars demux for
  byte-parity). DEEP-D2b (2026-07-13): the FRAMES-path
  bridge is gone too -- _multi_df_from_frames normalizes ClusterFrames to the
  source lane (zero-copy to_arrow) and joins single-backend; the fused try
  gets the lane-native frame. On kernel DECLINE,
  build_golden_records_from_frames RECOMPUTES multi_df via the polars join
  from a bridged source (Acero vs polars join ROW ORDER differs and the
  batch builder's first-occurrence tie-breaks are order-sensitive --
  recompute is byte-identical by construction). The GOLDEN BRIDGE is now
  decline-fallback-only on all three paths.**
- D6 after the spine holds a full-suite arrow lane with zero polars imports
  (assert via an import-hook test, the goldencheck 2.0.0 precedent).

## Frame-lane WIDENING roadmap (2026-07-13, post-deep-D2)

Ben's observation: ~11 of 13 feature classes DECLINE the Frame lane, and the
default config (quality/transform default-ON) never engages it. The decline
lane falls back to polars -- which cannot survive D6 (no polars to fall back
to). Every flag must resolve to PORT (consumer goes dual-rep) or EXTRA-GATE
(polars becomes that feature's own declared dep, the quality/transform
pattern) before D6. Criterion: default-config impact x port cost.

| Flag | Decision | Batch |
| --- | --- | --- |
| quality prep (default-ON) | EXTRA-GATE (locked design) -- BRIDGE at run_quality_check entry (pa->pl->pa, zero-copy) | W-1 (this) |
| transform prep (default-ON) | EXTRA-GATE (locked design) -- same bridge | W-1 (this) |
| writes_outputs (write_output+lineage) | PORT (core, common) | W-2 |
| validation rules/auto_fix | PORT (validate_dataframe/auto_fix seam twins exist: evaluate_validation_rule, auto_fix) | W-3 |
| identity | PORT (core feature; reads row attrs off collected) | W-4 |
| memory corrections | PORT (apply_corrections column reads) | W-4 |
| auto-suggest / block analyzer | PORT (W3d reductions already seam) | W-5 |
| postflight/preflight (auto_config) | PORT with the controller lane | W-5 |
| probabilistic EM | PORT (probabilistic.py already part-seam via D5a/c) | W-6 |
| NE-on-exact | PORT (small; _apply_negative_evidence_to_exact_pairs column reads) | W-6 |
| throughput sketch tier | EXTRA-GATE candidate (rare, polars-heavy local block) or late port | W-7 |
| rerank (cross-encoder) | LATE PORT (rare; reads text cols) | W-7 |
| llm boost/scorer | LATE PORT (rare; reads row text) | W-7 |
| semantic blocking | PORT (excluded by eager gate today; embedding reads) | W-7 |
| domain extraction | PORT (eager gate exclusion) | W-7 |
| adaptive golden rules | LATE PORT (refiner reads prepared_df) | W-7 |

**STATUS 2026-07-13: W-1..W-7 ALL IMPLEMENTED** (W-1 #1731; W-2..W-7 stacked
on the same branch series). The Frame lane now accepts EVERY feature class:
quality/transform (final extra-gate bridges), writes_outputs/lineage (ported;
native parquet), validation (seam), identity/memory (transitional bridges),
auto-suggest/postflight (transitional), probabilistic EM (seam-ported) +
NE-on-exact, throughput/rerank/llm/semantic/domain/adaptive (transitional
bridges; semantic raw capture + domain mirrored in the Frame branch, eager
shortcut still excludes them for stage-order safety). The predicate's only
remaining declines: NONE -- eligibility is now representation+env gates only.
TRANSITIONAL bridges (identity, memory, analyzer, postflight, throughput,
rerank, llm, semantic, domain, adaptive refiner) are the D6-prerequisite
port list.

W-1 (quality/transform bridges) restructures the engine Frame branch to run
the stages in CLASSIC PREP ORDER (quality -> transform -> standardize ->
matchkeys -> precompute; the E.164 stage-order lesson) -- the eager
standardize/matchkey shortcut only applies when prep is a no-op, exactly as
today. Prep CACHE is skipped on the Frame lane (correctness first). GATE:
the differential harness datasets run DEFAULT configs (quality ON), so
post-W-1 they exercise the Frame lane cross-rep e2e for the first time.

## Risks

| Risk | Mitigation |
| --- | --- |
| Internal-dict consumers missed at D3 | Recon enumerated them (dbt/tui/web/_api); grep for result["golden"]-style reads in the D3 PR |
| Classic-lane scorer port regresses wall | Per-stage wall gates; the fused lane covers the hot configs so D5's exposure is the declined-config tail |
| Deleting the polars opt-out strands a user mid-migration | D6 ships in a LATER 3.x minor than D1-D5; deprecation note already in tuning.mdx |
