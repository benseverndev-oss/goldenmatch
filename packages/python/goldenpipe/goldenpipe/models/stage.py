"""Stage protocol and @stage decorator."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol, runtime_checkable

from goldenpipe.models.context import PipeContext, StageResult


@dataclass
class StageInfo:
    """Metadata for stage registry and wiring validation."""
    name: str
    produces: list[str]
    consumes: list[str]
    config_schema: type | None = None


@runtime_checkable
class Stage(Protocol):
    """Full contract for pipeline stages."""
    info: StageInfo

    def validate(self, ctx: PipeContext) -> None: ...
    def run(self, ctx: PipeContext) -> StageResult: ...
    def rollback(self, ctx: PipeContext) -> None: ...


class _FunctionStage:
    """Wraps a plain function into a Stage-compatible object."""

    def __init__(
        self,
        fn: Callable[[PipeContext], StageResult],
        info: StageInfo,
    ):
        self._fn = fn
        self.info = info
        self.rollback = None

    def validate(self, ctx: PipeContext) -> None:
        pass

    def run(self, ctx: PipeContext) -> StageResult:
        return self._fn(ctx)


def stage(
    *,
    name: str,
    produces: list[str],
    consumes: list[str],
    config_schema: type | None = None,
) -> Callable[[Callable[[PipeContext], StageResult]], _FunctionStage]:
    """Decorator to create a stage from a plain function."""
    def decorator(fn: Callable[[PipeContext], StageResult]) -> _FunctionStage:
        info = StageInfo(
            name=name,
            produces=produces,
            consumes=consumes,
            config_schema=config_schema,
        )
        return _FunctionStage(fn, info)
    return decorator
