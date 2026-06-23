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
and writes `scripts/autoconfig_quality/datasets/historical_50k.parquet` containing
the record fields **and** the `cluster` truth column. The runtime loader reads
*only* that committed parquet (never splink), so the source is fixed.

The loader splits `cluster` out as ground truth and **drops it from the DataFrame
passed to dedupe** — the truth label must never reach auto-config, or it would be
classified as an identifier / matchkey and leak the answer. (The pair-truth
datasets don't need this: their truth lives in the `id`/`ncid` column the kernel
legitimately uses, and is external to the match decision.)

Vendoring cost: a ~2-5 MB parquet committed under
`scripts/autoconfig_quality/datasets/`. Provenance: Splink's historical-50k is
Wikidata-derived (CC0 / MIT-licensed datasets), safe to vendor.

## Per-dataset row cap

`Dataset` gains an optional `row_cap: int | None = <sentinel>` field. The F1 tier's
effective cap is the dataset's `row_cap` when set, else the CLI `--row-cap`
(default 20000). `historical_50k` sets `row_cap=None` = "no cap" (run the full 50k;
capping would both change the number and bias it if rows are cluster-ordered).
`evaluate_f1` is extended to treat `row_cap=None` as no truncation. All other
datasets fall below 20k and run fully under the default.

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
numbers (regression floors), not the published headline numbers — e.g. FEBRL3
lands ~0.91 memory-off vs ~0.944 published, which is correct as a floor. Each
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

## File structure

- Modify `scripts/autoconfig_quality/datasets.py` — add 4 loaders + registry
  entries; add the `row_cap` field to `Dataset`.
- Modify `scripts/autoconfig_quality/f1.py` — `evaluate_f1` honors `row_cap=None`.
- Modify `scripts/autoconfig_quality/__main__.py` — pass the per-dataset cap to
  `evaluate_f1` (dataset.row_cap else `--row-cap`).
- Create `scripts/autoconfig_quality/vendor_historical_50k.py` — one-time parquet
  generator (run locally with splink; not run in CI).
- Create `scripts/autoconfig_quality/datasets/historical_50k.parquet` — vendored.
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
