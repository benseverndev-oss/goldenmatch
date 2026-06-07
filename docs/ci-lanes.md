# CI lanes

Per the quality program, the suite's CI surface is split into **explicit lanes** so a "we have tests but skip them in CI" gap is visible rather than silent. This page documents each lane: what runs, what's gated on, and how to enable a not-configured lane.

## Quick reference

| Lane | When | What | How to enable |
|---|---|---|---|
| `python` (per-package matrix) | every PR | Unit tests + ruff per package | always on |
| `synthetic_benchmarks` | every PR (when goldenmatch changed) | Synthetic-fixture T1/T3 recovery tests against committed fixtures | always on |
| `python_skipped_lanes / db` | every PR | `test_db.py` + `test_reconcile.py` | provision Postgres service container |
| `python_skipped_lanes / mcp_watch` | every PR | `test_mcp_and_watch.py` | provision MCP runtime fixtures |
| `python_skipped_lanes / llm_boost` | every PR | `test_llm_boost.py` | set `OPENAI_API_KEY` repo secret |
| `python_skipped_lanes / embedder` | every PR | `test_embedder.py` | provision Vertex AI service account |
| `python_skipped_lanes / benchmarks` | every PR | `test_autoconfig_benchmarks.py` | provision DBLP-ACM/Febrl3/NCVR datasets |
| `web_ui_e2e` | when web/ changed | Playwright smoke against goldenmatch[web] | always on |
| `typescript` | when ts changed | `pnpm turbo run build test typecheck` | always on |
| `rust` | when rust/ changed | `cargo test --workspace` + clippy | always on |
| `dbt` | when dbt/ changed | `dbt parse` smoke | always on |
| `action` | when actions/ changed | `action.yml` presence check | always on |
| `benchmarks` (workflow) | weekly Mondays 06:00 UTC + workflow_dispatch | Real DBLP-ACM/Febrl3/NCVR + DQbench | set repo var `RUN_BENCHMARKS=true` |

## Skipped-lane prereqs

Each `python_skipped_lanes` matrix entry self-reports its prereq state. Today none are configured in CI; each lane prints a `::notice::` annotation explaining what would be needed to enable it. This is intentional: the gap is auditable, not hidden.

### `db` lane — Postgres-required tests

`tests/test_db.py` (Postgres connector + sync) and `tests/test_reconcile.py` (cluster reconciliation) require a live Postgres database. To enable in CI:

1. Add a `postgres` service container to the workflow:
   ```yaml
   services:
     postgres:
       image: postgres:16
       env:
         POSTGRES_PASSWORD: postgres
         POSTGRES_DB: goldenmatch_test
       ports: ["5432:5432"]
       options: >-
         --health-cmd "pg_isready -U postgres"
         --health-interval 10s
         --health-timeout 5s
         --health-retries 5
   ```
2. Set `POSTGRES_TEST_DSN=postgresql://postgres:postgres@localhost:5432/goldenmatch_test` in the lane's env.
3. Update the `db` lane's `case` block in `.github/workflows/ci.yml` to set `CONFIGURED="true"` when the env var is present.

`testing.postgresql` teardown errors on Windows are harmless (per package CLAUDE.md) but irrelevant on Linux runners.

### `mcp_watch` lane — MCP runtime tests

`tests/test_mcp_and_watch.py` exercises MCP server lifecycle + the `goldenmatch watch` daemon. Tests are flaky on Linux runners — historically this is why they were added to the `--ignore` list. Re-enabling requires:

1. Stabilizing the watch-daemon test fixtures (probably involves reducing the lock-file polling cadence).
2. Mocking the MCP transport layer in tests rather than spinning up real ports.

Out of scope for the v1 quality program; tracked as a TODO.

### `llm_boost` lane — LLM scoring tests

`tests/test_llm_boost.py` exercises the LLM scorer integration. Two prereqs:
- `OPENAI_API_KEY` repo secret (cost-controlled by `BudgetConfig(max_calls=500, max_cost_usd=1.0)`)
- `import torch` doesn't crash the runner — this segfaults on the maintainer's local Windows but works on Linux

