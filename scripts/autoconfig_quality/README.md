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

## Two strategies per dataset (default vs probabilistic)

Each ground-truth dataset records F1/P/R for **two strategies**, both floored:

- `f1` — the **default** strategy: `dedupe_df(df)` (exact + weighted matchkeys).
  This reflects the probabilistic-routing lever once
  `GOLDENMATCH_AUTOCONFIG_ROUTE_PROBABILISTIC` is enabled.
- `f1_probabilistic` — the **forced Fellegi-Sunter** strategy:
  `auto_configure_probabilistic_df(df)` → `dedupe_df(df, config=...)`.

Comparing the two per dataset is what tells us whether the probabilistic path
helps (route candidate) or hurts (leave deterministic) — the evidence base for the
routing lever, and its regression guard. Baseline snapshot (memory-off, native-0,
routing off):

| dataset | f1 (default) | f1_probabilistic | verdict |
| --- | --- | --- | --- |
| historical_50k | 0.4663 | 0.8294 | prob >> det (+0.36) |
| febrl3 | 0.9665 | 0.9907 | prob > det (+0.024) |
| ncvr_synthetic | 0.9828 | 0.9894 | prob > det (+0.007) |
| anchor_person_match | 0.9896 | 0.9607 | det > prob (don't route) |

The probabilistic strategy carries a small EM-convergence wobble (±0.004, recall
stable); the 0.01 floor tolerance absorbs it.

## Per-rule link-cut matrix (FS link-cut routing P3)

`rule_sweep.py` measures every Fellegi-Sunter link-cut rule on every labelled
dataset in the frozen corpus (`corpus.py`). For each dataset it auto-configures the
probabilistic config once, then runs the full pipeline once per arm on one shared
EM model (`MatchkeyConfig.model_path`):

- `default` trains and saves the model;
- `default_loaded` reloads it and is the baseline;
- each `link_cut_rule` arm reloads it too.

A pinned arm that an earlier precedence step overrode is **voided** and fails the
run, so a leaked `GOLDENMATCH_FS_EVIDENCE_CUT` cannot quietly turn nine arms into
nine copies of the default. The sweep also refuses to start while any cut-moving
env var is set.

Each arm runs in its own child process, reading one shared config file, under a
memory cap (`GOLDENMATCH_CUT_RULES_ARM_MEM_MB`, default 12000) and a timeout
(`GOLDENMATCH_CUT_RULES_ARM_TIMEOUT_S`, default 3600). A rule arm that is killed
or crashes is recorded (`crashed`: `memory_cap`, `timeout` or `crashed`, with its
peak RSS) and counts as below default, since blowing up is worse than the
baseline; the rest of the dataset's arms still run. A crashed `default` or
`default_loaded` arm leaves no baseline, so it fails the dataset.

```bash
python -m scripts.autoconfig_quality.rule_sweep --datasets person --out person.json
python -m scripts.autoconfig_quality.rule_sweep merge a.json b.json --out m.json --summary-md m.md
```

Measurement only: it writes no routing row. Large datasets run in the
`fs-cut-rule-matrix` workflow (one job per dataset), never on a laptop.

| Corpus | Datasets | Source and licence |
|---|---|---|
| design | person, household_hardneg, cotenant_hardneg, febrl3, febrl4, ncvr_synthetic, ncvr_real (local only), historical_50k, dblp_acm, dblp_scholar, amazon_google | as listed above |
| holdout | abt_buy | Leipzig, CC-BY: Database Group Leipzig; Köpcke, Thor & Rahm, VLDB 2010 |
| holdout | walmart_amazon, itunes_amazon, fodors_zagats | Magellan/DeepMatcher, cite-only, fetched for evaluation and never committed: Konda et al. 2016; Mudgal et al. 2018 |
| holdout | febrl1, febrl2 | FEBRL raw CSVs from recordlinkage, ANU open-source licence: Christen 2008 |
| holdout | synth_{person,biblio}_c{05,20}_d{10,40} | `generate_fixture.py`, seed 1009, corruption 0.5/2.0, dupe rate 0.10/0.40 |

The held-out set is frozen: a routing row ships only if it is never more than 0.01
F1 below the default on every design **and** held-out dataset. Changing the
held-out list after any row exists means re-running the full gate.

**Row authors (P4) read the `cut-rule-matrix` design artifact only.** The
held-out json and the per-dataset `cut-rules-<holdout dataset>` artifacts are
gate inputs, not evidence -- they must not be read while writing a row, or the
held-out set stops being held out.

**Magellan truth bias.** The Magellan/DeepMatcher truth (walmart_amazon,
itunes_amazon, fodors_zagats) covers only DeepMatcher's labelled candidate
pairs, so an unlabelled true match a rule links is scored as a false positive
and lower cuts are penalised -- the bias favours high-cut rules such as
`evidence_12` and `posterior_099`. A labelled-pairs-only metric is a P4
prerequisite before these datasets gate a row.

**Held-out independence.** Not every held-out dataset is an independent check:
`febrl1`/`febrl2` share the FEBRL generator with design's `febrl3`/`febrl4`, the
eight `synth_*` variants share one generator, and `fodors_zagats` is
near-saturated (F1 ~1.0, little room to show a regression). The genuinely
independent real held-out sets are `abt_buy`, `walmart_amazon` and
`itunes_amazon`.

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
