"""Model completeness / trust score (PR-16).

A headline, honest self-assessment of a discovered model: what fraction of tables have a
certified grain, sum-safe measures, and a certified join — a grain-weighted 0..1 score —
plus an explicit list of the gaps (so "80% complete" always comes with "these tables are
why it isn't 100%"). Pure aggregation of the existing discovery signals.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# A certified grain is load-bearing (without it a table double-counts), so it dominates.
_GRAIN_WEIGHT = 0.5
_CONNECTIVITY_WEIGHT = 0.25
_MEASURE_WEIGHT = 0.25


@dataclass(frozen=True)
class Gap:
    """One thing a table lacks. `kind` is `no_grain` / `no_measures` / `isolated`."""

    table: str
    kind: str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"table": self.table, "kind": self.kind, "detail": self.detail}


@dataclass(frozen=True)
class ModelCompleteness:
    """A grain-weighted 0..1 completeness score for a discovered model + the gaps."""

    score: float
    n_tables: int
    tables_with_grain: int
    tables_with_measures: int
    tables_connected: int
    gaps: list[Gap] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "n_tables": self.n_tables,
            "tables_with_grain": self.tables_with_grain,
            "tables_with_measures": self.tables_with_measures,
            "tables_connected": self.tables_connected,
            "gaps": [g.to_dict() for g in self.gaps],
        }


def _has_sum_safe_measure(pt: Any) -> bool:
    return any(getattr(m, "safe_to_sum", False) for m in getattr(pt, "measures", []))


def score_model(proposed_tables: list[Any], joins: list[Any]) -> ModelCompleteness:
    """Grain-weighted completeness score + gap list for the discovered model."""
    n = len(proposed_tables)
    if n == 0:
        return ModelCompleteness(0.0, 0, 0, 0, 0, [])

    connected = set()
    for j in joins:
        if getattr(j, "is_trustworthy", False):
            connected.add(j.from_table)
            connected.add(j.to_table)

    with_grain = sum(1 for pt in proposed_tables if pt.grain_trustworthy)
    with_measures = sum(1 for pt in proposed_tables if _has_sum_safe_measure(pt))
    n_connected = sum(1 for pt in proposed_tables if pt.table in connected)

    grain_frac = with_grain / n
    measure_frac = with_measures / n
    # A single table can't have joins, so connectivity is trivially satisfied there.
    conn_frac = 1.0 if n <= 1 else n_connected / n
    score = (_GRAIN_WEIGHT * grain_frac + _CONNECTIVITY_WEIGHT * conn_frac
             + _MEASURE_WEIGHT * measure_frac)

    gaps: list[Gap] = []
    for pt in proposed_tables:
        if not pt.grain_trustworthy:
            gaps.append(Gap(pt.table, "no_grain",
                            "no clean key — a metric on this grain double-counts"))
        if not _has_sum_safe_measure(pt):
            gaps.append(Gap(pt.table, "no_measures", "no sum-safe measure at this grain"))
        if n > 1 and pt.table not in connected:
            gaps.append(Gap(pt.table, "isolated", "no certified join to another table"))

    return ModelCompleteness(
        score=round(score, 6), n_tables=n, tables_with_grain=with_grain,
        tables_with_measures=with_measures, tables_connected=n_connected, gaps=gaps,
    )
