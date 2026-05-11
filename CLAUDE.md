# Golden Suite monorepo

Polyglot monorepo: `packages/{python,rust,typescript,dbt,actions}`. Per-package CLAUDE.md files own package-specific context.

## TypeScript: pnpm + Turborepo (post-2026-05-02 fold)
- `pnpm@9.15.0` pinned in root `package.json` (exact semver — Corepack rejects `9.x` ranges).
- Windows: enable Developer Mode for pnpm symlinks. Fallback if `corepack enable` needs admin: `npm i -g pnpm@9.15.0`.
- `.npmrc` carries two non-default settings with rationale comments — do NOT remove without reading them: `node-linker=hoisted` (turbo platform-binary conflict on Windows) and `auto-install-peers=false` (pnpm 8+ auto-installs optional peers, breaking goldenmatch fallback-path tests).
- Local: `pnpm install` → `pnpm turbo run build test typecheck` (lint dropped from CI invocation — currently identical to typecheck via `tsc --noEmit`).

## CI (.github/workflows/ci.yml)
- Pytest step uses `--timeout=120 --timeout-method=thread`. PR #66 hit a goldencheck pytest hang on Linux that didn't reproduce locally — timeout converts hangs into actionable failures.
- Pytest is `continue-on-error: true` per matrix package. Per-package `--ignore` lists in the case statement mirror each package's pre-fold tuning (see each `packages/python/<pkg>/CLAUDE.md` for the canonical list).
- Single TS job (not matrix) — relies on `pnpm-lock.yaml` being committed. PPRL tests in `packages/typescript/goldenmatch/tests/unit/pprl-protocol.test.ts` need 30s/45s timeouts under the post-fold shared-runner CI (was 5s/15s on dedicated runners).

## CI path filters (post-2026-05-06, PR #89)
- `.github/workflows/ci.yml` uses `dorny/paths-filter@v3` to gate jobs by changed paths. The `changes` job emits per-area outputs; each downstream job has `if: needs.changes.outputs.<area> == 'true'`. Python is a dynamic matrix — only changed packages enter the matrix.
- Workflow-file changes to `ci.yml` itself force every job to re-run (so the filter logic stays under test). Adding a new job means adding a new filter entry in the `changes` job AND wiring the `if:` gate.
- Doc-only PRs (README, screenshots, wiki refs) run only the `changes` job (~8s). Verified on PR #90.

## Railway: goldenmatch-mcp service
- Project `golden-suite-mcp`, service `goldenmatch-mcp`, env `production`. IDs in `packages/python/goldenmatch/.railway/` after `railway link`.
- Build/deploy config pinned in `packages/python/goldenmatch/railway.json`. Service `rootDirectory='packages/python/goldenmatch'` set via Railway GraphQL — DO NOT revert unless also moving `Dockerfile.mcp` back to repo root.
- Access token: `~/.railway/config.json` → `user.accessToken`. GraphQL endpoint: `https://backboard.railway.com/graphql/v2`.
- Status check: `cd packages/python/goldenmatch && railway deployment list | head -5`. Build logs: `railway logs --build <deployment-id>`.

