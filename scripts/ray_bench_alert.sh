#!/usr/bin/env bash
# ray_bench_alert.sh RUN_ID FAILED_STEP
# Files (or updates) a single dedicated GitHub issue when a bench-ray-cluster run
# fails. Idempotent: one open issue is reused (commented + reopened), never
# re-spammed. Mirrors scripts/qis_alert.sh.
#
# WHY a dispatch-only workflow needs this. bench-ray-cluster has no schedule, so
# it gets none of GitHub's automatic scheduled-failure email and never reaches
# main-health. A failure was therefore visible only to whoever happened to reread
# the Actions tab -- run 33174606401 sat failed and unnoticed. The failure is
# also expensive to leave unread: the head node bills for the ~15 min before the
# worker check gives up, and the most common causes (zone capacity, quota) are
# transient and zone-specific, so the fix is usually a re-dispatch elsewhere
# rather than a code change.
set -euo pipefail

RUN_ID="${1:?usage: ray_bench_alert.sh RUN_ID FAILED_STEP}"
FAILED_STEP="${2:-unknown}"
REPO="${GITHUB_REPOSITORY:-benseverndev-oss/goldenmatch}"
ASSIGNEE="benzsevern"
LABEL="bench-infra"
MARKER="<!-- ray-bench-alert -->"
RUN_URL="https://github.com/${REPO}/actions/runs/${RUN_ID}"
TITLE="bench-ray-cluster run failed"

gh label create "$LABEL" --repo "$REPO" --color 1D76DB \
  --description "Bench harness / cluster provisioning failures" --force >/dev/null 2>&1 || true

BODY="$(cat <<EOF
${MARKER}

**A \`bench-ray-cluster\` run failed.** This workflow is dispatch-only, so nothing
else surfaces the failure.

- Failing run: ${RUN_URL}
- Failed step: \`${FAILED_STEP}\`
- Zone: \`${ZONE:-unset}\` / head \`${HEAD_MACHINE:-unset}\` / workers \`${MAX_WORKERS:-unset}\`

**First thing to check: was it capacity, or was it us?**

\`Verify workers joined\` failing with \`0/N workers\` is almost always GCE
capacity in that zone, not a code change -- capacity is per-zone and independent
of quota. Re-dispatch in another zone before debugging anything. Every green run
to date used \`us-east1-b\`; \`us-central1-a\` has now produced \`0/3\` on
on-demand \`n2-standard-16\` (run 33174606401), and \`.ray/cluster-gce.yaml\`
already records its spot capacity there as intermittent.

A failure at \`Submit bench\` or later IS worth reading as a real signal.

Teardown runs under \`if: always()\`, so a failed run should not leak instances --
confirm \`Ray down\` and \`Defensive cleanup\` both succeeded on the run above.

This issue auto-updates on each failing run; close it once a run is green.
EOF
)"

EXISTING="$(gh issue list --repo "$REPO" --state open --label "$LABEL" --json number,body \
  --jq "map(select(.body | contains(\"${MARKER}\"))) | .[0].number // empty" 2>/dev/null || true)"

if [ -n "$EXISTING" ]; then
  gh issue comment "$EXISTING" --repo "$REPO" --body "$BODY"
  echo "updated existing alert issue #${EXISTING}"
else
  gh issue create --repo "$REPO" --title "$TITLE" --body "$BODY" \
    --label "$LABEL" --assignee "$ASSIGNEE"
  echo "filed a new alert issue"
fi
