"""Object storage source/sink connector (S3 / GCS / Azure Blob).

Reads parquet, CSV, JSON, and NDJSON files from cloud object storage
into a Polars LazyFrame and writes results back. Polars' native
``scan_parquet`` / ``scan_csv`` / ``write_*`` already understand the
common cloud URI schemes; this connector wraps them with credential
resolution, format inference from the URI suffix, and the standard
``read() / write()`` BaseConnector surface.

Requires one of the cloud extras:

  - ``pip install goldenmatch[s3]``         (boto3)
  - ``pip install goldenmatch[gcs]``        (google-cloud-storage)
  - ``pip install goldenmatch[azure_blob]`` (azure-storage-blob)

Polars handles the actual IO; the extras are pulled so credential
resolution + listing both work.

## URI schemes

| Scheme(s)                                   | Backend             |
| ------------------------------------------- | ------------------- |
| ``s3://`` / ``s3a://``                       | AWS / boto3         |
| ``gs://`` / ``gcs://``                        | GCP / google-cloud-storage |
| ``abfs://`` / ``abfss://`` / ``az://``        | Azure / azure-storage-blob |
| ``https://*.blob.core.windows.net/...``      | Azure (URL form)    |
| local path (no scheme)                       | filesystem          |

## Reading

  - ``config['path']``   -- URI of a single file or a glob
                            (e.g. ``s3://bucket/year=*/*.parquet``)
  - ``config['format']`` -- ``parquet`` / ``csv`` / ``json`` / ``ndjson``.
                            Inferred from suffix when omitted.
  - ``config['storage_options']`` -- forwarded to Polars; lets the
                            caller override credentials, region, etc.
  - Additional kwargs (``has_header``, ``separator`` for CSV;
    ``columns`` projection) are forwarded.

## Writing

  - ``config['path']``     -- destination URI
  - ``config['format']``   -- as above
  - ``config['mode']``     -- ``"overwrite"`` (default) or
                              ``"append"`` (CSV/NDJSON only; parquet
                              users should write a new partition file
                              and rely on object-storage immutability)

## Credentials

Polars + ``object_store`` (its Rust backend) auto-discover credentials:

  - AWS: standard ``AWS_ACCESS_KEY_ID`` / ``AWS_SECRET_ACCESS_KEY`` /
    ``AWS_SESSION_TOKEN`` + ``AWS_REGION`` env vars, IAM roles, the
    shared credentials file at ``~/.aws/credentials``
  - GCP: ``GOOGLE_APPLICATION_CREDENTIALS`` pointing at a service-
    account JSON, ADC, or workload identity
  - Azure: ``AZURE_STORAGE_ACCOUNT_NAME`` +
    ``AZURE_STORAGE_ACCOUNT_KEY`` or a SAS token, MSI

Pass explicit credentials via ``config['storage_options']`` if the
defaults are wrong for your environment.
"""
from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

import polars as pl

from goldenmatch.connectors.base import BaseConnector, ConnectorError

logger = logging.getLogger(__name__)


_SUPPORTED_FORMATS = ("parquet", "csv", "json", "ndjson")


