# GLiNER Entity-Recall Probe — Verdict

**Date:** 2026-07-01
**Branch:** `feat/gliner-recall-probe`
**Spec/Plan:** `docs/superpowers/specs/2026-07-01-gliner-recall-probe-design.md` · `docs/superpowers/plans/2026-07-01-gliner-recall-probe.md`
**Run:** Modal `gg-bench`, 7B (`qwen2.5-7b-instruct`), `--corpus wiki`, best config (`name_ci` + chunking `(6,2)`), aliased aligner. 19 docs, 65 gold.

## What this tested

Chunking (#1350) lifted real-prose substrate coverage to ~0.51. The residual was assumed to be **entities the 7B never emits** — an NER-recall gap GLiNER could close. This probe measured that assumption before building the hybrid: split the unaligned gold into **NER-miss** (no node exists — GLiNER-addressable) vs **edge-miss** (node exists, edge dropped — GLiNER can't help), and measured GLiNER's recovery of the NER-miss subset.

## Result — REFUTED (and the assumption was wrong)

| threshold | gliner_recall | llm_coverage | n_missed | **ner_miss** | **edge_miss** | ner_recovered | junk_rate |
|---|---|---|---|---|---|---|---|
| 0.4 | 0.5231 | 0.4923 | 33 | **0** | **33** | 0.0000 | 0.8884 |
| 0.3 | 0.5231 | 0.4923 | 33 | **0** | **33** | 0.0000 | 0.8980 |

**Every one of the 33 unaligned gold mentions is edge-miss. Zero are NER-miss.** The entity is already a node in the built graph; it simply has no surviving edge in its doc, so the edge-centric aligner can't reach it. 32 aligned + 33 exist-but-unedged = **all 65 gold entities are already extracted**. GLiNER — which only adds entities — has nothing to recover. `ner_recovered_frac = 0/0 = 0` at both thresholds, robustly.

## The real finding: the residual is an EDGE gap, not an entity gap

This is the valuable part, and it redirects the arc:

- **Entity extraction on real prose is essentially solved** by the current stack (name_ci + chunking). Every gold entity that the wikilinks name is present as a node. There is no entity-recall headroom for GLiNER to exploit.
- **The real-prose substrate ceiling is edge/relation recall** (and edge *survival* through resolution): the 7B extracts the entities but drops the relation between them, or the fuzzy resolver collapses a doc's src+dst into a self-loop that `build_batch` discards. Either way the node exists edgeless, invisible to the aligner.
- This coheres with the whole thread: coverage was always edge-gated (the aligner's candidate set is edge-derived), chunking WON by producing *more edges* per short window, and the recall-prompt was REFUTED precisely because it traded edges for entity noise. The consistent signal across three levers is that **edges, not entities, are the constraint.**

`gliner_recall = 0.52 ≈ llm_coverage = 0.49` independently confirms it: GLiNER's raw entity recall is no better than the pipeline's, and even the entities it surfaces are all already in the graph.

## Guardrails / caveats (as designed)

- **`junk_rate ≈ 0.89` is expected and NOT a precision indictment.** Gold is only the 65 *wikilinked* mentions; a real lead names many legitimate non-wikilinked entities that GLiNER correctly surfaces, all of which land in the junk numerator. The high value confirms GLiNER surfaces *many* entities — and still zero of them are NER-addressable misses, which only strengthens the refutation.
- **Threshold-robust.** 0.3 vs 0.4 are identical on the split (0 NER-miss either way); lowering the threshold surfaces more entities but no new *missed* gold, because there are none to surface.
- **Seq-length caveat is moot here.** The truncation concern biases toward REFUTED by undercounting GLiNER recall — but the refutation isn't driven by GLiNER's recall (0.52 is healthy); it's driven by `ner_miss = 0`. Even perfect GLiNER recall changes nothing when the residual contains zero NER-miss. No chunked-GLiNER sanity pass needed.

## Decision

- **Do NOT build the GLiNER hybrid.** The measure-first gate did exactly its job: ~one Modal run refuted a plausible lever before any linking pipeline was written.
- **The extraction-recall thread closes at chunking.** name_ci + chunking got the entities in; entity recall is not the frontier.
- **Next frontier: edge/relation recall + edge survival.** Two candidate sub-projects, to be scoped separately:
  1. **Relation recall** — the 7B extracts entities but drops the edge between them. Levers: relation-focused prompting, per-window relation density, or a dedicated relation extractor (e.g. REBEL, already stubbed in `extract_local`) fused with the LLM.
  2. **Edge survival through resolve** — quantify how many edges die as dropped self-loops when the fuzzy resolver merges a doc's src+dst. The `GOLDENGRAPH_SUBSTRATE_RESOLVER=exact` diagnostic lever already exists to isolate this. If a material share of the 33 edge-misses are resolver-induced self-loops, that's a cheaper fix than relation extraction.

The honest next step is a **diagnostic split of the 33 edge-misses**: relation-never-extracted vs edge-dropped-by-resolver. That decides which of the two frontiers to pursue.

## Shipped

Measurement tooling only (`gliner_probe_report` + `--gliner-probe` runner + `gliner` in the Modal image). No engine behavior change. The probe is reusable for any future NER-vs-edge diagnosis.
