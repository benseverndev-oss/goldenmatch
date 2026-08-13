"""Ontology-layer (RDF / OWL / SHACL) native identity provider.

The ontology layer is the semantic layer one level up: instead of metrics over
join keys it is classes, properties and constraints over **individuals** — and,
exactly like the semantic layer, it *asserts* identity without resolving it. OWL
even names the identity vocabulary — `owl:hasKey`, `owl:InverseFunctionalProperty`
(a value that uniquely identifies an individual), `owl:sameAs` (individual
equality) — but its only built-in resolution is brittle exact-match, which
over-merges on dirty keys and never merges fragmented ones. That gap is exactly
what GoldenMatch fills: the ontology owns the TBox (what a `Patient` *is*);
GoldenMatch owns the ABox identity (which records *are* the same `Patient`).

Bidirectional, mirroring the OSI wedge:

- **consume** an OWL/RDF ontology to learn the declared identifying keys
  (`parse_ontology`, `ontology_identity_keys`) and certify exactly those keys
  against the instance data (`certify_ontology_keys`, bridging wedge A) — the
  purest form of the wedge, because the ontology hands you the key declaration
  explicitly (`owl:hasKey` / IFP);
- **emit** the resolved identity as RDF (`emit_sameas_graph`, bridging wedge B):
  `owl:sameAs` linking each source individual to its resolved canonical
  individual, with W3C **PROV-O** provenance — point a triple store / reasoner at
  resolved individuals and every SPARQL query inherits correct identity;
- **conform** (`emit_identity_shacl`): a SHACL shape asserting the post-resolution
  invariant (each individual carries exactly one resolved id).

`rdflib` is the parser/serializer and an OPTIONAL dependency (`goldenmatch[ontology]`);
it is imported lazily inside each function, so `from goldenmatch.semantic import …`
never requires it. GoldenMatch does NOT reimplement an OWL reasoner or a triple
store — it is the identity provider FOR them (the replaceable-backend rule).
Library-only, advisory, parity-free.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# W3C vocabulary IRIs (kept as plain strings so importing this module needs no
# rdflib). The GoldenMatch provenance vocabulary rides under GM_NS.
GM_NS = "https://goldenmatch.dev/ns#"
DEFAULT_BASE_IRI = "https://goldenmatch.dev/id/"


def _require_rdflib():
    try:
        import rdflib  # noqa: F401
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise ImportError(
            "The ontology layer needs rdflib. Install it with "
            "`pip install goldenmatch[ontology]` (or `pip install rdflib`)."
        ) from exc
    return rdflib


# --- model -------------------------------------------------------------------


@dataclass
class OntologyProperty:
    name: str                             # local name (fragment / last path segment)
    iri: str
    functional: bool = False              # owl:FunctionalProperty (single-valued)
    inverse_functional: bool = False      # owl:InverseFunctionalProperty (a key)
    domain: list[str] = field(default_factory=list)  # local names of rdfs:domain classes
    label: str | None = None
    comment: str | None = None


@dataclass
class OntologyClass:
    name: str                             # local name
    iri: str
    has_keys: list[list[str]] = field(default_factory=list)  # owl:hasKey axioms (prop local names)
    label: str | None = None
    comment: str | None = None


@dataclass
class Ontology:
    iri: str | None = None
    classes: list[OntologyClass] = field(default_factory=list)
    properties: list[OntologyProperty] = field(default_factory=list)


# --- parse (consume) ---------------------------------------------------------


def _local(uri: Any) -> str:
    """Local name of an IRI: the fragment after `#`, else the last path segment."""
    s = str(uri)
    if "#" in s:
        return s.rsplit("#", 1)[1]
    return s.rsplit("/", 1)[-1]


def _load_graph(source: str | Any, fmt: str | None = None):
    """Load an rdflib Graph from a Graph (passthrough), a file path, or an RDF
    string. Strings default to Turtle; paths let rdflib guess by extension."""
    import os

    _require_rdflib()
    from rdflib import Graph

    if isinstance(source, Graph):
        return source
    g = Graph()
    if isinstance(source, str) and os.path.exists(source):
        g.parse(source, format=fmt) if fmt else g.parse(source)
    else:
        g.parse(data=str(source), format=fmt or "turtle")
    return g


def parse_ontology(source: str | Any, *, fmt: str | None = None) -> Ontology:
    """Parse an OWL/RDF ontology (rdflib Graph, file path, or RDF string) into an
    `Ontology`, extracting the identity-bearing axioms: `owl:Class` declarations
    with their `owl:hasKey` composite keys, and the `owl:FunctionalProperty` /
    `owl:InverseFunctionalProperty` characteristics with `rdfs:domain`.
    """
    _require_rdflib()
    from rdflib import OWL, RDF, RDFS, URIRef
    from rdflib.collection import Collection

    g = _load_graph(source, fmt)

    def _label(node) -> str | None:
        v = g.value(node, RDFS.label)
        return str(v) if v is not None else None

    def _comment(node) -> str | None:
        v = g.value(node, RDFS.comment)
        return str(v) if v is not None else None

    classes: list[OntologyClass] = []
    for c in g.subjects(RDF.type, OWL.Class):
        if not isinstance(c, URIRef):
            continue  # skip anonymous class expressions (restrictions, unions)
        has_keys: list[list[str]] = []
        for key_node in g.objects(c, OWL.hasKey):
            props = [_local(p) for p in Collection(g, key_node) if isinstance(p, URIRef)]
            if props:
                has_keys.append(props)
        classes.append(OntologyClass(
            name=_local(c), iri=str(c), has_keys=has_keys,
            label=_label(c), comment=_comment(c),
        ))

    props: dict[str, OntologyProperty] = {}

    def _prop(p) -> OntologyProperty | None:
        if not isinstance(p, URIRef):
            return None
        op = props.get(str(p))
        if op is None:
            op = OntologyProperty(
                name=_local(p), iri=str(p),
                domain=[_local(d) for d in g.objects(p, RDFS.domain) if isinstance(d, URIRef)],
                label=_label(p), comment=_comment(p),
            )
            props[str(p)] = op
        return op

    for p in g.subjects(RDF.type, OWL.InverseFunctionalProperty):
        op = _prop(p)
        if op is not None:
            op.inverse_functional = True
    for p in g.subjects(RDF.type, OWL.FunctionalProperty):
        op = _prop(p)
        if op is not None:
            op.functional = True

    onto_iri = next((str(s) for s in g.subjects(RDF.type, OWL.Ontology)), None)
    return Ontology(iri=onto_iri, classes=classes, properties=list(props.values()))


def ontology_identity_keys(onto: Ontology) -> list[dict[str, Any]]:
    """The ontology's DECLARED identifying keys — what GoldenMatch should resolve.

    One entry per declared key: `{class, key, source}` where `source` is
    `owl:hasKey` (composite, class-scoped) or `owl:InverseFunctionalProperty`
    (single-property, mapped to the property's `rdfs:domain` class). An IFP with
    no domain is surfaced with `class=None` (it identifies individuals globally).
    """
    out: list[dict[str, Any]] = []
    for c in onto.classes:
        for key in c.has_keys:
            out.append({"class": c.name, "key": list(key), "source": "owl:hasKey"})
    for p in onto.properties:
        if not p.inverse_functional:
            continue
        domains = p.domain or [None]
        for cls in domains:
            out.append({"class": cls, "key": [p.name], "source": "owl:InverseFunctionalProperty"})
    return out


# --- bridge: certify the ontology's identity keys (metric-aware, wedge A) ------


def certify_ontology_keys(onto: Ontology | str | Any, frames: dict[str, Any]) -> list[dict[str, Any]]:
    """Certify each of an ontology's declared identifying keys against instance
    data, via wedge A — certifying exactly the identity the reasoner will trust.
    `frames` maps class local-name -> table; a declared key whose class has no
    supplied frame (or a global IFP with `class=None`) is skipped.

    Returns `[{class, key, source, certificate}]`.
    """
    from goldenmatch.semantic.key_integrity import certify_key_integrity

    if not isinstance(onto, Ontology):
        onto = parse_ontology(onto)

    out: list[dict[str, Any]] = []
    for entry in ontology_identity_keys(onto):
        cls = entry["class"]
        df = frames.get(cls) if cls is not None else None
        if df is None:
            continue
        cert = certify_key_integrity(df, key=entry["key"])
        out.append({
            "class": cls,
            "key": list(entry["key"]),
            "source": entry["source"],
            "certificate": cert,
        })
    return out


# --- emit (produce) ----------------------------------------------------------


def _crosswalk_rows(crosswalk: Any) -> tuple[list[dict[str, Any]], str]:
    """Return `(rows, resolved_field)` from a `ResolvedCrosswalk` (or any object
    exposing `to_arrow()`/`table` + `resolved_key`)."""
    resolved_field = getattr(crosswalk, "resolved_key", "resolved_entity_id")
    table = crosswalk.to_arrow() if hasattr(crosswalk, "to_arrow") else getattr(crosswalk, "table", crosswalk)
    rows = table.to_pylist() if hasattr(table, "to_pylist") else list(table)
    return rows, resolved_field


def emit_sameas_graph(
    crosswalk: Any,
    *,
    base_iri: str = DEFAULT_BASE_IRI,
    run_name: str = "resolution",
    resolved_field: str | None = None,
    fmt: str = "turtle",
) -> str:
    """Emit the resolved identity of a `ResolvedCrosswalk` (wedge B) as RDF:
    `owl:sameAs` linking each source individual to its resolved canonical
    individual, plus W3C PROV-O provenance (the canonical entity
    `prov:wasDerivedFrom` each source record and `prov:wasGeneratedBy` a single
    resolution activity carrying GoldenMatch run metadata). Point a triple store
    or reasoner at the canonical individuals and identity is conformed.
    """
    from urllib.parse import quote

    _require_rdflib()
    from rdflib import OWL, RDF, RDFS, Graph, Literal, Namespace, URIRef
    from rdflib.namespace import PROV, XSD

    rows, rf = _crosswalk_rows(crosswalk)
    resolved_field = resolved_field or rf
    EX = Namespace(base_iri)
    GM = Namespace(GM_NS)

    g = Graph()
    g.bind("owl", OWL)
    g.bind("prov", PROV)
    g.bind("rdfs", RDFS)
    g.bind("gm", GM)
    g.bind("id", EX)

    activity = URIRef(f"{base_iri}activity/{quote(str(run_name), safe='')}")
    g.add((activity, RDF.type, PROV.Activity))
    g.add((activity, GM.generatedBy, Literal("goldenmatch.semantic")))
    n_entities = getattr(crosswalk, "n_entities", None)
    if n_entities is not None:
        g.add((activity, GM.nEntities, Literal(int(n_entities), datatype=XSD.integer)))
    rr = getattr(crosswalk, "reduction_ratio", None)
    if rr is not None:
        g.add((activity, GM.reductionRatio, Literal(round(float(rr), 6), datatype=XSD.decimal)))
    # Documented, human-readable vocabulary (the ontology "explicit documentation" rule).
    g.add((GM.generatedBy, RDFS.label, Literal("generated by")))
    g.add((GM.reductionRatio, RDFS.comment,
           Literal("1 - entities/records: how much resolution collapsed the source key space")))

    for row in rows:
        rid = row.get(resolved_field)
        src_name = row.get("source")
        src_pk = row.get("source_pk")
        if rid is None or src_pk is None:
            continue  # unresolved / null source key — nothing to assert
        src = URIRef(f"{base_iri}record/{quote(str(src_name), safe='')}/{quote(str(src_pk), safe='')}")
        canon = URIRef(f"{base_iri}entity/{quote(str(rid), safe='')}")
        g.add((src, RDF.type, PROV.Entity))
        g.add((canon, RDF.type, PROV.Entity))
        g.add((src, OWL.sameAs, canon))
        g.add((canon, PROV.wasDerivedFrom, src))
        g.add((canon, PROV.wasGeneratedBy, activity))

    return g.serialize(format=fmt)


def emit_identity_shacl(
    *,
    resolved_field: str = "resolved_entity_id",
    target_class: str | None = None,
    base_iri: str = DEFAULT_BASE_IRI,
    fmt: str = "turtle",
) -> str:
    """Emit a SHACL shape for the post-resolution identity invariant: every
    targeted individual carries exactly one resolved id (`sh:minCount 1`,
    `sh:maxCount 1` on the resolved-key path). This is the conformance direction —
    a shape a triple store's SHACL engine validates; GoldenMatch produces it, it
    does not execute it.
    """
    _require_rdflib()
    from rdflib import RDF, RDFS, Graph, Literal, Namespace, URIRef
    from rdflib.namespace import SH, XSD

    EX = Namespace(base_iri)
    GM = Namespace(GM_NS)
    g = Graph()
    g.bind("sh", SH)
    g.bind("gm", GM)
    g.bind("id", EX)

    shape = URIRef(f"{base_iri}shape/ResolvedIdentity")
    prop = URIRef(f"{base_iri}shape/ResolvedIdentity/resolvedKey")
    g.add((shape, RDF.type, SH.NodeShape))
    g.add((shape, RDFS.comment,
           Literal("Each resolved individual carries exactly one GoldenMatch resolved id.")))
    if target_class:
        g.add((shape, SH.targetClass, URIRef(f"{base_iri}class/{target_class}")))
    g.add((shape, SH.property, prop))
    g.add((prop, SH.path, GM[resolved_field]))
    g.add((prop, SH.minCount, Literal(1, datatype=XSD.integer)))
    g.add((prop, SH.maxCount, Literal(1, datatype=XSD.integer)))
    g.add((prop, SH.name, Literal(resolved_field)))

    return g.serialize(format=fmt)
