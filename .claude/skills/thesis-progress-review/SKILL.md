---
name: thesis-progress-review
description: Review GoldenMatch pull requests added since the previous run, assess project progress and thesis alignment, report only material deltas, and advance a local checkpoint after a complete review.
argument-hint: "[--lookback-days N]"
disable-model-invocation: true
---

# GoldenMatch thesis progress review

Review every GoldenMatch pull request created since this skill last completed. On the first run, use a rolling four-day lookback unless the user supplied another positive `--lookback-days` value.

The review is analytical, not a list of PR summaries. Report what changed since the previous run, why it matters, how confidence changed, and what phase the project is entering. Use the governing thesis as the evaluation rubric, not as a repeated preamble.

## Output and side-effect contract

Running this skill is read-only against GitHub and the repository. Apart from the local checkpoint file, do not create or update branches, commits, pull requests, issues, comments, releases, or repository files. Do not publish the generated report into the repository. Repository changes require a separate explicit user request.

Do not include a standalone recap of the governing thesis on routine runs. Read the thesis sources silently and mention a thesis clause only when a reviewed change advances it, contradicts it, exposes drift, or amends it. Stable background that has not changed since the prior run should not be repeated.

Keep the report delta-focused. When the reviewed work is routine, a short report is preferred. Expand only for material regressions, architectural changes, production-readiness evidence, or conflicts between repository claims and implementation.

## Safety and checkpoint contract

The helper stores its checkpoint in Git metadata by default, using `git rev-parse --git-path goldenmatch-thesis-progress-review.json`. The state is local to the clone or worktree and must not be committed.

`scan` is read-only. Never call `checkpoint` until all selected PRs and resurfaced open-PR updates have been reviewed and the final report is complete. If GitHub access, a PR inspection, thesis-file reading, or report generation is incomplete, leave the checkpoint unchanged and say so. Never checkpoint past an omitted or failed review.

A PR that is open, draft, closed without merge, dependency-only, superseded, or reverted still belongs in the review boundary. Treat its contribution differently; do not silently exclude it or count it as shipped progress.

Open PRs are tracked by head SHA and state. A later run resurfaces a previously reviewed open PR when its code, draft state, base, title, or terminal state changes, so an eventual merge or rewrite is not lost behind the numeric checkpoint.

## 1. Find the review boundary

Run from the repository root:

```bash
python3 .claude/skills/thesis-progress-review/scripts/pr_checkpoint.py scan --lookback-days 4
```

Honor a user-supplied positive lookback value on first run. On later runs, the stored PR number is authoritative and the lookback value is ignored.

Read the JSON fields:

- `previous_checkpoint` identifies the last fully reviewed newly created PR.
- `pull_requests` is chronological and contains every newly created PR after that checkpoint, or every PR in the first-run lookback.
- `tracked_updates` contains materially changed PRs that were open when a prior review completed.
- `checkpoint_candidate` is only a proposal. Do not write it yet.

When `review_item_count` is zero, report that there are no new or materially changed tracked PRs and leave the checkpoint unchanged.

## 2. Load the review criteria silently

Read the current versions of these sources before interpreting PRs:

1. `context-network/foundation/project-definition.md`
2. `context-network/architecture/one-product-two-engines.md`
3. `context-network/decisions/0047-one-product-two-engines-architecture.md`
4. `context-network/planning/north-star-roadmap.md`
5. `parity/thesis_conformance.yaml`
6. `README.md`, to detect public-status drift

Use these review dimensions internally:

- the zero-config first-run floor and progress toward expert-tuned quality;
- scale-invariant correctness;
- conformant shared capabilities and explicit intentional asymmetry;
- inspectable defaults, refusals, migrations, and model-backed behavior;
- distinct compute and control engines joined by an explicit versioned seam;
- default routing, representative behavioral evidence, and deferral re-validation;
- adoption-first progression rather than unbounded inward architecture work.

Treat roadmap and conformance claims as repository assertions to verify against PR evidence, not as automatic proof. Do not reproduce this list in the report unless a reviewed PR changes the governing criteria themselves.

