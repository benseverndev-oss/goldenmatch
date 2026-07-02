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

- No new alignment math. Every number below already exists in `substrate_eval.py` (`presence_aligner_report`, `_assign_real_nodes_presence`, `metrics.score`, `edge_recall`). This is assembly + naming + reporting.
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

1. **`SubstrateScorecard`** — a small assembler (pure) in `substrate_eval.py` that returns a labeled two-axis dict:
   ```
   {
     "presence": {"coverage": float},
     "relational": {"f1": float, "recall": float, "precision": float},
     "connectivity": {"coverage": float, "f1": float, "edge_recall": float},
     "coherence": {"components": int, "largest_fraction": float},
   }
   ```
   Implemented by calling `presence_aligner_report` once + `graph_coherence` + `edge_recall` (no re-derivation). `score_substrate` gains a `scorecard` key carrying this (existing flat keys stay for back-compat).

2. **`LEVER_AXIS_MAP`** — a committed constant mapping each substrate lever to the axis it primarily moves, so SP-B/SP-C can route ejections without re-deriving the semantics. Documentation + a data structure, e.g.:
   ```
   LEVER_AXIS_MAP = {
     "presence":    ["chunk_extract", "extract_recall", "extractor"],
     "relational":  ["xdoc_key", "entity_type_canon", "schema_canon", "relation_vocab",
                     "relation_reprompt", "rebel_fuse"],  # reprompt/rebel measurement-gated
     "connectivity":["relation_reprompt", "rebel_fuse"],
   }
   ```
   (Placement note: this is the metric-side contract the driver consumes; it lives with the scorecard, not the config object, so the axis semantics ship with the metric that defines them.)

3. **Reporting** — `run_substrate_eval` prints a two-block labeled line/section instead of the single `[substrate-*]` coverage figure:
   ```
   [substrate-<corpus>] presence: cov=1.000 | relational: F1=0.821 R=0.697 P=1.000 | connectivity: cov=0.508 F1=0.611 edge_recall=0.93 | coherence: comp=14 largest=0.71
   ```
   The old single-number line is removed (it was the misleading artifact); if any tooling greps `coverage=`, it now reads the connectivity block (documented in the report).

## Testing (TDD)

Pure-function tests in `tests/test_substrate_eval.py` (box-safe, no Modal, no LLM):
- `scorecard_presence_matches_relaxed` — presence.coverage == `presence_aligner_report`'s relaxed_coverage on a hand-built graph.
- `scorecard_relational_over_presence` — relational.f1 == relaxed_fb.
- `scorecard_connectivity_is_strict` — connectivity.coverage/f1 == strict_coverage/strict_fb.
- `scorecard_all_present_perfect` — a graph where every gold is a connected node yields presence=1.0, relational.f1=1.0, connectivity.coverage=1.0.
- `scorecard_present_but_unconnected` — a graph where gold entities exist as nodes but have no edges yields presence=1.0, connectivity.coverage low → the exact split this sub-project exists to expose.
- `lever_axis_map_covers_known_levers` — every lever name in `LEVER_AXIS_MAP` is a real `GOLDENGRAPH_*`-backed lever (guards against drift as levers are added).
- `score_substrate_backcompat` — existing flat keys unchanged; new `scorecard` key present.

## Risks / caveats

- **Presence's zero-collision (P=1.0) was partly this corpus's low surface ambiguity** (19 clean tech leads). On a homograph-heavy corpus the global presence match could over-merge (collisions), inflating presence at a precision cost. The scorecard reports `relational.precision` alongside presence exactly so that risk is visible, not hidden — presence is never reported without its precision companion. Documented, not corpus-corrected here.
- **`LEVER_AXIS_MAP` is a hypothesis, not a proof.** It encodes the arc's measured findings (chunking→presence WIN, name_ci→relational WIN, reprompt/rebel refuted). SP-B/SP-C must measurement-gate any lever it routes from this map; the map narrows the search, it does not authorize a blind flip.

## Follow-ons (out of scope here)

- **SP-B:** `SubstrateConfig` frozen dataclass + `apply()` env-materializer + rule-table default picker + staged build harness with ejection gates (profile → sample-extract → slice-build → full-build), each stage emitting this scorecard.
- **SP-C:** `suggest_substrate_config` MCP tool + bounded LLM tweak loop; on ejection hands the LLM `{stage, failing_axis, scorecard, config, candidate_levers}` (candidate_levers ← `LEVER_AXIS_MAP[failing_axis]`); final config must beat the rule-table baseline on this scorecard (the `review_config` self-verify).
