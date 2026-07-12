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
- **D2 — fused golden slot arrow**: `_try_fused_golden`/`run_golden_fused_arrow`
  returns arrow; `golden_df` slot retyped `Frame`-polymorphic. Slow-path
  `_golden_records_to_df` untouched (fused lane never hits it).
- **D3 — terminal splits + result dict via the seam** (pipeline.py ~3098-3230):
  dupes/unique splits via `filter_in` on `to_frame(collected_df)`; BOTH lanes
  emit pa.Table in the INTERNAL dict. Must migrate the internal-dict consumers
  in the SAME batch: dbt materialize (the W5-stamped patch loop flips here),
  tui/engine.py EngineResult reads, web/routers run+match.
- **D4 — ClusterFrames + golden-from-frames dual-backend** (pipeline.py ~2650,
  ~2925): ClusterFrames.metadata/assignments as Frames; the stats/member-id
  reads (`pl.col("size")>1 & ~oversized`) via seam ops. Real port work.
- **D5 — classic fuzzy scoring on the seam** (largest; REQUIRED before dep
  removal because configs the fused kernel declines still need block/score on
  arrow): build_blocks' per-block frames + score_blocks_parallel orchestration
  go Frame; leaf extraction (`to_list`/`to_numpy` for rapidfuzz) works on
  ArrowColumn. Probabilistic + semantic-blocking + bucket paths follow the
  same shape. Wall-gated per stage (>10% → owned kernel rule stands).
- **D6 — the deletion**: default already arrow; delete the `polars` opt-out
  value + PolarsFrame + `_polars_dtype` + polars constructor branches;
  `_polars_lazy` proxy SURVIVES only in the extra-gated integration modules
  (quality/transform — verified they import via the proxy, so polars loads
  only when the gated stages run); polars moves runtime→dev-dep group; parity
  suites collapse to single-backend; docs sweep; 3.x minor release.

## Risks

| Risk | Mitigation |
| --- | --- |
| Internal-dict consumers missed at D3 | Recon enumerated them (dbt/tui/web/_api); grep for result["golden"]-style reads in the D3 PR |
| Classic-lane scorer port regresses wall | Per-stage wall gates; the fused lane covers the hot configs so D5's exposure is the declined-config tail |
| Deleting the polars opt-out strands a user mid-migration | D6 ships in a LATER 3.x minor than D1-D5; deprecation note already in tuning.mdx |
