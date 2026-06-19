# Sail #2081 test harness — run notes

Goal: confirm the goldenmatch Sail tier now completes the multi-stage iterative-WCC
ER pipeline at scale, with lakehq/sail **#2081** ("don't fail a pending task while
workers are still launching"). #2081 was authored from this exact pipeline
(block → score → dedup → iterative WCC at 10M/100M on GKE) and is the driver-side
wedge that made the prior 100M binding run an HONEST-NULL.

## The wrinkle this harness solves

#2081 merged upstream 2026-06-17, but the latest **pysail wheel is 0.6.4 (2026-06-06)**
— it predates the fix. So `pip install pysail==0.6.4` ships the `sail` binary WITHOUT
#2081. This harness's `Dockerfile` builds the `sail` binary from the #2081 commit
(`SAIL_GIT_REF=3e6c419…`, Sail's own `cargo build -p sail-cli --release` recipe) and
drops it over the pysail-installed one, so the driver + every worker pod run the fix.
The Python side stays pysail 0.6.4 (Spark Connect client; 0.6.x protocol unchanged by
a scheduler bugfix).

**When pysail 0.6.5 (with #2081) ships:** delete the `sail-builder` stage + the
`COPY --from=sail-builder` line and bump `ARG PYSAIL_VERSION` — no source build needed.

## 1. Build the image (Cloud Build — the Rust build runs on GCP, not your box)

The Rust workspace build is ~10–25 min, so raise the Cloud Build timeout:

```bash
REGION=us-central1; PROJECT=<your-project>
IMG=${REGION}-docker.pkg.dev/${PROJECT}/sail/sail-goldenmatch:2081
gcloud builds submit --timeout=3000s --tag "$IMG" .
# (the Dockerfile defaults SAIL_GIT_REF to the #2081 merge commit; override with
#  --substitutions or a --build-arg via a cloudbuild.yaml if you want main HEAD.)
```

Verify the binary in the build log: the `sail --version` line + that the build came
from the `sail-builder` stage (not just `pip install pysail`).

## 2. Generate + upload the scale dataset (workers read it from gs://)

The 300k smoke is baked into the image for connectivity only — it's one-stage and
does NOT trigger the inter-stage worker scale-up #2081 is about. The scale test reads
from object store so all worker pods can read it:

```bash
BUCKET=gs://<your-bucket>/sail-bench-10m
# generate locally (gen_sail_smoke.py is in the image; or run it anywhere goldenmatch[sail] is):
python gen_sail_smoke.py /tmp/sail10m 10000000 64
gsutil -m cp -r /tmp/sail10m/* "$BUCKET/"
```

The Sail cluster's `sail-user` ServiceAccount needs read on the bucket (Workload
Identity — see README.md). Start at 10M (enough to provoke #2081, ~10x cheaper than
100M); raise to 100M + `activeDeadlineSeconds` once 10M is green.

## 3. Deploy the cluster + driver, then run the bench

```bash
gcloud container clusters create sail-smoke --zone us-central1-a --num-nodes 3 --machine-type e2-standard-8
gcloud container clusters get-credentials sail-smoke --zone us-central1-a

export SAIL_IMAGE="$IMG"
export SAIL_DATA="$BUCKET"
envsubst < sail.yaml          | kubectl apply -f -
envsubst < bench-job-2081.yaml | kubectl apply -f -

kubectl -n sail logs -f job/sail-bench-2081
```

`bench-job-2081.yaml` runs `SMOKE_WCC=scale` (pointer-jumping WCC → more stages →
more inter-stage scale-ups → exercises #2081 hard).

## 4. Success criteria (the A/B)

- **PASS (#2081 build):** the run reaches the final `RESULT {...}` JSON line — the
  full block→score→dedup→WCC pipeline completes. The driver log shows tasks created
  during a worker scale-up being **rescheduled** (not failed) until a worker registers.
- **The 0.6.4 baseline (control):** rebuild the image with `--build-arg PYSAIL_VERSION=0.6.4`
  AND no source binary (i.e. the old Dockerfile, `git stash` this change), run the same
  bench — it should die mid-WCC with `task scheduling timeout` on a task in `Created`
  state during an inter-stage scale-up. That failure → no failure is the proof #2081
  unblocks the tier.
- Watch for: `task scheduling timeout` in the driver log = the bug still firing (means
  the #2081 binary didn't actually land — recheck step 1's build log).

## 5. Cleanup

```bash
gcloud container clusters delete sail-smoke --zone us-central1-a --quiet
gsutil -m rm -r "$BUCKET"   # if you don't want to keep the dataset
```

## Cost

A 10M run on a 3×e2-standard-8 cluster is short-lived (well under the binding-run's
~$2–3 order for a Ray 100M; Sail GKE is similar). Tear the cluster down right after.
