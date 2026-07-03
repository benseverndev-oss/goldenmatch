# Benchmark registry

`registry.yml` is the single source of truth for every dispatch-only benchmark.
The consolidated [`bench.yml`](../workflows/bench.yml) workflow and the local
[`scripts/bench.py`](../../scripts/bench.py) dispatcher both read it, so "how is
suite X installed and invoked" is defined once and shared by CI and your laptop.

This replaces the old pattern of one `bench-<name>.yml` workflow per benchmark
(~50 near-identical lines each: checkout → setup → install → run one script →
upload). Those scaffolds differed only in *which script, which extras,
native-or-not, which runner, which env* — exactly the fields the registry holds.

## Run a benchmark

Locally:

```bash
python scripts/bench.py --list                       # the catalog
python scripts/bench.py lsh-recall                    # run with suite defaults
python scripts/bench.py lsh-recall -- --threshold 0.5 --num-perms 128
python scripts/bench.py perceptual --dry-run          # print the command, don't run
```

In CI (one workflow, pick the suite):

```bash
gh workflow run bench.yml --ref main \
  -f suite=lsh-recall -f runner=large-new-64GB \
  -f args="--threshold 0.5 --num-perms 128"
```

Per-suite CLI flags live in each bench script's own `argparse`; everything after
`--` (or any trailing args) is forwarded to the script verbatim. The workflow
takes one free-form `args` input for the same reason — it doesn't need to know
each bench's flags.

## Add a benchmark

1. Add the bench script (e.g. `scripts/bench_my_thing.py`) with its own argparse.
2. Add **one row** to `registry.yml`.
3. Add the suite name to the `suite:` `options:` list in `bench.yml`
   (a test, `test_bench_registry.py::test_workflow_choices_match_registry_keys`,
   fails if the two drift).

That's it — no new workflow file.

## Registry schema

| field      | type | default         | meaning |
|------------|------|-----------------|---------|
| `desc`     | str  | — (**required**) | one-line description |
| `script`   | str  | — (**required**) | entrypoint, relative to `workdir` |
| `workdir`  | str  | `.`             | dir to run from (repo-root-relative) |
| `native`   | bool | `false`         | build the Rust native kernel first |
| `install`  | str  | `uv`            | `uv` (`uv sync --all-packages`) or `pip` (editable install) |
| `extras`   | list | `[]`            | package extras for the pip editable install |
| `with`     | list | `[]`            | uv `--with` ephemeral run deps (uv only) |
| `pip`      | list | `[]`            | extra `pip install <dep>` packages |
| `runner`   | str  | `ubuntu-latest` | **recommended** runner; the workflow takes `runner` as an input (a step can't set `runs-on`), this is its default + what `--list` prints |
| `env`      | map  | `{}`            | static env vars exported before the run |
| `args`     | str  | `""`            | default args appended when none are supplied |
| `artifact` | str  | `""`            | path (workdir-relative) uploaded as an artifact |
| `summary`  | str  | `""`            | path (workdir-relative) cat into the job step-summary |

## What stays a standalone workflow (and why)

Not every bench fits the generic runner. These keep their own file:

- **Secret / remote orchestration** — `bench-distributed-stack` (`RAY_ADDRESS`),
  `bench-ray-cluster` (Infisical), `bench-sail-100m` (`SAIL_REMOTE`),
  `bench-phase5-*` (Ray cluster). Bespoke secret + service wiring.
- **Push/PR gates** — e.g. `bench-graphiti-smoke` (runs on path changes),
  `bench-probabilistic` (panel regression gate). They aren't dispatch-only.
- **Special harnesses** — `bench-issue-688` (builds the kernel from a *separate*
  `source_ref` for an A/B), the `er-kg-bench` / graphrag-QA family (isolated
  venvs + their own `erkgbench/run.py` delegating runner), `scale-audit`
  (conditional `duckdb-udf` extension install).

The registry covers the homogeneous dispatch-only majority; the heterogeneous
tail is intentionally out of scope. See the consolidation audit at
`docs/superpowers/specs/2026-06-23-workflow-consolidation-audit.md`.
