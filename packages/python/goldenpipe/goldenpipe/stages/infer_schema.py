"""infer_schema — stage 0 of GoldenPipe. Runs InferMap to label columns.

Produces ``ctx.artifacts['inferred_schema']`` (a goldencheck_types.InferredSchema
or None when InferMap is skipped). Consumes nothing — must run before any
other stage that wants typed columns.

Configuration via ``ctx.stage_config``:
    domain: str | None    Force a specific domain pack name.
    schema: InferredSchema | None    User-provided schema; skip InferMap.
    no_infer: bool        Skip InferMap entirely.

Flag precedence: schema > no_infer > domain > auto-detect.

The ``UNMAPPED_TYPE`` sentinel is the canonical "no canonical type" marker.
Use ``FieldMapping.is_unknown`` to test for it; never compare ``type`` to
the literal string.
"""
from __future__ import annotations

import logging

import infermap
from goldencheck_types import (
    UNMAPPED_TYPE,
    FieldMapping,
    InferredSchema,
    load_domain,
)

from goldenpipe.models.context import PipeContext, StageResult, StageStatus
from goldenpipe.models.stage import stage

log = logging.getLogger(__name__)


def _result_to_inferred_schema(result, domain: str) -> InferredSchema:
    """Convert an InferMap MapResult into a goldencheck_types.InferredSchema."""
    fields: dict[str, FieldMapping] = {}
    for fm in result.mappings:
        fields[fm.source] = FieldMapping(
            source_col=fm.source,
            canonical=fm.target,
            type=fm.target if fm.target else UNMAPPED_TYPE,
            confidence=fm.confidence,
            evidence={"reasoning": fm.reasoning},
        )
    # unmapped_source columns from InferMap also become unknown FieldMappings
    for col in result.unmapped_source:
        if col not in fields:
            fields[col] = FieldMapping(
                source_col=col,
                canonical=None,
                type=UNMAPPED_TYPE,
                confidence=0.0,
                evidence={},
            )
    confidence = (
        min((fm.confidence for fm in result.mappings), default=0.0)
        if result.mappings else 0.0
    )
    return InferredSchema(domain=domain, fields=fields, confidence=confidence)


def _validate_flags(cfg: dict) -> None:
    """Enforce: at most one of {schema, no_infer, domain} is set."""
    exclusive = sum([
        cfg.get("schema") is not None,
        bool(cfg.get("no_infer")),
        cfg.get("domain") is not None,
    ])
    if exclusive > 1:
        raise ValueError(
            "conflict: 'schema', 'no_infer', and 'domain' are mutually exclusive. "
            "Precedence: schema > no_infer > domain > auto-detect."
        )


@stage(
    name="infer_schema",
    produces=["inferred_schema"],
    consumes=[],
)
def infer_schema_stage(ctx: PipeContext) -> StageResult:
    cfg = ctx.stage_config or {}
    _validate_flags(cfg)

    if cfg.get("schema") is not None:
        ctx.artifacts["inferred_schema"] = cfg["schema"]
        return StageResult(status=StageStatus.SUCCESS)

    if cfg.get("no_infer"):
        ctx.artifacts["inferred_schema"] = None
        return StageResult(status=StageStatus.SUCCESS)

    if ctx.df is None:
        # No data to infer over; skip cleanly.
        ctx.artifacts["inferred_schema"] = None
        return StageResult(status=StageStatus.SUCCESS)

    explicit_domain = cfg.get("domain")
    if explicit_domain:
        domain = explicit_domain
        detect_score = 1.0  # caller pinned it explicitly
        detect_evidence: dict = {"detect_reason": "explicit"}
    else:
        result = infermap.detect_domain_detailed(ctx.df)
        if result.domain is not None:
            domain = result.domain
            detect_score = result.score
            detect_evidence = {
                "detect_reason": result.reason,
                "detect_score": result.score,
                "runner_up": result.runner_up,
                "runner_up_score": result.runner_up_score,
            }
        else:
            domain = "generic"
            detect_score = 0.0
            detect_evidence = {
                "detect_reason": result.reason,
                "detect_score": result.score,
                "runner_up": result.runner_up,
                "runner_up_score": result.runner_up_score,
                "fallback": True,
            }
            log.info(
                "infer_schema: detect_domain reason=%s (score=%.2f, runner_up=%s@%.2f); "
                "falling back to 'generic'. Pin via stage_config['domain'] to suppress.",
                result.reason, result.score, result.runner_up, result.runner_up_score,
            )

    pack = load_domain(domain)
    target = infermap.DomainPackTarget(pack)
    map_result = infermap.map(ctx.df, target, soft=True)
    inferred = _result_to_inferred_schema(map_result, domain)
    # Confidence reflects detection quality (was always min() of mapping
    # confidences before, regardless of whether detection was confident).
    # Replace because InferredSchema is frozen.
    from dataclasses import replace
    inferred = replace(inferred, confidence=detect_score)
    ctx.artifacts["inferred_schema"] = inferred
    ctx.artifacts.setdefault("infer_schema_evidence", detect_evidence)
    return StageResult(status=StageStatus.SUCCESS)
