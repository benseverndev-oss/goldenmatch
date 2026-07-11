#!/usr/bin/env bash
# qis_alert.sh TIER RUN_ID
# Files (or updates) a single dedicated, assigned "quality regression" GitHub
# issue when the quality-at-scale gate fails on a SCHEDULED run. Idempotent: one
# open issue per tier is reused (commented + reopened), never re-spammed. This is
# the loud channel on top of GitHub's automatic scheduled-failure email and the
# main-health tracking issue. Requires GH_TOKEN with issues:write.
set -euo pipefail

TIER="${1:?usage: qis_alert.sh TIER RUN_ID}"
RUN_ID="${2:?usage: qis_alert.sh TIER RUN_ID}"
REPO="${GITHUB_REPOSITORY:-benseverndev-oss/goldenmatch}"
ASSIGNEE="benzsevern"
LABEL="quality-regression"
MARKER="<!-- qis-gate-alert:${TIER} -->"
RUN_URL="https://github.com/${REPO}/actions/runs/${RUN_ID}"
TITLE="Zero-config quality regression at scale (${TIER} tier)"

# Ensure the label exists (idempotent).
gh label create "$LABEL" --repo "$REPO" --color B60205 \
  --description "Zero-config ER quality regression caught at scale" --force >/dev/null 2>&1 || true

BODY="$(cat <<EOF
${MARKER}

**The zero-config quality-at-scale gate failed on the \`${TIER}\` tier.** A
scale-dependent entity-resolution quality regression is present on \`main\` — the
exact class of bug this gate exists to catch within a day instead of two months.

- Failing run: ${RUN_URL} (see the job summary for the per-rung F1 table + the
  specific violation: scale-invariance / baseline-delta / absolute-floor).
- Reproduce locally: \`python scripts/qis_gate.py --tier ${TIER} --mode check\`.
- If this is an INTENTIONAL quality change (new expected baseline), re-bless:
  \`gh workflow run bench-quality-scale.yml -f tier=${TIER} -f mode=bless\`, then
  commit the regenerated \`scripts/baselines/qis_scorecard.json\`.

This issue auto-updates on each failing scheduled run and should be closed once
the gate is green again.
EOF
)"

# Find an existing OPEN issue carrying this tier's marker.
EXISTING="$(gh issue list --repo "$REPO" --state open --label "$LABEL" --json number,body \
  --jq "map(select(.body | contains(\"${MARKER}\"))) | .[0].number // empty" 2>/dev/null || true)"

if [ -n "$EXISTING" ]; then
  gh issue comment "$EXISTING" --repo "$REPO" \
    --body "Still failing — new run: ${RUN_URL}"
  echo "updated existing quality-regression issue #${EXISTING}"
else
  gh issue create --repo "$REPO" --title "$TITLE" --body "$BODY" \
    --label "$LABEL" --assignee "$ASSIGNEE"
  echo "opened quality-regression issue for ${TIER} tier"
fi
