# Relation Re-Prompt — Verdict

> **⚠️ CORRECTION (2026-07-02): this "WIN" was REFUTED by seeded re-measurement.** The +33% R(B) / +26% F1
> reported below was an **unseeded single-leg artifact** — the control (leg 100) and re-prompt (leg 101) legs
> were two independent draws from a distribution with ~0.14 F1 run-to-run spread (the 7B extraction is
> non-deterministic; see `2026-07-02-rebel-fuse-verdict.md`). Re-measured at a fixed `GOLDENGRAPH_LLM_SEED`
> across seeds 42 and 7, the re-prompt delta is **0.000 / −0.046** — no gain, slightly negative. See
> `2026-07-02-seed-determinism-verdict.md` for the corrected result. The `GOLDENGRAPH_RELATION_REPROMPT` gate
> is default-off (nothing incorrect shipped to users) but is **NOT recommended**. Chunking (the predecessor
> lever) was re-confirmed as a genuine win at both seeds. The analysis below is retained as-written for the
> record; treat its headline as superseded.

**Date:** 2026-07-01
**Branch:** `feat/relation-reprompt`
**Spec/Plan:** `docs/superpowers/specs/2026-07-01-relation-reprompt-design.md` · `docs/superpowers/plans/2026-07-01-relation-reprompt.md`
**Run:** Modal `gg-bench`, 7B (`qwen2.5-7b-instruct`), `--corpus wiki`, best config (`name_ci` + chunking `(6,2)`), aliased aligner. 19 docs, 65 gold.

## What this tested

The edge-miss diagnostic showed the real-prose residual is relation-never-extracted: the 7B extracts the entities but omits the edge between them. This lever is a gated `GOLDENGRAPH_RELATION_REPROMPT=1` **second pass** that hands the 7B the already-extracted entity list + the full doc text and asks only for the relations among them, appending the found edges (before canonicalization). Central hypothesis under test: **narrowing the *task* (relations over a provided list) beats the full-doc *density* that chunking removed by narrowing the *context*.**

## Result — WIN

| leg | R(B) | P(B) | F1(B) | coverage | components |
|---|---|---|---|---|---|
| 100 control (re-prompt off) | 0.2273 | 1.0000 | 0.3704 | 0.5077 | 14 |
| 101 `RELATION_REPROMPT=1` | **0.3030** | 1.0000 | **0.4651** | 0.4923 | **11** |

- **R(B) +0.076 (+33% relative), F1(B) +0.095 (+26% relative)** — a substantial substrate-quality lift.
- **P(B) held at 1.0** — zero precision cost; the re-prompt did NOT invent spurious edges (the recall-prompt failure mode did not recur).
- **components 14 → 11** — the graph got *more* connected, the opposite of fragmentation.
- **coverage flat (−1 gold mention, noise).** Control 0.5077 exactly reproduces chunking leg 83; re-prompt 0.4923 is one mention fewer, within 7B run-to-run variance.

## The mechanism (honest read: the win came through a different channel than predicted)

The spec predicted the win as raw **coverage** uplift (edge-miss entities gaining an edge → becoming alignment candidates). That is *not* where it showed up — coverage is flat. The lift is in **pairwise R(B)/F1**: the added relations enabled *correct cross-doc entity unification*. More edges → more nodes reachable per doc → gold mentions of the same entity across docs align to the same cross-doc-merged node → higher pairwise recall, and 3 fewer components. Because P(B) stayed at 1.0, every one of those new merges is correct. So the added edges improved the *quality of the clustering* (the actual substrate-as-KB headline, F1) rather than the raw any-node alignment rate. Both are the added relations doing their job; the substrate got better.

**The central hypothesis is confirmed.** Task-narrowing worked over the full-doc text — the 7B, handed the entity list and asked only to connect them, produced correct relations it had omitted in the joint extraction pass, *without* the density that broke joint extraction. Task-narrowing and context-narrowing are both valid axes; this lever exploited the first, chunking the second, and they compose (this ran on top of chunking `(6,2)`).

## Guardrails (all clean)

- **P(B) = 1.0** — no over-connection. The lever asks for relations "grounded in the text" and the 7B respected it; no spurious edges.
- **components down, not up** — no fragmentation; the new edges consolidated the graph.
- Two independent signals moving the right way (R(B)↑, components↓) with precision pinned confirm this isn't a metric artifact.

## Decision

- **Ship the gate**, default-off (opt-in), consistent with the chunking posture. A default-on flip needs the win to hold on a second corpus and to weigh the extra LLM call/doc (one re-prompt per document).
- **The relation-recall thread has its first win.** Entity extraction (name_ci + chunking) and now relation recall (re-prompt) both move the real-prose substrate.

## Honest caveats

- **Small N, one corpus.** 19 docs / 65 gold. The direction is strong and multi-signal (R(B)+33%, F1+26%, components−3, P=1.0), but absolute magnitudes are small-N; confirm on a second corpus before any default-on.
- **Coverage didn't move.** The ~0.49–0.51 any-node alignment rate is unchanged; the win is purely in cross-doc unification quality. There may still be edge-miss entities that remain singletons (no co-referent partner to help pairwise recall).
- **Cost.** One extra full-doc LLM call per document. Cheap on 19 docs; a real cost at corpus scale — part of why the gate stays off.

## Follow-ons

1. **Confirm on a second corpus** before considering default-on for the re-prompt.
2. **REBEL fusion** (`extract_local.rebel_extractor`) remains the next distinct relation-recall lever if more headroom is wanted — local relation extraction fused with LLM entities, complementary to the re-prompt.
3. **Coverage-side residual** — the flat coverage says some gold entities still never get an aligning edge; a future probe could split those (singletons with no co-referent vs still-edgeless) to see if there's more to win.
