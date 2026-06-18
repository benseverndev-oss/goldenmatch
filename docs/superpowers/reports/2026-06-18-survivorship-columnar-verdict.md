# Survivorship columnar scale path — measure-first VERDICT

> **Verdict: GO** (pursue a Phase-2 vectorized survivorship rewrite).
> Date: 2026-06-18. Bench: `scripts/bench_survivorship_columnar.py`
> (PR #1057). CI run: `bench-survivorship-columnar.yml` #27734780031 on
> `large-new-64GB`, `rows=1000000,5000000`, `runs=3`.
> Spec/plan: `docs/superpowers/{specs,plans}/2026-06-17-survivorship-columnar-measure-first*`.

## Measured results

| rows | variant | total s | sort s | partition s | loop s | peak RSS MB | row count |
|------|---------|--------:|-------:|------------:|-------:|------------:|----------:|
| 1,000,000 | slow  | 52.99 | 0.017 | 1.01 | **51.98** | 5076 | 333,334 |
| 1,000,000 | floor |  2.79 |   -   |   -  |    -      | 3080 | 333,334 |
| 5,000,000 | slow  | 268.11 | 0.082 | 6.18 | **261.86** | 24546 | 1,666,667 |
| 5,000,000 | floor |  13.21 |   -   |   -  |    -      | 13495 | 1,666,667 |

Row counts match between slow and floor at both scales (one golden record
per cluster) — the workload + measurement are sound.

## Reading

- **The slow path is ~19-20x the vectorized fast-path floor** (52.99/2.79 =
  19.0x at 1M; 268.11/13.21 = 20.3x at 5M).
- **The per-cluster Python loop is ~98% of the slow wall** (51.98/52.99 = 98%
  at 1M; 261.86/268.11 = 98% at 5M). `sort` and `partition_by` are negligible
  (<2.5% combined). The cost is entirely the per-cluster `resolve_cluster`
  iteration: ~1.67M Python iterations at 5M, each materializing 4-8 columns to
  Python lists + building row dicts + `group_winner` + the conditional eval.
- **Tax = ~95% of slow wall** (50.2s at 1M; 254.9s at 5M). The recoverable
  (partition+loop) proxy is ~100% of slow.
- **RSS:** the slow path uses ~1.65-1.82x the floor's RSS (5076 vs 3080 at 1M;
  24546 vs 13495 at 5M). The vectorized direction uses *less* RSS, not more.

This is **NOT** the no-op the prior columnar A/Bs were (scorer-columnar ~1-2%
slower; arrow kernels 1.05-1.07x). The survivorship per-cluster loop is a
genuine, catastrophic, vectorizable bottleneck at scale.

## A bug in the original verdict gate (now fixed)

The bench's `verdict()` printed **NO-GO**, which is wrong. The `rss_ok` gate
was inverted: it required `slow_rss <= floor_rss * 1.15`. Since the slow path
is RSS-heavy (the per-cluster materialization), it is never within 15% of the
lighter vectorized floor, so `rss_ok` flipped to False and forced NO-GO —
vetoing the GO *precisely when the slow path is RSS-bad*, which is exactly the
case a rewrite fixes. The intended gate ("a vectorized rewrite must not BLOW
UP RSS, the prior columnar failure mode") is captured by comparing the
vectorized proxy (floor) to slow: `floor_rss <= slow_rss * 1.15`. Under the
corrected gate the floor uses materially *less* RSS than slow at both scales,
so `rss_ok=True`. **Fixed in this change** (with a regression test pinning the
measured RSS-heavy-slow scenario as GO).

## Verdict: GO

With the corrected gate: tax = 95% of slow wall, recoverable ~100%, and the
vectorized direction *reduces* RSS. The vectorizable cost (per-cluster loop +
partition) dominates and clears the 25-30% bar by ~3x. **Pursue a Phase-2
vectorized survivorship rewrite.**

## De-risk (the one honest caveat)

`loop_wall` is coarse — it lumps the per-cluster **materialization** (`to_list`
+ row-dict building) and **`group_winner`** (both vectorizable) together with
the per-cluster **conditional `eval_predicate`** (one `ast` eval per conditional
field per cluster — *not* vectorizable). The 95% tax over-counts the truly
recoverable portion by whatever fraction the conditional eval consumes.

Even pessimistically the GO holds: the conditional eval runs once per
conditional field per cluster against the already-resolved scalar dict (cheap),
while the materialization touches 4-8 columns per cluster (the dominant
per-cluster cost). You would need >70% of the loop to be conditional-eval to
fall below the 25-30% bar — implausible. A `py-spy` profile over one 5M slow
run would pin the materialization / group_winner / eval_predicate split
exactly; recommended as the first step of the Phase-2 spec to size the target
precisely, but not a blocker for the GO.

## Recommendations

1. **Land the verdict-gate fix** (this change) so a re-run reports correctly.
2. **Brainstorm a Phase-2 vectorized survivorship rewrite** spec. Target: replace
   the per-cluster `resolve_cluster` loop with a columnar `group_winner` (polars
   `group_by`/agg over `__cluster_id__` — e.g. `most_complete` = argmax of a
   per-row populated-count expression per group), eliminating the per-cluster
   materialization. Keep the conditional `eval_predicate` per-cluster (it is
   cheap and inherently Python), or vectorize the common `state in [...]`-style
   predicates separately. Start with the py-spy split to confirm the
   materialization is the dominant recoverable cost.
