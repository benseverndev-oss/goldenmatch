# CLEAR-KG — Corpus-Level Entity-resolved And gRounded KG construction

A benchmark for building knowledge graphs from **document troves**, measuring the
two axes the market skips: **corpus-level entity resolution** and **span-grounded
faithfulness** — while staying honest on extraction. Full design in `SPEC.md`;
all measured numbers consolidated in **[`RESULTS.md`](RESULTS.md)**.

> **Companion board:** [`../er-kg-bench`](../er-kg-bench) runs the *real decision
> code* of the packaged frameworks (GraphRAG, Neo4j, LlamaIndex, KGGen/Cognee,
> mem0, Graphiti) against goldenmatch on real Wikidata/RxNorm data. CLEAR-KG
> proves the mechanisms + metrics on controlled and real-Wikipedia corpora;
> er-kg-bench scores the real tools. The homograph split-rate is a first-class
> metric on both. See `RESULTS.md` for how they fit together (including the
> honest "every resolver ties at split-rate 0 without the neighborhood signal"
> bridge finding).

**Why it exists** (from a 2026 landscape scan, 25/25 claims adversarially
verified): every incumbent doc→KG tool (Neo4j exact-match default, iText2KG
cosine@0.7, LlamaIndex none-built-in, KGGen LLM-judge clustering) does
`if similar: merge` for entity resolution, and no respected benchmark
(Text2KGBench, Re-DocRED — both single-document) even measures cross-document ER
or span-grounded faithfulness. That's the open seam CLEAR-KG targets.

## Status: Phase 0 (Tracks A + B + C + D spikes)

Phase 0 covers all four axes. The two **moats** — **corpus-level ER** (Track B)
and **span-grounded faithfulness** (Track C) — follow one method: reimplement the
market's *documented* mechanisms, run them on identical inputs, and measure them
collapsing on the axis they skip (Track B on synthetic **homographs** + a
**real-data** Wikipedia track; Track C on **distractor** and **hallucinated**
triples). **Track A** is table stakes — the convention-matching extraction-F1
harness — and lands one finding of its own: even the extraction metric's
canonicalization step is an ER problem. **Track D** composes all three into a
single **CLEAR score** (below) so a system cannot win by being strong on one axis
and hollow on the others.

## Track D — the CLEAR composite (headline)

An end-to-end **system** is a full pipeline = a shared extractor × an ER engine
(Track B) × a grounding engine (Track C). The **CLEAR score** is the *harmonic
mean* of the three axis scores measured on one corpus, so it is dragged toward the
weakest axis and zeroes out if any axis does. Extraction is shared (table stakes);
the composite is decided by the two moats.

| system (stack) | extract-F1 | ER-F1 (B³) | grounded-ok | **CLEAR** |
|---|--:|--:|--:|--:|
| `incumbent` — name-merge ER + presence grounding | 1.000 | 0.800 | 0.750 | **0.837** |
| `er_only` — neighborhood ER + presence grounding | 1.000 | 1.000 | 0.750 | **0.900** |
| **`goldenmatch`** — neighborhood ER + relation-aware grounding | 1.000 | 1.000 | 1.000 | **1.000** |

The `er_only` row is the point: **perfect extraction AND perfect ER cannot rescue
a system that grounds distractors** — one hollow axis (grounding 0.750) drags the
composite from 1.000 to 0.900. The incumbent, hollow on *both* moats, lands 0.837.
A system has to win corpus-level ER **and** span-grounded faithfulness to top the
end-to-end score — which is exactly the two axes the market skips. Run it:

```bash
python benchmarks/clear-kg/run_track_d.py
```

**We ran the market's three ER families** (faithful reimplementations of each
tool's documented ER *mechanism*, on identical inputs — isolating the ER
algorithm from extraction/LLM/server differences; see `incumbents.py`). On a
synthetic corpus (60 mentions / 20 gold entities):

