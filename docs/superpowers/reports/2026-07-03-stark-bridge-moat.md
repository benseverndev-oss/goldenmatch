# Alias-injected STaRK, Case B (bridge fragmentation) — verdict (conclusive negative)

**Question:** with the Case-A confound removed, does goldenmatch's resolver measurably
recover STaRK retrieval quality that ad-hoc dedup loses?

**Answer: no — directionally present but negligible (~1 question), and this
concludes the STaRK moat arc.** The corrected instrument works (confound gone, dense
control flat, ER mechanically merges what exact can't), but the retrieval moat is
+0.005 recall@20 (ER vs ad-hoc) — within noise. STaRK-PRIME is text-rich and
dense-dominated, so the graph path is rarely the only route to an answer, so
restoring a severed path rarely changes what is retrieved. Run on Modal (A10G,
64GB), PRIME, 200 queries, k=3, bridge_cap=8, seed=0, Ollama `nomic-embed-text`.

## Design (the correction to Case A)

Case A (2026-07-03, PR #1407) fragmented the **gold answers** + scored by canonical
equivalence — a confound: 3 aliases = 3 retrieval chances at the gold, so
fragmentation *helped*. Case B fixes it: fragment the gold answers' **1-hop
neighbors** (the BRIDGE), keep answers INTACT. Gold stays a single node (no
equivalence inflation); fragmenting a neighbor splits its edges + text, severing the
route a graph walk takes from a dense seed to the answer. Moat now reads on the
**GRAPH arm**; **dense = control** (answers untouched → should stay flat).

## Raw numbers

```
inject: targets=1639 neighbors  k=3  nodes 129375->132653 (aliases=4917)
                          fragmented   adhoc     er        clean
DENSE  recall@20            0.244      0.258     0.258     0.261     (ER-adhoc +0.000)
GRAPH  recall@20            0.183      0.185     0.190     0.213     (ER-adhoc +0.005, ER-frag +0.007)
ER clusters: 131099 (of 4917 aliases -> ~1554 merges); build ~1090-1240s/method.
```

## Reading

1. **Confound removed — dense control is FLAT.** Dense recall@20 barely moves
   (fragmented 0.244 → adhoc/er 0.258 ≈ clean 0.261). Fragmenting non-answer
   neighbors adds a little index noise (the 0.244 dip) that resolution cleans up,
   but there is no lottery-ticket inflation. The instrument is now correct.
2. **The moat is in the RIGHT DIRECTION on the graph arm.** `ER > adhoc > fragmented`
   (0.190 > 0.185 > 0.183): ER re-merges the severed bridge and the walk recovers a
   bit where exact-match cannot. Direction confirms the mechanism.
3. **...but the MAGNITUDE is negligible.** ER−adhoc = +0.005 recall@20 over 200
   queries ≈ **one question**. Within noise. No condition recovers to the clean
   graph number (0.213); best is er 0.190. Graph stays far below dense everywhere
   (0.190 vs 0.258), consistent with every prior run: the naive 1-hop walk is weak.
4. **ER mechanically works at scale** — ~1,554 alias merges (131,099 clusters vs
   132,653 fragmented). The resolver does merge variant surface forms exact-match
   leaves split; it just doesn't move the retrieval needle here.

## Why the moat is invisible on STaRK retrieval (the root cause)

For a severed bridge to matter, a query's answer must be reachable ONLY via the
graph walk (dense misses it directly) AND the specific fragmented neighbor must be
the bridge the walk needs. On text-rich STaRK-PRIME, dense answers most queries
directly, so the graph path is load-bearing for only a small slice — and restoring
it (what ER does) changes retrieval for that slice alone. This is the SAME root cause
as the vanilla and full-text runs: STaRK retrieval is a text problem; structure (and
therefore resolution-of-structure) is rarely the bottleneck.

## Conclusion — the STaRK moat arc ends here (honestly)

Across four runs the finding is consistent and now conclusive:

| run | finding |
|-----|---------|
| vanilla (names) | graph "+39%" — a weak-baseline artifact |
| full-text (fair dense) | graph delta INVERTS (−18%); dense is all you need |
| alias, Case A | confounded instrument (equivalence scoring inflates fragmentation) |
| alias, Case B | confound removed; moat directionally real but negligible (+0.005 ≈ 1q) |

**goldenmatch's ER is not un-valuable — it demonstrably merges variant surface forms
that exact-match can't (proven in the `stark_resolve` unit test AND at scale). It
simply does not move RETRIEVAL on a text-rich, dense-dominated benchmark, because
retrieval there does not depend on the resolved structure.** The moat lives where
resolution is THE bottleneck — deduplication / entity-resolution tasks (the suite's
actual home turf), not semi-structured retrieval. Recommend closing the STaRK
program on this honest negative rather than chasing further injection knobs; the
instrument is now sound and the signal is genuinely absent.

Entry: `scripts/distill/modal_stark.py --kb prime --sample 200 --inject --bridge --k 3`.
Harness (all box-TDD'd): `stark_inject` (+`bridge_targets`), `stark_resolve`,
`stark_moat`, `evaluate(id_map=)`.
