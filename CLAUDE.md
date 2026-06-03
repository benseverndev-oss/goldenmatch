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

## Railway: goldenmatch-bench-gen service
- Separate service from `goldenmatch-mcp`. Hosts the bench-data generator -- a FastAPI control plane that runs `scripts/generate_phase5_dataset.py` (and future bench generators) on Railway's beefy box and persists the result on a `/data` volume mount. Modeled on `goldenmatch-shell-company-network`'s `shellnet-job` pattern.
- Build: `packages/python/goldenmatch/Dockerfile.bench`. Railway config: `packages/python/goldenmatch/railway-bench.json`.
- Env vars (set on the service): `GOLDENMATCH_BENCH_JOB_TOKEN` (bearer), `GOLDENMATCH_BENCH_DATA_DIR=/data` (volume mount target).
- Endpoints (all bearer-auth except `/healthz`): `POST /generate?rows=N&workers=W`, `GET /status`, `GET /download?file=NAME`, `GET /list`, `GET /logs?job_id=ID`.
- Laptop-side trigger: `scripts/trigger_bench_gen.py --rows 50000000 --workers 16 [--upload-to-release bench-dataset-v1]`. Needs `GOLDENMATCH_BENCH_JOB_URL` + `GOLDENMATCH_BENCH_JOB_TOKEN` in the shell env.
- The trigger uploads to the existing `bench-dataset-v1` GitHub Release as a new asset when `--upload-to-release` is set; the `bench-phase5-simulated` workflow then downloads `bench_50000000.parquet` from that release at job time.
- Generator perf (vectorized + ProcessPoolExecutor on `--workers 4`): 1M rows in 1.4s on a 16-core box. 50M extrapolates to ~70s; 100M to ~140s. Way under the 60-min job cap that prevented running this in CI.

