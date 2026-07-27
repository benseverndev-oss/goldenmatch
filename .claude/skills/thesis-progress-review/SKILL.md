---
name: thesis-progress-review
description: Review GoldenMatch pull requests added since the previous run, relate them to the project thesis, assess project progress and drift, and advance a local checkpoint only after a complete review.
argument-hint: "[--lookback-days N]"
disable-model-invocation: true
---

# GoldenMatch thesis progress review

Review every GoldenMatch pull request created since this skill last completed. On the first run, use a rolling four-day lookback unless the user supplied another positive `--lookback-days` value.

The review is analytical, not a list of PR summaries. Explain what the work means for the project thesis, how confidence has changed, where evidence is still indirect, and what phase the project is entering.

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

## 2. Re-establish the governing thesis

Read the current versions of these sources before interpreting PRs:

1. `context-network/foundation/project-definition.md`
2. `context-network/architecture/one-product-two-engines.md`
3. `context-network/decisions/0047-one-product-two-engines-architecture.md`
4. `context-network/planning/north-star-roadmap.md`
5. `parity/thesis_conformance.yaml`
6. `README.md`, to detect public-status drift

Distill the thesis into explicit review dimensions:

- **North Star:** GoldenMatch should be the entity-resolution tool developers reach for by default.
- **Zero-config floor:** the first run should require no training or tuning and keep approaching expert-tuned quality.
- **Scale-invariant correctness:** speed, memory, or backend work must preserve the answer from laptop scale through 100M+.
- **Shared-capability conformance:** where a capability is shared, CLI/library/SQL/MCP/A2A/TypeScript/Python behavior must conform; intentional asymmetry must be explicit and justified.
- **Advanced, never black-box:** decisions, defaults, refusals, migrations, and model-backed behavior must remain inspectable and auditable.
- **One product, two engines:** bulk identity compute is Arrow-oriented and Rust-authoritative where practical; durable identity is a transaction-native control plane with deterministic, idempotent, auditable state transitions.
- **Explicit compute/control seam:** evidence and data cross through a versioned resolution-batch contract with bounded resource behavior.
- **Conformance v2:** an authoritative owner must be the default route; fixture parity must be supplemented by representative behavioral evidence; deferral premises must be revalidated.
- **Adoption-first progression:** the roadmap says the primary remaining deficit is external pull, discoverability, time-to-first-success, and outside contribution—not an endless supply of inward architecture projects.

Treat roadmap and conformance claims as repository assertions to verify against PR evidence, not as automatic proof.

## 3. Inspect every review item

For each new or resurfaced PR, inspect its current metadata and changed-file list:

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
- net-new capability from hardening, cleanup, release work, or documentation;
- individual chain PRs from the single outcome they collectively deliver;
- current behavior from stale status documents;
- fixture or synthetic evidence from representative and production evidence;
- architecture-program completion from adoption or production readiness.

For long PR sequences, review chronologically in coherent workstreams, but retain an auditable PR-number range or index for each workstream.

## 4. Orient the work to the thesis

For each workstream, answer:

1. Which North Star commitment, two-engine invariant, conformance-v2 test, or roadmap workstream does it advance?
2. Does it strengthen the shared product contract, clarify deliberate engine/surface divergence, or introduce drift?
3. What evidence was added: implementation, contract test, migration proof, representative benchmark, operational surface, release, or documentation?
4. What confidence should change, and why?
5. What remains unproven, newly risky, or adoption-irrelevant?

Assess project progression, not only feature completion. State whether the recent work moves the project through architecture definition, implementation, structural parity, behavioral validation, release hardening, pilot adoption, or production proof. Do not call the product complete merely because the architecture program or conformance ledger is complete.

## 5. Produce the report

Use this structure:

### Review boundary

State the prior checkpoint or first-run cutoff, the first and last reviewed PR, the number of new PRs and resurfaced updates, and the status mix. Mention dependency-only and superseded PRs separately.

### Project thesis

Give a compact thesis in your own words and identify its non-negotiable constraints.

### What changed, chronologically

Group PRs into a small number of dated workstreams. For each group, include PR numbers, thesis relationship, shipped/open status, and the meaningful outcome. Avoid one paragraph per trivial PR.

### Thesis scorecard

Assess at least these dimensions: zero-config/default routing, scale-invariant correctness, shared semantics, compute/control separation, explainability, migration/adoption ergonomics, operability, representative evidence, and public-document consistency. Use `strong`, `advancing`, `mixed`, `at risk`, or `not evidenced`, and add a confidence note.

### Project progression

Explain the phase transition produced by the reviewed work. Separate the repository's declared milestone state from your independent confidence in real-world readiness and external adoption.

### Risks, contradictions, and gaps

Call out stale public claims, duplicate abstractions, failed/open work, integration risk, missing adoption evidence, benchmark limitations, or claims not supported by changed code/tests.

### Next best work

Recommend only the few highest-leverage actions implied by the thesis and current phase. Prefer integration, compatibility, pilot, soak, rollback, release, getting-started, case-study, and real-user evidence when architecture gates are already satisfied; do not invent more architecture work without a demonstrated regression.

### Checkpoint

State the highest fully reviewed PR, any still-tracked open PRs, and whether the local checkpoint was advanced.

## 6. Advance the checkpoint only after completion

After the report is complete and every scan item has been accounted for, advance through the highest fully reviewed newly created PR. When the scan contains only tracked updates, pass the existing checkpoint number again so their snapshots are refreshed:

```bash
python3 .claude/skills/thesis-progress-review/scripts/pr_checkpoint.py checkpoint --through <PR_NUMBER>
```

Include the resulting PR number and tracked-open list in the report. If any review item was incomplete, do not run this command.

## Validate the helper after editing

```bash
python3 -m unittest discover \
  .claude/skills/thesis-progress-review/scripts \
  -p 'test_*.py' -v
```
