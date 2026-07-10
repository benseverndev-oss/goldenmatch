"""Denial-constraint data models."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

__all__ = ["Op", "Predicate", "DenialConstraint"]


class Op(Enum):
    EQ = "="
    NE = "≠"
    LT = "<"
    LE = "≤"
    GT = ">"
    GE = "≥"


@dataclass(frozen=True)
class Predicate:
    """One predicate. kind ∈ {"const" (t.A op literal), "single" (t.A op t.B, same tuple),
    "cross" (tα.A op tβ.B, across a pair)}."""

    kind: str
    col_a: str
    op: Op
    col_b: str | None
    literal: object | None

    def render(self) -> str:
        if self.kind == "const":
            # single-quote string literals; leave others as their repr
            lit = f"'{self.literal}'" if isinstance(self.literal, str) else repr(self.literal)
            return f"{self.col_a} {self.op.value} {lit}"
        return f"{self.col_a} {self.op.value} {self.col_b}"


@dataclass(frozen=True)
class DenialConstraint:
    """A discovered denial constraint ¬(p1 ∧ … ∧ pm): the predicate conjunction should
    (almost) never hold. g1 = fraction of elements (rows for single-tuple scope, pairs for
    cross) that violate it. exact = g1 measured on the full data (True) vs a sample (False)."""

    predicates: tuple[Predicate, ...]
    g1: float
    support: int
    tuple_scope: str  # "single" | "cross"
    exact: bool

    def columns(self) -> tuple[str, ...]:
        seen: list[str] = []
        for p in self.predicates:
            for c in (p.col_a, p.col_b):
                if c is not None and c not in seen:
                    seen.append(c)
        return tuple(seen)

    def render(self) -> str:
        return "¬(" + " ∧ ".join(p.render() for p in self.predicates) + ")"
