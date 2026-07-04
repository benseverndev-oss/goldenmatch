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

Phase 0 (mechanisms + metrics + one real-data track) is complete and pinned by
tests. Blocked-here-but-runnable-with-a-key: an LLM extraction pass for Track A's
competitive number on real prose, and an NLI backstop for the ER-aware matcher /
relation-aware grounder (this environment has no LLM key / no torch). Next:
broaden the Wikidata homograph seed set so the real-framework split-rate has more
support, and the Text2KG@ISWC write-up (`PAPER.md`, TODO).
