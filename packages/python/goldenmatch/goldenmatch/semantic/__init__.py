"""Semantic-layer interoperability (the MetricFlow / Cube / OSI wedge).

GoldenMatch produces the resolved, conformed entity keys that a semantic layer's
joins and measures silently assume. This subpackage exposes that as an *advisory*
capability over the keys a semantic model already declares — it certifies key
integrity, it never mutates a metric.

v1 (certify-only): point `certify_key_integrity` at a table + a declared key
(optionally sourced from a dbt/MetricFlow semantic model via
`parse_semantic_models`) and get a `KeyIntegrityCertificate` quantifying
uniqueness, measure fan-out, and — opt-in — entity-fragmentation / undercount.

NOTE: this module is intentionally NOT imported from ``goldenmatch/__init__.py``
so ``import goldenmatch`` stays lightweight and polars-free; import it explicitly
(``from goldenmatch.semantic import certify_key_integrity``).
"""
from __future__ import annotations

from goldenmatch.core.key_integrity_certificate import KeyIntegrityCertificate
from goldenmatch.semantic.crosswalk import ResolvedCrosswalk, build_resolved_crosswalk
from goldenmatch.semantic.key_integrity import certify_key_integrity
from goldenmatch.semantic.metricflow import (
    DeclaredKeySpec,
    emit_from_crosswalk,
    emit_metricflow_yaml,
    emit_semantic_model,
    parse_semantic_models,
)
from goldenmatch.semantic.osi import (
    OsiDataset,
    OsiField,
    OsiMetric,
    OsiModel,
    OsiRelationship,
    certify_osi_relationships,
    emit_osi_from_crosswalk,
    emit_osi_model,
    emit_osi_yaml,
    osi_join_keys,
    parse_osi_models,
)

__all__ = [
    # wedge A — certify a declared key
    "certify_key_integrity",
    "KeyIntegrityCertificate",
    "parse_semantic_models",
    "DeclaredKeySpec",
    # wedge B — resolve once, emit the conformed entity declaration
    "build_resolved_crosswalk",
    "ResolvedCrosswalk",
    "emit_semantic_model",
    "emit_metricflow_yaml",
    "emit_from_crosswalk",
    # wedge C — OSI / Apache Ossie native provider (bidirectional)
    "parse_osi_models",
    "osi_join_keys",
    "certify_osi_relationships",
    "emit_osi_model",
    "emit_osi_yaml",
    "emit_osi_from_crosswalk",
    "OsiModel",
    "OsiDataset",
    "OsiRelationship",
    "OsiField",
    "OsiMetric",
]