class ObjectStorageConnector(BaseConnector):
    """Read/write tabular data from S3, GCS, or Azure Blob Storage."""

    name = "object_storage"

    def read(self, config: dict) -> pl.LazyFrame:
        path = self._require_path(config)
        fmt = self._resolve_format(path, config)
        storage_options = self._storage_options(path, config)
        self._check_dependency(path)

        if fmt == "parquet":
            lf = pl.scan_parquet(path, storage_options=storage_options)
        elif fmt == "csv":
            lf = pl.scan_csv(
                path,
                storage_options=storage_options,
                has_header=config.get("has_header", True),
                separator=config.get("separator", ","),
                encoding=config.get("encoding", "utf8"),
            )
        elif fmt == "json":
            lf = pl.read_json(path, storage_options=storage_options).lazy()  # type: ignore[call-arg]
        elif fmt == "ndjson":
            lf = pl.scan_ndjson(path, storage_options=storage_options)
        else:  # pragma: no cover -- _resolve_format already checked
            raise ConnectorError(
                f"Unsupported format {fmt!r}. "
                f"Choose one of: {', '.join(_SUPPORTED_FORMATS)}."
            )

        cols = config.get("columns")
        if cols:
            lf = lf.select(cols)

        logger.info(
            "ObjectStorage: scanning %s (format=%s)", path, fmt
        )
        return lf

    def write(self, df: pl.DataFrame, config: dict) -> None:
        if df.height == 0:
            logger.info("ObjectStorage: write skipped on empty DataFrame.")
            return

        path = self._require_path(config)
        fmt = self._resolve_format(path, config)
        storage_options = self._storage_options(path, config)
        mode = config.get("mode", "overwrite")
        if mode not in ("overwrite", "append"):
            raise ConnectorError(
                f"ObjectStorage connector: unknown mode {mode!r}. "
                "Choose 'overwrite' or 'append'."
            )
        if mode == "append" and fmt == "parquet":
            raise ConnectorError(
                "ObjectStorage: 'append' is not supported for parquet. "
                "Write a new file under a partitioned prefix instead."
            )
        self._check_dependency(path)

        if fmt == "parquet":
            df.write_parquet(path, storage_options=storage_options)
        elif fmt == "csv":
            df.write_csv(
                path,
                storage_options=storage_options,
                separator=config.get("separator", ","),
                include_header=config.get("include_header", True),
            )
        elif fmt == "ndjson":
            # Polars' write_ndjson doesn't accept storage_options; fall back
            # to writing to a local path, OR raise if the user pointed at a
            # cloud URI. CSV / parquet cover the cloud case cleanly.
            scheme = urlparse(path).scheme.lower()
            if scheme in ("s3", "s3a", "gs", "gcs", "abfs", "abfss", "az",
                          "http", "https"):
                raise ConnectorError(
                    "ObjectStorage: write_ndjson to cloud URIs is not "
                    "supported by Polars. Use parquet or csv on cloud "
                    "destinations; ndjson works for local paths."
                )
            df.write_ndjson(path)
        else:
            raise ConnectorError(
                f"ObjectStorage write not supported for format {fmt!r}. "
                "Use parquet, csv, or ndjson."
            )

        logger.info(
            "ObjectStorage: wrote %d rows to %s (format=%s, mode=%s)",
            df.height, path, fmt, mode,
        )

    # ----- helpers ----------------------------------------------------------

    @staticmethod
    def _require_path(config: dict) -> str:
        path = config.get("path")
        if not path:
            raise ConnectorError(
                "ObjectStorage connector requires config['path'] -- "
                "an s3://, gs://, abfs://, or https://...blob URL."
            )
        return str(path)

    def _storage_options(self, path: str, config: dict) -> dict[str, Any] | None:
        explicit = config.get("storage_options")
        if explicit is not None:
            return dict(explicit)
        # The credentials_env loader populated self._credentials; surface
        # anything useful as Polars storage_options. Polars accepts
        # backend-specific keys; we forward AWS-style values when the URI
        # is s3, GCP-style when gs/gcs, etc.
        opts: dict[str, Any] = {}
        for k in ("aws_access_key_id", "aws_secret_access_key",
                  "aws_session_token", "aws_region",
                  "google_service_account",
                  "azure_storage_account_name", "azure_storage_account_key"):
            v = self._credentials.get(k)
            if v:
                opts[k] = v
        return opts or None

    @staticmethod
    def _resolve_format(path: str, config: dict) -> str:
        fmt = config.get("format")
        if fmt:
            f = str(fmt).lower()
            if f not in _SUPPORTED_FORMATS:
                raise ConnectorError(
                    f"Unsupported format {fmt!r}. "
                    f"Choose one of: {', '.join(_SUPPORTED_FORMATS)}."
                )
            return f

        lower = path.lower()
        if lower.endswith(".parquet") or lower.endswith(".pq"):
            return "parquet"
        if lower.endswith(".csv") or lower.endswith(".csv.gz"):
            return "csv"
        if lower.endswith(".ndjson") or lower.endswith(".jsonl"):
            return "ndjson"
        if lower.endswith(".json"):
            return "json"

        raise ConnectorError(
            f"Could not infer format from {path!r}. "
            "Pass config['format'] explicitly."
        )

    @staticmethod
    def _check_dependency(path: str) -> None:
        """Surface a helpful install hint if the user's path points at a
        cloud backend whose Polars-side dependency hasn't been installed.

        Polars' own import-error message is generic; the helpful version
        is "pip install goldenmatch[<cloud>]".
        """
        scheme = urlparse(path).scheme.lower()
        try:
            if scheme in ("s3", "s3a"):
                import boto3  # type: ignore[import-not-found]  # noqa: F401
            elif scheme in ("gs", "gcs"):
                import google.cloud.storage  # type: ignore[import-not-found]  # noqa: F401
            elif (
                scheme in ("abfs", "abfss", "az")
                or (
                    scheme in ("http", "https")
                    and ".blob.core.windows.net" in path.lower()
                )
            ):
                import azure.storage.blob  # type: ignore[import-not-found]  # noqa: F401
        except ImportError as exc:
            extra = {
                "s3": "s3", "s3a": "s3",
                "gs": "gcs", "gcs": "gcs",
                "abfs": "azure_blob", "abfss": "azure_blob", "az": "azure_blob",
                "http": "azure_blob", "https": "azure_blob",
            }.get(scheme, "<cloud>")
            raise ConnectorError(
                f"Object storage backend for {scheme!r} URIs requires an "
                f"extra. Install with: pip install goldenmatch[{extra}]"
            ) from exc


__all__ = ["ObjectStorageConnector"]
