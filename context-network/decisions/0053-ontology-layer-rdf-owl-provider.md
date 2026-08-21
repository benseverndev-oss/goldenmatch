# 0053 — Ontology layer: RDF / OWL / SHACL native identity provider (bidirectional)

**Status:** accepted (2026-08-13, Ben) • **Shipped:** `goldenmatch.semantic.ontology.{parse_ontology, ontology_identity_keys, certify_ontology_keys, emit_sameas_graph, emit_identity_shacl}` + `Ontology`/`OntologyClass`/`OntologyProperty` • **Builds on:** [0049 (certify)](0049-metric-aware-key-certification.md) + [0050 (resolve+emit)](0050-resolved-crosswalk-emit.md) + [0051 (OSI)](0051-osi-ossie-native-provider.md) • **Planning:** [../planning/semantic-layer-interchange.md](../planning/semantic-layer-interchange.md)

## Context
The semantic-layer arc (A/B/C + Cube) targets **metrics over join keys**. The
ontology layer is the same shape one level up: **classes, properties and
constraints over individuals** (RDF/OWL/SHACL — the W3C stack, distinct from the
MetricFlow/Cube/OSI packages). It splits onto the classic **TBox / ABox** divide:
the ontology owns the TBox (what a `Patient` *is*); GoldenMatch owns the ABox
identity (which records *are* the same `Patient`).

The gap is the same "assumed input," and OWL even names the identity vocabulary —
`owl:hasKey` (a class's identifying property set), `owl:InverseFunctionalProperty`
(a value that uniquely identifies an individual, i.e. a single-property key),
`owl:sameAs` (individual equality) — but its only built-in resolution is brittle
exact-match, which **over-merges on dirty keys and never merges fragmented ones**.
That is exactly what GoldenMatch resolves. For agentic AI / GraphRAG (the "knowledge
layer for enterprise AI" framing) this is the identity substrate the reasoning
stands on.

## Decision
1. **Bidirectional, mirroring the OSI wedge — schema-faithful, invent nothing.**
   - **consume:** `parse_ontology` reads the identity-bearing axioms (`owl:Class`
     + `owl:hasKey`, `owl:FunctionalProperty` / `owl:InverseFunctionalProperty`
     with `rdfs:domain`); `ontology_identity_keys` surfaces the declared
     identifying keys as "what to resolve."
   - **certify (bridge to A):** `certify_ontology_keys` certifies each declared
     key against instance frames via the existing key-integrity certifier — the
     *purest* form of the wedge, because the ontology hands you the key
     declaration explicitly (`owl:hasKey`/IFP), unlike MetricFlow where it is
     inferred from entities.
   - **emit (bridge to B):** `emit_sameas_graph` turns a wedge-B `ResolvedCrosswalk`
     into RDF — `owl:sameAs` linking each source individual to its resolved
     canonical individual, with W3C **PROV-O** provenance (`prov:wasDerivedFrom`,
     `prov:wasGeneratedBy` a resolution activity carrying run metadata).
   - **conform (O-C flavor):** `emit_identity_shacl` emits a SHACL shape for the
     post-resolution invariant (each individual carries exactly one resolved id).
2. **GoldenMatch metadata rides under a documented `gm:` vocabulary** (`GM_NS`)
   with `rdfs:label`/`rdfs:comment`, honoring the ontology "explicit
   documentation" + "standard vocabularies" principles (reuse `owl:`/`prov:`/`sh:`,
   namespace only GM-specific facts).
3. **`rdflib` is an OPTIONAL dependency** (`goldenmatch[ontology]`), **lazy-imported
   inside each function**, so `from goldenmatch.semantic import …` never requires
   it and `import goldenmatch` stays lightweight. Tests `importorskip("rdflib")`
   (the ray/lance/torch optional-dep convention).
4. **Library-only, advisory, parity-free** — same discipline as A/B/C; no
   MCP/CLI/A2A/TS surface (so `api_parity` is untouched), `semantic/` stays out of
   top-level `goldenmatch/__init__.py`.

## Conformance to the two-engines frame (0047)
- **One authoritative owner** ✅ — an RDF/OWL adapter over the control plane's
  resolved ids + the key-integrity certifier; no new identity or metric semantics.
  It does **not** reimplement an OWL reasoner or a triple store — those are
  replaceable backends (Jena / RDFox / GraphDB / Protégé), none synonymous with
  GoldenMatch. GoldenMatch is the identity **provider** for them.
- **Conformance defines correctness** ✅ — reads/writes the W3C objects
  (`owl:hasKey`/IFP in, `owl:sameAs` + PROV-O + SHACL out); pinned to those
  standards rather than inventing a shape.
- **Arrow at bulk boundaries** ✅ — ontology docs are small metadata; the frames
  `certify_ontology_keys` certifies are Arrow.

## Consequences / honest flags
- **`rdflib`-gated tests run in the required `python_goldenmatch` lane
  (2026-08-13 follow-on).** The tests `importorskip("rdflib")`, so absent the extra
  they SKIP (a false green). Rather than leave that, the required `python_goldenmatch`
  job now installs `goldenmatch[ontology]` (one step, mirroring how it already
  installs the `datafusion` / `documents` extras "so tests RUN rather than
  importorskip-SKIP"), so `tests/test_ontology.py` executes for real inside the
  merge gate. The default `uv sync` still omits optional extras (a plain
  `pip install goldenmatch` stays rdflib-free); coverage comes from the extra
  installed in that one lane, not a separate job.
- **Reasoning is out of scope by design.** `owl:sameAs` transitive closure, OWL DL
  inference and SHACL *execution* belong to a reasoner/triple store; GoldenMatch
  emits the shapes and triples, it does not evaluate them.
- **Best-effort axiom coverage.** v1 reads `owl:hasKey` + functional/inverse-
  functional characteristics with `rdfs:domain`; richer identity signals
  (property chains, `owl:sameAs` already asserted in-graph, class hierarchies for
  key inheritance) are follow-ons.
- **IFP without a domain** is surfaced with `class=None` (it identifies individuals
  globally) and skipped by `certify_ontology_keys` (no frame to bind) — honest,
  not guessed.
- **Still-deferred** (unchanged from 0051/0052): a CLI/MCP front door, live-catalog
  write-back, and metric-aware *blocking*.

---
**Classification:** decision/accepted • **Last updated:** 2026-08-13
