# Stage-2-B: Synthesis Self-Consistency — Validation Verdict (HONEST-NULL)

**Date:** 2026-06-30
**Spec:** `docs/superpowers/specs/2026-06-30-stage2b-synthesis-self-consistency-design.md`
**Plan:** `docs/superpowers/plans/2026-06-30-stage2b-synthesis-self-consistency.md`

## Run config

- Corpus: MuSiQue-Ans, seeded subset, **N=50**.
- Engine: goldengraph, open extraction, `GOLDENGRAPH_QA_MODE=auto`, **`GOLDENGRAPH_SYNTH_SAMPLES=5`**,
  `GOLDENGRAPH_SYNTH_TEMPERATURE=0.7` (default).
- Model: `qwen2.5:7b-instruct` + `nomic-embed-text`, Modal A10G, fair metric (stage-2-A, on main).

## Result vs the stage-2-A baseline

| metric | baseline (single-call) | self-consistency (SAMPLES=5) |
|---|---|---|
| `answer_match` (all 50) | 0.12 | **0.08** (worse) |
| `answer_match` (entity-subset, n=30) | 0.20 | **0.13** (worse) |
| SYNTHESIS bucket | 18 | 16 (−2, within noise) |
| EXTRACTION bucket | 21 | 25 |
| RETRIEVAL bucket | 14 | 12 |
| `support_recall` | 0.58 | 0.63 |

**Verdict: HONEST-NULL.** Self-consistency did not recover the synthesis misses — `answer_match`
dropped (0.12 → 0.08), and the SYNTHESIS bucket barely moved (−2).

## Why it failed (the mechanism)

Self-consistency only helps when the **correct** answer is the **modal** sample across the N draws. At
**12% base accuracy** on hard multi-hop reasoning, the correct answer is *not* the plurality — most of
the 5 samples are wrong, so majority-voting reinforces a wrong answer rather than surfacing the right
one. The temperature-0.7 diversity adds more *wrong* variants, making it marginally worse, not better.
This is a known failure mode of self-consistency: it amplifies a model that is right-more-often-than-any-
single-wrong-answer; it cannot manufacture correctness a low-accuracy base model doesn't have.

The stage-2-A diagnosis ("the answer is IN the ball; the model reads the wrong one") was correct, but
the corrective assumption — that the error is **variance** (so voting recovers it) — is refuted. The 7B's
synthesis errors are **systematic**: it reasons to the same (wrong) answer, or to a spread of wrong
answers that out-vote the right one.

## Confound (flagged for honesty)

The baseline and this run are SEPARATE live-7B builds, and 7B extraction is non-deterministic run-to-run
(EXTRACTION drifted 21 → 25 with no extraction-side change). So the small bucket movements partly reflect
extraction variance, not synthesis. The verdict is robust regardless: `answer_match` went **down**, and
the mechanism above explains why. A confirmatory re-run could separate the variance, but a
mechanistically-explained null does not warrant another ~70-min run.

## Disposition

- **The code ships, default-off.** `complete_many` + opt-in self-consistency in `synthesize_local` are
  merged behind `GOLDENGRAPH_SYNTH_SAMPLES` (default 1 = byte-identical single call), 31 tests green. It
  is a clean, tested capability that simply does not help on *this* stack at *this* base accuracy.
- **Do NOT enable by default** (it lowers answer-match).
- **Next lever:** because synthesis errors are systematic not variance, the productive directions are the
  ones that *constrain* or *raise* the base reasoning rather than average over it:
  1. **Constrained-ball selection** — force the final answer to be one of the retrieved ball entities
     (the stage-2-A YAGNI'd alternative), removing off-ball/free-form wrong answers.
  2. **Retrieval (broken chains)** — the clear second lever from stage-2-A (14), which also lifts the
     base subgraph quality synthesis reasons over.

## Lesson

Self-consistency is not a free win on a low-accuracy base model. It requires the correct answer to be
modal; verify the base accuracy clears that bar before assuming variance-reduction will help. Measured
in one falsifiable run, recorded, moved on — no tuning to force a number.
