# PR Merge Queue Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Serialize merges into `main` FIFO and fully unattended via GitHub's native merge queue, retiring the manual update-branch + auto-merge babysitter cascade.

**Architecture:** Add a `merge_group:` trigger to `ci.yml` so the single required check `ci-required` runs against each queued entry's temp branch, with `dorny/paths-filter` taught to diff the merge-group range. Then enable the merge queue on `main`'s ruleset and drop the strict "branch up to date" rule (the queue owns rebasing). No new long-lived infrastructure.

**Tech Stack:** GitHub Actions (`ci.yml`), GitHub repository rulesets / merge queue, `dorny/paths-filter@v4.0.1`, `gh` CLI.

**Spec:** `docs/superpowers/specs/2026-06-15-pr-merge-queue-design.md`

---

## Critical ordering constraint (read first)

The ruleset flip (Task 3) MUST happen AFTER the `ci.yml` change (Task 1) is merged
to `main`. If the queue is enabled before `main`'s `ci.yml` triggers on
`merge_group`, the first queued entry waits forever for a `ci-required` check that
never reports — a hard stall. Sequence is strictly: **Task 1 → merge to main →
Task 3 → Task 4**.

The Task 1 `ci.yml` change itself merges via the CURRENT (pre-queue) path: arm
`gh pr merge --auto --squash`, let it land.

## Clean-base requirement (read second)

The working tree on `feat/857-given-name-aliased-jw-ts` has an UNCOMMITTED
`ci.yml` edit. Do NOT build Task 1 on top of that. Branch the implementation off
freshly-fetched `origin/main` in a clean worktree (lesson:
`feedback_branch_off_fresh_origin_main`) so the `merge_group` change does not
tangle with #857's in-flight `ci.yml` work.

---

## File Structure

| File | Responsibility | Task |
|------|----------------|------|
| `.github/workflows/ci.yml` | Add `merge_group:` trigger; teach `changes`/paths-filter to diff the merge-group range | 1 |
| `main` branch ruleset (GitHub-side, no file) | Enable merge queue (squash / max 5 / 1-PR-5-min / 60-min timeout); disable strict up-to-date | 3 |
| `CLAUDE.md` (repo root) | Replace strict-up-to-date + babysitter guidance with merge-queue guidance | 5 |
| `~/.claude/projects/.../memory/reference_branch_protection_strict_up_to_date.md` | Update to describe the queue | 5 |
| `~/.claude/projects/.../memory/feedback_dont_poll_ci_arm_automerge.md` | Update: enqueue verb is the queue, not a laptop loop | 5 |

---

## Task 1: Add `merge_group` trigger + merge-group-aware path filtering to `ci.yml`

**Files:**
- Modify: `.github/workflows/ci.yml` (the `on:` block, lines 3-6; the `dorny/paths-filter` step, lines 51-56)

- [ ] **Step 1: Branch off fresh origin/main in a clean worktree**

```bash
gh auth switch --user benzsevern   # benseverndev-oss uses the personal account
git fetch origin main
git worktree add ../gm-merge-queue -b feat/pr-merge-queue-impl origin/main
cd ../gm-merge-queue
```

