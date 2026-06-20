# 0021 — goldenmatch-kg: drop-in ER for KG frameworks

**Status:** accepted • **Shipped:** PR #1127 (2026-06-19)

## Context
ER-KG-Bench established that zero-config goldenmatch beats every popular KG /
agent-memory framework's built-in entity-resolution default at the ER step (on the
self-sourced ghsuite corpus: `auto+fields` 0.969 vs the best framework 0.614), at
$0 / zero LLM calls. But goldenmatch is the entity-RESOLUTION layer, not a KG
builder: the frameworks ingest text, extract entities + relationships,
resolve/dedupe entities, and write a graph — the bench isolates and scores only
that resolve step. The value is to make goldenmatch trivially droppable in as the
resolve stage of the KG pipelines people already use, where each framework exposes
a seam.

## Decision
Ship a new standalone `goldenmatch-kg` package (PyPI; a monorepo package that is
EXCLUDED from the uv workspace) with a framework-agnostic core + three plugin shims.

**Core.** `resolve_entities(entities) -> EntityResolution` is the only
goldenmatch-touching code: it builds a frame from each entity's name/type/
description, runs zero-config `dedupe_df`, and maps the `__row_id__` clusters back
to entity ids (groups + canonical id/name maps). Each shim marshals its framework's
entities in and the merge decision out; the base-free decision helpers (`_resolve.py`)
are locally testable, the framework bindings are import-gated.

**Three seams, honestly unequal.**
- **neo4j-graphrag** — a real `GoldenMatchResolver` subclassing the library's
  `BasePropertySimilarityResolver`, overriding `run() -> ResolutionStats` (confirmed
  against installed 1.17.0). True in-pipeline plugin; replaces `FuzzyMatchResolver`.
- **LlamaIndex PropertyGraphIndex** — a `GoldenMatchEntityResolver` `TransformComponent`
  that canonicalizes entity names before upsert. Additive: LlamaIndex ships no fuzzy
  resolver (its default is exact name+label upsert), so this fills a missing capability.
- **Graphiti** — a post-ingestion `propose_entity_merges(nodes)` decision. Graphiti
  exposes no public resolver seam (dedup is private helpers), so this is a maintenance
  pass over public node objects, not an in-line plugin.

**Excluded from the uv workspace (load-bearing).** The three framework extras are
heavy, fast-moving dep trees that would enter the main `uv.lock` and risk breaking
`uv sync --all-packages` repo-wide (the documented `goldenmatch[native]` footgun).
goldenmatch-kg is therefore a standalone package installed by its own `goldenmatch-kg`
CI lane: core test always, plus a fresh-venv-per-framework matrix so the three dep
trees never collide; installing each extra un-skips that shim's real-library
integration test.

**Lift read off the existing bench board, not re-scored.** Each framework's default
ER row vs the goldenmatch row IS the per-framework delta (neo4j-graphrag 0.322 →
0.969; graphiti 0.379 → 0.969 on ghsuite). Adding "framework + goldenmatch" bench
rows would be degenerate (the goldenmatch resolver owns the clustering decision, so
such a row just reproduces the existing goldenmatch row). Correctness is instead
proven by core parity (vs real `dedupe_df`) + per-shim integration tests that run
each framework's real code.

## Consequences
- New PyPI package `goldenmatch-kg` with `[neo4j-graphrag]` / `[llamaindex]` /
  `[graphiti]` sub-extras, a `goldenmatch-kg.yml` CI lane, and per-framework docs.
  It is NOT a roster/published-suite package yet (no `publish-goldenmatch-kg.yml`),
  so the docs-roster gate does not require it in the root README / docs nav.
- **Shim tests inject a deterministic stub for the goldenmatch decision.** Zero-config
  `dedupe_df` on a ~3-row toy frame commits a degenerate best-effort RED config whose
  fuzzy merge varies by goldenmatch version (1.30 merged "Apple"/"Apple Inc"; 2.2 did
  not) and even across processes — so the shim tests verify the binding/marshaling,
  not goldenmatch's accuracy (covered by core parity + the bench). A flaky-by-version
  toy-merge assertion is the trap to avoid.
- Out of scope (v1): GraphRAG / mem0 / LightRAG / Cognee (no clean seam — recipe
  targets), relationship/edge resolution, MCP-routed resolution, and Graphiti's
  `resolve_existing_entities` client auto-fetch (a documented stub pending its public
  node-list API, which is not installable in the dev env to confirm).
