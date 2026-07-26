"""Tests for the ``goldenmatch import-dbt`` CLI command."""
from __future__ import annotations

import json

import polars as pl
import yaml
from goldenmatch.cli.main import app
from typer.testing import CliRunner

runner = CliRunner()


def _write_manifest(tmp_path, nodes, adapter="duckdb"):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"metadata": {"adapter_type": adapter}, "nodes": nodes}))
    return path


_DIM = {
    "model.s.dim_customers": {
        "resource_type": "model", "name": "dim_customers",
        "compiled_code": "select * from s qualify row_number() over "
        "(partition by lower(trim(email)) order by updated_at desc) = 1",
        "raw_code": "",
    }
}


def test_import_dbt_writes_config(tmp_path):
    manifest = _write_manifest(tmp_path, _DIM, adapter="snowflake")
    out = tmp_path / "gm.yaml"
    result = runner.invoke(app, ["import-dbt", str(manifest), "-o", str(out)])
    assert result.exit_code == 0, result.output
    assert "Distilled" in result.output
    assert out.exists()
    cfg = yaml.safe_load(out.read_text())
    assert cfg["matchkeys"][0]["type"] == "exact"
    assert cfg["matchkeys"][0]["fields"][0]["field"] == "email"
    assert cfg["blocking"]["keys"][0]["field_transforms"] == {"email": ["strip", "lowercase"]}


def test_import_dbt_non_er_project_writes_no_config(tmp_path):
    nodes = {
        "model.j.orders": {"resource_type": "model", "name": "orders",
                           "compiled_code": "select order_id from raw", "raw_code": ""},
    }
    manifest = _write_manifest(tmp_path, nodes)
    out = tmp_path / "gm.yaml"
    result = runner.invoke(app, ["import-dbt", str(manifest), "-o", str(out)])
    assert result.exit_code == 0, result.output
    assert "No config written" in result.output
    assert not out.exists()


def test_import_dbt_verify_needs_source(tmp_path):
    manifest = _write_manifest(tmp_path, _DIM)
    out_table = tmp_path / "out.parquet"
    pl.DataFrame({"id": [1], "cluster_id": ["c1"]}).write_parquet(out_table)
    result = runner.invoke(
        app, ["import-dbt", str(manifest), "-o", str(tmp_path / "g.yaml"),
              "--verify", str(out_table)]
    )
    assert result.exit_code == 1
    assert "needs --source" in result.output


def test_import_dbt_verify_reports_agreement(tmp_path):
    manifest = _write_manifest(tmp_path, _DIM)
    src = tmp_path / "src.csv"
    pl.DataFrame({
        "id": [1, 2, 3, 4],
        "email": ["a@x.com", "a@x.com", "b@y.com", "c@z.com"],
        "updated_at": ["2020", "2021", "2020", "2020"],
    }).write_csv(src)
    out_table = tmp_path / "out.csv"
    pl.DataFrame({"id": [1, 2, 3, 4], "cluster_id": ["c1", "c1", "c2", "c3"]}).write_csv(out_table)
    result = runner.invoke(
        app,
        ["import-dbt", str(manifest), "-o", str(tmp_path / "g.yaml"),
         "--verify", str(out_table), "--source", str(src), "--id-column", "id"],
    )
    assert result.exit_code == 0, result.output
    assert "agreement" in result.output.lower()


def test_import_dbt_catalog_emits_survivorship(tmp_path):
    manifest = _write_manifest(tmp_path, _DIM)
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({"nodes": {"model.s.dim_customers": {"columns": {
        "email": {"name": "email", "index": 0},
        "full_name": {"name": "full_name", "index": 1},
        "updated_at": {"name": "updated_at", "index": 2},
    }}}}))
    out = tmp_path / "gm.yaml"
    result = runner.invoke(
        app, ["import-dbt", str(manifest), "-o", str(out), "--catalog", str(catalog)]
    )
    assert result.exit_code == 0, result.output
    cfg = yaml.safe_load(out.read_text())
    assert cfg["golden_rules"]["field_rules"]["full_name"]["strategy"] == "most_recent"
    assert cfg["golden_rules"]["field_rules"]["full_name"]["date_column"] == "updated_at"


def test_import_dbt_strict_fails_on_lossy(tmp_path):
    nodes = {
        "model.p.dim_x": {"resource_type": "model", "name": "dim_x",
                          "compiled_code": "select 1 from s group by 1", "raw_code": ""},
    }
    manifest = _write_manifest(tmp_path, nodes, adapter="redshift")
    result = runner.invoke(app, ["import-dbt", str(manifest), "--strict"])
    assert result.exit_code == 1
    assert "conversion failed" in result.output.lower()
