# Fellegi-Sunter (probabilistic) matching — Splink parity

The `type: probabilistic` matchkey went from an accuracy-competitive *scorer*
to a full probabilistic-linkage *engine* on par with Splink: model lifecycle,
supervised training, FS-native explainability, calibration, and scale-out — and
then a perf pass that the scale-out exposed. The entity-resolution sibling of
the suite's Arrow-native / engine-maturity arc.

**Status:** SHIPPED (2026-06-08). Roadmap delivered across PRs #800 (Phases
0–4 + 3c bench harness), #802 (bench ground-truth fix), #803 (EM sampling perf).
Accuracy arc shipped 2026-06-09: #821 (head-to-head bench panel) + #823
(FS auto-config v2) now *beat* Splink on the shared evaluator (see below).
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

## Block-scoring perf — the per-block fan-out fix (2026-06-12, PR #869)
A follow-up pass on the *numpy* (default) auto-config path, prompted by auditing
the "Splink 3-19x faster" claim. Two findings reframed it
([decision 0012](../decisions/0012-fs-block-scoring-perf.md)):

1. **The bake-off measured numpy, not native.** It never set `GOLDENMATCH_FS_NATIVE`,
   and probabilistic mode doesn't refuse on a missing kernel. A `gm_prob_native`
   column (native built + symbol-asserted in CI) showed **native ≈ numpy, no wall
   change** — because the wall is per-block fan-out, not scoring math. historical_50k
   makes 31,735 blocks, **79% ≤8 rows**, each row in ~6 blocks (multi-pass overlap),
   so scoring fanned out into ~222k tiny FFI-bound `score_field_matrix` calls.
2. **Three output-identical optimizations** on `score_probabilistic_vectorized`,
   each gated by a fixed-`em_result` pair-set diff (200,058 pairs, byte-identical):
   value-dedup in per-field matrices (−32%), batch small blocks into shared
   per-field S×S matrices with diagonal sub-block extraction (−48%, native calls
   222k→4.3k), and a batch row-cap tune 512→256 (−20%).

**Measured (historical_50k, local, probabilistic auto-config):** 86.5s → **24.6s
(−72%)**, pairs identical at each step. The cluster-count hash is NOT a valid gate
here — the pipeline is non-deterministic run-to-run (11,542–11,545 clusters, EM
sample order) — so the pair-set diff is the FS correctness method.

## Accuracy arc — beating Splink (auto-config v2, #821 panel + #823)
The engine arc closed *feature* parity; the accuracy arc closes the head-to-head.
A shared evaluator (`scripts/bench_er_headtohead`, pairwise F1, one harness for
both engines) replaced ad-hoc per-dataset numbers, then **FS auto-config v2**
(#823) made the probabilistic auto-config *outscore* Splink on it.

**Scope.** v2 touches the probabilistic auto-config path only
(`auto_configure_probabilistic_df` / `build_probabilistic_matchkeys`); the
weighted/DQbench path and zero-config `dedupe_df` are untouched. Default-ON;
kill-switch `GOLDENMATCH_FS_AUTOCONFIG_V2=0` restores the legacy selection
byte-identically.

**Four levers:**
1. **Admit dates as a discriminator.** `dob` / date columns enter as a
   `levenshtein` field (v1 discarded them outright).
2a. **Drop redundant person-name composites.** When atomic given + family
   exist, drop `full_name` / `first_and_surname` composites (no new signal,
   just correlated weight).
2b. **Low-cardinality fuzzy floor** — give low-distinct fields a fuzzy
   comparison instead of exact-only.
3. **`_diversify_probabilistic_blocking`** — *additively* diversify blocking
   onto orthogonal stable keys (date-year + postcode/zip). Recall-POSITIVE
   (adds passes, never removes the primary).
4. **Admit description (title) + multi_name (authors) as `token_sort`** —
   lifts the DBLP-ACM venue-only mega-match (the 0.003 → 0.377 jump; a large
   relative gain, but still recall-bound — see the bibliographic note below).

**Head-to-head (pairwise F1, shared `bench_er_headtohead` evaluator) — deterministic as of #829:**
| Dataset | GM before | GM v2 | Splink |
|---|---|---|---|
| historical_50k (Splink's flagship) | 0.647 | **0.778** | 0.757 |
| febrl3 | 0.983 | **0.991** | 0.965 |
| synthetic_person | 0.972 | **0.998** | 0.996 |
| dblp_acm (bibliographic) | 0.003 | 0.377 | (Splink skips) |

GM also wins at the cluster level on historical_50k (B-cubed F1 0.844 vs 0.789).
The three-engine accuracy + perf bake-off is at
`docs/benchmarks/2026-06-09-splink-bakeoff.md`.

**Determinism (#829).** Before #829, `_sample_blocked_pairs` seeded-shuffled bare
block indices whose order was itself non-deterministic (parallel / hash-bucketed
construction), so the EM training sample — and thus the m/u weights, threshold,
and P/R — varied run-to-run. On one pre-fix CI run, three invocations of the
*identical* GM-prob path gave historical_50k F1 of 0.805 / 0.779 / 0.643. #829
sorts blocks by their stable `block_key` before the shuffle; post-fix the three
harnesses agree within 0.002 (0.7782 / 0.7783 / 0.7804). The earlier
`dblp_acm = 0.879` was a non-deterministic lucky draw that does not reproduce; the
deterministic value is 0.377 (both harnesses agree).

**Honest framing (this is pairwise F1, not the cited cluster metric).** These
are pairwise F1 under one shared evaluator. The often-cited ~0.97 Splink number
on historical_50k is a *cluster/entity-level* metric, NOT exhaustive
within-cluster pairwise F1 — a local diagnostic ran Splink 4.0.16 and it scores
~0.75 *pairwise* on this dataset under the same harness (recall-bound:
historical_50k has 5156 clusters, mean size ~10, no single field exceeds 0.60
recall, so the pairwise blocking ceiling for *any* engine is ~0.93). The claim
is "GoldenMatch matches/beats Splink head-to-head on the same evaluator," NOT
"0.97 pairwise." Splink is also 3-19x faster on these datasets.

**Bibliographic (DBLP-ACM): use the weighted path, not probabilistic.** Splink
skips dblp_acm; the probabilistic auto-config is weak there (0.377 pairwise,
recall-bound). The zero-config *weighted* controller scores 0.964 on DBLP-ACM and
is the right tool for that shape. The probabilistic path targets PII / person
linkage.

**Verification:** 3925 tests pass; 22 in `test_fs_autoconfig_v2.py`; flag=0 is
byte-identical to legacy.

## Where Splink still leads
Distributed Fellegi-Sunter at 1B+ rows on Spark, and the mature interactive
m/u + comparison-viewer charting UI, and raw per-node speed. GoldenMatch's FS
scale-out is measured single-node at 6M and inherits the bucket → Ray path; the
charting is data-export (`fs_model_report`, the waterfall) rather than a hosted
dashboard. On *PII accuracy*, though, Splink no longer leads — the head-to-head
above flips that on the shared evaluator (historical_50k 0.778 vs 0.757,
synthetic_person 0.998 vs 0.996).

**The "3-19x faster" figure is pre-optimization and needs a re-bench.** It came
from a bake-off that measured GM's *numpy* path before PR #869's block-scoring
fixes (which cut the historical_50k wall −72% locally; see the perf section
above). The committed bake-off `gm_probabilistic` walls are stale until
`bench-probabilistic.yml` (`run_bakeoff=true`) is re-run on the optimized branch;
Splink is still faster per node, but by a smaller and as-yet-unre-measured margin.
