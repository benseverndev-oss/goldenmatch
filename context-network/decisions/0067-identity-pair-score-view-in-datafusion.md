# 0067 — Identity's pair-score view is built in DataFusion; golden-suite bundles it

**Status:** accepted (2026-11-20, Ben) • **Shipped from it:** #3220 (goldenmatch 3.23.0, golden-suite 0.5.11) • **Extends:** [0002](0002-scale-mode-contract.md) / scale-substrate SP1 (#695) • **Frame:** [../architecture/one-product-two-engines.md](../architecture/one-product-two-engines.md), decision [0047](0047-one-product-two-engines-architecture.md)

## Context
An identity-enabled `dedupe_df` over ~14M person-shape records OOMed a 32 GB box
**after** scoring and clustering had finished within budget. Stage markers put the
death between `golden` and `identity_prep_row_filter`: RSS stepped up ~8 GB in under
a minute, before identity wrote a row.

The cause was identity's per-cluster pair-score view. `_pair_score_view()` builds it
on every identity-enabled frames-out run; on the B2c path it converted every scored
pair in the Arrow pair table to Python (`to_pydict()` + `list(zip(...))`, ~30M pairs),
then re-bucketed them in a Python dict (Arrow lane) or looped them back into a
dataframe. #696 had made the Polars-lane view a group-by, but after the Polars
eviction the Arrow lane fell back to the pure-Python reference.

The replacement already existed. SP1 (#695) built a DataFusion edge view for exactly
this and listed "identity per-pair edge streaming" as a deferred sub-project, because
at 100M the dict still fit a 64 GB box ("the dict doesn't finish at scale" was marked
*unproven*). A 32 GB box at 14M is that proof.

## Decision
1. **Identity's pair-score view is built in DataFusion by default when `datafusion` is
   importable** (`GOLDENMATCH_PAIR_SCORE_VIEW=auto`). The Arrow pair table goes in
   as-is; the join, same-cluster filter, dedup and ordering run in DataFusion; the
   result backs `ClusterPairScores.from_sorted_edges` (flat cid-sorted arrays plus run
   offsets). `python` is the kill-switch; `datafusion` requires it and never falls back
   silently. One seam, `build_pair_score_view`, serves the pipeline and the spine.
2. **It keeps the legacy view's contract, not SP1's scale-mode MAX contract**: input
   order, last-wins on a duplicated `(a, b)`, keys as given, first-occurrence order.
   Identity's evidence edges are durable state, so they stay byte-identical.
3. **golden-suite bundles `goldenmatch[datafusion]`**, so suite installs get the
   Arrow-native view without opting in.

## Conformance with 0047
- **Compute vs control stay distinct.** Bucketing scored pairs by cluster is bulk
  compute and moves to the Arrow engine; entity ids, merge/split, events and writes
  stay in the control plane, unchanged.
- **Arrow at bulk boundaries.** The B2c pair table crosses into the view without a
  Python object per pair.
- **Conformance defines correctness.** Byte-identical parity against the
  `from_pairs` reference, per cluster and at the identity layer.
- **DataFusion is a replaceable backend.** The Python reference stays as the fallback.

## Measurements
View build, 10M pairs, fresh process per mode (peak RSS added / wall):

| path | peak | build |
|---|---|---|
| old, Arrow lane (Python dict) | 3.62 GB | 70.2 s |
| old, Polars lane | 4.44 GB | 39.3 s |
| **DataFusion** | **1.80 GB** | **6.2 s** |

The ordered-aggregate form (`first_value(... ORDER BY ...)`) and lower partition
counts were measured and are no better than the two-pass join used.

## Consequences
- golden-suite's install grows by the `datafusion` wheel.
- DataFusion's working set is still O(pairs) (~1.6 GB per 10M pairs, transient). If
  that binds, the next lever is emitting per-cluster edge offsets from
  `build_clusters_arrow`, which already walks these pairs in Rust.
- Identity prep's payload dicts were a second O(N) term; #3220 also bounds them to one
  write-loop batch. The remaining per-row ids and hashes are pure per-row compute and
  are the natural next Arrow-kernel candidate.
- #3220 also stops identity prep copying the input frame down to referenced rows (it
  filters per chunk). With all three, the 14M-record identity resolve that OOMed on 32 GB
  **completes** (2.03M entities, 9.5M relationship edges, gold F1 0.954) -- but with
  ~0.1-0.2 GB to spare. Per-stage current RSS at 14M names the next terms: `golden`
  (+6.6 GB, golden records identity rebuilds from payloads anyway) and the start of
  identity resolve before `row_filter` exits (+4.3 GB, not yet attributed).
- **Follow-up (2026-11-22):** a repeat 14M run on 3.23.0 OOMed, so "completes" was one
  lucky run, not a margin. Ingest stage markers then attributed the pre-stage growth: a
  polars input was converted to Arrow twice (the lane eviction and the #3003 original-values
  capture); the capture now references the lane table, -2.2 GB from the first stage and
  cluster peak 24.8 -> 22.5 GB. That did NOT move the binding peak: both runs re-converge by
  `identity_pair_score_view` and peak at ~31.3 GB in identity resolve **after the write
  loop** (+3.9 GB over the loop's 27.4 GB, unmarked). That is the next term to attribute.
  Routing fast-path golden through the fused Arrow kernel was tried and dropped: no RSS
  change at 14M (golden still +~5.5 GB), 8 s slower, and it changed
  `__golden_confidence__` values.
- **Follow-up (2026-11-23):** stage markers after the write loop attributed the rest, and
  three fixes took the 14M resolve from 2,175 s / 29.3 GB to 1,084 s / 27.6 GB with identical
  output. (1) The Postgres relationship reconcile diffed ~9.5M edges in Python and inserted
  them with a per-row `executemany` (678 s, +1.9 GB); it now stages them with COPY and diffs
  in SQL (169 s, no spike). (2) Weak brand-new clusters (below `weak_confidence_threshold`,
  ~95% of clusters at the 0.6 default on that data) fell back to the per-row write path
  because their `conflicts_with` edge only existed there; the bulk path now writes it, and
  the write loop went 1,101 s to 566 s. (3) Smaller: a shared `most_complete` rule and a
  relationship index rebuild around large first loads. Two findings for what comes next:
  the remaining write-loop time is about half per-cluster Python row building, half
  Postgres; and a run near the 32 GB ceiling slowed Postgres (sharing the box) by ~2x, so
  memory headroom is also a speed lever. That 95% of clusters count as "weak" at F1 0.954
  suggests the 0.6 default does not fit probabilistic cluster confidence; not yet acted on.
- **Measurement gotcha:** identity-memory fixes look like no-ops below ~1M records.
  The bulk writer stages up to 250k rows, each pinning its payload until the flush,
  so at small N that window covers most of the data. Measure with the bounded
  windows set well below N.