| engine (documented mechanism) | pairwise-F1 | B³-F1 | **homograph split-rate** |
|---|--:|--:|--:|
| `neo4j_exact` — exact name (Neo4j `SimpleKGPipeline` default) | 0.705 | 0.826 | **0.000** |
| `neo4j_fuzzy` — RapidFuzz (Neo4j `FuzzyMatchResolver`) | 0.705 | 0.826 | **0.000** |
| `name_cosine` — embedding-cosine on name (iText2KG / spaCy / KGGen family) | 0.713 | 0.842 | **0.000** |
| **`goldenmatch`** — neighborhood ER | **0.889** | **0.929** | **1.000** |

**Every documented `if similar: merge` mechanism scores 0.000 on homographs** —
because they all resolve on the surface string, and two identical surfaces always
merge. At 60 entities / 963 confusable pairs the gap widens: all incumbents ~0.33
pairwise / 0.000 split; goldenmatch 0.854 / **0.983**. goldenmatch wins **both**
axes — the incumbents fail both ways (over-merge homographs, under-merge alias
variants); co-mention (neighborhood) overlap fixes both. This is the WhoIsWho SND
signal generalized: **structure, not string, resolves entities.**

> The incumbent baselines are faithful reimplementations of each tool's
> *documented* ER mechanism, not the packaged tools run end-to-end (which need API
> keys, a Neo4j server, torch). Running the packaged tools is a later phase; this
> isolates the ER algorithm, which is the cleaner comparison.

## Real-data validity track (kills "you wrote the docs")

The synthetic corpus proves the mechanism; the obvious objection is *"you
authored the documents, of course it works."* So we re-ran Track B on **real
Wikipedia prose we did not author**, where the ground truth is Wikipedia's own:

- an article **title** is the exact entity id (Wikipedia disambiguates for us);
- a curated set of ambiguous **surface** strings — `Java`, `Mercury`, `Amazon`,
  `Jaguar`, `Michael Jordan`, `Georgia`, `Phoenix`, `Cambridge` — each map to 2+
  distinct articles (real homographs);
- an article's outbound **links** are its real co-mention neighborhood, disjoint
  across homograph articles by construction of being about different things.

On **72 real mentions / 18 gold entities (Wikipedia articles) / 8 ambiguous
surfaces / 192 real confusable homograph pairs**:

| engine (documented mechanism) | pairwise-F1 | B³-F1 | **homograph split-rate** |
|---|--:|--:|--:|
| `neo4j_exact` / `neo4j_fuzzy` / `name_cosine` | 0.529 | 0.615 | **0.000** |
| **`goldenmatch`** — neighborhood ER | **1.000** | **1.000** | **1.000** |

**Every `if similar: merge` mechanism still scores 0.000 on homographs — on data
nobody in this repo authored.** The gap is not a property of our generator; it is
a property of resolving on the surface string. Run it yourself (fetch-on-demand,
cached, nothing committed):

```bash
python benchmarks/clear-kg/run_real.py            # fetch (cached) + run
python benchmarks/clear-kg/run_real.py --refresh  # re-fetch from Wikipedia
```

> The neighbor signature is per-ARTICLE (an entity's mentions share its outbound-
> link neighborhood), not per-chunk. Real per-chunk co-mentions are sparser (the
> WhoIsWho SND lesson) and would lower recall; the honest claim under test is the
> homograph **split**, which turns on the neighborhoods being disjoint — which
> they are, by article identity. Per-chunk co-mention detection is a later
> refinement of the recall axis, not the split-rate axis.

## Track C — span-grounded faithfulness (the second axis the market skips)

Track B asks *"is each entity one node?"* Track C asks the other question no
benchmark measures: **when a KG emits a triple, is it verifiably supported by a
specific source span — with a confidence — or invented?** The landscape scan
found faithfulness everywhere is *ontology conformance* or *within-sentence
presence* — never "does this span actually state this relation."

The discriminator (the faithfulness analogue of the homograph) is the
**distractor**: a sentence where the two entities co-occur but the claimed
relation is **not** stated (a different, same-type relation is). Plus
**hallucinated** triples whose entities never co-occur at all. We reimplement the
field's documented faithfulness mechanisms (`grounding.py`) and score them on
52 candidate triples (24 supported / 16 distractor / 12 hallucinated):

