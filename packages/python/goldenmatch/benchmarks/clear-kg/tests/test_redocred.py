"""Re-DocRED Track-A harness, exercised OFFLINE with a hand-built raw doc + a
mock extractor -- no key, no network. The live LLM number is produced by
run_redocred.py (network + key-gated), not here."""
from llm_extractor import _coerce, mock_extract
from redocred import load_docs
from score_redocred import score_redocred

# one DocRED-shaped raw doc: 2 sentences, 3 entities, 2 gold relations
_RAW = [{
    "title": "Toy",
    "sents": [["Acme", "was", "founded", "by", "Jane", "."],
              ["Acme", "is", "based", "in", "Portland", "."]],
    "vertexSet": [
        [{"name": "Acme", "sent_id": 0, "pos": [0, 1], "type": "ORG"}],
        [{"name": "Jane", "sent_id": 0, "pos": [4, 5], "type": "PER"}],
        [{"name": "Portland", "sent_id": 1, "pos": [4, 5], "type": "LOC"}],
    ],
    "labels": [{"h": 0, "t": 1, "r": "P112", "evidence": [0]},
               {"h": 0, "t": 2, "r": "P159", "evidence": [1]}],
}]
_REL_NAMES = {"P112": "founded by", "P159": "headquarters location"}


def test_load_shapes_docs_and_closed_schema():
    docs, schema = load_docs(limit=1, offline=(_RAW, _REL_NAMES))
    d = docs[0]
    assert d["text"].startswith("Acme was founded by Jane")
    assert len(d["entities"]) == 3
    assert d["gold"] == {(0, "founded by", 1), (0, "headquarters location", 2)}
    # schema is the FULL relation set, not just what occurs in the slice
    assert set(schema) == {"founded by", "headquarters location"}


def test_scoring_perfect_and_partial():
    docs, schema = load_docs(limit=1, offline=(_RAW, _REL_NAMES))
    perfect = score_redocred([mock_extract(docs[0], schema, oracle=1.0)], docs)
    assert perfect["f1"] == 1.0 and perfect["tp"] == 2
    half = score_redocred([mock_extract(docs[0], schema, oracle=0.5)], docs)
    assert half["tp"] == 1 and 0.0 < half["f1"] < 1.0


def test_coerce_normalizes_and_filters():
    docs, schema = load_docs(limit=1, offline=(_RAW, _REL_NAMES))
    # case-insensitive relation match; drop out-of-schema + out-of-range + self-pairs
    raw = ('{"triples": [{"h":0,"r":"FOUNDED BY","t":1}, {"h":0,"r":"invented","t":2}, '
           '{"h":0,"r":"founded by","t":9}, {"h":1,"r":"founded by","t":1}]}')
    got = _coerce(raw, docs[0], set(schema))
    assert got == {(0, "founded by", 1)}
