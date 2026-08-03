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
from goldenmatch.semantic.blocking import (
    SemanticFieldRoles,
    metric_aware_attributes,
    semantic_field_roles,
)
from goldenmatch.semantic.catalog import (
    emit_semantic_model_from_store,
    write_resolved_catalog,
)
from goldenmatch.semantic.certify import (
    KeyCertification,
    SemanticCertification,
    certification_report_dict,
    certify_semantic_model,
    detect_dialect,
)
from goldenmatch.semantic.crosswalk import ResolvedCrosswalk, build_resolved_crosswalk
from goldenmatch.semantic.cube import (
    Cube,
    CubeDimension,
    CubeJoin,
    CubeMeasure,
    certify_cube_joins,
    cube_join_keys,
    emit_cube_from_crosswalk,
    emit_cube_yaml,
    parse_cube_models,
)
from goldenmatch.semantic.discovery import (
    JoinCandidate,
    KeyCandidate,
    discover_joins,
    discover_keys,
)
from goldenmatch.semantic.key_integrity import (
    certify_key_integrity,
    certify_structural_json,
)
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
    osi_json_schema,
    parse_osi_models,
    validate_osi,
    validate_osi_schema,
)
from goldenmatch.semantic.serving import (
    ServingJoinCertificate,
    certify_serving_joins,
    entity_360,
    profile_from_crosswalk,
)

__all__ = [
    # zero-config front door — certify a whole semantic model (any dialect)
    "certification_report_dict",
    "certify_semantic_model",
    "SemanticCertification",
    "KeyCertification",
    "detect_dialect",
    # wedge A — certify a declared key
    "certify_key_integrity",
    "certify_structural_json",
    "KeyIntegrityCertificate",
    "parse_semantic_models",
    "DeclaredKeySpec",
    # metric-aware resolution — declared roles drive the ER attribute selection
    "semantic_field_roles",
    "metric_aware_attributes",
    "SemanticFieldRoles",
    # wedge B — resolve once, emit the conformed entity declaration
    "build_resolved_crosswalk",
    "ResolvedCrosswalk",
    "emit_semantic_model",
    "emit_metricflow_yaml",
    "emit_from_crosswalk",
    # live catalog write-back — persist the conformed declaration to a catalog file
    "write_resolved_catalog",
    # emit a conformed catalog directly from the durable identity store
    "emit_semantic_model_from_store",
    # Customer 360 drill-through — resolved key -> the whole customer view
    "profile_from_crosswalk",
    "entity_360",
    # certify the keys a Customer 360 serving layer joins on
    "certify_serving_joins",
    "ServingJoinCertificate",
    # wedge C — OSI / Apache Ossie native provider (bidirectional)
    "parse_osi_models",
    "osi_join_keys",
    "certify_osi_relationships",
    "emit_osi_model",
    "emit_osi_yaml",
    "emit_osi_from_crosswalk",
    "validate_osi",
    "validate_osi_schema",
    "osi_json_schema",
    "OsiModel",
    "OsiDataset",
    "OsiRelationship",
    "OsiField",
    "OsiMetric",
    # follow-on — Cube (cube.dev) dialect (reader + emitter + bridges)
    "parse_cube_models",
    "cube_join_keys",
    "certify_cube_joins",
    "emit_cube_yaml",
    "emit_cube_from_crosswalk",
    "Cube",
    "CubeDimension",
    "CubeMeasure",
    "CubeJoin",
    # semantic-model discovery (Phase 1) — certified key discovery per table
    "discover_keys",
    "KeyCandidate",
    # semantic-model discovery (Phase 3) — certified join / FK discovery across tables
    "discover_joins",
    "JoinCandidate",
]
