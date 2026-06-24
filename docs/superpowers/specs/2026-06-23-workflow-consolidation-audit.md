# GitHub Actions consolidation audit (2026-06-23)

**Context:** the `.github/workflows/` directory had grown to ~91 workflow files.
Repo-hygiene goal (user framing): *find where many little processes should
collapse into one parametrized, delegating process — "a single benchmark that
delegates to whichever benchmark makes sense and is easy to add to."* This audit
maps the workflow families, ships the benchmark consolidation, and risk-ranks
the rest.

## The families

| family | count | shape | consolidatable? |
|--------|------:|-------|-----------------|
| `bench-*` (+ scale-audit, eval) | ~40 | near-identical dispatch-only scaffolds: checkout → setup → install → run one script → upload | **YES — shipped here** |
| `publish-*` PyPI | 7 | standalone copy-paste (`publish-goldenmatch.yml` 122 lines, others 39–47) | **YES — reusable workflow (next PR)** |
| `publish-*-js` | 7 | already call reusable `_publish-js.yml` (31-line callers) | **already done — the template** |
| `publish-*-native` | 3 | maturin/abi3 wheel builds, likely copy-paste | **YES — `_publish-native.yml`** |
| `publish-*` specials | ~7 | containers, mcp, pg, embed, duckdb, goldensuite-mcp | mostly one-offs; leave |
| er-kg / graphrag bench | ~7 | isolated venvs + own `erkgbench/run.py` delegating runner | already partly consolidated; leave |
| CI / quality / security | ~10 | ci, codeql, scorecard, ast-grep, claude-code-review, … | distinct purposes; leave |

## Shipped in this PR — benchmark consolidation

The `bench-*` family was the textbook case: **36 of ~40 are `workflow_dispatch`-only**
(manual, gate nothing) and share one skeleton, varying only in *script / extras /
native-or-not / runner / env*.

**Design:**
- `.github/benchmarks/registry.yml` — one row per benchmark (the single source of truth).
- `scripts/bench.py` — dispatcher: `--list`, `--resolve <suite>` (emits the CI
  run-plan), and `run <suite> [-- args]` (builds + execs locally). The **same**
  dispatcher runs in CI, so "how is suite X installed and invoked" is defined once.
- `.github/workflows/bench.yml` — one parametrized workflow (`suite` choice +
  `runner` / `ref` / `args` inputs). Its run step is literally
  `python scripts/bench.py "$SUITE" -- $ARGS`, so all uv-vs-pip / with-deps / env
  / workdir branching lives in the dispatcher, not duplicated YAML.
- `tests/test_bench_registry.py` — schema validity, every script exists, command
  build (uv `--with` threading, pip plain), and a **no-drift gate** asserting the
  workflow's `suite` options equal the registry keys.

**Constraints discovered (and how they shaped the design):**
- `runs-on` is fixed at job start — a step can't compute it. So `runner` is a
  workflow **input**; the registry value is the *recommended default* surfaced by
  `--list`.
- Per-bench flags are bespoke — the workflow takes one free-form `args`
  passthrough; each script already parses its own argv. Trade: typed per-bench
  form fields → one `args` box. Fine for manual dispatch-only benches.
- Inputs flow through **env vars**, not `${{ }}` string interpolation into a run
  line — which sidesteps the shell-injection class flagged in PR #1199.

**Migrated (deleted) this PR — 11 homogeneous dispatch-only benches**, spanning
every axis (native/non-native, uv/pip, with-deps/pip-deps, root/package workdir,
env-light/heavy, artifact, runner-param, repo-var passthrough): `lsh-recall`,
`prepared-store`, `perceptual`, `quality-bridges`, `embedding-providers`,
`inhouse-embedder`, `native-bulk-fingerprint`, `native-cluster-kernel`,
`pair-stream-columnar`, `datafusion-vs-bucket`, `quality-invariant-scale`.

**Intentionally NOT migrated** (kept standalone — see `.github/benchmarks/README.md`):
secret/remote/distributed orchestration (`bench-distributed-stack`,
`bench-ray-cluster`, `bench-sail-100m`, `bench-phase5-*`), push/PR gates
(`bench-graphiti-smoke`, `bench-probabilistic`), and special harnesses
(`bench-issue-688`'s `source_ref` A/B, the er-kg/graphrag family, `scale-audit`'s
conditional `duckdb-udf` install).

**Follow-up (mechanical):** ~13 more dispatch-only `bench-*` workflows match the
skeleton and can be folded by adding a registry row + deleting the file. Left out
of this PR to keep the env/deps per-entry verified rather than guessed.

## Recommended next PR — PyPI publisher consolidation

**Highest remaining win, proven in-repo.** The JS publishers already use the
reusable-workflow pattern: each `publish-<pkg>-js.yml` is a 31-line caller of
`_publish-js.yml` with `with:` params + `secrets: inherit`. The **PyPI**
publishers never got the same treatment — they're 7 standalone copy-pastes
(plus 3 `-native` maturin ones).

**Plan:** add `_publish-pypi.yml` and `_publish-native.yml` reusable workflows
(mirroring `_publish-js.yml`), then shrink each `publish-<pkg>.yml` /
`publish-<pkg>-native.yml` to a ~15-line caller. Net: ~10 publishers → 2 reusable
workflows + 10 thin callers.

**Why a separate PR, not this one:** these touch **release pipelines** (PyPI
push, OIDC/trusted-publishing, cosign/sigstore signing, immutable-release draft
flow). A subtle break there surfaces *late* — releases are infrequent — exactly
the v1.6.0 / v2.1.0-class incidents in the package CLAUDE.md. It deserves its own
focused PR with a dry-run (`workflow_dispatch` build-only) validation per package
before any tag is cut. Risk: medium. Value: high. Novelty: low (copy the JS
template).

## Smaller candidates (low priority)

- `scale-audit.yml` / `scale-audit-5m.yml` / `scale-audit-5m-generate.yml` — a
  `rows`-parametrized single audit (the `duckdb-udf` conditional install is the
  one wrinkle).
- The er-kg / graphrag smoke family (`bench-graphiti-smoke`,
  `bench-lightrag-smoke`, `bench-msgraphrag-smoke`) — already share
  `erkgbench/run.py`; a single `smoke` matrix over the engine list would fold the
  three, but they carry push-path gates that differ per engine.
