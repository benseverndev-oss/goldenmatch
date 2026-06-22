# Auto-config quality harness — design

- **Date:** 2026-06-22
- **Branch:** `feat/autoconfig-quality-harness` (off `main`)
- **Context:** The auto-config decision logic is now a single source of truth — the shared
  pyo3-free `goldenmatch-autoconfig-core` kernel (planner / classifier / extrapolation /
  thresholds), default-on across Python / native / TS-wasm (`goldenmatch-native` 0.1.11 on
  PyPI). We can now *iterate* on that decision logic; this harness makes the quality impact of
  an iteration measurable.

## Problem

Iterating on the auto-config kernel (a threshold, a classification heuristic, the blocking-key
selector) currently means discovering quality regressions **one at a time, after the fact**,
through ~38 scattered `test_autoconfig_*.py` files that each pin one behavior on a small
fixture. The S1–S3 arc demonstrated the cost vividly: four separate regressions (email-floor
demotion of shared emails, phone reclassification, the zip5 blocking-coupling 5,800× pair
explosion, S1 routing) each surfaced on a *different* CI cycle, localized only by hand.

There is **no single harness** that runs auto-config across a corpus of datasets and emits a
comparative quality scorecard you can diff between two kernel versions. The reusable primitives
exist and are scattered: `evaluate_clusters` (F1), `measure_blocking_profile` (blocking cost),
`profile_columns` / `build_blocking` / `build_matchkeys` / `dedupe_df` (the decision path),
`make_healthcare_df` and the multisource CRM fixture (failure shapes), real labeled datasets
(FEBRL3 / DBLP-ACM / NCVR / historical_50k / DQbench tiers). What's missing is the **unifying
runner + scorecard + baseline-diff + gate** that closes the iteration loop.

This harness answers exactly one question: *is the auto-config's decision quality good, and did
my change move it?* It is **not** a performance bench (`bench.py` / `bench-zero-config`), **not**
cross-surface byte-parity (the golden vectors), and **not** an engine comparison
(`bench_er_headtohead`).

## Architecture

A focused package at **repo-root `scripts/autoconfig_quality/`** — the same `scripts/` tree as the
existing autoconfig tooling (`gen_autoconfig_golden.py`, `bench_autoconfig_sample_quality.py`,
`bench_er_headtohead/`), NOT the package-level `packages/python/goldenmatch/scripts/`. It imports
the `goldenmatch` package via the installed dist / `PYTHONPATH` (as the other repo-root scripts
do). Two anchor generators live in *other* trees and are imported explicitly: `make_healthcare_df`
is at `packages/python/goldenmatch/scripts/repro_issue_715.py` (add that dir to `sys.path`, as
`test_autoconfig_blocking_cost_715.py` already does); the shared-email `_crm_df` and the
`gen_labeled` person-match generator currently live inside test modules and must be **extracted to
an importable location** (a small refactor — one definition, no copy) so both the tests and this
harness share them.

Six small single-purpose units behind one CLI:

```
scripts/autoconfig_quality/
├── __main__.py        # CLI: report / gate / bless, filters, --fast-only, --native, --row-cap
├── datasets.py        # registry: each Dataset = {name, kind, loader, expected?}
├── signals.py         # FAST tier: config-quality signals (no full dedupe)
├── f1.py              # SLOW tier: full dedupe -> evaluate_clusters -> F1/P/R
├── scorecard.py       # build + serialize the per-dataset record (JSON)
├── diff.py            # current vs baseline -> delta table + gate verdict
└── baselines/
    └── scorecard.json # committed baseline (the bless target)
```

**Key boundary — consume, never reimplement.** The harness calls the existing decision
primitives (`profile_columns`, `build_blocking`, `build_matchkeys`, `measure_blocking_profile`,
`dedupe_df`, `evaluate_clusters`, `apply_planner_rules`) and never reimplements decision logic.
It therefore measures the *real* auto-config — the unified core kernel is exercised exactly as
production runs it, including the native dispatch.

