# Auto-config quality harness

A one-run scorecard for the auto-config **decision kernel**
(`goldenmatch-autoconfig-core` + its Python/blocking surfaces). It exists so a
kernel change's quality impact is measurable in a single command instead of being
discovered one regression at a time (the S1–S3 levers each shipped a regression
that a corpus-wide gate would have caught immediately).

## What it measures

Two tiers, per dataset:

- **Fast (config signals, no dedupe):** classification, exact matchkeys, blocking
  fields, blocking cost (`candidate_pairs`, `n_blocks`, `max_block`, `p99`,
  `reduction_ratio`), and the planner rung. Seconds across the whole corpus.
- **Slow (F1):** full dedupe → `evaluate_clusters` → F1 / precision / recall plus
  attribution (`blocking_recall`, `final_recall`, `threshold_loss`). Only runs for
  datasets that carry ground truth.

## Corpus

- **Anchors** (`anchor_*`, always present, deterministic) pin specific
  failure-shapes this harness was built to defend:
  - `anchor_sparse_zip` — 30k healthcare rows; `zip5` must stay classified `zip`
    (not `identifier`) and must NOT blow the compound blocking cost up
    (`candidate_pairs` stays ~1.5k, the blocking-decouple fix).
  - `anchor_shared_email` — shared-email CRM; `email` must survive as an exact
    matchkey while `phone` is demoted (the per-type matchkey floors).
  - `anchor_person_match` — 400 seeded entities with ground truth; carries an F1
    floor.
- **Real labeled datasets** reuse the repo's existing benchmark loaders and convert
  their native truth (rec_id / ncid string pairs, or a `cluster` label column) into
  the row-index pairs the F1 tier expects:
  - `febrl3` — recordlinkage-bundled (~5k rows); runs in CI.
  - `ncvr_synthetic` — PII-free NCVR-shaped, seed 42 (~15k rows); runs in CI. Its
    F1 is its OWN floor, never the real-data number.
  - `ncvr_real` — the gitignored NC voter sample; local-only, skip-when-absent.
  - `historical_50k` — Splink's Wikidata historical-people set, vendored as a
    committed parquet under `vendored/` and run at full 50k (`full_scan=True`); the
    `cluster` truth column is dropped before dedupe so the kernel can't see it.
  - `dblp_acm` — Leipzig bibliographic ER; gitignored, local-only.

  Skip-when-absent is uniform: a loader returns `None` when its data isn't on disk,
  so the gate stays green in CI while still running the dataset wherever it exists.

## Reading the attribution to nominate a lever

The F1 tier records, per dataset, an attribution split: `blocking_recall` (did
blocking surface the true pair at all), `final_recall` (did it survive scoring),
and `threshold_loss` (lost at the cut). That localizes a dataset's F1 loss to a
*lever class* — a low `blocking_recall` points at the blocking levers, a gap
between blocking and final recall points at the scorer/threshold levers. This is
what turns "F1 is low here" into "this specific decision is the culprit", which is
how the corpus nominates the next lever on evidence rather than guesswork. On a
dataset whose candidate set is too large to materialize, attribution records
`{"skipped": "scale"}` (the F1 floor still holds; only the localization is
deferred).

## The gate

`gate` diffs the current scorecard against the committed baseline
(`baselines/scorecard.json`) and exits non-zero on a regression:

- **Anchor, host-independent signal changed → FAIL.** Classification, matchkeys,
  blocking fields/cost are pure functions of the data + kernel.
- **Anchor F1 below `baseline − tolerance` → FAIL** (default tolerance 0.01).
- **Real dataset F1 below `baseline − tolerance` → FAIL**; its signal drift is
  informational.
- **`planner_rung` drift → WARN, never FAIL.** Backend/rule routing is coupled to
  native-wheel availability and box RAM+cores, not to the decision kernel — so a
  CI runner without the native wheel never flaps a dev baseline blessed with
  native on. Still recorded as visible drift.
- **Skipped / absent dataset → NEUTRAL.**

## The iterate loop

You changed the kernel (a floor, a classifier rule, the blocking decouple). Now:

```bash
# 1. See the impact. `report` prints the diff vs the committed baseline.
python -m scripts.autoconfig_quality report

# 2a. Drift is unintended -> fix the kernel, re-run report until the diff is clean.
# 2b. Drift is the intended improvement -> accept it as the new pinned truth:
python -m scripts.autoconfig_quality bless
git add scripts/autoconfig_quality/baselines/scorecard.json
git commit -m "quality: re-bless baseline (<what changed and why it's better>)"
```

The committed baseline's **git history is the trend log** — every bless is a
reviewable diff of how the auto-config's decisions moved and why.

Keep the loop fast: `--fast-only` skips the F1 tier entirely (config signals are
seconds), and `--datasets a,b` restricts the run. `historical_50k` is the only slow
entry (full 50k dedupe, ~1-3 min); exclude it with `--datasets` while iterating on
signal-level changes, then run the full corpus before you bless.

## Flags

| Flag | Default | Purpose |
| --- | --- | --- |
| `--fast-only` | off | Skip the F1 tier (config signals only). |
| `--datasets a,b` | all | Restrict to named datasets. |
| `--row-cap N` | 20000 | Cap rows fed to the F1 tier (tractability). |
| `--native {0,1,auto}` | run default | `GOLDENMATCH_NATIVE` for the run. The kernel signals are parity-identical across native/Python; only `planner_rung` (WARN) differs. |
| `--tolerance F` | 0.01 | F1 floor band. |

## Determinism notes

- Kernel signals are host-independent; F1 is parity-identical native vs Python.
  The only host-coupled signal (`planner_rung`) is WARN by design.
- The CI job pins `GOLDENMATCH_AUTOCONFIG_MEMORY=0` so the gate measures the
  static kernel, not learned per-run adjustments. Re-bless under the same setting.
- The harness sets `POLARS_SKIP_CPU_CHECK=1` itself; no extra env needed locally
  beyond making `scripts` importable (run from the repo root).

## Scope (YAGNI)

No wall-clock/perf metrics (that's the bench workflows), no web UI, no trend DB
(git log is the trend), no third-party-tool comparison. This gate answers exactly
one question: *did this change move an auto-config decision, and is that move
intended?*
