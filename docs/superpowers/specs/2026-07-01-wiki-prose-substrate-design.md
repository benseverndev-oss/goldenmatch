# Level 2: Real Wikipedia-Prose Substrate Validation — Design

**Date:** 2026-07-01
**Status:** Design (approved for spec)
**Follows:** level-1 real-*entity* validation (`feat/real-corpus-substrate`, PR #1340) — validated the type-jitter fix on real entities/types/aliases but with **synthetic edges + `X {rel} Y` rendering**. Level 2 is the deferred **real sentence prose** de-risk.

## Problem

Every substrate result so far — including level-1's real entities — used the engineered generator's clean `"{surface} {rel} {surface}."` rendering. Real Wikipedia prose is different in kind: complex clauses, distractor entities, apposition, coreference, dates/numbers. The 7B's *extraction* (finding the right entities + relations) is far harder there than on a bare template. Level 2 measures whether `name_ci`'s win survives real sentence extraction — the last unquantified term in the substrate story. The gap between level-1 (`name_ci` R(B)=0.976 on clean real-entity sentences) and level-2 is the **real-prose extraction penalty**.

## Approach (decided)

- **Corpus:** real Wikipedia articles, **seed + 1-hop expand** (a closed, interconnected set so cross-document co-reference exists).
- **Gold:** Wikipedia's own `[[Target|Surface]]` wikilinks, `Target` resolved to a Wikidata QID (editor-curated, precise). Gold restricted to targets inside the closed fetched set.
- **Alignment:** a new surface-and-doc alignment (the engineered `src::rel::dst` doc-id oracle is gone on real prose).

The alternatives (string-match gold; off-the-shelf EL dataset) were rejected in brainstorming (noisy gold; domain shift + heavy).

## Architecture

Offline fetch → committed snapshot → eval. `dataset/build_wiki_corpus.py` (network, run once like `build_real.py`) writes a reproducible snapshot; the eval reads the snapshot (pure, no eval-time network), builds a graph from the real prose via `ingest_corpus`, aligns gold mentions to built nodes via `align_real_mentions_to_nodes`, and scores **baseline `(name,typ)` vs `name_ci`** — the same comparison as levels 0/1.

## Components

### 1. Corpus fetch → committed snapshot (`dataset/build_wiki_corpus.py`)
- Input: a small **seed QID list** (interconnected; e.g. a handful of tech companies + acquisitions, or a curated seed file `dataset/wiki_seeds.jsonl`).
- Per seed QID: Wikidata sitelink → enwiki page → fetch **wikitext** (`action=query&prop=revisions&rvprop=content&rvslots=main`, pin `revid` for reproducibility) + a plain-text rendering.
- **1-hop expand:** collect the wikilink targets of the seeds' articles; fetch those articles too. The closed set = seeds ∪ their linked entities (bounded — cap at N articles).
- Per article: parse `[[Target|Surface]]` and `[[Target]]` wikilinks → `(Surface, Target_title)`; resolve `Target_title → QID` via Wikidata sitelinks (batch `wbgetentities` by title). Keep only gold whose `Target_QID` is in the closed set.
- **Plain text:** wikilinks reduced to their anchor `Surface` (so the LLM sees natural prose, no markup); templates/refs stripped best-effort.
- Emit `dataset/wiki_corpus.jsonl`: one record per article `{doc_id: <article_QID>, revid, text: <plain prose>, gold: [[Target_QID, Surface], ...]}`. Committed → reproducible + box-testable.

### 2. Corpus loader (`erkgbench/qa_e2e/wiki_corpus.py`)
- `load_wiki_corpus()` reads `wiki_corpus.jsonl` → `documents` (id=doc_id, text) + `gold_mentions` list `(Target_QID, Surface, doc_id)` flattened from every article's `gold`. Pure / no network.

### 3. New alignment `align_real_mentions_to_nodes(graph, gold_mentions)` (in `substrate_eval.py`)
- Build a `by_doc_surface` index from the graph: for each edge, for each `source_ref` doc, record its endpoint nodes; each node carries `surface_names`.
- Per gold mention `(qid, surface, doc)`: candidate nodes = built nodes touched by an edge sourced from `doc`; assign to the candidate whose `surface_names` matches `surface` (case-folded): **exact match > substring**, tie-break by most edges; no candidate matches → a fresh negative id (orphan / build miss, mirrors the engineered align's miss handling).
- Group gold-mention indices by assigned node → clustering → `metrics.score`.
- **Emit alignment coverage** (`aligned / total` gold mentions) into the scoreboard so a low-coverage run cannot masquerade as a low ER score.

### 4. Eval runner
- `run_substrate_eval.py` gains a `--corpus wiki` path (or a sibling `run_substrate_eval_real.py`): load the wiki snapshot instead of `generate_engineered`, build, align via `align_real_mentions_to_nodes`, score baseline vs `name_ci`. No ambiguity dial (real prose has its own surface variance).

## Validation

Substrate eval on `wiki_corpus.jsonl`, `GOLDENGRAPH_XDOC_KEY` ∈ {unset baseline, `name_ci`}. Report R(B)/P(B)/coverage beside the level-0 (engineered) and level-1 (real-entity) numbers. This is a **calibration**, not a pass/fail gate:
- **`name_ci` still beats baseline on real prose** → the fix generalizes all the way to real sentences (the strongest possible validation).
- **The level-1→level-2 drop** quantifies the real-prose extraction penalty.
- **Alignment coverage** must be high enough (say ≥0.7) for the numbers to mean anything; report it prominently.

## The key risk + its guard

**The alignment is an approximation** (surface+doc match, not a gold oracle) — the central threat to measurement validity. Guard: a **sanity check that `align_real_mentions_to_nodes` reproduces the engineered-align result on the engineered corpus** (feed it engineered gold + graph; it must recover the known R(B) within noise). If the general aligner can't reproduce the oracle's number where the oracle applies, it isn't trustworthy on real prose. This test gates the whole level-2 conclusion.

## Scope

**v1:** seed file + fetch script + committed snapshot + loader + new alignment + eval path + one Modal run. **Deferred:**
- Pronoun/coreference gold (Wikipedia doesn't wikilink pronouns → gold is named-mention co-reference).
- Larger corpora / multi-domain seeds.
- Homograph analysis on real prose.

## File plan

- `dataset/wiki_seeds.jsonl` — the seed QID list (committed).
- `dataset/build_wiki_corpus.py` — fetch + wikilink-parse + resolve + emit `wiki_corpus.jsonl` (network, offline).
- `dataset/wiki_corpus.jsonl` — the committed snapshot.
- `erkgbench/qa_e2e/wiki_corpus.py` — `load_wiki_corpus()` + the wikilink parser `parse_wikilinks(wikitext)`.
- `erkgbench/substrate_eval.py` — `align_real_mentions_to_nodes` + coverage.
- `erkgbench/run_substrate_eval.py` — `--corpus wiki` path.
- Tests: `tests/test_wiki_corpus.py` (parser + loader), `tests/test_substrate_eval.py` (align_real: match/tie-break/orphan/coverage + the engineered-reproduction sanity check).

## Testing

Box-safe pure tests for `parse_wikilinks` (`[[A|B]]`→(B,A), `[[A]]`→(A,A), nested/pipe edge cases), `load_wiki_corpus` (jsonl→docs+gold), and `align_real_mentions_to_nodes` (surface+doc match, exact>substring tie-break, orphan→fresh id, coverage, and reproduces engineered-align on an engineered fixture). Fetch script run once offline (network); one Modal run for the numbers.

## Risks

- **Alignment approximation** — guarded by the engineered-reproduction sanity check + coverage reporting (above).
- **Fetch fragility / Wikipedia markup** — best-effort plain-text; pin `revid`; commit the snapshot so the eval never re-fetches.
- **Sparse cross-doc co-reference** — seed+1-hop mitigates, but if coverage/co-reference is too thin the run is inconclusive (report it, don't over-claim).
- **Title→QID resolution misses** (redirects, disambiguation) — drop unresolved wikilinks from gold rather than guess.
