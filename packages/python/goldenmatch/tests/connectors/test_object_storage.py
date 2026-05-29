"""Tests for the object-storage connector.

Polars handles the actual cloud IO; we exercise the connector's
format inference, mode dispatch, and storage_options plumbing using
local-filesystem paths (which the same code paths handle).
"""
from __future__ import annotations

import polars as pl
import pytest


# ----- format inference -------------------------------------------------------


@pytest.mark.parametrize("path,fmt", [
    ("s3://bucket/data.parquet", "parquet"),
    ("gs://bucket/file.pq", "parquet"),
    ("local/data.csv", "csv"),
    ("abfs://container/data.csv.gz", "csv"),
    ("s3://bucket/file.ndjson", "ndjson"),
    ("s3://bucket/file.jsonl", "ndjson"),
    ("local/file.json", "json"),
])
def test_format_inference_from_suffix(path: str, fmt: str) -> None:
    from goldenmatch.connectors.object_storage import ObjectStorageConnector

    conn = ObjectStorageConnector(config={})
    assert conn._resolve_format(path, {}) == fmt


def test_format_inference_unknown_raises() -> None:
    from goldenmatch.connectors.base import ConnectorError
    from goldenmatch.connectors.object_storage import ObjectStorageConnector

    conn = ObjectStorageConnector(config={})
    with pytest.raises(ConnectorError, match="Could not infer format"):
        conn._resolve_format("s3://bucket/data.bin", {})


def test_explicit_format_overrides_suffix() -> None:
    from goldenmatch.connectors.object_storage import ObjectStorageConnector

    conn = ObjectStorageConnector(config={})
    assert conn._resolve_format(
        "s3://bucket/anything.bin", {"format": "parquet"}
    ) == "parquet"


def test_explicit_format_validated() -> None:
    from goldenmatch.connectors.base import ConnectorError
    from goldenmatch.connectors.object_storage import ObjectStorageConnector

    conn = ObjectStorageConnector(config={})
    with pytest.raises(ConnectorError, match="Unsupported format"):
        conn._resolve_format("anything", {"format": "avro"})


# ----- read (local-path round-trip) ------------------------------------------


def test_read_parquet_local(tmp_path) -> None:
    from goldenmatch.connectors.object_storage import ObjectStorageConnector

    p = tmp_path / "data.parquet"
    pl.DataFrame({"id": [1, 2], "name": ["a", "b"]}).write_parquet(p)
    conn = ObjectStorageConnector(config={})
    df = conn.read({"path": str(p)}).collect()
    assert df.height == 2
    assert df["name"].to_list() == ["a", "b"]


def test_read_csv_local(tmp_path) -> None:
    from goldenmatch.connectors.object_storage import ObjectStorageConnector

    p = tmp_path / "data.csv"
    pl.DataFrame({"id": [1, 2], "name": ["a", "b"]}).write_csv(p)
    conn = ObjectStorageConnector(config={})
    df = conn.read({"path": str(p)}).collect()
    assert df.height == 2


def test_read_with_column_projection(tmp_path) -> None:
    from goldenmatch.connectors.object_storage import ObjectStorageConnector

    p = tmp_path / "data.parquet"
    pl.DataFrame({"id": [1], "name": ["a"], "extra": [99]}).write_parquet(p)
    conn = ObjectStorageConnector(config={})
    df = conn.read({"path": str(p), "columns": ["id", "name"]}).collect()
    assert df.columns == ["id", "name"]


def test_read_missing_path_raises() -> None:
    from goldenmatch.connectors.base import ConnectorError
    from goldenmatch.connectors.object_storage import ObjectStorageConnector

    conn = ObjectStorageConnector(config={})
    with pytest.raises(ConnectorError, match="requires config\\['path'\\]"):
        conn.read({}).collect()


# ----- write -----------------------------------------------------------------


def test_write_parquet_local(tmp_path) -> None:
    from goldenmatch.connectors.object_storage import ObjectStorageConnector

    p = tmp_path / "out.parquet"
    df = pl.DataFrame({"id": [1, 2], "name": ["a", "b"]})
    conn = ObjectStorageConnector(config={})
    conn.write(df, {"path": str(p)})
    back = pl.read_parquet(p)
    assert back.height == 2


def test_write_csv_local(tmp_path) -> None:
    from goldenmatch.connectors.object_storage import ObjectStorageConnector

    p = tmp_path / "out.csv"
    df = pl.DataFrame({"id": [1, 2], "name": ["a", "b"]})
    conn = ObjectStorageConnector(config={})
    conn.write(df, {"path": str(p)})
    back = pl.read_csv(p)
    assert back.height == 2


