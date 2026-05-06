# goldenpipe stages

## Authoring a stage

```python
from goldenpipe.models.context import PipeContext, StageResult, StageStatus
from goldenpipe.models.stage import stage

@stage(name="<name>", produces=["<artifact_key>"], consumes=[])
def my_stage(ctx: PipeContext) -> StageResult:
    ctx.artifacts["<artifact_key>"] = ...
    return StageResult(status=StageStatus.SUCCESS)
```

## Gotchas

- `StageResult` only has `(status, decision, error)`. No `stage_name` field.
- `StageStatus.SUCCESS = "success"` (string enum).
- Pass cross-stage data via `ctx.artifacts: dict[str, Any]`. Stage config goes in `ctx.stage_config: dict[str, Any]`.
- `ctx.df` may be None — defensively check before infering / scanning.

## Existing stages

- `infer_schema` — produces `ctx.artifacts["inferred_schema"]` (a `goldencheck_types.InferredSchema` or `None`). Validates flag precedence: `schema > no_infer > domain > auto-detect`.