## 3. Inspect every review item

For each new or resurfaced PR, inspect its current metadata and changed-file list:

```bash
gh pr view <PR_NUMBER> --repo <OWNER/REPO> \
  --json number,title,author,body,state,isDraft,createdAt,updatedAt,mergedAt,closedAt,baseRefName,headRefName,labels,files,additions,deletions,commits,statusCheckRollup,url
```

Read the diff when the PR is substantive, changes a thesis-critical surface, makes a performance or conformance claim, is open or failing, has an unclear body, or could duplicate or supersede another PR:

```bash
gh pr diff <PR_NUMBER> --repo <OWNER/REPO>
```

At minimum, account for every selected PR in a chronological workstream. Separate dependency or mechanical maintenance from product progress so volume does not inflate thesis progress. Distinguish:

- merged evidence from open intent;
- executable tests or measurements from prose claims;
- net-new capability from hardening, cleanup, release work, or documentation;
- individual chain PRs from the single outcome they collectively deliver;
- current behavior from stale status documents;
- fixture or synthetic evidence from representative and production evidence;
- architecture-program completion from adoption or production readiness.

For long PR sequences, review chronologically in coherent workstreams, but retain an auditable PR-number range or index for each workstream.

## 4. Orient the work

For each workstream, determine:

1. Which product commitment, two-engine invariant, conformance-v2 test, or roadmap workstream it affects.
2. Whether it strengthens the shared product contract, clarifies deliberate engine or surface divergence, or introduces drift.
3. What evidence was added: implementation, contract test, migration proof, representative benchmark, operational surface, release, or documentation.
4. What confidence should change, and why.
5. What remains unproven, newly risky, or adoption-irrelevant.

Assess project progression, not only feature completion. State whether recent work moves the project through architecture definition, implementation, structural parity, behavioral validation, release hardening, pilot adoption, or production proof. Do not call the product complete merely because the architecture program or conformance ledger is complete.

## 5. Produce the report

Start with a one- or two-sentence verdict describing the most important change in project confidence or phase.

Use this default structure:

### Review boundary

State the prior checkpoint or first-run cutoff, the first and last reviewed PR, the number of new PRs and resurfaced updates, and the status mix. Mention dependency-only and superseded PRs separately. Keep this compact.

### What changed and why it matters

Group PRs into a small number of chronological workstreams. Include PR numbers, shipped or open status, the meaningful outcome, the relevant alignment or drift, and the evidence quality. Avoid one paragraph per trivial PR.

### Project progression

Explain only the phase transition or confidence change produced by this run. Separate the repository's declared milestone state from independent confidence in real-world readiness and external adoption.

### Risks and gaps

Call out stale public claims, duplicate abstractions, failed or open work, integration risk, missing adoption evidence, benchmark limitations, or claims not supported by changed code or tests.

### Next best work

Recommend only the few highest-leverage actions implied by the current phase. Prefer integration, compatibility, pilot, soak, rollback, release, getting-started, case-study, and real-user evidence when architecture gates are already satisfied.

### Checkpoint

State the highest fully reviewed PR, any still-tracked open PRs, and whether the local checkpoint was advanced.

Do not include a `Project thesis` section. Do not restate stable governing principles. An optional compact scorecard may be added only when several review dimensions materially changed during this run.

## 6. Advance the checkpoint only after completion

After the report is complete and every scan item has been accounted for, advance through the highest fully reviewed newly created PR. When the scan contains only tracked updates, pass the existing checkpoint number again so their snapshots are refreshed. On the first run, pass the same lookback value that `scan` used:

```bash
python3 .claude/skills/thesis-progress-review/scripts/pr_checkpoint.py \
  checkpoint --through <PR_NUMBER> --lookback-days <N>
```

Include the resulting PR number and tracked-open list in the report. If any review item was incomplete, do not run this command.

## Validate the helper after editing

```bash
python3 -m unittest discover \
  .claude/skills/thesis-progress-review/scripts \
  -p 'test_*.py' -v
```