Each unit has one job and a narrow interface: `datasets.py` yields `Dataset` records;
`signals.py` maps a df → a fast-signal dict; `f1.py` maps a (df, ground_truth) → an F1 dict;
`scorecard.py` assembles records → JSON; `diff.py` maps (current, baseline) → a delta table +
verdict. They can each be understood and tested independently.

## The dataset registry (`datasets.py`)

Each `Dataset` declares `loader() -> (df, ground_truth) | None` and a `kind`:

- **`real`** — FEBRL3, DBLP-ACM, NCVR, historical_50k, DQbench tiers. Loaded from local paths
  (`tests/benchmarks/datasets/…`, `~/.dqbench/datasets/…`). The loader returns `None` when the
  data is absent; the harness records the dataset as `skipped` with a reason and proceeds. Each
  carries its native ground-truth labels (match pairs / cluster ids). These drive the **F1 tier**
  and are **diffed informationally** in the fast tier (no single "correct" config to pin).
- **`anchor`** — committed synthetic generators that reproduce the failure shapes this harness
  exists to catch: a **sparse-zip healthcare** shape (`make_healthcare_df`, the zip5 blocking-
  coupling / sampling-artifact case), a **shared-email CRM** shape (the multisource demote-phone /
  keep-shared-email case), and a **person-match** shape (`gen_labeled`, the synthetic F1 floor).
  Anchors are deterministic (fixed seed), always available (zero setup), and each carries an
  `expected` block of **pinned fast-signal values** — the hard-gated regression net.

The corpus is **real-primary** (the representative quality signal) with **synthetic anchors** as
the always-on, deterministic regression net. Anchor generators are lifted from / shared with the
existing fixtures (`scripts/repro_issue_715.py`, the multisource test fixture) so there is one
definition of each shape, not a copy.

## The two metric tiers

### Fast tier — config quality, no dedupe (`signals.py`)

Runs the decision path on a sample (no full pipeline) and records, per dataset:

| Signal | Source | Regression it localizes |
|---|---|---|
| `classification` — `{col: col_type}` | `profile_columns` | zip5→identifier sampling-artifact mislabel; S2a phone reclassification |
| `exact_matchkeys` — columns backing exact keys | `build_matchkeys` | S3 email-floor demotion of shared emails |
| `blocking_fields` — columns in the chosen key/passes | `build_blocking` | the blocking-coupling bug (zip5 dropped from the compound) |
| `blocking_cost` — candidate pairs, max block, p99, reduction ratio | `measure_blocking_profile` | the 1,529→8.9M candidate-pair explosion |
| `planner_rung` — backend + rule_name | `apply_planner_rules` | S1 under-provisioning (simple vs chunked) |

`apply_planner_rules(profile, runtime, n_rows_full, rules, context=None)` takes an assembled
`ComplexityProfile` + `RuntimeProfile`, not a bare df — so the `planner_rung` signal assembles
them the same way the controller does: a `BlockingProfile` from `measure_blocking_profile`, a
`RuntimeProfile` from `capture_runtime_profile()`, wrapped in a minimal `ComplexityProfile`. The
plan pins this assembly.

