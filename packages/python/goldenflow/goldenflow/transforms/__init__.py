from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

import polars as pl

# Global transform registry
_REGISTRY: dict[str, TransformInfo] = {}


@dataclass
class TransformInfo:
    name: str
    func: Callable[..., pl.Expr | pl.Series | pl.DataFrame]
    input_types: list[str]
    auto_apply: bool
    priority: int
    mode: Literal["expr", "series", "dataframe"]


def register_transform(
    *,
    name: str,
    input_types: list[str],
    auto_apply: bool = False,
    priority: int = 50,
    mode: Literal["expr", "series", "dataframe"] = "series",
) -> Callable:
    """Decorator to register a transform function."""

    def decorator(func: Callable) -> Callable:
        _REGISTRY[name] = TransformInfo(
            name=name,
            func=func,
            input_types=input_types,
            auto_apply=auto_apply,
            priority=priority,
            mode=mode,
        )
        return func

    return decorator


def get_transform(name: str) -> TransformInfo | None:
    """Look up a transform by name."""
    return _REGISTRY.get(name)


def list_transforms() -> list[TransformInfo]:
    """Return all registered transforms, sorted by priority descending."""
    return sorted(_REGISTRY.values(), key=lambda t: t.priority, reverse=True)


def parse_transform_name(raw: str) -> tuple[str, list[str]]:
    """Parse 'name:param1:param2' into (name, [param1, param2])."""
    parts = raw.split(":")
    return parts[0], parts[1:]


def registry() -> dict[str, TransformInfo]:
    """Return the raw registry dict (for testing/inspection)."""
    return _REGISTRY
