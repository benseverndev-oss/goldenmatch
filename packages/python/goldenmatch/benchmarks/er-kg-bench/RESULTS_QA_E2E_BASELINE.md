# GraphRAG QA E2E -- recorded baselines

Durable record of measured `bench-graphrag-qa` headline numbers so every future
change is scored against a fixed point (the auto-generated `results/*.md` are
per-run and not committed). Append a new dated block per measured baseline; do
not rewrite history.

## 2026-06-23 -- goldengraph, MuSiQue N=50 (first non-zero accuracy)

Run: `bench-graphrag-qa` on `main` @ `9a0e272c` (after the anti-shatter engine
#1218, cross-doc linking #1215, and the synthesis-answer-parse fix #1223).
Config: `corpus=musique max_questions=50 ambiguity=0.0 cross_doc_link=1
profile_link=1 profile_merge_threshold=0.72 extractor=api build_debug=1
trace=1`, model `gpt-4o-mini`, budget `$12` (spent `$0.45`).

Headline:

| metric | value |
|---|---|
| answer_match | **0.14** (7/50) |
| exact_match | 0.12 |
| token_f1 | 0.164 |
| support_recall | 0.0 (NOT WIRED -- goldengraph returns no retrieved-fact ids; see engine adapter note) |
| graph | 4813 entities, 457 components (largest 1882) |

Build-debug hotspots (1000 docs, wall 1587s; summed time per step):

| step | summed | share |
|---|---|---|
| extract (LLM) | 8995s | 70% |
| fingerprint (LLM) | 2420s | 19% |
| resolve (goldenmatch) | 743s | 6% |
| pre_embed | 469s | 4% |
| link (cross-doc) | 136s | 1% |
| append | ~0s | ~0% |

LLM extraction + fingerprint = ~89% of build wall. The ER core (resolve) and the
cross-doc linker are not the bottleneck.

Failure-mode split (localize trace, first 10):
- EXTRACTION ~60% -- gold is a non-entity (date/amount/phrase) the entity-graph
  cannot emit (the structural ceiling; quantified going forward by the
  `answer_match (entity-subset)` column).
- SYNTHESIS ~30% -- chain retrieved + connected, wrong node written (addressed by
  the edge-direction prompt fix, PR #1227).
- RETRIEVAL-BROKEN-CHAIN ~10% -- bridge entity still shattered into a separate
  component despite active LLM fingerprints (a matcher-recall/threshold issue,
  not a missing feature).

Targets for the next re-run (same config): `answer_match` and especially
`answer_match (entity-subset)` should rise with PR #1227; the entity-subset
denominator quantifies how much of the 0.14 ceiling is structural.

## 2026-07-20 -- goldengraph, HotpotQA N=50 (first real HotpotQA point + support_recall wired)

Run: `bench-graphrag-qa` run `29781570857` on branch
`claude/goldenmatch-knowledge-graphs-6ifpov` @ `56942e3` (PR #1950 -- adds the
HotpotQA/2WikiMultiHopQA corpora + the OPENAI_BASE_URL empty-string fix that
unblocked the real-LLM run). Config: `corpus=hotpotqa max_questions=50
ambiguity=0.5 (ignored -- HotpotQA has no dial) mode=head_to_head extractor=api
qa_mode=local retrieval_hops=6 node_budget=256`, model `gpt-4o-mini`, budget
`$10` (spent `$0.155`). Pure-graph LOCAL path (no hybrid passages, no
chain-decomposition).

Headline:

| metric | value |
|---|---|
| answer_match | **0.42** |
| exact_match | 0.36 |
| token_f1 | 0.456 |
| **support_recall** | **0.79** (now WIRED -- adapter stamps doc ids + `ask(provenance_out=)`) |
| answer_match (entity-subset) | **0.5484** (n=31/50) |
| answer_type_mix | 31 entity, 11 phrase, 7 date, 1 number |

Read (vs the MuSiQue N=50 point above):
- **support_recall 0.79** is the first real number for this metric -- graph
  retrieval surfaces ~79% of the gold supporting paragraphs. The retrieval layer
  works; the loss is downstream.
- **The retrieval->answer gap is the story:** support_recall 0.79 vs answer_match
  0.42. The graph FINDS the evidence but under-converts it to answers ("barely
  converts at the answer layer", per the ER-answer ablation).
- **Structural ceiling visible:** 19/50 (38%) golds are non-entity
  (date/phrase/number) an entity-graph cannot emit; on the entity-answerable
  subset it is 0.55. Sample failures are the exact pattern -- gold `29 September
  2014` -> pred `Hamid Karzai` (a date question answered with an entity).
- Higher than MuSiQue 0.14 because HotpotQA is 2-hop (vs MuSiQue 2-4) and less
  adversarial. This is the trustworthy baseline the improvement roadmap targets:
  the highest-leverage next step is ANSWER-side (hybrid passages +
  chain-decomposition), not retrieval.

Caveat: `llm_judge` read 0.0 across the board -- the judge was not wired into this
dispatch, so ignore that column; `answer_match`/`support_recall` are the signals.

## 2026-07-20 -- goldengraph, 2WikiMultiHopQA N=50 (harder multi-hop; widens the retrieval->answer gap)

Run: `bench-graphrag-qa` run `29782964786` @ `f776369` (same PR #1950). Config
identical to the HotpotQA point above (`corpus=2wikimultihop max_questions=50
ambiguity=0.5 mode=head_to_head qa_mode=local retrieval_hops=6 node_budget=256`),
model `gpt-4o-mini`, budget `$10` (spent `$0.105`). Pure-graph LOCAL path.

Headline:

| metric | value | vs HotpotQA |
|---|---|---|
| answer_match | **0.30** | 0.42 (down) |
| exact_match | 0.24 | 0.36 |
| token_f1 | 0.306 | 0.456 |
| **support_recall** | **0.805** | 0.79 (**up**) |
| answer_match (entity-subset) | **0.389** (n=36/50) | 0.55 |
| answer_type_mix | 36 entity, 11 phrase, 2 number, 1 date | -- |

The load-bearing read (the two real points TOGETHER):
- **Retrieval is robust across hop depth:** support_recall 0.805 on 2Wiki (2-4 hop,
  comparison/compositional) is even slightly HIGHER than HotpotQA's 0.79 (2-hop).
  The graph keeps surfacing ~80% of gold supporting paragraphs.
- **Answer conversion degrades with hop depth:** answer_match 0.42 (HotpotQA) ->
  0.30 (2Wiki). The retrieval->answer GAP WIDENS from ~0.37 to ~0.50. Same failure
  signature -- comparison "yes" -> pred `Italy`, wrong node on a 2-hop chain.
- **Conclusion for the roadmap:** with retrieval already at ~0.80 on both real
  corpora and the loss concentrated (and growing) at the answer layer, the
  highest-leverage investment is ANSWER-side -- hybrid passages + question->relation
  chain-decomposition -- NOT more retrieval. These two points are the fixed baseline
  those levers must beat.

Caveat: `llm_judge` unwired (0.0) in this dispatch, as above.
