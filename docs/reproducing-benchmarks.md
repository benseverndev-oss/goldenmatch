# Reproducing GoldenMatch benchmarks

This doc maps every published GoldenMatch benchmark number back to a
committed runner, a dataset source, an environment, and the expected
output (with tolerance).

It is the canonical answer to "where does 91.04 come from and how do
I reproduce it?". Numbers come from `packages/python/goldenmatch/CHANGELOG.md`
entries for v1.8 through v1.12.

The four published headline numbers are independently reproducible from
a fresh `git clone` as of 2026-05-11 — see the verified-stamp footnotes
on each row below.

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
`benchmarks.yml` weekly workflow. Helpers live in
`scripts/dqbench_adapters/`.

---

## Published numbers and how to reproduce them

### DQbench composite 91.04 (v1.12, T1+T2+T3 weighted)

| Property | Value |
|---|---|
| Source | `packages/python/goldenmatch/CHANGELOG.md` v1.12.0 entry |
| Runner | `scripts/run_benchmarks.py --datasets dqbench` |
| Dataset | DQbench ER tier 1+2+3 (bundled with `pip install dqbench`) |
| Environment | `GOLDENMATCH_AUTOCONFIG_MEMORY=0`, no `OPENAI_API_KEY` (no-LLM run) |
| Adapter | `scripts/dqbench_adapters/goldenmatch_zeroconfig.py` |
| Expected | composite >= 90, T1 >= 88.9%, T2 >= 97.5%, T3 >= 85.5% |
| Tolerance | composite +/- 1.5 pp run-to-run; tier F1 +/- 2 pp |
| Variance | Deterministic given fixed seeds. No LLM means no API non-determinism. |
| Last verified | composite=91.04 — *verified 2026-05-11* |

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
| Runner | `scripts/run_benchmarks.py --datasets dblp-acm` |
| Helper | `scripts/dqbench_adapters/leipzig_eval.py` (mirrors `tests/benchmarks/run_leipzig.py`) |
| API call | `goldenmatch.match_df(dblp, acm)` (cross-source match, **not** `dedupe_df` on a concatenated frame) |
| Dataset | Leipzig DBLP-ACM, `DBLP2.csv` + `ACM.csv` + `DBLP-ACM_perfectMapping.csv` |
| Encoding | `utf8-lossy` |
| Drop location | `packages/python/goldenmatch/tests/benchmarks/datasets/DBLP-ACM/` |
| Source URL | https://dbs.uni-leipzig.de/de/research/projects/object_matching/benchmark_datasets_for_entity_resolution |
| Variance | Deterministic |
| Last verified | F1=0.9641 (P=0.9691, R=0.9591) — *verified 2026-05-11* |

The dataset is gitignored. After downloading the Leipzig zip, drop the
three CSVs into the path above.

```bash
python scripts/run_benchmarks.py --datasets dblp-acm \
  --output dblp_results.json
```

The runner emits cross-source pairs via `match_df` and joins them to
`idDBLP`/`idACM` from `DBLP-ACM_perfectMapping.csv`. (The pre-PR
positional join silently dropped every pair because DBLP IDs are
non-numeric strings like `conf/vldb/...` and the `int()` cast wiped
them; the new helper joins on string IDs.)

### Febrl3 F1 = 0.9443 (v1.8 through v1.12, flat)

| Property | Value |
|---|---|
| Source | CHANGELOG v1.8.0, unchanged through v1.12 |
| Runner | `scripts/run_benchmarks.py --datasets febrl3` |
| Helper | `scripts/dqbench_adapters/febrl3.py` |
| Dataset | Synthetic, bundled with `pip install recordlinkage` via `recordlinkage.datasets.load_febrl3` |
| Environment | `GOLDENMATCH_AUTOCONFIG_MEMORY=0` |
| Variance | Deterministic (synthetic dataset is fixed) |
| Last verified | F1=0.9443 (P=0.9865, R=0.9056) — *verified 2026-05-11* |

```bash
pip install recordlinkage
python scripts/run_benchmarks.py --datasets febrl3 --output febrl3_results.json
```

The helper translates emitted positional pairs back to rec_id strings
(`rec-XXX-org`/`rec-XXX-dup-N`) before set-comparing to the GT pairs
returned by `recordlinkage.datasets.load_febrl3(return_links=True)`.

### NCVR F1 = 0.9719 (v1.8 through v1.12, flat)

