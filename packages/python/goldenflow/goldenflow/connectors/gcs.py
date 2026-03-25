"""Google Cloud Storage connector for GoldenFlow."""
from __future__ import annotations

from pathlib import Path
import tempfile

import polars as pl


def read_gcs(uri: str, **kwargs) -> pl.DataFrame:
    """Read a file from GCS into a Polars DataFrame.

    Args:
        uri: GCS URI (gs://bucket/path/to/file.csv)
    """
    try:
        from google.cloud import storage
    except ImportError:
        raise ImportError("GCS support requires: pip install google-cloud-storage")

    client = storage.Client()
    bucket_name, blob_name = _parse_gcs_uri(uri)
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    suffix = Path(blob_name).suffix

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        blob.download_to_filename(tmp.name)
        tmp_path = Path(tmp.name)

    from goldenflow.connectors.file import read_file
    df = read_file(tmp_path, **kwargs)
    tmp_path.unlink(missing_ok=True)
    return df


def write_gcs(df: pl.DataFrame, uri: str, **kwargs) -> None:
    """Write a Polars DataFrame to GCS."""
    try:
        from google.cloud import storage
    except ImportError:
        raise ImportError("GCS support requires: pip install google-cloud-storage")

    client = storage.Client()
    bucket_name, blob_name = _parse_gcs_uri(uri)
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    suffix = Path(blob_name).suffix

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp_path = Path(tmp.name)

    from goldenflow.connectors.file import write_file
    write_file(df, tmp_path, **kwargs)
    blob.upload_from_filename(str(tmp_path))
    tmp_path.unlink(missing_ok=True)


def _parse_gcs_uri(uri: str) -> tuple[str, str]:
    """Parse gs://bucket/path into (bucket, path)."""
    if not uri.startswith("gs://"):
        raise ValueError(f"Invalid GCS URI: {uri}. Must start with gs://")
    path = uri[5:]
    parts = path.split("/", 1)
    if len(parts) != 2:
        raise ValueError(f"Invalid GCS URI: {uri}. Must be gs://bucket/path")
    return parts[0], parts[1]
