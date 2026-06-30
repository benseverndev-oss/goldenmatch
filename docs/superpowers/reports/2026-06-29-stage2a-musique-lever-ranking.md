# Stage-2-A: MuSiQue Lever-Ranking Verdict (N=50)

**Date:** 2026-06-30
**Spec:** `docs/superpowers/specs/2026-06-29-stage2a-musique-lever-ranking-design.md`
**Plan:** `docs/superpowers/plans/2026-06-29-stage2a-musique-lever-ranking.md`

## Run config

- Corpus: **MuSiQue-Ans**, seeded subset, **N=50** questions.
- Engine: goldengraph, open extraction, `GOLDENGRAPH_QA_MODE=auto`, `LITERAL_ATTRS` off (measured-dead).
- Model: `qwen2.5:7b-instruct` (chat) + `nomic-embed-text` (embed), Modal A10G.
- **Metric: the stage-2-A fair normalizer** (date/time/number-word canonicalization) — so both the
  headline `answer_match` AND the localize bucket categorization are format-fair.

## Headline (fair metric)

| metric | value |
|---|---|
| `answer_match` (all 50) | **0.12** |
| `answer_match` (entity-answerable subset, n=30) | **0.20** |
| `support_recall` | **0.58** |
| `exact_match` / `token_f1` | 0.08 / 0.13 |

Answer-type mix of the 50 golds: **entity 30, phrase 9, number 7, date 4** — i.e. **20 of 50 (40%)
are non-entity answers** an entity graph cannot emit as a node.

## Raw failure-stage tally

The harness localize categorizes each question's failing hop. Multi-hop questions can contribute to
more than one stage, so these are **failure-stage frequencies** (they rank the modes; they do not
partition the 50 questions):

| stage | count |
|---|---|
| EXTRACTION (gold not a graph node) | 21 |
| SYNTHESIS (retrieved, wrong answer) | 18 |
| RETRIEVAL-BROKEN-CHAIN (in graph, unreachable) | 14 |

## The refinement that changes the ranking

The lead bucket (EXTRACTION, 21) is **not** primarily an extraction-recall problem. Classifying its
gold answers:

- **~5 entities actually missed** — `Lana Wood`, `U.S. Marshal Rooster Cogburn`, `Han Chinese
  emigrants`, `The Australian Ballet`, `Yamanote Line loop`. A real extraction-recall gap on dense
  prose.
- **~16 non-entity answers** — dates (`11 February 1929`, `April 28, 1952`, `30 November 1999`),
  numbers (`1,438,159`, `1599`, `551-600`), descriptive phrases (`built on 16-bit architectures and
  offered improved graphics and sound`, `northeastern Oklahoma`, `rises in northern Minnesota and
  meanders slowly southwards`). These are **unanswerable-by-construction** for an entity graph — a
  representation mismatch, **already measured dead** (literal-attrs: 0.083 → 0.083, support_recall
  *worse*; see the design doc). NOT a lever we chase.

So ~3/4 of the EXTRACTION bucket is the structural non-entity issue, not extraction recall.

## Ranked FIXABLE levers (the verdict)

Stripping the ~16 structurally-unanswerable non-entity questions from EXTRACTION:

1. **SYNTHESIS — 18 (the dominant fixable lever).** The answer was in the retrieved ball; the model
   wrote the wrong one. Most tractable: it needs **no graph-structure change** — better answer-reading
   off the retrieved subgraph (synthesis prompting / answer extraction). `support_recall` 0.58 confirms
   retrieval surfaces the evidence for well over half the questions.
2. **RETRIEVAL-BROKEN-CHAIN — 14.** Entity in the graph but unreachable from the seeds (multi-hop
   chain breaks / under-merge on the denser real graph). Reuses the engineered-corpus chain/bridge work.
3. **Entity-extraction recall — ~5.** Entities the 7B missed in dense prose. Smallest fixable lever.

**Recommendation for the next sub-project (stage-2-B): target SYNTHESIS.** It is the largest fixable
failure mode, the most tractable (graph-structure-free), and it sits on top of a retrieval layer that
already surfaces the evidence (`support_recall` 0.58, and 18 questions where the ball *contained* the
answer). Retrieval (broken chains) is the clear second.

## Confidence

- At N=50, the raw EXTRACTION (21) and SYNTHESIS (18) tallies are close (~3 stage-lines apart) — but
  once the ~16 structurally-unanswerable non-entity questions are removed from EXTRACTION, **SYNTHESIS
  is clearly the top fixable lever** and RETRIEVAL clearly third.
- Per-bucket N is ~14–21 stage-failures — enough to **rank** the three modes, not to split the
  EXTRACTION/SYNTHESIS raw tallies before the entity/non-entity decomposition.
- 7B extraction is non-deterministic run-to-run; the *distribution* is stable enough to rank.

## What stage-2-A delivered

- A **format-fair metric** (date/time/number-word canonicalization in `metrics.py`) that also
  corrects the localize bucket categorization (the buckets derive from `answer_match`).
- A trustworthy, decomposed ranking that **redirects stage-2 away from the headline (extraction) toward
  the real fixable lever (synthesis)** — a result the unfair metric + raw bucket count would have
  obscured.
