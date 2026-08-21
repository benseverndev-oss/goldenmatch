# Golden Suite monorepo

Polyglot monorepo: `packages/{python,rust,typescript,dbt,actions}`. Per-package CLAUDE.md files own package-specific context.

**STANDING RULE — flaky test first.** The moment a test is observed flaky (intermittent CI red, a re-queue that goes green, a "passes locally / fails in CI" report), STOP the current work and fix the flakiness first, then resume. A flaky test is a real defect — it hides regressions and erodes the merge gate — so it takes priority over the feature in flight, not a "later" backlog item. Fix the ROOT cause (determinism, isolation, resource hardening), not the symptom (no blind retries/`rerun` bandaids, no loosened assertions); the swallowed-error / non-WAL-SQLite / xdist-shared-state classes are the usual suspects.

**North Star** (the pull that colors every decision): be the tool any developer reaches for *by default* for entity resolution. It is never "done" — defaults are re-earned every release. The full statement + the five decision-test commitments (zero-config, scale-invariant correctness, shared-capabilities-conform, approach-the-expert, never-black-box) live in `context-network/foundation/project-definition.md`. When two changes compete, take the one that advances it.

**Architecture governing frame** (colors every *architectural* decision, the way the North Star colors product decisions): GoldenMatch is **one product, two engines, many surfaces** — an Arrow-native, Rust-authoritative **Identity Compute Engine** + a transaction-native **Identity Control Plane** (SQLite/Postgres), exposed through many surfaces governed by spec + conformance. The full frame + roadmap live in `context-network/architecture/one-product-two-engines.md` (decision `context-network/decisions/0047-one-product-two-engines-architecture.md`). Read it before any architectural change; the change must conform or amend the frame (doc + 0047) in the same PR. Decision tests every change must pass:
- **One authoritative semantic owner per capability** — Rust where a clean shared core exists; pure-Python / standalone-TS algorithms are classified, conformance-tested *fallbacks*, not co-equal sources of truth. Am I adding a second source of truth for behavior that already has an owner?
- **Arrow at bulk boundaries, not the universal calling convention** — smallest stable primitive for scalar/small calls. Am I paying Arrow/FFI marshaling on a small call where two strings or a `list[str]` is cleaner?
- **Compute vs. control stay distinct** — the compute engine is Arrow/batch; the control plane is a transaction-native state machine (stable IDs, merge/split, provenance, audit). Am I forcing stateful identity logic into a columnar kernel, or vice versa?
- **Kernelize on measurement** — measure the *whole* path (conversion + orchestration), not a Rust-loop-vs-Python-loop microbench; state the motivation class (perf / semantic single-sourcing / portability / safety / maintainability).
- **Conformance defines correctness** — a new backend/binding/fallback proves itself against the conformance suite (exact / numerically-equivalent / semantically-equivalent / intentionally-divergent), not by calling the same library. DataFusion/Ray/Sail/Ballista/Spark are replaceable backends, none synonymous with GoldenMatch.

## TypeScript: pnpm + Turborepo (post-2026-05-02 fold)
- `pnpm@9.15.0` pinned in root `package.json` (exact semver — Corepack rejects `9.x` ranges).
- Windows: enable Developer Mode for pnpm symlinks. Fallback if `corepack enable` needs admin: `npm i -g pnpm@9.15.0`.
- `.npmrc` carries two non-default settings with rationale comments — do NOT remove without reading them: `node-linker=hoisted` (turbo platform-binary conflict on Windows) and `auto-install-peers=false` (pnpm 8+ auto-installs optional peers, breaking goldenmatch fallback-path tests).
- Local: `pnpm install` → `pnpm turbo run build test typecheck` (lint dropped from CI invocation — currently identical to typecheck via `tsc --noEmit`).

