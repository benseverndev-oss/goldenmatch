# Chunked Extraction — Design

**Date:** 2026-07-01
**Branch:** `feat/chunked-extraction` (off `main`)
**Program:** goldengraph substrate-quality arc — the second real-prose *extraction-recall* lever, after the recall-tuned prompt (`GOLDENGRAPH_EXTRACT_RECALL`) was **REFUTED** (`docs/superpowers/reports/2026-07-01-extract-recall-prompt-verdict.md`).

## Problem

The L2 clean-absolute finding put the real-prose substrate ceiling at **extraction recall**: on the wiki corpus the 7B extracts only ~0.44 of the wikilinked entities, and the aliased aligner sees coverage ~0.40 / R(B) ~0.23 over 12 components. The recall-prompt lever tried to lift this by *instructing* exhaustive entity extraction; it made the substrate **worse** (coverage −0.05, R(B) −0.07, components 12→25) because "extract EVERY entity" pushed the model to list entities at the expense of relations, and the substrate is edge-centric.

The diagnostic from that negative: the miss is **not** relation-centric framing — it is **density**. The docs are a single dense ~2750-char, ~20-sentence paragraph, extracted in one LLM pass. A weak model attending over the whole lead drops entities regardless of how exhaustively it is prompted.

## Goal

A gated `GOLDENGRAPH_CHUNK_EXTRACT=1` path that splits each document into **overlapping sentence windows**, extracts each window independently, and **unions** the extractions into one `Extraction` before resolution. Unlike the recall prompt, each window preserves *both* its entities and its relations (the model sees a short span, so it extracts both well); the union then aggregates. Env-tunable window size / overlap, measured on the wiki corpus with a 2-3 point sweep. Default-off; single-pass path byte-identical when the gate is off.

## Non-goals

- No change to `extract.py` (the single-text primitive stays independent of the vocab / recall / literal gates).
- No new cross-document or within-doc entity-dedup machinery — the existing `resolve()` already collapses duplicate mentions within a doc.
- No edge-multiplicity dedup (see Union semantics).
- Not a general RAG chunker: this chunks for *extraction*, not retrieval.

## Architecture

### The seam

`ingest._prepare_doc` does exactly one `extraction = (extractor or _extract)(text, llm)` per document (`ingest.py:671`). That single call is the density bottleneck. The lever inserts a thin wrapper at that one call site:

```
extraction = (extractor or _extract)(text, llm)          # today
        │
        ▼  when GOLDENGRAPH_CHUNK_EXTRACT=1
extraction = chunk_extract(text, llm, extractor or _extract)
```

`chunk_extract` splits the text, calls the *same* extractor per window, and unions. Everything downstream — `resolve → build_batch → _cross_doc_link → store.append` — is untouched. When the gate is off, `_prepare_doc` calls the extractor exactly as before.

### Data flow

```
text
 ├─ split_sentences(text)              -> [s0, s1, … sN]           (pure, no LLM)
 ├─ sentence_windows(sents, size, ov)  -> [w0, w1, … wk]           (pure, no LLM)
 ├─ for each window:  extractor(w, llm) -> Extraction_i            (one LLM call each)
 └─ union: concat mentions; OFFSET each window's rel/attr indices  -> one Extraction
                                        │
                                        ▼
                          resolve()  (collapses duplicate mentions across windows for free)
                                        │
                                        ▼
                          build_batch → cross-doc link → append   (UNCHANGED)
```

### Why this seam

