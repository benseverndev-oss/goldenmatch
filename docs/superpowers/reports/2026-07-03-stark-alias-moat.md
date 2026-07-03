# Alias-injected STaRK ER-moat — verdict (INCONCLUSIVE: confounded instrument)

**Question:** does goldenmatch's resolver recover retrieval quality that ad-hoc
exact-match dedup loses, on a real STaRK KB corrupted with alias duplicates?

**Answer: inconclusive — the experiment as designed is CONFOUNDED.** Answer-entity
fragmentation + canonical-equivalence scoring makes fragmentation *help* dense
retrieval (more aliases = more chances at the gold), so "more resolution → lower
recall" is a scoring artifact, not evidence about ER. The thesis is neither
confirmed nor refuted; the instrument is wrong. Run on Modal (A10G, 64GB), PRIME,
200 test queries, k=3, seed=0, Ollama `nomic-embed-text`.

## Raw numbers

```
inject: targets=474 gold entities  k=3  nodes 129375->130323 (aliases=1422)
[none  fragmented] clusters=130323  dense recall@20=0.365 hit@1=0.180 mrr=0.249 | graph 0.308
[exact ad-hoc]     clusters=130041  dense recall@20=0.288 hit@1=0.115 mrr=0.180 | graph 0.221
[er    goldenmatch]clusters=129420  dense recall@20=0.240 hit@1=0.045 mrr=0.109 | graph 0.224

DENSE recall@20:  fragmented=0.365  adhoc=0.288  er=0.240  clean=0.261 (PR #1402)
                  ER-adhoc=-0.048   ER-fragmented=-0.125   clean-fragmented=-0.104
```

## The confound (why this is inconclusive, not a refutation)

The design fragmented each **gold answer entity** into k=3 alias nodes and scored by
**canonical equivalence** (a hit = retrieving ANY alias of the gold entity). Under
that scoring, splitting the gold into 3 nodes gives the dense retriever **3 separate
embeddings competing for a top-20 slot instead of 1** — three lottery tickets. So
fragmentation *raises* recall (0.365 vs clean 0.261, `clean-fragmented = -0.104`),
and merging the aliases back *removes* tickets, *lowering* recall
(fragmented 0.365 → adhoc 0.288 → er 0.240). The multiple-chances effect dominates
the per-alias text-dilution the design assumed would hurt dense. The intended signal
("fragmentation hurts, ER recovers") is inverted by the scoring, so the ER−adhoc
margin (−0.048) says nothing about the moat.

The build-in guard caught it: the report's rule "CHECK clean-fragmented FIRST — if
not a real drop, inconclusive" fired exactly here (fragmentation *helped*).

## What IS real underneath (secondary, swamped)

- **ER mechanically works.** goldenmatch merged 903 of the ~948 possible alias
  merges (129,420 clusters vs 130,323 fragmented singletons; 1422 aliases → ~519
  clusters). The resolver does re-merge variant surface forms exact-match leaves
  split — the `stark_resolve` unit test's "moat in miniature" holds at scale.
- **`er` dense (0.240) slightly undershoots clean (0.261).** If ER re-merged the 3
  aliases perfectly into the original node, `er` should ≈ clean. The ~0.02 shortfall
  suggests the zero-config dedupe **over-merged** a few distinct entities with
  similar names (474 targets, name-variants → some cross-entity collisions) — a real
  ER-precision finding, but far too small to read against the scoring artifact.
- **Graph ≤ dense in every condition** (0.308/0.221/0.224 vs 0.365/0.288/0.240),
  consistent with PR #1402: the naive 1-hop walk doesn't help. The gap narrows under
  `er` (0.224 vs 0.240) but nothing to hang a moat on.

## Why the design was wrong, and the fix

Fragmenting the **answer** entity is the mistake: the answer is exactly what you
retrieve, so splitting it into equivalence-scored copies adds retrieval surface
instead of removing it. To make fragmentation genuinely HURT, corrupt the **path to**
the answer, not the answer:

- **Case B (deferred in the spec, now the clear next cut): fragment BRIDGE / path
  entities, keep gold answers intact.** A query reaches its answer by walking through
  intermediate entities; fragment those so the seed connects to bridge-alias-1 while
  the answer connects to bridge-alias-2 — the path is *severed*, and only ER
  re-merging the bridge restores it. Gold ids stay single (no equivalence-scoring
  inflation), and the effect lands on the GRAPH arm where structure is load-bearing.
- **Alternative:** keep answer-fragmentation but score without alias-count reward —
  e.g. designate ONE alias as the true answer and the other k−1 as **distractors**
  (not gold-equivalent), so fragmentation adds noise, not chances.

Either removes the multiple-chances confound. Case B is the sharper test and matches
the original moat intuition (resolution restores a broken retrieval path).

## Status

The harness is sound and reusable (`stark_inject` / `stark_resolve` / `stark_moat` /
`evaluate(id_map=)`, all box-TDD'd; the resolver + collapse mechanics verified). The
NEGATIVE is about the experimental *instrument*, and the discipline caught it before
it was mis-sold as a result. Next: re-run as **Case B (bridge fragmentation, gold
intact)** — a new injection target selector + no equivalence scoring, reusing the
same resolve/collapse/materialize path.

Entry: `scripts/distill/modal_stark.py --kb prime --sample 200 --inject --k 3`.
