"""Box-safe tests for the pure normalization helpers in fetch_data.py (no
network -- the download path is exercised manually / end-to-end, not in CI)."""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import pytest
from fetch_data import _decode, normalize_mapping, normalize_table


def test_decode_strips_utf8_bom_and_reads_latin1():
    # Scholar.csv is prefixed with a UTF-8 BOM; DBLP/Scholar contain latin-1 bytes.
    assert _decode(b'\xef\xbb\xbf"id","title"') == '"id","title"'
    assert _decode(b"W\xe4sch") == "Wäsch"  # latin-1 0xE4 -> a-umlaut


def test_normalize_table_renames_blocking_field_and_keeps_id():
    fields = ["id", "title", "description", "price"]
    rows = [{"id": "a1", "title": "Canon EOS", "description": "cam", "price": "9"}]
    header, out = normalize_table(fields, rows, {"title": "name"}, block="name")
    assert header == ["id", "name", "description", "price"]
    assert out[0]["name"] == "Canon EOS" and out[0]["id"] == "a1"


def test_normalize_table_requires_id_and_block():
    with pytest.raises(ValueError, match="id column"):
        normalize_table(["name", "price"], [], {}, block="name")
    with pytest.raises(ValueError, match="blocking field"):
        normalize_table(["id", "title"], [], {}, block="name")  # no title->name rename


def test_normalize_mapping_renames_dataset_specific_columns():
    rows = [{"idAbt": "1", "idBuy": "2"}, {"idAbt": "3", "idBuy": "4"}]
    out = normalize_mapping(rows, "idAbt", "idBuy")
    assert out == [{"idA": "1", "idB": "2"}, {"idA": "3", "idB": "4"}]


def test_normalize_mapping_rejects_wrong_columns():
    with pytest.raises(ValueError, match="idAbt"):
        normalize_mapping([{"id1": "1", "id2": "2"}], "idAbt", "idBuy")
