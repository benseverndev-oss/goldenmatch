# Presence-Aligner Probe — Design

**Date:** 2026-07-02
**Branch:** `feat/presence-aligner-probe` (off `main`)
**Program:** goldengraph substrate-quality arc — a **measure-first diagnostic** for the coverage ceiling. Does NOT change the shipped metric or the engine; it measures a second alignment to decide whether the node-provenance engine fix is worth building.

**Source note:** the ceiling findings live in `docs/superpowers/reports/2026-07-02-deepseek-v3-ceiling-verdict.md` (coverage ~0.49 is model-invariant) and the GLiNER/edge-miss verdicts (all unaligned gold have matching nodes; the block is doc-reachability), all on `main`.

## Problem

Real-prose substrate coverage is pinned at ~0.49 for both the 7B and DeepSeek-V3 — model-invariant. Root cause (confirmed in code): **provenance lives on edges, not nodes.** Graph entities carry no `source_refs`; the aligner's per-doc candidate set (`_assign_real_nodes_aliased`, `by_doc`) is built only from edge endpoints. So an entity extracted from a doc but with no surviving relation has *no doc association* and is structurally unreachable by the doc-keyed aligner — regardless of extractor. The GLiNER probe already showed all ~33 unaligned gold *have* matching nodes; they are present-but-doc-unreachable.

Open question: if the aligner could reach those edgeless-but-present nodes, **how much coverage returns, and does precision survive?** That decides whether the real fix — node-level provenance (a Rust-store change) — is worth building.

## Goal

