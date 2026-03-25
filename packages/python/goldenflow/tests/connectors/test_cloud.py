import sys
import pytest
from unittest.mock import patch
from goldenflow.connectors.s3 import _parse_s3_uri
from goldenflow.connectors.gcs import _parse_gcs_uri


def test_parse_s3_uri():
    bucket, key = _parse_s3_uri("s3://my-bucket/path/to/file.csv")
    assert bucket == "my-bucket"
    assert key == "path/to/file.csv"


def test_parse_s3_uri_invalid():
    with pytest.raises(ValueError):
        _parse_s3_uri("http://not-s3/file.csv")


def test_parse_gcs_uri():
    bucket, path = _parse_gcs_uri("gs://my-bucket/path/to/file.csv")
    assert bucket == "my-bucket"
    assert path == "path/to/file.csv"


def test_parse_gcs_uri_invalid():
    with pytest.raises(ValueError):
        _parse_gcs_uri("http://not-gcs/file.csv")


def test_read_s3_requires_boto3():
    # Simulate boto3 not being installed by hiding it from sys.modules
    saved = sys.modules.get("boto3")
    sys.modules["boto3"] = None  # type: ignore[assignment]
    try:
        from goldenflow.connectors.s3 import read_s3
        with pytest.raises(ImportError, match="boto3"):
            read_s3("s3://bucket/file.csv")
    finally:
        if saved is None:
            sys.modules.pop("boto3", None)
        else:
            sys.modules["boto3"] = saved


def test_read_gcs_requires_google_cloud():
    with pytest.raises(ImportError, match="google-cloud-storage"):
        from goldenflow.connectors.gcs import read_gcs
        read_gcs("gs://bucket/file.csv")
