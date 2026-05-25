"""Shared ConfigEdit lever vocabulary.

A small, closed edit language: each edit is a validated mutation of a
``GoldenMatchConfig`` with a human label. ``apply()`` returns ``None`` for a
no-op or an edit that would produce an invalid config (the caller skips it).

This vocabulary is the single lever language shared by:

- the agentic optimizer's proposers (``core/config_optimizer.py``) — each edit
  becomes one scored candidate; and
- the controller's ``LLMRefitPolicy`` (``core/autoconfig_policy.py``) — the LLM
  repair folds a list of edits onto the current config.

Living in its own module keeps the dependency graph acyclic (the optimizer
imports the policy, so the vocabulary can't live in either).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from goldenmatch.config.schemas import BlockingKeyConfig, GoldenMatchConfig

__all__ = [
    "ConfigEdit",
    "ThresholdShift",
    "ScorerSwap",
    "BlockingStrategyEdit",
    "WeightShift",
    "MatchkeyTypeSwap",
    "BlockingKeyEdit",
    "edit_from_spec",
    "parse_llm_edits",
    "fold_edits",
]

_PERTURBABLE_TYPES = ("weighted", "probabilistic")


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _perturbable_matchkeys(config: GoldenMatchConfig) -> list:
    return [
        mk for mk in config.get_matchkeys()
        if getattr(mk, "type", None) in _PERTURBABLE_TYPES and mk.threshold is not None
    ]


class ConfigEdit(Protocol):
    @property
    def label(self) -> str: ...

    def apply(self, config: GoldenMatchConfig) -> GoldenMatchConfig | None: ...


def _revalidate(cfg: GoldenMatchConfig) -> GoldenMatchConfig | None:
    try:
        return GoldenMatchConfig.model_validate(cfg.model_dump())
    except Exception:  # noqa: BLE001 - invalid edit -> skip
        return None


@dataclass(frozen=True)
class ThresholdShift:
    """Shift every perturbable matchkey threshold by ``delta`` (clamped)."""

    delta: float

    @property
    def label(self) -> str:
        return "baseline" if self.delta == 0.0 else f"threshold{self.delta:+.2f}"

    def apply(self, config: GoldenMatchConfig) -> GoldenMatchConfig | None:
        if not _perturbable_matchkeys(config):
            return config if self.delta == 0.0 else None
        cfg = config.model_copy(deep=True)
        changed = False
        for mk in cfg.get_matchkeys():
            if getattr(mk, "type", None) in _PERTURBABLE_TYPES and mk.threshold is not None:
                new_t = _clamp(mk.threshold + self.delta)
                if new_t != mk.threshold:
                    mk.threshold = new_t
                    changed = True
        if self.delta == 0.0:
            return cfg  # baseline: valid, no change
        return cfg if changed else None


@dataclass(frozen=True)
class ScorerSwap:
    """Swap one matchkey field's scorer."""

    matchkey: str
    field: str
    scorer: str

    @property
    def label(self) -> str:
        return f"scorer:{self.field}={self.scorer}"

    def apply(self, config: GoldenMatchConfig) -> GoldenMatchConfig | None:
        cfg = config.model_copy(deep=True)
        changed = False
        for mk in cfg.get_matchkeys():
            if mk.name != self.matchkey:
                continue
            for f in (mk.fields or []):
                if f.field == self.field and f.scorer != self.scorer:
                    f.scorer = self.scorer
                    changed = True
        if not changed:
            return None
        return _revalidate(cfg)


@dataclass(frozen=True)
class BlockingStrategyEdit:
    """Change the blocking strategy (keys preserved)."""

    strategy: str

    @property
    def label(self) -> str:
        return f"blocking:{self.strategy}"

    def apply(self, config: GoldenMatchConfig) -> GoldenMatchConfig | None:
        if config.blocking is None or config.blocking.strategy == self.strategy:
            return None
        cfg = config.model_copy(update={
            "blocking": config.blocking.model_copy(update={"strategy": self.strategy}),
        })
        return _revalidate(cfg)


@dataclass(frozen=True)
class WeightShift:
    """Reweight one field of a weighted matchkey (floor 0.0)."""

    matchkey: str
    field: str
    delta: float

    @property
    def label(self) -> str:
        return f"weight:{self.field}{self.delta:+.2f}"

    def apply(self, config: GoldenMatchConfig) -> GoldenMatchConfig | None:
        cfg = config.model_copy(deep=True)
        changed = False
        for mk in cfg.get_matchkeys():
            if mk.name != self.matchkey or getattr(mk, "type", None) != "weighted":
                continue
            for f in (mk.fields or []):
                if f.field == self.field and f.weight is not None:
                    new_w = max(0.0, f.weight + self.delta)
                    if new_w != f.weight:
                        f.weight = new_w
                        changed = True
        if not changed:
            return None
        return _revalidate(cfg)


