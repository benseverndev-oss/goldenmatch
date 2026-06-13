"""Sail tier (distributed, Spark Connect) -- the distributed sibling of the
one-box DataFusion spine.

Sail (LakeSail) is programmed via the Spark Connect protocol (PySpark
DataFrame/SQL), NOT the datafusion Python API. This package re-expresses the
spine's relational algorithm against PySpark; it is a parallel implementation,
not a port. Opt-in via ``pip install goldenmatch[sail]``.

Spec: docs/superpowers/specs/2026-06-03-sail-tier-design.md
"""
from __future__ import annotations

# Stable public IdentityGraph API (#859). These import without the [sail] extra
# (pyspark is imported lazily inside the builders), so a downstream consumer can
# pin the contract via `from goldenmatch.sail import IdentityGraphFrames,
# build_identity_graph` and a `inspect.signature` test, without a Spark runtime.
from goldenmatch.sail.identity import (
    EDGE_COLUMNS,
    EVENT_COLUMNS,
    NODE_COLUMNS,
    RECORD_COLUMNS,
    IdentityGraphFrames,
    build_identity_graph,
)

__all__ = [
    "IdentityGraphFrames",
    "build_identity_graph",
    "NODE_COLUMNS",
    "RECORD_COLUMNS",
    "EDGE_COLUMNS",
    "EVENT_COLUMNS",
]
