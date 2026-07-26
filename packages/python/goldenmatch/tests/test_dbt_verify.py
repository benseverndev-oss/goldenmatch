"""Tests for the dbt conversion auto-verify (``verify_against_dbt``).

A small synthetic source + a synthetic "old output" table where the converted
config SHOULD reproduce the clusters -> assert ``is_faithful``; a deliberately
diverging output -> assert the agreement drops and is reported; missing/empty/
no-overlap output -> assert the graceful degrade (None, warning finding).
"""
from __future__ import annotations

import polars as pl
from goldenmatch.config.dbt_verify import verify_against_dbt
from goldenmatch.config.from_dbt import from_dbt
from goldenmatch.config.from_splink import ConversionReport


def _email_dedupe_config():
    m = {
        "metadata": {"adapter_type": "duckdb"},
        "nodes": {
            "model.s.dim_customers": {
                "resource_type": "model", "name": "dim_customers",
                "compiled_code": "select * from s qualify row_number() over "
                "(partition by email order by updated_at desc) = 1",
                "raw_code": "",
            }
        },
    }
    return from_dbt(m).config


def _source():
    return pl.DataFrame({
        "id": [1, 2, 3, 4, 5, 6],
        "email": ["a@x.com", "a@x.com", "b@y.com", "b@y.com", "c@z.com", "d@w.com"],
        "name": ["Al", "Al", "Bo", "Bo", "Cy", "Di"],
        "updated_at": ["2020", "2021", "2020", "2021", "2020", "2020"],
    })


def test_verify_faithful_when_output_matches():
    config = _email_dedupe_config()
    out = pl.DataFrame({"id": [1, 2, 3, 4, 5, 6],
                        "cluster_id": ["c1", "c1", "c2", "c2", "c3", "c4"]})
    rep = ConversionReport()
    v = verify_against_dbt(config, _source(), out, id_column="id", report=rep)
    assert v is not None
    assert v.is_faithful
    assert v.agreement["f1"] == 1.0
    assert v.n_shared_ids == 6
    assert any("faithful" in f.message for f in rep.findings)


def test_verify_reports_divergence_when_output_overmerges():
    config = _email_dedupe_config()
    out = pl.DataFrame({"id": [1, 2, 3, 4, 5, 6], "cluster_id": ["c1"] * 6})
    rep = ConversionReport()
    v = verify_against_dbt(config, _source(), out, id_column="id", report=rep)
    assert v is not None
    assert not v.is_faithful
    assert v.agreement["f1"] < 0.95
    assert any(f.severity == "warning" and "DIVERGENT" in f.message for f in rep.findings)


def test_verify_degrades_on_empty_output():
    config = _email_dedupe_config()
    out = pl.DataFrame({"id": [], "cluster_id": []}, schema={"id": pl.Int64, "cluster_id": pl.Utf8})
    rep = ConversionReport()
    v = verify_against_dbt(config, _source(), out, id_column="id", report=rep)
    assert v is None
    assert any("empty" in f.message for f in rep.findings)


def test_verify_degrades_on_no_id_overlap():
    config = _email_dedupe_config()
    out = pl.DataFrame({"id": [100, 200], "cluster_id": ["c1", "c1"]})
    rep = ConversionReport()
    v = verify_against_dbt(config, _source(), out, id_column="id", report=rep)
    assert v is None
    assert any("shares no ids" in f.message for f in rep.findings)


def test_verify_degrades_on_empty_source():
    config = _email_dedupe_config()
    src = pl.DataFrame({"id": [], "email": []}, schema={"id": pl.Int64, "email": pl.Utf8})
    out = pl.DataFrame({"id": [1], "cluster_id": ["c1"]})
    rep = ConversionReport()
    v = verify_against_dbt(config, src, out, id_column="id", report=rep)
    assert v is None
    assert any("source is empty" in f.message for f in rep.findings)


def test_verify_named_output_columns():
    config = _email_dedupe_config()
    out = pl.DataFrame({
        "row_pk": [1, 2, 3, 4, 5, 6],
        "surrogate_id": ["c1", "c1", "c2", "c2", "c3", "c4"],
    })
    v = verify_against_dbt(
        config, _source(), out, id_column="id",
        output_id_column="row_pk", output_cluster_column="surrogate_id",
    )
    assert v is not None and v.is_faithful
