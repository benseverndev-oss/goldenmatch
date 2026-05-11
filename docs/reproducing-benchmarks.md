# Reproducing GoldenMatch benchmarks

This doc maps every published GoldenMatch benchmark number back to a
committed runner, a dataset source, an environment, and the expected
output (with tolerance).

It is the canonical answer to "where does 91.04 come from and how do
I reproduce it?". Numbers come from `packages/python/goldenmatch/CHANGELOG.md`
entries for v1.8 through v1.12.

If a published number is not currently reproducible from committed
code, the gap is documented inline (see "Known reproducibility gaps"
at the bottom). Half-truths are worse than honest gaps.

---

## TL;DR

```bash
# 1. Install
pip install -e packages/python/goldenmatch

# 2. (Optional) install dataset deps
pip install recordlinkage           # for Febrl3
pip install dqbench                 # for DQbench composite

# 3. Pin clean-room env
export GOLDENMATCH_AUTOCONFIG_MEMORY=0

# 4. Run one or all datasets
python scripts/run_benchmarks.py --datasets all \
  --output benchmark_results.json
```

The runner is `scripts/run_benchmarks.py`. The same script powers the
`benchmarks.yml` weekly workflow.

---

## Published numbers and how to reproduce them

### DQbench composite 91.04 (v1.12, T1+T2+T3 weighted)

| Property | Value |
|---|---|
| Source | `packages/python/goldenmatch/CHANGELOG.md` v1.12.0 entry |
| Runner | `scripts/run_benchmarks.py --datasets dqbench` |
| Dataset | DQbench ER tier 1+2+3 (bundled with `pip install dqbench`) |
| Environment | `GOLDENMATCH_AUTOCONFIG_MEMORY=0`, no `OPENAI_API_KEY` (no-LLM run) |
| Adapter | `goldenmatch-zeroconfig` (see gap note below) |
| Expected | composite >= 90, T1 >= 88.9%, T2 >= 97.5%, T3 >= 85.5% |
| Tolerance | composite +/- 1.5 pp run-to-run; tier F1 +/- 2 pp |
| Variance | Deterministic given fixed seeds. No LLM means no API non-determinism. |

```bash
export GOLDENMATCH_AUTOCONFIG_MEMORY=0
python scripts/run_benchmarks.py --datasets dqbench \
  --output dqbench_results.json
```

The composite is parsed from the `DQBench ER Score: X.XX` line in
`dqbench run`'s output.

### DQbench T3 jump 53.8 -> 85.5 (v1.11 -> v1.12, Path Y)

Same command as the composite above; tier breakdown is in `dqbench`'s
output and in the CHANGELOG entry for v1.12. The mechanism is the
`_apply_negative_evidence_to_exact_pairs` post-filter on the
`exact_email` matchkey (CHANGELOG v1.12.0). To reproduce both ends of
the delta, check out the v1.11.0 tag, run the dqbench command, then
check out v1.12.0 and rerun.

### DBLP-ACM F1 = 0.9641 (v1.8 through v1.12, flat)

| Property | Value |
|---|---|
| Source | CHANGELOG v1.8.0 ("Benchmarks" table) and unchanged through v1.12 |
| Dataset | Leipzig DBLP-ACM, `DBLP2.csv` + `ACM.csv` + `DBLP-ACM_perfectMapping.csv` |
| Encoding | latin-1 (utf-8 will crash) |
| Drop location | `packages/python/goldenmatch/tests/benchmarks/datasets/DBLP-ACM/` |
| Source URL | https://dbs.uni-leipzig.de/de/research/projects/object_matching/benchmark_datasets_for_entity_resolution |
| Variance | Deterministic |

The dataset is gitignored. After downloading the Leipzig zip, drop the
three CSVs into the path above.

```bash
python scripts/run_benchmarks.py --datasets dblp-acm \
  --output dblp_results.json
```

**Reproducibility gap:** the runner's GT-pair mapping is positional,
not ID-based (see `_measure_dblp_acm` in the script and "Known gaps"
below). On a 2026-05-10 dry run the script ran end-to-end but reported
F1=0.0 because emitted pairs use concatenated-frame row indices while
the perfect-mapping CSV uses the original DBLP/ACM IDs. The
**dedupe pipeline itself does produce F1=0.9641** when scored via the
package's own `tests/benchmarks/run_leipzig.py` harness (which joins
emitted pairs to ground-truth IDs correctly). The runner script's GT
join is a v1 simplification flagged in the source comment.

