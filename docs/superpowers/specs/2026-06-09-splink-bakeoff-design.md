# Splink (hand-rolled) vs GoldenMatch (autoconfig) bake-off -- design

**Date:** 2026-06-09
**Status:** Approved design, pre-plan
**Scope:** A reproducible accuracy + performance bake-off across the ER benchmark
datasets, pitting expert hand-rolled Splink against GoldenMatch's zero-tuning
auto-config (two GM modes). Extends `scripts/bench_er_headtohead/`.

## Problem / goal

We have a head-to-head accuracy panel (`run_panel.py`, used for the #823 numbers:
GM probabilistic auto-config beats Splink). What we do NOT have in one place is a
fair **autoconfig-vs-hand-rolled** comparison that records BOTH accuracy AND
performance (wall, peak RSS, throughput) for every engine on every benchmark
dataset. The existing perf runner (`run_goldenmatch.py`) deliberately uses an
EXPLICIT hand-built GM config (bucket+native+weighted) -- it measures GM-hand-built
vs Splink-hand-built raw engine speed, NOT GM autoconfig. This bake-off adds the
autoconfig comparison and unifies accuracy + perf into one artifact.

Goal: a committed bake-off table -- per benchmark dataset, for each of three engines,
pairwise P/R/F1 + B3 F1 AND dedupe wall (s) + peak RSS (MB) + throughput -- run on
one Linux CI runner for fairness, with honest framing.

## Engines (three columns per dataset)

1. **GM zero-config** -- `dedupe_df(df)` with NO config (the controller auto-picks
   weighted/exact/probabilistic). The product headline: "zero-config vs hand-tuned."
   Honestly surfaces the controller's overhead/RED-refuse behavior on off-distribution
   data (a real autoconfig cost, not hidden).
2. **GM probabilistic auto-config** -- `auto_configure_probabilistic_df(df)` ->
   `dedupe_df(df, config=...)`. Like-for-like vs Splink (both Fellegi-Sunter),
   zero-tuning. Directly validates the #823 win under the unified harness.
