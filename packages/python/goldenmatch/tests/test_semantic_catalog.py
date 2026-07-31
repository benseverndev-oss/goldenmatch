"""Tests for live catalog write-back (write_resolved_catalog)."""
from __future__ import annotations

import pytest
from goldenmatch.semantic import (
    parse_cube_models,
    parse_osi_models,
    parse_semantic_models,
    validate_osi,
    write_resolved_catalog,
)


class _Crosswalk:
    """Minimal ResolvedCrosswalk stand-in (the emitters duck-type these attrs)."""

    source_pk_column = "customer_id"
    resolved_key = "resolved_entity_id"
    n_records = 12
    n_entities = 11
    reduction_ratio = 1 - 11 / 12


def test_write_metricflow_catalog(tmp_path):
    out = tmp_path / "customer_crosswalk.yml"
    yaml_str = write_resolved_catalog(
        _Crosswalk(), out, dialect="metricflow", source_target="orders",
    )
    assert out.exists()
    written = out.read_text(encoding="utf-8")
    assert written == yaml_str
    # the persisted file parses back as a MetricFlow model declaring the resolved key
    specs = parse_semantic_models(written)
    assert specs and specs[0].key == ["resolved_entity_id"]


def test_write_cube_catalog(tmp_path):
    out = tmp_path / "crosswalk.cube.yml"
    write_resolved_catalog(
        _Crosswalk(), out, dialect="cube", source_target="orders",
    )
    cubes = parse_cube_models(out.read_text(encoding="utf-8"))
    names = {c.name for c in cubes}
    assert "crosswalk" in names and "orders" in names


def test_write_osi_catalog_is_valid_osi(tmp_path):
    out = tmp_path / "crosswalk.osi.yml"
    write_resolved_catalog(
        _Crosswalk(), out, dialect="osi", source_target="store_sales",
    )
    doc = out.read_text(encoding="utf-8")
    assert validate_osi(doc) == []                 # written catalog is valid OSI
    assert parse_osi_models(doc)                    # and round-trips


def test_write_refuses_to_clobber_without_overwrite(tmp_path):
    out = tmp_path / "c.yml"
    out.write_text("existing", encoding="utf-8")
    with pytest.raises(FileExistsError):
        write_resolved_catalog(_Crosswalk(), out, dialect="osi", source_target="s")
    assert out.read_text(encoding="utf-8") == "existing"   # untouched


def test_write_overwrite_true_replaces(tmp_path):
    out = tmp_path / "c.yml"
    out.write_text("stale", encoding="utf-8")
    write_resolved_catalog(
        _Crosswalk(), out, dialect="osi", source_target="s", overwrite=True,
    )
    assert out.read_text(encoding="utf-8") != "stale"


def test_write_creates_parent_dirs(tmp_path):
    out = tmp_path / "models" / "sub" / "c.yml"
    write_resolved_catalog(_Crosswalk(), out, dialect="metricflow", source_target="orders")
    assert out.exists()


def test_write_unknown_dialect_raises(tmp_path):
    with pytest.raises(ValueError, match="unknown dialect"):
        write_resolved_catalog(_Crosswalk(), tmp_path / "c.yml", dialect="looker", source_target="x")


def test_write_forwards_emit_kwargs(tmp_path):
    # metricflow emit accepts measures/grain — they must reach the emitter
    out = tmp_path / "c.yml"
    write_resolved_catalog(
        _Crosswalk(), out, dialect="metricflow", source_target="orders",
        measures=["revenue"], grain="order_date",
    )
    specs = parse_semantic_models(out.read_text(encoding="utf-8"))
    assert specs[0].measures == ["revenue"]
