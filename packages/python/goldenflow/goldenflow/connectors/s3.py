"""AWS S3 connector for GoldenFlow."""
from __future__ import annotations

from pathlib import Path
import tempfile

import polars as pl


def read_s3(uri: str, **kwargs) -> pl.DataFrame:
    """Read a file from S3 into a Polars DataFrame.

    Args:
        uri: S3 URI (s3://bucket/key.csv)
    """
    try:
        import boto3
    except ImportError:
        raise ImportError("S3 support requires: pip install boto3")

    client = boto3.client("s3")
    bucket, key = _parse_s3_uri(uri)
    suffix = Path(key).suffix

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        client.download_file(bucket, key, tmp.name)
        tmp_path = Path(tmp.name)

    from goldenflow.connectors.file import read_file
    df = read_file(tmp_path, **kwargs)
    tmp_path.unlink(missing_ok=True)
    return df


def write_s3(df: pl.DataFrame, uri: str, **kwargs) -> None:
    """Write a Polars DataFrame to S3."""
    try:
        import boto3
    except ImportError:
        raise ImportError("S3 support requires: pip install boto3")

    client = boto3.client("s3")
    bucket, key = _parse_s3_uri(uri)
    suffix = Path(key).suffix

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp_path = Path(tmp.name)

    from goldenflow.connectors.file import write_file
    write_file(df, tmp_path, **kwargs)
    client.upload_file(str(tmp_path), bucket, key)
    tmp_path.unlink(missing_ok=True)


def _parse_s3_uri(uri: str) -> tuple[str, str]:
    """Parse s3://bucket/key into (bucket, key)."""
    if not uri.startswith("s3://"):
        raise ValueError(f"Invalid S3 URI: {uri}. Must start with s3://")
    path = uri[5:]
    parts = path.split("/", 1)
    if len(parts) != 2:
        raise ValueError(f"Invalid S3 URI: {uri}. Must be s3://bucket/key")
    return parts[0], parts[1]
