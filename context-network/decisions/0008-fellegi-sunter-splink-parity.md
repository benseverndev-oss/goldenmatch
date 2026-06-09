# 0008 — Fellegi-Sunter: close the Splink engine gap, measure the scale-out, keep defaults reproducible

**Status:** accepted (2026-06-08, Ben) • **Shipped:** PRs #800 / #802 / #803; accuracy arc #821 / #823 (2026-06-09) • **Architecture:** [../architecture/fellegi-sunter-splink-parity.md](../architecture/fellegi-sunter-splink-parity.md)

## Context
The audit found GoldenMatch's Fellegi-Sunter matchkey accuracy-competitive with
Splink (DBLP-ACM 0.968) but missing the *engine* around it: single-node only,
retrains EM every run, no supervised-from-labels path, no FS-native
explainability, score that isn't a probability. "Is it on par with Splink?" was
the framing — and the honest answer was "the algorithm yes, the engine no."

## Decision
1. **Close the gap in dependency order, not all at once.** Lifecycle
   (persistence + supervised m) → explainability (waterfall) → scale-out
   (bucket, then native kernel) → calibration/accuracy-analysis. Each phase its
   own PR with a measured gate.
2. **Reuse the substrate, don't rebuild it.** FS scale-out rides the existing
   `score_buckets` orchestration (which already carries the Ray/DataFusion
   wiring) — the only greenfield low-level piece is one native kernel function.
   The "scale-out is easy because the substrate exists" bet held.
3. **Defaults stay reproducible; new power is opt-in.** Posterior calibration,
   the native FS kernel (discrete-level float-boundary risk), and monotonicity
   *enforce* are all opt-in; the default path is byte-stable. Don't relocate the
   operating point or trade reproducibility for a benchmark number.
4. **Measure the scale gate on a real runner, don't assert it from code.** The
   distributed-FS claim is a `workflow_dispatch` bench, run on demand — and
   running it is what surfaced the real bottleneck.

## Consequence
- FS feature parity with Splink is closed (lifecycle, supervised m,
  explainability, calibration, accuracy analysis, scale-out); Splink retains the
  distributed-1B+ and interactive-charting edge.
- The scale gate paid for itself: it exposed that EM (`_sample_blocked_pairs`,
  `O(Σ size_i²)`), not scoring, dominated the 6M wall. Fixing it cut native 6M
  from 269 s → 162.6 s and halved peak RSS. Lesson reaffirmed: **measure the
  whole pipeline at scale before claiming a stage is the bottleneck** — the
  native kernel had already won scoring; the time was elsewhere.
- A benchmark's *ground truth* is part of the measurement: the 6M F1 read 0.825
  only because the bench scored against a star GT instead of the entity clique.
  The matcher was right; the harness wasn't.

## Update — accuracy arc, beating Splink (2026-06-09, #821 / #823)
The original decision closed *feature* parity ("the algorithm yes, the engine
no" → both yes). The accuracy arc extends it to *beat* Splink head-to-head:
- **#821** built a shared evaluator (`scripts/bench_er_headtohead`, pairwise F1,
  one harness for both engines) — the panel that turns "on par" into a measured
  claim instead of ad-hoc per-dataset numbers.
- **#823 (FS auto-config v2)** makes the probabilistic auto-config outscore
  Splink on that panel via recall-positive field selection (admit dates as a
  `levenshtein` discriminator; drop redundant person-name composites; a
  low-cardinality fuzzy floor; admit title/author as `token_sort`) plus
  *additive* blocking diversification (`_diversify_probabilistic_blocking` onto
  orthogonal stable keys — adds passes, never removes the primary).
- Defaults-reproducible held: scope is the probabilistic auto-config path only;
  default-ON with kill-switch `GOLDENMATCH_FS_AUTOCONFIG_V2=0` restoring the
  legacy selection byte-identically (3925 tests pass, 22 in
  `test_fs_autoconfig_v2.py`, flag=0 byte-identical).
- Results (pairwise F1, shared evaluator): historical_50k 0.624 → 0.779 vs
  Splink 0.757; synthetic_person 0.972 → 0.998 vs 0.996; febrl3 0.983 → 0.991
  vs 0.965; dblp_acm 0.003 → 0.879. **Honest framing preserved:** these are
  *pairwise* F1; the cited ~0.97 Splink historical_50k number is a *cluster*
  metric, and Splink scores ~0.75 pairwise under the same harness (recall-bound,
  ~0.93 pairwise ceiling for any engine). Claim is "matches/beats on the same
  evaluator," not "0.97 pairwise." Splink retains the distributed-1B+ and
  interactive-charting edge.