Deterministic and fast (seconds per dataset — no full pipeline, only profiling + blocking +
matchkey selection, NOT the controller's iterative sample-dedupes). For **anchors**, each signal
is checked against its pinned `expected` (exact). For **real datasets**, signals are recorded and
diffed informationally.

### Slow tier — ground-truth accuracy, full dedupe (`f1.py`)

Per **real** dataset (row-capped for tractability): `dedupe_df(df)` → cluster assignments →
`evaluate_clusters(clusters, ground_truth)` → an `EvalResult`; the scorecard reads
`.summary()` (or `.f1` / `.precision` / `.recall`) for **F1 / precision / recall**. Note
`ground_truth` is a `set[tuple]` of matching record pairs, not a dict.

Plus the **blocking-recall vs threshold-loss attribution** so an F1 drop is localized to
"blocking lost candidates" vs "scoring threshold too strict." This reuses
`scripts/bench_er_headtohead/attribution.py::attribution(gt_pairs, candidate_pairs,
emitted_pairs)` — note it takes **pair sets**, not clusters: `emitted_pairs` comes from the
dedupe result (`scored_pairs` above threshold), but **`candidate_pairs` (post-blocking,
pre-scoring) is NOT on `DedupeResult`** — the harness must source it separately from the blocking
output (run `build_blocks` / `measure_blocking_profile`'s key on the capped df to materialize the
candidate set). The plan must budget that wiring.

Anchors may optionally carry an F1 floor too (the `gen_labeled` person-match anchor has labels).
`--fast-only` skips this tier for the tight iterate loop.

**The split:** the fast tier is the always-on, deterministic, anchor-pinned **regression net**;
the F1 tier is the slower, real-data **ground-truth confirmation**. You iterate against fast,
confirm against F1.

## Scorecard, baseline-diff, gate & bless

### Scorecard (`scorecard.py`)

One JSON artifact — per-dataset records under a metadata header:

```json
{
  "meta": {"native_version": "0.1.11", "git_sha": "<sha>",
           "datasets_run": ["anchor_sparse_zip", "febrl3", ...],
           "datasets_skipped": {"ncvr": "absent"}},
  "datasets": {
    "anchor_sparse_zip": {
      "kind": "anchor",
      "signals": {"classification": {"zip5": "zip"}, "blocking_fields": ["zip5","last_name","first_name"],
                  "blocking_cost": {"candidate_pairs": 1529, "max_block": 4},
                  "exact_matchkeys": [...], "planner_rung": "..."}
    },
    "febrl3": {"kind": "real",
               "signals": {...},
               "f1": {"f1": 0.991, "precision": 0.99, "recall": 0.99,
                      "attribution": {"blocking_recall": 0.998, "threshold_loss": 0.007}}}
  }
}
```

Provenance is `git_sha` + `native_version`, stamped from the environment — **no wall-clock
timestamp / RNG in the artifact**, so re-runs on the same code are byte-stable and cleanly
diffable. Float metrics are rounded to a fixed precision so trivial ULP noise doesn't churn the
diff.

### Diff (`diff.py`)

Joins current vs `baselines/scorecard.json` and prints a human delta table:

```
anchor_sparse_zip   candidate_pairs  1,529 → 8,931,083   ✗ (anchor-pinned)
                    zip5 col_type    identifier → zip    ✗ (anchor-pinned)
febrl3              blocking_fields  [..,zip] → [..]     ⚠ changed (real → informational)
febrl3              f1               0.991 → 0.991       ·
dblp_acm            f1               0.879 → 0.864       ✗ (below floor−tol)
```

Every signal on an **anchor** is pinned, so any change renders `✗` (hard FAIL). The `⚠ changed`
marker is reserved for **real-dataset** signal drift (informational — real datasets have no single
"correct" config to pin, only the F1 floor gates them).

### Gate verdict (two rules)

- **Anchors → exact.** Any pinned signal that changed = hard FAIL (an anchor encodes known-correct
  config; a change is a regression or an intended redesign that must be blessed).
- **Real datasets → floor + tolerance.** F1 must stay ≥ `baseline_f1 − tolerance` (default 0.01);
  within-band moves pass.
- **Skipped** datasets (loader returned `None`) are **neutral** — CI without the licensed data
  still runs the anchor gate.
- **Error** verdicts: an error on an **anchor** (signal extraction threw) is **FAIL** — anchors
  must always run. An error on a **real** dataset's signal or F1 extraction is **neutral** (a
  real-data flake or shape the harness can't profile must not block a kernel PR) — it's recorded
  as `error: <msg>` and reported, but does not fail the gate.

### Bless

`python -m autoconfig_quality bless` overwrites `baselines/scorecard.json` with the current run.
This is the deliberate "I accept this change" step: when a kernel change is intentional and
correct, you re-bless, and **the diff to the committed baseline file in the same PR is the
reviewable record of exactly what config behavior changed** — turning an opaque threshold tweak
into a legible behavioral diff.

## CLI & where it runs (`__main__.py`)

- `report` (default) — run the corpus, print the diff vs baseline, write the current scorecard to
  a temp path. Read-only; the **iterate loop**.
- `gate` — same run; exits non-zero on any anchor change or real F1 below floor−tolerance. The
  **CI/commit gate**.
- `bless` — overwrite the baseline. The **accept-intended-change** step.
- Flags: `--fast-only` (skip the F1 tier — the seconds-fast loop), `--datasets a,b` (filter),
  `--native 0|1|auto` (run against pure-Python or the native kernel — so the harness doubles as a
  **config-decision** native-parity check, complementing the golden vectors), `--row-cap N` (F1
  tractability).

**Where it runs:**
- **Local (primary)** — where the real datasets live. `--fast-only` for the tight loop; full
  `report` before a kernel PR.
- **CI** — a `quality-gate` job runs `gate --fast-only` always (anchors are committed,
  deterministic, zero-setup → an always-on net that would have caught this session's bugs in one
  run). The F1 tier runs in CI only for datasets that resolve (small committable ones like
  DBLP-ACM; NCVR / DQbench skip gracefully). Not a heavy paid-runner bench: the fast tier is
  seconds, capped F1 is minutes.

## Error handling

- A dataset loader that fails / finds no data → recorded `skipped` with a reason; never crashes
  the run (one missing dataset can't break the harness).
- A fast-signal extraction that throws on one dataset → that dataset's signals are `error: <msg>`,
  the rest proceed.
- Gate treats `skipped` as neutral, `error` on an anchor as FAIL.

## Testing

- Unit-test each unit in isolation: `signals.py` against a tiny hand-built df with a known
  classification/blocking outcome; `f1.py` against a 2-cluster toy with known pairs;
  `diff.py`/`scorecard.py` against fixed dicts (verdict logic, rounding, skipped/error handling).
- A smoke test that runs the full harness over the anchors (always available) and asserts the
  gate passes on the committed baseline — i.e. the harness gates *itself*.
- The harness is the test for *auto-config quality*; its own units get ordinary unit tests.

## Non-goals (YAGNI)

- Not a performance / wall-clock bench (`bench.py`, `bench-zero-config`).
- Not cross-surface byte-parity (the golden-vector harness) — though `--native` adds a
  config-decision parity check as a cheap bonus.
- Not a Splink / multi-engine comparison (`bench_er_headtohead`).
- No web UI, no historical trend storage — the committed baseline JSON *is* the history (its git
  log is the trend).

## Reuse inventory (nothing reinvented)

| Need | Existing primitive | Notes |
|---|---|---|
| F1 / P/R | `evaluate_clusters(clusters, ground_truth)` | `ground_truth` is `set[tuple]`; returns `EvalResult`, read `.summary()` / `.f1` |
| blocking cost | `measure_blocking_profile(df, cfg)` | returns a `BlockingProfile` |
| decision path | `profile_columns`, `build_blocking`, `build_matchkeys`, `apply_planner_rules` | planner needs assembled `ComplexityProfile`+`RuntimeProfile` (see fast tier) |
| full dedupe | `dedupe_df` | `DedupeResult` has `clusters` + `scored_pairs`; NO raw candidate set |
| F1 attribution | `scripts/bench_er_headtohead/attribution.py::attribution(gt_pairs, candidate_pairs, emitted_pairs)` | takes pair sets; candidate_pairs sourced from blocking separately |
| sparse-zip anchor | `packages/python/goldenmatch/scripts/repro_issue_715.py::make_healthcare_df` | `(n, seed=715, zip_present=…, rich_surnames=…)`; import via sys.path |
| shared-email anchor | `_crm_df` in `tests/test_autoconfig_multisource.py` | module-level fn — **extract to an importable shared location** |
| person-match anchor | `gen_labeled(n_entities=400, seed=7) -> (df, gt_pairs)` in `tests/test_quality_gate.py` | also **extract to a shared location** |
| real dataset loaders | partly in `test_autoconfig_benchmarks.py` + the DQbench tests | |
