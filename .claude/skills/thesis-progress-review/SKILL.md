---
name: thesis-progress-review
description: Review GoldenMatch pull requests added since the previous run, relate them to the one-product-two-engines thesis, assess project progress and drift, and advance a local checkpoint only after a complete review.
argument-hint: "[--lookback-days N]"
disable-model-invocation: true
---

# GoldenMatch thesis progress review

Review every GoldenMatch pull request that has appeared since this skill last completed. On the first run, use a rolling four-day lookback unless the user supplied another positive `--lookback-days` value.

The review is analytical, not a list of PR summaries. Explain what the work means for the project thesis, how confidence has changed, where evidence is still indirect, and what phase the project is entering.

## Safety and checkpoint contract

The helper stores its checkpoint in Git metadata by default, using `git rev-parse --git-path goldenmatch-thesis-progress-review.json`. The state is local to the clone or worktree and must not be committed.

`scan` is read-only. Never call `checkpoint` until all selected PRs have been reviewed and the final report is complete. If GitHub access, a PR inspection, thesis-file reading, or report generation is incomplete, leave the checkpoint unchanged and say so. Never checkpoint past an omitted or failed review.

A PR that is open, draft, closed without merge, dependency-only, superseded, or reverted still belongs in the review boundary. Treat its contribution differently; do not silently exclude it or count it as shipped progress.

## 1. Find the review boundary

Run from the repository root:

```bash
python3 .claude/skills/thesis-progress-review/scripts/pr_checkpoint.py scan --lookback-days 4
```

Honor a user-supplied positive lookback value on first run. On later runs, the stored PR number is authoritative and the lookback value is ignored.

Read the JSON fields:

- `previous_checkpoint` identifies the last fully reviewed PR.
- `pull_requests` is chronological and contains every newly created PR after that checkpoint, or every PR in the first-run lookback.
- `checkpoint_candidate` is only a proposal. Do not write it yet.

When `new_pr_count` is zero, report that there are no newly created PRs and leave the checkpoint unchanged.

## 2. Re-establish the governing thesis

Read the current versions of these sources before interpreting PRs:

1. `context-network/foundation/project-definition.md`
2. `context-network/architecture/one-product-two-engines.md`
3. `context-network/decisions/0047-one-product-two-engines-architecture.md`
4. `context-network/planning/north-star-roadmap.md`
5. `parity/thesis_conformance.yaml`
6. `README.md`, to detect public-status drift

Distill the thesis into explicit review dimensions:

- one coherent product and stable user-facing semantics;
- files-first local activation and sensible defaults;
- PostgreSQL/pgvector as the deliberate scale engine;
- shared identity, verification, retrieval, ranking, configuration, and typed-ID kernels;
- explicit, safe migration between deployment modes;
- stable TypeScript, Python, CLI, and connector contracts;
- operability, bounded resources, and truthful failure behavior;
- representative-scale evidence rather than assertion;
- no third storage model, duplicate semantic kernel, or ambiguous default.

Treat roadmap and conformance claims as repository assertions to verify against PR evidence, not as automatic proof.

## 3. Inspect every new PR

For each PR number from the scan, inspect its current metadata and changed-file list:

```bash
gh pr view <PR_NUMBER> --repo <OWNER/REPO> \
  --json number,title,author,body,state,isDraft,createdAt,updatedAt,mergedAt,closedAt,baseRefName,headRefName,labels,files,additions,deletions,commits,statusCheckRollup,url
```

Read the diff when the PR is substantive, changes a thesis-critical surface, makes a performance or conformance claim, is open or failing, has an unclear body, or could duplicate/supersede another PR:

```bash
gh pr diff <PR_NUMBER> --repo <OWNER/REPO>
```

At minimum, account for every selected PR in a chronological workstream. Separate dependency or mechanical maintenance from product progress so volume does not inflate thesis progress. Distinguish:

- merged evidence from open intent;
- executable tests or measurements from prose claims;
- net-new capability from hardening, cleanup, or documentation;
- individual chain PRs from the single outcome they collectively deliver;
- current behavior from stale status documents;
- fixture or synthetic scale evidence from production evidence.

For long PR sequences, review chronologically in coherent workstreams, but retain an auditable PR-number range or index for each workstream.

## 4. Orient the work to the thesis

For each workstream, answer:

1. Which thesis clause, milestone, invariant, or regression gate does it advance?
2. Does it strengthen the shared product contract, clarify deliberate engine divergence, or introduce drift?
3. What evidence was added: implementation, contract test, migration proof, benchmark, operational surface, or documentation?
4. What confidence should change, and why?
5. What remains unproven or newly risky?

Assess project progression, not only feature completion. State whether the recent work moves the project through architecture definition, implementation, parity, validation, release hardening, pilot adoption, or production proof. Do not call the product complete merely because the architecture program or conformance ledger is complete.

## 5. Produce the report

Use this structure:

### Review boundary

State the prior checkpoint or first-run cutoff, the first and last reviewed PR, the number of PRs, and the status mix. Mention dependency-only PRs separately.

### Project thesis

Give a compact thesis in your own words and identify its non-negotiable constraints.

### What changed, chronologically

Group PRs into a small number of dated workstreams. For each group, include PR numbers, thesis relationship, shipped/open status, and the meaningful outcome. Avoid one paragraph per trivial PR.

### Thesis scorecard

Assess at least these dimensions: activation/defaults, shared semantics, two-engine separation, APIs/connector, migration, operability, scale evidence, and documentation consistency. Use `strong`, `advancing`, `mixed`, `at risk`, or `not evidenced`, and add a confidence note.

### Project progression

Explain the phase transition produced by the reviewed work. Separate the repository's declared milestone state from your independent confidence in real-world readiness.

### Risks, contradictions, and gaps

Call out stale status, duplicated abstractions, failed/open work, integration risk, missing adoption evidence, benchmark limitations, or claims not supported by changed code/tests.

### Next best work

Recommend only the few highest-leverage actions implied by the thesis and current phase. Prefer integration, compatibility, pilot, soak, rollback, release, and real-world evidence when architecture gates are already satisfied; do not invent more architecture work without a demonstrated regression.

### Checkpoint

State the highest fully reviewed PR and whether the local checkpoint was advanced.

## 6. Advance the checkpoint only after completion

After the report is complete and every scanned PR has been accounted for, advance through the highest fully reviewed PR:

```bash
python3 .claude/skills/thesis-progress-review/scripts/pr_checkpoint.py checkpoint --through <PR_NUMBER>
```

Include the resulting PR number in the report. If no PRs were new, or any part of the review was incomplete, do not run this command.
