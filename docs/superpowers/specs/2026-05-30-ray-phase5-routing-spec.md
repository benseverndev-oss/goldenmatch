# Ray Phase 5 routing — RETRACTED, see file-based-bench spec

**Status:** Retracted 2026-05-30, same day as drafted
**Replaces:** N/A (this was the original PR sequence; superseded by reframing)
**Superseded by:** `2026-05-30-ray-file-based-bench-spec.md`

## Why this was retracted

The original spec proposed routing `dedupe_df(df)` (in-memory Polars
DataFrame input) through `run_dedupe_pipeline_distributed`. PRs #603,
#604, #605, #606 landed; v43 confirmed the routing path was functional
at 5M (370s / 16 GB / F1 0.9925).

But the comparison was on bucket's home turf:

- `dedupe_df(df)` assumes the input is already materialized in driver
  memory. Bucket is designed for exactly this; Ray's value is in
  **avoiding** the driver materialization. Wrapping `dedupe_df(df)`
  for Ray puts it on a workload where it can't win.
- The QIS bench is single-node (16 cores / 64 GB on one runner). Ray
  cannot out-scale bucket on one node by definition; the bench can
  only validate correctness, not scale-out behavior.
- The routing's `ray.data.from_arrow(df.to_arrow())` IS the driver
  materialization the streaming pipeline is supposed to avoid. We
  built a streaming pipeline and then prefixed it with a "collect
  everything to driver first" entry point.

The cheat-lines we were planning to close (driver-collect dedup,
`materialize_cluster_dict`, etc.) are local minima at 5M. They become
real bottlenecks at 100M+, and the only honest path to validating Ray's
behavior at that scale is a file-based bench against a real multi-node
cluster.

## What lands anyway

- **#604** (pandas dep on bench workflow) — keep. Even the file-based
  path needs pandas because `ray.data` imports it at module load.
- **#606** (driver-collect for `dedup_pairs_distributed`) — keep. It's
  a documented cheat-line workaround for a real Ray bug
  (single-partition HashAggregate). The proper fix (num_partitions
  config or hash-shuffle repartition) is deferred until we have a
  file-based bench against a real cluster to measure on.
- **#603 + #605** — reverted. The routing wrapper put Ray on the wrong
  shape; the workaround flag (`confidence_required=False`) was carried
  through the routing function so it goes with it.

## What this lane needs instead

See `2026-05-30-ray-file-based-bench-spec.md`.