def test_write_append_rejected_for_parquet(tmp_path) -> None:
    from goldenmatch.connectors.base import ConnectorError
    from goldenmatch.connectors.object_storage import ObjectStorageConnector

    p = tmp_path / "out.parquet"
    conn = ObjectStorageConnector(config={})
    df = pl.DataFrame({"id": [1]})
    with pytest.raises(ConnectorError, match="'append' is not supported for parquet"):
        conn.write(df, {"path": str(p), "mode": "append"})


def test_write_unknown_mode_raises(tmp_path) -> None:
    from goldenmatch.connectors.base import ConnectorError
    from goldenmatch.connectors.object_storage import ObjectStorageConnector

    p = tmp_path / "out.parquet"
    conn = ObjectStorageConnector(config={})
    df = pl.DataFrame({"id": [1]})
    with pytest.raises(ConnectorError, match="unknown mode"):
        conn.write(df, {"path": str(p), "mode": "upsert"})


def test_write_empty_df_skips(tmp_path) -> None:
    from goldenmatch.connectors.object_storage import ObjectStorageConnector

    p = tmp_path / "out.parquet"
    conn = ObjectStorageConnector(config={})
    conn.write(pl.DataFrame(), {"path": str(p)})
    assert not p.exists()


def test_write_ndjson_to_cloud_raises() -> None:
    """Polars can't write ndjson to cloud URIs; the connector surfaces a
    helpful error rather than the generic Polars failure."""
    from goldenmatch.connectors.base import ConnectorError
    from goldenmatch.connectors.object_storage import ObjectStorageConnector

    conn = ObjectStorageConnector(config={})
    df = pl.DataFrame({"id": [1]})
    with pytest.raises(ConnectorError, match="write_ndjson to cloud"):
        conn.write(df, {"path": "s3://bucket/file.ndjson"})


# ----- registry --------------------------------------------------------------


def test_load_connector_aliases() -> None:
    from goldenmatch.connectors.base import load_connector
    from goldenmatch.connectors.object_storage import ObjectStorageConnector

    for alias in ("object_storage", "s3", "gcs", "gs", "azure_blob", "abfs"):
        conn = load_connector(alias, {})
        assert isinstance(conn, ObjectStorageConnector), alias


# ----- dependency check ------------------------------------------------------


def test_dependency_check_local_path_is_noop() -> None:
    """Local paths have no cloud backend dependency."""
    from goldenmatch.connectors.object_storage import ObjectStorageConnector

    # Should not raise.
    ObjectStorageConnector._check_dependency("/tmp/data.parquet")
    ObjectStorageConnector._check_dependency("relative/path.csv")


def test_dependency_check_helpful_error_when_missing(monkeypatch) -> None:
    """If the s3 backend extra isn't installed, the user gets a
    ``pip install goldenmatch[s3]`` hint."""
    import sys
    from goldenmatch.connectors.base import ConnectorError
    from goldenmatch.connectors.object_storage import ObjectStorageConnector

    monkeypatch.setitem(sys.modules, "boto3", None)
    with pytest.raises(ConnectorError, match="pip install goldenmatch\\[s3\\]"):
        ObjectStorageConnector._check_dependency("s3://bucket/data.parquet")


# ----- storage options -------------------------------------------------------


def test_storage_options_explicit_passthrough() -> None:
    from goldenmatch.connectors.object_storage import ObjectStorageConnector

    conn = ObjectStorageConnector(config={})
    opts = conn._storage_options(
        "s3://b/k.parquet",
        {"storage_options": {"aws_region": "us-east-2", "custom": "yes"}},
    )
    assert opts == {"aws_region": "us-east-2", "custom": "yes"}


def test_storage_options_from_credentials(monkeypatch) -> None:
    """Credentials wired via ``credentials_env`` surface in storage_options."""
    from goldenmatch.connectors.object_storage import ObjectStorageConnector

    conn = ObjectStorageConnector(config={})
    conn._credentials = {
        "aws_access_key_id": "AKIA...",
        "aws_secret_access_key": "secret",
        "aws_region": "us-west-2",
        "unrelated_key": "ignored",
    }
    opts = conn._storage_options("s3://b/k.parquet", {})
    assert opts == {
        "aws_access_key_id": "AKIA...",
        "aws_secret_access_key": "secret",
        "aws_region": "us-west-2",
    }


def test_storage_options_none_when_empty() -> None:
    from goldenmatch.connectors.object_storage import ObjectStorageConnector

    conn = ObjectStorageConnector(config={})
    assert conn._storage_options("local/data.parquet", {}) is None
