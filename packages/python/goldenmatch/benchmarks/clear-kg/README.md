# CLEAR-KG — Corpus-Level Entity-resolved And gRounded KG construction

A benchmark for building knowledge graphs from **document troves**, measuring the
two axes the market skips: **corpus-level entity resolution** and **span-grounded
faithfulness** — while staying honest on extraction. Full design in `SPEC.md`.

**Why it exists** (from a 2026 landscape scan, 25/25 claims adversarially
verified): every incumbent doc→KG tool (Neo4j exact-match default, iText2KG
cosine@0.7, LlamaIndex none-built-in, KGGen LLM-judge clustering) does
`if similar: merge` for entity resolution, and no respected benchmark
(Text2KGBench, Re-DocRED — both single-document) even measures cross-document ER
or span-grounded faithfulness. That's the open seam CLEAR-KG targets.

## Status: Phase 0 (Track B spike)

Phase 0 proves the moat exists and is measurable: on a synthetic corpus with
controlled **homographs** (distinct entities sharing a surface string), does
principled ER keep them apart where `if same name: merge` cannot?

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
tests/         offline unit + end-to-end, incl. real-data track on a network-free fixture
```

## Next phases (see SPEC.md)

- **Track C — faithfulness:** span-grounded triple verification + confidence
  calibration (no incumbent measures it).
- **Track A — extraction:** triple-F1 (table stakes; extend er-kg-bench's
  `extraction_f1`).
- **Real-data recall axis:** per-chunk co-mention detection (this track's
  neighbor signature is per-article/stable — it validates the split-rate, not a
  sparse-recall regime). Broaden the homograph set beyond the curated 8.
- **Packaged incumbents end-to-end** (GraphRAG, Neo4j, iText2KG, KGGen) on all
  tracks, vs. the documented-mechanism reimplementations used today.

_Done:_ **real-data validity track** (Wikipedia homographs — see above); the moat
holds 1.000 vs 0.000 on prose we did not author.

## Note on the Phase-0 signal

Phase 0 uses a **consistent, distinctive co-mention signature** per entity so the
mechanism is cleanly visible. Real corpora are noisier (the WhoIsWho SND result
showed the neighborhood signal is genuinely sparse) — that realism is exactly
what the difficulty knobs (§4.3) and the real-data track exist to test. Phase 0
proves the mechanism and the metric; it does not claim realistic accuracy.
