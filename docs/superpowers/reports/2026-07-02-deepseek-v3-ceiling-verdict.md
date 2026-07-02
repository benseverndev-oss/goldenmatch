# DeepSeek-V3 Ceiling Reference — Verdict

**Date:** 2026-07-02
**Branch:** `feat/deepseek-ceiling`
**Run:** Modal `gg-bench`, `--corpus wiki`, best config (`name_ci` + chunking `(6,2)`), `--chat deepseek-chat` (DeepSeek-V3 via API). 19 docs, 65 gold. Out of the free/local thesis — a one-off ceiling reference.

## What this tested

The seeded re-measurement left the real-prose substrate at F1 ~0.49-0.51 on the local 7B, with a characterized recall gap (entities extracted, edges/coverage limited). Open question the local model can't answer: is that gap **model-limited** (a stronger extractor breaks through) or **structural** (the wikilink-gold oracle + edge-centric aligner cap it regardless of model)? Swap the 7B for DeepSeek-V3 and read the delta.

## Result — TWO ceilings, cleanly separated

| config | R(B) | P(B) | F1(B) | coverage | components |
|---|---|---|---|---|---|
| 7B seeded (42 / 7) | 0.303 / 0.343 | 1.0 | 0.465 / 0.511 | 0.492 / 0.508 | 9 / 14 |
| **V3 (leg 140)** | 0.3990 | 1.0 | **0.5704** | **0.4923** | 7 |
| **V3 (leg 141)** | 0.4091 | 1.0 | **0.5806** | **0.4923** | 7 |

### 1. Relation-recall QUALITY is model-limited — V3 breaks through

F1 **0.49 → 0.58 (+~0.11, ~+20% relative)**, R(B) **0.30 → 0.40**, P(B) still 1.0. The stronger model extracts better/more-correct relations, driving *correct* cross-doc unification (components 9-16 → 7) and lifting pairwise recall. So the 7B's ~0.5 was partly a small-model ceiling, not a hard wall — the model matters on the quality axis.

### 2. Coverage is STRUCTURAL — V3 hits the identical 0.4923

V3's coverage is **byte-identical to the 7B's** (0.4923 = 32/65; the 7B ranges 32-33/65). V3 aligns the *same ~49% of gold and no more*. The other ~51% never aligns **regardless of model** — this is the wikilink-gold artifact (not every real entity is wikilinked, and the gold surface may not match any extracted node) plus the edge-centric aligner, not the extractor. **No model fixes it.**

So V3's entire F1 gain comes from clustering the *alignable half* better, not from reaching more gold. Coverage is model-invariant on this rig.

### Bonus: V3 is nearly deterministic

Two unseeded V3 legs: 0.5704 vs 0.5806, spread 0.01 (vs the 7B's ~0.14 unseeded spread). The large model is stable run-to-run even without a seed — the variance that bit the arc was a small-model artifact. (V3 ran without `GOLDENGRAPH_LLM_SEED` to avoid a possible unknown-param rejection; its low spread made seeding unnecessary here.)

## Answer to "is ~0.5 good, and would a stronger model help?"

- **Yes, the model helps** — V3 lifts substrate F1 ~20% (0.49 → 0.58) at zero precision cost. The relation-recall ceiling was partly the 7B.
- **But V3 cannot break the coverage wall (~0.49).** About half the gold is structurally unreachable on this benchmark regardless of extractor.
- So ~0.5 was **model-limited on quality, structurally capped on reach.** A production substrate would be meaningfully better on V3; it would not be *complete*, and the incompleteness is the benchmark's structure, not the model.

## Strategic read

- **The architecture is validated as a healthy shape.** `name_ci + chunking` is the right structure; V3 is a **drop-in extractor upgrade** (just `--chat deepseek-chat`) that improves quality without changing the pipeline. The 7B did its job as the cheap iteration engine; V3 is the deployment-quality extractor.
- **Thesis fork, made explicit:** free/local 7B → ~0.49 F1; DeepSeek-V3 (paid API, pennies for this corpus) → ~0.58. Neither breaks the structural coverage ceiling. Choose per requirement: free-local for iteration/cost-sensitivity, V3 for quality.
- **The coverage ceiling is now the real frontier, and it is NOT an extraction problem.** Further substrate gains require attacking the benchmark structure — a less-strict gold definition (beyond wikilinks), or an aligner that can reach entities via non-edge signals — not a better model or another extraction lever.

## Honest caveats

- **Two legs, one corpus, unseeded V3.** The V3 spread is tiny (0.01) so two legs are informative, but this is 19 docs / 65 gold; treat +0.11 F1 as "clearly positive," not a precise effect size.
- **Out of thesis.** V3 is a paid API; this is a reference point, not a shipped default. The `deepseek-chat` bench route ships behind the model name (opt-in) with the key in a Modal secret.
- **Coverage-structural claim rests on the identical 0.4923.** A different corpus or a relaxed gold could move the coverage ceiling; the claim is "model-invariant on THIS rig," strongly suggested by V3 == 7B coverage.

## Follow-ons

1. **If chasing more: attack coverage, not extraction.** Relax the gold beyond wikilinks, or add a non-edge alignment path — the structural ceiling is where the remaining ~50% lives.
2. **`deepseek-chat` is now a one-flag bench option** for any future quality-vs-cost reference.
3. Optional: an e2e QA run on V3 (the goal-relevant metric) to see whether the +0.11 substrate F1 translates downstream.
