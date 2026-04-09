"""Tests for MapEngine.return_score_matrix flag."""
from __future__ import annotations

from infermap.engine import MapEngine
from infermap.providers import extract_schema
from tests.conftest import CANONICAL_CUSTOMERS_CSV, CRM_EXPORT_CSV


def _make_schema(*names: str):
    return [{n: "" for n in names}]


def test_score_matrix_is_none_by_default():
    engine = MapEngine()
    src = _make_schema("fname", "lname")
    tgt = _make_schema("first_name", "last_name")
    result = engine.map(src, tgt)
    assert result.score_matrix is None


def test_score_matrix_populated_when_flag_set():
    engine = MapEngine(return_score_matrix=True)
    src = _make_schema("fname", "lname")
    tgt = _make_schema("first_name", "last_name")
    result = engine.map(src, tgt)
    assert result.score_matrix is not None
    assert set(result.score_matrix.keys()) == {"fname", "lname"}
    for row in result.score_matrix.values():
        assert set(row.keys()) == {"first_name", "last_name"}
        for score in row.values():
            assert 0.0 <= score <= 1.0


def test_score_matrix_matches_assignment():
    """The (src, tgt) pair for each mapping should appear in the score matrix
    with a score equal to the mapping's confidence (modulo rounding)."""
    engine = MapEngine(return_score_matrix=True, min_confidence=0.0)
    src = _make_schema("fname", "email_addr")
    tgt = _make_schema("first_name", "email")
    result = engine.map(src, tgt)
    assert result.score_matrix is not None
    for m in result.mappings:
        sm_score = result.score_matrix[m.source][m.target]
        assert abs(sm_score - m.confidence) < 0.01


def test_map_equivalent_to_map_schemas():
    """engine.map(path, path) and engine.map_schemas(schema, schema) must
    produce equivalent mappings on identical inputs — this is the contract
    the benchmark runner depends on to avoid double extraction."""
    engine = MapEngine()
    result_map = engine.map(str(CRM_EXPORT_CSV), str(CANONICAL_CUSTOMERS_CSV))

    src_schema = extract_schema(str(CRM_EXPORT_CSV), sample_size=engine.sample_size)
    tgt_schema = extract_schema(str(CANONICAL_CUSTOMERS_CSV), sample_size=engine.sample_size)
    result_schemas = engine.map_schemas(src_schema, tgt_schema)

    pairs_map = sorted((m.source, m.target) for m in result_map.mappings)
    pairs_schemas = sorted((m.source, m.target) for m in result_schemas.mappings)
    assert pairs_map == pairs_schemas

    conf_map = {(m.source, m.target): m.confidence for m in result_map.mappings}
    conf_schemas = {(m.source, m.target): m.confidence for m in result_schemas.mappings}
    for key in conf_map:
        assert abs(conf_map[key] - conf_schemas[key]) < 1e-9
