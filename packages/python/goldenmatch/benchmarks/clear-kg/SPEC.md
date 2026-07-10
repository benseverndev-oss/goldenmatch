# CLEAR-KG — a benchmark for **C**orpus-**L**evel **E**ntity-resolved **A**nd g**R**ounded **KG** construction

*Draft spec v0.1. Name is a placeholder.*

---

## 0. Why this exists (the one-paragraph case)

The 2026 doc→KG landscape scan (109-agent adversarial verification, 25/25 claims
confirmed) found two things every incumbent skips and no benchmark measures:

1. **Corpus-level entity resolution.** Neo4j (exact-string default), iText2KG
   (cosine@0.7), LlamaIndex (no built-in ER), KGGen (LLM-judge clustering) — the
   entire field does `if similar: merge`. None uses principled/probabilistic or
   collective record linkage.
2. **Span-grounded faithfulness.** Faithfulness everywhere is measured as
   *ontology conformance* or *within-sentence presence* — never "is this triple
   verifiably supported by a specific source span, with a confidence."

And the respected extraction benchmarks (Text2KGBench, Re-DocRED) are all
**single-document** — they cannot even *see* cross-document ER.

**CLEAR-KG is the benchmark that unites extraction + corpus-level ER +
span-grounded faithfulness in one evaluation.** Whoever defines the scoreboard
that measures the thing they're best at, frames the category. That is the point.

---

## 1. Design principles

1. **Isolate the axes.** A system must be able to show a strong ER score even
   with only par extraction, and vice versa. → *tracks* that each hold the other
   capabilities fixed (given gold mentions, resolve them; given gold triples,
   ground them).
2. **Ground truth by construction, validated by reality.** The hard part of any
   corpus-level benchmark is aligned 3-way ground truth (entities *with
   cross-doc coref* + triples + provenance spans). We get it *exactly* by
   generating documents **from a known KG** (reverse direction), then guard
   external validity with a real-data track + human audit. (See §4, §8.)
3. **The homograph is the discriminator.** The single knob that separates
   principled ER from `if similar: merge` is **distinct entities that share a
   surface form** ("J. Smith" the cardiologist vs "J. Smith" the lawyer;
   "Mercury" the planet / element / car). Naive merge collapses them; real ER
   keeps them apart. This is the SND lesson operationalized and it is where the
   incumbents will visibly fail.
4. **Open and honest.** Public data + code, a real-data track, human-audited
   sample, and a limitations section that names the "graded-own-homework" risk
   head-on (§8). The credibility of the wedge depends on not gaming it.

---

## 2. The task and its four tracks

**Overall task:** given a *corpus of documents* (not a single doc), construct a
knowledge graph — resolved entities as nodes, relations as edges — where **every
edge carries a provenance span and a confidence**, and where **each real-world
entity is exactly one node** regardless of how many documents/aliases mention it.

| track | input → output | isolates | comparable to |
|---|---|---|---|
| **A · Extract** | doc → triples (per-doc) | extraction quality | Text2KGBench, Re-DocRED (table stakes) |
| **B · Resolve** | gold mentions across docs → entity clusters | **corpus-level ER** (the moat) | SciCo/H-CDCR (adjacent), WhoIsWho SND |
| **C · Ground** | docs + candidate triples → provenance span + verdict + confidence | **faithfulness** (the seam) | GraphEval, AEVS (adjacent) |
| **D · End-to-end** | corpus → grounded, resolved KG | the whole pipeline | *nothing — this is the greenfield* |

Track B and C are the differentiators; A is table stakes; D is the headline
composite. An incumbent can enter A and look fine, then get measured on B/C where
`if similar: merge` and ungrounded triples collapse.

---

## 3. Metrics (exact, per track)

### Track A — Extraction (table stakes)
Canonicalized triple-level **precision / recall / F1** vs the gold KG, matching
Text2KGBench/Re-DocRED convention (report both "exact" and "relaxed"/entity-typed
matching). Target: **≈ parity** with the LLM-extraction pack (Re-DocRED LLM SOTA
~74.6 F1; BERT SOTA ~80.7 — we don't try to beat BERT, we show we're in the pack).

### Track B — Corpus-level ER (the moat) — reuse the SND scorer
Predicted node↔gold-entity alignment scored as clustering quality over the mention
set. Report the standard trio so it's comparable across communities:
- **Pairwise P/R/F1** (macro over surface-form blocks) — *already implemented* in
  `benchmarks/whoiswho-snd/score.py`, parity-tested vs `core.evaluate`.
- **B³ (B-cubed) P/R/F1** and **CEAF-e** — the coreference-community standards, so
  SciCo/CDCR people can read our number.