## ghcr.io packages
- `publish-containers.yml` builds 7 images. 6 are new (created by this monorepo's GITHUB_TOKEN, default permissions). `goldenmatch-extensions` pre-existed from the standalone repo — its "Manage Actions access" must explicitly grant `benzsevern/goldenmatch` write role, or pushes fail with `permission_denied: write_package`.

## Performance audit (docs/superpowers/specs/2026-05-02-performance-audit-checklist.md)
- **Lesson:** the audit ranked items by static counts (boundary crossings, sequential ops). 3 of 3 measured items came in well under the framing. **Always measure wall-clock with the workload of interest before designing.** cProfile cumtime != wall (especially with threading); compare 5-run median wall on real shapes.

## CI poll loop pattern
`gh pr checks <N> | grep -qv pending` is WRONG (returns true on the first non-pending line). Use `while gh pr checks <N> | grep -qE "pending|in_progress"; do sleep 30; done`.

## `gh pr merge` under GitHub 502
First call may 502; second says "Merge already in progress" while PR state stays `OPEN`. The merge lands asynchronously seconds later. Poll with `until [ "$(gh pr view N --json state -q .state)" != "OPEN" ]; do sleep 10; gh pr merge N --squash --delete-branch 2>/dev/null || true; done` rather than treating the second error as terminal.

## CI step `continue-on-error: true` and step `conclusion`
`gh run view --json` reports `conclusion: success` for steps with `continue-on-error: true` regardless of the real exit code. Don't trust per-step JSON to gauge whether pytest is green — grep raw logs (`gh run view <id> --log | grep -E "passed|failed,"`) for the pytest summary line.

## `pytest -n auto` worker isolation
xdist runs each test in a worker process. Tests cannot share registry/global-state side effects (e.g. `register_transform` in test A is invisible to test B). Make every test self-contained — register inside the test that asserts.

## Test fixture paths: CWD differs by environment
Local CWD = package dir (e.g. `packages/python/goldencheck`); CI CWD = repo root. Bare relative paths like `Path("tests/fixtures/simple.csv")` pass locally and fail in CI. Anchor to `__file__`: `Path(__file__).parent.parent / "fixtures" / "simple.csv"`.

## GitHub auth
- `benzsevern/*` repos use personal account `benzsevern`, not work `benzsevern-mjh`. Always `gh auth switch --user benzsevern` before push, switch back after.

## Post-fold GitHub Actions
Only `.github/workflows/` at the repo root runs. Workflow files left under `packages/python/<pkg>/.github/workflows/` from pre-fold repos are orphaned (silently ignored). v1.6.0 release shipped no PyPI publish until `publish-goldenmatch.yml` was added at the root.
- `publish-goldenmatch.yml` — fires on `release: published` for `v*` tags (skips `goldenmatch-js-v*`); `workflow_dispatch` with `ref` input for retro-publish. Uses `PYPI_TOKEN` (trusted publishing not configured).

## Pre-fold archive
`_archive/goldenmatch-pre-fold/` retains the standalone repo's git history. Old specs/plans under `_archive/goldenmatch-pre-fold/docs/superpowers/` are sometimes the foundation for current work — search there before assuming a feature is undesigned.

## `gh` field-name gotchas
- `gh repo view --json topics` errors; the field is `repositoryTopics` (object array, `.repositoryTopics | map(.name)`).
- `gh release create --notes` body rejects em-dashes via the API (422). Keep release notes ASCII like everything else.
- `gh repo edit --description` rejects strings >350 chars (HTTP 422). Trim before retrying.

## Mermaid diagrams in README
- GitHub renders Mermaid natively in fenced ` ```mermaid ` blocks. Prefer it over ASCII for any diagram with more than two arrows.
- Mermaid auto-sizes nodes by label width. The `<sub>` HTML tag inside labels doesn't render visually but its bytes still count, so multi-line `Title<br/><sub>subtitle</sub>` labels overflow with the subtitle cropped. Use single-line node labels and put per-step detail in a Markdown table below the diagram. (Bit us in PR #89 → fixed in PR #90.)

## Workflow trigger ordering
A push of a tag that points at a commit predating a workflow file's introduction will NOT fire that workflow — GitHub Actions reads the workflow definition from the tag's commit, not from main HEAD. After landing a new `publish-*.yml` at the root, either re-tag at HEAD-of-main once the workflow is committed, or use `gh workflow run <file> --ref main` (which reads the workflow from main and checks out via the `ref` input). Bit us on `goldenmatch-js-v0.4.0` (v0.4.0 merge predated the publish workflow) and on `v1.6.0` (Python orphan).

## pnpm vs npm flag drift
- `pnpm pack` has no `--dry-run` flag (npm-only). pnpm always writes a `.tgz`; running plain `pnpm pack` on a CI dry-run path validates packing without publishing.
- `pnpm publish` from CI needs `--no-git-checks` because the runner checkout state confuses pnpm's "is this the latest commit on the branch?" guard.

## Stacked PR auto-closure on squash-merge
Squash-merging PR A with `--delete-branch` auto-closes any stacked PRs targeting A's branch — `gh pr reopen` rejects with "Could not open." Recovery: rebase locally onto main (or cherry-pick only the wave's own commits if a full rebase cascades add-add conflicts), force-push, open a fresh PR. Bit the TS parity wave twice (#139→#141, #140→#142).

## `gh pr merge --delete-branch` + local worktree
Cosmetic failure: `cannot delete branch 'X' used by worktree at ...`. The remote merge succeeded; only local cleanup failed. Safe to ignore unless you're scripting on the exit code.

## CI `UNSTABLE` vs failing
A `continue-on-error: true` step that exits non-zero still flips the parent job's conclusion to FAILURE → PR `mergeStateStatus: UNSTABLE`. The PR is still mergeable; the merge button just looks scary. Don't waste time chasing UNSTABLE if you know the failing lane is opt-in.

## pypistats.org throttling
pypistats `/api/packages/<pkg>/recent` 429s aggressively on unauthenticated bursts. Any script hitting it needs retry+backoff plus inter-request sleep (~1.5s). `scripts/suite_download_badges.py` is the reference implementation — preserves the prior badge value when throttled so the workflow exits 0.
