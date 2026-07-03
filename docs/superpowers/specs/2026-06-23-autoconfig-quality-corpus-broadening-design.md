# Auto-config quality harness: corpus broadening (real benchmarks)

**Status:** Design approved 2026-06-23. Extends the auto-config quality harness
(`scripts/autoconfig_quality/`, shipped in PR #1216).

## Motivation

The harness gates the auto-config decision kernel against a committed baseline,
but its corpus is currently 3 synthetic failure-shape anchors + `dblp_acm`
(skip-when-absent). That pins the regressions we already know about, but it
cannot tell us *where the kernel underperforms on real data* — which is the only
thing that justifies adding a new lever. The kernel is already lever-rich (S1-S3,
FS-v2, the noise-aware `token_sort`->`jaro_winkler` upgrade that already closed
the NCVR address gap), so the next move is demand-driven: broaden the corpus to
real labeled benchmarks, run the F1 tier, and read the attribution split
(blocking-recall / final-recall / threshold-loss) to localize each dataset's loss
to a *lever class*. A lever then earns its place only when a dataset shows the gap
and the harness can guard the fix.

Out of scope: DQbench (deferred — needs the dqbench CLI + `~/.dqbench` data); any
new lever (this change only adds *measurement*, it changes no kernel behavior).

## Principle: reuse, never reimplement

Every dataset already has a standalone loader in the repo. This change writes no
new dataset-loading or truth-parsing logic; each registry entry calls an existing
loader and adapts its output to the harness contract. This mirrors the existing
`_dblp_acm` loader and the harness's "consume the production primitives" stance.

## The harness contract (unchanged)

A `Dataset` loader returns `tuple[pl.DataFrame, set[tuple[int,int]]] | None`:
the `set` is ground-truth pairs in **0..n-1 ROW-INDEX space** (`i < j`, canonical
`(min,max)`) over the returned DataFrame; `None` means the data is absent
(skip-when-absent — recorded as skipped, never a crash). The F1 tier dedupes the
df and scores its clusters against these pairs.

## New / changed registry entries

DBLP-ACM is already present and unchanged. Four entries are added.

| entry | kind | reused loader | native truth | row-index conversion | CI |
| --- | --- | --- | --- | --- | --- |
| `febrl3` | real | `dqbench_adapters.febrl3.load_febrl3_df_and_gt()` | `set[(rec_id_a, rec_id_b)]` | map rec_id->row via `df["id"]` | yes |
| `ncvr_synthetic` | real | `dqbench_adapters.ncvr.build_ncvr_synthetic_df_and_gt(seed=42)` | `set[(ncid, ncid_DUP)]` | map ncid->row via `df["ncid"]` | yes |
| `ncvr_real` | real | `dqbench_adapters.ncvr.build_ncvr_df_and_gt(path, seed=42)` | `set[(ncid, ncid)]` | map ncid->row via `df["ncid"]` | no (gitignored PII) |
| `historical_50k` | real | committed vendored parquet | `cluster` column (int label) | group rows by `cluster`, emit within-cluster pairs | yes |

Conversion is uniform for the pair-truth datasets: build `{str(id_value): row_index}`
from the relevant id column, map each truth pair, drop any pair whose endpoints are
missing or equal, canonicalize `(min,max)`. This is the same recipe `_dblp_acm`
already uses.

### NCVR: two separate entries (determinism)

Real NCVR (gitignored PII at
`packages/python/goldenmatch/tests/benchmarks/datasets/NCVR/ncvoter_sample_10k.txt`)
and the committable PII-free synthetic NCVR have *different data and different F1*.
A single entry that silently fell back real->synthetic would pin one number but
measure the other across environments. So they are two registry entries with
independent baselines: `ncvr_synthetic` (always present, runs in CI) and
`ncvr_real` (skip-when-absent, local-only). Both seed `42`.

### historical_50k: vendored parquet

`splink_datasets.historical_50k` is version-dependent and requires splink at
runtime; the parquet fallback path is not committed. To make the dataset
deterministic and identical in CI and locally, a one-time generator
`scripts/autoconfig_quality/vendor_historical_50k.py` pulls the dataset via splink
and writes `scripts/autoconfig_quality/vendored/historical_50k.parquet` containing
the record fields **and** the `cluster` truth column. The runtime loader reads
*only* that committed parquet (never splink), so the source is fixed.

This deliberately does **not** reuse `scripts/bench_er_headtohead/datasets.py`'s
`_historical_50k()`: that helper pulls from splink-or-an-uncommitted-path at
runtime (non-deterministic across environments) and lives in a package with no
`__init__.py`. Reading our own committed parquet is the cleaner, CI-stable choice
and is the one place "reuse, never reimplement" is set aside on purpose.

The loader splits `cluster` out as ground truth and **drops it from the DataFrame
passed to dedupe** — the truth label must never reach auto-config, or it would be
classified as an identifier / matchkey and leak the answer. (The pair-truth
datasets don't need this: their truth lives in the `id`/`ncid` column the kernel
legitimately uses, and is external to the match decision.)

Path note: the parquet goes under `scripts/autoconfig_quality/vendored/` — **not**
`datasets/`, which `.gitignore` excludes globally (line 84, runtime-downloaded PII
benchmark data). The implementer creates the `vendored/` dir and confirms
`git check-ignore` does not match it. Vendoring cost: a ~2-5 MB parquet.
Provenance: Splink's historical-50k is Wikidata-derived (CC0 / MIT-licensed),
safe to vendor.

## Per-dataset full scan

`evaluate_f1(df, gt, row_cap=...)` **already** treats `row_cap=None` as "no
truncation" (`f1.py:54-58`, deterministic `df.head(row_cap)`) and is already
unit-tested with `row_cap=None` — so f1.py needs **no change** for capping.

The only real work is selecting the cap per dataset. `Dataset` gains a
`full_scan: bool = False` field (a clean boolean, deliberately not a tri-state
`int | None` whose `None`-means-no-cap collides with `None`-means-unset).
`historical_50k` sets `full_scan=True`. In `__main__.run()`, the effective cap
passed to `evaluate_f1` is `None if d.full_scan else cli_row_cap` (the `--row-cap`
default, 20000). Capping historical_50k would both change the number and bias it
if rows are cluster-ordered, so it runs the full 50k. All other datasets fall
below 20k and run fully under the default anyway.

## historical_50k scaling (full-50k F1)

The F1/P/R are cheap at 50k: `dedupe_df` + `evaluate_clusters` + the `scored_pairs`
set (bounded by matches found, ~O(n) for a blocked dataset) fit the `ubuntu-latest`
quality_gate runner (7 GB). The blow-up risk is the *attribution*:
`f1.py:_candidate_pairs` rebuilds the entire post-blocking candidate set as a
Python set via `combinations` over every block — O(block_size^2) per block, which
at 50k can reach tens of millions of tuples and is **not** bounded by the existing
`try/except` (an OOM is not catchable).

Fix: add a scale guard to `_candidate_pairs`. Collect each block's row-id list
(cheap, O(n) total), compute the projected pair count `sum(C(len,2))`; if it
exceeds a cap (env `GOLDENMATCH_QH_ATTR_MAX_PAIRS`, default ~10M), skip the
`combinations` materialization and signal `evaluate_f1` to record
`attribution: {"skipped": "scale"}` — explicit, **not** a misleading
`blocking_recall=0`. The F1/P/R floor is always computed; the attribution is
best-effort and degrades visibly. Attribution is informational (real datasets gate
on the F1 floor only; signal/attribution drift is never a FAIL), so a `skipped`
attribution can't break the gate or churn the baseline.

The runner stays `ubuntu-latest` — the 50k dedupe + cluster-eval fits, and the
guard bounds the attribution. Whether historical_50k's real candidate count fits
under the cap is **measured on the first run** (recorded with peak RSS + wall to
confirm the ~1-3 min budget), per the repo's "measure the real shape before
designing" rule. If we later want the attribution at 50k, raise the cap on a
roomier runner — a follow-up, not a blocker for the floor.

## Bless / vendoring environment

The bless and vendor steps run locally and have dependencies the gate corpus
needs. The implementer must run them in an environment where:
- `recordlinkage` is installed (FEBRL3's loader returns `None` without it —
  blessing without it would silently skip FEBRL3 and ship a baseline with no
  FEBRL3 floor, defeating the change). `recordlinkage` is **not** a declared dep.
- `splink` is installed for the one-time `vendor_historical_50k.py` (it's the
  `bench` extra in `packages/python/goldenmatch/pyproject.toml`). Not needed at
  gate time — only to generate the committed parquet once.
The bless must run with `GOLDENMATCH_AUTOCONFIG_MEMORY=0` and native 0 (same as the
existing anchors) so the new floors match what CI computes.

## CI

The `quality_gate` job (`.github/workflows/ci.yml`) gains a `uv pip install
recordlinkage` step (FEBRL3's loader needs it; `recordlinkage` is not a declared
dep — the existing `benchmark_runner_smoke` job installs it the same way). With the
new entries, the job's effective corpus becomes: anchors + `febrl3` +
`ncvr_synthetic` + `historical_50k`; `ncvr_real` and `dblp_acm` skip-when-absent.
The full 50k dedupe adds ~1-3 min to the job (which only fires on quality_gate
path-filter hits, not every PR). The baseline is re-blessed (memory-off, native-0)
to pin the new F1 floors.

## Determinism and the F1 floors

All loaders are seeded; the vendored parquet is fixed; native 0 vs 1 is
F1-parity-identical. The pinned F1s are the harness's deterministic *memory-off*
numbers (regression floors), not the published headline numbers — as measured on
first bless: FEBRL3 0.9665, ncvr_synthetic 0.9828, historical_50k 0.4663 (the low
one is the lever-nomination finding: recall-bound, `postcode_fake` excluded as a
matchkey), correct as floors regardless of the headline. Each
dataset is blessed to whatever it deterministically measures, gated as
`current >= floor - tolerance` like the existing anchors. Signal drift on real
datasets stays informational (only F1 floors them); `planner_rung` stays WARN.

## Testing

Extend `tests/test_datasets.py`: for each new entry, assert the loader returns
`(df, gt)` with `gt` a non-empty `set[tuple[int,int]]` in row-index range when the
data is present, and `None`/skip when absent (drive the absent path by pointing at
a nonexistent path where applicable). Assert `historical_50k`'s returned df does
**not** contain the `cluster` column. No heavy dedupe in unit tests — the F1 runs
*are* the gate; unit tests cover loading + conversion only. A registry-resolves
smoke (all entries importable, names unique) rounds it out.

Extend `tests/test_f1.py` for the attribution scale guard: on a small fixture
whose blocking yields a block over an env-lowered `GOLDENMATCH_QH_ATTR_MAX_PAIRS`,
`evaluate_f1` still returns F1/P/R and records `attribution: {"skipped": "scale"}`
(no OOM, no misleading `blocking_recall=0`). This is a small fixture, not a real
benchmark.

## File structure

- Modify `scripts/autoconfig_quality/datasets.py` — add 4 loaders + registry
  entries; add the `full_scan: bool = False` field to `Dataset`.
- Modify `scripts/autoconfig_quality/f1.py` — add the attribution scale guard (see
  "historical_50k scaling"); `evaluate_f1`'s `row_cap=None` handling already
  exists and is unchanged.
- Modify `scripts/autoconfig_quality/__main__.py` — pass `None if d.full_scan else
  cli_row_cap` to `evaluate_f1`.
- Create `scripts/autoconfig_quality/vendor_historical_50k.py` — one-time parquet
  generator (run locally with splink; not run in CI).
- Create `scripts/autoconfig_quality/vendored/historical_50k.parquet` — vendored
  (under `vendored/`, not the gitignored `datasets/`).
- Modify `.github/workflows/ci.yml` — `recordlinkage` install in `quality_gate`.
- Re-bless `scripts/autoconfig_quality/baselines/scorecard.json`.
- Modify `scripts/autoconfig_quality/tests/test_datasets.py` — loader tests.
- Update `scripts/autoconfig_quality/README.md` — document the corpus + the
  attribution-localizes-levers workflow.

## What this enables

After this lands, `report` over the broadened corpus produces, per real dataset,
an F1 plus the attribution split. Reading where each dataset loses (blocking vs
scoring vs threshold) is what nominates the next lever on evidence — the original
reason the harness exists. No lever is added by this change.
