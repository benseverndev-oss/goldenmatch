import polars as pl

from goldenflow.mapping.schema_mapper import SchemaMapper, ColumnMapping


def test_auto_map_identical_columns():
    source = pl.DataFrame({"email": ["a@t.com"], "name": ["John"]})
    target = pl.DataFrame({"email": ["b@t.com"], "name": ["Jane"]})
    mapper = SchemaMapper()
    mappings = mapper.map(source, target)
    assert len(mappings) >= 2
    email_map = next(m for m in mappings if m.source == "email")
    assert email_map.target == "email"
    assert email_map.confidence >= 0.9


def test_auto_map_aliased_columns():
    source = pl.DataFrame({"fname": ["John"], "email_address": ["a@t.com"]})
    target = pl.DataFrame({"first_name": ["Jane"], "email": ["b@t.com"]})
    mapper = SchemaMapper()
    mappings = mapper.map(source, target)
    fname_map = next((m for m in mappings if m.source == "fname"), None)
    assert fname_map is not None
    assert fname_map.target == "first_name"


def test_confidence_tiers():
    source = pl.DataFrame({"email": ["a@t.com"], "xyz_unknown": [1]})
    target = pl.DataFrame({"email": ["b@t.com"], "abc_other": [2]})
    mapper = SchemaMapper()
    mappings = mapper.map(source, target)
    email_map = next(m for m in mappings if m.source == "email")
    assert email_map.confidence >= 0.9  # auto-apply tier


def test_export_mapping_config():
    source = pl.DataFrame({"email": ["a@t.com"]})
    target = pl.DataFrame({"email": ["b@t.com"]})
    mapper = SchemaMapper()
    mappings = mapper.map(source, target)
    config = mapper.to_config(mappings)
    assert len(config.mappings) >= 1
