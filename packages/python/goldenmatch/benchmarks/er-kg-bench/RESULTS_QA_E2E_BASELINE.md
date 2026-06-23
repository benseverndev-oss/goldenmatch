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
