# Ray at scale: the setup that actually works

Operational companion to the two setup runbooks. Those tell you how to *provision*
a cluster ([`ray-gce-cluster.md`](ray-gce-cluster.md)) and how to *start* one
([`../distributed-ray-cluster-setup.md`](../distributed-ray-cluster-setup.md)).
This one is about the settings that decide whether a run is fast, comparable and
trustworthy — and about the traps that cost real money before they were written
down.

Everything with a number attached was measured on this repo's 100M rung. Where
something is not known, it says so.

---

## 1. Decide whether to distribute at all

Distribution is not free and it is not always a win. The single-box `bucket`
backend is validated through 200M and is simpler. See
[`../scale-envelope.md`](../scale-envelope.md) for the full ladder.

Reach for Ray when **the frame does not fit one box**, not when you want it
faster. On this workload the distributed engine's advantage is capacity, and the
comparison point is sobering: a Spark lane on comparable hardware ran 100M rows /
927.9M candidate pairs in **958s**, against **2906s** here.

> **Only scoring genuinely distributes today.** Clustering runs on the driver by
> default (§6). Plan around that, because it caps what any amount of parallelism
> can buy.

---

## 2. Cluster shape: the head is not a worker

The head and the workers do different jobs and should not be sized alike.

| role | shape in use | why |
|---|---|---|
| head | `n2-highmem-32` (32 vCPU / **256 GB**) | the driver materialises the full frame *and* the whole scored edge set |
| worker x3 | `n2-standard-16` (16 vCPU / 64 GB) | per-partition scoring, embarrassingly parallel |

**Size the head by driver RSS, not by core count.** Measured driver peak at 100M
is **125-132 GB**. A 128 GB head does not fit. This is the most common way to
lose a run an hour in.

Other settings that matter:

- `min_workers == max_workers`. Fixing both stops the autoscaler removing workers
  during long driver-side phases when nothing is queued.
- `ulimit -n 65536` before `ray start`. Ray opens a lot of sockets.
- `preemptible: false` while a code path is unproven. Spot capacity for large
  shapes is intermittent, and a preemption mid-run looks like a bug.
- `scopes: cloud-platform` on the node service account. Default scopes omit
  storage, which breaks `gs://` checkpoint reads *silently, and only at scale*.

---

## 3. Pin the environment, then record what actually resolved

This is the difference between a benchmark and an anecdote.

Between two 100M runs of the same fixture, **every dependency moved**:
`goldenmatch-native` 0.1.6 to 0.2.0 (11 releases), polars 1.41.2 to 1.44.0,
pyarrow 24.0.0 to 25.0.1, numpy 2.4.6 to 2.5.2, ray 2.55.1 to 2.58.0. The second
run came back **2.43x slower with byte-identical output**, and with eight
variables moved at once it was attributable to none of them.

**Pin exactly (`==`), not by floor.** `goldenmatch` itself stays pinned by git
SHA — it is the thing under test. Bump one dependency per commit, so a wall-clock
change has exactly one candidate cause.

**Then capture what landed**, because pins only record what you asked for:

- `pip freeze` from the head, into the run artifact
- the per-instance `cpuPlatform` from GCE. `n2` spans several CPU generations, so
  "same machine type" does not mean same hardware

> **Read the constraint already in the file before asking a registry what is
> newest.** A drift audit that queries PyPI for latest versions while ignoring the
> constraints in the config will "find" drift that never happened — and pinning to
> that answer can cross a cap that existed for a reason (§7.2).

---

## 4. The knobs that matter

All are `GOLDENMATCH_DISTRIBUTED_*`. Full reference:
[`docs-site/goldenmatch/tuning.mdx`](../../docs-site/goldenmatch/tuning.mdx).

### Routing

| knob | default | notes |
|---|---|---|
| `PIPELINE` | unset | `2` = the real Phase-5 engine (block-shuffle + distributed WCC). Without it, `backend=ray` is an in-memory path that does **not** distribute. |
| `BLOCK_SHUFFLE` | `1` | co-locates by blocking key so pairs cross partition boundaries correctly. |
| `WCC_SCRATCH` | unset | **must** be a shared `gs://` path on multi-node; a node-local path breaks cross-node parquet reads. Keep it in the cluster's own region — cross-region adds egress and latency to the stage you are measuring. |

