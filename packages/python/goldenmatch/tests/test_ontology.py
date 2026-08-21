"""Tests for the ontology-layer (RDF/OWL/SHACL) identity provider.

rdflib is an optional dependency (`goldenmatch[ontology]`); these skip when it is
absent, mirroring the ray/lance/torch optional-dep convention.
"""
from __future__ import annotations

import pyarrow as pa
import pytest

pytest.importorskip("rdflib")

from goldenmatch.semantic import (  # noqa: E402
    Ontology,
    asserted_sameas_pairs,
    certify_ontology,
    certify_ontology_keys,
    discover_ontology,
    effective_has_keys,
    emit_golden_triples,
    emit_identity_shacl,
    emit_ontology_shapes,
    emit_sameas_graph,
    ontology_identity_keys,
    parse_ontology,
    reconcile_ontology_identity,
)

# A real-shaped OWL ontology in Turtle: a Patient class with a composite
# owl:hasKey, plus an inverse-functional property (mrn) that identifies a Patient.
_TTL = """
@prefix owl:  <http://www.w3.org/2002/07/owl#> .
@prefix rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix ex:   <https://example.org/clinic#> .

ex:Clinic a owl:Ontology .

ex:Patient a owl:Class ;
    rdfs:label "Patient" ;
    rdfs:comment "A person receiving care." ;
    owl:hasKey ( ex:firstName ex:lastName ex:birthDate ) .

ex:mrn a owl:InverseFunctionalProperty ;
    rdfs:domain ex:Patient ;
    rdfs:label "medical record number" .

ex:ssn a owl:InverseFunctionalProperty, owl:FunctionalProperty ;
    rdfs:domain ex:Patient .
"""


def test_parse_extracts_classes_keys_and_ifps():
    onto = parse_ontology(_TTL)
    assert isinstance(onto, Ontology)
    assert onto.iri == "https://example.org/clinic#Clinic"
    patient = next(c for c in onto.classes if c.name == "Patient")
    assert patient.label == "Patient"
    assert patient.has_keys == [["firstName", "lastName", "birthDate"]]
    mrn = next(p for p in onto.properties if p.name == "mrn")
    assert mrn.inverse_functional and mrn.domain == ["Patient"]
    ssn = next(p for p in onto.properties if p.name == "ssn")
    assert ssn.inverse_functional and ssn.functional


def test_identity_keys_are_what_to_resolve():
    onto = parse_ontology(_TTL)
    keys = {(k["class"], tuple(k["key"]), k["source"]) for k in ontology_identity_keys(onto)}
    assert ("Patient", ("firstName", "lastName", "birthDate"), "owl:hasKey") in keys
    assert ("Patient", ("mrn",), "owl:InverseFunctionalProperty") in keys
    assert ("Patient", ("ssn",), "owl:InverseFunctionalProperty") in keys


def test_certify_ontology_keys_bridges_wedge_a():
    onto = parse_ontology(_TTL)
    # mrn has a duplicate -> the IFP the reasoner trusts is actually unsafe
    frames = {"Patient": pa.table({
        "mrn": ["m1", "m1", "m2"],
        "ssn": ["a", "b", "c"],
        "firstName": ["Bob", "Bob", "Amy"],
        "lastName": ["Smith", "Smith", "Jones"],
        "birthDate": ["1990-01-01", "1990-01-01", "1985-05-05"],
    })}
    rep = certify_ontology_keys(onto, frames)
    by_key = {tuple(e["key"]): e for e in rep}
    # the composite hasKey and both IFPs all got certified against the frame
    assert ("firstName", "lastName", "birthDate") in by_key
    assert ("mrn",) in by_key and ("ssn",) in by_key
    mrn_cert = by_key[("mrn",)]["certificate"]
    assert mrn_cert.estimate == 0.5 and mrn_cert.max_fan_out == 2.0
    # ssn is unique -> certificate passes
    assert by_key[("ssn",)]["certificate"].estimate == 1.0


