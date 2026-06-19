# Sail-on-GKE bring-up kit + smoke proof

Reusable kit for running the `goldenmatch.sail` (Spark Connect) pipeline on a real
distributed Sail cluster on GKE, plus the diagnostic that motivated the WCC
lineage-checkpoint fix in this package.

## Result of the 2026-06-15 smoke proof
The full Sail pipeline runs **end-to-end, distributed, on a real multi-node GKE Sail
cluster** (driver in `kubernetes-cluster` mode spawns worker pods across nodes; the
rapidfuzz scorer UDF runs on workers). At N=4000 it produced 747 golden records in
~35s. It did NOT scale: the WCC stage blew up superlinearly (2.9s @1.5K -> 22.4s @4K
-> wedged by 12K rows).

## Root cause + fix
The WCC is `goldenmatch/sail/clustering.py` (ours). It iterates Spark joins and, per
round, re-joins `labels` against itself, growing the Spark Connect plan unbounded.
Sail has no working lineage-truncation primitive (`cache` is a no-op; `persist` /
`localCheckpoint` / `checkpoint` are "planned" -- see Sail issue
https://github.com/lakehq/sail/issues/482, which we commented on with this benchmark).
So the loop recomputes all prior rounds (O(rounds^2)) and wedges.

Fix (this package): truncate lineage with a parquet write+read barrier
(`_truncate_lineage`) every N rounds. `connected_components_scale` already had this;
this kit's PR brings the label-prop `connected_components` to parity and threads
`wcc_checkpoint_interval` / `wcc_checkpoint_dir` through `run_sail_pipeline` for both
algorithms. Opt-in (default off = byte-identical). At scale the checkpoint dir must be
shared storage reachable by all workers (`gs://...` via Workload Identity).

## Files
- `Dockerfile` -- driver/worker/bench image: `python:3.12-slim` + `pysail==0.6.4` +
  `goldenmatch[sail]` + pinned `pandas<2.3` / `pyarrow<18` (pyspark 3.5 needs them) +
  `setuptools` (py3.12 dropped distutils). Bakes a 300k-row smoke dataset at build.
- `gen_sail_smoke.py` -- synthetic dataset (soundex-spread surnames; prints block stats).
- `run_smoke.py` -- in-cluster end-to-end runner (`SMOKE_DATA`, `SMOKE_WCC` env).
- `diag.py` -- per-stage timing probe (mounted via ConfigMap; how the WCC wall was found).
- `sail.yaml` -- driver Deployment + Service + RBAC (`${SAIL_IMAGE}` substituted at apply).
- `bench-job.yaml` / `diag-job.yaml` -- the run + diagnostic Jobs.

## Runbook
```
# APIs + tools
gcloud services enable container.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com
gcloud components install kubectl gke-gcloud-auth-plugin

# image (Cloud Build; no local Docker)
gcloud artifacts repositories create sail --repository-format=docker --location=us-central1
gcloud builds submit --tag REGION-docker.pkg.dev/PROJECT/sail/sail-goldenmatch:v1 \
  --timeout=1800 .

# cluster + deploy (render ${SAIL_IMAGE} -> the AR image before apply)
gcloud container clusters create sail-smoke --zone us-central1-a --num-nodes 3 \
  --machine-type e2-standard-4
gcloud container clusters get-credentials sail-smoke --zone us-central1-a
kubectl apply -f sail.yaml
kubectl -n sail rollout status deploy/sail-spark-server
kubectl apply -f bench-job.yaml
kubectl -n sail logs job/sail-smoke-bench -f

# teardown
gcloud container clusters delete sail-smoke --zone us-central1-a --quiet
```

## Operational gotchas
- Clean up worker pods by `sail-worker-*` name, NEVER by `-l app.kubernetes.io/name=sail`
  (that label also matches the driver).
- Deleting the bench Job orphans the driver's worker pods.
- Sail re-provisions a fresh worker pool per stage (stateless-by-design); the wall-time
  tax on iterative pipelines is mitigated by lineage truncation, not warm pools.