### Scoring throughput

| knob | default | when to move it |
|---|---|---|
| `SCORE_NUM_CPUS` | `2` | CPUs reserved per scoring task. Lower means more concurrency and more RAM per node. |
| `SHUFFLE_PARTS` | `min(256, cpu*4)` | **raise it** (512 at 100M) when wide exploded records form giant blocks and pin `_score` to one node. |
| `SCORE_PROJECT` | `1` | projects to scoring-relevant columns before the shuffle. Output-invariant. Leave on. |
| `OP_RESERVATION` | unset | lower toward `0.2` when `_score` shows `[backpressured:tasks(ResourceBudget)]` with the object store well under capacity. |
| `SCORE_CONCURRENCY` | unset | explicit task-pool size once blocks are narrow. |

### Clustering

| knob | default | notes |
|---|---|---|
| `CLUSTERING_THRESHOLD` | memory-aware | above the pair count means **driver-side** clustering. Read §6 before changing. |
| `WCC` | `two_phase` | `randomized_contraction` is what the at-scale pipeline passes. Avoid `pointer_jump` (can deadlock). |

### Diagnostics (output-invariant, near-free)

| knob | what it splits |
|---|---|
| `GOLDENMATCH_CLUSTER_DEBUG=1` | driver clustering: pull / derive-ids / index / connected-components / build-output |
| `GOLDENMATCH_BUCKET_DEBUG=1` | per-bucket scorer: prep / kernel / post-filter |

---

## 5. Getting settings to the processes that read them

**`ray submit` hands the driver a fresh shell.** Ray executes remote commands as
`docker exec <container> /bin/bash -c ...` and forwards only its *own* env vars.
So three plausible mechanisms all fail silently:

| mechanism | what it actually reaches |
|---|---|
| `export` in the CI step before `ray submit` | nothing — the driver gets a fresh shell |
| `export` in `head_start_ray_commands` | the raylet and its workers, **not** the ray-submit driver |
| appending to `~/.bashrc` via `ray exec` | **nothing** — non-interactive bash never sources it |

**Use `docker.run_options` in the cluster yaml.** Those apply at `docker run`, so
every later `docker exec`, driver included, inherits them:

```yaml
docker:
  run_options:
    - "--env"
    - "GOLDENMATCH_NATIVE=1"
```

For per-dispatch values, pass **CLI flags** instead: argv survives the submit
boundary. Anything read as an env var must also be read *at call time*, never
captured into a module constant at import — the driver imports the package before
it can set anything.

> The failure mode is a knob that changes nothing rather than erroring. A
> `BUCKET_DEBUG` dispatch set via `~/.bashrc` completed a full 100M run and
> produced no timing at all, which is indistinguishable from a measured null.

---

## 6. The clustering trade, stated honestly

At 100M the at-scale config sets `CLUSTERING_THRESHOLD=2000000000`, routing
clustering to the **driver** rather than the distributed WCC. The reason recorded
in the code is that the distributed WCC's `gs://` checkpoint rounds were a
multi-hour tail.

That is a choice between two poor options, not evidence the driver path is good:

- connected components on 226M edges costs **~70 seconds** (scipy on 20M edges is
  6.4s, near-linear)
- the driver stage takes **~16 minutes**

So the stage is dominated by data movement, not by clustering. Two fixes have
landed against it — arrow-native id derivation, and a single projected pass
instead of two full ones — taking the 100M dedupe from **6736s to 2906s (2.32x,
byte-identical output)**. Roughly 12 minutes of that stage is still unattributed,
which is what `GOLDENMATCH_CLUSTER_DEBUG` exists to name.

**If you are choosing a threshold:** driver-side clustering needs the whole edge
set in driver RAM and scales with one core. Distributing it is the architectural
answer; both implementations currently need work.

---

## 7. Traps that cost real runs

### 7.1 Zone capacity is not quota

`ZONE_RESOURCE_POOL_EXHAUSTED` means Google has no such hardware in that zone
right now. It is **not** a quota error, and a quota pre-flight cannot predict it.

Observed in one day: `n2-highmem-32` unavailable across **all four**
`us-central1` zones, and `c2d-highmem-32` — a different vendor's silicon —
unavailable in the same four. `us-east1-b` had capacity immediately.

