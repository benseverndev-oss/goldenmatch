# Semantic Layering (MetricFlow / Cube / OSI / Ontology) — brainstorm / planning

> **Status:** wedges **A + B + C** SHIPPED + **follow-ons** (Cube dialect, OSI
> conformance validation) + the **ontology layer** (RDF/OWL/SHACL) — decisions
> [0049](../decisions/0049-metric-aware-key-certification.md) (certify) +
> [0050](../decisions/0050-resolved-crosswalk-emit.md) (resolve once + emit) +
> [0051](../decisions/0051-osi-ossie-native-provider.md) (OSI/Ossie provider) +
> [0052](../decisions/0052-cube-dialect-and-osi-validation.md) (Cube dialect +
> OSI validation) +
> [0053](../decisions/0053-ontology-layer-rdf-owl-provider.md) (ontology layer:
> RDF/OWL/SHACL native identity provider) +
> [0054](../decisions/0054-ontology-layer-consume-audit.md) (deeper consume +
> certification report + reconciliation) +
> [0055](../decisions/0055-ontology-layer-produce-discover.md) (richer emit +
> ontology discovery).
>
> **Ontology layer (0053 + 0054)** — the semantic-layer thesis one level up
> (TBox/ABox): an ontology *asserts* identity (`owl:hasKey`,
> `owl:InverseFunctionalProperty`, `owl:sameAs`) but resolves it only by brittle
> exact-match. `parse_ontology` + `ontology_identity_keys` (consume the declared
> identifying keys, with `owl:hasKey` inheritance down `rdfs:subClassOf`),
> `certify_ontology_keys` / `certify_ontology` (bridge to A — certify exactly those
> keys, whole-ontology roll-up), `reconcile_ontology_identity` (diff asserted
> `owl:sameAs` vs resolved identity — over-merge / fragmentation),
> `emit_sameas_graph` (bridge to B — `owl:sameAs` + PROV-O, optional `rdf:type`),
> `emit_golden_triples` (typed individuals + conformed values), `emit_identity_shacl`
> / `emit_ontology_shapes` (conformance shapes), and `discover_ontology` (draft OWL
> from data, `owl:hasKey` pre-graded by the certifier — the generative half).
> `rdflib` optional (`goldenmatch[ontology]`); GoldenMatch is the identity provider
> FOR the reasoner/triple store, never a reimplementation of one. **The ontology
> arc is complete** (0053 v1 + 0054 consume/audit + 0055 produce/discover) and now
> has a **CLI + MCP front door** ([0056](../decisions/0056-ontology-cli-mcp-front-door.md):
> `goldenmatch ontology certify|discover`, `ontology_certify`/`ontology_discover`
> MCP tools) and **live-catalog write-back** ([0057](../decisions/0057-ontology-live-catalog-writeback.md):
> `write_ontology_catalog` / `write_resolved_identity_graph` → file or a live
> SPARQL 1.1 Graph Store endpoint; `ontology discover --endpoint`). **The ontology
> arc is fully complete — no deferred items remain.**
> Captures the framing for how GoldenMatch relates to the semantic-layer /
> metrics-layer ecosystem (dbt Semantic Layer + MetricFlow, Cube, and the Open
> Semantic Interchange spec), and a crawl→walk→run plan. Problem **A**
> (metric-aware key certification): `goldenmatch.semantic.certify_key_integrity`
> + a MetricFlow YAML reader + a `key.integrity` goldenanalysis analyzer + a
> `goldenmatch_key_integrity` dbt test. Problem **B** (emit the resolved crosswalk):
> `build_resolved_crosswalk` (durable control-plane entity ids) +
> `emit_semantic_model` / `emit_metricflow_yaml` (declare the conformed key as the
> primary entity; round-trips through the A reader). Problem **C** (OSI/Apache Ossie
> native provider): `parse_osi_models` + `osi_join_keys` (consume — learn what to
> resolve), `certify_osi_relationships` (bridge to A — certify the keys metrics
> join on), `emit_osi_from_crosswalk` (bridge to B — emit the conformed join), all
> schema-faithful to Ossie `0.2.0.dev0`. **Follow-ons** (0052): a **Cube (cube.dev)
> dialect** — `parse_cube_models` + `cube_join_keys` (consume), `certify_cube_joins`
> (bridge to A), `emit_cube_from_crosswalk` (bridge to B), completing the
> MetricFlow ✓ / OSI ✓ / Cube ✓ reader+emitter symmetry — and **OSI conformance
> validation** (`validate_osi`): a dependency-free structural validator over the
> Ossie `0.2.0.dev0` required-field + enum constraints that also flags the
> non-Ossie keys (`cardinality`/`foreign_key`/`aggregation`) hand-written docs
> tend to invent. The arc is complete — the whole crawl→walk→run is landed.
>
> **Cross-language single-sourcing (ratified,
> [0059](../decisions/0059-semantic-key-integrity-single-sourced-kernel.md)).** The
> one clean columnar primitive under all of this — the **structural** key-integrity
> certifier (uniqueness-at-grain + fan-out) — is authored ONCE in the Rust crate
> `key-integrity-core` and bound by every surface (TS via `key-integrity-wasm`,
> Python via the `certify_structural_json` native shim, SQL via pgrx + DuckDB), with
> one Python-generated golden asserted on all three. The join-cardinality wrappers
> (`certify_serving_joins`/`certify_cube_joins`/`certify_osi_relationships`) delegate
> to it on both Python and TS — no second source of truth. The shared kernel is
> **opt-in by design** (pyarrow's `group_by` IS the Arrow-at-bulk boundary and is
> measurably faster; the kernel exists for the single-owner guarantee, not speed).
> Recorded on the thesis-conformance board as
> `semantic-key-integrity-single-sourced-kernel` (`default_routed: opt-in`). The rest
> of the semantic layer (discovery, resolution tier, namer, warehouse introspection)
> is orchestration/stateful and correctly stays Python-authoritative.
>
> **Feature-store surface (Feast,
> [0060](../decisions/0060-feast-feature-store-provider.md)).** The wedge one layer
> over into ML: a Feast `FeatureView` is keyed on an `Entity`'s `join_keys`, and a
> duplicated join key fans out every aggregated feature (a `sum`/`count`
> double-counts), a fragmented entity splits its own feature history
> (training-serving skew), non-conformed keys can't join. Feast's
> `Entity(join_keys=[...])` maps one-to-one onto MetricFlow `entity (primary)` /
> Cube `primary_key`, so the same certifier applies. `goldenmatch.semantic.feast`:
> `parse_feast_models` / `parse_feast_objects` (declarative doc or duck-typed Feast
> SDK objects — no `feast` dependency), `feast_join_keys`,
> `certify_feast_feature_views` (bridge A — features are the fan-out measures),
> `emit_feast_from_crosswalk` (bridge B). `certify_semantic_model` auto-detects a
> top-level `feature_views:` as the `"feast"` dialect, so the CLI/MCP/REST front
> doors certify a feature repo with no new surface (parity-free, like Cube). The
> next missing provider surfaces from the same filter: **BI semantic-model dialects
> (Malloy/LookML)** and **data contracts (ODCS)**; the CDP/activation lane is
> competitive prior art, not a plug-under gap.
> Greenfield when written: no prior repo code, doc, or decision referenced this
> ecosystem (grep, 2026-07-30).

