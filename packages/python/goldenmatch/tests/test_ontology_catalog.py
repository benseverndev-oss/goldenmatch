"""Live-catalog write-back for the ontology layer (write_ontology_catalog).

These test the write path with a plain RDF string, so they need no rdflib and run
in the normal lane. The endpoint tests mock urllib (no live triple store).
"""
from __future__ import annotations

import pytest
from goldenmatch.semantic import write_ontology_catalog

_TTL = "@prefix owl: <http://www.w3.org/2002/07/owl#> .\n<urn:a> owl:sameAs <urn:b> .\n"


def test_write_to_file(tmp_path):
    dest = tmp_path / "sub" / "graph.ttl"
    out = write_ontology_catalog(_TTL, dest)
    assert out["written"] == str(dest) and out["bytes"] == len(_TTL.encode("utf-8"))
    assert dest.read_text(encoding="utf-8") == _TTL


def test_write_to_file_refuses_clobber(tmp_path):
    dest = tmp_path / "graph.ttl"
    write_ontology_catalog(_TTL, dest)
    with pytest.raises(FileExistsError):
        write_ontology_catalog(_TTL, dest)
    # overwrite=True replaces
    out = write_ontology_catalog(_TTL + "# more\n", dest, overwrite=True)
    assert out["bytes"] > len(_TTL.encode("utf-8"))


def test_requires_exactly_one_destination(tmp_path):
    with pytest.raises(ValueError):
        write_ontology_catalog(_TTL)  # neither
    with pytest.raises(ValueError):
        write_ontology_catalog(_TTL, tmp_path / "x.ttl", endpoint="http://ts/data")  # both


def test_rejects_bad_mode():
    with pytest.raises(ValueError):
        write_ontology_catalog(_TTL, endpoint="http://ts/data", mode="upsert")


def _mock_urlopen(monkeypatch, status=204):
    captured: dict = {}

    class _Resp:
        def __init__(self):
            self.status = status
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def getcode(self):
            return status

    def fake(req, timeout=None):
        captured["method"] = req.get_method()
        captured["url"] = req.full_url
        captured["content_type"] = req.get_header("Content-type")
        captured["body"] = req.data
        return _Resp()

    monkeypatch.setattr("urllib.request.urlopen", fake)
    return captured


def test_endpoint_replace_is_put_named_graph(monkeypatch):
    captured = _mock_urlopen(monkeypatch, status=204)
    out = write_ontology_catalog(_TTL, endpoint="http://ts/data",
                                 graph_iri="urn:g", mode="replace")
    assert captured["method"] == "PUT"
    assert "graph=urn%3Ag" in captured["url"]
    assert captured["content_type"] == "text/turtle"
    assert captured["body"] == _TTL.encode("utf-8")
    assert out["status"] == 204 and out["method"] == "PUT" and out["graph"] == "urn:g"


def test_endpoint_merge_is_post_default_graph(monkeypatch):
    captured = _mock_urlopen(monkeypatch, status=200)
    out = write_ontology_catalog(_TTL, endpoint="http://ts/data", mode="merge")
    assert captured["method"] == "POST"
    assert captured["url"].endswith("?default")
    assert out["status"] == 200 and out["mode"] == "merge"


def test_endpoint_http_error_is_wrapped(monkeypatch):
    from urllib.error import HTTPError

    def boom(req, timeout=None):
        raise HTTPError(req.full_url, 403, "Forbidden", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", boom)
    with pytest.raises(RuntimeError, match="HTTP 403"):
        write_ontology_catalog(_TTL, endpoint="http://ts/data")


def test_write_resolved_identity_graph_to_file(tmp_path):
    pytest.importorskip("rdflib")
    import pyarrow as pa
    import rdflib
    from goldenmatch.semantic import write_resolved_identity_graph
    from rdflib.namespace import OWL

    class _XW:
        resolved_key = "resolved_entity_id"

        def to_arrow(self):
            return pa.table({
                "source": ["crm", "crm"],
                "source_pk": ["1", "2"],
                "resolved_entity_id": ["e1", "e1"],
            })

    dest = tmp_path / "identity.ttl"
    out = write_resolved_identity_graph(_XW(), dest=dest, base_iri="https://ex.test/id/")
    assert out["written"] == str(dest)
    g = rdflib.Graph()
    g.parse(str(dest), format="turtle")
    # both records were written as owl:sameAs to the same canonical entity
    assert len(list(g.triples((None, OWL.sameAs, None)))) == 2