def test_certify_accepts_source_string_and_skips_absent_frames():
    # passing raw Turtle (not a parsed Ontology) + no frames -> nothing to certify
    assert certify_ontology_keys(_TTL, frames={}) == []


class _XW:
    source = "crm"
    source_pk_column = "customer_id"
    resolved_key = "resolved_entity_id"
    n_records = 3
    n_entities = 2
    reduction_ratio = 1 - 2 / 3

    def to_arrow(self):
        return pa.table({
            "source": ["crm", "crm", "crm"],
            "source_pk": ["1", "2", "3"],
            "resolved_entity_id": ["e1", "e1", "e2"],
        })


def test_emit_sameas_graph_links_sources_to_resolved_entities():
    import rdflib
    from rdflib.namespace import OWL, PROV

    ttl = emit_sameas_graph(_XW(), base_iri="https://ex.test/id/")
    g = rdflib.Graph()
    g.parse(data=ttl, format="turtle")

    e1 = rdflib.URIRef("https://ex.test/id/entity/e1")
    rec1 = rdflib.URIRef("https://ex.test/id/record/crm/1")
    rec2 = rdflib.URIRef("https://ex.test/id/record/crm/2")
    # records 1 and 2 both resolve to entity e1 (owl:sameAs)
    assert (rec1, OWL.sameAs, e1) in g
    assert (rec2, OWL.sameAs, e1) in g
    # PROV-O: the canonical entity was derived from each source record
    assert (e1, PROV.wasDerivedFrom, rec1) in g
    # exactly two distinct canonical entities emitted
    entities = set(g.objects(predicate=OWL.sameAs))
    assert len(entities) == 2


def test_emit_identity_shacl_asserts_single_resolved_key():
    import rdflib
    from rdflib.namespace import SH

    ttl = emit_identity_shacl(resolved_field="resolved_entity_id",
                              target_class="Patient", base_iri="https://ex.test/id/")
    g = rdflib.Graph()
    g.parse(data=ttl, format="turtle")
    shapes = list(g.subjects(rdflib.RDF.type, SH.NodeShape))
    assert len(shapes) == 1
    # the property shape pins the resolved key to exactly one value
    assert any(int(o) == 1 for o in g.objects(predicate=SH.minCount))
    assert any(int(o) == 1 for o in g.objects(predicate=SH.maxCount))


# --- PR1: deeper consume + certification report + reconciliation --------------

# Patient is a subclass of Person; Person carries the owl:hasKey, and a
# cardinality-1 restriction marks mrn single-valued on Patient.
_TTL_INHERIT = """
@prefix owl:  <http://www.w3.org/2002/07/owl#> .
@prefix rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix ex:   <https://example.org/clinic#> .

ex:Person a owl:Class ;
    owl:hasKey ( ex:ssn ) .

ex:Patient a owl:Class ;
    rdfs:subClassOf ex:Person ;
    rdfs:subClassOf [ a owl:Restriction ; owl:onProperty ex:mrn ; owl:maxCardinality 1 ] .
"""


def test_subclass_inherits_haskey():
    onto = parse_ontology(_TTL_INHERIT)
    patient = next(c for c in onto.classes if c.name == "Patient")
    assert patient.parents == ["Person"]
    # the composite (here single) key is inherited from Person
    assert effective_has_keys(onto, "Patient") == [["ssn"]]
    # ...and surfaces as an identity key tagged inherited
    keys = {(k["class"], tuple(k["key"]), k["source"]) for k in ontology_identity_keys(onto)}
    assert ("Patient", ("ssn",), "owl:hasKey(inherited)") in keys
    assert ("Person", ("ssn",), "owl:hasKey") in keys


def test_cardinality_one_restriction_parsed():
    onto = parse_ontology(_TTL_INHERIT)
    patient = next(c for c in onto.classes if c.name == "Patient")
    assert patient.max_one_properties == ["mrn"]