The lane's `case` block already checks `OPENAI_API_KEY`. Setting the secret in the `benseverndev-oss/goldenmatch` repo (Settings → Secrets and variables → Actions) flips this lane to `configured`. Cost: ~$0.05–0.50 per run depending on test scope.

### `embedder` lane — Vertex AI tests

`tests/test_embedder.py` exercises the Vertex AI embedding integration. Requires:
- GCP service account JSON in `GOOGLE_APPLICATION_CREDENTIALS_JSON` repo secret
- Service account has `roles/aiplatform.user` on project `gen-lang-client-0692108803`

`import torch` segfaults on the maintainer's local machine (same issue as `llm_boost`) so this is Linux-CI-only territory. Vertex AI `text-embedding-004` does not support fine-tuning — only inference (per package CLAUDE.md).

### `benchmarks` lane — Real benchmark datasets

`tests/test_autoconfig_benchmarks.py` exercises auto-config against real benchmark datasets (DBLP-ACM, Febrl3, NCVR). Datasets are gitignored:
- `tests/benchmarks/datasets/DBLP-ACM/` (Leipzig CSVs, latin-1 encoding)
- `tests/benchmarks/datasets/NCVR/` (488MB voter zip; sample at `ncvoter_sample_10k.txt`)
- Febrl3 ships with `recordlinkage` PyPI package

To enable: set repo var `RUN_BENCHMARKS=true`. `scripts/run_benchmarks.py` now **auto-pulls** missing file-backed datasets (`--download`, default on):
- **DBLP-ACM** downloads from Leipzig automatically (override with repo var `DBLP_ACM_URL` → `GOLDENMATCH_DBLP_ACM_URL` if Leipzig 404s; the Magellan mirror carries identical CSVs). Verified end-to-end: fresh pull → F1 0.9641.
- **Febrl3** is self-contained via `recordlinkage` (installed in the lane).
- **NCVR**'s real source is a 4.3 GB NC SBE extract carrying real voter PII (names + home addresses), so it's gitignored and NOT mirrored. The lane now **falls back to a committed PII-free synthetic NCVR-shaped fixture** (`dqbench_adapters.ncvr.generate_synthetic_ncvr`) when the real sample is absent — results are labelled **`NCVR-synthetic`** (its own baseline, ~0.98, NOT the real-data 0.9719). To run the real-data number, host `ncvoter_sample_10k.txt` privately and set repo var `NCVR_SAMPLE_URL` → `GOLDENMATCH_NCVR_SAMPLE_URL`.
- **DQbench** is installed from the **org repo** (`git+https://github.com/benseverndev-oss/dqbench`), NOT PyPI — PyPI `dqbench` 1.0.0 is a stale slice with only the detection API (no `EntityResolutionAdapter`), so the ER lane needs the git version. The ER composite responds to `--planning-effort` (`thinking` lifts it 51.56 → 57.11 by fixing budget-limited RED commits on T2).

The synthetic-fixture smoke (`synthetic_benchmarks` job in `ci.yml`) covers regressions against committed synthetic fixtures on every PR.

## Real-benchmark workflow (`benchmarks.yml`)

Runs on `schedule` (weekly) + `workflow_dispatch`. Gated by `vars.RUN_BENCHMARKS=true` repo variable. When configured:

- Reads datasets from `tests/benchmarks/datasets/` (must be present on the runner; see the `benchmarks` lane note above)
- Runs `scripts/run_benchmarks.py --datasets all --output benchmark_results.json --summary-md $GITHUB_STEP_SUMMARY`
- Uploads results as `benchmark-results-<run_id>` artifact (90-day retention)
- DQbench-with-LLM is opt-in via `workflow_dispatch` input (cost-bounded by adapter's `BudgetConfig`)

To trigger a one-off measurement (e.g. before a release):
```bash
gh workflow run benchmarks --repo benseverndev-oss/goldenmatch -f datasets=all -f with_llm=false
```

## Coverage gate

The `python` job's `Coverage floors` step (only on the `goldenmatch` matrix entry) runs `scripts/check_coverage_floors.py` against `coverage.xml`. Per-module floors are declared in that script's `FLOORS` dict. Floors are conservative (~5pp below today's measured value); ratchet upward as packages improve.

A regression in any tracked module fails the `python / pkg=goldenmatch` job with a clear "actual=X% < floor=Y%" diff.
