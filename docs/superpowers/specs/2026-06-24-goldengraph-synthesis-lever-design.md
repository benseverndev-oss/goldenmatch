# goldengraph — synthesis lever (scoping)

**Status:** scoping / design (not yet implemented)
**Date:** 2026-06-24
**Author:** measure-driven loop
**Parent:** `2026-06-24-goldengraph-phrase-span-extraction-design.md` (which pivoted here)

## 1. Why — the trace says synthesis is HALF the loss

A traced N=50 MuSiQue run on `main` (run 28121496629, `literal_attrs=true`,
`trace=true`, judge=0.34 / answer_match=0.30) classified every miss by stage:

| stage | count | % | meaning |
|-------|-------|---|---------|
| **SYNTHESIS** | **25** | **50%** | answer retrieved INTO the ball, wrong answer written |
| RETRIEVAL-BROKEN-CHAIN | 12 | 24% | in graph, different component from the seeds |
| EXTRACTION | 11 | 22% | gold genuinely not a node |
| RETRIEVAL-BUDGET | 2 | 4% | reachable but outside the budget ball |

**SYNTHESIS is the single largest bucket by a wide margin**, and it is the
cheapest to attack: no graph, extraction, or retrieval change — the answer edge
is already in the retrieved subgraph. The trace prints the exact present edges:

- gold `1599` — ball has `British East India Company -[established in]-> 1599`
- gold `1,438,159` — ball has `The Bronx -[has a population of]-> 1,438,159`
- gold `Karen Fairchild` — ball has `Home Alone Tonight -[features]-> Karen Fairchild`
- gold `The Australian Ballet` — ball has **3** answer-edges (`-[is well known for]->`,
  `-[receives funding through]->`, `-[is represented by]->`)

In each, the LLM had the answer in front of it and wrote something else.

## 2. The HARD constraint — do not strand the answer

A relation-aware **focusing pass that pruned the ball to query-named predicates
was already built and measured WORSE** on the QA-e2e bench (2026-06-22, reverted;
see `answer.py:_retrieve_local` docstring lines 25-29). Root cause: real
LLM-extracted predicates rarely match the query's relation words verbatim, so
predicate-pruning dropped the true chain. The standing lesson recorded in source:
*"Precision is now attacked on the synthesis side, which cannot strand the
answer."*

**So this lever must operate on the FULL ball and never drop an edge.** Anything
that filters/prunes the subgraph before the LLM risks re-introducing the measured
regression. The levers below all preserve every edge.

## 3. Why synthesis fails (hypotheses, ranked)

The retrieved ball is large — 194–478 entities, 213–548 edges in the SYNTHESIS
cases. The answer edge is one line among hundreds, dumped in arbitrary (store)
order. Likely failure modes:

1. **Salience** — the answer edge is buried in a few hundred edges with no
   ordering, so the model's single pass doesn't find/commit to it. (Strongest
   hypothesis — the Australian-Ballet case has the answer present 3× and still
   misses.)
2. **No grounding requirement** — the prompt asks for a multi-hop walk but never
   forces the answer to be backed by a specific edge in the list, so the model
   confabulates a plausible-but-absent answer.
3. **Final-hop direction / selection** — the model picks a plausible wrong edge
   incident to the carried entity (the `_LOCAL_HEAD` already warns about arrow
   direction, so this is the already-attacked part).

## 4. Design — full-ball-preserving synthesis improvements

Two coordinated changes, both keep every edge:

### Lever A — salience ORDERING (not pruning)
Re-order the subgraph's edges (and entity list) so the most question-relevant
appear FIRST, while keeping ALL of them. Ordering signal options, to A/B:
- **embedding cosine** of the question against each edge's `"subj predicate obj"`
  rendered string (robust to predicate-wording mismatch — the reason pruning
  failed — because it scores the whole edge incl. entity names, and it only
  *orders*, never drops);
- a cheap lexical fallback (token overlap of the question with the edge string)
  if embedding-per-edge cost is too high.
The hook is in `ask()` (which has the `embedder`) — re-rank `subgraph["edges"]`
before `synthesize_local`; `_format_subgraph` already renders in list order, so
ordering upstream is a one-line behavioral change with no signature churn. Because
nothing is dropped, the measured pruning regression cannot recur — worst case the
ordering is uninformative and it's the status quo.

### Lever B — answer-grounding / edge citation
Amend the synthesis prompt to require the model to **quote the exact edge from the
Relationships list that yields its final answer** (e.g. an `Evidence: <subj
-[pred]-> obj>` line before `Answer:`). This directly targets failure mode 2 —
the "answer was present, model wrote something else" case — by forcing the answer
to be grounded in a present edge. Parse stays tolerant (`_extract_answer` already
keys on the last `Answer:` line). Risk: over-refusal ("cannot answer" when the
edge phrasing is loose) — measure; keep the "commit to the most plausible"
instruction.

### Optional Lever C — two-pass / self-verify (only if A+B underdeliver)
Pass 1 over the FULL ball: "list the ≤5 edges most relevant to the question"
(no dropping — selection is the model's, full context preserved). Pass 2 answers
from those. Or a verify pass: after `Answer:`, check it's supported by a listed
edge, else re-answer. More LLM cost; hold unless A+B leave SYNTHESIS large.

## 5. Success criterion

**The SYNTHESIS bucket (25/50) shrinks WITHOUT the EXTRACTION / BROKEN-CHAIN
buckets growing.** The trace is the instrument: re-run the same traced N=50 and
read the stage histogram, not just the headline judge. A real win converts
SYNTHESIS misses to hits; a regression would show edges going missing
(EXTRACTION/BROKEN-CHAIN up) — the signature of accidental stranding.

## 6. Plan

1. **Lever A** (edge salience ordering in `ask`, embedding signal) + offline test
   that ordering preserves the full edge set (no drop). Ship behind a flag
   (`GOLDENGRAPH_SYNTH_RANK`?) default-off so the entity-only baseline is
   byte-identical until measured.
2. **Lever B** (edge-citation prompt) — gated, offline prompt-shape test.
3. **Measure** — one traced N=50, A then A+B, watching the stage histogram.
4. If SYNTHESIS still dominates, add **Lever C**.

## 7. The #2 follow-up — cross-doc SCORING-miss (BROKEN-CHAIN)

The 12 BROKEN-CHAIN misses (24%) are the next lever after synthesis. The
shatter-probe verdict on this run: **`{NO-BRIDGE:1, RECALL-miss:1,
SCORING-miss:10}`** — 10 of 12 are SCORING-misses: candidate pairs with
`cosine=1.000 fuzzy=100 shared_token=True` that the cross-doc matcher left in
SEPARATE components (e.g. "Fujian"/"Fujian", "true"/"true", "American
Southeast"/"Southeast region"). #1249 improved the exact-name bridge but did not
close this; the answer's component never connects to the seed's, so the chain is
severed before retrieval even runs. This is a goldenprofile cross-doc-merge
tightening, tracked here as the next lever once synthesis is measured. It is
NOT a synthesis problem (the answer isn't in the ball at all), so it stays
separate.

## 8. Recommendation

Build **Lever A first** (salience ordering — the strongest hypothesis, fully
strand-safe, one-line hook), measure the stage histogram, then add **Lever B**
(grounding) if SYNTHESIS is still the top bucket. Hold C unless needed. Keep the
cross-doc SCORING-miss as the #2 lever. Every step is measured on the same traced
bench, and the strand-safety constraint (§2) is the non-negotiable guardrail.