3. **Splink hand-rolled** -- the existing per-dataset expert configs in
   `run_splink.py::_SETTINGS_BY_DATASET` (compound blocking rules + JaroWinkler /
   DamerauLevenshtein / ExactMatch comparisons + EM training rules). Reused as-is
   (already genuinely hand-tuned, not a strawman). Splink honestly **skips dblp_acm**
   (bibliographic isn't its tuned domain) -- recorded as "skips," never a zero.

## Datasets

All benchmark datasets via `datasets.py::load_dataset`: **historical_50k** (50K,
Splink's home turf -- the scale anchor + headline), **febrl3**, **synthetic_person**,
**dblp_acm**. No synthetic 1M scale curve in this pass (scoped to the real benchmark
datasets; an optional throughput curve is a documented follow-up).

## Metrics (per engine x dataset)

- **Accuracy:** pairwise precision / recall / F1 + B3 (cluster) F1, via `evaluate.py`
  against `attribution.truth_to_pairs(truth)` and the per-record predicted cluster
  assignment. Identical evaluator for all three engines.
- **Performance:** `dedupe_wall_seconds`, `peak_rss_mb` (Linux `resource` RU_MAXRSS),
  throughput (`scored_pairs/sec` and `rows/sec`). Each engine self-times in its OWN
  subprocess so peak RSS is that engine's true high-water mark.

## Design

### 1. GM runner modes -- `run_goldenmatch.py`
Add `--mode {hand_built, zeroconfig, probabilistic}` (default `hand_built` so
`orchestrate.py`'s existing perf bench is byte-unchanged):
- `hand_built`: today's explicit bucket+native+weighted config (unchanged).
- `zeroconfig`: `dedupe_df(df)` -- no config. Captures the controller path incl. its
  overhead. **MUST catch `ControllerNotConfidentError` SPECIFICALLY** and write
  `status=refused` + the reason to the result JSON, then **exit 0 (do NOT re-raise)** so
  the orchestrator reads it as a clean data point, not a subprocess failure. (The runner
  currently does `except BaseException ... raise`; the refuse case must be handled before
  that re-raise.)
- `probabilistic`: `cfg = auto_configure_probabilistic_df(df)`; **then walk `cfg`'s
  matchkeys and force `rerank=False` on any weighted matchkey** (hard guard, not "if"),
  so CI never attempts a HuggingFace cross-encoder download; `dedupe_df(df, config=cfg)`.

**CORRECTNESS -- record_id remap (REQUIRED; the existing `--pred-out` is wrong for the
real datasets):** today `run_goldenmatch.py`'s `--pred-out` writes `record_id` as the
int `__row_id__` (cluster member positions), relying on "record_id == row index" -- which
holds ONLY for `synthetic_person`. `historical_50k` / `dblp_acm` / `febrl3` carry STRING
record_ids (Wikidata `Q...`, `dblp:123`/`acm:456`, `rec-123-org`) that are NOT row
positions, so the unremapped pred never joins the truth table and accuracy reads ~0. Fix
(copy `run_panel.py:83-108`): for the **bake-off modes only (`zeroconfig` /
`probabilistic`)**, the runner reads the input df's real `record_id` column, remaps each
cluster member `__row_id__` -> `df["record_id"][member]`, and writes `record_id` as a
**string-typed** column; the `run_bakeoff.py` orchestrator casts its truth to Utf8 (§2)
so that lane's preds + truth share one string key space.

**`hand_built` mode is left BYTE-UNCHANGED** -- it keeps today's int64 `__row_id__`
`--pred-out` write (`run_goldenmatch.py:138-158`). This is REQUIRED for back-compat:
`orchestrate.py`'s synthetic fixture (`generate_fixture.py`) carries an int64 `record_id`
column AND writes int64 truth with no Utf8 cast, so a string remap there would mismatch
the join and zero out the synthetic F1. The remap is gated on MODE, NOT on record_id
column presence (the synthetic fixture HAS the column). Net: bake-off lane = string key
space (truth cast to Utf8); orchestrate lane = int64 key space (unchanged). Each lane is
self-consistent: pred dtype matches truth dtype within the lane.

### 2. Orchestrator -- new `run_bakeoff.py` (additive; does NOT disturb `run_panel.py`)
The existing `run_panel.py` (accuracy-only, GM inline) stays for the CI panel lane.
`run_bakeoff.py` is the unified accuracy+perf orchestrator:
- For each dataset: `load_dataset` -> write `records` to a temp parquet **preserving the
  dataset's REAL `record_id` column** (string for the non-synthetic datasets -- do NOT
  overwrite it with a row index), and write `truth` to a temp parquet with `record_id`
  **cast to Utf8** so it joins the string-keyed preds (mirror `run_panel.py:358`).
- Run THREE subprocesses (each self-times, each `--pred-out`):
  `run_goldenmatch.py --mode zeroconfig`, `run_goldenmatch.py --mode probabilistic`,
  `run_splink.py` (its dataset settings). A per-engine timeout records `timeout`/`error`,
  never hangs the bake-off. Splink's pred output is ALREADY real-record_id-keyed
  (`run_splink.py:255-262` projects `record_id, cluster_id AS pred_cluster_id`), so the
  orchestrator reuses `run_panel.py::_run_splink`'s eval flow verbatim -- no Splink-side
  adaptation. GM preds are corrected by the §1 remap to the same string key space.
- Compute accuracy from each `--pred-out` (joined to the Utf8 truth) via `evaluate.py`;
  collect perf from each engine's result JSON. All three engines + truth share ONE
  string `record_id` key space, so the join is apples-to-apples.
- Emit `bakeoff.md` + `bakeoff.json`: one row per (dataset, engine) with accuracy +
  perf columns, plus a per-dataset GM-vs-Splink delta (wall ratio, RSS ratio, F1 delta).
- `--require-native` defaults on for the GM runs (a silent pure-Python fallback would
  make the perf comparison a lie -- mirror `run_goldenmatch.py`'s existing guard);
  CI installs/builds the native runtime.

### 3. CI lane -- `bench-probabilistic.yml`
Add a `bake-off` job (`workflow_dispatch`, `runs-on: large-new-64GB` per the bench
default) that installs `goldenmatch[bench]` + builds the native runtime + fetches the
Leipzig DBLP-ACM dataset (as the panel lane does), runs `run_bakeoff.py`, and uploads
`bakeoff.md` / `bakeoff.json` as artifacts. Does NOT gate CI (it's a measurement, not
a test).

### 4. Recording
Commit the produced `bakeoff.md` results to `docs/benchmarks/2026-06-09-splink-bakeoff.md`
(create `docs/benchmarks/` if absent) and link it from
`context-network/architecture/fellegi-sunter-splink-parity.md` and
`docs-site/reference/vendor-comparison.mdx`. The committed doc carries the run's
runner + commit SHA for provenance.

## Fairness + honest framing (recorded with the numbers)

- Same Linux runner, same datasets, same evaluator. Single run per cell (perf has
  runner variance -- noted; accuracy is deterministic).
- Pairwise F1 under one shared evaluator; the cited ~0.97 Splink historical_50k figure
  is a cluster-level metric (preserve the caveat already in the docs).
- Splink's config is genuinely hand-tuned per dataset; GM gets ZERO tuning (the point).
- GM zero-config's controller overhead / RED-refuse is reported, not hidden -- if
  zero-config refuses or is slower than hand-built/Splink, that is the honest result.
- Splink skipping dblp_acm is recorded as "skips," not scored 0.

## Scope / out of scope

- **In:** `--mode` on `run_goldenmatch.py` (+ zeroconfig/probabilistic paths);
  `run_bakeoff.py`; the `bake-off` CI lane; the committed results doc + doc links;
  tests for the new runner modes + the orchestrator's table assembly (no live CI).
- **Out:** synthetic 1M+ throughput curve (follow-up); re-tuning the Splink configs
  (reuse as-is); NCVR adapter (ground-truth synthesis is separate); changing the
  existing `run_panel.py` accuracy lane or `orchestrate.py`.

## Testing

- `run_goldenmatch.py --mode zeroconfig` / `--mode probabilistic` on a tiny fixture
  produce a valid result JSON + a `--pred-out` parquet (no live CI; small df).
- `run_bakeoff.py` table assembly: given stub per-engine result + pred files, it emits
  a well-formed `bakeoff.json`/`.md` with the right columns + the delta rows; a missing
  engine result becomes a `skips`/`error` cell, not a crash.
- A `--mode probabilistic` smoke that the emitted config routes through the probabilistic
  path (consumed end-to-end on a small df).
- Existing `run_panel.py` / `orchestrate.py` / `run_splink.py` behavior unchanged
  (default `--mode hand_built`).

## Affected files (anticipated)

- Modified: `scripts/bench_er_headtohead/run_goldenmatch.py` (`--mode`); maybe
  `evaluate.py` (reuse, no change expected); `.github/workflows/bench-probabilistic.yml`
  (bake-off lane); `context-network/architecture/fellegi-sunter-splink-parity.md` +
  `docs-site/reference/vendor-comparison.mdx` (link the results).
- New: `scripts/bench_er_headtohead/run_bakeoff.py`; tests under
  `packages/python/goldenmatch/tests/` (or `scripts/bench_er_headtohead/`);
  `docs/benchmarks/2026-06-09-splink-bakeoff.md` (results, committed after the CI run).
