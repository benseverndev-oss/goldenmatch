"""infer_schema — stage 0 of GoldenPipe. Runs InferMap to label columns.

Produces ``ctx.artifacts['inferred_schema']`` (a goldencheck_types.InferredSchema
or None when InferMap is skipped). Consumes nothing — must run before any
other stage that wants typed columns.

Configuration via ``ctx.stage_config``:
    domain: str | None    Force a specific domain pack name.
    schema: InferredSchema | None    User-provided schema; skip InferMap.
    no_infer: bool        Skip InferMap entirely.

Flag precedence: schema > no_infer > domain > auto-detect.
"""
from __future__ import annotations

from goldencheck_types import InferredSchema, FieldMapping, load_domain
import infermap

from goldenpipe.models.context import PipeContext, StageResult, StageStatus
from goldenpipe.models.stage import stage


def _result_to_inferred_schema(result, domain: str) -> InferredSchema:
    """Convert an InferMap MapResult into a goldencheck_types.InferredSchema."""
    fields: dict[str, FieldMapping] = {}
    for fm in result.mappings:
        fields[fm.source] = FieldMapping(
            source_col=fm.source,
            canonical=fm.target,
            type=fm.target if fm.target else "unknown",
            confidence=fm.confidence,
            evidence={"reasoning": fm.reasoning},
        )
    # unmapped_source columns from InferMap also become unknown FieldMappings
    for col in result.unmapped_source:
        if col not in fields:
            fields[col] = FieldMapping(
                source_col=col,
                canonical=None,
                type="unknown",
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

    domain = cfg.get("domain") or infermap.detect_domain(ctx.df) or "generic"
    pack = load_domain(domain)
    target = infermap.DomainPackTarget(pack)
    result = infermap.map(ctx.df, target, soft=True)
    ctx.artifacts["inferred_schema"] = _result_to_inferred_schema(result, domain)
    return StageResult(status=StageStatus.SUCCESS)
