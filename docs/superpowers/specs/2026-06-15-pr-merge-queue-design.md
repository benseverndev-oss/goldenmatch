# PR Merge Queue (FIFO serialized merges to `main`)

**Date:** 2026-06-15
**Status:** Approved design, pre-implementation
**Author:** Ben Severn (with Claude)

## Problem

`main` moves fast — 40+ commits/session via parallel worktrees, with several PRs
green and ready at once. Branch protection requires the aggregated check
`ci-required` plus a strict "branch up to date with base" rule. With strict
up-to-date enabled, only one PR can merge at a time, and every PR must be
rebased/updated onto the new `main` before it is allowed to merge. Today that
serialization is done by hand: arm `gh pr merge --auto`, then babysit an
update-branch cascade across the in-flight PRs. That manual loop is the thing
this design removes.

This is documented pain in repo memory:
- `reference_branch_protection_strict_up_to_date` — main requires `ci-required`
  + strict up-to-date head; multi-PR merges cascade into an arm-auto-merge +
  update-branch babysitter loop.
- `feedback_dont_poll_ci_arm_automerge` — after pushing a PR, arm auto-merge and
  STOP; never sit in a CI poll loop from the laptop.

## Goal

Serialize merges into `main` FIFO, fully unattended: each queued PR is
auto-rebased onto the new `main` as the one ahead of it lands, CI re-runs, and it
merges when green — with no laptop, no babysitter, and no poll loop. Retire the
manual update-branch cascade.

## Non-goals

- Custom merge ordering / priority lanes (queue is FIFO; revisit only if needed).
- Skipping or re-interpreting `continue-on-error` / UNSTABLE lanes (the queue
  inherits the existing `ci-required` gate unchanged).
- Replacing the merge verb humans/agents already use — `gh pr merge --auto
  --squash` stays the enqueue command; only its server-side effect changes.

## Approach

Use **GitHub's native merge queue** (free on this public repo). It is purpose-built
for exactly this: FIFO serialization, automatic rebase onto a temp branch
(`main` + earlier-queued PRs + this one), CI re-run via the `merge_group` event,
merge-on-green, pop-the-next, and re-test of entries behind a bounced one. It
also tests up to N entries speculatively in parallel, so it is faster than a
strict-serial cascade.

Rejected alternatives:
- **Custom GitHub Actions FIFO workflow** — full control, but reimplements merge
  queue; concurrency/event-ordering races are hard and easy to wedge; ongoing
  maintenance. Kept as the documented fallback if native merge queue ever can't
  express a needed gate.
- **Local `gh` babysitter CLI** — to serialize the cascade, something must watch
  `main`; locally that is a laptop poll loop, which contradicts
  `feedback_dont_poll_ci_arm_automerge`. Dies when the lid closes. Weakest fit.

## What changes

Three changes; nothing new to maintain long-term:

1. **`ci.yml` gains a `merge_group:` trigger** so the single required check
   `ci-required` runs against each queued entry's temp branch.
2. **`main`'s branch ruleset enables the merge queue** and drops "require branches
   up to date" (the queue now owns rebasing).
3. **The enqueue verb stays `gh pr merge --auto --squash`** / the "Merge when
   ready" button — same command, now puts the PR in line instead of merging
   immediately.

## The load-bearing risk

The queue gates on exactly one check: `ci-required` (defined at
`.github/workflows/ci.yml:1631` — an `if: always()` job that inspects upstream
`needs` results, passing on `success`/`skipped`, failing on
`failure`/`cancelled`).

- **Why the repo absorbs the UNSTABLE noise for free:** non-required
  matrix-instance checks and `continue-on-error` lanes that flip red are *not*
  the required check, so they do not stall the queue. The queue inherits the same
  gate as the current merge button — the noise already (correctly) ignored stays
  ignored.
- **The actual failure mode to prevent:** if `ci-required` does not *run and
  report* on a `merge_group` event, the queue waits forever for a check that
  never arrives. So the implementation must make `ci-required` fire on
  `merge_group`. Concretely, the `changes` path-filter job (which today diffs the
  PR base/head via `dorny/paths-filter`) must compute its diff from the
  merge-group range (`github.event.merge_group.base_sha..head_sha`) so that
  doc-only entries still fast-pass and code entries still run the right lanes.
  Advisory PR workflows (e.g. `claude-code-review.yml`) stay on `pull_request`
  only — they are not required, so they should not run on `merge_group` and slow
  the queue.

## Components (isolated, testable units)

### 1. CI merge_group enablement (`.github/workflows/ci.yml`)
- **Does:** adds the `merge_group:` trigger; teaches the `changes` job to diff the
  merge-group range so path-filtering still works on queued entries.
- **Interface / proof:** a queued entry produces a green (or correctly red)
  `ci-required` check.
- **Depends on:** existing `ci-required` aggregation job (unchanged logic).

### 2. Branch ruleset change (`main`)
- **Does:** enables the merge queue with the settings below; removes the strict
  "require branches up to date" rule.
- **Interface / proof:** `gh api repos/benseverndev-oss/goldenmatch/rulesets`
  (or the branch-protection endpoint) shows the merge queue active on `main`.
- **Depends on:** repo admin (auth switch to `benzsevern`).

### 3. Workflow + docs
- **Does:** confirms `gh pr merge --auto --squash` as the enqueue verb; updates
  `CLAUDE.md` (the strict-up-to-date / babysitter guidance becomes "use the merge
  queue") and the two stale memory notes
  (`reference_branch_protection_strict_up_to_date`,
  `feedback_dont_poll_ci_arm_automerge`).
- **Interface / proof:** docs/memory describe the queue, not the manual cascade.

### 4. Validation harness
- **Does:** the rollout acceptance test — 3 trivial PRs land FIFO unattended; 1
  deliberately-red PR is bounced from the queue without blocking the entries
  behind it.
- **Interface / proof:** observed FIFO landings + clean bounce.

## Queue settings (defaults; adjustable)

- **Merge method:** squash (matches the existing SOP and `--delete-branch`).
- **Build concurrency / max PRs per group:** 5 (parallel-worktree throughput;
  speculative parallel testing).
- **Minimum to merge / wait time:** 1 PR / 5 min, so a lone ready PR does not sit
  waiting to fill a batch.
- **Status-check timeout:** ~60 min, so slower CI lanes do not time the queue out.
- **On failure:** GitHub default — bounce the failing entry, re-test the entries
  behind it.

## Admin, rollback, testing

- **Admin:** the ruleset edit needs admin → switch to `benzsevern`, make the
  change, then **switch back to `benzsevern-mjh`** (standing rule
  `feedback_github_auth_switch`).
- **Rollback:** turn the queue off and restore strict up-to-date — a ~60-second
  ruleset revert, no code to unwind. The `merge_group` trigger is harmless to
  leave in `ci.yml`.
- **Testing:** the validation harness (3 green PRs land FIFO + 1 red PR bounced
  cleanly) is the acceptance gate.

## Open questions / follow-ups

- Exact `dorny/paths-filter` configuration for the `merge_group` event (resolved
  in the implementation plan with the precise YAML).
- Wait time ships at the authoritative 5 min (per Queue settings); revisit only
  as a tuning follow-up once real queue depth is observed — not a design change.