- **Homograph split-rate** (headline sub-metric): of the gold pairs that are
  *different entities sharing a surface form*, what fraction did the system
  correctly keep apart? This is the number that goes to ~0 for `if similar:
  merge` and stays high for principled ER. **This is the money metric.**

### Track C — Faithfulness / grounding (the seam)
For each emitted triple:
- **Grounding coverage** = fraction of triples that carry a provenance span.
- **Grounding correctness** = of grounded triples, fraction whose cited span
  actually entails the triple (checked against gold provenance; NLI-backed for
  paraphrase). 
- **Hallucination rate** = fraction of emitted triples supported by *no* span
  anywhere in the corpus (i.e., invented).
- **Confidence calibration** = AUROC and ECE of the system's per-triple confidence
  vs correctness. (Systems that emit no confidence score 0 here — grading the
  "never black box" axis directly.)

### Track D — End-to-end (headline composite)
A single **CLEAR score** = harmonic mean of {triple-F1, ER-F1 (B³), grounded-&-
correct rate}, so you cannot win by being great at one axis and hollow on the
others. Report the three components alongside; the composite is the leaderboard
sort key.

---

## 4. Data

### 4.1 Primary: the synthetic KG→docs generator (exact 3-way ground truth)
Reverse the usual direction. Start from a **known subgraph of a real KG**
(Wikidata / a public relational DB) so *content* is real, then generate a document
corpus that expresses it:

1. **Sample a seed subgraph**: N entities, their types, and M gold triples from
   Wikidata (real names, real relations → realism of content).
2. **Spread entities across documents** with controlled coreference: each entity
   appears in `k` documents under **alias variants** (abbreviations, honorifics,
   transliterations, typos, nicknames) — every mention tagged with its gold
   entity id (→ cross-doc coref ground truth *by construction*).
3. **Express each gold triple** in a document as natural prose (LLM-generated,
   strong model), recording the **char span** that expresses it (→ provenance
   ground truth by construction).
4. **Inject the difficulty knobs** (§4.3), crucially homographs and hallucination
   bait.

Output: a corpus + three aligned ground-truth files (entities-with-coref, triples,
provenance-spans). This is the only way to get all three *exactly* aligned, and it
lets us dial difficulty — which no existing benchmark can.

### 4.2 Secondary: the real-data validity track (external validity)
Because "you generated the docs" is a fair objection, add a **real track** by
*stitching existing gold resources*:
- Take **DocRED/Re-DocRED** (real Wikipedia, gold entities+relations per doc) and
  **link entities across its documents via Wikidata QIDs** (DocRED entities are
  Wikidata-linkable) → a *real* multi-doc corpus with cross-doc entity ground
  truth we did not fabricate. Provenance (the supporting-evidence sentences) is
  already annotated in DocRED. This gives a real-prose Track A/B/C with minimal
  fabrication — the honest anchor for the synthetic track's controlled sweeps.
- Report both. Synthetic = control + difficulty sweeps; real = external validity.

### 4.3 Difficulty knobs (synthetic track)
| knob | what it stresses | who it breaks |
|---|---|---|
| **alias/variant rate** | mention normalization | thin normalizers |
| **cross-doc spread `k`** | corpus-level coref (not single-doc) | single-doc tools |
| **homograph density** | distinct entities, shared surface form | `if similar: merge` (→ 0) |
| **distractor entities** | precision under many similar names | cosine-threshold ER |
| **hallucination bait** | facts stated then contradicted / plausible-but-false | ungrounded extractors |
| **relation paraphrase** | relation canonicalization | free-form-relation tools |

The homograph + distractor sweep is the exact axis where your SND / name_ci /
SP-C precision work (0.815→0.932) becomes a visible, measured advantage.

### 4.4 Schema (JSON, sketch)
```
corpus/doc_{i}.txt                          # the prose
entities.jsonl   {entity_id, type, canonical, aliases[], mentions:[{doc,span}]}
triples.jsonl    {subj_entity_id, relation, obj_entity_id, provenance:[{doc,span}]}
# system output (per track) mirrors these ids so scoring is a set-alignment
```

---

## 5. Baselines to run (where the incumbents fall over)

Run the field on all four tracks — the report *is* the marketing:
- **Microsoft GraphRAG**, **Neo4j SimpleKGPipeline** (default exact-match + opt-in
  fuzzy/semantic), **iText2KG** (cosine@0.7), **LlamaIndex PropertyGraphIndex**,
  **KGGen** (LLM-judge clustering), **LangChain LLMGraphTransformer**.
