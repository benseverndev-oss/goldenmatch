# Ray on GCE — provisioning runbook

The `bench-ray-cluster` workflow runs the goldenmatch QIS bench against an
ephemeral GCE Ray cluster. This document covers the one-time GCP setup
needed before the workflow can be dispatched.

## What gets provisioned per bench

| node | instance type | count | preemptible | $/hr (us-central1) |
|---|---|---|---|---|
| head | `n2-standard-16` | 1 | no | ~$0.78 |
| worker | `n2-standard-16` | 3 (default) | yes | ~$0.20 each |

Default cost: ~$1 per 30-minute bench. Override via the workflow's
`max_workers` input.

Teardown is automatic — `ray down` runs in `if: always()` and a defensive
`gcloud compute instances delete` sweep catches any stragglers. Worst-case
leak: ~$1/hr if both teardown steps fail.

## One-time GCP setup

### 1. Pick / create a GCP project

```sh
gcloud projects create goldenmatch-ray-bench --name="goldenmatch ray bench"
gcloud config set project goldenmatch-ray-bench
gcloud services enable compute.googleapis.com iam.googleapis.com
```

The project ID becomes the `GCP_PROJECT_ID` GitHub secret.

### 2. Create the service account

```sh
gcloud iam service-accounts create gm-ray-bench \
    --display-name="goldenmatch Ray bench"

SA_EMAIL="gm-ray-bench@$(gcloud config get-value project).iam.gserviceaccount.com"

# Compute Admin: create/delete instances + disks
gcloud projects add-iam-policy-binding "$(gcloud config get-value project)" \
    --member="serviceAccount:$SA_EMAIL" \
    --role="roles/compute.admin"

# Service Account User: let Ray attach this SA to the instances it creates
gcloud projects add-iam-policy-binding "$(gcloud config get-value project)" \
    --member="serviceAccount:$SA_EMAIL" \
    --role="roles/iam.serviceAccountUser"

# Issue a JSON key
gcloud iam service-accounts keys create gm-ray-bench-key.json \
    --iam-account="$SA_EMAIL"
```

### 3. Set the GitHub secrets

```sh
gh secret set GCP_PROJECT_ID \
    --repo benseverndev-oss/goldenmatch \
    --body "$(gcloud config get-value project)"

gh secret set GCP_SA_KEY \
    --repo benseverndev-oss/goldenmatch \
    --body "$(cat gm-ray-bench-key.json)"

rm gm-ray-bench-key.json   # don't keep the JSON on disk
```

### 4. Dispatch the workflow

```sh
gh workflow run bench-ray-cluster.yml \
    --repo benseverndev-oss/goldenmatch \
    -f rows=5000000 \
    -f label=v44-5m-ray-gce \
    -f max_workers=3
```

The workflow's step summary shows the wall / RSS / F1 numbers when it
completes; the full JSON artifact is downloadable from the run page.

## Verifying teardown

If a run misbehaves, double-check no instances are leaked:

```sh
gcloud compute instances list \
    --filter="labels.ray-cluster-name~goldenmatch-bench" \
    --project="$(gcloud config get-value project)"
```

Anything that shows up there is a leak. Delete with:

```sh
gcloud compute instances delete <name> --zone=us-central1-a
```

## Cost guardrails

- The workers are preemptible by default (60-80% cheaper, ~20% chance of
  preemption per hour). Ray retries preempted partitions; for benches
  under 30 min the retry cost is usually less than the savings.
- Idle timeout is 5 minutes — if the bench finishes early, autoscaler
  releases workers automatically.
- The defensive `gcloud compute instances delete` step at the end of the
  workflow catches anything `ray down` misses.
- Set a GCP billing alert at, say, $20/month to catch surprises.

## Switching off preemption

If preemption-driven retries dominate the bench wall, edit
`.ray/cluster-gce.yaml` and flip the worker `preemptible: true` to
`false`. Cost roughly 4x but wall is more predictable.

## Related

- Spec: `docs/superpowers/specs/2026-05-30-ray-file-based-bench-spec.md`
  (gitignored — local design notes for the broader lane)
- Phase 5 distributed pipeline: `goldenmatch/distributed/pipeline.py`
- QIS bench harness: `scripts/quality_invariant_scale.py`
