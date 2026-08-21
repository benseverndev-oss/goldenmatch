"""Zero-config front door: certify every declared key in a semantic model.

`certify_semantic_model(source, frames)` auto-detects the dialect
(dbt/MetricFlow, Cube, or OSI/Ossie), reads every declared identity key, and
certifies each against the supplied data frames via wedge A
(`certify_key_integrity`). One call, any of the three semantic layers, an
advisory certificate per key — "point it at the semantic model you already have
and get a fan-out / undercount report on the first run."

This is the surface-agnostic core the `goldenmatch certify-keys` CLI command and
the `certify_semantic_model` MCP tool both delegate to (shared-capabilities
conform across surfaces). Advisory only — it never mutates a metric or a key.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from goldenmatch.core.key_integrity_certificate import KeyIntegrityCertificate
from goldenmatch.semantic.cube import certify_cube_joins
from goldenmatch.semantic.feast import certify_feast_feature_views
from goldenmatch.semantic.key_integrity import certify_key_integrity
from goldenmatch.semantic.malloy import certify_malloy_joins
from goldenmatch.semantic.metricflow import _load, parse_semantic_models
from goldenmatch.semantic.odcs import certify_odcs_contract
from goldenmatch.semantic.osi import certify_osi_relationships


@dataclass
class KeyCertification:
    """One certified declared key: which model/dataset/cube declared it, the key
    column(s), and the advisory `KeyIntegrityCertificate`."""

    target: str                          # the model / dataset / cube name
    key: list[str]
    certificate: KeyIntegrityCertificate
    context: str = ""                    # e.g. the relationship / join it feeds


@dataclass
class SemanticCertification:
    """The result of certifying a whole semantic model: the detected dialect and
    one `KeyCertification` per declared key that had a supplied frame."""

    dialect: str                         # "metricflow" | "cube" | "osi" | "feast" | "malloy" | "odcs"
    entries: list[KeyCertification] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # targets with no frame
    note: str = ""

    @property
    def n_certified(self) -> int:
        return len(self.entries)

    @property
    def untrustworthy(self) -> list[KeyCertification]:
        """Entries whose declared key is NOT unique at grain (or fans out) — the
        keys a metric silently depends on that will miscount."""
        return [e for e in self.entries if not e.certificate.is_trustworthy()]

    @property
    def all_trustworthy(self) -> bool:
        return not self.untrustworthy


def detect_dialect(data: dict[str, Any]) -> str:
    """Detect the semantic-layer dialect from a loaded document's top-level shape.

    Cube uses a `cubes:` list, MetricFlow a `semantic_models:` list, OSI/Ossie a
    `semantic_model` list (singular) + `version`, Feast a `feature_views:` list,
    Malloy a `sources:` list, and ODCS a `kind: DataContract` + a `schema:` list.
    Raises if none match.
    """
    # ODCS is detected on its unambiguous `kind: DataContract` marker first, so a
    # contract that also happens to carry a generic key can't be misread.
    if str(data.get("kind", "")).strip() == "DataContract":
        return "odcs"
    if "cubes" in data:
        return "cube"
    if "semantic_models" in data:
        return "metricflow"
    if "semantic_model" in data:
        return "osi"
    if "feature_views" in data:
        return "feast"
    if "sources" in data:
        return "malloy"
    # ODCS v2 (pre-Bitol) had no `kind`; a top-level `schema:`/`dataset:` list with
    # an `apiVersion` is the data-contract shape.
    if "apiVersion" in data and isinstance(data.get("schema") or data.get("dataset"), list):
        return "odcs"
    raise ValueError(
        "certify_semantic_model: could not detect a semantic-layer dialect; "
        "expected a top-level 'cubes' (Cube), 'semantic_models' (dbt/MetricFlow), "
        "'semantic_model' (OSI/Ossie), 'feature_views' (Feast), 'sources' "
        "(Malloy), or 'kind: DataContract' (ODCS) key"
    )


def certify_semantic_model(
    source: str | Any,
    frames: dict[str, Any],
    *,
    resolve: bool = False,
    metric_aware: bool = True,
) -> SemanticCertification:
    """Certify every declared identity key in a semantic model.

    Args:
        source: a path, raw YAML string, or loaded dict for a dbt/MetricFlow,
            Cube, or OSI/Ossie semantic model (the dialect is auto-detected).
        frames: maps each model/dataset/cube name to the table backing it
            (pyarrow / polars / pandas / dict). A target with no supplied frame
            is skipped and recorded in `skipped`.
        resolve: also run entity resolution to measure fragmentation / undercount.
            Applied uniformly across all three dialects.
        metric_aware: when resolving, drive the ER off the model's own declared
            roles — resolve on the declared **dimensions** and never on a
            **measure** (the differentiated wedge). A model that declares no
            dimensions is byte-identical to the blind selection. Set False to
            resolve on every non-key, non-measure column (the prior behavior).
            No effect unless `resolve=True`.

    Returns:
        A `SemanticCertification` with one `KeyCertification` per certified key.
    """
    from goldenmatch.semantic.blocking import (
        _frame_columns,
        metric_aware_attributes,
        semantic_field_roles,
    )

    data = _load(source)
    dialect = detect_dialect(data)
    entries: list[KeyCertification] = []
    skipped: list[str] = []

    # Read the model's declared roles once, so the resolution tier is metric-aware
    # across every dialect (a measure is never identity evidence).
    roles = semantic_field_roles(data) if (resolve and metric_aware) else None

    if dialect == "metricflow":
        for spec in parse_semantic_models(data):
            df = frames.get(spec.model)
            if df is None:
                skipped.append(spec.model)
                continue
            attributes = (
                metric_aware_attributes(roles, _frame_columns(df))
                if roles is not None else None
            )
            cert = certify_key_integrity(
                df, key=spec.key, measures=spec.measures, grain=spec.grain,
                resolve=resolve, attributes=attributes,
            )
            entries.append(KeyCertification(target=spec.model, key=list(spec.key), certificate=cert))
    elif dialect == "cube":
        # certify_cube_joins already certifies the one-side key of each join.
        for rep in certify_cube_joins(data, frames, resolve=resolve, roles=roles):
            entries.append(KeyCertification(
                target=rep["to_cube"], key=list(rep["key"]), certificate=rep["certificate"],
                context=f"join from {rep['from_cube']}",
            ))
    elif dialect == "feast":
        # certify_feast_feature_views certifies the entity join key each feature
        # view rides on, with the view's features as the fan-out measures.
        for rep in certify_feast_feature_views(data, frames, resolve=resolve, roles=roles):
            entries.append(KeyCertification(
                target=rep["feature_view"], key=list(rep["key"]), certificate=rep["certificate"],
                context=f"entity {rep['entity']}",
            ))
    elif dialect == "malloy":
        # certify_malloy_joins certifies the one-side key of each source join.
        for rep in certify_malloy_joins(data, frames, resolve=resolve, roles=roles):
            entries.append(KeyCertification(
                target=rep["to_source"], key=list(rep["key"]), certificate=rep["certificate"],
                context=f"join from {rep['from_source']}",
            ))
    elif dialect == "odcs":
        # certify_odcs_contract certifies each schema object's declared identity
        # key — the composite primary key plus each standalone `unique` property.
        for rep in certify_odcs_contract(data, frames, resolve=resolve, roles=roles):
            ctx = "primary key" if rep["kind"] == "primary_key" else f"unique: {rep['key'][0]}"
            entries.append(KeyCertification(
                target=rep["object"], key=list(rep["key"]), certificate=rep["certificate"],
                context=ctx,
            ))
    else:  # osi
        for rep in certify_osi_relationships(data, frames, resolve=resolve, roles=roles):
            entries.append(KeyCertification(
                target=rep["dataset"], key=list(rep["key"]), certificate=rep["certificate"],
                context=f"relationship {rep['relationship']}",
            ))

    return SemanticCertification(dialect=dialect, entries=entries, skipped=skipped)


def certification_report_dict(report: SemanticCertification) -> dict[str, Any]:
    """JSON-serializable view of a `SemanticCertification` — the single source the
    MCP `certify_semantic_model` tool, the REST `POST /semantic/certify` endpoint,
    and the CLI `certify-keys --json` output all emit, so the wire shape can't
    drift across surfaces.

    Each key carries the full trust-verdict block (`certificate_verdict`) — the
    same `key_integrity` projection the catalog emitters write back — so a consumer
    reads the advisory `verdict` plus fan-out, per-measure inflation, and the
    resolution-tier undercount bounds in one place. `n_untrustworthy` /
    `all_trustworthy` are the build-gate signals.
    """
    from goldenmatch.core.key_integrity_certificate import certificate_verdict

    return {
        "dialect": report.dialect,
        "n_certified": report.n_certified,
        "n_untrustworthy": len(report.untrustworthy),
        "all_trustworthy": report.all_trustworthy,
        "skipped": list(report.skipped),
        "note": report.note,
        "keys": [
            {
                "target": e.target,
                "key": list(e.key),
                "context": e.context,
                "key_integrity": certificate_verdict(e.certificate),
            }
            for e in report.entries
        ],
    }
