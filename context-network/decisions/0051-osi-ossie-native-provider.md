# 0051 — Wedge C: OSI / Apache Ossie native identity provider (bidirectional)

**Status:** accepted (2026-07-30, Ben) • **Shipped:** `goldenmatch.semantic.{parse_osi_models, osi_join_keys, certify_osi_relationships, emit_osi_model, emit_osi_yaml, emit_osi_from_crosswalk}` + `Osi*` dataclasses • **Builds on:** [0049 (certify)](0049-metric-aware-key-certification.md) + [0050 (resolve+emit)](0050-resolved-crosswalk-emit.md) • **Planning:** [../planning/semantic-layer-interchange.md](../planning/semantic-layer-interchange.md)

## Context
Wedges A + B target dbt/MetricFlow. Wedge C is the planning doc's moonshot: make
GoldenMatch a native provider for **Open Semantic Interchange (Apache Ossie)** —
the vendor-neutral interchange spec onto which the whole ecosystem is converging.
C closes the loop bidirectionally: **consume** an OSI model to learn which keys the
metrics join on, and **emit** valid OSI declaring the GoldenMatch-resolved key as
the conformed join. Resolve once against the interchange standard → every
OSI-consuming tool (dbt SL, Polaris, Cube, …) inherits correct joins.

The ADR-0049 caveat was "attach when the spec firms up." It has: the Ossie core
(datasets / fields / relationships / metrics) ships a **published JSON Schema**
(`core-spec/osi-schema.json`, spec `0.2.0.dev0`) that validates a real TPC-DS
example — concrete enough to build a faithful reader/emitter today.

## Decision
1. **Schema-faithful to the real Ossie objects — invent nothing.** Top level is
   `version` + a `semantic_model` LIST. Identity is `datasets` + `primary_key` /
   `unique_keys` + `relationships` — there is **no `entity` object**. A
   relationship's *direction* IS its cardinality (`from` = many, `to` = one), so
   we emit **no `cardinality`, no `foreign_key`, no `aggregation` key** (FKs live
   in the relationship's `from_columns`/`to_columns`; aggregation lives inside the
   metric's SQL `expression`). Field/metric expressions are dialect-scoped
   (`expression.dialects[].{dialect,expression}`). `parse_osi_models(emit_osi_yaml(...))`
   round-trips.
2. **GoldenMatch metadata rides in `custom_extensions`.** Provenance (n_entities,
   reduction_ratio) and an optional key-integrity certificate go under
   `custom_extensions.goldenmatch` — the schema-sanctioned extension point — so
   emitted OSI stays valid against the incubating 0.x spec.
3. **Bidirectional bridges to A + B.** `certify_osi_relationships(model, frames)`
   certifies the ONE-side key of each relationship via wedge A — certifying
   *exactly* the identity the metrics depend on (metric-aware). `emit_osi_from_crosswalk`
   turns a wedge-B `ResolvedCrosswalk` into an OSI crosswalk dataset + conformed
   relationship. `osi_join_keys` surfaces "what to resolve."
4. **Library-only, advisory, parity-free** — same discipline as A + B; no
   MCP/CLI/A2A/TS surface, `semantic/` stays out of top-level `goldenmatch/__init__.py`
   so `import goldenmatch` stays polars-free.

## Conformance to the two-engines frame (0047)
- **One authoritative owner** ✅ — an interchange adapter over the control plane's
  resolved ids + wedge A/B; no new identity or metric semantics.
- **Conformance defines correctness** ✅ — this is the purest expression of the
  frame's "conformance" test: GM emits/reads the Ossie JSON-Schema objects, and we
  pinned to a spec version rather than guessing.
- **Arrow at bulk boundaries** ✅ — OSI docs are small metadata (YAML); the frames
  the bridge certifies are Arrow.

## Consequences / honest flags
- **Targets Ossie `0.2.0.dev0` (0.x / incubating).** Additive churn is expected
  (the Metric Language WG may later add structured aggregation fields). We validate
  round-trip internally against the documented objects; we do **not** run the
  official reference converters (dbt/Polaris) in CI. Pin + revisit on a spec bump.
- **`certify_osi_relationships` needs the data frames** (OSI references tables by
  `source` name only); the caller supplies `{dataset: frame}`. Wiring OSI `source`
  refs to a live warehouse is a follow-on.
- **Follow-ons (not v1):** Cube emitter, JSON-Schema validation against a vendored
  `osi-schema.json`, writing back into a live Ossie catalog, a CLI/MCP front door,
  and metric-aware *blocking* (resolve the entities that drive metrics first).
- **Numbering note:** a concurrent PR also used `0049` (`0049-customer-360-identity-store-spine.md`);
  this collision predates and is unrelated to the semantic-layer arc (0049 A / 0050 B / 0051 C).

---
**Classification:** decision/accepted • **Last updated:** 2026-07-30