`resolve()` clusters duplicate mentions *within a document* into single entities, so an entity that appears in three overlapping windows becomes one node with no new code. The union is pure index arithmetic. `extract.py` is never touched, so chunking composes cleanly with the literal / vocab / recall gates (each window's `_extract` still honors them internally).

## Components

Three units in a new module `goldengraph/chunk_extract.py`, each independently testable.

### 1. `split_sentences(text: str) -> list[str]`

Stdlib `re` only (network-free, no nltk/spacy — matches the repo norm set by `build_wiki_corpus`). Split on `(?<=[.!?])\s+`; drop empties/whitespace. Abbreviations ("Inc.", "U.S.") will occasionally over-split; that is harmless for extraction (a fragment yields fewer entities, never wrong ones), so tests assert a lower bound, not an exact count.

### 2. `sentence_windows(sents, size, overlap) -> list[str]`

Slide a window of `size` sentences advancing by `size - overlap`, each window joined back with a space. Guards:
- `len(sents) <= size` → one window containing the whole doc (chunking is a correct no-op).
- `overlap >= size` → clamp to `size - 1` (guarantees forward progress; no infinite loop).
- `overlap < 0` → treat as 0.

### 3. `chunk_extract(text, llm, extractor) -> Extraction`

```python
merged_mentions, merged_rels, merged_attrs = [], [], []
for window in sentence_windows(split_sentences(text), size, overlap):
    try:
        ex = extractor(window, llm)          # honors LITERAL_ATTRS / vocab / recall internally
    except Exception:
        continue                             # a bad window degrades recall, never sinks the doc
    base = len(merged_mentions)
    merged_mentions += ex.mentions
    merged_rels  += [Relationship(r.subj + base, r.predicate, r.obj + base) for r in ex.relationships]
    merged_attrs += [Attribute(a.subj + base, a.predicate, a.value, a.typ)
                     for a in getattr(ex, "attributes", ())]
return Extraction(mentions=merged_mentions, relationships=merged_rels, attributes=merged_attrs)
```

**Config (read at call time so the sweep varies env between Modal legs):**
- `GOLDENGRAPH_CHUNK_EXTRACT` — the gate (default off).
- `GOLDENGRAPH_CHUNK_SENTENCES` — window size (default `4`).
- `GOLDENGRAPH_CHUNK_OVERLAP` — sentence overlap (default `1`).

### Union semantics — no dedup (deliberate)

Overlap re-extracts some mentions and edges. We do **not** dedup at union time:
- Duplicate **mentions** → `resolve()` collapses them into one entity downstream. No action needed.
- Duplicate **edges** between the same resolved pair are benign: identical `(subj_local, predicate, obj_local)` edges union their `source_refs` in the store and do not change graph structure. The substrate metrics key on entity-pairs and components, not edge multiplicity.

Adding edge-dedup would mean touching shared `build_batch` — out of scope. If the measurement shows edge inflation distorting a signal, that is a follow-up, not part of this lever.

## Error handling

Fail-soft, matching the existing per-doc guard:
- `_prepare_doc` already wraps extract+resolve in a try/except yielding an empty extraction on failure; `chunk_extract` runs inside it.
- Additionally, a single window whose `extractor` raises is skipped (its mentions simply don't contribute) rather than failing the whole doc.

## Measurement — falsifiable bar

Same rig as the recall-prompt verdict, so results are directly comparable: Modal `gg-bench`, 7B, `--corpus wiki`, `GOLDENGRAPH_XDOC_KEY=name_ci`, aliased aligner (on `main` via #1345). One control leg (chunking off) re-confirms the ~0.40 coverage / 0.23 R(B) / 12-component baseline on the current build, then 2-3 chunked legs varying `(GOLDENGRAPH_CHUNK_SENTENCES, GOLDENGRAPH_CHUNK_OVERLAP)` — e.g. `(4,1)`, `(3,1)`, `(6,2)`.

| Outcome | Signature | Action |
|---|---|---|
| **WIN** | coverage ↑ **and** R(B) ↑, components not materially worse than control, P(B) ~1.0 | Ship the gate; document the winning `(size, overlap)`; consider default-on only if robust |
| **REFUTED** | coverage flat/down, **or** coverage up only by fragmenting the graph (components blow up) | Ship gate default-off; write the negative; escalate to GLiNER (next lever) |

The **shape of the sweep is itself a result**: if larger windows recover edges while smaller windows recover entities, that tension is the finding regardless of a single win/lose verdict. Precision (P(B) ~1.0) and component count are the guardrails that distinguish "real recall lift" from "fragmentation dressed as recall" — the exact trap the recall prompt fell into.

## Testing

All box-safe: no LLM, no network. Run via the main `.venv` + `PYTHONPATH` shadow, `POLARS_SKIP_CPU_CHECK=1 GOLDENGRAPH_NATIVE=0 -p no:cacheprovider`.

1. **`split_sentences`** — a multi-sentence string yields ≥ N sentences (lower-bound assertion tolerates abbreviation over-split); empty string → `[]`.
2. **`sentence_windows`** — `size/overlap` produce the expected index spans; `len(sents) <= size` → exactly one window equal to the whole text; `overlap >= size` clamped (terminates, covers all sentences).
3. **`chunk_extract` union** — a capturing stub LLM returns a fixed 2-entity/1-relationship extraction per call; assert mentions concatenate across windows and that a relationship from window *k* has `subj`/`obj` pointing into window *k*'s mention block (offset applied), not window 0's.
4. **Gate wiring** — `GOLDENGRAPH_CHUNK_EXTRACT` off → `_prepare_doc` calls a monkeypatched counting extractor exactly once; on → called once per window.

## Rollout

Default-off gated feature. If the sweep produces a WIN, the verdict records the winning `(size, overlap)` and the gate ships opt-in; a default-on flip would be a separate decision gated on robustness across more than the wiki corpus. If REFUTED, the gate still ships (an opt-in recall knob for callers who want it) and the verdict escalates to the GLiNER hybrid extractor (`extract_local.gliner_extractor`) as the next extraction-recall lever.
