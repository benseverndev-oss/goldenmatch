# Stage-2-C: Surface-Bridged Retrieval — Validation Verdict (CONSTRUCTION-CEILING NULL)

**Date:** 2026-06-30
**Spec:** `docs/superpowers/specs/2026-06-30-stage2c-surface-bridged-retrieval-design.md`
**Plan:** `docs/superpowers/plans/2026-06-30-stage2c-surface-bridged-retrieval.md`

## Run config

- Corpus: MuSiQue-Ans, seeded subset, **N=20** (matched A/B on the identical questions).
- Engine: goldengraph, open extraction, `qwen2.5:7b-instruct` + `nomic-embed-text`, Modal A10G, fair metric.
- A: `GOLDENGRAPH_QA_MODE=auto` (bridge OFF = the `local` baseline).
- B: `+ GOLDENGRAPH_RETRIEVAL_BRIDGE=1` (bridge ON).

## Result (matched N=20 A/B)

| metric | bridge-OFF (local) | bridge-ON |
|---|---|---|
| `answer_match` | **0.15** | **0.00** (worse) |
| `support_recall` | 0.65 | 0.725 |
| SYNTHESIS `same_component=False` (islands) | 6-of-7 | 6-of-7 (unchanged) |

**Verdict: CONSTRUCTION-CEILING NULL.** Surface-bridging did NOT reconnect the disconnected components
and it tanked `answer_match` (0.15 → 0.00).

## Why (both failure modes, both pointing the same way)

1. **Islands persist 6-of-7 in BOTH arms.** Bridging unions same-NAME siblings; the components stayed
   disconnected, so the disconnection is NOT name-based under-merge. On real prose the bridge entity is
   phrased differently across paragraphs (coref/alias), so `seeds_by_name` cannot unify it. The graph is
   **genuinely** fragmented.
2. **answer_match → 0.00.** The bridged ball is a connectivity-SUPERSET — it pulls in same-name siblings'
   unrelated neighborhoods (on a ~2-4k-entity graph, generic names collide), adding noise that degrades
   synthesis to zero.

The A/B rules out a bug: bridge-OFF scores a normal **0.15** (the best real-corpus number to date), so
the bridge-ON 0.0 is genuinely caused by bridging, not a hard subset.

## Disposition

- **The code ships, default-off.** `_retrieve_local_bridged` + the `GOLDENGRAPH_RETRIEVAL_BRIDGE` gate
  are merged behind the default-off flag (16 tests green; default path byte-identical). It is a clean,
  tested capability that does not help on this stack. **Do NOT enable.**
- **Cheap connectivity levers are exhausted.** Across stage-2 we have now measured out THREE in a row:
  self-consistency (2-B, null), cross-doc-link (2-C diag, ineffective + too expensive), surface-bridge
  (2-C, null + harmful). All fail for the same reason: **the 7B builds a fragmented graph on real
  prose, and no cheap post-hoc lever can manufacture a connection the extraction never created.**

## The earned finding

Real-corpus multi-hop QA is bottlenecked by **7B graph-construction quality** (entity consistency across
paragraphs), not by retrieval or synthesis post-processing. `support_recall ≈ 0.65–0.72` confirms the
evidence is retrieved; the graph wiring it together is the ceiling. The remaining real fixes are bigger
programs, not cheap levers:
- **Hand synthesis the raw passages (hybrid mode)** — sidesteps the fragmented graph by reading the
  answer from text. (Stage-2-D: unblock hybrid on the local stack via the nomic passage embedder.)
- **A stronger extractor** (frontier model or fine-tuned distill) — fixes construction at the source
  (costs API spend / training).

## Lesson

`in_ball=True` is not `reachable`. The localize `same_component` field revealed that a chunk of the
"SYNTHESIS" bucket was really connectivity failure. When a retrieval lever is proposed, check whether the
disconnection is *bridgeable* (name-based under-merge) or *genuine* before building — here it was genuine,
and bridging both failed to help AND added noise. Measured in one matched A/B, recorded, moved on.