## CI (.github/workflows/ci.yml)
- Pytest step uses `--timeout=120 --timeout-method=thread`. PR #66 hit a goldencheck pytest hang on Linux that didn't reproduce locally — timeout converts hangs into actionable failures.
- Pytest is BLOCKING: the per-package pytest step has no `continue-on-error`, so a failing lane fails the `python` job and the `ci-required` gate (which fails on any upstream `result` that is not `success`/`skipped`). Per-package `--ignore` lists in the case statement mirror each package's pre-fold tuning (see each `packages/python/<pkg>/CLAUDE.md` for the canonical list). (History: pytest was once `continue-on-error: true`; that masked real regressions and was removed — do not reintroduce it.)
- Single TS job (not matrix) — relies on `pnpm-lock.yaml` being committed. PPRL tests in `packages/typescript/goldenmatch/tests/unit/pprl-protocol.test.ts` need 30s/45s timeouts under the post-fold shared-runner CI (was 5s/15s on dedicated runners).

## CI path filters (post-2026-05-06, PR #89)
- `.github/workflows/ci.yml` uses `dorny/paths-filter@v3` to gate jobs by changed paths. The `changes` job emits per-area outputs; each downstream job has `if: needs.changes.outputs.<area> == 'true'`. Python is a dynamic matrix — only changed packages enter the matrix.
- Workflow-file changes to `ci.yml` itself force every job to re-run (so the filter logic stays under test). Adding a new job means adding a new filter entry AND wiring the `if:` gate.
- **The path-filter definitions live in `.github/filters.yml`, NOT inline in ci.yml (extracted to shrink ci.yml's add-heavy merge-conflict surface).** `dorny/paths-filter` loads it via `filters: .github/filters.yml`. Adding a lane now touches THREE spots: (1) the filter entry in `.github/filters.yml`, (2) the matching `changes.outputs.<name>` line in ci.yml, (3) the job's `if:` gate in ci.yml. The filter mapping is byte-for-byte the old inline block — a malformed `filters.yml` fails the `changes` job loudly (paths-filter parses it), so there's no silent-skip risk.
- Doc-only PRs (README, screenshots, wiki refs) run only the `changes` job (~8s). Verified on PR #90.
- **`workflow_lint` now validates ALL ~118 workflow files, and rejects DUPLICATE YAML KEYS** (`scripts/check_workflow_yaml.py` + `test_workflow_yaml.py`, 2026-08-09). Two holes, both of the "a check exists and does not fire" class this repo keeps hitting: (1) the job was gated on `ci_workflow`, which lists only `ci.yml` / `filters.yml` / `check_filter_coverage.py`, so editing any of the OTHER ~115 workflows ran no YAML validation at all; it now has its own **`any_workflow`** filter (`.github/workflows/**`) — kept SEPARATE from `ci_workflow` because that one also drives `force_all` in the merge queue, where widening it would re-run the full matrix on every workflow edit. (2) The check was an inline `yaml.safe_load`, which **ACCEPTS duplicate mapping keys and silently keeps the last** — so inserting an `if:` above a step that already had one yields an inert guard that parses clean (it reached main in #2440's `bench-er-kg` edit and was caught by hand, not CI). The script uses a duplicate-rejecting loader and refuses to pass on a scan of fewer than 20 files.
- **The merge queue is ALSO path-filtered now (PR #1257).** The `changes` filter step runs on `merge_group` too, diffing HEAD against `github.event.merge_group.base_sha` (the up-to-date `main` the group sits on) — so a queued entry only runs the lanes its paths actually touched, against *current* main (fresher than the PR's own base). `force_all` no longer fires on every `merge_group`; it now fires only on (1) `workflow_dispatch run_all=true`, or (2) a `merge_group` entry that edits `ci.yml` itself (re-validates the full filter wiring against main before it lands — `ci_workflow` output read in the `flags` step). Net effect: most merge-queue entries skip the heavy lanes (distributed/sail/native/pgrx/…) they don't touch, the way PR runs already do. The "every queued entry runs the FULL matrix" invariant is intentionally dropped — the safety argument is that the `merge_group` diff base is *newer* than the PR base, and the path filters already encode cross-package dependency edges (e.g. the `distributed` filter includes `core/cluster.py` + `backends/ray_backend.py`).

## Performance audit (docs/superpowers/specs/2026-05-02-performance-audit-checklist.md)
- **Lesson:** the audit ranked items by static counts (boundary crossings, sequential ops). 3 of 3 measured items came in well under the framing. **Always measure wall-clock with the workload of interest before designing.** cProfile cumtime != wall (especially with threading); compare 5-run median wall on real shapes.

## Any CI lane that `pip install`s goldenmatch + runs the pipeline MUST set `ARROW_DEFAULT_MEMORY_POOL=system` (2026-07-10)
pyarrow's bundled **mimalloc** allocator SIGSEGVs (exit 139) in `mi_thread_init` on the polars / `ThreadPoolExecutor` worker threads goldenmatch's pipeline spawns, when pyarrow comes from a **fresh PyPI install** (the workspace `uv sync` pyarrow does NOT trip it, which is why the main `ci.yml` python matrix is fine). This is a recurring class, not a one-off — it hit the `rust` lane (#1627, `packages/rust/extensions/CLAUDE.md`) and `goldengraph-pipeline` (#1634), and the guard was then swept into the other auto-triggered pipeline lanes (bench-er-kg / bench-{graphiti,lightrag,msgraphrag}-smoke / goldenmatch-kg / native-wheel-drift). **Standard: any new workflow that `pip install goldenmatch` (or pyarrow) from PyPI and runs dedupe/score/`run_ablation` sets `ARROW_DEFAULT_MEMORY_POOL: system` at the workflow `env:` level.** It's the documented Arrow knob and harmless where unneeded. Separate workflows are never in `ci-required`, so an unguarded lane reds `main` invisibly (no PR gates it) — the mistake that surprised us twice on 2026-07-10.

## Polars jemalloc page-decay is the biggest cheap scale lever — set `_RJEM_MALLOC_CONF` for large pipeline runs (2026-07-20)
Polars' allocator is **jemalloc** (Rust `jemalloc-sys`, `_RJEM_` symbol prefix — so plain `MALLOC_CONF` does NOT reach it; it reads **`_RJEM_MALLOC_CONF`**). Default jemalloc holds freed **dirty/muzzy** pages ~10s for reuse. GoldenMatch's staged batch pipeline frees large polars frames between stages (score → cluster → golden), so those retained pages inflate peak RSS by **~a third with no live data behind them** — the per-stage resident-RSS split at 1M showed RSS climbing monotonically and "freed nowhere", which turned out to be jemalloc RETENTION, not live references (glibc `MALLOC_ARENA_MAX`/`TRIM` recovered only ~7% because they don't touch jemalloc). **Setting `_RJEM_MALLOC_CONF="dirty_decay_ms:1000,muzzy_decay_ms:0"` cut the 1M person FS peak 3278→2208 MB (−33%) at +0.8% wall (noise), byte-identical output** (`dirty_decay_ms:0,muzzy_decay_ms:0` is −35% at +3% wall — the muzzy-immediate/dirty-1s balance is the Pareto point: hot dirty-page reuse kept, cold muzzy pages returned to the OS). **Standard: any workflow / Dockerfile / large-run entry that runs the goldenmatch pipeline at scale sets `_RJEM_MALLOC_CONF: "dirty_decay_ms:1000,muzzy_decay_ms:0"` at the `env:` level** (sibling to `ARROW_DEFAULT_MEMORY_POOL: system`). Read at process start (jemalloc init) so the LIBRARY can't set it — it's an env recommendation like the Arrow knob; wired into `bench-er-headtohead.yml`, sweep into the other pipeline lanes (bench-quality-scale / qis_gate / bench-er-kg / the MCP + bench Dockerfiles) when they next matter. This reframes the FS ≥1M "frame-residency" scale story (`docs/superpowers/specs/2026-07-20-fs-frame-residency-bucket-streaming-design.md`): the apparent ~3.3 GB 1M peak is ~2.2 GB TRUE live working set + ~1.1 GB allocator retention; the architectural bounded-streaming work is now a *further* optimization on the ~2.2 GB live floor, not the urgent lever.

## `[skip ci]` / `[ci skip]` in a commit message skips the run — even in prose (2026-07-10)
GitHub Actions skips a workflow when the HEAD commit message contains `[skip ci]` / `[ci skip]` / `[no ci]` / `[skip actions]` ANYWHERE — including descriptive text. A commit titled "re-trigger CI after [skip ci] baseline" skips *itself*. When a PR head has no CI run, `ci-required` is MISSING (a required check that never ran is not `success`/`skipped`), so the PR silently cannot leave the merge queue — it looks armed but never merges. Bit #1620: the `gym-bless` auto-commit carries `[skip ci]`, and the empty commit pushed to re-fire CI *mentioned* `[skip ci]` in its own message and got skipped too. Fix: to re-fire CI on a head, push a commit whose message does NOT contain the literal substring (`git commit --allow-empty` with a clean message works); don't quote the directive in prose.

## Merge queue: `main` serializes merges FIFO (since 2026-06-15)
`main`'s `protect-main` ruleset has a native merge queue (squash, ALLGREEN, max 5 in flight, `min_entries_to_merge=1` so a lone PR forms a group immediately, 5-min batch wait, 60-min check timeout). Strict "branch up to date" is OFF — the queue rebases each entry onto the new `main` itself. To land a PR: `gh pr merge <N> --auto --squash` (or "Merge when ready") then STOP — the queue runs CI on the `merge_group` event and merges FIFO unattended; no manual update-branch cascade. CI wiring landed in PR #943: the `merge_group` trigger plus a `force_all` flag (`ci.yml` `flags` step). **PR #1257 narrowed `force_all`:** the `changes` filter step now runs on `merge_group` too (diffing against `merge_group.base_sha` = current `main`), so a queued entry is **path-filtered against fresh main** and runs only the lanes it touches — `force_all` no longer fires on every queued entry, only on a `workflow_dispatch run_all=true` or a queued `ci.yml` self-edit (full re-validation of the filter wiring). This took the merge-queue wall from ~20 min toward ~11–12 min on lanes-untouched entries. The single gate is still `ci-required` (skipped lanes count as pass); non-required UNSTABLE lanes don't stall the queue. Rollback to "full matrix on every entry": re-add `[ "${{ github.event_name }}" = "merge_group" ]` to the `flags` step's `force_all` condition. Rollback (~60s): re-PUT the ruleset dropping the `merge_queue` rule + setting `strict_required_status_checks_policy:true` (`gh api -X PUT repos/benseverndev-oss/goldenmatch/rulesets/16264681`); the `merge_group` wiring is harmless when the queue is off.

## `pytest -n auto` worker isolation
xdist runs each test in a worker process. Tests cannot share registry/global-state side effects (e.g. `register_transform` in test A is invisible to test B). Make every test self-contained — register inside the test that asserts.

## Test fixture paths: CWD differs by environment
Local CWD = package dir (e.g. `packages/python/goldencheck`); CI CWD = repo root. Bare relative paths like `Path("tests/fixtures/simple.csv")` pass locally and fail in CI. Anchor to `__file__`: `Path(__file__).parent.parent / "fixtures" / "simple.csv"`.

## GitHub auth
- `benzsevern/*` AND `benseverndev-oss/*` repos use personal account `benzsevern`, not work `benzsevern-mjh`. Always `gh auth switch --user benzsevern` before push, switch back after. (The `benseverndev-oss` org is owned by the personal `benzsevern` account; same auth dance applies.)


## Tier-1 rules whose detail lives elsewhere

Each line below is the part that changes what you do on ANY change. The linked
file carries the incident history, measurements and mechanism. Read the link when
you touch that area; do not read it by default.

- **Parity surfaces are gated.** Adding, renaming or removing a scorer, transform,
  analyzer, blocking strategy, MCP tool, CLI command, A2A skill or SQL function
  means updating `parity/<pkg>.yaml` in the SAME PR, or CI fails. Scorers and
  blocking strategies additionally have coverage FLOORS — an uncovered one must be
  declared in a `*_deferred:` map with a reason, never left silent. Kernel symbols
  referenced from Python must be registered in `native/src/lib.rs`.
  → [context-network/operations/cross-language-parity-gates.md](context-network/operations/cross-language-parity-gates.md)
- **The native kernel is the reference; pure Python is the fallback.**
  `GOLDENMATCH_NATIVE=auto|0|1`. If you add a kernel symbol the host depends on,
  republish the wheel in the same change — otherwise every `pip install` env
  silently keeps taking the slow path.
  → [context-network/operations/native-runtime.md](context-network/operations/native-runtime.md)
- **Cutting a release.** Push the tag only (or use `cut-goldenmatch-release.yml`);
  the workflow owns the GitHub Release. NEVER `gh release create` a `v*` tag —
  immutable releases permanently tombstone the name. A member release drags
  `golden-suite`: bump its floor and cut it too, member-to-PyPI first.
  → [context-network/operations/release-and-registries.md](context-network/operations/release-and-registries.md)
- **Every distributable ships an `llms.txt` inside the installed artifact**, plus
  `Documentation` + `AI agents (llms.txt)` URLs. A new package with neither a
  pointer nor a declared deferral fails `check_docs_consistency`. The file only
  reaches users on that package's NEXT release.
  → [context-network/operations/agent-discoverability.md](context-network/operations/agent-discoverability.md)
- **Some consumers install goldenmatch from source on their own path-filtered
  lanes**, so a goldenmatch change never triggers them. `downstream_symbols`
  (in `ci-required`) catches symbol breaks at PR time; nightly schedules catch
  behavioural ones.
  → [context-network/operations/out-of-workspace-consumers.md](context-network/operations/out-of-workspace-consumers.md)
- **Two scheduled gates guard ER QUALITY, not correctness** — the suggestion gym
  and the zero-config quality-at-scale (QIS) gate. Neither is in `ci-required`, so
  a green PR says nothing about them; both need an explicit re-bless after an
  intentional kernel change.
  → [context-network/operations/quality-gates.md](context-network/operations/quality-gates.md)
- **Hosted services** (goldenmatch MCP, bench-data generator) run on Railway.
  → [context-network/operations/railway-services.md](context-network/operations/railway-services.md)
- **`gh` / CI-UI / pnpm / stacked-PR specifics** that each cost an afternoon once.
  → [context-network/operations/github-and-tooling-gotchas.md](context-network/operations/github-and-tooling-gotchas.md)

**Keeping this file small is a rule, not a preference.** It is loaded into every
session in every package, so bytes here are a tax on all work. Before adding a
section, apply the Tier-1 test: *would an agent do the wrong thing without this,
on a change anywhere in the repo?* If the answer is "only when touching X", it
belongs next to X or in `context-network/operations/`, with at most a one-line
pointer here.

## The context budget is enforced (2026-08-09)

`scripts/check_context_budget.py` caps every `CLAUDE.md` at a declared byte
budget (`claude_refs` job, `ci-required`). The splits that cut always-loaded
context 84% were a one-time cleanup against a standing habit — the root file had
been running +208/−5 lines a month — so the ceiling is what stops the regrowth.
It is a RATCHET: budgets sit just above current size, growth fails, shrinking is
reported with a suggested tighter number and never fails. Raising a budget is
allowed and sometimes right, but it is a visible line in the diff, which is the
point. `--show` prints every file against its budget.

## A commit message must not contain a CI-skip directive

`[skip ci]` / `[ci skip]` / `[no ci]` / `[skip actions]` anywhere in a commit
message — **including in prose** — makes GitHub Actions skip the whole run, which
leaves `ci-required` MISSING rather than failing, so the PR sits in the merge
queue looking armed and never merges with nothing red to show for it. The
`no-ci-skip-directive` pre-commit hook (`commit-msg` stage) refuses it. CI cannot
catch this — there is no run to fail — which is why it lives in a local hook.
`default_install_hook_types` covers both stages, so a plain `pre-commit install`
is enough. To write ABOUT the directive, break the literal ("the
skip-CI-directive trap"); to skip CI deliberately, `git commit --no-verify`.
