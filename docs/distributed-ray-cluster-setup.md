# Multi-node Ray cluster setup for goldenmatch Phase 5

goldenmatch's Phase 5 distributed pipeline runs against an existing Ray
cluster. We don't ship cluster bootstrap automation — same posture Splink
takes for Spark. This document describes the cluster shape that the
roadmap's parity bench targets.

## Why goldenmatch doesn't ship the cluster

Splink supports Spark but doesn't ship a Spark cluster. Same posture
here: bringing up multi-node Ray is an ops problem (`ray up`, GKE/EKS
Helm, etc.) that's better handled by the team running the workload than
embedded in a Python package. Cluster lifecycle, autoscaling, network
policy, and IAM all belong in your existing platform stack.

## Recommended cluster shape

Phase 5 kill criterion: **100M-row dedupe in under 30 min**. To hit that:

| Role | Node type | Count |
|---|---|---|
| Head | 4-core / 16 GB RAM | 1 |
| Worker | 16-core / 64 GB RAM | 4 |

Total: **64 cores, 272 GB cluster RAM**.

### Why 4 workers minimum

At 100M rows the pair list after scoring is ~50M+ pairs (~10 GB
materialized). Single-node can't hold that with the cluster dict + row
data in memory. 4 workers gives ~16 GB pair-list-per-node which fits
comfortably; the Ray Dataset shuffle distributes the rest.

### Why 64 GB workers

Phase 1-4 measured at 16c/64GB and produced known walls:
- 25M end-to-end on single-node bucket: 6.5 min / 57.7 GB peak RSS.
- 25M / 8.7s cluster wall (Phase 3 scipy via Ray Dataset).
- 25M / 109.2s golden wall (Phase 4 distributed).

100M is 4x the row count on 4x the workers. Wall expected ~equivalent to
single-node 25M.

### Network

10 Gbps inter-node minimum. The shuffle stages (pair dedup,
golden record repartition by cluster_id) push GBs across the network.
Slower interconnect dominates wall.

## `ray up` config example

```yaml
# ray-cluster.yaml
cluster_name: goldenmatch-phase5

provider:
  type: gcp  # or aws, azure, kubernetes
  region: us-central1
  project_id: your-project

head_node_type: head
worker_node_types:
  head:
    node_config:
      machineType: n2-standard-4
    resources: {"CPU": 4}
    min_workers: 0
    max_workers: 0
  worker:
    node_config:
      machineType: n2-standard-16
    resources: {"CPU": 16}
    min_workers: 4
    max_workers: 4

# Pin python + packages to match the goldenmatch dev image.
setup_commands:
  - pip install "goldenmatch[ray]==1.16.0"
  - pip install psutil pandas scipy

# Increase shared-memory size for Ray's object store.
# Default 30% is too low for the shuffle stages at 100M.
file_mounts: {}
```

Equivalent Kubernetes setup: use the [KubeRay
operator](https://docs.ray.io/en/latest/cluster/kubernetes/index.html)
with a `RayCluster` resource. Worker spec:

```yaml
# kuberay-cluster.yaml (excerpt)
spec:
  workerGroupSpecs:
  - replicas: 4
    template:
      spec:
        containers:
        - name: ray-worker
          resources:
            requests:
              cpu: "16"
              memory: "64Gi"
          # Increase /dev/shm beyond k8s default 64 MB so Ray's
          # plasma object store gets a real share of RAM:
          volumeMounts:
          - mountPath: /dev/shm
            name: dshm
        volumes:
        - name: dshm
          emptyDir:
            medium: Memory
            sizeLimit: 20Gi
```

## Sizing the Ray object store

Per worker, set `object_store_memory` to 20-30 GB. The default 30% of
node memory is fine on a dedicated worker; double-check via
`ray status` after the cluster is up.

```python
import ray
ray.init(address="auto", object_store_memory=20 * 1024**3)  # 20 GB
```

For Phase 5 the goldenmatch client picks this up automatically when
`RAY_ADDRESS` is set; no code change needed on the bench script.

## Verifying the cluster

```bash
RAY_ADDRESS=ray://head-ip:10001 python -c "import ray; ray.init(); print(ray.cluster_resources())"
```

Expect:
```
{'CPU': 68.0, 'memory': 270000000000.0, 'object_store_memory': 80000000000.0, ...}
```

Ray Dashboard runs on the head node port 8265 by default. SSH-tunnel or
proxy it for live job tracking during the bench.

## Running the Phase 5 bench

```bash
# Locally, with RAY_ADDRESS pointing at the cluster head.
export RAY_ADDRESS=ray://YOUR-HEAD-IP:10001
export GOLDENMATCH_ENABLE_DISTRIBUTED_RAY=1
export GOLDENMATCH_DISTRIBUTED_PIPELINE=2

python packages/python/goldenmatch/scripts/bench_phase5_end2end.py \
    --input bench-dataset-v1/bench_100000000.parquet \
    --output gs://your-bucket/phase5_golden.parquet
```

The bench script runs on the client; all heavy work happens on the Ray
cluster. Client peak RSS reported in the bench output is the local
process — usually < 1 GB.

## Cost framing

Rough back-of-envelope (GCP, us-central1, 2026 pricing):

| Item | $/hr |
|---|---|
| 1 × `n2-standard-4` (head) | ~$0.20 |
| 4 × `n2-standard-16` (workers) | ~$3.10 |
| **Total** | ~$3.30/hr |

A 30-min Phase 5 bench run costs ~$1.65 in cluster time. Cheap; the
overhead is in the dataset upload + ops setup, not compute.

## Bringing down the cluster

```bash
ray down ray-cluster.yaml
```

Don't forget — idle Ray clusters bill the same as busy ones. The Phase 5
bench is `workflow_dispatch` only on the GitHub side specifically
because it requires an out-of-band cluster that someone has to remember
to tear down.

## When you don't need multi-node

Below ~50M rows, the single-node bucket pipeline (run `26095134836`:
25M in 6.5 min / 57.7 GB on one 64 GB box) is faster than distributing.
Phase 5 is the regime where single-node OOMs — don't reach for it
otherwise.

The roadmap kill criteria document where the boundaries sit:

- < 5M rows / < 50M pairs: scipy.csgraph on driver (Phase 3 routing)
- < 5M multi-member clusters: in-memory golden (Phase 4 routing)
- < 25M rows: single-node bucket pipeline (no distributed pipeline at all)
- >= 50M rows: multi-node distributed pipeline (Phase 5)

Numbers calibrated against actual measurements; revisit when hardware
changes (256 GB workers move the boundary higher).