| Property | Value |
|---|---|
| Source | CHANGELOG v1.8.0, unchanged through v1.12 |
| Runner | `scripts/run_benchmarks.py --datasets ncvr` |
| Helper | `scripts/dqbench_adapters/ncvr.py` |
| Dataset | NC voter sample, tab-delimited, 10K rows |
| Drop location | `packages/python/goldenmatch/tests/benchmarks/datasets/NCVR/ncvoter_sample_10k.txt` |
| Source URL | https://www.ncsbe.gov/results-data/voter-registration-data (full 4.3 GB extract) |
| Sampling | first 10K rows of `ncvoter_Statewide.zip` |
| GT construction | corruption-based, `seed=42`, N=5000 base + 2500 corrupted (mirrors `tests/test_autoconfig_benchmarks.py::test_autoconfig_ncvr_meets_target`) |
| Variance | Deterministic given pinned seed |
| Last verified | F1=0.9719 (P=0.9820, R=0.9620) — *verified 2026-05-11* |

```bash
python scripts/run_benchmarks.py --datasets ncvr --output ncvr_results.json
```

NCVR has no canonical pair ground truth, so the helper builds it
synthetically: sample N records, corrupt half (typo/swap/drop/abbrev/case)
and emit `(orig_ncid, orig_ncid + "_DUP")` pairs. Seed 42 makes the GT
deterministic. This is the same construction used by the committed
benchmark test the v1.8 number was measured against.

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

The runner invokes `dqbench` via `subprocess.run` with
`--adapter scripts/dqbench_adapters/goldenmatch_zeroconfig.py`.

---

## CI cadence

`.github/workflows/benchmarks.yml` runs Mondays 06:00 UTC and on
`workflow_dispatch`. Results are uploaded as artifacts
(`benchmark-results-<run_id>`, 90-day retention) and posted to the
workflow step summary. Forks without `vars.RUN_BENCHMARKS=true` and
the dataset secrets get a `::notice::` and exit 0.

`.github/workflows/ci.yml` also runs a no-dataset smoke lane on every
PR (`benchmark_runner_smoke`) that imports the runner and helpers and
runs `--help` to catch packaging regressions. The full real-dataset
runs stay in `benchmarks.yml` so PR CI stays under 10 minutes.

See `docs/ci-lanes.md` for the full lane breakdown.

---

## Known reproducibility gaps

The original five gaps documented in PR #143 are all closed as of
2026-05-11. The DQbench adapter is committed at
`scripts/dqbench_adapters/goldenmatch_zeroconfig.py`; the DBLP-ACM
runner joins on source IDs via `dqbench_adapters/leipzig_eval.py`;
Febrl3 and NCVR have working GT helpers under `dqbench_adapters/`.

What still requires external work (not bugs, just dataset realities):

1. **NCVR dataset is not redistributable from this repo.** It is the
   first 10K rows of `ncvoter_Statewide.zip` from the NC State Board
   of Elections. Public data but bandwidth-heavy; we don't mirror it.
   Drop the sample at
   `packages/python/goldenmatch/tests/benchmarks/datasets/NCVR/ncvoter_sample_10k.txt`
   before running `--datasets ncvr`.
2. **Leipzig DBLP-ACM mirror availability.** The canonical link at
   `dbs.uni-leipzig.de` has been intermittently 404-ing in 2026. If
   the link is dead, the Magellan benchmark mirror at
   `sites.google.com/site/anhaidgroup/projects/data` carries identical
   CSVs.
3. **DQbench dataset bundling.** The `dqbench` PyPI package ships its
   own tier datasets. Pinning the `dqbench` version (currently any
   2024+ release) is enough; we don't re-publish the tiers.

---

## Footnotes

The four end-to-end runs documented above were executed on 2026-05-11
against `feature/benchmark-provenance-fix`:

- `python scripts/run_benchmarks.py --datasets dqbench` -> composite=91.04
- `python scripts/run_benchmarks.py --datasets dblp-acm` -> F1=0.9641
  (P=0.9691, R=0.9591)
- `python scripts/run_benchmarks.py --datasets febrl3` -> F1=0.9443
  (P=0.9865, R=0.9056)
- `python scripts/run_benchmarks.py --datasets ncvr` -> F1=0.9719
  (P=0.9820, R=0.9620)

All four match the v1.12 CHANGELOG headline numbers within published
tolerance. *Verified 2026-05-11*.