### Febrl3 F1 = 0.9443 (v1.8 through v1.12, flat)

| Property | Value |
|---|---|
| Source | CHANGELOG v1.8.0, unchanged through v1.12 |
| Dataset | Synthetic, bundled with `pip install recordlinkage` via `recordlinkage.datasets.load_febrl3` |
| Environment | `GOLDENMATCH_AUTOCONFIG_MEMORY=0` |
| Variance | Deterministic (synthetic dataset is fixed) |

```bash
pip install recordlinkage
python scripts/run_benchmarks.py --datasets febrl3 --output febrl3_results.json
```

**Reproducibility gap:** the runner's `_measure_febrl3` returns an
empty ground-truth set in v1 (see the explicit comment in the source).
The pipeline runs and produces clusters, but the F1 it reports is 0.
The 0.9443 number in the CHANGELOG was measured by the pre-fold
harness `.profile_tmp/baseline_febrl3_ncvr.py`, which is gitignored.
The package-level harness `tests/benchmarks/run_leipzig.py` is the
nearest committed alternative for Febrl3 today.

### NCVR F1 = 0.9719 (v1.8 through v1.12, flat)

| Property | Value |
|---|---|
| Source | CHANGELOG v1.8.0, unchanged through v1.12 |
| Dataset | NC voter sample, tab-delimited, 10K rows |
| Drop location | `packages/python/goldenmatch/tests/benchmarks/datasets/NCVR/ncvoter_sample_10k.txt` |
| Source URL | https://www.ncsbe.gov/results-data/voter-registration-data (full 4.3 GB extract) |
| Sampling | first 10K rows of `ncvoter_Statewide.zip` |
| Variance | Deterministic |

```bash
python scripts/run_benchmarks.py --datasets ncvr --output ncvr_results.json
```

**Reproducibility gap:** same shape as Febrl3. The runner reports F1=0
because the ground-truth mapping is corruption-based and not yet
committed as a JSON fixture (see source comment in `_measure_ncvr`).
The headline 0.9719 was measured by the gitignored
`.profile_tmp/baseline_febrl3_ncvr.py`.

---

## Environment requirements

Pin these for any benchmark run you want to publish:

| Variable | Value | Why |
|---|---|---|
| `GOLDENMATCH_AUTOCONFIG_MEMORY` | `0` | Disables the cross-run config cache at `~/.goldenmatch/autoconfig_memory.db`. Without this the controller may reuse a prior run's config and the number you measure depends on whatever you ran last. |
| `OPENAI_API_KEY` | unset | No-LLM benchmark numbers are deterministic. Setting this key enables LLM scoring and introduces vendor-side non-determinism. Pass `--with-llm` explicitly when measuring LLM-augmented numbers. |
| `GOLDENMATCH_AUTOCONFIG_INDICATOR_BUDGET` | unset (default) | `fast` skips two expensive indicators. Leave it unset to match published numbers. |

Python 3.11+. Polars, pyarrow, and `recordlinkage` (for Febrl3) must be
on the path. The benchmarks workflow uses `uv sync --all-packages`.

---

## What variance to expect

**Composite + tier F1 (no LLM)** is deterministic given fixed seeds
and a fixed dataset. Run-to-run variance comes from:

1. **Auto-config memory cache.** Set `GOLDENMATCH_AUTOCONFIG_MEMORY=0`.
2. **DQbench tier dataset version.** Pin the installed `dqbench`
   release.
3. **Floating-point reduction order** in `rapidfuzz.cdist` threading.
   Sub-0.1 pp drift on F1 is normal.

Allow +/- 1.5 pp on composite, +/- 2 pp on individual tier F1 between
clean-room runs.

**LLM-augmented runs** (`--with-llm`) are not deterministic. OpenAI
sampling temperature and model-version drift produce composite swings
of 1-3 pp. Treat the headline "LLM" numbers as a single observation,
not a re-runnable target.

---

## One-click reproduction (DQbench composite)

