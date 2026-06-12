# Ray file-based bench harness — design

**Status:** Draft, 2026-05-30
**Replaces:** `2026-05-30-ray-phase5-routing-spec.md` (retracted)

## Problem

The Ray Phase 5 streaming pipeline is feature-complete from a code-path
perspective (PRs #337-#367 over April-May 2026). But it has never been
benchmarked on its **native shape**:

- Input: partition-aware reads from on-disk Parquet (or remote storage)
- Compute: distributed across N worker nodes
- Output: streaming `write_parquet` (no driver collect)

Every previous Ray bench in this repo has done one of:

1. **`bench-phase5-simulated`** — 4 Ray workers inside ONE
   `large-new-64GB` runner. Honest scoping per CLAUDE.md: *"single NIC
   + single disk + shared OS page cache; this is a regression check,
   NOT a Splink-Spark parity proof."*
2. **`bench-phase5-end2end`** — designed for real multi-node, gated on
   `RAY_ADDRESS` secret + `bench_100000000.parquet` on the bench
   release. **Never run.** The secret was never provisioned.
3. **The QIS bench** (`bench-quality-invariant-scale`) — single runner,
   in-memory DataFrame input. NOT Ray's native shape.

PRs #603-#606 from this session shipped a routing wrapper around
`dedupe_df(df)` to test Ray on the QIS bench's in-memory workload.
v43 result (5M-ray-realistic): 370s / 16 GB / F1 0.9925, vs bucket's
240s / 18 GB / F1 0.9923. **Ray lost on wall by 1.54×, won on RSS by
2 GB.** Both numbers are on the wrong workload for Ray.

## Goal

Design and stand up a bench harness that compares Ray and bucket on
the **same Parquet input**, calls each via its NATIVE entry point
(file-based `dedupe(files=...)` for Ray; current in-memory
`dedupe_df(df)` is still bucket's shape), and validates Ray's scale-out
at 100M+ rows where bucket physically cannot fit.

## Out of scope

- Provisioning a Ray cluster (infrastructure work; happens outside
  this spec).
- The Ray Phase 5 pipeline's correctness — already shipped and (modulo
  PR #606's documented cheat-line) functional at 5M.
- Optimizing Ray below 100M. Below that, bucket wins by construction.

## Inventory of what exists

### File-based input plumbing

| component | status |
|---|---|
| `goldenmatch.distributed.dataset.read_parquet_partitioned` | shipped (PRs #337-#340), tested at 25M |
| `apply_transforms_distributed(ds, transforms)` | shipped, used by Phase 5 |
| `_load_input_frames(config)` env-gate | shipped (`backend="ray" + GOLDENMATCH_ENABLE_DISTRIBUTED_RAY=1`) |
| `dedupe(files=...)` entry point | shipped, but **does it actually route Ray to the partitioned loader?** Need to audit. |

### Streaming pipeline

| component | status |
|---|---|
| `_run_phase5_pipeline` | shipped, validated at 5M via v43 (with #606 cheat-line) |
| `score_blocks_distributed` | shipped |
| `dedup_pairs_distributed` | shipped, but uses the #606 driver-collect cheat-line |
| `build_clusters` polymorphic on Ray Dataset | shipped (Phase 3) |
| `build_golden_records_batch` polymorphic on Ray Dataset | shipped (Phase 4) |
| `materialize_cluster_dict` | shipped — explicit cheat-line at the Phase 3→4 boundary |

### Multi-node infrastructure

| component | status |
|---|---|
| Multi-node Ray cluster bootstrap | docs at `docs/distributed-ray-cluster-setup.md` (Splink-posture: docs not bootstrap) |
| `bench-phase5-end2end` workflow | exists, requires `RAY_ADDRESS` secret + bench-release Parquet asset |
| 100M Parquet on `bench-dataset-v1` release | not generated |
| `RAY_ADDRESS` GitHub secret | not provisioned |

## Proposed work, ordered by infrastructure dependency

### Phase A: in-repo plumbing (no external infrastructure)

#### PR A1: audit and document `dedupe(files=...)` Ray routing

Trace what actually happens when a user calls
`dedupe(files=["data.parquet"], config=cfg, backend="ray")`:

- Does `_load_input_frames` engage the partitioned loader?
- Does the controller's auto-config route through the distributed
  path or fall back to a driver materialize?
- Where does the pipeline's prep frame live (driver or workers)?
- Does scoring engage `score_blocks_distributed` or fall back to the
  legacy `score_blocks_ray` swap?

Output: a clear mermaid diagram + audit table of where each stage
runs. No code changes; this is the basis for PR A2+.

#### PR A2: generate Parquet bench dataset

Modify `scripts/generate_phase5_dataset.py` (or write a sibling) to
produce QIS-shape Parquet files at multiple scales (1M, 5M, 25M,
100M). Upload to `bench-dataset-v1` GitHub Release as assets.

QIS-shape = realistic synthetic person data, 5 rows per cluster, the
same shape we've benched bucket against all session.

This is the **honest apples-to-apples input**. Both bucket and Ray
read the same Parquet.

#### PR A3: file-based bench workflow

New `bench-ray-file-based.yml` workflow that:

- Takes `rows`, `backend` (`bucket | ray`), `cluster_address` inputs.
- Downloads the appropriate Parquet from the bench-dataset-v1 release.
- Calls `dedupe(files=...)` with the right backend.
- If `backend=ray` AND `cluster_address` is provided, sets
  `RAY_ADDRESS` to point at the external cluster. Otherwise uses
  local Ray (single-runner simulated).
- Records the same stage timings + RSS + F1 as the QIS harness.

The output JSON shape matches the existing QIS bench so the
comparison reports stay consistent.

### Phase B: validation rungs (no external infrastructure)

#### PR B1: bucket-file-based baseline at 5M, 25M, 100M

Run the new workflow with `backend=bucket` on each scale. This
establishes the file-based bucket baseline — different from the
in-memory `dedupe_df(df)` numbers we have because file reading has
its own cost.

100M-bucket WILL fail (out of memory on one box). That's the
expected breakpoint; it's the rung that justifies Ray's existence.

#### PR B2: ray-simulated bench at 5M, 25M

Run the new workflow with `backend=ray` + no `cluster_address` (local
Ray on one runner). This validates the file-based Ray path
end-to-end. We expect it to lose to bucket at 5M-25M (Ray init +
single-node bottleneck), but the gap should be smaller than v43's
1.54× because we're not paying for `from_arrow` materialization.

### Phase C: real-cluster kill criterion (requires infrastructure)

#### Infrastructure dependency

Someone provisions a Ray cluster:

- 4 nodes minimum (so the architecture is exercised)
- 16 cores / 64 GB each (matches the simulated baseline)
- `RAY_ADDRESS` set as a GitHub secret on the goldenmatch-monorepo repo

Possible providers: Railway (we already use it for the MCP service),
GCE, EKS, locally-provisioned. The choice is outside this spec.

#### PR C1: ray-cluster bench at 100M, 200M, 500M

Run the new workflow with `backend=ray` + `cluster_address` pointing
at the provisioned cluster. Generate Parquet at 100M (extending the
generator from A2) and at 200M / 500M.

**Whole-lane kill criterion:** Ray completes 100M end-to-end within
30 minutes wall AND under 40 GB max per-node RSS. (Numbers from the
spec at `docs/superpowers/plans/2026-05-19-phase-5-multi-node-parity.md`.)

If Ray passes 100M: close the documented cheat-lines (#606,
`materialize_cluster_dict`, etc.) one PR at a time, re-bench at each
to verify. Lane is alive.

If Ray fails 100M: real architectural problem; revisit Phase 5's
design before further investment.

## Risks

- **Cluster provisioning is a real ops task.** GitHub Actions can't
  do it. Someone has to commit to maintaining a Ray cluster for the
  duration of this lane.
- **The 100M Parquet generator may itself be slow.** CLAUDE.md notes
  the generator at 50M ran in ~70s; 100M extrapolates to ~140s.
  Should fit in a `large-new-64GB` runner, but worth verifying.
- **Closing the cheat-lines is its own multi-PR initiative**, only
  unlocked after Phase C1's pass.

## Decision needed

Two questions to answer before this lane proceeds:

1. **Is anyone going to provision the Ray cluster?** Without this,
   the lane stalls at Phase B. Phase A and B are useful (real
   file-based bench harness) but don't validate Ray's value
   proposition.
2. **What's the target deployment shape?** If goldenmatch users
   primarily run on one machine via `dedupe_df(df)`, Ray is a niche
   feature. If users have S3-scale data and read from Parquet, Ray
   is core. The answer determines how much to invest.

If both answers are "yes" / "S3-scale matters", ship Phase A and B
this session, queue Phase C for when the cluster arrives. If either
answer is "no", acknowledge the lane is parked and document that
the existing Ray code is correctness-tested but performance-
unvalidated at scale.
