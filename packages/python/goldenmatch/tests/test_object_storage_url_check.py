"""Azure-blob URL detection must match on the host, not a substring (CodeQL #323).

``".blob.core.windows.net" in path`` is satisfied by a hostile URL that merely
embeds that text in its path or query (e.g. ``https://evil.com/.blob.core.windows.net``).
``_is_azure_blob_https`` parses the URL and checks the hostname suffix instead.
"""

from __future__ import annotations

import pytest
from goldenmatch.connectors.object_storage import _is_azure_blob_https


@pytest.mark.parametrize(
    "url",
    [
        "https://acct.blob.core.windows.net/container/blob.parquet",
        "http://acct.blob.core.windows.net/c/b",
        "https://acct.BLOB.CORE.WINDOWS.NET/c/b",  # case-insensitive host
    ],
)
def test_real_azure_blob_hosts(url: str) -> None:
    assert _is_azure_blob_https(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.com/.blob.core.windows.net/x",  # substring in path, not host
        "https://evil.com/?redirect=.blob.core.windows.net",
        "https://blob.core.windows.net.evil.com/x",  # suffix-confusion attack
        "https://example.com/data.parquet",
        "s3://bucket/key",
    ],
)
def test_non_azure_urls(url: str) -> None:
    assert _is_azure_blob_https(url) is False