A pure, box-testable scorer `presence_aligner_report` that, on the same built graph, computes coverage / R(B) / P(B) two ways — **strict** (today's edge-based aligner) and **relaxed** (reach edgeless nodes via global surface/alias match) — and reports the delta. Plus a `--presence-probe` runner to measure it on the best-config graph. Measurement-only; nothing ships to the engine or changes the shipped metric.

## Non-goals

- **Not the engine fix.** No store / `build_batch` / node-provenance change. That is the *conditional* next sub-project this probe gates.
- Not a change to the shipped substrate metric — the strict path stays exactly `_assign_real_nodes_aliased`; the relaxed path is a *second* measurement reported alongside.
- Not a gold-definition change (a separate axis).

## Architecture

Two units, mirroring the GLiNER probe: a **pure scorer** (box-tested) and an **impure runner** (Modal).

### Unit 1 — pure `substrate_eval.presence_aligner_report(graph, gold_mentions, qid_aliases) -> dict`

- `strict = _assign_real_nodes_aliased(graph, gold, aliases)` — the shipped edge-based assignment (unchanged, reused verbatim).
- `relaxed = _assign_real_nodes_presence(graph, gold, aliases)` — strict assignment, then for every gold left unaligned (`node_of < 0`), a **global** match: assign it to a node (any node, ignoring the edge/doc gate) whose surface set matches the gold's alias set, using the same exact-before-substring rule (`_alias_match_surface`, exact set-intersection largest-wins, then substring), lowest node id on tie. Still-unmatched keep their unique decrementing negatives.
- Cluster each assignment (gold indices grouped by node) and score B-cubed via the existing pure `erkgbench.metrics.score(qids, clustering)`.
- Return: `strict_coverage`, `relaxed_coverage` (fraction with `node_of >= 0`), `strict_pb`/`relaxed_pb`, `strict_rb`/`relaxed_rb`, `strict_fb`/`relaxed_fb`, `n_gold`. Pure (metrics is pure math; no LLM/network) → box-testable.

**Why the relaxed path detects collisions honestly:** if two *distinct* gold entities (different QIDs) share a surface and the global fallback maps both to the same node, `metrics.score` sees them clustered together → a false pair → `relaxed_pb` drops. So the precision cost of reaching edgeless nodes globally is *measured*, not hidden — that is the whole point of the probe.

### Unit 2 — runner `run_substrate_eval.run_wiki_presence_probe()`

Reuses `_wiki_build()` (best-config graph), calls the pure scorer, prints one `[presence-probe] strict_cov=… relaxed_cov=… strict_pb=… relaxed_pb=… strict_rb=… relaxed_rb=…` line + a markdown block. Selected by a `--presence-probe` CLI flag on `main` + `GOLDENGRAPH_PRESENCE_PROBE` env alias (so the Modal `--opts` mechanism sets it), mirroring the `--gliner-probe` wiring. `run_wiki` (metrics-only) already has the `_wiki_build` helper from the GLiNER probe — reuse it, no refactor.

## Error handling

Pure scorer is total: empty gold → `n_gold=0`, coverages `1.0`/`1.0` or `0.0` guarded, no divide-by-zero; empty graph → strict and relaxed both leave all gold unaligned. The runner inherits `run_wiki`'s fail-soft build.

## Measurement — the falsifiable read

Two graphs (reproducible): **7B seeded** (seed 42) and **DeepSeek-V3** best config (`name_ci` + chunking `(6,2)`, SCHEMA_CANON off). One `--presence-probe` leg each. Read `relaxed_coverage` vs `strict_coverage` and `relaxed_pb` vs `strict_pb`:

| outcome | signature | meaning → next step |
|---|---|---|
| **Metric artifact** | `relaxed_cov` → ~0.9-1.0 at `relaxed_pb` ~1.0 | The ~51% is entities-present-but-doc-unreachable; the ceiling is the aligner, not the substrate. → **Build node-provenance** (recovers coverage *within-doc*, keeping precision) as the next sub-project. |
| **Real limit** | `relaxed_pb` craters | Cross-doc surface collisions are real; the edge-requirement was load-bearing precision, and ~0.5 is an honest substrate limit. Node provenance (per-doc, safer than global) may still help partially, but the ceiling is partly real. |

Node provenance is the *per-doc* version of what the relaxed probe does *globally*: the probe's global match over-reaches (any doc, not literally only zero-edge nodes), so its `relaxed_pb` is a **directional lower bound** (heuristic, not a theorem) on the precision a real per-doc node-provenance fix would achieve. A clean relaxed result therefore strongly implies a clean engine fix; a dirty one bounds the risk.

**Verdict-wording guards (carry into the report, do not harden):** (1) keep "lower bound" as directional, not a guarantee — the relaxed path re-aligns only the strict *residual*, while a per-doc fix re-aligns the whole gold set against a different pool. (2) "edgeless" is shorthand — the global fallback reaches any-doc same-surface nodes, not strictly zero-edge ones. (3) `strict_pb` and `relaxed_pb` are over *different pair populations* (relaxed clusters more gold → more pairs), so the table must say a P drop means "more-aligned-with-some-collisions," not pure error.

## Testing

Box-safe (hand-built graphs, pure `metrics`, no LLM/network), in `tests/test_substrate_eval.py`:

1. **Edgeless node recovered** — a graph with a gold whose matching node exists but has *no edge in its doc*: assert `strict` leaves it unaligned (`node_of<0`) while `relaxed` aligns it, so `relaxed_coverage > strict_coverage`.
2. **Collision shows as precision drop** — two distinct-QID gold sharing a surface, one only reachable via the global fallback to the *other's* node: assert the mis-alignment lands in `relaxed_pb < 1.0` (the probe detects the risk it's meant to detect).
3. **Strict path unchanged** — `strict_coverage`/`strict_rb`/`strict_pb` equal the existing `real_alignment_coverage_aliased` + `align_real_mentions_to_nodes_aliased` + `metrics.score` on the same graph (no drift in the shipped path).
4. **Degenerate guards** — empty gold → `n_gold=0`, no divide-by-zero; empty graph → both coverages 0.0.

Run via the erkgbench `.venv` + `PYTHONPATH` shadow, `POLARS_SKIP_CPU_CHECK=1`.

## Rollout

Measurement artifact only. Emits `docs/superpowers/reports/2026-07-02-presence-aligner-probe-verdict.md` with the strict-vs-relaxed table and the PASS (build node provenance) / STOP (ceiling partly real) call. Nothing ships to the engine or the shipped metric. If PASS, the verdict hands off to a **node-provenance** engine spec (stamp `source_refs` on entity nodes: store + `build_batch` + query + a per-doc aligner path). If the relaxed precision is dirty, the verdict records that ~0.5 is a more honest limit than it looked and the arc turns to the gold definition.
