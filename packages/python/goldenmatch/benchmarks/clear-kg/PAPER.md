# CLEAR-KG: Measuring the Two Axes of Document-to-KG Construction the Field Skips — Corpus-Level Entity Resolution and Span-Grounded Faithfulness

**Draft v0.1 — Text2KG @ ISWC short-paper target. Not for citation.** All numbers
below are produced by committed, offline-testable harnesses; live-LLM and
network-fetched numbers name their model/source and are reproducible with the
commands in the appendix.

---

## Abstract

Benchmarks for knowledge-graph construction from text overwhelmingly measure
**extraction** — per-document triple precision/recall/F1 (Text2KGBench,
Re-DocRED) — and are almost all **single-document**, so they cannot see the two
properties that decide whether a KG built from a *document trove* is correct:
(1) **corpus-level entity resolution** — is each real-world entity exactly one
node, across documents and surface variants, *including distinct entities that
share a surface* (homographs)? and (2) **span-grounded faithfulness** — is each
emitted triple verifiably supported by a specific source span, with a confidence,
rather than invented? Every incumbent doc→KG tool we surveyed resolves entities
with an `if similar: merge` rule and grounds triples by *presence* or *type
conformance*, never by whether a span states the relation. We present **CLEAR-KG**,
a benchmark that isolates and measures four axes — Extract (A), Resolve (B), Ground
(C), and an end-to-end composite (D) — and reports the axis-specific metrics the
field lacks: the **homograph split-rate**, **grounded-&-correct rate** with
confidence calibration, and a harmonic-mean **CLEAR score** that a system cannot
win while hollow on any axis. On controlled corpora, on real Wikipedia prose, and
against the *real decision code* of the packaged frameworks (via the companion
er-kg-bench board), we find: every documented ER and grounding mechanism collapses
on its target axis (homograph split-rate → 0.000; distractor false-support →
1.000), while a neighborhood/collective resolver and a relation-aware grounder
separate and verify correctly; extraction, by contrast, is a hard,
**fine-tuning-bound commodity** — a zero-shot sweep from `gpt-4o-mini` to `gpt-5`
on Re-DocRED climbs only 0.151 → 0.282 F1, consistent with the literature's low
zero-shot LLM DocRE (frozen GPT-4 ~15.6 F1), while competitive numbers come from
task-specific fine-tuning (DREEAM, AutoRE), not scale — and a controlled prompt
experiment shows the recall bottleneck is substantially a single-shot artifact,
not a hard ceiling. The strategic reading: extraction is the axis you *buy* (or
fine-tune); corpus-level ER and span-grounded faithfulness are the axes you
*build*, and the ones no benchmark measured.

---

## 1. Introduction

A knowledge graph built from a document trove is only as trustworthy as three
properties: the triples are **extracted** correctly, each real entity is **one
node** (not fragmented across aliases, not conflated across homographs), and each
edge is **grounded** in a source span rather than hallucinated. The field measures
the first and almost ignores the other two.

The single knob that separates principled entity resolution from `if similar:
merge` is the **homograph**: two distinct entities that share a surface form
("Michael Jordan" the athlete vs. the ML professor; "Georgia" the country vs. the
US state; "J. Smith" the cardiologist vs. the lawyer). String/embedding merge
collapses them; structure-aware resolution keeps them apart. Symmetrically, the
knob that separates real grounding from bookkeeping is the **distractor**: a
sentence where two entities co-occur but the claimed relation is *not* stated.
Presence- and type-based grounding accept it; relation-aware grounding refuses it.

CLEAR-KG operationalizes both, plus a composite that resists hollow single-axis
wins, and reports them on controlled corpora, on real Wikipedia, and against the
real packaged frameworks.

## 2. Background and related work

**Extraction benchmarks.** Text2KGBench and DocRED/Re-DocRED are the standards for
document-level triple/relation extraction. On the standard Re-DocRED relation
task (classification given gold entity pairs), fine-tuned SOTA (DREEAM/ATLOP-class)
is ~77–80 F1; the harder end-to-end triple-extraction setting is much lower even
fine-tuned (AutoRE, a QLoRA-tuned LLM, ~52 F1 test); frozen zero-shot GPT-4 is
~15.6 F1 — the literature is explicit that fine-tuning, not prompting a frozen
model, is what makes LLMs competitive here. Both are
single-document: they cannot express cross-document coreference, so corpus-level
ER is invisible to them.

