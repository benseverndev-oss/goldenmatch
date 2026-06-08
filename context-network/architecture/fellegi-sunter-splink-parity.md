# Fellegi-Sunter (probabilistic) matching — Splink parity

The `type: probabilistic` matchkey went from an accuracy-competitive *scorer*
to a full probabilistic-linkage *engine* on par with Splink: model lifecycle,
supervised training, FS-native explainability, calibration, and scale-out — and
then a perf pass that the scale-out exposed. The entity-resolution sibling of
the suite's Arrow-native / engine-maturity arc.

**Status:** SHIPPED (2026-06-08). Roadmap delivered across PRs #800 (Phases
0–4 + 3c bench harness), #802 (bench ground-truth fix), #803 (EM sampling perf).
**Decision:** [../decisions/0008-fellegi-sunter-splink-parity.md](../decisions/0008-fellegi-sunter-splink-parity.md).
**Code-level notes:** `packages/python/goldenmatch/CLAUDE.md` (Fellegi-Sunter
section), `docs/scale-envelope.md` (FS-at-scale), `docs-site/goldenmatch/scoring.mdx`.

## The audit that framed it
A 2026-06-07 audit reproduced DBLP-ACM **P=0.978 / R=0.958 / F1=0.968** and
found the algorithm faithful to Splink (u from random pairs, m from EM, blocking
fields excluded) — but the surrounding *engine* was missing: FS ran single-node
sequential only (every scale backend declined it), retrained EM every run, had
no supervised-from-labels path, no FS-native explainability, and an admittedly
non-probability default score. The work closed those, in dependency order.

## What shipped (the parity surface)
- **Phase 0 — hygiene.** Match-weight monotonicity guard (PAV isotonic, default
  *warn* — `enforce` measured to trade F1, so opt-in); fixed the mis-tuned
  posterior cut (0.50 → 0.99) and stale calibration comments.
- **Phase 1a — model lifecycle.** `EMResult` JSON save/load + `validate_for` +
  `MatchkeyConfig.model_path` + `load_or_train_em` (train-once → reuse;
  byte-identical pairs on reload).
- **Phase 1b — supervised m.** `estimate_m_from_labels` (Splink's
  `estimate_m_from_label_column`) + label adapters from the review-queue /
  memory corrections store.
- **Phase 2 — explainability.** Match-weight waterfall (`explain_pair_fs` /
  `FSWaterfall`), surfaced in `goldenmatch explain --pair` + the lineage
  sidecar; `EngineResult.em_results` exposed.
- **Phase 3a — scale-out (numpy).** Probabilistic matchkeys ride the shared
  `score_buckets` orchestration (which carries the Ray / DataFusion wiring) —
  clusters identical to polars-direct.
- **Phase 3b — native FS kernel (opt-in).** `score_block_pairs_fs` in
  `goldenmatch-native`; default OFF (`GOLDENMATCH_FS_NATIVE=1`) because FS's
  discrete levels amplify rapidfuzz float diffs at exact thresholds.
- **Phase 4 — accuracy analysis from labels.** `threshold_sweep` /
  `recommend_threshold` / `fs_model_report` /
  `probability_two_random_records_match`; `goldenmatch evaluate
  --threshold-sweep`.
- **Phase 3c — distributed validation.** `bench-fs-distributed.yml`
  (`workflow_dispatch`) — the at-scale gate, run on demand.

## The perf pass the scale gate exposed
The 6M bench (269 s native) was **train_em-bound, not scoring-bound** — the
native kernel had already cut `bucket_score` to ~14 s. `_sample_blocked_pairs`
enumerated **every** within-block pair across **every** block (`O(Σ size_i²)`,
~140M tuples) before sampling 10K; fixed to a block-stratified early-exit (#803).
A second, separate fix corrected the *bench's* ground truth from a star
(`base→dup`) to the entity *clique* (#802) — FS was always right; the GT was
incomplete.

## Measured (6M rows, `backend=bucket`, 16c/64GB)
| FS path | Wall | Peak RSS | `bucket_score` | F1 |
|---|---|---|---|---|
| numpy (default) | 288.5 s | 11.3 GB | 136.6 s | 1.000 |
| native (`FS_NATIVE=1`) | 162.6 s | 11.3 GB | 12.7 s (~10.8×) | 1.000 |

The EM fix shaved ~100 s off both paths and halved peak RSS; native is ~10.8×
on the scoring step (tiny-block regime). Original pre-fix figures (269 s / 0.825)
were the un-bounded EM + the star-GT bench bug.

## Where Splink still leads
Distributed Fellegi-Sunter at 1B+ rows on Spark, and the mature interactive
m/u + comparison-viewer charting UI. GoldenMatch's FS scale-out is measured
single-node at 6M and inherits the bucket → Ray path; the charting is
data-export (`fs_model_report`, the waterfall) rather than a hosted dashboard.
