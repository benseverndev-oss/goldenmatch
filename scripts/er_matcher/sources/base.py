"""Loader contract + registry for the multi-source ER data pipeline.
CPU/box-safe: stdlib only, never imports torch/transformers."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Protocol, TypedDict, runtime_checkable


class Row(TypedDict):
    a: dict[str, Any]
    b: dict[str, Any]
    label: str  # "match" | "no_match"
    domain: str
    source: str  # e.g. "leipzig", "febrl", "synthetic"
    dataset: str  # PairSource.name; == registry key == sources.yaml key
    eid_a: str
    eid_b: str


@runtime_checkable
class PairSource(Protocol):
    name: str

    def splits(self) -> dict[str, Iterable[Row]]: ...


_REGISTRY: dict[str, PairSource] = {}


def register(source: PairSource) -> None:
    _REGISTRY[source.name] = source


def get_source(name: str) -> PairSource:
    if name not in _REGISTRY:
        raise KeyError(f"no registered source {name!r}; have {sorted(_REGISTRY)}")
    return _REGISTRY[name]


def iter_sources() -> Iterable[PairSource]:
    return list(_REGISTRY.values())
