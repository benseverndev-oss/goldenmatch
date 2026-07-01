# L2 Clean Absolute via Alias-Anchored Alignment — Design

**Date:** 2026-07-01
**Status:** Design (approved for spec)
**Follows:** level-2 real-Wikipedia-prose validation (`feat/wiki-prose-substrate`, PR #1341). L2 showed `name_ci` still helps on real prose (R(B) 0.126→0.232) but at **coverage=0.40** — the surface+doc aligner missed 60% of gold, so the L2 absolute is a floor, not a clean number.

## Problem

The 40% coverage has two causes: (a) **surface-form mismatch** — the gold wikilink surface (`Big Blue`) differs from the form the 7B extracted (`IBM`), so surface match fails even when the entity WAS extracted; and (b) **real-prose extraction drop** — the 7B never extracted the entity, so no built node exists to align to (a real floor). Pure offset alignment is blocked: the LLM build emits `surface_names`/`record_keys` but **no character spans** to align a gold offset to. Alias-anchoring dissolves (a) so coverage reflects only (b), the true extraction recall, and the aligned-subset R(B) becomes a clean resolution number.

## Approach (decided)

Match a built node to a gold QID via the QID's **full Wikidata alias set** (labels + altLabels), not the single wikilink surface. Rejected: build-side extraction spans (invasive to goldengraph core, LLM offsets unreliable); position-recovery hybrid (coreference-ambiguous).

## Architecture

Extend the committed wiki snapshot with a `{QID: [alias, ...]}` map (fetched offline from Wikidata, reusing `build_real.py`'s pattern). A new alias-anchored aligner assigns each gold mention to the built node in its doc whose `surface_names` intersect the mention's QID aliases. The `--corpus wiki` eval swaps to this aligner. Re-run baseline vs `name_ci` → the clean L2 absolute + a coverage that now means extraction recall.

## Components

### 1. Alias fetch → committed `dataset/wiki_aliases.json` (`build_wiki_corpus.py`)
- After resolving the closed-set QIDs, batch `wbgetentities` (`props=labels|aliases`, `languages=en`) for those QIDs → `{QID: sorted(set(label + altLabels))}`. Reuse the shipped `build_real.py` alias-fetch shape.
- Write `dataset/wiki_aliases.json`. Committed → reproducible + box-testable; the eval never re-fetches.
- Politeness: same UA + backoff as the corpus fetch; small QID set (~17), so a handful of calls.

### 2. `load_wiki_corpus` returns aliases (`qa_e2e/wiki_corpus.py`)
- Load `wiki_aliases.json` **resolved relative to the passed `path`** (`Path(path).parent / "wiki_aliases.json"`, mirroring how the default corpus path is derived) — NOT a hardcoded dataset dir, so an explicit `path` (e.g. the `tmp_path` test) that has no sibling alias file correctly gets the empty map. Return `(documents, gold_mentions, qid_aliases)`. `qid_aliases` = `{QID: set(lowercased aliases)}`. Back-compat: alias file absent → empty map (aligner degrades to surface-only). **Both existing callers must update to the 3-tuple:** `run_substrate_eval.run_wiki` and `tests/test_wiki_corpus.py::test_load_wiki_corpus_flattens_gold`.

### 3. `align_real_mentions_to_nodes_aliased(graph, gold_mentions, qid_aliases)` + coverage (`substrate_eval.py`)
- Per gold mention `(QID, surface, doc)`: candidates = built nodes touched by an edge sourced from `doc`. The mention's match set = `qid_aliases.get(QID, {surface_lc})` UNION `{surface_lc}` (always include the literal wikilink surface as a fallback). Assign to the candidate whose node surface set (`surface_names` ∪ `{canonical_name}`, case-folded — same fields `_assign_real_nodes` reads) **intersect** the match set; tie-break by **largest intersection**, then **lowest node id**; no match → a UNIQUE decrementing negative (orphan).
- `real_alignment_coverage_aliased` = fraction assigned a non-orphan node.
- Keep the existing surface-only `align_real_mentions_to_nodes` (the engineered-reproduction guard + the surface-only baseline still use it). The aliased fn is additive.

### 4. `run_substrate_eval --corpus wiki` uses the aliased aligner
- Load the 3-tuple `(documents, gold, qid_aliases)`; align via `align_real_mentions_to_nodes_aliased`; emit R(B)/P(B)/coverage. Same baseline-vs-`name_ci` selection by `GOLDENGRAPH_XDOC_KEY`.

## Validation

Modal, `--corpus wiki`, `GOLDENGRAPH_XDOC_KEY` ∈ {unset, `name_ci`}. Report R(B)/P(B)/**coverage** vs the surface-only L2 numbers (baseline R(B)=0.126 / name_ci 0.232 / coverage 0.40).
- **coverage rises (0.40 → higher):** most of the old miss was surface-mismatch; the new coverage = true extraction recall; the aligned-subset R(B) is the clean absolute.
- **coverage stays ~0.40:** real-prose extraction-drop is the true floor; the low number is genuine, not an alignment artifact — also a clean finding.
- Either way `name_ci` vs baseline at equal coverage confirms the direction (expected to persist).

## Scope

**v1:** alias fetch + snapshot file + loader change + aliased aligner + eval swap + one Modal re-run + verdict update. Reuse the existing 19-article corpus (re-fetch only aliases). **Deferred (unchanged):** build-side extraction spans, larger/multi-domain corpus, pronoun coreference.

## File plan

- `dataset/build_wiki_corpus.py` — `fetch_aliases(qids)` + write `wiki_aliases.json`; run offline to regenerate.
- `dataset/wiki_aliases.json` — committed alias map.
- `erkgbench/qa_e2e/wiki_corpus.py` — `load_wiki_corpus` returns `qid_aliases` (back-compat empty if absent).
- `erkgbench/substrate_eval.py` — `align_real_mentions_to_nodes_aliased` + `real_alignment_coverage_aliased` (via a shared `_assign_real_nodes_aliased`).
- `erkgbench/run_substrate_eval.py` — wiki path uses the aliased aligner.
- Tests: `tests/test_wiki_corpus.py` (loader returns the 3-tuple incl. aliases; update the existing 2-tuple unpack), `tests/test_substrate_eval.py` (aliased aligner: alias-set match finds the node when the wikilink surface misses, largest-intersection tie-break, orphan uniqueness, and reduces to **exact-surface** align when `qid_aliases[QID] == {surface}`).

## Testing

Box-safe pure tests as above. Alias fetch is offline (run once, commit the JSON). One Modal re-run for the clean numbers.

## Risks

- **Alias over-match** — a very common alias (e.g. a short token) could match the wrong node in a doc. Mitigated by same-doc candidate restriction + largest-intersection tie-break; if it bites, tighten to exact-alias (drop substring). The surface fallback is always included so behavior is never worse than surface-only.
- **Extraction-drop floor** — alias-anchoring cannot recover entities the 7B never extracted; that residual is real and is the point of the measurement.
- **Snapshot drift** — the alias JSON is pinned/committed; regenerate only alongside the corpus.