- **Prediction (to verify, not assume):** all cluster near-par on Track A, and
  **fall off a cliff on Track B homograph split-rate and Track C grounding** —
  because none does principled ER and none grounds triples to spans with
  confidence. If a baseline *doesn't* collapse (e.g., GraphRAG's opaque resolution
  turns out to be decent), that's a finding we need, not a threat to hide.
- **goldengraph** entry: goldenmatch ER for Track B + a grounded/confidence
  extraction pass for Track C. The thesis is it tops B and C decisively while
  sitting in the pack on A.

---

## 6. What a win looks like (the claim it substantiates)

> "On CLEAR-KG, goldengraph is **table-stakes-competitive on extraction** and
> **best-in-class on corpus-level entity resolution (homograph split-rate X% vs
> ≤Y% for every LLM-merge baseline) and span-grounded faithfulness** — the two
> axes that determine whether a KG built from a real document trove is *correct*,
> and the two axes no incumbent measures."

That is a defensible, measured "best on the market" — scoped to the axes that
matter for real multi-doc corpora, not a vibe and not a leaderboard you can't top.

---

## 7. Build plan (phased, reuses what you already have)

- **Phase 0 — spike (≈2 days).** Synthetic generator on a *tiny* Wikidata subgraph
  (20 entities, 5 homographs, 3 docs each). Wire Track B scoring by **reusing the
  SND `pairwise_f1_macro` + set-overlap scorer verbatim**. Run goldenmatch ER vs a
  cosine@0.7 baseline. Goal: see the homograph split-rate gap on 20 entities.
- **Phase 1 — Tracks B + C (≈1 wk).** Full synthetic generator with all knobs;
  B³/CEAF metrics; Track C grounding + NLI verifier + calibration. Run 3–4
  incumbent baselines. This is the differentiator proof.
- **Phase 2 — Track A + real track (≈1 wk).** Extraction F1 harness (extend
  er-kg-bench's existing `extraction_f1` mode); the DocRED×Wikidata real multi-doc
  corpus for external validity.
- **Phase 3 — package (≈few days).** Leaderboard, dataset card, repo, short paper.
  Submit to a Text2KG / KG-construction workshop (Text2KG @ ISWC is the venue).

Existing assets that drop straight in: SND pairwise-F1 scorer, goldenmatch ER
engine (the moat), goldengraph extraction + temporal store, er-kg-bench
`extraction_f1`/`retrieval_coverage` modes, goldenmatch PPRL (for §9).

---

## 8. Honest limitations & threats to validity

- **Synthetic validity ("you graded your own homework").** The whole field already
  does this (KGGen's MINE, iText2KG's FDR are both author-run self-evals) — but
  that's a reason to be *cleaner*, not an excuse. Mitigations: real content
  (Wikidata) not invented facts; the DocRED real track as the external anchor;
  human audit of a ≥200-item sample for prose naturalness + span correctness;
  publish the generator so others can inspect/regenerate.
- **Adoption lift.** A benchmark is only a wedge if others run it. This is
  evangelism (workshop paper, leaderboard, run-the-incumbents report), not just
  code. Budget for it. The ER-community metrics (B³/CEAF) and the DocRED anchor are
  deliberate bridges so two research communities can read the number.
- **NLI-backed grounding is imperfect** (entailment models err on paraphrase);
  gold provenance spans in the synthetic track backstop it; report both
  gold-span-match and NLI-verified.
- **The moat only shows because the benchmark measures it.** That's the whole
  strategy — but it means the value is contingent on the benchmark being seen as
  legitimate. Legitimacy = the honesty items above. Don't cut them.
- **Extraction is still LLM-bound.** If your extractor is materially behind the
  pack on Track A, ER/grounding wins read as "good at the easy part." Use a
  *tuned* competitive extractor (the DeepSeek/7B lesson: don't cheap out to the
  point of failing table stakes).

---

## 9. The through-line to the PPRL thesis

Track B *is* the input to your real goal. The private cross-KG crossover work
(anonymize two graphs, match by encoded neighborhoods) sits directly on top of a
**corpus-level-resolved graph** — because a "crossover" is two resolved nodes in
graph A and B being the same real entity, which is exactly what Track B produces
and scores. CLEAR-KG proves the resolution layer the PPRL thesis assumes. Build
the benchmark, win the ER axis, and the private-matching thesis inherits a
*measured, defensible* substrate instead of an unproven one.

**One sentence:** stop trying to make the KG answer questions; make it *resolve
correctly and cite its sources*, measure exactly that, and you're building on the
one layer the entire market treats as an afterthought and you've spent years
mastering.
