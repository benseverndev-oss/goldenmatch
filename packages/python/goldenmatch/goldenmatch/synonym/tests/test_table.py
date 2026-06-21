from __future__ import annotations

import json

from goldenmatch.synonym.table import SynonymTable


def test_from_json_symmetric_normalized_equivalence(tmp_path):
    p = tmp_path / "t.json"
    p.write_text(json.dumps({"aliases": {"ibuprofen": ["Advil", "Motrin"]}}), encoding="utf-8")
    t = SynonymTable.from_json(p)
    assert t.is_available()
    assert t.are_equivalent("Advil", "ibuprofen")
    assert t.are_equivalent("advil ", "MOTRIN")  # case/space normalized, symmetric
    assert not t.are_equivalent("Advil", "Tylenol")
    assert not t.are_equivalent(None, "Advil")


def test_missing_file_is_empty_and_graceful():
    t = SynonymTable.from_json("/no/such/synonym/file.json")
    assert not t.is_available()
    assert not t.are_equivalent("Advil", "ibuprofen")


def test_empty_has_no_equivalences():
    t = SynonymTable.empty()
    assert not t.is_available()
    assert not t.are_equivalent("a", "a")  # table asserts known synonyms only
