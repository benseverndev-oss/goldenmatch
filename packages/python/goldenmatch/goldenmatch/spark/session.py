"""Spark Connect session helpers.

The tier is programmed via PySpark / **Spark Connect**, not the DataFusion Python
API -- a re-expression of the one-box spine's algorithm, not a port. It speaks
the Connect protocol and is server-agnostic: Apache Spark (the target), Sail, or
anything else exposing ``sc://``.
"""
from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_ENV = "SPARK_REMOTE"
_LEGACY_ENV = "SAIL_REMOTE"
_WARNED_LEGACY = False


def _remote_from_env() -> str | None:
    """``SPARK_REMOTE``, falling back to the legacy ``SAIL_REMOTE`` with a
    one-time warning. The tier was never Sail-specific; the old name is kept
    working so existing clusters and CI secrets do not break on the rename."""
    global _WARNED_LEGACY
    url = os.environ.get(_ENV)
    if url:
        return url
    legacy = os.environ.get(_LEGACY_ENV)
    if legacy and not _WARNED_LEGACY:
        _WARNED_LEGACY = True
        logger.warning(
            "%s is deprecated; use %s. The tier targets Apache Spark Connect "
            "(any server), and the old name implied otherwise.",
            _LEGACY_ENV,
            _ENV,
        )
    return legacy


def connect(remote: str | None = None) -> Any:
    """Return a SparkSession connected to a Spark Connect endpoint.

    ``remote`` (or ``SPARK_REMOTE``, or legacy ``SAIL_REMOTE``) is an
    ``sc://host:port`` URL. ``local[*]`` also works on Apache Spark, which
    spawns its own in-process Connect server -- useful for dev and CI with no
    cluster at all.

    Raises if neither is set: the tier has no implicit cluster bootstrap
    (bring-your-own).
    """
    from pyspark.sql import SparkSession

    url = remote or _remote_from_env()
    if not url:
        raise RuntimeError(
            f"No Spark remote: pass remote='sc://host:port', set {_ENV}, or use "
            f"'local[*]' for an in-process Apache Spark Connect server."
        )
    return SparkSession.builder.remote(url).getOrCreate()