| engine (documented mechanism) | support-F1 | coverage | **distractor false-support** | hallucination | conf-AUROC |
|---|--:|--:|--:|--:|--:|
| `ungrounded` — assert, cite no span (LlamaIndex / LangChain default) | 0.632 | 0.00 | **1.000** | 1.000 | 0.500 |
| `sentence_presence` — entities share a sentence (within-sentence presence) | 0.750 | 0.77 | **1.000** | 0.000 | 0.500 |
| `ontology_conformance` — relation type matches the schema | 0.632 | 0.00 | **1.000** | 1.000 | 0.500 |
| **`relation_aware`** — a span states *this* relation, with a confidence | **1.000** | 0.46 | **0.000** | 0.000 | **1.000** |

**Every documented mechanism marks a distractor "supported" (1.000)** — because
none reads whether the span expresses that relation; `ontology_conformance` and
`ungrounded` pass hallucinations through too. Only relation-aware grounding
refuses both (0.000 / 0.000), lands perfect support-F1, and is the **only engine
that emits a calibrated confidence** (AUROC 1.000 vs 0.500, ECE ≈ 0.02) — the
"never black box" axis measured directly: a system with no graded confidence
scores 0.5 by construction. Run it:

```bash
python benchmarks/clear-kg/run_track_c.py
```

> `relation_aware` uses a trigger-lexicon relation proxy, not an NLI model
> (torch-free by design). It is a real, if simple, NLU check — not reading a
> hidden label — and the distractor bait defeats presence/type grounding
> *regardless* of how the relation is phrased, because they never look at the
> relation. Gold provenance spans are the backstop; an NLI-backed verifier and
> LLM-generated multi-sentence prose are later phases (SPEC §3, §8).

## Track A — extraction (table stakes), and the metric inherits the moat

Track A is **table stakes**: canonicalized triple precision / recall / F1 vs the
gold KG, in the Text2KGBench / Re-DocRED convention (report **exact** and
**relaxed**/canonicalized matching). We are *not* trying to beat the LLM-
extraction pack here (Re-DocRED SOTA ~74.6 F1 LLM / ~80.7 BERT) — Phase 0 ships
the convention-matching **harness**, ready to score an LLM extractor's output on
the real-data corpus (the competitive number is the next phase, and requires an
LLM pass; deterministic reference extractors stand in for it offline).

The one measured, non-obvious finding is ours: **canonicalized ("relaxed")
triple matching is itself an entity-resolution problem.** The field resolves a
predicted entity surface to a gold entity by string match — which mis-credits
homographs. On a faithful extractor's output (24 gold triples, 4 homograph
subjects), scored under three canonicalization modes:

