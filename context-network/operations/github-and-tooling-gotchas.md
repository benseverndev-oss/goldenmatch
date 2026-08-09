# GitHub and tooling gotchas

Small, hard-won specifics about `gh`, the CI UI, pnpm-vs-npm flags, stacked PRs and Mermaid. Each cost someone an afternoon once.

> Extracted from the root `CLAUDE.md` so it is read when relevant rather than
> loaded into every session. Linked from the map at the bottom of that file.

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

