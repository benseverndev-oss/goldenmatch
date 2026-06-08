# 0008 — Fellegi-Sunter: close the Splink engine gap, measure the scale-out, keep defaults reproducible

**Status:** accepted (2026-06-08, Ben) • **Shipped:** PRs #800 / #802 / #803 • **Architecture:** [../architecture/fellegi-sunter-splink-parity.md](../architecture/fellegi-sunter-splink-parity.md)

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