```bash
# Fresh checkout, fresh venv, no inherited memory cache
git clone https://github.com/benzsevern/goldenmatch
cd goldenmatch
git checkout v1.12.0      # tag where the 91.04 number landed

python -m venv .venv
. .venv/bin/activate       # Windows: .venv\Scripts\activate

pip install -U pip
pip install -e packages/python/goldenmatch
pip install dqbench

export GOLDENMATCH_AUTOCONFIG_MEMORY=0
unset OPENAI_API_KEY                # no-LLM measurement

python scripts/run_benchmarks.py --datasets dqbench \
  --output dqbench_results.json

# Expected: composite >= 90 (target was >= 75; v1.12 shipped at 91.04)
cat dqbench_results.json
```

Note that the script invokes `dqbench` via `subprocess.run` and expects
the adapter file at `.profile_tmp/goldenmatch_zeroconfig_adapter.py`.
See the gap note below.

---

## CI cadence

`.github/workflows/benchmarks.yml` runs Mondays 06:00 UTC and on
`workflow_dispatch`. Results are uploaded as artifacts
(`benchmark-results-<run_id>`, 90-day retention) and posted to the
workflow step summary. Forks without `vars.RUN_BENCHMARKS=true` and
the dataset secrets get a `::notice::` and exit 0.

See `docs/ci-lanes.md` for the full lane breakdown.

---

## Known reproducibility gaps

These are honest issues with the committed runner. Don't paper over them.

1. **DQbench adapter lives in `.profile_tmp/`, which is gitignored.**
   The runner `_run_dqbench` requires
   `.profile_tmp/goldenmatch_zeroconfig_adapter.py`. The file is short
   (about 50 lines, instantiates `GoldenMatchZeroConfigAdapter` and
   calls `goldenmatch.dedupe_df(df)`) but it is not committed today.
   A fresh checkout cannot reproduce the DQbench composite without
   recreating the adapter. The adapter source is documented in
   `packages/python/goldenmatch/CLAUDE.md` under the v1.11 and v1.12
   ship notes.

2. **DBLP-ACM ground-truth join in the runner is positional,
   not ID-based.** `_measure_dblp_acm` trusts row order and casts IDs
   to int. DBLP IDs are strings like `conf/vldb/...` so the int cast
   silently drops them, the GT set ends up empty, and F1 prints as 0
   even though the pipeline produced the correct clusters. The
   package-level `tests/benchmarks/run_leipzig.py` does the join
   correctly and is the path the 0.9641 number was originally
   measured on.

3. **Febrl3 GT is stubbed.** `_measure_febrl3` returns an empty
   ground-truth set with an explicit `# GT mapping omitted in v1 of
   this script` comment. The 0.9443 in CHANGELOG was measured by
   `.profile_tmp/baseline_febrl3_ncvr.py` (gitignored).

4. **NCVR GT is stubbed.** Same shape as Febrl3. The corruption-based
   ground-truth mapping is not committed as a fixture. `_measure_ncvr`
   comments that "v2 should pull GT from a committed JSON fixture".

5. **NCVR dataset is not redistributable from this repo.** It is the
   first 10K rows of `ncvoter_Statewide.zip` from the NC State Board
   of Elections. Public data but bandwidth-heavy; we don't mirror it.

If you need any of these gaps closed for a release-cycle measurement,
the path forward for each is small and well-scoped:

- For (1): commit the adapter into `scripts/dqbench_adapters/`.
- For (2): rewrite `_measure_dblp_acm`'s GT join to map by original
  source ID rather than positional row index.
- For (3) and (4): commit a one-time GT fixture JSON next to each
  dataset.

---

## Footnotes

The DBLP-ACM end-to-end run of `scripts/run_benchmarks.py
--datasets dblp-acm --output dblp_results.json` was executed on
2026-05-10 against the committed runner at HEAD of
`feature/benchmark-repro-doc`. The script ran without errors and
produced a results JSON. As noted in gap (2) above, the runner's
positional GT join does not match the IDs in
`DBLP-ACM_perfectMapping.csv`, so the F1 it reported was 0.0; the
0.9641 in the CHANGELOG was measured by a different (and correctly
ID-joined) harness. The verified result here is that **the runner
script executes end-to-end and emits a results file**, not that it
reproduces the published number out of the box. *Verified 2026-05-10*.
