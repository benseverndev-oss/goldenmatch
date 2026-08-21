# GitHub and tooling gotchas

Small, hard-won specifics about `gh`, the CI UI, pnpm-vs-npm flags, stacked PRs and Mermaid. Each cost someone an afternoon once.

> Extracted from the root `CLAUDE.md` so it is read when relevant rather than
> loaded into every session. Linked from the map at the bottom of that file.

## A workflow CANNOT `git push` to `main` -- use `scripts/open_data_pr.sh`
Since the merge queue landed (2026-06-15) `protect-main` rejects every direct push with `GH013: ... Changes must be made through a pull request / the merge queue`, including one from `github-actions[bot]` with `contents: write`. `git log --since=2026-06-15` shows zero bot commits on `main`: every commit-back step written before this was discovered is dead. It fails at the END of the job, so the work runs, burns the minutes, and is thrown away with a red run -- and on a weekly lane nobody notices for a week (the North Star scoreboard's first-ever run, #2466; `benchmarks.yml` had been broken the same way, unnoticed, behind an earlier failure).

Any scheduled job that regenerates committed data calls `scripts/open_data_pr.sh <branch> <commit-subject> <pr-title> <path>...` instead: rolling bot branch, PR, auto-merge, merge queue.

**It needs `secrets.GM_BOT_TOKEN`, a PAT -- NOT `GITHUB_TOKEN`.** A PR opened with `GITHUB_TOKEN` deliberately does not trigger workflow runs, so `ci-required` never reports and the PR wedges in the queue unmergeable forever -- the same "required check that never ran" trap as a skip-CI directive in a commit message. The script hard-fails on an empty token rather than letting that surface as a stuck PR a week later. For the same reason such a commit subject must NOT carry a CI-skip directive: it has to pass `ci-required` to merge at all.

## CI poll loop pattern
`gh pr checks <N> | grep -qv pending` is WRONG (returns true on the first non-pending line). Use `while gh pr checks <N> | grep -qE "pending|in_progress"; do sleep 30; done`.

## `gh pr merge` under GitHub 502
First call may 502; second says "Merge already in progress" while PR state stays `OPEN`. The merge lands asynchronously seconds later. Poll with `until [ "$(gh pr view N --json state -q .state)" != "OPEN" ]; do sleep 10; gh pr merge N --squash --delete-branch 2>/dev/null || true; done` rather than treating the second error as terminal.

## CI step `continue-on-error: true` and step `conclusion`
`gh run view --json` reports `conclusion: success` for steps with `continue-on-error: true` regardless of the real exit code. Don't trust per-step JSON to gauge whether pytest is green — grep raw logs (`gh run view <id> --log | grep -E "passed|failed,"`) for the pytest summary line.

## `gh` field-name gotchas
- `gh repo view --json topics` errors; the field is `repositoryTopics` (object array, `.repositoryTopics | map(.name)`).
- `gh release create --notes` body rejects em-dashes via the API (422). Keep release notes ASCII like everything else.
- `gh repo edit --description` rejects strings >350 chars (HTTP 422). Trim before retrying.

## Mermaid diagrams in README
- GitHub renders Mermaid natively in fenced ` ```mermaid ` blocks. Prefer it over ASCII for any diagram with more than two arrows.
- Mermaid auto-sizes nodes by label width. The `<sub>` HTML tag inside labels doesn't render visually but its bytes still count, so multi-line `Title<br/><sub>subtitle</sub>` labels overflow with the subtitle cropped. Use single-line node labels and put per-step detail in a Markdown table below the diagram. (Bit us in PR #89 → fixed in PR #90.)

## pnpm vs npm flag drift
- `pnpm pack` has no `--dry-run` flag (npm-only). pnpm always writes a `.tgz`; running plain `pnpm pack` on a CI dry-run path validates packing without publishing.
- `pnpm publish` from CI needs `--no-git-checks` because the runner checkout state confuses pnpm's "is this the latest commit on the branch?" guard.

## Stacked PR auto-closure on squash-merge
Squash-merging PR A with `--delete-branch` auto-closes any stacked PRs targeting A's branch — `gh pr reopen` rejects with "Could not open." Recovery: rebase locally onto main (or cherry-pick only the wave's own commits if a full rebase cascades add-add conflicts), force-push, open a fresh PR. Bit the TS parity wave twice (#139→#141, #140→#142).

## `gh pr merge --delete-branch` + local worktree
Cosmetic failure: `cannot delete branch 'X' used by worktree at ...`. The remote merge succeeded; only local cleanup failed. Safe to ignore unless you're scripting on the exit code.

## CI `UNSTABLE` vs failing
A `continue-on-error: true` step that exits non-zero still flips the parent job's conclusion to FAILURE → PR `mergeStateStatus: UNSTABLE`. The PR is still mergeable; the merge button just looks scary. Don't waste time chasing UNSTABLE if you know the failing lane is opt-in.

