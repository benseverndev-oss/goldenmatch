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

**Result — full na-v3-style synthetic corpus, 60 mentions / 20 gold entities:**

| engine | pairwise-F1 | B³-F1 | **homograph split-rate** |
|---|--:|--:|--:|
| `exact_surface` (Neo4j-default `if same name: merge`) | 0.705 | 0.826 | **0.000** |
| **`goldenmatch`** (neighborhood ER) | **0.889** | **0.929** | **1.000** |

At larger scale (60 entities / 963 confusable pairs) the gap widens: exact_surface
pairwise collapses to 0.334 (split 0.000) while goldenmatch holds 0.854 (split
0.983). goldenmatch wins **both** axes — the incumbent fails both ways
(over-merges homographs, under-merges alias variants); co-mention (neighborhood)
overlap fixes both. This is the WhoIsWho SND signal generalized: **structure, not
string, resolves entities.**

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
run_track_b.py generate -> resolve -> score, per engine
tests/         offline unit + end-to-end (goldenmatch beats exact_surface on both axes)
```

## Next phases (see SPEC.md)

- **Track C — faithfulness:** span-grounded triple verification + confidence
  calibration (no incumbent measures it).
- **Track A — extraction:** triple-F1 (table stakes; extend er-kg-bench's
  `extraction_f1`).
- **Real-data validity track:** DocRED × Wikidata QIDs for a non-synthetic
  multi-doc corpus with cross-doc entity ground truth.
- **LLM-generated prose** (Phase 0 uses templated prose) + Wikidata content for
  realism; incumbent baselines (GraphRAG, Neo4j, iText2KG, KGGen) on all tracks.

## Note on the Phase-0 signal

Phase 0 uses a **consistent, distinctive co-mention signature** per entity so the
mechanism is cleanly visible. Real corpora are noisier (the WhoIsWho SND result
showed the neighborhood signal is genuinely sparse) — that realism is exactly
what the difficulty knobs (§4.3) and the real-data track exist to test. Phase 0
proves the mechanism and the metric; it does not claim realistic accuracy.
