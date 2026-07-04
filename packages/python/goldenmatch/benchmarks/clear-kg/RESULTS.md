# CLEAR-KG — consolidated results

Every number below is produced by a committed `run_*.py` and pinned by an
offline test (`tests/`, 47 passing). Phase 0 proves the *mechanisms* and the
*metrics* on controlled corpora + one real-data (Wikipedia) track; the
competitive numbers on the real packaged frameworks live in the companion
**er-kg-bench** board (see the last section). Reproduce all of it:

```bash
export GOLDENMATCH_NATIVE=0 POLARS_SKIP_CPU_CHECK=1
python run_track_a.py     # extraction (table stakes) + the ER-in-the-metric finding
python run_track_b.py     # corpus-level ER (homograph split-rate)
python run_real.py        # Track B on REAL Wikipedia prose (fetch-on-demand)
python run_track_c.py     # span-grounded faithfulness
python run_track_d.py     # the end-to-end CLEAR composite
python -m pytest tests/ -q
```

## The one-line thesis, measured

> Extraction is a commodity every tool is fine at; the two axes that decide
> whether a KG built from a document trove is *correct* — **corpus-level entity
> resolution** and **span-grounded faithfulness** — are the two no incumbent
> measures, and the two a principled resolver wins. CLEAR-KG measures exactly
> those, and composes them so you can't win one and be hollow on the rest.

## Track B — corpus-level ER (the homograph split-rate)

Headline = of gold mention-pairs sharing a surface but belonging to different
entities, the fraction correctly kept apart. Faithful reimplementations of each
tool's *documented* ER mechanism, on identical inputs.

| corpus | engine | pairwise-F1 | **homograph split-rate** |
|---|---|--:|--:|
| synthetic (60 mentions / 20 entities) | `neo4j_exact` / `neo4j_fuzzy` / `name_cosine` | 0.705–0.713 | **0.000** |
| synthetic | **`goldenmatch`** (neighborhood ER) | **0.889** | **1.000** |
| synthetic (60 entities / 963 confusable) | incumbents | ~0.33 | **0.000** |
| synthetic | **`goldenmatch`** | **0.854** | **0.983** |
| **REAL Wikipedia** (72 mentions / 18 articles / 192 confusable pairs) | incumbents | 0.529 | **0.000** |
| **REAL Wikipedia** | **`goldenmatch`** | **1.000** | **1.000** |

Every `if similar: merge` mechanism scores 0.000 — including on real Wikipedia
prose nobody in this repo authored (the "you wrote the docs" objection, killed).

## Track C — span-grounded faithfulness

Of an emitted triple: is it supported by a span, with a confidence, or invented?
Discriminator = the **distractor** (entities co-occur, relation not stated) +
**hallucinated** triples. 52 candidates (24 supported / 16 distractor / 12 halluc).

| engine (documented mechanism) | support-F1 | **distractor false-support** | hallucination | conf-AUROC |
|---|--:|--:|--:|--:|
| `ungrounded` (LlamaIndex / LangChain default) | 0.632 | **1.000** | 1.000 | 0.500 |
| `sentence_presence` (within-sentence presence) | 0.750 | **1.000** | 0.000 | 0.500 |
| `ontology_conformance` (type matches schema) | 0.632 | **1.000** | 1.000 | 0.500 |
| **`relation_aware`** (span states *this* relation + confidence) | **1.000** | **0.000** | 0.000 | **1.000** |

Only relation-aware grounding refuses the distractor and is the only engine
emitting a calibrated confidence (AUROC 1.000, ECE ≈ 0.02) — the "never black
box" axis measured directly.

## Track A — extraction (table stakes), and the metric inherits the moat

Convention-matching triple-F1 (Text2KGBench / Re-DocRED: exact + relaxed). Not a
bid to beat the LLM pack (SOTA ~74.6 / 80.7) — the finding is that canonicalized
("relaxed") matching is itself an ER problem.

