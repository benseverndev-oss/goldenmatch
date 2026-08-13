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
    certify_ontology_keys,
    emit_identity_shacl,
    emit_sameas_graph,
    ontology_identity_keys,
    parse_ontology,
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
