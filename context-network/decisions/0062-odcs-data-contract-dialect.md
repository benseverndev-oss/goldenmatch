# 0062 — ODCS (Open Data Contract Standard) dialect for the semantic-layer certifier

**Status:** accepted (2026-08-13, Ben) • **Shipped:** `goldenmatch.semantic.odcs` (`parse_odcs_contract`, `odcs_identity_keys`, `certify_odcs_contract`, `emit_odcs_yaml`, `emit_odcs_from_crosswalk`) + an `"odcs"` dialect in `certify_semantic_model` • **Builds on:** [0049 (metric-aware key certification)](0049-metric-aware-key-certification.md), [0052 (Cube dialect)](0052-cube-dialect-and-osi-validation.md), [0060 (Feast provider)](0060-feast-feature-store-provider.md), [0061 (Malloy dialect)](0061-malloy-bi-dialect.md) • **Planning:** [../planning/semantic-layer-interchange.md](../planning/semantic-layer-interchange.md)

## Context
The remaining adjacent-standard surface from the semantic-layer roadmap filter is
the **data-contract** lane. A data contract is the most *literal* statement of the
semantic-layer thesis: it is a machine-readable promise about a dataset, and its
identity promise is declared right in the schema. In **ODCS** (Open Data Contract
Standard — the PayPal-origin spec now under the Bitol project / Linux Foundation AI
& Data), a v3 `schema` object's properties carry **`primaryKey: true`** (ordered by
**`primaryKeyPosition`** for a composite key) and **`unique: true`**. The contract
*asserts* "these columns identify a row" — and nothing checks that assertion against
the data. That is the same gap 0049 catches, except here it is the contract's
headline term: a duplicated primary key means the uniqueness promise is simply
false, and every downstream consumer that trusts the contract inherits the miscount.

ODCS `primaryKey`/`primaryKeyPosition` maps one-to-one onto MetricFlow
`entity (primary)`, Cube `primary_key`, and Malloy `primary_key` — so the
metric-aware certifier applies unchanged.

## Decision
A new dialect module `goldenmatch/semantic/odcs.py`, structured exactly like Cube
(0052), Feast (0060), and Malloy (0061) — consume, certify (bridge A), emit
(bridge B):

1. **Consume.** `parse_odcs_contract(source)` reads a declarative ODCS doc (dict /
   YAML / JSON / path) into an `ODCSContract` of `ODCSSchemaObject`s. It reads the
   **v3** shape (`schema:` / `properties:`) canonically and is tolerant of the
   **legacy v2** spelling on read (`dataset:` / `columns:`, `isPrimary` / `isUnique`)
   so an existing contract certifies without a rewrite. `odcs_identity_keys` yields
   each object's declared identity keys: the composite primary key (properties with
   `primaryKey: true`, ordered by `primaryKeyPosition`) **plus each standalone
   `unique` property** — a data contract commonly promises both, and each is worth
   certifying. No `open-data-contract-standard` dependency; it reads the plain doc.
2. **Certify (bridge A).** `certify_odcs_contract(contract, frames, resolve=…)`
   certifies **every declared key** of each schema object via `certify_key_integrity`
   — one certificate for the composite primary key, one per standalone `unique`
   property — with the object's **numeric properties as the fan-out measures** a
   duplicated key would inflate under aggregation (mirrors Feast treating features as
   measures; the numeric/descriptive split comes from ODCS `logicalType`). A frame is
   matched by object `name` or `physicalName`.
3. **Emit (bridge B).** `emit_odcs_from_crosswalk` emits a v3 `DataContract` whose
   schema object declares the GoldenMatch-resolved key as `primaryKey: true` +
   `unique: true`, with provenance (+ optional certificate verdict) in the contract's
   `customProperties` `goldenmatch` entry — ODCS *has* a metadata slot, so provenance
   is embedded inline (like the Cube/Feast YAML dialects, unlike Malloy's tuple).
   `parse_odcs_contract(emit_odcs_yaml(...))` round-trips.
4. **Front-door wiring, no new surface.** `certify_semantic_model` auto-detects
   `kind: DataContract` (v3, unambiguous) — or an `apiVersion` + `schema`/`dataset`
   list for the kind-less v2 — as the `"odcs"` dialect and dispatches to
   `certify_odcs_contract`; `semantic_field_roles` learns the ODCS role split
   (primaryKey = identity, numeric = measures, descriptive = dimensions). CLI / MCP /
   REST certify a data contract with **no new MCP tool / CLI command / A2A skill** —
   parity-free, like Cube / Feast / Malloy.

## Conformance to the two-engines frame (0047)
- **One authoritative owner** ✅ — an *adapter surface* over the single-sourced
  `certify_key_integrity` (0059). GoldenMatch does not author the contract, its
  quality rules, or its SLAs — the contract owner keeps that. No second source of
  truth; `quality` blocks are passed through opaquely, never rewritten.
- **Conformance defines correctness** ✅ — ODCS is another dialect target;
  `parse_odcs_contract(emit_odcs_yaml(...))` round-trips, and the v2 reader is a
  semantically-equivalent ingest of the older spelling.
- **Compute vs. control stay distinct** ✅ — the contract is small metadata
  (YAML/JSON), correctly not Arrow-marshaled; certification rides the columnar key
  path.

## Consequences / honest flags
- **One entry per declared key, not one per object.** Unlike the join dialects
  (one entry per join), ODCS emits one certificate for the composite PK and one for
  each `unique` property, since the contract makes multiple distinct identity
  promises. The front-door `context` disambiguates (`primary key` vs
  `unique: <col>`).
- **Reads the declaration only.** Like every dialect, this reads/writes the contract
  document; it does not evaluate the contract's `quality` rules, honor its SLAs, or
  query a warehouse. `frames` are caller-supplied. Certifying the identity promise is
  precisely the term no ODCS tool verifies against data.
- **Numeric = measure is a heuristic.** A data contract does not label "measures"; we
  treat numeric `logicalType` non-key properties as the fan-out targets. This is
  advisory framing for the fan-out report, not a claim about business semantics — and
  it never affects the uniqueness verdict, which is the load-bearing result.
- **No new parity surface** — verified: `odcs` appears in zero gated Python surfaces,
  so `parity/goldenmatch.yaml` is unchanged, consistent with Cube / Feast / Malloy.
- **LookML / Power BI remain the open BI dialects** — the last follow-ons from the
  same filter; the data-contract and BI-Malloy lanes are now landed.

---
**Classification:** decision/accepted • **Last updated:** 2026-08-13
