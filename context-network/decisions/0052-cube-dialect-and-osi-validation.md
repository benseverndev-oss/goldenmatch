# 0052 — Follow-ons: Cube (cube.dev) dialect + OSI conformance validation

**Status:** accepted (2026-07-30, Ben) • **Shipped:** `goldenmatch.semantic.{parse_cube_models, cube_join_keys, certify_cube_joins, emit_cube_yaml, emit_cube_from_crosswalk}` + `Cube*` dataclasses, and `goldenmatch.semantic.validate_osi` • **Builds on:** [0049 (certify)](0049-metric-aware-key-certification.md) + [0050 (resolve+emit)](0050-resolved-crosswalk-emit.md) + [0051 (OSI)](0051-osi-ossie-native-provider.md) • **Planning:** [../planning/semantic-layer-interchange.md](../planning/semantic-layer-interchange.md)

## Context
Wedges A + B + C landed the semantic-layer arc against dbt/MetricFlow and
OSI/Apache Ossie. ADR 0051 named two follow-ons "not v1": a **Cube emitter** and
**JSON-Schema validation**. This ADR ships both — the two that stay within the
established discipline (library-only, advisory, parity-free) and need no new
gated surface.

- **Cube (cube.dev)** is the third major semantic layer, and it is the same shape
  as the other two: a cube's identity is a **dimension marked `primary_key: true`**
  and joins live in a `joins:` block whose `relationship` (`one_to_one` /
  `one_to_many` / `many_to_one`) encodes cardinality from the declaring cube's
  point of view — so the join key is, again, the identity the metrics silently
  assume. Adding it completes the MetricFlow ✓ / OSI ✓ / Cube ✓ reader+emitter
  symmetry: GoldenMatch reads any of the three and emits the conformed key back
  into it.
- **OSI validation** hardens wedge C. Emitting *valid* OSI is a claim; without a
  check it can only be asserted. The Ossie 0.x schema is small enough to validate
  structurally with no dependency, and the highest-value check is not generic
  JSON-Schema conformance but flagging the **non-Ossie keys hand-written docs tend
  to invent** — `cardinality`, `foreign_key`, `aggregation` — which are exactly
  the ones 0051 was careful never to emit.

## Decision
1. **Cube dialect — schema-faithful to the current snake_case YAML data model,
   invent nothing.** Top level is a `cubes:` list; a cube is `name` +
   `sql_table`/`sql` + `dimensions` + `measures` + `joins`. Primary key = the
   `primary_key: true` dimensions (composite = several). Joins are
   `{name, relationship, sql}` with member refs in the **YAML** single-brace form
   (`{CUBE}.fk = {other.pk}`, column outside the braces for self, inside for the
   other cube — the `${...}` template form is JS-models only). **Legacy
   relationship aliases** (`has_one`/`has_many`/`belongs_to` + camelCase) are read
   and normalized to the modern enum on emit. `parse_cube_models(emit_cube_yaml(...))`
   round-trips. `views:` are a consumption re-projection with no keys/joins of
   their own and are intentionally not modeled.
2. **Same bridges to A + B as OSI.** `cube_join_keys` surfaces "what to resolve"
   (best-effort parsing FK/PK columns out of the join `sql`); `certify_cube_joins`
   certifies the ONE-side key of each join via wedge A (metric-aware);
   `emit_cube_from_crosswalk` turns a wedge-B `ResolvedCrosswalk` into a crosswalk
   cube (keyed on the source PK) + a `many_to_one` join from the source cube — so
   metrics group by the conformed `resolved_entity_id`. GoldenMatch provenance
   rides in the crosswalk cube's `meta` (Cube's sanctioned free-form extension
   point).
3. **`validate_osi` is a dependency-free STRUCTURAL validator**, not a jsonschema
   dependency: it checks the Ossie `0.2.0.dev0` required-field sets, list shape,
   and enum membership (dialects + datatypes), and — the distinctive part — flags
   the keys the schema does NOT define (`cardinality`/`foreign_key`/`aggregation`)
   so both GoldenMatch-emitted and third-party docs stay portable across Ossie
   consumers. `validate_osi(emit_osi_yaml(...)) == []` is the round-trip contract.
4. **Library-only, advisory, parity-free** — same discipline as A/B/C; no
   MCP/CLI/A2A/TS surface (so `api_parity` is untouched), and `semantic/` stays
   out of top-level `goldenmatch/__init__.py` so `import goldenmatch` stays
   polars-free.

## Conformance to the two-engines frame (0047)
- **One authoritative owner** ✅ — a second interchange adapter (Cube) over the
  control plane's resolved ids + wedge A/B, plus a validator over C's own output;
  no new identity or metric semantics.
- **Conformance defines correctness** ✅ — `validate_osi` makes wedge C's "we emit
  valid OSI" checkable rather than asserted; the Cube reader/emitter is pinned to
  the documented snake_case data model and round-trip-tested.
- **Arrow at bulk boundaries** ✅ — dialect docs are small metadata (YAML); the
  frames `certify_cube_joins` certifies are Arrow.

## Consequences / honest flags
- **Cube join-column parsing is best-effort from the join `sql` string.** The
  relationship + target cube are structured; the FK/PK columns are recovered by
  parsing `{...}` member refs, which covers the standard equi-join form but not
  arbitrary SQL. `certify_cube_joins` simply skips a join whose target columns
  can't be parsed (never guesses).
- **`validate_osi` is structural, not full JSON-Schema.** It deliberately does
  NOT vendor `osi-schema.json` or pull a `jsonschema` dependency; it validates the
  required-field/enum constraints that keep GoldenMatch-emitted OSI round-trippable
  and portable. A vendored-schema validation mode remains a possible follow-on if
  a consumer needs byte-level schema conformance.
- **Targets Ossie `0.2.0.dev0` / Cube's current YAML model** — both incubating /
  evolving; pin + revisit on a spec bump, as with 0051.
- **Still-deferred follow-ons** (unchanged from 0051): a CLI/MCP front door (would
  add an `api_parity`-gated surface), metric-aware *blocking*, dbt crosswalk
  materialization, and writing back into a live catalog.

---
**Classification:** decision/accepted • **Last updated:** 2026-07-30