## The premise

**A semantic layer is a join graph, and the join graph runs on entity keys.**
Every modern semantic layer — OSI, dbt/MetricFlow, Cube — is built on the same
three tiers: **entities** (business objects with keys) → **dimensions & measures**
defined *relative to* those entities → **metrics** composed from measures and
sliced by dimensions. Joins between models happen on **entity key equality**
(`orders.customer_id = customers.id`). The set of entity keys *is* the semantic
model's edge set.

So the correctness of every metric is **downstream of the correctness of the
entity keys the joins run on** — and **none of these tools resolve those keys.**
They all treat conformed, unique, cross-source keys as an *assumed input
precondition*. That is the gap, and it is exactly the thing GoldenMatch produces.

The North Star ("the tool any developer reaches for *by default* for entity
resolution — to the point where reaching for anything else is the harder
choice") has no "…except when the identity feeds a metric" clause. The point at
which every metric silently depends on resolved identity is the deepest possible
*default* position: you don't reach for GM, your numbers are just correct because
it ran under the layer.

## Why this bites (the failure mode all three layers share)

A metric can be perfectly **defined** — governed, certified, OSI-exchanged,
AI-queryable — and still be numerically **wrong**, because the entity keys
underneath were never resolved. Three break modes, all usually **silent**:

| Break | What the layer does | Metric symptom |
|---|---|---|
| **Dirty keys** (`Bob Smith`/`Robert Smith`, `123 Main St`/`…Street`, no shared surrogate) | The equi-join simply doesn't match; the same entity fragments into multiple rows | Customer counts inflate; per-customer averages deflate; **undercount** |
| **Duplicated keys** (one entity, many PK values) | **Fan-out.** Cube's mechanical dedup removes duplicate *key values*, not duplicate *entities*; MetricFlow `count_distinct` over a not-truly-unique entity | `count_distinct(customer)` over-counts; `sum(revenue)` double-counts across a many-to-one join |
| **Non-conformed keys across sources** (Salesforce `customer_id` ≠ warehouse `customer_id`, no crosswalk) | The join is **impossible to declare** — MetricFlow can't draw a graph edge; Cube's join `sql` has nothing valid to equate; OSI can name the `Relationship` but the keys don't align | Cross-source metrics can't be built at all |

For **AI agents** this is worse, not better: the agent trusts the certified
definition and confidently returns a wrong number. OSI's own pitch is "data
readiness for AI" — but a semantic contract over unresolved keys is confidently
wrong at machine speed. Resolved identity is the missing half of "readiness."

## The vocabulary map (ER concept ↔ semantic-layer concept)

The whole opportunity is that GM already produces the semantic layer's
foundational artifacts *under different names*:

| GoldenMatch concept | OSI | dbt / MetricFlow | Cube |
|---|---|---|---|
| Source record | Dataset row | Row in a semantic model's dbt model | Row in a cube's table |
| **Stable entity ID** (control plane) | Dataset primary key | **`entity` (`primary`)** | **`primary_key`** |
| Source-key crosswalk (`source`, `source_pk` → `entity_id`) | **`Relationship`** (join keys + cardinality) | shared **`entity`** = graph edge (implicit join) | **`joins` block** (`sql` + relationship type) |
| Match/merge cluster | — (resolved key space) | conformed entity value space | de-duplicated `primary_key` space |
| Golden / canonical record | certified Dataset row | the primary entity a measure is defined against | the row a measure aggregates |
| Field semantics (goldencheck-types domain packs) | Dimension type / valid values | Dimension | Dimension (`type`) |
| Provenance / audit seal (control plane) | certification / ownership context | metric/entity metadata | governance (View) |

Note the asymmetry: GM owns **identity and keys**; the semantic layer owns
**measures, metrics, and grain**. GM must *not* try to own metric semantics —
there is already an authoritative owner for that (MetricFlow / Cube). This is the
same boundary discipline as goldencheck↔goldenmatch (DQ feeds ER, doesn't become
ER; decision 0007).

## The architectural key (and why the gap is small)

**GoldenMatch already emits exactly the artifact a semantic layer assumes it was
handed: a stable entity ID + a cross-source key crosswalk + provenance.** The
control plane's `source_records` (indexed on `entity_id`/`source`/`record_hash`)
*is* the MetricFlow `entity` / Cube `primary_key` / OSI `Relationship` join key,
under a different name. The instant identity is resolved, the semantic layer's
"entity/primary_key/Relationship" is a **projection of the control-plane store**.

So the v1 surface is **not** "build a semantic layer." It is a **metadata
adapter** — the same posture as the `goldenmatch-kg` drop-in shims and the
existing `dbt-goldensuite` macro package. The load-bearing pieces already ship:

| Need | Existing component | Ready? |
|---|---|---|
| Resolved stable ID + cross-source crosswalk | Identity Control Plane (`source_records`, `stabilize.py`, `ResolutionBatch v1`) | **The artifact already exists** — needs a projection/emit, not new resolution |
| Warehouse-native, declarative binding | `packages/dbt/goldensuite/` (dedupe, two-table match, `identity_*` macros, quality gates) across Postgres/DuckDB/Snowflake | **Same substrate MetricFlow/dbt-SL live on** |
| "Is this declared key actually a key?" | goldencheck (rule discovery, functional dependencies, composite-key/uniqueness kernels) | **Key-integrity certification already computed** |
| Canonical field/domain vocabulary | `goldencheck-types` (16 domain packs, cross-language, versioned) | Maps onto dimension semantics |
| Spec + conformance discipline | the two-engines frame's conformance levels (exact / numeric / semantic / divergent) | **OSI is just another conformance target** — GM already thinks this way |

## Three problems hiding in "semantic layering" (proposed priority)

Mirrors the multimodal-ER framing: pick the wedge that reuses the most machinery,
carries the least black-box exposure, and ships trust on day one.

| # | Problem | Shape | Priority |
|---|---|---|---|
| **A** | **Metric-aware key certification** — "does the `entity`/`primary_key` you declared for this measure *actually* uniquely identify a real-world entity, or will it fan out your `sum(revenue)`?" GM certifies the layer's *existing* keys; it does not replace them. | A new dbt test / OSI validator over goldencheck + goldenmatch; **advisory, never mutates a number** | **THE WEDGE (v1)** |
| **B** | **Emit the resolved crosswalk as semantic-layer entities** — GM resolves identity across sources and materializes (1) a conformed key column/table the layer joins on, and (2) the entity/relationship *declarations* (MetricFlow `entity`, Cube `primary_key`+`joins`, OSI `Datasets`+`Relationships`). "Resolve once, every metric inherits correct joins." | A projection/codegen surface over the control-plane store + the dbt package | **Natural second step** |
| **C** | **OSI-native identity provider (bidirectional)** — GM consumes an OSI semantic model to know *which* keys/relationships feed measures (metric-aware blocking: resolve the entities that actually drive metrics first), and writes resolved `Relationships` + certification/provenance back into the OSI/Ossie catalog. | New: OSI spec ingest + catalog write-back; loop closes | **Moonshot** |

**Why A first.** It reuses the most existing machinery (goldencheck's key/FD
kernels + goldenmatch's "would these rows collapse under resolution?"), it is
**purely additive and advisory** — it reports "this PK fans out `sum(revenue)` by
3.2%," it never silently rewrites the metric — so it satisfies *Advanced, never
black-box* by construction, and it delivers value on the very first run against a
semantic model someone already has. B is A plus a write/codegen path. C needs the
OSI/Ossie spec to firm up.

## Where the surfaces attach

- **A** ships as a `dbt` generic test in `dbt-goldensuite` (sibling to the
  existing `goldenmatch_match_quality` test) — "certify that the entity declared
  for this semantic model / cube is unique at its grain, and quantify the
  fan-out/undercount if not." Zero-config-able on a single model.
- **B** ships as a projection over the control-plane store to two targets:
  (i) a materialized conformed-key table/column (already dbt-expressible), and
  (ii) a codegen emit of MetricFlow `semantic_models[].entities` YAML / Cube
  `joins` + `primary_key` / OSI `Datasets`+`Relationships` YAML.
- **C** is the OSI conformance play: GM as a reference converter/provider in the
  Apache Ossie ecosystem, read + write.

MCP/A2A surfaces come along for free once A/B are library-callable (commitment 3:
shared capabilities conform across surfaces).

## Fit against the decision tests

Tested against the two-engines frame (0047) and the North Star commitments:

- **One authoritative semantic owner per capability.** The binding is an
  *adapter surface*, not a new source of truth. Entity IDs + crosswalk come from
  the **control plane**; key-integrity from **goldencheck**; resolution from the
  **compute engine**. GM does **not** author measures/metrics — MetricFlow/Cube
  remain their owner. ✅ (guard the boundary: this must not drift into "GM defines
  metrics.")
- **Compute vs. control stay distinct.** The crosswalk + stable IDs are
  control-plane reads; resolution is stateless compute; the semantic-layer
  artifacts (YAML) are control/metadata, not Arrow batches. ✅
- **Arrow at bulk boundaries, not the universal calling convention.** Semantic
  artifacts are small metadata (YAML/JSON), correctly *not* Arrow-marshalled. ✅
- **Conformance defines correctness.** OSI is literally an interchange spec;
  "GM emits/validates OSI" is a conformance target with the four levels already
  in the frame. This is the most natural possible fit. ✅
- **Zero-config should embarrass the alternatives.** A user with an existing
  semantic model gets a fan-out/undercount certificate on the first run, no
  tuning — the semantic layer offered them *nothing* here. ✅
- **Advanced, never black-box.** A is advisory and explainable; it quantifies and
  cites, never silently mutates a metric. ✅

## Tensions / risks (call them out now)

- **Scope creep into "GM is a semantic layer."** The strongest failure mode. GM
  owns identity/keys; it must never start defining measures, metric grain, or
  business KPIs. Hold this the way the DQ↔ER boundary is held. The test: *am I
  adding a second source of truth for something MetricFlow/Cube already owns?*
- **OSI is young and moving.** Spec first shipped Sept 2025; donated to Apache
  (incubating as "Ossie") June 2026; three working groups (Metric Language,
  Catalog, Ontology) still forming. Betting the wedge on OSI is betting on a
  moving target. **Mitigation:** anchor v1 (A) on the *stable* surfaces we
  already have (the dbt package; MetricFlow `entity` YAML is stable), and treat
  OSI (C) as the conformance target that's still crystallizing — attach when it
  firms up, don't lead with it.
- **Prior art in the activation lane.** Senzing (ERKG), Zingg/Splink, RudderStack
  Profiles/Hightouch, Reltio all resolve identity — but for customer-360 /
  activation / MDM, and **none are metric-aware**: they don't know a duplicated
  PK fans out a `sum`, or that a non-conformed `customer_id` breaks a MetricFlow
  graph edge. The open white space is specifically *metric-aware* key resolution
  expressed in the semantic layer's own vocabulary. That framing — not "another
  ER engine" — is the wedge.

## Open questions (for the ADR)

1. Is the v1 wedge **A** (certify existing keys) alone, or A+B thin slice
   (certify + emit a conformed key)? A is safer and more clearly additive.
2. First-class target for B's codegen: MetricFlow `entity` YAML (cleanest,
   stablest data model) vs. Cube (most explicit about *why* key quality matters,
   best demo) vs. OSI (highest leverage, least mature)?
3. Does key-integrity certification live as a new goldencheck relation, a new
   goldenanalysis "recall/key certificate," or a thin composition of both?
   (goldenanalysis already emits "recall certificates" — a "key-integrity
   certificate" is a sibling.)
4. What is the conformance level GM targets for OSI emit — semantically-
   equivalent (round-trips through Ossie's own converters) or exact?

## Sources

Landscape research (2026-07-30): OSI launch + founding coalition (Snowflake
lead; Salesforce/Tableau, dbt Labs, Cube, Sigma, ThoughtSpot, et al.), Apache
Ossie incubation; dbt/MetricFlow semantic-model data model (entities
`primary`/`foreign`/`unique`/`natural`, shared-entity graph joins); Cube data
model (`primary_key` mandatory, fan-out dedup on key value not entity);
activation-lane ER prior art (Senzing/ERKG, Zingg, Splink, RudderStack, Reltio).
Grounding files: `context-network/foundation/project-definition.md`,
`context-network/architecture/one-product-two-engines.md`,
`context-network/decisions/0047-one-product-two-engines-architecture.md`,
`context-network/architecture/identity-control-plane-manifesto.md`,
`packages/dbt/goldensuite/README.md`,
`packages/python/goldencheck-types/CLAUDE.md`.
