"""CLI + MCP front door for the ontology layer (`goldenmatch ontology`, ontology_* tools).

rdflib is optional (`goldenmatch[ontology]`); skip when absent (ray/lance convention).
"""
from __future__ import annotations

import json

import pytest

pytest.importorskip("rdflib")

from goldenmatch.cli.main import app  # noqa: E402
from goldenmatch.mcp.server import _tool_ontology_certify, _tool_ontology_discover  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

runner = CliRunner()

_ONTOLOGY = """
@prefix owl:  <http://www.w3.org/2002/07/owl#> .
@prefix ex:   <https://example.org/clinic#> .

ex:Patient a owl:Class ;
    owl:hasKey ( ex:mrn ) .
"""


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


def _patients_csv(tmp_path, *, unique=True):
    mrns = ["m1", "m2", "m3"] if unique else ["m1", "m1", "m3"]
    return _write(tmp_path, "patients.csv", "mrn\n" + "\n".join(mrns) + "\n")


def _customers_csv(tmp_path):
    return _write(tmp_path, "customers.csv",
                  "customer_id,city\n1,NY\n2,NY\n3,LA\n4,LA\n")


# --- CLI ---------------------------------------------------------------------


def test_cli_ontology_certify_json_all_safe(tmp_path):
    onto = _write(tmp_path, "clinic.ttl", _ONTOLOGY)
    csv = _patients_csv(tmp_path, unique=True)
    res = runner.invoke(app, ["ontology", "certify", onto, "-d", f"Patient={csv}", "--json"])
    assert res.exit_code == 0, res.output
    report = json.loads(res.output)
    assert report["all_safe"] is True and report["n_keys"] == 1
    assert report["keys"][0]["key"] == ["mrn"]


def test_cli_ontology_certify_fail_untrustworthy_exits_nonzero(tmp_path):
    onto = _write(tmp_path, "clinic.ttl", _ONTOLOGY)
    csv = _patients_csv(tmp_path, unique=False)  # duplicate mrn -> unsafe
    res = runner.invoke(app, ["ontology", "certify", onto, "-d", f"Patient={csv}",
                              "--fail-untrustworthy"])
    assert res.exit_code == 1


def test_cli_ontology_discover_writes_owl(tmp_path):
    csv = _customers_csv(tmp_path)
    out = str(tmp_path / "discovered.ttl")
    res = runner.invoke(app, ["ontology", "discover", "-d", f"Customer={csv}",
                              "-o", out, "--json"])
    assert res.exit_code == 0, res.output
    disc = json.loads(res.output)
    assert disc["n_classes"] == 1 and disc["classes"][0]["class"] == "Customer"
    assert disc["classes"][0]["key"] == ["customer_id"]
    # the emitted OWL round-trips
    from goldenmatch.semantic import parse_ontology
    onto = parse_ontology(out)
    assert next(c for c in onto.classes if c.name == "Customer").has_keys == [["customer_id"]]


def test_cli_ontology_bad_data_spec_errors(tmp_path):
    onto = _write(tmp_path, "clinic.ttl", _ONTOLOGY)
    res = runner.invoke(app, ["ontology", "certify", onto, "-d", "no-equals-sign"])
    assert res.exit_code == 2


# --- MCP ---------------------------------------------------------------------


def test_mcp_ontology_certify(tmp_path):
    onto = _write(tmp_path, "clinic.ttl", _ONTOLOGY)
    csv = _patients_csv(tmp_path, unique=False)
    out = _tool_ontology_certify(onto, {"Patient": csv})
    assert out["n_keys"] == 1 and out["all_safe"] is False
    assert out["keys"][0]["is_unique"] is False


def test_mcp_ontology_discover_returns_turtle(tmp_path):
    csv = _customers_csv(tmp_path)
    out = _tool_ontology_discover({"Customer": csv}, None, None)
    assert out["n_classes"] == 1 and out["n_trustworthy"] == 1
    assert "turtle" in out and "owl:hasKey" in out["turtle"] or "hasKey" in out["turtle"]


def test_mcp_ontology_certify_input_guards():
    assert "error" in _tool_ontology_certify("", {"Patient": "x.csv"})
    assert "error" in _tool_ontology_certify("o.ttl", {})


def test_cli_ontology_discover_endpoint_write_back(tmp_path, monkeypatch):
    captured = {}

    class _Resp:
        status = 204
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def getcode(self): return 204

    def fake(req, timeout=None):
        captured["method"] = req.get_method()
        captured["url"] = req.full_url
        return _Resp()

    monkeypatch.setattr("urllib.request.urlopen", fake)
    csv = _customers_csv(tmp_path)
    res = runner.invoke(app, ["ontology", "discover", "-d", f"Customer={csv}",
                              "--endpoint", "http://ts/data", "--graph-iri", "urn:g"])
    assert res.exit_code == 0, res.output
    assert captured["method"] == "PUT" and "graph=urn%3Ag" in captured["url"]
    assert "HTTP 204" in res.output
