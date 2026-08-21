# 0054 — Ontology layer, part 2: deeper consume + certification report + reconciliation

**Status:** accepted (2026-08-13, Ben) • **Shipped:** `goldenmatch.semantic.ontology.{effective_has_keys, certify_ontology, asserted_sameas_pairs, reconcile_ontology_identity}` + `OntologyCertification`/`OntologyReconciliation`; `OntologyClass` gains `parents` + `max_one_properties` • **Builds on:** [0053 (ontology v1)](0053-ontology-layer-rdf-owl-provider.md) • **Planning:** [../planning/semantic-layer-interchange.md](../planning/semantic-layer-interchange.md)

## Context
ADR 0053 shipped the ontology layer as a v1 bidirectional adapter (consume
declared keys + certify + emit `owl:sameAs`/PROV-O/SHACL). It was honest about
being a floor. This is the first of two flesh-out ADRs, deepening the **read /
audit** side to the depth the semantic layer reached (`certify_semantic_model`,
`reconcile_model`). ADR 0055 will do the **produce / generate** side.

## Decision
1. **Deeper parse — inherit keys, read cardinality restrictions.**
   `parse_ontology` now records each class's named `rdfs:subClassOf` `parents`
   and, from blank-node `owl:Restriction`s with `owl:onProperty` + a cardinality
   of 1, its `max_one_properties` (single-valued signals). `effective_has_keys`
   resolves a class's `owl:hasKey` axioms **including those inherited from
   ancestors** (a subclass IS identified by its superclass's key), cycle-safe;
   `ontology_identity_keys` surfaces inherited keys tagged `owl:hasKey(inherited)`.
2. **Whole-ontology certification report.** `certify_ontology(onto, frames) ->
   OntologyCertification` certifies every declared identity key and rolls the
   per-key certificates into one verdict (`all_safe`, `n_unsafe`, per-key
   `estimate`/`max_fan_out`/`is_unique`), with `to_dict()` — the ontology-layer
   analogue of `certification_report_dict`. It REUSES `certify_ontology_keys`
   (→ the wedge-A certifier); it does not fork a second validator.
3. **Reconciliation — asserted vs resolved identity.** `asserted_sameas_pairs`
   reads the ontology's ABox `owl:sameAs` links; `reconcile_ontology_identity(
   source, crosswalk)` diffs them against a wedge-B `ResolvedCrosswalk` and
   returns an `OntologyReconciliation` flagging **over-merges** (asserted same,
   resolved different — the exact-match IFP/`sameAs` over-merged) and
   **fragmentations** (resolved same, never asserted — identity the ontology
   missed), plus `agreements`. `iri_for` maps a crosswalk record to its individual
   IRI, defaulting to `emit_sameas_graph`'s scheme so the two compose.

## Conformance to the two-engines frame (0047)
- **One authoritative owner** ✅ — certification reuses the single key-integrity
  certifier; reconciliation is a partition diff over the control plane's resolved
  ids vs the ontology's asserted `sameAs`. No new identity semantics, no reasoner.
- **Conformance defines correctness** ✅ — reads the real OWL axioms
  (`hasKey`/`subClassOf`/restrictions/`sameAs`); certification IS the falsification
  test the whole arc is built on.
- **Arrow at bulk boundaries** ✅ — ontology docs are small metadata; the frames
  certified are Arrow.

## Consequences / honest flags
- **Reconciliation is a structural partition diff, not CCMS.** It reports the
  actionable IRI-pairs (over-merge / fragmentation bridges) rather than a TWI
  number; `core/compare_clusters.py` remains available for a scalar summary.
- **Fragmentation is reported at component granularity** — one bridge pair per
  extra asserted-component a resolved entity spans (bounded by component count),
  not every within-entity pair. `max_examples` caps both lists with a `note`.
- **Cardinality restrictions are parsed but advisory** — `max_one_properties`
  records a single-valued signal (`owl:maxCardinality`/`cardinality` = 1); it is
  not yet folded into the certification verdict (a functional-property fan-out
  check is a candidate follow-on). Qualified-cardinality variants are read.
- **Still-deferred to 0055:** richer emit (`rdf:type` to classes, per-class SHACL
  from a parsed ontology) and ontology discovery (draft OWL from data + crosswalk,
  keys pre-graded). CLI/MCP front door remains deferred (parity surface).
- **`rdflib`-gated** — tests run in the required `python_goldenmatch` lane via the
  `[ontology]` extra (per 0053's follow-on).

---
**Classification:** decision/accepted • **Last updated:** 2026-08-13
