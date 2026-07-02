# Relation Re-Prompt — Design

**Date:** 2026-07-01
**Branch:** `feat/relation-reprompt` (off `main`)
**Program:** goldengraph substrate-quality arc — the first **relation-recall** lever, after the edge-miss diagnostic showed the real-prose residual is *relation-never-extracted* (the 7B extracts the entities but omits the edge connecting them), not resolver-dropped and not an entity-recall gap.

## Problem

The GLiNER probe + edge-miss diagnostic pinned the real-prose ceiling precisely: of 65 gold, 32 align and 33 are **edge-miss** — the entity is a node in the graph but has no surviving edge, so the edge-centric aligner can't reach it. Exact resolution recovers zero of them, so the relations were simply never extracted. The entities are already correct and unioned (name_ci + chunking got them in); only the edges are missing.

## Goal

A gated `GOLDENGRAPH_RELATION_REPROMPT=1` **second pass** that, after (chunked) extraction, hands the 7B the already-extracted entity list plus the full document text and asks specifically *"what relations hold among these entities?"*, appending the found edges. It splits the hard joint task (find entities AND relations in dense prose) into two easy tasks — entities first (already done), then relations over a given list — the same "narrow the task" principle that made chunking win. Runs whole-doc over the unioned entity set, so it also attacks the cross-window relation loss that is chunking's known limitation. Default-off; measured on the wiki rig via coverage uplift (edge-miss → aligned).

## Non-goals

- No change to `extract.py`, `chunk_extract.py`, or the union logic — the entities are correct; this only adds edges.
- No edge dedup (consistent with the chunking union decision; duplicates are benign for the pair/component metrics).
- Not REBEL fusion and not a first-pass prompt change — those are separate, later levers if this underdelivers.
- No gold relations exist, so no direct relation-P/R; measured indirectly via the substrate metric (below).

## Architecture

### New module `goldengraph/relation_reprompt.py`

Isolated and box-testable, mirroring `chunk_extract.py`:

- `relation_reprompt_enabled() -> bool` — `GOLDENGRAPH_RELATION_REPROMPT` gate. Case-insensitive, stripped, empty-safe: `""/"0"/"false"/"no"/"off"` → off (reuse the `chunk_extract_enabled` pattern to avoid the empty-string-env footgun).
- `relation_reprompt(text, mentions, llm, *, relation_vocab=None) -> list[Relationship]` — formats `mentions` as a numbered entity list, prompts `llm`, parses `{subj, predicate, obj}` with subj/obj as indices **into the provided list**, drops out-of-range endpoints and self-loops, returns new `Relationship`s indexed into that same `mentions` list. Empty `mentions` → `[]` (no LLM call). Any LLM/parse error → `[]` (fail-soft).

### The seam — `ingest._prepare_doc`

After the extraction is built (single-pass or chunked), before resolve:

```python
extraction = chunk_extract(text, llm, _extractor) if chunk_extract_enabled() else _extractor(text, llm)
if relation_reprompt_enabled():
    extraction.relationships += relation_reprompt(text, extraction.mentions, llm)
```

`text` = the whole doc; `extraction.mentions` = the unioned entity set → whole-doc-over-unioned-entities scope. Runs after chunk_extract (composes with the chunking win) and independently of it. Everything downstream (`build_batch → _cross_doc_link → append`) is untouched; the extra edges give the edge-miss entities the edges the aligner needs.

## Components

### The prompt (`_RELATION_REPROMPT`)

Gives the model the easy half — entities provided, only connect them:

```
Given this text and a numbered list of entities found in it, list every relation that
holds BETWEEN TWO of these entities, grounded in the text. Return STRICT JSON only:
{"relationships": [{"subj": <entity #>, "predicate": "<verb phrase>", "obj": <entity #>}]}
`subj`/`obj` are numbers from the entity list. Use only relations stated or clearly
implied by the text. Omit an entity if it has no relation.
Entities:
0: <name> (<type>)
1: ...
Text:
<full doc text>
```

When `GOLDENGRAPH_RELATION_VOCAB` (or the `relation_vocab` arg) is set, prepend the existing `_RELATION_VOCAB_INSTRUCTION` from `extract.py` (reuse — same closed-predicate + direction rules as first-pass extraction, so the re-prompt honors the same schema). Resolve the vocab with the same precedence as `extract._relation_vocab` (arg, else env, else open).