Make the **zone a dispatch input** and derive the region from it, so the two
cannot disagree. A capacity miss then costs a re-dispatch rather than a code
change, and nothing at all in money: the run dies before provisioning.

### 7.2 pandas 3.x breaks Ray's hash partitioner

Ray 2.56.0 computes
`hash_pandas_object(table.to_pandas(types_mapper=pd.ArrowDtype)).values` and then
`np.mod(hashes, n, out=hashes)`. Under **pandas 3.x** that `.values` is a
**read-only** view of the Arrow buffer, so the in-place write raises:

```
ValueError: output array is read-only
```

About 10 minutes in, after provisioning — so this one costs cluster time. Keep
pandas `<3` for Ray 2.56.

### 7.3 `string` vs `large_string`

polars `.to_arrow()` emits `large_string` (64-bit offsets); building a table with
pyarrow directly emits `string` (32-bit). Ray's shuffle path handles the former.

This is invisible to value-level checks: a fixture verified cell-by-cell through
`to_pylist()` compares **equal** across the two types. **Value equality is not
type equality**, and the type is what downstream dispatches on. Assert the
schema, not only the data.

### 7.4 Do not run Ray suites on a Windows dev box

They OOM inside Ray's serialiser (`MemoryError: Unable to allocate internal
buffer`), which makes pass/fail nondeterministic. An A/B run that way will show
regressions that are not there. Local Ray results are not evidence in either
direction; Linux CI (`distributed_wcc`, `distributed_broad`) is the gate.

---

## 8. Measured envelope

100M rows, realistic shape, 1x `n2-highmem-32` + 3x `n2-standard-16` (80 vCPU):

| configuration | dedupe | driver RSS | pairwise F1 |
|---|---:|---:|---|
| pre-optimisation | 6736s | 129.8 GB | 0.926551 |
| + arrow-native id derive | 3237s | 125.2 GB | 0.926551 |
| + single projected pass | **2906s** | 132.0 GB | 0.926551 |

Output is byte-identical across all three — same tp/fp/fn. Every gain is pure
overhead removal, not an accuracy trade.

Stage split at the last measurement: scoring ~38 min (all four nodes at 33-50%
CPU), driver clustering ~16 min (one core). Provisioning adds ~10 min.

---

## 9. Diagnosis playbook

Read **host** metrics, not Ray's logical reservations. `ray status` reports
*reserved* CPUs; a task holding 2 reserved CPUs while using a fraction of one
shows as busy there and idle in Cloud Monitoring. They can disagree for an hour
without either being wrong.

```sh
TOKEN=$(gcloud auth print-access-token)
curl -sS -H "Authorization: Bearer $TOKEN" --get \
  "https://monitoring.googleapis.com/v3/projects/PROJECT/timeSeries" \
  --data-urlencode 'filter=metric.type="compute.googleapis.com/instance/cpu/utilization"' \
  --data-urlencode "interval.startTime=START" \
  --data-urlencode "interval.endTime=END"
```

| signature | meaning |
|---|---|
| all nodes 30-50% | distributed scoring — healthy |
| workers ~1%, head ~5% | a driver-side stage (clustering, or oracle scoring) |
| near-zero everywhere, no progress | a park or stall, not slow work |
| head busy, workers idle, no I/O | driver-side CPU with no data movement |

Swap the metric type for `network/received_bytes_count` or
`disk/read_bytes_count` to separate "moving data" from "computing". 26 MB over 15
minutes with zero disk reads is not an I/O stage, whatever it looks like.

---

## 10. Checklist

- [ ] Head sized by driver RSS (256 GB+ at 100M); workers by core count
- [ ] `min_workers == max_workers`
- [ ] `cloud-platform` scope on node service accounts
- [ ] Every dependency pinned `==`; pandas `<3` for Ray 2.56
- [ ] `pip freeze` and `cpuPlatform` captured into the run artifact
- [ ] Cluster-wide env in `docker.run_options`; per-dispatch values as CLI flags
- [ ] Env read at call time, never into a module constant
- [ ] `WCC_SCRATCH` a shared `gs://` path in the cluster's own region
- [ ] Zone a dispatch input; region derived from it
- [ ] Schema asserted, not only values
- [ ] Ray suites verified on Linux CI, not a dev box