def test_certify_ontology_rolls_up_safe_and_unsafe():
    onto = parse_ontology(_TTL)  # Patient: hasKey(firstName,lastName,birthDate) + mrn/ssn IFPs
    frames = {"Patient": pa.table({
        "mrn": ["m1", "m1", "m2"],       # duplicate -> unsafe
        "ssn": ["a", "b", "c"],          # unique -> safe
        "firstName": ["Bob", "Bob", "Amy"],
        "lastName": ["Smith", "Smith", "Jones"],
        "birthDate": ["1990-01-01", "1990-01-01", "1985-05-05"],
    })}
    report = certify_ontology(onto, frames)
    assert report.n_keys == 3 and report.n_unsafe >= 1
    assert report.all_safe is False
    d = report.to_dict()
    assert d["n_keys"] == 3 and "certificate" not in d["keys"][0]
    mrn = next(k for k in report.keys if k["key"] == ["mrn"])
    assert mrn["is_unique"] is False


def test_certify_ontology_all_safe_when_every_key_unique():
    onto = parse_ontology(_TTL)
    frames = {"Patient": pa.table({
        "mrn": ["m1", "m2", "m3"], "ssn": ["a", "b", "c"],
        "firstName": ["A", "B", "C"], "lastName": ["x", "y", "z"],
        "birthDate": ["2000-01-01", "2000-01-02", "2000-01-03"],
    })}
    report = certify_ontology(onto, frames)
    assert report.all_safe is True and report.n_unsafe == 0


class _RXW:
    """Crosswalk: 4 crm records -> entities e1 (1,2,3) and e2 (4)."""
    resolved_key = "resolved_entity_id"

    def to_arrow(self):
        return pa.table({
            "source": ["crm"] * 4,
            "source_pk": ["1", "2", "3", "4"],
            "resolved_entity_id": ["e1", "e1", "e1", "e2"],
        })


_ASSERTED = """
@prefix owl: <http://www.w3.org/2002/07/owl#> .
<https://ex.test/id/record/crm/1> owl:sameAs <https://ex.test/id/record/crm/2> .
<https://ex.test/id/record/crm/3> owl:sameAs <https://ex.test/id/record/crm/4> .
"""


def test_asserted_sameas_pairs_reads_links():
    pairs = asserted_sameas_pairs(_ASSERTED)
    assert ("https://ex.test/id/record/crm/1", "https://ex.test/id/record/crm/2") in pairs
    assert len(pairs) == 2


def test_reconcile_flags_over_merge_and_fragmentation():
    rec = reconcile_ontology_identity(_ASSERTED, _RXW(), base_iri="https://ex.test/id/")
    # (1,2): asserted same, both resolved e1 -> agreement
    assert rec.agreements == 1
    # (3,4): asserted same but e1 != e2 -> over-merge
    assert rec.over_merges == [("https://ex.test/id/record/crm/3",
                                "https://ex.test/id/record/crm/4")]
    # e1 spans the {1,2} component and the unasserted 3 -> fragmentation bridge
    assert rec.fragmentations == [("https://ex.test/id/record/crm/1",
                                   "https://ex.test/id/record/crm/3")]
    assert rec.n_asserted_links == 2 and rec.n_resolved_records == 4
    d = rec.to_dict()
    assert d["agreements"] == 1 and d["n_over_merges"] == 1 and d["n_fragmentations"] == 1


# --- PR2: richer emit (typed individuals + per-class SHACL) + discovery --------