@dataclass(frozen=True)
class MatchkeyTypeSwap:
    """Swap a matchkey between ``weighted`` and ``probabilistic``.

    A weighted matchkey already carries everything probabilistic needs (each
    field has a scorer); going the other way we backfill a threshold and uniform
    per-field weights so the weighted invariant holds.
    """

    matchkey: str
    target_type: str  # "weighted" | "probabilistic"

    @property
    def label(self) -> str:
        return f"mktype:{self.matchkey}={self.target_type}"

    def apply(self, config: GoldenMatchConfig) -> GoldenMatchConfig | None:
        if self.target_type not in _PERTURBABLE_TYPES:
            return None
        cfg = config.model_copy(deep=True)
        changed = False
        for mk in cfg.get_matchkeys():
            cur = getattr(mk, "type", None)
            if mk.name != self.matchkey or cur == self.target_type or cur not in _PERTURBABLE_TYPES:
                continue
            mk.type = self.target_type
            mk.comparison = None
            if self.target_type == "weighted":
                if mk.threshold is None:
                    mk.threshold = mk.link_threshold if mk.link_threshold is not None else 0.5
                for f in (mk.fields or []):
                    if f.weight is None:
                        f.weight = 1.0
            changed = True
        if not changed:
            return None
        return _revalidate(cfg)


@dataclass(frozen=True)
class BlockingKeyEdit:
    """Add or remove a blocking key, identified by its field set + transforms."""

    action: str  # "add" | "remove"
    fields: tuple[str, ...]
    transforms: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        return f"block-{self.action}:{'+'.join(self.fields)}"

    def apply(self, config: GoldenMatchConfig) -> GoldenMatchConfig | None:
        if config.blocking is None or self.action not in ("add", "remove") or not self.fields:
            return None
        target, ttx = list(self.fields), list(self.transforms)
        keys = list(config.blocking.keys or [])
        exists = any(k.fields == target and (k.transforms or []) == ttx for k in keys)
        if self.action == "add":
            if exists:
                return None
            keys = [*keys, BlockingKeyConfig(fields=target, transforms=ttx)]
        else:
            if not exists:
                return None
            keys = [k for k in keys if not (k.fields == target and (k.transforms or []) == ttx)]
        cfg = config.model_copy(update={"blocking": config.blocking.model_copy(update={"keys": keys})})
        return _revalidate(cfg)


def edit_from_spec(spec: object) -> ConfigEdit | None:
    """Map one LLM-emitted edit spec (a JSON object) to a ``ConfigEdit``.

    Returns ``None`` for an unknown op or a malformed spec — the closed
    vocabulary is the only thing the LLM can drive, so junk is dropped, not run.
    """
    if not isinstance(spec, dict):
        return None
    op = spec.get("op")
    try:
        if op == "threshold_shift":
            return ThresholdShift(float(spec["delta"]))
        if op == "scorer_swap":
            return ScorerSwap(str(spec["matchkey"]), str(spec["field"]), str(spec["scorer"]))
        if op == "blocking_strategy":
            return BlockingStrategyEdit(str(spec["strategy"]))
        if op == "weight_shift":
            return WeightShift(str(spec["matchkey"]), str(spec["field"]), float(spec["delta"]))
        if op == "matchkey_type":
            return MatchkeyTypeSwap(str(spec["matchkey"]), str(spec["target_type"]))
        if op == "blocking_key":
            return BlockingKeyEdit(
                str(spec["action"]),
                tuple(spec["fields"]),
                tuple(spec.get("transforms", ())),
            )
    except (KeyError, TypeError, ValueError):
        return None
    return None


def parse_llm_edits(payload: object) -> list[ConfigEdit]:
    """Parse an LLM response into a list of validated ``ConfigEdit``s.

    ``{"action": "stop"}`` / ``{"action": "satisfied"}`` (or any non-list
    ``edits``) yields an empty list.
    """
    if not isinstance(payload, dict) or payload.get("action") in ("stop", "satisfied"):
        return []
    raw = payload.get("edits")
    if not isinstance(raw, list):
        return []
    return [e for e in (edit_from_spec(s) for s in raw) if e is not None]


def fold_edits(config: GoldenMatchConfig, edits: list[ConfigEdit]) -> GoldenMatchConfig:
    """Apply ``edits`` in sequence onto ``config``, skipping any that don't
    apply (return ``None``). Returns the folded config — equal to the input
    when every edit was a no-op. Used by the controller's single-trajectory LLM
    repair, where a list of edits composes into one new config."""
    cur = config
    for edit in edits:
        nxt = edit.apply(cur)
        if nxt is not None:
            cur = nxt
    return cur
