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

## One-time setup

### 1. Pick / create a GCP project

```sh
gcloud projects create goldenmatch-ray-bench --name="goldenmatch ray bench"
gcloud config set project goldenmatch-ray-bench
gcloud services enable compute.googleapis.com iam.googleapis.com
```

### 2. Create the GCP service account

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

### 3. Store the GCP creds in Infisical

The workflow pulls these at runtime from Infisical project
`a99885f0`, env `dev`. Set them with `infisical secrets set` (use
`--silent` and redirect stdout so values don't echo into the
terminal):

```powershell
# Project id
$projectId = (gcloud config get-value project)
infisical.cmd secrets set --projectId a99885f0 --env dev `
    GCP_PROJECT_ID=$projectId > $null

# Service account JSON (multi-line; pass via env var to avoid quoting)
$env:_GM_SA_JSON = Get-Content gm-ray-bench-key.json -Raw
infisical.cmd secrets set --projectId a99885f0 --env dev `
    GCP_SA_KEY=$env:_GM_SA_JSON > $null
Remove-Item env:_GM_SA_JSON
Remove-Item gm-ray-bench-key.json   # don't keep the JSON on disk
```

Verify by name only (no values):

```powershell
infisical.cmd secrets --projectId a99885f0 --env dev | Select-String GCP
```

### 4. Create a Machine Identity for GitHub Actions

The workflow authenticates to Infisical via a dedicated Machine
Identity using Universal Auth (client id + secret pair):

1. Infisical web UI → `goldenmatch` project → Access Control →
   Machine Identities → Create.
2. Name: `goldenmatch-bench-ray-cluster`. Auth method: **Universal
   Auth**. Trusted IPs: `0.0.0.0/0` (Actions runners are dynamic;
   restrict later if needed).
3. Project permissions: read-only on env `dev`, paths `/GCP_*`.
4. Copy the generated **Client ID** and **Client Secret**. The
   secret is shown ONCE.

### 5. Set the two GitHub Actions secrets

```sh
gh secret set INFISICAL_CLIENT_ID \
    --repo benseverndev-oss/goldenmatch \
    --body "<paste client id>"

gh secret set INFISICAL_CLIENT_SECRET \
    --repo benseverndev-oss/goldenmatch \
    --body "<paste client secret>"
```

These two are the ONLY GH-Actions secrets the workflow needs. Future
Infisical-backed secrets reuse the same auth pair.

### 6. Dispatch the workflow

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
