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
from goldenmatch.semantic.key_integrity import certify_key_integrity
from goldenmatch.semantic.metricflow import _load, parse_semantic_models
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

    dialect: str                         # "metricflow" | "cube" | "osi"
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
    `semantic_model` list (singular) + `version`. Raises if none match.
    """
    if "cubes" in data:
        return "cube"
    if "semantic_models" in data:
        return "metricflow"
    if "semantic_model" in data:
        return "osi"
    raise ValueError(
        "certify_semantic_model: could not detect a semantic-layer dialect; "
        "expected a top-level 'cubes' (Cube), 'semantic_models' (dbt/MetricFlow), "
        "or 'semantic_model' (OSI/Ossie) key"
    )


def certify_semantic_model(
    source: str | Any,
    frames: dict[str, Any],
    *,
    resolve: bool = False,
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

    Returns:
        A `SemanticCertification` with one `KeyCertification` per certified key.
    """
    data = _load(source)
    dialect = detect_dialect(data)
    entries: list[KeyCertification] = []
    skipped: list[str] = []

    if dialect == "metricflow":
        for spec in parse_semantic_models(data):
            df = frames.get(spec.model)
            if df is None:
                skipped.append(spec.model)
                continue
            cert = certify_key_integrity(
                df, key=spec.key, measures=spec.measures, grain=spec.grain,
                resolve=resolve,
            )
            entries.append(KeyCertification(target=spec.model, key=list(spec.key), certificate=cert))
    elif dialect == "cube":
        # certify_cube_joins already certifies the one-side key of each join.
        for rep in certify_cube_joins(data, frames, resolve=resolve):
            entries.append(KeyCertification(
                target=rep["to_cube"], key=list(rep["key"]), certificate=rep["certificate"],
                context=f"join from {rep['from_cube']}",
            ))
    else:  # osi
        for rep in certify_osi_relationships(data, frames, resolve=resolve):
            entries.append(KeyCertification(
                target=rep["dataset"], key=list(rep["key"]), certificate=rep["certificate"],
                context=f"relationship {rep['relationship']}",
            ))

    return SemanticCertification(dialect=dialect, entries=entries, skipped=skipped)