**Doc→KG tools.** A 2026 landscape scan (25/25 claims adversarially verified)
found the entire field resolves entities with `if similar: merge`: Neo4j's
`SimpleKGPipeline` (exact-string default; `FuzzyMatchResolver` opt-in), iText2KG
(cosine@0.7), LlamaIndex PropertyGraphIndex (none built-in), KGGen (LLM-judge
clustering). None uses principled probabilistic or collective record linkage.
Faithfulness, where measured at all, is *ontology conformance* or *within-sentence
presence* — never span-level relation support with a confidence.

**ER-quality benchmarks.** The companion board **er-kg-bench** (this repository)
scores the *documented default dedup rule* of the packaged frameworks (Microsoft
GraphRAG, LightRAG, Cognee, mem0, Graphiti, Neo4j LLM KG-Builder,
neo4j-graphrag-python, LlamaIndex PGI) against a principled resolver on real
Wikidata/RxNorm-grounded records, with fidelity tiers (`real-inproc` runs the
framework's real decision code; `validated` reproduces its exact rule vs. source).
CLEAR-KG supplies the doc→KG-construction axes and the neighborhood-structured
corpora that board's flat records lack; the two are complementary.

## 3. Benchmark design

**Task.** Given a *corpus* of documents, construct a KG whose nodes are resolved
entities and whose edges carry a provenance span and a confidence, with each
real-world entity exactly one node. Four tracks isolate the axes:

| track | input → output | isolates | headline metric |
|---|---|---|---|
| **A · Extract** | doc → triples | extraction quality | triple micro-F1 (exact + relaxed) |
| **B · Resolve** | gold mentions → entity clusters | corpus-level ER | **homograph split-rate** |
| **C · Ground** | docs + candidate triples → span + verdict + confidence | faithfulness | **grounded-&-correct**, distractor false-support, confidence AUROC/ECE |
| **D · End-to-end** | corpus → grounded, resolved KG | the whole pipeline | **CLEAR score** |

**Homograph split-rate (B).** Of gold mention-pairs sharing a normalized surface
but belonging to *different* entities, the fraction the system keeps in different
clusters. → 0 for every `if similar: merge` mechanism (identical surfaces always
merge); high for neighborhood/collective ER. We also report pairwise-F1 and B³ for
cross-community comparability.

**Grounded-&-correct + calibration (C).** Of the triples a system grounds and
marks supported, the fraction whose cited span actually supports the claim
(grounding precision); plus the **distractor false-support rate** (of gold
distractors, the fraction wrongly marked supported), the **hallucination rate**,
and the **confidence AUROC/ECE** — a system emitting no graded confidence scores
0.5 AUROC by construction, grading the "never black box" axis directly.

**CLEAR score (D).** The *harmonic mean* of {extraction-F1, ER-F1 (B³),
grounded-&-correct}, so the composite is dragged to the weakest axis and zeroes on
any zero component — a system cannot win extraction and be hollow on ER or
grounding.

## 4. Data

- **Synthetic generators (A, B, C, D).** Deterministic, seeded, offline. A known
  KG is reversed into a document corpus with controlled homographs (B), aligned
  triple + provenance ground truth (A, D), and supported/distractor/hallucinated
  candidate triples (C). No corpus is committed; the generators are.
- **Real-data validity track (B).** English Wikipedia via the action API: article
  titles are exact entity ids, curated ambiguous surfaces map to distinct articles
  (real homographs), and outbound links are the real co-mention neighborhoods.
  Fetch-on-demand, gitignored; no Wikipedia text is redistributed.
- **Real-prose extraction (A).** Re-DocRED dev split (real Wikipedia, gold
  document-level triples, 95-relation closed Wikidata schema).
- **Real packaged frameworks (companion).** er-kg-bench's Wikidata/RxNorm records.

## 5. Results

### 5.1 Track B — corpus-level ER (the homograph split-rate)

Faithful reimplementations of each tool's documented ER *mechanism*, on identical
inputs (isolating the algorithm from extraction/LLM/server differences):

| corpus | engine | pairwise-F1 | **split-rate** |
|---|---|--:|--:|
| synthetic, 20 ent / 5 homograph pairs (60 mentions) | `neo4j_exact` / `neo4j_fuzzy` / `name_cosine` | 0.705–0.713 | **0.000** |
| synthetic | **`goldenmatch`** (neighborhood ER) | **0.889** | **1.000** |
| synthetic, 60 ent / 15 homograph pairs (963 confusable) | incumbents | ~0.33 | **0.000** |
| synthetic | **`goldenmatch`** | **0.854** | **0.983** |
| **real Wikipedia**, 72 mentions / 18 entities / 192 confusable pairs | incumbents | 0.529 | **0.000** |
| **real Wikipedia** | **`goldenmatch`** | **1.000** | **1.000** |

Every `if similar: merge` mechanism scores 0.000 — on synthetic *and* on real
Wikipedia prose we did not author.

### 5.2 Track C — span-grounded faithfulness

52 candidate triples (24 supported / 16 distractor / 12 hallucinated):

| engine (documented mechanism) | support-F1 | **distractor false-support** | hallucination | conf-AUROC |
|---|--:|--:|--:|--:|
| `ungrounded` (LlamaIndex / LangChain default) | 0.632 | **1.000** | 1.000 | 0.500 |
| `sentence_presence` (within-sentence presence) | 0.750 | **1.000** | 0.000 | 0.500 |
| `ontology_conformance` (type matches schema) | 0.632 | **1.000** | 1.000 | 0.500 |
| **`relation_aware`** (span states *this* relation, with confidence) | **1.000** | **0.000** | 0.000 | **1.000** (ECE ≈ 0.02) |

Only relation-aware grounding refuses the distractor and is the only engine
emitting a calibrated confidence.

### 5.3 Track A — extraction

**The metric inherits the moat (synthetic).** Canonicalized ("relaxed") triple
matching is itself an ER problem: on an extractor's alias/homograph-varied output,
`exact` matching scores F1 0.250 (alias penalty), string-based `relaxed` 0.750 but
mis-credits homographs (homograph-recall 0.500), and only ER-aware canonicalization
scores correctly (1.000 / 1.000).

**Real-prose floor→ceiling (Re-DocRED, 20 docs / 853 gold triples, zero-shot):**

| model | micro-P | micro-R | **micro-F1** | wall |
|---|--:|--:|--:|--:|
| `gpt-4o-mini` | 0.339 | 0.097 | **0.151** | 90s |
| `gpt-4.1-mini` | 0.448 | 0.121 | **0.190** | 69s |
| `gpt-4o` | 0.481 | 0.122 | **0.195** | 58s |
| `gpt-4.1` | 0.399 | 0.130 | **0.196** | 47s |
| `gpt-5-mini` (reasoning) | 0.473 | 0.182 | **0.262** | 704s |
| `gpt-5` (reasoning) | 0.604 | 0.184 | **0.282** | 1439s |

Reference (from the literature): standard Re-DocRED relation task, fine-tuned
DREEAM/ATLOP ~77–80 F1; end-to-end triple extraction fine-tuned (AutoRE) ~52 F1;
frozen zero-shot GPT-4 ~15.6 F1. Our zero-shot single-pass sweep (0.15–0.28) sits
in the frozen-LLM regime. In the single-shot prompt, chat models plateau at ~0.196
(recall ~0.12) and the reasoning tier reaches ~0.28 (recall ~0.18; mini→full buys
precision 0.47→0.60, not recall).

**The recall bottleneck is largely a single-shot prompting artifact, not a hard
ceiling.** A controlled experiment (same 5 docs, same `gpt-4.1`, temperature 0,
only the prompt varies) — a baseline "list every triple" prompt scores R 0.129 /
F1 0.190; a single instruction to be exhaustive and include inverse relations
lifts it to R 0.196 / F1 0.248 (reaching the reasoning models' single-shot recall
*without* a stronger model); a two-pass union helps less (R 0.165, added noise).
The **residual** gap to SOTA is closed by task-specific *fine-tuning* (DREEAM's
evidence-guided RoBERTa; AutoRE's QLoRA-tuned LLM), not by prompting or scale — so
the correct reading is not "reasoning can't do it" but *"extraction is
fine-tuning-bound"*: the commodity axis you invest in, not the differentiator.

### 5.4 Track D — the CLEAR composite

A system = shared extractor × ER engine × grounding engine:

| system | extract-F1 | ER-F1 | grounded-ok | **CLEAR** |
|---|--:|--:|--:|--:|
| `incumbent` (name-merge + presence) | 1.000 | 0.800 | 0.750 | **0.837** |
| `er_only` (neighborhood ER + presence) | 1.000 | 1.000 | 0.750 | **0.900** |
| **`goldenmatch`** (neighborhood ER + relation-aware) | 1.000 | 1.000 | 1.000 | **1.000** |

Perfect extraction *and* perfect ER cannot rescue hollow grounding (`er_only`
drops to 0.900): the composite rewards winning *both* moats.

### 5.5 The real frameworks, and an honest bridge (companion board)

er-kg-bench runs the packaged frameworks' real decision code on real records; we
add the homograph split-rate as a first-class metric there. On its current records
(name + type + context; two real homographs — "Michael Jordan", "Georgia"), **every
resolver scores split-rate 0.000, `goldenmatch` included.** This is not a
goldenmatch failure — it is the crux: the homographs share the *exact* surface, so
on records carrying no co-mention neighborhood, nothing can separate them (even
exact-match merges identical strings). The split is only *recoverable* with the
structure CLEAR-KG Track B supplies (goldenmatch 1.000 on the Wikipedia track,
§5.1). Both boards together: the incumbents cannot separate real homographs, and
neither can any resolver without the neighborhood structure — which is precisely
why the resolution layer, and a benchmark that carries the structure, matter.

## 6. Discussion

**The moats are measurable and the incumbents collapse on them.** Split-rate 0.000
and distractor false-support 1.000 are not close calls; they are structural
consequences of resolving on the string and grounding on co-occurrence. A
neighborhood/collective resolver and a relation-aware grounder win both axes and
the composite decisively.

**Extraction is the commodity axis.** Across a zero-shot single-pass sweep the
number climbs only 0.151 → 0.282, consistent with the literature's frozen-LLM
regime (~15.6 for GPT-4), and — as the prompt experiment above shows — the recall
bottleneck is largely a single-shot artifact rather than a hard ceiling. What
actually closes the gap to SOTA is *task-specific fine-tuning*, not prompting or
scale. Extraction is fine-tuning-bound — the axis to *buy* or fine-tune, not the
axis that differentiates a KG-construction system.

**Therefore the strategic surface is the ER + faithfulness layer** — exactly the
two axes no prior benchmark measured, and the two CLEAR-KG puts front and center.

## 7. Limitations and threats to validity

- **Synthetic validity ("graded own homework").** Phase-0 uses templated prose to
  make each mechanism cleanly visible; the synthetic 1.000s are *not* field
  accuracy. Mitigations: real content (Wikipedia, Wikidata via the companion), the
  real-data Wikipedia track and Re-DocRED as external anchors, committed
  regenerable generators, and offline tests pinning every claim. A human audit of a
  ≥200-item sample for prose naturalness + span correctness is TODO.
- **Small real-data support.** The Wikipedia homograph set (8 surfaces, 192
  confusable pairs) and the er-kg-bench homographs (2) are small; the metric
  infrastructure is the contribution, and support grows with the Wikidata seed set.
- **Single-pass, zero-shot extraction.** The Re-DocRED numbers are a floor→ceiling
  *range*, not the extraction ceiling; retrieval + few-shot + multi-pass (the SOTA
  recipe) are out of scope here.
- **Relation-aware grounding uses a trigger-lexicon proxy**, not an NLI model
  (torch-free by design); gold provenance spans backstop it. An NLI-verified column
  is TODO.
- **The moat only shows because the benchmark measures it** — the value is
  contingent on the benchmark being seen as legitimate, which is why the honesty
  items above are load-bearing, not optional.

## 8. Conclusion

CLEAR-KG measures the two axes that decide whether a KG built from a document trove
is correct — corpus-level entity resolution and span-grounded faithfulness — and
composes them so no system wins by being hollow. The documented incumbent
mechanisms collapse on both (split-rate 0.000; distractor false-support 1.000)
while principled ER + relation-aware grounding win both and the composite;
extraction, meanwhile, is a fine-tuning-bound commodity — cheap zero-shot lands in
the frozen-LLM regime, better prompting recovers much of the recall gap, and
task-specific fine-tuning closes the rest. Stop trying to make the KG answer
questions; make it *resolve correctly and cite its sources*, and measure exactly
that.

---

## Appendix — reproducibility

```bash
cd packages/python/goldenmatch/benchmarks/clear-kg
export GOLDENMATCH_NATIVE=0 POLARS_SKIP_CPU_CHECK=1
python run_track_a.py            # extraction metric study (synthetic)
python run_track_b.py            # corpus-level ER (homograph split-rate)
python run_real.py               # Track B on real Wikipedia (fetch-on-demand)
python run_track_c.py            # span-grounded faithfulness
python run_track_d.py            # the CLEAR composite
OPENAI_API_KEY=... python run_redocred.py --docs 20 --model gpt-5   # real-prose extraction
python -m pytest tests/ -q       # 50 offline tests (no key/network)

# companion board (real packaged frameworks + the split-rate)
cd ../er-kg-bench && python erkgbench/run.py
```

Consolidated numbers: `RESULTS.md`. Data provenance/licensing: `DATA_CARD.md`.
Full design: `SPEC.md`.

_TODO before submission: LLM-generated (non-templated) prose for the synthetic
tracks; a ≥200-item human audit; an NLI-verified grounding column; the packaged
incumbents run end-to-end on Tracks A/C/D; a broader Wikidata homograph seed; author
list + venue formatting._
