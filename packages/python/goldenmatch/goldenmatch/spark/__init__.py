"""The Spark tier (distributed, Spark Connect) -- the distributed sibling of the
one-box DataFusion spine.

Programmed via the Spark Connect protocol (PySpark DataFrame/SQL), NOT the
datafusion Python API: this package re-expresses the spine's relational
algorithm against PySpark. It is a parallel implementation, not a port, and it
is server-agnostic -- Apache Spark (the target), Sail, or anything else exposing
``sc://``. Opt-in via ``pip install goldenmatch[spark]``.

**``run_config_pipeline`` is the entry point for a real config**, and it takes a
SPARK DataFrame. That is deliberate: the product goal is running over data the
customer's cluster already holds, so the tier never asks for a driver-side
frame. ``dedupe_df``'s ``backend=`` parameter selects a block-scorer for a LOCAL
dataset and is a different seam; passing ``backend="spark"`` there raises and
points here rather than silently running single-box.

Specs: docs/superpowers/specs/2026-08-10-spark-native-execution-design.md
(current), docs/superpowers/specs/2026-06-03-sail-tier-design.md (S1-S5 history)
"""
from __future__ import annotations

# Stable public IdentityGraph API (#859). These import without the [spark] extra
# (pyspark is imported lazily inside the builders), so a downstream consumer can
# pin the contract via `from goldenmatch.spark import IdentityGraphFrames,
# build_identity_graph` and a `inspect.signature` test, without a Spark runtime.
from goldenmatch.spark.autoconfig import auto_configure_spark
from goldenmatch.spark.config_pipeline import run_config_pipeline
from goldenmatch.spark.identity import (
    EDGE_COLUMNS,
    EVENT_COLUMNS,
    NODE_COLUMNS,
    RECORD_COLUMNS,
    IdentityGraphFrames,
    build_identity_graph,
    build_identity_graph_incremental,
)

__all__ = [
    "IdentityGraphFrames",
    "build_identity_graph",
    "build_identity_graph_incremental",
    "run_config_pipeline",
    "auto_configure_spark",
    "NODE_COLUMNS",
    "RECORD_COLUMNS",
    "EDGE_COLUMNS",
    "EVENT_COLUMNS",
]
