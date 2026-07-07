# Path-aware retrieval — lever measurements

Spec: `docs/superpowers/specs/2026-07-07-goldengraph-path-aware-retrieval-design.md`.
Plan: `docs/superpowers/plans/2026-07-07-goldengraph-path-aware-retrieval.md`.
Motivation: the path-selection gap diagnosed in `RESULTS_ER_ANSWER_ABLATION.md`
("Follow-up diagnosis") — a correct-but-noisy ~45-entity neighborhood that *contains*
the answer chain (bridge-recall 1.0) yet answers only ~0.275, because the model can walk
the chain in isolation but can't find it among real sibling edges.

Each lever is checked against the **recall guard first** (LLM-free): the pruned/walked
subgraph must not drop the answer chain vs the unpruned ball. A lever that raises
answer-match by *stranding* the answer is a regression, not a win — so a recall drop STOPs
the lever before any paid answer-match run. Harness: `erkgbench/qa_e2e/retrieval_levers.py`
(`measure_lever`, multi-seed via `seed_by_query_fn(embedder, k=5)` — the same `k=5` regime
the product `ask` local path uses; the stock ablation's single seed makes
`filter_subgraph_to_paths`'s anchor-to-anchor bridge inert, review finding #2).

## Lever A — `filter_subgraph_to_paths` on the local ball — REFUTED (recall guard, ~$0)

Product gate `GOLDENGRAPH_LOCAL_FILTER=path` (default OFF, byte-identical off) wires the
existing topology prune into `ask`'s local branch. Measured on the engineered corpus,
n=40, `k=5` seeds (`text-embedding-3-small`), amb ∈ {0, 0.5, 1.0}. **No answer-match LLM
spend** — the recall guard alone is decisive.

**Bridge-recall of the pruned subgraph vs the unpruned ball, by halo** (whole_chain):

| dial | amb | none | halo=1 | halo=2 | halo=3 |
|------|-----|------|--------|--------|--------|
| oracle | 0.0 | 1.000 | 0.667 | 0.897 | 0.949 |
| oracle | 0.5 | 1.000 | 0.600 | 0.825 | 1.000 |
| oracle | 1.0 | 1.000 | 0.538 | 0.846 | 0.949 |
| goldengraph | 0.0 | 0.949 | 0.641 | 0.846 | 0.897 |
| goldengraph | 0.5 | 0.575 | 0.275 | 0.325 | 0.450 |
| goldengraph | 1.0 | 0.744 | 0.385 | 0.538 | 0.641 |

At the plan's default `halo=1` the prune strands **33–46%** of answer chains even on the
`oracle` dial (perfect ER) — the recall guard STOPs Lever A per the Task 1.3 decision gate.

**Is there a recall-safe halo?** A bigger halo recovers recall — but only by re-importing
the neighborhood. Node retention (fraction of the ball's entities kept), `oracle` dial:

| halo | recall (amb 0/0.5/1.0) | node retention | pruned |
|------|------------------------|----------------|--------|
| 1 | 0.67 / 0.60 / 0.54 | ~0.57 | 43% |
| 2 | 0.90 / 0.83 / 0.85 | ~0.88 | 12% |
| 3 | 0.95 / 1.00 / 0.95 | ~0.95 | **5%** |

Recall and retention move in lockstep: **recall-safe ⟺ almost-no-pruning.** At `halo=3`
(the recall-safe point) the prune keeps ~95% of the ~45-entity ball, so synthesis sees
essentially the same neighborhood — the lever is inert and cannot move answer-match. There
is no operating point where the prune both keeps the chain AND meaningfully shrinks the
neighborhood.

**Why (confirms the spec's predicted blind spot).** `filter_subgraph_to_paths` keeps seeds
+ *anchor-to-anchor* shortest paths + a `halo`-hop neighborhood. The engineered answers sit
at the *end* of a single-anchor multi-hop chain, not *between* two seeds — so only `halo`
reaches them, and the halo big enough to reach the chain end re-imports the distractors the
prune was meant to remove. Anchor-to-anchor topology is the wrong primitive for
single-anchor chains.

**Verdict:** Lever A refuted for ~$0. The gate ships (default OFF, a real capability + the
reusable measurement harness for Lever C) but is NOT a default candidate on this corpus.
Route → Phase 2 (Lever B, engineered-only mechanism check) / Phase 3 (Lever C —
answer-candidate-scored prune, recall-safe by construction, which is the primitive Lever A
lacks).

**Caveats.** Engineered/synthetic corpus (a diagnosis instrument, absolute numbers low by
construction), seed 7, n=40, `text-embedding-3-small` seeding. The recall guard is
corpus-topology-driven and LLM-free, so it is robust to LLM noise, but a product default
still needs the real-corpus (2WikiMultiHopQA) gate.