| matching mode | F1 | homograph-recall |
|---|--:|--:|
| `exact` (canonical string only) | 0.250 | 0.000 |
| `relaxed` (alias, string-based — the field's relaxed) | 0.750 | **0.500** |
| `er_aware` (alias + co-mention) | **1.000** | **1.000** |

`exact` under-counts (alias penalty); `relaxed` mis-credits homographs; only
ER-aware canonicalization is correct.

### Real-prose extraction on Re-DocRED (the competitive floor)

The synthetic Track A proves the metric; the *competitive* number needs real
prose + gold triples. `run_redocred.py` runs an LLM relation extractor on the
**Re-DocRED** dev set (real Wikipedia, gold document-level triples, 95-relation
closed schema — the standard the SPEC benchmarks against) and scores micro
relation-F1.

Floor→ceiling sweep, same 20 docs / 853 gold triples / 95-relation closed schema,
zero-shot single-pass, `temperature=0` (chat) or default (reasoning):

| extractor | micro-P | micro-R | **micro-F1** | wall |
|---|--:|--:|--:|--:|
| `gpt-4o-mini` (floor) | 0.339 | 0.097 | **0.151** | 90s |
| `gpt-4.1-mini` | 0.448 | 0.121 | **0.190** | 69s |
| `gpt-4o` | 0.481 | 0.122 | **0.195** | 58s |
| `gpt-4.1` (chat ceiling) | 0.399 | 0.130 | **0.196** | 47s |
| `gpt-5-mini` (reasoning) | 0.473 | 0.182 | **0.262** | 704s |
| **`gpt-5`** (reasoning) | 0.604 | 0.184 | **0.282** | 1439s |

_Reference: Re-DocRED relation-F1 SOTA **~80.7** (fine-tuned BERT/DREEAM) · **~74.6** (strong LLM)._

**Read this honestly.** None of these is a goldenmatch capability — goldenmatch does
ER, not extraction; extraction is the commodity input. The sweep maps where a
zero-shot LLM extractor lands on the real standard benchmark:

- **The chat family plateaus at ~0.196**, all recall-bound at ~0.10–0.13. A bigger
  chat model buys *precision* (0.34→0.48), not the recall document-level RE needs
  — the models emit ~11–14 triples/doc against ~43 dense gold (Re-DocRED annotates
  inverse and implicit relations a single pass misses).
- **The reasoning tier breaks that plateau:** `gpt-5-mini` F1 **0.262**, `gpt-5`
  F1 **0.282** (+34–44% over the best chat model) via multi-step inference — at
  14–29× the wall-clock (704s / 1439s vs ~50s).
- **But recall hits a wall at ~0.18 for BOTH reasoning models** (0.182 / 0.184):
  going from `gpt-5-mini` to full `gpt-5` buys *precision* (0.47→0.60), not recall.
  Single-pass zero-shot — even the strongest reasoning model — cannot recover the
  dense inverse/implicit gold relations; that needs the multi-pass + retrieval
  scaffolding the published ~74.6 "LLM" SOTA uses. So the whole sweep sits well
  below fine-tuned SOTA (~0.81), and the gap is structural (recall), not model size.
- _(`gpt-5` was first mis-measured at the old 6000-token budget, where hidden
  reasoning exhausted the completion budget → empty output on 10/20 docs; the
  reasoning-model default is now 16000, and the 0.282 row above is the clean re-run,
  0 empties.)_

The takeaway is the thesis: extraction is the hard, LLM-bound, reasoning-hungry
**commodity** axis you buy — the durable win is the ER + faithfulness layer, which
is why CLEAR-KG puts the moats there. Harness offline-tested
(`tests/test_redocred.py`, mock); run with
`OPENAI_API_KEY=... python run_redocred.py --docs N --model <name>`.

## Track D — the CLEAR composite (headline)

CLEAR = harmonic mean of {extraction-F1, ER-F1, grounded-&-correct}, so it's
dragged to the weakest axis. A system = shared extractor × ER engine × grounding
engine.

| system | extract-F1 | ER-F1 | grounded-ok | **CLEAR** |
|---|--:|--:|--:|--:|
| `incumbent` (name-merge + presence) | 1.000 | 0.800 | 0.750 | **0.837** |
| `er_only` (neighborhood ER + presence) | 1.000 | 1.000 | 0.750 | **0.900** |
| **`goldenmatch`** (neighborhood ER + relation-aware) | 1.000 | 1.000 | 1.000 | **1.000** |

Perfect extraction *and* perfect ER can't rescue hollow grounding (`er_only`
drops to 0.900). You must win **both** moats to top the end-to-end score.

## Companion: the real packaged frameworks (er-kg-bench)

CLEAR-KG proves the mechanisms on controlled + real-Wikipedia corpora. The
sibling board [`../er-kg-bench`](../er-kg-bench) runs the *real decision code* of
the packaged frameworks (Microsoft GraphRAG, Neo4j LLM KG-Builder,
neo4j-graphrag, LlamaIndex PGI, KGGen/Cognee, mem0, Graphiti) against goldenmatch
on **real Wikidata/RxNorm-grounded** records, with fidelity tiers. The homograph
split-rate is now a first-class column there too (`erkgbench/metrics.py::
homograph_split_rate`).

**An honest bridge finding.** On er-kg-bench's current records (name + type +
context, 2 real homographs — "Michael Jordan", "Georgia"), **every resolver
scores split-rate 0.000, goldenmatch included.** That is not a failure of
goldenmatch; it is the whole point: the homographs share the *exact* surface, so
on records that carry no co-mention neighborhood, nothing can separate them —
even exact-match merges identical strings. The split is only *recoverable* with
the structural signal CLEAR-KG Track B supplies and standard record fields don't
(goldenmatch 1.000 on the Wikipedia track above). Together the two boards say:
the incumbents can't separate real homographs, and neither can any resolver
without the neighborhood structure — which is exactly why the resolution layer,
and a benchmark that carries the structure, matter.

## Status & next

Phase 0 (mechanisms + metrics + real-data tracks) is complete and pinned by tests.
The **real-prose Track A number is now measured** (Re-DocRED, above) — the
zero-shot LLM floor that confirms extraction is the commodity axis. Still
runnable-with-more-setup: an NLI backstop for the ER-aware matcher / relation-aware
grounder on paraphrased relations (needs torch or an LLM-judge lane). Next:
broaden the Wikidata homograph seed set so the real-framework split-rate (in the
er-kg-bench companion) has more support, and the Text2KG@ISWC write-up
(`PAPER.md`, TODO).