def test_emit_sameas_graph_types_canonical_entities_to_class():
    import rdflib

    ttl = emit_sameas_graph(_XW(), base_iri="https://ex.test/id/", target_class="Patient")
    g = rdflib.Graph()
    g.parse(data=ttl, format="turtle")
    e1 = rdflib.URIRef("https://ex.test/id/entity/e1")
    patient = rdflib.URIRef("https://ex.test/id/class/Patient")
    assert (e1, rdflib.RDF.type, patient) in g
    # prov:Entity typing is still present (additive)
    from rdflib.namespace import PROV
    assert (e1, rdflib.RDF.type, PROV.Entity) in g
    # no target_class -> no class typing (backward compatible)
    g2 = rdflib.Graph()
    g2.parse(data=emit_sameas_graph(_XW(), base_iri="https://ex.test/id/"), format="turtle")
    assert (e1, rdflib.RDF.type, patient) not in g2


def test_emit_ontology_shapes_per_class_from_haskey():
    import rdflib
    from rdflib.namespace import SH

    ttl = emit_ontology_shapes(_TTL)
    g = rdflib.Graph()
    g.parse(data=ttl, format="turtle")
    shapes = list(g.subjects(rdflib.RDF.type, SH.NodeShape))
    assert len(shapes) == 1
    # targets the Patient class by its real IRI
    assert (shapes[0], SH.targetClass,
            rdflib.URIRef("https://example.org/clinic#Patient")) in g
    # one property shape per key property, path reconstructed in the class namespace
    paths = {str(o) for o in g.objects(predicate=SH.path)}
    assert "https://example.org/clinic#firstName" in paths
    assert "https://example.org/clinic#birthDate" in paths
    assert all(int(o) == 1 for o in g.objects(predicate=SH.minCount))


def test_emit_golden_triples_typed_individuals_with_values():
    import rdflib

    golden = pa.table({
        "resolved_entity_id": ["e1", "e2"],
        "name": ["Bob Smith", "Amy Jones"],
        "city": ["NY", "LA"],
    })
    ttl = emit_golden_triples(golden, class_name="Patient", id_column="resolved_entity_id",
                              base_iri="https://ex.test/id/")
    g = rdflib.Graph()
    g.parse(data=ttl, format="turtle")
    e1 = rdflib.URIRef("https://ex.test/id/entity/e1")
    assert (e1, rdflib.RDF.type, rdflib.URIRef("https://ex.test/id/class/Patient")) in g
    assert (e1, rdflib.URIRef("https://ex.test/id/prop/name"),
            rdflib.Literal("Bob Smith")) in g
    # id column is not re-emitted as an attribute
    assert not list(g.objects(e1, rdflib.URIRef("https://ex.test/id/prop/resolved_entity_id")))


def test_discover_ontology_emits_pregraded_haskey():
    frames = {"Customer": pa.table({
        "customer_id": [1, 2, 3, 4],       # unique -> the trustworthy key
        "email": ["a@x", "a@x", "b@x", "c@x"],
        "city": ["NY", "NY", "LA", "LA"],
    })}
    disc = discover_ontology(frames, base_iri="https://ex.test/id/")
    assert len(disc.classes) == 1
    entry = disc.classes[0]
    assert entry["class"] == "Customer" and entry["key"] == ["customer_id"]
    assert entry["is_trustworthy"] is True and entry["estimate"] == 1.0
    # the emitted OWL round-trips: the class carries owl:hasKey (customer_id)
    onto = parse_ontology(disc.turtle)
    customer = next(c for c in onto.classes if c.name == "Customer")
    assert customer.has_keys == [["customer_id"]]
    d = disc.to_dict()
    assert d["n_classes"] == 1 and d["n_trustworthy"] == 1


def test_discover_ontology_flags_untrustworthy_when_no_clean_key():
    # every column fans out -> best candidate is flagged untrustworthy, still emitted
    frames = {"Event": pa.table({
        "kind": ["click", "click", "view", "view"],
        "channel": ["web", "web", "app", "app"],
    })}
    disc = discover_ontology(frames, base_iri="https://ex.test/id/")
    assert len(disc.classes) == 1
    assert disc.classes[0]["is_trustworthy"] is False
    assert disc.to_dict()["n_trustworthy"] == 0
