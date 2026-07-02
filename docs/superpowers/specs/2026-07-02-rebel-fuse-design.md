# REBEL Fusion — Design

**Date:** 2026-07-02
**Branch:** `feat/rebel-fuse` (off `main`)
**Program:** goldengraph substrate-quality arc — a second, distinct **relation-recall** lever, after the relation re-prompt WON (R(B) +33%, F1 +26%, PR #1355). This tests whether a discriminative relation extractor (REBEL) recovers correct edges the generative 7B re-prompt still misses.

**Source note:** the motivating findings (edge-miss residual is relation-never-extracted; re-prompt win) are in `docs/superpowers/reports/2026-07-01-{edge-miss-diagnostic,relation-reprompt}-verdict.md`, both on `main`.

## Problem

The re-prompt recovered relation recall by re-asking the *same* 7B for relations over a provided entity list. REBEL (Babelscape/rebel-large, a BART seq2seq model trained end-to-end for relation extraction) is a *different kind* of extractor — discriminative, English-Wikipedia-trained, well-suited to lead prose. It may catch relations the 7B omits even on the second pass. But it may also be redundant with the re-prompt, or inject noise via surface-mapping errors. This lever builds the fusion and measures its **marginal value on top of the re-prompt** — the only question that matters, since re-prompt already ships.

## Goal

A gated `GOLDENGRAPH_REBEL_FUSE=1` pass that runs REBEL per window over the doc, maps each `(head, rel, tail)` triple's endpoints onto the **already-extracted** entity set, and appends an edge only when *both* endpoints map — adding edges among known entities, never new nodes. Independent of the re-prompt gate (both can be on). Default-off; measured as the 3-way delta control / re-prompt / re-prompt+REBEL on the wiki rig.

## Non-goals

- **No new entity nodes.** REBEL triples with an endpoint that doesn't map to an existing mention are dropped (the diagnostic proved entities aren't the gap; an unmapped endpoint can't be edged; dropping keeps precision).
- No predicate-vocab snapping — REBEL's relation labels are kept verbatim (the substrate metric aligns on entity-pairs, not predicate strings). **Caveat (measurement scope):** REBEL emits Wikidata-style labels ("founded by", "country of citizenship"), which will almost never match a closed `GOLDENGRAPH_RELATION_VOCAB`. So under `GOLDENGRAPH_SCHEMA_CANON=1`, `_maybe_canonicalize` (which *drops out-of-schema edges*) would silently discard most REBEL edges — nullifying the lever. This is asymmetric with the re-prompt, which passes the vocab instruction so its edges survive canon. Therefore **the measurement rig runs `SCHEMA_CANON` off / no relation vocab** (as the chunking + re-prompt rig does), and a WIN here is claimed **only for canon-off configs**. Making REBEL edges survive a canon config would need a predicate-normalization step — out of scope for this lever (a follow-on if a canon-on deployment wants it).
- No edge dedup (consistent with re-prompt/chunking; duplicates are benign for pair/component metrics).
- Not a training/fine-tuning effort — off-the-shelf REBEL only.

## Architecture

### New module `goldengraph/rebel_fuse.py`

Isolated, box-testable, parallel to `relation_reprompt.py`:

- `rebel_fuse_enabled() -> bool` — `GOLDENGRAPH_REBEL_FUSE` gate (case-insensitive, stripped, empty-safe: `""/"0"/"false"/"no"/"off"` → off; mirror `relation_reprompt_enabled`).
- `rebel_fuse(text, mentions, *, rebel=None) -> list[Relationship]` — windows `text`, runs the REBEL triple-extractor per window, maps triple endpoints onto `mentions`, returns `Relationship(subj_idx, rel, obj_idx)` for triples where both endpoints map and `subj_idx != obj_idx`. `rebel` is an injectable `text -> list[(head,rel,tail)]` callable (tests pass a fake); default `None` → the lazily-loaded cached real REBEL. Empty mentions → `[]` (no model call). Any error → `[]` (fail-soft).
- `_load_rebel()` — module-level cached singleton guarded by a `threading.Lock` (the prepare phase runs concurrently across docs). Loads Babelscape/rebel-large once; returns a `text -> list[(head,rel,tail)]` callable that tokenizes (truncate 256), `generate`s, decodes with special tokens, and reuses the existing unit-tested `extract_local.parse_rebel_triplets`. It does NOT reuse `triplets_to_extraction` (that mints untyped mentions — we want only edges among existing entities).

### The seam — `ingest._prepare_doc`

After the extraction is built and after the re-prompt append, BEFORE `_maybe_canonicalize`:

```python
extraction = chunk_extract(...) if chunk_extract_enabled() else _extractor(text, llm)
if relation_reprompt_enabled():
    try: extraction.relationships += relation_reprompt(text, extraction.mentions, llm)
    except Exception: pass
if rebel_fuse_enabled():
    try: extraction.relationships += rebel_fuse(text, extraction.mentions)
    except Exception: pass
# ... then _maybe_canonicalize
```

Same placement rationale as the re-prompt (edges get direction/schema canonicalization) and the same load-bearing `try/except` (the outer `_prepare_doc` except returns an EMPTY extraction, so a REBEL failure must never propagate there and discard the first-pass work). Downstream (`_maybe_canonicalize → resolve → build_batch → append`) is untouched.

## Components

### `_load_rebel()` triple-extractor

Reuses `rebel_extractor`'s load path (transformers `AutoModelForSeq2SeqLM` + `AutoTokenizer`, `Babelscape/rebel-large`; `transformers`+`torch` are already in the Modal image via the gliner dep). Returns a callable that, per input text: tokenize with `truncation=True, max_length=256`, `generate(max_length=256)`, `decode(skip_special_tokens=False)`, then `parse_rebel_triplets(decoded)` → `list[(head, rel, tail)]`. Cached in a module global under a `threading.Lock` (double-checked) so concurrent first-calls in the parallel prepare phase load it exactly once.

**Concurrency note (perf, not correctness):** after load, the shared model's `generate` is called from up to `GOLDENGRAPH_BUILD_WORKERS` (default 8) prepare threads. Torch eval-forward is reentrant so this is correct, but it oversubscribes intra-op CPU threads — a perf wrinkle on the shared box, not a bug. Acceptable for a 19-doc measurement; a real deployment might serialize REBEL or lower the worker count.

### Windowing

REBEL's ~256-token window can't hold the ~2750-char lead, so window the input: `sentence_windows(split_sentences(text), size, overlap)` (reuse `chunk_extract`'s utilities — DRY) with `GOLDENGRAPH_REBEL_SENTENCES` (default 4) / `GOLDENGRAPH_REBEL_OVERLAP` (default 1), parsed defensively (empty-string-env safe, same helper style as chunk_extract). Run REBEL per window; concatenate triples across windows.

**Residual truncation (accepted).** A 4-sentence window is not *guaranteed* ≤256 tokens — a window of long sentences still hits `truncation=True, max_length=256` and silently drops tail relations. Windowing mitigates but does not eliminate this. The bias is conservative (it costs REBEL *fewer* edges, biasing toward REFUTED), so a WIN is trustworthy; if REFUTED, a smaller `REBEL_SENTENCES` is the cheap first retry before believing it. Not token-capped in v1.

### Surface → entity mapping (`_match_mention`)

`_match_mention(surface_lc: str, mentions) -> int | None`: case-fold the REBEL surface; return the index of the first mention whose name matches — **exact (case-folded) preferred over substring-either-way**, lowest index breaking ties; `None` if none match. A local helper (the bench's `_alias_match_surface` lives in the erkgbench package, not importable here; keep a small self-contained version). A triple `(head, rel, tail)` yields `Relationship(_match_mention(head), rel, _match_mention(tail))` only when both are non-None and distinct.

### Predicate

Kept verbatim from REBEL. No snapping.

## Error handling

Fail-soft at two levels (mirrors re-prompt): `rebel_fuse` returns `[]` on empty mentions or any exception (model load, inference, decode, mapping); a per-window inference error skips that window (continue), not the doc. The seam wraps the call in `try/except: pass` as the load-bearing second guard against the empty-extraction fallback.

## Measurement — the 3-way marginal delta

Same wiki/7B/best-config rig (`name_ci` + chunking `(6,2)`, **`SCHEMA_CANON` off / no relation vocab** — see the predicate caveat in Non-goals; REBEL edges must survive to drive merges), plain `run_wiki` (coverage / R(B) / P(B) / components):

| leg | config | reads |
|---|---|---|
| control | best config | baseline R(B)/F1 |
| re-prompt | + `RELATION_REPROMPT=1` | the shipped win (~R(B) 0.303 / F1 0.465) |
| **re-prompt+REBEL** | + `RELATION_REPROMPT=1` + `REBEL_FUSE=1` | **does REBEL add on top?** |
| REBEL-alone (optional) | + `REBEL_FUSE=1` only | REBEL's standalone relation recall vs control |

- **WIN:** re-prompt+REBEL R(B)/F1 **above** re-prompt-alone, P(B) ~1.0, components not worse — REBEL found correct edges the re-prompt missed.
- **REFUTED (redundant):** re-prompt+REBEL ≈ re-prompt-alone → REBEL overlaps what re-prompt recovers; ship default-off, relation-recall thread saturates at the re-prompt.
- **REFUTED (harmful):** P(B) drops / components collapse → REBEL injected spurious or mis-mapped edges. P(B) + components are the guardrails (the recall-prompt lesson).

The REBEL-alone leg is a cheap bonus (is REBEL competitive standalone?); the verdict hinges on the marginal delta over re-prompt.

**Surface-mapping precision is the key risk.** REBEL surfaces may substring-match the wrong entity (e.g. a short head colliding with an unrelated mention), creating a false edge → P(B) drop. The exact-before-substring rule and the P(B) guardrail are the defense; if P(B) falls, that's the signal the mapping is too loose.

## Testing

Box-safe (injected fake REBEL, no model/network), in `packages/python/goldengraph/tests/test_rebel_fuse.py`:

1. **Mapping** — fake rebel returns `[("Amazon","founded by","Jeff Bezos")]` over mentions `[Amazon/org, Jeff Bezos/person]` → one `Relationship(0,"founded by",1)`; a substring case (`"Bezos"` → `"Jeff Bezos"`) maps; case-folded.
2. **Drop unmapped + self-loop** — a triple whose head matches no mention → dropped; a triple whose head and tail map to the same index → dropped.
3. **Windowing** — fake rebel asserts it is called once per sentence window (reuses `sentence_windows`); triples concatenate across windows.
4. **Gate + empty guards** — `rebel_fuse_enabled` env parsing (case-insensitive, empty-safe); empty `mentions` → `[]` with the fake rebel never called.
5. **Wiring in `_prepare_doc`** — gate off → `rebel_fuse` not invoked (monkeypatch counter); gate on → invoked once, edges appended; a raising `rebel_fuse` preserves the first-pass extraction (not the empty-extraction fallback). For this to patch the seam without loading the real model, `ingest.py` must import `rebel_fuse` as a top-level name (`from .rebel_fuse import rebel_fuse, rebel_fuse_enabled`, mirroring the re-prompt import) so `monkeypatch.setattr(ingest, "rebel_fuse", fake)` intercepts the seam's call.

Run via the goldengraph `.venv` + `PYTHONPATH` shadow, `POLARS_SKIP_CPU_CHECK=1 GOLDENGRAPH_NATIVE=0 -p no:cacheprovider`. The real REBEL model is never loaded in tests (always injected or monkeypatched).

## Rollout

Default-off gated feature. If the marginal delta is a WIN, the verdict records it and the gate ships opt-in (a default-on flip would need a second corpus and weighs REBEL's per-doc inference cost + the model dependency). If REFUTED (redundant or harmful), the gate still ships as an opt-in knob and the verdict closes the relation-recall thread at the re-prompt — the substrate arc then turns to cross-corpus robustness of the shipped stack (name_ci + chunking + re-prompt).
