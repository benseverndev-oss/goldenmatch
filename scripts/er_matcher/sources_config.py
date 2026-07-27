"""Validated per-source config loader + loader factory for the multi-source
ER data pipeline (Task 7). `load_sources` mirrors `train.load_config`'s
strict-validation style: unknown keys on a source entry are rejected
outright (so a yaml typo can't silently no-op an override). `build_source`
dispatches to an EXPLICIT per-loader builder rather than a generic **kwargs
spray, because the loader constructors genuinely differ (Magellan takes no
`seed`). CPU/box-safe: stdlib + PyYAML only, never torch/transformers/
network.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from sources.base import PairSource
from sources.febrl import FebrlSource
from sources.leipzig import LeipzigSource
from sources.magellan import MagellanSource
from sources.ncvr import NcvrSource

_VALID_MECHANISMS = {"bundle", "generate", "fetch"}


@dataclass
class SourceEntry:
    name: str  # the yaml key -- not itself a yaml body field
    loader: str
    mechanism: str  # "bundle" | "generate" | "fetch"
    license: str
    domain: str = "generic"
    attribution: str | None = None
    weight: float = 1.0
    eval_only: bool = False
    kwargs: dict[str, Any] = field(default_factory=dict)


# Known per-entry body keys, i.e. every SourceEntry field except `name`
# (which comes from the yaml mapping key, never from the entry body).
_KNOWN_ENTRY_KEYS = {f.name for f in fields(SourceEntry)} - {"name"}


def load_sources(path: Path) -> list[SourceEntry]:
    """PyYAML-load a `{sources: {name: {...}}}` doc into `SourceEntry`s.

    Raises `ValueError` with a clear message on:
      - a missing/non-mapping top-level `sources` key,
      - a non-mapping entry body,
      - unknown keys on an entry,
      - a missing `loader` / `mechanism` / `license`,
      - a `mechanism` outside {bundle, generate, fetch},
      - `license == "CC-BY"` without a non-empty `attribution`,
      - a non-bool `eval_only`,
      - a non-numeric (or bool) `weight`,
      - a non-dict `kwargs`.
    """
    import yaml  # PyYAML; same light always-present dep as train.load_config

    raw = yaml.safe_load(Path(path).read_text()) or {}
    sources_raw = raw.get("sources")
    if not isinstance(sources_raw, dict):
        raise ValueError("sources.yaml must have a top-level `sources` mapping")

    entries: list[SourceEntry] = []
    for name, body in sources_raw.items():
        if not isinstance(body, dict):
            raise ValueError(f"source {name!r}: entry must be a mapping, got {type(body).__name__}")

        unknown = set(body) - _KNOWN_ENTRY_KEYS
        if unknown:
            raise ValueError(
                f"source {name!r}: unknown keys {sorted(unknown)} "
                f"(known: {sorted(_KNOWN_ENTRY_KEYS)})"
            )

        for required in ("loader", "mechanism", "license"):
            if not body.get(required):
                raise ValueError(f"source {name!r}: missing required key {required!r}")

        mechanism = body["mechanism"]
        if mechanism not in _VALID_MECHANISMS:
            raise ValueError(
                f"source {name!r}: mechanism {mechanism!r} must be one of "
                f"{sorted(_VALID_MECHANISMS)}"
            )

        license_ = body["license"]
        attribution = body.get("attribution")
        if license_ == "CC-BY" and not attribution:
            raise ValueError(f"source {name!r}: license CC-BY requires a non-empty attribution")

        eval_only = body.get("eval_only", False)
        if not isinstance(eval_only, bool):
            raise ValueError(
                f"source {name!r}: eval_only must be a bool, got {type(eval_only).__name__} "
                f"({eval_only!r}) -- a yaml typo like `flase` silently becomes truthy otherwise"
            )

        weight = body.get("weight", 1.0)
        # bool is an int subclass in Python; reject it explicitly so a typo'd
        # `weight: true` doesn't silently pass as weight=1.
        if isinstance(weight, bool) or not isinstance(weight, (int, float)):
            raise ValueError(
                f"source {name!r}: weight must be an int or float, got {type(weight).__name__} "
                f"({weight!r})"
            )

        kwargs = body.get("kwargs", {})
        if not isinstance(kwargs, dict):
            raise ValueError(
                f"source {name!r}: kwargs must be a mapping, got {type(kwargs).__name__}"
            )

        entries.append(
            SourceEntry(
                name=name,
                loader=body["loader"],
                mechanism=mechanism,
                license=license_,
                domain=body.get("domain", "generic"),
                attribution=attribution,
                weight=weight,
                eval_only=eval_only,
                kwargs=kwargs,
            )
        )

    return entries


# Per-loader builder dispatch. Explicit (not a generic **entry.kwargs spray)
# because the constructors genuinely differ -- Magellan has NO `seed` param
# (it's pre-labeled, eval-only, no negative synthesis) and Ncvr takes only
# `name` + passthrough kwargs.
_BUILDERS: dict[str, Callable[[SourceEntry, int], PairSource]] = {
    "leipzig": lambda e, seed: LeipzigSource(name=e.name, domain=e.domain, seed=seed, **e.kwargs),
    "febrl": lambda e, seed: FebrlSource(name=e.name, domain=e.domain, seed=seed, **e.kwargs),
    "magellan": lambda e, seed: MagellanSource(name=e.name, domain=e.domain, **e.kwargs),
    "ncvr": lambda e, seed: NcvrSource(name=e.name, **e.kwargs),
}


def build_source(entry: SourceEntry, *, seed: int) -> PairSource:
    """Factory: construct the concrete loader for `entry`, dispatched by
    `entry.loader`. Raises `ValueError` for an unrecognized loader name.
    The returned source's `.name` always equals `entry.name` (the yaml key)
    since every builder passes `name=e.name` explicitly.
    """
    builder = _BUILDERS.get(entry.loader)
    if builder is None:
        raise ValueError(f"unknown loader {entry.loader!r} (known: {sorted(_BUILDERS)})")
    return builder(entry, seed)
