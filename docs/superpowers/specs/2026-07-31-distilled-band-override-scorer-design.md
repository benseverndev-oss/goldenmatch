# Design: distilled band-override scorer (local LLM → µs student)

- **Date:** 2026-07-31
- **Status:** DRAFT (design proposal from the FS + local-LLM investigation, 2026-07-30/31)
- **Related:** `core/_llm_loader.py` (local ER-matcher, PR #2288 shipped the 1.5B),
  `core/er_matcher/*`, `core/probabilistic.py` (`estimate_m_from_labels`),
  `core/llm_scorer.py` (`provider="local"` boost), `score_buckets` / score-core kernels.

## Problem

Fellegi-Sunter resolves the bulk of ER **fully locally** — on `historical_50k`,
**0.93 pair-F1 at recall 1.0**, CPU + native Rust kernels, no LLM/GPU/API. That
is the product's moat. But FS is a **linear model with a conditional-independence
assumption**, so on hard/corrupted data (no clean identifier) it leaves a residual
**uncertain band** it cannot separate — measured on `historical_50k` at scores
[0.55, 0.65], where FS's per-threshold accuracy is only ~0.64–0.71 and its
false-positives cap precision.

The shipped local-LLM boost (`provider="local"`, the 1.5B) *is* accurate on that
band (measured **0.885**, 97% label-precision on its match calls) — but at
**~3.9 s/pair on 4-core CPU**, and the band is ~67k pairs / ~42k distinct
(only 1.6× compressible), a **live** per-pair LLM boost is **~70 hours**.
Impractical. Latency micro-opts (grammar-constrained output, threads=physical,
prefix cache) give ~3–5×; the workload needs ~10,000×.

## What was ruled out (measured, 2026-07-30/31)

1. **Global FS refit** (`estimate_m_from_labels` on band labels): **neutral-to-harmful.**
   Even a *perfect oracle* labeler via iterative refit could not beat baseline
   (0.927 → 0.913). Labeling only the band gives an **unrepresentative** sample —
   hard positives bias `m` pessimistic, hard negatives bias `u` high (the pos+neg
   variant collapsed recall 0.83 → 0.50). FS's model class can't absorb the band's
   nonlinear field interactions by re-estimating m/u.
2. **Live per-pair LLM at query time:** perf-impossible at scale (above).

## Proposal: distill the LLM's band judgment into a µs CPU student

**We train, users consume.** The teacher (LLM) and the distillation run **once, in
OUR pipeline** — never at the user's end. We ship the trained student as a **pinned
artifact** (weights + sha256, exactly like the 1.5B GGUF registry in
`core/er_matcher/registry.py`). Users get a fast, accurate, fully-local band-override
scorer **out of the box — no labeling, no training, no LLM dependency at all.** This
is the same distribution model as the 1.5B itself (trained centrally on synthetic +
eval-only data, pinned, consumed zero-config).

**Teacher offline (ours), student online (theirs).** The LLM labels a band sample
during our training; a **small quantized student** — a GBDT or NNUE-style pair-head
over comparison features — reproduces those verdicts and serves the band **override**
at query time in the score-core kernel, fully local.

- **Override, not refit.** The student *replaces FS's decision on the band pairs
  only*; non-band pairs keep FS. This sidesteps the unrepresentative-sample failure
  (it's a local classifier, not a global re-estimate).
- **Nonlinear.** A small tree/MLP expresses the field-interaction correlations FS's
  linear/conditional-independence model can't — the actual headroom.

### Measured evidence (`historical_50k`)

- **Ceiling (features suffice):** GBDT on comparison features, gold labels, 5-fold CV
  → **band accuracy 0.959 / F1 0.967** (vs LLM 0.885, FS 0.64–0.71), at **2.86 µs/pair**
  (~1.4M× faster than the 1.5B).
- **Distillation (deployable):** student trained on the **1.5B's 600 labels**
  (not gold) → **0.880 vs ground truth ≈ matches the teacher (0.885)**.
- **End-to-end dedupe F1 (fixed candidate universe):**
  - baseline (FS): P 0.971 / R 0.834 / **F1 0.897**
  - override, 1.5B-labeled student: P 0.971 / R 0.889 / **F1 0.928 (+0.031)**
  - override, gold-ceiling student: P 0.973 / R 0.920 / **F1 0.945 (+0.048)**
  The gain is recall: FS scored true matches in the band below its operating
  threshold; the student promotes them, precision held.

## Architecture

1. **Feature extractor** (dataset-agnostic): per matchkey field, `[jaro_winkler,
   token_sort, exact, both_present]` + the FS score. Same comparison primitives the
   scorer already computes — reuse `core/scorer` / score-core, no new math.
2. **Student model:** start with GBDT (portable, tiny, fast to train); NNUE-style
   quantized pair-head is the follow-on for a pure-integer score-core kernel with an
   **incrementally-updatable per-record accumulator** (embed each record once, reuse
   across a block's N² pairs; O(changed-fields) update on streaming edits — maps onto
   `match_one` / `add_to_cluster` / incremental identity #1109).
3. **Training pipeline (OURS, offline):** teacher (the 1.5B or a bigger GPU teacher)
   labels a band sample over a **diverse corpus** (synthetic + benchmark ER, the
   1.5B's own training-data posture: fully synthetic + eval-only restricted sets) →
   train student → serialize + pin (weights + sha256, registered like the GGUF). The
   student learns a domain-agnostic *"how to combine comparison signals"* rule, not
   entities — which is what lets a centrally-trained artifact transfer to users' data.
   Runs in CI/Modal, never at the user's end.
4. **Runtime (online):** `score_buckets` / FS path routes band pairs (score in an
   adaptive window around the operating threshold) through the student kernel; else FS.
   The student is a **score-core kernel** — single-sourced Rust → native/WASM/TS,
   quantized/deterministic, with parity fixtures like every other scorer.

## Integration & scope

- **Shipped, pre-trained, zero-config — users do NOT train it.** We train the student
  centrally and ship it pinned; users just consume it, fully local, no LLM/labels at
  their end. It can be default-on once the generalization gate (below) passes, exactly
  like the noise-aware scorer or FS-autoconfig-v2 defaults — not a "bring your labels"
  tier.
- **Additive / safe.** Where FS is already confident (clean identifiers → no band —
  confirmed no-op on febrl3/synthetic), the override does nothing.
- **Generalization is the load-bearing requirement,** not a caveat: a centrally-trained
  student must transfer to unseen user data. This is tractable because the inputs are
  **generic comparison features**, not raw entities — but it needs **schema
  normalization** (map any dataset's fields into a fixed, order-invariant feature space)
  and possibly a **small family of per-domain students** (person / product / biblio,
  mirroring the domain packs).

## Open questions / validation gates (before any default-on)

1. **Generalization / transfer (THE gate — because we ship one trained artifact).**
   A centrally-trained student must lift F1 on datasets it was NOT trained on. The
   +3–5 is one dataset (`historical_50k`); febrl3/synthetic are *solved* (no band →
   no-op, confirmed). Required: (a) **train on a diverse corpus, evaluate held-out** —
   messy real data (NCVR, product ER with entity truth); (b) **schema normalization**
   so any field layout maps into the student's fixed feature space; (c) full-candidate-set
   extraction (pre-review-cut FS scores; the public `dedupe_df().scored_pairs` exposes
   only emitted matches on cleanly-separated data). → CI transfer-panel gate before
   default-on.
2. **Cluster-level F1**, not just candidate-set pair-F1 (plug the student as a live
   band override and re-cluster).
3. **Calibration** of the student score to a threshold/posterior (Platt/temperature)
   — FS gives a principled posterior; the student needs one.
4. **Label budget vs gain:** deployable +3.1 vs ceiling +4.8 → more/better teacher
   labels close some gap; quantify the curve.
5. **Explainability:** FS has per-field lineage; the student needs a feature-attribution
   surface to honor "never black-box" (NNUE accumulator contributions are inspectable).

## Non-goals

- Replacing zero-config FS (this is an opt-in tier).
- Running any model at query time (the whole point is offline teacher → µs student).
