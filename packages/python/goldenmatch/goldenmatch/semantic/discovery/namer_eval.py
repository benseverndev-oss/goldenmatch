"""Namer quality eval harness (PR-19).

The namer (PR-7) is the one non-deterministic, LLM-driven part of discovery. This module
measures how good its names are against a labeled gold set: `score_naming(suggestions,
gold)` is a pure, deterministic scorer, and `run_namer_eval(model, tables, gold, *,
backend)` is a thin wrapper that names the model then scores it.

The scorer is backend-agnostic, so the real provider (via `load_namer_backend`) is opt-in
for a live run and CI exercises the harness with a fake backend — no API calls, fully
deterministic.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


def _norm(name: str) -> str:
    """Normalize for comparison: lowercase, strip everything but alphanumerics. So
    'Total Revenue' == 'total-revenue!'. Deliberately NOT a stemmer — singular/plural
    variants must be listed as explicit gold aliases, not silently conflated."""
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def _accepted(gold_value: Any) -> set[str]:
    if isinstance(gold_value, (set, list, tuple)):
        return {_norm(v) for v in gold_value}
    return {_norm(gold_value)}


@dataclass(frozen=True)
class TargetResult:
    """The eval outcome for one gold target."""

    target: str
    gold: tuple[str, ...]
    suggested: str | None
    matched: bool
    verified: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "gold": list(self.gold),
            "suggested": self.suggested,
            "matched": self.matched,
            "verified": self.verified,
        }


@dataclass(frozen=True)
class NamerQuality:
    """Naming quality over a labeled gold set.

    - `coverage`: fraction of gold targets the namer suggested *any* name for.
    - `accuracy`: fraction of gold targets named *correctly* (of all targets).
    - `precision`: fraction of *suggested* names that were correct.
    - `verified_accuracy`: correct AND passed self-critique, of all targets.
    """

    n_targets: int
    n_suggested: int
    n_correct: int
    n_verified_correct: int
    coverage: float
    accuracy: float
    precision: float
    verified_accuracy: float
    results: tuple[TargetResult, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_targets": self.n_targets,
            "n_suggested": self.n_suggested,
            "n_correct": self.n_correct,
            "n_verified_correct": self.n_verified_correct,
            "coverage": self.coverage,
            "accuracy": self.accuracy,
            "precision": self.precision,
            "verified_accuracy": self.verified_accuracy,
            "results": [r.to_dict() for r in self.results],
        }


def score_naming(suggestions: list[Any], gold: dict[str, Any]) -> NamerQuality:
    """Score a list of `NameSuggestion` against a gold `{target: accepted_name(s)}` map.

    Only gold targets are scored — extra suggestions the gold set doesn't cover are
    ignored (the gold set defines the evaluation surface).
    """
    by_target: dict[str, Any] = {s.target: s for s in suggestions}

    results: list[TargetResult] = []
    n_suggested = n_correct = n_verified_correct = 0
    for target in sorted(gold):
        accepted = _accepted(gold[target])
        sug = by_target.get(target)
        suggested_name = sug.suggested_name if sug is not None else None
        verified = bool(sug.verified) if sug is not None else False
        matched = suggested_name is not None and _norm(suggested_name) in accepted
        if sug is not None:
            n_suggested += 1
        if matched:
            n_correct += 1
            if verified:
                n_verified_correct += 1
        gold_display = tuple(sorted(gold[target])) if isinstance(
            gold[target], (set, list, tuple)) else (str(gold[target]),)
        results.append(TargetResult(target, gold_display, suggested_name, matched, verified))

    n = len(gold)
    return NamerQuality(
        n_targets=n,
        n_suggested=n_suggested,
        n_correct=n_correct,
        n_verified_correct=n_verified_correct,
        coverage=(n_suggested / n) if n else 0.0,
        accuracy=(n_correct / n) if n else 0.0,
        precision=(n_correct / n_suggested) if n_suggested else 0.0,
        verified_accuracy=(n_verified_correct / n) if n else 0.0,
        results=tuple(results),
    )


def run_namer_eval(
    model: Any,
    tables: dict[str, Any],
    gold: dict[str, Any],
    *,
    backend: Any,
) -> NamerQuality:
    """Name `model` with `backend`, then score the result against `gold`.

    `backend` is any `NamerBackend` — a fake for CI, or `load_namer_backend()` for a live
    provider run (opt-in). Naming never mutates the certified structure; this only reads
    `name_semantic_model`'s advisory output.
    """
    from goldenmatch.semantic.discovery.namer import name_semantic_model

    suggestions = name_semantic_model(model, tables, backend=backend)
    return score_naming(suggestions, gold)