| matching mode | P | R | F1 | homograph-recall |
|---|--:|--:|--:|--:|
| `exact` — canonical string only (Text2KGBench exact) | 0.250 | 0.250 | 0.250 | 0.000 |
| `relaxed` — alias match, string-based (the field's relaxed) | 0.750 | 0.750 | 0.750 | **0.500** |
| `er_aware` — alias + co-mention disambiguation | **1.000** | **1.000** | **1.000** | **1.000** |

`exact` under-counts (the alias-canonicalization penalty that is *why* the field
uses a relaxed metric); `relaxed` recovers aliases but **mis-credits homographs**
(resolves the ambiguous surface to the wrong gold entity — homograph-recall
0.500); only ER-aware canonicalization scores the homograph triples correctly.
A `lossy` extractor scores strictly lower under every mode, so F1 tracks
extraction quality as it must. Run it:

```bash
python benchmarks/clear-kg/run_track_a.py
```

> Relations are treated as schema-closed (normalized to canonical in every mode),
> so the **entity surface** is the sole axis under study. The reference extractors
> are deterministic stand-ins on templated prose — the object of study is the
> metric's ER-dependence, not a SOTA extraction number.

## The homograph split-rate (the money metric)

Of gold mention-pairs that **share a surface string but are different entities**,
the fraction the system correctly keeps in different clusters. Goes to ~0 for
every `if similar: merge` incumbent; principled ER (surface blocking + co-mention
set overlap) keeps them apart. It's the one number that separates real entity
resolution from name-merge.

## Running

```bash
# from packages/python/goldenmatch (with goldenmatch importable)
export GOLDENMATCH_NATIVE=0 POLARS_SKIP_CPU_CHECK=1

python benchmarks/clear-kg/run_track_b.py                       # 20 entities / 5 homograph pairs
python benchmarks/clear-kg/run_track_b.py --n-entities 60 --homograph-pairs 15

python -m pytest benchmarks/clear-kg/tests/ -q                 # offline, no network
```

Env knobs: `CLEARKG_ER_THRESHOLD` (default 0.5), `CLEARKG_SURFACE_WEIGHT`
(default 0.0 — a positive weight gives homographs a nonzero floor and breaks
splits, so pure co-mention is the clean signal).

## Layout

```
SPEC.md        full benchmark design (4 tracks, metrics, data, baselines, phases)
generate.py    synthetic KG->corpus generator with controlled homographs + exact
               3-way ground truth (entities, triples, provenance spans)
er_utils.py    normalization + the co-mention set-overlap plugin scorer
track_b.py     the two ER engines (exact_surface baseline, goldenmatch neighborhood ER)
score.py       pairwise-F1, B-cubed, homograph split-rate
run_track_b.py generate -> resolve -> score, per engine (SYNTHETIC corpus)
real_data.py   Wikipedia fetcher + pure mention-builder (REAL homographs)
run_real.py    fetch (cached) -> resolve -> score, per engine (REAL corpus)
grounding_data.py  Track C dataset: supported / distractor / hallucinated triples
grounding.py   Track C engines (ungrounded, sentence_presence, ontology_conformance,
               relation_aware) -- the documented faithfulness mechanisms + the moat
score_c.py     Track C metrics: support-PRF, distractor false-support, hallucination,
               grounding coverage, confidence AUROC + ECE
run_track_c.py generate -> verify -> score, per engine (span-grounded faithfulness)
extract_data.py    Track A dataset: gold KG + alias/homograph-varied docs
extractors.py  Track A extractors (pattern, lossy) -- doc -> surface triples
extract_score.py   Track A metric: canonicalized triple-PRF in exact/relaxed/er_aware modes
run_track_a.py extract -> score under each canonicalization mode (extraction F1)
pipeline_data.py   Track D unified corpus (aligned entity/triple/provenance truth)
score_d.py     Track D CLEAR composite (harmonic mean of the three axes)
run_track_d.py full-pipeline systems -> extract + resolve + ground -> CLEAR score
tests/         offline unit + end-to-end for Tracks A, B, C, D, and the real-data track
```

## Next phases (see SPEC.md)

- **Real-data recall axis:** per-chunk co-mention detection (this track's
  neighbor signature is per-article/stable — it validates the split-rate, not a
  sparse-recall regime). Broaden the homograph set beyond the curated 8.
- **Packaged incumbents end-to-end** (GraphRAG, Neo4j, iText2KG, KGGen) on all
  tracks, vs. the documented-mechanism reimplementations used today.

- **Competitive numbers on real data:** an LLM extraction pass on the real-data
  corpus for Track A's "in the pack" number (extend er-kg-bench's
  `extraction_f1`), an NLI backstop for the ER-aware matcher / relation-aware
  grounder on paraphrased relations, and the *packaged* incumbents (GraphRAG,
  Neo4j, iText2KG, KGGen) run end-to-end vs the documented-mechanism baselines.
- **Package / evangelize:** dataset card, leaderboard, Text2KG @ ISWC paper.

_Done:_ all four tracks spiked — **A** (extraction-F1 harness + the ER-in-the-
metric finding), **B** (corpus-level ER moat) + **real-data validity track**
(Wikipedia homographs, 1.000 vs 0.000 on prose we did not author), **C** (span-
grounded faithfulness — relation-aware grounding wins every axis vs the documented
presence/type mechanisms), and **D** (the CLEAR composite — a system must win both
moats to top the end-to-end score).

## Note on the Phase-0 signal

Phase 0 uses a **consistent, distinctive co-mention signature** per entity so the
mechanism is cleanly visible. Real corpora are noisier (the WhoIsWho SND result
showed the neighborhood signal is genuinely sparse) — that realism is exactly
what the difficulty knobs (§4.3) and the real-data track exist to test. Phase 0
proves the mechanism and the metric; it does not claim realistic accuracy.