### Parsing

A relationships-only reuse of `parse_extraction`'s discipline: `json.loads(_strip_fence(raw))`; for each rel require `subj`/`obj` are `int`, `0 <= idx < len(mentions)`, `subj != obj`; build `Relationship(subj, predicate=str(...), obj)`. Malformed JSON or a bad endpoint → drop that edge (or return `[]` on top-level JSON failure). Same defensive posture that keeps LLM drift from poisoning the graph.

### JSON-mode reuse

Call through `extract._complete_extraction(llm, prompt)` so the re-prompt gets the same forced-JSON path (`complete_json` when available and `GOLDENGRAPH_EXTRACT_JSON_MODE != 0`) with the `.complete` fallback for stubs.

### No edge dedup

The re-prompt may re-emit first-pass edges. Duplicates are benign — the substrate metrics key on entity-pairs and components, not edge multiplicity — so append without dedup, exactly as `chunk_extract`'s union does.

## Error handling

Fail-soft throughout: empty mentions → `[]` (no call); LLM error or unparseable output → `[]` (extraction unchanged, doc still builds). The `_prepare_doc` try/except already guards the surrounding extract+resolve.

## Measurement

No gold relations exist, so success is measured **indirectly** on the same wiki/7B/best-config rig (`name_ci` + chunking `(6,2)`): a relation-recall win converts edge-miss entities into aligned ones, so **coverage and R(B) rise**.

- **Primary signal (always available):** `run_wiki` coverage / R(B) / P(B) / components — control (re-prompt off) vs `GOLDENGRAPH_RELATION_REPROMPT=1`.
- **Confirmatory readout (if #1353's `--gliner-probe` is on main by measurement time):** `edge_miss` should drop from 33, `ner_miss` stays 0 — a direct count of edge-miss entities that gained an edge. If #1353 isn't merged yet, rebase onto main first, else skip this readout (coverage is sufficient for the verdict).

| signal | control | WIN target |
|---|---|---|
| coverage | ~0.49 | up |
| R(B) | baseline | up |
| P(B) | ~1.0 | holds ~1.0 |
| components | ~14 | not materially worse |
| edge_miss (if probe) | 33 | down |

- **WIN:** coverage/R(B) up, P(B) holds, components stable (and edge_miss down if measured).
- **REFUTED:** coverage flat → the relations aren't in the lead text, or the 7B won't emit them even handed the entity list and asked directly. Clean negative; the thread then turns to REBEL fusion or accepts the ceiling.

**Watch for over-connection (the recall-prompt lesson).** Asking for relations could make the 7B invent spurious edges, which would show as P(B) dropping or components collapsing (over-merge). P(B) and components are the guardrails, exactly as in the chunking sweep.

## Testing

Box-safe (capturing/fixed stub LLM, no network), in `packages/python/goldengraph/tests/test_relation_reprompt.py`:

1. **Prompt formatting** — a capturing stub records the prompt; assert the numbered entity list and each entity's name/type appear; with `relation_vocab` set, the vocab instruction is prepended.
2. **Parse + index mapping** — stub returns a fixed `{"relationships":[{subj,predicate,obj}]}`; assert the returned `Relationship`s carry the predicate and indices point into the provided `mentions`.
3. **Defensive drops** — an out-of-range endpoint and a `subj==obj` self-loop are dropped; malformed JSON → `[]`.
4. **Gate + empty guards** — `relation_reprompt_enabled` env parsing (case-insensitive, empty-safe); empty `mentions` → `[]` with no LLM call.
5. **Wiring in `_prepare_doc`** — gate off → the re-prompt callable is not invoked (counter stub); gate on → invoked once and `extraction.relationships` is extended.

Run via the goldengraph `.venv` + `PYTHONPATH` shadow, `POLARS_SKIP_CPU_CHECK=1 GOLDENGRAPH_NATIVE=0 -p no:cacheprovider`.

## Rollout

Default-off gated feature. If the wiki measurement shows a WIN, the verdict records it and the gate ships opt-in (a default-on flip would need the win to hold on a second corpus and to weigh the extra LLM call/doc). If REFUTED, the gate still ships (an opt-in relation-recall knob) and the verdict escalates to REBEL fusion (`extract_local.rebel_extractor`, already stubbed) — local relation extraction fused with the LLM entities, the edge-side analog of the GLiNER-hybrid shape.
