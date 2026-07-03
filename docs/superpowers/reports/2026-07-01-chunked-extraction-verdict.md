# Chunked Extraction — Verdict

**Date:** 2026-07-01
**Branch:** `feat/chunked-extraction`
**Spec/Plan:** `docs/superpowers/specs/2026-07-01-chunked-extraction-design.md` · `docs/superpowers/plans/2026-07-01-chunked-extraction.md`
**Run:** Modal `gg-bench`, 7B (`qwen2.5-7b-instruct`), `--corpus wiki`, `GOLDENGRAPH_XDOC_KEY=name_ci`, **aliased aligner** (on `main` via #1345). 19 docs, 65 gold mentions.

## What this tested

The recall-tuned prompt lever was REFUTED — the real-prose extraction-recall miss is **density**, not framing: the 7B attends over a whole ~20-sentence Wikipedia lead in one pass and drops entities. This lever splits each doc into **overlapping sentence windows**, extracts each independently, and unions the results (concat mentions, offset relationship/attribute indices) before resolution. Unlike the recall prompt, each short window preserves *both* its entities and its relations. Gated `GOLDENGRAPH_CHUNK_EXTRACT=1`, env-tunable window size / overlap, swept over 3 `(size, overlap)` points against a fresh control.

## Result — WIN at (6, 2)

| leg | (size, overlap) | coverage | R(B) | P(B) | F1(B) | components |
|---|---|---|---|---|---|---|
| 80 | control (off) | 0.4462 | 0.3182 | 1.000 | 0.4828 | 13 |
| 81 | (4, 1) | 0.3692 | 0.1162 | 0.958 | 0.2072 | 10 |
| 82 | (3, 1) | **0.4769** | 0.2020 | 0.930 | 0.3320 | **26** |
| 83 | **(6, 2)** | **0.5077** | **0.3434** | 1.000 | **0.5113** | 14 |

**(6, 2) lifts every signal with the guardrails intact:** coverage +0.062 (0.446 → 0.508), R(B) +0.025 (0.318 → 0.343), F1 +0.028 (0.483 → 0.511), **P(B) held at 1.0, components essentially flat (13 → 14)**. This is the first substrate improvement in the extraction-recall thread.

## The sweep shape is the finding

The three chunked points trace exactly the tension the spec predicted:

- **(3, 1) small window** — coverage *up* (0.477) but R(B) *down* (0.202), components **double** (13 → 26), P(B) drops to 0.93. Classic fragmentation: a 3-sentence window recovers more entities but severs cross-sentence relations, so the graph shatters into edge-starved components. This is the REFUTED signature (the exact trap the recall prompt fell into).
- **(4, 1)** — the worst point: every signal below control. A 4-sentence stride-3 window is the unhappy middle — small enough to lose relations, not small enough to maximize entity recall, and the lost edges drag coverage down too (the aligner is edge-centric).
- **(6, 2) large window + overlap** — the sweet spot. Six sentences keep most cross-sentence relations inside a single window; +2 overlap stitches the boundaries; and chunking ~20 sentences into ~7 windows still lifts entity recall over the single dense pass. Recall goes up *without* fragmenting.

So the mechanism is confirmed: **window size trades entity recall against relation preservation**, and only a large-enough window with overlap gets the recall lift while keeping the graph connected. Component count + P(B) were the guardrails that distinguished the real win (83) from fragmentation dressed as coverage (82).

## Decisions

- **Ship the gate**, default-off (opt-in). **Shipped default is `(6, 2)`** — the measured winner — so an opt-in with no tuning lands on the good config, not the degrading `(4, 1)` the plan originally proposed. `_chunk_params` and its test were updated to `(6, 2)` after the sweep.
- **Not flipped default-on.** The lift is real and directionally clean but modest on a 19-doc / 65-gold corpus (+0.062 coverage ≈ 4 more gold mentions aligned). A default-on flip needs the win to hold across more than the wiki corpus and at a 7-10× extraction-call cost — a separate decision.

## Honest caveats

- **Small N.** 19 docs / 65 gold. The direction is clean across all four signals and the sweep shape is mechanistically coherent, but the absolute magnitudes are small — read this as "chunking with a large-enough window is the right lever," not "+6pp coverage is a stable number."
- **Cost.** (6, 2) is ~4 windows/doc here; (3, 1) would be ~10×. The gate stays off partly for this — chunking is an opt-in quality/throughput trade, not free.
- **Only the surface-realizable ceiling moved.** Coverage 0.508 still leaves ~half the gold unaligned; the remaining miss is entities the 7B never emits in any window. The next lever attacks *that*.

## Follow-ons

1. **GLiNER hybrid** (`extract_local.gliner_extractor`) — high-recall NER for entities + LLM for relations, composed with chunking. The next extraction-recall lever for the residual ~0.49 miss.
2. **Confirm (6, 2) on a second corpus** before any default-on consideration.
3. **Larger windows** — the sweep stopped at 6; (8, 2)/(10, 3) might recover more edges still, at higher cost. Worth one more point if GLiNER underdelivers.
