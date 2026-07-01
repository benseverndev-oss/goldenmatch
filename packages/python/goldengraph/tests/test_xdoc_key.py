"""GOLDENGRAPH_XDOC_KEY relaxes the cross-doc record_key payload to defeat extractor type/case jitter.

Pure test of the normalization (`_key_payload`) -- no goldenmatch / fingerprint needed."""
from goldengraph.resolve import _key_payload


def test_default_key_uses_name_and_type(monkeypatch):
    monkeypatch.delenv("GOLDENGRAPH_XDOC_KEY", raising=False)
    assert _key_payload("Schema Matching", "Process") == {"name": "Schema Matching", "typ": "Process"}


def test_name_mode_drops_type(monkeypatch):
    monkeypatch.setenv("GOLDENGRAPH_XDOC_KEY", "name")
    # same name, DIFFERENT type -> identical payload (so the store unifies them cross-doc)
    assert (
        _key_payload("schema matching", "Process")
        == _key_payload("schema matching", "Algorithm")
        == {"name": "schema matching"}
    )
    # case still matters in bare name mode
    assert _key_payload("Schema matching", "X") != _key_payload("schema matching", "X")


def test_name_ci_mode_folds_case_and_drops_type(monkeypatch):
    monkeypatch.setenv("GOLDENGRAPH_XDOC_KEY", "name_ci")
    # the token_sort/schema-matching real jitter: differing case AND type -> one key
    assert (
        _key_payload("Schema Matching", "Process")
        == _key_payload("schema matching", "Algorithm")
        == {"name": "schema matching"}
    )


def test_name_ci_type_mode_folds_case_and_coarsens_type(monkeypatch):
    monkeypatch.setenv("GOLDENGRAPH_XDOC_KEY", "name_ci_type")
    # same entity, jittered type across docs -> SAME key (both coarsen to 'concept')
    assert (
        _key_payload("Schema Matching", "Process")
        == _key_payload("schema matching", "Algorithm")
        == {"name": "schema matching", "typ": "concept"}
    )
    # homograph: same name, DIFFERENT coarse class -> DIFFERENT key (stays separate)
    assert _key_payload("Vertex", "company") != _key_payload("Vertex", "product")
