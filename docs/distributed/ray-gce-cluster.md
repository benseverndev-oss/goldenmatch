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
gcloud services enable \
    compute.googleapis.com \
    iam.googleapis.com \
    cloudresourcemanager.googleapis.com \
    iamcredentials.googleapis.com
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

### 3. Store the GCP creds in Doppler

The workflows pull these at runtime from Doppler project `showcase`,
config `dev`:

| Doppler secret name | Format | Decoded form |
|---|---|---|
| `GCP_RAY_BENCH_PROJECT_ID` | plain string | the GCP project id where the bench SA lives (`golden-490919`) |
| `GCP_SA_KEY_B64` | base64-encoded | service account JSON (`goldenmatch-vertex-bench@golden-490919`) |

`GCP_RAY_BENCH_PROJECT_ID` is a Ray-bench-specific name on purpose -- the
existing `GOOGLE_CLOUD_PROJECT` secret points at a different project used
for Vertex AI work. Reusing it would 403 the bench SA on every
cloudresourcemanager call.

The workflows base64-decode `GCP_SA_KEY_B64` and write the JSON straight
to disk; the raw bytes never round-trip through env vars.

Set them by piping the value in, never as an argument -- an argument
lands in shell history and in the process table:

```powershell
# Project id (plain)
gcloud config get-value project | doppler secrets set GCP_RAY_BENCH_PROJECT_ID `
    --scope "D:\personal" -p showcase -c dev --silent

# Service account JSON: base64-encode locally, store the encoded form
$saB64 = [Convert]::ToBase64String([System.IO.File]::ReadAllBytes("gm-ray-bench-key.json"))
$saB64 | doppler secrets set GCP_SA_KEY_B64 `
    --scope "D:\personal" -p showcase -c dev --silent
Remove-Variable saB64
Remove-Item gm-ray-bench-key.json   # don't keep the JSON on disk
```

Verify by name only (no values):

```powershell
doppler secrets --scope "D:\personal" -p showcase -c dev --only-names |
    Select-String "GCP_RAY_BENCH_PROJECT_ID|GCP_SA_KEY_B64"
```

### 4. Create the Doppler service token for GitHub Actions

A service token carries its own project and config, so the workflows need
no login step and no scope flags -- the token *is* the scope. Create it
read-only:

```sh
doppler configs tokens create gh-actions-goldenmatch \
    --scope "D:\personal" -p showcase -c dev --access read --plain
```

The value is printed ONCE. Pipe it straight into the GitHub secret rather
than pasting it, so it never reaches the terminal or shell history:

```sh
doppler configs tokens create gh-actions-goldenmatch \
    --scope "D:\personal" -p showcase -c dev --access read --plain \
  | gh secret set DOPPLER_TOKEN --repo benseverndev-oss/goldenmatch
```

Confirm the token can read both secrets and cannot write, before relying
on it -- a token that silently fails looks exactly like a workflow bug:

```sh
doppler secrets get GCP_RAY_BENCH_PROJECT_ID --plain --token "$TOK"   # -> golden-490919
printf '%s' x | doppler secrets set CANARY --silent --token "$TOK"    # must be REFUSED
```

### 5. Set the GitHub Actions secret

`DOPPLER_TOKEN` is the ONLY GH-Actions secret these workflows need --
one, where the previous Infisical setup needed a client id and secret
pair. Future Doppler-backed secrets ride the same token.

Rotate by creating a replacement token, updating the GH secret, then
revoking the old one by slug:

```sh
doppler configs tokens --scope "D:\personal" -p showcase -c dev --json
doppler configs tokens revoke <slug> --scope "D:\personal" -p showcase -c dev
```

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

### Sweeping the #957 score-tuning knobs

The four score knobs only apply to the **distributed** engine, so a sweep needs
`distributed=1`; the legacy `backend=ray` path never enters the block-shuffle
score stage. The workflow refuses a dispatch that sets a knob with
`distributed=0` rather than running a bench that measures nothing.

```sh
gh workflow run bench-ray-cluster.yml     --repo benseverndev-oss/goldenmatch     -f rows=100000000     -f distributed=1     -f head_machine=n2-highmem-32     -f label=957-conc60-res02     -f score_concurrency=60     -f op_reservation=0.2
```

Leave a knob **empty** to keep the engine's own default: pinning every knob to
its default silently stops testing the default. Each run's effective values are
recorded in its artifact JSON under `score_knobs` and echoed in the step
summary, so a null result is attributable to a configuration rather than to a
sweep that never varied anything.

The knobs reach the run as **flags**, not environment variables: `ray submit`
gives the driver a fresh shell, so anything exported around the submit is lost.
(That was the whole reason #957's experiment could not be run for a release
cycle -- the engine also captured the values at import time, before the driver
could set them. Both halves are fixed; the flags are the transport.)

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
