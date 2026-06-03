"""Sail (Spark Connect) session helpers. Sail is programmed via PySpark /
Spark Connect, NOT the datafusion Python API -- this is a re-expression of
the one-box spine's algorithm, not a port."""
from __future__ import annotations

import os
from typing import Any


def connect(remote: str | None = None) -> Any:
    """Return a SparkSession connected to a Sail server.

    ``remote`` (or the ``SAIL_REMOTE`` env var) is an ``sc://host:port`` URL.
    Raises if neither is set -- the Sail tier has no implicit cluster
    bootstrap (bring-your-own).
    """
    from pyspark.sql import SparkSession

    url = remote or os.environ.get("SAIL_REMOTE")
    if not url:
        raise RuntimeError(
            "No Sail remote: pass remote='sc://host:port' or set SAIL_REMOTE."
        )
    return SparkSession.builder.remote(url).getOrCreate()
