# Substrate Metric Split — Design (SP-A)

**Date:** 2026-07-02
**Status:** design, pre-implementation
**Sub-project:** SP-A of the substrate-builder config-surface program (SP-B `SubstrateConfig` + staged harness, SP-C MCP/LLM tweak loop follow on this).

## Problem

The substrate is scored by one headline number — an edge-gated `coverage` (~0.49 on wiki) — that **conflates two independent questions**:

1. **Presence:** is the gold entity in the knowledge base at all (extracted + resolved into a node)?
2. **Relational:** given it's present, is it correctly cross-doc-merged and connected?

The presence-aligner probe (#1367) proved these diverge hard: on wiki, presence is ~1.0 while the edge-gated number reads 0.49 — so "0.49" was never "the substrate is half-good," it was the relational/connectivity gate under-counting a substrate whose real relational F1 is ~0.82 (7B) / ~0.90 (V3). Reporting one conflated number hid a good substrate behind a bad-looking figure and, worse, gives no signal about **which** failure is happening.

This sub-project makes the two axes first-class in the scorecard, so:
- Nobody again reads 0.49 as the substrate's quality.
- The downstream config driver (SP-B/SP-C) can **route** a fix by axis: low presence → extraction/chunking levers; high presence + low relational → resolution/xdoc-key/schema-canon levers.

## Non-goals

- No new alignment math. Every number below already exists in `substrate_eval.py` (`presence_aligner_report`, `_assign_real_nodes_presence`, `metrics.score`, `edge_recall`). This is assembly + naming + reporting — plus ONE optional-with-safe-default param (`qid_aliases`) added to `score_substrate` so it can embed the scorecard (acknowledged in Deliverable #2; not a behavior change when omitted).
- No config object, no MCP, no LLM (those are SP-B/SP-C).
- No change to the shipped strict/edge-based aligner behavior (the strict numbers stay reported as the connectivity view).

## The two axes (canonical definitions)

Built entirely from existing pure functions:

| axis | number | source | meaning |
|---|---|---|---|
| **Presence** | `presence_coverage` | `relaxed_coverage` from `presence_aligner_report` (global surface/alias match to any node) | fraction of gold entities that exist as a node in the KB at all |
| **Relational** | `relational_f1` / `_r` / `_p` | `relaxed_fb/_rb/_pb` (clustering scored over the presence alignment) | given presence, how correctly are mentions cross-doc-clustered |
| **Connectivity** (secondary) | `connected_coverage` = `strict_coverage`, `connected_f1` = `strict_fb` | strict edge-gated path | how much of the substrate is actually wired with surviving edges (the old headline, kept + relabeled) |

Rationale for scoring relational over the *presence* (relaxed) alignment rather than the strict one: the relational axis should answer "is the entity correctly clustered," decoupled from whether it happens to have a surviving in-doc edge. The strict view is retained and relabeled **connectivity** so the edge-drop story (the node-provenance verdict's cross-doc residual) is still visible, just no longer masquerading as "coverage."

## Deliverables

1. **`substrate_scorecard(graph, gold_mentions, qid_aliases=None)`** — a NEW standalone pure assembler in `substrate_eval.py` (NOT bolted onto `score_substrate`, which cannot host the presence axis — see below). Returns a labeled dict:
   ```
   {
     "presence": {"coverage": float} | None,   # None when qid_aliases is None
     "relational": {"f1": float, "recall": float, "precision": float},
     "connectivity": {"coverage": float, "f1": float, "edge_recall": float},
     "coherence": {"components": int, "largest_fraction": float},
   }
   ```
   - **Presence requires aliases (the Critical constraint).** `presence_aligner_report` / `_assign_real_nodes_presence` both take `qid_aliases`, and the presence (relaxed global) match only makes sense with an alias set. `qid_aliases` exists **only on the wiki path** (`_wiki_build` → `run_wiki`); the engineered path scores `er_f1_b` via the `align_mentions_to_nodes` **doc-id oracle** and has no aliases. Therefore:
     - **wiki path (aliases present):** full three-axis. `presence.coverage` = `relaxed_coverage`; `relational.{f1,recall,precision}` = `relaxed_{fb,rb,pb}`; `connectivity.{coverage,f1}` = `strict_{coverage,fb}` — all from ONE `presence_aligner_report` call. `connectivity.edge_recall` = `edge_recall(...)`; `coherence` = `graph_coherence(...)`.
     - **engineered path (no aliases):** `presence` = `None` (the doc-id oracle already IS the presence-equivalent for synthetic docs; there is no alias-based global relaxation to compute). `relational` = `metrics.score(...)` over `align_mentions_to_nodes` (i.e. today's `er_r_b`/`er_f1_b`). **`connectivity.coverage`/`.f1` = `None`** — they derive from `presence_aligner_report`'s `strict_*`, which itself needs `qid_aliases`, so they are as uncomputable as presence on this path; only `connectivity.edge_recall` (takes just `graph, gold_mentions`) and `coherence` are alias-free and ARE filled. Sourcing engineered connectivity from the doc-id oracle instead was rejected: it would collapse connectivity into the identical `relational` numbers, adding no signal. So engineered = `{presence:None, relational:{...}, connectivity:{coverage:None, f1:None, edge_recall:float}, coherence:{...}}`. This keeps the assembler total on both corpora without inventing math.
   - **No re-derivation:** built entirely from `presence_aligner_report`, `graph_coherence`, `edge_recall`, `metrics.score`.

2. **`score_substrate` back-compat (explicit).** `score_substrate(*, gold_mentions, resolver_clusters, graph)` keeps its exact current signature and all flat keys UNCHANGED. It gains ONE optional param `qid_aliases=None` and, when non-None, an embedded `"scorecard"` key = `substrate_scorecard(graph, gold_mentions, qid_aliases)`. When `qid_aliases` is None (engineered callers today) the return is byte-identical to current + a `scorecard` with `presence=None`. This is a param ADDITION to a called API — acknowledged here as slightly more than "naming," and kept safe by the default.

3. **`LEVER_AXIS_MAP`** — a committed constant mapping each axis to **the levers that can move it** (deliberately "can move," not "primarily" — a lever may affect more than one axis; the map is a search-narrowing hint for the SP-C router, not an exclusive assignment). Lives with the scorecard (the metric-side contract the driver consumes):
   ```
   LEVER_AXIS_MAP = {
     "presence":    ["chunk_extract", "extract_recall", "extractor"],
     "relational":  ["xdoc_key", "entity_type_canon", "schema_canon", "relation_vocab",
                     "relation_reprompt", "rebel_fuse"],  # reprompt/rebel measurement-gated
     "connectivity":["relation_reprompt", "rebel_fuse", "relation_vocab"],  # edge-adding levers
   }
   ```
   `relation_reprompt`/`rebel_fuse` legitimately appear under both `relational` and `connectivity` — they add edges (connectivity) which in turn drive cross-doc unification (relational); the "can move" framing makes that overlap correct rather than contradictory.

4. **Reporting — BOTH print sites + the markdown tables.** There are TWO eval outputs:
   - `run_substrate_eval.py` wiki line (currently `[substrate-wiki] ... coverage=...`) → replace with the labeled block:
     ```
     [substrate-wiki] presence: cov=1.000 | relational: F1=0.821 R=0.697 P=1.000 | connectivity: cov=0.508 F1=0.611 edge_recall=0.93 | coherence: comp=14 largest=0.71
     ```
   - engineered `[substrate]` line (currently `ER-F1(A)/(B)`, no `coverage=`) → append `relational: F1=.. R=.. P=.. | connectivity: edge_recall=.. | coherence: comp=.. largest=..` (no `presence:` block since presence=None, and no `connectivity: cov/F1` since those are None on the no-alias path — only `edge_recall` prints).
   - the two markdown tables (the `coverage` column in the wiki `out_md` and `_to_markdown`) → relabel the `coverage` column to `connectivity_cov` and add `presence_cov` (wiki only) + `relational_f1` columns, so the persisted `results/substrate_*.md` carry the split too.
   Consumer risk is low: a repo grep shows only the two eval modules + `tests/test_substrate_eval.py` reference these strings — no CI/workflow parses them. The report notes that any ad-hoc `grep coverage=` now reads the **connectivity** block (the old number, correctly relabeled).

## Testing (TDD)

Pure-function tests in `tests/test_substrate_eval.py` (box-safe, no Modal, no LLM):
- `scorecard_presence_matches_relaxed` — with `qid_aliases`, presence.coverage == `presence_aligner_report`'s relaxed_coverage on a hand-built graph.
- `scorecard_relational_over_presence` — relational.f1/recall/precision == relaxed_fb/rb/pb.
- `scorecard_connectivity_is_strict` — connectivity.coverage/f1 == strict_coverage/strict_fb.
- `scorecard_no_aliases_presence_none` — `substrate_scorecard(graph, gold, qid_aliases=None)` returns `presence=None`, `connectivity.coverage`/`.f1` = `None` (alias-dependent), and still fills `relational` (over `align_mentions_to_nodes`), `connectivity.edge_recall`, and `coherence` (the engineered path).
- `scorecard_all_present_perfect` — a graph where every gold is a connected node yields presence=1.0, relational.f1=1.0, connectivity.coverage=1.0.
- `scorecard_present_but_unconnected` — a graph where gold entities exist as nodes but have no edges yields presence=1.0 (global match) while connectivity.coverage≈0 (doc-keyed strict path finds no edge candidates) → the exact split this sub-project exists to expose.
- `lever_axis_map_names_are_real_gates` — every lever name that appears in ANY `LEVER_AXIS_MAP` value resolves to a real `GOLDENGRAPH_*` gate. Source of truth = an explicit `KNOWN_LEVERS` dict (lever name → env var) defined alongside the map (there is no lever registry in `goldengraph` to enumerate against; SP-B introduces `SubstrateConfig` which becomes that registry, at which point this test upgrades to a bidirectional check). This test guards map→real drift only; it deliberately does NOT claim to catch a newly-added lever omitted from the map (that is an SP-B concern once the config registry exists).
- `score_substrate_backcompat_no_aliases` — `score_substrate(...)` without `qid_aliases` returns all existing flat keys unchanged (byte-identical values on a fixture) plus a `scorecard` with `presence=None`.
- `score_substrate_embeds_scorecard_with_aliases` — passing `qid_aliases` embeds a full three-axis `scorecard`.

## Risks / caveats

- **Presence's zero-collision (P=1.0) was partly this corpus's low surface ambiguity** (19 clean tech leads). On a homograph-heavy corpus the global presence match could over-merge (collisions), inflating presence at a precision cost. The scorecard reports `relational.precision` alongside presence exactly so that risk is visible, not hidden — presence is never reported without its precision companion. Documented, not corpus-corrected here.
- **`LEVER_AXIS_MAP` is a hypothesis, not a proof.** It encodes the arc's measured findings (chunking→presence WIN, name_ci→relational WIN, reprompt/rebel refuted). SP-B/SP-C must measurement-gate any lever it routes from this map; the map narrows the search, it does not authorize a blind flip.

## Follow-ons (out of scope here)

- **SP-B:** `SubstrateConfig` frozen dataclass + `apply()` env-materializer + rule-table default picker + staged build harness with ejection gates (profile → sample-extract → slice-build → full-build), each stage emitting this scorecard.
- **SP-C:** `suggest_substrate_config` MCP tool + bounded LLM tweak loop; on ejection hands the LLM `{stage, failing_axis, scorecard, config, candidate_levers}` (candidate_levers ← `LEVER_AXIS_MAP[failing_axis]`); final config must beat the rule-table baseline on this scorecard (the `review_config` self-verify).
