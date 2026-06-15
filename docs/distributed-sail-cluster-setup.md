# Sail tier — GKE cluster setup + the S4 100M binding bench

How to stand up a **distributed Sail (LakeSail / Spark Connect) cluster on GKE**
and run the S4 binding bench (`bench-sail-100m.yml` / `scripts/bench_sail_100m.py`).
Mirrors the Ray posture in [`distributed-ray-cluster-setup.md`](distributed-ray-cluster-setup.md):
**docs-not-bootstrap — you bring the cluster.** Sail is an *additive* scale-out
option; Ray clustering stays the default
([`decisions/0004`](../context-network/decisions/0004-sail-tier-scope.md)).

> **STATUS: SCAFFOLD.** Authored from the LakeSail Kubernetes guide without a
> live GKE shakedown. The manifests + commands are the right *shape*; treat the
> first real run as a bring-up, and verify the marked points. LakeSail image
> coordinates and `SAIL_*` env keys drift — check
> <https://docs.lakesail.com/sail/latest/guide/deployment/kubernetes.html>.

> **COST:** a multi-node GKE cluster + a 100M run is real (modest) spend. Set a
> budget, and **delete the cluster when done** (last step). This is an
> outward, billed action — run it deliberately.

## What runs where

- **Driver pod** = the Sail Spark Connect server (`Deployment`, `replicas: 1`),
  gRPC on `50051`, exposed by a `Service`. It launches **worker pods on demand**.
- **The bench driver** (`bench_sail_100m.py`) runs *outside* the cluster (your
  laptop or the GitHub runner) and connects via `SAIL_REMOTE=sc://<host>:50051`.
- **UDFs run on the workers.** The rapidfuzz scorer (S1) and the R1 native
  `score_field_pairwise` kernel are PySpark Python UDFs executed in the worker's
  Python env — so the worker image MUST bundle `goldenmatch[sail,native]`. The
  custom image ([`deploy/sail/Dockerfile`](../packages/python/goldenmatch/deploy/sail/Dockerfile))
  is used for BOTH driver and workers (via `SAIL_KUBERNETES__IMAGE`).

## Prerequisites

- `gcloud`, `kubectl`, `docker` installed and authenticated; a GCP project with
  **billing enabled** and the GKE + Artifact Registry APIs on.
- A 100M parquet on `gs://` reachable from the cluster (generate via
  `scripts/generate_phase5_dataset.py`, or reuse the `bench-dataset-v1` release
  asset the Ray bench uses — upload it to your bucket).

```bash
export PROJECT=<your-gcp-project>
export REGION=us-central1
export REPO=goldenmatch           # Artifact Registry repo
export IMAGE=${REGION}-docker.pkg.dev/${PROJECT}/${REPO}/sail-goldenmatch:v1
export BUCKET=gs://<your-bench-bucket>
gcloud config set project "$PROJECT"
gcloud services enable container.googleapis.com artifactregistry.googleapis.com
```

## 1. Create the GKE cluster

```bash
gcloud container clusters create sail-bench \
  --region "$REGION" --num-nodes 1 \
  --machine-type e2-standard-16 \         # 16 vCPU / 64 GB per node; size to the bench
  --enable-autoscaling --min-nodes 1 --max-nodes 8 \
  --workload-pool "${PROJECT}.svc.id.goog"   # Workload Identity for GCS access
gcloud container clusters get-credentials sail-bench --region "$REGION"
```

## 2. Build + push the custom image

```bash
gcloud artifacts repositories create "$REPO" --repository-format=docker --location="$REGION" || true
gcloud auth configure-docker "${REGION}-docker.pkg.dev"
docker build \
  --build-arg GOLDENMATCH_VERSION=<pin-a-version> \
  -t "$IMAGE" packages/python/goldenmatch/deploy/sail
docker push "$IMAGE"
```

> VERIFY: the Dockerfile's `SAIL_IMAGE` base must ship a Python 3.11+ interpreter
> (it runs the UDF workers) and the `sail` CLI. If the LakeSail base is
> distroless, invert the Dockerfile (Python base + install the `sail` binary).

## 3. Grant the cluster GCS access

The workers read the input parquet and write the WCC lineage-checkpoint dir
(`--wcc-checkpoint-dir`, the S2 100M fix), both on `gs://`. Bind the
`sail-user` KSA to a GCS-capable GSA via Workload Identity:

```bash
gcloud iam service-accounts create sail-gcs --project "$PROJECT"
gsutil iam ch serviceAccount:sail-gcs@${PROJECT}.iam.gserviceaccount.com:objectAdmin "$BUCKET"
gcloud iam service-accounts add-iam-policy-binding \
  sail-gcs@${PROJECT}.iam.gserviceaccount.com \
  --role roles/iam.workloadIdentityUser \
  --member "serviceAccount:${PROJECT}.svc.id.goog[sail/sail-user]"
# annotate the KSA after step 4 applies it:
#   kubectl -n sail annotate serviceaccount sail-user \
#     iam.gke.io/gcp-service-account=sail-gcs@${PROJECT}.iam.gserviceaccount.com
```

## 4. Deploy Sail

```bash
sed "s#REPLACE_IMAGE#${IMAGE}#g" \
  packages/python/goldenmatch/deploy/sail/k8s/sail-server.yaml | kubectl apply -f -
kubectl -n sail rollout status deploy/sail-spark-server
# (then the Workload-Identity KSA annotation from step 3)
```

## 5. Connect + run the bench

Local driver via port-forward (simplest; keeps gRPC off the public net):

```bash
kubectl -n sail port-forward service/sail-spark-server 50051:50051 &
export SAIL_REMOTE=sc://localhost:50051
python packages/python/goldenmatch/scripts/bench_sail_100m.py \
  --input "${BUCKET}/bench_100000000.parquet" \
  --wcc-checkpoint-dir "${BUCKET}/_wcc_ckpt" \
  --out .profile_tmp/sail_100m.json
```

Or via the workflow (set the `SAIL_REMOTE` repo secret to a cluster-reachable
endpoint — e.g. an internal LoadBalancer, since the GitHub runner can't reach a
`port-forward`):

```bash
gh workflow run bench-sail-100m.yml -f input="${BUCKET}/bench_100000000.parquet"
```

## 6. Read the verdict

Kill criterion (additive, NOT a Ray retirement): completes where one-box
OOMs/can't, **per-node RSS bounded, wall improves with node count**. Re-run at
1 / 2 / 4 / 8 max-nodes to see the wall scale. Commit the numbers to
`docs/superpowers/specs/2026-06-13-sail-tier-past-one-box-roadmap.md` (R4).

## 7. Tear down (do not skip — cost)

```bash
gcloud container clusters delete sail-bench --region "$REGION" --quiet
```

## Verify-points (because this is a scaffold)

1. **LakeSail base image + `sail` CLI** — image coords drift; confirm the base
   has Python 3.11+ for the UDF workers (Dockerfile note).
2. **`SAIL_*` env keys** — confirm against the current LakeSail K8s guide.
3. **Worker GCS auth** — Workload Identity is the clean path; a mounted SA key
   also works. The first run will tell you if the workers can read `gs://`.
4. **WCC at 100M** — the S2 pointer-jump now checkpoints lineage each round
   (`--wcc-checkpoint-dir`); watch the first 100M run for plan-growth/hangs and
   tune `--wcc-checkpoint-interval` if needed.
