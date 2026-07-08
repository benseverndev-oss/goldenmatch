from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from goldenflow._polars_lazy import pl

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
    # Phase 4d: the pure per-element reference `str|None -> value`. When present, the
    # columnar in-memory engine can apply this transform over a plain list WITHOUT
    # Polars (a scalar op composes into a Polars-free chain), so a config that mixes
    # owned Rust kernels with scalar-only transforms still runs Polars-free. It is the
    # SAME function the Polars `series`-mode path calls via `map_elements`, so a
    # scalar-applied column is byte-identical to the Polars engine. `None` = no
    # Polars-free path yet (the transform stays on the Polars engine).
    scalar: Callable[[str | None], object] | None = None


def register_transform(
    *,
    name: str,
    input_types: list[str],
    auto_apply: bool = False,
    priority: int = 50,
    mode: Literal["expr", "series", "dataframe"] = "series",
    scalar: Callable[[str | None], object] | None = None,
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
            scalar=scalar,
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