Verify the base `ci.yml` is clean (no #857 edits):

Run: `git status --porcelain -- .github/workflows/ci.yml`
Expected: empty output (file matches origin/main)

- [ ] **Step 2: Add the `merge_group:` trigger**

In `.github/workflows/ci.yml`, change the `on:` block from:

```yaml
on:
  push:
    branches: [main]
  pull_request:
```

to:

```yaml
on:
  push:
    branches: [main]
  pull_request:
  # Merge queue: GitHub fires `merge_group` for each queued entry against a temp
  # branch (main + earlier-queued PRs + this PR). `ci-required` MUST run here or
  # the queue stalls waiting for a check that never reports.
  merge_group:
```

- [ ] **Step 3: Teach `dorny/paths-filter` to diff the merge-group range**

In the `changes` job, change the paths-filter step (currently lines 51-56) from:

```yaml
      - uses: dorny/paths-filter@fbd0ab8f3e69293af611ebaee6363fc25e6d187d  # v4.0.1
        id: filter
        with:
          # `filters` runs against the diff between the PR head and base
          # (or push head and the previous push for branch builds). Per-path
          # globs are unanchored — `'foo/**'` matches at any depth.
          filters: |
```

to:

```yaml
      - uses: dorny/paths-filter@fbd0ab8f3e69293af611ebaee6363fc25e6d187d  # v4.0.1
        id: filter
        with:
          # On `merge_group` the event carries no PR base/head, so diff the
          # queue entry's range explicitly: base_sha = main tip + earlier-queued
          # entries, head_sha = this entry. The empty-string fallback on every
          # other event lets paths-filter auto-detect (PR base/head, or push
          # before/after) exactly as before. SAFETY: if this range is wrong and
          # reports "nothing changed", code lanes would SKIP and `ci-required`
          # would pass UNTESTED code — Task 4 validates this does not happen.
          base: ${{ github.event_name == 'merge_group' && github.event.merge_group.base_sha || '' }}
          ref: ${{ github.event_name == 'merge_group' && github.event.merge_group.head_sha || '' }}
          # `filters` runs against the diff between the PR head and base
          # (or push head and the previous push for branch builds). Per-path
          # globs are unanchored — `'foo/**'` matches at any depth.
          filters: |
```

(Leave the `filters:` content and every other job untouched.)

- [ ] **Step 4: Lint the workflow**

Run: `actionlint .github/workflows/ci.yml` (or, if actionlint is unavailable, `python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/ci.yml')); print('yaml ok')"`)
Expected: no errors (`yaml ok`).

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: run ci-required on merge_group for native merge queue

Adds a merge_group trigger and makes dorny/paths-filter diff the
merge-group range so queued entries gate on ci-required exactly like PRs.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

- [ ] **Step 6: Bring the design docs onto this branch (single clean PR)**

Cherry-pick the spec+plan commit from `feat/pr-merge-queue` so the docs land with the change:

```bash
git cherry-pick feat/pr-merge-queue   # the spec+plan docs commit (e3608093)
```

If the cherry-pick reports nothing or conflicts on unrelated files, instead copy just the two doc files (`docs/superpowers/specs/2026-06-15-pr-merge-queue-design.md`, this plan) into the worktree and commit them. The goal: this branch has exactly the `ci.yml` change + the two design docs, nothing from #857.

- [ ] **Step 7: Open the PR and merge via the CURRENT path**

```bash
GH_TOKEN=$(gh auth token --user benzsevern) gh pr create \
  --base main --head feat/pr-merge-queue-impl \
  --title "ci: merge_group trigger for native merge queue" \
  --body "Implements docs/superpowers/specs/2026-06-15-pr-merge-queue-design.md. Wires ci-required to run on merge_group ahead of enabling the queue. Merges via the current (pre-queue) path."
gh pr merge <N> --auto --squash --delete-branch
```

Wait for it to land on `main`. Switch auth back: `gh auth switch --user benzsevern-mjh` (standing rule `feedback_github_auth_switch`).
Expected: PR merged; `main` now triggers CI on `merge_group`.

---

## Task 2: Capture the current `main` ruleset (so the flip is precise, not assumed)

**Files:** none (read-only inspection)

- [ ] **Step 1: Read the active ruleset and its required-checks/strict policy**

```bash
gh auth switch --user benzsevern
gh api repos/benseverndev-oss/goldenmatch/rulesets --jq '.[] | {id, name, target, enforcement}'
```

Identify the ruleset that targets the `main` branch; note its `id`.

- [ ] **Step 2: Dump that ruleset's rules**

```bash
gh api repos/benseverndev-oss/goldenmatch/rulesets/<RULESET_ID> > /tmp/ruleset-before.json
cat /tmp/ruleset-before.json | jq '.rules[] | {type, parameters}'
```

Expected: a `required_status_checks` rule listing `ci-required` and (likely)
`strict_required_status_checks_policy: true`. Confirm there is NO existing
`merge_queue` rule.

- [ ] **Step 3: Record findings**

Note in the PR/working notes: the ruleset id, the exact `required_status_checks`
rule shape, and whether strict policy is on. This is the source of truth for the
Task 3 edit and the Task 3 rollback. `/tmp/ruleset-before.json` IS the rollback
artifact — keep it.

---

## Task 3: Enable the merge queue on `main` + disable strict up-to-date

**Files:** none (GitHub-side ruleset change)

> **AUTHORIZATION GATE:** This edits branch protection on a PUBLIC repo's `main`.
> It is outward-facing and admin-only. Present the exact change to Ben and get an
> explicit go-ahead before applying. Do NOT apply unprompted.

- [ ] **Step 1: Confirm Task 1 is on `main`**

Run: `gh api repos/benseverndev-oss/goldenmatch/contents/.github/workflows/ci.yml --jq '.content' | base64 -d | grep -n "merge_group"`
Expected: shows the `merge_group:` trigger line — proves the prerequisite is live on `main`.

- [ ] **Step 2: Add the `merge_queue` rule and turn off strict policy**

Preferred: GitHub web UI (Settings → Rules → Rulesets → the `main` ruleset):
- Enable **"Require merge queue"** with parameters:
  - Merge method: **Squash**
  - Build concurrency / max PRs to build: **5**
  - Minimum PRs to merge: **1**, wait time: **5 min**
  - Status check timeout: **60 min**
  - Only merge non-failing entries: **on** (GitHub default — bounce a failing entry, re-test those behind it)
- In **"Require status checks to pass"**, UNCHECK **"Require branches to be up to date before merging"** (the queue replaces strict up-to-date). Keep `ci-required` as the required check.

`gh api` alternative (only if scripting): PATCH the ruleset adding a `merge_queue`
rule and setting `strict_required_status_checks_policy: false` on the
`required_status_checks` rule. Build the payload from `/tmp/ruleset-before.json`
so nothing else changes. Verify against the UI shape before sending.

- [ ] **Step 3: Verify the queue is active and strict policy is off**

```bash
gh api repos/benseverndev-oss/goldenmatch/rulesets/<RULESET_ID> --jq '.rules[] | select(.type=="merge_queue" or .type=="required_status_checks") | {type, parameters}'
```
Expected: a `merge_queue` rule present; `required_status_checks` shows
`strict_required_status_checks_policy: false` and `ci-required` still required.

- [ ] **Step 4: Switch auth back**

Run: `gh auth switch --user benzsevern-mjh`
Expected: active account is the work account (standing rule `feedback_github_auth_switch`).

---

## Task 4: Validation harness (the real acceptance gate)

Proves three things on the LIVE queue: (a) FIFO landing, (b) a code-touching entry
actually RUNS its lane in the queue and merges (not a trivial-skip false pass),
(c) a red entry is bounced WITHOUT blocking the entries behind it.

**Files (throwaway, reverted at the end):**
- A scratch doc line for the doc-only PR (e.g. append a line to `docs/SCRATCH-queue-test.md`)
- A no-op comment in a code-lane file for the code PR (e.g. a `# queue-test` comment in `packages/python/goldenmatch/goldenmatch/core/scorer.py`)
- A deliberately-failing change for the red PR (e.g. a temp test that asserts `False`)

- [ ] **Step 1: Open PR A — doc-only (fast-pass expected)**

Branch off `main`, append a line to a scratch doc, push, open PR A. This entry
should fast-pass in the queue (code lanes skip; `ci-required` green on skips).

- [ ] **Step 2: Open PR B — code-lane touch (must RUN, not skip)**

Branch off `main`, add a `# queue-test` comment to
`packages/python/goldenmatch/goldenmatch/core/scorer.py` (a `python` +
`benchmark_runner` lane file), push, open PR B. This is the SAFETY test: in the
queue the python lane MUST run.

- [ ] **Step 3: Open PR C — deliberately red (must be bounced)**

Branch off `main`, add a temp test `tests/test_queue_bounce.py` with
`def test_bounce(): assert False`, push, open PR C. In the queue this entry's
`ci-required` MUST go red and the entry MUST be removed without blocking others.

- [ ] **Step 4: Enqueue all three (FIFO: A, then B, then C, then a 4th safe PR D behind C)**

Open a 4th trivial safe PR D (another scratch-doc line). Enqueue in order
A, B, C, D via `gh pr merge <N> --squash` ("Merge when ready"). D behind C proves
a bounced entry doesn't wedge the queue.

- [ ] **Step 5: Observe the queue**

Run: `gh api repos/benseverndev-oss/goldenmatch/merge-queue/main 2>/dev/null || echo "inspect via UI: Pull requests → Merge queue"`
Watch the Actions tab for `merge_group` CI runs. Confirm:
- PR B's `merge_group` run shows the **python lane RAN** (not skipped) — open the run, confirm the `python (goldenmatch)` job executed. THIS IS THE CRITICAL CHECK. If python skipped, STOP — paths-filter mis-detected the range (see Troubleshooting) and the queue is unsafe.
- PR A fast-passes (code lanes skipped, `ci-required` green).
- A and B merge to `main` in FIFO order.
- PR C is bounced (removed from queue, not merged); PR D still merges.

Expected: A, B, D merged in order; C bounced; B's lane ran.

- [ ] **Step 6: Clean up**

Close PR C (never merged). Open a tiny follow-up PR (through the queue) that
reverts the scratch-doc lines and the `# queue-test` comment, and deletes
`docs/SCRATCH-queue-test.md`. Confirm `main` is back to a clean state.

**Troubleshooting (if Step 5 shows the python lane SKIPPED on PR B's merge_group run):**
paths-filter could not reach `base_sha`. Add `with: { fetch-depth: 0 }` to the
`actions/checkout` step in the `changes` job (line ~50) so the base commit is
present, re-merge `ci.yml`, and re-run Step 2/5. Fallback if still flaky: on
`merge_group`, bypass filtering and emit all lanes true (run the full matrix) —
safe but slower; document the cost.

---

## Task 5: Update docs + memory

**Files:**
- Modify: `CLAUDE.md` (repo root) — the merge/CI guidance sections
- Modify: `C:\Users\bsevern\.claude\projects\D--show-case-goldenmatch\memory\reference_branch_protection_strict_up_to_date.md`
- Modify: `C:\Users\bsevern\.claude\projects\D--show-case-goldenmatch\memory\feedback_dont_poll_ci_arm_automerge.md`

- [ ] **Step 1: Update root `CLAUDE.md`**

Replace the strict-up-to-date babysitter guidance with: "`main` uses a native
merge queue. Enqueue with `gh pr merge --auto --squash` (or 'Merge when ready')
and walk away — the queue rebases each entry onto the new `main`, re-runs
`ci-required` on `merge_group`, and merges FIFO. No manual update-branch cascade.
`ci-required` is the only gate; UNSTABLE non-required lanes don't stall the
queue." Keep the existing `gh pr merge` 502-retry note.

- [ ] **Step 2: Update the two memory notes**

- `reference_branch_protection_strict_up_to_date`: note strict up-to-date is
  REPLACED by the merge queue as of 2026-06-15; the babysitter loop is retired.
- `feedback_dont_poll_ci_arm_automerge`: reinforce — the queue is the unattended
  mechanism; still never poll CI from the laptop. Cross-link `[[...]]` both notes.

- [ ] **Step 3: Run the rollout-docs-sweep**

Invoke the `rollout-docs-sweep` skill (standing rule `feedback_rollout_docs_sweep`)
to catch any other doc surface (CONTRIBUTING.md, context-network, docs-site) that
references the old manual merge cascade.

- [ ] **Step 4: Commit + land via the (now live) queue**

```bash
git add CLAUDE.md
git commit -m "docs: merge queue replaces manual update-branch cascade"
gh pr create --base main --title "docs: merge queue rollout" --body "..."
gh pr merge <N> --auto --squash --delete-branch   # dogfoods the queue
```

(Memory files live outside the repo — write them directly, no PR.)

---

## Done criteria

- `main`'s `ci.yml` triggers `ci-required` on `merge_group` (Task 1, verified live).
- Merge queue active on `main`; strict up-to-date off (Task 3, verified via API).
- Validation harness passed: FIFO landings, code-lane RAN on a queued entry, red
  entry bounced without wedging the queue (Task 4).
- Docs + memory describe the queue, not the manual cascade (Task 5).
- Rollback artifact `/tmp/ruleset-before.json` retained; rollback = re-enable
  strict up-to-date + remove the `merge_queue` rule (~60s, no code to unwind).
