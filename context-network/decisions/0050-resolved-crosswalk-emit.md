# 0050 — Wedge B: resolve once, emit the conformed entity declaration

**Status:** accepted (2026-07-30, Ben) • **Shipped:** `goldenmatch.semantic.build_resolved_crosswalk` + `emit_semantic_model` / `emit_metricflow_yaml` / `emit_from_crosswalk` • **Builds on:** [0049](0049-metric-aware-key-certification.md) • **Planning:** [../planning/semantic-layer-interchange.md](../planning/semantic-layer-interchange.md)

## Context
Wedge A ([0049](0049-metric-aware-key-certification.md)) *certifies* the key a
semantic model already declares. Wedge B is the other half of the planning doc:
**produce the conformed key.** A semantic layer joins on entity-key equality but
never resolves those keys; GoldenMatch does. B runs entity resolution once and
hands back the resolved join key + the MetricFlow declaration that points every
metric at it — "resolve once, every metric inherits correct joins."

## Decision
1. **The resolved key is the control plane's durable `entity_id` (UUIDv7), not a
   run-local surrogate.** `build_resolved_crosswalk` runs the normal `dedupe_df`
   pipeline with the identity graph enabled and reads the assigned ids back from
   `source_records` by record id, returning a `{source, source_pk,
   resolved_entity_id}` crosswalk. Reusing the identity graph is exactly "the
   control plane owns stable IDs" (frame decision test) — inventing a
   canonical-representative id would be a second identity scheme, which the
   frame forbids. Pass `store_path` for ids durable across runs; omit for ephemeral.
2. **Emit is the codegen inverse of A's reader.** `emit_semantic_model` /
   `emit_metricflow_yaml` generate `semantic_models` YAML declaring the resolved
   key as the PRIMARY entity and the original source key as a `unique` entity, so
   `parse_semantic_models(emit_metricflow_yaml(...))` round-trips. House style:
   `yaml.safe_dump(sort_keys=False, default_flow_style=False)`.
3. **MetricFlow-first, library-only, advisory.** Same scope discipline as A: no
   Cube/OSI emitter, no dbt/CLI/MCP surface, no key mutation in the warehouse — B
   produces artifacts a user drops into their project. Parity-free (the module has
   no MCP/CLI/A2A/TS entry; `semantic/` stays out of top-level `goldenmatch/__init__.py`
   so `import goldenmatch` stays polars-free).

## Conformance to the two-engines frame (0047)
- **One authoritative owner** ✅ — the resolved id comes from the Identity Control
  Plane; B is a projection + codegen adapter, not a new id scheme.
- **Compute vs control distinct** ✅ — resolution is the compute pipeline; the
  durable ids + crosswalk are control-plane reads; the emitted YAML is metadata.
- **Arrow at bulk boundaries** ✅ — the crosswalk is a pyarrow table; the YAML is
  small metadata.
- **Advanced, never black-box** ✅ — the crosswalk is inspectable; emit is a scaffold
  the user reviews (measure aggregations are a stamped default, flagged as such).

## Consequences / honest flags
- **The crosswalk inherits ER behavior** — on tiny/degenerate frames zero-config ER
  may not merge (documented toy-merge degeneracy); durability needs a stable
  `store_path`. Ephemeral runs note their non-durability.
- **Emitted measures carry a placeholder `agg`** (`sum`) — the entity/join
  declaration is the deliverable; the user sets real per-measure aggregations.
- **Follow-ons (not v1):** Cube/OSI emitters, a dbt materialization that builds the
  crosswalk table in-warehouse (the existing `goldenmatch_dedupe` `clusters` shape
  is run-local, not the durable entity crosswalk), a CLI/MCP front door, and wiring
  the crosswalk emit into the `key.integrity` reporting surface.

---
**Classification:** decision/accepted • **Last updated:** 2026-07-30