## ghcr.io packages
- `publish-containers.yml` builds 7 images. 6 are new (created by this monorepo's GITHUB_TOKEN, default permissions). `goldenmatch-extensions` pre-existed from the standalone repo — its "Manage Actions access" must explicitly grant `benseverndev-oss/goldenmatch` write role, or pushes fail with `permission_denied: write_package`.

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
- `benzsevern/*` AND `benseverndev-oss/*` repos use personal account `benzsevern`, not work `benzsevern-mjh`. Always `gh auth switch --user benzsevern` before push, switch back after. (The `benseverndev-oss` org is owned by the personal `benzsevern` account; same auth dance applies.)

## goldenmatch-native (optional compiled runtime)
- `goldenmatch-native` is a SEPARATE maturin/abi3 package (polars / polars-runtime split): `goldenmatch` stays pure-Python; `pip install goldenmatch[native]` pulls the compiled `_native` kernel. Crate + maturin `pyproject.toml` at `packages/rust/extensions/native/`; same crate still builds in-tree via `scripts/build_native.py`.
- Loader discover order (`goldenmatch/core/_native_loader.py`): `goldenmatch._native` (in-tree build) -> `goldenmatch_native._native` (the wheel) -> pure Python.
- Release: tag `goldenmatch-native-v*` fires `publish-goldenmatch-native.yml` (distinct from Python `v*` / TS `goldenmatch-js-v*`). Build BOTH macOS arches on `macos-14` (cross-compile x86_64) -- `macos-13` Intel runners queue indefinitely in this org. `workflow_dispatch` has a `publish` toggle for a build-matrix dry run (no PyPI upload).
- **#688 ROOT CAUSE was a rayon `LockLatch` futex park in the native kernel (NOT the wheel skew below).** The block-scoring kernel parallelized intra-bucket work with rayon (`par_iter().collect()`); on 8-core AMD EPYC Linux (`ubuntu-latest-xlarge`) the calling thread parked on `rayon_core::latch::LockLatch::wait_and_reset` with near-zero forward progress -- ~190s of futex wait, **zero CPU in the actual scoring** (py-spy `--native` confirmed: `score_one` barely registered). Env-specific: does NOT reproduce on `large-new-64GB` (16c) or `ubuntu-latest` (both sub-second) -- it's a futex/scheduler interaction tied to that CPU/core-count, not "Linux" generally (Windows escapes via `WaitOnAddress`). The kernel's rayon path had only ever been parity-validated, never perf-validated on Linux. **NOT a pyo3/GIL deadlock** -- the rayon closure touches zero Python objects (refuted from source). Fix (#692, `goldenmatch-native 0.1.3`): score small/medium calls in the CALLING thread (no rayon, no latch); fan out to rayon only above a candidate-pair threshold `GOLDENMATCH_NATIVE_RAYON_MIN_PAIRS` (default 20M; `0`=always rayon, huge=always sequential). The Python caller (`score_buckets`) already parallelizes across buckets, so no machine parallelism is lost. Verified 286.9s -> 1.2s (240x) on the wedge runner, byte-identical output.
- **`GOLDENMATCH_BUCKET_DEBUG=1`** (#699) prints a per-bucket prep / kernel / post-filter timing split for `backend=bucket` -- the prep-vs-kernel breakdown that localizes "Polars wrapping vs the Rust kernel" in one run (it pointed straight at the kernel for #688). Off by default, zero cost, output-invariant.
- **Wheel/caller symbol skew is a real silent-slow-fallback footgun (a SECONDARY #688 bug, not the 44x).** Python call sites reach new kernel symbols via `try: native_module().X except AttributeError` with a slower fallback. So when a kernel perf-optimization adds a NEW symbol, EVERY env on `pip install goldenmatch[native]` keeps hitting the slow fallback until the wheel is **republished** -- in-tree builds (Windows dev) pick it up immediately, masking it. In #688, `build_exclude_set` (#552) landed one day after wheel `0.1.0`, so the published wheel rebuilt the kernel HashSet per bucket call -- real overhead, but republishing 0.1.2 did NOT move the wall (the rayon park dominated; see above). Lesson stands regardless: confirm a new kernel symbol is in the PUBLISHED wheel (`unzip` the `.so`, grep the symbol) before assuming any env benefits, and **republish the wheel in the same change that adds a depended-on symbol**. Corollary (`feedback_verify_perf_not_just_ship`): for a perf fix, verify the WALL moved on the failing env, not just that the symbol/version shipped.
- **Republish reads the version from `pyproject.toml`, not `Cargo.toml`.** maturin uses `[project].version` in `packages/rust/extensions/native/pyproject.toml`; the two had drifted (pyproject `0.1.0` vs Cargo `0.1.1`). Bump BOTH in lockstep -- a republish without bumping pyproject rebuilds the old version and `skip-existing: true` silently no-ops (no new wheel, no error).
- **A/B harness for the native kernel:** `scripts/bench_issue_688.py` (the #688 repro, path selected by `GOLDENMATCH_NATIVE_RAYON_MIN_PAIRS`) + `.github/workflows/bench-issue-688.yml` (`workflow_dispatch`; `source_ref` builds the kernel from a branch, `runner` picks the env). **#688 reproduces only on an 8-core x86 EPYC runner** (`ubuntu-latest-xlarge`), not `large-new-64GB` (16c). Brand-new GitHub-hosted larger runners take 30-60 min to provision and sometimes stall allocation entirely (sit "Ready", jobs queue forever) -- see `reference_github_hosted_runners_688`.

## Post-fold GitHub Actions
Only `.github/workflows/` at the repo root runs. Workflow files left under `packages/python/<pkg>/.github/workflows/` from pre-fold repos are orphaned (silently ignored). v1.6.0 release shipped no PyPI publish until `publish-goldenmatch.yml` was added at the root.
- `publish-goldenmatch.yml` — fires on `release: published` for `v*` tags (skips `goldenmatch-js-v*`); `workflow_dispatch` with `ref` input for retro-publish. Uses `PYPI_TOKEN` (trusted publishing not configured).
- `publish-mcp.yml` — auto-syncs `packages/python/<pkg>/server.json` to the official MCP Registry (`registry.modelcontextprotocol.io`) after every PyPI publish. Same tag patterns as the per-package PyPI workflows. Auth via GitHub OIDC (`id-token: write`); no secrets needed. `workflow_dispatch` with `package` input (`all` / one of five) lets you force-refresh listings without re-tagging. Updating the registry directly via the web UI also works, but the workflow keeps versions in lockstep with PyPI automatically.

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

## MCP Registry vs mcp-marketplace.io
Official registry is `registry.modelcontextprotocol.io` (suite is listed at `io.github.benzsevern/{goldenmatch,goldencheck,goldenflow,goldenpipe,infermap}`). `mcp-marketplace.io` is a third-party aggregator and does NOT list this suite. Maintainer dashboard for the official registry uses `io.github.X/Y` package identifiers + Approved/Remote/Edit buttons — that's the screenshot you'll see, not the marketplace site. `publish-mcp.yml` auto-syncs all five listings on `release: published`; `workflow_dispatch` with `package=all` force-refreshes without re-tagging.

## Publish workflows: read version from git tag, not PyPI
`publish-mcp.yml` and the per-package `publish-<pkg>.yml` workflows both fire off `release: published`. If the MCP sync queries PyPI for the version, it races against the parallel PyPI publish and reads the prior version → registry returns 400 "cannot publish duplicate version". Always derive version from the git tag (`${TAG##*-v}`, then `${V#v}`) for release events and from `pyproject.toml` for `workflow_dispatch`. PR #167 has the canonical pattern.
