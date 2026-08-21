# 0055 — Ontology layer, part 3: richer emit + ontology discovery

**Status:** accepted (2026-08-13, Ben) • **Shipped:** `goldenmatch.semantic.ontology.{emit_ontology_shapes, emit_golden_triples, discover_ontology}` + `DiscoveredOntology`; `emit_sameas_graph` gains `target_class` • **Builds on:** [0053 (ontology v1)](0053-ontology-layer-rdf-owl-provider.md) + [0054 (consume/audit)](0054-ontology-layer-consume-audit.md) • **Planning:** [../planning/semantic-layer-interchange.md](../planning/semantic-layer-interchange.md)

## Context
0053 shipped the ontology-layer v1; 0054 deepened the **read / audit** side. This
closes the flesh-out with the **produce / generate** side — bringing the ontology
layer to parity with the semantic layer's generative half (`discover_semantic_model`,
where every key ships PRE-GRADED by the certifier).

## Decision
1. **Richer emit — typed individuals, not just links.**
   `emit_sameas_graph` gains an optional `target_class` that also types each
   canonical entity `rdf:type <base>class/<class>` (additive; default output
   unchanged). `emit_golden_triples(golden, class_name, id_column)` emits the
   resolved GOLDEN records as typed individuals carrying their conformed attribute
   values (`rdf:type` + `<base>prop/<col>` literals) — the resolved entities enter
   the graph as queryable individuals, not just `owl:sameAs` edges.
2. **Per-class SHACL from the ontology.** `emit_ontology_shapes(onto)` emits a
   `sh:NodeShape` per class with an (inheritance-aware) `owl:hasKey`, targeting the
   class by its real IRI, with a property shape per key property asserting the
   property is present (`sh:minCount 1`) — the ontology-derived counterpart to the
   single generic `emit_identity_shacl`. Key-property IRIs are reconstructed in the
   class's namespace (the common single-namespace shape), falling back to `gm:`.
3. **Ontology discovery — keys shipped falsified.** `discover_ontology(frames) ->
   DiscoveredOntology` proposes a draft OWL ontology, one `owl:Class` per input
   frame, whose `owl:hasKey` is chosen by **reusing the semantic layer's
   certifier-backed `discover_keys`** (never a second discovery path) and annotated
   with the certificate (`gm:keyTrustworthy` / `gm:keyUniquenessEstimate`). A frame
   with no clean key still yields a class flagged untrustworthy — the loud signal a
   reasoner keyed on that grain would over-merge. `turtle` round-trips through
   `parse_ontology`.

## Conformance to the two-engines frame (0047)
- **One authoritative owner** ✅ — discovery reuses `discover_keys` (→ the single
  key-integrity certifier); emit is an RDF projection of the control plane's
  resolved ids + golden records. No new identity semantics, no reasoner.
- **Conformance defines correctness** ✅ — emitted OWL round-trips through the
  reader; discovered keys carry the certifier's verdict, not a guess.
- **Arrow at bulk boundaries** ✅ — discovery/golden-emit consume Arrow frames;
  the RDF out is small metadata.

## Consequences / honest flags
- **Discovered keys are single-column** (`discover_keys` proposes single-column
  candidates in this slice); composite-key discovery is a follow-on. The emitted
  `owl:hasKey` is a valid 1-property key.
- **`emit_ontology_shapes` asserts presence, not global uniqueness.** SHACL cannot
  express "unique across the class extension" without SPARQL-based constraints;
  the shape asserts each key property is present (`minCount 1`), and the *uniqueness*
  verdict lives in `certify_ontology`. A SPARQL-constraint uniqueness shape is a
  possible follow-on.
- **Property-IRI reconstruction assumes a single class namespace** — faithful for
  typical ontologies; multi-namespace key properties fall back to `gm:` and should
  be reviewed. (Full IRIs are available when the key property is also declared
  functional/IFP; a future pass can thread those through.)
- **Ontology layer flesh-out is now complete** (0053 v1 + 0054 consume/audit +
  0055 produce/discover). Still deliberately deferred, as across the whole arc: a
  CLI/MCP front door (parity surface) and live-catalog write-back.

---
**Classification:** decision/accepted • **Last updated:** 2026-08-13
